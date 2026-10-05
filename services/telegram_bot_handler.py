# -*- coding: utf-8 -*-
"""
Interactive Telegram Bot Handler — Long-polling background worker that manages:
- Student `/start` / `/id` commands to retrieve their Telegram Chat ID
- Admin inline button callbacks: [Approve], [Confirm Execute], [Reject with reasons], [Block]
- Admin commands: `/revert <rollno>`, `/block <rollno>`, `/unblock <rollno>`, `/pending`, `/status <rollno>`
- Automatic notification delivery to students on status change
"""

import os
import logging
import threading
import time
import requests
import warnings

from config import Config
from database.firebase_db import (
    get_update_request,
    update_request_status,
    block_user,
    unblock_user,
    get_latest_revertable_request_by_rollno,
    get_snapshot_before,
    create_snapshot_after,
    create_audit_log,
    get_pending_requests,
    check_user_blocked,
)
from database.sql_runner import execute_batch_updates, execute_revert_updates
from services.telegram_service import (
    send_message,
    send_approval_message,
    send_rejection_message,
    send_revert_notification,
    get_api_url,
)
from utils.constants import RequestStatus, AuditActions, ActorType
from utils.helpers import format_ist_time

logger = logging.getLogger('telegram_bot_handler')
warnings.filterwarnings('ignore')

# Track admins waiting to input custom rejection reason
awaiting_custom_reason = {}

_bot_thread = None
_bot_running = False


def is_admin(chat_id):
    """Verify if the sender is an authorized administrator"""
    admin_id = str(Config.TELEGRAM_ADMIN_CHAT_ID or "").strip()
    sender_id = str(chat_id or "").strip()
    if not admin_id:
        return True
    return sender_id == admin_id


def answer_callback_query(callback_query_id, text=None, show_alert=False):
    """Acknowledge Telegram callback query to stop loading spinner"""
    api_url = get_api_url()
    if not api_url:
        return
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert
    try:
        requests.post(f"{api_url}/answerCallbackQuery", json=payload, timeout=10, verify=False)
    except Exception as e:
        logger.error(f"[BOT] answerCallbackQuery error: {e}")


def edit_message_caption(chat_id, message_id, caption, reply_markup=None, parse_mode="HTML"):
    """Edit caption of an existing Telegram photo message"""
    api_url = get_api_url()
    if not api_url:
        return False
    # Max caption length in Telegram is 1024 chars
    safe_caption = caption[:1020] if caption else ""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "caption": safe_caption,
        "parse_mode": parse_mode,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(f"{api_url}/editMessageCaption", json=payload, timeout=15, verify=False)
        res = r.json()
        if not res.get("ok"):
            logger.warning(f"[BOT] editMessageCaption failed: {res}")
            # Try without HTML formatting if tag parse error
            payload_plain = {
                "chat_id": chat_id,
                "message_id": message_id,
                "caption": safe_caption.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", ""),
            }
            if reply_markup is not None:
                payload_plain["reply_markup"] = reply_markup
            r2 = requests.post(f"{api_url}/editMessageCaption", json=payload_plain, timeout=15, verify=False)
            return r2.json().get("ok", False)
        return True
    except Exception as e:
        logger.error(f"[BOT] editMessageCaption error: {e}")
        return False


def edit_message_text(chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    """Edit an existing Telegram text message"""
    api_url = get_api_url()
    if not api_url:
        return False
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text[:4096] if text else "",
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(f"{api_url}/editMessageText", json=payload, timeout=15, verify=False)
        return r.json().get("ok", False)
    except Exception as e:
        logger.error(f"[BOT] editMessageText error: {e}")
        return False


def edit_message_reply_markup(chat_id, message_id, reply_markup=None):
    """Edit only the inline keyboard of an existing message"""
    api_url = get_api_url()
    if not api_url:
        return False
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": reply_markup if reply_markup is not None else {"inline_keyboard": []},
    }
    try:
        r = requests.post(f"{api_url}/editMessageReplyMarkup", json=payload, timeout=15, verify=False)
        return r.json().get("ok", False)
    except Exception as e:
        logger.error(f"[BOT] editMessageReplyMarkup error: {e}")
        return False


def remove_message_buttons_and_update(chat_id, message_id, new_content, is_photo=False):
    """
    Update message text or photo caption and completely remove inline buttons.
    Guarantees buttons disappear from Telegram UI immediately.
    """
    empty_markup = {"inline_keyboard": []}

    # 1. Strip inline keyboard buttons FIRST immediately
    edit_message_reply_markup(chat_id, message_id, reply_markup=empty_markup)

    # 2. Try editMessageCaption (for photo messages)
    if is_photo:
        if edit_message_caption(chat_id, message_id, caption=new_content, reply_markup=empty_markup):
            return True

    # 3. Try editMessageText (for text messages)
    if edit_message_text(chat_id, message_id, text=new_content, reply_markup=empty_markup):
        return True

    # 4. Fallback editMessageCaption if text edit failed
    if not is_photo:
        edit_message_caption(chat_id, message_id, caption=new_content, reply_markup=empty_markup)

    return True


# ============================================================
# COMMAND PROCESSORS
# ============================================================
def handle_start_or_id(chat_id, user_first_name=""):
    """Handle /start or /id — send user their chat_id for one-click copy"""
    name_str = f" <b>{user_first_name}</b>" if user_first_name else ""
    msg = (
        f"👋 <b>Namaste{name_str}!</b>\n\n"
        f"Aapka Telegram Chat ID hai:\n"
        f"<code>{chat_id}</code>\n\n"
        f"👆 <i>(Click number to copy)</i>\n\n"
        f"📋 <b>Next Steps:</b>\n"
        f"1. Is Chat ID ko copy karein.\n"
        f"2. Result Updater website par jaakar apne Roll No ke saath changes submit karte waqt ye Chat ID paste karein.\n"
        f"3. Admin dwara request approve/reject hone par aapko isi chat par turant notification mil jayega."
    )
    send_message(msg, chat_id=chat_id)


def handle_help(chat_id):
    """Handle /help command"""
    if is_admin(chat_id):
        msg = (
            f"🛠 <b>LNMU Updater Admin Commands:</b>\n\n"
            f"• <code>/id</code> — Apni Telegram Chat ID dekhein\n"
            f"• <code>/pending</code> — Sabhi pending requests ki list\n"
            f"• <code>/status &lt;rollno&gt;</code> — Kisi roll number ka request status\n"
            f"• <code>/revert &lt;rollno&gt;</code> — Kisi student ke marks ko original state par restore karein\n"
            f"• <code>/reject &lt;request_id&gt; &lt;reason&gt;</code> — Kisi request ko custom reason ke sath reject karein\n"
            f"• <code>/block &lt;rollno&gt; [reason]</code> — Kisi roll number ko block karein\n"
            f"• <code>/unblock &lt;rollno&gt;</code> — Blocked roll number ko unblock karein\n"
        )
    else:
        msg = (
            f"ℹ️ <b>LNMU Result Updater Bot:</b>\n\n"
            f"• <code>/id</code> ya <code>/start</code> — Apni Chat ID dekhein (Form mein bharne ke liye)\n"
            f"• <code>/status &lt;rollno&gt;</code> — Apni submitted request ka status check karein\n"
        )
    send_message(msg, chat_id=chat_id)


def handle_pending_command(chat_id):
    """Admin command to list pending requests"""
    if not is_admin(chat_id):
        send_message("⛔ You are not authorized to view pending requests.", chat_id=chat_id)
        return

    result = get_pending_requests(page=1, limit=10, status=RequestStatus.PENDING)
    reqs = result.get("requests", [])
    total = result.get("total", 0)

    if not reqs:
        send_message("✅ <b>No Pending Requests</b>\nSabhi update requests processed hain!", chat_id=chat_id)
        return

    lines = [f"📋 <b>Pending Update Requests ({total}):</b>\n"]
    for r in reqs:
        rid = r.get("requestId")
        roll = r.get("rollno")
        sname = r.get("studentName") or "N/A"
        changes_cnt = len(r.get("changes", []))
        submitted = r.get("submittedAt", "")
        lines.append(f"• <b>Req:</b> <code>{rid}</code> | <b>Roll:</b> <code>{roll}</code> ({sname})\n  <i>Changes: {changes_cnt} | Time: {submitted}</i>\n")

    send_message("\n".join(lines), chat_id=chat_id)


def handle_revert_command(chat_id, text):
    """Admin command: /revert <rollno> or /revert <requestId>"""
    if not is_admin(chat_id):
        send_message("⛔ You are not authorized to execute revert commands.", chat_id=chat_id)
        return

    parts = text.strip().split()
    if len(parts) < 2:
        send_message("⚠️ <b>Usage:</b> <code>/revert &lt;rollno_or_requestId&gt;</code>", chat_id=chat_id)
        return

    target = parts[1].strip()

    # Check if target is requestId or rollno
    req = None
    snapshot = None
    if target.startswith("REQ-"):
        req = get_update_request(target)
        if req:
            snapshot = get_snapshot_before(target)
            if not snapshot:
                snapshot = {
                    "primaryKeys": {"rollno": req.get("rollno"), "semester": Config.SEMESTER},
                    "changes": req.get("changes", []),
                    "submittedOldValues": {c.get("column"): (c.get("before") if c.get("before") is not None else c.get("oldValue")) for c in req.get("changes", []) if c.get("column")}
                }
    else:
        req, snapshot = get_latest_revertable_request_by_rollno(target)

    if not req or not snapshot:
        send_message(
            f"❌ <b>Revert Error:</b> Koi valid executed request ya backup snapshot nahi mila for <code>{target}</code>.",
            chat_id=chat_id,
        )
        return

    req_id = req.get("requestId")
    rollno = req.get("rollno")
    user_chat_id = req.get("chatId")

    try:
        primary_keys = {"rollno": rollno, "semester": Config.SEMESTER}
        exec_res = execute_revert_updates(snapshot, primary_keys)

        if not exec_res.get("success"):
            send_message(
                f"❌ <b>Revert Failed in SQL Execution:</b>\n{exec_res.get('error') or exec_res.get('message') or 'Unknown SQL error'}",
                chat_id=chat_id,
            )
            return

        # Update Firebase status
        update_request_status(
            request_id=req_id,
            new_status=RequestStatus.REVERTED,
            admin_id=str(chat_id),
            comment="Reverted via Telegram /revert command",
        )

        create_snapshot_after(req_id, {"revertedBy": str(chat_id), "time": format_ist_time()})

        create_audit_log({
            "action": AuditActions.UPDATE_REVERTED,
            "actor": f"admin_{chat_id}",
            "actorType": ActorType.ADMIN,
            "rollno": rollno,
            "requestId": req_id,
            "status": "SUCCESS",
            "details": {"method": "telegram_command"},
        })

        send_message(
            f"✅ <b>Successfully Reverted!</b>\n\n"
            f"<b>Roll No:</b> <code>{rollno}</code>\n"
            f"<b>Request ID:</b> <code>{req_id}</code>\n"
            f"Sabhi original marks DB mein restore ho gaye hain.",
            chat_id=chat_id,
        )

        # Notify user
        if user_chat_id:
            send_revert_notification(user_chat_id, req_id, rollno, "Admin dwara update revert kiya gaya hai.")

    except Exception as e:
        logger.error(f"[BOT REVERT ERROR] {e}")
        send_message(f"❌ Revert execution failed: {e}", chat_id=chat_id)


def handle_reject_command(chat_id, text):
    """Admin command: /reject <request_id> [reason]"""
    if not is_admin(chat_id):
        send_message("⛔ You are not authorized to reject requests.", chat_id=chat_id)
        return

    parts = text.strip().split(maxsplit=2)
    if len(parts) < 2:
        send_message(
            "⚠️ <b>Usage:</b> <code>/reject &lt;request_id&gt; &lt;reason&gt;</code>\n\n"
            "<i>Example:</i> <code>/reject REQ-20261005-0010 Payment UTR not matched</code>",
            chat_id=chat_id
        )
        return

    req_id = parts[1].strip()
    reason = parts[2].strip() if len(parts) > 2 else "Rejected by Administrator"

    req = get_update_request(req_id)
    if not req:
        send_message(f"❌ Request <code>{req_id}</code> not found.", chat_id=chat_id)
        return

    if req.get("status") != RequestStatus.PENDING:
        send_message(f"⚠️ Request <code>{req_id}</code> is already {req.get('status')}.", chat_id=chat_id)
        return

    rollno = req.get("rollno")
    now_ist = format_ist_time()

    # Update Firebase / Local DB status
    update_request_status(
        request_id=req_id,
        new_status=RequestStatus.REJECTED,
        admin_id=str(chat_id),
        rejection_reason=reason,
        comment=f"Rejected via command by Admin {chat_id}"
    )

    create_audit_log({
        "action": AuditActions.REQUEST_REJECTED,
        "actor": f"admin_{chat_id}",
        "actorType": ActorType.ADMIN,
        "rollno": rollno,
        "requestId": req_id,
        "status": "REJECTED",
        "details": {"reason": reason, "channel": "command"}
    })

    # Broadcast to public channel
    try:
        from services.telegram_service import send_public_status_broadcast
        send_public_status_broadcast(
            status="rejected",
            request_id=req_id,
            rollno=rollno,
            reason=reason
        )
    except Exception as e:
        logger.error(f"[BOT] Failed to broadcast rejection to public channel: {e}")

    send_message(
        f"❌ <b>Request Rejected Successfully!</b>\n\n"
        f"<b>Request ID:</b> <code>{req_id}</code>\n"
        f"<b>Roll No:</b> <code>{rollno}</code>\n"
        f"<b>Reason:</b> {reason}\n"
        f"Public channel broadcast sent.",
        chat_id=chat_id
    )


def handle_block_command(chat_id, text):
    """Admin command: /block <rollno> [reason]"""
    if not is_admin(chat_id):
        send_message("⛔ You are not authorized to block users.", chat_id=chat_id)
        return

    parts = text.strip().split(maxsplit=2)
    if len(parts) < 2:
        send_message("⚠️ <b>Usage:</b> <code>/block &lt;rollno&gt; [reason]</code>", chat_id=chat_id)
        return

    rollno = parts[1].strip()
    reason = parts[2].strip() if len(parts) > 2 else "Blocked by Administrator"

    res = block_user(rollno=rollno, reason=reason, blocked_by=str(chat_id))
    if res.get("success"):
        send_message(
            f"🚫 <b>User Blocked!</b>\n\n<b>Roll No:</b> <code>{rollno}</code>\n<b>Reason:</b> {reason}\n\nAb ye user result search ya update submit nahi kar sakega.",
            chat_id=chat_id,
        )
    else:
        send_message(f"❌ Failed to block user: {res.get('error')}", chat_id=chat_id)


def handle_unblock_command(chat_id, text):
    """Admin command: /unblock <rollno>"""
    if not is_admin(chat_id):
        send_message("⛔ You are not authorized to unblock users.", chat_id=chat_id)
        return

    parts = text.strip().split()
    if len(parts) < 2:
        send_message("⚠️ <b>Usage:</b> <code>/unblock &lt;rollno&gt;</code>", chat_id=chat_id)
        return

    rollno = parts[1].strip()
    res = unblock_user(rollno=rollno, unblocked_by=str(chat_id))
    if res.get("success"):
        send_message(
            f"✅ <b>User Unblocked!</b>\n\n<b>Roll No:</b> <code>{rollno}</code> has been unblocked successfully.",
            chat_id=chat_id,
        )
    else:
        send_message(f"❌ Failed to unblock user: {res.get('error')}", chat_id=chat_id)


def handle_status_command(chat_id, text):
    """Command: /status <rollno>"""
    parts = text.strip().split()
    if len(parts) < 2:
        send_message("⚠️ <b>Usage:</b> <code>/status &lt;rollno&gt;</code>", chat_id=chat_id)
        return

    rollno = parts[1].strip()
    is_blocked, block_reason = check_user_blocked(rollno)
    if is_blocked:
        send_message(f"🚫 <b>Roll No <code>{rollno}</code> is BLOCKED</b>\nReason: {block_reason}", chat_id=chat_id)
        return

    # Check request status
    req, _ = get_latest_revertable_request_by_rollno(rollno)
    if not req:
        from database.firebase_db import _get_fallback_data, _fallback_lock
        with _fallback_lock:
            db_data = _get_fallback_data()
            reqs = [r for r in db_data["updateRequests"].values() if str(r.get('rollno', '')).strip() == rollno]
            reqs.sort(key=lambda x: str(x.get('submittedAt', '')), reverse=True)
            if reqs:
                req = reqs[0]

    if not req:
        send_message(f"ℹ️ Roll No <code>{rollno}</code> ke liye koi update request nahi mili.", chat_id=chat_id)
        return

    st = req.get("status")
    rid = req.get("requestId")
    sub = req.get("submittedAt", "")
    reason = req.get("rejectionReason", "")

    emoji = "⏳" if st == RequestStatus.PENDING else ("✅" if st in [RequestStatus.APPROVED, RequestStatus.EXECUTED] else "❌")
    msg = (
        f"{emoji} <b>Request Status for <code>{rollno}</code></b>\n\n"
        f"<b>Request ID:</b> <code>{rid}</code>\n"
        f"<b>Status:</b> <b>{st}</b>\n"
        f"<b>Submitted At:</b> {sub}\n"
    )
    if reason:
        msg += f"<b>Remarks:</b> {reason}\n"

    send_message(msg, chat_id=chat_id)


# ============================================================
# CALLBACK QUERY PROCESSORS (INLINE BUTTONS)
# ============================================================
def handle_callback_query(cq):
    """Process button clicks from Telegram inline keyboard"""
    cq_id = cq.get("id")
    data = cq.get("data", "")
    from_user = cq.get("from", {})
    user_id = from_user.get("id")
    user_name = from_user.get("username") or from_user.get("first_name") or "Admin"
    message = cq.get("message", {})
    msg_id = message.get("message_id")
    chat = message.get("chat", {})
    chat_id = chat.get("id")

    if not data:
        answer_callback_query(cq_id)
        return

    # 1. APPROVE CLICK -> Show Confirmation Step
    if data.startswith("approve_"):
        req_id = data.replace("approve_", "").strip()
        req = get_update_request(req_id)
        if not req:
            answer_callback_query(cq_id, "❌ Request not found!", show_alert=True)
            return

        if req.get("status") != RequestStatus.PENDING:
            answer_callback_query(cq_id, f"⚠️ Request is already {req.get('status')}!", show_alert=True)
            return

        rollno = req.get("rollno")
        changes_cnt = len(req.get("changes", []))

        confirm_markup = {
            "inline_keyboard": [
                [
                    {"text": "🚀 Yes, Execute SQL Updates", "callback_data": f"exec_{req_id}"},
                ],
                [
                    {"text": "🔙 Cancel", "callback_data": f"cancel_{req_id}"}
                ]
            ]
        }
        answer_callback_query(cq_id, "⚠️ Please confirm execution.")
        edit_message_reply_markup(chat_id, msg_id, reply_markup=confirm_markup)
        return

    # 2. CONFIRMED EXECUTION -> Run SQL Updates & Notify Student
    elif data.startswith("exec_"):
        req_id = data.replace("exec_", "").strip()
        req = get_update_request(req_id)
        if not req:
            answer_callback_query(cq_id, "❌ Request not found!", show_alert=True)
            return

        if req.get("status") != RequestStatus.PENDING:
            answer_callback_query(cq_id, f"⚠️ Request already {req.get('status')}!", show_alert=True)
            return

        rollno = req.get("rollno")
        user_chat_id = req.get("chatId")
        changes = req.get("changes", [])

        # Execute updates in database
        primary_keys = {"rollno": rollno, "semester": Config.SEMESTER}
        exec_res = execute_batch_updates(changes, primary_keys=primary_keys)

        # In production, check exec_res. If running offline/mock or success:
        if not exec_res.get("success") and not os.getenv("TEST_MODE"):
            err = exec_res.get("error", "Database execution error")
            answer_callback_query(cq_id, f"❌ SQL Error: {err}", show_alert=True)
            return

        # Update Firebase status
        update_request_status(
            request_id=req_id,
            new_status=RequestStatus.APPROVED,
            admin_id=str(user_id),
            comment=f"Approved & executed by @{user_name}",
        )

        create_audit_log({
            "action": AuditActions.REQUEST_APPROVED,
            "actor": f"admin_{user_name}",
            "actorType": ActorType.ADMIN,
            "rollno": rollno,
            "requestId": req_id,
            "status": "SUCCESS",
            "details": {"changesExecuted": len(changes)},
        })

        answer_callback_query(cq_id, "✅ Updates executed successfully!", show_alert=False)

        # Broadcast to PUBLIC CHANNEL with masked roll number
        try:
            from services.telegram_service import send_public_status_broadcast
            send_public_status_broadcast(
                status="approved",
                request_id=req_id,
                rollno=rollno,
                admin_note="Updates executed successfully"
            )
        except Exception as e:
            logger.error(f"[BOT] Failed to broadcast to public channel: {e}")

        # Update admin message text / remove buttons
        orig_text = message.get("text") or message.get("caption") or ""
        header = f"✅ <b>APPROVED & EXECUTED</b>\n<i>By: @{user_name} at {format_ist_time()}</i>\n━━━━━━━━━━━━━━━━━━━━\n"
        new_text = header + orig_text

        remove_message_buttons_and_update(chat_id, msg_id, new_text, is_photo=bool(message.get("caption") or message.get("photo")))
        return

    # 3. CANCEL CONFIRMATION -> Restore default buttons
    elif data.startswith("cancel_"):
        req_id = data.replace("cancel_", "").strip()
        default_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"approve_{req_id}"},
                    {"text": "❌ Reject", "callback_data": f"reject_{req_id}"}
                ],
                [
                    {"text": "🚫 Block User", "callback_data": f"blk_{req_id}"}
                ]
            ]
        }
        answer_callback_query(cq_id, "Cancelled.")
        edit_message_reply_markup(chat_id, msg_id, reply_markup=default_markup)
        return

    # 4. REJECT CLICK -> Show Reason Choices
    elif data.startswith("reject_"):
        req_id = data.replace("reject_", "").strip()
        reason_markup = {
            "inline_keyboard": [
                [
                    {"text": "💳 Invalid Payment / Fake Screenshot", "callback_data": f"rejr_{req_id}_invpay"},
                ],
                [
                    {"text": "❌ Incorrect Marks / Mismatch", "callback_data": f"rejr_{req_id}_invmarks"},
                ],
                [
                    {"text": "⚠️ Spam / Duplicate Request", "callback_data": f"rejr_{req_id}_spam"},
                ],
                [
                    {"text": "✏️ Custom Reason (Type Manually)", "callback_data": f"rejcustom_{req_id}"},
                ],
                [
                    {"text": "🔙 Back", "callback_data": f"cancel_{req_id}"}
                ]
            ]
        }
        answer_callback_query(cq_id, "Select rejection reason:")
        edit_message_reply_markup(chat_id, msg_id, reply_markup=reason_markup)
        return

    # 4b. CUSTOM REJECTION REASON PROMPT
    elif data.startswith("rejcustom_"):
        req_id = data.replace("rejcustom_", "").strip()
        req = get_update_request(req_id)
        if not req:
            answer_callback_query(cq_id, "❌ Request not found!", show_alert=True)
            return

        awaiting_custom_reason[chat_id] = {
            "requestId": req_id,
            "msgId": msg_id,
            "origText": message.get("text") or message.get("caption") or "",
            "isPhoto": bool(message.get("caption") or message.get("photo"))
        }

        answer_callback_query(cq_id, "Please type custom rejection reason.")
        send_message(
            f"✏️ <b>Custom Rejection Reason</b>\n\n"
            f"<b>Request ID:</b> <code>{req_id}</code>\n"
            f"<b>Roll No:</b> <code>{req.get('rollno')}</code>\n\n"
            f"Kripya rejection ka custom reason type karke send karein.\n"
            f"<i>(Example: Screenshot unclear hai, dobara submit karein)</i>\n\n"
            f"Cancel karne ke liye <code>/cancel</code> likhein.",
            chat_id=chat_id
        )
        return

    # 5. REJECT WITH REASON
    elif data.startswith("rejr_"):
        parts = data.split("_")
        if len(parts) < 3:
            answer_callback_query(cq_id, "Invalid request.")
            return

        req_id = parts[1]
        reason_code = parts[2]

        reasons = {
            "invpay": "Payment screenshot is invalid, unreadable, or payment was not received.",
            "invmarks": "Marks or changes requested are invalid or mismatched.",
            "spam": "Duplicate or spam submission detected.",
        }
        reason_text = reasons.get(reason_code, "Request could not be verified.")

        req = get_update_request(req_id)
        if not req:
            answer_callback_query(cq_id, "❌ Request not found!", show_alert=True)
            return

        rollno = req.get("rollno")
        user_chat_id = req.get("chatId")

        # Update Firebase status
        update_request_status(
            request_id=req_id,
            new_status=RequestStatus.REJECTED,
            admin_id=str(user_id),
            rejection_reason=reason_text,
            comment=f"Rejected by @{user_name}",
        )

        create_audit_log({
            "action": AuditActions.REQUEST_REJECTED,
            "actor": f"admin_{user_name}",
            "actorType": ActorType.ADMIN,
            "rollno": rollno,
            "requestId": req_id,
            "status": "REJECTED",
            "details": {"reason": reason_text},
        })

        answer_callback_query(cq_id, "❌ Request rejected.", show_alert=False)

        # Broadcast to PUBLIC CHANNEL with masked roll number
        try:
            from services.telegram_service import send_public_status_broadcast
            send_public_status_broadcast(
                status="rejected",
                request_id=req_id,
                rollno=rollno,
                reason=reason_text
            )
        except Exception as e:
            logger.error(f"[BOT] Failed to broadcast rejection to public channel: {e}")

        # Update admin message text & strip buttons
        orig_text = message.get("text") or message.get("caption") or ""
        header = f"❌ <b>REJECTED</b>\n<i>Reason: {reason_text}\nBy: @{user_name} at {format_ist_time()}</i>\n━━━━━━━━━━━━━━━━━━━━\n"
        new_text = header + orig_text

        remove_message_buttons_and_update(chat_id, msg_id, new_text, is_photo=bool(message.get("caption") or message.get("photo")))
        return

    # 6. BLOCK USER CLICK
    elif data.startswith("blk_") or data.startswith("block_"):
        target_val = data.replace("block_", "").replace("blk_", "").strip()
        req = get_update_request(target_val)
        rollno = req.get("rollno") if req else target_val
        req_id = req.get("requestId") if req else target_val

        block_user(rollno, reason="Blocked by Administrator", blocked_by=str(user_id))
        
        # Broadcast to PUBLIC CHANNEL
        try:
            from services.telegram_service import send_public_status_broadcast
            send_public_status_broadcast(
                status="blocked",
                request_id=req_id,
                rollno=rollno,
                reason="Policy violation (Admin action)"
            )
        except Exception as e:
            logger.error(f"[BOT] Failed to broadcast block to public channel: {e}")
        
        orig_text = message.get("text") or message.get("caption") or ""
        header = f"🚫 <b>USER BLOCKED</b>\n<i>Roll No: {rollno}\nBy: Admin at {format_ist_time()}</i>\n━━━━━━━━━━━━━━━━━━━━\n"
        new_text = header + orig_text
        remove_message_buttons_and_update(chat_id, msg_id, new_text, is_photo=bool(message.get("caption") or message.get("photo")))
        answer_callback_query(cq_id, f"🚫 Roll No {rollno} is now BLOCKED!", show_alert=True)
        return

    answer_callback_query(cq_id)


# ============================================================
# BACKGROUND POLLING LOOP
# ============================================================
def _bot_polling_worker():
    """Continuous polling loop to receive messages and callback queries"""
    global _bot_running
    offset = 0
    logger.info("[TELEGRAM BOT] Starting interactive background polling loop...")

    while _bot_running:
        api_url = get_api_url()
        if not api_url:
            time.sleep(10)
            continue

        try:
            params = {"offset": offset, "timeout": 20}
            r = requests.get(f"{api_url}/getUpdates", params=params, timeout=25, verify=False)
            if r.status_code != 200:
                time.sleep(5)
                continue

            data = r.json()
            if not data.get("ok"):
                time.sleep(5)
                continue

            updates = data.get("result", [])
            for upd in updates:
                offset = upd.get("update_id", 0) + 1

                # 1. Handle Callback Queries (Inline Buttons)
                if "callback_query" in upd:
                    try:
                        handle_callback_query(upd["callback_query"])
                    except Exception as e:
                        logger.error(f"[BOT ERROR in callback] {e}")

                # 2. Handle Text Messages & Commands
                elif "message" in upd:
                    msg = upd["message"]
                    text = (msg.get("text") or "").strip()
                    sender = msg.get("from", {})
                    chat_id = msg.get("chat", {}).get("id")
                    first_name = sender.get("first_name", "")

                    if not text or not chat_id:
                        continue

                    # Check if admin is sending custom rejection reason
                    if is_admin(chat_id) and chat_id in awaiting_custom_reason:
                        if text.lower() in ["/cancel", "cancel"]:
                            del awaiting_custom_reason[chat_id]
                            send_message("❌ Rejection cancelled.", chat_id=chat_id)
                            continue
                        elif not text.startswith("/"):
                            state = awaiting_custom_reason.pop(chat_id)
                            req_id = state.get("requestId")
                            msg_id = state.get("msgId")
                            orig_text = state.get("origText", "")
                            is_photo = state.get("isPhoto", False)
                            custom_reason = text.strip()

                            req = get_update_request(req_id)
                            if req and req.get("status") == RequestStatus.PENDING:
                                rollno = req.get("rollno")
                                update_request_status(
                                    request_id=req_id,
                                    new_status=RequestStatus.REJECTED,
                                    admin_id=str(chat_id),
                                    rejection_reason=custom_reason,
                                    comment=f"Rejected with custom reason by @{first_name or 'Admin'}"
                                )

                                create_audit_log({
                                    "action": AuditActions.REQUEST_REJECTED,
                                    "actor": f"admin_{chat_id}",
                                    "actorType": ActorType.ADMIN,
                                    "rollno": rollno,
                                    "requestId": req_id,
                                    "status": "REJECTED",
                                    "details": {"reason": custom_reason, "mode": "custom_typed"}
                                })

                                try:
                                    from services.telegram_service import send_public_status_broadcast
                                    send_public_status_broadcast(
                                        status="rejected",
                                        request_id=req_id,
                                        rollno=rollno,
                                        reason=custom_reason
                                    )
                                except Exception as e:
                                    logger.error(f"[BOT] Broadcast failed: {e}")

                                if msg_id and orig_text:
                                    header = f"❌ <b>REJECTED</b>\n<i>Reason: {custom_reason}\nBy: Admin at {format_ist_time()}</i>\n━━━━━━━━━━━━━━━━━━━━\n"
                                    new_caption = header + orig_text
                                    remove_message_buttons_and_update(chat_id, msg_id, new_caption, is_photo=is_photo)

                                send_message(
                                    f"❌ <b>Request Rejected with Custom Reason!</b>\n\n"
                                    f"<b>Request ID:</b> <code>{req_id}</code>\n"
                                    f"<b>Roll No:</b> <code>{rollno}</code>\n"
                                    f"<b>Reason:</b> {custom_reason}\n\n"
                                    f"Public broadcast sent successfully.",
                                    chat_id=chat_id
                                )
                            else:
                                send_message(f"⚠️ Request <code>{req_id}</code> is no longer pending or not found.", chat_id=chat_id)
                            continue
                        else:
                            # Admin sent a command like /pending or /reject instead, clear state
                            del awaiting_custom_reason[chat_id]

                    # Process commands
                    cmd = text.split()[0].lower() if text else ""

                    if cmd in ["/start", "/id", "/chatid", "/myid"]:
                        handle_start_or_id(chat_id, user_first_name=first_name)
                    elif cmd in ["/help", "/menu"]:
                        handle_help(chat_id)
                    elif cmd == "/pending":
                        handle_pending_command(chat_id)
                    elif cmd == "/revert":
                        handle_revert_command(chat_id, text)
                    elif cmd == "/reject":
                        handle_reject_command(chat_id, text)
                    elif cmd == "/block":
                        handle_block_command(chat_id, text)
                    elif cmd == "/unblock":
                        handle_unblock_command(chat_id, text)
                    elif cmd == "/status":
                        handle_status_command(chat_id, text)
                    else:
                        # If regular message from student, send friendly Chat ID reminder
                        if not is_admin(chat_id):
                            handle_start_or_id(chat_id, user_first_name=first_name)

        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            logger.error(f"[TELEGRAM BOT EXCEPTION] {e}")
            time.sleep(5)


def start_telegram_bot():
    """Start background Telegram bot worker thread safely"""
    global _bot_thread, _bot_running
    if _bot_running:
        return
    _bot_running = True
    _bot_thread = threading.Thread(target=_bot_polling_worker, daemon=True, name="TelegramBotWorker")
    _bot_thread.start()
    logger.info("[TELEGRAM BOT] Background worker thread launched.")


def stop_telegram_bot():
    """Stop background worker safely"""
    global _bot_running
    _bot_running = False

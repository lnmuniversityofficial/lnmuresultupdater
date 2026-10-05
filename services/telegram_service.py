# -*- coding: utf-8 -*-
"""
Telegram Service — Bot notifications, inline keyboard action handlers, and user/admin messaging.
"""

import os
import json
import logging
import warnings
import requests
from config import Config

logger = logging.getLogger('telegram_service')
warnings.filterwarnings('ignore')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass


def get_api_url():
    token = Config.TELEGRAM_BOT_TOKEN
    return f"https://api.telegram.org/bot{token}" if token else None


def send_message(text, chat_id=None, reply_markup=None, parse_mode="HTML"):
    """Send text message via Telegram Bot API"""
    api_url = get_api_url()
    if not api_url:
        logger.info(f"[TELEGRAM SIMULATION] Message to {chat_id or Config.TELEGRAM_ADMIN_CHAT_ID}:\n{text}")
        return True

    target_chat = chat_id or Config.TELEGRAM_ADMIN_CHAT_ID
    if not target_chat:
        logger.warning("[TELEGRAM] No chat_id provided and TELEGRAM_ADMIN_CHAT_ID not configured")
        return False

    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        r = requests.post(f"{api_url}/sendMessage", json=payload, timeout=15, verify=False)
        res = r.json()
        if res.get("ok"):
            logger.info(f"[TELEGRAM] Message sent successfully to {target_chat}")
            return True
        else:
            logger.error(f"[TELEGRAM ERROR] {res}")
            return False
    except Exception as e:
        logger.error(f"[TELEGRAM EXCEPTION] {e}")
        return False


def send_photo(photo_url_or_path, caption="", chat_id=None, reply_markup=None):
    """Send photo with caption to Telegram chat"""
    api_url = get_api_url()
    target_chat = chat_id or Config.TELEGRAM_ADMIN_CHAT_ID
    if not api_url or not target_chat:
        logger.warning(f"[TELEGRAM PHOTO] Missing api_url or target_chat: chat={target_chat}")
        return False

    try:
        if str(photo_url_or_path).startswith("http://") or str(photo_url_or_path).startswith("https://"):
            payload = {
                "chat_id": target_chat,
                "photo": photo_url_or_path,
                "caption": caption,
                "parse_mode": "HTML"
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
            r = requests.post(f"{api_url}/sendPhoto", json=payload, timeout=20, verify=False)
        else:
            if not os.path.exists(photo_url_or_path):
                logger.error(f"[TELEGRAM PHOTO] Local file does not exist: {photo_url_or_path}")
                return False
            data_payload = {
                "chat_id": str(target_chat),
                "caption": caption,
                "parse_mode": "HTML"
            }
            if reply_markup:
                data_payload["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, dict) else str(reply_markup)
            with open(photo_url_or_path, 'rb') as f:
                r = requests.post(
                    f"{api_url}/sendPhoto",
                    data=data_payload,
                    files={"photo": f},
                    timeout=30,
                    verify=False
                )
        res = r.json()
        if res.get("ok"):
            logger.info(f"✅ [TELEGRAM PHOTO SUCCESS] Sent photo to {target_chat}")
            return True
        else:
            logger.error(f"[TELEGRAM PHOTO FAILED] Telegram API response: {res}")
            return False
    except Exception as e:
        logger.error(f"[TELEGRAM PHOTO EXCEPTION] {e}")
        return False


def send_admin_notification(request_details):
    """
    Format and send comprehensive new update request to Admin Private Chat with inline action buttons.
    Sends payment screenshot as photo if available, otherwise sends text message.
    ADMIN ONLY - Full details with complete roll number.
    """
    req_id = request_details.get("requestId", "N/A")
    rollno = request_details.get("rollno", "N/A")
    student_name = request_details.get("studentName") or request_details.get("student", {}).get("name", "N/A")
    amount = request_details.get("amount", Config.PAYMENT_AMOUNT)
    user_msg = request_details.get("userMessage") or "None"
    changes = request_details.get("changes", [])
    screenshot_url = request_details.get("paymentScreenshotUrl") or ""
    local_path = request_details.get("paymentScreenshotPath") or ""

    user_edits = []
    summary_edits = []

    for c in changes:
        col = str(c.get('column', '')).strip()
        old_v = c.get('before') if c.get('before') is not None else c.get('oldValue', '-')
        new_v = c.get('after') if c.get('after') is not None else (c.get('newValue') if c.get('newValue') is not None else c.get('value', '-'))
        subj = str(c.get('subject') or '').strip()
        label = str(c.get('label') or '').strip()
        is_derived = bool(c.get('isDerived', False))

        is_primary = (
            col.startswith('a1te') or col.startswith('ap1te') or col.startswith('a1pe')
        ) and not is_derived

        if is_primary:
            display_name = f"{subj} " if subj else ""
            field_type = label or ("Practical" if (col.startswith('ap1te') or col.startswith('a1pe')) else "Theory")
            user_edits.append(f"• <b>{display_name}{field_type}</b> (<code>{col}</code>): <b>{old_v}</b> ➔ <b>{new_v}</b>")
        else:
            if col in ('sgpa', 'obtgp', 'res_2', 'resremark', 'rs1') or col.startswith(('a1gr_', 'ap1gr_')):
                summary_edits.append(f"• <code>{col}</code>: {old_v} ➔ <b>{new_v}</b>")

    lines = [
        f"🚨 <b>NEW RESULT UPDATE REQUEST</b>",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"<b>Request ID:</b> <code>{req_id}</code>",
        f"<b>Roll No:</b> <code>{rollno}</code>",
        f"<b>Student:</b> {student_name}",
        f"<b>Fee Amount:</b> ₹{amount:.2f}",
        f"<b>Note:</b> {user_msg}",
        f"",
        f"📝 <b>Mark Updates ({len(user_edits) if user_edits else len(changes)}):</b>"
    ]

    if user_edits:
        lines.extend(user_edits)
    else:
        for c in changes[:10]:
            col = c.get('column', '')
            old_v = c.get('before') if c.get('before') is not None else c.get('oldValue', '-')
            new_v = c.get('after') if c.get('after') is not None else (c.get('newValue') if c.get('newValue') is not None else c.get('value', '-'))
            lines.append(f"• <code>{col}</code>: <b>{old_v}</b> ➔ <b>{new_v}</b>")

    if summary_edits:
        lines.append(f"\n📊 <b>Auto Metrics:</b>")
        lines.extend(summary_edits[:5])

    lines.append(f"\n<i>Approve or Reject via inline buttons below.</i>")
    caption = "\n".join(lines)

    # Inline action keyboard
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve_{req_id}"},
                {"text": "❌ Reject", "callback_data": f"reject_{req_id}"}
            ],
            [
                {"text": "🚫 Block User", "callback_data": f"block_{rollno}"}
            ]
        ]
    }

    # Send as photo message if screenshot URL or local path exists
    photo_target = local_path if (local_path and os.path.exists(local_path)) else screenshot_url
    sent = False
    if photo_target:
        sent = send_photo(photo_target, caption=caption, chat_id=Config.TELEGRAM_ADMIN_CHAT_ID, reply_markup=reply_markup)

    # Fallback to text message if photo was not sent or failed
    if not sent:
        sent = send_message(caption, chat_id=Config.TELEGRAM_ADMIN_CHAT_ID, reply_markup=reply_markup)

    return sent


def send_public_status_broadcast(status, request_id, rollno, reason="", admin_note=""):
    """
    Broadcast status updates to PUBLIC CHANNEL with masked roll number.
    Status: 'approved', 'rejected', 'blocked'
    NO screenshot, NO sensitive details - public transparency only.
    """
    from utils.helpers import mask_rollno, format_ist_time
    
    masked_roll = mask_rollno(rollno)
    timestamp = format_ist_time()
    
    if status == "approved":
        text = (
            f"✅ <b>RESULT UPDATED</b>\n\n"
            f"<b>Request ID:</b> <code>{request_id}</code>\n"
            f"<b>Roll No:</b> <code>{masked_roll}</code>\n"
            f"<b>Status:</b> Approved & Executed\n"
            f"<b>Time:</b> {timestamp}\n"
        )
        if admin_note:
            text += f"\n<i>{admin_note}</i>"
    
    elif status == "rejected":
        text = (
            f"❌ <b>REQUEST REJECTED</b>\n\n"
            f"<b>Request ID:</b> <code>{request_id}</code>\n"
            f"<b>Roll No:</b> <code>{masked_roll}</code>\n"
            f"<b>Reason:</b> {reason or 'Invalid submission'}\n"
            f"<b>Time:</b> {timestamp}\n"
        )
    
    elif status == "blocked":
        text = (
            f"🚫 <b>USER BLOCKED</b>\n\n"
            f"<b>Roll No:</b> <code>{masked_roll}</code>\n"
            f"<b>Reason:</b> {reason or 'Policy violation'}\n"
            f"<b>Date:</b> {timestamp}\n\n"
            f"<i>This user has been permanently blocked from submitting requests.</i>"
        )
    
    else:
        return False
    
    # Send to public channel
    public_channel = Config.TELEGRAM_PUBLIC_CHANNEL_ID
    if public_channel:
        return send_message(text, chat_id=public_channel, parse_mode="HTML")
    else:
        logger.warning("[PUBLIC BROADCAST] TELEGRAM_PUBLIC_CHANNEL_ID not configured")
        return False


def send_approval_message(chat_id, request_details):
    """Notify student that their request has been approved and updated"""
    req_id = request_details.get("requestId", "")
    rollno = request_details.get("rollno", "")
    admin_msg = request_details.get("approvalMessage") or "Your marks have been updated successfully in the official portal."

    text = (
        f"🎉 <b>Result Update Approved!</b>\n\n"
        f"Dear Student, your request <code>{req_id}</code> for Roll No <code>{rollno}</code> has been approved and executed.\n\n"
        f"<b>Admin Note:</b> {admin_msg}\n\n"
        f"You can now check your updated marksheet on the LNMU result portal.\n\n"
        f"<i>Thank you for using LNMU Result Updater!</i>"
    )
    return send_message(text, chat_id=chat_id)


def send_rejection_message(chat_id, reason, admin_comment=""):
    """Notify student that their request was rejected"""
    text = (
        f"❌ <b>Result Update Request Rejected</b>\n\n"
        f"Your result update request could not be approved.\n\n"
        f"<b>Reason:</b> {reason}\n"
    )
    if admin_comment:
        text += f"<b>Remarks:</b> {admin_comment}\n"

    text += f"\n<i>Note: A 24-hour cooldown period has been applied before you can submit a new request.</i>"
    return send_message(text, chat_id=chat_id)


def send_revert_notification(chat_id, request_id, rollno, details=""):
    """Notify user and admin of a reverted update"""
    text = (
        f"⚠️ <b>Update Reverted Notice</b>\n\n"
        f"The update previously applied for Request ID <code>{request_id}</code> (Roll No: <code>{rollno}</code>) has been reverted to original values by the administrator.\n"
    )
    if details:
        text += f"\n<b>Reason:</b> {details}\n"

    if chat_id:
        send_message(text, chat_id=chat_id)

    admin_text = f"🔄 <b>ADMIN ALERT: Update Reverted</b>\n\nRequest ID: <code>{request_id}</code>\nRoll No: <code>{rollno}</code>\nDetails: {details}"
    send_message(admin_text, chat_id=Config.TELEGRAM_ADMIN_CHAT_ID)
    return True


def send_otp_notification(otp, chat_id=None):
    """Send login OTP to Admin or User"""
    text = (
        f"🔐 <b>LNMU Updater — Login Verification OTP</b>\n\n"
        f"<b>OTP Code:</b> <code>{otp}</code>\n"
        f"<b>Validity:</b> 5 minutes\n\n"
        f"<i>Do not share this OTP with anyone.</i>"
    )
    return send_message(text, chat_id=chat_id)


def send_direct_update_notification(rollno, updates, actor="admin", request_id="N/A"):
    """Notify admin channel right before a direct per-cell DB write happens."""
    lines = "\n".join(
        f"  • <code>{u.get('column')}</code> = "
        f"{'<i>NULL</i>' if u.get('is_null') else u.get('value')}"
        for u in updates[:40]
    )
    more = f"\n  …and {len(updates) - 40} more" if len(updates) > 40 else ""

    text = (
        f"⚡ <b>DIRECT DB UPDATE — STARTING</b>\n\n"
        f"<b>Request:</b> <code>{request_id}</code>\n"
        f"<b>Roll No:</b> <code>{rollno}</code>\n"
        f"<b>Actor:</b> {actor}\n"
        f"<b>Cells:</b> {len(updates)}\n\n"
        f"<b>Updates:</b>\n{lines}{more}"
    )
    return send_message(text, chat_id=Config.TELEGRAM_ADMIN_CHAT_ID)


def send_update_result_notification(rollno, result, request_id="N/A"):
    """Notify admin channel with the per-cell execution outcome."""
    ok = result.get("success_count", 0)
    total = result.get("total", 0)
    failed = result.get("failure_count", 0)
    status = result.get("status", "UNKNOWN")

    text = (
        f"{'✅' if result.get('success') else '⚠️'} <b>DIRECT DB UPDATE — {status}</b>\n\n"
        f"<b>Request:</b> <code>{request_id}</code>\n"
        f"<b>Roll No:</b> <code>{rollno}</code>\n"
        f"<b>Updated:</b> {ok}/{total}"
    )
    if failed:
        text += f"\n<b>Failed:</b> {failed}"

    return send_message(text, chat_id=Config.TELEGRAM_ADMIN_CHAT_ID)

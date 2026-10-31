#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot — Handles requests, approvals, and broadcasts.
Now includes a listener for inline buttons and admin replies.
"""

import warnings
import requests
import json
import threading
import time
import sqlite3
from datetime import datetime

warnings.filterwarnings('ignore')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = "9031936915:AAE8ELXAZSb_cW5cP1jDpTtWBq_l4U9QFRs"  # Get from @BotFather
CHAT_ID = "6693212794"               # Your personal chat ID (where you want admin notifications)
BROADCAST_CHANNEL = "-1003925639959"  # Broadcast channel ID
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_FILE = "final.db"     # Path to the SQLite database
SECRET_TOKEN = "your_secret_token_here"  # For internal Flask routes (if needed)

# In-memory storage for mapping Telegram message IDs to request IDs (for tracking)
# In production, consider using a database or Redis.
message_to_request_map = {}
lock = threading.Lock()

# ============================================================
# HELPER FUNCTIONS
# ============================================================
def get_db_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def send_message(text, parse_mode="HTML", reply_markup=None, chat_id=None):
    """Send a text message via Telegram."""
    target_chat = chat_id or CHAT_ID
    payload = {
        "chat_id": target_chat,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup

    try:
        r = requests.post(f"{API_URL}/sendMessage", json=payload, timeout=15, verify=False)
        result = r.json()
        if result.get("ok"):
            return result.get("result", {}).get("message_id")
        else:
            print(f"[TELEGRAM ERROR] {result}")
            return None
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return None

def send_photo_with_caption(photo_path_or_url, caption="", reply_markup=None, chat_id=None):
    """Send a photo with caption via Telegram."""
    target_chat = chat_id or CHAT_ID
    data = {
        "chat_id": target_chat,
        "caption": caption,
        "parse_mode": "HTML"
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup

    try:
        if photo_path_or_url.startswith('http'):
            data["photo"] = photo_path_or_url
            r = requests.post(f"{API_URL}/sendPhoto", data=data, timeout=30, verify=False)
        else:
            with open(photo_path_or_url, 'rb') as f:
                r = requests.post(
                    f"{API_URL}/sendPhoto",
                    data=data,
                    files={"photo": f},
                    timeout=60,
                    verify=False
                )
        result = r.json()
        if result.get("ok"):
            return result.get("result", {}).get("message_id")
        else:
            print(f"[TELEGRAM PHOTO ERROR] {result}")
            return None
    except Exception as e:
        print(f"[TELEGRAM PHOTO ERROR] {e}")
        return None

def answer_callback_query(callback_query_id, text=None, show_alert=False):
    """Answer a callback query to remove the loading state."""
    payload = {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": show_alert
    }
    try:
        r = requests.post(f"{API_URL}/answerCallbackQuery", json=payload, timeout=10, verify=False)
        return r.json().get("ok", False)
    except Exception as e:
        print(f"[TELEGRAM CALLBACK ERROR] {e}")
        return False

# ============================================================
# NOTIFICATION FUNCTIONS (Updated for new flow)
# ============================================================
def notify_admin_request(request_id, rollno, semester, student_name, payment_screenshot=None, edits_json=None, user_message=None):
    """
    Notify the admin about a new request with inline buttons for Approve/Reject.
    Shows requested cell edits, user custom message, calculated fee, and payment details.
    """
    masked_roll = rollno[:5] + "****" + rollno[-3:] if len(rollno) >= 8 else rollno
    
    edits_summary = ""
    theory_count = 0
    practical_count = 0
    if edits_json:
        try:
            edits = json.loads(edits_json) if isinstance(edits_json, str) else edits_json
            if edits:
                edits_summary = "\n<b>📋 Proposed Edits:</b>\n"
                for e in edits:
                    col = e.get('column', '')
                    val = e.get('value', '')
                    if "a1te" in col:
                        theory_count += 1
                        col_type = "Theory (a1te)"
                    elif "a1pe" in col:
                        practical_count += 1
                        col_type = "Practical (a1pe)"
                    else:
                        col_type = col
                    edits_summary += f"• <code>{col}</code> ({col_type}) ➔ <b>{val}</b>\n"
        except Exception:
            pass

    calculated_amount = (theory_count * 1000) + (practical_count * 500)
    if calculated_amount <= 0:
        calculated_amount = 1000

    msg_text = user_message.strip() if user_message else "None"

    text = (
        f"🚨 <b>New Update Request #{request_id}</b>\n\n"
        f"<b>Roll No:</b> <code>{rollno}</code> (Masked: {masked_roll})\n"
        f"<b>Name:</b> {student_name or 'N/A'}\n"
        f"<b>Semester:</b> {semester}\n"
        f"<b>User Message:</b> {msg_text}\n"
        f"<b>Calculated Fee:</b> ₹{calculated_amount} (Theory: {theory_count} × ₹1000, Practical: {practical_count} × ₹500)\n"
        f"<b>Payment:</b> UPI (buypremiumcontent@ybl)\n"
        f"{edits_summary}\n"
        f"<i>Please verify payment screenshot below and choose an action.</i>"
    )

    markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve_{request_id}"},
                {"text": "❌ Reject", "callback_data": f"reject_menu_{request_id}"}
            ]
        ]
    }

    if payment_screenshot:
        msg_id = send_photo_with_caption(payment_screenshot, caption=text, reply_markup=markup)
    else:
        msg_id = send_message(text, reply_markup=markup)

    if msg_id:
        with lock:
            message_to_request_map[msg_id] = request_id
    return msg_id

def broadcast_status(rollno, status, reason=""):
    """Broadcast a status update to admin / channel (with rolled mask)."""
    masked_roll = rollno[:5] + "****" + rollno[-3:] if len(rollno) >= 8 else rollno
    
    if status == 'approved' or status == 'updated':
        emoji = "✅"
        msg = f"{emoji} <b>Result Update Approved & Updated!</b>\n\n<b>Roll No:</b> <code>{masked_roll}</code>\nYour result update request has been successfully approved and updated."
    elif status == 'pending':
        emoji = "📥"
        msg = f"{emoji} <b>New Update Request Pending</b>\n\n<b>Roll No:</b> <code>{masked_roll}</code>\nRequest submitted for admin review."
    else:
        emoji = "❌"
        msg = f"{emoji} <b>Result Update Rejected</b>\n\n<b>Roll No:</b> <code>{masked_roll}</code>\n<b>Reason:</b> {reason or 'Not specified'}\nYou can resubmit after 24 hours with valid details."

    target = BROADCAST_CHANNEL

    return send_message(msg, chat_id=target)

def notify_user_request_received(rollno):
    """Notify the user that their request has been received."""
    msg = (
        f"📥 <b>Request Received Successfully!</b>\n\n"
        f"Your result update request has been submitted for admin review along with your payment screenshot.\n\n"
        f"⚠️ <i>Note: You can submit another request for this roll number only after 24 hours if rejected.</i>"
    )
    return send_message(msg)

def notify_user_approved(rollno, semester):
    """Notify the user that their request has been approved."""
    msg = (
        f"✅ <b>Request Approved & Updated!</b>\n\n"
        f"<b>Roll No:</b> <code>{rollno}</code>\n"
        f"<b>Semester:</b> {semester}\n"
        f"<b>Status:</b> Success\n\n"
        f"📝 Your result has been successfully updated in the database."
    )
    return send_message(msg)

def notify_user_rejected(rollno, semester, reason):
    """Notify the user that their request has been rejected."""
    msg = (
        f"❌ <b>Request Rejected</b>\n\n"
        f"<b>Roll No:</b> <code>{rollno}</code>\n"
        f"<b>Semester:</b> {semester}\n\n"
        f"<b>Reason:</b> {reason}\n\n"
        f"📋 <b>Next Steps:</b>\n"
        f"• Ensure payment screenshot is clear and valid\n"
        f"• Submit new request after 24 hours\n"
        f"• Contact support if you believe this is an error\n\n"
        f"⏱️ <i>Cooldown: 24 hours before retry</i>"
    )
    return send_message(msg)

# ============================================================
# REQUEST PROCESSING
# ============================================================
def process_approved_request(request_id):
    """
    Process an approved request by executing the proposed edits via sql_runner,
    logging old/new values in audit_history, and notifying user & channel.
    """
    send_message(f"⏳ Processing approval and updating database for Request #{request_id}...")
    
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT rollno, semester, student_name, edits_json FROM request_queue WHERE id = ?", (request_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False, "Request not found"
    
    rollno = row['rollno']
    semester = row['semester']
    student_name = row['student_name']
    edits_json = row['edits_json']
    conn.close()

    updates = []
    if edits_json:
        try:
            updates = json.loads(edits_json)
        except Exception:
            pass

    if not updates:
        return False, "No edits found in request"

    from sql_runner import execute_batch_updates
    res = execute_batch_updates(updates=updates, rollno=rollno, semester=semester)
    
    if not res.get('success') and res.get('success_count', 0) == 0:
        err_msg = res.get('message', 'Unknown error')
        send_message(f"❌ <b>Update Failed for Request #{request_id}</b>\nError: {err_msg}", reply_markup={
            "inline_keyboard": [[{"text": "🔄 Retry Update", "callback_data": f"retry_{request_id}"}]]
        })
        return False, err_msg

    # Log to audit history
    conn = get_db_conn()
    cur = conn.cursor()
    now_str = datetime.now().isoformat()
    for u in updates:
        col = u.get('column')
        new_val = u.get('value')
        cur.execute(
            "INSERT INTO audit_history (request_id, rollno, semester, column_name, old_value, new_value, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (request_id, rollno, semester, col, "", str(new_val), now_str)
        )

    cur.execute(
        "UPDATE request_queue SET status = 'updated', processed_at = ?, processed_by = 'admin' WHERE id = ?",
        (now_str, request_id)
    )
    conn.commit()
    conn.close()

    notify_user_approved(rollno, semester)
    broadcast_status(rollno, 'approved')
    send_message(f"✅ <b>Request #{request_id} Successfully Approved & Updated!</b> (Roll: <code>{rollno}</code>)")

    return True, "Request approved and executed successfully"

# ============================================================
# LISTENER FOR TELEGRAM UPDATES
# ============================================================
def start_listener():
    """Start a background thread to listen for Telegram updates (callback queries and messages)."""
    def listener_loop():
        offset = 0
        while True:
            try:
                # Get updates from Telegram
                params = {
                    "offset": offset,
                    "timeout": 30,  # Long polling
                    "allowed_updates": ["callback_query", "message"]
                }
                r = requests.get(f"{API_URL}/getUpdates", params=params, timeout=35, verify=False)
                result = r.json()
                
                if not result.get("ok"):
                    err_code = result.get("error_code", 0)
                    if err_code == 409:
                        # Another instance is running — wait longer and retry
                        print(f"[TELEGRAM LISTENER] 409 Conflict — waiting 15s for other instance to die...")
                        time.sleep(15)
                    elif err_code == 401:
                        print(f"[TELEGRAM LISTENER ERROR] 401 Unauthorized — invalid token. Stopping.")
                        break
                    else:
                        print(f"[TELEGRAM LISTENER ERROR] Failed to get updates: {result}")
                        time.sleep(5)
                    continue
                
                updates = result.get("result", [])
                for upd in updates:
                    offset = upd["update_id"] + 1
                    
                    # Handle callback queries (from inline buttons)
                    if "callback_query" in upd:
                        cq = upd["callback_query"]
                        cq_id = cq["id"]
                        data = cq["data"]
                        message = cq["message"]
                        msg_id = message["message_id"]
                        from_id = cq["from"]["id"]
                        
                        # Only allow admin to act (check if from_id matches CHAT_ID)
                        if str(from_id) != CHAT_ID:
                            answer_callback_query(cq_id, "Unauthorized", show_alert=True)
                            continue
                        
                        if data.startswith("approve_"):
                            request_id = int(data.split("_")[1])
                            answer_callback_query(cq_id, "Approving...")
                            
                            # Update request status to processing
                            conn = get_db_conn()
                            cur = conn.cursor()
                            cur.execute("UPDATE request_queue SET status = 'processing' WHERE id = ?", (request_id,))
                            conn.commit()
                            conn.close()
                            
                            # Process the approval (this will fetch from LNMU and update DB)
                            success, msg = process_approved_request(request_id)
                            if not success:
                                # If processing failed, revert to pending?
                                conn = get_db_conn()
                                cur = conn.cursor()
                                cur.execute("UPDATE request_queue SET status = 'failed', admin_notes = ? WHERE id = ?", 
                                            (msg, request_id))
                                conn.commit()
                                conn.close()
                                answer_callback_query(cq_id, f"Processing failed: {msg}", show_alert=True)
                            else:
                                answer_callback_query(cq_id, "Request approved and processed!", show_alert=False)
                                
                        elif data.startswith("reject_menu_"):
                            request_id = int(data.split("_")[2])
                            answer_callback_query(cq_id, "Select rejection reason:")
                            # Send message with option buttons
                            reason_markup = {
                                "inline_keyboard": [
                                    [{"text": "❌ Payment Screenshot Blur/Invalid", "callback_data": f"rejreason_{request_id}_Invalid Payment"}],
                                    [{"text": "❌ Payment Not Received", "callback_data": f"rejreason_{request_id}_Payment Not Received"}],
                                    [{"text": "❌ Incorrect Marks / Invalid Edits", "callback_data": f"rejreason_{request_id}_Invalid Marks"}],
                                    [{"text": "✍️ Custom Reason (Type in chat)", "callback_data": f"rejcustom_{request_id}"}]
                                ]
                            }
                            send_message(f"⚠️ Select rejection reason for Request #{request_id}:", reply_markup=reason_markup)

                        elif data.startswith("rejreason_"):
                            parts = data.split("_")
                            request_id = int(parts[1])
                            reason = parts[2]
                            answer_callback_query(cq_id, f"Rejected: {reason}")
                            
                            # Execute rejection
                            conn = get_db_conn()
                            cur = conn.cursor()
                            cur.execute("UPDATE request_queue SET status = 'rejected', rejection_reason = ?, processed_at = ? WHERE id = ?", 
                                        (reason, datetime.now().isoformat(), request_id))
                            cur.execute("INSERT INTO request_history (original_request_id, rollno, semester, status, reason, action_by) "
                                        "SELECT id, rollno, semester, 'rejected', ?, 'admin' FROM request_queue WHERE id = ?", 
                                        (reason, request_id))
                            conn.commit()
                            conn.close()

                            conn = get_db_conn()
                            cur = conn.cursor()
                            cur.execute("SELECT rollno, semester FROM request_queue WHERE id = ?", (request_id,))
                            row = cur.fetchone()
                            conn.close()
                            if row:
                                notify_user_rejected(row['rollno'], row['semester'], reason)
                                broadcast_status(row['rollno'], 'rejected', reason)

                        elif data.startswith("rejcustom_"):
                            request_id = int(data.split("_")[1])
                            answer_callback_query(cq_id, "Send custom reason in chat as text.")
                            conn = get_db_conn()
                            cur = conn.cursor()
                            cur.execute("UPDATE request_queue SET status = 'awaiting_reason' WHERE id = ?", (request_id,))
                            conn.commit()
                            conn.close()
                            send_message(f"✍️ Please type your custom rejection reason for Request #{request_id} (or send /reject <reason>):")

                        elif data.startswith("retry_"):
                            request_id = int(data.split("_")[1])
                            answer_callback_query(cq_id, "Retrying update...")
                            success, msg = process_approved_request(request_id)
                            if not success:
                                send_message(f"❌ Retry failed: {msg}\nClick below to try again.", reply_markup={
                                    "inline_keyboard": [[{"text": "🔄 Retry Update", "callback_data": f"retry_{request_id}"}]]
                                })
                            else:
                                send_message(f"✅ Retry successful! Request #{request_id} updated.")
                    
                    # Handle messages (for rejection reasons and other commands)
                    elif "message" in upd:
                        msg = upd["message"]
                        msg_id = msg["message_id"]
                        chat_id = msg["chat"]["id"]
                        from_id = msg["from"]["id"]
                        text = msg.get("text", "")
                        
                        # Ignore messages not from admin
                        if str(from_id) != CHAT_ID:
                            continue
                        
                        # Check if this is a reply to a bot message or if there is a request in 'awaiting_reason' status for this admin
                        request_id = None
                        if "reply_to_message" in msg:
                            replied_msg_id = msg["reply_to_message"]["message_id"]
                            with lock:
                                request_id = message_to_request_map.get(replied_msg_id)
                        
                        if not request_id:
                            # Check DB for any request in 'awaiting_reason' status
                            conn = get_db_conn()
                            cur = conn.cursor()
                            cur.execute("SELECT id FROM request_queue WHERE status = 'awaiting_reason' ORDER BY requested_at DESC LIMIT 1")
                            row = cur.fetchone()
                            if row:
                                request_id = row['id']
                            conn.close()

                        if request_id and text.startswith("/reject "):
                            reason = text[8:].strip()  # Remove "/reject "
                            if not reason:
                                reason = "No reason provided"
                                
                                # Update request as rejected
                                conn = get_db_conn()
                                cur = conn.cursor()
                                cur.execute("UPDATE request_queue SET status = 'rejected', rejection_reason = ?, processed_at = ? WHERE id = ?", 
                                            (reason, datetime.now().isoformat(), request_id))
                                
                                # Add to history
                                cur.execute("INSERT INTO request_history (original_request_id, rollno, semester, status, reason, action_by) "
                                            "SELECT id, rollno, semester, 'rejected', ?, 'admin' FROM request_queue WHERE id = ?", 
                                            (reason, request_id))
                                
                                conn.commit()
                                conn.close()
                                
                                # Get request details to notify user and broadcast
                                conn = get_db_conn()
                                cur = conn.cursor()
                                cur.execute("SELECT rollno, semester FROM request_queue WHERE id = ?", (request_id,))
                                row = cur.fetchone()
                                if row:
                                    rollno = row['rollno']
                                    semester = row['semester']
                                    conn.close()
                                    
                                    notify_user_rejected(rollno, semester, reason)
                                    broadcast_status(rollno, 'rejected', reason)
                                    
                                    # Clean up the mapping
                                    with lock:
                                        if replied_msg_id in message_to_request_map:
                                            del message_to_request_map[replied_msg_id]
                                else:
                                    conn.close()
                                
                                # Answer the callback query if any (we already answered when setting awaiting_reason))
                                # We don't have a callback query ID here, so skip.
                            else:
                                # Not a rejection command, ignore or handle other commands
                                pass
                        
                        # Handle direct commands
                        elif text.startswith("/start") or text.startswith("/help"):
                            send_message("🤖 <b>Admin Bot Active</b>\n\nCommands:\n• /revert &lt;rollno&gt; - Revert updates for a roll number\n• Click Approve/Reject on pending requests", chat_id=chat_id)
                        elif text.startswith("/revert "):
                            rollno_to_revert = text[8:].strip()
                            if rollno_to_revert:
                                conn = get_db_conn()
                                cur = conn.cursor()
                                cur.execute("SELECT * FROM audit_history WHERE rollno = ? ORDER BY id DESC", (rollno_to_revert,))
                                history_rows = cur.fetchall()
                                conn.close()

                                if not history_rows:
                                    send_message(f"⚠️ No audit history found for roll no <code>{rollno_to_revert}</code>", chat_id=chat_id)
                                else:
                                    from sql_runner import execute_batch_updates
                                    revert_updates = []
                                    semester_val = history_rows[0]['semester']
                                    for h in history_rows:
                                        revert_updates.append({"column": h['column_name'], "value": h['old_value'] or "0"})
                                    
                                    res = execute_batch_updates(updates=revert_updates, rollno=rollno_to_revert, semester=semester_val)
                                    if res.get('success') or res.get('success_count', 0) > 0:
                                        conn = get_db_conn()
                                        cur = conn.cursor()
                                        cur.execute("DELETE FROM audit_history WHERE rollno = ?", (rollno_to_revert,))
                                        conn.commit()
                                        conn.close()

                                        send_message(f"✅ Successfully reverted changes for Roll No: <code>{rollno_to_revert}</code>", chat_id=chat_id)
                                        broadcast_status(rollno_to_revert, 'rejected', "Reverted by admin")
                                    else:
                                        send_message(f"❌ Revert failed: {res.get('message', 'Unknown error')}", chat_id=chat_id)
                            else:
                                send_message("⚠️ Usage: <code>/revert &lt;rollno&gt;</code>", chat_id=chat_id)
                
                # Sleep briefly to avoid hogging CPU
                time.sleep(1)
                
            except Exception as e:
                print(f"[TELEGRAM LISTENER ERROR] {e}")
                time.sleep(5)

    # Start the listener in a daemon thread
    thread = threading.Thread(target=listener_loop, daemon=True)
    thread.start()
    print("[TELEGRAM LISTENER] Started background listener for Telegram updates.")

# ============================================================
# INITIALIZATION
# ============================================================
def init_bot():
    """Initialize the bot: start the listener."""
    start_listener()

# If run directly, just start the listener and keep the main thread alive
if __name__ == "__main__":
    init_bot()
    print("Bot listener running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nStopping bot...")

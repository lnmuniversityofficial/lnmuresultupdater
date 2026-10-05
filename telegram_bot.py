#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot — notifications bhejta hai
"""

import warnings
import requests

warnings.filterwarnings('ignore')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# ============================================================
# HARDCODED CONFIG
# ============================================================
BOT_TOKEN = "8742898258:AAGMX5fSLAs9rEFR7ir-np-nGnyUrPHpUQk"
CHAT_ID = "6693212794"
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message(text, parse_mode="HTML"):
    """Telegram pe message bhejo"""
    try:
        r = requests.post(
            f"{API_URL}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=15,
            verify=False
        )
        result = r.json()
        if result.get("ok"):
            print(f"[TELEGRAM] Message sent ({len(text)} chars)")
            return True
        else:
            print(f"[TELEGRAM ERROR] {result}")
            return False
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False


def send_document(filepath, caption=""):
    """Telegram pe document bhejo"""
    try:
        with open(filepath, 'rb') as f:
            r = requests.post(
                f"{API_URL}/sendDocument",
                data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                files={"document": f},
                timeout=60,
                verify=False
            )
        result = r.json()
        if result.get("ok"):
            print(f"[TELEGRAM] Document sent: {filepath}")
            return True
        else:
            print(f"[TELEGRAM ERROR] {result}")
            return False
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False


def notify_otp(otp):
    """OTP notification"""
    text = (
        f"🔐 <b>LNMU Updater — Login OTP</b>\n\n"
        f"<b>OTP:</b> <code>{otp}</code>\n"
        f"<b>Valid for:</b> 5 minutes\n\n"
        f"<i>Agar aapne request nahi ki, ignore karein.</i>"
    )
    return send_message(text)


def notify_search(rollno, sno, student, subjects, summary):
    """Search hone pe notification with result"""
    lines = [
        f"🔍 <b>New Search</b>",
        f"",
        f"<b>Roll No:</b> <code>{rollno}</code>",
        f"<b>Sno:</b> <code>{sno}</code>",
        f"<b>Name:</b> {student.get('name', 'N/A')}",
        f"<b>Father:</b> {student.get('fatherName', 'N/A')}",
        f"<b>College:</b> {student.get('college', 'N/A')}",
        f"<b>Semester:</b> {student.get('semester', '2')}",
        f"",
        f"📚 <b>Subjects ({len(subjects)}):</b>",
    ]
    for s in subjects:
        lines.append(
            f"• <b>{s.get('name','')}</b> | "
            f"T:{s.get('theory',0)}+{s.get('cia',0)}={s.get('theoryTotal',0)} | "
            f"P:{s.get('practical',0)}+{s.get('ps',0)}={s.get('practicalTotal',0)} | "
            f"Grade:{s.get('theoryGrade','-')} | GP:{s.get('theoryGP',0)}"
        )
    lines.extend([
        f"",
        f"📊 <b>Summary:</b>",
        f"• <b>Total GP:</b> {summary.get('totalGradePoints',0)}",
        f"• <b>Obt Credits:</b> {summary.get('obtCredits',0)}",
        f"• <b>SGPA:</b> {summary.get('sgpa','0.00')}",
        f"• <b>Result:</b> {summary.get('resultStatus','-')}",
    ])
    return send_message("\n".join(lines))


def notify_update_before(rollno, sno, changes):
    """Update se pehle ka notification"""
    lines = [
        f"⚠️ <b>Update Initiated</b>",
        f"",
        f"<b>Roll No:</b> <code>{rollno}</code>",
        f"<b>Sno:</b> <code>{sno}</code>",
        f"<b>Changes:</b> {len(changes)} cells",
        f"",
        f"📝 <b>Updates:</b>",
    ]
    for c in changes[:30]:
        lines.append(f"• <code>{c.get('column','')}</code> = {c.get('value','')}")
    if len(changes) > 30:
        lines.append(f"... +{len(changes)-30} more")
    return send_message("\n".join(lines))


def notify_update_after(rollno, sno, result, updates):
    """Update ke baad ka notification"""
    success = result.get("success_count", 0)
    total = result.get("total", 0)
    failed = result.get("failed", [])

    emoji = "✅" if result.get("success") else "⚠️"
    lines = [
        f"{emoji} <b>Update Complete</b>",
        f"",
        f"<b>Roll No:</b> <code>{rollno}</code>",
        f"<b>Sno:</b> <code>{sno}</code>",
        f"<b>Success:</b> {success}/{total}",
        f"",
        f"📝 <b>Updated Cells:</b>",
    ]
    for u in updates[:30]:
        lines.append(f"• <code>{u.get('column','')}</code> = {u.get('value','')}")
    if len(updates) > 30:
        lines.append(f"... +{len(updates)-30} more")

    if failed:
        lines.extend([
            f"",
            f"❌ <b>Failed ({len(failed)}):</b>",
        ])
        for f_ in failed[:10]:
            lines.append(f"• <code>{f_.get('column','')}</code>: {f_.get('error','')[:60]}")

    return send_message("\n".join(lines))
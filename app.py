#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LNMU Result Updater — Server
====================================
Files:
  - app.py         : Flask server
  - sql_runner.py  : SQL execution module
  - telegram_bot.py: Telegram notifications
  - db_lookup.py   : SQLite lookup (final.db)
  - index.html     : Frontend UI
  - final.db       : SQLite database (students table)
"""

import os
import io
import base64
import sqlite3
import time
import warnings
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify, send_from_directory
import requests

from sql_runner import execute_batch_updates, preview_updates, get_subject_credit, get_student_row, BATCH_TABLE_CATALOG
from telegram_bot import (
    notify_admin_request,
    broadcast_status,
    notify_user_request_received,
    notify_user_approved,
    notify_user_rejected,
    start_listener,
    send_message,
)
from db_lookup import get_dob_by_rollno, check_db

warnings.filterwarnings('ignore')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

app = Flask(__name__, static_folder='.', static_url_path='')

DB_FILE = "final.db"
UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

UPI_ID = "buypremiumcontent@ybl"

# ============================================================
# DB HELPERS
# ============================================================
def get_db_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_tables():
    conn = get_db_conn()
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS request_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rollno TEXT NOT NULL,
            student_name TEXT,
            semester TEXT,
            requested_at TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            rejection_reason TEXT,
            payment_screenshot TEXT,
            admin_notes TEXT,
            processed_at TEXT,
            processed_by TEXT,
            user_message TEXT,
            edits_json TEXT
        );
        CREATE TABLE IF NOT EXISTS request_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_request_id INTEGER,
            rollno TEXT,
            semester TEXT,
            status TEXT,
            reason TEXT,
            action_by TEXT,
            acted_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS audit_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER,
            rollno TEXT,
            semester TEXT,
            column_name TEXT,
            old_value TEXT,
            new_value TEXT,
            updated_at TEXT
        );
    """)
    conn.commit()
    conn.close()

# ============================================================
# DYNAMIC BATCH & UPSTREAM CONFIG (2327, 2428, 2529)
# ============================================================
BATCH_CONFIG = {
    '23': {
        'name': '2023-27',
        'summary_url': 'https://res.lnmuniversity.com/api/ResultApi/GetResultSummary2327',
        'summary_url_fallback': 'https://lnmuniversity.com/ugentrance23/photo/Result_CBCS_23_27.aspx/GetResultSummary2327',
        'full_url': 'https://res.lnmuniversity.com/api/ResultApi/GetFullResult2327',
        'full_url_fallback': 'https://lnmuniversity.com/ugentrance23/photo/Result_CBCS_23_27.aspx/GetFullResult2327',
        'student_in_summary': True,
    },
    '24': {
        'name': '2024-28',
        'summary_url': 'https://res.lnmuniversity.com/api/ResultApi/GetResultSummary2428',
        'summary_url_fallback': 'https://lnmuniversity.com/ugentrance23/photo/Result_CBCS_24_28.aspx/GetResultSummary2428',
        'full_url': 'https://res.lnmuniversity.com/api/ResultApi/GetFullResult2428',
        'full_url_fallback': 'https://lnmuniversity.com/ugentrance23/photo/Result_CBCS_24_28.aspx/GetFullResult2428',
        'student_in_summary': True,
    },
    '25': {
        'name': '2025-29',
        'summary_url': 'https://res.lnmuniversity.com/api/ResultApi/GetSemesters2529',
        'summary_url_fallback': 'https://res.lnmuniversity.com/api/ResultApi/GetResultSummary_Live',
        'full_url': 'https://res.lnmuniversity.com/api/ResultApi/GetFullResult2529',
        'full_url_fallback': 'https://res.lnmuniversity.com/api/ResultApi/GetFullResult_Live',
        'student_in_summary': False,
    }
}

def detect_batch(rollno):
    """Roll number prefix se batch identify karo (23 -> 2327, 24 -> 2428, 25 -> 2529)"""
    r = str(rollno).strip()
    if len(r) >= 2 and r[:2] in BATCH_CONFIG:
        return r[:2]
    return '25'

# Subject-credit lookup cache (batch, sem, subject_code, kind) -> (value_or_None, timestamp)
# Taaki har semester-switch pe dobara poora table-scan na karna pade
_CREDIT_CACHE = {}
_CREDIT_CACHE_TTL = 1800  # 30 min — credit values practically kabhi nahi badalte

IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_now():
    return datetime.now(IST)


# ============================================================
# AUTH ROUTES (removed — open system)
# ============================================================
@app.route('/api/check-auth', methods=['GET'])
def api_check_auth():
    return jsonify({'authenticated': True})


# ============================================================
# REQUEST QUEUE ROUTES
# ============================================================
@app.route('/api/generate-qr', methods=['POST'])
def generate_qr():
    """Generate UPI QR code for payment based on dynamic pricing (a1te_n = 1000, a1pe_n = 500)"""
    try:
        data = request.get_json(silent=True) or {}
        updates = data.get('updates', [])
        
        from edit_rules import plan_for_request
        rollno = str(data.get('rollno') or '').strip()
        semester = str(data.get('semester') or '2').strip()
        plan = plan_for_request(rollno, semester, updates) if rollno else None
        if plan is None:
            return jsonify({'success': False, 'error': 'rollno/semester nahi bheja ya DB row nahi mili'}), 400
        amount = plan['fee']
        theory_count = sum(1 for c in plan['fee_cells'] if c.startswith('a1te_'))
        practical_count = sum(1 for c in plan['fee_cells'] if c.startswith('a1pe_'))
        if amount <= 0:
            return jsonify({'success': False, 'error': 'Koi chargeable change nahi hai (fee 0)',
                            'blocked': plan['blocked'], 'unchanged': len(plan['unchanged'])}), 400

        # QR ab nahi banta (payment step hata diya). Sirf calculated fee bhejte hain.
        return jsonify({
            'success': True,
            'qr_base64': None,
            'upi_id': UPI_ID,
            'amount': amount,
            'theory_count': theory_count,
            'practical_count': practical_count,
            'blocked': plan['blocked'],
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/request-update', methods=['POST'])
def request_update():
    """User submits update request with payment screenshot and edits"""
    rollno = request.form.get('rollno', '').strip()
    semester = request.form.get('semester', '').strip()
    student_name = request.form.get('student_name', '').strip()
    user_message = request.form.get('message', '').strip()
    updates_json = request.form.get('updates', '[]').strip()

    if not rollno:
        return jsonify({'success': False, 'error': 'Roll No khali hai'}), 400
    if not semester:
        return jsonify({'success': False, 'error': 'Semester select karo'}), 400

    # 24-hour cooldown check
    conn = get_db_conn()
    cur = conn.cursor()
    cutoff = (get_ist_now() - timedelta(hours=24)).isoformat()
    cur.execute(
        "SELECT id, status, requested_at FROM request_queue "
        "WHERE rollno = ? AND requested_at > ? AND status NOT IN ('approved', 'updated') "
        "ORDER BY requested_at DESC LIMIT 1",
        (rollno, cutoff)
    )
    recent = cur.fetchone()
    if recent:
        conn.close()
        status = recent['status']
        if status == 'rejected':
            return jsonify({
                'success': False,
                'error': 'Is roll number ki request 24 ghante mein reject hui hai. Kal try karo.'
            }), 429
        elif status in ('pending', 'processing', 'awaiting_reason'):
            return jsonify({
                'success': False,
                'error': 'Is roll number ki request already pending hai. Admin ke response ka wait karo.'
            }), 429

    # Payment screenshot save
    screenshot_path = None
    if 'screenshot' in request.files:
        f = request.files['screenshot']
        if f and f.filename:
            ext = os.path.splitext(f.filename)[1] or '.jpg'
            fname = f"pay_{rollno}_{int(time.time())}{ext}"
            screenshot_path = os.path.join(UPLOADS_DIR, fname)
            f.save(screenshot_path)
    
    # Payment screenshot ab optional hai (payment step hata diya gaya hai)

    # Insert request into queue with edits_json
    now_ist = get_ist_now().isoformat()
    cur.execute(
        "INSERT INTO request_queue (rollno, student_name, semester, requested_at, status, payment_screenshot, user_message, edits_json) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
        (rollno, student_name, semester, now_ist, screenshot_path, user_message, updates_json)
    )
    req_id = cur.lastrowid
    conn.commit()
    conn.close()

    print(f"[REQUEST] New request #{req_id} | Roll: {rollno} | Sem: {semester}")

    # Telegram admin notification (with screenshot and edits)
    try:
        notify_admin_request(req_id, rollno, semester, student_name, screenshot_path, updates_json, user_message)
    except Exception as e:
        print(f"[TELEGRAM] Admin notify fail: {e}")

    # Broadcast channel: new request (rollno masked)
    try:
        masked = rollno[:5] + "****" + rollno[-3:] if len(rollno) >= 8 else rollno
        broadcast_status(rollno, 'pending')
    except Exception as e:
        print(f"[TELEGRAM] Broadcast fail: {e}")

    return jsonify({
        'success': True,
        'request_id': req_id,
        'message': 'Request submit ho gayi! Admin review karega. Status ke liye channel join karo.'
    })


@app.route('/api/admin/process-request', methods=['POST'])
def admin_process_request():
    """Admin approves or rejects a request (internal API called by bot or admin panel)"""
    # Simple secret token check for internal use
    auth = request.headers.get('X-Admin-Token', '')
    if auth != os.environ.get('ADMIN_SECRET', 'lnmu_admin_secret_2024'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    data = request.get_json(silent=True) or {}
    req_id = data.get('request_id')
    action = data.get('action', '').lower()  # 'approve' or 'reject'
    reason = data.get('reason', '').strip()

    if not req_id or action not in ('approve', 'reject'):
        return jsonify({'success': False, 'error': 'request_id aur action chahiye'}), 400

    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM request_queue WHERE id = ?", (req_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'Request nahi mila'}), 404

    rollno = row['rollno']
    semester = row['semester']
    student_name = row['student_name'] or ''

    if action == 'approve':
        cur.execute(
            "UPDATE request_queue SET status = 'approved', processed_at = ?, processed_by = 'admin' WHERE id = ?",
            (get_ist_now().isoformat(), req_id)
        )
        cur.execute(
            "INSERT INTO request_history (original_request_id, rollno, semester, status, reason, action_by) VALUES (?,?,?,'approved','',?)",
            (req_id, rollno, semester, 'admin')
        )
        cur.execute(
            "INSERT INTO audit_log (action, rollno, details, performed_by) VALUES ('approve', ?, ?, 'admin')",
            (rollno, f"Request #{req_id} approved")
        )
        conn.commit()
        conn.close()

        try:
            notify_user_approved(rollno, semester)
        except Exception as e:
            print(f"[TELEGRAM] User approve notify fail: {e}")
        try:
            broadcast_status(rollno, 'approved')
        except Exception as e:
            print(f"[TELEGRAM] Broadcast approve fail: {e}")

        return jsonify({'success': True, 'message': f'Request #{req_id} approved'})

    else:  # reject
        if not reason:
            conn.close()
            return jsonify({'success': False, 'error': 'Rejection reason dena zaroori hai'}), 400
        cur.execute(
            "UPDATE request_queue SET status = 'rejected', rejection_reason = ?, processed_at = ?, processed_by = 'admin' WHERE id = ?",
            (reason, get_ist_now().isoformat(), req_id)
        )
        cur.execute(
            "INSERT INTO request_history (original_request_id, rollno, semester, status, reason, action_by) VALUES (?,?,?,'rejected',?,?)",
            (req_id, rollno, semester, reason, 'admin')
        )
        cur.execute(
            "INSERT INTO audit_log (action, rollno, details, performed_by) VALUES ('reject', ?, ?, 'admin')",
            (rollno, f"Request #{req_id} rejected: {reason}")
        )
        conn.commit()
        conn.close()

        try:
            notify_user_rejected(rollno, semester, reason)
        except Exception as e:
            print(f"[TELEGRAM] User reject notify fail: {e}")
        try:
            broadcast_status(rollno, 'rejected', reason)
        except Exception as e:
            print(f"[TELEGRAM] Broadcast reject fail: {e}")

        return jsonify({'success': True, 'message': f'Request #{req_id} rejected'})


@app.route('/api/request-status', methods=['GET'])
def request_status():
    """Check status of a request by rollno"""
    rollno = request.args.get('rollno', '').strip()
    if not rollno:
        return jsonify({'success': False, 'error': 'rollno chahiye'}), 400
    
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, status, rejection_reason, requested_at, processed_at FROM request_queue "
        "WHERE rollno = ? ORDER BY requested_at DESC LIMIT 1",
        (rollno,)
    )
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return jsonify({'success': True, 'status': None, 'message': 'Koi request nahi mili'})
    
    return jsonify({
        'success': True,
        'request_id': row['id'],
        'status': row['status'],
        'rejection_reason': row['rejection_reason'],
        'requested_at': row['requested_at'],
        'processed_at': row['processed_at']
    })


# ============================================================
# LNMU HEADERS & UPSTREAM CALLS
# ============================================================
LNMU_HEADERS = {
    'accept': '*/*',
    'accept-encoding': 'gzip, deflate, br',
    'accept-language': 'en,en-US;q=0.9,hi;q=0.8,gu;q=0.7,ne;q=0.6',
    'connection': 'keep-alive',
    'host': 'res.lnmuniversity.com',
    'referer': 'https://res.lnmuniversity.com/Result/searchsemresultlive',
    'sec-ch-ua': '"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36',
    'x-requested-with': 'XMLHttpRequest',
}

BASE_HEADERS = {
    'Accept-Language': 'en,en-US;q=0.9,hi;q=0.8,gu;q=0.7,ne;q=0.6',
    'Cache-Control': 'max-age=0',
    'Connection': 'keep-alive',
    'Host': 'res.lnmuniversity.com',
    'Referer': 'https://res.lnmuniversity.com/Result/SearchSemResultLive',
    'sec-ch-ua': '"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36',
}

JSON_HEADERS = {
    **BASE_HEADERS,
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Encoding': 'gzip, deflate, br',
    'X-Requested-With': 'XMLHttpRequest',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
}


def fetch_full_result_direct(sno, roll_no, dob, batch_key=None, semester=None):
    """Directly fetch full result from upstream LNMU endpoint"""
    if not batch_key:
        batch_key = detect_batch(roll_no)
    cfg = BATCH_CONFIG.get(batch_key, BATCH_CONFIG['25'])

    urls_to_try = []
    
    # Special handling for batch 24: its batch-specific endpoint is broken
    # Use generic GetFullResult with sourceTable parameter instead
    if batch_key == '24':
        # For batch 24, we need sourceTable and semester
        if semester:
            urls_to_try.append(f"https://res.lnmuniversity.com/api/ResultApi/GetFullResult?sno={sno}&rollNo={roll_no}&sourceTable=2428&semester={semester}")
        # Also try without semester (though it usually fails)
        urls_to_try.append(f"https://res.lnmuniversity.com/api/ResultApi/GetFullResult?sno={sno}&rollNo={roll_no}&sourceTable=2428")
        if cfg.get('full_url_fallback'):
            urls_to_try.append(f"{cfg['full_url_fallback']}?sno={sno}&rollNo={roll_no}")
    else:
        # Standard approach for batches 23 and 25
        urls_to_try.append(f"{cfg['full_url']}?sno={sno}&rollNo={roll_no}")
        if cfg.get('full_url_fallback'):
            urls_to_try.append(f"{cfg['full_url_fallback']}?sno={sno}&rollNo={roll_no}")

    for url in urls_to_try:
        try:
            print(f"[FULL RESULT] Fetching: {url}")
            r = requests.get(url, headers=JSON_HEADERS, timeout=30, verify=False)
            if r.status_code == 200:
                data = r.json()
                if data.get('success'):
                    return data
        except Exception as e:
            print(f"[FULL RESULT ERR] {url} -> {e}")

    return {"success": False, "message": "Failed to fetch full result"}


def fetch_batch_summary(rollno, dob):
    """LNMU se batch ke mutabiq student summary fetch karo"""
    batch_key = detect_batch(rollno)
    cfg = BATCH_CONFIG.get(batch_key, BATCH_CONFIG['25'])

    urls_to_try = [cfg['summary_url']]
    if cfg.get('summary_url_fallback'):
        urls_to_try.append(cfg['summary_url_fallback'])

    last_err = ""
    summary_data = None

    for url in urls_to_try:
        try:
            r = requests.get(
                url,
                params={'rollno': rollno, 'dob': dob},
                headers=LNMU_HEADERS,
                timeout=15,
                verify=False
            )
            print(f"[LNMU SUMMARY] {url} -> HTTP {r.status_code}")
            if r.status_code == 200:
                res = r.json()
                if res.get('success'):
                    summary_data = res
                    break
                else:
                    last_err = res.get('message', 'Unsuccessful summary response')
            else:
                last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
            print(f"[LNMU SUMMARY ERR] {url} -> {e}")

    if not summary_data:
        return {"success": False, "message": last_err or "Failed to fetch summary"}

    student_info = summary_data.get('student') or {}
    semesters = summary_data.get('semesters') or []

    # If student info is empty and semesters exist, enrich from first semester
    if not student_info.get('name') and semesters:
        first_sno = semesters[0].get('sno')
        if first_sno:
            try:
                full_res = fetch_full_result_direct(first_sno, rollno, dob, batch_key, semesters[0].get('semester'))
                if full_res.get('success') and full_res.get('student'):
                    student_info = full_res.get('student')
            except Exception as e:
                print(f"[STUDENT ENRICH ERR] {e}")
    
    # If still no student info, fill from summary data
    if not student_info.get('name') and semesters:
        student_info = {
            'name': '',
            'fatherName': '',
            'motherName': '',
            'rollNo': rollno,
            'dob': dob,
            'abcId': '',
            'collegeName': '',
            'stream': '',
            'category': '',
            'batch': BATCH_CONFIG.get(batch_key, {}).get('name', ''),
            'entry': 'REGULAR'
        }

    return {
        "success": True,
        "batch": batch_key,
        "batch_name": cfg["name"],
        "student": student_info,
        "semesters": semesters,
        "raw": summary_data
    }


# ============================================================
# HELPERS
# ============================================================
def safe_int(v, default=0):
    try:
        if v is None or v == '':
            return default
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return default


def safe_str(v, default=''):
    if v is None:
        return default
    return str(v).strip()


def get_grade(total):
    if total >= 90: return {'letter': 'O',  'num': 10}
    if total >= 80: return {'letter': 'A+', 'num': 9}
    if total >= 70: return {'letter': 'A',  'num': 8}
    if total >= 60: return {'letter': 'B+', 'num': 7}
    if total >= 55: return {'letter': 'B',  'num': 6}
    if total >= 50: return {'letter': 'C',  'num': 5}
    if total >= 45: return {'letter': 'P',  'num': 4}
    return {'letter': 'F', 'num': 0}


def convert_lnmu_json(data, semester_num=None, rollno=None, batch=None):
    result = {'student': {}, 'subjects': [], 'summary': {}, 'pubDate': ''}

    s = data.get('student') or {}
    stream = safe_str(s.get('stream') or 'Science')
    sem_val = semester_num or safe_str(s.get('semester') or '2')

    def _correct_credit(subject_code, kind):
        """
        Subject-code se sahi credit dhundo — student ke rollno se match karne ki
        zaroorat nahi (credit sabke liye same hota hai), isliye seedha batch ke
        tables pe query chalao aur PEHLA valid result milte hi ruk jao.
        Result cache hota hai taaki semester switch karne par dobara scan na ho.
        """
        if not subject_code or not batch:
            return None

        cache_key = (batch, sem_val, subject_code, kind)
        cached = _CREDIT_CACHE.get(cache_key)
        if cached and (time.time() - cached[1]) < _CREDIT_CACHE_TTL:
            return cached[0]

        tables = BATCH_TABLE_CATALOG.get(batch, [])
        found = None
        for t in tables:
            try:
                val = get_subject_credit(t["db"], t["schema"], t["table"], sem_val, subject_code, kind, timeout=8)
            except Exception as e:
                print(f"[CREDIT-FIX] lookup error {t['table']}: {e}")
                val = None
            if val:
                found = val
                break  # pehla milte hi ruk jao — poore batch ko scan karne ki zaroorat nahi

        _CREDIT_CACHE[cache_key] = (found, time.time())
        return found

    result['student'] = {
        'name': safe_str(s.get('name')),
        'fatherName': safe_str(s.get('fatherName')),
        'motherName': safe_str(s.get('motherName')),
        'rollNo': safe_str(s.get('rollNo')),
        'dob': safe_str(s.get('dob')),
        'abcId': safe_str(s.get('abcId')),
        'college': safe_str(s.get('collegeName')),
        'studentType': safe_str(s.get('entry') or 'REGULAR'),
        'stream': stream,
        'category': safe_str(s.get('category')),
        'batch': safe_str(s.get('batch')),
        'courseDisplay': f'Bachelor of {stream} CBCS',
        'semester': sem_val,
    }

    # Student ki apni DB row (ek baar) — code, credit, sgpa/result/cop yahin se
    _own = {'row': None, 'loaded': False}
    def _own_row():
        if not _own['loaded']:
            _own['loaded'] = True
            if rollno:
                for t in BATCH_TABLE_CATALOG.get(batch, []):
                    r = get_student_row(t["db"], t["schema"], t["table"], rollno, sem_val)
                    if r:
                        _own['row'] = r
                        print(f"[OWN-ROW] roll={rollno} sem={sem_val} table={t['table']} keys={list(r.keys())}")
                        break
        return _own['row'] or {}

    def _own_val(col, slot):
        r = _own_row()
        for k, v in r.items():
            if str(k).lower() == f"{col}_{slot}":
                try:
                    return int(float(v)) if v is not None and str(v).strip() != '' else 0
                except (TypeError, ValueError):
                    return 0
        return 0

    _row_cache = {}

    def _db_row(rl):
        """Roll ki sem ki DB row (cache ke saath). Nahi mili to {}."""
        if not rl:
            return {}
        if rl not in _row_cache:
            found = {}
            for t in BATCH_TABLE_CATALOG.get(batch, []):
                r = get_student_row(t["db"], t["schema"], t["table"], rl, sem_val)
                if r:
                    found = {str(k).lower(): v for k, v in r.items()}
                    break
            _row_cache[rl] = found
        return _row_cache[rl]

    def _subject_name_fallback(slot):
        """Official subTitle khaali ho to naam DB se:
        1) apni row ka a1id_<slot>name (agar subject code bhi wahi hai)
        2) agle roll (roll+1) ki row mein SAME subject code wali slot ka naam.
        Slot number se nahi, subject CODE se match hota hai."""
        own = _db_row(rollno)
        code = str(own.get(f"a1id_{slot}") or "").strip()
        if not code:
            return ""
        own_name = own.get(f"a1id_{slot}name")
        if own_name not in (None, ""):
            return str(own_name).strip()
        if rollno and str(rollno).isdigit():
            nxt = _db_row(str(int(rollno) + 1))
            for m in range(1, 7):
                if str(nxt.get(f"a1id_{m}") or "").strip() == code:
                    nm = nxt.get(f"a1id_{m}name")
                    if nm not in (None, ""):
                        return str(nm).strip()
                    break
        return ""

    def _own_code(slot):
        r = _own_row()
        for k, v in r.items():
            if str(k).lower() == f"a1id_{slot}" and v is not None:
                return str(v).strip()
        return ""

    _slot = 0
    for sub in (data.get('subjects') or []):
        if not isinstance(sub, dict):
            continue
        sub_type = safe_str(sub.get('subType'))
        sub_title = safe_str(sub.get('subTitle'))
        _slot_guess = _slot + 1  # _slot ka increment neeche hota hai
        if not sub_title:
            sub_title = _subject_name_fallback(_slot_guess)
            if sub_title:
                print(f"[NAME-FALLBACK] roll={rollno} slot={_slot_guess} -> {sub_title}")
        full_name = f"{sub_type} - {sub_title}" if sub_type else sub_title

        max_te = safe_int(sub.get('maxTE')) or 70
        max_ts = safe_int(sub.get('maxTS')) or 30
        max_pe = safe_int(sub.get('maxPE')) or 0
        max_ps = safe_int(sub.get('maxPS')) or 0

        te = safe_int(sub.get('teMarks'))
        ts = safe_int(sub.get('tsMarks'))
        pe = safe_int(sub.get('peMarks'))
        ps = safe_int(sub.get('psMarks'))

        credits = safe_int(sub.get('credits'))
        prac_credits = safe_int(sub.get('pracCredits'))
        _slot += 1
        subject_code = safe_str(sub.get('a1id'))
        if not subject_code and rollno:
            subject_code = _own_code(_slot)
            if not subject_code:
                print(f"[CODE-FIX] code nahi mila roll={rollno} slot={_slot} keys={list(sub.keys())}")

        # Pehle student ki APNI DB row ka credit (dusre roll no se nahi)
        if credits <= 0 and rollno:
            credits = _own_val('a1cr', _slot)
        if (max_pe > 0 or max_ps > 0) and prac_credits <= 0 and rollno:
            prac_credits = _own_val('ap1cr', _slot)

        # Missing/0 credit: pehle isi SUBJECT CODE ka asli credit dhundo
        # (kisi bhi dusre student ke row se, exact match), tabhi generic guess lagao
        if credits <= 0:
            fixed = _correct_credit(subject_code, 'theory')
            if fixed:
                credits = fixed
            else:
                # Last-resort generic default agar lookup bhi fail ho jaaye
                if 'mjc' in sub_type.lower(): credits = 6
                elif 'mic' in sub_type.lower(): credits = 4
                elif 'idc' in sub_type.lower(): credits = 3
                elif 'aec' in sub_type.lower(): credits = 2
                elif 'sec' in sub_type.lower(): credits = 3
                elif 'vac' in sub_type.lower(): credits = 3
                else: credits = 4

        if (max_pe > 0 or max_ps > 0) and prac_credits <= 0:
            fixed_p = _correct_credit(subject_code, 'practical')
            # Agar practical lookup theory credit ke barabar value de raha hai,
            # to yeh galat column/value hai (theory ka hi copy) — ignore karo,
            # standard practical default (2) use karo.
            if fixed_p and fixed_p != credits:
                prac_credits = fixed_p
            else:
                if fixed_p and fixed_p == credits:
                    print(f"[CREDIT-FIX] WARNING: subject {subject_code} — practical credit lookup theory ({credits}) jaisa hi aaya, "
                          f"ap1cr_{sem_val} column galat lag raha hai, default 1 use kar rahe hain")
                prac_credits = 1

        theory_total = te + ts
        practical_total = pe + ps
        g_theory = get_grade(theory_total)
        g_prac = get_grade(practical_total)

        theory_gp = g_theory['num'] * credits
        prac_gp = g_prac['num'] * prac_credits if (max_pe > 0 or max_ps > 0) else 0

        result['subjects'].append({
            'name': full_name,
            'code': subject_code,
            'theoryFM': max_te, 'theory': te,
            'ciaFM': max_ts, 'cia': ts,
            'pracFM': max_pe, 'practical': pe,
            'psFM': max_ps, 'ps': ps,
            'theoryCredit': credits,
            'practicalCredit': prac_credits,
            'hasPractical': (max_pe > 0 or max_ps > 0),
            'theoryTotal': theory_total,
            'practicalTotal': practical_total,
            'theoryGrade': g_theory['letter'],
            'theoryNum': g_theory['num'],
            'theoryGP': theory_gp,
            'practicalGrade': g_prac['letter'],
            'practicalNum': g_prac['num'],
            'practicalGP': prac_gp,
        })

    sm = data.get('summary') or {}
    total_gp = sum(s['theoryGP'] + s['practicalGP'] for s in result['subjects'])
    raw_total_credits = sum(s['theoryCredit'] + s['practicalCredit'] for s in result['subjects'])
    total_credits = min(raw_total_credits, 20)  # semester ka total credit kabhi 20 se zyada nahi hota
    if raw_total_credits > 20:
        print(f"[CREDIT-FIX] WARNING: roll={rollno} sem={sem_val} total credits {raw_total_credits} > 20, "
              f"20 par cap kiya — subject credits manually check karo")
    sgpa = round(total_gp / total_credits, 2) if total_credits > 0 else 0

    real_result = safe_str(sm.get('resultStatus') or sm.get('remarks') or sm.get('result'))
    real_cop = safe_str(sm.get('carryOver') or sm.get('cop') or sm.get('carryOverPapers'))

    db_cols = {}
    db_vals = {}
    _r = _own_row() if rollno else {}
    for k, v in _r.items():
        kl = str(k).lower()
        if 'sgpa' in kl and 'sgpa' not in db_cols:
            db_cols['sgpa'] = k
        elif kl in ('resremark', f'res_{sem_val}', 'res', 'rs1', 'result') and ('result' not in db_cols or kl == 'resremark'):
            db_cols['result'] = k
        elif kl == 'cop' or kl == f'cop_{sem_val}':
            db_cols['cop'] = k
    for name, col in db_cols.items():
        v = _r.get(col)
        db_vals[name] = '' if v is None else str(v).strip()
    calc_sgpa = f"{sgpa:.2f}"
    if 'sgpa' in db_vals and db_vals['sgpa']:
        try:
            calc_sgpa = f"{float(db_vals['sgpa']):.2f}"
        except ValueError:
            pass
    if 'result' in db_vals and db_vals['result']:
        real_result = db_vals['result']
    if 'cop' in db_cols:
        real_cop = db_vals.get('cop', '')
    print(f"[DB-SUMMARY] roll={rollno} cols={db_cols} vals={db_vals}")

    result['summary'] = {
        'totalGradePoints': total_gp,
        'obtCredits': total_credits,
        'sgpa': calc_sgpa,
        'dbColumns': db_cols,
        'hasDbSummary': bool(db_vals),
        'resultStatus': real_result,
        'carryOver': real_cop,
    }
    result['pubDate'] = safe_str(sm.get('resultdate'))

    return result





# ============================================================
# MAIN ROUTES
# ============================================================
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/api/lookup', methods=['POST'])
def lookup_rollno():
    """
    Roll no → final.db se DOB → LNMU multi-batch summary → candidate info + semesters list
    Checks 24-hour active request cooldown first.
    """
    data = request.get_json(silent=True) or {}
    rollno = data.get('rollno', '').strip()

    if not rollno:
        return jsonify({'success': False, 'error': 'Roll No khali hai'}), 400

    print(f"\n[LOOKUP] Roll: {rollno}")

    # Check 24-hour cooldown / active pending request
    conn = get_db_conn()
    cur = conn.cursor()
    cutoff = (get_ist_now() - timedelta(hours=24)).isoformat()
    cur.execute(
        "SELECT id, status, requested_at FROM request_queue "
        "WHERE rollno = ? AND requested_at > ? AND status NOT IN ('approved', 'updated') "
        "ORDER BY requested_at DESC LIMIT 1",
        (rollno, cutoff)
    )
    active_req = cur.fetchone()
    conn.close()

    if active_req:
        status = active_req['status']
        req_time = active_req['requested_at']
        msg = f"❌ Is Roll Number par pichle 24 ghante ke andar request pending ya rejected hai ({status} at {req_time}). Aap dobara search ya submit nahi kar sakte."
        print(f"[LOOKUP COOLDOWN] Roll {rollno} blocked due to active request ({status})")
        return jsonify({'success': False, 'error': msg}), 400

    # Step 1: final.db se DOB nikal
    t0 = time.time()
    db_result = get_dob_by_rollno(rollno)
    lookup_time = (time.time() - t0) * 1000
    print(f"[LOOKUP DB] {lookup_time:.2f}ms | Found: {db_result.get('found')}")

    if not db_result.get('found'):
        return jsonify({
            'success': False,
            'error': db_result.get('error', 'Roll No DB mein nahi mila')
        }), 404

    dob = db_result['dob']
    student_name = db_result.get('name', '')
    print(f"[LOOKUP] DOB from DB: {dob}")

    # Step 2: LNMU Dynamic Batch Summary
    t0 = time.time()
    summary_resp = fetch_batch_summary(rollno, dob)
    api_time = (time.time() - t0) * 1000
    print(f"[LOOKUP API] {api_time:.2f}ms | Success: {summary_resp.get('success')}")

    if not summary_resp.get('success'):
        return jsonify({
            'success': False,
            'error': summary_resp.get('message') or 'LNMU summary API fail'
        }), 404

    stu = summary_resp.get('student') or {}
    if not stu.get('name') and student_name:
        stu['name'] = student_name
    student_name = safe_str(stu.get('name')) or student_name

    semesters = summary_resp.get('semesters') or []
    if not semesters:
        return jsonify({
            'success': False,
            'error': 'LNMU response mein koi semester nahi mila'
        }), 404

    batch_key = summary_resp.get('batch', detect_batch(rollno))
    batch_name = summary_resp.get('batch_name', BATCH_CONFIG.get(batch_key, {}).get('name', ''))

    # Default to latest or primary semester
    default_sem = semesters[-1] if semesters else {}
    sno = safe_str(default_sem.get('sno'))

    return jsonify({
        'success': True,
        'sno': sno,
        'rollno': rollno,
        'dob': dob,
        'name': student_name,
        'batch': batch_key,
        'batch_name': batch_name,
        'student': stu,
        'semesters': semesters,
        'selected_semester': default_sem.get('semester', '2'),
        'summary': default_sem,
        'lookup_ms': round(lookup_time, 2),
        'api_ms': round(api_time, 2),
    })


@app.route('/api/fetch')
def fetch_result():
    """sno + rollNo + sem + batch → LNMU result"""
    sno = request.args.get('sno', '').strip()
    roll_no = request.args.get('rollNo', '').strip()
    dob = request.args.get('dob', '').strip()
    sem = request.args.get('sem', '').strip()
    batch = request.args.get('batch', '').strip()

    if not (sno and roll_no):
        return jsonify({'success': False, 'error': 'sno aur rollNo chahiye'}), 400

    if not batch:
        batch = detect_batch(roll_no)

    try:
        full_res = fetch_full_result_direct(sno, roll_no, dob, batch, sem)
        if full_res.get('success'):
            # If full result lacks student info, get it from summary
            if not full_res.get('student') or not full_res.get('student').get('name'):
                summary_res = fetch_batch_summary(roll_no, dob)
                if summary_res.get('success'):
                    # Merge student info from summary
                    if not full_res.get('student'):
                        full_res['student'] = {}
                    full_res['student'].update(summary_res.get('student', {}))
                    # Also add semesters if needed for context
                    if 'semesters' not in full_res:
                        full_res['semesters'] = summary_res.get('semesters', [])
            parsed = convert_lnmu_json(full_res, semester_num=sem, rollno=roll_no, batch=batch)
            if parsed['subjects']:
                parsed['meta'] = {
                    'sno': sno,
                    'rollno': roll_no,
                    'sem': sem or parsed['student'].get('semester', '2'),
                    'dob': dob,
                    'batch': batch,
                    'batch_name': BATCH_CONFIG.get(batch, {}).get('name', '')
                }

                try:
                    pass  # search logging removed
                except Exception as e:
                    print(f"[TELEGRAM] Search notify fail: {e}")

                return jsonify({'success': True, 'data': parsed})
            else:
                return jsonify({'success': False, 'error': 'No subjects found in result'}), 404

        return jsonify({'success': False, 'error': full_res.get('message', 'Data fetch fail')}), 404

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/preview-updates', methods=['POST'])
def preview_updates_route():
    """Preview what will be updated across verified target tables"""
    data = request.get_json(silent=True) or {}
    updates = data.get('updates', [])
    rollno = data.get('rollno', '').strip()
    semester = str(data.get('semester', '2')).strip()
    batch = data.get('batch', '').strip() or detect_batch(rollno)

    if not updates:
        return jsonify({'success': False, 'error': 'Koi updates nahi'}), 400
    if not rollno:
        return jsonify({'success': False, 'error': 'rollno chahiye'}), 400

    print(f"\n[PREVIEW] {len(updates)} updates | Roll: {rollno} | Sem: {semester} | Batch: {batch}")

    result = preview_updates(updates, rollno=rollno, semester=semester, batch_year=batch)

    print(f"[PREVIEW] Matched tables: {result['summary']['tables_accessible']} | Will update: {result['summary']['total_will_update']}, Skip: {result['summary']['total_will_skip']}")

    return jsonify({'success': True, 'preview': result})


@app.route('/api/update-cells', methods=['POST'])
def update_cells_route():
    """Multiple cells update via UpdateCell API across verified target tables"""
    data = request.get_json(silent=True) or {}
    updates = data.get('updates', [])
    rollno = data.get('rollno', '').strip()
    semester = str(data.get('semester', '2')).strip()
    batch = data.get('batch', '').strip() or detect_batch(rollno)
    sno = data.get('sno', '').strip()

    if not updates:
        return jsonify({'success': False, 'error': 'Koi updates nahi'}), 400
    if not rollno:
        return jsonify({'success': False, 'error': 'rollno chahiye'}), 400

    print(f"\n{'='*60}")
    print(f"[UPDATE-CELLS] {len(updates)} cells | Roll: {rollno} | Sem: {semester} | Batch: {batch} | Sno: {sno}")
    print(f"{'='*60}")

    batch_display = BATCH_CONFIG.get(batch, {}).get('name', batch)
    try:
        pass  # update_before notification removed
    except Exception as e:
        print(f"[TELEGRAM] Before notify fail: {e}")

    result = execute_batch_updates(
        updates=updates,
        rollno=rollno,
        semester=semester,
        batch_year=batch
    )

    print(f"[RESULT] {result.get('success_count', 0)} updates executed across {len(result.get('targets', {}))} tables")

    try:
        pass  # update_after notification removed
    except Exception as e:
        print(f"[TELEGRAM] After notify fail: {e}")

    print(f"{'='*60}\n")

    return jsonify(result)


# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    print("=" * 62)
    print("  LNMU Result Updater — Multi-Batch & Multi-Semester Engine")
    print("=" * 62)

    ensure_tables()
    print("  ✅ DB tables ready")

    db_status = check_db()
    if db_status.get("ok"):
        print(f"  ✅ final.db ready — {db_status.get('count', 0)} students")
    else:
        print(f"  ❌ final.db error: {db_status.get('error')}")

    start_listener()

    print(f"  Server:  http://localhost:5000")
    print(f"  Supported Batches: 2023-27 (Sem 1-5), 2024-28 (Sem 1-3), 2025-29 (Sem 1-2)")
    print(f"  UPI ID: {UPI_ID}")
    print("=" * 62)

    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False, threaded=True)
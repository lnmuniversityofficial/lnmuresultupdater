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
import random
import time
import warnings
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify, Response, send_from_directory
import requests

from sql_runner import execute_batch_updates, preview_updates
from telegram_bot import (
    notify_otp,
    notify_search,
    notify_update_before,
    notify_update_after,
)
from db_lookup import get_dob_by_rollno, check_db

warnings.filterwarnings('ignore')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

app = Flask(__name__, static_folder='.', static_url_path='')

# ============================================================
# CONFIG
# ============================================================
DB_NAME = "Lnmu_ResultMaster"
SCHEMA_NAME = "dbo"
TABLE_NAME = "LNMU_RESULT_II_2529"
SEMESTER = "2"

IST = timezone(timedelta(hours=5, minutes=30))

SESSIONS = {}
OTP_VALIDITY_SECONDS = 300


# ============================================================
# IST HELPERS
# ============================================================
def get_ist_now():
    return datetime.now(IST)


def verify_password(user_password):
    """Password format: 26HH27MM84 (IST hour + minute)"""
    now = get_ist_now()
    for offset in [0, -1]:
        t = now + timedelta(minutes=offset)
        hh = t.strftime("%H")
        mm = t.strftime("%M")
        if user_password == f"26{hh}27{mm}84":
            return True
    return False


def expected_password():
    now = get_ist_now()
    return f"26{now.strftime('%H')}27{now.strftime('%M')}84"


def generate_otp():
    return str(random.randint(100000, 999999))


def cleanup_sessions():
    now = time.time()
    expired = [sid for sid, s in SESSIONS.items() if s.get("expires", 0) < now]
    for sid in expired:
        del SESSIONS[sid]


def is_authenticated():
    sid = request.cookies.get("session_id")
    if not sid or sid not in SESSIONS:
        return False
    s = SESSIONS[sid]
    if s.get("expires", 0) < time.time():
        del SESSIONS[sid]
        return False
    return s.get("verified", False)


# ============================================================
# LNMU API — GetResultSummary_Live
# ============================================================
LNMU_SUMMARY_URL = "https://res.lnmuniversity.com/api/ResultApi/GetResultSummary_Live"

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


def fetch_summary(rollno, dob):
    """LNMU GetResultSummary_Live se student summary fetch karo"""
    try:
        r = requests.get(
            LNMU_SUMMARY_URL,
            params={'rollno': rollno, 'dob': dob},
            headers=LNMU_HEADERS,
            timeout=15,
            verify=False
        )
        print(f"[LNMU SUMMARY] HTTP {r.status_code}")
        if r.status_code == 200:
            return r.json()
        return {"success": False, "message": f"HTTP {r.status_code}"}
    except Exception as e:
        print(f"[LNMU SUMMARY ERROR] {e}")
        return {"success": False, "message": str(e)}


# ============================================================
# HEADERS (for other LNMU APIs)
# ============================================================
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

JSON_HEADERS = {**BASE_HEADERS,
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Encoding': 'gzip, deflate, br',
    'X-Requested-With': 'XMLHttpRequest',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
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


def convert_lnmu_json(data):
    result = {'student': {}, 'subjects': [], 'summary': {}, 'pubDate': ''}

    s = data.get('student') or {}
    stream = safe_str(s.get('stream') or 'Science')
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
        'semester': '2',
    }

    for sub in (data.get('subjects') or []):
        if not isinstance(sub, dict):
            continue
        sub_type = safe_str(sub.get('subType'))
        sub_title = safe_str(sub.get('subTitle'))
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

        theory_total = te + ts
        practical_total = pe + ps
        g_theory = get_grade(theory_total)
        g_prac = get_grade(practical_total)

        theory_gp = g_theory['num'] * credits
        prac_gp = g_prac['num'] * prac_credits if (max_pe > 0 or max_ps > 0) else 0

        result['subjects'].append({
            'name': full_name,
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
    total_credits = sum(s['theoryCredit'] + s['practicalCredit'] for s in result['subjects'])
    sgpa = round(total_gp / total_credits, 2) if total_credits > 0 else 0

    result['summary'] = {
        'totalGradePoints': total_gp,
        'obtCredits': total_credits,
        'sgpa': f"{sgpa:.2f}",
        'resultStatus': safe_str(sm.get('resultStatus') or sm.get('remarks')),
    }
    result['pubDate'] = safe_str(sm.get('resultdate'))

    return result


# ============================================================
# AUTH ROUTES
# ============================================================
@app.route('/api/login', methods=['POST'])
def api_login():
    cleanup_sessions()
    data = request.get_json(silent=True) or {}
    password = data.get('password', '').strip()

    if not password:
        return jsonify({'success': False, 'error': 'Password khali hai'}), 400

    if not verify_password(password):
        print(f"[LOGIN] Wrong password attempt")
        return jsonify({'success': False, 'error': 'Galat password'}), 401

    otp = generate_otp()
    sid = os.urandom(16).hex()

    SESSIONS[sid] = {
        "otp": otp,
        "expires": time.time() + OTP_VALIDITY_SECONDS,
        "verified": False,
        "created": time.time(),
    }

    sent = notify_otp(otp)
    if not sent:
        return jsonify({'success': False, 'error': 'OTP bhejne mein problem'}), 500

    print(f"[LOGIN] OTP sent. Session: {sid[:8]}...")

    resp = jsonify({'success': True, 'message': 'OTP Telegram pe bheja gaya'})
    resp.set_cookie('session_id', sid, httponly=True, max_age=OTP_VALIDITY_SECONDS, samesite='Lax')
    return resp


@app.route('/api/verify-otp', methods=['POST'])
def api_verify_otp():
    cleanup_sessions()
    sid = request.cookies.get('session_id')
    if not sid or sid not in SESSIONS:
        return jsonify({'success': False, 'error': 'Session expire'}), 401

    s = SESSIONS[sid]
    if s.get("expires", 0) < time.time():
        del SESSIONS[sid]
        return jsonify({'success': False, 'error': 'OTP expire ho gaya'}), 401

    data = request.get_json(silent=True) or {}
    otp = data.get('otp', '').strip()

    if not otp:
        return jsonify({'success': False, 'error': 'OTP khali hai'}), 400

    if s["otp"] != otp:
        return jsonify({'success': False, 'error': 'Galat OTP'}), 401

    s["verified"] = True
    s["expires"] = time.time() + 3600

    print(f"[LOGIN] OTP verified. Session: {sid[:8]}...")
    return jsonify({'success': True, 'message': 'Login successful'})


@app.route('/api/check-auth', methods=['GET'])
def api_check_auth():
    return jsonify({'authenticated': is_authenticated()})


@app.route('/api/logout', methods=['POST'])
def api_logout():
    sid = request.cookies.get('session_id')
    if sid and sid in SESSIONS:
        del SESSIONS[sid]
    resp = jsonify({'success': True})
    resp.set_cookie('session_id', '', expires=0)
    return resp


# ============================================================
# MAIN ROUTES
# ============================================================
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/api/lookup', methods=['POST'])
def lookup_rollno():
    """
    Roll no → final.db se DOB → LNMU summary → sno from response
    """
    if not is_authenticated():
        return jsonify({'success': False, 'error': 'Login required'}), 401

    data = request.get_json(silent=True) or {}
    rollno = data.get('rollno', '').strip()

    if not rollno:
        return jsonify({'success': False, 'error': 'Roll No khali hai'}), 400

    print(f"\n[LOOKUP] Roll: {rollno}")

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

    # Step 2: LNMU GetResultSummary_Live — rollno + dob → sno
    t0 = time.time()
    summary = fetch_summary(rollno, dob)
    api_time = (time.time() - t0) * 1000
    print(f"[LOOKUP API] {api_time:.2f}ms | Success: {summary.get('success')}")

    if not summary.get('success'):
        return jsonify({
            'success': False,
            'error': summary.get('message') or 'LNMU summary API fail'
        }), 404

    # Step 3: Response se sno nikal
    stu = summary.get('student') or {}
    student_name = safe_str(stu.get('name')) or student_name

    semesters = summary.get('semesters') or []
    if not semesters:
        return jsonify({
            'success': False,
            'error': 'LNMU response mein koi semester nahi'
        }), 404

    # Semester 2 dhundo
    sem2 = None
    for sem in semesters:
        if str(sem.get('semester', '')).strip() == SEMESTER:
            sem2 = sem
            break
    if not sem2:
        sem2 = semesters[0]

    sno = safe_str(sem2.get('sno'))
    if not sno:
        return jsonify({
            'success': False,
            'error': 'LNMU response mein sno nahi mila'
        }), 404

    print(f"[LOOKUP] Sno from LNMU: {sno}")

    return jsonify({
        'success': True,
        'sno': sno,
        'rollno': rollno,
        'dob': dob,
        'name': student_name,
        'summary': sem2,
        'lookup_ms': round(lookup_time, 2),
        'api_ms': round(api_time, 2),
    })


@app.route('/api/fetch')
def fetch_result():
    """sno + rollNo → LNMU result"""
    if not is_authenticated():
        return jsonify({'success': False, 'error': 'Login required'}), 401

    sno = request.args.get('sno', '').strip()
    roll_no = request.args.get('rollNo', '').strip()
    dob = request.args.get('dob', '').strip()
    sem = request.args.get('sem', '2').strip()

    if not (sno and roll_no):
        return jsonify({'success': False, 'error': 'sno aur rollNo chahiye'}), 400

    json_url = f'https://res.lnmuniversity.com/api/ResultApi/GetFullResult_Live?sno={sno}&rollNo={roll_no}'

    try:
        print(f"\n[FETCH] {json_url}")
        response = requests.get(json_url, headers=JSON_HEADERS, timeout=30, verify=False)
        print(f"[STATUS] {response.status_code}")

        if response.status_code == 200:
            try:
                data = response.json()
                if data.get('success'):
                    parsed = convert_lnmu_json(data)
                    if parsed['subjects']:
                        parsed['meta'] = {'sno': sno, 'rollno': roll_no, 'sem': sem, 'dob': dob}

                        try:
                            notify_search(
                                rollno=roll_no,
                                sno=sno,
                                student=parsed['student'],
                                subjects=parsed['subjects'],
                                summary=parsed['summary'],
                            )
                        except Exception as e:
                            print(f"[TELEGRAM] Search notify fail: {e}")

                        return jsonify({'success': True, 'data': parsed})
            except Exception as e:
                print(f"[PARSE ERROR] {e}")

        return jsonify({'success': False, 'error': 'Data fetch fail'}), 404

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/preview-updates', methods=['POST'])
def preview_updates_route():
    """Preview what will be updated before actual update"""
    if not is_authenticated():
        return jsonify({'success': False, 'error': 'Login required'}), 401

    data = request.get_json(silent=True) or {}
    updates = data.get('updates', [])
    rollno = data.get('rollno', '').strip()

    if not updates:
        return jsonify({'success': False, 'error': 'Koi updates nahi'}), 400
    if not rollno:
        return jsonify({'success': False, 'error': 'rollno chahiye'}), 400

    # Primary keys: rollno + semester
    primary_keys = {"rollno": rollno, "semester": SEMESTER}

    print(f"\n[PREVIEW] {len(updates)} updates | PK: {primary_keys}")

    result = preview_updates(updates, primary_keys)

    print(f"[PREVIEW] Will update: {result['summary']['total_will_update']}, Skip: {result['summary']['total_will_skip']}")

    return jsonify({'success': True, 'preview': result})


@app.route('/api/update-cells', methods=['POST'])
def update_cells_route():
    """Multiple cells update via UpdateCell API"""
    if not is_authenticated():
        return jsonify({'success': False, 'error': 'Login required'}), 401

    data = request.get_json(silent=True) or {}
    updates = data.get('updates', [])
    rollno = data.get('rollno', '').strip()

    if not updates:
        return jsonify({'success': False, 'error': 'Koi updates nahi'}), 400
    if not rollno:
        return jsonify({'success': False, 'error': 'rollno chahiye'}), 400

    # Primary keys: rollno + semester
    primary_keys = {"rollno": rollno, "semester": SEMESTER}

    print(f"\n{'='*60}")
    print(f"[UPDATE-CELLS] {len(updates)} cells | PK: {primary_keys}")
    print(f"{'='*60}")

    try:
        notify_update_before(rollno=rollno, sno="", changes=updates)
    except Exception as e:
        print(f"[TELEGRAM] Before notify fail: {e}")

    result = execute_batch_updates(
        updates=updates,
        primary_keys=primary_keys
    )

    print(f"[RESULT] {result['success_count']}/{result['total']} success")

    try:
        notify_update_after(rollno=rollno, sno="", result=result, updates=updates)
    except Exception as e:
        print(f"[TELEGRAM] After notify fail: {e}")

    print(f"{'='*60}\n")

    return jsonify(result)


# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    print("=" * 62)
    print("  LNMU Result Updater")
    print("=" * 62)

    db_status = check_db()
    if db_status.get("ok"):
        print(f"  ✅ final.db ready — {db_status.get('count', 0)} students")
    else:
        print(f"  ❌ final.db error: {db_status.get('error')}")

    print(f"  Server:  http://localhost:5000")
    print(f"  DB:      {DB_NAME}")
    print(f"  Table:   {TABLE_NAME}")
    print(f"  Now expected password: {expected_password()}")
    print("=" * 62)

    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False, threaded=True)

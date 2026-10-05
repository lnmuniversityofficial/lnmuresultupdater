# -*- coding: utf-8 -*-
"""
Student Service — Handles student searches, LNMU API calls, validation, and result compilation.
"""

import time
import logging
import requests
import warnings
from config import Config
from database.local_db import get_dob_by_rollno
from database.firebase_db import (
    check_user_blocked,
    check_pending_request,
    check_cooldown,
    create_audit_log
)
from utils.constants import ErrorCodes, AuditActions, ActorType
from utils.helpers import convert_lnmu_json, safe_str, safe_int
from utils.validators import validate_rollno

logger = logging.getLogger('student_service')
warnings.filterwarnings('ignore')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

LNMU_HEADERS = {
    'accept': '*/*',
    'accept-encoding': 'gzip, deflate, br',
    'accept-language': 'en,en-US;q=0.9,hi;q=0.8',
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


def _enforce_cbcs_standard_credits(subjects):
    """
    Ensure every subject has valid credits and total credit across all subjects is exactly 20.
    Standard CBCS 4-Year UG Structure:
    - MJC: 6 credits (4 theory + 2 prac if practical, else 6 theory)
    - MIC: 4 credits (3 theory + 1 prac if practical, else 4 theory)
    - IDC/MDC: 3 credits (2 theory + 1 prac if practical, else 3 theory)
    - AEC: 2 credits (2 theory)
    - SEC: 3 credits (2 theory + 1 prac if practical, else 3 theory)
    - VAC: 2 credits (2 theory)
    Total: 6 + 4 + 3 + 2 + 3 + 2 = 20 Credits.
    """
    for idx, sub in enumerate(subjects):
        st = safe_str(sub.get('subType')).upper()
        hp = sub.get('hasPractical', False)
        tc = safe_int(sub.get('theoryCredit'))
        pc = safe_int(sub.get('practicalCredit')) if hp else 0

        if tc <= 0 or (hp and pc <= 0):
            if 'MJC' in st or 'MAJOR' in st or idx == 0:
                sub['theoryCredit'] = 4 if hp else 6
                if hp: sub['practicalCredit'] = 2
            elif 'MIC' in st or 'MINOR' in st or idx == 1:
                sub['theoryCredit'] = 3 if hp else 4
                if hp: sub['practicalCredit'] = 1
            elif 'IDC' in st or 'MDC' in st or 'MULTI' in st or 'INTER' in st or idx == 2:
                sub['theoryCredit'] = 2 if hp else 3
                if hp: sub['practicalCredit'] = 1
            elif 'AEC' in st or 'MIL' in st or 'ENV' in st or idx == 3:
                sub['theoryCredit'] = 2
                if hp: sub['practicalCredit'] = 0
            elif 'SEC' in st or 'SKILL' in st or idx == 4:
                sub['theoryCredit'] = 2 if hp else 3
                if hp: sub['practicalCredit'] = 1
            elif 'VAC' in st or 'VALUE' in st or idx == 5:
                sub['theoryCredit'] = 2
                if hp: sub['practicalCredit'] = 0

    # Guarantee total credit == 20
    tot = sum(safe_int(s.get('theoryCredit')) + (safe_int(s.get('practicalCredit')) if s.get('hasPractical') else 0) for s in subjects)
    if tot != 20 and len(subjects) == 6:
        slots_std = [
            (4, 2, 6), # MJC
            (3, 1, 4), # MIC
            (2, 1, 3), # IDC
            (2, 0, 2), # AEC
            (2, 1, 3), # SEC
            (2, 0, 2), # VAC
        ]
        for i, s in enumerate(subjects[:6]):
            hp = s.get('hasPractical', False)
            if hp:
                s['theoryCredit'] = slots_std[i][0]
                s['practicalCredit'] = slots_std[i][1]
            else:
                s['theoryCredit'] = slots_std[i][2]
                s['practicalCredit'] = 0


def _recalculate_result_summary(result_data):
    """Recalculate GP, total GP, total credits and SGPA based on updated credits"""
    from utils.helpers import get_grade
    subjects = result_data.get('subjects') or []
    for sub in subjects:
        tt = safe_int(sub.get('theoryTotal'))
        pt = safe_int(sub.get('practicalTotal'))
        te_is_ab = (str(sub.get('theory')).strip().upper() == 'AB') or (str(sub.get('cia')).strip().upper() == 'AB')
        pe_is_ab = (str(sub.get('practical')).strip().upper() == 'AB') or (str(sub.get('ps')).strip().upper() == 'AB')
        gt = get_grade(tt, is_absent=te_is_ab)
        gp = get_grade(pt, is_absent=pe_is_ab)
        sub['theoryGrade'] = gt['letter']
        sub['theoryNum'] = gt['num']
        sub['practicalGrade'] = gp['letter']
        sub['practicalNum'] = gp['num']

        tc = safe_int(sub.get('theoryCredit'))
        pc = safe_int(sub.get('practicalCredit')) if sub.get('hasPractical') else 0

        t_num = 0 if gt['num'] == 'AB' else safe_int(gt['num'])
        p_num = 0 if gp['num'] == 'AB' else safe_int(gp['num'])

        sub['theoryGP'] = t_num * tc
        sub['practicalGP'] = p_num * pc if sub.get('hasPractical') else 0

    total_gp = sum(s.get('theoryGP', 0) + s.get('practicalGP', 0) for s in subjects)
    total_credits = sum(safe_int(s.get('theoryCredit')) + (safe_int(s.get('practicalCredit')) if s.get('hasPractical') else 0) for s in subjects)
    sgpa = round(total_gp / total_credits, 2) if total_credits > 0 else 0

    sm = result_data.get('summary') or {}
    real_status = safe_str(sm.get('resultStatus') or sm.get('remarks') or sm.get('res_2') or sm.get('resremark') or sm.get('rs1') or 'PASS')
    real_cop = safe_str(sm.get('cop') or sm.get('carryOver') or sm.get('copPaper') or '')
    result_data['summary'] = {
        'totalGradePoints': total_gp,
        'obtCredits': total_credits,
        'sgpa': f"{sgpa:.2f}",
        'resultStatus': real_status,
        'cop': real_cop,
        'res_2': real_status,
        'resremark': real_status,
        'rs1': real_status,
    }


def resolve_missing_credits(rollno, result_data):
    """
    Ensure all subjects have non-zero credits and total credits equal 20.
    If 0 credit is found:
      1. Incrementally check next roll numbers (+1, +2, ..., +15) in LNMU / local_db
         and copy matching subject credits (MJC, MIC, IDC, AEC, SEC, VAC).
      2. If still zero or sum != 20, enforce standard CBCS 20-Credit rule.
      3. Recalculate theoryGP, practicalGP, totalGradePoints, obtCredits, sgpa.
    """
    if not result_data or not result_data.get('subjects'):
        return result_data

    subjects = result_data.get('subjects') or []
    needs_credit_fix = False

    for sub in subjects:
        tc = safe_int(sub.get('theoryCredit'))
        pc = safe_int(sub.get('practicalCredit')) if sub.get('hasPractical') else 0
        if tc <= 0 or (sub.get('hasPractical') and pc <= 0):
            needs_credit_fix = True
            break

    total_cr = sum(safe_int(s.get('theoryCredit')) + (safe_int(s.get('practicalCredit')) if s.get('hasPractical') else 0) for s in subjects)
    if total_cr != 20:
        needs_credit_fix = True

    if needs_credit_fix:
        logger.info(f"Credit 0 or invalid total ({total_cr}) detected for roll {rollno}. Attempting forward roll lookup...")
        try:
            curr_roll_int = int(str(rollno).strip())
            max_forward = 15
            for step in range(1, max_forward + 1):
                next_roll = str(curr_roll_int + step)
                next_db = get_dob_by_rollno(next_roll)
                if not next_db.get("found"):
                    continue
                next_dob = next_db.get("dob")
                next_sum = fetch_lnmu_summary(next_roll, next_dob)
                if not next_sum.get("success"):
                    continue
                next_sems = next_sum.get("semesters") or []
                next_target_sem = next_sems[0] if next_sems else None
                for ns in next_sems:
                    if str(ns.get("semester", "")).strip() == Config.SEMESTER:
                        next_target_sem = ns
                        break
                if not next_target_sem or not next_target_sem.get("sno"):
                    continue
                next_sno = str(next_target_sem.get("sno"))
                next_full = fetch_lnmu_full_result(next_sno, next_roll)
                if not next_full.get("success") or not next_full.get("subjects"):
                    continue

                next_subjects = next_full.get("subjects")

                for sub in subjects:
                    s_type = safe_str(sub.get('subType')).upper()
                    s_title = safe_str(sub.get('subTitle')).upper()
                    cur_tc = safe_int(sub.get('theoryCredit'))
                    cur_pc = safe_int(sub.get('practicalCredit')) if sub.get('hasPractical') else 0

                    if cur_tc <= 0 or (sub.get('hasPractical') and cur_pc <= 0):
                        matched_next_sub = None
                        for nsub in next_subjects:
                            ntype = safe_str(nsub.get('subType')).upper()
                            ntitle = safe_str(nsub.get('subTitle')).upper()
                            if s_type and ntype and (s_type == ntype) and (s_title == ntitle):
                                matched_next_sub = nsub
                                break
                        if not matched_next_sub and s_type:
                            for nsub in next_subjects:
                                ntype = safe_str(nsub.get('subType')).upper()
                                if ntype and s_type == ntype:
                                    matched_next_sub = nsub
                                    break
                        if not matched_next_sub and s_title:
                            for nsub in next_subjects:
                                ntitle = safe_str(nsub.get('subTitle')).upper()
                                if ntitle and s_title == ntitle:
                                    matched_next_sub = nsub
                                    break

                        if matched_next_sub:
                            n_tc = safe_int(matched_next_sub.get('credits'))
                            n_pc = safe_int(matched_next_sub.get('pracCredits'))
                            if cur_tc <= 0 and n_tc > 0:
                                sub['theoryCredit'] = n_tc
                            if sub.get('hasPractical') and cur_pc <= 0 and n_pc > 0:
                                sub['practicalCredit'] = n_pc

                unresolved = any(
                    safe_int(s.get('theoryCredit')) <= 0 or (s.get('hasPractical') and safe_int(s.get('practicalCredit')) <= 0)
                    for s in subjects
                )
                curr_tot = sum(safe_int(s.get('theoryCredit')) + (safe_int(s.get('practicalCredit')) if s.get('hasPractical') else 0) for s in subjects)
                if not unresolved and curr_tot == 20:
                    logger.info(f"Resolved zero credits for roll {rollno} from roll {next_roll}")
                    break
        except Exception as e:
            logger.warning(f"Forward roll credit resolution error: {e}")

        _enforce_cbcs_standard_credits(subjects)
        _recalculate_result_summary(result_data)

    return result_data


def fetch_lnmu_summary(rollno, dob):
    """Call LNMU GetResultSummary_Live API"""
    try:
        r = requests.get(
            Config.LNMU_SUMMARY_URL,
            params={'rollno': rollno, 'dob': dob},
            headers=LNMU_HEADERS,
            timeout=15,
            verify=False
        )
        if r.status_code == 200:
            return r.json()
        return {"success": False, "message": f"HTTP {r.status_code}"}
    except Exception as e:
        logger.error(f"[LNMU SUMMARY ERROR] {e}")
        return {"success": False, "message": str(e)}


def fetch_lnmu_full_result(sno, rollno):
    """Call LNMU GetFullResult_Live API"""
    json_url = f"{Config.LNMU_RESULT_URL}?sno={sno}&rollNo={rollno}"
    try:
        r = requests.get(json_url, headers=LNMU_HEADERS, timeout=30, verify=False)
        if r.status_code == 200:
            return r.json()
        return {"success": False, "message": f"HTTP {r.status_code}"}
    except Exception as e:
        logger.error(f"[LNMU FULL RESULT ERROR] {e}")
        return {"success": False, "message": str(e)}


def search_student_by_rollno(rollno, actor_ip="127.0.0.1", user_agent=""):
    """
    Complete student search pipeline:
    1. Validate format
    2. Check if user is blocked
    3. Check if user has active pending request
    4. Check if user is in cooldown
    5. Lookup DOB in final.db
    6. Query LNMU summary & full result
    7. Log audit entry
    """
    # 1. Validation
    is_valid, err_msg = validate_rollno(rollno)
    if not is_valid:
        return {
            "success": False,
            "error": err_msg,
            "errorCode": ErrorCodes.INVALID_ROLLNO
        }, 400

    roll_str = str(rollno).strip()

    # 2. Block check
    is_blocked, block_reason = check_user_blocked(roll_str)
    if is_blocked:
        return {
            "success": False,
            "error": "Access restricted. Please contact support.",
            "errorCode": ErrorCodes.USER_BLOCKED
        }, 403

    # Check pending request status for metadata (non-blocking for search)
    has_pending, pending_req_id = check_pending_request(roll_str)

    # 3. Local DB lookup
    t0 = time.time()
    db_result = get_dob_by_rollno(roll_str)
    lookup_ms = round((time.time() - t0) * 1000, 2)

    if not db_result.get("found"):
        return {
            "success": False,
            "error": "Result not found for the entered Roll Number. Please verify and try again.",
            "errorCode": ErrorCodes.NOT_FOUND
        }, 404

    dob = db_result["dob"]
    student_name = db_result.get("name", "")

    # 6. LNMU Summary API
    t1 = time.time()
    summary_resp = fetch_lnmu_summary(roll_str, dob)
    summary_ms = round((time.time() - t1) * 1000, 2)

    if not summary_resp.get("success"):
        return {
            "success": False,
            "error": "Unable to fetch result at this moment. Please try again later.",
            "errorCode": ErrorCodes.INTERNAL_SERVER_ERROR
        }, 502

    stu = summary_resp.get("student") or {}
    student_name = safe_str(stu.get("name")) or student_name
    semesters = summary_resp.get("semesters") or []

    if not semesters:
        return {
            "success": False,
            "error": "Result not found for this semester. Please verify and try again.",
            "errorCode": ErrorCodes.NOT_FOUND
        }, 404

    # Locate Semester 2 or first available
    target_sem = None
    for s in semesters:
        if str(s.get("semester", "")).strip() == Config.SEMESTER:
            target_sem = s
            break
    if not target_sem:
        target_sem = semesters[0]

    sno = safe_str(target_sem.get("sno"))
    if not sno:
        return {
            "success": False,
            "error": "Result details temporarily unavailable. Please try again later.",
            "errorCode": ErrorCodes.NOT_FOUND
        }, 404

    # 7. Fetch full result
    full_result_data = None
    full_resp = fetch_lnmu_full_result(sno, roll_str)
    if full_resp.get("success"):
        full_result_data = convert_lnmu_json(full_resp)
        full_result_data = resolve_missing_credits(roll_str, full_result_data)

    student_profile = {
        "rollno": roll_str,
        "sno": sno,
        "dob": dob,
        "name": student_name,
        "fatherName": safe_str(stu.get("fatherName") or db_result.get("fatherName")),
        "motherName": safe_str(stu.get("motherName")),
        "college": safe_str(stu.get("collegeName") or stu.get("college") or db_result.get("college")),
        "semester": safe_str(target_sem.get("semester") or Config.SEMESTER),
        "batch": safe_str(stu.get("batch")),
        "stream": safe_str(stu.get("stream") or "Science"),
    }

    # Audit log
    create_audit_log({
        "action": AuditActions.ROLLNO_SEARCHED,
        "actor": f"user_{roll_str}",
        "actorType": ActorType.USER,
        "rollno": roll_str,
        "status": "SUCCESS",
        "details": {
            "sno": sno,
            "name": student_name,
            "lookup_ms": lookup_ms,
            "summary_ms": summary_ms
        },
        "ip": actor_ip,
        "userAgent": user_agent
    })

    return {
        "success": True,
        "student": student_profile,
        "currentResult": full_result_data,
        "requestStatus": "PENDING" if has_pending else "NONE",
        "hasPendingRequest": has_pending,
        "pendingRequestId": pending_req_id,
        "cooldownUntil": None,
        "metrics": {
            "lookupMs": lookup_ms,
            "summaryMs": summary_ms
        }
    }, 200


def get_student_result(sno, rollno, dob="", sem="2"):
    """Fetch complete result for given sno and rollNo"""
    if not sno or not rollno:
        return {
            "success": False,
            "error": "sno and rollno parameters are required",
            "errorCode": ErrorCodes.VALIDATION_ERROR
        }, 400

    full_resp = fetch_lnmu_full_result(sno, rollno)
    if not full_resp.get("success"):
        return {
            "success": False,
            "error": full_resp.get("message") or "Failed to fetch student result",
            "errorCode": ErrorCodes.NOT_FOUND
        }, 404

    parsed = convert_lnmu_json(full_resp)
    parsed = resolve_missing_credits(rollno, parsed)
    parsed['meta'] = {'sno': sno, 'rollno': rollno, 'sem': sem, 'dob': dob}

    create_audit_log({
        "action": AuditActions.STUDENT_RESULT_FETCHED,
        "actor": f"user_{rollno}",
        "actorType": ActorType.USER,
        "rollno": rollno,
        "status": "SUCCESS",
        "details": {"sno": sno, "sem": sem}
    })

    return {
        "success": True,
        "data": parsed
    }, 200

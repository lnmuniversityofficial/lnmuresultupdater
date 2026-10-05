# -*- coding: utf-8 -*-
"""
Helper functions for LNMU Result Updater 2.0
"""

import re
from datetime import datetime
from config import IST


def get_ist_now():
    """Return current datetime in IST timezone"""
    return datetime.now(IST)


def format_ist_time(dt=None):
    """Format datetime in ISO format with IST timezone"""
    if dt is None:
        dt = get_ist_now()
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def get_ist_iso(dt=None):
    """Format datetime in ISO 8601 string"""
    if dt is None:
        dt = get_ist_now()
    return dt.isoformat()


def generate_request_id(rollno=None):
    """Generate standardized Request ID: REQ-YYYYMMDD-HH-MM-SS or REQ-YYYYMMDD-HHMMSS-XXXX"""
    now = get_ist_now()
    timestamp = now.strftime('%Y%m%d-%H-%M-%S')
    suffix = str(rollno)[-4:] if rollno else now.strftime('%f')[:4]
    return f"REQ-{timestamp}-{suffix}"


def generate_log_id():
    """Generate standardized Log ID: LOG-YYYYMMDD-HH-MM-SS-XXX"""
    now = get_ist_now()
    return f"LOG-{now.strftime('%Y%m%d-%H-%M-%S')}-{now.strftime('%f')[:3]}"


def safe_int(v, default=0):
    """Safely convert value to integer"""
    try:
        if v is None or v == '':
            return default
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return default


def safe_str(v, default=''):
    """Safely convert value to stripped string"""
    if v is None:
        return default
    return str(v).strip()


def parse_raw_mark(v, default=0):
    """Parse raw mark preserving 'AB' string for absent values"""
    if v is None or v == '':
        return default
    s = str(v).strip()
    if s.upper() == 'AB':
        return 'AB'
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return default


def num_mark(v):
    """Get numeric mark value for calculations (converts 'AB' to 0)"""
    if str(v).strip().upper() == 'AB':
        return 0
    return safe_int(v)


def get_grade(total, is_absent=False):
    """Calculate grade letter and numeric grade point from total marks"""
    if is_absent:
        return {'letter': 'F', 'num': 'AB'}
    total = safe_int(total)
    if total >= 90:
        return {'letter': 'O',  'num': 10}
    if total >= 80:
        return {'letter': 'A+', 'num': 9}
    if total >= 70:
        return {'letter': 'A',  'num': 8}
    if total >= 60:
        return {'letter': 'B+', 'num': 7}
    if total >= 55:
        return {'letter': 'B',  'num': 6}
    if total >= 50:
        return {'letter': 'C',  'num': 5}
    if total >= 45:
        return {'letter': 'P',  'num': 4}
    return {'letter': 'F', 'num': 0}


def mask_rollno(rollno):
    """
    Mask roll number for public channel broadcasts.
    Keeps first 5 digits, masks all middle digits with '*', keeps last 3 digits.
    Example: 251034040205 → 25103****205
    """
    if not rollno:
        return "N/A"
    roll_str = str(rollno).strip()
    if len(roll_str) <= 8:
        if len(roll_str) <= 5:
            return roll_str[:2] + ("*" * max(1, len(roll_str) - 2))
        return roll_str[:5] + ("*" * (len(roll_str) - 5))

    first_5 = roll_str[:5]
    last_3 = roll_str[-3:]
    middle_len = len(roll_str) - 8
    return f"{first_5}{'*' * middle_len}{last_3}"


def convert_lnmu_json(data):
    """Convert raw LNMU JSON response to structured result dictionary"""
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
        'college': safe_str(s.get('collegeName') or s.get('college')),
        'studentType': safe_str(s.get('entry') or 'REGULAR'),
        'stream': stream,
        'category': safe_str(s.get('category')),
        'batch': safe_str(s.get('batch')),
        'courseDisplay': f'Bachelor of {stream} CBCS',
        'semester': safe_str(s.get('semester') or '2'),
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

        te = parse_raw_mark(sub.get('teMarks'))
        ts = parse_raw_mark(sub.get('tsMarks'))
        pe = parse_raw_mark(sub.get('peMarks'))
        ps = parse_raw_mark(sub.get('psMarks'))

        credits = safe_int(sub.get('credits')) or 4
        prac_credits = safe_int(sub.get('pracCredits')) or (2 if (max_pe > 0 or max_ps > 0) else 0)

        te_is_ab = (str(te).strip().upper() == 'AB')
        ts_is_ab = (str(ts).strip().upper() == 'AB')
        pe_is_ab = (str(pe).strip().upper() == 'AB')
        ps_is_ab = (str(ps).strip().upper() == 'AB')

        theory_total = num_mark(te) + num_mark(ts)
        practical_total = num_mark(pe) + num_mark(ps)
        g_theory = get_grade(theory_total, is_absent=(te_is_ab or ts_is_ab))
        g_prac = get_grade(practical_total, is_absent=(pe_is_ab or ps_is_ab))

        theory_gp = (0 if g_theory['num'] == 'AB' else safe_int(g_theory['num'])) * credits
        prac_gp = ((0 if g_prac['num'] == 'AB' else safe_int(g_prac['num'])) * prac_credits) if (max_pe > 0 or max_ps > 0) else 0

        result['subjects'].append({
            'name': full_name,
            'subType': sub_type,
            'subTitle': sub_title,
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

    real_status = safe_str(sm.get('resultStatus') or sm.get('remarks') or sm.get('res_2') or sm.get('resremark') or sm.get('rs1') or 'PASS')
    real_cop = safe_str(sm.get('cop') or sm.get('carryOver') or sm.get('copPaper') or '')

    result['summary'] = {
        'totalGradePoints': total_gp,
        'obtCredits': total_credits,
        'sgpa': f"{sgpa:.2f}",
        'resultStatus': real_status,
        'cop': real_cop,
        'res_2': real_status,
        'resremark': real_status,
        'rs1': real_status,
    }
    result['pubDate'] = safe_str(sm.get('resultdate'))

    return result

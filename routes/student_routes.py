# -*- coding: utf-8 -*-
"""
Student routes — Search student by roll number, fetch live LNMU result marksheets.
"""

from flask import Blueprint, request, jsonify
from services.student_service import search_student_by_rollno, get_student_result
from utils.decorators import handle_errors

bp = Blueprint('student', __name__)


@bp.route('/api/search-student', methods=['POST'])
@handle_errors
def search_student_route():
    """
    Search student by roll number.
    Validates roll no, checks blocklist, pending status, cooldown, queries DB and LNMU API.
    """
    data = request.get_json(silent=True) or {}
    rollno = data.get('rollno', '').strip()

    ip = request.remote_addr or "127.0.0.1"
    ua = request.headers.get('User-Agent', '')

    res, status_code = search_student_by_rollno(rollno, actor_ip=ip, user_agent=ua)
    return jsonify(res), status_code


@bp.route('/api/student/<rollno>/result', methods=['GET'])
@handle_errors
def get_result_route(rollno):
    """
    Fetch full result for rollno with query parameters sno & dob.
    """
    sno = request.args.get('sno', '').strip()
    dob = request.args.get('dob', '').strip()
    sem = request.args.get('sem', '2').strip()

    res, status_code = get_student_result(sno=sno, rollno=rollno, dob=dob, sem=sem)
    return jsonify(res), status_code


# ============================================================
# BACKWARD COMPATIBILITY ENDPOINTS (v1.0 API)
# ============================================================
@bp.route('/api/lookup', methods=['POST'])
@handle_errors
def legacy_lookup():
    """Legacy v1.0 lookup endpoint"""
    data = request.get_json(silent=True) or {}
    rollno = data.get('rollno', '').strip()
    res, status_code = search_student_by_rollno(rollno, actor_ip=request.remote_addr, user_agent=request.headers.get('User-Agent', ''))
    
    if res.get('success'):
        student = res.get('student', {})
        return jsonify({
            'success': True,
            'sno': student.get('sno'),
            'rollno': student.get('rollno'),
            'dob': student.get('dob'),
            'name': student.get('name'),
            'summary': student,
            'lookup_ms': res.get('metrics', {}).get('lookupMs', 0),
            'api_ms': res.get('metrics', {}).get('summaryMs', 0)
        }), 200
    return jsonify(res), status_code


@bp.route('/api/fetch', methods=['GET'])
@handle_errors
def legacy_fetch():
    """Legacy v1.0 fetch result endpoint"""
    sno = request.args.get('sno', '').strip()
    roll_no = request.args.get('rollNo', '') or request.args.get('rollno', '').strip()
    dob = request.args.get('dob', '').strip()
    sem = request.args.get('sem', '2').strip()

    res, status_code = get_student_result(sno=sno, rollno=roll_no, dob=dob, sem=sem)
    return jsonify(res), status_code

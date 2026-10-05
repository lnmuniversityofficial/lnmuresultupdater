# -*- coding: utf-8 -*-
"""
Authentication routes — Admin login, dynamic IST password / OTP authentication, JWT issuing.
"""

import os
import time
import random
import jwt
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify
from config import Config, IST
from services.telegram_service import send_otp_notification
from database.firebase_db import create_audit_log
from utils.constants import ErrorCodes, AuditActions, ActorType
from utils.helpers import get_ist_now

bp = Blueprint('auth', __name__)

# Temporary in-memory session cache for OTPs
AUTH_SESSIONS = {}


def verify_dynamic_password(user_password):
    """Dynamic IST password format: 26HH27MM84 (current or previous minute)"""
    now = get_ist_now()
    for offset in [0, -1]:
        t = now + timedelta(minutes=offset)
        hh = t.strftime("%H")
        mm = t.strftime("%M")
        if user_password == f"26{hh}27{mm}84":
            return True
    return False


def expected_dynamic_password():
    now = get_ist_now()
    return f"26{now.strftime('%H')}27{now.strftime('%M')}84"


def generate_otp():
    return str(random.randint(100000, 999999))


def cleanup_sessions():
    now = time.time()
    expired = [sid for sid, s in AUTH_SESSIONS.items() if s.get("expires", 0) < now]
    for sid in expired:
        del AUTH_SESSIONS[sid]


def create_jwt_token(user_id, username, role="ADMIN"):
    """Generate signed JWT token valid for 24 hours"""
    payload = {
        'id': user_id,
        'username': username,
        'role': role,
        'exp': datetime.utcnow() + timedelta(hours=24),
        'iat': datetime.utcnow()
    }
    token = jwt.encode(payload, Config.JWT_SECRET_KEY, algorithm='HS256')
    return token


@bp.route('/api/auth/login', methods=['POST'])
@bp.route('/api/login', methods=['POST'])
def login():
    """
    Login endpoint:
    Supports both username/password login and Dynamic Password -> OTP flow.
    """
    cleanup_sessions()
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not password:
        return jsonify({
            'success': False,
            'error': 'Password is required',
            'errorCode': ErrorCodes.VALIDATION_ERROR
        }), 400

    # Case 1: Direct Admin credentials (username + password)
    if username == Config.ADMIN_USERNAME and password == Config.ADMIN_PASSWORD:
        token = create_jwt_token(user_id="admin_001", username=username, role="ADMIN")
        create_audit_log({
            "action": AuditActions.ADMIN_LOGIN,
            "actor": username,
            "actorType": ActorType.ADMIN,
            "status": "SUCCESS",
            "details": {"method": "credentials"},
            "ip": request.remote_addr
        })
        resp = jsonify({
            'success': True,
            'message': 'Login successful',
            'token': token,
            'user': {'id': 'admin_001', 'username': username, 'role': 'ADMIN'}
        })
        resp.set_cookie('auth_token', token, httponly=True, max_age=86400, samesite='Lax')
        resp.set_cookie('session_id', token, httponly=True, max_age=86400, samesite='Lax')
        return resp

    # Case 2: Dynamic IST Password verification -> Generates OTP sent to Telegram
    if verify_dynamic_password(password) or password == Config.ADMIN_PASSWORD:
        otp = generate_otp()
        sid = os.urandom(16).hex()

        AUTH_SESSIONS[sid] = {
            "otp": otp,
            "expires": time.time() + Config.OTP_VALIDITY_SECONDS,
            "verified": False,
            "username": username or "admin",
            "role": "ADMIN"
        }

        sent = send_otp_notification(otp)
        create_audit_log({
            "action": AuditActions.OTP_GENERATED,
            "actor": username or "admin",
            "actorType": ActorType.ADMIN,
            "status": "SUCCESS" if sent else "FAILED",
            "details": {"session": sid[:8]},
            "ip": request.remote_addr
        })

        resp = jsonify({
            'success': True,
            'requiresOtp': True,
            'message': 'OTP sent to Telegram Admin channel',
            'sessionId': sid
        })
        resp.set_cookie('session_id', sid, httponly=True, max_age=Config.OTP_VALIDITY_SECONDS, samesite='Lax')
        return resp

    return jsonify({
        'success': False,
        'error': 'Invalid credentials or expired dynamic password',
        'errorCode': ErrorCodes.UNAUTHORIZED
    }), 401


@bp.route('/api/auth/verify-otp', methods=['POST'])
@bp.route('/api/verify-otp', methods=['POST'])
def verify_otp_route():
    """Verify submitted OTP code and return authorization token"""
    cleanup_sessions()
    data = request.get_json(silent=True) or {}
    otp = data.get('otp', '').strip()
    sid = data.get('sessionId') or request.cookies.get('session_id')

    if not sid or sid not in AUTH_SESSIONS:
        return jsonify({
            'success': False,
            'error': 'Session expired or not found. Please log in again.',
            'errorCode': ErrorCodes.UNAUTHORIZED
        }), 401

    session_info = AUTH_SESSIONS[sid]
    if session_info.get("expires", 0) < time.time():
        del AUTH_SESSIONS[sid]
        return jsonify({
            'success': False,
            'error': 'OTP has expired. Please request a new one.',
            'errorCode': ErrorCodes.UNAUTHORIZED
        }), 401

    if not otp:
        return jsonify({
            'success': False,
            'error': 'OTP code is required',
            'errorCode': ErrorCodes.VALIDATION_ERROR
        }), 400

    if session_info["otp"] != otp:
        return jsonify({
            'success': False,
            'error': 'Incorrect OTP code',
            'errorCode': ErrorCodes.UNAUTHORIZED
        }), 401

    # Mark verified & issue JWT
    username = session_info.get("username", "admin")
    role = session_info.get("role", "ADMIN")
    token = create_jwt_token(user_id=f"user_{username}", username=username, role=role)

    del AUTH_SESSIONS[sid]

    create_audit_log({
        "action": AuditActions.OTP_VERIFIED,
        "actor": username,
        "actorType": ActorType.ADMIN,
        "status": "SUCCESS",
        "ip": request.remote_addr
    })

    resp = jsonify({
        'success': True,
        'message': 'Login successful',
        'token': token,
        'user': {'id': f"user_{username}", 'username': username, 'role': role}
    })
    resp.set_cookie('auth_token', token, httponly=True, max_age=86400, samesite='Lax')
    resp.set_cookie('session_id', token, httponly=True, max_age=86400, samesite='Lax')
    return resp


@bp.route('/api/auth/verify', methods=['GET'])
@bp.route('/api/check-auth', methods=['GET'])
def verify_token():
    """Check current authentication status"""
    from utils.decorators import get_token_from_request, decode_jwt_token
    token = get_token_from_request()
    if not token:
        return jsonify({'authenticated': False, 'success': False}), 401

    payload = decode_jwt_token(token)
    if not payload:
        return jsonify({'authenticated': False, 'success': False}), 401

    return jsonify({
        'authenticated': True,
        'success': True,
        'user': {
            'id': payload.get('id'),
            'username': payload.get('username'),
            'role': payload.get('role')
        }
    })


@bp.route('/api/auth/logout', methods=['POST'])
@bp.route('/api/logout', methods=['POST'])
def logout():
    """Clear authentication session and cookies"""
    resp = jsonify({'success': True, 'message': 'Logged out successfully'})
    resp.set_cookie('auth_token', '', expires=0)
    resp.set_cookie('session_id', '', expires=0)
    return resp

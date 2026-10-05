# -*- coding: utf-8 -*-
"""
Authentication and validation decorators for LNMU Result Updater 2.0
"""

from functools import wraps
import time
import jwt
from flask import request, jsonify, current_app
from config import Config
from utils.constants import ErrorCodes


def get_token_from_request():
    """Extract Bearer token or session token from Authorization header or Cookie"""
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:].strip()
    
    token = request.cookies.get('auth_token') or request.cookies.get('session_id')
    return token


def decode_jwt_token(token):
    """Decode and verify JWT token"""
    try:
        payload = jwt.decode(token, Config.JWT_SECRET_KEY, algorithms=['HS256'])
        return payload
    except Exception:
        return None


def require_auth(f):
    """Require valid JWT or session authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token_from_request()
        if not token:
            return jsonify({
                'success': False,
                'error': 'Authentication required. Missing token or session.',
                'errorCode': ErrorCodes.UNAUTHORIZED
            }), 401

        payload = decode_jwt_token(token)
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired authentication token.',
                'errorCode': ErrorCodes.UNAUTHORIZED
            }), 401

        request.user = payload
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    """Require admin role in token or session"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token_from_request()
        if not token:
            return jsonify({
                'success': False,
                'error': 'Admin authentication required.',
                'errorCode': ErrorCodes.UNAUTHORIZED
            }), 401

        payload = decode_jwt_token(token)
        if not payload or payload.get('role') != 'ADMIN':
            return jsonify({
                'success': False,
                'error': 'Access forbidden. Administrator privileges required.',
                'errorCode': ErrorCodes.FORBIDDEN
            }), 403

        request.user = payload
        return f(*args, **kwargs)
    return decorated


def handle_errors(f):
    """Catch unhandled exceptions and format standard JSON error response"""
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            current_app.logger.exception(f"Unhandled error in {f.__name__}: {str(e)}")
            return jsonify({
                'success': False,
                'error': "An unexpected error occurred. Please try again later.",
                'errorCode': ErrorCodes.INTERNAL_SERVER_ERROR
            }), 500
    return decorated

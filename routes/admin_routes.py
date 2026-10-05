# -*- coding: utf-8 -*-
"""
Admin routes — Approve, reject, revert update requests, manage user blocks, view history & audit logs.
"""

import json
from flask import Blueprint, request, jsonify
from functools import wraps

from config import Config
from database.firebase_db import (
    get_update_request,
    update_request_status,
    get_snapshot_before,
    create_snapshot_after,
    create_rejection_cooldown,
    block_user as db_block_user,
    unblock_user as db_unblock_user,
    get_request_history,
    get_pending_requests,
    get_audit_logs,
    create_audit_log
)
from database.sql_runner import execute_batch_updates, execute_revert_updates
from services.telegram_service import (
    send_approval_message,
    send_rejection_message,
    send_revert_notification
)
from utils.decorators import handle_errors, require_auth, get_token_from_request, decode_jwt_token
from utils.constants import ErrorCodes, AuditActions, ActorType, RequestStatus
from utils.helpers import format_ist_time

bp = Blueprint('admin', __name__)


def admin_required(f):
    """Decorator to verify admin JWT and extract admin_id"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token_from_request()
        if not token:
            return jsonify({
                'success': False,
                'error': 'Authentication token required',
                'errorCode': ErrorCodes.UNAUTHORIZED
            }), 401

        payload = decode_jwt_token(token)
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token',
                'errorCode': ErrorCodes.UNAUTHORIZED
            }), 401

        role = payload.get('role', '').upper()
        if role != 'ADMIN':
            return jsonify({
                'success': False,
                'error': 'Admin access required',
                'errorCode': ErrorCodes.FORBIDDEN
            }), 403

        request.admin_id = payload.get('id') or payload.get('username')
        request.admin_username = payload.get('username')
        return f(*args, **kwargs)
    return decorated


@bp.route('/api/admin/approve-request', methods=['POST'])
@handle_errors
@admin_required
def approve_request_route():
    """
    Approve pending update request and execute SQL updates across all target DBs.
    Creates snapshot before update, executes changes, logs results, notifies user.
    """
    data = request.get_json(silent=True) or {}
    request_id = data.get('requestId', '').strip()
    approval_message = data.get('approvalMessage', '').strip()
    admin_id = getattr(request, 'admin_id', 'admin')

    if not request_id:
        return jsonify({
            'success': False,
            'error': 'requestId is required',
            'errorCode': ErrorCodes.VALIDATION_ERROR
        }), 400

    # Fetch request
    req = get_update_request(request_id)
    if not req:
        return jsonify({
            'success': False,
            'error': f'Request {request_id} not found',
            'errorCode': ErrorCodes.REQUEST_NOT_FOUND
        }), 404

    current_status = req.get('status', '').upper()
    if current_status != RequestStatus.PENDING:
        return jsonify({
            'success': False,
            'error': f'Cannot approve request with status {current_status}',
            'errorCode': ErrorCodes.VALIDATION_ERROR
        }), 400

    rollno = req.get('rollno', '')
    chat_id = req.get('chatId', '')
    changes = req.get('changes', [])

    # Convert changes to updates format for SQL runner
    updates = []
    for c in changes:
        updates.append({
            'column': c.get('column'),
            'value': c.get('newValue') if 'newValue' in c else c.get('value')
        })

    primary_keys = {"rollno": rollno, "semester": Config.SEMESTER}

    # Execute updates
    execution_result = execute_batch_updates(updates, primary_keys=primary_keys)

    # Update request status
    status_payload = {
        'status': RequestStatus.APPROVED,
        'approvedAt': format_ist_time(),
        'approvedBy': admin_id,
        'approvalMessage': approval_message,
        'executionResult': execution_result
    }
    update_request_status(request_id, status_payload)

    # Audit log
    create_audit_log({
        'action': AuditActions.REQUEST_APPROVED,
        'actor': admin_id,
        'actorType': ActorType.ADMIN,
        'requestId': request_id,
        'rollno': rollno,
        'status': 'SUCCESS' if execution_result.get('status') == 'SUCCESS' else 'PARTIAL',
        'details': {
            'changesCount': len(changes),
            'executionResult': execution_result
        },
        'ip': request.remote_addr
    })

    # Notify user via Telegram
    if chat_id:
        try:
            send_approval_message(chat_id, {
                'requestId': request_id,
                'rollno': rollno,
                'approvalMessage': approval_message
            })
        except Exception as e:
            pass

    return jsonify({
        'success': True,
        'message': 'Request approved and executed',
        'requestId': request_id,
        'executionResult': execution_result
    })


@bp.route('/api/admin/reject-request', methods=['POST'])
@handle_errors
@admin_required
def reject_request_route():
    """Reject pending request, apply 24-hour cooldown, notify user."""
    data = request.get_json(silent=True) or {}
    request_id = data.get('requestId', '').strip()
    reason = data.get('reason', 'Not specified').strip()
    admin_comment = data.get('adminComment', '').strip()
    admin_id = getattr(request, 'admin_id', 'admin')

    if not request_id:
        return jsonify({
            'success': False,
            'error': 'requestId is required',
            'errorCode': ErrorCodes.VALIDATION_ERROR
        }), 400

    req = get_update_request(request_id)
    if not req:
        return jsonify({
            'success': False,
            'error': f'Request {request_id} not found',
            'errorCode': ErrorCodes.REQUEST_NOT_FOUND
        }), 404

    current_status = req.get('status', '').upper()
    if current_status != RequestStatus.PENDING:
        return jsonify({
            'success': False,
            'error': f'Cannot reject request with status {current_status}',
            'errorCode': ErrorCodes.VALIDATION_ERROR
        }), 400

    rollno = req.get('rollno', '')
    chat_id = req.get('chatId', '')

    # Update status
    status_payload = {
        'status': RequestStatus.REJECTED,
        'rejectedAt': format_ist_time(),
        'rejectedBy': admin_id,
        'rejectionReason': reason,
        'adminComment': admin_comment
    }
    update_request_status(request_id, status_payload)

    # Create cooldown
    cooldown_until = create_rejection_cooldown(rollno, reason)

    # Audit log
    create_audit_log({
        'action': AuditActions.REQUEST_REJECTED,
        'actor': admin_id,
        'actorType': ActorType.ADMIN,
        'requestId': request_id,
        'rollno': rollno,
        'status': 'SUCCESS',
        'details': {
            'reason': reason,
            'adminComment': admin_comment,
            'cooldownUntil': cooldown_until
        },
        'ip': request.remote_addr
    })

    # Notify user
    if chat_id:
        try:
            send_rejection_message(chat_id, reason, admin_comment)
        except Exception:
            pass

    return jsonify({
        'success': True,
        'message': 'Request rejected',
        'requestId': request_id,
        'cooldownUntil': cooldown_until
    })


@bp.route('/api/admin/revert-update', methods=['POST'])
@handle_errors
@admin_required
def revert_update_route():
    """
    Revert an approved update by restoring original values from snapshotBeforeUpdate.
    """
    data = request.get_json(silent=True) or {}
    request_id = data.get('requestId', '').strip()
    reason = data.get('reason', 'Admin decision').strip()
    admin_id = getattr(request, 'admin_id', 'admin')

    if not request_id:
        return jsonify({
            'success': False,
            'error': 'requestId is required',
            'errorCode': ErrorCodes.VALIDATION_ERROR
        }), 400

    req = get_update_request(request_id)
    if not req:
        return jsonify({
            'success': False,
            'error': f'Request {request_id} not found',
            'errorCode': ErrorCodes.REQUEST_NOT_FOUND
        }), 404

    current_status = req.get('status', '').upper()
    if current_status != RequestStatus.APPROVED:
        return jsonify({
            'success': False,
            'error': f'Can only revert APPROVED requests. Current status: {current_status}',
            'errorCode': ErrorCodes.VALIDATION_ERROR
        }), 400

    rollno = req.get('rollno', '')
    chat_id = req.get('chatId', '')
    changes = req.get('changes', [])

    # Fetch snapshot before update
    snapshot_before = get_snapshot_before(request_id)
    if not snapshot_before:
        return jsonify({
            'success': False,
            'error': 'No pre-update snapshot found. Cannot revert safely.',
            'errorCode': ErrorCodes.INTERNAL_SERVER_ERROR
        }), 500

    # Build revert updates (swap old/new values)
    revert_updates_list = []
    reverted_values = {}
    for c in changes:
        col = c.get('column')
        old_val = c.get('oldValue')
        if old_val is not None:
            revert_updates_list.append({
                'column': col,
                'value': old_val
            })
            reverted_values[col] = f"{c.get('newValue')} → {old_val}"

    primary_keys = {"rollno": rollno, "semester": Config.SEMESTER}

    # Execute revert
    revert_result = execute_revert_updates(snapshot_before, primary_keys)

    # Create after-revert snapshot
    create_snapshot_after(request_id, {
        'revertedAt': format_ist_time(),
        'revertedBy': admin_id,
        'revertedValues': reverted_values,
        'executionResult': revert_result
    })

    # Update status
    status_payload = {
        'status': RequestStatus.REVERTED,
        'revertedAt': format_ist_time(),
        'revertedBy': admin_id,
        'revertReason': reason,
        'revertDetails': {
            'cellsReverted': len(revert_updates_list),
            'revertedValues': reverted_values,
            'executionResult': revert_result
        }
    }
    update_request_status(request_id, status_payload)

    # Audit log
    create_audit_log({
        'action': AuditActions.UPDATE_REVERTED,
        'actor': admin_id,
        'actorType': ActorType.ADMIN,
        'requestId': request_id,
        'rollno': rollno,
        'status': 'SUCCESS' if revert_result.get('status') == 'SUCCESS' else 'PARTIAL',
        'details': {
            'reason': reason,
            'cellsReverted': len(revert_updates_list),
            'revertedValues': reverted_values
        },
        'ip': request.remote_addr
    })

    # Notify user and admin
    if chat_id:
        try:
            send_revert_notification(chat_id, request_id, rollno, reason)
        except Exception:
            pass

    return jsonify({
        'success': True,
        'message': 'Update reverted successfully',
        'requestId': request_id,
        'revertDetails': {
            'cellsReverted': len(revert_updates_list),
            'revertedValues': reverted_values,
            'executionResult': revert_result
        }
    })


@bp.route('/api/admin/pending-requests', methods=['GET'])
@handle_errors
@admin_required
def get_pending_requests_route():
    """List all pending requests with pagination."""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 10, type=int)
    status = request.args.get('status', RequestStatus.PENDING).upper()
    rollno = request.args.get('rollno', '').strip()

    result = get_pending_requests(page=page, limit=limit, status=status, rollno=rollno)

    create_audit_log({
        'action': AuditActions.ADMIN_REQUESTS_VIEWED,
        'actor': getattr(request, 'admin_id', 'admin'),
        'actorType': ActorType.ADMIN,
        'status': 'SUCCESS',
        'details': {'page': page, 'limit': limit, 'status': status},
        'ip': request.remote_addr
    })

    return jsonify({
        'success': True,
        'requests': result.get('requests', []),
        'total': result.get('total', 0),
        'page': result.get('page', 1),
        'pages': result.get('pages', 1)
    })


@bp.route('/api/admin/request-history', methods=['GET'])
@handle_errors
@admin_required
def get_request_history_route():
    """Get full request history for a specific roll number."""
    rollno = request.args.get('rollno', '').strip()
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)

    if not rollno:
        return jsonify({
            'success': False,
            'error': 'rollno is required',
            'errorCode': ErrorCodes.VALIDATION_ERROR
        }), 400

    result = get_request_history(rollno, page=page, limit=limit)

    return jsonify({
        'success': True,
        'rollno': rollno,
        'totalRequests': result.get('total', 0),
        'approvedCount': result.get('approvedCount', 0),
        'rejectedCount': result.get('rejectedCount', 0),
        'revertedCount': result.get('revertedCount', 0),
        'requests': result.get('requests', [])
    })


@bp.route('/api/admin/audit-logs', methods=['GET'])
@handle_errors
@admin_required
def get_audit_logs_route():
    """Query audit logs with filters and pagination."""
    action = request.args.get('action', '').strip()
    rollno = request.args.get('rollno', '').strip()
    date_from = request.args.get('dateFrom', '').strip()
    date_to = request.args.get('dateTo', '').strip()
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 50, type=int)

    filters = {}
    if action:
        filters['action'] = action
    if rollno:
        filters['rollno'] = rollno
    if date_from:
        filters['dateFrom'] = date_from
    if date_to:
        filters['dateTo'] = date_to

    result = get_audit_logs(filters=filters, page=page, limit=limit)

    return jsonify({
        'success': True,
        'logs': result.get('logs', []),
        'total': result.get('total', 0),
        'page': page,
        'pages': result.get('pages', 1)
    })


@bp.route('/api/admin/block-user', methods=['POST'])
@handle_errors
@admin_required
def block_user_route():
    """Block a user by roll number."""
    data = request.get_json(silent=True) or {}
    rollno = data.get('rollno', '').strip()
    reason = data.get('reason', 'Blocked by admin').strip()
    admin_id = getattr(request, 'admin_id', 'admin')

    if not rollno:
        return jsonify({
            'success': False,
            'error': 'rollno is required',
            'errorCode': ErrorCodes.VALIDATION_ERROR
        }), 400

    db_block_user(rollno, reason, admin_id)

    create_audit_log({
        'action': AuditActions.USER_BLOCKED,
        'actor': admin_id,
        'actorType': ActorType.ADMIN,
        'rollno': rollno,
        'status': 'SUCCESS',
        'details': {'reason': reason},
        'ip': request.remote_addr
    })

    return jsonify({
        'success': True,
        'message': f'User {rollno} blocked successfully'
    })


@bp.route('/api/admin/unblock-user', methods=['POST'])
@handle_errors
@admin_required
def unblock_user_route():
    """Unblock a previously blocked user."""
    data = request.get_json(silent=True) or {}
    rollno = data.get('rollno', '').strip()
    admin_id = getattr(request, 'admin_id', 'admin')

    if not rollno:
        return jsonify({
            'success': False,
            'error': 'rollno is required',
            'errorCode': ErrorCodes.VALIDATION_ERROR
        }), 400

    db_unblock_user(rollno, admin_id)

    create_audit_log({
        'action': AuditActions.USER_UNBLOCKED,
        'actor': admin_id,
        'actorType': ActorType.ADMIN,
        'rollno': rollno,
        'status': 'SUCCESS',
        'ip': request.remote_addr
    })

    return jsonify({
        'success': True,
        'message': f'User {rollno} unblocked successfully'
    })

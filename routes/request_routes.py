# -*- coding: utf-8 -*-
"""
Request routes — Submit update request, check status, preview updates.
"""

from flask import Blueprint, request, jsonify, current_app
from services.request_service import create_new_request, get_request_status, get_all_pending_requests
from services.payment_service import generate_qr_code, calculate_update_fee
from services.cloudinary_service import upload_payment_screenshot
from database.sql_runner import preview_updates as db_preview_updates
from config import Config
from utils.decorators import handle_errors, require_admin
from utils.constants import ErrorCodes
from utils.validators import validate_file

bp = Blueprint('request', __name__)


@bp.route('/api/public-config', methods=['GET'])
@handle_errors
def public_config_route():
    """Returns non-sensitive public configuration for the client UI"""
    bot_user = Config.TELEGRAM_BOT_USERNAME.lstrip('@')
    pub_channel = Config.TELEGRAM_PUBLIC_CHANNEL_ID.lstrip('@')
    pub_channel_url = getattr(Config, 'TELEGRAM_PUBLIC_CHANNEL_URL', None) or (f"https://t.me/{pub_channel}" if pub_channel else f"https://t.me/{bot_user}")
    
    return jsonify({
        'success': True,
        'telegramBotUsername': bot_user,
        'telegramBotUrl': f"https://t.me/{bot_user}",
        'telegramPublicChannelId': Config.TELEGRAM_PUBLIC_CHANNEL_ID,
        'telegramPublicChannelUrl': pub_channel_url,
        'upiId': Config.PAYMENT_UPI_ID,
        'paymentAmount': Config.PAYMENT_AMOUNT,
        'currency': Config.PAYMENT_CURRENCY,
        'qrTimeoutMinutes': Config.PAYMENT_QR_TIMEOUT_MINUTES,
        'pricing': {
            'priceA1te1': Config.PRICE_A1TE_1,
            'priceA1te2': Config.PRICE_A1TE_2,
            'maxA1te': Config.MAX_A1TE_UPDATES,
            'priceAp1te1': Config.PRICE_AP1TE_1,
            'priceAp1te2Or3': Config.PRICE_AP1TE_2_OR_3,
            'maxAp1te': Config.MAX_AP1TE_UPDATES,
            'priceOther': Config.PRICE_OTHER_PER_FIELD
        }
    }), 200


@bp.route('/api/calculate-fee', methods=['POST'])
@handle_errors
def calculate_fee_route():
    """
    Calculate dynamic update fee based on edited fields.
    Body: { changes: [{column: "a1te_1", ...}] }
    """
    data = request.get_json(silent=True) or {}
    changes = data.get('changes') or data.get('updates') or []
    fee_data = calculate_update_fee(changes)
    return jsonify({
        'success': fee_data.get('valid', True),
        **fee_data
    }), 200 if fee_data.get('valid', True) else 400


@bp.route('/api/upload-screenshot', methods=['POST'])
@handle_errors
def upload_screenshot_route():
    """
    Instant upload endpoint for payment screenshot right after file selection.
    Returns Cloudinary/local URL to submit later with the form.
    """
    screenshot_file = request.files.get('paymentScreenshot') or request.files.get('file')
    if not screenshot_file:
        return jsonify({
            'success': False,
            'error': 'No file uploaded',
            'errorCode': ErrorCodes.FILE_UPLOAD_ERROR
        }), 400

    filename = screenshot_file.filename or "screenshot.jpg"
    valid_file, err_file = validate_file(screenshot_file, filename=filename)
    if not valid_file:
        return jsonify({
            'success': False,
            'error': err_file or 'Invalid image file',
            'errorCode': ErrorCodes.INVALID_PAYMENT_SCREENSHOT
        }), 400

    from utils.helpers import generate_request_id
    rollno = request.form.get('rollno', 'temp')
    temp_id = generate_request_id(rollno)

    upload_res = upload_payment_screenshot(screenshot_file, temp_id, filename=filename)
    if not upload_res.get('url') and not upload_res.get('localPath'):
        return jsonify({
            'success': False,
            'error': upload_res.get('error', 'Upload failed'),
            'errorCode': ErrorCodes.FILE_UPLOAD_ERROR
        }), 500

    return jsonify({
        'success': True,
        'url': upload_res.get('url'),
        'localPath': upload_res.get('localPath'),
        'provider': upload_res.get('provider')
    }), 200


@bp.route('/api/submit-request', methods=['POST'])
@handle_errors
def submit_request_route():
    """
    Submit new result update request with payment screenshot.
    Supports both multipart form data AND JSON payload with pre-uploaded screenshot URL.
    """
    import json
    
    is_json = request.is_json
    if is_json:
        data = request.get_json(silent=True) or {}
        rollno = str(data.get('rollno', '')).strip()
        chat_id = str(data.get('chatId', '')).strip()
        changes = data.get('changes', [])
        user_message = str(data.get('userMessage', '')).strip()
        student_data = data.get('studentData', {})
        screenshot_url = data.get('screenshotUrl') or data.get('paymentScreenshotUrl')
        screenshot_path = data.get('screenshotPath') or data.get('paymentScreenshotPath')
        screenshot_file = None
        screenshot_filename = ""
    else:
        rollno = request.form.get('rollno', '').strip()
        chat_id = request.form.get('chatId', '').strip()
        changes_json = request.form.get('changes', '[]')
        user_message = request.form.get('userMessage', '').strip()
        student_data_json = request.form.get('studentData', '{}')
        screenshot_url = request.form.get('screenshotUrl') or request.form.get('paymentScreenshotUrl')
        screenshot_path = request.form.get('screenshotPath') or request.form.get('paymentScreenshotPath')

        try:
            changes = json.loads(changes_json)
        except Exception:
            return jsonify({
                'success': False,
                'error': 'Invalid changes JSON format',
                'errorCode': ErrorCodes.VALIDATION_ERROR
            }), 400

        try:
            student_data = json.loads(student_data_json)
        except Exception:
            student_data = {}

        screenshot_file = request.files.get('paymentScreenshot')
        screenshot_filename = screenshot_file.filename if screenshot_file else ""

    # Call service
    res, status_code = create_new_request(
        rollno=rollno,
        chat_id=chat_id,
        changes=changes,
        user_message=user_message,
        screenshot_file=screenshot_file,
        screenshot_filename=screenshot_filename,
        screenshot_url=screenshot_url,
        screenshot_path=screenshot_path,
        student_data=student_data,
        ip_address=request.remote_addr or "127.0.0.1",
        user_agent=request.headers.get('User-Agent', '')
    )

    return jsonify(res), status_code


@bp.route('/api/request/<request_id>/status', methods=['GET'])
@handle_errors
def request_status_route(request_id):
    """Get status and details of a specific request by request_id"""
    res, status_code = get_request_status(request_id)
    return jsonify(res), status_code


@bp.route('/api/preview-updates', methods=['POST'])
@handle_errors
def preview_updates_route():
    """
    Preview what will be updated across all target databases before actual submission.
    Shows which columns exist, current values, and planned updates.
    """
    data = request.get_json(silent=True) or {}
    updates = data.get('updates', [])
    rollno = data.get('rollno', '').strip()

    if not updates:
        return jsonify({
            'success': False,
            'error': 'No updates provided',
            'errorCode': ErrorCodes.VALIDATION_ERROR
        }), 400

    if not rollno:
        return jsonify({
            'success': False,
            'error': 'rollno is required',
            'errorCode': ErrorCodes.VALIDATION_ERROR
        }), 400

    primary_keys = {"rollno": rollno, "semester": Config.SEMESTER}
    result = db_preview_updates(updates, primary_keys)

    return jsonify({
        'success': True,
        'preview': result
    })


@bp.route('/api/generate-payment-qr', methods=['POST'])
@handle_errors
def generate_payment_qr_route():
    """Generate UPI payment QR code with amount, rollno, student name and changes"""
    data = request.get_json(silent=True) or {}
    rollno = data.get('rollno', '')
    student_name = data.get('studentName', '')
    amount = data.get('amount')
    changes = data.get('changes') or data.get('updates')

    qr_data = generate_qr_code(amount=amount, rollno=rollno, student_name=student_name, changes=changes)
    return jsonify({
        'success': True,
        'qrCode': qr_data
    })


# ============================================================
# DIRECT CELL UPDATE (Admin flow only)
# ============================================================
@bp.route('/api/update-cells', methods=['POST'])
@handle_errors
@require_admin
def update_cells_route():
    """
    Execute per-cell updates against the real result databases.

    Body: { updates: [{column, value, is_null?}], rollno: str }

    Flow: snapshot -> telegram notify -> batch execute -> audit log.
    The pre-update snapshot is stored so /api/admin/revert-update can restore it.
    """
    from utils.decorators import get_token_from_request, decode_jwt_token
    from database.sql_runner import execute_batch_updates, fetch_row_snapshot
    from database.firebase_db import create_audit_log, create_snapshot_before
    from services.telegram_service import send_direct_update_notification, send_update_result_notification
    from utils.constants import AuditActions, ActorType
    from utils.helpers import generate_request_id, get_ist_iso

    payload = get_token_from_request()
    actor = 'anonymous'
    if payload:
        claims = decode_jwt_token(payload) or {}
        actor = claims.get('username') or claims.get('id') or 'admin'

    data = request.get_json(silent=True) or {}
    updates = data.get('updates', [])
    rollno = str(data.get('rollno', '')).strip()

    if not updates:
        return jsonify({'success': False, 'error': 'Koi updates nahi'}), 400
    if not rollno:
        return jsonify({'success': False, 'error': 'rollno chahiye'}), 400

    primary_keys = {'rollno': rollno, 'semester': Config.SEMESTER}

    # 1. Snapshot BEFORE update (enables revert)
    snapshot_before = fetch_row_snapshot(primary_keys)
    request_id = generate_request_id(rollno)

    # 2. Notify admin before writing
    try:
        send_direct_update_notification(rollno=rollno, updates=updates, actor=actor, request_id=request_id)
    except Exception as e:
        current_app.logger.warning(f"[TELEGRAM] pre-update notify failed: {e}")

    # 3. Execute
    result = execute_batch_updates(updates=updates, primary_keys=primary_keys)

    # 4. Persist snapshot for revert + audit
    if snapshot_before:
        try:
            create_snapshot_before(request_id, {
                'primaryKeys': primary_keys,
                'updates': updates,
                'snapshotBefore': snapshot_before,
                'actor': actor,
                'createdAt': get_ist_iso(),
            })
        except Exception as e:
            current_app.logger.warning(f"[REVERT SNAPSHOT] save failed: {e}")

    create_audit_log({
        'action': AuditActions.UPDATE_EXECUTED,
        'actor': actor,
        'actorType': ActorType.ADMIN,
        'rollno': rollno,
        'status': 'SUCCESS' if result.get('success') else 'PARTIAL',
        'details': {
            'requestId': request_id,
            'cells': len(updates),
            'successCount': result.get('success_count', 0),
            'failureCount': result.get('failure_count', 0),
        },
        'ip': request.remote_addr,
    })

    # 5. Notify admin after write
    try:
        send_update_result_notification(rollno=rollno, result=result, request_id=request_id)
    except Exception as e:
        current_app.logger.warning(f"[TELEGRAM] post-update notify failed: {e}")

    return jsonify(result)

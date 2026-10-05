# -*- coding: utf-8 -*-
"""
Request Service — Manages student request lifecycle: submission, validation,
before-snapshot capture, status queries, and notifications.
"""

import logging
from config import Config
from database.firebase_db import (
    create_update_request,
    get_update_request,
    check_user_blocked,
    check_pending_request,
    check_cooldown,
    create_snapshot_before,
    create_audit_log,
    get_pending_requests
)
from database.sql_runner import fetch_row_snapshot
from services.cloudinary_service import upload_payment_screenshot
from services.payment_service import calculate_update_fee
from services.telegram_service import send_admin_notification
from utils.constants import RequestStatus, ErrorCodes, AuditActions, ActorType
from utils.validators import (
    validate_rollno,
    validate_chatid,
    validate_message,
    validate_changes,
    validate_file
)
from utils.helpers import generate_request_id, format_ist_time

logger = logging.getLogger('request_service')


def create_new_request(
    rollno,
    chat_id,
    changes,
    user_message="",
    screenshot_file=None,
    screenshot_filename="",
    screenshot_url=None,
    screenshot_path=None,
    student_data=None,
    ip_address="127.0.0.1",
    user_agent=""
):
    """
    Complete request submission workflow:
    1. Validate inputs & enforce update limits per column type
    2. Dynamic fee calculation
    3. Check blocked / duplicate / cooldown
    4. Process payment screenshot (pre-uploaded or newly uploaded)
    5. Capture before/after snapshot
    6. Save optimized compact document in Firebase
    7. Notify Admin via Telegram
    8. Write audit log
    """
    # 1. Input validations
    valid_roll, err_roll = validate_rollno(rollno)
    if not valid_roll:
        return {"success": False, "error": err_roll, "errorCode": ErrorCodes.INVALID_ROLLNO}, 400

    valid_msg, err_msg = validate_message(user_message)
    if not valid_msg:
        return {"success": False, "error": err_msg, "errorCode": ErrorCodes.VALIDATION_ERROR}, 400

    valid_changes, err_changes = validate_changes(changes)
    if not valid_changes:
        return {"success": False, "error": err_changes, "errorCode": ErrorCodes.VALIDATION_ERROR}, 400

    # Dynamic fee calculation & column update limits (a1te max 2, ap1te max 3)
    fee_calc = calculate_update_fee(changes)
    if not fee_calc.get("valid"):
        return {
            "success": False,
            "error": fee_calc.get("error") or "Update limit exceeded",
            "errorCode": ErrorCodes.VALIDATION_ERROR
        }, 400

    final_amount = fee_calc.get("amount", Config.PAYMENT_AMOUNT)

    # Screenshot validation
    if not screenshot_file and not screenshot_url:
        return {"success": False, "error": "Payment screenshot is required", "errorCode": ErrorCodes.FILE_UPLOAD_ERROR}, 400

    if screenshot_file:
        valid_file, err_file = validate_file(screenshot_file, filename=screenshot_filename)
        if not valid_file:
            return {"success": False, "error": err_file, "errorCode": ErrorCodes.INVALID_PAYMENT_SCREENSHOT}, 400

    roll_str = str(rollno).strip()
    chat_str = str(chat_id).strip() if chat_id else ""

    # 2. Block, pending, cooldown checks
    is_blocked, block_reason = check_user_blocked(roll_str)
    if is_blocked:
        return {"success": False, "error": block_reason or "Account suspended", "errorCode": ErrorCodes.USER_BLOCKED}, 403

    has_pending, existing_id = check_pending_request(roll_str)
    if has_pending:
        return {
            "success": False,
            "error": "Request already pending. Please wait for approval.",
            "existingRequestId": existing_id,
            "errorCode": ErrorCodes.DUPLICATE_REQUEST
        }, 409

    in_cooldown, cd_until = check_cooldown(roll_str)
    if in_cooldown:
        return {
            "success": False,
            "error": f"Submission is in cooldown until {cd_until}",
            "cooldownUntil": cd_until,
            "errorCode": ErrorCodes.COOLDOWN_ACTIVE
        }, 429

    # Generate unique Request ID
    request_id = generate_request_id(roll_str)

    # 3. Process Screenshot
    final_screenshot_url = screenshot_url
    final_screenshot_path = screenshot_path

    if screenshot_file:
        upload_res = upload_payment_screenshot(screenshot_file, request_id, filename=screenshot_filename)
        final_screenshot_url = upload_res.get("url")
        final_screenshot_path = upload_res.get("localPath")

    # 4. Normalize Before/After values for each change
    clean_changes = []
    columns_to_watch = []
    for c in changes:
        if isinstance(c, dict):
            col = str(c.get("column", "")).strip()
            before_val = c.get("before") if c.get("before") is not None else c.get("oldValue")
            after_val = c.get("after") if c.get("after") is not None else (c.get("newValue") if c.get("newValue") is not None else c.get("value"))
            
            clean_changes.append({
                "column": col,
                "before": before_val,
                "after": after_val,
                "oldValue": before_val,
                "newValue": after_val,
                "value": after_val,
                "is_null": bool(c.get("is_null", False)) or (after_val is None) or (col == "cop" and (not after_val or str(after_val).strip() in ("", "None", "null"))),
                "subject": str(c.get("subject") or "").strip(),
                "label": str(c.get("label") or "").strip(),
                "isDerived": bool(c.get("isDerived", False))
            })
            if col:
                columns_to_watch.append(col)

    # 5. Capture snapshot before update for reliable revert
    primary_keys = {"rollno": roll_str, "semester": Config.SEMESTER}
    submitted_old = {
        c.get("column"): (c.get("before") if c.get("before") is not None else c.get("oldValue"))
        for c in clean_changes
        if c.get("column")
    }
    try:
        before_snapshot = fetch_row_snapshot(primary_keys, columns_to_watch)
        if not before_snapshot or not isinstance(before_snapshot, dict):
            before_snapshot = {
                "primaryKeys": primary_keys,
                "fallback": True,
                "targets": {}
            }
    except Exception as e:
        logger.warning(f"Failed to fetch live DB snapshot: {e}")
        before_snapshot = {
            "primaryKeys": primary_keys,
            "fallback": True,
            "targets": {}
        }

    before_snapshot["submittedOldValues"] = submitted_old
    before_snapshot["changes"] = clean_changes
    before_snapshot["rollno"] = roll_str
    create_snapshot_before(request_id, before_snapshot)

    # 6. Assemble compact & optimized request document for Firebase
    student_meta = student_data or {}
    if not isinstance(student_meta, dict):
        student_meta = {}
    
    student_name = (
        student_meta.get("name")
        or student_meta.get("studentName")
        or (student_meta.get("student", {}).get("name") if isinstance(student_meta.get("student"), dict) else None)
    )
    college_name = (
        student_meta.get("collegeName")
        or student_meta.get("college")
        or (student_meta.get("student", {}).get("collegeName") if isinstance(student_meta.get("student"), dict) else None)
    )
    
    # Fallback to local_db if student_name is empty
    if not student_name or student_name in ("N/A", "Student", "None", ""):
        try:
            from database.local_db import get_student_by_rollno
            st_rec = get_student_by_rollno(roll_str)
            if st_rec and st_rec.get("name"):
                student_name = st_rec.get("name")
        except Exception:
            pass

    request_doc = {
        "requestId": request_id,
        "rollno": roll_str,
        "studentName": student_name or "Student",
        "college": college_name or "",
        "amount": final_amount,
        "currency": Config.PAYMENT_CURRENCY,
        "changes": clean_changes,
        "paymentScreenshotUrl": final_screenshot_url,
        "paymentScreenshotPath": final_screenshot_path,
        "status": RequestStatus.PENDING,
        "submittedAt": format_ist_time(),
        "userMessage": user_message or "",
        "reviewedAt": None,
        "rejectionReason": None
    }

    create_update_request(request_doc)

    # 7. Send Telegram Admin notification
    telegram_sent = False
    try:
        telegram_sent = send_admin_notification(request_doc)
    except Exception as e:
        logger.error(f"Error sending telegram admin notification: {e}")

    # 8. Audit log
    create_audit_log({
        "action": AuditActions.REQUEST_SUBMITTED,
        "actor": f"user_{roll_str}",
        "actorType": ActorType.USER,
        "rollno": roll_str,
        "requestId": request_id,
        "status": "SUCCESS",
        "details": {
            "amount": final_amount,
            "changesCount": len(clean_changes),
            "screenshotUrl": final_screenshot_url,
            "telegramNotified": telegram_sent
        },
        "ip": ip_address,
        "userAgent": user_agent
    })

    return {
        "success": True,
        "requestId": request_id,
        "message": "Request submitted successfully. Awaiting administrator review.",
        "telegramNotificationSent": telegram_sent,
        "data": {
            "requestId": request_id,
            "amount": final_amount,
            "currency": Config.PAYMENT_CURRENCY,
            "status": RequestStatus.PENDING,
            "submittedAt": request_doc["submittedAt"]
        }
    }, 201


def get_request_status(request_id):
    """Fetch status and public details of request"""
    if not request_id:
        return {"success": False, "error": "requestId is required", "errorCode": ErrorCodes.VALIDATION_ERROR}, 400

    req = get_update_request(request_id)
    if not req:
        return {"success": False, "error": f"Request {request_id} not found", "errorCode": ErrorCodes.REQUEST_NOT_FOUND}, 404

    return {
        "success": True,
        "request": {
            "requestId": req.get("requestId"),
            "rollno": req.get("rollno"),
            "studentName": req.get("studentName"),
            "status": req.get("status"),
            "submittedAt": req.get("submittedAt"),
            "approvedAt": req.get("approvedAt"),
            "rejectedAt": req.get("rejectedAt"),
            "revertedAt": req.get("revertedAt"),
            "rejectionReason": req.get("rejectionReason"),
            "changes": req.get("changes", []),
            "userMessage": req.get("userMessage")
        }
    }, 200


def get_all_pending_requests(page=1, limit=10, status=RequestStatus.PENDING, rollno=None):
    """Fetch paginated pending requests for admin"""
    result = get_pending_requests(page=page, limit=limit, rollno=rollno, status=status)
    return {
        "success": True,
        "requests": result.get("requests", []),
        "total": result.get("total", 0),
        "page": result.get("page", 1),
        "pages": result.get("pages", 1)
    }, 200

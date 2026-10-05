# -*- coding: utf-8 -*-
"""
Firebase Database operations module (with automatic local persistent fallback).
Handles update requests, audit logs, user blocking, snapshots, and cooldowns.
"""

import os
import json
import time
import threading
import logging
from datetime import datetime, timedelta
from config import Config, IST
from utils.constants import RequestStatus, ActorType, AuditActions
from utils.helpers import get_ist_now, format_ist_time, generate_log_id

logger = logging.getLogger('firebase_db')

_firebase_initialized = False
_firebase_db_ref = None
_firestore_client = None

# Thread lock for fallback local storage
_fallback_lock = threading.Lock()


def _get_fallback_data():
    """Read local fallback storage"""
    filepath = Config.FALLBACK_DATA_PATH
    if not os.path.exists(filepath):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        initial_data = {
            "updateRequests": {},
            "auditLogs": [],
            "blockedUsers": {},
            "snapshotBeforeUpdate": {},
            "snapshotAfterRevert": {},
            "rejectionCooldowns": {},
            "sessions": {}
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(initial_data, f, indent=2)
        return initial_data

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading fallback data: {e}")
        return {
            "updateRequests": {},
            "auditLogs": [],
            "blockedUsers": {},
            "snapshotBeforeUpdate": {},
            "snapshotAfterRevert": {},
            "rejectionCooldowns": {},
            "sessions": {}
        }


def _save_fallback_data(data):
    """Save local fallback storage atomically"""
    filepath = Config.FALLBACK_DATA_PATH
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    temp_path = filepath + '.tmp'
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        try:
            os.replace(temp_path, filepath)
        except Exception:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Error saving fallback data: {e}")


def initialize_firebase():
    """Initialize Firebase Admin SDK if service account file exists"""
    global _firebase_initialized, _firestore_client, _firebase_db_ref
    if _firebase_initialized:
        return True

    sdk_path = Config.FIREBASE_ADMIN_SDK_PATH
    if sdk_path and os.path.exists(sdk_path):
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore, db

            if not firebase_admin._apps:
                cred = credentials.Certificate(sdk_path)
                options = {}
                if Config.FIREBASE_DATABASE_URL:
                    options['databaseURL'] = Config.FIREBASE_DATABASE_URL
                firebase_admin.initialize_app(cred, options)

            try:
                _firestore_client = firestore.client()
            except Exception:
                _firestore_client = None

            if Config.FIREBASE_DATABASE_URL:
                try:
                    _firebase_db_ref = db.reference()
                except Exception:
                    _firebase_db_ref = None

            _firebase_initialized = True
            logger.info("✅ Firebase Admin SDK initialized successfully")
            return True
        except Exception as e:
            logger.warning(f"Firebase Admin SDK initialization failed: {e}. Using persistent storage.")
            _firebase_initialized = False
            return False
    else:
        logger.info(f"Firebase credentials not found at '{sdk_path}'. Using local persistent database.")
        _firebase_initialized = False
        return False


# Initialize on import
initialize_firebase()


# ============================================================
# UPDATE REQUESTS
# ============================================================
def create_update_request(data):
    """
    Create a new update request document.
    Status defaults to 'PENDING'.
    """
    request_id = data.get('requestId')
    if not request_id:
        from utils.helpers import generate_request_id
        request_id = generate_request_id(data.get('rollno'))
        data['requestId'] = request_id

    if 'status' not in data:
        data['status'] = RequestStatus.PENDING
    if 'submittedAt' not in data:
        data['submittedAt'] = format_ist_time()

    # If Firebase Firestore is active
    if _firestore_client:
        try:
            _firestore_client.collection('updateRequests').document(request_id).set(data)
            return request_id
        except Exception as e:
            logger.error(f"Firestore create_update_request error: {e}")

    # Fallback local persistence
    with _fallback_lock:
        db_data = _get_fallback_data()
        db_data["updateRequests"][request_id] = data
        _save_fallback_data(db_data)

    return request_id


def get_update_request(request_id):
    """Fetch update request by requestId"""
    if _firestore_client:
        try:
            doc = _firestore_client.collection('updateRequests').document(request_id).get()
            if doc.exists:
                return doc.to_dict()
        except Exception as e:
            logger.error(f"Firestore get_update_request error: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        return db_data["updateRequests"].get(request_id)


def update_request_status(request_id, status=None, extra_data=None, new_status=None, admin_id=None, comment=None, rejection_reason=None, **kwargs):
    """Update request status and merge extra metadata flexibly"""
    final_status = status or new_status
    update_fields = {'updatedAt': format_ist_time()}
    if final_status:
        update_fields['status'] = final_status
    if admin_id:
        update_fields['approvedBy'] = admin_id
        update_fields['reviewedBy'] = admin_id
    if comment:
        update_fields['comment'] = comment
    if rejection_reason:
        update_fields['rejectionReason'] = rejection_reason
    if extra_data and isinstance(extra_data, dict):
        update_fields.update(extra_data)
    if kwargs:
        update_fields.update(kwargs)

    firestore_success = False
    if _firestore_client:
        try:
            _firestore_client.collection('updateRequests').document(request_id).update(update_fields)
            firestore_success = True
        except Exception as e:
            logger.error(f"Firestore update_request_status error: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        if request_id in db_data["updateRequests"]:
            db_data["updateRequests"][request_id].update(update_fields)
            _save_fallback_data(db_data)
            return True
        elif firestore_success:
            return True
        return False


def get_pending_requests(page=1, limit=10, rollno=None, status=None):
    """Query requests with pagination, status filter, and optional rollno filter"""
    if _firestore_client:
        try:
            query = _firestore_client.collection('updateRequests')
            if status:
                query = query.where('status', '==', status)
            if rollno:
                query = query.where('rollno', '==', str(rollno).strip())
            docs = query.order_by('submittedAt', direction='DESCENDING').stream()
            all_reqs = [d.to_dict() for d in docs]
            total = len(all_reqs)
            start = (page - 1) * limit
            end = start + limit
            pages = (total + limit - 1) // limit if total > 0 else 1
            return {
                "requests": all_reqs[start:end],
                "total": total,
                "page": page,
                "pages": pages
            }
        except Exception as e:
            logger.warning(f"Firestore query failed, using fallback: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        reqs = list(db_data["updateRequests"].values())

        if status:
            reqs = [r for r in reqs if r.get('status') == status]
        if rollno:
            roll_str = str(rollno).strip()
            reqs = [r for r in reqs if str(r.get('rollno', '')).strip() == roll_str]

        reqs.sort(key=lambda x: str(x.get('submittedAt', '')), reverse=True)

        total = len(reqs)
        start = (page - 1) * limit
        end = start + limit
        pages = (total + limit - 1) // limit if total > 0 else 1

        return {
            "requests": reqs[start:end],
            "total": total,
            "page": page,
            "pages": pages
        }


def check_pending_request(rollno):
    """
    Check if a roll number already has an active PENDING request.
    Returns: (has_pending: bool, request_id: str or None)
    """
    roll_str = str(rollno).strip()

    if _firestore_client:
        try:
            docs = _firestore_client.collection('updateRequests')\
                .where('rollno', '==', roll_str)\
                .where('status', '==', RequestStatus.PENDING)\
                .limit(1).stream()
            for doc in docs:
                data = doc.to_dict()
                return True, data.get('requestId')
        except Exception as e:
            logger.warning(f"Firestore check_pending_request error: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        for req in db_data["updateRequests"].values():
            if str(req.get('rollno', '')).strip() == roll_str and req.get('status') == RequestStatus.PENDING:
                return True, req.get('requestId')
        return False, None


# ============================================================
# USER BLOCKING & UNBLOCKING
# ============================================================
def check_user_blocked(rollno):
    """
    Check if roll number is blocked.
    Returns: (is_blocked: bool, reason: str or None)
    """
    roll_str = str(rollno).strip()

    if _firestore_client:
        try:
            doc = _firestore_client.collection('blockedUsers').document(roll_str).get()
            if doc.exists:
                data = doc.to_dict()
                if data.get('isActive', True):
                    return True, data.get('reason', 'Account suspended. Contact admin')
        except Exception as e:
            logger.warning(f"Firestore check_user_blocked error: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        user_block = db_data["blockedUsers"].get(roll_str)
        if user_block and user_block.get('isActive', True):
            return True, user_block.get('reason', 'Account suspended. Contact admin')
        return False, None


def block_user(rollno, reason="Blocked by admin", admin_id=None, blocked_by=None, **kwargs):
    """Block a student by roll number"""
    roll_str = str(rollno).strip()
    acting_admin = blocked_by or admin_id or kwargs.get('blockedBy') or "system"
    data = {
        "rollno": roll_str,
        "reason": reason,
        "adminId": str(acting_admin),
        "blockedBy": str(acting_admin),
        "blockedAt": format_ist_time(),
        "isActive": True
    }

    if _firestore_client:
        try:
            _firestore_client.collection('blockedUsers').document(roll_str).set(data)
        except Exception as e:
            logger.error(f"Firestore block_user error: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        db_data["blockedUsers"][roll_str] = data
        _save_fallback_data(db_data)
    return {"success": True, "message": f"Roll No {roll_str} blocked"}


def unblock_user(rollno, admin_id=None, unblocked_by=None, **kwargs):
    """Unblock a student by roll number"""
    roll_str = str(rollno).strip()
    acting_admin = unblocked_by or admin_id or kwargs.get('unblockedBy') or "system"
    data = {
        "isActive": False,
        "unblockedAt": format_ist_time(),
        "unblockedBy": str(acting_admin)
    }

    if _firestore_client:
        try:
            _firestore_client.collection('blockedUsers').document(roll_str).update(data)
        except Exception as e:
            logger.error(f"Firestore unblock_user error: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        if roll_str in db_data["blockedUsers"]:
            db_data["blockedUsers"][roll_str].update(data)
            _save_fallback_data(db_data)
    return {"success": True, "message": f"Roll No {roll_str} unblocked"}


# ============================================================
# COOLDOWN MANAGEMENT
# ============================================================
def create_rejection_cooldown(rollno, reason, hours=24):
    """Set cooldown for rejected roll number (default 24h)"""
    roll_str = str(rollno).strip()
    now = get_ist_now()
    cooldown_until = now + timedelta(hours=hours)
    data = {
        "rollno": roll_str,
        "reason": reason,
        "createdAt": format_ist_time(now),
        "cooldownUntil": format_ist_time(cooldown_until),
        "cooldownTimestamp": cooldown_until.timestamp()
    }

    if _firestore_client:
        try:
            _firestore_client.collection('rejectionCooldowns').document(roll_str).set(data)
        except Exception as e:
            logger.error(f"Firestore create_rejection_cooldown error: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        db_data["rejectionCooldowns"][roll_str] = data
        _save_fallback_data(db_data)
    return data["cooldownUntil"]


def check_cooldown(rollno):
    """
    Check if roll number is under rejection cooldown.
    Returns: (is_cooldown: bool, cooldown_until: str or None)
    """
    roll_str = str(rollno).strip()
    now_ts = get_ist_now().timestamp()

    if _firestore_client:
        try:
            doc = _firestore_client.collection('rejectionCooldowns').document(roll_str).get()
            if doc.exists:
                data = doc.to_dict()
                if data.get('cooldownTimestamp', 0) > now_ts:
                    return True, data.get('cooldownUntil')
        except Exception as e:
            logger.warning(f"Firestore check_cooldown error: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        cd = db_data["rejectionCooldowns"].get(roll_str)
        if cd and cd.get('cooldownTimestamp', 0) > now_ts:
            return True, cd.get('cooldownUntil')
        return False, None


# ============================================================
# SNAPSHOTS (BEFORE UPDATE & AFTER REVERT)
# ============================================================
def create_snapshot_before(request_id, snapshot):
    """Save snapshotBeforeUpdate for rollback / revert operations"""
    data = {
        "requestId": request_id,
        "snapshot": snapshot,
        "createdAt": format_ist_time()
    }

    if _firestore_client:
        try:
            _firestore_client.collection('snapshotBeforeUpdate').document(request_id).set(data)
        except Exception as e:
            logger.error(f"Firestore create_snapshot_before error: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        db_data["snapshotBeforeUpdate"][request_id] = data
        _save_fallback_data(db_data)
    return True


def get_snapshot_before(request_id):
    """Retrieve snapshotBeforeUpdate for given request_id"""
    if _firestore_client:
        try:
            doc = _firestore_client.collection('snapshotBeforeUpdate').document(request_id).get()
            if doc.exists:
                return doc.to_dict().get('snapshot')
        except Exception as e:
            logger.error(f"Firestore get_snapshot_before error: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        item = db_data["snapshotBeforeUpdate"].get(request_id)
        if item:
            return item.get('snapshot')
        return None


def create_snapshot_after(request_id, snapshot):
    """Save snapshotAfterRevert (backup of values that were reverted from)"""
    data = {
        "requestId": request_id,
        "snapshot": snapshot,
        "createdAt": format_ist_time()
    }

    if _firestore_client:
        try:
            _firestore_client.collection('snapshotAfterRevert').document(request_id).set(data)
        except Exception as e:
            logger.error(f"Firestore create_snapshot_after error: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        db_data["snapshotAfterRevert"][request_id] = data
        _save_fallback_data(db_data)
    return True


# ============================================================
# AUDIT LOGS
# ============================================================
def create_audit_log(log_data):
    """Create a structured audit log entry"""
    log_id = log_data.get('logId') or generate_log_id()
    log_data['logId'] = log_id
    if 'timestamp' not in log_data:
        log_data['timestamp'] = format_ist_time()

    if _firestore_client:
        try:
            _firestore_client.collection('auditLogs').document(log_id).set(log_data)
            return log_id
        except Exception as e:
            logger.error(f"Firestore create_audit_log error: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        db_data["auditLogs"].append(log_data)
        # Cap audit logs to latest 5000 in fallback
        if len(db_data["auditLogs"]) > 5000:
            db_data["auditLogs"] = db_data["auditLogs"][-5000:]
        _save_fallback_data(db_data)
    return log_id


def get_audit_logs(filters=None, page=1, limit=50):
    """Query audit logs with filters (action, rollno, dateFrom, dateTo) and pagination"""
    filters = filters or {}
    action_filter = filters.get('action')
    rollno_filter = filters.get('rollno')
    date_from = filters.get('dateFrom')
    date_to = filters.get('dateTo')

    with _fallback_lock:
        db_data = _get_fallback_data()
        logs = list(db_data.get("auditLogs", []))

        if action_filter:
            logs = [l for l in logs if l.get('action') == action_filter]
        if rollno_filter:
            r_str = str(rollno_filter).strip()
            logs = [l for l in logs if str(l.get('rollno', '')).strip() == r_str]
        if date_from:
            logs = [l for l in logs if str(l.get('timestamp', '')) >= date_from]
        if date_to:
            # Append end of day time
            end_date = f"{date_to} 23:59:59" if len(date_to) == 10 else date_to
            logs = [l for l in logs if str(l.get('timestamp', '')) <= end_date]

        logs.sort(key=lambda x: str(x.get('timestamp', '')), reverse=True)

        total = len(logs)
        start = (page - 1) * limit
        end = start + limit
        pages = (total + limit - 1) // limit if total > 0 else 1

        return {
            "logs": logs[start:end],
            "total": total,
            "page": page,
            "pages": pages
        }


def get_latest_revertable_request_by_rollno(rollno):
    """Find the most recent approved/executed request for a roll number with a snapshot"""
    roll_str = str(rollno).strip()
    if _firestore_client:
        try:
            docs = _firestore_client.collection('updateRequests')\
                .where('rollno', '==', roll_str)\
                .stream()
            reqs = [d.to_dict() for d in docs if d.to_dict().get('status') in [RequestStatus.APPROVED, RequestStatus.EXECUTED, 'APPROVED', 'EXECUTED']]
            reqs.sort(key=lambda x: str(x.get('submittedAt', '') or x.get('createdAt', '')), reverse=True)
            if reqs:
                req = reqs[0]
                req_id = req.get('requestId')
                snapshot = get_snapshot_before(req_id)
                if not snapshot:
                    snapshot = {
                        "primaryKeys": {"rollno": roll_str, "semester": Config.SEMESTER},
                        "changes": req.get("changes", []),
                        "submittedOldValues": {c.get("column"): (c.get("before") if c.get("before") is not None else c.get("oldValue")) for c in req.get("changes", []) if c.get("column")}
                    }
                return req, snapshot
        except Exception as e:
            logger.warning(f"Firestore get_latest_revertable_request_by_rollno error: {e}")

    with _fallback_lock:
        db_data = _get_fallback_data()
        reqs = [
            r for r in db_data["updateRequests"].values()
            if str(r.get('rollno', '')).strip() == roll_str
            and r.get('status') in [RequestStatus.APPROVED, RequestStatus.EXECUTED, 'APPROVED', 'EXECUTED']
        ]
        reqs.sort(key=lambda x: str(x.get('submittedAt', '') or x.get('createdAt', '')), reverse=True)
        if reqs:
            req = reqs[0]
            req_id = req.get('requestId')
            snapshot = get_snapshot_before(req_id)
            if not snapshot:
                snapshot = {
                    "primaryKeys": {"rollno": roll_str, "semester": Config.SEMESTER},
                    "changes": req.get("changes", []),
                    "submittedOldValues": {c.get("column"): (c.get("before") if c.get("before") is not None else c.get("oldValue")) for c in req.get("changes", []) if c.get("column")}
                }
            return req, snapshot
    return None, None


def get_request_history(rollno, page=1, limit=20):
    """Get all requests history for a student roll number"""
    roll_str = str(rollno).strip()
    with _fallback_lock:
        db_data = _get_fallback_data()
        reqs = [r for r in db_data["updateRequests"].values() if str(r.get('rollno', '')).strip() == roll_str]
        reqs.sort(key=lambda x: str(x.get('submittedAt', '')), reverse=True)

        total = len(reqs)
        approved_count = sum(1 for r in reqs if r.get('status') == RequestStatus.APPROVED)
        rejected_count = sum(1 for r in reqs if r.get('status') == RequestStatus.REJECTED)
        reverted_count = sum(1 for r in reqs if r.get('status') == RequestStatus.REVERTED)
        pending_count = sum(1 for r in reqs if r.get('status') == RequestStatus.PENDING)

        start = (page - 1) * limit
        end = start + limit

        return {
            "rollno": roll_str,
            "totalRequests": total,
            "approvedCount": approved_count,
            "rejectedCount": rejected_count,
            "revertedCount": reverted_count,
            "pendingCount": pending_count,
            "requests": reqs[start:end]
        }

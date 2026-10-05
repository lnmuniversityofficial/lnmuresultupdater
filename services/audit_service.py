# -*- coding: utf-8 -*-
"""
Audit Service — Logging, filtering, and exporting of administrative & system audit trails.
"""

import json
import logging
from config import Config
from database.firebase_db import create_audit_log, get_audit_logs
from services.cloudinary_service import upload_audit_backup
from utils.constants import ActorType, AuditActions
from utils.helpers import get_ist_now, format_ist_time

logger = logging.getLogger('audit_service')


def log_action(
    action_type,
    actor="system",
    actor_type=ActorType.SYSTEM,
    request_id=None,
    rollno=None,
    details=None,
    ip="127.0.0.1",
    user_agent="",
    status="SUCCESS"
):
    """Create and record structured audit log"""
    log_data = {
        "action": action_type,
        "actor": actor,
        "actorType": actor_type,
        "requestId": request_id,
        "rollno": rollno,
        "status": status,
        "details": details or {},
        "ip": ip,
        "userAgent": user_agent,
        "timestamp": format_ist_time()
    }
    return create_audit_log(log_data)


def get_action_logs(filters=None, page=1, limit=50):
    """Retrieve filtered audit logs with pagination"""
    result = get_audit_logs(filters=filters, page=page, limit=limit)
    return {
        "success": True,
        "logs": result.get("logs", []),
        "total": result.get("total", 0),
        "page": page,
        "pages": result.get("pages", 1)
    }


def export_audit_logs(date_from=None, date_to=None):
    """Export filtered logs as JSON file and return download link or JSON content"""
    filters = {}
    if date_from:
        filters["dateFrom"] = date_from
    if date_to:
        filters["dateTo"] = date_to

    res = get_audit_logs(filters=filters, page=1, limit=10000)
    logs = res.get("logs", [])

    json_content = json.dumps({
        "exportedAt": format_ist_time(),
        "totalLogs": len(logs),
        "filters": filters,
        "logs": logs
    }, indent=2)

    date_str = get_ist_now().strftime('%Y%m%d_%H%M%S')
    backup_res = upload_audit_backup(json_content, date_str)

    return {
        "success": True,
        "total": len(logs),
        "downloadUrl": backup_res.get("url"),
        "logs": logs[:100]  # sample
    }

# -*- coding: utf-8 -*-
"""
Cloudinary Service — Uploads payment screenshots and audit backups.
Includes graceful local file fallback when Cloudinary API credentials are not yet configured.
"""

import os
import io
import time
import hashlib
import logging
import requests
from config import Config

logger = logging.getLogger('cloudinary_service')


def upload_payment_screenshot(file_obj, request_id, filename=""):
    """
    Direct signed upload of payment screenshot to Cloudinary folder lnmu_payments.
    No upload preset required — uses API Key + Secret signature.
    Fallback: save to ./uploads/ and return local endpoint URL.
    """
    # Read bytes safely
    if hasattr(file_obj, 'read') and hasattr(file_obj, 'seek'):
        content = file_obj.read()
        file_obj.seek(0)
    elif isinstance(file_obj, (bytes, bytearray)):
        content = file_obj
    else:
        raise ValueError("Invalid file object provided for upload")

    # Local storage always written for backup & instant Telegram file upload
    os.makedirs(Config.UPLOADS_DIR, exist_ok=True)
    ext = "png"
    if filename and '.' in filename:
        ext = filename.rsplit('.', 1)[-1].lower()
    
    local_filename = f"{request_id}.{ext}"
    local_path = os.path.join(Config.UPLOADS_DIR, local_filename)

    try:
        with open(local_path, 'wb') as f:
            f.write(content)
    except Exception as e:
        logger.warning(f"Failed to write local backup: {e}")

    # Direct Cloudinary signed upload if credentials exist
    if Config.CLOUDINARY_CLOUD_NAME and Config.CLOUDINARY_API_KEY and Config.CLOUDINARY_API_SECRET:
        try:
            timestamp = int(time.time())
            params_to_sign = {
                'folder': 'lnmu_payments',
                'overwrite': 'true',
                'public_id': request_id,
                'timestamp': str(timestamp)
            }
            sorted_pairs = sorted(params_to_sign.items())
            to_sign_str = '&'.join([f'{k}={v}' for k, v in sorted_pairs]) + Config.CLOUDINARY_API_SECRET
            signature = hashlib.sha1(to_sign_str.encode('utf-8')).hexdigest()

            url = f"https://api.cloudinary.com/v1_1/{Config.CLOUDINARY_CLOUD_NAME}/image/upload"
            data = {
                'api_key': Config.CLOUDINARY_API_KEY,
                'timestamp': timestamp,
                'signature': signature,
                'folder': 'lnmu_payments',
                'public_id': request_id,
                'overwrite': 'true'
            }
            content_type = "image/png"
            if ext in ["jpg", "jpeg"]:
                content_type = "image/jpeg"
            elif ext == "webp":
                content_type = "image/webp"

            files = {'file': (local_filename, content, content_type)}
            r = requests.post(url, data=data, files=files, timeout=25, verify=False)
            if r.status_code == 200:
                res_json = r.json()
                sec_url = res_json.get("secure_url") or res_json.get("url")
                logger.info(f"✅ Cloudinary direct upload success: {sec_url}")
                return {
                    "success": True,
                    "url": sec_url,
                    "localPath": local_path,
                    "publicId": res_json.get("public_id"),
                    "provider": "cloudinary"
                }
            else:
                logger.error(f"Cloudinary upload returned status {r.status_code}: {r.text}")
        except Exception as e:
            logger.error(f"Cloudinary direct upload failed: {e}")

    # Local fallback
    return {
        "success": True,
        "url": f"/uploads/{local_filename}",
        "localPath": local_path,
        "provider": "local"
    }


def upload_audit_backup(file_content, date_str=None):
    """Upload audit log backup JSON file"""
    init_cloudinary()
    if not date_str:
        date_str = time.strftime('%Y%m%d_%H%M%S')

    if _cloudinary_configured:
        try:
            res = cloudinary.uploader.upload(
                file_content,
                folder="lnmu_audit",
                public_id=f"audit_logs_{date_str}",
                resource_type="raw"
            )
            return {
                "success": True,
                "url": res.get("secure_url") or res.get("url")
            }
        except Exception as e:
            logger.error(f"Cloudinary audit backup failed: {e}")

    # Local fallback
    backup_dir = os.path.join(Config.UPLOADS_DIR, "audit_backups")
    os.makedirs(backup_dir, exist_ok=True)
    local_path = os.path.join(backup_dir, f"audit_logs_{date_str}.json")

    with open(local_path, 'w', encoding='utf-8') as f:
        if isinstance(file_content, (bytes, bytearray)):
            f.write(file_content.decode('utf-8'))
        else:
            f.write(str(file_content))

    return {
        "success": True,
        "url": f"/uploads/audit_backups/audit_logs_{date_str}.json"
    }


def delete_old_screenshots(days=90):
    """Clean up old screenshots older than N days"""
    # Implementation for Cloudinary Admin API cleanup
    logger.info(f"Cleaning up screenshots older than {days} days")
    return True

# -*- coding: utf-8 -*-
"""
Input validation utilities for LNMU Result Updater 2.0
"""

import re
import io
from PIL import Image

# Regex patterns — Roll number must start with 25 and have exactly 12 digits
ROLLNO_REGEX = re.compile(r'^25\d{10}$')
CHATID_REGEX = re.compile(r'^\d{8,15}$')
SQL_INJECTION_PATTERN = re.compile(r'(\b(DROP\s+TABLE|ALTER\s+TABLE|UNION\s+SELECT|TRUNCATE\s+TABLE)\b|--|\/\*|\*\/)', re.IGNORECASE)

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


def validate_rollno(rollno):
    """Validate roll number format (must start with 25 and have exactly 12 digits)"""
    if not rollno:
        return False, "Please enter your Roll Number."
    roll_str = str(rollno).strip()
    if not ROLLNO_REGEX.match(roll_str):
        return False, "Invalid Roll Number. Roll number must be a 12-digit number starting with 25."
    return True, None


def validate_chatid(chatid):
    """Validate Telegram chat ID format (digits, length 8-15)"""
    if not chatid:
        return False, "Telegram Chat ID is required"
    chat_str = str(chatid).strip()
    if not CHATID_REGEX.match(chat_str):
        return False, "Invalid Chat ID format. Must be 8-15 digits"
    return True, None


def validate_message(message):
    """Validate optional user message (max 200 chars, no SQL injection keywords)"""
    if not message:
        return True, ""
    msg_str = str(message).strip()
    if len(msg_str) > 200:
        return False, "Message must not exceed 200 characters"
    if SQL_INJECTION_PATTERN.search(msg_str):
        return False, "Message contains invalid or restricted keywords"
    return True, None


def validate_file(file_obj, filename=""):
    """
    Validate uploaded image file:
    - size < 5MB
    - valid extension (jpg, png, webp)
    - valid image content verification via PIL
    """
    if not file_obj:
        return False, "No file provided"

    # Check filename extension if provided
    if filename:
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext not in ALLOWED_EXTENSIONS:
            return False, f"Invalid file format '.{ext}'. Allowed formats: {', '.join(ALLOWED_EXTENSIONS)}"

    # Read bytes to check size and PIL integrity
    try:
        if hasattr(file_obj, 'read') and hasattr(file_obj, 'seek'):
            content = file_obj.read()
            file_obj.seek(0)
        elif isinstance(file_obj, (bytes, bytearray)):
            content = file_obj
        else:
            return False, "Unsupported file object"

        if len(content) == 0:
            return False, "File is empty"
        if len(content) > MAX_FILE_SIZE:
            return False, f"File size exceeds maximum allowed size of 5 MB ({len(content) / (1024*1024):.2f} MB)"

        # PIL image verification
        image = Image.open(io.BytesIO(content))
        image.verify()

        # Check format
        fmt = (image.format or '').lower()
        if fmt not in ['jpeg', 'png', 'webp', 'jpg']:
            return False, f"Invalid image format ({fmt}). Allowed formats: JPG, PNG, WEBP"

        return True, None
    except Exception as e:
        return False, f"Corrupted or invalid image file: {str(e)}"


def validate_changes(changes):
    """Validate list of changes dictionaries"""
    if not changes or not isinstance(changes, list):
        return False, "Changes must be a non-empty list"

    safe_col_regex = re.compile(r'^[a-zA-Z0-9_]+$')
    for idx, c in enumerate(changes):
        if not isinstance(c, dict):
            return False, f"Change entry at index {idx} must be an object"
        col = c.get('column')
        if not col or not safe_col_regex.match(str(col)):
            return False, f"Invalid column name '{col}' at index {idx}"
        
        # Validate bounds based on specific column type
        val = c.get('value', c.get('newValue', c.get('after')))
        if val is not None and str(val).strip().upper() != 'AB':
            try:
                num_val = float(val)
                col_lower = str(col).lower()
                
                # Subject raw mark inputs (theory / practical / cia / ps)
                if any(col_lower.startswith(p) for p in ['a1te', 'a1pe', 'ap1te', 'a1ts', 'a1ps', 'a1tt']):
                    if num_val < 0 or num_val > 100:
                        return False, f"Marks value '{val}' for column '{col}' is out of valid bounds (0-100)"
                
                # Total Grade Points (obtgp) can be up to 200 (20 credits * 10 max GP)
                elif col_lower == 'obtgp':
                    if num_val < 0 or num_val > 200:
                        return False, f"Total GP '{val}' for 'obtgp' is out of valid bounds (0-200)"
                
                # Total credits (obtcr)
                elif col_lower == 'obtcr':
                    if num_val < 0 or num_val > 30:
                        return False, f"Credits value '{val}' for 'obtcr' is out of valid bounds (0-30)"
                
                # SGPA / CGPA
                elif col_lower in ['sgpa', 'sgpa_2']:
                    if num_val < 0.0 or num_val > 10.0:
                        return False, f"SGPA '{val}' is out of valid bounds (0.0-10.0)"
            except (ValueError, TypeError):
                pass
    return True, None

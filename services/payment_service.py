# -*- coding: utf-8 -*-
"""
Payment Service — UPI URL & QR Code generation, validation, and timer configuration.
"""

import io
import re
import base64
import urllib.parse
import qrcode
from config import Config
from utils.validators import validate_file


def calculate_update_fee(changes):
    """
    Calculate dynamic update fee based on edited columns and enforce limits:
    - a1te_n (Theory): Max Config.MAX_A1TE_UPDATES (default 2)
        1 update -> Config.PRICE_A1TE_1 (1000)
        2 updates -> Config.PRICE_A1TE_2 (1500)
    - ap1te_n (Practical): Max Config.MAX_AP1TE_UPDATES (default 3)
        1 update -> Config.PRICE_AP1TE_1 (500)
        2 or 3 updates -> Config.PRICE_AP1TE_2_OR_3 (1000)
    - Other columns: Config.PRICE_OTHER_PER_FIELD per field (500)
    """
    if not changes or not isinstance(changes, list):
        return {
            "valid": True,
            "amount": Config.PAYMENT_AMOUNT,
            "a1teCount": 0,
            "ap1teCount": 0,
            "otherCount": 0,
            "totalFields": 0,
            "breakdown": {"base": Config.PAYMENT_AMOUNT},
            "error": None
        }

    a1te_cols = []
    ap1te_cols = []
    other_cols = []

    derived_names = {
        "obtgp", "obtcr", "sgpa", "sgpa_2", "res_2", "resremark", "rs1", "cop"
    }

    for item in changes:
        col = ""
        is_derived = False
        if isinstance(item, dict):
            col = str(item.get("column", "")).strip().lower()
            is_derived = bool(item.get("isDerived", False))
        elif isinstance(item, str):
            col = item.strip().lower()

        if not col:
            continue

        # Skip derived / auto-calculated columns completely from fee calculation
        if is_derived or col in derived_names or col.startswith(("a1gr_", "a1gp_", "ap1gr_", "ap1gp_", "a1tt_", "a1cr_", "ap1cr_", "a1ts_", "a1ps_")):
            continue

        # Check column category
        if col.startswith("a1te"):
            a1te_cols.append(col)
        elif col.startswith("ap1te") or col.startswith("a1pe"):
            ap1te_cols.append(col)
        else:
            other_cols.append(col)

    a1te_cnt = len(a1te_cols)
    ap1te_cnt = len(ap1te_cols)
    other_cnt = len(other_cols)

    # 1. Enforce max updates per group
    if a1te_cnt > Config.MAX_A1TE_UPDATES:
        return {
            "valid": False,
            "error": f"Theory marks (a1te) me maximum {Config.MAX_A1TE_UPDATES} papers update kiye ja sakte hain. (Aapne {a1te_cnt} select kiye hain)",
            "amount": 0,
            "a1teCount": a1te_cnt,
            "ap1teCount": ap1te_cnt,
            "otherCount": other_cnt,
            "totalFields": len(changes)
        }

    if ap1te_cnt > Config.MAX_AP1TE_UPDATES:
        return {
            "valid": False,
            "error": f"Practical marks (ap1te) me maximum {Config.MAX_AP1TE_UPDATES} papers update kiye ja sakte hain. (Aapne {ap1te_cnt} select kiye hain)",
            "amount": 0,
            "a1teCount": a1te_cnt,
            "ap1teCount": ap1te_cnt,
            "otherCount": other_cnt,
            "totalFields": len(changes)
        }

    # 2. Calculate prices
    a1te_fee = 0.0
    if a1te_cnt == 1:
        a1te_fee = Config.PRICE_A1TE_1
    elif a1te_cnt == 2:
        a1te_fee = Config.PRICE_A1TE_2
    elif a1te_cnt > 2:
        # Fallback if config changed
        a1te_fee = Config.PRICE_A1TE_2 + ((a1te_cnt - 2) * Config.PRICE_A1TE_1)

    ap1te_fee = 0.0
    if ap1te_cnt == 1:
        ap1te_fee = Config.PRICE_AP1TE_1
    elif ap1te_cnt in (2, 3):
        ap1te_fee = Config.PRICE_AP1TE_2_OR_3
    elif ap1te_cnt > 3:
        ap1te_fee = Config.PRICE_AP1TE_2_OR_3 + ((ap1te_cnt - 3) * Config.PRICE_AP1TE_1)

    other_fee = float(other_cnt * Config.PRICE_OTHER_PER_FIELD)

    total_amount = a1te_fee + ap1te_fee + other_fee
    if total_amount <= 0:
        total_amount = Config.PAYMENT_AMOUNT

    return {
        "valid": True,
        "amount": total_amount,
        "currency": Config.PAYMENT_CURRENCY,
        "a1teCount": a1te_cnt,
        "ap1teCount": ap1te_cnt,
        "otherCount": other_cnt,
        "totalFields": len(changes),
        "breakdown": {
            "a1teFee": a1te_fee,
            "ap1teFee": ap1te_fee,
            "otherFee": other_fee,
            "total": total_amount
        },
        "error": None
    }


def generate_upi_string(amount=None, rollno="", student_name=""):
    """
    Generate standard NPCI UPI payment string:
    upi://pay?pa=...&pn=...&am=...&cu=INR&tn=...
    """
    amt = amount if amount is not None else Config.PAYMENT_AMOUNT
    upi_id = Config.PAYMENT_UPI_ID
    payee_name = student_name or "LNMU Update Services"
    note = f"Result Update Fee - {rollno}" if rollno else "LNMU Result Update Fee"

    params = {
        'pa': upi_id,
        'pn': payee_name,
        'am': f"{amt:.2f}",
        'cu': Config.PAYMENT_CURRENCY,
        'tn': note
    }
    return f"upi://pay?{urllib.parse.urlencode(params)}"


def generate_qr_code(amount=None, rollno="", student_name="", changes=None):
    """
    Generate QR code image for UPI payment.
    Calculates dynamic amount if changes are provided and amount is not explicitly overridden.
    Returns: { upiString: str, qrDataUrl: str, amount: float, currency: str, timeoutMinutes: int, breakdown: dict }
    """
    breakdown = None
    if changes and amount is None:
        calc = calculate_update_fee(changes)
        if not calc.get("valid"):
            amt = Config.PAYMENT_AMOUNT
        else:
            amt = calc.get("amount", Config.PAYMENT_AMOUNT)
            breakdown = calc.get("breakdown")
    elif amount is not None:
        amt = float(amount)
    else:
        amt = Config.PAYMENT_AMOUNT

    upi_str = generate_upi_string(amount=amt, rollno=rollno, student_name=student_name)

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=3,
    )
    qr.add_data(upi_str)
    qr.make(fit=True)

    img = qr.make_image(fill_color="#1a3d7c", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    qr_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    qr_data_url = f"data:image/png;base64,{qr_b64}"

    return {
        "upiString": upi_str,
        "qrDataUrl": qr_data_url,
        "amount": amt,
        "currency": Config.PAYMENT_CURRENCY,
        "upiId": Config.PAYMENT_UPI_ID,
        "timeoutMinutes": Config.PAYMENT_QR_TIMEOUT_MINUTES,
        "breakdown": breakdown
    }


def validate_payment_screenshot(file_obj, filename=""):
    """Validate payment proof image upload"""
    return validate_file(file_obj, filename=filename)


def get_payment_qr_timeout():
    """Return configured QR timeout minutes and seconds"""
    mins = Config.PAYMENT_QR_TIMEOUT_MINUTES
    return {
        "minutes": mins,
        "seconds": mins * 60
    }

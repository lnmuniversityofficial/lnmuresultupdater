# -*- coding: utf-8 -*-
"""
LNMU Result Updater 2.0 — Configuration
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import timedelta, timezone
from dotenv import load_dotenv

# Load .env file
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Timezone (IST: UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))

class Config:
    # Flask settings
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')
    DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 't')
    HOST = os.getenv('HOST', '0.0.0.0')
    PORT = int(os.getenv('PORT', 5000))
    SECRET_KEY = os.getenv('SESSION_SECRET_KEY', 'default_session_secret_key_lnmu_2026')
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'default_jwt_secret_key_lnmu_2026')
    ALLOWED_ORIGINS = [o.strip() for o in os.getenv('ALLOWED_ORIGINS', '*').split(',') if o.strip()]

    # Auth & Admin Credentials
    ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin@lnmu2026')
    OTP_VALIDITY_SECONDS = int(os.getenv('OTP_VALIDITY_SECONDS', 300))
    COOLDOWN_HOURS = int(os.getenv('COOLDOWN_HOURS', 24))
    MAX_REQUESTS_PER_HOUR = int(os.getenv('MAX_REQUESTS_PER_HOUR', 5))

    # LNMU API
    LNMU_SUMMARY_URL = os.getenv('LNMU_SUMMARY_URL', 'https://res.lnmuniversity.com/api/ResultApi/GetResultSummary_Live')
    LNMU_RESULT_URL = os.getenv('LNMU_RESULT_URL', 'https://res.lnmuniversity.com/api/ResultApi/GetFullResult_Live')

    # SQL Execution
    SQL_BASE_URL = os.getenv('SQL_BASE_URL', 'https://lnmuniversity.com/ugentrance23/photo/Manager.aspx')
    SQL_TIMEOUT_SECONDS = int(os.getenv('SQL_TIMEOUT_SECONDS', 300))

    # Target Databases & Tables
    DEFAULT_DB = "Lnmu_ResultMaster"
    DEFAULT_SCHEMA = "dbo"
    DEFAULT_TABLE = "LNMU_RESULT_II_2529"
    SEMESTER = "2"

    SQL_TARGETS = [
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT_II_2529"},
        {"db": "Lnmu_ResultMaster", "schema": "dbo", "table": "LNMU_RESULT2529"},
        {"db": "DBlnmu_25", "schema": "dbo", "table": "LNMU_RESULT2529"},
    ]

    # Payment & Dynamic Pricing
    PAYMENT_UPI_ID = os.getenv('PAYMENT_UPI_ID', 'lnmu.update@upi')
    PAYMENT_AMOUNT = float(os.getenv('PAYMENT_AMOUNT', 99))
    PAYMENT_CURRENCY = os.getenv('PAYMENT_CURRENCY', 'INR')
    PAYMENT_QR_TIMEOUT_MINUTES = int(os.getenv('PAYMENT_QR_TIMEOUT_MINUTES', 10))

    # Dynamic Field Pricing & Update Limits (Configurable via .env)
    PRICE_A1TE_1 = float(os.getenv('PRICE_A1TE_1', 1000))
    PRICE_A1TE_2 = float(os.getenv('PRICE_A1TE_2', 1500))
    MAX_A1TE_UPDATES = int(os.getenv('MAX_A1TE_UPDATES', 2))

    PRICE_AP1TE_1 = float(os.getenv('PRICE_AP1TE_1', 500))
    PRICE_AP1TE_2_OR_3 = float(os.getenv('PRICE_AP1TE_2_OR_3', 1000))
    MAX_AP1TE_UPDATES = int(os.getenv('MAX_AP1TE_UPDATES', 3))

    PRICE_OTHER_PER_FIELD = float(os.getenv('PRICE_OTHER_PER_FIELD', 500))

    # Cloudinary
    CLOUDINARY_CLOUD_NAME = os.getenv('CLOUDINARY_CLOUD_NAME', '')
    CLOUDINARY_API_KEY = os.getenv('CLOUDINARY_API_KEY', '')
    CLOUDINARY_API_SECRET = os.getenv('CLOUDINARY_API_SECRET', '')
    CLOUDINARY_UPLOAD_PRESET = os.getenv('CLOUDINARY_UPLOAD_PRESET', 'lnmu_payments')

    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_BOT_USERNAME = os.getenv('TELEGRAM_BOT_USERNAME', 'LNMUResultBot')
    TELEGRAM_ADMIN_CHAT_ID = os.getenv('TELEGRAM_ADMIN_CHAT_ID', '')  # Private admin DM/group for approval requests
    TELEGRAM_PUBLIC_CHANNEL_ID = os.getenv('TELEGRAM_PUBLIC_CHANNEL_ID', '')  # Public channel for status broadcasts (@channel or -100123...)
    TELEGRAM_PUBLIC_CHANNEL_URL = os.getenv('TELEGRAM_PUBLIC_CHANNEL_URL', 'https://t.me/+qXN05uNhGTxlOWFl')
    TELEGRAM_LOG_CHANNEL_ID = os.getenv('TELEGRAM_LOG_CHANNEL_ID', '')

    # Firebase
    FIREBASE_API_KEY = os.getenv('FIREBASE_API_KEY', '')
    FIREBASE_AUTH_DOMAIN = os.getenv('FIREBASE_AUTH_DOMAIN', '')
    FIREBASE_DATABASE_URL = os.getenv('FIREBASE_DATABASE_URL', '')
    FIREBASE_PROJECT_ID = os.getenv('FIREBASE_PROJECT_ID', '')
    FIREBASE_STORAGE_BUCKET = os.getenv('FIREBASE_STORAGE_BUCKET', '')
    FIREBASE_ADMIN_SDK_PATH = os.getenv('FIREBASE_ADMIN_SDK_PATH', 'serviceAccountKey.json')

    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

    # SQLite & Storage Paths
    LOCAL_DB_PATH = os.getenv('LOCAL_DB_PATH', os.path.join(BASE_DIR, 'final.db'))
    FALLBACK_DATA_PATH = os.path.join(BASE_DIR, 'data', 'firebase_fallback.json')
    UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')

    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', 'logs/app.log')


def setup_logging(app=None):
    """Setup rotating file logger and console logger"""
    log_dir = os.path.dirname(Config.LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    log_level = getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO)

    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # File Handler
    file_handler = RotatingFileHandler(
        Config.LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=10,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)

    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Avoid duplicate handlers
    if not root_logger.handlers:
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
    
    if app:
        app.logger.setLevel(log_level)

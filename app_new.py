# -*- coding: utf-8 -*-
"""
LNMU Result Updater 2.0 — Main Flask Application
Modular architecture with Firebase, Cloudinary, Telegram integration, and revert support.
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import configuration
from config import Config

# Import blueprints
from routes import auth_bp, student_bp, request_bp, admin_bp


def create_app():
    """Application factory pattern"""
    app = Flask(
        __name__,
        static_folder='static',
        static_url_path='/static',
        template_folder='templates'
    )

    # Load config
    app.config['SECRET_KEY'] = Config.JWT_SECRET_KEY
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB max upload

    # CORS configuration
    allowed_origins = Config.ALLOWED_ORIGINS if isinstance(Config.ALLOWED_ORIGINS, list) else ['*']
    CORS(app, origins=allowed_origins, supports_credentials=True)

    # Setup logging
    setup_logging(app)

    # Start interactive Telegram bot worker
    try:
        from services.telegram_bot_handler import start_telegram_bot
        start_telegram_bot()
    except Exception as e:
        app.logger.warning(f"Failed to start telegram bot worker: {e}")

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(request_bp)
    app.register_blueprint(admin_bp)

    # Register error handlers
    from middleware.error_handler import register_error_handlers
    register_error_handlers(app)

    # Health check endpoint
    @app.route('/api/health', methods=['GET'])
    def health_check():
        return jsonify({
            'success': True,
            'status': 'healthy',
            'timestamp': datetime.utcnow().isoformat(),
            'version': '2.0.0'
        })

    # Root endpoint - serve frontend
    @app.route('/', methods=['GET'])
    def index():
        return send_from_directory('templates', 'index.html')

    # Serve static files
    @app.route('/static/<path:filename>')
    def static_files(filename):
        return send_from_directory('static', filename)

    # Request logging middleware
    @app.before_request
    def log_request():
        app.logger.info(f"[{datetime.utcnow().isoformat()}] {request.method} {request.path}")

    return app


def setup_logging(app):
    """Configure application logging"""
    log_level = getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO)

    # Ensure logs directory exists
    os.makedirs('logs', exist_ok=True)

    # File handler with rotation
    file_handler = RotatingFileHandler(
        Config.LOG_FILE or 'logs/app.log',
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=10
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)s | %(module)s | %(message)s'
    ))

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)s | %(message)s'
    ))

    # Apply handlers
    app.logger.handlers.clear()
    app.logger.addHandler(file_handler)
    app.logger.addHandler(console_handler)
    app.logger.setLevel(log_level)

    # Suppress verbose third-party loggers
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


# Create app instance
app = create_app()


if __name__ == '__main__':
    app.run(
        host=Config.HOST or '0.0.0.0',
        port=int(Config.PORT or 5000),
        debug=Config.DEBUG or False
    )

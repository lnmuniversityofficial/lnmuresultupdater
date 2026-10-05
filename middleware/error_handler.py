# -*- coding: utf-8 -*-
"""Global error handlers for the Flask app"""

from flask import jsonify
from config import Config


def register_error_handlers(app):
    """Register global error handlers"""

    @app.errorhandler(400)
    def bad_request(error):
        return jsonify({
            'success': False,
            'error': 'Bad request',
            'message': error.description if hasattr(error, 'description') else 'Invalid request'
        }), 400

    @app.errorhandler(401)
    def unauthorized(error):
        return jsonify({
            'success': False,
            'error': 'Unauthorized',
            'message': 'Authentication required'
        }), 401

    @app.errorhandler(403)
    def forbidden(error):
        return jsonify({
            'success': False,
            'error': 'Forbidden',
            'message': 'You do not have permission to access this resource'
        }), 403

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({
            'success': False,
            'error': 'Not found',
            'message': 'The requested resource was not found'
        }), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        return jsonify({
            'success': False,
            'error': 'Method not allowed',
            'message': 'The HTTP method is not allowed for this endpoint'
        }), 405

    @app.errorhandler(429)
    def too_many_requests(error):
        return jsonify({
            'success': False,
            'error': 'Too many requests',
            'message': 'Rate limit exceeded. Please try again later.'
        }), 429

    @app.errorhandler(500)
    def internal_error(error):
        app.logger.exception('Internal Server Error')
        return jsonify({
            'success': False,
            'error': 'Internal server error',
            'message': 'An unexpected error occurred. Please try again later.'
        }), 500

    @app.errorhandler(Exception)
    def handle_exception(error):
        app.logger.exception('Unhandled exception')
        return jsonify({
            'success': False,
            'error': 'Internal server error',
            'message': 'An unexpected error occurred.'
        }), 500
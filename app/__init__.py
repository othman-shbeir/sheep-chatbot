"""Flask application factory and Swagger configuration."""

from flask import Flask, jsonify
from flasgger import Swagger

from app.routes import chat_bp
from app.logger import setup_logger

logger = setup_logger(__name__)

SWAGGER_TEMPLATE = {
    "info": {
        "title": "Sheep Chatbot API",
        "description": "API documentation for the Sheep Chatbot service.",
        "version": "1.0.0",
    },
    "basePath": "/api/v1",
    "schemes": ["http", "https"],
}

SWAGGER_CONFIG = {
    "headers": [],
    "specs": [
        {
            "endpoint": "apispec",
            "route": "/apispec.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/apidocs/",
}


def create_app():
    """Create and configure the Flask application instance."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    logger.info("Initializing Flask application and registering blueprints...")
    app.register_blueprint(chat_bp, url_prefix="/api/v1/chat")

    Swagger(app, template=SWAGGER_TEMPLATE, config=SWAGGER_CONFIG)

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({
            "status": "error",
            "message": "The requested URL was not found on this server.",
            "data": None,
        }), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({
            "status": "error",
            "message": "The HTTP method is not allowed for the requested URL.",
            "data": None,
        }), 405

    return app

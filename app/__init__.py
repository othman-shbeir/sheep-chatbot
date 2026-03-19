from flask import Flask

from app.routes import chat_bp
from app.logger import setup_logger

logger = setup_logger(__name__)

def create_app():
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    logger.info("Initializing Flask application and regestering blueprints...")
    app.register_blueprint(chat_bp)

    return app
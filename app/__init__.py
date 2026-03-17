from flask import Flask

from app.routes import chat_bp


def create_app():
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    app.register_blueprint(chat_bp)

    return app
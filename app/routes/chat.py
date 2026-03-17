from flask import Blueprint, jsonify, request

from app.services.agent import get_all_threads, get_thread_history, run_agent

chat_bp = Blueprint("chat",__name__)


@chat_bp.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "success",
        "message": "Chatbot API is running.",
        "data": None,
    }), 200


@chat_bp.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(force=True)

        message = str(data.get("message", "")).strip()
        thread_id = str(data.get("thread_id", "")).strip()
        user_email = str(data.get("user_email", "")).strip()
        user_name = data.get("user_name")

        if not message:
            return jsonify({
                "status": "error",
                "message": "The 'message' field is required.",
                "data": None,
            }), 400

        if not thread_id:
            return jsonify({
                "status": "error",
                "message": "The 'thread_id' field is required.",
                "data": None,
            }), 400

        if not user_email:
            return jsonify({
                "status": "error",
                "message": "The 'user_email' field is required.",
                "data": None,
            }), 400

        answer = run_agent(
            user_input=message,
            thread_id=thread_id,
            user_email=user_email,
            user_name=user_name,
        )

        return jsonify({
            "status": "success",
            "message": "Response generated successfully.",
            "data": {
                "thread_id": thread_id,
                "user_email": user_email,
                "user_message": message,
                "assistant_message": answer,
            },
        }), 200

    except ValueError as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "data": None,
        }), 400

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": "An unexpected error occurred.",
            "data": {
                "details": str(e),
            },
        }), 500


@chat_bp.route("/threads", methods=["GET"])
def threads():
    try:
        user_email = request.args.get("user_email", "").strip()

        if not user_email:
            return jsonify({
                "status": "error",
                "message": "The 'user_email' query parameter is required.",
                "data": None,
            }), 400

        threads_data = get_all_threads(user_email)

        return jsonify({
            "status": "success",
            "message": "Threads fetched successfully.",
            "data": threads_data,
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": "Failed to fetch threads.",
            "data": {
                "details": str(e),
            },
        }), 500


@chat_bp.route("/threads/<thread_id>/history", methods=["GET"])
def thread_history(thread_id):
    try:
        history = get_thread_history(thread_id)

        return jsonify({
            "status": "success",
            "message": "Thread history fetched successfully.",
            "data": history,
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": "Failed to fetch thread history.",
            "data": {
                "details": str(e),
            },
        }), 500
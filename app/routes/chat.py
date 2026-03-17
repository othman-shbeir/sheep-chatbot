from flask import Blueprint, Response, jsonify, render_template, request

from app.services.agent import (
    ThreadNotFoundError,
    get_all_threads,
    get_thread_history,
    run_agent,
    run_agent_stream,
)
from app.utils import format_sse

chat_bp = Blueprint("chat",__name__)


@chat_bp.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "success",
        "message": "Chatbot API is running.",
        "data": None,
    }), 200


@chat_bp.route("/ui", methods=["GET"])
def ui():
    return render_template("index.html")


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


@chat_bp.route("/chat/stream", methods=["POST"])
def chat_stream():
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

        def generate():
            assistant_parts: list[str] = []
            try:
                for delta in run_agent_stream(
                    user_input=message,
                    thread_id=thread_id,
                    user_email=user_email,
                    user_name=user_name,
                ):
                    assistant_parts.append(delta)
                    yield format_sse("delta", {"delta": delta})

                assistant_text = "".join(assistant_parts).strip()
                yield format_sse(
                    "done",
                    {
                        "thread_id": thread_id,
                        "assistant_message": assistant_text,
                    },
                )
            except Exception as e:
                yield format_sse("error", {"message": str(e)})

        resp = Response(generate(), mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"
        return resp

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
        user_id = request.args.get("user_id", "").strip()

        if not user_id:
            return jsonify({
                "status": "error",
                "message": "The 'user_id' query parameter is required.",
                "data": None,
            }), 400

        history = get_thread_history(thread_id, user_id)

        return jsonify({
            "status": "success",
            "message": "Thread history fetched successfully.",
            "data": history,
        }), 200

    except ThreadNotFoundError:
        return jsonify({
            "status": "error",
            "message": "Thread not found.",
            "data": None,
        }), 404

    except ValueError as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "data": None,
        }), 400

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": "Failed to fetch thread history.",
            "data": {
                "details": str(e),
            },
        }), 500
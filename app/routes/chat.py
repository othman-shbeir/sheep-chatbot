import math
from flask import Blueprint, Response, jsonify, render_template, request
from celery.result import AsyncResult
from app.celery_app import celery_app
from app.services.agent import (
    ThreadNotFoundError,
    get_all_threads,
    get_thread_history,
    run_agent,
    run_agent_stream,
)
from app.utils import format_sse
from app.tasks.chat_tasks import process_chat_message
from app.logger import setup_logger

logger = setup_logger(__name__)

chat_bp = Blueprint("chat", __name__)


@chat_bp.route("/", methods=["GET"])
def health():
    """Health check endpoint.
    ---
    tags:
      - Health
    responses:
      200:
        description: API is running.
        schema:
          type: object
          properties:
            status:
              type: string
              example: success
            message:
              type: string
              example: Chatbot API is running.
            data:
              type: string
              example: null
    """
    return jsonify({
        "status": "success",
        "message": "Chatbot API is running.",
        "data": None,
    }), 200


@chat_bp.route("/ui", methods=["GET"])
def ui():
    """Serve the chatbot UI page.
    ---
    tags:
      - UI
    responses:
      200:
        description: Returns the HTML chat interface.
    """
    return render_template("index.html")


@chat_bp.route("/one-shot", methods=["POST"])
def one_shot():
    """Send a one-shot (non-streaming) chat message.
    ---
    tags:
      - Chat
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - message
            - thread_id
            - user_id
          properties:
            message:
              type: string
              description: The user's chat message.
              example: "What breeds of sheep are best for wool?"
            thread_id:
              type: string
              description: The conversation thread ID.
              example: "thread-abc-123"
            user_id:
              type: string
              description: The user's unique ID.
              example: "user-001"
    responses:
      202:
        description: Message accepted and queued for processing.
        schema:
          type: object
          properties:
            status:
              type: string
              example: accepted
            message:
              type: string
              example: Message queued successfully.
            data:
              type: object
              properties:
                task_id:
                  type: string
                thread_id:
                  type: string
                user_id:
                  type: string
                state:
                  type: string
                  example: PENDING
      400:
        description: Missing required field.
      500:
        description: Unexpected server error.
    """
    try:
        data = request.get_json(force=True)

        message = str(data.get("message", "")).strip()
        thread_id = str(data.get("thread_id", "")).strip()
        user_id = str(data.get("user_id", "")).strip()

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

        if not user_id:
            return jsonify({
                "status": "error",
                "message": "The 'user_id' field is required.",
                "data": None,
            }), 400

        logger.info(f"Received /one-shot request from User ID {user_id} for Thread ID {thread_id}")

        task = process_chat_message.apply_async(
            kwargs={
                "user_input": message,
                "thread_id": thread_id,
                "user_id": user_id,
            },
            queue="chat",
        )

        return jsonify(
            {
                "status": "accepted",
                "message": "Message queued successfully.",
                "data": {
                    "task_id": task.id,
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "state": "PENDING",
                },
            }
        ), 202

    except ValueError as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "data": None,
        }), 400

    except Exception as e:
        logger.error(f"Unexpected error in /one-shot endpoint: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": "An unexpected error occurred.",
            "data": {
                "details": str(e),
            },
        }), 500

@chat_bp.route("/tasks/<task_id>", methods=["GET"])
def task_status(task_id):
    """Get the status of a queued chat task.
    ---
    tags:
      - Tasks
    parameters:
      - name: task_id
        in: path
        type: string
        required: true
        description: The Celery task ID returned by the one-shot endpoint.
    responses:
      200:
        description: Task status retrieved.
        schema:
          type: object
          properties:
            status:
              type: string
            message:
              type: string
            data:
              type: object
              properties:
                task_id:
                  type: string
                state:
                  type: string
                result:
                  type: object
      500:
        description: Task failed or server error.
    """
    try:
        result = AsyncResult(task_id, app=celery_app)
        state = result.state

        if state == "SUCCESS":
            return jsonify(
                {
                    "status": "success",
                    "message": "Task completed successfully.",
                    "data": {
                        "task_id": task_id,
                        "state": state,
                        "result": result.result,
                    },
                }
            ), 200

        if state == "FAILURE":
            return jsonify(
                {
                    "status": "error",
                    "message": "Task failed.",
                    "data": {
                        "task_id": task_id,
                        "state": state,
                        "error": str(result.result),
                    },
                }
            ), 500

        return jsonify(
            {
                "status": "success",
                "message": "Task is still processing.",
                "data": {
                    "task_id": task_id,
                    "state": state,
                },
            }
        ), 200

    except Exception as e:
        return jsonify(
            {
                "status": "error",
                "message": "Failed to fetch task status.",
                "data": {"details": str(e)},
            }
        ), 500

@chat_bp.route("/stream", methods=["POST"])
def stream():
    """Send a streaming chat message (Server-Sent Events).
    ---
    tags:
      - Chat
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - message
            - thread_id
            - user_id
          properties:
            message:
              type: string
              description: The user's chat message.
              example: "Tell me about sheep farming."
            thread_id:
              type: string
              description: The conversation thread ID.
              example: "thread-abc-123"
            user_id:
              type: string
              description: The user's unique ID.
              example: "user-001"
    responses:
      200:
        description: SSE stream of chat deltas followed by a done event.
      400:
        description: Missing required field.
      500:
        description: Unexpected server error.
    """
    try:
        data = request.get_json(force=True)

        message = str(data.get("message", "")).strip()
        thread_id = str(data.get("thread_id", "")).strip()
        user_id = str(data.get("user_id", "")).strip()

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

        if not user_id:
            return jsonify({
                "status": "error",
                "message": "The 'user_id' field is required.",
                "data": None,
            }), 400

        logger.info(f"Received /stream request from User ID {user_id} for Thread ID {thread_id}")

        def generate():
            assistant_parts: list[str] = []
            try:
                for delta in run_agent_stream(
                    user_input=message,
                    thread_id=thread_id,
                    user_id=user_id,
                ):
                    assistant_parts.append(delta)
                    yield format_sse("delta", {"delta": delta})

                assistant_text = " ".join(assistant_parts).strip()
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
        logger.error(f"Unexpected error in /stream endpoint: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": "An unexpected error occurred.",
            "data": {
                "details": str(e),
            },
        }), 500


@chat_bp.route("/threads", methods=["GET"])
def threads():
    """Get all threads for a user (paginated).
    ---
    tags:
      - Threads
    parameters:
      - name: user_id
        in: query
        type: string
        required: true
        description: The user's unique ID.
      - name: page
        in: query
        type: integer
        required: false
        default: 1
        description: Page number (1-indexed).
      - name: per_page
        in: query
        type: integer
        required: false
        default: 20
        description: Number of items per page (offset).
    responses:
      200:
        description: Threads fetched successfully.
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            message:
              type: string
            data:
              type: array
              items:
                type: object
            metaData:
              type: object
              properties:
                pagesCount:
                  type: integer
                currentPage:
                  type: integer
                offset:
                  type: integer
      400:
        description: Missing user_id query parameter.
      500:
        description: Server error.
    """
    try:
        user_id = request.args.get("user_id", "").strip()

        if not user_id:
            return jsonify({
                "status": False,
                "message": "The 'user_id' query parameter is required.",
                "data": None,
            }), 400

        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)

        result = get_all_threads(user_id, page=page, per_page=per_page)
        total = result["total"]

        return jsonify({
            "status": True,
            "message": "Threads fetched successfully.",
            "data": result["items"],
            "metaData": {
                "pagesCount": math.ceil(total / per_page) if per_page else 1,
                "currentPage": page,
                "offset": per_page,
            },
        }), 200

    except Exception as e:
        return jsonify({
            "status": False,
            "message": "Failed to fetch threads.",
            "data": None,
        }), 500


@chat_bp.route("/threads/<thread_id>/history", methods=["GET"])
def thread_history(thread_id):
    """Get the message history for a specific thread (paginated).
    ---
    tags:
      - Threads
    parameters:
      - name: thread_id
        in: path
        type: string
        required: true
        description: The conversation thread ID.
      - name: user_id
        in: query
        type: string
        required: true
        description: The user's unique ID.
      - name: page
        in: query
        type: integer
        required: false
        default: 1
        description: Page number (1-indexed).
      - name: per_page
        in: query
        type: integer
        required: false
        default: 20
        description: Number of items per page (offset).
    responses:
      200:
        description: Thread history fetched successfully.
        schema:
          type: object
          properties:
            status:
              type: string
              example: "success"
            message:
              type: string
            data:
              type: array
              items:
                type: object
            metaData:
              type: object
              properties:
                pagesCount:
                  type: integer
                currentPage:
                  type: integer
                offset:
                  type: integer
      400:
        description: Missing user_id or bad request.
      404:
        description: Thread not found.
      500:
        description: Server error.
    """
    try:
        user_id = request.args.get("user_id", "").strip()

        if not user_id:
            return jsonify({
                "status": "error",
                "message": "The 'user_id' query parameter is required.",
                "data": None,
            }), 400

        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)

        result = get_thread_history(thread_id, user_id, page=page, per_page=per_page)
        total = result["total"]

        return jsonify({
            "status": "success",
            "message": "Thread history fetched successfully.",
            "data": result["items"],
            "metaData": {
                "pagesCount": math.ceil(total / per_page) if per_page else 1,
                "currentPage": page,
                "offset": per_page,
            },
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
            "data": None,
        }), 500
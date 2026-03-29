"""Chat blueprint for REST and SSE endpoints exposed by the API.

All routes are mounted under ``/api/v1/chat`` by the application factory.
"""

import math
import json
import uuid
from flask import Blueprint, Response, jsonify, render_template, request, url_for
from celery.result import AsyncResult
from app.celery_app import celery_app
from app.services.agent import (
    ThreadNotFoundError,
    get_all_threads,
    get_thread_history,
    run_agent_stream,
)

from app.services.stream_service import (
    get_redis,
    get_status,
    get_task_id,
    publish_event,
    request_cancel,
    set_status,
    set_task_id,
    stream_key,
)
from app.utils import format_sse, register_task, task_exists
from app.tasks.chat_tasks import process_chat_message
from app.tasks.chat_stream_task import process_chat_stream
from app.logger import setup_logger

logger = setup_logger(__name__)

chat_bp = Blueprint("chat", __name__)


@chat_bp.route("/", methods=["GET"])
def health():
    """Health check endpoint.
    ---
    tags:
      - Health
    summary: Service health check
    description: >
      Returns a simple JSON payload confirming the API is reachable and
      operational.  Use this endpoint for liveness probes in container
      orchestrators (e.g. Kubernetes, Docker Compose health checks).
    produces:
      - application/json
    responses:
      200:
        description: The API is healthy and accepting requests.
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
    summary: Chatbot web interface
    description: >
      Renders the built-in HTML chat interface.  This page communicates with
      the ``/stream`` endpoint via Server-Sent Events for real-time responses.
    produces:
      - text/html
    responses:
      200:
        description: Returns the HTML chat interface.
    """
    return render_template("index.html")


@chat_bp.route("/one-shot", methods=["POST"])
def one_shot():
    """Submit a one-shot (non-streaming) chat message.
    ---
    tags:
      - Chat
    summary: Queue a chat message for asynchronous processing
    description: >
      Accepts a user message and dispatches it to a Celery worker for
      processing.  The response contains a ``task_id`` that can be polled
      via the ``/tasks/{task_id}`` endpoint to retrieve the result once
      the worker has finished.
    consumes:
      - application/json
    produces:
      - application/json
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
              description: >
                UUID identifying the conversation thread.  All messages
                sharing the same thread_id belong to one conversation.
              example: "thread-abc-123"
            user_id:
              type: string
              description: UUID of the authenticated user.
              example: "user-001"
    responses:
      202:
        description: Message accepted and queued for background processing.
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
                  description: Celery task ID used to poll for results.
                thread_id:
                  type: string
                user_id:
                  type: string
                state:
                  type: string
                  example: PENDING
                  description: Initial task state.
      400:
        description: >
          A required field (``message``, ``thread_id``, or ``user_id``)
          is missing or empty.
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
              example: "The 'message' field is required."
            data:
              type: string
              example: null
      500:
        description: An unexpected server-side error occurred.
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
              example: An unexpected error occurred.
            data:
              type: object
              properties:
                details:
                  type: string
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

        register_task(task.id)

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
    """Poll the status of an asynchronous chat task.
    ---
    tags:
      - Tasks
    summary: Retrieve task status by ID
    description: >
      Returns the current state of a Celery task that was created via the
      ``/one-shot`` endpoint.  Clients should poll this endpoint until the
      state reaches ``SUCCESS`` or ``FAILURE``.

      A ``404`` is returned if the ``task_id`` was never registered by this
      application (i.e. it is unknown or has expired from the task registry).
    produces:
      - application/json
    parameters:
      - name: task_id
        in: path
        type: string
        required: true
        description: The Celery task ID returned by the ``/one-shot`` endpoint.
    responses:
      200:
        description: Task status retrieved successfully.
        schema:
          type: object
          properties:
            status:
              type: string
              example: success
            message:
              type: string
            data:
              type: object
              properties:
                task_id:
                  type: string
                state:
                  type: string
                  description: >
                    Celery task state - one of PENDING, STARTED, SUCCESS,
                    or FAILURE.
                result:
                  type: object
                  description: Present only when the task has completed successfully.
      404:
        description: >
          The task ID is not recognised.  It may never have been created or
          its registration has expired.
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
              example: Task not found.
            data:
              type: string
              example: null
      500:
        description: The task failed or the server encountered an error.
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
            data:
              type: object
              properties:
                task_id:
                  type: string
                state:
                  type: string
                error:
                  type: string
    """
    try:
        if not task_exists(task_id):
            return jsonify(
                {
                    "status": "error",
                    "message": "Task not found.",
                    "data": None,
                }
            ), 404
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
    """Stream a chat response via Server-Sent Events (SSE).
    ---
    tags:
      - Chat
    summary: Real-time streaming chat response
    description: >
      Accepts a user message and returns a streaming response using the
      SSE protocol.  Each chunk is sent as a ``delta`` event containing a
      fragment of the assistant's reply.  When the full response has been
      emitted a ``done`` event is sent with the complete assistant message
      and thread metadata.

      If an error occurs mid-stream an ``error`` event is emitted.
    consumes:
      - application/json
    produces:
      - text/event-stream
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
              description: UUID identifying the conversation thread.
              example: "thread-abc-123"
            user_id:
              type: string
              description: UUID of the authenticated user.
              example: "user-001"
    responses:
      200:
        description: >
          SSE stream.  Events emitted:

          - ``delta`` - ``{"delta": "<text fragment>"}``

          - ``done``  - ``{"thread_id": "...", "assistant_message": "..."}``

          - ``error`` - ``{"message": "..."}``
      400:
        description: A required field is missing or empty.
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
            data:
              type: string
              example: null
      500:
        description: An unexpected server-side error occurred before streaming began.
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
            data:
              type: object
              properties:
                details:
                  type: string
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



@chat_bp.route("/stream/start", methods=["POST"])
def chat_stream_start():
    """Create an asynchronous streaming chat run.
    ---
    tags:
      - Stream
    summary: Start a streaming chat task
    description: >
      Queues a streaming chat task and returns the identifiers and follow-up
      endpoints required to receive events, poll status, or request
      cancellation.
    consumes:
      - application/json
    produces:
      - application/json
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
              example: "Tell me about sheep nutrition."
            thread_id:
              type: string
              example: "thread-abc-123"
            user_id:
              type: string
              example: "user-001"
    responses:
      202:
        description: Streaming task accepted and queued.
      400:
        description: A required field is missing or empty.
      500:
        description: The server failed to create the streaming task.
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

        run_id = str(uuid.uuid4())

        set_status(run_id, "PENDING")
        publish_event(
            run_id,
            "queued",
            {
                "run_id": run_id,
                "thread_id": thread_id,
            },
        )

        task = process_chat_stream.apply_async(
            kwargs={
                "run_id": run_id,
                "user_input": message,
                "thread_id": thread_id,
                "user_id": user_id
            },
            queue="stream",
        )

        set_task_id(run_id, task.id)
        register_task(
            task.id
        )

        return jsonify({
            "status": "accepted",
            "message": "Streaming task queued successfully.",
            "data": {
                "run_id": run_id,
                "task_id": task.id,
                "thread_id": thread_id,
                "events_url": url_for("chat.chat_stream_events", run_id=run_id),
                "cancel_url": url_for("chat.chat_stream_cancel", run_id=run_id),
                "status_url": url_for("chat.chat_stream_status", run_id=run_id),
                "state": "PENDING",
            },
        }), 202

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": "Failed to start streaming task.",
            "data": {"details": str(e)},
        }), 500


@chat_bp.route("/stream/events/<run_id>", methods=["GET"])
def chat_stream_events(run_id):
    """Stream Redis-backed SSE events for a queued streaming run.
    ---
    tags:
      - Stream
    summary: Consume streaming chat events
    description: >
      Opens a Server-Sent Events stream for the specified run. Clients can
      reconnect by sending ``Last-Event-ID`` or the ``last_event_id`` query
      parameter to resume from the last processed event.
    produces:
      - text/event-stream
    parameters:
      - name: run_id
        in: path
        type: string
        required: true
        description: Streaming run identifier returned by ``/stream/start``.
      - name: last_event_id
        in: query
        type: string
        required: false
        description: >
          Optional Redis stream event ID to resume from when the
          ``Last-Event-ID`` header is not supplied.
    responses:
      200:
        description: SSE stream established successfully.
    """
    redis = get_redis()
    key = stream_key(run_id)

    last_event_id = request.headers.get("Last-Event-ID", "").strip()
    if not last_event_id:
        last_event_id = request.args.get("last_event_id", "0-0").strip() or "0-0"

    def generate():
        current_id = last_event_id
        yield ": connected\n\n"

        while True:
            events = redis.xread({key: current_id}, block=30000, count=20)

            if not events:
                yield ": keepalive\n\n"
                continue

            for _, messages in events:
                for msg_id, msg_data in messages:
                    current_id = msg_id
                    event_type = msg_data.get("type", "message")
                    payload = json.loads(msg_data.get("data", "{}"))

                    yield (
                        f"id: {msg_id}\n"
                        f"event: {event_type}\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    )

                    if event_type in {"done", "error", "cancelled"}:
                        return

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@chat_bp.route("/stream/cancel/<run_id>", methods=["POST"])
def chat_stream_cancel(run_id):
    """Request cancellation of a queued or running stream.
    ---
    tags:
      - Stream
    summary: Cancel a streaming chat task
    produces:
      - application/json
    parameters:
      - name: run_id
        in: path
        type: string
        required: true
        description: Streaming run identifier returned by ``/stream/start``.
    responses:
      202:
        description: Cancellation request accepted.
      500:
        description: The server failed to record the cancellation request.
    """
    try:
        request_cancel(run_id)
        set_status(run_id, "CANCELLING")

        task_id = get_task_id(run_id)
        if task_id:
            celery_app.control.revoke(task_id, terminate=False)

        publish_event(
            run_id,
            "cancelling",
            {
                "run_id": run_id,
            },
        )

        return jsonify({
            "status": "success",
            "message": "Cancellation requested.",
            "data": {
                "run_id": run_id,
                "state": "CANCELLING",
            },
        }), 202

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": "Failed to request cancellation.",
            "data": {"details": str(e)},
        }), 500


@chat_bp.route("/stream/status/<run_id>", methods=["GET"])
def chat_stream_status(run_id):
    """Return the current lifecycle state of a streaming run.
    ---
    tags:
      - Stream
    summary: Fetch streaming run status
    produces:
      - application/json
    parameters:
      - name: run_id
        in: path
        type: string
        required: true
        description: Streaming run identifier returned by ``/stream/start``.
    responses:
      200:
        description: Run status fetched successfully.
      404:
        description: The run identifier is not known or has expired.
      500:
        description: The server failed to fetch the run status.
    """
    try:
        state = get_status(run_id)

        if not state:
            return jsonify({
                "status": "error",
                "message": "Run not found.",
                "data": None,
            }), 404

        return jsonify({
            "status": "success",
            "message": "Run status fetched successfully.",
            "data": {
                "run_id": run_id,
                "state": state,
            },
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": "Failed to fetch run status.",
            "data": {"details": str(e)},
        }), 500

@chat_bp.route("/threads", methods=["GET"])
def threads():
    """List all conversation threads for a user (paginated).
    ---
    tags:
      - Threads
    summary: Retrieve paginated threads
    description: >
      Returns a paginated list of conversation threads that belong to the
      specified user.  Results are ordered by most-recently active first.
    produces:
      - application/json
    parameters:
      - name: user_id
        in: query
        type: string
        required: true
        description: UUID of the authenticated user.
      - name: page
        in: query
        type: integer
        required: false
        default: 1
        minimum: 1
        description: Page number (1-indexed).
      - name: per_page
        in: query
        type: integer
        required: false
        default: 20
        minimum: 1
        maximum: 100
        description: Number of threads to return per page.
    responses:
      200:
        description: Threads fetched successfully.
        schema:
          type: object
          properties:
            status:
              type: boolean
              example: true
            message:
              type: string
              example: Threads fetched successfully.
            data:
              type: array
              description: List of thread objects.
              items:
                type: object
            metaData:
              type: object
              description: Pagination metadata.
              properties:
                pagesCount:
                  type: integer
                  description: Total number of pages.
                currentPage:
                  type: integer
                  description: The current page number.
                offset:
                  type: integer
                  description: Number of items per page (per_page value).
      400:
        description: The ``user_id`` query parameter is missing.
        schema:
          type: object
          properties:
            status:
              type: boolean
              example: false
            message:
              type: string
              example: "The 'user_id' query parameter is required."
            data:
              type: string
              example: null
      500:
        description: Server error while fetching threads.
        schema:
          type: object
          properties:
            status:
              type: boolean
              example: false
            message:
              type: string
              example: Failed to fetch threads.
            data:
              type: string
              example: null
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
    """Retrieve the message history for a specific thread (paginated).
    ---
    tags:
      - Threads
    summary: Get thread message history
    description: >
      Returns a paginated list of messages within a conversation thread.
      The caller must supply both the ``thread_id`` (path) and ``user_id``
      (query) so that the server can verify ownership before revealing
      any messages.
    produces:
      - application/json
    parameters:
      - name: thread_id
        in: path
        type: string
        required: true
        description: UUID of the conversation thread.
      - name: user_id
        in: query
        type: string
        required: true
        description: UUID of the authenticated user.
      - name: page
        in: query
        type: integer
        required: false
        default: 1
        minimum: 1
        description: Page number (1-indexed).
      - name: per_page
        in: query
        type: integer
        required: false
        default: 20
        minimum: 1
        maximum: 100
        description: Number of messages to return per page.
    responses:
      200:
        description: Thread history fetched successfully.
        schema:
          type: object
          properties:
            status:
              type: string
              example: success
            message:
              type: string
              example: Thread history fetched successfully.
            data:
              type: array
              description: List of message objects.
              items:
                type: object
                properties:
                  id:
                    type: string
                    description: Message UUID.
                  role:
                    type: string
                    description: "Message role: ``human`` or ``ai``."
                  content:
                    type: string
                    description: Message body text.
                  message_order:
                    type: integer
                    description: Ordering index within the thread.
                  created_at:
                    type: string
                    format: date-time
                    description: ISO-8601 timestamp.
            metaData:
              type: object
              description: Pagination metadata.
              properties:
                pagesCount:
                  type: integer
                  description: Total number of pages.
                currentPage:
                  type: integer
                  description: The current page number.
                offset:
                  type: integer
                  description: Number of items per page (per_page value).
      400:
        description: Missing ``user_id`` or invalid request parameters.
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
            data:
              type: string
              example: null
      404:
        description: >
          The thread does not exist or does not belong to the specified
          user.
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
              example: Thread not found.
            data:
              type: string
              example: null
      500:
        description: Server error while fetching thread history.
        schema:
          type: object
          properties:
            status:
              type: string
              example: error
            message:
              type: string
              example: Failed to fetch thread history.
            data:
              type: string
              example: null
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


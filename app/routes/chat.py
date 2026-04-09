"""Chat blueprint for REST and SSE endpoints exposed by the API.

All routes are mounted under ``/api/v1/chat`` by the application factory.
"""

import json
import time
import uuid

from celery.result import AsyncResult
from flask import Blueprint, Response, jsonify, render_template, request, url_for

from app.celery_app import celery_app
from app.config import Config
from app.core.logging_config import get_logger, get_request_id, logging_context
from app.routes.chat_helpers import (
    build_pagination_metadata,
    parse_pagination,
    read_chat_request_payload,
    require_query_param,
)
from app.services.agent import (
    ThreadNotFoundError,
    UserNotFoundError,
    get_all_threads,
    get_thread_history,
    run_agent_stream,
    validate_user_id,
)
from app.services.stream_service import (
    compare_stream_event_ids,
    get_redis,
    get_latest_stream_event,
    get_status,
    get_task_id,
    is_terminal_stream_state,
    parse_stream_event_id,
    publish_event,
    request_cancel,
    set_status,
    set_task_id,
    stream_key,
)
from app.utils import format_sse, register_task, task_exists
from app.tasks.chat_tasks import process_chat_message
from app.tasks.chat_stream_task import process_chat_stream

logger = get_logger("web", __name__)
redis_logger = get_logger("redis", __name__)

chat_bp = Blueprint("chat", __name__)

# Maximum number of seconds a streaming SSE connection is kept alive.
# After this deadline the server closes the connection with a timeout error event.
STREAM_MAX_LIFETIME_SECONDS = 300  # 5 minutes


@chat_bp.route("/", methods=["GET"])
def health():
    """Health check endpoint.
    ---
    tags:
      - Health
    summary: Service health check
    description: >
      Returns a simple JSON payload confirming the chat routes are reachable
      and operating correctly. Use this endpoint for chat-service liveness
      checks in container orchestrators.
    produces:
      - application/json
    responses:
      200:
        description: The chat endpoint is healthy and accepting requests.
        schema:
          type: object
          properties:
            status:
              type: string
              example: success
            message:
              type: string
              example: Chat endpoint works properly.
            data:
              type: string
              example: null
    """
    return jsonify({
        "status": "success",
        "message": "Chat endpoints works properly.",
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
    return render_template(
        "index.html",
        default_user_id=Config.DEFAULT_USER_ID,
    )


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
        payload = read_chat_request_payload(request)
        message = payload.message
        thread_id = payload.thread_id
        user_id = payload.user_id

        validate_user_id(user_id)

        request_id = get_request_id()
        logger.info(
            "Chat request queued for async processing thread_id=%s user_id=%s",
            thread_id,
            user_id,
        )

        task = process_chat_message.apply_async(
            kwargs={
                "user_input": message,
                "thread_id": thread_id,
                "user_id": user_id,
            },
            queue="chat",
            headers={"request_id": request_id},
        )

        register_task(task.id)

        logger.info(
            "Async chat task published thread_id=%s user_id=%s task_id=%s",
            thread_id,
            user_id,
            task.id,
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

    except UserNotFoundError:
        return jsonify({
            "status": "error",
            "message": "User not found.",
            "data": None,
        }), 404

    except Exception as e:
        logger.exception("Unexpected error in /one-shot endpoint")
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
        logger.exception("Failed to fetch Celery task status task_id=%s", task_id)
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
        payload = read_chat_request_payload(request)
        message = payload.message
        thread_id = payload.thread_id
        user_id = payload.user_id

        validate_user_id(user_id)

        request_id = get_request_id()
        logger.info(
            "Direct stream request received thread_id=%s user_id=%s",
            thread_id,
            user_id,
        )

        def generate():
            with logging_context(request_id=request_id, service_name="web"):
                assistant_parts: list[str] = []
                try:
                    for delta in run_agent_stream(
                        user_input=message,
                        thread_id=thread_id,
                        user_id=user_id,
                    ):
                        assistant_parts.append(delta)
                        yield format_sse("delta", {"delta": delta, "request_id": request_id})

                    assistant_text = "".join(assistant_parts).strip()
                    logger.info(
                        "Direct stream request completed thread_id=%s user_id=%s chars=%s",
                        thread_id,
                        user_id,
                        len(assistant_text),
                    )
                    yield format_sse(
                        "done",
                        {
                            "thread_id": thread_id,
                            "assistant_message": assistant_text,
                            "request_id": request_id,
                        },
                    )
                except Exception as e:
                    logger.exception(
                        "Direct stream request failed thread_id=%s user_id=%s",
                        thread_id,
                        user_id,
                    )
                    yield format_sse(
                        "error",
                        {"message": str(e), "request_id": request_id},
                    )

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

    except UserNotFoundError:
        return jsonify({
            "status": "error",
            "message": "User not found.",
            "data": None,
        }), 404

    except Exception as e:
        logger.exception("Unexpected error in /stream endpoint")
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
        payload = read_chat_request_payload(request)
        message = payload.message
        thread_id = payload.thread_id
        user_id = payload.user_id

        validate_user_id(user_id)

        run_id = str(uuid.uuid4())
        request_id = get_request_id()
        logger.info(
            "Queued stream request received run_id=%s thread_id=%s user_id=%s",
            run_id,
            thread_id,
            user_id,
        )

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
                "user_id": user_id,
            },
            queue="stream",
            headers={"request_id": request_id},
        )

        set_task_id(run_id, task.id)
        register_task(task.id)
        logger.info(
            "Queued stream task published run_id=%s task_id=%s thread_id=%s",
            run_id,
            task.id,
            thread_id,
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

    except ValueError as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "data": None,
        }), 400

    except UserNotFoundError:
        return jsonify({
            "status": "error",
            "message": "User not found.",
            "data": None,
        }), 404

    except Exception as e:
        logger.exception("Failed to start streaming task")
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

      The connection is automatically closed after
      ``STREAM_MAX_LIFETIME_SECONDS`` seconds (default 300) even if no
      terminal event has been received, and a ``timeout`` SSE event is
      emitted before closing.
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
      204:
        description: >
          The stream has already reached a terminal event and the caller is
          fully caught up, so there are no more events to deliver.
      400:
        description: The supplied ``last_event_id`` is not a valid Redis stream ID.
      404:
        description: The run identifier is not known or has expired.
      409:
        description: >
          The supplied ``last_event_id`` is ahead of the latest available
          stream event and cannot be resumed safely.
    """
    redis = get_redis()
    key = stream_key(run_id)
    request_id = get_request_id()

    last_event_id = request.headers.get("Last-Event-ID", "").strip()
    if not last_event_id:
        last_event_id = request.args.get("last_event_id", "0-0").strip() or "0-0"

    try:
        parse_stream_event_id(last_event_id)
    except ValueError as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "data": None,
        }), 400

    latest_event_id, latest_event_type = get_latest_stream_event(redis, run_id)
    state = get_status(run_id)
    is_terminal = is_terminal_stream_state(state, latest_event_type)

    if not state and not latest_event_id:
        return jsonify({
            "status": "error",
            "message": "Run not found.",
            "data": None,
        }), 404

    if latest_event_id and compare_stream_event_ids(last_event_id, latest_event_id) > 0:
        return jsonify({
            "status": "error",
            "message": "last_event_id is ahead of the latest available event.",
            "data": {
                "run_id": run_id,
                "last_event_id": last_event_id,
                "latest_event_id": latest_event_id,
                "state": state,
            },
        }), 409

    if is_terminal and (
        not latest_event_id
        or compare_stream_event_ids(last_event_id, latest_event_id) >= 0
    ):
        return Response(status=204)

    def generate():
        with logging_context(request_id=request_id, service_name="web"):
            current_id = last_event_id
            # Record the wall-clock deadline for this connection.
            deadline = time.monotonic() + STREAM_MAX_LIFETIME_SECONDS

            redis_logger.info(
                "Redis stream subscription opened run_id=%s last_event_id=%s deadline_seconds=%s",
                run_id,
                last_event_id,
                STREAM_MAX_LIFETIME_SECONDS,
            )
            yield ": connected\n\n"

            try:
                while True:
                    #  Hard timeout check 
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        redis_logger.warning(
                            "Redis stream subscription timed out run_id=%s current_id=%s "
                            "limit_seconds=%s",
                            run_id,
                            current_id,
                            STREAM_MAX_LIFETIME_SECONDS,
                        )
                        yield format_sse(
                            "timeout",
                            {
                                "run_id": run_id,
                                "message": (
                                    f"Stream connection closed after "
                                    f"{STREAM_MAX_LIFETIME_SECONDS}s. "
                                    "Reconnect to resume."
                                ),
                                "request_id": request_id,
                            },
                        )
                        return

                    # Cap the Redis block time so we re-check the deadline at
                    # least once per 30-second window even when idle.
                    block_ms = min(30000, int(remaining * 1_000))

                    events = redis.xread({key: current_id}, block=block_ms, count=20)

                    if not events:
                        #  Timeout on this xread window
                        redis_logger.debug(
                            "Redis stream read timeout run_id=%s current_id=%s",
                            run_id,
                            current_id,
                        )
                        latest_event_id, latest_event_type = get_latest_stream_event(redis, run_id)
                        state = get_status(run_id)
                        is_terminal = is_terminal_stream_state(state, latest_event_type)

                        if latest_event_id and compare_stream_event_ids(current_id, latest_event_id) > 0:
                            yield format_sse(
                                "error",
                                {
                                    "message": "last_event_id is ahead of the latest available event.",
                                    "run_id": run_id,
                                    "last_event_id": current_id,
                                    "latest_event_id": latest_event_id,
                                    "state": state,
                                    "request_id": request_id,
                                },
                            )
                            return

                        if is_terminal and (
                            not latest_event_id
                            or compare_stream_event_ids(current_id, latest_event_id) >= 0
                        ):
                            redis_logger.info(
                                "Redis stream subscription completed run_id=%s current_id=%s state=%s",
                                run_id,
                                current_id,
                                state,
                            )
                            return

                        yield ": keepalive\n\n"
                        continue

                    #  Deliver received events 
                    for _, messages in events:
                        redis_logger.debug(
                            "Redis stream batch received run_id=%s count=%s",
                            run_id,
                            len(messages),
                        )
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
                                redis_logger.info(
                                    "Redis stream terminal event delivered run_id=%s "
                                    "event_type=%s event_id=%s",
                                    run_id,
                                    event_type,
                                    msg_id,
                                )
                                return

            except Exception:
                redis_logger.exception("Redis stream subscription failed run_id=%s", run_id)
                raise

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
        logger.info("Stream cancellation requested run_id=%s", run_id)
        request_cancel(run_id)
        set_status(run_id, "CANCELLING")

        task_id = get_task_id(run_id)
        if task_id:
            celery_app.control.revoke(task_id, terminate=False)
            logger.info("Celery revoke requested run_id=%s task_id=%s", run_id, task_id)

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
        logger.exception("Failed to request stream cancellation run_id=%s", run_id)
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
        logger.exception("Failed to fetch stream status run_id=%s", run_id)
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
        user_id = require_query_param(request.args.get("user_id"), "user_id")
        page, per_page = parse_pagination(
            request.args.get("page"),
            request.args.get("per_page"),
        )

        result = get_all_threads(user_id, page=page, per_page=per_page)
        total = result["total"]

        return jsonify({
            "status": True,
            "message": "Threads fetched successfully.",
            "data": result["items"],
            "metaData": build_pagination_metadata(total, page, per_page),
        }), 200

    except ValueError as e:
        return jsonify({
            "status": False,
            "message": str(e),
            "data": None,
        }), 400

    except UserNotFoundError:
        return jsonify({
            "status": False,
            "message": "User not found.",
            "data": None,
        }), 404

    except Exception as e:
        logger.exception("Failed to fetch threads user_id=%s", user_id)
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
        user_id = require_query_param(request.args.get("user_id"), "user_id")
        page, per_page = parse_pagination(
            request.args.get("page"),
            request.args.get("per_page"),
        )

        result = get_thread_history(thread_id, user_id, page=page, per_page=per_page)
        total = result["total"]

        return jsonify({
            "status": "success",
            "message": "Thread history fetched successfully.",
            "data": result["items"],
            "metaData": build_pagination_metadata(total, page, per_page),
        }), 200

    except UserNotFoundError:
        return jsonify({
            "status": "error",
            "message": "User not found.",
            "data": None,
        }), 404

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
        logger.exception(
            "Failed to fetch thread history thread_id=%s user_id=%s",
            thread_id,
            user_id,
        )
        return jsonify({
            "status": "error",
            "message": "Failed to fetch thread history.",
            "data": None,
        }), 500

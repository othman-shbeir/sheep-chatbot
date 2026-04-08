import json
import sys
from dataclasses import dataclass
from urllib.parse import urlencode, urljoin

import requests

try:
    from .config import BASE_URL
except ImportError:
    from config import BASE_URL


_START_TIMEOUT = 10
_SSE_READ_TIMEOUT = 120
_STATUS_TIMEOUT = 10
_MAX_RECONNECTS = 3


class StreamError(Exception):
    """Raised when the server returns an error event or unexpected response."""


@dataclass(frozen=True)
class StreamRun:
    """Identifiers and follow-up endpoints returned by /stream/start."""

    run_id: str
    events_url: str
    status_url: str | None


def send_message(message: str, thread_id: str, user_id: str) -> None:
    """Send a message and stream the assistant's response to stdout."""
    stream_run = _start_stream(message, thread_id, user_id)
    _consume_stream(stream_run)


def _start_stream(message: str, thread_id: str, user_id: str) -> StreamRun:
    """POST /stream/start and return the run metadata needed to consume events."""
    url = f"{BASE_URL}/stream/start"
    payload = {
        "message": message,
        "thread_id": thread_id,
        "user_id": user_id,
    }

    try:
        response = requests.post(url, json=payload, timeout=_START_TIMEOUT)
    except requests.exceptions.ConnectionError:
        raise StreamError(
            f"Could not connect to the API at {BASE_URL}.\n"
            "Make sure the server is running."
        )
    except requests.exceptions.Timeout:
        raise StreamError("Request to /stream/start timed out.")

    if response.status_code != 202:
        _raise_from_response("/stream/start", response)

    data = response.json().get("data", {})
    run_id = data.get("run_id")
    events_url = _resolve_url(data.get("events_url")) or f"{BASE_URL}/stream/events/{run_id}"
    status_url = _resolve_url(data.get("status_url"))

    if not run_id:
        raise StreamError("Server did not return a run_id.")

    return StreamRun(
        run_id=run_id,
        events_url=events_url,
        status_url=status_url,
    )


def _consume_stream(stream_run: StreamRun) -> None:
    """Consume Redis-backed SSE events with resume support."""
    printed_prefix = False
    last_event_id = "0-0"
    reconnect_attempts = 0
    saw_terminal_event = False

    while reconnect_attempts <= _MAX_RECONNECTS:
        url = _build_events_url(stream_run.events_url, last_event_id)

        try:
            with requests.get(
                url,
                stream=True,
                timeout=(10, _SSE_READ_TIMEOUT),
                headers={"Accept": "text/event-stream"},
            ) as response:
                if response.status_code == 204:
                    state = _fetch_run_state(stream_run.status_url)
                    if state == "FAILURE":
                        raise StreamError("The streaming run failed.")
                    if state == "CANCELLED":
                        raise StreamError("Stream was cancelled by the server.")
                    if state in {"SUCCESS", None}:
                        if printed_prefix and not saw_terminal_event:
                            print()
                        return
                    reconnect_attempts += 1
                    continue

                if response.status_code != 200:
                    _raise_from_response("/stream/events", response)

                for event in _iter_sse_events(response):
                    if event["id"]:
                        last_event_id = event["id"]

                    event_type = event["event"]
                    payload = event["payload"]

                    if event_type in {"queued", "started", "cancelling"}:
                        continue

                    if event_type == "delta":
                        if not printed_prefix:
                            print("\nAssistant: ", end="", flush=True)
                            printed_prefix = True
                        print(payload.get("delta", ""), end="", flush=True)
                        continue

                    if event_type == "done":
                        if not printed_prefix:
                            print("\nAssistant: ", end="", flush=True)
                            printed_prefix = True
                        saw_terminal_event = True
                        print()
                        return

                    if event_type == "error":
                        if printed_prefix:
                            print()
                        raise StreamError(
                            f"Server error: {payload.get('message', 'unknown error')}"
                        )

                    if event_type == "cancelled":
                        if printed_prefix:
                            print()
                        raise StreamError("Stream was cancelled by the server.")

                    if event_type == "timeout":
                        reconnect_attempts += 1
                        if reconnect_attempts > _MAX_RECONNECTS:
                            if printed_prefix:
                                print()
                            raise StreamError(
                                "Stream timed out repeatedly while waiting for completion."
                            )
                        break
                else:
                    state = _fetch_run_state(stream_run.status_url)
                    if state == "SUCCESS":
                        if printed_prefix and not saw_terminal_event:
                            print()
                        return
                    if state == "FAILURE":
                        if printed_prefix:
                            print()
                        raise StreamError("The streaming run failed.")
                    if state == "CANCELLED":
                        if printed_prefix:
                            print()
                        raise StreamError("Stream was cancelled by the server.")

                    reconnect_attempts += 1
                    if reconnect_attempts > _MAX_RECONNECTS:
                        if printed_prefix:
                            print()
                        raise StreamError("Streaming disconnected before completion.")
                    continue

        except requests.exceptions.Timeout:
            reconnect_attempts += 1
            if reconnect_attempts > _MAX_RECONNECTS:
                if printed_prefix:
                    print()
                raise StreamError(
                    "SSE connection timed out waiting for the next event.\n"
                    "The server may be under heavy load. Try again."
                )
            continue
        except requests.exceptions.ConnectionError:
            reconnect_attempts += 1
            if reconnect_attempts > _MAX_RECONNECTS:
                if printed_prefix:
                    print()
                raise StreamError("Lost connection to the API mid-stream.")
            continue

    if printed_prefix:
        print()
    raise StreamError("Streaming disconnected before completion.")


def _iter_sse_events(response: requests.Response):
    """Yield parsed SSE frames from a streaming HTTP response."""
    current_event_id = ""
    current_event_type = "message"
    data_lines: list[str] = []

    for raw_line in response.iter_lines(decode_unicode=True):
        line = raw_line or ""

        if line == "":
            event = _build_sse_event(current_event_id, current_event_type, data_lines)
            if event is not None:
                yield event

            current_event_id = ""
            current_event_type = "message"
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        if line.startswith("id:"):
            current_event_id = line[len("id:"):].strip()
            continue

        if line.startswith("event:"):
            current_event_type = line[len("event:"):].strip() or "message"
            continue

        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())

    event = _build_sse_event(current_event_id, current_event_type, data_lines)
    if event is not None:
        yield event


def _build_sse_event(event_id: str, event_type: str, data_lines: list[str]) -> dict | None:
    """Build a normalized event dict from buffered SSE fields."""
    if not event_id and not data_lines and event_type == "message":
        return None

    raw_data = "\n".join(data_lines).strip()
    if raw_data:
        try:
            payload = json.loads(raw_data)
        except json.JSONDecodeError:
            payload = {"raw": raw_data}
    else:
        payload = {}

    return {
        "id": event_id,
        "event": event_type or "message",
        "payload": payload,
    }


def _fetch_run_state(status_url: str | None) -> str | None:
    """Read the latest run state from /stream/status/<run_id> when available."""
    if not status_url:
        return None

    try:
        response = requests.get(status_url, timeout=_STATUS_TIMEOUT)
    except requests.RequestException:
        return None

    if response.status_code == 404:
        return None

    if response.status_code != 200:
        return None

    try:
        return response.json().get("data", {}).get("state")
    except Exception:
        return None


def _build_events_url(events_url: str, last_event_id: str) -> str:
    """Append the resume cursor to the events URL when needed."""
    if not last_event_id or last_event_id == "0-0":
        return events_url

    separator = "&" if "?" in events_url else "?"
    return f"{events_url}{separator}{urlencode({'last_event_id': last_event_id})}"


def _resolve_url(url: str | None) -> str | None:
    """Resolve API-relative URLs returned by the backend against BASE_URL."""
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("/"):
        return urljoin(BASE_URL, url)
    return urljoin(f"{BASE_URL.rstrip('/')}/", url)


def _raise_from_response(endpoint: str, response: requests.Response) -> None:
    """Raise a StreamError with a readable message from an error response."""
    try:
        body = response.json()
        message = body.get("message", response.text)
    except Exception:
        message = response.text or "no response body"

    raise StreamError(f"{endpoint} returned {response.status_code}: {message}")

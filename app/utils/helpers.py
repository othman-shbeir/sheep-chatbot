"""General-purpose helper utilities.

Contains pure functions used across the application for content extraction
and Server-Sent Events (SSE) formatting.
"""

from langchain_core.messages import BaseMessage
import json


def extract_text_content(message: BaseMessage) -> str:
    """Extract plain-text content from a LangChain message.

    LangChain message ``content`` may be a plain string **or** a list of
    content blocks (e.g. ``[{"type": "text", "text": "..."}]``).  This
    function normalises both representations into a single string.

    Args:
        message: Any LangChain ``BaseMessage`` subclass.

    Returns:
        str: The concatenated text content, stripped of leading/trailing
        whitespace.  Returns an empty string when no text is found.
    """
    content = getattr(message, "content", "")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and "text" in item:
                    parts.append(str(item["text"]))
                elif "text" in item:
                    parts.append(str(item["text"]))
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()

    return str(content).strip()


def extract_stream_text_content(message: BaseMessage) -> str:
    """Extract streaming text without trimming chunk boundaries.

    Streaming model chunks may carry meaningful leading or trailing spaces.
    Unlike :func:`extract_text_content`, this helper preserves those boundary
    characters so concatenated deltas reconstruct the original response text.
    """
    content = getattr(message, "content", "")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and "text" in item:
                    parts.append(str(item["text"]))
                elif "text" in item:
                    parts.append(str(item["text"]))
            else:
                parts.append(str(item))
        return "\n".join(parts)

    return str(content)


def format_sse(event: str, data: dict) -> str:
    """Format a payload as a Server-Sent Events (SSE) message.

    The returned string conforms to the `SSE specification
    <https://html.spec.whatwg.org/multipage/server-sent-events.html>`_ and
    can be yielded directly from a Flask streaming response.

    Args:
        event: The SSE event name (e.g. ``"delta"``, ``"done"``, ``"error"``).
        data: A JSON-serialisable dictionary that will be sent as the
            ``data:`` field.

    Returns:
        str: A complete SSE frame ending with a double newline.

    Example::

        >>> format_sse("delta", {"delta": "Hello"})
        'event: delta\\ndata: {"delta": "Hello"}\\n\\n'
    """
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"

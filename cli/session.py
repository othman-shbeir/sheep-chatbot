import os
import uuid

# Path to the local session file in the user's home directory.
_SESSION_FILE = os.path.expanduser("~/.sheep_chat")


def load_thread_id() -> str:
    """Return the saved thread_id, or create and save a new one."""
    if os.path.exists(_SESSION_FILE):
        thread_id = _read_session_file()
        if thread_id:
            return thread_id

    # No valid session found — generate a fresh thread_id.
    return _create_new_session()


def reset_thread_id() -> str:
    """Delete the current session and start a fresh conversation thread."""
    thread_id = str(uuid.uuid4())
    _write_session_file(thread_id)
    return thread_id


def _read_session_file() -> str | None:
    """Read and validate the thread_id from the session file."""
    try:
        with open(_SESSION_FILE, "r", encoding="utf-8") as f:
            thread_id = f.read().strip()

        # Validate it looks like a UUID before trusting it.
        uuid.UUID(thread_id)
        return thread_id

    except (ValueError, OSError):
        # File is corrupted or unreadable — treat as missing.
        return None


def _create_new_session() -> str:
    """Generate a new thread_id, persist it, and return it."""
    thread_id = str(uuid.uuid4())
    _write_session_file(thread_id)
    return thread_id


def _write_session_file(thread_id: str) -> None:
    """Write thread_id to the session file, creating it if needed."""
    try:
        with open(_SESSION_FILE, "w", encoding="utf-8") as f:
            f.write(thread_id)
    except OSError as e:
        # Non-fatal — the CLI still works, just won't persist across runs.
        print(f"[warning] Could not save session file: {e}")
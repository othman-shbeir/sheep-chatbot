import argparse
import sys

try:
    from .config import BASE_URL, USER_ID
    from .session import load_thread_id, reset_thread_id
    from .stream_client import StreamError, send_message
except ImportError:
    from config import BASE_URL, USER_ID
    from session import load_thread_id, reset_thread_id
    from stream_client import StreamError, send_message

# ANSI colour helpers

def _cyan(text: str) -> str:
    return f"\033[96m{text}\033[0m"

def _yellow(text: str) -> str:
    return f"\033[93m{text}\033[0m"

def _red(text: str) -> str:
    return f"\033[91m{text}\033[0m"

def _dim(text: str) -> str:
    return f"\033[2m{text}\033[0m"


# Command handlers
def _handle_slash_command(command: str, thread_id: str) -> str:
    """Handle /commands entered by the user. Returns the (possibly new) thread_id."""
    cmd = command.strip().lower()

    if cmd == "/reset":
        thread_id = reset_thread_id()
        print(_yellow(f"Conversation reset. New thread: {thread_id}"))

    elif cmd == "/thread":
        print(_dim(f"Current thread_id: {thread_id}"))

    elif cmd == "/help":
        print(_dim(
            "\nAvailable commands:\n"
            "  /reset    Start a new conversation thread\n"
            "  /thread   Show the current thread_id\n"
            "  /help     Show this help message\n"
            "  exit      Quit the CLI\n"
        ))

    else:
        print(_yellow(f"Unknown command: {command}. Type /help for available commands."))

    return thread_id


# Main loop
def main() -> None:
    parser = argparse.ArgumentParser(description="Sheep Chatbot CLI")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Start a fresh conversation thread and exit.",
    )
    args = parser.parse_args()

    #  Startup 
    thread_id = load_thread_id()

    if args.reset:
        thread_id = reset_thread_id()
        print(_yellow(f"Session reset. New thread_id: {thread_id}"))
        sys.exit(0)

    print(_cyan("=" * 52))
    print(_cyan("      Sheep Chatbot CLI"))
    print(_cyan("=" * 52))
    print(_dim(f"   API:      {BASE_URL}"))
    print(_dim(f"   User:     {USER_ID}"))
    print(_dim(f"   Thread:   {thread_id}"))
    print(_dim("   Type /help for commands, 'exit' to quit."))
    print(_cyan("=" * 52))
    print()

    #  Input loop 
    while True:
        try:
            user_input = input(_cyan("User: ")).strip()
        except (KeyboardInterrupt, EOFError):
            # Ctrl+C: exit cleanly.
            print()
            print(_yellow("Goodbye!"))
            sys.exit(0)

        # Empty input — just loop again.
        if not user_input:
            continue

        # Exit commands.
        if user_input.lower() in {"exit", "quit"}:
            print(_yellow("Goodbye!"))
            sys.exit(0)

        # Slash commands (/reset, /thread, /help).
        if user_input.startswith("/"):
            thread_id = _handle_slash_command(user_input, thread_id)
            continue

        # Send message to API 
        try:
            send_message(
                message=user_input,
                thread_id=thread_id,
                user_id=USER_ID,
            )
        except StreamError as e:
            print(_red(f"\n[error] {e}"))
        except Exception as e:
            print(_red(f"\n[unexpected error] {e}"))

        print()


if __name__ == "__main__":
    main()

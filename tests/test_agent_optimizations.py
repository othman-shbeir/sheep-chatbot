"""Unit tests for agent-layer refactors and query-path optimizations."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.agents import agent


class _SessionContext:
    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self):
        self.committed = False

    def commit(self):
        self.committed = True


class AgentOptimizationTestCase(unittest.TestCase):
    """Verify agent helpers keep behavior while reducing duplicated work."""

    def test_build_context_messages_fetches_summary_boundary_once(self):
        fake_session = object()
        summary = SimpleNamespace(summary_text="Earlier summary", covered_until_order=7)
        unsummarized_messages = [
            SimpleNamespace(role="user", content="Hello"),
            SimpleNamespace(role="assistant", content="Hi"),
        ]

        with (
            patch.object(agent, "require_database"),
            patch.object(agent, "SessionLocal", return_value=_SessionContext(fake_session)),
            patch.object(agent, "get_latest_summary", return_value=summary) as get_summary,
            patch.object(
                agent,
                "get_messages_after_order",
                return_value=unsummarized_messages,
            ) as get_messages,
        ):
            context_messages = agent.build_context_messages("thread-123")

        self.assertEqual(get_summary.call_count, 1)
        get_messages.assert_called_once_with(fake_session, "thread-123", 7)
        self.assertEqual(context_messages[1].content.splitlines()[0], "Conversation memory summary:")
        self.assertEqual(len(context_messages), 4)

    def test_maybe_compact_thread_uses_config_keep_recent_default(self):
        fake_session = _FakeSession()
        unsummarized_messages = [
            SimpleNamespace(
                role="user",
                content=f"Message {index}",
                id=index,
                message_order=index,
            )
            for index in range(1, 7)
        ]

        with (
            patch.object(agent, "require_database"),
            patch.object(agent, "SessionLocal", return_value=_SessionContext(fake_session)),
            patch.object(agent.Config, "SUMMARY_TRIGGER_COUNT", 4),
            patch.object(agent.Config, "KEEP_RECENT_COUNT", 2),
            patch.object(agent, "get_latest_summary", return_value=None) as get_summary,
            patch.object(
                agent,
                "get_messages_after_order",
                return_value=unsummarized_messages,
            ) as get_messages,
            patch.object(agent, "get_llm") as get_llm,
            patch.object(agent, "extract_text_content", return_value="condensed"),
            patch.object(agent, "create_summary") as create_summary,
        ):
            get_llm.return_value.invoke.return_value = SimpleNamespace(content="condensed")
            agent.maybe_compact_thread("thread-123")

        self.assertEqual(get_summary.call_count, 1)
        get_messages.assert_called_once_with(fake_session, "thread-123", None)
        create_summary.assert_called_once()
        self.assertEqual(create_summary.call_args.kwargs["covered_until_order"], 4)
        self.assertTrue(fake_session.committed)


if __name__ == "__main__":
    unittest.main()

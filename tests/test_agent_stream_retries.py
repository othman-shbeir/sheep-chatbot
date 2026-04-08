"""Regression tests for transient model-stream retry behavior."""

import unittest
from unittest.mock import patch

from app.agents import agent


class FakeChunk:
    """Minimal streaming chunk carrying text content."""

    def __init__(self, content):
        self.content = content


class FakeProviderError(Exception):
    """Provider error stub with the fields inspected by retry logic."""

    def __init__(self, status_code, message, status="UNAVAILABLE"):
        super().__init__(message)
        self.status_code = status_code
        self.response_json = {
            "error": {
                "code": status_code,
                "message": message,
                "status": status,
            }
        }


class RetryThenSuccessModel:
    """Fail once before streaming any token, then succeed."""

    def __init__(self):
        self.calls = 0

    def stream(self, _messages):
        self.calls += 1
        if self.calls == 1:
            raise FakeProviderError(
                503,
                "This model is currently experiencing high demand. Please try again later.",
            )

        yield FakeChunk("hello")
        yield FakeChunk(" world")


class PartialFailureModel:
    """Fail after a partial streamed response has already been emitted."""

    def __init__(self):
        self.calls = 0

    def stream(self, _messages):
        self.calls += 1
        yield FakeChunk("partial")
        raise FakeProviderError(
            503,
            "This model is currently experiencing high demand. Please try again later.",
        )


class AgentStreamRetryTestCase(unittest.TestCase):
    """Verify transient retries only happen before the first streamed token."""

    def test_stream_retries_transient_error_before_first_chunk(self):
        model = RetryThenSuccessModel()

        with (
            patch("app.agents.agent.get_model_with_search", return_value=model),
            patch("app.agents.agent.time.sleep") as sleep_mock,
        ):
            chunks = list(agent._stream_model_with_retry(["message"]))

        self.assertEqual([chunk.content for chunk in chunks], ["hello", " world"])
        self.assertEqual(model.calls, 2)
        sleep_mock.assert_called_once_with(1.0)

    def test_stream_does_not_retry_after_partial_output(self):
        model = PartialFailureModel()

        with (
            patch("app.agents.agent.get_model_with_search", return_value=model),
            patch("app.agents.agent.time.sleep") as sleep_mock,
        ):
            iterator = agent._stream_model_with_retry(["message"])
            self.assertEqual(next(iterator).content, "partial")
            with self.assertRaises(FakeProviderError):
                next(iterator)

        self.assertEqual(model.calls, 1)
        sleep_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Regression tests for SSE stream cursor validation and replay behavior."""

import json
import unittest
from unittest.mock import patch

from app import create_app


class FakeRedis:
    """Minimal Redis stub for stream cursor endpoint tests."""

    def __init__(self, latest_event=None, xread_result=None):
        self.latest_event = latest_event
        self.xread_result = xread_result or []

    def xrevrange(self, key, count=1):
        if self.latest_event is None:
            return []
        return [self.latest_event]

    def xread(self, streams, block=None, count=None):
        return self.xread_result


class StreamEventsCursorHandlingTestCase(unittest.TestCase):
    """Verify SSE resume behavior for valid and invalid Redis stream cursors."""

    def setUp(self):
        app = create_app()
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_stream_events_returns_204_when_terminal_stream_is_fully_consumed(self):
        redis = FakeRedis(
            latest_event=("5-0", {"type": "done", "data": json.dumps({"ok": True})})
        )

        with (
            patch("app.routes.chat.get_redis", return_value=redis),
            patch("app.routes.chat.get_status", return_value="SUCCESS"),
        ):
            response = self.client.get(
                "/api/v1/chat/stream/events/run-123?last_event_id=5-0"
            )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.get_data(as_text=True), "")

    def test_stream_events_rejects_cursor_ahead_of_latest_event(self):
        redis = FakeRedis(
            latest_event=("5-0", {"type": "done", "data": json.dumps({"ok": True})})
        )

        with (
            patch("app.routes.chat.get_redis", return_value=redis),
            patch("app.routes.chat.get_status", return_value="SUCCESS"),
        ):
            response = self.client.get(
                "/api/v1/chat/stream/events/run-123?last_event_id=6-0"
            )

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(
            payload["message"],
            "last_event_id is ahead of the latest available event.",
        )
        self.assertEqual(payload["data"]["latest_event_id"], "5-0")

    def test_stream_events_replays_missing_terminal_event(self):
        redis = FakeRedis(
            latest_event=("5-0", {"type": "done", "data": json.dumps({"ok": True})}),
            xread_result=[
                (
                    "run:run-123:events",
                    [
                        (
                            "5-0",
                            {
                                "type": "done",
                                "data": json.dumps({"assistant_message": "complete"}),
                            },
                        )
                    ],
                )
            ],
        )

        with (
            patch("app.routes.chat.get_redis", return_value=redis),
            patch("app.routes.chat.get_status", return_value="SUCCESS"),
        ):
            response = self.client.get(
                "/api/v1/chat/stream/events/run-123?last_event_id=4-0"
            )

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("id: 5-0", body)
        self.assertIn("event: done", body)
        self.assertIn('"assistant_message": "complete"', body)

    def test_stream_events_rejects_invalid_cursor_format(self):
        redis = FakeRedis()

        with (
            patch("app.routes.chat.get_redis", return_value=redis),
            patch("app.routes.chat.get_status", return_value="PENDING"),
        ):
            response = self.client.get(
                "/api/v1/chat/stream/events/run-123?last_event_id=invalid"
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["message"],
            "last_event_id must be a valid Redis stream ID in the form "
            "'<milliseconds>-<sequence>'.",
        )


if __name__ == "__main__":
    unittest.main()

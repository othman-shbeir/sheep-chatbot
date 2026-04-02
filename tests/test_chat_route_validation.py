"""Regression tests for shared chat route validation helpers."""

import unittest

from app import create_app


class ChatRouteValidationTestCase(unittest.TestCase):
    """Validate request parsing and pagination guardrails on chat routes."""

    def setUp(self):
        app = create_app()
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_one_shot_rejects_non_object_json_body(self):
        response = self.client.post("/api/v1/chat/one-shot", json=[])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["message"],
            "Request body must be a JSON object.",
        )

    def test_stream_start_rejects_invalid_json_payload(self):
        response = self.client.post(
            "/api/v1/chat/stream/start",
            data="{",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["message"],
            "Request body must contain valid JSON.",
        )

    def test_threads_rejects_page_below_one(self):
        response = self.client.get("/api/v1/chat/threads?user_id=user-1&page=0")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["message"],
            "The 'page' query parameter must be greater than or equal to 1.",
        )

    def test_threads_rejects_non_integer_page(self):
        response = self.client.get("/api/v1/chat/threads?user_id=user-1&page=abc")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["message"],
            "The 'page' query parameter must be an integer.",
        )

    def test_thread_history_rejects_per_page_above_maximum(self):
        response = self.client.get(
            "/api/v1/chat/threads/thread-1/history?user_id=user-1&per_page=101"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["message"],
            "The 'per_page' query parameter must be between 1 and 100.",
        )


if __name__ == "__main__":
    unittest.main()

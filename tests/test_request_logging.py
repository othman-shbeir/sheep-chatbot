"""Regression tests for Flask request-id logging middleware."""

import io
import unittest
from unittest.mock import patch

from flask import Flask, Response, jsonify

from app.config.config import Config
from app.core.logging_config import configure_flask_logging, get_request_id, reset_logging


class RequestLoggingMiddlewareTestCase(unittest.TestCase):
    """Verify request identifiers are generated and propagated by Flask hooks."""

    def setUp(self):
        reset_logging()

    def tearDown(self):
        reset_logging()

    def _build_app(self) -> Flask:
        app = Flask(__name__)
        configure_flask_logging(app)

        @app.get("/ping")
        def ping():
            return jsonify({"request_id": get_request_id()})

        @app.get("/events")
        def events():
            def generate():
                yield "data: hello\n\n"
                yield "data: done\n\n"

            return Response(generate(), mimetype="text/event-stream")

        return app

    def test_existing_request_id_header_is_reused(self):
        with (
            patch.object(Config, "LOG_ENABLE_CONSOLE", "false"),
            patch.object(Config, "LOG_ENABLE_FILE", "false"),
        ):
            client = self._build_app().test_client()
            response = client.get("/ping", headers={Config.REQUEST_ID_HEADER: "req-external"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers[Config.REQUEST_ID_HEADER], "req-external")
        self.assertEqual(response.get_json()["request_id"], "req-external")

    def test_request_id_is_generated_when_the_header_is_missing(self):
        with (
            patch.object(Config, "LOG_ENABLE_CONSOLE", "false"),
            patch.object(Config, "LOG_ENABLE_FILE", "false"),
        ):
            client = self._build_app().test_client()
            response = client.get("/ping")

        self.assertEqual(response.status_code, 200)
        generated_request_id = response.headers[Config.REQUEST_ID_HEADER]
        self.assertTrue(generated_request_id.startswith("req-"))
        self.assertEqual(response.get_json()["request_id"], generated_request_id)

    def test_non_streaming_response_logs_duration_ms(self):
        stream = io.StringIO()

        with (
            patch.object(Config, "LOG_ENABLE_CONSOLE", "true"),
            patch.object(Config, "LOG_ENABLE_FILE", "false"),
            patch("sys.stdout", stream),
            patch("app.core.logging_config.time.perf_counter", side_effect=[100.0, 100.25]),
        ):
            client = self._build_app().test_client()
            response = client.get("/ping")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "HTTP response sent method=GET path=/ping status=200 duration_ms=250",
            stream.getvalue(),
        )

    def test_streaming_response_logs_duration_when_the_stream_closes(self):
        stream = io.StringIO()

        with (
            patch.object(Config, "LOG_ENABLE_CONSOLE", "true"),
            patch.object(Config, "LOG_ENABLE_FILE", "false"),
            patch("sys.stdout", stream),
            patch("app.core.logging_config.time.perf_counter", side_effect=[200.0, 202.5]),
        ):
            client = self._build_app().test_client()
            response = client.get("/events", buffered=False)
            list(response.response)
            response.close()

        log_output = stream.getvalue()
        self.assertIn("HTTP stream opened method=GET path=/events status=200", log_output)
        self.assertIn(
            "HTTP stream closed method=GET path=/events status=200 duration_ms=2500",
            log_output,
        )
        self.assertNotIn(
            "HTTP response sent method=GET path=/events status=200 duration_ms=",
            log_output,
        )


if __name__ == "__main__":
    unittest.main()

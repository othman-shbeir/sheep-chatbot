"""Regression tests for the centralized logging configuration."""

import io
import logging
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config.config import Config
from app.core.logging_config import (
    MultiProcessRotatingFileHandler,
    ServiceFileRouterHandler,
    bind_log_context,
    configure_logging,
    get_logger,
    reset_log_context,
    reset_logging,
)


class LoggerSetupTestCase(unittest.TestCase):
    """Verify logging configuration and service routing behavior."""

    def setUp(self):
        reset_logging()

    def tearDown(self):
        reset_logging()

    def test_contextual_service_and_request_id_are_injected_into_text_logs(self):
        stream = io.StringIO()

        with (
            patch.object(Config, "LOG_ENABLE_CONSOLE", "true"),
            patch.object(Config, "LOG_ENABLE_FILE", "false"),
            patch.object(Config, "LOG_JSON", "false"),
            patch.object(Config, "LOG_LEVEL", "INFO"),
            patch("sys.stdout", stream),
        ):
            configure_logging(force=True)
            tokens = bind_log_context(request_id="req-123", service_name="web")

            try:
                get_logger("system", "tests.logging").info("request started")
            finally:
                reset_log_context(tokens)

        log_output = stream.getvalue().strip()
        self.assertIn("[INFO] [web] [tests.logging] [req-123] request started", log_output)

    def test_service_logs_are_written_to_the_matching_file(self):
        log_dir = Path("tests") / ".tmp-logs"
        if log_dir.exists():
            shutil.rmtree(log_dir)

        try:
            with (
                patch.object(Config, "LOG_ENABLE_CONSOLE", "false"),
                patch.object(Config, "LOG_ENABLE_FILE", "true"),
                patch.object(Config, "LOG_JSON", "false"),
                patch.object(Config, "LOG_LEVEL", "INFO"),
                patch.object(Config, "LOG_DIR", str(log_dir)),
            ):
                configure_logging(force=True)
                get_logger("redis", "tests.redis").warning("redis timeout")

            router_handler = next(
                handler
                for handler in logging.getLogger().handlers
                if isinstance(handler, ServiceFileRouterHandler)
            )
            redis_log = log_dir / "redis.log"
            self.assertTrue(redis_log.exists())
            self.assertIsInstance(
                router_handler._handlers["redis"],
                MultiProcessRotatingFileHandler,
            )
            self.assertIn(
                "[WARNING] [redis] [tests.redis] [-] redis timeout",
                redis_log.read_text(encoding="utf-8"),
            )
        finally:
            reset_logging()
            if log_dir.exists():
                shutil.rmtree(log_dir)


if __name__ == "__main__":
    unittest.main()

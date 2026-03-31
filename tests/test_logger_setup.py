"""Regression tests for the shared logger configuration helper."""

import logging
import unittest
import uuid
from unittest.mock import patch

from app.logger import setup_logger


class LoggerSetupTestCase(unittest.TestCase):
    """Verify logger setup remains stable across repeated calls."""

    def setUp(self):
        self.logger_names: list[str] = []

    def tearDown(self):
        for logger_name in self.logger_names:
            logger = logging.getLogger(logger_name)
            logger.handlers.clear()
            logger.propagate = True

    def _new_logger_name(self) -> str:
        logger_name = f"tests.logger.{uuid.uuid4()}"
        self.logger_names.append(logger_name)
        return logger_name

    def test_setup_logger_reuses_the_same_handler(self):
        logger_name = self._new_logger_name()

        first_logger = setup_logger(logger_name)
        second_logger = setup_logger(logger_name)

        self.assertIs(first_logger, second_logger)
        self.assertEqual(len(first_logger.handlers), 1)
        self.assertFalse(first_logger.propagate)

    def test_setup_logger_honors_log_level_environment_variable(self):
        logger_name = self._new_logger_name()

        with patch.dict("os.environ", {"LOG_LEVEL": "DEBUG"}, clear=False):
            logger = setup_logger(logger_name)

        self.assertEqual(logger.level, logging.DEBUG)
        self.assertEqual(logger.handlers[0].level, logging.DEBUG)


if __name__ == "__main__":
    unittest.main()

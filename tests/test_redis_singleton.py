"""Regression tests for the shared Redis client singleton."""

import unittest
from unittest.mock import patch

from app.services import redis_client, stream_service
from app.utils import task_registry


class RedisSingletonTestCase(unittest.TestCase):
    """Verify the stream and task helpers share one Redis client."""

    def setUp(self):
        self.original_pool = redis_client._redis_pool
        self.original_client = redis_client._redis_client
        redis_client._redis_pool = None
        redis_client._redis_client = None

    def tearDown(self):
        redis_client._redis_pool = self.original_pool
        redis_client._redis_client = self.original_client

    def test_stream_service_and_task_registry_share_one_client(self):
        fake_pool = object()
        fake_client = object()

        with (
            patch.object(
                redis_client.LoggingConnectionPool,
                "from_url",
                return_value=fake_pool,
            ) as from_url,
            patch.object(redis_client, "Redis", return_value=fake_client) as redis_cls,
        ):
            stream_client = stream_service.get_redis()
            task_registry_client = task_registry.get_redis()
            second_stream_client = stream_service.get_redis()

        self.assertIs(stream_client, fake_client)
        self.assertIs(task_registry_client, fake_client)
        self.assertIs(second_stream_client, fake_client)
        self.assertEqual(from_url.call_count, 1)
        self.assertEqual(redis_cls.call_count, 1)


if __name__ == "__main__":
    unittest.main()

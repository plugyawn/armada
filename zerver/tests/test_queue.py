from typing import Any
from unittest import mock

import orjson
from django.test import override_settings
from pika.exceptions import AMQPConnectionError, ConnectionClosed
from typing_extensions import override

from zerver.lib.queue import (
    QUEUE_REDELIVERED_EVENT_KEY,
    QueueEventDiscardError,
    SimpleQueueClient,
    TornadoQueueClient,
    get_queue_client,
    queue_json_publish_rollback_unsafe,
)
from zerver.lib.test_classes import ZulipTestCase


class TestTornadoQueueClient(ZulipTestCase):
    @mock.patch("zerver.lib.queue.ExceptionFreeTornadoConnection", autospec=True)
    def test_on_open_closed(self, mock_cxn: mock.MagicMock) -> None:
        with self.assertLogs("zulip.queue", "WARNING") as m:
            mock_cxn().channel.side_effect = ConnectionClosed(500, "test")
            connection = TornadoQueueClient()
            connection._on_open(mock.MagicMock())
            self.assertEqual(
                m.output,
                [
                    "WARNING:zulip.queue:TornadoQueueClient couldn't open channel: connection already closed"
                ],
            )


class TestQueueImplementation(ZulipTestCase):
    def test_priority_consumers_share_one_prefetch_and_preserve_redelivery(self) -> None:
        client = object.__new__(SimpleQueueClient)
        channel = mock.MagicMock()
        client.channel = channel
        client.prefetch = 1
        client.is_consuming = False
        callbacks: dict[str, Any] = {}
        received: list[dict[str, Any]] = []

        def ensure_queue(queue_name: str, callback: Any) -> None:
            callback(channel)

        def basic_consume(*, queue: str, on_message_callback: Any, **kwargs: Any) -> None:
            callbacks[queue] = (on_message_callback, kwargs)

        def start_consuming() -> None:
            callback, _kwargs = callbacks["pear_worker_1"]
            callback(
                channel,
                mock.Mock(delivery_tag=41, redelivered=True),
                mock.Mock(),
                orjson.dumps({"run_id": 9}),
            )

        client.ensure_queue = ensure_queue  # type: ignore[method-assign]
        channel.basic_consume.side_effect = basic_consume
        channel.start_consuming.side_effect = start_consuming

        client.start_json_consumers(
            {"pear_worker": 0, "pear_worker_1": 10},
            lambda events: received.extend(events),
            annotate_redelivery=True,
        )

        channel.basic_qos.assert_called_once_with(prefetch_count=1, global_qos=True)
        self.assertEqual(callbacks["pear_worker"][1]["arguments"], {"x-priority": 0})
        self.assertEqual(callbacks["pear_worker_1"][1]["arguments"], {"x-priority": 10})
        self.assertEqual(
            received,
            [{"run_id": 9, QUEUE_REDELIVERED_EVENT_KEY: True}],
        )
        channel.basic_ack.assert_called_once_with(41)

    def test_priority_consumer_nacks_and_requeues_callback_failures(self) -> None:
        client = object.__new__(SimpleQueueClient)
        channel = mock.MagicMock()
        channel.is_open = True
        client.channel = channel
        client.prefetch = 1
        client.is_consuming = False
        callbacks: dict[str, Any] = {}

        def ensure_queue(queue_name: str, callback: Any) -> None:
            callback(channel)

        def basic_consume(*, queue: str, on_message_callback: Any, **kwargs: Any) -> None:
            callbacks[queue] = (on_message_callback, kwargs)

        def start_consuming() -> None:
            callback, _kwargs = callbacks["pear_worker"]
            callback(
                channel,
                mock.Mock(delivery_tag=42, redelivered=False),
                mock.Mock(),
                orjson.dumps({"run_id": 10}),
            )

        client.ensure_queue = ensure_queue  # type: ignore[method-assign]
        channel.basic_consume.side_effect = basic_consume
        channel.start_consuming.side_effect = start_consuming

        def fail_before_turn(_events: list[dict[str, Any]]) -> None:
            raise RuntimeError("pre-turn setup failed")

        with self.assertRaisesRegex(RuntimeError, "pre-turn setup failed"):
            client.start_json_consumers(
                {"pear_worker": 10, "pear_worker_1": 0},
                fail_before_turn,
                annotate_redelivery=True,
            )

        channel.basic_ack.assert_not_called()
        channel.basic_nack.assert_called_once_with(42, requeue=True)

    def test_priority_consumer_discards_permanently_invalid_delivery(self) -> None:
        client = object.__new__(SimpleQueueClient)
        channel = mock.MagicMock()
        channel.is_open = True
        client.channel = channel
        client.prefetch = 1
        client.is_consuming = False
        callbacks: dict[str, Any] = {}

        def ensure_queue(_queue_name: str, callback: Any) -> None:
            callback(channel)

        def basic_consume(*, queue: str, on_message_callback: Any, **_kwargs: Any) -> None:
            callbacks[queue] = on_message_callback

        def start_consuming() -> None:
            callbacks["pear_worker"](
                channel,
                mock.Mock(delivery_tag=43, redelivered=False),
                mock.Mock(),
                orjson.dumps({"run_id": 11}),
            )

        client.ensure_queue = ensure_queue  # type: ignore[method-assign]
        channel.basic_consume.side_effect = basic_consume
        channel.start_consuming.side_effect = start_consuming

        client.start_json_consumers(
            {"pear_worker": 10},
            lambda _events: (_ for _ in ()).throw(QueueEventDiscardError("invalid event")),
        )

        channel.basic_ack.assert_not_called()
        channel.basic_nack.assert_called_once_with(43, requeue=False)

    def test_priority_consumer_discards_malformed_json(self) -> None:
        client = object.__new__(SimpleQueueClient)
        channel = mock.MagicMock()
        channel.is_open = True
        client.channel = channel
        client.prefetch = 1
        client.is_consuming = False
        callback: Any = None

        def ensure_queue(_queue_name: str, register: Any) -> None:
            register(channel)

        def basic_consume(*, on_message_callback: Any, **_kwargs: Any) -> None:
            nonlocal callback
            callback = on_message_callback

        def start_consuming() -> None:
            callback(
                channel,
                mock.Mock(delivery_tag=44, redelivered=False),
                mock.Mock(),
                b"not-json",
            )

        client.ensure_queue = ensure_queue  # type: ignore[method-assign]
        channel.basic_consume.side_effect = basic_consume
        channel.start_consuming.side_effect = start_consuming

        client.start_json_consumers({"pear_worker": 10}, lambda _events: None)

        channel.basic_ack.assert_not_called()
        channel.basic_nack.assert_called_once_with(44, requeue=False)

    @override_settings(USING_RABBITMQ=True)
    def test_register_consumer(self) -> None:
        output = []

        queue_client = get_queue_client()

        def collect(events: list[dict[str, Any]]) -> None:
            assert isinstance(queue_client, SimpleQueueClient)
            assert len(events) == 1
            output.append(events[0])
            queue_client.stop_consuming()

        queue_json_publish_rollback_unsafe("test_suite", {"event": "my_event"})

        queue_client.start_json_consumer("test_suite", collect)

        self.assert_length(output, 1)
        self.assertEqual(output[0]["event"], "my_event")

    @override_settings(USING_RABBITMQ=True)
    def test_register_consumer_nack(self) -> None:
        output = []
        count = 0

        queue_client = get_queue_client()

        def collect(events: list[dict[str, Any]]) -> None:
            assert isinstance(queue_client, SimpleQueueClient)
            assert len(events) == 1
            queue_client.stop_consuming()
            nonlocal count
            count += 1
            if count == 1:
                raise Exception("Make me nack!")
            output.append(events[0])

        queue_json_publish_rollback_unsafe("test_suite", {"event": "my_event"})

        try:
            queue_client.start_json_consumer("test_suite", collect)
        except Exception:
            queue_client.start_json_consumer("test_suite", collect)

        # Confirm that we processed the event fully once
        self.assertEqual(count, 2)
        self.assert_length(output, 1)
        self.assertEqual(output[0]["event"], "my_event")

    @override_settings(USING_RABBITMQ=True)
    def test_queue_error_json(self) -> None:
        queue_client = get_queue_client()
        assert isinstance(queue_client, SimpleQueueClient)
        actual_publish = queue_client.publish

        self.counter = 0

        def throw_connection_error_once(self_obj: Any, *args: Any, **kwargs: Any) -> None:
            self.counter += 1
            if self.counter <= 1:
                raise AMQPConnectionError("test")
            actual_publish(*args, **kwargs)

        with (
            mock.patch("zerver.lib.queue.SimpleQueueClient.publish", throw_connection_error_once),
            self.assertLogs("zulip.queue", level="WARN") as warn_logs,
        ):
            queue_json_publish_rollback_unsafe("test_suite", {"event": "my_event"})
        self.assertEqual(
            warn_logs.output,
            ["WARNING:zulip.queue:Failed to send to rabbitmq, trying to reconnect and send again"],
        )

        assert queue_client.channel
        method, _header, message = queue_client.channel.basic_get("test_suite")
        assert method is not None
        assert method.delivery_tag is not None
        assert message is not None
        queue_client.channel.basic_ack(method.delivery_tag)
        result = orjson.loads(message)
        self.assertEqual(result["event"], "my_event")

        method, _header, message = queue_client.channel.basic_get("test_suite")
        assert message is None

    @override_settings(USING_RABBITMQ=True)
    @override
    def setUp(self) -> None:
        queue_client = get_queue_client()
        assert queue_client.channel
        queue_client.channel.queue_declare("test_suite", durable=True)
        queue_client.channel.queue_purge("test_suite")
        super().setUp()

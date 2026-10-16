import json
import os
import tempfile
import time
from typing import Any
from unittest import TestCase
from unittest.mock import patch

with patch("scripts.lib.zulip_tools.get_config", return_value="1"):
    from scripts.lib.check_rabbitmq_queue import (
        ARMADA_WORKER_STATS_FILES,
        aggregate_parallel_queue_stats,
        armada_worker_health_results,
        check_rabbitmq_queues,
        normal_queues,
        queue_statistics_filenames,
    )


def run_queue_check(
    queues: dict[str, tuple[int, int]], slot_stats: list[dict[str, Any]]
) -> tuple[str, str]:
    """Run check_rabbitmq_queues against a stubbed rabbitmqctl, which reports
    {name: (messages, consumers)}, and these slots' statistics files;
    return what it writes for nagios."""
    with tempfile.TemporaryDirectory() as stats_dir:
        for slot, stats in enumerate(slot_stats):
            with open(os.path.join(stats_dir, f"armada_worker-{slot}.stats"), "w") as f:
                json.dump(stats, f)

        def check_output(command: list[str], **kwargs: object) -> str:
            if command[0] == "/usr/sbin/rabbitmqctl":
                return "".join(
                    f"{name}\t{messages}\t{consumers}\n"
                    for name, (messages, consumers) in queues.items()
                )
            assert command[1] == "QUEUE_STATS_DIR"
            return stats_dir + "\n"

        written: list[tuple[str, str]] = []

        def atomic_nagios_write(name: str, status: str, message: str) -> int:
            written.append((status, message))
            return 0

        with (
            patch.dict(os.environ, {"USER": "root"}),
            patch("scripts.lib.check_rabbitmq_queue.subprocess.check_output", check_output),
            patch("scripts.lib.check_rabbitmq_queue.atomic_nagios_write", atomic_nagios_write),
            patch("scripts.lib.check_rabbitmq_queue.sys.exit"),
        ):
            check_rabbitmq_queues()
        [result] = written
        return result


def fresh_slot_stats() -> dict[str, Any]:
    return {
        "update_time": int(time.time()),
        "recent_average_consume_time": None,
        "queue_last_emptied_timestamp": int(time.time()),
        "consumed_since_last_emptied": 0,
    }


class ParallelQueueStatsTest(TestCase):
    def test_armada_pool_exposes_both_queues_and_both_slot_files(self) -> None:
        self.assertIn("armada_worker", normal_queues)
        self.assertIn("armada_worker_1", normal_queues)
        self.assertEqual(queue_statistics_filenames("armada_worker"), ARMADA_WORKER_STATS_FILES)
        self.assertEqual(queue_statistics_filenames("armada_worker_1"), ARMADA_WORKER_STATS_FILES)
        self.assertEqual(queue_statistics_filenames("email_senders"), ("email_senders.stats",))

    def test_armada_pool_stats_require_both_slots_and_combine_capacity(self) -> None:
        first = {
            "update_time": 100,
            "recent_average_consume_time": 4.0,
            "queue_last_emptied_timestamp": 80,
            "consumed_since_last_emptied": 3,
        }
        second = {
            "update_time": 110,
            "recent_average_consume_time": 2.0,
            "queue_last_emptied_timestamp": 90,
            "consumed_since_last_emptied": 5,
        }

        self.assertEqual(aggregate_parallel_queue_stats([first, {}]), {})
        self.assertEqual(
            aggregate_parallel_queue_stats([first, second]),
            {
                "update_time": 110,
                "recent_average_consume_time": 1.5,
                "queue_last_emptied_timestamp": 80,
                "consumed_since_last_emptied": 8,
            },
        )

    def test_pool_health_does_not_hide_dead_slot_or_consumer(self) -> None:
        stats = [{"update_time": 100}, {"update_time": 400}]
        results = armada_worker_health_results(
            stats,
            {"armada_worker": 1, "armada_worker_1": 0},
            {"armada_worker": 2, "armada_worker_1": 1},
            now=500,
        )
        self.assertEqual(
            [(result["name"], result["message"]) for result in results],
            [
                ("armada_worker_1", "expected 2 consumers, found 1"),
                ("armada_worker-0.stats", "worker slot appears stale, last update 100"),
            ],
        )

    def test_idle_pool_with_every_consumer_is_healthy(self) -> None:
        # rabbitmqctl's message count is not its consumer count: an
        # empty queue with both slots subscribed is the healthy state.
        stats = [{"update_time": 100}, {"update_time": 100}]
        results = armada_worker_health_results(
            stats,
            {"armada_worker": 0, "armada_worker_1": 0},
            {"armada_worker": 2, "armada_worker_1": 2},
            now=500,
        )
        self.assertEqual(results, [])

    def test_check_passes_an_idle_pool_and_names_a_missing_consumer(self) -> None:
        idle = {"armada_worker": (0, 2), "armada_worker_1": (0, 2)}
        stats = [fresh_slot_stats(), fresh_slot_stats()]
        self.assertEqual(run_queue_check(idle, stats), ("ok", "queues normal"))

        one_consumer_gone = {"armada_worker": (0, 2), "armada_worker_1": (0, 1)}
        self.assertEqual(
            run_queue_check(one_consumer_gone, stats),
            (
                "critical",
                "queue armada_worker_1 problem: CRITICAL:expected 2 consumers, found 1",
            ),
        )

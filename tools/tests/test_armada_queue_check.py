import configparser
import json
import os
import subprocess
import sys
import tempfile
import time
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from types import ModuleType
from typing import Any
from unittest import TestCase, mock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_queue_check(zulip_conf: str = "") -> ModuleType:
    """A fresh copy of scripts/lib/check_rabbitmq_queue.py, as it loads on a
    server whose /etc/zulip/zulip.conf is zulip_conf."""
    config_file = configparser.RawConfigParser()
    config_file.read_string(zulip_conf)
    loader = SourceFileLoader(
        "armada_queue_check_under_test",
        os.path.join(ROOT_DIR, "scripts/lib/check_rabbitmq_queue.py"),
    )
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    with mock.patch("scripts.lib.zulip_tools.get_config_file", return_value=config_file):
        loader.exec_module(module)
    return module


def worker_slots_conf(slots: int) -> str:
    return f"[armada]\nworker_slots = {slots}\n"


def run_queue_check(
    queue_check: ModuleType,
    queues: dict[str, tuple[int, int]],
    slot_stats: dict[int, dict[str, Any]],
    queue_stats: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Run check_rabbitmq_queues against a stubbed rabbitmqctl, which reports
    {name: (messages, consumers)}, with these slots' statistics files and
    other queues' ones; return what it writes for nagios."""
    with tempfile.TemporaryDirectory() as stats_dir:
        stats_files = {
            **{f"armada_worker-{slot}.stats": stats for slot, stats in slot_stats.items()},
            **{f"{queue}.stats": stats for queue, stats in (queue_stats or {}).items()},
        }
        for file_name, stats in stats_files.items():
            with open(os.path.join(stats_dir, file_name), "w") as f:
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
            mock.patch.dict(os.environ, {"USER": "root"}),
            mock.patch.object(subprocess, "check_output", check_output),
            mock.patch.object(queue_check, "atomic_nagios_write", atomic_nagios_write),
            mock.patch.object(sys, "exit"),
        ):
            queue_check.check_rabbitmq_queues()
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
    def test_pool_queues_and_files_follow_worker_slots(self) -> None:
        for zulip_conf, queues in (
            ("", ["armada_worker", "armada_worker_1"]),
            (worker_slots_conf(1), ["armada_worker"]),
            (worker_slots_conf(2), ["armada_worker", "armada_worker_1"]),
            (worker_slots_conf(8), ["armada_worker", *(f"armada_worker_{n}" for n in range(1, 8))]),
        ):
            with self.subTest(zulip_conf=zulip_conf):
                queue_check = load_queue_check(zulip_conf)
                files = tuple(f"armada_worker-{slot}.stats" for slot in range(len(queues)))
                self.assertEqual(queue_check.ARMADA_WORKER_QUEUE_NAMES, tuple(queues))
                self.assertEqual(queue_check.ARMADA_WORKER_STATS_FILES, files)
                self.assertEqual(queue_check.normal_queues[: len(queues)], queues)
                self.assertNotIn(f"armada_worker_{len(queues)}", queue_check.normal_queues)
                for queue in queues:
                    self.assertEqual(queue_check.queue_statistics_filenames(queue), files)
                self.assertEqual(
                    queue_check.queue_statistics_filenames(f"armada_worker_{len(queues)}"),
                    (f"armada_worker_{len(queues)}.stats",),
                )
                self.assertEqual(
                    queue_check.queue_statistics_filenames("email_senders"),
                    ("email_senders.stats",),
                )

    def test_a_bad_worker_slots_stops_the_check(self) -> None:
        for value in ("0", "-1", "two", "2.5", ""):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, r"\[armada\] worker_slots"),
            ):
                load_queue_check(f"[armada]\nworker_slots = {value}\n")

    def test_armada_pool_stats_require_every_slot_and_combine_capacity(self) -> None:
        queue_check = load_queue_check()
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

        self.assertEqual(queue_check.aggregate_parallel_queue_stats([first, {}]), {})
        self.assertEqual(queue_check.aggregate_parallel_queue_stats([first]), {})
        self.assertEqual(
            queue_check.aggregate_parallel_queue_stats([first, second]),
            {
                "update_time": 110,
                "recent_average_consume_time": 1.5,
                "queue_last_emptied_timestamp": 80,
                "consumed_since_last_emptied": 8,
            },
        )

    def test_pool_health_does_not_hide_dead_slot_or_consumer(self) -> None:
        queue_check = load_queue_check()
        stats = [{"update_time": 100}, {"update_time": 400}]
        results = queue_check.armada_worker_health_results(
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
        # empty queue with every slot subscribed is the healthy state.
        queue_check = load_queue_check()
        stats = [{"update_time": 100}, {"update_time": 100}]
        results = queue_check.armada_worker_health_results(
            stats,
            {"armada_worker": 0, "armada_worker_1": 0},
            {"armada_worker": 2, "armada_worker_1": 2},
            now=500,
        )
        self.assertEqual(results, [])

    def test_check_passes_an_idle_pool_and_names_a_missing_consumer(self) -> None:
        queue_check = load_queue_check()
        idle = {"armada_worker": (0, 2), "armada_worker_1": (0, 2)}
        stats = {0: fresh_slot_stats(), 1: fresh_slot_stats()}
        self.assertEqual(run_queue_check(queue_check, idle, stats), ("ok", "queues normal"))
        # Other queues keep their own single statistics file.
        self.assertEqual(
            run_queue_check(
                queue_check,
                {**idle, "embedded_bots": (0, 1)},
                stats,
                {"embedded_bots": fresh_slot_stats()},
            ),
            ("ok", "queues normal"),
        )
        self.assertEqual(
            run_queue_check(queue_check, {**idle, "embedded_bots": (0, 1)}, stats),
            ("warning", "queue embedded_bots problem: UNKNOWN:invalid or no stats data"),
        )

        one_consumer_gone = {"armada_worker": (0, 2), "armada_worker_1": (0, 1)}
        self.assertEqual(
            run_queue_check(queue_check, one_consumer_gone, stats),
            (
                "critical",
                "queue armada_worker_1 problem: CRITICAL:expected 2 consumers, found 1",
            ),
        )

    def test_check_calls_a_slot_without_statistics_critical(self) -> None:
        queue_check = load_queue_check()
        idle = {"armada_worker": (0, 2), "armada_worker_1": (0, 2)}
        self.assertEqual(
            run_queue_check(queue_check, idle, {0: fresh_slot_stats()}),
            (
                "critical",
                "queue armada_worker-1.stats problem: CRITICAL:missing or invalid stats data",
            ),
        )

    def test_check_with_eight_slots(self) -> None:
        queue_check = load_queue_check(worker_slots_conf(8))
        names = ["armada_worker", *(f"armada_worker_{slot}" for slot in range(1, 8))]
        idle = dict.fromkeys(names, (0, 8))
        stats = {slot: fresh_slot_stats() for slot in range(8)}
        self.assertEqual(run_queue_check(queue_check, idle, stats), ("ok", "queues normal"))

        # Slot 5 is down: it consumes nothing and writes no statistics.
        slot_5_down = dict.fromkeys(names, (0, 7))
        del stats[5]
        status, message = run_queue_check(queue_check, slot_5_down, stats)
        self.assertEqual(status, "critical")
        self.assertEqual(
            message.split("; "),
            [
                *(
                    f"queue {name} problem: CRITICAL:expected 8 consumers, found 7"
                    for name in names
                ),
                "queue armada_worker-5.stats problem: CRITICAL:missing or invalid stats data",
            ],
        )

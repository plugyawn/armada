from unittest import TestCase
from unittest.mock import patch

with patch("scripts.lib.zulip_tools.get_config", return_value="1"):
    from scripts.lib.check_rabbitmq_queue import (
        PEAR_WORKER_STATS_FILES,
        aggregate_parallel_queue_stats,
        normal_queues,
        pear_worker_health_results,
        queue_statistics_filenames,
    )


class ParallelQueueStatsTest(TestCase):
    def test_pear_pool_exposes_both_queues_and_both_slot_files(self) -> None:
        self.assertIn("pear_worker", normal_queues)
        self.assertIn("pear_worker_1", normal_queues)
        self.assertEqual(queue_statistics_filenames("pear_worker"), PEAR_WORKER_STATS_FILES)
        self.assertEqual(queue_statistics_filenames("pear_worker_1"), PEAR_WORKER_STATS_FILES)
        self.assertEqual(queue_statistics_filenames("email_senders"), ("email_senders.stats",))

    def test_pear_pool_stats_require_both_slots_and_combine_capacity(self) -> None:
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
        results = pear_worker_health_results(stats, {"pear_worker": 2, "pear_worker_1": 1}, now=500)
        self.assertTrue(any(result["name"] == "pear_worker_1" for result in results))
        self.assertTrue(any(result["name"] == "pear_worker-0.stats" for result in results))

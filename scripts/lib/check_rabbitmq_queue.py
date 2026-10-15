import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from contextlib import suppress
from typing import Any

ZULIP_PATH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.append(ZULIP_PATH)
from scripts.lib.zulip_tools import atomic_nagios_write, get_config, get_config_file

normal_queues = [
    "armada_worker",
    "armada_worker_1",
    "deferred_work",
    "deferred_email_senders",
    "digest_emails",
    "email_mirror",
    "email_senders",
    "embed_links",
    "embedded_bots",
    "missedmessage_emails",
    "missedmessage_mobile_notifications",
    "outgoing_webhooks",
    "thumbnail",
    "user_activity",
    "user_activity_interval",
]

ARMADA_WORKER_QUEUE_NAMES = ("armada_worker", "armada_worker_1")
ARMADA_WORKER_STATS_FILES = ("armada_worker-0.stats", "armada_worker-1.stats")

mobile_notification_shards = int(
    get_config(get_config_file(), "application_server", "mobile_notification_shards", "1")
)
user_activity_shards = int(
    get_config(get_config_file(), "application_server", "user_activity_shards", "1")
)

OK = 0
WARNING = 1
CRITICAL = 2
UNKNOWN = 3

states = {
    0: "OK",
    1: "WARNING",
    2: "CRITICAL",
    3: "UNKNOWN",
}

MAX_SECONDS_TO_CLEAR: defaultdict[str, int] = defaultdict(
    lambda: 30,
    deferred_work=600,
    digest_emails=1200,
    missedmessage_mobile_notifications=120,
    embed_links=60,
    email_senders=90,
    deferred_email_senders=3600,
)
CRITICAL_SECONDS_TO_CLEAR: defaultdict[str, int] = defaultdict(
    lambda: 60,
    deferred_work=900,
    missedmessage_mobile_notifications=180,
    digest_emails=1800,
    embed_links=90,
    email_senders=300,
    deferred_email_senders=4500,
)


def aggregate_parallel_queue_stats(slot_stats: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine process-level stats into one conservative pool capacity view."""
    if len(slot_stats) != len(ARMADA_WORKER_STATS_FILES) or any(not stats for stats in slot_stats):
        return {}
    consume_times = [
        stats["recent_average_consume_time"]
        for stats in slot_stats
        if stats.get("recent_average_consume_time") is not None
    ]
    average_consume_time = (
        sum(consume_times) / len(consume_times) / len(slot_stats) if consume_times else None
    )
    return {
        "update_time": max(stats["update_time"] for stats in slot_stats),
        "recent_average_consume_time": average_consume_time,
        "queue_last_emptied_timestamp": min(
            stats["queue_last_emptied_timestamp"] for stats in slot_stats
        ),
        "consumed_since_last_emptied": sum(
            stats["consumed_since_last_emptied"] for stats in slot_stats
        ),
    }


def queue_statistics_filenames(queue_name: str) -> tuple[str, ...]:
    if queue_name in ARMADA_WORKER_QUEUE_NAMES:
        return ARMADA_WORKER_STATS_FILES
    return (f"{queue_name}.stats",)


def analyze_queue_stats(
    queue_name: str, stats: dict[str, Any], queue_count_rabbitmqctl: int
) -> dict[str, Any]:
    now = int(time.time())
    if stats == {}:
        return dict(status=UNKNOWN, name=queue_name, message="invalid or no stats data")

    if now - stats["update_time"] > 180 and queue_count_rabbitmqctl > 10:
        # Queue isn't updating the stats file and has some events in
        # the backlog, it's likely stuck.
        #
        # TODO: There's an unlikely race condition here - if the queue
        # was fully emptied and was idle due to no new events coming
        # for over 180 seconds, suddenly gets a burst of events and
        # this code runs exactly in the very small time window between
        # those events popping up and the queue beginning to process
        # the first one (which will refresh the stats file at the very
        # start), we'll incorrectly return the CRITICAL status. The
        # chance of that happening should be negligible because the queue
        # worker should wake up immediately and log statistics before
        # starting to process the first event.
        return dict(
            status=CRITICAL,
            name=queue_name,
            message="queue appears to be stuck, last update {}, queue size {}".format(
                stats["update_time"], queue_count_rabbitmqctl
            ),
        )

    current_size = queue_count_rabbitmqctl
    average_consume_time = stats["recent_average_consume_time"]
    if average_consume_time is None:
        # Queue just started; we can't effectively estimate anything.
        #
        # If the queue is stuck in this state and not processing
        # anything, eventually the `update_time` rule above will fire.
        return dict(status=OK, name=queue_name, message="")

    expected_time_to_clear_backlog = current_size * average_consume_time
    if expected_time_to_clear_backlog > MAX_SECONDS_TO_CLEAR[queue_name]:
        if expected_time_to_clear_backlog > CRITICAL_SECONDS_TO_CLEAR[queue_name]:
            status = CRITICAL
        else:
            status = WARNING

        return dict(
            status=status,
            name=queue_name,
            message=f"clearing the backlog will take too long: {expected_time_to_clear_backlog}s, size: {current_size}",
        )

    return dict(status=OK, name=queue_name, message="")


def armada_worker_health_results(
    slot_stats: list[dict[str, Any]], queue_counts: dict[str, int], now: int | None = None
) -> list[dict[str, Any]]:
    """Check pool topology and each slot independently."""
    now = int(time.time()) if now is None else now
    results: list[dict[str, Any]] = []
    for queue in ARMADA_WORKER_QUEUE_NAMES:
        consumers = queue_counts.get(queue, 0)
        if consumers != len(ARMADA_WORKER_STATS_FILES):
            results.append(
                dict(
                    status=CRITICAL,
                    name=queue,
                    message=f"expected {len(ARMADA_WORKER_STATS_FILES)} consumers, found {consumers}",
                )
            )
    for index, stats in enumerate(slot_stats):
        if not stats:
            results.append(
                dict(
                    status=CRITICAL,
                    name=ARMADA_WORKER_STATS_FILES[index],
                    message="missing or invalid stats data",
                )
            )
        elif now - stats["update_time"] > 180 and any(
            queue_counts.get(queue, 0) > 0 for queue in ARMADA_WORKER_QUEUE_NAMES
        ):
            results.append(
                dict(
                    status=CRITICAL,
                    name=ARMADA_WORKER_STATS_FILES[index],
                    message=f"worker slot appears stale, last update {stats['update_time']}",
                )
            )
    return results


WARN_COUNT_THRESHOLD_DEFAULT = 10
CRITICAL_COUNT_THRESHOLD_DEFAULT = 50


def check_other_queues(queue_counts_dict: dict[str, int]) -> list[dict[str, Any]]:
    """Do a simple queue size check for queues whose workers don't publish stats files."""

    results = []
    for queue, count in queue_counts_dict.items():
        if queue in normal_queues:
            continue

        if count > CRITICAL_COUNT_THRESHOLD_DEFAULT:
            results.append(dict(status=CRITICAL, name=queue, message=f"count critical: {count}"))
        elif count > WARN_COUNT_THRESHOLD_DEFAULT:
            results.append(dict(status=WARNING, name=queue, message=f"count warning: {count}"))
        else:
            results.append(dict(status=OK, name=queue, message=""))

    return results


def check_rabbitmq_queues() -> None:
    pattern = re.compile(r"(\w+)\t(\d+)\t(\d+)")
    if "USER" in os.environ and os.environ["USER"] not in ["root", "rabbitmq"]:
        print("This script must be run as the root or rabbitmq user")

    list_queues_output = subprocess.check_output(
        ["/usr/sbin/rabbitmqctl", "list_queues", "name", "messages", "consumers"],
        text=True,
    )
    queue_counts_rabbitmqctl = {}
    queues_with_consumers = []
    for line in list_queues_output.split("\n"):
        line = line.strip()
        m = pattern.match(line)
        if m:
            queue = m.group(1)
            count = int(m.group(2))
            consumers = int(m.group(3))
            queue_counts_rabbitmqctl[queue] = count
            if consumers > 0 and not queue.startswith("notify_tornado"):
                queues_with_consumers.append(queue)

    queue_stats_dir = subprocess.check_output(
        [os.path.join(ZULIP_PATH, "scripts/get-django-setting"), "QUEUE_STATS_DIR"],
        text=True,
    ).strip()
    queue_stats: dict[str, dict[str, Any]] = {}

    check_queues = list(normal_queues)
    if mobile_notification_shards > 1:
        # For sharded queue workers, where there's a separate queue
        # for each shard, we need to make sure none of those are
        # backlogged.
        check_queues += [
            f"missedmessage_mobile_notifications_shard{d}"
            for d in range(1, mobile_notification_shards + 1)
        ]
    if user_activity_shards > 1:
        check_queues += [f"user_activity_shard{d}" for d in range(1, user_activity_shards + 1)]

    queues_to_check = set(check_queues).intersection(set(queues_with_consumers))
    armada_slot_stats: list[dict[str, Any]] = []
    for filename in ARMADA_WORKER_STATS_FILES:
        file_path = os.path.join(queue_stats_dir, filename)
        stats: dict[str, Any] = {}
        if os.path.exists(file_path):
            with open(file_path) as f, suppress(json.decoder.JSONDecodeError):
                stats = json.load(f)
        armada_slot_stats.append(stats)
    results = armada_worker_health_results(armada_slot_stats, queue_counts_rabbitmqctl)
    for queue in queues_to_check:
        stats_files = queue_statistics_filenames(queue)
        loaded_stats: list[dict[str, Any]] = []
        for filename in stats_files:
            file_path = os.path.join(queue_stats_dir, filename)
            if not os.path.exists(file_path):
                loaded_stats.append({})
                continue
            with open(file_path) as f:
                try:
                    loaded_stats.append(json.load(f))
                except json.decoder.JSONDecodeError:
                    loaded_stats.append({})
        queue_stats[queue] = (
            aggregate_parallel_queue_stats(loaded_stats)
            if queue in ARMADA_WORKER_QUEUE_NAMES
            else loaded_stats[0]
        )

    for queue_name, stats in queue_stats.items():
        results.append(analyze_queue_stats(queue_name, stats, queue_counts_rabbitmqctl[queue_name]))

    results.extend(check_other_queues(queue_counts_rabbitmqctl))

    status = max(result["status"] for result in results)

    if status > 0:
        queue_error_template = "queue {} problem: {}:{}"
        error_message = "; ".join(
            queue_error_template.format(result["name"], states[result["status"]], result["message"])
            for result in results
            if result["status"] > 0
        )
        sys.exit(
            atomic_nagios_write(
                "check-rabbitmq-results",
                "critical" if status == CRITICAL else "warning",
                error_message,
            )
        )
    else:
        atomic_nagios_write("check-rabbitmq-results", "ok", "queues normal")

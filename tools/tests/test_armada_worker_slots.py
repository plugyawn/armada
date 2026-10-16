import configparser
import json
import os
import re
import shutil
import subprocess
from unittest import TestCase, skipUnless

from scripts.lib.armada_worker_slots import (
    armada_worker_slot_queue_name,
    armada_worker_slot_queue_names,
    armada_worker_slot_statistics_file,
    get_armada_worker_slots,
)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RENDER_ERB = """
require "erb"
require "json"
JSON.parse(STDIN.read).each { |name, value| instance_variable_set("@#{name}", value) }
print ERB.new(File.read(ARGV[0]), trim_mode: "-").result(binding)
"""


def zulip_conf(text: str) -> configparser.RawConfigParser:
    config_file = configparser.RawConfigParser()
    config_file.read_string(text)
    return config_file


def render_supervisor_conf(*, armada_worker_slots: int, multiprocess: bool) -> str:
    """puppet/zulip/templates/supervisor/zulip.conf.template.erb, rendered
    with the queues app_frontend_base.pp lists and the given slots."""
    with open(os.path.join(ROOT_DIR, "puppet/zulip/manifests/app_frontend_base.pp")) as f:
        queue_list = re.search(r"\$queues = \[(.*?)\]", f.read(), re.DOTALL)
    assert queue_list is not None
    values = {
        "armada_worker_slots": armada_worker_slots,
        "ca_bundle": "",
        "codex_env": "",
        "katex_server": False,
        "katex_server_port": "9700",
        "mobile_notification_shards": 1,
        "proxy": "",
        "queues": re.findall(r"'([a-z_]+)'", queue_list.group(1)),
        "queues_multiprocess": multiprocess,
        "tornado_ports": [9800],
        "tusd_server_listen": "127.0.0.1",
        "user_activity_shards": 1,
        "worker_counts": {},
    }
    return subprocess.run(
        [
            "ruby",
            "-e",
            RENDER_ERB,
            os.path.join(ROOT_DIR, "puppet/zulip/templates/supervisor/zulip.conf.template.erb"),
        ],
        input=json.dumps(values),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def supervisor_processes(conf_text: str, program: str) -> dict[str, str]:
    """{process name: command} for one supervisor program, as supervisord
    expands numprocs."""
    conf = configparser.RawConfigParser(inline_comment_prefixes=(";",), strict=False)
    conf.read_string(conf_text)
    section = conf[f"program:{program}"]
    start = int(section.get("numprocs_start", "0"))
    numbers = range(start, start + int(section.get("numprocs", "1")))
    name = section.get("process_name", program)
    return {
        name % {"process_num": number}: section["command"] % {"process_num": number}
        for number in numbers
    }


class WorkerSlotsSettingTest(TestCase):
    def test_slots_come_from_the_armada_section(self) -> None:
        self.assertEqual(get_armada_worker_slots(zulip_conf("")), 2)
        self.assertEqual(get_armada_worker_slots(zulip_conf("[armada]\ncodex_bin = codex\n")), 2)
        for slots in (1, 2, 8, 16):
            with self.subTest(slots=slots):
                conf = zulip_conf(f"[armada]\nworker_slots = {slots}\n")
                self.assertEqual(get_armada_worker_slots(conf), slots)
        # The same key in another section is not the setting.
        self.assertEqual(
            get_armada_worker_slots(zulip_conf("[application_server]\nworker_slots = 8\n")), 2
        )

    def test_anything_but_a_positive_whole_number_is_refused(self) -> None:
        for value in ("0", "-1", "08", "two", "2.5", "1e1", ""):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    ValueError,
                    rf"^\[armada\] worker_slots in zulip.conf must be a whole number of at least 1, not '{value}'$",
                ),
            ):
                get_armada_worker_slots(zulip_conf(f"[armada]\nworker_slots = {value}\n"))

    def test_each_slot_has_a_queue_and_a_statistics_file(self) -> None:
        self.assertEqual(armada_worker_slot_queue_name(0), "armada_worker")
        self.assertEqual(armada_worker_slot_queue_name(7), "armada_worker_7")
        self.assertEqual(armada_worker_slot_queue_names(1), ("armada_worker",))
        self.assertEqual(armada_worker_slot_queue_names(2), ("armada_worker", "armada_worker_1"))
        self.assertEqual(
            armada_worker_slot_queue_names(8),
            ("armada_worker", *(f"armada_worker_{slot}" for slot in range(1, 8))),
        )
        self.assertEqual(armada_worker_slot_statistics_file(0), "armada_worker-0.stats")
        self.assertEqual(armada_worker_slot_statistics_file(7), "armada_worker-7.stats")


@skipUnless(shutil.which("ruby"), "ruby renders puppet's templates")
class SupervisorTemplateTest(TestCase):
    def test_one_process_per_slot(self) -> None:
        for slots in (1, 2, 8):
            with self.subTest(slots=slots):
                conf_text = render_supervisor_conf(armada_worker_slots=slots, multiprocess=True)
                processes = supervisor_processes(conf_text, "zulip_events_armada_worker")
                self.assertEqual(
                    processes,
                    {
                        f"zulip_events_armada_worker_slot{slot}": (
                            "nice -n10 /home/zulip/deployments/current/manage.py process_queue"
                            f" --queue_name=armada_worker --skip-checks --worker_num {slot}"
                        )
                        for slot in range(slots)
                    },
                )
                # The processes are the slots Django routes to, for the same zulip.conf.
                django_slots = get_armada_worker_slots(
                    zulip_conf(f"[armada]\nworker_slots = {slots}\n")
                )
                self.assertEqual(
                    [int(command.rsplit(" ", 1)[1]) for command in processes.values()],
                    list(range(django_slots)),
                )
                self.assertIn(
                    "stdout_logfile=/var/log/zulip/events_armada_worker_slot%(process_num)s.log",
                    conf_text,
                )
                # Only the agent's queue runs per slot.
                self.assertEqual(
                    supervisor_processes(conf_text, "zulip_events_armada_monitor_worker"),
                    {
                        "zulip_events_armada_monitor_worker": (
                            "nice -n10 /home/zulip/deployments/current/manage.py process_queue"
                            " --queue_name=armada_monitor_worker --skip-checks"
                        )
                    },
                )

    def test_one_threaded_process_runs_every_queue_on_a_small_machine(self) -> None:
        conf_text = render_supervisor_conf(armada_worker_slots=8, multiprocess=False)
        self.assertNotIn("[program:zulip_events_armada_worker]", conf_text)
        [command] = supervisor_processes(conf_text, "zulip_events").values()
        self.assertIn(" armada_worker ", command.split("--multi_threaded", 1)[1])

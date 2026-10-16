import configparser
from unittest import TestCase

from scripts.lib.armada_worker_slots import (
    armada_worker_slot_queue_name,
    armada_worker_slot_queue_names,
    armada_worker_slot_statistics_file,
    get_armada_worker_slots,
)


def zulip_conf(text: str) -> configparser.RawConfigParser:
    config_file = configparser.RawConfigParser()
    config_file.read_string(text)
    return config_file


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

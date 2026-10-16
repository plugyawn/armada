"""The agent's worker slots: how many there are, and each one's queue.

A slot is one armada_worker process, which runs one agent run at a time,
so the number of slots is how many runs go at once.  It is set in one
place, `[armada] worker_slots` in /etc/zulip/zulip.conf (default 2), and
read in three: puppet starts a process per slot
(puppet/zulip/manifests/app_frontend_base.pp), Django routes each run to
a slot's queue (ARMADA_WORKER_SLOTS, zproject/computed_settings.py), and
the queue check expects every slot's consumers and statistics
(scripts/lib/check_rabbitmq_queue.py).  This module is the Python side's
one reading of the key and its one naming of a slot's queue and file.
docs/armada/deploy.md says how to change it on a running server.
"""

import configparser
import re

from scripts.lib.zulip_tools import get_config

# Puppet's default, in app_frontend_base.pp, is the same number.
DEFAULT_ARMADA_WORKER_SLOTS = 2


def get_armada_worker_slots(config_file: configparser.RawConfigParser) -> int:
    value = get_config(config_file, "armada", "worker_slots", str(DEFAULT_ARMADA_WORKER_SLOTS))
    if re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise ValueError(
            f"[armada] worker_slots in zulip.conf must be a whole number of at least 1, not {value!r}"
        )
    return int(value)


def armada_worker_slot_queue_name(slot: int) -> str:
    # Slot 0's queue keeps the name of the agent's original, only queue.
    return "armada_worker" if slot == 0 else f"armada_worker_{slot}"


def armada_worker_slot_queue_names(slots: int) -> tuple[str, ...]:
    return tuple(armada_worker_slot_queue_name(slot) for slot in range(slots))


def armada_worker_slot_statistics_file(slot: int) -> str:
    return f"armada_worker-{slot}.stats"

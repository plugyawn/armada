# Documented in https://zulip.readthedocs.io/en/latest/subsystems/queuing.html
from typing import Any

from typing_extensions import override

from zerver.lib.bot_lib import EmbeddedBotHandler
from zerver.lib.pear import PearEmbeddedBotHandler
from zerver.lib.pear_worker import (
    PEAR_ADDRESSING_QUEUE_NAME,
    PearDatabaseWorkerRunStore,
    PearQueueWorkerDispatcher,
    build_pear_addressing_intent_classifier_from_settings,
    load_pear_active_session_from_zulip,
)
from zerver.models.users import get_user_profile_by_id
from zerver.worker.base import QueueProcessingWorker, assign_queue


@assign_queue(PEAR_ADDRESSING_QUEUE_NAME)
class PearAddressingWorker(QueueProcessingWorker):
    """Route Pear messages without blocking Zulip's shared bot queue."""

    PREFETCH = 10
    MAX_CONSUME_SECONDS = 10

    @override
    def setup(self) -> None:
        super().setup()
        self.pear_handler = PearEmbeddedBotHandler(
            active_session_loader=load_pear_active_session_from_zulip,
            worker_run_store=PearDatabaseWorkerRunStore(),
            worker_dispatcher=PearQueueWorkerDispatcher(),
            addressing_intent_classifier=build_pear_addressing_intent_classifier_from_settings(),
        )

    @override
    def consume(self, event: dict[str, Any]) -> None:
        user_profile = get_user_profile_by_id(event["user_profile_id"])
        bot_handler = EmbeddedBotHandler(user_profile)
        self.pear_handler.handle_message(event["message"], bot_handler)

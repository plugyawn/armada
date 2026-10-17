# Documented in https://zulip.readthedocs.io/en/latest/subsystems/queuing.html
from typing import Any

from typing_extensions import override

from zerver.lib.armada import ArmadaEmbeddedBotHandler
from zerver.lib.armada_worker import (
    ARMADA_ADDRESSING_QUEUE_NAME,
    ArmadaDatabaseWorkerRunStore,
    ArmadaQueueWorkerDispatcher,
    build_armada_addressing_intent_classifier_from_settings,
    load_armada_routing_session_from_zulip,
)
from zerver.lib.bot_lib import EmbeddedBotHandler
from zerver.models.users import get_user_profile_by_id
from zerver.worker.base import QueueProcessingWorker, assign_queue


@assign_queue(ARMADA_ADDRESSING_QUEUE_NAME)
class ArmadaAddressingWorker(QueueProcessingWorker):
    """Route messages for the agent without blocking Zulip's shared bot queue."""

    PREFETCH = 10
    MAX_CONSUME_SECONDS = 10

    @override
    def setup(self) -> None:
        super().setup()
        self.armada_handler = ArmadaEmbeddedBotHandler(
            active_session_loader=load_armada_routing_session_from_zulip,
            worker_run_store=ArmadaDatabaseWorkerRunStore(),
            worker_dispatcher=ArmadaQueueWorkerDispatcher(),
            addressing_intent_classifier=build_armada_addressing_intent_classifier_from_settings(),
        )

    @override
    def consume(self, event: dict[str, Any]) -> None:
        user_profile = get_user_profile_by_id(event["user_profile_id"])
        bot_handler = EmbeddedBotHandler(user_profile)
        self.armada_handler.handle_message(event["message"], bot_handler)

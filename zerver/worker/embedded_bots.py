# Documented in https://zulip.readthedocs.io/en/latest/subsystems/queuing.html
import logging
from collections.abc import Mapping
from typing import Any

from typing_extensions import override
from zulip_bots.lib import extract_query_without_mention

from zerver.lib.bot_lib import (
    EmbeddedBotHandler,
    EmbeddedBotQuitError,
    do_flag_service_bots_messages_as_processed,
    get_bot_handler,
)
from zerver.lib.queue import queue_json_publish_rollback_unsafe
from zerver.models import UserProfile
from zerver.models.bots import get_bot_services
from zerver.models.users import get_user_profile_by_id
from zerver.worker.base import QueueProcessingWorker, assign_queue

logger = logging.getLogger(__name__)


@assign_queue("embedded_bots")
class EmbeddedBotWorker(QueueProcessingWorker):
    def get_bot_api_client(self, user_profile: UserProfile) -> EmbeddedBotHandler:
        return EmbeddedBotHandler(user_profile)

    @override
    def consume(self, event: Mapping[str, Any]) -> None:
        user_profile_id = event["user_profile_id"]
        user_profile = get_user_profile_by_id(user_profile_id)

        message: dict[str, Any] = event["message"]

        # TODO: Do we actually want to allow multiple Services per bot user?
        services = get_bot_services(user_profile_id)
        for service in services:
            service_message = dict(message)
            try:
                if service.name == "armada":
                    from zerver.lib.armada import ARMADA_TRIGGER_MESSAGE_KEY
                    from zerver.lib.armada_worker import ARMADA_ADDRESSING_QUEUE_NAME

                    service_message[ARMADA_TRIGGER_MESSAGE_KEY] = event["trigger"]
                if event["trigger"] == "mention":
                    service_message["content"] = extract_query_without_mention(
                        message=service_message,
                        client=self.get_bot_api_client(user_profile),
                    )
                    assert service_message["content"] is not None
                    if service.name == "armada":
                        service_message["agent_explicit_mention"] = True
                if service.name == "armada":
                    queue_json_publish_rollback_unsafe(
                        ARMADA_ADDRESSING_QUEUE_NAME,
                        {
                            "user_profile_id": user_profile_id,
                            "message": service_message,
                        },
                    )
                    continue
                bot_handler = get_bot_handler(str(service.name))
                if bot_handler is None:
                    logging.error(
                        "Error: User %s has bot with invalid embedded bot service %s",
                        user_profile_id,
                        service.name,
                    )
                    continue
                if hasattr(bot_handler, "initialize"):
                    bot_handler.initialize(self.get_bot_api_client(user_profile))
                bot_handler.handle_message(
                    message=service_message,
                    bot_handler=self.get_bot_api_client(user_profile),
                )
            except EmbeddedBotQuitError as e:
                logging.warning("%s", e)
        do_flag_service_bots_messages_as_processed(user_profile, [message["id"]])

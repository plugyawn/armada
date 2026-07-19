import orjson

from zerver.actions.message_edit import maybe_delete_previous_resolve_topic_notification
from zerver.lib.bot_lib import EmbeddedBotHandler
from zerver.lib.event_types import MessageEvent
from zerver.lib.message_cache import MessageDict
from zerver.lib.message_threads import bulk_message_thread_summaries
from zerver.lib.retention import (
    move_messages_to_archive,
    restore_data_from_archive,
    restore_data_from_archive_by_transactions,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import mock_queue_publish
from zerver.lib.topic import get_latest_message_for_user_in_topic
from zerver.models import ArchivedMessage, ArchiveTransaction, Message, UserMessage, UserProfile
from zerver.models.streams import get_stream


class MessageThreadTest(ZulipTestCase):
    def _thread_message(self, users: list[UserProfile]) -> tuple[str, int]:
        stream_name = "native-message-threads"
        self.make_stream(stream_name)
        for user in users:
            self.subscribe(user, stream_name)
        return stream_name, self.send_stream_message(
            users[0],
            stream_name,
            content="Parent message",
            topic_name="thread topic",
        )

    def _send_reply(
        self,
        sender: UserProfile,
        stream_name: str,
        root_message_id: int,
        content: str,
        *,
        topic_name: str = "thread topic",
    ) -> int:
        response = self.assert_json_success(
            self.api_post(
                sender,
                "/api/v1/messages",
                {
                    "type": "channel",
                    "to": orjson.dumps(stream_name).decode(),
                    "topic": topic_name,
                    "content": content,
                    "thread_root_message_id": root_message_id,
                },
            )
        )
        return response["id"]

    def test_reply_is_an_ordinary_message_and_event(self) -> None:
        othello = self.example_user("othello")
        hamlet = self.example_user("hamlet")
        stream_name, root_message_id = self._thread_message([othello, hamlet])

        with self.capture_send_event_calls(expected_num_events=1) as events:
            reply_id = self._send_reply(
                hamlet,
                stream_name,
                root_message_id,
                "**hello** <script>alert(1)</script>",
            )

        reply = Message.objects.get(id=reply_id)
        self.assertEqual(reply.thread_root_id, root_message_id)
        self.assertEqual(reply.content, "**hello** <script>alert(1)</script>")
        self.assertIn("<strong>hello</strong>", reply.rendered_content)
        self.assertNotIn("<script>", reply.rendered_content)

        event = events[0]["event"]
        MessageEvent.model_validate(event)
        self.assertEqual(event["type"], "message")
        self.assertEqual(event["message"]["id"], reply_id)
        self.assertEqual(event["message"]["thread_root_message_id"], root_message_id)

        serialized_reply = MessageDict.wide_dict(reply)
        self.assertEqual(serialized_reply["thread_root_message_id"], root_message_id)
        serialized_root = MessageDict.wide_dict(Message.objects.get(id=root_message_id))
        self.assertNotIn("reply_summary", serialized_root)
        MessageDict.bulk_hydrate_message_thread_summaries(
            [serialized_root],
            user_profile=othello,
        )
        summary = serialized_root["reply_summary"]
        self.assertEqual(summary["reply_count"], 1)
        self.assertEqual(summary["unread_reply_count"], 1)
        self.assertEqual(summary["last_sender"]["user_id"], hamlet.id)

        move_messages_to_archive([reply_id], hamlet.realm, skip_notify=True)
        serialized_root = MessageDict.wide_dict(Message.objects.get(id=root_message_id))
        MessageDict.bulk_hydrate_message_thread_summaries(
            [serialized_root],
            user_profile=othello,
        )
        self.assertNotIn(
            "reply_summary",
            serialized_root,
        )

    def test_thread_narrow_and_default_history(self) -> None:
        hamlet = self.example_user("hamlet")
        othello = self.example_user("othello")
        stream_name, root_message_id = self._thread_message([hamlet, othello])
        reply_ids = [
            self._send_reply(hamlet, stream_name, root_message_id, f"reply {index}")
            for index in range(3)
        ]
        second_root_id = self.send_stream_message(
            hamlet,
            stream_name,
            content="Second parent",
            topic_name="thread topic",
        )

        self.login_user(hamlet)
        common_params = {
            "anchor": 10**16,
            "num_before": 100,
            "num_after": 0,
        }
        default_result = self.assert_json_success(
            self.client_get(
                "/json/messages",
                {
                    **common_params,
                    "narrow": orjson.dumps(
                        [
                            {"operator": "channel", "operand": stream_name},
                            {"operator": "topic", "operand": "thread topic"},
                        ]
                    ).decode(),
                },
            )
        )
        self.assertEqual(
            [message["id"] for message in default_result["messages"]],
            [root_message_id, *reply_ids, second_root_id],
        )

        root_only_result = self.assert_json_success(
            self.client_get(
                "/json/messages",
                {
                    **common_params,
                    "exclude_thread_replies": "true",
                    "narrow": orjson.dumps(
                        [
                            {"operator": "channel", "operand": stream_name},
                            {"operator": "topic", "operand": "thread topic"},
                        ]
                    ).decode(),
                },
            )
        )
        self.assertEqual(
            [message["id"] for message in root_only_result["messages"]],
            [root_message_id, second_root_id],
        )

        thread_result = self.assert_json_success(
            self.client_get(
                "/json/messages",
                {
                    **common_params,
                    "exclude_thread_replies": "true",
                    "narrow": orjson.dumps(
                        [{"operator": "thread", "operand": root_message_id}]
                    ).decode(),
                },
            )
        )
        self.assertEqual(
            [message["id"] for message in thread_result["messages"]],
            reply_ids,
        )
        self.assertTrue(
            all(
                message["thread_root_message_id"] == root_message_id
                for message in thread_result["messages"]
            )
        )

        negated_thread_result = self.assert_json_success(
            self.client_get(
                "/json/messages",
                {
                    **common_params,
                    "narrow": orjson.dumps(
                        [
                            {
                                "operator": "thread",
                                "operand": root_message_id,
                                "negated": True,
                            }
                        ]
                    ).decode(),
                },
            )
        )
        self.assertTrue(
            set(reply_ids).isdisjoint(
                message["id"] for message in negated_thread_result["messages"]
            )
        )

        # Replies retain ordinary UserMessage state.  A capable root-only feed
        # hides them, while an explicit unread narrow can still retrieve them.
        self.login_user(othello)
        unread_result = self.assert_json_success(
            self.client_get(
                "/json/messages",
                {
                    **common_params,
                    "exclude_thread_replies": "true",
                    "narrow": orjson.dumps([{"operator": "is", "operand": "unread"}]).decode(),
                },
            )
        )
        unread_ids = {message["id"] for message in unread_result["messages"]}
        self.assertTrue(set(reply_ids).issubset(unread_ids))

        # A realm member who was never subscribed still has access to the
        # complete reply history of a public-channel root.  The root access
        # check is the authority; a missing UserMessage row is not.
        cordelia = self.example_user("cordelia")
        self.login_user(cordelia)
        public_history_result = self.assert_json_success(
            self.client_get(
                "/json/messages",
                {
                    **common_params,
                    "narrow": orjson.dumps(
                        [{"operator": "thread", "operand": root_message_id}]
                    ).decode(),
                },
            )
        )
        self.assertEqual(
            [message["id"] for message in public_history_result["messages"]],
            reply_ids,
        )

    def test_topic_level_latest_message_ignores_newer_reply(self) -> None:
        hamlet = self.example_user("hamlet")
        stream_name, first_root_id = self._thread_message([hamlet])
        second_root_id = self.send_stream_message(
            hamlet,
            stream_name,
            content="Second parent",
            topic_name="thread topic",
        )
        self._send_reply(hamlet, stream_name, first_root_id, "Newer reply")

        stream = get_stream(stream_name, hamlet.realm)
        assert stream.recipient_id is not None
        self.assertEqual(
            get_latest_message_for_user_in_topic(
                hamlet.realm_id,
                hamlet,
                stream.recipient_id,
                "thread topic",
                history_public_to_subscribers=True,
                acting_user_has_channel_content_access=True,
            ),
            second_root_id,
        )

    def test_root_only_first_unread_anchor_maps_reply_to_root(self) -> None:
        hamlet = self.example_user("hamlet")
        othello = self.example_user("othello")
        stream_name, root_message_id = self._thread_message([hamlet, othello])

        self.login_user(othello)
        self.assert_json_success(
            self.client_post(
                "/json/messages/flags",
                {
                    "messages": orjson.dumps([root_message_id]).decode(),
                    "op": "add",
                    "flag": "read",
                },
            )
        )
        reply_id = self._send_reply(hamlet, stream_name, root_message_id, "new unread reply")

        root_feed = self.assert_json_success(
            self.client_get(
                "/json/messages",
                {
                    "anchor": "first_unread",
                    "num_before": 0,
                    "num_after": 10,
                    "exclude_thread_replies": "true",
                    "narrow": orjson.dumps(
                        [
                            {"operator": "channel", "operand": stream_name},
                            {"operator": "topic", "operand": "thread topic"},
                        ]
                    ).decode(),
                },
            )
        )
        self.assertEqual(root_feed["anchor"], root_message_id)
        self.assertEqual(
            [message["id"] for message in root_feed["messages"]],
            [root_message_id],
        )
        self.assertEqual(
            root_feed["messages"][0]["reply_summary"]["unread_reply_count"],
            1,
        )

        explicit_unread = self.assert_json_success(
            self.client_get(
                "/json/messages",
                {
                    "anchor": "first_unread",
                    "num_before": 0,
                    "num_after": 10,
                    "exclude_thread_replies": "true",
                    "narrow": orjson.dumps([{"operator": "is", "operand": "unread"}]).decode(),
                },
            )
        )
        self.assertIn(reply_id, [message["id"] for message in explicit_unread["messages"]])

    def test_protected_history_join_after_root_projects_reply_as_standalone(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        stream_name = "protected-thread-join"
        self.make_stream(
            stream_name,
            invite_only=True,
            history_public_to_subscribers=False,
        )
        self.subscribe(hamlet, stream_name)
        root_message_id = self.send_stream_message(
            hamlet,
            stream_name,
            content="root before join",
            topic_name="thread topic",
        )

        self.subscribe(cordelia, stream_name)
        with self.capture_send_event_calls(expected_num_events=1) as events:
            reply_id = self._send_reply(
                hamlet,
                stream_name,
                root_message_id,
                "reply after join",
            )

        # The durable message and shared event template stay canonical. The
        # internal marker tells the event queue which client needs a standalone
        # projection without weakening the relationship for other viewers.
        reply = Message.objects.get(id=reply_id)
        self.assertEqual(reply.thread_root_id, root_message_id)
        wide_reply = MessageDict.wide_dict(reply)
        self.assertEqual(wide_reply["thread_root_message_id"], root_message_id)
        event = events[0]["event"]
        self.assertEqual(event["message_dict"]["thread_root_message_id"], root_message_id)
        self.assertIn(cordelia.id, event["user_ids_without_thread_root_access"])
        self.assertNotIn(hamlet.id, event["user_ids_without_thread_root_access"])

        self.assertTrue(
            UserMessage.objects.filter(
                user_profile=cordelia,
                message_id=reply_id,
                flags__andz=UserMessage.flags.read.mask,
            ).exists()
        )
        self.login_user(cordelia)
        self.assert_json_error(
            self.client_get(f"/json/messages/{root_message_id}"),
            "Invalid message(s)",
        )
        standalone = self.assert_json_success(self.client_get(f"/json/messages/{reply_id}"))[
            "message"
        ]
        self.assertEqual(standalone["id"], reply_id)
        self.assertNotIn("thread_root_message_id", standalone)
        self.assertNotIn("reply_summary", standalone)

        root_feed = self.assert_json_success(
            self.client_get(
                "/json/messages",
                {
                    "anchor": "first_unread",
                    "num_before": 0,
                    "num_after": 10,
                    "exclude_thread_replies": "true",
                    "narrow": orjson.dumps(
                        [
                            {"operator": "channel", "operand": stream_name},
                            {"operator": "topic", "operand": "thread topic"},
                        ]
                    ).decode(),
                },
            )
        )
        self.assertEqual(root_feed["anchor"], reply_id)
        self.assertEqual([message["id"] for message in root_feed["messages"]], [reply_id])
        self.assertNotIn("thread_root_message_id", root_feed["messages"][0])

        self.login_user(hamlet)
        related = self.assert_json_success(self.client_get(f"/json/messages/{reply_id}"))["message"]
        self.assertEqual(related["thread_root_message_id"], root_message_id)

    def test_protected_history_service_bot_receives_standalone_reply(self) -> None:
        hamlet = self.example_user("hamlet")
        bot = self.create_test_bot(
            short_name="history",
            user_profile=hamlet,
            full_name="History Bot",
            bot_type=UserProfile.EMBEDDED_BOT,
        )
        stream_name = "protected-thread-bot-join"
        self.make_stream(
            stream_name,
            invite_only=True,
            history_public_to_subscribers=False,
        )
        self.subscribe(hamlet, stream_name)
        root_message_id = self.send_stream_message(
            hamlet,
            stream_name,
            content="root before bot join",
            topic_name="thread topic",
        )
        self.subscribe(bot, stream_name)

        with mock_queue_publish("zerver.actions.message_send.queue_event_on_commit") as queue:
            reply_id = self._send_reply(
                hamlet,
                stream_name,
                root_message_id,
                "@**History Bot** reply after join",
            )

        queue.assert_called_once()
        queue_name, service_event = queue.call_args.args[:2]
        self.assertEqual(queue_name, "embedded_bots")
        self.assertEqual(service_event["user_profile_id"], bot.id)
        self.assertEqual(service_event["message"]["id"], reply_id)
        self.assertNotIn("thread_root_message_id", service_event["message"])
        self.assertEqual(
            Message.objects.get(id=reply_id).thread_root_id,
            root_message_id,
        )

    def test_resolve_notification_undo_ignores_newer_reply(self) -> None:
        hamlet = self.example_user("hamlet")
        stream_name, _root_message_id = self._thread_message([hamlet])
        notification_id = self.send_stream_message(
            hamlet,
            stream_name,
            content="Topic was resolved",
            topic_name="thread topic",
        )
        Message.objects.filter(id=notification_id).update(
            type=Message.MessageType.RESOLVE_TOPIC_NOTIFICATION
        )
        reply_id = self._send_reply(
            hamlet,
            stream_name,
            notification_id,
            "Reply after the notification",
        )

        stream = get_stream(stream_name, hamlet.realm)
        self.assertTrue(
            maybe_delete_previous_resolve_topic_notification(
                hamlet,
                stream,
                "thread topic",
            )
        )
        self.assertFalse(Message.objects.filter(id__in=[notification_id, reply_id]).exists())

    def test_rejects_nested_mismatched_and_inaccessible_replies(self) -> None:
        sender = self.example_user("othello")
        outsider = self.example_user("hamlet")
        stream_name = "private-native-message-threads"
        self.make_stream(stream_name, invite_only=True)
        self.subscribe(sender, stream_name)
        root_message_id = self.send_stream_message(
            sender,
            stream_name,
            topic_name="thread topic",
        )
        reply_id = self._send_reply(sender, stream_name, root_message_id, "first reply")

        nested_result = self.api_post(
            sender,
            "/api/v1/messages",
            {
                "type": "channel",
                "to": orjson.dumps(stream_name).decode(),
                "topic": "thread topic",
                "content": "nested reply",
                "thread_root_message_id": reply_id,
            },
        )
        self.assert_json_error(nested_result, "Replies cannot be nested")

        mismatch_result = self.api_post(
            sender,
            "/api/v1/messages",
            {
                "type": "channel",
                "to": orjson.dumps(stream_name).decode(),
                "topic": "different topic",
                "content": "wrong destination",
                "thread_root_message_id": root_message_id,
            },
        )
        self.assert_json_error(
            mismatch_result,
            "Reply target does not match the message destination",
        )

        inaccessible_result = self.api_post(
            outsider,
            "/api/v1/messages",
            {
                "type": "channel",
                "to": orjson.dumps(stream_name).decode(),
                "topic": "thread topic",
                "content": "not allowed",
                "thread_root_message_id": root_message_id,
            },
        )
        self.assert_json_error(inaccessible_result, "Invalid message(s)")

    def test_summary_uses_recent_unique_participants(self) -> None:
        users = [
            self.example_user("othello"),
            self.example_user("hamlet"),
            self.example_user("cordelia"),
            self.example_user("iago"),
            self.example_user("desdemona"),
        ]
        stream_name, root_message_id = self._thread_message(users)
        for index, user in enumerate(users):
            self._send_reply(user, stream_name, root_message_id, f"reply {index}")

        with self.assert_database_query_count(2):
            summary = bulk_message_thread_summaries(
                [root_message_id],
                user_profile=users[0],
            )[root_message_id]
        self.assertEqual(summary["reply_count"], 5)
        self.assertEqual(summary["unread_reply_count"], 4)
        self.assertEqual(summary["last_sender"]["user_id"], users[-1].id)
        self.assertEqual(
            [participant["user_id"] for participant in summary["participants"]],
            [user.id for user in reversed(users[-4:])],
        )

    def test_fetch_thread_summary_is_fresh_and_viewer_specific(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        stream_name, root_message_id = self._thread_message([hamlet, cordelia])

        self.login_user(cordelia)
        initial = self.assert_json_success(self.client_get(f"/json/messages/{root_message_id}"))
        self.assertNotIn("reply_summary", initial["message"])

        reply_id = self._send_reply(hamlet, stream_name, root_message_id, "fresh reply")
        after_send = self.assert_json_success(self.client_get(f"/json/messages/{root_message_id}"))
        self.assertEqual(after_send["message"]["reply_summary"]["reply_count"], 1)
        self.assertEqual(after_send["message"]["reply_summary"]["unread_reply_count"], 1)

        mark_read = self.client_post(
            "/json/messages/flags",
            {
                "messages": orjson.dumps([reply_id]).decode(),
                "op": "add",
                "flag": "read",
            },
        )
        self.assert_json_success(mark_read)
        after_read = self.assert_json_success(self.client_get(f"/json/messages/{root_message_id}"))
        self.assertEqual(after_read["message"]["reply_summary"]["unread_reply_count"], 0)

        move_messages_to_archive([reply_id], hamlet.realm, skip_notify=True)
        after_delete = self.assert_json_success(
            self.client_get(f"/json/messages/{root_message_id}")
        )
        self.assertNotIn("reply_summary", after_delete["message"])

    def test_protected_history_summary_does_not_reveal_inaccessible_replies(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        stream_name = "protected-thread-summary"
        self.make_stream(
            stream_name,
            invite_only=True,
            history_public_to_subscribers=False,
        )
        self.subscribe(hamlet, stream_name)
        self.subscribe(cordelia, stream_name)
        root_message_id = self.send_stream_message(
            hamlet,
            stream_name,
            content="visible root",
            topic_name="thread topic",
        )

        self.unsubscribe(cordelia, stream_name)
        self._send_reply(hamlet, stream_name, root_message_id, "hidden reply")
        self.subscribe(cordelia, stream_name)

        self.login_user(cordelia)
        hidden = self.assert_json_success(self.client_get(f"/json/messages/{root_message_id}"))
        self.assertNotIn("reply_summary", hidden["message"])

        visible_reply_id = self._send_reply(
            hamlet,
            stream_name,
            root_message_id,
            "visible reply",
        )
        visible = self.assert_json_success(self.client_get(f"/json/messages/{root_message_id}"))
        self.assertEqual(visible["message"]["reply_summary"]["reply_count"], 1)
        self.assertEqual(visible["message"]["reply_summary"]["unread_reply_count"], 1)

        mark_topic_read = self.client_post(
            "/json/mark_topic_as_read",
            {
                "stream_id": get_stream(stream_name, hamlet.realm).id,
                "topic_name": "thread topic",
            },
        )
        self.assert_json_success(mark_topic_read)
        after_topic_read = self.assert_json_success(
            self.client_get(f"/json/messages/{root_message_id}")
        )
        self.assertEqual(
            after_topic_read["message"]["reply_summary"]["unread_reply_count"],
            0,
        )
        self.assertTrue(
            UserMessage.objects.filter(
                user_profile=cordelia,
                message_id=visible_reply_id,
                flags__andnz=UserMessage.flags.read.mask,
            ).exists()
        )

    def test_embedded_bot_reply_preserves_existing_thread(self) -> None:
        hamlet = self.example_user("hamlet")
        bot = self.create_test_bot(short_name="threadbot", user_profile=hamlet)
        stream_name, root_message_id = self._thread_message([hamlet, bot])
        reply_id = self._send_reply(hamlet, stream_name, root_message_id, "@bot question")

        incoming = MessageDict.wide_dict(Message.objects.get(id=reply_id))
        result = EmbeddedBotHandler(bot).send_reply(incoming, "bot answer")
        bot_reply = Message.objects.get(id=result["id"])
        self.assertEqual(bot_reply.thread_root_id, root_message_id)

    def test_embedded_service_bot_can_reply_in_direct_message_thread(self) -> None:
        hamlet = self.example_user("hamlet")
        bot = self.create_test_bot(short_name="dmthreadbot", user_profile=hamlet)
        root_message_id = self.send_personal_message(hamlet, bot, "direct root")
        self.assertFalse(
            UserMessage.objects.filter(
                user_profile=bot,
                message_id=root_message_id,
            ).exists()
        )

        response = self.assert_json_success(
            self.api_post(
                hamlet,
                "/api/v1/messages",
                {
                    "type": "private",
                    "to": orjson.dumps([bot.id]).decode(),
                    "content": "question for bot",
                    "thread_root_message_id": root_message_id,
                },
            )
        )
        incoming = MessageDict.wide_dict(Message.objects.get(id=response["id"]))
        result = EmbeddedBotHandler(bot).send_reply(incoming, "bot answer")
        bot_reply = Message.objects.get(id=result["id"])
        self.assertEqual(bot_reply.thread_root_id, root_message_id)

    def test_topic_move_keeps_thread_together(self) -> None:
        hamlet = self.example_user("hamlet")
        stream_name, root_message_id = self._thread_message([hamlet])
        reply_id = self._send_reply(hamlet, stream_name, root_message_id, "reply")

        self.login_user(hamlet)
        edit_result = self.client_patch(
            f"/json/messages/{reply_id}",
            {"content": "edited reply"},
        )
        self.assert_json_success(edit_result)
        self.assertEqual(Message.objects.get(id=reply_id).content, "edited reply")

        reaction_result = self.api_post(
            hamlet,
            f"/api/v1/messages/{reply_id}/reactions",
            {"emoji_name": "tada"},
        )
        self.assert_json_success(reaction_result)
        self.assertEqual(
            MessageDict.wide_dict(Message.objects.get(id=reply_id))["reactions"][0]["emoji_name"],
            "tada",
        )

        move_result = self.client_patch(
            f"/json/messages/{root_message_id}",
            {"topic": "moved thread", "propagate_mode": "change_one"},
        )
        self.assert_json_success(move_result)
        self.assertEqual(Message.objects.get(id=root_message_id).topic_name(), "moved thread")
        self.assertEqual(Message.objects.get(id=reply_id).topic_name(), "moved thread")

        split_result = self.client_patch(
            f"/json/messages/{reply_id}",
            {"topic": "split thread", "propagate_mode": "change_one"},
        )
        self.assert_json_error(split_result, "Replies cannot be moved independently")

    def test_topic_move_notifies_user_with_only_reply_usermessage(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        stream_name = "protected-native-message-threads"
        self.make_stream(stream_name, invite_only=True)
        self.subscribe(hamlet, stream_name)
        self.subscribe(cordelia, stream_name)
        root_message_id = self.send_stream_message(
            hamlet,
            stream_name,
            topic_name="thread topic",
            content="Parent message",
        )
        reply_id = self._send_reply(hamlet, stream_name, root_message_id, "reply")

        UserMessage.objects.filter(
            user_profile=cordelia,
            message_id=root_message_id,
        ).delete()
        self.assertTrue(
            UserMessage.objects.filter(user_profile=cordelia, message_id=reply_id).exists()
        )
        self.unsubscribe(cordelia, stream_name)

        self.login_user(hamlet)
        with self.capture_send_event_calls(expected_num_events=1) as events:
            move_result = self.client_patch(
                f"/json/messages/{root_message_id}",
                {"topic": "moved thread", "propagate_mode": "change_one"},
            )
        self.assert_json_success(move_result)
        update_event = events[0]
        self.assertEqual(update_event["event"]["type"], "update_message")
        notified_user_ids = {
            user["id"] if isinstance(user, dict) else user for user in update_event["users"]
        }
        self.assertIn(cordelia.id, notified_user_ids)

    def test_root_archive_and_restore_preserves_reply_messages(self) -> None:
        hamlet = self.example_user("hamlet")
        stream_name, root_message_id = self._thread_message([hamlet])
        reply_id = self._send_reply(hamlet, stream_name, root_message_id, "reply")

        with self.capture_send_event_calls(expected_num_events=2) as events:
            move_messages_to_archive([root_message_id], hamlet.realm)
        delete_event = next(
            event["event"] for event in events if event["event"]["type"] == "delete_message"
        )
        self.assertEqual(delete_event["message_ids"], [root_message_id, reply_id])
        self.assertFalse(Message.objects.filter(id__in=[root_message_id, reply_id]).exists())
        archived_reply = ArchivedMessage.objects.get(id=reply_id)
        self.assertEqual(archived_reply.thread_root_message_id, root_message_id)

        archive_transaction = ArchiveTransaction.objects.get(
            id=archived_reply.archive_transaction_id
        )
        self.assertEqual(restore_data_from_archive(archive_transaction), 2)
        restored_reply = Message.objects.get(id=reply_id)
        self.assertEqual(restored_reply.thread_root_id, root_message_id)

    def test_manual_archive_chunk_boundary_does_not_reprocess_reply(self) -> None:
        hamlet = self.example_user("hamlet")
        stream_name, root_message_id = self._thread_message([hamlet])
        reply_id = self._send_reply(hamlet, stream_name, root_message_id, "reply")

        move_messages_to_archive(
            [root_message_id, reply_id],
            hamlet.realm,
            chunk_size=1,
            skip_notify=True,
        )
        self.assertFalse(Message.objects.filter(id__in=[root_message_id, reply_id]).exists())
        self.assertEqual(
            set(
                ArchivedMessage.objects.filter(id__in=[root_message_id, reply_id]).values_list(
                    "id", flat=True
                )
            ),
            {root_message_id, reply_id},
        )

    def test_restore_orders_root_before_independently_archived_reply(self) -> None:
        hamlet = self.example_user("hamlet")
        stream_name, root_message_id = self._thread_message([hamlet])
        reply_id = self._send_reply(hamlet, stream_name, root_message_id, "reply")

        move_messages_to_archive([reply_id], hamlet.realm, skip_notify=True)
        reply_transaction = ArchiveTransaction.objects.get(
            id=ArchivedMessage.objects.get(id=reply_id).archive_transaction_id
        )
        move_messages_to_archive([root_message_id], hamlet.realm, skip_notify=True)
        root_transaction = ArchiveTransaction.objects.get(
            id=ArchivedMessage.objects.get(id=root_message_id).archive_transaction_id
        )

        with self.assertRaisesRegex(
            Message.DoesNotExist,
            f"archived root message {root_message_id}",
        ):
            restore_data_from_archive(reply_transaction)
        self.assertFalse(Message.objects.filter(id__in=[root_message_id, reply_id]).exists())

        restored_count = restore_data_from_archive_by_transactions(
            [reply_transaction, root_transaction]
        )
        self.assertEqual(restored_count, 2)
        self.assertEqual(Message.objects.get(id=reply_id).thread_root_id, root_message_id)

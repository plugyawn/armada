from zerver.lib.event_types import MessageThreadEvent
from zerver.lib.message_cache import MessageDict
from zerver.lib.message_threads import bulk_message_thread_summaries
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Message, MessageThread, MessageThreadReply, UserProfile


class MessageThreadTest(ZulipTestCase):
    def _thread_message(self, users: list[UserProfile]) -> int:
        stream_name = "message-threads"
        self.make_stream(stream_name)
        for user in users:
            self.subscribe(user, stream_name)
        return self.send_stream_message(users[0], stream_name, content="Parent message")

    def test_empty_thread_is_lazy(self) -> None:
        hamlet = self.example_user("hamlet")
        message_id = self._thread_message([hamlet])

        self.login_user(hamlet)
        response = self.assert_json_success(
            self.client_get(
                f"/json/messages/{message_id}/thread",
                intentionally_undocumented=True,
            )
        )
        self.assertEqual(
            response,
            {
                "result": "success",
                "msg": "",
                "reply_summary": None,
                "replies": [],
                "has_more": False,
            },
        )
        self.assertFalse(MessageThread.objects.filter(message_id=message_id).exists())
        self.assertNotIn("reply_summary", MessageDict.wide_dict(Message.objects.get(id=message_id)))
        self.login_user(hamlet)
        fetched = self.assert_json_success(self.client_get(f"/json/messages/{message_id}"))
        self.assertNotIn("reply_summary", fetched["message"])

    def test_post_renders_and_emits_recipient_event(self) -> None:
        othello = self.example_user("othello")
        hamlet = self.example_user("hamlet")
        message_id = self._thread_message([othello, hamlet])

        with self.capture_send_event_calls(expected_num_events=1) as events:
            response = self.assert_json_success(
                self.api_post(
                    hamlet,
                    f"/api/v1/messages/{message_id}/thread",
                    {"content": "**hello** <script>alert(1)</script>"},
                    intentionally_undocumented=True,
                )
            )

        reply = response["reply"]
        summary = response["reply_summary"]
        self.assertEqual(reply["message_id"], message_id)
        self.assertEqual(reply["sender_id"], hamlet.id)
        self.assertEqual(reply["content_type"], "text/html")
        self.assertIn("<strong>hello</strong>", reply["content"])
        self.assertNotIn("<script>", reply["content"])
        self.assertEqual(summary["reply_count"], 1)
        self.assertEqual(summary["last_sender"]["user_id"], hamlet.id)
        self.assertEqual([user["user_id"] for user in summary["participants"]], [hamlet.id])

        event = events[0]["event"]
        MessageThreadEvent.model_validate(event)
        self.assertEqual(event["type"], "message_thread")
        self.assertEqual(event["reply_summary"], summary)
        self.assertEqual(event["reply"], reply)
        self.assertIn(othello.id, events[0]["users"])
        self.assertIn(hamlet.id, events[0]["users"])

        stored_reply = MessageThreadReply.objects.get(id=reply["id"])
        self.assertEqual(stored_reply.content, "**hello** <script>alert(1)</script>")
        self.assertEqual(stored_reply.rendered_content, reply["content"])

        wide_message = MessageDict.wide_dict(Message.objects.get(id=message_id))
        self.assertEqual(wide_message["reply_summary"], summary)
        send_hot_path_message = MessageDict.wide_dict(
            Message.objects.get(id=message_id),
            include_message_thread_summary=False,
        )
        self.assertNotIn("reply_summary", send_hot_path_message)
        self.login_user(hamlet)
        fetched = self.assert_json_success(self.client_get(f"/json/messages/{message_id}"))
        self.assertEqual(fetched["message"]["reply_summary"], summary)

    def test_pagination_and_bounded_participants(self) -> None:
        users = [
            self.example_user("othello"),
            self.example_user("hamlet"),
            self.example_user("cordelia"),
            self.example_user("iago"),
            self.example_user("desdemona"),
        ]
        message_id = self._thread_message(users)
        reply_ids: list[int] = []
        for index, user in enumerate(users):
            response = self.assert_json_success(
                self.api_post(
                    user,
                    f"/api/v1/messages/{message_id}/thread",
                    {"content": f"reply {index}"},
                    intentionally_undocumented=True,
                )
            )
            reply_ids.append(response["reply"]["id"])

        self.login_user(users[0])
        latest_page = self.assert_json_success(
            self.client_get(
                f"/json/messages/{message_id}/thread",
                {"limit": 2},
                intentionally_undocumented=True,
            )
        )
        self.assertTrue(latest_page["has_more"])
        self.assertEqual([reply["id"] for reply in latest_page["replies"]], reply_ids[-2:])

        older_page = self.assert_json_success(
            self.client_get(
                f"/json/messages/{message_id}/thread",
                {"limit": 3, "before": reply_ids[-2]},
                intentionally_undocumented=True,
            )
        )
        self.assertFalse(older_page["has_more"])
        self.assertEqual([reply["id"] for reply in older_page["replies"]], reply_ids[:3])

        with self.assert_database_query_count(2):
            summary = bulk_message_thread_summaries([message_id])[message_id]
        self.assertEqual(summary["reply_count"], 5)
        self.assertEqual(summary["last_sender"]["user_id"], users[-1].id)
        self.assertEqual(
            [participant["user_id"] for participant in summary["participants"]],
            [user.id for user in reversed(users[-4:])],
        )

    def test_rejects_empty_and_inaccessible_replies(self) -> None:
        sender = self.example_user("othello")
        outsider = self.example_user("hamlet")
        stream_name = "private-message-threads"
        self.make_stream(stream_name, invite_only=True)
        self.subscribe(sender, stream_name)
        message_id = self.send_stream_message(sender, stream_name)

        self.login_user(outsider)
        get_result = self.client_get(
            f"/json/messages/{message_id}/thread",
            intentionally_undocumented=True,
        )
        self.assert_json_error(get_result, "Invalid message(s)")
        post_result = self.api_post(
            outsider,
            f"/api/v1/messages/{message_id}/thread",
            {"content": "not allowed"},
            intentionally_undocumented=True,
        )
        self.assert_json_error(post_result, "Invalid message(s)")

        empty_result = self.api_post(
            sender,
            f"/api/v1/messages/{message_id}/thread",
            {"content": "   \n"},
            intentionally_undocumented=True,
        )
        self.assert_json_error(empty_result, "Message must not be empty")

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pyvips

from zerver.actions.streams import do_change_stream_group_based_setting
from zerver.lib.armada import ARMADA_AGENT_NAME, ARMADA_EMBEDDED_BOT_SERVICE
from zerver.lib.armada_agent import (
    ArmadaAppServerNotification,
    ArmadaAppServerTurnContext,
    ArmadaCodexAppServerToolRunner,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Attachment, Message, NamedUserGroup, UserMessage, UserProfile
from zerver.models.bots import AgentRun
from zerver.models.groups import SystemGroups
from zerver.models.streams import get_stream


class MessageToolsTest(ZulipTestCase):
    """The agent's message tools, called as a scripted Codex calls them:
    a JSON-RPC item/tool/call request into the runtime, answered with the
    reply Codex reads and an event the run shows."""

    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.cordelia = self.example_user("cordelia")
        # Owned by an administrator, so the agent itself may post where a
        # member may not: the tools' refusals below are the asker's.
        self.agent = self.create_test_bot(
            "agent",
            self.example_user("iago"),
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        for user in (self.hamlet, self.cordelia, self.agent):
            self.subscribe(user, "Denmark")
            self.subscribe(user, "Verona")

    def ask(
        self, asker: UserProfile, content: str, *, topic: str = "parser", stream: str = "Denmark"
    ) -> AgentRun:
        """The person's request, and the run answering it."""
        message_id = self.send_stream_message(asker, stream, content, topic_name=topic)
        return AgentRun.objects.create(
            realm=asker.realm,
            bot_profile=self.agent,
            requesting_user=asker,
            trigger_message_id=message_id,
            stream_name=stream,
            topic_name=topic,
            status=AgentRun.RUNNING,
        )

    def call(
        self,
        run: AgentRun | None,
        tool: str,
        arguments: dict[str, object],
        *,
        requesting_user_id: int | None = None,
        workdir: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Send Codex's request through the runtime; return the reply's
        result and the event the run shows."""
        runner = ArmadaCodexAppServerToolRunner(
            codex_bin="/bin/false",
            workdir=workdir,
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="on-request",
            timeout_seconds=5,
            output_limit=1000,
        )
        turn = ArmadaAppServerTurnContext(
            thread_id="thread-1",
            event_callback=None,
            user_input_provider=None,
            is_main=True,
            requesting_user_id=(
                requesting_user_id
                if requesting_user_id is not None or run is None
                else run.requesting_user_id
            ),
            trigger_message_id=run.trigger_message_id if run is not None else None,
        )
        sent: list[dict[str, Any]] = []
        with patch.object(runner, "send_message", side_effect=sent.append):
            runner.handle_server_request(
                {
                    "id": 7,
                    "method": "item/tool/call",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "callId": "call-1",
                        "tool": tool,
                        "namespace": None,
                        "arguments": json.dumps(arguments),
                    },
                },
                turn_context=turn,
            )
        [reply] = sent
        self.assertEqual(reply["id"], 7)
        notification = turn.messages.get_nowait()
        assert isinstance(notification, ArmadaAppServerNotification)
        [event] = notification.events
        return reply["result"], dict(event)

    def answer(self, result: dict[str, Any]) -> Any:
        self.assertTrue(result["success"], result)
        [item] = result["contentItems"]
        return json.loads(item["text"])

    def refusal(self, result: dict[str, Any]) -> str:
        self.assertFalse(result["success"])
        [item] = result["contentItems"]
        return item["text"]

    def test_a_new_thread_offers_the_message_tools(self) -> None:
        runner = ArmadaCodexAppServerToolRunner(
            codex_bin="/bin/false",
            workdir=None,
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="on-request",
            timeout_seconds=5,
            output_limit=1000,
        )
        tools = {tool["name"]: tool for tool in runner.thread_params()["dynamicTools"]}
        self.assertEqual(
            set(tools["armada_post"]["inputSchema"]["properties"]),
            {"content", "channel", "topic", "thread", "files"},
        )
        self.assertEqual(
            set(tools["armada_read"]["inputSchema"]["properties"]),
            {"channel", "topic", "thread", "after", "limit"},
        )

    def test_a_post_in_another_topic_is_the_agents_message(self) -> None:
        run = self.ask(self.hamlet, "keep the team posted in Verona")
        result, event = self.call(
            run,
            "armada_post",
            {
                "channel": "Verona",
                "topic": "parser updates",
                "content": "Started on the parser, @**Cordelia, Lear's daughter**.",
            },
        )
        message = Message.objects.get(id=self.answer(result)["message_id"])
        self.assertEqual(
            self.answer(result),
            {"message_id": message.id, "channel": "Verona", "topic": "parser updates"},
        )
        self.assertEqual(
            (message.sender_id, message.topic_name(), message.thread_root_id, message.content),
            (
                self.agent.id,
                "parser updates",
                None,
                "Started on the parser, @**Cordelia, Lear's daughter**.",
            ),
        )
        # A mention as people write one notifies the person.
        self.assertIn(
            "mentioned",
            UserMessage.objects.get(user_profile=self.cordelia, message=message).flags_list(),
        )
        # What the run shows people.
        self.assertEqual(
            (event["kind"], event["status"], event["title"], event["item_id"]),
            ("tool", "done", "Posted in #Verona > parser updates.", "call-1"),
        )
        self.assertEqual(event["metadata"]["tool_name"], "armada_post")

    def test_a_post_in_a_thread_goes_under_its_root(self) -> None:
        run = self.ask(self.hamlet, "update me in a new thread")
        result, event = self.call(
            run,
            "armada_post",
            {"thread": run.trigger_message_id, "content": "Step 1 of 3 done."},
        )
        first = Message.objects.get(id=self.answer(result)["message_id"])
        self.assertEqual(first.thread_root_id, run.trigger_message_id)
        self.assertEqual(self.answer(result)["thread"], run.trigger_message_id)
        self.assertEqual(event["title"], "Replied in a thread in #Denmark > parser.")

        # A reply's id means its thread: replies are never nested.
        result, _event = self.call(
            run, "armada_post", {"thread": first.id, "content": "Step 2 of 3 done."}
        )
        second = Message.objects.get(id=self.answer(result)["message_id"])
        self.assertEqual(second.thread_root_id, run.trigger_message_id)

    def test_a_post_with_no_place_goes_where_the_run_was_asked(self) -> None:
        run = self.ask(self.hamlet, "what is left?")
        result, _event = self.call(run, "armada_post", {"content": "Two tests."})
        message = Message.objects.get(id=self.answer(result)["message_id"])
        self.assertEqual(
            (message.recipient_id, message.topic_name(), message.thread_root_id),
            (get_stream("Denmark", self.hamlet.realm).recipient_id, "parser", None),
        )

        # A run asked in a thread posts in that thread.
        root_id = self.send_stream_message(self.cordelia, "Denmark", "The parser.", "parser")
        reply_id = self.send_stream_message(self.hamlet, "Denmark", "Why?", "parser")
        Message.objects.filter(id=reply_id).update(thread_root_id=root_id)
        thread_run = AgentRun.objects.create(
            realm=self.hamlet.realm,
            bot_profile=self.agent,
            requesting_user=self.hamlet,
            trigger_message_id=reply_id,
            stream_name="Denmark",
            topic_name="parser",
            session_kind=AgentRun.SIDE,
            source_message_id=root_id,
            status=AgentRun.RUNNING,
        )
        result, _event = self.call(thread_run, "armada_post", {"content": "Because."})
        message = Message.objects.get(id=self.answer(result)["message_id"])
        self.assertEqual(message.thread_root_id, root_id)

    def test_a_post_attaches_files_people_can_open(self) -> None:
        run = self.ask(self.hamlet, "show me the page")
        workdir = tempfile.mkdtemp(prefix="armada-message-tools-")
        self.addCleanup(shutil.rmtree, workdir)
        png = pyvips.Image.black(16, 8).pngsave_buffer()
        Path(workdir, "shot.png").write_bytes(png)
        notes = Path(workdir, "notes.txt")
        notes.write_text("All green.\n")

        result, _event = self.call(
            run,
            "armada_post",
            {"content": "The page now:", "files": ["shot.png", str(notes)]},
            workdir=workdir,
        )
        message = Message.objects.get(id=self.answer(result)["message_id"])
        attachments = {
            attachment.file_name: attachment
            for attachment in Attachment.objects.filter(messages=message)
        }
        # Uploaded as the agent, claimed by its message.
        self.assertEqual(set(attachments), {"shot.png", "notes.txt"})
        self.assertTrue(all(a.owner_id == self.agent.id for a in attachments.values()))
        self.assertEqual(
            message.content,
            "The page now:\n"
            f"[shot.png](/user_uploads/{attachments['shot.png'].path_id})\n"
            f"[notes.txt](/user_uploads/{attachments['notes.txt'].path_id})",
        )
        # The screenshot shows inline.
        assert message.rendered_content is not None
        self.assertIn('class="message_inline_image"', message.rendered_content)

        # Anyone who can read the message can open its files.
        self.login_user(self.cordelia)
        response = self.client_get(f"/user_uploads/{attachments['shot.png'].path_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.getvalue(), png)

        # Files alone are a post too.
        result, _event = self.call(
            run, "armada_post", {"content": "", "files": ["shot.png"]}, workdir=workdir
        )
        self.assertTrue(result["success"])

        # The realm's own upload limit, with the error a person gets.
        with self.settings(MAX_FILE_UPLOAD_SIZE=0):
            result, _event = self.call(
                run, "armada_post", {"content": "Big", "files": ["shot.png"]}, workdir=workdir
            )
        self.assertEqual(
            self.refusal(result),
            "File is larger than this server's configured maximum upload size (0 MiB).",
        )
        for files, refusal in [
            (["missing.png"], "No file, or an empty one, at missing.png."),
            ([workdir], f"Could not read {workdir}."),
            ([""], "files must be a list of paths."),
            (["shot.png"] * 11, "Attach at most 10 files."),
        ]:
            result, _event = self.call(
                run, "armada_post", {"content": "x", "files": files}, workdir=workdir
            )
            self.assertEqual(self.refusal(result), refusal)
        result, _event = self.call(run, "armada_post", {"content": "x", "files": ["shot.png"]})
        self.assertEqual(
            self.refusal(result),
            "This run has no working directory; give shot.png as an absolute path.",
        )

    def test_a_post_has_the_askers_rights(self) -> None:
        realm = self.hamlet.realm
        run = self.ask(self.hamlet, "post it everywhere")

        # A private channel the asker is not in.
        self.make_stream("secret", invite_only=True)
        self.subscribe(self.agent, "secret")
        self.subscribe(self.example_user("iago"), "secret")
        # A channel only administrators may post in; the agent's owner is one.
        do_change_stream_group_based_setting(
            get_stream("Verona", realm),
            "can_send_message_group",
            NamedUserGroup.objects.get(
                name=SystemGroups.ADMINISTRATORS, realm_for_sharding=realm, is_system_group=True
            ),
            acting_user=self.example_user("iago"),
        )
        hidden_id = self.send_stream_message(self.example_user("iago"), "secret", "x", "plans")

        for arguments, refusal in [
            (
                {"channel": "secret", "topic": "plans", "content": "Hi"},
                "Not authorized to send to channel 'secret'",
            ),
            (
                {"channel": "Verona", "topic": "news", "content": "Hi"},
                "You do not have permission to post in this channel.",
            ),
            ({"thread": hidden_id, "content": "Hi"}, "Invalid message(s)"),
            (
                {"channel": "nowhere", "topic": "x", "content": "Hi"},
                "Channel 'nowhere' does not exist",
            ),
        ]:
            result, event = self.call(run, "armada_post", arguments)
            self.assertEqual(self.refusal(result), refusal)
            self.assertEqual((event["status"], event["title"]), ("failed", refusal))
        # The agent posted nothing.
        self.assertFalse(Message.objects.filter(realm=realm, sender=self.agent).exists())

    def test_a_read_returns_what_the_asker_can_read(self) -> None:
        run = self.ask(self.hamlet, "how is the parser going?")
        first = self.send_stream_message(self.cordelia, "Verona", "Lexer done.", "parser log")
        reply = self.send_stream_message(self.hamlet, "Verona", "Nice.", "parser log")
        Message.objects.filter(id=reply).update(thread_root_id=first)
        second = self.send_stream_message(self.agent, "Verona", "x" * 3100, "parser log")
        AgentRun.objects.create(
            realm=self.hamlet.realm,
            bot_profile=self.agent,
            requesting_user=self.hamlet,
            trigger_message_id=second,
            result_message_id=second,
            stream_name="Verona",
            topic_name="parser log",
            status=AgentRun.DONE,
        )

        result, event = self.call(run, "armada_read", {"channel": "Verona", "topic": "parser log"})
        read = self.answer(result)
        # Threads fold to their first message, with a reply count.
        self.assertEqual(
            [
                (message["id"], message["sender"], message.get("replies"))
                for message in read["messages"]
            ],
            [(first, self.cordelia.full_name, 1), (second, "Onix", None)],
        )
        self.assertEqual(read["messages"][0]["content"], "Lexer done.")
        self.assertRegex(read["messages"][0]["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertTrue(read["messages"][1]["content"].endswith("... [truncated]"))
        self.assertEqual(
            (read["channel"], read["topic"], read["more"], read["run"]),
            (
                "Verona",
                "parser log",
                False,
                {"status": "done", "request": second, "answer": second},
            ),
        )
        self.assertEqual(event["title"], "Read #Verona > parser log.")

        # after and limit page forward and back.
        result, _event = self.call(
            run, "armada_read", {"channel": "Verona", "topic": "parser log", "after": first}
        )
        self.assertEqual([m["id"] for m in self.answer(result)["messages"]], [second])
        result, _event = self.call(
            run, "armada_read", {"channel": "Verona", "topic": "parser log", "limit": 1}
        )
        self.assertEqual(
            ([m["id"] for m in self.answer(result)["messages"]], self.answer(result)["more"]),
            ([second], True),
        )

        # A thread in full, its first message first.
        result, event = self.call(run, "armada_read", {"thread": reply})
        read = self.answer(result)
        self.assertEqual([m["id"] for m in read["messages"]], [first, reply])
        self.assertEqual((read["thread"], "run" in read), (first, False))
        self.assertEqual(event["title"], "Read a thread in #Verona > parser log.")

        # With no place, where the run was asked.
        result, _event = self.call(run, "armada_read", {})
        self.assertEqual(
            [m["id"] for m in self.answer(result)["messages"]], [run.trigger_message_id]
        )
        self.assertEqual(self.answer(result)["run"]["status"], "running")

    def test_a_read_has_the_askers_access(self) -> None:
        run = self.ask(self.hamlet, "read the secret plans")
        self.make_stream("secret", invite_only=True)
        iago = self.example_user("iago")
        self.subscribe(iago, "secret")
        hidden_id = self.send_stream_message(iago, "secret", "x", "plans")
        for arguments, refusal in [
            ({"channel": "secret", "topic": "plans"}, "Invalid channel name 'secret'"),
            ({"thread": hidden_id}, "Invalid message(s)"),
            ({"channel": "Verona", "topic": "x", "limit": 101}, "limit must be between 1 and 100."),
        ]:
            result, _event = self.call(run, "armada_read", arguments)
            self.assertEqual(self.refusal(result), refusal)

    def test_refusals_are_answers_the_agent_reads(self) -> None:
        run = self.ask(self.hamlet, "post somewhere")
        for call_run, requesting_user_id, arguments, refusal in [
            (
                run,
                self.agent.id,
                {"content": "Hi"},
                "No person asked for this turn, so there is nobody to act for.",
            ),
            (None, self.hamlet.id, {"content": "Hi"}, "This turn is not part of a run."),
            (
                run,
                None,
                {"thread": 1, "channel": "Verona", "content": "Hi"},
                "Give thread, or channel and topic, not both.",
            ),
            (run, None, {"channel": "Verona", "content": "Hi"}, "Give both channel and topic."),
            (run, None, {"content": "  "}, "Give content, or files to attach."),
            (run, None, {"content": 3}, "content must be a string."),
            (run, None, {"content": "Hi", "files": "a.png"}, "files must be a list of paths."),
            (
                run,
                None,
                {"thread": self.send_personal_message(self.hamlet, self.cordelia), "content": "Hi"},
                f"{ARMADA_AGENT_NAME} posts and reads only in channels.",
            ),
        ]:
            result, _event = self.call(
                call_run, "armada_post", arguments, requesting_user_id=requesting_user_id
            )
            self.assertEqual(self.refusal(result), refusal)

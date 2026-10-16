import json
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pyvips
from typing_extensions import override

from zerver.actions.realm_settings import do_change_realm_permission_group_setting
from zerver.actions.streams import do_change_stream_group_based_setting
from zerver.lib.armada import ARMADA_AGENT_NAME, ARMADA_EMBEDDED_BOT_SERVICE
from zerver.lib.armada_agent import (
    ArmadaAppServerNotification,
    ArmadaAppServerTurnContext,
    ArmadaCodexAppServerToolRunner,
)
from zerver.lib.armada_worker import armada_worker_queue_name, load_armada_active_session_from_zulip
from zerver.lib.streams import access_stream_by_name
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.topic import messages_for_topic
from zerver.models import Attachment, Message, NamedUserGroup, Stream, UserMessage, UserProfile
from zerver.models.bots import AgentRun, AgentRunEvent
from zerver.models.groups import SystemGroups
from zerver.models.streams import get_stream


class MessageToolsTest(ZulipTestCase):
    """The agent's message tools, called as a scripted Codex calls them:
    a JSON-RPC item/tool/call request into the runtime, answered with the
    reply Codex reads and an event the run shows."""

    @override
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
        # A started run gets a worktree; keep them out of the checkout.
        workdir_root = tempfile.mkdtemp(prefix="armada-message-tools-workspaces-")
        self.addCleanup(shutil.rmtree, workdir_root, ignore_errors=True)
        workdir_root_setting = self.settings(ARMADA_TOPIC_WORKDIR_ROOT=workdir_root)
        workdir_root_setting.enable()
        self.addCleanup(workdir_root_setting.disable)

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
        arguments: object,
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
        self.assertEqual(
            tools["armada_start_run"]["inputSchema"]["required"], ["channel", "topic", "brief"]
        )
        # The caps are the realm's settings, and the model reads them.
        self.assertIn(
            "Limits: a run can start at most 5 runs, and runs started this way nest at most 2 "
            "deep (a run that deep starts none).",
            tools["armada_start_run"]["description"],
        )
        self.assertEqual(
            (
                set(tools["armada_create_channel"]["inputSchema"]["properties"]),
                tools["armada_create_channel"]["inputSchema"]["required"],
            ),
            ({"name", "description", "private", "subscribers"}, ["name"]),
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

    def start(
        self, run: AgentRun, arguments: Mapping[str, object]
    ) -> tuple[dict[str, Any], dict[str, Any], MagicMock]:
        """Start a run; the worker queue is captured, not consumed."""
        with patch("zerver.lib.armada_worker.queue_json_publish_rollback_unsafe") as publish:
            result, event = self.call(run, "armada_start_run", arguments)
        return result, event, publish

    def topic_messages(self, stream: str, topic: str) -> list[Message]:
        recipient_id = get_stream(stream, self.hamlet.realm).recipient_id
        assert recipient_id is not None
        return list(messages_for_topic(self.hamlet.realm_id, recipient_id, topic).order_by("id"))

    def test_a_started_run_works_from_its_brief_in_its_own_topic(self) -> None:
        run = self.ask(self.hamlet, "have someone fix the lexer, and update me in a thread")
        brief = (
            "Fix the lexer's handling of non-ASCII identifiers.\n\n"
            f"Post progress with armada_post in thread {run.trigger_message_id}."
        )
        result, event, publish = self.start(
            run, {"channel": "Verona", "topic": "lexer fix", "brief": brief}
        )
        answer = self.answer(result)
        [brief_message, progress_message] = self.topic_messages("Verona", "lexer fix")
        self.assertEqual(
            answer,
            {
                "message_id": brief_message.id,
                "channel": "Verona",
                "topic": "lexer fix",
                "link": f"#**Verona>lexer fix@{brief_message.id}**",
            },
        )
        self.assertEqual(event["title"], "Started a run in #Verona > lexer fix.")
        # The brief is the agent's message and the new topic's request, and
        # says where it came from.
        self.assertEqual(brief_message.sender_id, self.agent.id)
        self.assertEqual(
            brief_message.content,
            f"{brief}\n\nStarted for @_**{self.hamlet.full_name}|{self.hamlet.id}** from "
            f"#**Denmark>parser@{run.trigger_message_id}**.",
        )
        # A run for the same person, in the new topic's own worktree, with
        # its progress message, as a person's request gets.
        child = AgentRun.objects.get(trigger_message_id=brief_message.id)
        assert child.workspace_node is not None
        self.assertEqual(
            (
                child.status,
                child.session_kind,
                child.requesting_user_id,
                child.workspace_node.topic_name,
                child.result_message_id,
            ),
            (AgentRun.QUEUED, AgentRun.SHARED, self.hamlet.id, "lexer fix", progress_message.id),
        )
        self.assertEqual(progress_message.sender_id, self.agent.id)
        # Queued for the worker, with the brief as the request.
        [(queue_name, worker_event)] = [call.args for call in publish.call_args_list]
        self.assertEqual(
            queue_name, armada_worker_queue_name(self.agent.id, stream="Verona", topic="lexer fix")
        )
        self.assertEqual(
            {key: worker_event[key] for key in worker_event if key != "message"},
            {
                "user_profile_id": self.agent.id,
                "message_id": brief_message.id,
                "stream": "Verona",
                "topic": "lexer fix",
                "content": brief_message.content,
                "requesting_user_id": self.hamlet.id,
                "session_kind": AgentRun.SHARED,
                "source_stream": "",
                "source_topic": "",
                "source_message_id": None,
                "progress_message_id": progress_message.id,
            },
        )
        # The run's tools act for the same person, though the agent sent
        # the brief.
        active_session = load_armada_active_session_from_zulip(
            worker_event["message"], SimpleNamespace(user_profile=self.agent)
        )
        self.assertEqual(active_session.get("requesting_user_id"), self.hamlet.id)
        # The event log says who started whom.
        self.assertEqual(
            AgentRunEvent.objects.get(run=run, event_type="run_started").payload,
            {
                "run_id": child.id,
                "stream": "Verona",
                "topic": "lexer fix",
                "message_id": brief_message.id,
            },
        )
        self.assertEqual(
            AgentRunEvent.objects.get(run=child, event_type="started_by").payload,
            {
                "run_id": run.id,
                "depth": 1,
                "stream": "Denmark",
                "topic": "parser",
                "message_id": run.trigger_message_id,
            },
        )

    def test_runs_started_by_runs_are_capped(self) -> None:
        with self.settings(
            ARMADA_WORKER_MAX_RUNS_STARTED_PER_RUN=2,
            ARMADA_WORKER_MAX_RUN_DEPTH=2,
            ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_REALM=10,
        ):
            run = self.ask(self.hamlet, "split the work")
            for topic in ["one", "two"]:
                result, _event, _publish = self.start(
                    run, {"channel": "Verona", "topic": topic, "brief": f"Part {topic}."}
                )
                self.assertTrue(result["success"], result)
            result, _event, publish = self.start(
                run, {"channel": "Verona", "topic": "three", "brief": "Part three."}
            )
            self.assertEqual(
                self.refusal(result), "This run has started 2 runs, the most one run may start."
            )
            self.assertEqual((self.topic_messages("Verona", "three"), publish.called), ([], False))

            # A started run starts one more, which is at the maximum depth.
            child = AgentRun.objects.get(
                trigger_message_id=self.topic_messages("Verona", "one")[0].id
            )
            result, _event, _publish = self.start(
                child, {"channel": "Verona", "topic": "deeper", "brief": "Part one, half."}
            )
            grandchild = AgentRun.objects.get(trigger_message_id=self.answer(result)["message_id"])
            self.assertEqual(grandchild.requesting_user_id, self.hamlet.id)
            result, _event, _publish = self.start(
                grandchild, {"channel": "Verona", "topic": "deepest", "brief": "Quarter."}
            )
            self.assertEqual(
                self.refusal(result),
                "This run is at depth 2, where runs started by runs stop: do the work here, or "
                "ask the person to start a run.",
            )
            self.assertEqual(self.topic_messages("Verona", "deepest"), [])

    def test_a_start_has_the_askers_rights(self) -> None:
        run = self.ask(self.hamlet, "start runs")
        self.ask(self.cordelia, "keep going", stream="Verona", topic="busy")
        self.make_stream("secret", invite_only=True)
        self.subscribe(self.agent, "secret")
        runs_before = AgentRun.objects.count()
        for overrides, arguments, refusal in [
            (
                {},
                {"channel": "Verona", "topic": "busy", "brief": "Do more."},
                "This topic already has an active run.",
            ),
            (
                {"ARMADA_WORKER_TRUSTED_USERS_ONLY": True},
                {"channel": "Verona", "topic": "new", "brief": "Do it."},
                f"This user is not allowed to start {ARMADA_AGENT_NAME}'s runs.",
            ),
            (
                {},
                {"channel": "secret", "topic": "new", "brief": "Do it."},
                "Not authorized to send to channel 'secret'",
            ),
            ({}, {"channel": "Verona", "topic": "new", "brief": " "}, "Give the run a brief."),
        ]:
            with self.settings(**overrides):
                result, _event, publish = self.start(run, arguments)
            self.assertEqual(self.refusal(result), refusal)
            self.assertFalse(publish.called)
        # Admitted, then beaten to the last slot: the brief goes too.
        with patch(
            "zerver.lib.armada_worker.ArmadaDatabaseWorkerRunStore.start", return_value=False
        ):
            result, _event, publish = self.start(
                run, {"channel": "Verona", "topic": "new", "brief": "Do it."}
            )
        self.assertEqual(self.refusal(result), "The run could not start; try again.")
        self.assertFalse(publish.called)
        # Nothing was started, and no brief was left behind.
        self.assertEqual(AgentRun.objects.count(), runs_before)
        self.assertEqual(
            [message.sender_id for message in self.topic_messages("Verona", "busy")],
            [self.cordelia.id],
        )
        self.assertEqual(self.topic_messages("Verona", "new"), [])

    def test_a_channel_is_created_for_the_asker(self) -> None:
        realm = self.hamlet.realm
        othello = self.example_user("othello")
        run = self.ask(self.hamlet, "make a channel for the parser team, with Cordelia and Othello")
        result, event = self.call(
            run,
            "armada_create_channel",
            {
                "name": "#parser-team ",
                "description": "Where the parser\nis built.",
                # As the transcript shows names, or as a mention.
                "subscribers": [self.cordelia.full_name, f"@**{othello.full_name}**"],
            },
        )
        stream = get_stream("parser-team", realm)
        self.assertEqual(
            self.answer(result),
            {
                "channel_id": stream.id,
                "channel": "parser-team",
                "private": False,
                "subscribers": sorted(
                    [self.hamlet.full_name, self.cordelia.full_name, othello.full_name]
                ),
                "link": "#**parser-team**",
            },
        )
        self.assertEqual(event["title"], "Created #parser-team.")
        # The asker made it, as if from Armada's own form: a public channel
        # with the organization's defaults, them as its creator.
        self.assertEqual(
            (stream.invite_only, stream.description, stream.creator_id),
            (False, "Where the parser is built.", self.hamlet.id),
        )
        # The agent needs no subscription to a public channel.
        self.assertEqual(
            {user.id for user in self.users_subscribed_to_stream("parser-team", realm)},
            {self.hamlet.id, self.cordelia.id, othello.id},
        )
        # The notice a person's new channel gets.
        notice = Message.objects.get(realm=realm, recipient=stream.recipient)
        self.assertEqual(notice.topic_name(), "channel events")
        self.assertIn(f"created by @_**{self.hamlet.full_name}|{self.hamlet.id}**", notice.content)

    def test_a_private_channel_takes_the_agent_too(self) -> None:
        realm = self.hamlet.realm
        run = self.ask(self.hamlet, "make a private channel for the plans")
        result, _event = self.call(run, "armada_create_channel", {"name": "plans", "private": True})
        self.assertEqual(
            (self.answer(result)["private"], get_stream("plans", realm).invite_only), (True, True)
        )
        self.assertEqual(
            {user.id for user in self.users_subscribed_to_stream("plans", realm)},
            {self.hamlet.id, self.agent.id},
        )
        # Its owner is not in the channel, so only its own subscription lets
        # the agent post there, and read it as a run there does.
        result, _event = self.call(
            run, "armada_post", {"channel": "plans", "topic": "first steps", "content": "Ready."}
        )
        self.assertEqual(
            Message.objects.get(id=self.answer(result)["message_id"]).sender_id, self.agent.id
        )
        access_stream_by_name(self.agent, "plans")

    def test_creating_a_channel_has_the_askers_rights(self) -> None:
        realm = self.hamlet.realm
        administrators = NamedUserGroup.objects.get(
            name=SystemGroups.ADMINISTRATORS, realm_for_sharding=realm, is_system_group=True
        )
        for setting in ["can_create_public_channel_group", "can_create_private_channel_group"]:
            do_change_realm_permission_group_setting(
                realm, setting, administrators, acting_user=None
            )
        polonius = self.example_user("polonius")
        self.subscribe(polonius, "Denmark")
        othello = self.example_user("othello")
        othello.full_name = self.cordelia.full_name
        othello.save(update_fields=["full_name"])
        run = self.ask(self.hamlet, "make channels")
        streams_before = Stream.objects.filter(realm=realm).count()
        for asker, arguments, refusal in [
            (
                self.hamlet,
                {"name": "plans", "private": True},
                "King Hamlet may not create private channels in this organization, so neither "
                "can you: an administrator can create it, or let them.",
            ),
            (
                self.hamlet,
                {"name": "plans"},
                "King Hamlet may not create channels in this organization, so neither can you: "
                "an administrator can create it, or let them.",
            ),
            (
                polonius,
                {"name": "plans"},
                "Polonius may not create channels in this organization, so neither can you: an "
                "administrator can create it, or let them.",
            ),
            (self.example_user("iago"), {"name": "verona"}, "Channel 'verona' already exists"),
            (self.example_user("iago"), {"name": " "}, "Channel name can't be empty."),
            (
                self.example_user("iago"),
                {"name": "plans", "subscribers": ["Nobody At All"]},
                "Nobody here is named Nobody At All.",
            ),
            (
                self.example_user("iago"),
                {"name": "plans", "subscribers": [self.cordelia.full_name]},
                f"More than one person here is named {self.cordelia.full_name}; ask which one "
                "is meant.",
            ),
            (
                self.example_user("iago"),
                {"name": "plans", "subscribers": ["@****"]},
                "subscribers must be a list of full names.",
            ),
            (
                self.example_user("iago"),
                {"name": "plans", "subscribers": "Cordelia"},
                "subscribers must be a list of full names.",
            ),
            (
                self.example_user("iago"),
                {"name": "plans", "private": "yes"},
                "private must be true or false.",
            ),
            (
                self.example_user("iago"),
                {"name": "plans", "description": "x" * 1025},
                "description is longer than 1024 characters.",
            ),
        ]:
            asker_run = run if asker == self.hamlet else self.ask(asker, "make a channel")
            result, _event = self.call(asker_run, "armada_create_channel", arguments)
            self.assertEqual(self.refusal(result), refusal)
        self.assertEqual(Stream.objects.filter(realm=realm).count(), streams_before)

        # An administrator may, for whoever asked them.
        result, _event = self.call(
            self.ask(self.example_user("iago"), "make one"),
            "armada_create_channel",
            {"name": "plans", "private": True},
        )
        self.assertTrue(get_stream("plans", realm).invite_only)

    def test_a_channel_made_in_the_meantime_is_refused(self) -> None:
        run = self.ask(self.hamlet, "make an elsinore channel")
        # Someone else creates it between the name check and the creation:
        # the asker does not end up in their channel.
        self.make_stream("elsinore")
        with patch("zerver.lib.armada_message_tools.check_stream_name_available"):
            result, _event = self.call(run, "armada_create_channel", {"name": "elsinore"})
        self.assertEqual(self.refusal(result), "Channel 'elsinore' already exists")
        self.assertEqual(self.users_subscribed_to_stream("elsinore", self.hamlet.realm), [])

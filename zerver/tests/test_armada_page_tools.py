import json
from typing import Any
from unittest import mock

from zerver.lib.armada_agent import ArmadaAppServerTurnContext, ArmadaCodexAppServerToolRunner
from zerver.lib.armada_page_tools import answer_armada_page_tool
from zerver.lib.armada_pages import create_post
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Post, UserProfile


class PageToolsTest(ZulipTestCase):
    """The agent's tools for personal pages (contract C6): reads with the
    asking person's access, drafts only on their page."""

    def read(self, asker: UserProfile, arguments: object) -> Any:
        answer = answer_armada_page_tool(asker.id, "armada_page_read", arguments)
        self.assertTrue(answer["success"], answer["text"])
        return json.loads(answer["text"])

    def test_reading_follows_the_asking_persons_access(self) -> None:
        hamlet = self.example_user("hamlet")
        iago = self.example_user("iago")
        older = create_post(hamlet, "Older", "First.", draft=False)
        newer = create_post(hamlet, "Newer", "*Second.*", draft=False)
        draft = create_post(hamlet, "Unfinished", "Not yet.", draft=True)

        pages = self.read(iago, {})["pages"]
        self.assertEqual(
            next(page for page in pages if page["user_id"] == hamlet.id),
            {
                "user_id": hamlet.id,
                "full_name": hamlet.full_name,
                "post_count": 2,
                "updated": int(newer.date_updated.timestamp()),
            },
        )

        page = self.read(iago, {"user_id": hamlet.id})
        self.assertEqual(page["full_name"], hamlet.full_name)
        self.assertEqual([post["id"] for post in page["posts"]], [newer.id, older.id])
        # The author's own read sees the draft too.
        page = self.read(hamlet, json.dumps({"user_id": hamlet.id}))
        self.assertEqual([post["id"] for post in page["posts"]], [draft.id, newer.id, older.id])

        post = self.read(iago, {"post_id": newer.id})
        self.assertEqual(post["content"], "*Second.*")
        self.assertEqual(post["rendered"], "<p><em>Second.</em></p>")

        answer = answer_armada_page_tool(iago.id, "armada_page_read", {"post_id": draft.id})
        self.assertEqual(
            answer, {"success": False, "text": "No such post.", "title": "No such post."}
        )

    def test_a_read_says_what_it_read(self) -> None:
        hamlet = self.example_user("hamlet")
        post = create_post(hamlet, "On tests", "Tested.", draft=False)
        self.assertEqual(
            answer_armada_page_tool(hamlet.id, "armada_page_read", {})["title"],
            "Read the list of pages.",
        )
        self.assertEqual(
            answer_armada_page_tool(hamlet.id, "armada_page_read", {"user_id": hamlet.id})["title"],
            f"Read {hamlet.full_name}'s page.",
        )
        self.assertEqual(
            answer_armada_page_tool(hamlet.id, "armada_page_read", {"post_id": post.id})["title"],
            "Read the post “On tests”.",
        )

    def test_a_draft_lands_on_the_asking_persons_page(self) -> None:
        hamlet = self.example_user("hamlet")
        iago = self.example_user("iago")
        answer = answer_armada_page_tool(
            hamlet.id, "armada_page_draft", {"title": "  Notes  ", "content": "**Draft**"}
        )
        post = Post.objects.get(author=hamlet)
        self.assertEqual(
            (post.title, post.content, post.draft, post.drafted_by_agent),
            ("Notes", "**Draft**", True, True),
        )
        self.assertEqual(
            answer,
            {
                "success": True,
                "text": (
                    f"Drafted “Notes” (post {post.id}) on {hamlet.full_name}'s page. It is a "
                    "draft only they see until they publish it. Say in your reply that you "
                    "drafted it, with its title."
                ),
                "title": f"Drafted “Notes” on {hamlet.full_name}'s page.",
            },
        )
        # Nobody else sees it until hamlet publishes it.
        self.assertEqual(self.read(iago, {"user_id": hamlet.id})["posts"], [])

    def test_refusals_are_answers_the_agent_reads(self) -> None:
        hamlet = self.example_user("hamlet")
        bot = self.example_user("default_bot")
        for asker_id, tool, arguments, message in [
            (
                None,
                "armada_page_read",
                {},
                "No person asked for this turn, so it has no page to use.",
            ),
            (
                bot.id,
                "armada_page_draft",
                {"title": "Hi", "content": ""},
                "No person asked for this turn, so it has no page to use.",
            ),
            (hamlet.id, "armada_page_read", "{not json", "The tool's arguments are not JSON."),
            (hamlet.id, "armada_page_read", ["user_id"], "The tool's arguments must be an object."),
            (hamlet.id, "armada_page_read", {"user_id": True}, "user_id must be an integer."),
            (hamlet.id, "armada_page_read", {"post_id": "7"}, "post_id must be an integer."),
            (
                hamlet.id,
                "armada_page_read",
                {"user_id": hamlet.id, "post_id": 1},
                "Give user_id or post_id, not both.",
            ),
            (hamlet.id, "armada_page_read", {"user_id": 99999}, "No such user"),
            (hamlet.id, "armada_page_read", {"user_id": bot.id}, "No such user"),
            (hamlet.id, "armada_page_draft", {"title": "Hi"}, "content must be a string."),
            (
                hamlet.id,
                "armada_page_draft",
                {"title": " ", "content": ""},
                "A post needs a title.",
            ),
        ]:
            self.assertEqual(
                answer_armada_page_tool(asker_id, tool, arguments),
                {"success": False, "text": message, "title": message},
            )
        self.assertFalse(Post.objects.exists())

    def test_a_tool_thread_closes_its_database_connection(self) -> None:
        # Codex's calls are answered on threads of their own, outside any
        # transaction; a test runs inside one, so stand in for the thread's.
        hamlet = self.example_user("hamlet")
        with mock.patch("zerver.lib.armada_page_tools.connection") as connection:
            connection.in_atomic_block = False
            answer_armada_page_tool(hamlet.id, "armada_page_read", {})
        connection.close.assert_called_once_with()


class PageToolCallsTest(ZulipTestCase):
    """The runner registers the page tools and answers Codex's calls of
    them as the person who started the turn."""

    def make_runner(self) -> ArmadaCodexAppServerToolRunner:
        return ArmadaCodexAppServerToolRunner(
            codex_bin="/bin/false",
            workdir=None,
            model="gpt-5.5",
            sandbox="workspace-write",
            approval_policy="on-request",
            timeout_seconds=5,
            output_limit=1000,
        )

    def turn(self, requesting_user_id: int | None) -> ArmadaAppServerTurnContext:
        return ArmadaAppServerTurnContext(
            thread_id="thread-1",
            event_callback=None,
            user_input_provider=None,
            is_main=True,
            requesting_user_id=requesting_user_id,
        )

    def test_a_new_thread_registers_the_page_tools(self) -> None:
        params = self.make_runner().thread_params()
        self.assertEqual(
            [tool["name"] for tool in params["dynamicTools"]],
            [
                "armada_artifact",
                "armada_page_read",
                "armada_page_draft",
                "armada_post",
                "armada_read",
                "armada_start_run",
                "armada_later",
                "armada_send_when",
            ],
        )

    def test_a_call_is_answered_as_the_person_who_started_the_turn(self) -> None:
        hamlet = self.example_user("hamlet")
        result, error, event = self.make_runner().answer_for_server_request(
            "item/tool/call",
            {
                "tool": "armada_page_draft",
                "callId": "call-1",
                "namespace": None,
                "arguments": json.dumps({"title": "Notes", "content": "Hello."}),
            },
            turn_context=self.turn(hamlet.id),
        )
        post = Post.objects.get(author=hamlet, drafted_by_agent=True)
        self.assertIsNone(error)
        assert result is not None
        self.assertTrue(result["success"])
        self.assertIn(f"(post {post.id})", result["contentItems"][0]["text"])
        self.assertEqual(
            event,
            {
                "kind": "tool",
                "status": "done",
                "title": f"Drafted “Notes” on {hamlet.full_name}'s page.",
                "item_id": "call-1",
                "metadata": {"tool_name": "armada_page_draft", "human_tool_result": True},
            },
        )

    def test_a_call_outside_a_turn_has_no_person_to_act_as(self) -> None:
        result, error, event = self.make_runner().answer_for_server_request(
            "item/tool/call", {"tool": "armada_page_read", "arguments": {}}
        )
        self.assertIsNone(error)
        self.assertEqual(
            result,
            {
                "success": False,
                "contentItems": [
                    {
                        "type": "inputText",
                        "text": "No person asked for this turn, so it has no page to use.",
                    }
                ],
            },
        )
        assert event is not None
        self.assertEqual(event["status"], "failed")
        self.assertNotIn("item_id", event)

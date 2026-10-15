from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from unittest import mock

import time_machine

from zerver.lib.armada_pages import draft_post, list_pages, read_page, read_post, update_bio
from zerver.lib.event_schema import check_armada_post_event
from zerver.lib.exceptions import JsonableError, MarkdownRenderingError
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.users import get_user_ids_who_can_access_user
from zerver.models import Post, UserProfile


class PagesTest(ZulipTestCase):
    """Personal pages and their posts (zerver/lib/armada_pages.py)."""

    def write(self, user: UserProfile, title: str, content: str, **extra: Any) -> int:
        self.login_user(user)
        result = self.client_post(
            "/json/armada/posts", {"title": title, "content": content, **extra}
        )
        return self.assert_json_success(result)["id"]

    def page(self, viewer: UserProfile, user: UserProfile) -> dict[str, Any]:
        self.login_user(viewer)
        return self.assert_json_success(self.client_get(f"/json/armada/pages/{user.id}"))

    def post(self, viewer: UserProfile, post_id: int) -> Any:
        self.login_user(viewer)
        return self.client_get(f"/json/armada/posts/{post_id}")

    def post_events(self, events: list[Mapping[str, Any]]) -> list[tuple[str, set[int]]]:
        for event in events:
            # The queue gives each event its id on the way out.
            check_armada_post_event("events[0]", {**event["event"], "id": 0})
        return [(event["event"]["op"], set(event["users"])) for event in events]

    def test_a_post_is_markdown_rendered_as_messages_are(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        moment = datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc)
        with time_machine.travel(moment, tick=False):
            post_id = self.write(
                hamlet, "  On tests  ", "**Tested**, not <script>alert(1)</script>"
            )
        post = self.assert_json_success(self.post(cordelia, post_id))["post"]
        self.assertEqual(
            post,
            {
                "id": post_id,
                "user_id": hamlet.id,
                "title": "On tests",
                "content": "**Tested**, not <script>alert(1)</script>",
                "rendered": "<p><strong>Tested</strong>, not &lt;script&gt;alert(1)&lt;/script&gt;</p>",
                "created": int(moment.timestamp()),
                "updated": int(moment.timestamp()),
                "draft": False,
                "drafted_by_agent": False,
            },
        )

        # The author changes it; the rendering follows the content.
        self.login_user(hamlet)
        self.assert_json_success(
            self.client_patch(f"/json/armada/posts/{post_id}", {"content": "*Edited*"})
        )
        post = self.assert_json_success(self.post(cordelia, post_id))["post"]
        self.assertEqual((post["title"], post["rendered"]), ("On tests", "<p><em>Edited</em></p>"))

    def test_pages_list_people_with_what_the_viewer_can_see(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        with time_machine.travel(datetime(2026, 10, 1, tzinfo=timezone.utc), tick=False):
            self.write(hamlet, "First", "one")
        with time_machine.travel(datetime(2026, 10, 2, tzinfo=timezone.utc), tick=False):
            self.write(hamlet, "Second", "two")
            self.write(hamlet, "Unfinished", "three", draft="true")
        with time_machine.travel(datetime(2026, 10, 3, tzinfo=timezone.utc), tick=False):
            update_bio(cordelia, "Plays the long game.")

        self.login_user(cordelia)
        pages = self.assert_json_success(self.client_get("/json/armada/pages"))["pages"]
        by_person = {page["user_id"]: page for page in pages}
        self.assertEqual(
            by_person[hamlet.id],
            {
                "user_id": hamlet.id,
                "post_count": 2,
                "updated": int(datetime(2026, 10, 2, tzinfo=timezone.utc).timestamp()),
            },
        )
        self.assertEqual(by_person[cordelia.id]["post_count"], 0)
        # Most recently changed first; untouched pages last, with no time.
        self.assertEqual([page["user_id"] for page in pages[:2]], [cordelia.id, hamlet.id])
        self.assertIsNone(pages[-1]["updated"])
        # Bots and deactivated people have no place in the list.
        people = {page["user_id"] for page in pages}
        self.assertNotIn(self.example_user("default_bot").id, people)
        # The author counts their own draft.
        self.assertEqual(
            {page["user_id"]: page["post_count"] for page in list_pages(hamlet)}[hamlet.id], 3
        )

        page = self.page(cordelia, hamlet)
        self.assertEqual(page["bio"], "")
        self.assertEqual([post["title"] for post in page["posts"]], ["Second", "First"])
        self.assertEqual(
            [post["title"] for post in self.page(hamlet, hamlet)["posts"]],
            ["Unfinished", "Second", "First"],
        )
        self.assertEqual(self.page(hamlet, cordelia)["bio"], "Plays the long game.")

    def test_drafts_are_their_authors_alone(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        post_id = self.write(hamlet, "Draft", "not yet", draft="true")
        self.assertTrue(self.assert_json_success(self.post(hamlet, post_id))["post"]["draft"])
        # Someone else gets the same answer as for a post that is not there.
        self.assert_json_error(self.post(cordelia, post_id), "No such post.", status_code=404)
        self.assert_json_error(self.post(cordelia, 10**9), "No such post.", status_code=404)

        # Publishing shows it.
        self.login_user(hamlet)
        self.assert_json_success(
            self.client_patch(f"/json/armada/posts/{post_id}", {"draft": "false"})
        )
        self.assertFalse(self.assert_json_success(self.post(cordelia, post_id))["post"]["draft"])

    def test_only_the_author_writes(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        post_id = self.write(hamlet, "Mine", "words")
        self.login_user(cordelia)
        self.assert_json_error(
            self.client_patch(f"/json/armada/posts/{post_id}", {"title": "Theirs"}),
            "No such post.",
            status_code=404,
        )
        self.assert_json_error(
            self.client_delete(f"/json/armada/posts/{post_id}"), "No such post.", status_code=404
        )
        self.assertEqual(Post.objects.get(id=post_id).title, "Mine")

        self.login_user(hamlet)
        self.assert_json_success(self.client_delete(f"/json/armada/posts/{post_id}"))
        self.assertFalse(Post.objects.filter(id=post_id).exists())
        self.assert_json_error(self.post(hamlet, post_id), "No such post.", status_code=404)

        # Bots have no pages.
        bot = self.example_user("default_bot")
        with self.assertRaisesRegex(JsonableError, "Bots do not have pages."):
            update_bio(bot, "beep")
        with self.assertRaisesRegex(JsonableError, "Bots do not have pages."):
            draft_post(bot, "Title", "beep")
        self.login_user(hamlet)
        self.assert_json_error(self.client_get(f"/json/armada/pages/{bot.id}"), "No such user")
        self.assert_json_error(self.client_get("/json/armada/pages/999999"), "No such user")
        with self.assertRaisesRegex(JsonableError, "No such user"):
            read_page(hamlet, bot)

    def test_the_bio(self) -> None:
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)
        self.assert_json_success(self.client_patch("/json/armada/pages/me", {"bio": "Prince."}))
        self.assertEqual(self.page(hamlet, hamlet)["bio"], "Prince.")
        self.assert_json_success(self.client_patch("/json/armada/pages/me", {"bio": "x" * 280}))
        self.assert_json_error(
            self.client_patch("/json/armada/pages/me", {"bio": "x" * 281}),
            "A bio is at most 280 characters.",
        )
        self.assertEqual(self.page(hamlet, hamlet)["bio"], "x" * 280)

    def test_titles_and_lengths(self) -> None:
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)
        for title, content, error in [
            ("   ", "body", "A post needs a title."),
            ("t" * 201, "body", "A title is at most 200 characters."),
            ("Long", "x" * 50001, "A post is at most 50000 characters."),
        ]:
            self.assert_json_error(
                self.client_post("/json/armada/posts", {"title": title, "content": content}),
                error,
            )
        post_id = self.write(hamlet, "t" * 200, "")
        self.assert_json_error(
            self.client_patch(f"/json/armada/posts/{post_id}", {"title": ""}),
            "A post needs a title.",
        )
        self.assertFalse(Post.objects.filter(title="").exists())

        # Content the renderer cannot take is refused, and nothing is saved.
        with mock.patch(
            "zerver.lib.armada_pages.markdown_convert", side_effect=MarkdownRenderingError
        ):
            self.assert_json_error(
                self.client_post("/json/armada/posts", {"title": "Odd", "content": "odd"}),
                "Unable to render the post.",
            )
        self.assertFalse(Post.objects.filter(title="Odd").exists())

    def test_reading_follows_user_access(self) -> None:
        # Polonius is a guest who cannot see Othello.
        self.set_up_db_for_testing_user_access()
        polonius = self.example_user("polonius")
        othello = self.example_user("othello")
        hamlet = self.example_user("hamlet")
        hidden = self.write(othello, "Hidden", "from guests")
        shown = self.write(hamlet, "Shown", "to guests")
        people = {page["user_id"] for page in list_pages(polonius)}
        self.assertIn(hamlet.id, people)
        self.assertNotIn(othello.id, people)
        self.login_user(polonius)
        self.assert_json_error(
            self.client_get(f"/json/armada/pages/{othello.id}"), "Insufficient permission"
        )
        with self.assertRaisesRegex(JsonableError, "No such user"):
            read_page(polonius, othello)
        self.assert_json_error(self.post(polonius, hidden), "No such post.", status_code=404)
        self.assertEqual(read_post(polonius, shown)["title"], "Shown")

    def test_changes_are_pushed_to_who_may_see_them(self) -> None:
        hamlet = self.example_user("hamlet")
        everyone = set(get_user_ids_who_can_access_user(hamlet))
        others = everyone - {hamlet.id}
        self.login_user(hamlet)

        with self.capture_send_event_calls(expected_num_events=2) as events:
            published = self.write(hamlet, "Out", "now")
        self.assertEqual(self.post_events(events), [("add", {hamlet.id}), ("add", others)])
        self.assertEqual(
            events[0]["event"]["post"],
            {
                "id": published,
                "user_id": hamlet.id,
                "title": "Out",
                "updated": int(Post.objects.get(id=published).date_updated.timestamp()),
                "draft": False,
            },
        )

        with self.capture_send_event_calls(expected_num_events=1) as events:
            drafted = self.write(hamlet, "Later", "maybe", draft="true")
        self.assertEqual(self.post_events(events), [("add", {hamlet.id})])

        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.client_patch(f"/json/armada/posts/{drafted}", {"title": "Still later"})
        self.assertEqual(self.post_events(events), [("update", {hamlet.id})])

        with self.capture_send_event_calls(expected_num_events=2) as events:
            self.client_patch(f"/json/armada/posts/{drafted}", {"draft": "false"})
        self.assertEqual(self.post_events(events), [("update", {hamlet.id}), ("add", others)])

        with self.capture_send_event_calls(expected_num_events=2) as events:
            self.client_patch(f"/json/armada/posts/{published}", {"title": "Out again"})
        self.assertEqual(self.post_events(events), [("update", {hamlet.id}), ("update", others)])

        with self.capture_send_event_calls(expected_num_events=2) as events:
            self.client_patch(f"/json/armada/posts/{published}", {"draft": "true"})
        self.assertEqual(self.post_events(events), [("update", {hamlet.id}), ("remove", others)])

        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.client_delete(f"/json/armada/posts/{published}")
        self.assertEqual(self.post_events(events), [("remove", {hamlet.id})])
        self.assertEqual(events[0]["event"]["post"]["id"], published)

        with self.capture_send_event_calls(expected_num_events=2) as events:
            self.client_delete(f"/json/armada/posts/{drafted}")
        self.assertEqual(self.post_events(events), [("remove", {hamlet.id}), ("remove", others)])

    def test_the_agent_drafts_on_the_askers_page(self) -> None:
        hamlet = self.example_user("hamlet")
        with self.capture_send_event_calls(expected_num_events=1) as events:
            post = draft_post(hamlet, "What we shipped", "- pages\n- posts")
        self.assertEqual(self.post_events(events), [("add", {hamlet.id})])
        self.assertEqual(
            (post.author_id, post.draft, post.drafted_by_agent), (hamlet.id, True, True)
        )
        full = read_post(hamlet, post.id)
        self.assertTrue(full["drafted_by_agent"])
        self.assertIn("<li>pages</li>", full["rendered"])
        with self.assertRaisesRegex(JsonableError, "No such post."):
            read_post(self.example_user("cordelia"), post.id)

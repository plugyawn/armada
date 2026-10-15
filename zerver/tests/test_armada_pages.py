import os
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from importlib import import_module
from io import StringIO
from typing import Any
from unittest import mock

import time_machine
from django.apps import apps
from django.conf import settings
from django.utils.timezone import now as timezone_now

from zerver.actions.message_delete import do_delete_messages
from zerver.actions.uploads import do_delete_old_unclaimed_attachments
from zerver.lib.armada_pages import draft_post, list_pages, read_page, read_post, update_bio
from zerver.lib.event_schema import check_armada_post_event
from zerver.lib.exceptions import JsonableError, MarkdownRenderingError
from zerver.lib.markdown import markdown_convert
from zerver.lib.retention import clean_archived_data, delete_expired_attachments
from zerver.lib.streams import render_stream_description
from zerver.lib.test_classes import UploadSerializeMixin, ZulipTestCase
from zerver.lib.users import get_user_ids_who_can_access_user
from zerver.models import Attachment, Message, Post, UserProfile
from zerver.models.realms import get_realm


class PagesTest(UploadSerializeMixin, ZulipTestCase):
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

    def upload(self, user: UserProfile, *, weeks_ago: int = 0) -> Attachment:
        self.login_user(user)
        file = StringIO("zulip!")
        file.name = "notes.txt"
        with time_machine.travel(timezone_now() - timedelta(weeks=weeks_ago), tick=False):
            result = self.client_post("/json/user_uploads", {"file": file})
        url = self.assert_json_success(result)["url"]
        return Attachment.objects.get(path_id=url.removeprefix("/user_uploads/"))

    def link(self, attachment: Attachment) -> str:
        return f"[{attachment.file_name}](/user_uploads/{attachment.path_id})"

    def reads(self, viewer: UserProfile, attachment: Attachment) -> bool:
        self.login_user(viewer)
        response = self.client_get(f"/user_uploads/{attachment.path_id}")
        if response.status_code == 403:
            return False
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.getvalue(), b"zulip!")
        return True

    def claims(self, post_id: int) -> set[str]:
        return set(Post.objects.get(id=post_id).attachments.values_list("path_id", flat=True))

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

    def test_any_rendering_collects_its_upload_links(self) -> None:
        # The renderer collects the upload a link names whatever it
        # renders, not only a message, so that a post sees its own; a
        # channel description, which claims nothing, renders as before.
        realm = get_realm("zulip")
        path_id = f"{realm.id}/ff/notes.txt"
        description = (
            f"[here](http://{realm.host}/user_uploads/{path_id}), [there](/user_uploads/{path_id})"
        )
        self.assertEqual(
            render_stream_description(description, realm),
            f'<p><a href="user_uploads/{path_id}">here</a>, <a href="/user_uploads/{path_id}">there</a></p>',
        )
        rendering = markdown_convert(description, message_realm=realm, no_previews=True)
        self.assertEqual(rendering.potential_attachment_path_ids, [path_id, path_id])

    def test_a_posts_readers_read_its_uploads(self) -> None:
        # Polonius is a guest who cannot see Othello.
        self.set_up_db_for_testing_user_access()
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        othello = self.example_user("othello")
        polonius = self.example_user("polonius")

        shown = self.upload(hamlet)
        shown_post = self.write(hamlet, "Shown", f"Look: {self.link(shown)}")
        self.assertEqual(self.claims(shown_post), {shown.path_id})
        self.assertTrue(self.reads(cordelia, shown))
        self.assertTrue(self.reads(polonius, shown))

        hidden = self.upload(othello)
        self.write(othello, "Hidden", self.link(hidden))
        self.assertTrue(self.reads(cordelia, hidden))
        self.assertFalse(self.reads(polonius, hidden))

        # A draft's uploads are its author's alone, until it is published.
        drafted = self.upload(hamlet)
        draft_id = self.write(hamlet, "Draft", self.link(drafted), draft="true")
        self.assertEqual(self.claims(draft_id), {drafted.path_id})
        self.assertTrue(self.reads(hamlet, drafted))
        self.assertFalse(self.reads(cordelia, drafted))
        self.login_user(hamlet)
        self.assert_json_success(
            self.client_patch(f"/json/armada/posts/{draft_id}", {"draft": "false"})
        )
        self.assertTrue(self.reads(cordelia, drafted))
        self.login_user(hamlet)
        self.assert_json_success(
            self.client_patch(f"/json/armada/posts/{draft_id}", {"draft": "true"})
        )
        self.assertFalse(self.reads(cordelia, drafted))

        # An upload also sent to a private channel: the message does
        # not open it to Cordelia, the post does.
        self.make_stream("private", invite_only=True)
        self.subscribe(hamlet, "private")
        in_both = self.upload(hamlet)
        self.send_stream_message(hamlet, "private", self.link(in_both))
        self.assertFalse(self.reads(cordelia, in_both))
        self.write(hamlet, "Both", self.link(in_both))
        self.assertTrue(self.reads(cordelia, in_both))

    def test_naming_an_upload_does_not_claim_it(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        othello = self.example_user("othello")

        # Hamlet's upload in no post, and his upload in a draft: Cordelia
        # may read neither, so her post that names them claims neither.
        unposted = self.upload(hamlet)
        drafted = self.upload(hamlet)
        self.write(hamlet, "Draft", self.link(drafted), draft="true")
        with self.assertLogs(level="WARNING") as logs:
            post_id = self.write(
                cordelia, "Borrowed", f"{self.link(unposted)} {self.link(drafted)}"
            )
        self.assertEqual(
            logs.output,
            [
                f"WARNING:root:User {cordelia.id} tried to claim upload {path_id} in post {post_id}, but lacks permission"
                for path_id in sorted([unposted.path_id, drafted.path_id])
            ],
        )
        self.assertEqual(self.claims(post_id), set())
        for attachment in (unposted, drafted):
            self.assertFalse(self.reads(cordelia, attachment))
            self.assertFalse(self.reads(othello, attachment))

        # Editing the post to name them again changes nothing.
        self.login_user(cordelia)
        with self.assertLogs(level="WARNING"):
            result = self.client_patch(
                f"/json/armada/posts/{post_id}", {"content": f"Again: {self.link(unposted)}"}
            )
        self.assert_json_success(result)
        self.assertEqual(self.claims(post_id), set())
        self.assertFalse(self.reads(othello, unposted))

        # An upload she may read is hers to show, as in a message.
        shared = self.upload(hamlet)
        self.send_personal_message(hamlet, cordelia, self.link(shared))
        self.assertFalse(self.reads(othello, shared))
        shown_post = self.write(cordelia, "Shown", self.link(shared))
        self.assertEqual(self.claims(shown_post), {shared.path_id})
        self.assertTrue(self.reads(othello, shared))

    def test_an_edit_releases_the_uploads_it_drops(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        first = self.upload(hamlet)
        second = self.upload(hamlet)
        post_id = self.write(hamlet, "Post", self.link(first))
        self.assertEqual(self.claims(post_id), {first.path_id})

        self.login_user(hamlet)
        self.assert_json_success(
            self.client_patch(f"/json/armada/posts/{post_id}", {"content": self.link(second)})
        )
        self.assertEqual(self.claims(post_id), {second.path_id})
        self.assertFalse(self.reads(cordelia, first))
        self.assertTrue(self.reads(cordelia, second))

        # An edit that leaves the content alone leaves the claims alone.
        self.login_user(hamlet)
        self.assert_json_success(
            self.client_patch(f"/json/armada/posts/{post_id}", {"title": "Renamed"})
        )
        self.assertEqual(self.claims(post_id), {second.path_id})

        # Deleting the post releases everything it held.
        self.login_user(hamlet)
        self.assert_json_success(self.client_delete(f"/json/armada/posts/{post_id}"))
        self.assertFalse(Attachment.objects.get(id=second.id).armada_posts.exists())
        self.assertFalse(self.reads(cordelia, second))

    def test_a_posts_uploads_are_not_unclaimed(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        kept = self.upload(hamlet, weeks_ago=2)
        released = self.upload(hamlet, weeks_ago=2)
        post_id = self.write(hamlet, "Post", f"{self.link(kept)} {self.link(released)}")
        self.login_user(hamlet)
        self.assert_json_success(
            self.client_patch(f"/json/armada/posts/{post_id}", {"content": self.link(kept)})
        )

        def stored(attachment: Attachment) -> bool:
            assert settings.LOCAL_FILES_DIR
            on_disk = os.path.isfile(os.path.join(settings.LOCAL_FILES_DIR, attachment.path_id))
            self.assertEqual(Attachment.objects.filter(id=attachment.id).exists(), on_disk)
            return on_disk

        do_delete_old_unclaimed_attachments(1)
        self.assertTrue(stored(kept))
        self.assertFalse(stored(released))

        # A message that shared the post's upload is deleted, and its
        # archive purged, without taking the upload from the post.
        self.subscribe(hamlet, "Denmark")
        message_id = self.send_stream_message(hamlet, "Denmark", self.link(kept))
        do_delete_messages(hamlet.realm, [Message.objects.get(id=message_id)], acting_user=None)
        delete_expired_attachments(hamlet.realm)
        with self.settings(ARCHIVED_DATA_VACUUMING_DELAY_DAYS=0):
            clean_archived_data()
        do_delete_old_unclaimed_attachments(1)
        self.assertTrue(stored(kept))
        self.assertEqual(self.claims(post_id), {kept.path_id})
        self.assertTrue(self.reads(cordelia, kept))

        # Dropping it from an edited message does not offer to delete it.
        message_id = self.send_stream_message(hamlet, "Denmark", self.link(kept))
        self.login_user(hamlet)
        result = self.client_patch(f"/json/messages/{message_id}", {"content": "Gone."})
        self.assertEqual(self.assert_json_success(result)["detached_uploads"], [])

    def test_posts_from_before_claims_claim_their_authors_uploads(self) -> None:
        # Migrations are not in the coverage report; the backfill is
        # tested here on the shapes a post's links take.
        backfill = import_module("zerver.migrations.0817_claim_uploads_of_existing_posts")
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        own = self.upload(hamlet)
        linked_in_full = self.upload(hamlet)
        theirs = self.upload(cordelia)
        # Posts as they were written before posts claimed their uploads.
        old_post = Post.objects.create(
            realm_id=hamlet.realm_id,
            author=hamlet,
            title="Old",
            content=(
                f"{self.link(own)}, http://{hamlet.realm.host}/user_uploads/{linked_in_full.path_id}"
                f" and {self.link(theirs)}"
            ),
            rendered_content="",
        )
        plain_post = Post.objects.create(
            realm_id=hamlet.realm_id,
            author=hamlet,
            title="Plain",
            content="Words.",
            rendered_content="",
        )

        # Run again, the backfill changes nothing; it uses no schema
        # editor.
        for _ in range(2):
            backfill.claim_uploads_of_existing_posts(apps, None)
            self.assertEqual(self.claims(old_post.id), {own.path_id, linked_in_full.path_id})
            self.assertEqual(self.claims(plain_post.id), set())
        # Only the author's own uploads: Cordelia's waits for an edit,
        # which checks whether Hamlet may read it.
        self.assertTrue(self.reads(self.example_user("othello"), own))
        self.assertFalse(self.reads(self.example_user("othello"), theirs))

# INTERIM BETA: deleted with zerver/views/armada_beta.py and
# web/src/armada/beta/ when the rebuild's conversation lands.
import importlib
import json
import os
import re
import struct
import tempfile
import zlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any
from unittest import mock

import orjson
import time_machine
from django.apps import apps as django_apps
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.actions.create_user import do_create_user
from zerver.actions.realm_settings import (
    do_change_realm_permission_group_setting,
    do_set_realm_property,
)
from zerver.actions.users import do_deactivate_user
from zerver.lib.armada import ARMADA_QUESTION_TIMEOUT_SECONDS
from zerver.lib.armada_gate import invalidate_armada_selected_bot_cache
from zerver.lib.armada_worker import ArmadaDatabaseWorkerRunStore
from zerver.lib.event_schema import check_armada_partner_event, check_armada_partner_me_event
from zerver.lib.events import fetch_initial_state_data
from zerver.lib.exceptions import JsonableError
from zerver.lib.initial_password import initial_password
from zerver.lib.partner import (
    PARTNER_FOLD_SECONDS,
    armada_sprite_dir,
    choose_partner,
    fold_partner,
    fold_partner_after_run,
    get_row,
    invalidate_agent_evolution,
    level_for_xp,
    load_pack,
    partner_fold,
    save_row,
    stone_for_species,
    user_zone,
)
from zerver.lib.rate_limiter import RateLimitedUser
from zerver.lib.templates import font_preloads
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import ratelimit_rule
from zerver.lib.users import get_user_ids_who_can_access_user
from zerver.models import (
    AgentRun,
    BotStorageData,
    Message,
    NamedUserGroup,
    Partner,
    Reaction,
    UserProfile,
)
from zerver.models.bots import Service
from zerver.models.groups import SystemGroups
from zerver.tornado.django_api import EventQueueData
from zerver.views.armada_beta import live_run_ids_for_user


def tiny_png(width: int = 3, height: int = 2) -> bytes:
    """A real PNG made here, never a copied sprite: one opaque pixel in a
    transparent canvas."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    rows = b"".join(
        b"\x00"
        + b"".join(b"\xff\x00\x00\xff" if (x, y) == (1, 1) else b"\x00" * 4 for x in range(width))
        for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def species(
    num: int,
    types: list[str],
    *,
    prevo: str | None = None,
    evos: list[str] | None = None,
    line: int | None = None,
    drawn: bool = True,
    shiny: bool = True,
) -> dict[str, Any]:
    """A made-up species for the tests' pack: the names are not Pokémon."""
    sprite = {
        "regular": f"made/{num}.png",
        "shiny": f"made/{num}-shiny.png" if shiny else None,
        "size": [68, 56],
        "box": [24, 34, 20, 19],
        "shiny_box": [24, 34, 20, 19] if shiny else None,
    }
    return {
        "num": num,
        "name": f"Creature {num}",
        "form": "",
        "gen": 1,
        "types": types,
        "prevo": prevo,
        "evos": evos or [],
        "how": None,
        "line": line or num,
        "first": prevo is None,
        "sprite": sprite if drawn else None,
    }


# Three-form line 1 > 2 > 3; a branch 10 > 11 | 12; 20 never evolves; 30
# evolves through a form with no sprite (31) into 32; 50 has no shiny;
# 95 > 208 is the agent's line.
PACK = {
    "version": 1,
    "count": 14,
    "starters": ["1", "10"],
    "species": {
        "1": species(1, ["Grass", "Poison"], evos=["2"]),
        "2": species(2, ["Grass"], prevo="1", evos=["3"], line=1),
        "3": species(3, ["Fire"], prevo="2", line=1),
        "10": species(10, ["Normal"], evos=["11", "12"]),
        "11": species(11, ["Water"], prevo="10", line=10),
        "12": species(12, ["Electric"], prevo="10", line=10),
        "20": species(20, ["Fire"]),
        "30": species(30, ["Bug"], evos=["31"]),
        "31": species(31, ["Bug"], prevo="30", evos=["32"], line=30, drawn=False),
        "32": species(32, ["Bug"], prevo="31", line=30),
        "50": species(50, ["Normal", "Flying"], shiny=False),
        "60": species(60, ["Ice"], evos=["61"]),
        "95": species(95, ["Rock", "Ground"], evos=["208"]),
        "208": species(208, ["Steel", "Ground"], prevo="95", line=95),
    },
}


class ArmadaBetaTest(ZulipTestCase):
    def test_signed_out_is_sent_to_sign_in(self) -> None:
        result = self.client_get("/beta/")
        self.assertEqual(result.status_code, 302)
        self.assertIn("/login/", result["Location"])

    def test_signed_in_gets_the_page_with_session_csrf_and_no_key(self) -> None:
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)
        result = self.client_get("/beta/")
        self.assertEqual(result.status_code, 200)
        html = result.content.decode()
        self.assertIn('name="csrfmiddlewaretoken"', html)
        self.assertIn("armada-beta-stubentry.js", html)
        self.assertIn('id="col"', html)
        # The page is told how long a run waits for an answer, so that a
        # question the server has given up on stops reading as waiting.
        self.assertIn(f'data-question-timeout="{ARMADA_QUESTION_TIMEOUT_SECONDS}"', html)
        self.assertIn('data-live-run-ids="[]"', html)
        self.assertIn('data-read-receipts="on"', html)
        # The page signs in by the session alone: no API key is ever
        # written into it.
        self.assertNotIn(hamlet.api_key, html)

    def get_old_interface(self, url: str) -> str:
        queue_data = EventQueueData(queue_id="test-queue-id", idle_queue_timeout_secs=600)
        with (
            mock.patch("zerver.lib.events.request_event_queue", return_value=queue_data),
            mock.patch("zerver.lib.events.get_user_events", return_value=[]),
        ):
            return self.client_get(url).content.decode()

    @override_settings(ARMADA_NEW_INTERFACE_AT_ROOT=True)
    def test_root_serves_the_new_interface_and_the_old_one_moves(self) -> None:
        # Signed out, the old interface's address sends you to sign in,
        # and / never serves the new interface (this realm is web-public,
        # so / shows its spectator view).
        result = self.client_get("/legacy/")
        self.assertEqual(result.status_code, 302)
        self.assertIn("/login/", result["Location"])
        self.assertNotIn('id="beta-data"', self.get_old_interface("/"))

        self.login("hamlet")
        html = self.client_get("/").content.decode()
        self.assertIn('id="beta-data"', html)
        self.assertIn("<title>Armada</title>", html)
        self.assertNotIn('id="app-loading"', html)
        # Nothing in the new interface links to the old one.
        self.assertNotIn("/legacy/", html)
        self.assertNotIn("old interface", html.lower())

        html = self.get_old_interface("/legacy/")
        self.assertIn('id="app-loading"', html)
        self.assertNotIn('id="beta-data"', html)

        # The terms of service still come first.
        with self.settings(TERMS_OF_SERVICE_VERSION="99.99"):
            html = self.client_get("/").content.decode()
        self.assertIn("Accept the Terms of Service", html)
        self.assertNotIn('id="beta-data"', html)

    def test_root_serves_the_old_interface_when_turned_off(self) -> None:
        self.assertFalse(settings.ARMADA_NEW_INTERFACE_AT_ROOT)
        self.login("hamlet")
        html = self.get_old_interface("/")
        self.assertIn('id="app-loading"', html)
        self.assertNotIn('id="beta-data"', html)

    def make_run(
        self,
        requester: UserProfile,
        stream_name: str,
        *,
        status: str = AgentRun.RUNNING,
        session_kind: str = AgentRun.SHARED,
        minutes_ago: int = 0,
    ) -> AgentRun:
        trigger_message_id = self.send_stream_message(requester, stream_name, "a request")
        return AgentRun.objects.create(
            realm=requester.realm,
            bot_profile=self.example_user("default_bot"),
            requesting_user=requester,
            trigger_message_id=trigger_message_id,
            stream_name=stream_name,
            topic_name="test",
            session_kind=session_kind,
            status=status,
            date_created=timezone_now() - timedelta(minutes=minutes_ago),
        )

    def test_live_runs_are_the_live_shared_runs_in_my_channels(self) -> None:
        hamlet = self.example_user("hamlet")
        othello = self.example_user("othello")
        self.subscribe(hamlet, "Denmark")
        self.subscribe(othello, "Denmark")
        self.make_stream("hamlet-not-here")
        self.subscribe(othello, "hamlet-not-here")

        older = self.make_run(othello, "Denmark", minutes_ago=5)
        queued = self.make_run(othello, "Denmark", status=AgentRun.QUEUED)
        self.make_run(othello, "Denmark", status=AgentRun.DONE)
        self.make_run(othello, "Denmark", status=AgentRun.FAILED)
        self.make_run(othello, "Denmark", session_kind=AgentRun.SIDE)
        self.make_run(othello, "hamlet-not-here")

        self.assertEqual(live_run_ids_for_user(hamlet), [queued.id, older.id])

        # What the page is given is what armada/runs then lets this person
        # read, in one request.
        self.login_user(hamlet)
        html = self.client_get("/beta/").content.decode()
        self.assertIn(f'data-live-run-ids="[{queued.id},{older.id}]"', html)
        result = self.client_get(
            "/json/armada/runs", {"run_ids": orjson.dumps([queued.id, older.id]).decode()}
        )
        runs = self.assert_json_success(result)["armada_runs"]
        self.assertEqual({run["id"] for run in runs}, {queued.id, older.id})

        with mock.patch("zerver.views.armada_beta.ARMADA_BETA_LIVE_RUN_LIMIT", 1):
            self.assertEqual(live_run_ids_for_user(hamlet), [queued.id])

    def test_the_beta_preloads_the_faces_the_app_does(self) -> None:
        # The beta's page is drawn in Söhne, like the app's, so a first
        # paint never waits on it.
        stats = {
            "status": "done",
            "assets": {
                f"files/{name}": {"name": f"files/{name}", "sourceFilename": source}
                for name, source in [
                    ("soehne-buch.woff2", "../fonts/licensed/soehne-buch.woff2"),
                    ("GeistMonoVF.woff2", "fonts/armada/GeistMonoVF.woff2"),
                    ("HankenGrotesk-VF.woff2", "fonts/armada/HankenGrotesk-VF.woff2"),
                ]
            },
        }
        with mock.patch("zerver.lib.templates.webpack_stats", return_value=stats):
            beta = font_preloads("armada-beta")
            self.assertEqual(beta, font_preloads("app"))
        self.assertEqual(
            [url.rsplit("/", 1)[-1] for url in beta], ["soehne-buch.woff2", "GeistMonoVF.woff2"]
        )

    def beta_data(self, html: str) -> dict[str, str]:
        tag = re.search(r'<div id="beta-data"[^>]*>', html)
        assert tag is not None
        return {
            name: unescape(value) for name, value in re.findall(r'data-([a-z-]+)="([^"]*)"', tag[0])
        }

    def test_the_page_brings_what_the_page_does_not_register_for(self) -> None:
        # The page registers without the realm bundle and the realm's bots
        # (web/src/armada/beta/connection.js): the agent's user ids, the
        # rules for editing and deleting and the presence intervals come
        # with it, written as register writes them, a named group by its id
        # and an anonymous one by its members and subgroups.
        hamlet = self.example_user("hamlet")
        realm = hamlet.realm
        do_set_realm_property(realm, "message_content_delete_limit_seconds", 600, acting_user=None)
        moderators = NamedUserGroup.objects.get(
            name=SystemGroups.MODERATORS, realm_for_sharding=realm, is_system_group=True
        )
        do_change_realm_permission_group_setting(
            realm,
            "can_delete_own_message_group",
            self.create_or_update_anonymous_group_for_setting(
                [self.example_user("cordelia"), hamlet], [moderators]
            ),
            acting_user=None,
        )
        state = fetch_initial_state_data(hamlet, realm=realm, event_types=["realm", "realm_bot"])

        self.login_user(hamlet)
        data = self.beta_data(self.client_get("/beta/").content.decode())
        self.assertEqual(orjson.loads(data["agent-user-ids"]), state["agent_user_ids"])
        rules = orjson.loads(data["edit-and-delete"])
        self.assertEqual(
            rules,
            {
                name: state["realm_" + name]
                for name in [
                    "allow_message_editing",
                    "message_content_edit_limit_seconds",
                    "message_content_delete_limit_seconds",
                    "can_delete_any_message_group",
                    "can_delete_own_message_group",
                ]
            },
        )
        self.assertEqual(rules["message_content_delete_limit_seconds"], 600)
        self.assertEqual(
            rules["can_delete_own_message_group"],
            {
                "direct_members": sorted([self.example_user("cordelia").id, hamlet.id]),
                "direct_subgroups": [moderators.id],
            },
        )
        self.assertIsInstance(rules["can_delete_any_message_group"], int)
        self.assertEqual(int(data["presence-ping"]), state["server_presence_ping_interval_seconds"])
        self.assertEqual(
            int(data["presence-offline"]), state["server_presence_offline_threshold_seconds"]
        )

    def test_read_receipts_off_is_written_into_the_page(self) -> None:
        hamlet = self.example_user("hamlet")
        hamlet.realm.enable_read_receipts = False
        hamlet.realm.save(update_fields=["enable_read_receipts"])
        self.login_user(hamlet)
        html = self.client_get("/beta/").content.decode()
        self.assertIn('data-read-receipts="off"', html)

    def test_the_page_names_the_partner_pack_version(self) -> None:
        self.login("hamlet")
        with self.sprite_dir() as sprites:
            html = self.client_get("/beta/").content.decode()
            version = str(int(os.stat(os.path.join(sprites, "species.json")).st_mtime))
            self.assertIn(f'data-sprite-version="{version}"', html)
        with self.sprite_dir(pack=False):
            html = self.client_get("/beta/").content.decode()
            self.assertIn('data-sprite-version=""', html)

    @contextmanager
    def sprite_dir(self, *, pack: bool = True) -> Iterator[str]:
        with tempfile.TemporaryDirectory() as sprites:
            os.makedirs(os.path.join(sprites, "marks"))
            os.makedirs(os.path.join(sprites, "made"))
            # A themed mark the beta no longer reads: served to no one.
            with open(os.path.join(sprites, "marks", "ball.svg"), "w") as f:
                f.write('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
            with open(os.path.join(sprites, "made", "1.png"), "wb") as f:
                f.write(tiny_png())
            with open(os.path.join(sprites, "notes.txt"), "w") as f:
                f.write("not served")
            if pack:
                with open(os.path.join(sprites, "species.json"), "w") as f:
                    json.dump(PACK, f)
            with mock.patch.dict(os.environ, {"ARMADA_SPRITE_DIR": sprites}):
                yield sprites

    def test_sprites_do_not_spend_the_api_budget(self) -> None:
        # A picker's first screen asks for a hundred sprites at once; they
        # must not leave the page's own requests rate limited.
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)
        RateLimitedUser(hamlet).clear_history()
        with self.sprite_dir(), ratelimit_rule(60, 3, domain="api_by_user"):
            for _ in range(5):
                self.assertEqual(self.client_get("/beta/sprites/made/1.png").status_code, 200)
            statuses = [self.client_get("/beta/").status_code for _ in range(4)]
        self.assertEqual(statuses, [200, 200, 200, 429])
        RateLimitedUser(hamlet).clear_history()

    def test_sprites_are_served_to_signed_in_people_only(self) -> None:
        with self.sprite_dir() as sprites:
            # Signed out: sent to sign in, and no file is in the answer.
            for path in ["made/1.png", "species.json"]:
                result = self.client_get(f"/beta/sprites/{path}")
                self.assertEqual(result.status_code, 302)
                self.assertIn("/login/", result["Location"])

            self.login("hamlet")
            result = self.client_get("/beta/sprites/made/1.png")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result["Content-Type"], "image/png")
            self.assertEqual(result["Cache-Control"], "private, max-age=31536000, immutable")
            self.assertEqual(result["X-Content-Type-Options"], "nosniff")
            self.assertEqual(result.content, tiny_png())

            result = self.client_get("/beta/sprites/species.json")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result["Content-Type"], "application/json")
            self.assertEqual(result["Cache-Control"], "private, max-age=31536000, immutable")
            self.assertEqual(orjson.loads(result.content)["species"]["1"]["name"], "Creature 1")

            # A file that is not on this server, a file of another kind (the
            # themed marks the beta once read among them), a directory, and
            # every way out of the directory (a link included): none is
            # served.
            os.symlink("/etc/hostname", os.path.join(sprites, "made", "out.png"))
            for path in [
                "made/2.png",
                "marks/ball.svg",
                "marks/egg.svg",
                "notes.txt",
                "made",
                "made/out.png",
                "..%2Fsecret.png",
                "made/..%2F..%2Fsecret.png",
                "%2e%2e/secret.png",
                # Paths the file system refuses: a NUL byte, a file used as
                # a directory, a name longer than any file's.
                "species.json%00.png",
                "made/..%00/notes.txt",
                "made/1.png/2.png",
                "made/" + "x" * 300 + ".png",
            ]:
                self.assertEqual(self.client_get(f"/beta/sprites/{path}").status_code, 404, path)
            self.assertEqual(self.client_post("/beta/sprites/made/1.png").status_code, 405)

        # A directory that is there but holds nothing: no pack, so the page
        # draws initials, and every file is a 404.
        with tempfile.TemporaryDirectory() as empty:
            with mock.patch.dict(os.environ, {"ARMADA_SPRITE_DIR": empty}):
                self.assertIn('data-sprite-version=""', self.client_get("/beta/").content.decode())
                for path in ["species.json", "made/1.png"]:
                    self.assertEqual(self.client_get(f"/beta/sprites/{path}").status_code, 404)

        # With no sprite directory at all, every file is a 404.
        with mock.patch.dict(os.environ, {"ARMADA_SPRITE_DIR": "/nonexistent/sprites"}):
            self.assertEqual(self.client_get("/beta/sprites/made/1.png").status_code, 404)
            self.assertEqual(self.client_get("/beta/sprites/species.json").status_code, 404)

        # Without ARMADA_SPRITE_DIR the directory is the checkout's git-ignored one.
        with mock.patch.dict(os.environ, {"ARMADA_SPRITE_DIR": ""}):
            self.assertEqual(
                armada_sprite_dir(), os.path.join(settings.DEPLOY_ROOT, "web/sprites/licensed")
            )


class PartnerTest(ZulipTestCase):
    """Partners on the server: the pack, choosing, experience and levels,
    evolution, and the agent's own partner (zerver/lib/partner.py)."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.realm = self.example_user("iago").realm
        invalidate_armada_selected_bot_cache(self.realm.id)
        self.agent = do_create_user(
            "armada-agent-bot@zulip.testserver",
            None,
            self.realm,
            "Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            bot_owner=self.example_user("iago"),
            acting_user=None,
        )
        Service.objects.create(name="armada", user_profile=self.agent, base_url="", token="")
        invalidate_armada_selected_bot_cache(self.realm.id)
        self.sprites = tempfile.TemporaryDirectory()
        with open(os.path.join(self.sprites.name, "species.json"), "w") as f:
            json.dump(PACK, f)
        patcher = mock.patch.dict(os.environ, {"ARMADA_SPRITE_DIR": self.sprites.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.sprites.cleanup)
        self.addCleanup(invalidate_armada_selected_bot_cache, self.realm.id)

    def new_person(self, name: str, tz: str = "") -> UserProfile:
        return do_create_user(
            f"{name}@zulip.testserver",
            initial_password(f"{name}@zulip.testserver"),
            self.realm,
            name.title(),
            timezone=tz,
            acting_user=None,
        )

    def partners(self, user: UserProfile) -> dict[str, Any]:
        self.login_user(user)
        return self.assert_json_success(self.client_get("/json/armada/partners"))

    def choose(self, user: UserProfile, species_id: str, **extra: Any) -> Any:
        self.login_user(user)
        return self.client_post("/json/armada/partner", {"species": species_id, **extra})

    def set_xp(self, user: UserProfile, xp: int) -> None:
        row = get_row(user)
        row.update(xp=xp, folded_at=timezone_now().timestamp())
        save_row(user, row)

    def send_at(self, user: UserProfile, moment: datetime, n: int = 1) -> list[int]:
        ids = [self.send_stream_message(user, "Verona", f"message {i}") for i in range(n)]
        Message.objects.filter(id__in=ids).update(date_sent=moment)
        return ids

    def finish_run(self, user: UserProfile, moment: datetime, status: str) -> None:
        trigger = self.send_stream_message(user, "Verona", "a request")
        Message.objects.filter(id=trigger).update(date_sent=moment)
        # As the worker does when a run finishes.
        invalidate_agent_evolution(self.realm.id)
        AgentRun.objects.create(
            realm=self.realm,
            bot_profile=self.agent,
            requesting_user=user,
            trigger_message_id=trigger,
            stream_name="Verona",
            topic_name="test",
            status=status,
            date_created=moment,
            date_finished=moment,
        )

    def test_levels_and_stones(self) -> None:
        self.assertEqual(level_for_xp(0), 1)
        self.assertEqual(level_for_xp(1023), 15)
        self.assertEqual(level_for_xp(1024), 16)
        self.assertEqual(level_for_xp(3600), 30)
        self.assertEqual(level_for_xp(10**9), 100)
        pack = load_pack()
        assert pack is not None
        self.assertEqual(stone_for_species(pack, "1"), "moss")
        # Normal yields to a second type: Normal and Flying is lapis.
        self.assertEqual(stone_for_species(pack, "50"), "lapis")
        self.assertEqual(stone_for_species(pack, "10"), "flint")
        self.assertEqual(stone_for_species(pack, "12"), "citrine")
        # An evolution the pack does not have is not offered.
        self.assertEqual(pack.next_forms("60"), [])
        self.assertEqual(pack.next_forms("30"), ["32"])
        # A time zone the server does not know counts days in UTC.
        nowhere = self.example_user("hamlet")
        nowhere.timezone = "Not/AZone"
        self.assertEqual(user_zone(nowhere), timezone.utc)
        # The pack is read again only when its file changes.
        self.assertIs(load_pack(), pack)
        os.utime(os.path.join(self.sprites.name, "species.json"), (1, 1))
        self.assertIsNot(load_pack(), pack)

    def test_off_without_a_pack_or_an_agent(self) -> None:
        cordelia = self.example_user("cordelia")
        # The sprite directory is there, and now empty.
        os.remove(os.path.join(self.sprites.name, "species.json"))
        self.assertEqual(os.listdir(self.sprites.name), [])
        self.assertEqual(self.partners(cordelia), {"result": "success", "msg": "", "on": False})
        self.assert_json_error(
            self.choose(cordelia, "1"), "Partners are not set up on this server."
        )

    def test_choosing_a_partner(self) -> None:
        # New people: their history is empty, so they arrive as they choose.
        cordelia = self.new_person("cordelia2")
        othello = self.new_person("othello2")
        state = self.partners(cordelia)
        self.assertTrue(state["on"])
        self.assertEqual(
            state["agent"], {"user_id": self.agent.id, "species": "95", "shiny": False}
        )
        self.assertIsNone(state["me"]["species"])
        self.assertEqual(state["partners"], {})

        # Only a first form the pack draws, never the agent's line.
        self.assert_json_error(
            self.choose(cordelia, "2"),
            "Choose the first form of a line; your partner grows into the rest.",
        )
        self.assert_json_error(self.choose(cordelia, "999"), "There is no such partner.")
        self.assert_json_error(self.choose(cordelia, "31"), "There is no such partner.")
        self.assert_json_error(self.choose(cordelia, "95"), "This line is Onix's.")

        state = self.assert_json_success(self.choose(cordelia, "1"))
        self.assertEqual(state["me"]["species"], "1")
        self.assertEqual(state["me"]["chosen"], "1")
        self.assertEqual(state["partners"][str(cordelia.id)]["stone"], "moss")

        # One person a line: the line's other forms are taken too.
        self.assert_json_error(self.choose(othello, "1"), "Cordelia2 has this line.")
        # Changing releases the old line at once.
        self.assert_json_success(self.choose(cordelia, "20"))
        self.assert_json_success(self.choose(othello, "1"))

        # A line held by a deactivated person is free, and their row is cleared.
        do_deactivate_user(othello, acting_user=None)
        self.assertNotIn(str(othello.id), self.partners(cordelia)["partners"])
        self.assert_json_success(self.choose(cordelia, "1"))
        self.assertIsNone(get_row(othello)["species"])

        # Bots have no partners.
        with self.assertRaisesRegex(JsonableError, "Bots do not have partners."):
            choose_partner(self.agent, "10", [])

    def test_a_veteran_arrives_in_the_form_their_level_allows(self) -> None:
        iago = self.new_person("iago2")
        hamlet = self.new_person("hamlet2")
        self.set_xp(iago, 3600)
        state = self.assert_json_success(self.choose(iago, "1"))
        self.assertEqual(state["me"]["species"], "3")
        self.assertFalse(state["me"]["shiny"])
        # A branch on the way: the pick decides; with no pick it stops there.
        self.set_xp(hamlet, 1024)
        self.assertEqual(self.assert_json_success(self.choose(hamlet, "10"))["me"]["species"], "10")
        state = self.assert_json_success(
            self.choose(hamlet, "10", into=orjson.dumps(["12"]).decode())
        )
        self.assertEqual(state["me"]["species"], "12")
        self.assertIsNone(state["me"]["ready"])
        # A form with no sprite is passed over to the next one the pack draws.
        self.assertEqual(self.assert_json_success(self.choose(hamlet, "30"))["me"]["species"], "32")
        # Shiny on arrival at level 50.
        self.set_xp(hamlet, 10000)
        state = self.assert_json_success(self.choose(hamlet, "20"))
        self.assertEqual((state["me"]["species"], state["me"]["shiny"]), ("20", True))

    def test_later_is_remembered(self) -> None:
        cordelia = self.new_person("cordelia2")
        self.login_user(cordelia)
        state = self.assert_json_success(
            self.client_post("/json/armada/partner", {"later": "true"})
        )
        self.assertTrue(state["me"]["later"])
        self.assertTrue(self.partners(cordelia)["me"]["later"])
        # Choosing clears it; a post with neither changes nothing.
        self.assert_json_success(self.choose(cordelia, "1"))
        state = self.assert_json_success(self.client_post("/json/armada/partner", {}))
        self.assertFalse(state["me"]["later"])
        self.assertEqual(state["me"]["species"], "1")

    def test_experience_counts_use_with_the_daily_caps(self) -> None:
        aaron = self.new_person("aaronpartner", tz="Asia/Kolkata")
        hamlet = self.example_user("hamlet")
        monday = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)  # 11:30 in Kolkata
        tuesday = monday + timedelta(days=1)
        # Monday: 12 messages, a done run (its trigger is a 13th message), a
        # cancelled run (a 14th), a failed one (a 15th): 25 + 15 + 5 + 5.
        self.send_at(aaron, monday, 12)
        self.finish_run(aaron, monday, AgentRun.DONE)
        self.finish_run(aaron, monday, AgentRun.CANCELLED)
        self.finish_run(aaron, monday, AgentRun.FAILED)
        # Tuesday: 40 messages (30 count), 11 runs ended (8 count), 12
        # reactions from others (10 count) and one of my own (none):
        # 25 + 30 + 40 + 10. The run triggers are among the 40 + 11 messages.
        self.send_at(aaron, tuesday, 40)
        for _ in range(11):
            self.finish_run(aaron, tuesday, AgentRun.DONE)
        target = self.send_at(aaron, tuesday)[0]
        for i, person in enumerate(["hamlet", "othello", "cordelia", "iago"] * 3):
            Reaction.objects.create(
                user_profile=self.example_user(person),
                message_id=target,
                emoji_name=f"e{i}",
                emoji_code=f"1f60{i % 10}",
                reaction_type="unicode_emoji",
            )
        Reaction.objects.create(
            user_profile=aaron,
            message_id=target,
            emoji_name="mine",
            emoji_code="1f44d",
            reaction_type="unicode_emoji",
        )
        # Late on Tuesday in UTC is Wednesday in Kolkata: a new day.
        self.send_at(aaron, tuesday.replace(hour=20), 2)
        with time_machine.travel(tuesday.replace(hour=21), tick=False):
            # A read writes nothing; the fold it asks for runs after it.
            self.login_user(aaron)
            with (
                mock.patch.object(partner_fold, "start", side_effect=partner_fold.work) as start,
                self.captureOnCommitCallbacks(execute=True),
            ):
                self.assertEqual(self.partners(aaron)["me"]["xp"], 0)
            start.assert_called_once_with(aaron.id)
            me = self.partners(aaron)["me"]
        self.assertEqual(me["xp"], (25 + 15 + 10) + (25 + 30 + 40 + 10) + (25 + 2))
        self.assertEqual(me["active_days"], 3)
        self.assertEqual(me["level"], level_for_xp(me["xp"]))
        self.assertEqual(me["today_xp"], 27)

        # A fold within 30 s reads nothing; after that only what is new, and a
        # reaction now counts on the day of the fold; deleted messages keep theirs.
        Message.objects.filter(realm_id=aaron.realm_id, sender=aaron, date_sent=monday).delete()
        later = tuesday.replace(hour=21) + timedelta(seconds=10)
        self.send_at(aaron, later)
        with time_machine.travel(later, tick=False):
            self.assertEqual(self.partners(aaron)["me"]["xp"], me["xp"])
        with time_machine.travel(later + timedelta(seconds=31), tick=False):
            Reaction.objects.create(
                user_profile=hamlet,
                message_id=target,
                emoji_name="late",
                emoji_code="1f389",
                reaction_type="unicode_emoji",
            )
            with CaptureQueriesContext(connection) as queries:
                fold_partner(aaron)
            # Read before the request below, which resets the query log.
            fold_queries = queries.captured_queries
            state = self.partners(aaron)
        # The fold read only what was past the watermarks.
        reads = [q["sql"] for q in fold_queries if 'FROM "zerver_message"' in q["sql"]]
        self.assertTrue(any('"zerver_message"."id" >' in sql for sql in reads), reads)
        self.assertLess(len(fold_queries), 30)
        self.assertEqual(state["me"]["xp"], me["xp"] + 2)
        self.assertEqual(state["me"]["today_xp"], 29)
        # A day passes: today's experience starts again at nothing.
        with time_machine.travel(later + timedelta(days=2), tick=False):
            self.assertEqual(self.partners(aaron)["me"]["today_xp"], 0)

    def test_evolution_branches_hold_and_shiny(self) -> None:
        cordelia = self.new_person("cordelia2")
        self.assert_json_success(self.choose(cordelia, "10"))
        self.login_user(cordelia)
        self.assert_json_error(
            self.client_post("/json/armada/partner/evolve", {"into": "11"}),
            "Your partner cannot change yet.",
        )
        self.assertFalse(self.partners(cordelia)["partners"][str(cordelia.id)]["may_change"])
        self.set_xp(cordelia, 1024)
        state = self.partners(cordelia)
        me = state["me"]
        self.assertEqual(me["ready"], {"kind": "evolve", "choices": ["11", "12"]})
        # The team is told that Cordelia can evolve now, so their pages hold
        # her next message until they know whether she did.
        self.assertTrue(state["partners"][str(cordelia.id)]["may_change"])
        self.assertEqual((me["level"], me["level_xp"], me["next_level_xp"]), (16, 1024, 1156))

        # Stop holds it as it is; Evolve later takes the change.
        held = self.assert_json_success(
            self.client_post("/json/armada/partner/evolve", {"hold": "true"})
        )
        self.assertTrue(held["me"]["held"])
        self.assertEqual(held["me"]["species"], "10")
        self.assert_json_error(
            self.client_post("/json/armada/partner/evolve", {"into": "3"}),
            "Your partner cannot become that.",
        )
        with time_machine.travel(datetime(2026, 10, 14, 12, tzinfo=timezone.utc), tick=False):
            state = self.assert_json_success(
                self.client_post("/json/armada/partner/evolve", {"into": "12"})
            )
        mine = state["partners"][str(cordelia.id)]
        self.assertEqual(
            (mine["species"], mine["stone"], mine["evolved_kind"]), ("12", "citrine", "evolve")
        )
        self.assertEqual(
            mine["evolved_at"], datetime(2026, 10, 14, 12, tzinfo=timezone.utc).timestamp()
        )
        self.assertFalse(state["me"]["held"])
        self.assertIsNone(state["me"]["ready"])
        self.assertFalse(mine["may_change"])

        # Every partner turns shiny at level 50.
        self.set_xp(cordelia, 10000)
        self.assertEqual(
            self.partners(cordelia)["me"]["ready"], {"kind": "shiny", "choices": ["12"]}
        )
        state = self.assert_json_success(
            self.client_post("/json/armada/partner/evolve", {"into": "12"})
        )
        self.assertTrue(state["me"]["shiny"])
        self.assertEqual(state["partners"][str(cordelia.id)]["evolved_kind"], "shiny")
        self.assertIsNone(state["me"]["ready"])

    def test_a_species_that_never_evolves_turns_shiny_at_the_last_level(self) -> None:
        cordelia = self.new_person("cordelia2")
        othello = self.new_person("othello2")
        self.assert_json_success(self.choose(cordelia, "20"))
        self.assert_json_success(self.choose(othello, "50"))
        self.set_xp(cordelia, 3600)
        self.set_xp(othello, 10000)
        self.assertEqual(
            self.partners(cordelia)["me"]["ready"], {"kind": "shiny", "choices": ["20"]}
        )
        # A species with no shiny sprite stays as it is.
        self.assertIsNone(self.partners(othello)["me"]["ready"])

    def test_partners_read_takes_no_lock_and_writes_nothing(self) -> None:
        hamlet = self.example_user("hamlet")
        self.assert_json_success(self.choose(hamlet, "1"))
        self.partners(hamlet)
        # My session, the agent's bot, the partners and the people shown;
        # no lock, no write, and the agent's evolution from memcached.
        with (
            self.assert_database_query_count(4, keep_cache_warm=True),
            CaptureQueriesContext(connection) as queries,
        ):
            self.assert_json_success(self.client_get("/json/armada/partners"))
        statements = [query["sql"] for query in queries.captured_queries]
        self.assertFalse([sql for sql in statements if "FOR UPDATE" in sql or "UPDATE " in sql])

    def test_a_fold_writes_only_what_it_found(self) -> None:
        hamlet = self.example_user("hamlet")
        self.assert_json_success(self.choose(hamlet, "1"))
        message_id = self.send_stream_message(hamlet, "Verona", "a message")
        moment = timezone_now() + timedelta(seconds=PARTNER_FOLD_SECONDS)
        with (
            time_machine.travel(moment, tick=False),
            CaptureQueriesContext(connection) as queries,
        ):
            fold_partner(hamlet)
        self.assertFalse([q for q in queries.captured_queries if "UPDATE " in q["sql"]])

        # Someone reacts: only a fold counts that.
        Reaction.objects.create(
            user_profile=self.example_user("othello"),
            message_id=message_id,
            emoji_name="tada",
            emoji_code="1f389",
            reaction_type="unicode_emoji",
        )
        xp = get_row(hamlet)["xp"]
        later = moment + timedelta(seconds=PARTNER_FOLD_SECONDS)
        # Without a pack there is nothing to fold for.
        with mock.patch.dict(os.environ, {"ARMADA_SPRITE_DIR": "/nonexistent/sprites"}):
            fold_partner(hamlet)
        self.assertEqual(get_row(hamlet)["xp"], xp)
        with time_machine.travel(later, tick=False):
            fold_partner(hamlet)
        self.assertEqual(get_row(hamlet)["xp"], xp + settings.PARTNER_XP_REACTION)

    def test_a_finished_run_counts_toward_its_requesters_partner(self) -> None:
        hamlet = self.example_user("hamlet")
        self.assert_json_success(self.choose(hamlet, "1"))
        trigger = self.send_stream_message(hamlet, "Verona", "a request")
        run = AgentRun.objects.create(
            realm=self.realm,
            bot_profile=self.agent,
            requesting_user=hamlet,
            trigger_message_id=trigger,
            stream_name="Verona",
            topic_name="test",
            status=AgentRun.RUNNING,
        )
        store = ArmadaDatabaseWorkerRunStore()
        with (
            mock.patch("zerver.lib.partner.fold_partner_after_run") as after_run,
            self.captureOnCommitCallbacks(execute=True),
        ):
            store.cancel_locked_run(run, summary="Stopped.")
        # A cancelled run counts for nothing.
        after_run.assert_not_called()

        run.status = AgentRun.RUNNING
        run.save(update_fields=["status"])
        with (
            mock.patch("zerver.lib.partner.fold_partner_after_run") as after_run,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.assertTrue(
                store._update_run(
                    run,
                    AgentRun.DONE,
                    stream="Verona",
                    topic="test",
                    result_message_id=None,
                    error=None,
                )
            )
        after_run.assert_called_once_with(hamlet.id)

        before = get_row(hamlet)["xp"]
        cache.set(f"armada:agent-partner-evolved:{self.realm.id}", True)
        fold_partner_after_run(hamlet.id)
        self.assertEqual(get_row(hamlet)["xp"], before + settings.PARTNER_XP_RUN)
        self.assertIsNone(cache.get(f"armada:agent-partner-evolved:{self.realm.id}"))
        fold_partner_after_run(0)

    def test_the_agent_evolves_with_the_teams_runs(self) -> None:
        cordelia = self.example_user("cordelia")
        moment = timezone_now()
        with override_settings(PARTNER_AGENT_RUNS=3, PARTNER_AGENT_HANDS=2):
            for _ in range(3):
                self.finish_run(cordelia, moment, AgentRun.DONE)
            # Three runs, but from one pair of hands.
            self.assertEqual(self.partners(cordelia)["agent"]["species"], "95")
            self.finish_run(self.example_user("hamlet"), moment, AgentRun.DONE)
            self.assertEqual(self.partners(cordelia)["agent"]["species"], "208")
        # The thresholds are back to the server's: count again.
        invalidate_agent_evolution(self.realm.id)
        # Without the drawing, the agent keeps its committed mark.
        PACK_WITHOUT = json.loads(json.dumps(PACK))
        PACK_WITHOUT["species"]["95"]["sprite"] = None
        with open(os.path.join(self.sprites.name, "species.json"), "w") as f:
            json.dump(PACK_WITHOUT, f)
        os.utime(os.path.join(self.sprites.name, "species.json"), (2, 2))
        self.assertIsNone(self.partners(cordelia)["agent"]["species"])

    def test_partners_follow_user_access(self) -> None:
        # Polonius is a guest, and the realm limits what guests can see: he
        # sees the partners of the people he can see, and himself.
        self.set_up_db_for_testing_user_access()
        polonius = self.example_user("polonius")
        hamlet = self.example_user("hamlet")
        othello = self.example_user("othello")
        for person, species_id in [(hamlet, "1"), (othello, "10"), (polonius, "20")]:
            choose_partner(person, species_id, [])
        self.assertEqual(
            set(self.partners(polonius)["partners"]), {str(hamlet.id), str(polonius.id)}
        )
        # A member sees everyone's.
        self.assertEqual(
            set(self.partners(hamlet)["partners"]),
            {str(hamlet.id), str(othello.id), str(polonius.id)},
        )
        # A refusal names the holder only to someone who can see them.
        self.assert_json_error(self.choose(polonius, "10"), "Someone else has this line.")
        self.assert_json_error(self.choose(polonius, "1"), "King Hamlet has this line.")

    def partner_events(
        self, events: list[Mapping[str, Any]], op: str = "update"
    ) -> list[Mapping[str, Any]]:
        """The armada_partner events of one op: "update" (a partner as the
        team sees it) or "me" (mine as only I see it)."""
        found = [
            e for e in events if e["event"]["type"] == "armada_partner" and e["event"]["op"] == op
        ]
        check = check_armada_partner_event if op == "update" else check_armada_partner_me_event
        for e in found:
            # The queue gives each event its id on the way out.
            check("events[0]", {**e["event"], "id": 0})
        return found

    def test_a_choice_and_an_evolution_are_pushed(self) -> None:
        cordelia = self.new_person("cordelia2")
        self.login_user(cordelia)
        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(self.choose(cordelia, "10"))
        [pushed] = self.partner_events(events)
        self.assertEqual(
            pushed["event"]["partner"],
            {
                "user_id": cordelia.id,
                **self.partners(cordelia)["partners"][str(cordelia.id)],
            },
        )
        self.assertEqual(set(pushed["users"]), set(get_user_ids_who_can_access_user(cordelia)))

        # Holding changes nothing the team sees, so nothing is pushed.
        self.set_xp(cordelia, 1024)
        self.login_user(cordelia)
        with self.capture_send_event_calls(expected_num_events=0):
            self.assert_json_success(
                self.client_post("/json/armada/partner/evolve", {"hold": "true"})
            )
        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(
                self.client_post("/json/armada/partner/evolve", {"into": "11"})
            )
        [pushed] = self.partner_events(events)
        self.assertEqual(
            (pushed["event"]["partner"]["species"], pushed["event"]["partner"]["evolved_kind"]),
            ("11", "evolve"),
        )
        # Putting the choice off is mine alone.
        with self.capture_send_event_calls(expected_num_events=0):
            self.assert_json_success(self.client_post("/json/armada/partner", {"later": "true"}))

    def test_a_level_crossed_by_a_message_is_pushed_ahead_of_it(self) -> None:
        cordelia = self.new_person("cordelia2")
        self.assert_json_success(self.choose(cordelia, "10"))
        # Level 15, one short of 16 (1024): the day's first message gives
        # the day's 25 and its own 1.
        self.set_xp(cordelia, 1000)
        self.subscribe(cordelia, "Verona")
        self.login_user(cordelia)

        def send(content: str) -> int:
            result = self.client_post(
                "/json/messages",
                {
                    "type": "stream",
                    "to": orjson.dumps("Verona").decode(),
                    "topic": "levels",
                    "content": content,
                },
            )
            return self.assert_json_success(result)["id"]

        # Her first message in a topic also follows it for her. Her own
        # view (experience, the evolution now ready) goes to her alone.
        with self.capture_send_event_calls(expected_num_events=5) as events:
            message_id = send("over the line")
        self.assertEqual(
            [(e["event"]["type"], e["event"].get("op")) for e in events],
            [
                ("armada_partner", "update"),
                ("armada_partner", "me"),
                ("muted_topics", None),
                ("user_topic", None),
                ("message", None),
            ],
        )
        [pushed] = self.partner_events(events)
        self.assertEqual(
            (pushed["event"]["partner"]["level"], pushed["event"]["partner"]["may_change"]),
            (16, True),
        )
        [mine] = self.partner_events(events, "me")
        self.assertEqual(mine["users"], [cordelia.id])
        self.assertEqual((mine["event"]["me"]["xp"], mine["event"]["me"]["level"]), (1026, 16))
        self.assertEqual(mine["event"]["me"]["ready"], {"kind": "evolve", "choices": ["11", "12"]})
        self.assertEqual(events[4]["event"]["message"], message_id)
        row = get_row(cordelia)
        self.assertEqual((row["xp"], row["message_watermark"]), (1026, message_id))

        # The next message counts and crosses nothing: nothing for the
        # team, and her own view as the page would fetch it.
        with self.capture_send_event_calls(expected_num_events=2) as events:
            send("still level 16")
        self.assertEqual(self.partner_events(events), [])
        [mine] = self.partner_events(events, "me")
        self.assertEqual(get_row(cordelia)["xp"], 1027)
        self.assertEqual(mine["event"]["me"], self.partners(cordelia)["me"])

        # Past the day's cap a message counts for nothing: nothing is pushed.
        row = get_row(cordelia)
        row["day_messages"] = settings.PARTNER_CAP_MESSAGES
        save_row(cordelia, row)
        with self.capture_send_event_calls(expected_num_events=1) as events:
            send("over the cap")
        self.assertEqual([e["event"]["type"] for e in events], ["message"])

    def test_sending_costs_a_query_only_with_a_pack(self) -> None:
        hamlet = self.example_user("hamlet")

        def partner_queries() -> int:
            with CaptureQueriesContext(connection) as queries:
                self.send_stream_message(hamlet, "Verona", "hello")
            return sum('"zerver_partner"' in q["sql"] for q in queries.captured_queries)

        # Someone with no partner: one look.
        self.assertEqual(partner_queries(), 1)
        os.remove(os.path.join(self.sprites.name, "species.json"))
        self.assertEqual(partner_queries(), 0)
        # The agent's own messages never fold.
        self.assertEqual(Partner.objects.filter(user_profile=self.agent).count(), 0)

    def test_a_broken_pack_never_stops_a_send(self) -> None:
        cordelia = self.example_user("cordelia")
        self.assert_json_success(self.choose(cordelia, "1"))
        with open(os.path.join(self.sprites.name, "species.json"), "w") as f:
            f.write("{not json")
        with self.assertLogs(level="ERROR") as logs:
            message_id = self.send_stream_message(cordelia, "Verona", "still sent")
        self.assertEqual(
            logs.output[0].splitlines()[0], "ERROR:root:The partner pack could not be read"
        )
        self.assertEqual(Message.objects.get(id=message_id).content, "still sent")

    def test_pushes_follow_user_access(self) -> None:
        self.set_up_db_for_testing_user_access()
        polonius = self.example_user("polonius")
        othello = self.example_user("othello")
        hamlet = self.example_user("hamlet")
        with self.capture_send_event_calls(expected_num_events=1) as events:
            choose_partner(othello, "10", [])
        [pushed] = self.partner_events(events)
        self.assertNotIn(polonius.id, pushed["users"])
        self.assertIn(hamlet.id, pushed["users"])
        with self.capture_send_event_calls(expected_num_events=1) as events:
            choose_partner(hamlet, "1", [])
        self.assertIn(polonius.id, self.partner_events(events)[0]["users"])

    def test_a_pack_missing_a_sprite_file(self) -> None:
        # species.json names made/1.png, which this directory does not hold.
        # The partner is still listed (the page draws initials when its file
        # does not load), and the file is a 404.
        cordelia = self.example_user("cordelia")
        self.assert_json_success(self.choose(cordelia, "1"))
        self.assertEqual(self.partners(cordelia)["partners"][str(cordelia.id)]["species"], "1")
        self.assertEqual(self.client_get("/beta/sprites/made/1.png").status_code, 404)

    def test_rows_are_partners_in_the_realm(self) -> None:
        cordelia = self.example_user("cordelia")
        moment = datetime(2026, 10, 14, 12, 30, 15, 123456, tzinfo=timezone.utc)
        with time_machine.travel(moment, tick=False):
            self.assert_json_success(self.choose(cordelia, "1"))
        partner = Partner.objects.get(user_profile=cordelia)
        self.assertEqual(
            (partner.realm_id, partner.chosen, partner.species), (self.realm.id, "1", "1")
        )
        # Times keep their microseconds through the row's timestamps.
        self.assertEqual(partner.chosen_at, moment)
        self.assertEqual(get_row(cordelia)["chosen_at"], moment.timestamp())
        # Someone with no row has the empty one.
        self.assertIsNone(get_row(self.example_user("hamlet"))["species"])

    def test_the_migration_moves_rows_out_of_bot_storage_and_back(self) -> None:
        # zerver/migrations/0813_partner.py, run on today's models: the rows
        # the interim kept as JSON under the agent's bot become partners,
        # and the reverse puts them back.
        migration = importlib.import_module("zerver.migrations.0813_partner")
        cordelia = self.example_user("cordelia")
        hamlet = self.example_user("hamlet")
        other_bot = self.example_user("default_bot")
        old_agent = do_create_user(
            "armada-old-agent-bot@zulip.testserver",
            None,
            self.realm,
            "Old agent",
            bot_type=UserProfile.EMBEDDED_BOT,
            bot_owner=self.example_user("iago"),
            acting_user=None,
        )
        Service.objects.create(name="armada", user_profile=old_agent, base_url="", token="")
        invalidate_armada_selected_bot_cache(self.realm.id)
        stored = {
            **get_row(cordelia),
            "species": "2",
            "chosen": "1",
            "xp": 1100,
            "day": "2026-10-14",
            "chosen_at": 1760000000.5,
            "runs_watermark": 1760400000.25,
        }
        # A row written before a key existed reads as that key's default.
        del stored["held"]
        BotStorageData.objects.bulk_create(
            [
                BotStorageData(
                    bot_profile=self.agent,
                    key=f"partner:{cordelia.id}",
                    value=json.dumps(stored),
                ),
                # A person deleted since keeps nothing.
                BotStorageData(bot_profile=self.agent, key="partner:999999", value="{}"),
                BotStorageData(bot_profile=self.agent, key="other", value="kept"),
                # Another bot's keys are its own, whatever they look like.
                BotStorageData(bot_profile=other_bot, key="partner:notes", value="not JSON"),
                # An agent bot the realm used before keeps an older row.
                BotStorageData(
                    bot_profile=old_agent,
                    key=f"partner:{cordelia.id}",
                    value=json.dumps({**stored, "species": "1", "xp": 900}),
                ),
            ]
        )
        migration.move_partners_to_their_table(django_apps, connection.schema_editor())
        self.assertEqual(
            set(BotStorageData.objects.values_list("bot_profile_id", "key")),
            {(self.agent.id, "other"), (other_bot.id, "partner:notes")},
        )
        moved = get_row(cordelia)
        self.assertEqual(moved, {**stored, "held": False})
        self.assertEqual(Partner.objects.get(user_profile=cordelia).realm_id, self.realm.id)

        save_row(hamlet, {**get_row(hamlet), "species": "20", "chosen": "20"})
        migration.move_partners_back_to_bot_storage(django_apps, connection.schema_editor())
        back = {
            row.key: json.loads(row.value)
            for row in BotStorageData.objects.filter(key__startswith="partner:").exclude(
                bot_profile=other_bot
            )
        }
        self.assertEqual(set(back), {f"partner:{cordelia.id}", f"partner:{hamlet.id}"})
        self.assertEqual(back[f"partner:{cordelia.id}"], moved)
        self.assertEqual(back[f"partner:{hamlet.id}"]["species"], "20")
        self.assertEqual(
            set(
                BotStorageData.objects.filter(key__startswith="partner:").values_list(
                    "bot_profile_id", flat=True
                )
            ),
            {self.agent.id, other_bot.id},
        )
        self.assertEqual(
            BotStorageData.objects.filter(bot_profile=other_bot).values_list("value", flat=True)[0],
            "not JSON",
        )

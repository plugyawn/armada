import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

import responses
from django.conf import settings
from django.test import override_settings
from typing_extensions import override

from zerver.lib.armada_plugin_check import (
    ARMADA_PLUGIN_PREVIEW_DIRECTORY,
    check_plugin,
    preview_browser,
    preview_site,
)
from zerver.lib.armada_plugin_repository import (
    init_team_plugin_repository,
    install_team_plugin,
    team_plugin_repository,
)
from zerver.lib.armada_plugins import (
    PluginOrigin,
    list_plugin_installs,
    remove_plugin,
    update_plugin,
)
from zerver.lib.exceptions import JsonableError
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import PluginInstall, PluginWidgetState, Repository
from zerver.tests.test_armada_plugin_preview import fake_chromium
from zerver.tests.test_armada_widgets import EventsCaught

FIXTURES = Path(__file__).parent / "fixtures" / "armada_plugins"
TRIAL = FIXTURES / "trial"
REDIS_URL = "redis://:hunter2-0123456789abcdef@10.0.0.7:6379/0"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class PluginCheckTest(EventsCaught):
    """A plugin's check (zerver/lib/armada_plugin_check.py): the manifest
    against the contract, the draft that holds its id and its secrets, the
    trial of its program (zerver/lib/armada_plugin_trial.py), and its
    widgets drawn by a browser (zerver/lib/armada_plugin_preview.py), here
    a fake one that speaks the DevTools protocol and draws nothing."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        overridden = override_settings(
            ARMADA_TOPIC_WORKDIR_ROOT=str(self.root / "workspaces"),
            ARMADA_PLUGIN_DIR=str(self.root / "plugins"),
            ARMADA_PLUGIN_PREVIEW_BROWSER=None,
            ARMADA_WORKER_BROWSER_DIR=None,
        )
        overridden.enable()
        self.addCleanup(overridden.disable)
        # The tests may not reach the internet; the plugin on trial, on
        # this machine, they do reach.
        network = responses.RequestsMock(assert_all_requests_are_fired=False)
        network.add_passthru(re.compile(r"^http://127\.0\.0\.1:[0-9]+/"))
        network.start()
        self.addCleanup(network.stop)
        self.hamlet = self.example_user("hamlet")
        self.realm = self.hamlet.realm
        init_team_plugin_repository(self.realm, acting_user=self.example_user("desdemona"))
        self.log = self.root / "chromium.log"
        self.chromium_path = fake_chromium(self.root)

    def worktree(self, behaviour: str = "good", **changes: Any) -> Path:
        """A worktree of the team's repository, with the trial plugin in
        plugins/queue/, doing by default what behaviour says, committed."""
        repository = team_plugin_repository(self.realm)
        assert repository is not None
        worktree = self.root / f"worktree-{len(list(self.root.glob('worktree-*')))}"
        git("clone", "-q", repository.repo_cache_path, str(worktree))
        directory = worktree / "plugins" / "queue"
        shutil.copytree(TRIAL, directory, dirs_exist_ok=True)
        manifest = json.loads((TRIAL / "armada-plugin.json").read_text())
        manifest["settings"]["properties"]["behaviour"]["default"] = behaviour
        manifest.update(changes)
        # None takes a key out.
        manifest = {key: value for key, value in manifest.items() if value is not None}
        (directory / "armada-plugin.json").write_text(json.dumps(manifest))
        git("-C", str(worktree), "add", "-A")
        git("-C", str(worktree), "commit", "-q", "--allow-empty", "-m", "Make the queue plugin.")
        return worktree

    @contextmanager
    def browser(self, behaviour: str = "", requests: list[str] | None = None) -> Iterator[None]:
        with (
            override_settings(ARMADA_PLUGIN_PREVIEW_BROWSER=self.chromium_path),
            mock.patch.dict(
                os.environ,
                {
                    "FAKE_CHROMIUM": behaviour,
                    "FAKE_CHROMIUM_LOG": str(self.log),
                    "FAKE_CHROMIUM_REQUESTS": json.dumps(requests or []),
                },
            ),
        ):
            yield

    def chromium(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_a_good_plugin_is_tried_drawn_and_seen_by_no_one(self) -> None:
        worktree = self.worktree()
        url = self.realm.url
        requests = [
            f"{url}/json/register",
            f"{url}/json/events?queue_id=preview",
            f"{url}/json/armada/partners",
            f"{settings.STATIC_URL}webpack-bundles/nowhere.js",
            "https://elsewhere.example/script.js",
        ]
        with self.browser(requests=requests), self.events() as events:
            result = check_plugin(self.hamlet, workdir=worktree, path="plugins/queue")
        draft = PluginInstall.objects.get(realm=self.realm, plugin_id="queue")
        self.assertEqual(
            (draft.state, draft.installed_by, draft.source_path),
            (PluginInstall.DRAFT, self.hamlet, "plugins/queue"),
        )
        previews = worktree / ARMADA_PLUGIN_PREVIEW_DIRECTORY
        self.assertEqual(
            {key: value for key, value in result.items() if key not in ("lines", "seconds")},
            {
                "ok": True,
                "plugin": "queue",
                "install": {"id": draft.id, "state": "draft", "version": "0.1.0"},
                "problems": [],
                "waiting_for": [],
                "widgets": [{"key": "queue", "label": "Eval queue", "pushed": True}],
                "tools": [
                    {"name": "queue_status", "description": "What is waiting, and for what."}
                ],
                "posts": [],
                "asked": [],
                "previews": [
                    {
                        "widget": None,
                        "state": "rest",
                        "theme": "light",
                        "path": str(previews / "queue-rest-light.png"),
                    },
                    {
                        "widget": "queue",
                        "state": "pressed",
                        "theme": "light",
                        "path": str(previews / "queue-queue-pressed-light.png"),
                    },
                    {
                        "widget": None,
                        "state": "rest",
                        "theme": "dark",
                        "path": str(previews / "queue-rest-dark.png"),
                    },
                    {
                        "widget": "queue",
                        "state": "pressed",
                        "theme": "dark",
                        "path": str(previews / "queue-queue-pressed-dark.png"),
                    },
                ],
                "notes": [],
            },
        )
        for preview in result["previews"]:
            self.assertEqual(Path(preview["path"]).read_bytes()[:8], PNG_SIGNATURE)
        self.assertEqual(
            result["lines"],
            ["queue plugin starting in a trial: 1", "push: success", "push: success"],
        )
        self.assertEqual((worktree / "plugins" / "queue" / "setup-ran").read_text(), "1")

        # The trial's pushes went to the trial alone: no widget document
        # was kept, and no armada_widget event sent; the draft's own
        # events went to its maker alone.
        self.assertFalse(PluginWidgetState.objects.filter(install=draft).exists())
        self.assertEqual(
            [
                event["event"]["type"]
                for event in events
                if event["event"]["type"].startswith("armada_")
            ],
            ["armada_plugin"],
        )
        [plugin_event] = [event for event in events if event["event"]["type"] == "armada_plugin"]
        self.assertEqual(list(plugin_event["users"]), [self.hamlet.id])
        self.assertNotIn(
            draft.id,
            [plugin["id"] for plugin in list_plugin_installs(self.example_user("cordelia"))],
        )

        # The browser was given the page, the trial's documents and the
        # files; the event queue was never answered, and nothing outside.
        log = self.chromium()
        answers = {entry["request"]: entry for entry in log if "answer" in entry}
        asked = {entry["url"]: entry["request"] for entry in log if "url" in entry}
        page = answers[asked[f"{url}/"]]
        self.assertEqual((page["answer"], page["status"]), ("Fetch.fulfillRequest", 200))
        self.assertIn('id="beta-data"', page["body"])
        register = json.loads(answers[asked[f"{url}/json/register"]]["body"])
        self.assertEqual([plugin["id"] for plugin in register["armada_plugins"]], [draft.id])
        self.assertEqual(
            register["armada_plugins"][0]["widget_defaults"],
            {"queue": {"nav": "shown", "column": False}},
        )
        [shown] = register["armada_widgets"]
        self.assertEqual(
            (shown["widget"], shown["document"]["data"]["waiting"]), ("queue", "13 waiting")
        )
        self.assertNotIn(asked[f"{url}/json/events?queue_id=preview"], answers)
        self.assertEqual(
            json.loads(answers[asked[f"{url}/json/armada/partners"]]["body"]),
            {"result": "success", "msg": ""},
        )
        self.assertEqual(
            answers[asked[f"{settings.STATIC_URL}webpack-bundles/nowhere.js"]]["status"], 404
        )
        self.assertEqual(answers[asked["https://elsewhere.example/script.js"]]["status"], 404)
        # Light then dark, motion reduced; each pressed row pressed in its
        # middle; each picture the nav's foot, or with the sheet beside it.
        self.assertEqual(
            [entry["media"][0]["value"] for entry in log if "media" in entry],
            ["light", "light", "dark", "dark"],
        )
        self.assertEqual(
            log[next(i for i, e in enumerate(log) if "media" in e)]["media"][1]["value"], "reduce"
        )
        self.assertEqual(
            [entry["press"] for entry in log if "press" in entry][:3],
            [["mouseMoved", 116, 727], ["mousePressed", 116, 727], ["mouseReleased", 116, 727]],
        )
        self.assertEqual(
            [entry["picture"] for entry in log if "picture" in entry][:2],
            [
                {"x": 0, "y": 701, "width": 244, "height": 199, "scale": 1},
                {"x": 0, "y": 485, "width": 596, "height": 415, "scale": 1},
            ],
        )
        self.assertTrue(log[-1]["closed"])

        # Checked again, it is the same draft.
        with self.browser():
            again = check_plugin(self.hamlet, workdir=worktree, path="plugins/queue")
        self.assertEqual(again["install"]["id"], draft.id)

    def test_a_bad_document_says_where_and_what(self) -> None:
        with self.browser():
            result = check_plugin(self.hamlet, workdir=self.worktree("bad"), path="plugins/queue")
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["problems"],
            [
                {
                    "where": "push 1 of queue at $.blocks[0].items[1].figure",
                    "what": "is longer than 24 characters",
                }
            ],
        )
        # It said so as soon as the program had gone quiet.
        self.assertLess(result["seconds"], 10)
        self.assertEqual((result["widgets"][0]["pushed"], result["previews"]), (False, []))
        self.assertEqual(result["lines"][-1], "push: error")

    def test_a_drafts_secret_reaches_its_trial_alone_and_is_never_shown(self) -> None:
        worktree = self.worktree("secret")
        check_plugin(self.hamlet, workdir=worktree, path="plugins/queue")
        draft = PluginInstall.objects.get(realm=self.realm, plugin_id="queue")
        update_plugin(self.hamlet, draft, secrets={"redis_url": REDIS_URL})
        with self.browser(requests=[f"{self.realm.url}/json/register"]):
            result = check_plugin(self.hamlet, workdir=worktree, path="plugins/queue")
        # The program had it, since it said it, and it was blanked: in
        # what it said, in what it pushed, and so in what was drawn.
        self.assertIn("connecting to [secret]", result["lines"])
        self.assertNotIn(REDIS_URL, json.dumps(result))
        self.assertNotIn(REDIS_URL, self.log.read_text())
        self.assertIn("Queue at [secret]", self.log.read_text())
        # Someone else checking it gets no trial of it at all.
        cordelia = self.example_user("cordelia")
        other = check_plugin(cordelia, workdir=self.worktree("secret"), path="plugins/queue")
        self.assertEqual(
            other["problems"][0],
            {
                "where": "$.id",
                "what": "Eval queue is being made by King Hamlet, and holds its id: give this"
                " plugin another id.",
            },
        )
        # Its trial had no secret to say.
        self.assertIn("connecting to ", other["lines"])
        self.assertNotIn("hunter2", json.dumps(other))

    def test_a_draft_keeps_what_was_chosen_and_follows_new_defaults(self) -> None:
        check_plugin(self.hamlet, workdir=self.worktree("exit"), path="plugins/queue")
        draft = PluginInstall.objects.get(realm=self.realm, plugin_id="queue")
        self.assertEqual(draft.settings, {"behaviour": "exit"})
        # The next version's default is what its trial has.
        result = check_plugin(self.hamlet, workdir=self.worktree("chatty"), path="plugins/queue")
        draft.refresh_from_db()
        self.assertEqual(
            (draft.settings, result["posts"][0]["content"]),
            ({"behaviour": "chatty"}, "Queue is up."),
        )
        # What its maker chose stays, whatever the default.
        update_plugin(self.hamlet, draft, plugin_settings={"behaviour": "patches"})
        result = check_plugin(self.hamlet, workdir=self.worktree("good"), path="plugins/queue")
        draft.refresh_from_db()
        self.assertEqual(draft.settings, {"behaviour": "patches"})
        self.assertEqual(
            result["problems"][0],
            {"where": "push 1 of queue", "what": "queue's document comes before its data."},
        )

    def test_a_manifest_that_is_wrong_says_where_and_what(self) -> None:
        worktree = self.worktree()
        directory = worktree / "plugins" / "queue"
        self.assertEqual(
            check_plugin(self.hamlet, workdir=worktree, path="../elsewhere")["problems"],
            [{"where": "../elsewhere", "what": "That is not a directory of the worktree."}],
        )
        (directory / "armada-plugin.json").write_text("{")
        self.assertEqual(
            check_plugin(self.hamlet, workdir=worktree, path="plugins/queue/")["problems"],
            [
                {
                    "where": "plugins/queue/armada-plugin.json",
                    "what": "It is not JSON: Expecting property name enclosed in double quotes:"
                    " line 1 column 2 (char 1).",
                }
            ],
        )
        (directory / "armada-plugin.json").unlink()
        self.assertEqual(
            check_plugin(self.hamlet, workdir=worktree, path="plugins/queue")["problems"],
            [
                {
                    "where": "plugins/queue/armada-plugin.json",
                    "what": "There is no armada-plugin.json here.",
                }
            ],
        )
        manifest = json.loads((TRIAL / "armada-plugin.json").read_text())
        manifest["provides"]["widgets"][0]["label"] = "The evaluations waiting for a GPU"
        (directory / "armada-plugin.json").write_text(json.dumps(manifest))
        result = check_plugin(self.hamlet, workdir=worktree, path="plugins/queue")
        self.assertEqual(
            (result["ok"], result["plugin"], result["problems"]),
            (
                False,
                None,
                [
                    {
                        "where": "plugins/queue/armada-plugin.json at $.provides.widgets[0].label",
                        "what": "is longer than 24 characters",
                    }
                ],
            ),
        )
        self.assertFalse(PluginInstall.objects.filter(plugin_id="queue").exists())
        # A plugin's directory is named after it; a remote plugin is not tried.
        moved = self.worktree()
        shutil.move(moved / "plugins" / "queue", moved / "plugins" / "eval")
        result = check_plugin(self.hamlet, workdir=moved, path="plugins/eval")
        self.assertEqual(
            result["problems"][0],
            {
                "where": "plugins/eval",
                "what": "A plugin's code is in plugins/queue/, named after its id: move it there.",
            },
        )
        manifest["provides"]["widgets"][0]["label"] = "Eval queue"
        remote = {
            **manifest,
            "run": None,
            "secrets": None,
            "remote": {"url": "https://queue.example.com"},
        }
        result = check_plugin(self.hamlet, workdir=self.worktree(**remote), path="plugins/queue")
        self.assertEqual(
            (result["ok"], result["notes"], result["install"]),
            (True, ["A remote plugin runs where its author hosts it: it is not tried here."], None),
        )

    def test_an_installed_plugin_is_checked_as_its_next_version(self) -> None:
        worktree = self.worktree()
        install = install_team_plugin(
            self.hamlet,
            plugin_id="queue",
            sha=git("-C", str(worktree), "rev-parse", "HEAD"),
            workdir=worktree,
            plugin_settings={"behaviour": "silent"},
            secrets={"redis_url": REDIS_URL},
            origin=PluginOrigin(made_by=self.hamlet),
        )
        generation = install.generation
        cordelia = self.example_user("cordelia")
        manifest = json.loads((TRIAL / "armada-plugin.json").read_text())
        manifest["version"] = "0.2.0"
        manifest["settings"]["properties"]["behaviour"]["enum"].remove("silent")
        manifest["secrets"]["redis_url"]["required"] = True
        manifest["secrets"]["token"] = {"title": "Token", "env": "QUEUE_TOKEN", "required": True}
        result = check_plugin(cordelia, workdir=self.worktree(**manifest), path="plugins/queue")
        install.refresh_from_db()
        self.assertEqual(
            (result["install"], install.generation, install.version),
            ({"id": install.id, "state": "starting", "version": "0.1.0"}, generation, "0.1.0"),
        )
        # The install's setting no longer fits: the trial takes the new
        # default, and says so; the secret it lacks, it waits for.
        self.assertEqual(
            result["problems"][0],
            {
                "where": "its settings at $.behaviour",
                "what": 'is "silent", not one of ["good","bad","exit","unhealthy","secret","chatty","patches","wide"]',
            },
        )
        self.assertEqual(result["waiting_for"], ["token"])
        remove_plugin(self.hamlet, install)
        result = check_plugin(self.hamlet, workdir=self.worktree(), path="plugins/queue")
        self.assertRegex(
            result["problems"][0]["what"],
            r"^Eval queue was removed on \d\d \w\w\w and keeps its id for a week: undo the",
        )

    def test_without_a_plugins_repository_no_draft_is_kept(self) -> None:
        worktree = self.worktree()
        Repository.objects.filter(realm=self.realm).update(clone_status=Repository.FAILED)
        result = check_plugin(self.hamlet, workdir=worktree, path="plugins/queue")
        self.assertEqual(
            (result["ok"], result["install"], result["notes"][0][:23]),
            (True, None, "No draft is kept: it ne"),
        )
        self.assertFalse(PluginInstall.objects.filter(plugin_id="queue").exists())
        # The draft's own refusal, here a bot's address someone holds.
        Repository.objects.filter(realm=self.realm).update(clone_status=Repository.READY)
        with mock.patch(
            "zerver.lib.armada_plugin_check.install_plugin",
            side_effect=JsonableError(
                "queue-plugin-bot@zulip.testserver is taken, so the plugin cannot have its own bot."
            ),
        ):
            result = check_plugin(self.hamlet, workdir=worktree, path="plugins/queue")
        self.assertEqual(
            result["problems"],
            [
                {
                    "where": "the draft",
                    "what": "queue-plugin-bot@zulip.testserver is taken, so the plugin cannot have its own bot.",
                }
            ],
        )

    def test_the_preview_says_why_it_was_not_drawn(self) -> None:
        worktree = self.worktree()
        result = check_plugin(self.hamlet, workdir=worktree, path="plugins/queue")
        self.assertEqual(
            (result["ok"], result["previews"], result["notes"]),
            (
                True,
                [],
                ["No preview: this server has no browser to draw one (ARMADA_WORKER_BROWSER_DIR)."],
            ),
        )
        with self.browser("crash", [f"{self.realm.url}/json/register"]):
            result = check_plugin(self.hamlet, workdir=worktree, path="plugins/queue")
        self.assertEqual(
            (result["ok"], result["previews"], result["notes"]),
            (True, [], ["No preview: The browser stopped."]),
        )

    def test_a_figure_too_wide_or_a_label_cut_is_a_problem(self) -> None:
        with self.browser(requests=[f"{self.realm.url}/json/register"]):
            result = check_plugin(self.hamlet, workdir=self.worktree("wide"), path="plugins/queue")
        self.assertEqual(
            result["problems"],
            [
                {
                    "where": "widget queue at rest",
                    "what": "“12 waiting” is wider than its cell, which is as wide as its rest.sizer:"
                    " make the sizer as wide as the widest figure it shows.",
                },
                {
                    "where": "widget queue pressed, row mmlu",
                    "what": "Its label, “mmlu-5shot-with-chain-of-thought”, is cut: keep it short,"
                    " and say the rest in its second line, sub.",
                },
                {
                    "where": "widget queue pressed, row gsm8k",
                    "what": "Its figure, “2h 14m 30s”, is cut: keep a row's figure short.",
                },
            ],
        )
        self.assert_length(result["previews"], 4)
        long_label = json.loads((TRIAL / "armada-plugin.json").read_text())["provides"]
        long_label["widgets"][0]["label"] = "Evaluations waiting now"
        with self.browser(requests=[f"{self.realm.url}/json/register"]):
            result = check_plugin(
                self.hamlet, workdir=self.worktree(provides=long_label), path="plugins/queue"
            )
        self.assertEqual(
            result["problems"],
            [
                {
                    "where": "widget queue at rest",
                    "what": "The nav cuts its label, “Evaluations waiting now”: give it a shorter label.",
                }
            ],
        )


class PreviewPartsTest(ZulipTestCase):
    """The parts of the preview that need no browser."""

    def test_where_its_files_come_from(self) -> None:
        hamlet = self.example_user("hamlet")
        with override_settings(DEBUG=True, STATIC_URL="http://localhost:9991/static/"):
            site = preview_site(
                hamlet,
                install_id=3,
                manifest=json.loads((TRIAL / "armada-plugin.json").read_text()),
                documents={},
            )
        self.assertEqual(
            (site.page_url, site.files, site.network),
            (
                f"{hamlet.realm.url}/",
                (),
                ("http://localhost:9991/static/", "http://localhost:9991/webpack/"),
            ),
        )
        with override_settings(
            DEBUG=False,
            STATIC_URL="https://armada.example/static/",
            STATIC_ROOT="/home/zulip/prod-static",
        ):
            site = preview_site(
                hamlet,
                install_id=3,
                manifest=json.loads((TRIAL / "armada-plugin.json").read_text()),
                documents={},
            )
        self.assertEqual(
            (site.files, site.network),
            ((("https://armada.example/static/", "/home/zulip/prod-static"),), ()),
        )

    def test_which_browser_draws(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for build in (
            "chromium-1187/chrome-linux",
            "chromium-1248/chrome-linux64",
            "chromium_headless_shell-1300/chrome-linux",
        ):
            (root / "browsers" / build).mkdir(parents=True)
            (root / "browsers" / build / "chrome").write_text("")
        with override_settings(ARMADA_PLUGIN_PREVIEW_BROWSER=None, ARMADA_WORKER_BROWSER_DIR=None):
            self.assertIsNone(preview_browser())
        with override_settings(
            ARMADA_PLUGIN_PREVIEW_BROWSER=None, ARMADA_WORKER_BROWSER_DIR=str(root)
        ):
            self.assertEqual(
                preview_browser(),
                str(root / "browsers" / "chromium-1248" / "chrome-linux64" / "chrome"),
            )
        with override_settings(
            ARMADA_PLUGIN_PREVIEW_BROWSER=None, ARMADA_WORKER_BROWSER_DIR=str(root / "empty")
        ):
            self.assertIsNone(preview_browser())
        with override_settings(
            ARMADA_PLUGIN_PREVIEW_BROWSER="/Applications/Chrome",
            ARMADA_WORKER_BROWSER_DIR=str(root),
        ):
            self.assertEqual(preview_browser(), "/Applications/Chrome")

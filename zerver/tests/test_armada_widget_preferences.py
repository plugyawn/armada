import copy
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest import mock

import time_machine
from typing_extensions import override

from zerver.lib.armada import ARMADA_EMBEDDED_BOT_SERVICE
from zerver.lib.armada_agent import (
    ArmadaAppServerNotification,
    ArmadaAppServerTurnContext,
    ArmadaCodexAppServerToolRunner,
    armada_codex_dynamic_tools,
    armada_codex_thread_version,
)
from zerver.lib.armada_plugin_events import fetch_armada_plugin_state
from zerver.lib.armada_plugin_tools import armada_plugin_tool_specs
from zerver.lib.armada_plugins import (
    install_plugin,
    remove_plugin,
    restore_plugin,
    update_plugin_version,
)
from zerver.lib.armada_widget_preferences import access_widget, update_widget_preference
from zerver.lib.event_schema import (
    check_armada_widget_preference_remove_event,
    check_armada_widget_preference_update_event,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import AgentRun, PluginInstall, PluginWidgetPreference, UserProfile
from zerver.tests.test_events import BaseAction

SHA = "4e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d5"


class PreferenceTestMixin:
    """The GPU tracker, whose one widget declares two figures, two facets
    and an alert (zerver/tests/fixtures/armada_plugins/gpus)."""

    def manifest(self, plugin: str = "gpus") -> dict[str, Any]:
        assert isinstance(self, ZulipTestCase)
        return json.loads(self.fixture_data("armada-plugin.json", f"armada_plugins/{plugin}"))

    def install(self, user: UserProfile, plugin: str = "gpus") -> PluginInstall:
        settings = {"manage_prefix": "nl"} if plugin == "gpus" else {"entity": "runix"}
        return install_plugin(
            user,
            manifest=self.manifest(plugin),
            source_kind="github",
            source=f"github.com/plugyawn/armada-{plugin}",
            sha=SHA,
            plugin_settings=settings,
        )


class WidgetPreferenceTest(PreferenceTestMixin, ZulipTestCase):
    """A person's own view of a widget (zerver/lib/armada_widget_preferences.py):
    only what the manifest declares, for them alone, and it follows the
    install through Remove, Undo and an update."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.cordelia = self.example_user("cordelia")
        self.gpus = self.install(self.hamlet)

    def url(self, install: PluginInstall | None = None, widget: str = "gpus") -> str:
        return f"/api/v1/armada/widgets/{(install or self.gpus).id}/{widget}/preference"

    def patch(self, user: UserProfile, **params: Any) -> Any:
        widget = params.pop("widget", "gpus")
        install = params.pop("install", None)
        return self.api_patch(
            user,
            self.url(install, widget),
            {
                name: value if isinstance(value, str) else json.dumps(value)
                for name, value in params.items()
            },
            subdomain=user.realm.string_id,
        )

    @contextmanager
    def events(self) -> Iterator[list[Any]]:
        caught: list[Any] = []
        with (
            mock.patch("zerver.tornado.event_queue.process_notification", caught.append),
            self.captureOnCommitCallbacks(execute=True),
        ):
            yield caught

    def preference_events(self, caught: list[Any]) -> list[tuple[dict[str, Any], list[int]]]:
        found = []
        for sent in caught:
            event = {**sent["event"], "id": 0}
            if event["type"] != "armada_widget_preference":
                continue
            if event["op"] == "update":
                check_armada_widget_preference_update_event("events[0]", event)
            else:
                check_armada_widget_preference_remove_event("events[0]", event)
            found.append((event, list(sent["users"])))
        return found

    def snapshot(self, user: UserProfile) -> list[dict[str, Any]]:
        return fetch_armada_plugin_state(
            user, want_plugins=False, want_widgets=False, want_preferences=True
        )["armada_widget_preferences"]

    def test_a_person_changes_their_own_view_and_no_one_elses(self) -> None:
        with self.events() as caught:
            result = self.patch(
                self.hamlet,
                shown=False,
                position=2,
                label="Free GPUs",
                rest_figure="jobs",
                filters={"gpu": "H100"},
                alerts={"free_at_least": 4},
                in_column=False,
            )
        self.assert_json_success(result)
        mine = {
            "install_id": self.gpus.id,
            "widget": "gpus",
            "shown": False,
            "position": 2,
            "label": "Free GPUs",
            "rest_figure": "jobs",
            "filters": {"gpu": "H100"},
            "alerts": {"free_at_least": 4},
            "in_column": False,
            "seen_at": None,
        }
        [(event, users)] = self.preference_events(caught)
        self.assertEqual(
            (event["op"], event["preference"], users), ("update", mine, [self.hamlet.id])
        )
        self.assertEqual(self.snapshot(self.hamlet), [mine])
        # Cordelia's view is the team's, untouched.
        self.assertEqual(self.snapshot(self.cordelia), [])

        # A field left out stays; the label and the figure go back with "".
        with self.events() as caught:
            self.assert_json_success(self.patch(self.hamlet, label="", rest_figure=""))
        [(event, _users)] = self.preference_events(caught)
        self.assertEqual(event["preference"], {**mine, "label": "", "rest_figure": ""})
        # reset puts the named fields back to the team's.
        self.assert_json_success(self.patch(self.hamlet, reset=["shown", "position", "in_column"]))
        row = PluginWidgetPreference.objects.get(user=self.hamlet, install=self.gpus)
        self.assertEqual((row.shown, row.position, row.in_column), (None, None, None))
        self.assertEqual((row.filters, row.alerts), ({"gpu": "H100"}, {"free_at_least": 4}))

        # DELETE is the team's view, every field.
        with self.events() as caught:
            self.assert_json_success(self.api_delete(self.hamlet, self.url()))
        [(event, users)] = self.preference_events(caught)
        self.assertEqual(
            (event, users),
            (
                {
                    "type": "armada_widget_preference",
                    "op": "remove",
                    "install_id": self.gpus.id,
                    "widget": "gpus",
                    "id": 0,
                },
                [self.hamlet.id],
            ),
        )
        self.assertFalse(PluginWidgetPreference.objects.filter(user=self.hamlet).exists())
        # Deleting what is not there says nothing to anyone.
        with self.events() as caught:
            self.assert_json_success(self.api_delete(self.hamlet, self.url()))
        self.assertEqual(self.preference_events(caught), [])

    def test_a_widget_opened_on_one_device_is_not_new_on_any(self) -> None:
        opened = datetime(2026, 10, 17, 9, 30, tzinfo=timezone.utc)
        # Opening it is a change of its own, and the first time is the one kept.
        with time_machine.travel(opened, tick=False), self.events() as caught:
            self.assert_json_success(self.patch(self.hamlet, seen=True))
        [(event, users)] = self.preference_events(caught)
        self.assertEqual(
            (event["preference"]["seen_at"], event["preference"]["shown"], users),
            (int(opened.timestamp()), None, [self.hamlet.id]),
        )
        with time_machine.travel(opened + timedelta(days=1), tick=False):
            self.assert_json_success(self.patch(self.hamlet, seen=True, label="Pods"))
        [mine] = self.snapshot(self.hamlet)
        self.assertEqual((mine["seen_at"], mine["label"]), (int(opened.timestamp()), "Pods"))
        # It is new to Cordelia still.
        self.assertEqual(self.snapshot(self.cordelia), [])

        # Going back to the team's view keeps it opened: an update, not a removal.
        with self.events() as caught:
            self.assert_json_success(self.api_delete(self.hamlet, self.url()))
        [(event, _users)] = self.preference_events(caught)
        self.assertEqual(
            (event["op"], event["preference"]["label"], event["preference"]["seen_at"]),
            ("update", "", int(opened.timestamp())),
        )
        self.assertEqual(self.snapshot(self.hamlet), [event["preference"]])

        # seen=false makes it new again; then the team's view is no row at all.
        self.assert_json_success(self.patch(self.hamlet, seen=False))
        self.assertIsNone(PluginWidgetPreference.objects.get(user=self.hamlet).seen_at)
        with self.events() as caught:
            self.assert_json_success(self.api_delete(self.hamlet, self.url()))
        [(event, _users)] = self.preference_events(caught)
        self.assertEqual(event["op"], "remove")
        self.assertFalse(PluginWidgetPreference.objects.filter(user=self.hamlet).exists())

    def test_only_what_the_manifest_declares(self) -> None:
        refused: list[tuple[dict[str, Any], str]] = [
            ({"rest_figure": "hottest"}, "GPUs has no figure named hottest."),
            ({"filters": {"zone": "a"}}, "GPUs has no facet named zone."),
            (
                {"filters": {"gpu": ["H100"]}},
                "A facet's value is a word, a number or true or false.",
            ),
            (
                {"filters": {"gpu": "H" * 81}},
                "A facet's value is a word, a number or true or false.",
            ),
            ({"alerts": {"busy": 2}}, "GPUs has no alert named busy."),
            (
                {"alerts": {"free_at_least": 65}},
                "When this many are free takes a number from 1 to 64.",
            ),
            (
                {"alerts": {"free_at_least": True}},
                "When this many are free takes a number from 1 to 64.",
            ),
            ({"label": "x" * 25}, "A widget's label is plain words, at most 24 characters."),
            ({"label": "<b>GPUs</b>"}, "A widget's label is plain words, at most 24 characters."),
            ({"position": 1000}, "A widget's place is a number from 0 to 999."),
            ({}, "Nothing to change."),
            ({"shown": True, "reset": ["shown"]}, "shown is both given and reset."),
        ]
        for params, error in refused:
            with self.subTest(params=params):
                self.assert_json_error(self.patch(self.hamlet, **params), error)
        # A widget opened from a row has no view of its own, and a key the
        # manifest does not have is refused.
        wandb = self.install(self.hamlet, "wandb")
        self.assert_json_error(
            self.patch(self.hamlet, install=wandb, widget="run", shown=False),
            "Weights & Biases has no widget named run in a nav or a column.",
        )
        # One in the nav alone has no list in the column, and the other way.
        manifest = self.manifest("wandb")
        manifest["id"] = "wandb-nav"
        manifest["provides"]["widgets"][0]["in"] = ["nav"]
        del manifest["provides"]["widgets"][0]["column_slots"]
        nav_only = install_plugin(
            self.hamlet,
            manifest=manifest,
            source_kind="github",
            source="github.com/plugyawn/armada-wandb-nav",
            sha=SHA,
            plugin_settings={"entity": "runix"},
        )
        self.assert_json_error(
            self.patch(self.hamlet, install=nav_only, widget="runs", in_column=True),
            "Training is not in the column.",
        )
        manifest["id"] = "wandb-column"
        manifest["provides"]["widgets"][0]["in"] = ["column"]
        manifest["provides"]["widgets"][0]["column_slots"] = 3
        del manifest["provides"]["widgets"][0]["team_default"]
        column_only = install_plugin(
            self.hamlet,
            manifest=copy.deepcopy(manifest),
            source_kind="github",
            source="github.com/plugyawn/armada-wandb-column",
            sha=SHA,
            plugin_settings={"entity": "runix"},
        )
        self.assert_json_error(
            self.patch(self.hamlet, install=column_only, widget="runs", shown=True),
            "Training is not in the nav.",
        )
        self.assert_json_success(
            self.patch(self.hamlet, install=column_only, widget="runs", in_column=False)
        )

    def test_who_may_change_a_view(self) -> None:
        self.assert_json_error(
            self.patch(self.example_user("polonius"), shown=False),
            "Guests cannot see, install, link or remove plugins.",
        )
        bot = UserProfile.objects.get(id=self.gpus.bot_id)
        self.assert_json_error(
            self.patch(bot, shown=False),
            "Plugins are installed, linked and removed by people, not bots.",
        )
        self.assert_json_error(
            self.patch(self.mit_user("starnine"), shown=False), "No such plugin", status_code=404
        )
        remove_plugin(self.hamlet, self.gpus)
        self.assert_json_error(self.patch(self.hamlet, shown=False), "GPUs is removed.")

    def test_a_view_follows_its_install(self) -> None:
        update_widget_preference(
            self.hamlet, self.gpus, "gpus", {"shown": False, "filters": {"gpu": "H100"}}
        )
        update_widget_preference(self.cordelia, self.gpus, "gpus", {"label": "Pods"})
        # Remove takes each person's view from their clients; the rows stay
        # for the week Undo lasts.
        with self.events() as caught:
            remove_plugin(self.hamlet, self.gpus)
        self.assertEqual(
            sorted((event["op"], users[0]) for event, users in self.preference_events(caught)),
            sorted([("remove", self.hamlet.id), ("remove", self.cordelia.id)]),
        )
        self.assertEqual(self.snapshot(self.hamlet), [])
        self.assertEqual(PluginWidgetPreference.objects.filter(install=self.gpus).count(), 2)
        # Undo brings them back, each to its own person.
        with self.events() as caught:
            restore_plugin(self.hamlet, self.gpus)
        back = self.preference_events(caught)
        self.assertEqual(
            sorted((event["preference"]["label"], users) for event, users in back),
            [("", [self.hamlet.id]), ("Pods", [self.cordelia.id])],
        )
        self.assert_length(self.snapshot(self.cordelia), 1)

        # An update that drops the widget drops everyone's view of it.
        new = self.manifest()
        new["version"] = "0.2.0"
        new["provides"]["widgets"][0]["key"] = "pods"
        with self.events() as caught:
            update_plugin_version(self.hamlet, self.gpus, sha="6" * 40, manifest=new)
        self.assertEqual(
            sorted(
                (event["op"], event["widget"]) for event, _users in self.preference_events(caught)
            ),
            [("remove", "gpus"), ("remove", "gpus")],
        )
        self.assertFalse(PluginWidgetPreference.objects.filter(install=self.gpus).exists())

        # A draft's discard takes its maker's view along.
        draft = install_plugin(
            self.hamlet,
            manifest=self.manifest("wandb"),
            source_kind="github",
            source="github.com/plugyawn/armada-wandb",
            sha=SHA,
            draft=True,
        )
        install, _declaration = access_widget(self.hamlet, draft.id, "runs")
        update_widget_preference(self.hamlet, install, "runs", {"shown": False})
        with self.events() as caught:
            remove_plugin(self.hamlet, draft)
        [(event, users)] = self.preference_events(caught)
        self.assertEqual((event["op"], users), ("remove", [self.hamlet.id]))


class WidgetPreferenceEventsAppliedTest(PreferenceTestMixin, BaseAction):
    """Each preference event, applied to a /register snapshot, gives what a
    fresh /register gives (verify_action), and matches the API's
    documentation of /events."""

    def test_events_keep_a_snapshot_current(self) -> None:
        kinds = ["armada_plugin", "armada_widget", "armada_widget_preference"]
        install = self.install(self.user_profile)
        with self.verify_action(event_types=kinds) as events:
            update_widget_preference(
                self.user_profile, install, "gpus", {"shown": False, "alerts": {"free_at_least": 2}}
            )
        check_armada_widget_preference_update_event("events[0]", events[0])
        with self.verify_action(event_types=kinds):
            update_widget_preference(self.user_profile, install, "gpus", {"shown": None})
        with self.verify_action(event_types=kinds) as events:
            update_widget_preference(self.user_profile, install, "gpus", {}, seen=True)
        self.assertIsNotNone(events[0]["preference"]["seen_at"])
        with self.verify_action(event_types=kinds, num_events=2) as events:
            remove_plugin(self.user_profile, install)
        check_armada_widget_preference_remove_event("events[1]", events[1])
        with self.verify_action(event_types=kinds, num_events=2):
            restore_plugin(self.user_profile, install)
        url = f"/api/v1/armada/widgets/{install.id}/gpus/preference"
        # Opened, so going back to the team's view is an update that keeps it.
        with self.verify_action(event_types=["armada_widget_preference"]) as events:
            self.api_delete(self.user_profile, url)
        check_armada_widget_preference_update_event("events[0]", events[0])
        with self.verify_action(event_types=kinds):
            update_widget_preference(self.user_profile, install, "gpus", {}, seen=False)
        with self.verify_action(event_types=["armada_widget_preference"]) as events:
            self.api_delete(self.user_profile, url)
        check_armada_widget_preference_remove_event("events[0]", events[0])


class WidgetPreferenceToolTest(PreferenceTestMixin, ZulipTestCase):
    """armada_widget_preference (zerver/lib/armada_plugin_tools.py), called
    as Codex calls it: it changes the view of the person the run acts for,
    on their own row alone, and the run's trace shows what it changed."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.cordelia = self.example_user("cordelia")
        self.gpus = self.install(self.hamlet)
        # The run the tool is called from: started by Hamlet's message.
        agent = self.create_test_bot(
            "agent",
            self.example_user("iago"),
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        message_id = self.send_stream_message(self.hamlet, "Denmark", "only the H100s, for me")
        AgentRun.objects.create(
            realm=self.hamlet.realm,
            bot_profile=agent,
            requesting_user=self.hamlet,
            trigger_message_id=message_id,
            stream_name="Denmark",
            topic_name="test",
            status=AgentRun.RUNNING,
        )
        self.trigger_message_id = message_id

    def call(self, arguments: object) -> tuple[dict[str, Any], dict[str, Any], list[Any]]:
        runner = ArmadaCodexAppServerToolRunner(
            codex_bin="/bin/false",
            workdir=None,
            model="gpt-6-astra",
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=5,
            output_limit=1000,
        )
        turn = ArmadaAppServerTurnContext(
            thread_id="thread-1",
            event_callback=None,
            user_input_provider=None,
            is_main=True,
            requesting_user_id=self.hamlet.id,
            trigger_message_id=self.trigger_message_id,
        )
        sent: list[dict[str, Any]] = []
        caught: list[Any] = []
        with (
            mock.patch.object(runner, "send_message", side_effect=sent.append),
            mock.patch("zerver.tornado.event_queue.process_notification", caught.append),
            self.captureOnCommitCallbacks(execute=True),
        ):
            runner.handle_server_request(
                {
                    "id": 7,
                    "method": "item/tool/call",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "callId": "call-1",
                        "tool": "armada_widget_preference",
                        "namespace": None,
                        "arguments": json.dumps(arguments),
                    },
                },
                turn_context=turn,
            )
        [reply] = sent
        notification = turn.messages.get_nowait()
        assert isinstance(notification, ArmadaAppServerNotification)
        [event] = notification.events
        return reply["result"], dict(event), caught

    def test_a_new_thread_is_offered_the_tool_and_its_version_follows(self) -> None:
        self.assertIn(
            "armada_widget_preference", [spec["name"] for spec in armada_codex_dynamic_tools()]
        )
        # The thread version is a fingerprint of the tools a new thread is
        # given: without this one, it is another, so topics start afresh.
        with mock.patch(
            "zerver.lib.armada_agent.armada_plugin_tool_specs",
            lambda: [
                spec
                for spec in armada_plugin_tool_specs()
                if spec["name"] != "armada_widget_preference"
            ],
        ):
            without = armada_codex_thread_version()
        self.assertNotEqual(without, armada_codex_thread_version())

    def test_the_asker_s_own_view_changes_and_no_one_else_s(self) -> None:
        result, event, caught = self.call(
            {
                "id": "gpus",
                "widget": "gpus",
                "filters": {"gpu": "H100"},
                "rest_figure": "jobs",
                "shown": None,
            }
        )
        self.assertTrue(result["success"])
        mine = {
            "install_id": self.gpus.id,
            "widget": "gpus",
            "shown": None,
            "position": None,
            "label": "",
            "rest_figure": "jobs",
            "filters": {"gpu": "H100"},
            "alerts": {},
            "in_column": None,
            "seen_at": None,
        }
        self.assertEqual(json.loads(result["contentItems"][0]["text"]), mine)
        # The trace's row says what changed, and for whom.
        self.assertEqual(
            (event["status"], event["title"]),
            ("done", "Changed GPUs for King Hamlet alone: filters, rest_figure, shown."),
        )
        # The event went to Hamlet's clients alone; Cordelia's view is the team's.
        [preference_event] = [
            sent for sent in caught if sent["event"]["type"] == "armada_widget_preference"
        ]
        self.assertEqual(
            (preference_event["event"]["preference"], list(preference_event["users"])),
            (mine, [self.hamlet.id]),
        )
        self.assertFalse(PluginWidgetPreference.objects.filter(user=self.cordelia).exists())

    def test_only_the_asker_s_view_and_only_what_is_declared(self) -> None:
        for arguments, refusal in (
            (
                {"id": "gpus", "widget": "gpus", "user_id": self.cordelia.id, "shown": False},
                "user_id is not part of a person's view of a widget.",
            ),
            (
                {"id": "gpus", "widget": "gpus", "filters": {"zone": "a"}},
                "GPUs has no facet named zone.",
            ),
            (
                {"id": "gpus", "widget": "gpus", "position": True},
                "position is a whole number or null.",
            ),
            ({"id": "gpus", "widget": "gpus", "label": 7}, "label is a string."),
            ({"id": "gpus", "widget": "gpus", "shown": "yes"}, "shown is true, false or null."),
            ({"id": "gpus", "widget": "gpus"}, "Nothing to change."),
            ({"id": "gpus", "widget": 7, "shown": False}, "widget must be a string."),
            ({"id": "wandb", "widget": "runs", "shown": False}, "No such plugin"),
            (
                {"id": "gpus", "widget": "machines", "shown": False},
                "GPUs has no widget named machines in a nav or a column.",
            ),
        ):
            with self.subTest(arguments=arguments):
                result, event, _caught = self.call(arguments)
                self.assertFalse(result["success"])
                self.assertEqual(event["title"], refusal)
        self.assertFalse(PluginWidgetPreference.objects.exists())

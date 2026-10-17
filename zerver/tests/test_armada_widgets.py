import copy
import json
import os
import re
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

import requests
import responses
import time_machine
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.lib.armada_mcp_client import ArmadaMCPClient, ArmadaMCPError
from zerver.lib.armada_plugin_events import fetch_armada_plugin_state
from zerver.lib.armada_plugins import (
    add_plugin_link,
    armada_plugin_token,
    install_plugin,
    remove_plugin,
    remove_plugin_link,
    set_plugin_state,
    update_plugin,
    update_plugin_version,
)
from zerver.lib.armada_widgets import flush_widget_events, mark_stale_widgets, push_widget
from zerver.lib.event_schema import (
    check_armada_plugin_add_or_update_event,
    check_armada_plugin_remove_event,
    check_armada_widget_remove_event,
    check_armada_widget_update_event,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import PluginInstall, PluginLink, PluginWidgetState, RealmAuditLog, UserProfile
from zerver.models.realm_audit_logs import AuditLogEventType
from zerver.models.users import active_user_ids
from zerver.tests.test_events import BaseAction

SHA = "4e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d5"
FAKE = Path(__file__).parent / "fixtures" / "armada_plugins" / "fake" / "armada_fake_plugin.py"
WHEN = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


class EventsCaught(ZulipTestCase):
    @contextmanager
    def events(self) -> Iterator[list[Any]]:
        """Every event sent, however many: capture_send_event_calls counts."""
        caught: list[Any] = []
        with (
            mock.patch("zerver.tornado.event_queue.process_notification", caught.append),
            self.captureOnCommitCallbacks(execute=True),
        ):
            yield caught


class WidgetTest(EventsCaught):
    """Widgets (zerver/lib/armada_widgets.py): a plugin's bot pushes its own
    widgets and nothing else's; who receives each document; at most one
    event every two seconds a widget and scope; a widget not fed turns
    stale; and a tool action is called on the plugin's MCP endpoint for the
    person who pressed it, once they confirmed it if it asks."""

    def fixture(self, plugin: str, name: str) -> dict[str, Any]:
        return json.loads(self.fixture_data(name, f"armada_plugins/{plugin}"))

    def install(self, plugin: str = "wandb", **changes: Any) -> PluginInstall:
        manifest = self.fixture(plugin, "armada-plugin.json")
        manifest.update(changes)
        settings = {"entity": "runix"} if plugin == "wandb" else {"manage_prefix": "nl"}
        return install_plugin(
            self.example_user("hamlet"),
            manifest=manifest,
            source_kind="github",
            source=f"github.com/plugyawn/armada-{plugin}",
            sha=SHA,
            plugin_settings=settings,
        )

    def bot(self, install: PluginInstall) -> UserProfile:
        return UserProfile.objects.get(id=install.bot_id)

    def push(self, install: PluginInstall, widget: str = "runs", **params: Any) -> Any:
        return self.api_post(
            self.bot(install),
            "/api/v1/armada/widgets",
            {
                "widget": widget,
                **{
                    name: value if isinstance(value, str) else json.dumps(value)
                    for name, value in params.items()
                },
            },
        )

    def runs(self, running: str = "3 running") -> dict[str, Any]:
        document = self.fixture("wandb", "runs.json")
        document["data"]["running"] = running
        return document

    def link(self, install: PluginInstall, stream: str, topic: str | None = None) -> PluginLink:
        return add_plugin_link(
            self.example_user("hamlet"),
            install,
            kind="project",
            value="runix/nextlat-scaling",
            stream_id=self.get_stream_id(stream),
            topic=topic,
        )

    def widget_events(self, events: list[Any]) -> list[tuple[str, dict[str, Any], set[int]]]:
        found = []
        for sent in events:
            event = {**sent["event"], "id": 0}
            if event["type"] == "armada_widget":
                if event["op"] == "update":
                    check_armada_widget_update_event("events[0]", event)
                else:
                    check_armada_widget_remove_event("events[0]", event)
                found.append((event["op"], event, set(sent["users"])))
        return found

    def test_a_plugins_bot_pushes_its_widgets_to_who_can_see_them(self) -> None:
        install = self.install()
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        self.make_stream("training", invite_only=True)
        self.subscribe(hamlet, "training")
        link = self.link(install, "training", "lr-sweep")
        with self.capture_send_event_calls(expected_num_events=1) as events:
            version = self.assert_json_success(self.push(install, document=self.runs()))["version"]
        self.assertEqual(version, 1)
        [(op, event, users)] = self.widget_events(events)
        # The nav is everyone's.
        self.assertEqual((op, event["link_id"], event["item"]), ("update", None, ""))
        self.assertEqual(users, set(active_user_ids(install.realm_id)))
        self.assertEqual(event["document"], self.runs())
        # A linked place's list goes to its channel's subscribers.
        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(self.push(install, document=self.runs(), link_id=link.id))
        [(op, event, users)] = self.widget_events(events)
        self.assertEqual(
            (event["link_id"], event["stream_id"], event["topic"]),
            (link.id, link.stream_id, "lr-sweep"),
        )
        self.assertEqual(users, {hamlet.id, install.bot_id})
        # A row's own document, opened from the list.
        run = self.fixture("wandb", "run.json")
        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(self.push(install, "run", document=run, item="lr-3e-4-b"))
        [(op, event, users)] = self.widget_events(events)
        self.assertEqual((event["widget"], event["item"]), ("run", "lr-3e-4-b"))

        # /register holds what each person may see, as the events did.
        def scopes(user: UserProfile) -> list[tuple[str, int | None, str]]:
            state = fetch_armada_plugin_state(user, want_plugins=True, want_widgets=True)
            return [
                (widget["widget"], widget["link_id"], widget["item"])
                for widget in state["armada_widgets"]
            ]

        self.assertEqual(
            scopes(hamlet), [("run", None, "lr-3e-4-b"), ("runs", None, ""), ("runs", link.id, "")]
        )
        self.assertEqual(scopes(cordelia), [("run", None, "lr-3e-4-b"), ("runs", None, "")])
        self.assertEqual(scopes(self.example_user("polonius")), scopes(cordelia))
        # A link to a private channel is shown to its readers alone.
        plugins = fetch_armada_plugin_state(cordelia, want_plugins=True, want_widgets=False)
        self.assertEqual(plugins["armada_plugins"][0]["links"], [])
        self.assertNotIn("armada_widgets", plugins)
        plugins = fetch_armada_plugin_state(hamlet, want_plugins=True, want_widgets=False)
        self.assertEqual([link["id"] for link in plugins["armada_plugins"][0]["links"]], [link.id])

    def test_a_bot_pushes_only_within_its_own_install(self) -> None:
        wandb = self.install()
        gpus = self.install("gpus")
        gpus_link = add_plugin_link(
            self.example_user("hamlet"),
            gpus,
            kind="pool",
            value="nlscale-a",
            stream_id=self.get_stream_id("Verona"),
            topic="lr-sweep",
        )
        hamlet = self.example_user("hamlet")
        refused = [
            # A person, or a bot of a person's, pushes nothing.
            (
                self.api_post(
                    hamlet,
                    "/api/v1/armada/widgets",
                    {"widget": "runs", "document": json.dumps(self.runs())},
                ),
                "Only a plugin's own bot pushes its widgets.",
            ),
            (
                self.api_post(
                    self.create_test_bot("helper", hamlet),
                    "/api/v1/armada/widgets",
                    {"widget": "runs", "document": json.dumps(self.runs())},
                ),
                "Only a plugin's own bot pushes its widgets.",
            ),
            # Another plugin's widget, or its link, is not this bot's.
            (
                self.push(wandb, "gpus", document=self.runs()),
                "Weights & Biases declares no widget named gpus.",
            ),
            (self.push(wandb, document=self.runs(), link_id=gpus_link.id), "No such link"),
            (
                self.push(wandb, "run", document=self.runs()),
                "run is not in the nav; it is pushed to a link.",
            ),
            (
                self.push(
                    gpus, "gpus", document=self.fixture("gpus", "gpus.json"), item="nlscale-a"
                ),
                "gpus is not opened from a row, so it has no item nlscale-a.",
            ),
            (
                self.push(wandb, document=self.runs(), data={"running": "4"}),
                "A push gives a widget's document, or data for the one it has.",
            ),
            (self.push(wandb), "A push gives a widget's document, or data for the one it has."),
            (
                self.push(wandb, data={"running": "4 running"}),
                "runs's document comes before its data.",
            ),
        ]
        for result, error in refused:
            self.assert_json_error(
                result, error, status_code=404 if error == "No such link" else 400
            )
        manifest = wandb.manifest
        manifest["provides"]["widgets"][0]["in"] = ["nav"]
        del manifest["provides"]["widgets"][0]["column_slots"]
        PluginInstall.objects.filter(id=wandb.id).update(manifest=manifest)
        wandb_link = self.link(wandb, "Verona")
        self.assert_json_error(
            self.push(wandb, document=self.runs(), link_id=wandb_link.id),
            "runs is not in the column, so it is not pushed to a link.",
        )
        # A plugin turned off pushes nothing; nor does another realm's.
        update_plugin(hamlet, gpus, disabled=True)
        self.assert_json_error(
            self.push(gpus, "gpus", document=self.fixture("gpus", "gpus.json")),
            "GPUs is turned off.",
        )
        lear = install_plugin(
            self.lear_user("cordelia"),
            manifest=self.fixture("wandb", "armada-plugin.json"),
            source_kind="github",
            source="github.com/plugyawn/armada-wandb",
            sha=SHA,
            plugin_settings={"entity": "runix"},
        )
        self.assert_json_error(
            self.api_post(
                self.bot(lear),
                "/api/v1/armada/widgets",
                {
                    "widget": "runs",
                    "document": json.dumps(self.runs()),
                    "link_id": str(wandb_link.id),
                },
                subdomain="lear",
            ),
            "No such link",
            status_code=404,
        )
        self.assertFalse(PluginWidgetState.objects.exists())

    def test_data_lands_in_the_document_and_is_checked_there(self) -> None:
        install = self.install()
        self.assert_json_success(self.push(install, document=self.runs()))
        self.assert_json_success(
            self.push(install, data={"running": "4 running", "news": "failed"})
        )
        state = PluginWidgetState.objects.get(install=install, widget="runs")
        self.assertEqual(
            (state.document["data"]["running"], state.document["data"]["news"], state.version),
            ("4 running", "failed", 2),
        )
        self.assertEqual(state.document["blocks"], self.runs()["blocks"])
        self.assert_json_error(
            self.push(install, data={"news": "red"}),
            "The widget is not valid: $.rest.news: 'red' is not failed, needs or empty",
        )
        # Rows are checked against the manifest too.
        runs = self.runs()
        runs["data"]["runs"][0]["open"] = {"widget": "runs"}
        self.assert_json_error(
            self.push(install, document=runs),
            "A row opens runs, which Weights & Biases does not open from a row.",
        )
        runs = self.runs()
        runs["data"]["runs"][0]["facets"] = {"gpu": "H100"}
        self.assert_json_error(
            self.push(install, document=runs),
            "A row's facet gpu is not one Weights & Biases declares.",
        )
        runs["data"]["runs"][0]["facets"] = {"user": "iago"}
        self.assert_json_success(self.push(install, document=runs))

    def test_at_most_one_event_every_two_seconds_a_widget_and_scope(self) -> None:
        install = self.install()

        def at(seconds: float) -> "time_machine.travel":
            return time_machine.travel(WHEN + timedelta(seconds=seconds), tick=False)

        def pushed(seconds: float, running: str) -> int:
            with at(seconds), self.events() as events:
                self.assert_json_success(self.push(install, document=self.runs(running)))
            return len(self.widget_events(events))

        self.assertEqual(pushed(0, "1 running"), 1)
        self.assertEqual(pushed(0.5, "2 running"), 0)
        self.assertEqual(pushed(1.0, "3 running"), 0)
        state = PluginWidgetState.objects.get(install=install)
        self.assertTrue(state.pending)
        with self.capture_send_event_calls(expected_num_events=0):
            self.assertEqual(flush_widget_events(WHEN + timedelta(seconds=1.9)), 0)
        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assertEqual(flush_widget_events(WHEN + timedelta(seconds=2.1)), 1)
        [(_op, event, _users)] = self.widget_events(events)
        # The newest document wins.
        self.assertEqual((event["document"]["data"]["running"], event["version"]), ("3 running", 3))
        state.refresh_from_db()
        self.assertFalse(state.pending)
        # The same document again keeps it fresh and tells no one.
        with at(3), self.capture_send_event_calls(expected_num_events=0):
            self.assert_json_success(self.push(install, document=self.runs("3 running")))
        state.refresh_from_db()
        self.assertEqual((state.version, state.date_fed), (3, WHEN + timedelta(seconds=3)))
        self.assertEqual(pushed(4.2, "4 running"), 1)

    def test_a_widget_not_fed_for_three_cadences_turns_stale(self) -> None:
        install = self.install()
        with time_machine.travel(WHEN, tick=False):
            self.assert_json_success(self.push(install, document=self.runs()))
        # Training's cadence is 30 s.
        with self.capture_send_event_calls(expected_num_events=0):
            self.assertEqual(mark_stale_widgets(WHEN + timedelta(seconds=90)), 0)
        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assertEqual(mark_stale_widgets(WHEN + timedelta(seconds=91)), 1)
        [(_op, event, _users)] = self.widget_events(events)
        self.assertTrue(event["stale"])
        self.assertEqual(mark_stale_widgets(WHEN + timedelta(seconds=200)), 0)
        # Fed again, the same document is fresh at once.
        with (
            time_machine.travel(WHEN + timedelta(seconds=201), tick=False),
            self.capture_send_event_calls(expected_num_events=1) as events,
        ):
            self.assert_json_success(self.push(install, document=self.runs()))
        [(_op, event, _users)] = self.widget_events(events)
        self.assertFalse(event["stale"])
        # A widget whose cadence the manifest does not give waits a minute
        # three times.
        manifest = install.manifest
        del manifest["provides"]["widgets"][0]["cadence"]
        PluginInstall.objects.filter(id=install.id).update(manifest=manifest)
        self.assertEqual(mark_stale_widgets(WHEN + timedelta(seconds=201 + 179)), 0)
        self.assertEqual(mark_stale_widgets(WHEN + timedelta(seconds=201 + 181)), 1)

    def test_a_drafts_pushes_reach_no_one(self) -> None:
        manifest = self.fixture("wandb", "armada-plugin.json")
        draft = install_plugin(
            self.example_user("hamlet"),
            manifest=manifest,
            source_kind="github",
            source="github.com/plugyawn/armada-wandb",
            sha=SHA,
            draft=True,
        )
        with self.capture_send_event_calls(expected_num_events=0):
            self.assert_json_success(self.push(draft, document=self.runs()))
        self.assertEqual(PluginWidgetState.objects.get(install=draft).document, self.runs())
        state = fetch_armada_plugin_state(
            self.example_user("hamlet"), want_plugins=True, want_widgets=True
        )
        self.assertEqual(
            ([plugin["state"] for plugin in state["armada_plugins"]], state["armada_widgets"]),
            (["draft"], []),
        )
        self.assertEqual(
            fetch_armada_plugin_state(
                self.example_user("cordelia"), want_plugins=True, want_widgets=True
            ),
            {"armada_plugins": [], "armada_widgets": []},
        )

    def test_the_first_push_after_a_start_is_in_recent(self) -> None:
        install = self.install()
        set_plugin_state(install, PluginInstall.RUNNING, detail="", failures=0)
        self.assert_json_success(self.push(install, document=self.runs()))
        self.assert_json_success(self.push(install, document=self.runs("4 running")))

        def started() -> list[dict[str, Any]]:
            return [
                row.extra_data
                for row in RealmAuditLog.objects.filter(
                    modified_user_id=install.bot_id, event_type=AuditLogEventType.PLUGIN_STARTED
                ).order_by("id")
            ]

        expected = {
            "install_id": install.id,
            "plugin": "wandb",
            "version": "0.4.1",
            "widget": "runs",
        }
        self.assertEqual(started(), [expected])
        install.refresh_from_db()
        set_plugin_state(install, PluginInstall.STARTING, detail="", failures=1)
        set_plugin_state(install, PluginInstall.RUNNING, detail="", failures=0)
        self.assert_json_success(self.push(install, document=self.runs("5 running")))
        self.assertEqual(started(), [expected, expected])

    def test_removals_take_documents_from_their_clients(self) -> None:
        hamlet = self.example_user("hamlet")
        install = self.install()
        link = self.link(install, "Verona", "lr-sweep")
        self.assert_json_success(self.push(install, document=self.runs()))
        self.assert_json_success(self.push(install, document=self.runs(), link_id=link.id))
        self.assert_json_success(
            self.push(install, "run", document=self.fixture("wandb", "run.json"), item="lr-3e-4-b")
        )
        with self.events() as events:
            remove_plugin_link(hamlet, install, link.id)
        self.assertEqual(
            [(op, event["link_id"]) for op, event, users in self.widget_events(events)],
            [("remove", link.id)],
        )
        new = copy.deepcopy(install.manifest)
        new["provides"]["widgets"] = new["provides"]["widgets"][:1]
        with self.events() as events:
            update_plugin_version(hamlet, install, sha="7" * 40, manifest=new)
        self.assertEqual(
            [
                (op, event["widget"], event["item"])
                for op, event, users in self.widget_events(events)
            ],
            [("remove", "run", "lr-3e-4-b")],
        )
        self.assertEqual(list(PluginWidgetState.objects.values_list("widget", flat=True)), ["runs"])
        with self.events() as events:
            remove_plugin(hamlet, install)
        removed = [sent["event"] for sent in events if sent["event"]["type"] == "armada_plugin"]
        self.assertEqual(
            removed, [{"type": "armada_plugin", "op": "remove", "install_id": install.id}]
        )
        check_armada_plugin_remove_event("events[0]", {**removed[0], "id": 0})
        self.assertFalse(PluginWidgetState.objects.exists())


class PluginEventTest(EventsCaught):
    """armada_plugin events: everyone sees an install, a draft only its
    maker, and each person only the links they can read."""

    def plugin_events(self, events: list[Any]) -> list[tuple[str, dict[str, Any], set[int]]]:
        found = []
        for sent in events:
            event = {**sent["event"], "id": 0}
            if event["type"] == "armada_plugin":
                if event["op"] == "remove":
                    check_armada_plugin_remove_event("events[0]", event)
                else:
                    check_armada_plugin_add_or_update_event("events[0]", event)
                found.append((event["op"], event, set(sent["users"])))
        return found

    def test_who_sees_an_install_and_its_links(self) -> None:
        hamlet = self.example_user("hamlet")
        manifest = json.loads(self.fixture_data("armada-plugin.json", "armada_plugins/wandb"))
        with self.events() as events:
            install = install_plugin(
                hamlet,
                manifest=manifest,
                source_kind="github",
                source="github.com/plugyawn/armada-wandb",
                sha=SHA,
                plugin_settings={"entity": "runix"},
            )
        [(op, event, users)] = self.plugin_events(events)
        self.assertEqual(op, "add")
        self.assertEqual(users, set(active_user_ids(install.realm_id)))
        self.assertEqual(
            {
                key: event["plugin"][key]
                for key in ("id", "plugin", "name", "glyph", "state", "links")
            },
            {
                "id": install.id,
                "plugin": "wandb",
                "name": "Weights & Biases",
                "glyph": "chart",
                "state": "waiting",
                "links": [],
            },
        )
        # A person's settings and secrets are not in what everyone sees.
        self.assertNotIn("settings", event["plugin"])
        self.make_stream("training", invite_only=True)
        self.subscribe(hamlet, "training")
        with self.events() as events:
            secret = add_plugin_link(
                hamlet,
                install,
                kind="project",
                value="runix/secret-run",
                stream_id=self.get_stream_id("training"),
                topic=None,
            )
        groups = {
            frozenset(users): [link["id"] for link in event["plugin"]["links"]]
            for op, event, users in self.plugin_events(events)
        }
        readers = frozenset({hamlet.id, install.bot_id})
        self.assertEqual(groups[readers], [secret.id])
        self.assertEqual([links for users, links in groups.items() if users != readers], [[]])
        # A public channel's link is read by every member, and by a guest
        # only where the guest is subscribed.
        self.make_stream("open-runs")
        with self.events() as events:
            public = add_plugin_link(
                hamlet,
                install,
                kind="project",
                value="runix/open-run",
                stream_id=self.get_stream_id("open-runs"),
                topic=None,
            )
        by_user = {
            user_id: [link["id"] for link in event["plugin"]["links"]]
            for op, event, users in self.plugin_events(events)
            for user_id in users
        }
        polonius = self.example_user("polonius")
        self.assertEqual(by_user[hamlet.id], [secret.id, public.id])
        self.assertEqual(by_user[self.example_user("cordelia").id], [public.id])
        self.assertEqual(by_user[polonius.id], [])
        self.subscribe(polonius, "open-runs")
        state = fetch_armada_plugin_state(polonius, want_plugins=True, want_widgets=False)
        self.assertEqual([link["id"] for link in state["armada_plugins"][0]["links"]], [public.id])

    def test_a_drafts_events_go_to_its_maker(self) -> None:
        hamlet = self.example_user("hamlet")
        manifest = json.loads(self.fixture_data("armada-plugin.json", "armada_plugins/wandb"))
        with self.events() as events:
            draft = install_plugin(
                hamlet,
                manifest=manifest,
                source_kind="github",
                source="github.com/plugyawn/armada-wandb",
                sha=SHA,
                draft=True,
            )
        [(op, event, users)] = self.plugin_events(events)
        self.assertEqual((op, event["plugin"]["state"], users), ("add", "draft", {hamlet.id}))
        with self.events() as events:
            install_plugin(
                hamlet,
                manifest=manifest,
                source_kind="github",
                source="github.com/plugyawn/armada-wandb",
                sha=SHA,
                plugin_settings={"entity": "runix"},
            )
        [(op, event, users)] = self.plugin_events(events)
        # Installed, it waits for its API key, as everyone now sees.
        self.assertEqual((op, event["plugin"]["state"]), ("add", "waiting"))
        self.assertEqual(users, set(active_user_ids(draft.realm_id)))


class MCPClientTest(ZulipTestCase):
    """The MCP client (zerver/lib/armada_mcp_client.py), against answers
    recorded with responses: JSON and server-sent events, the session, the
    revision, pages of tools, and each way a call can fail."""

    URL = "http://127.0.0.1:47101/mcp"

    @override
    def setUp(self) -> None:
        super().setUp()
        self.network = responses.RequestsMock()
        self.network.start()
        self.addCleanup(self.network.stop)
        self.received: list[dict[str, Any]] = []

    def serve(self, *answers: Any, sse: bool = False) -> None:
        queue = list(answers)

        def answer(request: requests.PreparedRequest) -> tuple[int, dict[str, str], str]:
            assert request.body is not None
            message = json.loads(request.body)
            self.received.append({"message": message, "headers": dict(request.headers)})
            reply = queue.pop(0)
            if isinstance(reply, int):
                return reply, {}, ""
            if callable(reply):
                reply = reply(message)
            if sse:
                return (
                    200,
                    {"Content-Type": "text/event-stream", "Mcp-Session-Id": "s1"},
                    (
                        ": a comment\r\n\r\nevent: message\r\ndata: "
                        + json.dumps(reply)
                        + "\r\n\r\n"
                    ),
                )
            return (
                200,
                {"Content-Type": "application/json", "Mcp-Session-Id": "s1"},
                json.dumps(reply),
            )

        self.network.add_callback(responses.POST, self.URL, callback=answer)

    def mcp(self) -> ArmadaMCPClient:
        return ArmadaMCPClient(self.URL, token="t0ken", session=requests.Session())

    @staticmethod
    def result(value: Any) -> Any:
        return lambda message: {"jsonrpc": "2.0", "id": message["id"], "result": value}

    def initialized(self) -> list[Any]:
        return [self.result({"protocolVersion": "2025-03-26"}), 202]

    def test_a_session_lists_tools_page_by_page_and_calls_one(self) -> None:
        self.serve(
            *self.initialized(),
            self.result({"tools": [{"name": "a"}, "junk"], "nextCursor": "2"}),
            self.result({"tools": [{"name": "b"}]}),
            self.result({"content": [{"type": "text", "text": "ok"}]}),
            sse=True,
        )
        self.network.add(responses.DELETE, self.URL, status=405)
        with self.mcp() as client:
            self.assertEqual([tool["name"] for tool in client.list_tools()], ["a", "b"])
            self.assertEqual(
                client.call_tool("a", {"n": 1}, meta={"armada/user": {"id": 1}}),
                {"content": [{"type": "text", "text": "ok"}]},
            )
        initialize, initialized, first, second, call = (item["message"] for item in self.received)
        self.assertEqual(initialize["params"]["protocolVersion"], "2025-06-18")
        self.assertEqual(
            initialized, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        self.assertEqual((first["params"], second["params"]), ({}, {"cursor": "2"}))
        self.assertEqual(
            call["params"],
            {"name": "a", "arguments": {"n": 1}, "_meta": {"armada/user": {"id": 1}}},
        )
        # The first request carries no session; every later one carries the
        # server's, and the revision agreed on.
        headers = [item["headers"] for item in self.received]
        self.assertNotIn("Mcp-Session-Id", headers[0])
        self.assertEqual(headers[0]["Authorization"], "Bearer t0ken")
        self.assertEqual(
            {(header["Mcp-Session-Id"], header["MCP-Protocol-Version"]) for header in headers[1:]},
            {("s1", "2025-03-26")},
        )
        [delete] = [call for call in self.network.calls if call.request.method == "DELETE"]
        self.assertEqual(delete.request.headers["Mcp-Session-Id"], "s1")

    def test_each_way_a_call_fails(self) -> None:
        cases: list[tuple[list[Any], str]] = [
            ([401], "answered initialize with HTTP 401"),
            (
                [self.result({"protocolVersion": "2099-01-01"})],
                "speaks an MCP revision Armada does not: 2099-01-01",
            ),
            (
                [lambda message: {"jsonrpc": "2.0", "id": message["id"] + 5, "result": {}}],
                "did not answer initialize",
            ),
            (
                [
                    *self.initialized(),
                    lambda message: {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32602, "message": "unknown tool"},
                    },
                ],
                "refused tools/call: unknown tool",
            ),
            (
                [
                    *self.initialized(),
                    lambda message: {"jsonrpc": "2.0", "id": message["id"], "error": "?"},
                ],
                "refused tools/call",
            ),
            (
                [*self.initialized(), self.result(["not", "a", "result"])],
                "answered tools/call with something other than a result",
            ),
        ]
        for answers, error in cases:
            self.network.reset()
            self.serve(*answers)
            client = self.mcp()
            with self.assertRaisesRegex(ArmadaMCPError, f"^{re.escape(error)}$"):
                client.initialize()
                client.call_tool("x", {}, meta={})
        self.network.reset()
        self.network.add(responses.POST, self.URL, body="not json", content_type="application/json")
        with self.assertRaisesRegex(
            ArmadaMCPError, r"^answered initialize with something other than JSON$"
        ):
            self.mcp().initialize()
        self.network.reset()
        self.network.add(responses.POST, self.URL, body=requests.ConnectionError("refused"))
        with self.assertRaisesRegex(ArmadaMCPError, r"^could not be reached: ConnectionError$"):
            self.mcp().initialize()
        # Closing a session the server never gave asks nothing.
        self.mcp().close()


class ToolActionTest(ZulipTestCase):
    """A widget's tool action, run on the plugin's real MCP endpoint (the
    fake plugin, started here), for the person who pressed it."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.network = responses.RequestsMock(assert_all_requests_are_fired=False)
        self.network.add_passthru(re.compile(r"^http://127\.0\.0\.1:[0-9]+/"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.hamlet = self.example_user("hamlet")
        manifest = json.loads(self.fixture_data("armada-plugin.json", "armada_plugins/gpus"))
        self.install = install_plugin(
            self.hamlet,
            manifest=manifest,
            source_kind="github",
            source="github.com/plugyawn/armada-gpus",
            sha=SHA,
            plugin_settings={"manage_prefix": "nl"},
        )
        self.document = json.loads(self.fixture_data("gpus.json", "armada_plugins/gpus"))
        self.document["actions"].append(
            {"label": "Echo", "tool": "echo", "arguments": {"count": 8, "gpu": "H100"}}
        )
        self.document["actions"].append({"label": "Ask", "prompt": "Find 8 GPUs"})
        bot = UserProfile.objects.get(id=self.install.bot_id)
        self.assert_json_success(
            self.api_post(
                bot,
                "/api/v1/armada/widgets",
                {"widget": "gpus", "document": json.dumps(self.document)},
            )
        )

    def serve(self, **settings: Any) -> None:
        """The fake plugin, on a port of its own, as the host would start it."""
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        PluginInstall.objects.filter(id=self.install.id).update(port=port)
        self.install.refresh_from_db()
        process = subprocess.Popen(
            ["python3", str(FAKE)],
            env={
                "PATH": os.environ["PATH"],
                "ARMADA_PORT": str(port),
                "ARMADA_PLUGIN_TOKEN": armada_plugin_token(self.install),
                "ARMADA_SETTINGS": json.dumps(settings),
                "ARMADA_EMAIL": self.install.bot.email,
                "ARMADA_API_KEY": self.install.bot.api_key,
            },
            stdout=subprocess.DEVNULL,
        )
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if requests.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                    return
            except requests.ConnectionError:
                time.sleep(0.05)
        raise AssertionError("the fake plugin did not start")

    def press(self, user: UserProfile, **params: Any) -> Any:
        self.login_user(user)
        return self.client_post(
            f"/json/armada/plugins/{self.install.id}/actions",
            {
                "widget": "gpus",
                **{
                    name: value if isinstance(value, str) else json.dumps(value)
                    for name, value in params.items()
                },
            },
        )

    def test_a_tool_is_called_for_the_person_who_pressed(self) -> None:
        self.serve()
        result = self.assert_json_success(self.press(self.hamlet, index=1))
        self.assertFalse(result["is_error"])
        self.assertEqual(
            json.loads(result["text"]),
            {
                "arguments": {"count": 8, "gpu": "H100"},
                "_meta": {
                    "armada/user": {
                        "id": self.hamlet.id,
                        "email": self.hamlet.email,
                        "full_name": self.hamlet.full_name,
                    }
                },
            },
        )
        [audit] = RealmAuditLog.objects.filter(event_type=AuditLogEventType.PLUGIN_TOOL_CALLED)
        self.assertEqual(
            (audit.acting_user_id, audit.modified_user_id, audit.extra_data),
            (
                self.hamlet.id,
                self.install.bot_id,
                {
                    "install_id": self.install.id,
                    "plugin": "gpus",
                    "tool": "echo",
                    "widget": "gpus",
                    "link_id": None,
                    "item": "",
                    "row": None,
                    "is_error": False,
                },
            ),
        )

    def test_an_action_that_cannot_be_undone_waits_for_its_confirmation(self) -> None:
        self.serve(sse=True)
        self.assert_json_error(
            self.press(self.hamlet, row="nlscale-c"),
            "End it needs confirming: End nlscale-c? It has not started yet.",
        )
        self.assertFalse(
            RealmAuditLog.objects.filter(event_type=AuditLogEventType.PLUGIN_TOOL_CALLED).exists()
        )
        result = self.assert_json_success(self.press(self.hamlet, row="nlscale-c", confirmed=True))
        self.assertEqual(
            result, {"result": "success", "msg": "", "text": "No such pod.", "is_error": True}
        )

    def test_what_a_press_cannot_do(self) -> None:
        self.serve()
        for params, error in (
            ({"index": 0}, "GPUs refused tools/call: unknown tool."),
            ({"index": 2}, "That action asks the agent or opens a page, which the app does."),
            ({"index": 9}, "No such action"),
            ({"row": "lab-4090"}, "No such action"),
            ({"row": "no-such-row"}, "No such action"),
        ):
            self.assert_json_error(self.press(self.hamlet, **params), error)
        self.assert_json_error(
            self.press(self.hamlet, item="x", index=1), "No such widget", status_code=404
        )
        self.assert_json_error(
            self.press(self.example_user("polonius"), index=1),
            "Guests cannot see, install, link or remove plugins.",
        )
        # A linked place's action is for those who can read its channel.
        self.make_stream("pods", invite_only=True)
        self.subscribe(self.hamlet, "pods")
        link = add_plugin_link(
            self.hamlet,
            self.install,
            kind="pool",
            value="nlscale-a",
            stream_id=self.get_stream_id("pods"),
            topic="lr-sweep",
        )
        bot = UserProfile.objects.get(id=self.install.bot_id)
        self.assert_json_success(
            self.api_post(
                bot,
                "/api/v1/armada/widgets",
                {"widget": "gpus", "document": json.dumps(self.document), "link_id": str(link.id)},
            )
        )
        self.assert_json_success(self.press(self.hamlet, index=1, link_id=link.id))
        self.assert_json_error(
            self.press(self.example_user("cordelia"), index=1, link_id=link.id),
            "Invalid channel ID",
        )
        # Recent tells that press only to those who can read its channel.
        recent_url = f"/json/armada/plugins/{self.install.id}/recent"
        self.login_user(self.hamlet)
        [press, *_] = self.assert_json_success(self.client_get(recent_url))["recent"]
        self.assertEqual(
            (press["type"], press["stream_id"], press["detail"]["link_id"]),
            ("tool_called", self.get_stream_id("pods"), link.id),
        )
        self.login("cordelia")
        self.assertNotIn(
            self.get_stream_id("pods"),
            [
                line["stream_id"]
                for line in self.assert_json_success(self.client_get(recent_url))["recent"]
            ],
        )
        # A plugin with no tools, turned off, or not answering.
        manifest = self.install.manifest
        tools = manifest["provides"].pop("tools")
        PluginInstall.objects.filter(id=self.install.id).update(manifest=manifest)
        self.assert_json_error(self.press(self.hamlet, index=1), "GPUs has no tools.")
        manifest["provides"]["tools"] = tools
        PluginInstall.objects.filter(id=self.install.id).update(manifest=manifest, port=1)
        self.assert_json_error(
            self.press(self.hamlet, index=1), "GPUs could not be reached: ConnectionError."
        )
        update_plugin(self.hamlet, self.install, disabled=True)
        self.assert_json_error(self.press(self.hamlet, index=1), "GPUs is turned off.")


class PluginEventsAppliedTest(BaseAction):
    """Each plugin and widget event, applied to a /register snapshot, gives
    what a fresh /register gives (verify_action), and matches the API's
    documentation of /events."""

    def test_events_keep_a_snapshot_current(self) -> None:
        hamlet = self.user_profile
        manifest = json.loads(self.fixture_data("armada-plugin.json", "armada_plugins/wandb"))
        runs = json.loads(self.fixture_data("runs.json", "armada_plugins/wandb"))
        kinds = ["armada_plugin", "armada_widget"]
        with self.verify_action(event_types=kinds) as events:
            install = install_plugin(
                hamlet,
                manifest=manifest,
                source_kind="github",
                source="github.com/plugyawn/armada-wandb",
                sha=SHA,
                plugin_settings={"entity": "runix"},
            )
        check_armada_plugin_add_or_update_event("events[0]", events[0])
        bot = UserProfile.objects.get(id=install.bot_id)
        with self.verify_action(event_types=kinds) as events:
            push_widget(bot, widget="runs", link_id=None, item="", document=runs, data=None)
        check_armada_widget_update_event("events[0]", events[0])
        with self.verify_action(event_types=kinds) as events:
            link = add_plugin_link(
                hamlet,
                install,
                kind="project",
                value="runix/nextlat-scaling",
                stream_id=self.get_stream_id("Verona"),
                topic="lr-sweep",
            )
        with self.verify_action(event_types=kinds):
            push_widget(bot, widget="runs", link_id=link.id, item="", document=runs, data=None)
        with (
            time_machine.travel(timezone_now() + timedelta(seconds=300), tick=False),
            self.verify_action(event_types=kinds, num_events=2) as events,
        ):
            mark_stale_widgets(timezone_now())
        self.assertEqual([event["stale"] for event in events], [True, True])
        with self.verify_action(event_types=kinds, num_events=2) as events:
            remove_plugin_link(hamlet, install, link.id)
        check_armada_widget_remove_event("events[0]", events[0])
        with self.verify_action(event_types=kinds):
            update_plugin(hamlet, install, widget_defaults={"runs": {"nav": "offered"}})
        # Its one widget left goes, and then the plugin.
        with self.verify_action(event_types=kinds, num_events=2) as events:
            remove_plugin(hamlet, install)
        check_armada_widget_remove_event("events[0]", events[0])
        check_armada_plugin_remove_event("events[1]", events[1])

    def test_a_draft_appears_to_its_maker_then_to_everyone(self) -> None:
        manifest = json.loads(self.fixture_data("armada-plugin.json", "armada_plugins/wandb"))
        kinds = ["armada_plugin", "armada_widget"]
        where = {
            "manifest": manifest,
            "source_kind": "github",
            "source": "github.com/plugyawn/armada-wandb",
            "sha": SHA,
        }
        with self.verify_action(event_types=kinds):
            draft = install_plugin(self.user_profile, draft=True, **where)
        with self.verify_action(event_types=kinds):
            install_plugin(self.user_profile, plugin_settings={"entity": "runix"}, **where)
        # Someone else's draft reaches no one else.
        cordelia = self.example_user("cordelia")
        other = json.loads(self.fixture_data("armada-plugin.json", "armada_plugins/gpus"))
        with self.verify_action(event_types=kinds, state_change_expected=False, num_events=0):
            install_plugin(
                cordelia,
                manifest=other,
                source_kind="github",
                source="github.com/plugyawn/armada-gpus",
                sha=SHA,
                draft=True,
            )
        self.assertEqual(draft.state, PluginInstall.DRAFT)

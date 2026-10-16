import importlib
from datetime import timedelta
from typing import Any

from django.apps import apps
from django.db import connection
from typing_extensions import override

from zerver.lib.armada import (
    ARMADA_DEFAULT_STREAM,
    ARMADA_DEFAULT_TOPIC,
    ARMADA_EMBEDDED_BOT_SERVICE,
)
from zerver.lib.armada_run_card import (
    rebuild_armada_run_card,
    record_agent_run_event,
    serialize_armada_run_card,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserProfile
from zerver.models.bots import AgentRun, AgentRunEvent


def plan(*steps: tuple[str, str]) -> dict[str, Any]:
    return {
        "kind": "plan",
        "metadata": {"steps": [{"step": step, "status": status} for step, status in steps]},
    }


def child(
    thread_id: str, *, method: str = "", kind: str = "agent", **metadata: Any
) -> dict[str, Any]:
    return {
        "kind": kind,
        "title": f"{thread_id} {method or kind}",
        "metadata": {"codex_thread_id": thread_id, "codex_method": method, **metadata},
    }


def session(thread_id: str) -> dict[str, Any]:
    return {
        "kind": "session",
        "metadata": {
            "codex_thread_id": thread_id,
            "thread": {
                "id": thread_id,
                "parent_thread_id": "root-thread",
                "agent_nickname": thread_id.title(),
                "status": {"type": "active"},
            },
        },
    }


class ArmadaRunCardTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        owner = self.example_user("hamlet")
        agent = self.create_test_bot(
            "armada-card",
            owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.agent_run = AgentRun.objects.create(
            realm=owner.realm,
            bot_profile=agent,
            requesting_user=owner,
            trigger_message_id=5150,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            status=AgentRun.RUNNING,
            codex_thread_id="root-thread",
        )

    def record(self, event_type: str, payload: dict[str, Any]) -> AgentRunEvent:
        return record_agent_run_event(
            self.agent_run, event_type, summary=event_type, payload=payload
        )

    def test_card_follows_the_newest_events_and_is_what_the_events_rebuild(self) -> None:
        self.record("queued", {})
        self.record("usage", {"usage": {"input_tokens": 100, "cached_input_tokens": 50}})
        self.record("transcript", plan(("Read", "in_progress"), ("Change", "pending")))
        self.record("presentation", {"overview": "Reading  the code", "current_step": "Read"})
        self.record("agent", session("scout"))
        self.record("agent", child("scout", method="turn/started"))
        self.record("agent", session("builder"))
        self.record("agent", child("builder", kind="command"))
        self.record("tool_results", {"tool_count": 3})
        self.record("agent", child("scout", method="turn/completed", turn_status="failed"))
        # A plan update with no steps does not hide the last good plan.
        self.record("transcript", {"kind": "plan", "metadata": {"steps": []}})
        usage = self.record(
            "transcript",
            {"kind": "status", "metadata": {"usage": {"input_tokens": 400, "output_tokens": 7}}},
        )
        self.record("transcript", {"kind": "assistant", "body": "Done reading."})
        self.record("tool_results", {"tool_count": 2})
        last = self.record("transcript", plan(("Read", "completed"), ("Change", "in_progress")))

        stored = AgentRun.objects.get(id=self.agent_run.id)
        self.assertEqual(stored.card, self.agent_run.card)
        self.assertEqual(stored.last_event_id, last.id)
        self.assertEqual(stored.tool_count, 5)
        events = list(AgentRunEvent.objects.filter(run=self.agent_run))
        self.assertEqual(rebuild_armada_run_card(stored, events), stored.card)

        card = serialize_armada_run_card(stored)
        card_usage = card["usage"]
        assert isinstance(card_usage, dict)
        self.assertEqual(card_usage["input_tokens"], 400)
        self.assertEqual(card_usage["output_tokens"], 7)
        self.assertEqual(card_usage["event_id"], usage.id)
        self.assertIn("400", card_usage["summary"])
        self.assertEqual(
            card["plan"],
            [{"step": "Read", "status": "completed"}, {"step": "Change", "status": "in_progress"}],
        )
        # The overview was of "Read"; the plan has moved on.
        self.assertIsNone(card["overview"])
        self.assertEqual(card["tool_count"], 5)
        agents = card["agents"]
        assert isinstance(agents, list)
        self.assertEqual(
            [(agent["thread_id"], agent["status"]) for agent in agents],
            [("scout", "failed"), ("builder", "active")],
        )
        self.assertEqual(agents[1]["current_activity"], "builder command")

        self.record("presentation", {"overview": "Changing  the code", "current_step": "Change"})
        self.assertEqual(serialize_armada_run_card(self.agent_run)["overview"], "Changing the code")
        # A plan update without its checklist changes nothing either.
        self.record("transcript", {"kind": "plan", "metadata": {}})
        self.assertEqual(serialize_armada_run_card(self.agent_run)["plan"], card["plan"])

        # A finished run's usage carries how long it took.
        self.agent_run.status = AgentRun.DONE
        self.agent_run.date_finished = self.agent_run.date_created + timedelta(seconds=90)
        finished_usage = serialize_armada_run_card(self.agent_run)["usage"]
        assert isinstance(finished_usage, dict)
        self.assertEqual(finished_usage["duration_seconds"], 90.0)

    def test_a_run_without_events_has_an_empty_card(self) -> None:
        self.assertEqual(
            serialize_armada_run_card(self.agent_run),
            {"usage": None, "tool_count": 0, "plan": None, "overview": None, "agents": []},
        )
        self.assertEqual(rebuild_armada_run_card(self.agent_run, []), {})

    def test_the_newest_event_id_never_goes_back(self) -> None:
        first = self.record("queued", {})
        # As if a writer holding a newer event had committed first.
        AgentRun.objects.filter(id=self.agent_run.id).update(last_event_id=first.id + 1000)
        self.record("transcript", {"kind": "assistant", "body": "later"})
        self.assertEqual(AgentRun.objects.get(id=self.agent_run.id).last_event_id, first.id + 1000)

    def test_existing_runs_get_their_cards_from_their_events(self) -> None:
        events = [
            ("usage", {"usage": {"input_tokens": 10}}),
            ("transcript", plan(("Read", "in_progress"))),
            ("presentation", {"overview": "Reading", "current_step": "Read"}),
            ("agent", session("scout")),
            ("agent", child("scout", method="turn/started")),
        ]
        # Written before runs had cards, straight to the table.
        for event_type, payload in events:
            AgentRunEvent.objects.create(run=self.agent_run, event_type=event_type, payload=payload)
        empty = AgentRun.objects.create(
            realm=self.agent_run.realm,
            bot_profile=self.agent_run.bot_profile,
            trigger_message_id=5151,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            status=AgentRun.DONE,
        )
        migration = importlib.import_module("zerver.migrations.0820_agentrun_card")
        migration.backfill_run_cards(apps, connection.schema_editor())

        backfilled = AgentRun.objects.get(id=self.agent_run.id)
        written = list(AgentRunEvent.objects.filter(run=self.agent_run).order_by("id"))
        self.assertEqual(backfilled.last_event_id, written[-1].id)
        self.assertEqual(backfilled.card, rebuild_armada_run_card(backfilled, written))
        self.assertEqual(serialize_armada_run_card(backfilled)["overview"], "Reading")
        agents = backfilled.card["agents"]
        self.assertEqual([agent["thread_id"] for agent in agents], ["scout"])
        empty.refresh_from_db()
        self.assertIsNone(empty.last_event_id)
        self.assertEqual(empty.card, {})

"""What Armada's hot paths cost: queries per request or per event.

Each test pins a cost that a topic open, a run event or a worktree read
pays many times a minute, so that a change that adds a query to one of
them has to change a number here.
"""

from typing import Any
from unittest.mock import patch

import orjson
from django.db import connection
from typing_extensions import override

from zerver.actions.armada import (
    ARMADA_RUN_RECIPIENTS_SECONDS,
    armada_run_recipient_ids,
    armada_run_recipients,
    send_armada_run_event,
)
from zerver.lib.armada import (
    ARMADA_DEFAULT_STREAM,
    ARMADA_DEFAULT_TOPIC,
    ARMADA_EMBEDDED_BOT_SERVICE,
)
from zerver.lib.armada_run_card import record_agent_run_event
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import queries_captured
from zerver.models import UserProfile
from zerver.models.bots import AgentRun, AgentRunEvent, Workspace


class ArmadaRequestCostTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        armada_run_recipients.clear()
        self.addCleanup(armada_run_recipients.clear)
        self.owner = self.example_user("hamlet")
        self.subscribe(self.owner, ARMADA_DEFAULT_STREAM)
        self.agent = self.create_test_bot(
            "armada-costs",
            self.owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )

    def make_run(self, status: str = AgentRun.RUNNING) -> AgentRun:
        trigger_message_id = self.send_stream_message(
            self.owner,
            ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            content="check the repository",
        )
        return AgentRun.objects.create(
            realm=self.owner.realm,
            bot_profile=self.agent,
            requesting_user=self.owner,
            trigger_message_id=trigger_message_id,
            status=status,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
        )

    def test_live_run_events_reuse_their_recipients(self) -> None:
        run = self.make_run()
        event = AgentRunEvent.objects.create(run=run, event_type="transcript", summary="Reading")
        with patch("zerver.actions.armada.send_event_on_commit") as send_event:
            # The trigger message, its channel, the subscribers and the
            # people holding the message.
            with self.assert_database_query_count(4):
                send_armada_run_event(run, event)
            with self.assert_database_query_count(0):
                send_armada_run_event(run, event)
            self.assertNotIn(self.example_user("cordelia").id, send_event.call_args.args[2])

            self.subscribe(self.example_user("cordelia"), ARMADA_DEFAULT_STREAM)
            # Once the recipients are older than they may be, they are
            # read again.
            expires_at, user_ids = armada_run_recipients[run.id]
            armada_run_recipients[run.id] = (expires_at - ARMADA_RUN_RECIPIENTS_SECONDS, user_ids)
            with self.assert_database_query_count(4):
                send_armada_run_event(run, event)
            self.assertIn(self.example_user("cordelia").id, send_event.call_args.args[2])
        self.assertEqual(send_event.call_count, 3)

    def test_finished_runs_are_not_kept(self) -> None:
        run = self.make_run()
        armada_run_recipient_ids(run)
        self.assertIn(run.id, armada_run_recipients)

        run.status = AgentRun.DONE
        with self.assert_database_query_count(4):
            recipients = armada_run_recipient_ids(run)
        self.assertIn(self.owner.id, recipients)
        self.assertNotIn(run.id, armada_run_recipients)
        with self.assert_database_query_count(4):
            armada_run_recipient_ids(run)

    def test_kept_recipients_are_bounded(self) -> None:
        runs = [self.make_run() for _ in range(3)]
        with patch("zerver.actions.armada.ARMADA_RUN_RECIPIENTS_LIMIT", 2):
            for run in runs:
                armada_run_recipient_ids(run)
        self.assertEqual(list(armada_run_recipients), [runs[1].id, runs[2].id])

    def test_run_without_its_trigger_message_has_no_recipients(self) -> None:
        run = self.make_run()
        run.trigger_message_id = 0
        with patch("zerver.actions.armada.send_event_on_commit") as send_event:
            send_armada_run_event(run, AgentRunEvent(run=run, event_type="transcript"))
        send_event.assert_not_called()

        side_run = self.make_run()
        side_run.session_kind = AgentRun.SIDE
        self.assertEqual(armada_run_recipient_ids(side_run), [self.owner.id])

    def plans_of(self, queries: list[str], table: str) -> list[str]:
        """The plans of the queries that look a topic up in this table."""
        from psycopg2.sql import SQL

        plans = []
        with connection.cursor() as cursor:
            for sql in queries:
                if f'FROM "{table}"' in sql and "UPPER(" in sql:
                    cursor.execute(SQL("EXPLAIN {}").format(SQL(sql)))
                    plans.append("\n".join(row[0] for row in cursor.fetchall()))
        self.assertNotEqual(plans, [], f"no query read {table}")
        return plans

    def test_topic_lookups_use_an_index(self) -> None:
        """Opening a topic finds its worktree and its runs through an index
        on the case-folded names, never by reading every worktree and run
        the realm ever had."""
        realm = self.owner.realm
        Workspace.objects.bulk_create(
            Workspace(realm=realm, stream_name=ARMADA_DEFAULT_STREAM, topic_name=f"other {i}")
            for i in range(300)
        )
        Workspace.objects.create(
            realm=realm, stream_name=ARMADA_DEFAULT_STREAM, topic_name=ARMADA_DEFAULT_TOPIC
        )
        AgentRun.objects.bulk_create(
            AgentRun(
                realm=realm,
                bot_profile=self.agent,
                requesting_user=self.owner,
                trigger_message_id=1_000_000 + i,
                stream_name=ARMADA_DEFAULT_STREAM,
                topic_name=f"other {i % 300}",
                status=AgentRun.DONE,
            )
            for i in range(3000)
        )
        self.make_run()
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE zerver_workspace, zerver_agentrun")

        self.login_user(self.owner)
        topic = {"stream": ARMADA_DEFAULT_STREAM.upper(), "topic": ARMADA_DEFAULT_TOPIC.upper()}
        with queries_captured() as queries:
            result = self.client_get("/json/armada/runs", topic, intentionally_undocumented=True)
        self.assert_length(self.assert_json_success(result)["armada_runs"], 1)
        sql = [query.sql for query in queries]

        for plan in self.plans_of(sql, "zerver_workspace"):
            self.assertIn("zerver_workspace_scope_uniq", plan)
            self.assertRegex(plan, r"Index Cond: .*upper\(\(stream_name\)::text\)")
        for plan in self.plans_of(sql, "zerver_agentrun"):
            self.assertIn("zerver_agentrun_topic", plan)
            self.assertNotIn("Seq Scan on zerver_agentrun", plan)

    def record_events(self, run: AgentRun, count: int) -> None:
        for index in range(count):
            record_agent_run_event(
                run,
                "transcript",
                summary="Reading",
                payload={"kind": "command", "title": f"Command {index}"},
            )
        record_agent_run_event(
            run,
            "usage",
            summary="Usage",
            payload={"usage": {"input_tokens": 100 + count, "output_tokens": 5}},
        )

    def test_run_list_reads_runs_not_their_events(self) -> None:
        """A topic's run list costs the same however many events its runs
        streamed, and asking for runs by id checks each channel once."""
        runs = [self.make_run(status=AgentRun.DONE) for _ in range(5)]
        for run in runs:
            self.record_events(run, 3)
        self.login_user(self.owner)
        topic = {"stream": ARMADA_DEFAULT_STREAM, "topic": ARMADA_DEFAULT_TOPIC}

        def list_runs(params: dict[str, str]) -> list[dict[str, Any]]:
            result = self.client_get("/json/armada/runs", params, intentionally_undocumented=True)
            return self.assert_json_success(result)["armada_runs"]

        # The session, me and my client; the channel, my subscription, the
        # worktree and the runs.
        with self.assert_database_query_count(7):
            listed = list_runs(topic)
        self.assertEqual(
            [run["snapshot_sequence"] for run in listed],
            [AgentRun.objects.get(id=run["id"]).last_event_id for run in listed],
        )
        for run in runs:
            self.record_events(run, 40)
        with self.assert_database_query_count(7):
            listed = list_runs(topic)
        self.assertEqual({run["usage"]["input_tokens"] for run in listed}, {140})

        by_id = {"run_ids": orjson.dumps([run.id for run in runs]).decode()}
        # The session, me and my client; the runs, and the channel and my
        # subscription once for all five.
        with self.assert_database_query_count(6):
            self.assert_length(list_runs(by_id), 5)

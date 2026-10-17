import json
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

from typing_extensions import override

from zerver.lib.armada import (
    ARMADA_EMBEDDED_BOT_SERVICE,
    ArmadaLiveRunOperation,
    ArmadaRunAsking,
    ArmadaWorkerTurn,
    get_armada_live_run_state,
)
from zerver.lib.armada_agent import (
    ArmadaAppServerNotification,
    ArmadaAppServerTurnContext,
    ArmadaCodexAppServerToolRunner,
    armada_codex_dynamic_tools,
)
from zerver.lib.armada_plugins import install_plugin, update_plugin
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import AgentRun, PluginInstall, UserProfile

SHA = "4e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d5"
WANDB_KEY = "wandb-local-0a1b2c3d4e5f60718293a4b5c6d7f3a"


class PersonAsking:
    """A run's user-input provider, as ArmadaRunAsking is, with the wait
    on its person a test gives it."""

    def __init__(self, wait: Callable[..., bool]) -> None:
        self.wait_on_person = wait

    def __call__(self, questions: list[dict[str, Any]]) -> dict[str, list[str]] | None:
        raise AssertionError("A settings request asks no question.")


class PluginSettingsRequestTest(ZulipTestCase):
    """armada_plugin_request_settings (zerver/lib/armada_plugin_tools.py),
    called as Codex calls it: it asks the person the run acts for to fill in
    a plugin's settings in its sheet, waits as a question does, and answers
    with names, never values."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.agent = self.create_test_bot(
            "agent",
            self.example_user("iago"),
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        manifest = json.loads(self.fixture_data("armada-plugin.json", "armada_plugins/wandb"))
        self.install = install_plugin(
            self.hamlet,
            manifest=manifest,
            source_kind="github",
            source="github.com/plugyawn/armada-wandb",
            sha=SHA,
            plugin_settings={"entity": "runix"},
        )
        message_id = self.send_stream_message(self.hamlet, "Denmark", "make us a W&B widget")
        self.agent_run = AgentRun.objects.create(
            realm=self.hamlet.realm,
            bot_profile=self.agent,
            requesting_user=self.hamlet,
            trigger_message_id=message_id,
            stream_name="Denmark",
            topic_name="test",
            status=AgentRun.RUNNING,
        )
        self.asks: list[tuple[str, ArmadaLiveRunOperation]] = []

    def waiting(self, person_saves: Callable[[], None] | None) -> Callable[..., bool]:
        """A run's person-wait: the person saves the sheet, or doesn't."""

        def wait(ask: str, operation: ArmadaLiveRunOperation, answered: Callable[[], bool]) -> bool:
            self.asks.append((ask, operation))
            if person_saves is not None:
                person_saves()
            return answered()

        return wait

    def call(
        self,
        arguments: object,
        *,
        person_wait: Callable[..., bool] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
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
            user_input_provider=None if person_wait is None else PersonAsking(person_wait),
            is_main=True,
            requesting_user_id=self.hamlet.id,
            trigger_message_id=self.agent_run.trigger_message_id,
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
                        "tool": "armada_plugin_request_settings",
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
        return reply["result"], dict(event)

    def test_a_new_thread_is_offered_the_tool(self) -> None:
        self.assertIn(
            "armada_plugin_request_settings",
            [spec["name"] for spec in armada_codex_dynamic_tools()],
        )

    def test_the_person_sets_a_secret_in_the_sheet_and_only_its_name_comes_back(self) -> None:
        result, event = self.call(
            {"id": "wandb", "fields": ["api_key"], "prefill": {"posts": "crashes"}},
            person_wait=self.waiting(
                lambda: update_plugin(self.hamlet, self.install, secrets={"api_key": WANDB_KEY})
            ),
        )
        self.assertTrue(result["success"])
        self.assertEqual(
            json.loads(result["contentItems"][0]["text"]), {"set": ["api_key"], "missing": []}
        )
        self.assertEqual(
            (event["status"], event["title"]),
            ("done", "Asked King Hamlet for api_key, in the settings."),
        )
        [(ask, operation)] = self.asks
        self.assertEqual(
            ask,
            f"Onix asks @**King Hamlet|{self.hamlet.id}** to set up **Weights & Biases**, in its "
            "settings: API key.\n\n*Open the settings from the button, or from Weights & Biases "
            "in Plugins. The run waits up to 10 minutes.*",
        )
        self.assertEqual(
            operation,
            {
                "kind": "settings",
                "label": "waiting on you",
                "install_id": self.install.id,
                "fields": ["api_key"],
                "prefill": {"posts": "crashes"},
            },
        )
        # The secret is in the plugin's store alone: not in the answer Codex
        # reads, the trace's row, or the message that asked.
        for text in (json.dumps(result), json.dumps(event), ask):
            self.assertNotIn(WANDB_KEY, text)

    def test_what_is_still_missing_is_said(self) -> None:
        result, event = self.call(
            {"id": "wandb", "fields": ["api_key", "entity"]}, person_wait=self.waiting(None)
        )
        self.assertEqual(
            json.loads(result["contentItems"][0]["text"]),
            {"set": ["entity"], "missing": ["api_key"]},
        )
        self.assertEqual(
            event["title"],
            "Asked King Hamlet for api_key, entity, in the settings. Still missing: api_key.",
        )

    def test_what_is_set_already_needs_no_asking(self) -> None:
        result, event = self.call(
            {"id": "wandb", "fields": ["entity"]}, person_wait=self.waiting(None)
        )
        self.assertEqual(
            json.loads(result["contentItems"][0]["text"]), {"set": ["entity"], "missing": []}
        )
        self.assertEqual(event["title"], "Already set for Weights & Biases: entity.")
        self.assertEqual(self.asks, [])

    def test_what_cannot_be_asked(self) -> None:
        wait = self.waiting(None)
        for arguments, refusal in (
            (
                {"id": "wandb", "fields": ["token"]},
                "Weights & Biases has no setting or secret named token.",
            ),
            ({"id": "wandb", "fields": []}, "fields names each setting or secret to fill in once."),
            (
                {"id": "wandb", "fields": ["api_key", "api_key"]},
                "fields names each setting or secret to fill in once.",
            ),
            (
                {"id": "wandb", "fields": ["api_key"], "prefill": {"api_key": "x"}},
                "prefill gives values to settings only; a secret is never given.",
            ),
            ({"id": "gpus", "fields": ["api_key"]}, "No such plugin"),
        ):
            result, event = self.call(arguments, person_wait=wait)
            self.assertFalse(result["success"])
            self.assertEqual(event["title"], refusal)
        # A side conversation asks nobody.
        result, event = self.call({"id": "wandb", "fields": ["api_key"]})
        self.assertEqual(
            event["title"],
            "Nobody can be asked from here; say in your reply what Weights & Biases needs.",
        )
        # A draft is its maker's alone.
        PluginInstall.objects.filter(id=self.install.id).update(
            state=PluginInstall.DRAFT, made_by=self.example_user("iago")
        )
        PluginInstall.objects.filter(id=self.install.id).update(installed_by=None)
        result, event = self.call({"id": "wandb", "fields": ["api_key"]}, person_wait=wait)
        self.assertEqual(event["title"], "No such plugin")
        self.assertEqual(self.asks, [])


class WaitOnPersonTest(ZulipTestCase):
    """ArmadaWorkerTurn.wait_on_person: the run waits on a person as it
    waits on a question's answer, with its live state saying what for."""

    def turn(self) -> tuple[ArmadaWorkerTurn, MagicMock, MagicMock]:
        handler = MagicMock()
        handler.worker_run_cancel_requested.return_value = False
        bot_handler = MagicMock()
        turn = ArmadaWorkerTurn(
            handler,
            {"id": 900},
            bot_handler,
            content="make us a widget",
            active_session=None,
            message_id=900,
            stream="Denmark",
            topic="test",
            post_started_progress=False,
        )
        return turn, handler, bot_handler

    def test_the_wait_shows_what_it_is_for_until_it_is_answered(self) -> None:
        turn, handler, bot_handler = self.turn()
        operation: ArmadaLiveRunOperation = {
            "kind": "settings",
            "label": "waiting on you",
            "install_id": 3,
            "fields": ["api_key"],
            "prefill": {},
        }
        checks: list[object] = []

        def answered() -> bool:
            state = get_armada_live_run_state(900)
            checks.append(state.get("operation") if state is not None else None)
            return len(checks) == 3

        with (
            patch("zerver.lib.armada.ARMADA_QUESTION_POLL_SECONDS", 0),
            patch("zerver.lib.armada.ARMADA_QUESTION_HEARTBEAT_SECONDS", 0),
        ):
            # As Codex's tool reaches it: through the run's provider.
            self.assertTrue(ArmadaRunAsking(turn).wait_on_person("Set it up.", operation, answered))
        bot_handler.send_reply.assert_called_once_with({"id": 900}, "Set it up.")
        self.assertEqual(checks, [operation, operation, operation])
        handler.record_worker_event.assert_called_with(
            bot_handler, 900, "question_waiting", summary="Waiting for a person."
        )
        # Done waiting, the run shows no operation and takes steering again.
        state = get_armada_live_run_state(900)
        assert state is not None
        self.assertIsNone(state.get("operation"))
        self.assertFalse(turn.question_open)

    def test_time_running_out_or_a_stop_ends_the_wait(self) -> None:
        operation: ArmadaLiveRunOperation = {"kind": "settings", "label": "waiting on you"}
        turn, _handler, _bot_handler = self.turn()
        with (
            patch("zerver.lib.armada.ARMADA_QUESTION_TIMEOUT_SECONDS", 0),
            patch("zerver.lib.armada.ARMADA_QUESTION_POLL_SECONDS", 0),
        ):
            self.assertFalse(turn.wait_on_person("Set it up.", operation, lambda: False))

        turn, handler, _bot_handler = self.turn()
        handler.worker_run_cancel_requested.return_value = True
        self.assertFalse(turn.wait_on_person("Set it up.", operation, lambda: True))
        handler.worker_runtime.interrupt.assert_called_once_with()

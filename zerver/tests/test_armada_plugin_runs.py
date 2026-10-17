import json
import shutil
import tempfile
from typing import Any
from unittest.mock import patch

from typing_extensions import override

from zerver.actions.streams import bulk_remove_subscriptions
from zerver.actions.users import do_deactivate_user
from zerver.lib.armada import ARMADA_EMBEDDED_BOT_SERVICE
from zerver.lib.armada_gate import invalidate_armada_selected_bot_cache
from zerver.lib.armada_plugins import add_plugin_link, install_plugin, update_plugin
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import AgentRun, Message, PluginInstall, UserProfile
from zerver.models.streams import get_stream

SHA = "4e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d5"


class PluginRunTest(ZulipTestCase):
    """POST /api/v1/armada/runs (zerver/lib/armada_plugin_runs.py): a
    plugin's bot asks the agent, for the person who installed the plugin,
    in a place the plugin is linked to, with that person's rights."""

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
        invalidate_armada_selected_bot_cache(self.hamlet.realm_id)
        for user in (self.hamlet, self.agent):
            self.subscribe(user, "Verona")
            self.subscribe(user, "Denmark")
        workdir_root = tempfile.mkdtemp(prefix="armada-plugin-runs-")
        self.addCleanup(shutil.rmtree, workdir_root, ignore_errors=True)
        workdir_root_setting = self.settings(ARMADA_TOPIC_WORKDIR_ROOT=workdir_root)
        workdir_root_setting.enable()
        self.addCleanup(workdir_root_setting.disable)
        manifest = json.loads(self.fixture_data("armada-plugin.json", "armada_plugins/wandb"))
        self.install = install_plugin(
            self.hamlet,
            manifest=manifest,
            source_kind="github",
            source="github.com/plugyawn/armada-wandb",
            sha=SHA,
            plugin_settings={"entity": "runix"},
        )
        PluginInstall.objects.filter(id=self.install.id).update(state=PluginInstall.RUNNING)
        self.install.refresh_from_db()
        add_plugin_link(
            self.hamlet,
            self.install,
            kind="project",
            value="runix/nextlat-scaling",
            stream_id=self.get_stream_id("Verona"),
            topic=None,
        )
        add_plugin_link(
            self.hamlet,
            self.install,
            kind="project",
            value="runix/other",
            stream_id=self.get_stream_id("Denmark"),
            topic="lr-sweep",
        )
        self.bot = UserProfile.objects.get(id=self.install.bot_id)

    def ask(self, user: UserProfile, stream: str, topic: str, brief: str) -> Any:
        with patch("zerver.lib.armada_worker.queue_json_publish_rollback_unsafe"):
            return self.api_post(
                user,
                "/api/v1/armada/runs",
                {
                    "stream_id": json.dumps(self.get_stream_id(stream)),
                    "topic": topic,
                    "brief": brief,
                },
            )

    def test_a_plugins_bot_asks_the_agent_for_who_installed_it(self) -> None:
        brief = "Run lr-3e-4-b crashed at step 1200: find out why."
        message_id = self.assert_json_success(self.ask(self.bot, "Verona", "lr-3e-4-b", brief))[
            "message_id"
        ]
        message = Message.objects.get(id=message_id)
        self.assertEqual(message.sender_id, self.agent.id)
        self.assertEqual(
            message.content,
            f"{brief}\n\nAsked by @_**Weights & Biases|{self.bot.id}** for "
            f"@_**{self.hamlet.full_name}|{self.hamlet.id}**.",
        )
        run = AgentRun.objects.get(trigger_message_id=message_id)
        self.assertEqual(
            (run.status, run.requesting_user_id, run.stream_name, run.topic_name),
            # The run row keeps the channel's name as the worker keys it.
            (AgentRun.QUEUED, self.hamlet.id, "verona", "lr-3e-4-b"),
        )
        # A topic's link holds for that topic.
        self.assert_json_success(self.ask(self.bot, "Denmark", "LR-sweep", brief))

    def test_who_may_ask_and_where(self) -> None:
        brief = "Look at the crash."
        not_a_plugin = "Only a plugin's bot starts a run here; people ask the agent with a message."
        self.assert_json_error(self.ask(self.hamlet, "Verona", "t", brief), not_a_plugin)
        helper = self.create_test_bot("helper", self.hamlet)
        self.assert_json_error(self.ask(helper, "Verona", "t", brief), not_a_plugin)
        not_linked = "Weights & Biases is not linked there, so it cannot ask the agent there."
        self.assert_json_error(self.ask(self.bot, "Denmark", "other", brief), not_linked)
        self.assert_json_error(self.ask(self.bot, "Scotland", "t", brief), not_linked)
        for bad_brief in ("  ", "x" * 4001):
            self.assert_json_error(
                self.ask(self.bot, "Verona", "t", bad_brief),
                "A brief is between 1 and 4000 characters.",
            )

        # With the rights of the person who installed it: a channel they
        # left is not theirs to ask in.
        self.make_stream("training", invite_only=True)
        for user in (self.hamlet, self.agent):
            self.subscribe(user, "training")
        add_plugin_link(
            self.hamlet,
            self.install,
            kind="project",
            value="runix/private",
            stream_id=self.get_stream_id("training"),
            topic=None,
        )
        bulk_remove_subscriptions(
            self.hamlet.realm,
            [self.hamlet],
            [get_stream("training", self.hamlet.realm)],
            acting_user=None,
        )
        result = self.ask(self.bot, "training", "t", brief)
        self.assert_json_error_contains(result, "Not authorized to send to channel")
        self.assertFalse(AgentRun.objects.filter(stream_name="training").exists())

        # Turned off, removed, an installer who left, or no agent.
        update_plugin(self.hamlet, self.install, disabled=True)
        self.assert_json_error(
            self.ask(self.bot, "Verona", "t", brief), "Weights & Biases is turned off."
        )
        update_plugin(self.hamlet, self.install, disabled=False)
        PluginInstall.objects.filter(id=self.install.id).update(state=PluginInstall.RUNNING)
        do_deactivate_user(self.agent, acting_user=None)
        invalidate_armada_selected_bot_cache(self.hamlet.realm_id)
        self.assert_json_error(
            self.ask(self.bot, "Verona", "t", brief), "This organization has no agent to ask."
        )
        do_deactivate_user(self.hamlet, acting_user=None)
        self.assert_json_error(
            self.ask(self.bot, "Verona", "t", brief),
            "Nobody who installed Weights & Biases can ask for it any more.",
        )
        PluginInstall.objects.filter(id=self.install.id).update(state=PluginInstall.REMOVED)
        self.assert_json_error(
            self.ask(self.bot, "Verona", "t", brief), "Weights & Biases is turned off."
        )

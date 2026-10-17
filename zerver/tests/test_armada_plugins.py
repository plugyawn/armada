import json
import logging
import re
from datetime import timedelta
from typing import Any
from unittest import mock

import time_machine
from cryptography.fernet import Fernet, InvalidToken
from django.test import RequestFactory, override_settings
from django.utils.timezone import now as timezone_now

from zerver.filters import ZulipExceptionReporterFilter
from zerver.lib.armada_plugin_validation import (
    ArmadaPluginContractError,
    canonicalize_plugin_settings,
)
from zerver.lib.armada_plugins import (
    ArmadaPluginKeyError,
    PluginOrigin,
    armada_plugin_secret_environment,
    armada_plugin_token,
    install_plugin,
    purge_removed_plugins,
    update_plugin,
    update_plugin_version,
)
from zerver.lib.exceptions import JsonableError
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.timestamp import datetime_to_timestamp
from zerver.models import (
    Message,
    PluginInstall,
    PluginLink,
    PluginSecret,
    PluginWidgetState,
    RealmAuditLog,
    Recipient,
    Repository,
    Stream,
    Subscription,
    UserProfile,
)
from zerver.models.realm_audit_logs import AuditLogEventType
from zproject.sentry import add_context

SHA = "4e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d5"
# Long enough to show its last four characters.
WANDB_KEY = "wandb-local-0a1b2c3d4e5f60718293a4b5c6d7f3a"
KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


class PluginInstallTest(ZulipTestCase):
    """Installs (zerver/lib/armada_plugins.py): the bot and rows an install
    makes, its settings saved whole, its secrets kept encrypted and never
    shown, its links, and Remove, Undo and the purge taking everything
    back. Every route looks only in the person's realm."""

    def manifest(self, plugin: str = "wandb") -> dict[str, Any]:
        return json.loads(self.fixture_data("armada-plugin.json", f"armada_plugins/{plugin}"))

    def post_install(
        self,
        user: UserProfile,
        *,
        plugin: str = "wandb",
        settings: dict[str, Any] | None = None,
        secrets: dict[str, str] | None = None,
        subdomain: str = "zulip",
        **params: Any,
    ) -> Any:
        self.login_user(user)
        return self.client_post(
            "/json/armada/plugins",
            subdomain=subdomain,
            info={
                "source_kind": "github",
                "source": f"github.com/plugyawn/armada-{plugin}",
                "sha": SHA,
                "manifest": json.dumps(self.manifest(plugin)),
                "settings": json.dumps({"entity": "runix"} if settings is None else settings),
                "secrets": json.dumps({"api_key": WANDB_KEY} if secrets is None else secrets),
                **params,
            },
        )

    def install(self, user: UserProfile | None = None, **kwargs: Any) -> PluginInstall:
        result = self.post_install(user or self.example_user("hamlet"), **kwargs)
        return PluginInstall.objects.get(id=self.assert_json_success(result)["plugin"]["id"])

    def patch(self, user: UserProfile, install: PluginInstall, **params: object) -> Any:
        self.login_user(user)
        return self.client_patch(
            f"/json/armada/plugins/{install.id}",
            {name: json.dumps(value) for name, value in params.items()},
        )

    def link(
        self,
        user: UserProfile,
        install: PluginInstall,
        stream: str,
        topic: str | None = None,
        **params: Any,
    ) -> Any:
        self.login_user(user)
        info: dict[str, Any] = {
            "kind": "project",
            "value": "runix/nextlat-scaling",
            "stream_id": self.get_stream_id(stream, user.realm),
            **params,
        }
        if topic is not None:
            info["topic"] = topic
        return self.client_post(f"/json/armada/plugins/{install.id}/links", info)

    def subscriptions(self, user: UserProfile) -> set[str]:
        stream_ids = Subscription.objects.filter(
            user_profile=user, active=True, recipient__type=Recipient.STREAM
        ).values_list("recipient__type_id", flat=True)
        return set(Stream.objects.filter(id__in=stream_ids).values_list("name", flat=True))

    def audits(self, install: PluginInstall) -> list[tuple[int, int | None, dict[str, Any]]]:
        return [
            (row.event_type, row.acting_user_id, row.extra_data)
            for row in RealmAuditLog.objects.filter(
                modified_user_id=install.bot_id, event_type__gte=AuditLogEventType.PLUGIN_INSTALLED
            ).order_by("id")
        ]

    def rows(self, install_id: int) -> dict[str, int]:
        return {
            "installs": PluginInstall.objects.filter(id=install_id).count(),
            "secrets": PluginSecret.objects.filter(install_id=install_id).count(),
            "links": PluginLink.objects.filter(install_id=install_id).count(),
            "widgets": PluginWidgetState.objects.filter(install_id=install_id).count(),
        }

    def discard(self, install: PluginInstall) -> None:
        """Gone, as after a purge: its bot is deactivated and free."""
        PluginInstall.objects.filter(id=install.id).delete()
        UserProfile.objects.filter(id=install.bot_id).update(is_active=False)

    def feed(self, install: PluginInstall, link: PluginLink | None = None) -> None:
        PluginWidgetState.objects.create(
            install=install,
            widget="runs",
            link=link,
            document=json.loads(self.fixture_data("runs.json", "armada_plugins/wandb")),
        )

    def test_install_creates_the_bot_and_rows(self) -> None:
        hamlet = self.example_user("hamlet")
        result = self.post_install(hamlet, settings={"entity": "runix"})
        plugin = self.assert_json_success(result)["plugin"]
        install = PluginInstall.objects.get(id=plugin["id"])
        self.assertEqual(
            (install.plugin_id, install.name, install.version, install.source, install.sha),
            ("wandb", "Weights & Biases", "0.4.1", "github.com/plugyawn/armada-wandb", SHA),
        )
        self.assertEqual(install.manifest, self.manifest())
        # The manifest's default fills what was not given.
        self.assertEqual(install.settings, {"entity": "runix", "posts": "both"})
        self.assertEqual(install.state, PluginInstall.STARTING)
        self.assertEqual(install.installed_by, hamlet)
        self.assertEqual(plugin["state"], "starting")

        bot = install.bot
        self.assertEqual(bot.delivery_email, "wandb-plugin-bot@zulip.testserver")
        self.assertEqual(bot.full_name, "Weights & Biases")
        self.assertTrue(bot.is_bot and bot.is_active)
        self.assertEqual(bot.bot_type, UserProfile.DEFAULT_BOT)
        self.assertEqual(bot.role, UserProfile.ROLE_MEMBER)
        # No one owns it: its key is in no one's bot settings.
        self.assertIsNone(bot.bot_owner)
        # It sees only where it is linked.
        self.assertEqual(self.subscriptions(bot), set())

        [secret] = PluginSecret.objects.filter(install=install)
        self.assertEqual((secret.name, secret.hint, secret.set_by), ("api_key", "7f3a", hamlet))
        self.assertNotIn(WANDB_KEY, secret.ciphertext)
        self.assertEqual(
            plugin["secrets"],
            [
                {
                    "name": "api_key",
                    "title": "API key",
                    "required": True,
                    "set": True,
                    "hint": WANDB_KEY[-4:],
                    "set_by": hamlet.id,
                    "date_set": datetime_to_timestamp(secret.date_set),
                }
            ],
        )
        self.assertEqual(
            self.audits(install),
            [
                (
                    AuditLogEventType.PLUGIN_INSTALLED,
                    hamlet.id,
                    {
                        "install_id": install.id,
                        "plugin": "wandb",
                        "version": "0.4.1",
                        "source_kind": "github",
                        "source": "github.com/plugyawn/armada-wandb",
                        "source_path": "",
                        "sha": SHA,
                        "settings": {"entity": "runix", "posts": "both"},
                        "secrets": ["api_key"],
                    },
                )
            ],
        )

    def test_an_install_waits_for_what_it_needs(self) -> None:
        hamlet = self.example_user("hamlet")
        install = self.install(hamlet, settings={}, secrets={})
        self.assertEqual(
            (install.state, install.state_detail, install.generation),
            (PluginInstall.WAITING, "entity, api_key", 1),
        )
        self.assert_json_success(self.patch(hamlet, install, settings={"entity": "runix"}))
        install.refresh_from_db()
        self.assertEqual((install.state, install.state_detail), (PluginInstall.WAITING, "api_key"))
        self.assert_json_success(self.patch(hamlet, install, secrets={"api_key": "short"}))
        install.refresh_from_db()
        self.assertEqual(
            (install.state, install.state_detail, install.generation),
            (PluginInstall.STARTING, "", 3),
        )
        # A short secret does not show its end.
        self.assertEqual(PluginSecret.objects.get(install=install).hint, "")
        # Clearing a required setting makes it wait again.
        self.assert_json_success(self.patch(hamlet, install, settings={"entity": ""}))
        install.refresh_from_db()
        self.assertEqual((install.state, install.state_detail), (PluginInstall.WAITING, "entity"))

    def test_settings_are_saved_whole(self) -> None:
        hamlet = self.example_user("hamlet")
        install = self.install(hamlet, settings={"entity": "runix", "posts": "crashes"})
        self.assert_json_success(self.patch(hamlet, install, settings={"entity": "nextlat"}))
        install.refresh_from_db()
        # What was not given goes back to its default.
        self.assertEqual(install.settings, {"entity": "nextlat", "posts": "both"})
        self.assertEqual(install.generation, 2)
        self.assertEqual(
            self.audits(install)[-1],
            (
                AuditLogEventType.PLUGIN_SETTINGS_CHANGED,
                hamlet.id,
                {
                    "install_id": install.id,
                    "plugin": "wandb",
                    "settings": {
                        "old": {"entity": "runix", "posts": "crashes"},
                        "new": {"entity": "nextlat", "posts": "both"},
                    },
                },
            ),
        )
        for settings, error in (
            ({"entity": 3}, "$.entity: expected type string"),
            ({"entity": "runix", "nope": 1}, "$.nope: unsupported property"),
            (
                {"entity": "<b>runix</b>"},
                "$.entity: a plugin sends text, not HTML, and '<b>runix</b>' holds markup",
            ),
        ):
            self.assert_json_error(
                self.patch(hamlet, install, settings=settings),
                f"The settings are not valid: {error}",
            )
        with self.assertRaisesRegex(ArmadaPluginContractError, r"^\$: the settings are an object$"):
            canonicalize_plugin_settings(install.manifest, [])
        # The same settings again change nothing, and record nothing.
        count = len(self.audits(install))
        self.assert_json_success(self.patch(hamlet, install, settings={"entity": "nextlat"}))
        install.refresh_from_db()
        self.assertEqual((install.generation, len(self.audits(install))), (2, count))

    def test_where_it_shows_and_its_tools(self) -> None:
        hamlet = self.example_user("hamlet")
        install = self.install(hamlet)
        self.assert_json_success(
            self.patch(
                hamlet,
                install,
                widget_defaults={"runs": {"nav": "offered", "column": False}},
                disabled_tools=["compare_runs", "compare_runs"],
            )
        )
        install.refresh_from_db()
        before = {"runs": {"nav": "shown", "column": True}, "run": {"nav": "off", "column": False}}
        after = {
            "runs": {"nav": "offered", "column": False},
            "run": {"nav": "off", "column": False},
        }
        self.assertEqual(
            (install.widget_defaults, install.disabled_tools, install.generation),
            (after, ["compare_runs"], 1),
        )
        self.assertEqual(
            self.audits(install)[-1][2],
            {
                "install_id": install.id,
                "plugin": "wandb",
                "widget_defaults": {"old": before, "new": after},
                "disabled_tools": {"old": [], "new": ["compare_runs"]},
            },
        )
        self.assert_json_error(
            self.patch(hamlet, install, disabled_tools=["rm -rf"]),
            "disabled_tools lists at most 64 tool names.",
        )
        # Turning it off and on restarts it, and lifts a failure.
        self.assert_json_success(self.patch(hamlet, install, disabled=True))
        install.refresh_from_db()
        self.assertEqual((install.disabled, install.generation), (True, 2))
        install.state = PluginInstall.FAILED
        install.failures = 5
        install.save()
        self.assert_json_success(self.patch(hamlet, install, disabled=False))
        install.refresh_from_db()
        self.assertEqual(
            (install.disabled, install.state, install.failures, install.generation),
            (False, PluginInstall.STARTING, 0, 3),
        )

    def test_no_response_record_or_log_carries_a_secret(self) -> None:
        hamlet = self.example_user("hamlet")
        replaced = "wandb-local-ffffffffffffffffffffffff9e21"
        with (
            self.assertLogs(level="DEBUG") as logs,
            self.captureOnCommitCallbacks(execute=True),
            mock.patch("zerver.tornado.event_queue.process_notification") as events,
        ):
            logging.getLogger(__name__).info("installing")
            responses = [self.post_install(hamlet)]
            install = PluginInstall.objects.get(plugin_id="wandb")
            responses.append(self.patch(hamlet, install, secrets={"api_key": replaced}))
            self.login_user(hamlet)
            responses.append(self.client_get("/json/armada/plugins"))
            responses.append(self.link(hamlet, install, "Verona"))
            responses.append(self.patch(hamlet, install, secrets={"nope": "x"}))
        for secret in (WANDB_KEY, replaced):
            for response in responses:
                self.assertNotIn(secret, response.content.decode())
            self.assertNotIn(secret, "\n".join(logs.output))
            self.assertNotIn(secret, json.dumps(events.call_args_list, default=str))
            for row in RealmAuditLog.objects.filter(realm=hamlet.realm):
                self.assertNotIn(secret, json.dumps(row.extra_data, default=str))
            for ciphertext, hint in PluginSecret.objects.values_list("ciphertext", "hint"):
                self.assertNotIn(secret, ciphertext + hint)
        # The secret was kept: the plugin's process gets the new one.
        self.assertEqual(armada_plugin_secret_environment(install), {"WANDB_API_KEY": replaced})

    def test_error_reports_never_carry_a_secret(self) -> None:
        request = RequestFactory().post(
            "/json/armada/plugins", {"secrets": json.dumps({"api_key": WANDB_KEY})}
        )
        cleaned = ZulipExceptionReporterFilter().get_post_parameters(request)
        self.assertEqual(cleaned["secrets"], "**********")
        event: Any = {"request": {"data": {"secrets": WANDB_KEY, "sha": SHA}}}
        scrubbed = add_context(event, {})
        assert scrubbed is not None
        self.assertEqual(scrubbed["request"]["data"], {"secrets": "[Filtered]", "sha": SHA})

    def test_secrets_are_encrypted_at_rest_and_keys_rotate(self) -> None:
        hamlet = self.example_user("hamlet")
        with override_settings(ARMADA_PLUGIN_SECRETS_KEY=KEY_A):
            wandb = self.install(hamlet)
            gpus = self.install(
                hamlet,
                plugin="gpus",
                settings={"manage_prefix": "nlscale-"},
                secrets={"prime_api_key": "prime-0123456789abcdef"},
            )
            secret = PluginSecret.objects.get(install=wandb)
            self.assertEqual(Fernet(KEY_A).decrypt(secret.ciphertext.encode()).decode(), WANDB_KEY)
            self.assertEqual(armada_plugin_secret_environment(wandb), {"WANDB_API_KEY": WANDB_KEY})
            # Each plugin's environment holds its own secrets, no other's.
            self.assertEqual(
                armada_plugin_secret_environment(gpus), {"PRIME_API_KEY": "prime-0123456789abcdef"}
            )
            tokens = {wandb.id: armada_plugin_token(wandb), gpus.id: armada_plugin_token(gpus)}
            self.assertEqual(armada_plugin_token(wandb), tokens[wandb.id])
            self.assertNotEqual(tokens[wandb.id], tokens[gpus.id])
            self.assert_length(tokens[wandb.id], 64)

        # Rotation: a new key first. Every install's token changes at once;
        # every secret still reads, and a new one is kept under the new key.
        with override_settings(ARMADA_PLUGIN_SECRETS_KEY=f"{KEY_B} {KEY_A}"):
            for install in (wandb, gpus):
                self.assertNotEqual(armada_plugin_token(install), tokens[install.id])
            self.assertEqual(armada_plugin_secret_environment(wandb), {"WANDB_API_KEY": WANDB_KEY})
            self.assert_json_success(
                self.patch(hamlet, wandb, secrets={"api_key": "rotated-key-000000"})
            )
            secret.refresh_from_db()
            self.assertEqual(
                Fernet(KEY_B).decrypt(secret.ciphertext.encode()), b"rotated-key-000000"
            )
            rotated = armada_plugin_token(wandb)

        # With the old key dropped, a secret kept under it cannot be read.
        with override_settings(ARMADA_PLUGIN_SECRETS_KEY=KEY_B):
            self.assertEqual(armada_plugin_token(wandb), rotated)
            self.assertEqual(
                armada_plugin_secret_environment(wandb), {"WANDB_API_KEY": "rotated-key-000000"}
            )
            with self.assertRaises(InvalidToken):
                armada_plugin_secret_environment(gpus)

        # A development server's secrets file may predate the key.
        with override_settings(ARMADA_PLUGIN_SECRETS_KEY=None):
            self.assertEqual(armada_plugin_token(wandb), armada_plugin_token(wandb))

    def test_a_production_server_without_a_key_keeps_no_secrets(self) -> None:
        hamlet = self.example_user("hamlet")
        gpus = self.install(hamlet, plugin="gpus", settings={"manage_prefix": "x"}, secrets={})
        error = (
            "This server has no armada_plugin_secrets_key in zulip-secrets.conf,"
            " so it cannot keep a plugin's secrets or give it a token."
        )
        # Called directly: the test client's login needs DEVELOPMENT.
        with override_settings(ARMADA_PLUGIN_SECRETS_KEY=None, DEVELOPMENT=False):
            with self.assertRaisesRegex(ArmadaPluginKeyError, f"^{re.escape(error)}$"):
                install_plugin(
                    hamlet,
                    source_kind="github",
                    source="github.com/plugyawn/armada-wandb",
                    sha=SHA,
                    manifest=self.manifest(),
                    plugin_settings={"entity": "runix"},
                    secrets={"api_key": WANDB_KEY},
                )
            with self.assertRaisesRegex(ArmadaPluginKeyError, f"^{re.escape(error)}$"):
                update_plugin(hamlet, gpus, secrets={"prime_api_key": "prime-key"})
            with self.assertRaisesRegex(ArmadaPluginKeyError, f"^{re.escape(error)}$"):
                armada_plugin_token(gpus)
        self.assertEqual(list(PluginInstall.objects.values_list("plugin_id", flat=True)), ["gpus"])
        self.assertFalse(PluginSecret.objects.exists())

    def recent(self, user: UserProfile, install: PluginInstall, **params: object) -> Any:
        self.login_user(user)
        return self.client_get(
            f"/json/armada/plugins/{install.id}/recent",
            {name: json.dumps(value) for name, value in params.items()},
        )

    def test_recent_is_who_did_what_newest_first_without_a_secret(self) -> None:
        hamlet = self.example_user("hamlet")
        othello = self.example_user("othello")
        new_key = "wandb-local-ffeeddccbbaa99887766554433221100"
        start = timezone_now()
        with time_machine.travel(start, tick=False):
            install = self.install(hamlet)
        with time_machine.travel(start + timedelta(minutes=1), tick=False):
            self.assert_json_success(self.link(hamlet, install, "Verona"))
        with time_machine.travel(start + timedelta(minutes=2), tick=False):
            self.assert_json_success(self.patch(othello, install, secrets={"api_key": new_key}))
        # A link to a channel Othello cannot read, and a removal and its undoing.
        self.make_stream("training", invite_only=True)
        self.subscribe(hamlet, "training")
        with time_machine.travel(start + timedelta(minutes=3), tick=False):
            self.assert_json_success(self.link(hamlet, install, "training", "lr-sweep"))
        with time_machine.travel(start + timedelta(minutes=4), tick=False):
            self.login_user(hamlet)
            self.assert_json_success(self.client_delete(f"/json/armada/plugins/{install.id}"))
            self.assert_json_success(self.client_post(f"/json/armada/plugins/{install.id}/restore"))
        # Another install's lines are its own.
        self.install(hamlet, plugin="gpus", settings={"manage_prefix": "x"}, secrets={})

        result = self.recent(hamlet, install)
        lines = self.assert_json_success(result)["recent"]
        self.assertEqual(
            [
                (line["type"], line["acting_user_id"], line["time"] - datetime_to_timestamp(start))
                for line in lines
            ],
            [
                ("restored", hamlet.id, 240),
                ("removed", hamlet.id, 240),
                ("linked", hamlet.id, 180),
                ("settings_changed", othello.id, 120),
                ("linked", hamlet.id, 60),
                ("installed", hamlet.id, 0),
            ],
        )
        self.assertEqual(lines[-1]["detail"]["secrets"], ["api_key"])
        self.assertEqual(lines[3]["detail"], {"secrets": ["api_key"]})
        self.assertEqual(
            [(line["stream_id"], line["detail"].get("topic")) for line in lines[2::2]],
            [(self.get_stream_id("training"), "lr-sweep"), (self.get_stream_id("Verona"), None)],
        )
        for key in (WANDB_KEY, new_key):
            self.assertNotIn(key.encode(), result.content)

        # Othello cannot read the private channel, so its line is left out.
        self.assertEqual(
            [
                line["type"]
                for line in self.assert_json_success(self.recent(othello, install))["recent"]
            ],
            ["restored", "removed", "settings_changed", "linked", "installed"],
        )
        self.assertEqual(
            [
                line["id"]
                for line in self.assert_json_success(self.recent(hamlet, install, limit=2))[
                    "recent"
                ]
            ],
            [lines[0]["id"], lines[1]["id"]],
        )
        for limit in (0, 101):
            self.assert_json_error(
                self.recent(hamlet, install, limit=limit), "limit is between 1 and 100."
            )

    def test_links_take_the_bot_where_it_is_linked(self) -> None:
        hamlet = self.example_user("hamlet")
        install = self.install(hamlet)
        bot = install.bot
        link_id = self.assert_json_success(self.link(hamlet, install, "Verona"))["id"]
        topic_link_id = self.assert_json_success(self.link(hamlet, install, "Verona", "lr-sweep"))[
            "id"
        ]
        self.assertEqual(
            list(
                PluginLink.objects.filter(install=install)
                .values_list("kind", "value", "stream__name", "topic")
                .order_by("id")
            ),
            [
                ("project", "runix/nextlat-scaling", "Verona", None),
                ("project", "runix/nextlat-scaling", "Verona", "lr-sweep"),
            ],
        )
        self.assertEqual(self.subscriptions(bot), {"Verona"})
        verona = self.get_stream_id("Verona")
        self.assertEqual(
            [
                (row.event_type, row.modified_stream_id, row.extra_data)
                for row in RealmAuditLog.objects.filter(
                    modified_user=bot, event_type=AuditLogEventType.PLUGIN_LINKED
                ).order_by("id")
            ],
            [
                (
                    AuditLogEventType.PLUGIN_LINKED,
                    verona,
                    {
                        "install_id": install.id,
                        "plugin": "wandb",
                        "link_id": link_id,
                        "kind": "project",
                        "value": "runix/nextlat-scaling",
                        "topic": topic,
                    },
                )
                for link_id, topic in ((link_id, None), (topic_link_id, "lr-sweep"))
            ],
        )

        self.assert_json_error(
            self.link(hamlet, install, "Verona", "LR-sweep"), "That is linked there already."
        )
        self.assert_json_error(
            self.link(hamlet, install, "Verona"), "That is linked there already."
        )
        self.assert_json_error(
            self.link(hamlet, install, "Verona", kind="pool"),
            "Weights & Biases offers no link named pool.",
        )
        self.assert_json_error(
            self.link(hamlet, install, "Verona", value="no-slash"),
            "no-slash is not a Project that Weights & Biases understands.",
        )
        self.assert_json_error(
            self.link(hamlet, install, "Verona", value="<b>a</b>/b"),
            "<b>a</b>/b is not a Project that Weights & Biases understands.",
        )
        self.assert_json_error(self.link(hamlet, install, "Verona", " "), "Invalid topic name")
        self.make_stream("secret-training", invite_only=True)
        self.assert_json_error(self.link(hamlet, install, "secret-training"), "Invalid channel ID")

        gpus = self.install(hamlet, plugin="gpus", settings={"manage_prefix": "nl"}, secrets={})
        self.assert_json_error(
            self.link(hamlet, gpus, "Denmark", kind="pool", value="nlscale-a"),
            "Machines is linked to a topic, not to a whole channel.",
        )
        manifest = install.manifest
        manifest["provides"]["links"]["project"]["to"] = ["channel"]
        install.manifest = manifest
        install.save()
        self.assert_json_error(
            self.link(hamlet, install, "Denmark", "lr-sweep"),
            "Project is linked to a whole channel, not to a topic.",
        )

        # The bot leaves the channel with the last link there.
        self.login_user(hamlet)
        self.assert_json_success(
            self.client_delete(f"/json/armada/plugins/{install.id}/links/{link_id}")
        )
        self.assertEqual(self.subscriptions(bot), {"Verona"})
        self.assert_json_success(
            self.client_delete(f"/json/armada/plugins/{install.id}/links/{topic_link_id}")
        )
        self.assertEqual(self.subscriptions(bot), set())
        self.assertEqual(
            self.audits(install)[-1][0:2], (AuditLogEventType.PLUGIN_UNLINKED, hamlet.id)
        )
        result = self.client_delete(f"/json/armada/plugins/{install.id}/links/{link_id}")
        self.assert_json_error(result, "No such link", status_code=404)

    def test_remove_takes_everything_back_and_undo_returns_it(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        install = self.install(hamlet)
        bot = install.bot
        self.assert_json_success(self.link(hamlet, install, "Verona"))
        link = PluginLink.objects.get(install=install)
        self.feed(install)
        self.feed(install, link)
        message_id = self.send_stream_message(bot, "Verona", "lr-1e-4-a finished.", "lr-sweep")

        self.login_user(cordelia)
        self.assert_json_success(self.client_delete(f"/json/armada/plugins/{install.id}"))
        install.refresh_from_db()
        bot.refresh_from_db()
        self.assertEqual(install.state, PluginInstall.REMOVED)
        self.assertFalse(bot.is_active)
        self.assertEqual(
            self.rows(install.id), {"installs": 1, "secrets": 1, "links": 1, "widgets": 0}
        )
        # Its messages stay, signed by its bot.
        self.assertEqual(Message.objects.get(id=message_id).sender, bot)
        [listed] = self.assert_json_success(self.client_get("/json/armada/plugins"))["plugins"]
        self.assertEqual(listed["state"], "removed")
        self.assertEqual(
            self.audits(install)[-1][0:2], (AuditLogEventType.PLUGIN_REMOVED, cordelia.id)
        )
        for result in (
            self.client_delete(f"/json/armada/plugins/{install.id}"),
            self.patch(cordelia, install, disabled=True),
            self.link(cordelia, install, "Denmark"),
            self.client_delete(f"/json/armada/plugins/{install.id}/links/{link.id}"),
        ):
            self.assert_json_error(result, "Weights & Biases is removed; undo the removal first.")

        with time_machine.travel(timezone_now() + timedelta(days=6), tick=False):
            self.login_user(cordelia)
            self.assert_json_success(self.client_post(f"/json/armada/plugins/{install.id}/restore"))
        install.refresh_from_db()
        bot.refresh_from_db()
        self.assertEqual((install.state, install.date_removed), (PluginInstall.STARTING, None))
        self.assertTrue(bot.is_active)
        self.assertEqual(install.settings, {"entity": "runix", "posts": "both"})
        self.assertEqual(armada_plugin_secret_environment(install), {"WANDB_API_KEY": WANDB_KEY})
        self.assertEqual(self.subscriptions(bot), {"Verona"})
        self.assertEqual(
            self.audits(install)[-1][0:2], (AuditLogEventType.PLUGIN_RESTORED, cordelia.id)
        )
        self.assert_json_error(
            self.client_post(f"/json/armada/plugins/{install.id}/restore"),
            "Weights & Biases is not removed.",
        )

        # A week after a removal, it can no longer be undone, and the purge
        # leaves nothing behind but the deactivated bot and its messages.
        self.assert_json_success(self.client_delete(f"/json/armada/plugins/{install.id}"))
        later = timezone_now() + timedelta(days=7, minutes=1)
        with time_machine.travel(later, tick=False):
            self.assert_json_error(
                self.client_post(f"/json/armada/plugins/{install.id}/restore"),
                "Weights & Biases was removed more than a week ago; install it again.",
            )
        self.assertEqual(purge_removed_plugins(timezone_now()), 0)
        self.assertEqual(purge_removed_plugins(later), 1)
        self.assertEqual(
            self.rows(install.id), {"installs": 0, "secrets": 0, "links": 0, "widgets": 0}
        )
        bot.refresh_from_db()
        self.assertFalse(bot.is_active)
        self.assertEqual(Message.objects.get(id=message_id).sender, bot)

    def test_installing_again_reuses_the_bot(self) -> None:
        hamlet = self.example_user("hamlet")
        first = self.install(hamlet)
        bot = first.bot
        key = bot.api_key
        self.assertEqual(
            self.post_install(hamlet).json()["msg"], "Weights & Biases is already installed."
        )
        self.login_user(hamlet)
        self.assert_json_success(self.client_delete(f"/json/armada/plugins/{first.id}"))
        # Installing over a removal replaces it, with the same bot and a new key.
        second = self.install(hamlet, settings={"entity": "nextlat"})
        bot.refresh_from_db()
        self.assertEqual(second.bot, bot)
        self.assertTrue(bot.is_active)
        self.assertNotEqual(bot.api_key, key)
        self.assertEqual(self.rows(first.id)["installs"], 0)
        # After a purge, its bot is found by its address.
        self.assert_json_success(self.client_delete(f"/json/armada/plugins/{second.id}"))
        purge_removed_plugins(timezone_now() + timedelta(days=8))
        UserProfile.objects.filter(id=bot.id).update(full_name="Old name")
        third = self.install(hamlet)
        bot.refresh_from_db()
        self.assertEqual((third.bot, bot.full_name), (bot, "Weights & Biases"))

        # An address someone else holds is not taken over.
        self.assert_json_success(self.client_delete(f"/json/armada/plugins/{third.id}"))
        purge_removed_plugins(timezone_now() + timedelta(days=8))
        UserProfile.objects.filter(id=bot.id).update(is_active=True)
        self.assert_json_error(
            self.post_install(hamlet),
            "wandb-plugin-bot@zulip.testserver is taken, so the plugin cannot have its own bot.",
        )

    def test_every_route_looks_only_in_the_realm(self) -> None:
        lear = self.install(self.lear_user("cordelia"), subdomain="lear")
        lear_link = PluginLink.objects.create(
            install=lear,
            kind="project",
            value="a/b",
            stream=Stream.objects.filter(realm=lear.realm).order_by("id")[0],
        )
        hamlet = self.example_user("hamlet")
        self.login_user(hamlet)
        self.assertEqual(
            self.assert_json_success(self.client_get("/json/armada/plugins"))["plugins"], []
        )
        for result in (
            self.patch(hamlet, lear, disabled=True),
            self.client_delete(f"/json/armada/plugins/{lear.id}"),
            self.client_post(f"/json/armada/plugins/{lear.id}/restore"),
            self.client_post(f"/json/armada/plugins/{lear.id}/retry"),
            self.client_post(
                f"/json/armada/plugins/{lear.id}/links",
                {"kind": "project", "value": "a/b", "stream_id": self.get_stream_id("Verona")},
            ),
            self.client_delete(f"/json/armada/plugins/{lear.id}/links/{lear_link.id}"),
            self.client_get(f"/json/armada/plugins/{lear.id}/recent"),
        ):
            self.assert_json_error(result, "No such plugin", status_code=404)
        # A link of another install, or a channel of another realm, is not found either.
        mine = self.install(hamlet)
        self.assert_json_error(
            self.client_delete(f"/json/armada/plugins/{mine.id}/links/{lear_link.id}"),
            "No such link",
            status_code=404,
        )
        self.assert_json_error(
            self.client_post(
                f"/json/armada/plugins/{mine.id}/links",
                {"kind": "project", "value": "a/b", "stream_id": lear_link.stream_id},
            ),
            "Invalid channel ID",
        )
        lear.refresh_from_db()
        self.assertEqual((lear.state, lear.disabled), (PluginInstall.STARTING, False))

    def test_members_manage_plugins_and_bots_and_guests_do_not(self) -> None:
        hamlet = self.example_user("hamlet")
        install = self.install(hamlet)
        polonius = self.example_user("polonius")
        guest_error = "Guests cannot see, install, link or remove plugins."
        self.login_user(polonius)
        self.assert_json_error(self.client_get("/json/armada/plugins"), guest_error)
        self.assert_json_error(self.post_install(polonius), guest_error)
        self.assert_json_error(self.patch(polonius, install, disabled=True), guest_error)
        self.assert_json_error(
            self.client_post(f"/json/armada/plugins/{install.id}/retry"), guest_error
        )
        self.assert_json_error(
            self.client_get(f"/json/armada/plugins/{install.id}/recent"), guest_error
        )

        bot_error = "Plugins are installed, linked and removed by people, not bots."
        # Neither a bot of a person's nor the plugin's own bot may.
        for bot in (self.create_test_bot("helper", hamlet), install.bot):
            self.assert_json_error(self.api_get(bot, "/api/v1/armada/plugins"), bot_error)
            self.assert_json_error(
                self.api_delete(bot, f"/api/v1/armada/plugins/{install.id}"), bot_error
            )
            self.assert_json_error(
                self.api_get(bot, f"/api/v1/armada/plugins/{install.id}/recent"), bot_error
            )
            self.assert_json_error(
                self.api_post(
                    bot,
                    f"/api/v1/armada/plugins/{install.id}/links",
                    {"kind": "project", "value": "a/b", "stream_id": self.get_stream_id("Verona")},
                ),
                bot_error,
            )
        install.refresh_from_db()
        self.assertEqual((install.state, install.disabled), (PluginInstall.STARTING, False))
        self.assertEqual(self.subscriptions(install.bot), set())

    def test_an_install_pins_a_commit_of_a_repository(self) -> None:
        hamlet = self.example_user("hamlet")
        manifest = self.manifest()
        del manifest["name"]
        self.assert_json_error(
            self.post_install(hamlet, manifest=json.dumps(manifest)),
            "The plugin's manifest is not valid: $: missing required property 'name'",
        )
        self.assert_json_error(
            self.post_install(hamlet, source="gitlab.com/plugyawn/armada-wandb"),
            "A plugin is installed from github.com/<owner>/<repository>.",
        )
        with override_settings(ARMADA_PLUGIN_LOCAL_SOURCES=False):
            self.assert_json_error(
                self.post_install(hamlet, source_kind="local", source="/srv/plugins/wandb"),
                "A local plugin is installed from one of this server's repositories.",
            )
        self.assert_json_error(
            self.post_install(hamlet, sha="main"),
            "A plugin is installed at a commit: 40 hexadecimal digits.",
        )
        self.assert_json_error(
            self.post_install(hamlet, secrets={"token": "x"}),
            "The plugin asks for no secret named token.",
        )
        self.assert_json_error(
            self.post_install(hamlet, secrets={"api_key": ""}),
            "A secret is between 1 and 4096 characters.",
        )
        self.assertFalse(PluginInstall.objects.exists())
        install = self.install(hamlet, source="https://github.com/plugyawn/armada-wandb.git/")
        self.assertEqual(install.source, "github.com/plugyawn/armada-wandb")
        gpus = self.install(
            hamlet,
            plugin="gpus",
            source_kind="local",
            source="/srv/plugins/../plugins/gpus",
            settings={"manage_prefix": "nl"},
            secrets={},
        )
        self.assertEqual((gpus.source_kind, gpus.source), ("local", "/srv/plugins/gpus"))

    def test_where_a_plugins_code_comes_from(self) -> None:
        hamlet = self.example_user("hamlet")
        remote = self.manifest()
        del remote["run"], remote["secrets"]
        remote["remote"] = {"url": "https://plugins.example.com/wandb"}
        install = self.install(
            hamlet, manifest=json.dumps(remote), source_kind="remote", source="", sha="", secrets={}
        )
        self.assertEqual(
            (install.source_kind, install.source, install.sha, install.source_path),
            ("remote", "https://plugins.example.com/wandb", "", ""),
        )
        self.discard(install)
        refused: list[tuple[dict[str, Any], str]] = [
            (
                {"source_kind": "remote", "sha": SHA},
                "A remote plugin has no repository, path or commit here.",
            ),
            (
                {"source_kind": "remote", "sha": "", "path": "x"},
                "A remote plugin has no repository, path or commit here.",
            ),
            ({"source_kind": "svn"}, "A plugin comes from github, local or remote."),
            (
                {"path": "../secrets"},
                "A plugin's path is a directory in its repository, as plugins/eval-queue.",
            ),
            (
                {"path": "plugins/./gpus"},
                "A plugin's path is a directory in its repository, as plugins/eval-queue.",
            ),
        ]
        for params, error in refused:
            self.assert_json_error(
                self.post_install(hamlet, manifest=json.dumps(remote), secrets={}, **params), error
            )
        self.assert_json_error(
            self.post_install(
                hamlet, manifest=json.dumps(remote), source_kind="github", secrets={}
            ),
            "A plugin whose code is here says how to run it, in run.",
        )
        self.assert_json_error(
            self.post_install(hamlet, source_kind="remote", sha=""),
            "A remote plugin's manifest names its service, in remote.",
        )

        # The team's plugins repository, a directory of it at a commit.
        verona = Stream.objects.get(realm=hamlet.realm, name="Verona")
        repository = Repository.objects.create(
            realm=hamlet.realm, stream=verona, repo_cache_path="/srv/armada/projects/plugins.git"
        )
        install = self.install(
            hamlet,
            source_kind="local",
            source="",
            repository_id=repository.id,
            path="/plugins/wandb/",
        )
        self.assertEqual(
            (install.source_kind, install.source_repository, install.source_path, install.sha),
            ("local", repository, "plugins/wandb", SHA),
        )
        self.assertEqual(
            self.assert_json_success(self.client_get("/json/armada/plugins"))["plugins"][0][
                "source_repository_id"
            ],
            repository.id,
        )
        self.discard(install)
        unready = Repository.objects.create(
            realm=hamlet.realm, stream=Stream.objects.get(realm=hamlet.realm, name="Denmark")
        )
        secret = self.make_stream("private-plugins", invite_only=True)
        hidden = Repository.objects.create(
            realm=hamlet.realm, stream=secret, repo_cache_path="/srv/armada/projects/hidden.git"
        )
        lear = Repository.objects.create(
            realm=self.lear_user("cordelia").realm,
            stream=Stream.objects.filter(realm=self.lear_user("cordelia").realm).order_by("id")[0],
            repo_cache_path="/srv/armada/projects/lear.git",
        )
        for repository_id, error in (
            (unready.id, "No such repository"),
            (lear.id, "No such repository"),
            (hidden.id, "Invalid channel ID"),
        ):
            self.assert_json_error(
                self.post_install(
                    hamlet, source_kind="local", source="", repository_id=str(repository_id)
                ),
                error,
            )

    def test_six_widgets_show_for_everyone_and_then_they_are_offered(self) -> None:
        hamlet = self.example_user("hamlet")

        def gpus(index: int, **changes: Any) -> dict[str, Any]:
            manifest = self.manifest("gpus")
            manifest.update(id=f"gpus-{index}", name=f"GPUs {index}")
            manifest["provides"]["widgets"][0].update(changes)
            return manifest

        def install(index: int, **params: Any) -> PluginInstall:
            return self.install(
                hamlet,
                plugin="gpus",
                manifest=json.dumps(params.pop("manifest", gpus(index))),
                settings={"manage_prefix": "nl"},
                secrets={},
                **params,
            )

        def nav(install: PluginInstall) -> str:
            return install.widget_defaults["gpus"]["nav"]

        self.assertEqual(install(0).widget_defaults, {"gpus": {"nav": "shown", "column": True}})
        # A widget its maker made offered is offered; a draft, a removed or
        # a turned off plugin does not count.
        self.assertEqual(nav(install(1, manifest=gpus(1, team_default="offered"))), "offered")
        install_plugin(
            hamlet,
            manifest=gpus(2),
            source_kind="github",
            source="github.com/plugyawn/armada-gpus",
            sha=SHA,
            plugin_settings={"manage_prefix": "nl"},
            draft=True,
        )
        off = install(3)
        update_plugin(hamlet, off, disabled=True)
        shown = [install(index) for index in range(4, 9)]
        self.assertEqual([nav(install) for install in shown], ["shown"] * 5)
        self.assertEqual(nav(install(9)), "offered")
        # Its maker may still show it.
        self.assertEqual(
            nav(install(10, widget_defaults=json.dumps({"gpus": {"nav": "shown"}}))), "shown"
        )
        for choice, error in (
            ({"jobs": {"nav": "shown"}}, "GPUs 11 has no widget named jobs."),
            ({"gpus": {"nav": "maybe"}}, None),
            ({"gpus": "shown"}, None),
            ({"gpus": {"nav": "shown", "pinned": True}}, None),
            ({"gpus": {"column": "yes"}}, None),
        ):
            self.assert_json_error(
                self.post_install(
                    hamlet,
                    plugin="gpus",
                    manifest=json.dumps(gpus(11)),
                    settings={"manage_prefix": "nl"},
                    secrets={},
                    widget_defaults=json.dumps(choice),
                ),
                error or "A widget's default is its nav (shown, offered or off) and its column.",
            )
        wandb = self.install(hamlet)
        for wrong in ({"run": {"nav": "shown"}}, {"run": {"column": True}}):
            self.assert_json_error(
                self.patch(hamlet, wandb, widget_defaults=wrong),
                "A widget's default is its nav (shown, offered or off) and its column.",
            )

    def test_a_draft_is_its_makers_until_it_is_installed(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        verona = Stream.objects.get(realm=hamlet.realm, name="Verona")
        origin = PluginOrigin(made_by=hamlet, stream=verona, topic="lr-sweep", message_id=42)
        draft = install_plugin(
            hamlet,
            manifest=self.manifest(),
            source_kind="github",
            source="github.com/plugyawn/armada-wandb",
            sha=SHA,
            draft=True,
            origin=origin,
        )
        bot = draft.bot
        self.assertEqual(
            (draft.state, draft.state_detail), (PluginInstall.DRAFT, "entity, api_key")
        )
        self.assertTrue(bot.is_active)
        self.assertEqual(self.audits(draft), [])
        # Its maker sets it up; it stays a draft.
        self.assert_json_success(
            self.patch(hamlet, draft, settings={"entity": "runix"}, secrets={"api_key": WANDB_KEY})
        )
        draft.refresh_from_db()
        self.assertEqual((draft.state, draft.state_detail), (PluginInstall.DRAFT, ""))
        [listed] = self.assert_json_success(self.client_get("/json/armada/plugins"))["plugins"]
        self.assertEqual(
            (
                listed["state"],
                listed["made_by"],
                listed["made_in_stream_id"],
                listed["made_in_topic"],
                listed["made_in_message_id"],
            ),
            ("draft", hamlet.id, verona.id, "lr-sweep", 42),
        )
        # No one else sees it, or takes its id.
        self.login_user(cordelia)
        self.assertEqual(
            self.assert_json_success(self.client_get("/json/armada/plugins"))["plugins"], []
        )
        self.assert_json_error(
            self.patch(cordelia, draft, disabled=True), "No such plugin", status_code=404
        )
        self.assert_json_error(
            self.post_install(cordelia), "Weights & Biases is being made, and holds its id."
        )
        # Checked again, at another commit, it is still a draft.
        again = install_plugin(
            hamlet,
            manifest=self.manifest(),
            source_kind="github",
            source="github.com/plugyawn/armada-wandb",
            sha="5" * 40,
            draft=True,
            origin=origin,
        )
        self.assertEqual(
            (again.id, again.state, again.sha), (draft.id, PluginInstall.DRAFT, "5" * 40)
        )
        # Installed, it keeps its bot, settings and secrets.
        installed = install_plugin(
            hamlet,
            manifest=self.manifest(),
            source_kind="github",
            source="github.com/plugyawn/armada-wandb",
            sha=SHA,
            origin=origin,
        )
        self.assertEqual(
            (installed.id, installed.bot, installed.state), (draft.id, bot, PluginInstall.STARTING)
        )
        self.assertEqual(installed.settings, {"entity": "runix", "posts": "both"})
        self.assertEqual(armada_plugin_secret_environment(installed), {"WANDB_API_KEY": WANDB_KEY})
        self.assertEqual(
            self.audits(installed),
            [
                (
                    AuditLogEventType.PLUGIN_MADE,
                    hamlet.id,
                    {
                        "install_id": draft.id,
                        "plugin": "wandb",
                        "from_sha": None,
                        "to_sha": SHA,
                        "from_version": None,
                        "to_version": "0.4.1",
                        "run_id": None,
                        "for_user_id": hamlet.id,
                    },
                )
            ],
        )
        # Once installed, the team sees it.
        self.login_user(cordelia)
        self.assertEqual(
            len(self.assert_json_success(self.client_get("/json/armada/plugins"))["plugins"]), 1
        )

    def test_a_discarded_draft_leaves_its_id_and_bot_free(self) -> None:
        hamlet = self.example_user("hamlet")
        manifest = self.manifest()
        manifest["name"] = "W&B, early"
        draft = install_plugin(
            hamlet,
            manifest=manifest,
            source_kind="github",
            source="github.com/plugyawn/armada-wandb",
            sha=SHA,
            plugin_settings={"entity": "runix"},
            secrets={"api_key": WANDB_KEY},
            draft=True,
        )
        self.login_user(hamlet)
        self.assert_json_success(self.client_delete(f"/json/armada/plugins/{draft.id}"))
        self.assertEqual(
            self.rows(draft.id), {"installs": 0, "secrets": 0, "links": 0, "widgets": 0}
        )
        bot = UserProfile.objects.get(id=draft.bot_id)
        self.assertFalse(bot.is_active)
        # Installed afresh, with its own name, by its bot.
        install = self.install(hamlet)
        self.assertEqual((install.bot_id, install.bot.full_name), (bot.id, "Weights & Biases"))
        # A draft made again over its own draft renames its bot too.
        self.discard(install)
        renamed = install_plugin(
            hamlet,
            manifest=manifest,
            source_kind="github",
            source="github.com/plugyawn/armada-wandb",
            sha=SHA,
            draft=True,
        )
        install_plugin(
            hamlet,
            manifest=self.manifest(),
            source_kind="github",
            source="github.com/plugyawn/armada-wandb",
            sha=SHA,
            draft=True,
        )
        renamed.refresh_from_db()
        self.assertEqual(renamed.bot.full_name, "Weights & Biases")

    def test_an_update_moves_it_to_another_commit_and_back(self) -> None:
        hamlet = self.example_user("hamlet")
        cordelia = self.example_user("cordelia")
        install = self.install(hamlet, settings={"entity": "runix", "posts": "crashes"})
        self.feed(install)
        PluginWidgetState.objects.create(
            install=install, widget="run", item="lr-1e-4-a", document={"version": 1, "blocks": []}
        )
        old = self.manifest()
        new = self.manifest()
        new["version"] = "0.5.0"
        new["name"] = "Weights and Biases"
        new["provides"]["widgets"] = new["provides"]["widgets"][:1]
        new["provides"]["events"] = ["message", "agent_run", "reaction"]
        new["reaches"] = ["api.wandb.ai", "files.wandb.ai"]
        new["secrets"] = {"token": {"title": "Token", "env": "WANDB_TOKEN"}}
        del new["settings"]["properties"]["posts"]
        diff = update_plugin_version(cordelia, install, sha="6" * 40, manifest=new, for_user=hamlet)
        self.assertEqual(
            diff,
            {
                "hosts": ["files.wandb.ai"],
                "secrets": ["token"],
                "events": ["reaction"],
                "posts": False,
                "dropped_widgets": ["run"],
            },
        )
        install.refresh_from_db()
        self.assertEqual(
            (install.sha, install.version, install.name, install.bot.full_name, install.generation),
            ("6" * 40, "0.5.0", "Weights and Biases", "Weights and Biases", 2),
        )
        self.assertEqual(install.settings, {"entity": "runix"})
        self.assertEqual(install.widget_defaults, {"runs": {"nav": "shown", "column": True}})
        self.assertEqual(
            list(
                PluginWidgetState.objects.filter(install=install).values_list("widget", flat=True)
            ),
            ["runs"],
        )
        self.assertFalse(PluginSecret.objects.filter(install=install).exists())
        self.assertEqual(
            self.audits(install)[-1],
            (
                AuditLogEventType.PLUGIN_UPDATED,
                cordelia.id,
                {
                    "install_id": install.id,
                    "plugin": "wandb",
                    "from_sha": SHA,
                    "to_sha": "6" * 40,
                    "from_version": "0.4.1",
                    "to_version": "0.5.0",
                    "run_id": None,
                    "for_user_id": hamlet.id,
                    "diff": diff,
                },
            ),
        )
        # Undone: back to the commit it came from.
        self.assertEqual(
            update_plugin_version(cordelia, install, sha=SHA, manifest=old)["dropped_widgets"], []
        )
        install.refresh_from_db()
        self.assertEqual(
            (install.sha, install.version, install.name), (SHA, "0.4.1", "Weights & Biases")
        )
        self.assertEqual(install.widget_defaults["run"], {"nav": "off", "column": False})
        self.assertEqual(self.audits(install)[-1][2]["for_user_id"], cordelia.id)
        for manifest, sha, error in (
            (
                {**old, "id": "other"},
                SHA,
                "The manifest at that commit is another plugin's: other.",
            ),
            (old, "main", "A plugin is installed at a commit: 40 hexadecimal digits."),
        ):
            with self.assertRaisesRegex(JsonableError, f"^{re.escape(error)}$"):
                update_plugin_version(cordelia, install, sha=sha, manifest=manifest)
        remote = self.manifest()
        del remote["run"], remote["secrets"]
        remote.update(id="far", remote={"url": "https://plugins.example.com/far"})
        far = self.install(
            hamlet, manifest=json.dumps(remote), source_kind="remote", sha="", secrets={}
        )
        with self.assertRaisesRegex(JsonableError, r"^A remote plugin is updated where it runs\.$"):
            update_plugin_version(hamlet, far, sha=SHA, manifest=remote)

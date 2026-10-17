import json
import os
import re
import socket
import stat
import subprocess
import time
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

import responses
from django.test import override_settings
from typing_extensions import override

from zerver.lib.armada import (
    ARMADA_DEFAULT_STREAM,
    ARMADA_EMBEDDED_BOT_SERVICE,
    ArmadaWorkerRuntime,
)
from zerver.lib.armada_agent import (
    ARMADA_BROWSER_MCP_SERVER,
    ArmadaCodexAppServerToolRunner,
    ArmadaCodexAppServerTranscriptTranslator,
    armada_codex_thread_version,
    build_armada_codex_developer_instructions,
)
from zerver.lib.armada_plugin_agents import (
    ArmadaPluginServer,
    _warn_no_plugins_under_os_users,
    armada_plugin_config_args,
    armada_plugin_environment,
    armada_plugin_fingerprint,
    armada_plugin_instructions,
    armada_plugin_servers,
)
from zerver.lib.armada_plugin_validation import (
    ARMADA_AGENT_MCP_SERVERS,
    ArmadaPluginContractError,
    canonicalize_plugin_manifest,
)
from zerver.lib.armada_plugins import armada_plugin_token, install_plugin, update_plugin
from zerver.lib.armada_worker import latest_armada_codex_run_for_topic
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import AgentRun, PluginInstall, UserProfile, Workspace
from zerver.worker import armada_worker as armada_worker_module

SHA = "4e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d5"
FAKE_PLUGIN = (
    Path(__file__).parent / "fixtures" / "armada_plugins" / "fake" / "armada_fake_plugin.py"
)

# A Codex stand-in that does with its MCP flags what Codex does: for
# mcpServerStatus/list it reaches each plugin's server at its url, with
# the token from the variable bearer_token_env_var names, and lists its
# tools less those turned off. Its flags' values are JSON, as Armada
# writes them.
ARMADA_FAKE_MCP_APP_SERVER = """#!/usr/bin/env python3
import json, os, sys, urllib.request

servers = {}
arguments = sys.argv[1:]
for flag, value in zip(arguments, arguments[1:]):
    if flag == "--config" and value.startswith("mcp_servers."):
        key, _, raw = value.partition("=")
        _, name, field = key.split(".", 2)
        servers.setdefault(name, {})[field] = json.loads(raw)


def call(server, method, session):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": "Bearer " + os.environ.get(server["bearer_token_env_var"], ""),
    }
    if session:
        headers["Mcp-Session-Id"] = session
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}).encode()
    request = urllib.request.Request(server["url"], data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read()), response.headers.get("Mcp-Session-Id")


def status():
    data = []
    for name, server in sorted(servers.items()):
        try:
            _, session = call(server, "initialize", None)
            listed, _ = call(server, "tools/list", session)
        except OSError as error:
            data.append({"name": name, "runtimeStatus": "failed", "toolsError": str(error)})
            continue
        tools = [
            tool["name"]
            for tool in listed["result"]["tools"]
            if tool["name"] not in server.get("disabled_tools", [])
        ]
        data.append({"name": name, "runtimeStatus": "connected", "tools": tools})
    return {"data": data}


for line in sys.stdin:
    message = json.loads(line)
    if "id" in message and "method" in message:
        result = status() if message["method"] == "mcpServerStatus/list" else {}
        print(json.dumps({"id": message["id"], "result": result}), flush=True)
"""


class PluginTestCase(ZulipTestCase):
    def manifest(self, plugin: str) -> dict[str, Any]:
        return json.loads(self.fixture_data("armada-plugin.json", f"armada_plugins/{plugin}"))

    def install(
        self,
        plugin: str = "wandb",
        *,
        manifest: dict[str, Any] | None = None,
        user: UserProfile | None = None,
        state: int = PluginInstall.RUNNING,
        port: int | None = None,
        **kwargs: Any,
    ) -> PluginInstall:
        manifest = manifest or self.manifest(plugin)
        install = install_plugin(
            user or self.example_user("hamlet"),
            manifest=manifest,
            source_kind="github",
            source=f"github.com/plugyawn/armada-{manifest['id']}",
            sha=SHA,
            plugin_settings={"wandb": {"entity": "runix"}, "gpus": {"manage_prefix": "nl"}}.get(
                manifest["id"], {}
            ),
            **kwargs,
        )
        PluginInstall.objects.filter(id=install.id).update(state=state, port=port)
        install.refresh_from_db()
        return install

    def serve_fake_plugin(self, install: PluginInstall, **settings: object) -> None:
        """The fake plugin on a port of its own, as the host starts it."""
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        PluginInstall.objects.filter(id=install.id).update(port=port)
        install.refresh_from_db()
        process = subprocess.Popen(
            ["python3", str(FAKE_PLUGIN)],
            env={
                "PATH": os.environ["PATH"],
                "ARMADA_PORT": str(port),
                "ARMADA_PLUGIN_TOKEN": armada_plugin_token(install),
                "ARMADA_SETTINGS": json.dumps(settings),
                "ARMADA_EMAIL": install.bot.email,
                "ARMADA_API_KEY": install.bot.api_key,
            },
            stdout=subprocess.DEVNULL,
        )
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1):
                    return
            except OSError:
                time.sleep(0.05)
        raise AssertionError("the fake plugin did not start")


class PluginAgentTest(PluginTestCase):
    """The team's plugins as the agent's tools (zerver/lib/armada_plugin_agents.py,
    PL4): each running plugin's MCP server in Codex's flags, its token in
    the app-server's environment alone, a line of the instructions each,
    the plugin's name on the trace, a fresh thread when the plugins change
    what a thread is told, and a warm app-server restarted between turns
    when they change what it was started with."""

    def test_each_plugin_that_offers_tools_is_one_of_codexs_servers(self) -> None:
        wandb = self.install("wandb", port=47101)
        update_plugin(self.example_user("hamlet"), wandb, disabled_tools=["compare_runs"])
        wandb.refresh_from_db()
        # Starting or failed: its line of the instructions, and no server.
        self.install("gpus", state=PluginInstall.FAILED)
        # Turned off, removed, a draft, without tools, or another realm's: nothing.
        fake = self.install("fake", port=47102)
        update_plugin(self.example_user("hamlet"), fake, disabled=True)
        PluginInstall.objects.filter(id=fake.id).update(state=PluginInstall.RUNNING)
        removed = self.manifest("fake") | {"id": "removed"}
        self.install(manifest=removed, state=PluginInstall.REMOVED, port=47103)
        draft = self.manifest("fake") | {"id": "draft"}
        self.install(manifest=draft, state=PluginInstall.DRAFT, port=47104)
        no_tools = self.manifest("fake") | {"id": "quiet"}
        del no_tools["provides"]["tools"]
        self.install(manifest=no_tools, port=47105)
        self.install("wandb", user=self.lear_user("cordelia"), port=47106)

        servers = armada_plugin_servers(self.example_user("hamlet").realm)
        token = armada_plugin_token(wandb)
        self.assertEqual(
            servers,
            [
                ArmadaPluginServer(
                    server="gpus",
                    install_id=PluginInstall.objects.get(plugin_id="gpus").id,
                    name="GPUs",
                    glyph="gpu",
                    instructions=(
                        "Use for GPUs: where there is room, what runs where,"
                        " and pods to make or end."
                    ),
                    running=False,
                ),
                ArmadaPluginServer(
                    server="wandb",
                    install_id=wandb.id,
                    name="Weights & Biases",
                    glyph="chart",
                    instructions="Use for anything about training runs: state, metrics, crashes.",
                    running=True,
                    url="http://127.0.0.1:47101/mcp",
                    token=token,
                    generation=wandb.generation,
                    disabled_tools=("compare_runs",),
                ),
            ],
        )
        self.assertEqual(
            armada_plugin_config_args(servers),
            [
                "--config",
                'mcp_servers.wandb.url="http://127.0.0.1:47101/mcp"',
                "--config",
                'mcp_servers.wandb.bearer_token_env_var="ARMADA_PLUGIN_TOKEN_WANDB"',
                "--config",
                "mcp_servers.wandb.required=false",
                "--config",
                "mcp_servers.wandb.startup_timeout_sec=10",
                "--config",
                "mcp_servers.wandb.tool_timeout_sec=30",
                "--config",
                'mcp_servers.wandb.default_tools_approval_mode="approve"',
                "--config",
                'mcp_servers.wandb.disabled_tools=["compare_runs"]',
            ],
        )
        self.assertEqual(armada_plugin_environment(servers), {"ARMADA_PLUGIN_TOKEN_WANDB": token})
        self.assertEqual(
            armada_plugin_instructions(servers),
            "Your team's plugins give you tools, from MCP servers named by each plugin's id."
            " A plugin that is not running has none at the moment:\n"
            "- GPUs (gpus): Use for GPUs: where there is room, what runs where,"
            " and pods to make or end.\n"
            "- Weights & Biases (wandb): Use for anything about training runs:"
            " state, metrics, crashes.\n",
        )
        self.assertEqual(armada_plugin_instructions([]), "")
        # A plugin with no instructions is listed by its name, and an id
        # with a hyphen still names a variable.
        quiet = ArmadaPluginServer(
            server="armada-gpus",
            install_id=1,
            name="Pods",
            glyph="gpu",
            instructions="",
            running=True,
        )
        self.assertEqual(
            armada_plugin_instructions([quiet]).splitlines()[1], "- Pods (armada-gpus)"
        )
        self.assertEqual(quiet.token_variable, "ARMADA_PLUGIN_TOKEN_ARMADA_GPUS")

    def test_a_server_without_its_plugin_key_gives_codex_no_plugin(self) -> None:
        self.install("wandb", port=47101)
        with override_settings(DEVELOPMENT=False, ARMADA_PLUGIN_SECRETS_KEY=""):
            [server] = armada_plugin_servers(self.example_user("hamlet").realm)
        self.assertEqual((server.server, server.running, server.token), ("wandb", False, ""))

    def test_per_topic_os_users_get_no_plugins(self) -> None:
        self.install("wandb", port=47101)
        _warn_no_plugins_under_os_users.cache_clear()
        with (
            override_settings(ARMADA_TOPIC_ISOLATION_BACKEND="os-user"),
            self.assertLogs("zerver.lib.armada_plugin_agents", "WARNING") as logs,
        ):
            self.assertEqual(armada_plugin_servers(self.example_user("hamlet").realm), [])
            self.assertEqual(armada_plugin_servers(self.example_user("hamlet").realm), [])
        # Said once a process.
        self.assertEqual(
            logs.output,
            [
                "WARNING:zerver.lib.armada_plugin_agents:The agent gets no plugin tools:"
                " ARMADA_TOPIC_ISOLATION_BACKEND is os-user, and sudo would drop the tokens"
                " from the app-server's environment."
            ],
        )

    def test_the_app_server_reaches_the_plugin_with_the_token_from_its_environment(self) -> None:
        fake = self.install("fake")
        update_plugin(self.example_user("hamlet"), fake, disabled_tools=["pod_terminate"])
        PluginInstall.objects.filter(id=fake.id).update(state=PluginInstall.RUNNING)
        self.serve_fake_plugin(fake)
        servers = armada_plugin_servers(fake.realm)
        token = armada_plugin_token(fake)
        with TemporaryDirectory() as directory:
            script = Path(directory) / "codex"
            script.write_text(ARMADA_FAKE_MCP_APP_SERVER)
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
            runner = ArmadaCodexAppServerToolRunner(
                codex_bin=str(script),
                workdir=directory,
                model="gpt-6-astra",
                sandbox="workspace-write",
                approval_policy="never",
                timeout_seconds=10,
                output_limit=200,
                plugin_servers=servers,
            )
            self.addCleanup(runner.close)
            # Nothing anyone can read of the process holds the token.
            self.assertFalse(any(token in part for part in runner.build_command()))
            runner.ensure_started()
            self.assertEqual(
                runner.request("mcpServerStatus/list", {}),
                {"data": [{"name": "fake", "runtimeStatus": "connected", "tools": ["echo"]}]},
            )
            assert runner.process is not None
            with open(f"/proc/{runner.process.pid}/cmdline", "rb") as cmdline:
                self.assertNotIn(token.encode(), cmdline.read())

            # A runner without the plugin has neither its flags nor its token.
            bare = ArmadaCodexAppServerToolRunner(
                codex_bin=str(script),
                workdir=directory,
                model="gpt-6-astra",
                sandbox="workspace-write",
                approval_policy="never",
                timeout_seconds=10,
                output_limit=200,
            )
            self.addCleanup(bare.close)
            bare.ensure_started()
            self.assertEqual(bare.request("mcpServerStatus/list", {}), {"data": []})

    def test_each_plugin_has_a_line_of_a_new_threads_instructions(self) -> None:
        self.install("wandb", port=47101)
        servers = armada_plugin_servers(self.example_user("hamlet").realm)
        runner = ArmadaCodexAppServerToolRunner(
            codex_bin="codex",
            workdir="/srv/armada-workspaces/lr-sweep",
            model="gpt-6-astra",
            sandbox="workspace-write",
            approval_policy="never",
            timeout_seconds=10,
            output_limit=200,
            plugin_servers=servers,
        )
        line = "- Weights & Biases (wandb): Use for anything about training runs"
        self.assertIn(line, runner.thread_params()["developerInstructions"])
        self.assertIn(line, runner.side_fork_params("thread-1")["developerInstructions"])
        self.assertNotIn(line, build_armada_codex_developer_instructions())
        # A resumed thread keeps the instructions it was started with.
        self.assertNotIn("developerInstructions", runner.thread_params(existing_thread_id="t"))

    def test_the_trace_names_the_plugin_of_a_tool_call(self) -> None:
        wandb = self.install("wandb", port=47101)
        translator = ArmadaCodexAppServerTranscriptTranslator(
            plugin_servers=armada_plugin_servers(wandb.realm)
        )

        def call(server: str) -> dict[str, Any]:
            [event] = translator.events_from_message(
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "mcpToolCall",
                            "id": "call-1",
                            "server": server,
                            "tool": "list_runs",
                            "status": "completed",
                        }
                    },
                }
            )
            return dict(event)

        named = call("wandb")
        self.assertEqual((named["kind"], named["title"]), ("tool", "Weights & Biases: list_runs"))
        self.assertEqual(
            named["metadata"]["plugin"],
            {"id": "wandb", "install_id": wandb.id, "name": "Weights & Biases", "glyph": "chart"},
        )
        other = call("elsewhere")
        self.assertEqual(other["title"], "list_runs")
        self.assertNotIn("plugin", other["metadata"])

    def test_a_topic_starts_a_fresh_thread_when_the_plugins_change_its_instructions(
        self,
    ) -> None:
        hamlet = self.example_user("hamlet")
        bot = self.create_test_bot(
            "onix",
            hamlet,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        workspace = Workspace.objects.create(
            realm=hamlet.realm, stream_name=ARMADA_DEFAULT_STREAM, topic_name="lr-sweep"
        )
        run = AgentRun.objects.create(
            realm=hamlet.realm,
            bot_profile=bot,
            requesting_user=hamlet,
            trigger_message_id=9201,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name="lr-sweep",
            workspace_node=workspace,
            status=AgentRun.DONE,
            codex_thread_id="thread-1",
            codex_thread_version=armada_codex_thread_version(),
        )

        def resumed() -> AgentRun | None:
            return latest_armada_codex_run_for_topic(
                bot, stream=ARMADA_DEFAULT_STREAM, topic="lr-sweep", before_message_id=9300
            )

        self.assertEqual(resumed(), run)
        wandb = self.install("wandb", port=47101)
        # A thread told of no plugins is not resumed once one offers tools.
        self.assertIsNone(resumed())
        AgentRun.objects.filter(id=run.id).update(
            codex_thread_version=armada_codex_thread_version(armada_plugin_servers(hamlet.realm))
        )
        self.assertEqual(resumed(), run)
        # A plugin failing and coming back, or a new token or generation,
        # changes nothing a thread was told.
        PluginInstall.objects.filter(id=wandb.id).update(
            state=PluginInstall.FAILED, generation=wandb.generation + 1
        )
        self.assertEqual(resumed(), run)
        manifest = wandb.manifest
        manifest["provides"]["tools"]["instructions"] = "Use for runs."
        PluginInstall.objects.filter(id=wandb.id).update(manifest=manifest)
        self.assertIsNone(resumed())

    def test_a_warm_app_server_restarts_between_turns_when_its_plugins_change(self) -> None:
        class FingerprintedRunner:
            def __init__(self, servers: list[ArmadaPluginServer]) -> None:
                self.plugin_fingerprint = armada_plugin_fingerprint(servers)

        class FakeRuntime(ArmadaWorkerRuntime):
            closed = False

            @override
            def close(self) -> None:
                self.closed = True

        built: list[FakeRuntime] = []

        def build_runtime(**kwargs: Any) -> FakeRuntime:
            runtime = FakeRuntime()
            runtime.tool_runner = FingerprintedRunner(kwargs["plugin_servers"])  # type: ignore[assignment]  # A runner as the cache sees it.
            built.append(runtime)
            return runtime

        wandb = self.install("wandb", port=47101)
        realm = wandb.realm

        def turn() -> ArmadaWorkerRuntime:
            runtime = armada_worker_module.get_armada_worker_runtime(
                "gpt-6-astra",
                "/srv/armada-workspaces/lr-sweep",
                "medium",
                None,
                None,
                armada_plugin_servers(realm),
            )
            armada_worker_module.release_armada_worker_runtime(runtime)
            return runtime

        armada_worker_module.close_armada_worker_runtimes()
        self.addCleanup(armada_worker_module.close_armada_worker_runtimes)
        with mock.patch.object(
            armada_worker_module,
            "build_armada_worker_runtime_from_settings",
            side_effect=build_runtime,
        ):
            first = turn()
            self.assertIs(turn(), first)
            # The plugin's settings change, which restarts it.
            update_plugin(self.example_user("hamlet"), wandb, plugin_settings={"entity": "other"})
            PluginInstall.objects.filter(id=wandb.id).update(state=PluginInstall.RUNNING)
            second = turn()
            self.assertIsNot(second, first)
            self.assertTrue(built[0].closed)
            self.assertIs(turn(), second)
            # A tool turned off restarts it too.
            update_plugin(self.example_user("hamlet"), wandb, disabled_tools=["compare_runs"])
            self.assertIsNot(turn(), second)
        # A runtime with no app-server is one with no plugins.
        self.assertEqual(
            armada_worker_module.armada_worker_runtime_plugin_fingerprint(ArmadaWorkerRuntime()),
            armada_plugin_fingerprint([]),
        )

    def test_no_plugin_takes_the_agents_own_server_name(self) -> None:
        self.assertIn(ARMADA_BROWSER_MCP_SERVER, ARMADA_AGENT_MCP_SERVERS)
        with self.assertRaisesRegex(
            ArmadaPluginContractError,
            r"^\$\.id: 'playwright' names one of the agent's own tools; choose another id$",
        ):
            canonicalize_plugin_manifest(self.manifest("fake") | {"id": "playwright"})


class PluginMCPRouteTest(PluginTestCase):
    """/api/v1/armada/plugins/<id>/mcp (zerver/lib/armada_plugin_mcp.py):
    an agent off the server, with an Armada API key, reaches a running
    plugin's MCP endpoint through Armada, which presents the plugin's
    token, names the caller, keeps the tools turned off out, and answers
    only JSON."""

    @override
    def setUp(self) -> None:
        super().setUp()
        # The tests may not reach the internet; the fake plugin, on this
        # machine, they do reach.
        self.network = responses.RequestsMock(assert_all_requests_are_fired=False)
        self.network.add_passthru(re.compile(r"^http://127\.0\.0\.1:[0-9]+/"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.fake = self.install("fake")
        update_plugin(self.example_user("hamlet"), self.fake, disabled_tools=["pod_terminate"])
        PluginInstall.objects.filter(id=self.fake.id).update(state=PluginInstall.RUNNING)
        self.fake.refresh_from_db()

    def mcp(
        self,
        user: UserProfile | None,
        message: object,
        *,
        install: PluginInstall | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        install = install or self.fake
        headers = dict(headers or {})
        if user is not None:
            headers["Authorization"] = self.encode_user(user)
        return self.client_post(
            f"/api/v1/armada/plugins/{install.id}/mcp",
            message if isinstance(message, str) else json.dumps(message),
            content_type="application/json",
            intentionally_undocumented=True,
            headers=headers,
            subdomain=user.realm.subdomain if user is not None else "zulip",
        )

    def request(self, method: str, **params: object) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": 7, "method": method, "params": params}

    def test_an_agent_off_the_server_uses_the_teams_plugin(self) -> None:
        self.serve_fake_plugin(self.fake)
        hamlet = self.example_user("hamlet")
        result = self.mcp(hamlet, self.request("initialize", protocolVersion="2025-06-18"))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result["Content-Type"], "application/json")
        self.assertEqual(result["Mcp-Session-Id"], "fake-session")
        self.assertEqual(result.json()["result"]["serverInfo"]["name"], "fake")
        result = self.mcp(
            hamlet,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Mcp-Session-Id": "fake-session"},
        )
        self.assertEqual((result.status_code, result.content), (202, b""))

        listed = self.mcp(hamlet, self.request("tools/list")).json()
        self.assertEqual([tool["name"] for tool in listed["result"]["tools"]], ["echo"])
        called = self.mcp(
            hamlet,
            self.request("tools/call", name="echo", arguments={"gpu": "H100"}, _meta={"k": 1}),
        ).json()
        self.assertEqual(
            json.loads(called["result"]["content"][0]["text"]),
            {
                "arguments": {"gpu": "H100"},
                "_meta": {
                    "k": 1,
                    "armada/user": {
                        "id": hamlet.id,
                        "email": hamlet.email,
                        "full_name": hamlet.full_name,
                    },
                },
            },
        )
        refused = self.mcp(hamlet, self.request("tools/call", name="pod_terminate")).json()
        self.assertEqual(
            refused,
            {
                "jsonrpc": "2.0",
                "id": 7,
                "error": {"code": -32602, "message": "pod_terminate is turned off for this team."},
            },
        )
        # A person's own bot, such as a mini, may too.
        mini = self.create_test_bot("mini", hamlet)
        self.assertEqual(self.mcp(mini, self.request("tools/list")).status_code, 200)

        # The call is in Recent; the refused one never reached the plugin.
        self.login_user(hamlet)
        lines = self.assert_json_success(
            self.client_get(f"/json/armada/plugins/{self.fake.id}/recent")
        )["recent"]
        self.assertEqual(
            [(line["type"], line["acting_user_id"], line["detail"]) for line in lines[:1]],
            [("tool_called", hamlet.id, {"tool": "echo", "via": "mcp", "is_error": False})],
        )
        self.assertEqual([line["type"] for line in lines].count("tool_called"), 1)

        deleted = self.client_delete(
            f"/api/v1/armada/plugins/{self.fake.id}/mcp",
            HTTP_AUTHORIZATION=self.encode_user(hamlet),
            HTTP_MCP_SESSION_ID="fake-session",
            intentionally_undocumented=True,
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            self.client_get(
                f"/api/v1/armada/plugins/{self.fake.id}/mcp",
                HTTP_AUTHORIZATION=self.encode_user(hamlet),
                intentionally_undocumented=True,
            ).status_code,
            405,
        )

    def test_a_plugin_answering_with_events_is_answered_as_json(self) -> None:
        self.serve_fake_plugin(self.fake, sse=True)
        result = self.mcp(self.example_user("hamlet"), self.request("tools/list"))
        self.assertEqual(result["Content-Type"], "application/json")
        self.assertEqual([tool["name"] for tool in result.json()["result"]["tools"]], ["echo"])
        errored = self.mcp(
            self.example_user("hamlet"), self.request("tools/call", name="no_such_tool")
        ).json()
        self.assertEqual(errored["error"]["message"], "unknown tool")

    def test_who_reaches_a_plugins_tools(self) -> None:
        self.serve_fake_plugin(self.fake)
        tools_list = self.request("tools/list")
        self.assertEqual(self.mcp(None, tools_list).status_code, 401)
        self.assert_json_error(
            self.mcp(self.lear_user("cordelia"), tools_list), "No such plugin", status_code=404
        )
        self.assert_json_error(
            self.mcp(self.example_user("polonius"), tools_list),
            "Guests cannot use the team's plugins' tools.",
        )
        self.assert_json_error(
            self.mcp(self.fake.bot, tools_list), "A plugin's bot cannot use the plugins' tools."
        )
        hamlet = self.example_user("hamlet")
        for state, error in (
            (PluginInstall.REMOVED, "No such plugin"),
            (PluginInstall.DRAFT, "No such plugin"),
            (PluginInstall.FAILED, "Fake is not running."),
        ):
            PluginInstall.objects.filter(id=self.fake.id).update(state=state)
            self.assert_json_error(
                self.mcp(hamlet, tools_list),
                error,
                status_code=404 if error == "No such plugin" else 400,
            )
        PluginInstall.objects.filter(id=self.fake.id).update(
            state=PluginInstall.RUNNING, disabled=True
        )
        self.assert_json_error(self.mcp(hamlet, tools_list), "Fake is not running.")
        quiet = self.install(manifest=self.manifest("fake") | {"id": "quiet", "provides": {}})
        self.assert_json_error(self.mcp(hamlet, tools_list, install=quiet), "Fake has no tools.")

    def test_what_a_plugin_or_a_caller_gets_wrong(self) -> None:
        hamlet = self.example_user("hamlet")
        # A port nothing listens on, where the answers below are made up.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        PluginInstall.objects.filter(id=self.fake.id).update(port=port)
        self.fake.refresh_from_db()
        endpoint = f"http://127.0.0.1:{port}/mcp"
        for body, error in (
            ("not json", "An MCP message is one JSON object."),
            ("[1, 2]", "An MCP message is one JSON object."),
            (json.dumps(self.request("tools/call")), "A tool call names its tool."),
        ):
            self.assert_json_error(self.mcp(hamlet, body), error)
        self.assert_json_error(
            self.mcp(hamlet, self.request("tools/list")),
            "Fake could not be reached: ConnectionError.",
        )
        for status, code in ((401, 400), (500, 400), (404, 404)):
            self.network.upsert(responses.POST, endpoint, status=status)
            self.assert_json_error(
                self.mcp(hamlet, self.request("tools/list")),
                f"Fake answered with HTTP {status}.",
                status_code=code,
            )
        self.network.upsert(responses.POST, endpoint, body="<html>", content_type="text/html")
        self.assert_json_error(
            self.mcp(hamlet, self.request("tools/list")),
            "Fake answered with something other than JSON.",
        )
        self.network.upsert(
            responses.POST,
            endpoint,
            body='event: message\ndata: {"jsonrpc": "2.0", "method": "notifications/progress"}\n\n',
            content_type="text/event-stream",
        )
        self.assert_json_error(self.mcp(hamlet, self.request("tools/list")), "Fake did not answer.")
        # What goes to the plugin: its own token, never the caller's key,
        # and the transport's headers.
        self.network.upsert(
            responses.POST,
            endpoint,
            json={"jsonrpc": "2.0", "id": 7, "result": {"tools": []}},
            headers={"Mcp-Session-Id": "s-2"},
        )
        result = self.mcp(
            hamlet,
            self.request("tools/list"),
            headers={"Mcp-Session-Id": "s-1", "MCP-Protocol-Version": "2025-06-18"},
        )
        self.assertEqual(result["Mcp-Session-Id"], "s-2")
        sent = self.network.calls[-1].request.headers
        self.assertEqual(
            (sent["Authorization"], sent["Mcp-Session-Id"], sent["MCP-Protocol-Version"]),
            (f"Bearer {armada_plugin_token(self.fake)}", "s-1", "2025-06-18"),
        )
        self.network.add(responses.DELETE, endpoint, status=500)
        self.assert_json_error(
            self.client_delete(
                f"/api/v1/armada/plugins/{self.fake.id}/mcp",
                HTTP_AUTHORIZATION=self.encode_user(hamlet),
                intentionally_undocumented=True,
            ),
            "Fake answered with HTTP 500.",
        )

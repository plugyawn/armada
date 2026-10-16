import json
from typing import Any

from zerver.lib.armada_plugin_contract import ARMADA_PLUGIN_MANIFEST_SCHEMA, ARMADA_WIDGET_SCHEMA
from zerver.lib.armada_plugin_validation import (
    ArmadaPluginContractError,
    canonicalize_plugin_manifest,
    canonicalize_widget_document,
)
from zerver.lib.test_classes import ZulipTestCase

RUN_STATES = '["done","live","waiting","needs","failed","paused","stopped"]'


class PluginContractTest(ZulipTestCase):
    """The plugin contract (tools/armada/plugin_contract.json): a manifest
    and a widget document are data, and nothing a plugin sends can carry
    HTML, CSS, a colour, a script, a picture or a glyph the sprite does
    not have."""

    def fixture(self, plugin: str, name: str) -> dict[str, Any]:
        return json.loads(self.fixture_data(name, f"armada_plugins/{plugin}"))

    def manifest(self, plugin: str = "wandb") -> dict[str, Any]:
        return self.fixture(plugin, "armada-plugin.json")

    def runs(self) -> dict[str, Any]:
        return self.fixture("wandb", "runs.json")

    def row(self, **fields: object) -> dict[str, Any]:
        return {"key": "lr-3e-4-b", "label": "lr-3e-4-b", **fields}

    def listed(self, *rows: dict[str, Any], **block: object) -> dict[str, Any]:
        return {
            "version": 1,
            "blocks": [{"type": "list", "label": "Training", "items": list(rows), **block}],
        }

    def refuses_manifest(self, manifest: object, error: str) -> None:
        with self.assertRaises(ArmadaPluginContractError) as context:
            canonicalize_plugin_manifest(manifest)
        self.assertEqual(str(context.exception), error)

    def refuses_widget(self, document: object, error: str) -> None:
        with self.assertRaises(ArmadaPluginContractError) as context:
            canonicalize_widget_document(document)
        self.assertEqual(str(context.exception), error)

    def test_the_first_two_plugins_and_their_widgets_pass(self) -> None:
        for plugin in ("wandb", "gpus"):
            manifest = self.manifest(plugin)
            self.assertEqual(canonicalize_plugin_manifest(manifest), manifest)
        for plugin, name in (("wandb", "runs.json"), ("wandb", "run.json"), ("gpus", "gpus.json")):
            document = self.fixture(plugin, name)
            canonical, encoded = canonicalize_widget_document(document)
            self.assertEqual(canonical, document)
            self.assertEqual(json.loads(encoded), document)

    def test_a_widget_refuses_html(self) -> None:
        self.refuses_widget(
            {"version": 1, "blocks": [{"type": "html", "html": "<b>3 running</b>"}]},
            "$.blocks[0]: value does not match any allowed contract variant",
        )
        self.refuses_widget(
            self.listed(self.row(sub="<b>bold</b>")),
            "$.blocks[0].items[0].sub: a plugin sends text, not HTML,"
            " and '<b>bold</b>' holds markup",
        )
        runs = self.runs()
        runs["data"]["running"] = "<img src=x onerror=alert(1)>"
        self.refuses_widget(
            runs,
            "$.data.running: a plugin sends text, not HTML,"
            " and '<img src=x onerror=alert(1)>' holds markup",
        )
        # A less-than sign in words is text.
        canonicalize_widget_document(self.listed(self.row(sub="loss < 2.3 since step 40,000")))
        # A tool's arguments go to the plugin and are never drawn.
        canonicalize_widget_document(
            self.listed(
                self.row(action={"label": "Grep", "tool": "grep", "arguments": {"q": "<tag>"}})
            )
        )

    def test_a_widget_refuses_css(self) -> None:
        self.refuses_widget(
            self.listed(self.row(style="font-weight: bold")),
            "$.blocks[0].items[0].style: unsupported property",
        )
        runs = self.runs()
        runs["css"] = ".armada-nav { display: none }"
        self.refuses_widget(runs, "$.css: unsupported property")
        self.refuses_widget(
            self.listed(self.row(sub="<style>* { color: red }</style>")),
            "$.blocks[0].items[0].sub: a plugin sends text, not HTML,"
            " and '<style>* { color: red }</style>' holds markup",
        )

    def test_a_widget_refuses_colours(self) -> None:
        self.refuses_widget(
            self.listed(self.row(color="#ff0000")),
            "$.blocks[0].items[0].color: unsupported property",
        )
        self.refuses_widget(
            self.listed(self.row(state="red")),
            f'$.blocks[0].items[0].state: expected one of {RUN_STATES}, got "red"',
        )
        # The nav's news is a mark, never a colour.
        runs = self.runs()
        runs["data"]["news"] = "red"
        self.refuses_widget(runs, "$.rest.news: 'red' is not failed, needs or empty")
        for news in ("failed", "needs", "", None):
            runs["data"]["news"] = news
            canonicalize_widget_document(runs)

    def test_a_widget_refuses_scripts_and_pictures(self) -> None:
        self.refuses_widget(
            self.listed(self.row(open={"url": "javascript:alert(document.cookie)"})),
            "$.blocks[0].items[0].open: value does not match any allowed contract variant",
        )
        self.refuses_widget(
            self.listed(self.row(sub="<script>alert(1)</script>")),
            "$.blocks[0].items[0].sub: a plugin sends text, not HTML,"
            " and '<script>alert(1)</script>' holds markup",
        )
        run = self.fixture("wandb", "run.json")
        run["actions"][0]["url"] = "javascript:alert(1)"
        self.refuses_widget(run, "$.actions[0]: value does not match any allowed contract variant")
        # A plugin brings no images: the artifact's media block is not in
        # the widget's vocabulary.
        self.refuses_widget(
            {
                "version": 1,
                "blocks": [
                    {
                        "type": "media",
                        "kind": "image",
                        "alt": "a chart",
                        "src": "/user_uploads/a.png",
                    }
                ],
            },
            "$.blocks[0]: value does not match any allowed contract variant",
        )
        self.assertNotIn("mediaBlock", ARMADA_WIDGET_SCHEMA["$defs"])

    def test_glyphs_are_the_sprites(self) -> None:
        with self.assertRaisesRegex(
            ArmadaPluginContractError,
            r'^\$\.blocks\[0\]\.items\[0\]\.glyph: expected one of \["search",.*, got "rocket"$',
        ):
            canonicalize_widget_document(self.listed(self.row(glyph="rocket")))
        manifest = self.manifest()
        manifest["glyph"] = "rocket"
        with self.assertRaisesRegex(
            ArmadaPluginContractError, r'^\$\.glyph: expected one of .*, got "rocket"$'
        ):
            canonicalize_plugin_manifest(manifest)
        glyphs = ARMADA_WIDGET_SCHEMA["$defs"]["glyph"]["enum"]
        self.assertEqual(ARMADA_PLUGIN_MANIFEST_SCHEMA["$defs"]["glyph"]["enum"], glyphs)
        self.assertIn("gpu", glyphs)
        self.assertIn("plugin", glyphs)

    def test_a_list_holds_at_most_32_rows_and_3_in_the_column(self) -> None:
        rows = [self.row(key=f"run-{index}", label=f"run-{index}") for index in range(33)]
        canonicalize_widget_document(self.listed(*rows[:32]))
        self.refuses_widget(self.listed(*rows), "$.blocks[0].items: array exceeds 32 items")
        runs = self.runs()
        runs["data"]["runs"] = rows
        # The bound rows are refused as items are.
        with self.assertRaisesRegex(ArmadaPluginContractError, r"^\$\.data\.runs: "):
            canonicalize_widget_document(runs)
        self.refuses_widget(
            self.listed(self.row(), slots=4), "$.blocks[0].slots: value must be <= 3"
        )
        manifest = self.manifest()
        manifest["provides"]["widgets"][0]["column_slots"] = 4
        self.refuses_manifest(manifest, "$.provides.widgets[0].column_slots: value must be <= 3")

    def test_a_remote_plugin_cannot_ask_for_secrets(self) -> None:
        manifest = self.manifest()
        del manifest["run"]
        manifest["remote"] = {"url": "https://plugins.example.com/wandb"}
        self.refuses_manifest(
            manifest,
            "$.secrets: a remote plugin cannot ask for secrets;"
            " they go only to processes on this server",
        )
        del manifest["secrets"]
        canonicalize_plugin_manifest(manifest)
        manifest["remote"]["url"] = "http://plugins.example.com/wandb"
        with self.assertRaisesRegex(ArmadaPluginContractError, r"^\$\.remote\.url: value "):
            canonicalize_plugin_manifest(manifest)

    def test_a_manifest_runs_a_program_or_names_a_service(self) -> None:
        manifest = self.manifest()
        manifest["remote"] = {"url": "https://plugins.example.com/wandb"}
        error = (
            "$: a plugin gives run (a program this server runs) or remote"
            " (a service its author hosts), one of the two"
        )
        self.refuses_manifest(manifest, error)
        del manifest["run"], manifest["remote"]
        self.refuses_manifest(manifest, error)

    def test_a_manifest_names_the_plugin_api_it_needs(self) -> None:
        manifest = self.manifest()
        manifest["requires"]["armada"] = ">=2"
        self.refuses_manifest(
            manifest,
            "$.requires.armada: the plugin needs version 2 of Armada's plugin API,"
            " and this server has version 1",
        )

    def test_a_manifest_names_its_things_once_and_plainly(self) -> None:
        manifest = self.manifest()
        manifest["description"] = "Runs <i>live</i>."
        self.refuses_manifest(
            manifest,
            "$.description: a plugin sends text, not HTML, and 'Runs <i>live</i>.' holds markup",
        )
        # A command line and a pattern are never drawn.
        manifest = self.manifest()
        manifest["run"]["command"] = ["sh", "-c", "armada-wandb </dev/null"]
        manifest["provides"]["links"]["project"]["pattern"] = "^(?P<entity>[^/]+)/[^/]+$"
        canonicalize_plugin_manifest(manifest)

        manifest = self.manifest()
        manifest["provides"]["widgets"][1]["key"] = "runs"
        self.refuses_manifest(manifest, "$.provides.widgets[1].key: 'runs' names two widgets")
        manifest = self.manifest()
        manifest["provides"]["widgets"][0]["in"] = ["nav", "nav"]
        self.refuses_manifest(manifest, "$.provides.widgets[0].in: names a place twice")
        manifest = self.manifest()
        del manifest["provides"]["widgets"][0]["column_slots"]
        error = (
            "$.provides.widgets[0]: a widget in the column gives column_slots,"
            " its rows there (1 to 3), and only such a widget"
        )
        self.refuses_manifest(manifest, error)
        manifest = self.manifest()
        manifest["provides"]["widgets"][1]["column_slots"] = 2
        self.refuses_manifest(manifest, error.replace("widgets[0]", "widgets[1]"))
        manifest = self.manifest()
        del manifest["provides"]["links"]
        self.refuses_manifest(
            manifest,
            "$.provides.links: a widget in the column shows where the plugin is linked,"
            " so the plugin offers a link",
        )
        manifest["provides"]["links"] = {"project": {"title": "Project", "to": ["topic"]}}
        manifest["provides"]["links"]["project"]["pattern"] = "^[unclosed"
        with self.assertRaisesRegex(
            ArmadaPluginContractError,
            r"^\$\.provides\.links\.project\.pattern: not a regular expression: ",
        ):
            canonicalize_plugin_manifest(manifest)

    def test_a_widget_declares_its_team_default_and_personal_options(self) -> None:
        manifest = self.manifest("gpus")
        widget = manifest["provides"]["widgets"][0]
        self.assertEqual([option["key"] for option in widget["options"]["facets"]], ["gpu", "user"])
        manifest = self.manifest()
        manifest["provides"]["widgets"][1]["team_default"] = "offered"
        self.refuses_manifest(
            manifest,
            "$.provides.widgets[1].team_default: only a widget in the nav has a team default",
        )
        manifest = self.manifest()
        manifest["provides"]["widgets"][0]["team_default"] = "off"
        self.refuses_manifest(
            manifest,
            '$.provides.widgets[0].team_default: expected one of ["shown","offered"], got "off"',
        )
        manifest = self.manifest("gpus")
        options = manifest["provides"]["widgets"][0]["options"]
        options["facets"].append({"key": "gpu", "title": "Card"})
        self.refuses_manifest(
            manifest, "$.provides.widgets[0].options.facets: two options share a key"
        )
        manifest = self.manifest("gpus")
        alert = manifest["provides"]["widgets"][0]["options"]["alerts"][0]
        alert["min"] = 64
        self.refuses_manifest(
            manifest, "$.provides.widgets[0].options.alerts[0]: its min is not below its max"
        )
        alert["op"] = ">"
        with self.assertRaisesRegex(
            ArmadaPluginContractError,
            r"^\$\.provides\.widgets\[0\]\.options\.alerts\[0\]\.op: expected one of",
        ):
            canonicalize_plugin_manifest(manifest)

    def test_a_row_carries_at_most_four_facets_of_plain_values(self) -> None:
        canonicalize_widget_document(
            self.listed(self.row(facets={"gpu": "H100", "count": 8, "ours": True}))
        )
        self.refuses_widget(
            self.listed(self.row(facets=dict.fromkeys(("a", "b", "c", "d", "e"), "x"))),
            "$.blocks[0].items[0].facets: object exceeds 4 properties",
        )
        self.refuses_widget(
            self.listed(self.row(facets={"gpu": {"name": "H100"}})),
            "$.blocks[0].items[0].facets.gpu: value does not match any allowed contract variant",
        )
        self.refuses_widget(
            self.listed(self.row(facets={"GPU": "H100"})),
            "$.blocks[0].items[0].facets.GPU: value \"GPU\" does not match '^[a-z][a-z0-9_]{0,31}$'",
        )

    def test_a_secret_never_takes_a_name_armada_sets(self) -> None:
        for env in ("ARMADA_API_KEY", "PATH", "HOME"):
            manifest = self.manifest()
            manifest["secrets"]["api_key"]["env"] = env
            self.refuses_manifest(
                manifest, f"$.secrets.api_key.env: Armada sets {env} for every plugin"
            )
        manifest = self.manifest("gpus")
        manifest["secrets"]["ssh_key"]["env"] = "PRIME_API_KEY"
        self.refuses_manifest(manifest, "$.secrets.ssh_key.env: PRIME_API_KEY names two secrets")
        manifest["secrets"]["Bad-Name"] = manifest["secrets"].pop("ssh_key")
        with self.assertRaisesRegex(ArmadaPluginContractError, r"^\$\.secrets\.Bad-Name: value "):
            canonicalize_plugin_manifest(manifest)

    def test_settings_are_a_form_armada_can_draw(self) -> None:
        manifest = self.manifest()
        manifest["settings"]["required"] = ["entity", "project"]
        self.refuses_manifest(
            manifest, "$.settings.required: 'project' is not one of its properties"
        )
        manifest = self.manifest()
        manifest["settings"]["properties"]["entity"] = {"title": "Entity"}
        self.refuses_manifest(
            manifest, "$.settings.properties.entity: a setting gives a type or an enum"
        )
        manifest = self.manifest()
        manifest["settings"]["properties"]["posts"]["default"] = "always"
        self.refuses_manifest(
            manifest,
            '$.settings.properties.posts.default: expected one of ["both","crashes","never"],'
            ' got "always"',
        )
        manifest = self.manifest("gpus")
        del manifest["settings"]["properties"]["ssh_hosts"]["items"]
        error = (
            "$.settings.properties.ssh_hosts: an array setting gives its items,"
            " and only an array setting"
        )
        self.refuses_manifest(manifest, error)
        manifest = self.manifest("gpus")
        manifest["settings"]["properties"]["manage_prefix"]["items"] = {"type": "string"}
        self.refuses_manifest(manifest, error.replace("ssh_hosts", "manage_prefix"))
        manifest = self.manifest("gpus")
        manifest["settings"]["properties"]["ssh_hosts"]["items"]["pattern"] = "("
        with self.assertRaisesRegex(
            ArmadaPluginContractError,
            r"^\$\.settings\.properties\.ssh_hosts\.pattern: not a regular expression: ",
        ):
            canonicalize_plugin_manifest(manifest)
        manifest = self.manifest("gpus")
        manifest["settings"]["properties"]["ssh_hosts"]["default"] = ["a", 3]
        self.refuses_manifest(
            manifest, "$.settings.properties.ssh_hosts.default[1]: expected type string"
        )

    def test_bound_data_fits_the_block_that_draws_it(self) -> None:
        run = self.fixture("wandb", "run.json")
        for bound, value, kind in (
            ("step", "42,100", "number"),
            ("metrics", [1, 2], "rows"),
            ("lines", [1, 2], "strings"),
        ):
            document = json.loads(json.dumps(run))
            document["data"][bound] = value
            index = next(
                index
                for index, block in enumerate(document["blocks"])
                if block.get("bind") == f"data.{bound}"
            )
            self.refuses_widget(
                document, f"$.blocks[{index}].bind: the data it binds is not {kind}"
            )
        self.refuses_widget(
            {
                "version": 1,
                "data": {"loss": [2.3, 2.2]},
                "blocks": [{"type": "metric", "label": "loss", "value": 0, "bind": "data.loss"}],
            },
            "$.blocks[0].bind: the data it binds is not scalar",
        )
        self.refuses_widget(
            {
                "version": 1,
                "data": {"loss": ["high"]},
                "blocks": [{"type": "series", "label": "loss", "values": [], "bind": "data.loss"}],
            },
            "$.blocks[0].bind: the data it binds is not numbers",
        )
        runs = self.runs()
        runs["data"]["running"] = ["3", "running"]
        self.refuses_widget(
            runs, "$.rest.figure: the nav's one figure is a string or a number, not a list"
        )
        # A bound block whose data has not come yet draws its literal value.
        runs = self.runs()
        del runs["data"]["runs"]
        canonicalize_widget_document(runs)

    def test_a_list_has_one_source_of_rows_and_distinct_keys(self) -> None:
        runs = self.runs()
        runs["blocks"][0]["items"] = []
        error = "$.blocks[0]: a list gives its rows as items or binds them, one of the two"
        self.refuses_widget(runs, error)
        del runs["blocks"][0]["items"], runs["blocks"][0]["bind"]
        self.refuses_widget(runs, error)
        self.refuses_widget(
            self.listed(self.row(), self.row()),
            "$.blocks[0].items[1].key: two rows are 'lr-3e-4-b'",
        )
        runs = self.runs()
        runs["data"]["runs"][2]["key"] = "lr-3e-4-b"
        self.refuses_widget(runs, "$.data.runs[2].key: two rows are 'lr-3e-4-b'")
        self.refuses_widget(
            self.listed(self.row(spark={"values": [1, 2], "domain": [100, 0]})),
            "$.blocks[0].items[0].spark.domain: its foot is below its top",
        )
        # No rows yet is a list too, whether given or bound.
        canonicalize_widget_document(self.listed())
        runs = self.runs()
        runs["data"]["runs"] = []
        canonicalize_widget_document(runs)

    def test_actions_ask_the_agent_call_a_tool_or_open_a_page(self) -> None:
        gpus = self.fixture("gpus", "gpus.json")
        confirm = gpus["data"]["machines"][3]["action"]
        self.assertEqual(confirm["confirm"], "End nlscale-c? It has not started yet.")
        gpus["actions"].append({"label": "Both", "prompt": "Find 8 GPUs", "tool": "gpus_free"})
        self.refuses_widget(gpus, "$.actions[1]: value does not match any allowed contract variant")
        self.refuses_widget(
            self.listed(self.row(action={"label": "Run", "tool": "rm -rf"})),
            "$.blocks[0].items[0].action: value does not match any allowed contract variant",
        )

    def test_scenes_in_a_widget_keep_the_artifacts_rules(self) -> None:
        def scene(*children: dict[str, str]) -> dict[str, Any]:
            root = {"type": "group", "layout": "row", "children": list(children)}
            return {"version": 1, "blocks": [{"type": "scene", "label": "pods", "root": root}]}

        node = {"type": "shape", "id": "a", "shape": "circle", "label": "a"}
        canonicalize_widget_document(scene(node, {**node, "id": "b"}))
        self.refuses_widget(scene(node, node), "$.blocks[0]: duplicate scene node id: a")

    def test_documents_are_bounded_finite_json(self) -> None:
        self.refuses_widget(
            self.listed(self.row(figure=float("nan"))),
            "The widget document is not finite JSON: Out of range float values are not JSON compliant: nan",
        )
        self.refuses_widget(
            {"version": 1, "blocks": [{"type": "copy", "body": "x" * 70_000}]},
            "The widget document exceeds 64000 bytes",
        )
        self.refuses_manifest(
            {**self.manifest(), "description": "x" * 40_000}, "The manifest exceeds 32000 bytes"
        )
        self.refuses_manifest({"id": "wandb"}, "$: missing required property 'name'")

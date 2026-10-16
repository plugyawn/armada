#!/usr/bin/env python3
"""The artifact and plugin contracts' generated copies, from
tools/armada/artifact_contract.json and tools/armada/plugin_contract.json.

    tools/armada/artifact_contract.py            print what differs from what --write writes
    tools/armada/artifact_contract.py --write    write every copy
    tools/armada/artifact_contract.py --check    fail if a copy differs (run by tools/lint)

The server validates an artifact and offers the agent its tool from
zerver/lib/armada_artifact_contract.py; the web app validates what it renders
from web/src/armada_artifact_contract_generated.ts.  A plugin's manifest and
widget documents are checked from zerver/lib/armada_plugin_contract.py, and
drawn from web/src/armada/api/plugin_contract_generated.ts.  Each carries its
schemas as compact JSON literals, so the server and the web app can never
disagree.

A widget document is an artifact document with what the plugin contract adds
(widget_schema below), and the glyphs and run states either contract may name
are the sprite's (tools/armada/icons_gen.py), so a contract can never name a
drawing the kit does not have.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tools.armada import icons_gen
else:
    import icons_gen

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SOURCE = "tools/armada/artifact_contract.json"
PLUGIN_SOURCE = "tools/armada/plugin_contract.json"
PYTHON = "zerver/lib/armada_artifact_contract.py"
TYPESCRIPT = "web/src/armada_artifact_contract_generated.ts"
PLUGIN_PYTHON = "zerver/lib/armada_plugin_contract.py"
PLUGIN_TYPESCRIPT = "web/src/armada/api/plugin_contract_generated.ts"
HEADER = f"Generated from {SOURCE}. Do not edit."
PLUGIN_HEADER = f"Generated from {PLUGIN_SOURCE} and {SOURCE}. Do not edit."
SPRITE_NAMES = {
    "x-armada-sprite-glyphs": list(icons_gen.G),
    "x-armada-sprite-marks": list(icons_gen.PX),
}


def quoted(schema: object) -> str:
    """The schema as a single-quoted literal, valid in Python and TypeScript."""
    text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def with_sprite_names(schema: Any) -> Any:
    """The schema with each enum the contract leaves to the sprite filled in."""
    if isinstance(schema, dict):
        return {
            key: SPRITE_NAMES[value]
            if key == "enum" and isinstance(value, str)
            else with_sprite_names(value)
            for key, value in schema.items()
        }
    if isinstance(schema, list):
        return [with_sprite_names(value) for value in schema]
    return schema


def widget_schema(artifact: dict[str, Any], extension: dict[str, Any]) -> dict[str, Any]:
    """The artifact schema with the plugin contract's additions for a widget:
    its blocks less those dropped, plus list; rest; tool and link actions beside the
    artifact's prompt actions; and data that may hold a list's rows."""
    widget = copy.deepcopy(artifact)
    widget["$id"] = extension["$id"]
    widget["title"] = extension["title"]
    defs = widget["$defs"]
    clashes = set(defs) & {*extension["$defs"], "widgetAction"}
    assert not clashes, f"the widget's $defs would replace the artifact's {sorted(clashes)}"
    blocks = defs["block"]["oneOf"]
    for name in extension["drop_blocks"]:
        del defs[name]
        blocks.remove({"$ref": f"#/$defs/{name}"})
    blocks.append({"$ref": "#/$defs/listBlock"})
    defs.update(copy.deepcopy(extension["$defs"]))
    defs["widgetAction"] = copy.deepcopy(extension["action"])
    widget["properties"].update(copy.deepcopy(extension["properties"]))
    widget["properties"]["actions"]["items"] = {"$ref": "#/$defs/widgetAction"}
    data = widget["properties"]["data"]
    # anyOf, not oneOf: an empty array is both a list's rows and scalars.
    data["additionalProperties"] = {
        "anyOf": [copy.deepcopy(extension["data_value"]), data["additionalProperties"]]
    }
    return with_sprite_names(widget)


def python_copy(contract: dict[str, Any]) -> str:
    tool = contract["tool"]
    description = "\n".join(f"        {json.dumps(line)}" for line in tool["description"])
    return f'''"""{HEADER}"""

import json
from typing import Any, Final

ARMADA_ARTIFACT_SCHEMA: Final[dict[str, Any]] = json.loads(
    {quoted(contract["schema"])}
)
ARMADA_ARTIFACT_TOOL_SPEC: Final[dict[str, Any]] = {{
    "type": "function",
    "name": {json.dumps(tool["name"])},
    "description": (
{description}
    ),
    "inputSchema": ARMADA_ARTIFACT_SCHEMA,
}}
'''


def typescript_copy(contract: dict[str, Any]) -> str:
    return f"""// {HEADER}

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment -- JSON.parse of the literal below
export const ARMADA_ARTIFACT_SCHEMA: Record<string, unknown> = JSON.parse(
    {quoted(contract["schema"])},
);
"""


def plugin_schemas(contract: dict[str, Any], plugin: dict[str, Any]) -> tuple[Any, Any]:
    return (
        with_sprite_names(plugin["manifest"]),
        widget_schema(contract["schema"], plugin["widget"]),
    )


def plugin_python_copy(contract: dict[str, Any], plugin: dict[str, Any]) -> str:
    manifest, widget = plugin_schemas(contract, plugin)
    return f'''"""{PLUGIN_HEADER}"""

import json
from typing import Any, Final

ARMADA_PLUGIN_API_VERSION: Final = {plugin["api_version"]}
ARMADA_PLUGIN_MANIFEST_SCHEMA: Final[dict[str, Any]] = json.loads(
    {quoted(manifest)}
)
ARMADA_WIDGET_SCHEMA: Final[dict[str, Any]] = json.loads(
    {quoted(widget)}
)
'''


def plugin_typescript_copy(contract: dict[str, Any], plugin: dict[str, Any]) -> str:
    manifest, widget = plugin_schemas(contract, plugin)
    return f"""// {PLUGIN_HEADER}

export const ARMADA_PLUGIN_API_VERSION = {plugin["api_version"]};

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment -- JSON.parse of the literal below
export const ARMADA_PLUGIN_MANIFEST_SCHEMA: Record<string, unknown> = JSON.parse(
    {quoted(manifest)},
);

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment -- JSON.parse of the literal below
export const ARMADA_WIDGET_SCHEMA: Record<string, unknown> = JSON.parse(
    {quoted(widget)},
);
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--write", action="store_true", help="write every copy")
    parser.add_argument(
        "--check", action="store_true", help="fail if a copy is not what --write writes"
    )
    options = parser.parse_args()

    with open(os.path.join(ROOT, SOURCE), encoding="utf-8") as f:
        contract = json.load(f)
    with open(os.path.join(ROOT, PLUGIN_SOURCE), encoding="utf-8") as f:
        plugin = json.load(f)
    status = 0
    for path, content in (
        (PYTHON, python_copy(contract)),
        (TYPESCRIPT, typescript_copy(contract)),
        (PLUGIN_PYTHON, plugin_python_copy(contract, plugin)),
        (PLUGIN_TYPESCRIPT, plugin_typescript_copy(contract, plugin)),
    ):
        full = os.path.join(ROOT, path)
        if options.write:
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
            continue
        try:
            with open(full, encoding="utf-8") as f:
                current = f.read()
        except FileNotFoundError:
            current = None
        if current != content:
            print(
                f"{path} is not what tools/armada/artifact_contract.py writes."
                f" Change {SOURCE}, {PLUGIN_SOURCE} or the sprite and run"
                " tools/armada/artifact_contract.py --write;"
                " the contracts' copies are never edited by hand."
            )
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())

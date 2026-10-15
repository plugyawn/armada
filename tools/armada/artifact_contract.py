#!/usr/bin/env python3
"""The artifact contract's generated copies, from tools/armada/artifact_contract.json.

    tools/armada/artifact_contract.py            print what differs from what --write writes
    tools/armada/artifact_contract.py --write    write both copies
    tools/armada/artifact_contract.py --check    fail if a copy differs (run by tools/lint)

The server validates an artifact and offers the agent its tool from
zerver/lib/armada_artifact_contract.py; the web app validates what it renders
from web/src/armada_artifact_contract_generated.ts.  Both carry the schema as
one compact JSON literal, so the two can never disagree.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SOURCE = "tools/armada/artifact_contract.json"
PYTHON = "zerver/lib/armada_artifact_contract.py"
TYPESCRIPT = "web/src/armada_artifact_contract_generated.ts"
HEADER = f"Generated from {SOURCE}. Do not edit."


def quoted(schema: object) -> str:
    """The schema as a single-quoted literal, valid in Python and TypeScript."""
    text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def python_copy(contract: dict[str, object]) -> str:
    tool = contract["tool"]
    assert isinstance(tool, dict)
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


def typescript_copy(contract: dict[str, object]) -> str:
    return f"""// {HEADER}

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment -- JSON.parse of the literal below
export const ARMADA_ARTIFACT_SCHEMA: Record<string, unknown> = JSON.parse(
    {quoted(contract["schema"])},
);
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--write", action="store_true", help="write both copies")
    parser.add_argument(
        "--check", action="store_true", help="fail if a copy is not what --write writes"
    )
    options = parser.parse_args()

    with open(os.path.join(ROOT, SOURCE), encoding="utf-8") as f:
        contract = json.load(f)
    status = 0
    for path, content in ((PYTHON, python_copy(contract)), (TYPESCRIPT, typescript_copy(contract))):
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
                f" Change {SOURCE} and run tools/armada/artifact_contract.py --write;"
                " the contract's copies are never edited by hand."
            )
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())

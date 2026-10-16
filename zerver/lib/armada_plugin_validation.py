"""A plugin's manifest and its widget documents, checked against the plugin
contract (tools/armada/plugin_contract.json; design/r9/plugins, sections 2
and 4).

A plugin brings data and nothing else. The schemas have no place for HTML,
CSS, a colour, a font, an image or a script, the glyphs and run states it
may name are the sprite's, and the checks here also refuse markup inside any
text, so nothing a plugin sends can reach the page as anything but words
that Armada draws in its own design.
"""

import json
import re
from collections.abc import Iterator
from typing import Any

from zerver.lib.armada_artifact import ArmadaArtifactError, validate_armada_scene_block
from zerver.lib.armada_json_schema import json_schema_error
from zerver.lib.armada_plugin_contract import (
    ARMADA_PLUGIN_API_VERSION,
    ARMADA_PLUGIN_MANIFEST_SCHEMA,
    ARMADA_WIDGET_SCHEMA,
)

ARMADA_PLUGIN_MANIFEST_FILE = "armada-plugin.json"
ARMADA_PLUGIN_MANIFEST_LIMIT = 32_000
ARMADA_WIDGET_DOCUMENT_LIMIT = 64_000
# What a widget's rest.news may say; it takes the figure's place in the nav.
ARMADA_WIDGET_NEWS = ("failed", "needs")
# Set by the plugin host in every plugin's environment, beside everything
# named ARMADA_*, so a secret may not take one of these names.
ARMADA_PLUGIN_RESERVED_ENV = frozenset({"HOME", "LANG", "PATH"})
# A tag as an HTML parser opens one: "<" then a letter, "/", "!" or "?".
# "loss < 2.3" is text; "<b>" is markup.
MARKUP = re.compile(r"<[A-Za-z/!?]")

# What each kind of bound block draws its bound value as.
BOUND_VALUE_KINDS = {
    "metric": "scalar",
    "meter": "number",
    "series": "numbers",
    "log": "strings",
    "list": "rows",
}


class ArmadaPluginContractError(ValueError):
    pass


def _finite_json(value: object, *, limit: int, what: str) -> tuple[Any, str]:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        )
    except (TypeError, ValueError) as error:
        raise ArmadaPluginContractError(f"{what} is not finite JSON: {error}") from error
    if len(encoded.encode()) > limit:
        raise ArmadaPluginContractError(f"{what} exceeds {limit} bytes")
    return json.loads(encoded), encoded


def _strings(value: object, path: str, *, skip: frozenset[str]) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{path}[{index}]", skip=skip)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in skip:
                yield from _strings(item, f"{path}.{key}", skip=skip)


def _refuse_markup(value: object, *, skip: frozenset[str]) -> None:
    for path, text in _strings(value, "$", skip=skip):
        if MARKUP.search(text) is not None:
            raise ArmadaPluginContractError(
                f"{path}: a plugin sends text, not HTML, and {text[:40]!r} holds markup"
            )


def _compile(pattern: str, path: str) -> None:
    try:
        re.compile(pattern)
    except re.error as error:
        raise ArmadaPluginContractError(f"{path}: not a regular expression: {error}") from error


def _check_settings(settings: dict[str, Any]) -> None:
    properties = settings["properties"]
    for name in settings.get("required", []):
        if name not in properties:
            raise ArmadaPluginContractError(
                f"$.settings.required: {name!r} is not one of its properties"
            )
    for name, setting in properties.items():
        path = f"$.settings.properties.{name}"
        if "type" not in setting and "enum" not in setting:
            raise ArmadaPluginContractError(f"{path}: a setting gives a type or an enum")
        if (setting.get("type") == "array") != ("items" in setting):
            raise ArmadaPluginContractError(
                f"{path}: an array setting gives its items, and only an array setting"
            )
        for pattern in (setting.get("pattern"), setting.get("items", {}).get("pattern")):
            if pattern is not None:
                _compile(pattern, f"{path}.pattern")
        if "default" in setting:
            # A setting is itself a schema of the subset json_schema_error reads.
            error = json_schema_error(setting, setting["default"])
            if error is not None:
                raise ArmadaPluginContractError(f"{path}.default{error.removeprefix('$')}")


def _check_secrets(secrets: dict[str, Any]) -> None:
    named: set[str] = set()
    for name, secret in secrets.items():
        env = secret["env"]
        path = f"$.secrets.{name}.env"
        if env in ARMADA_PLUGIN_RESERVED_ENV or env.startswith("ARMADA_"):
            raise ArmadaPluginContractError(f"{path}: Armada sets {env} for every plugin")
        if env in named:
            raise ArmadaPluginContractError(f"{path}: {env} names two secrets")
        named.add(env)


def _check_options(options: dict[str, Any], path: str) -> None:
    """The personal options a widget declares (design/r9/plugins/ROUND2.md,
    section D): each kind's keys are distinct, and an alert's range runs
    upward."""
    for kind, declared in options.items():
        keys = [option["key"] for option in declared]
        if len(set(keys)) != len(keys):
            raise ArmadaPluginContractError(f"{path}.{kind}: two options share a key")
    for index, alert in enumerate(options.get("alerts", [])):
        if "min" in alert and "max" in alert and not alert["min"] < alert["max"]:
            raise ArmadaPluginContractError(f"{path}.alerts[{index}]: its min is not below its max")


def _check_provides(provides: dict[str, Any]) -> None:
    keys: set[str] = set()
    in_column = False
    for index, widget in enumerate(provides.get("widgets", [])):
        path = f"$.provides.widgets[{index}]"
        if widget["key"] in keys:
            raise ArmadaPluginContractError(f"{path}.key: {widget['key']!r} names two widgets")
        keys.add(widget["key"])
        places = widget["in"]
        if len(set(places)) != len(places):
            raise ArmadaPluginContractError(f"{path}.in: names a place twice")
        if ("column" in places) != ("column_slots" in widget):
            raise ArmadaPluginContractError(
                f"{path}: a widget in the column gives column_slots, its rows there (1 to 3),"
                " and only such a widget"
            )
        if "team_default" in widget and "nav" not in places:
            raise ArmadaPluginContractError(
                f"{path}.team_default: only a widget in the nav has a team default"
            )
        _check_options(widget.get("options", {}), f"{path}.options")
        in_column = in_column or "column" in places
    links = provides.get("links", {})
    if in_column and not links:
        raise ArmadaPluginContractError(
            "$.provides.links: a widget in the column shows where the plugin is linked,"
            " so the plugin offers a link"
        )
    for name, link in links.items():
        if "pattern" in link:
            _compile(link["pattern"], f"$.provides.links.{name}.pattern")


def canonicalize_plugin_manifest(value: object) -> dict[str, Any]:
    """The manifest, as finite JSON with sorted keys, or ArmadaPluginContractError
    naming the first thing wrong with it."""
    manifest, _encoded = _finite_json(
        value, limit=ARMADA_PLUGIN_MANIFEST_LIMIT, what="The manifest"
    )
    error = json_schema_error(ARMADA_PLUGIN_MANIFEST_SCHEMA, manifest)
    if error is not None:
        raise ArmadaPluginContractError(error)
    # A command line and a regular expression are not drawn, and may hold "<".
    _refuse_markup(manifest, skip=frozenset({"run", "pattern"}))
    if ("run" in manifest) == ("remote" in manifest):
        raise ArmadaPluginContractError(
            "$: a plugin gives run (a program this server runs) or remote"
            " (a service its author hosts), one of the two"
        )
    if "remote" in manifest and manifest.get("secrets"):
        raise ArmadaPluginContractError(
            "$.secrets: a remote plugin cannot ask for secrets;"
            " they go only to processes on this server"
        )
    needed = int(manifest["requires"]["armada"].removeprefix(">="))
    if needed > ARMADA_PLUGIN_API_VERSION:
        raise ArmadaPluginContractError(
            f"$.requires.armada: the plugin needs version {needed} of Armada's plugin API,"
            f" and this server has version {ARMADA_PLUGIN_API_VERSION}"
        )
    if "settings" in manifest:
        _check_settings(manifest["settings"])
    _check_secrets(manifest.get("secrets", {}))
    _check_provides(manifest["provides"])
    return manifest


def _check_rows(rows: list[dict[str, Any]], path: str) -> None:
    keys: set[str] = set()
    for index, row in enumerate(rows):
        if row["key"] in keys:
            raise ArmadaPluginContractError(f"{path}[{index}].key: two rows are {row['key']!r}")
        keys.add(row["key"])
        domain = row.get("spark", {}).get("domain")
        if domain is not None and not domain[0] < domain[1]:
            raise ArmadaPluginContractError(
                f"{path}[{index}].spark.domain: its foot is below its top"
            )


def _check_bound_value(kind: str, value: object, path: str) -> None:
    if kind == "scalar":
        fits = not isinstance(value, list)
    elif kind == "number":
        fits = isinstance(value, int | float) and not isinstance(value, bool)
    elif kind == "numbers":
        fits = isinstance(value, list) and all(
            isinstance(item, int | float) and not isinstance(item, bool) for item in value
        )
    elif kind == "strings":
        fits = isinstance(value, list) and all(isinstance(item, str) for item in value)
    else:
        fits = isinstance(value, list) and all(isinstance(item, dict) for item in value)
    if not fits:
        raise ArmadaPluginContractError(f"{path}: the data it binds is not {kind}")


def canonicalize_widget_document(value: object) -> tuple[dict[str, Any], str]:
    """The widget document and its canonical encoding, or ArmadaPluginContractError
    naming the first thing wrong with it. The data is checked against the
    blocks that bind it, so a patch of data alone is checked by passing the
    document it lands in."""
    document, encoded = _finite_json(
        value, limit=ARMADA_WIDGET_DOCUMENT_LIMIT, what="The widget document"
    )
    error = json_schema_error(ARMADA_WIDGET_SCHEMA, document)
    if error is not None:
        raise ArmadaPluginContractError(error)
    # A tool's arguments go to the plugin and are never drawn.
    _refuse_markup(document, skip=frozenset({"arguments"}))
    data = document.get("data", {})
    for index, block in enumerate(document["blocks"]):
        path = f"$.blocks[{index}]"
        if block["type"] == "scene":
            try:
                validate_armada_scene_block(block)
            except ArmadaArtifactError as error:
                raise ArmadaPluginContractError(f"{path}: {error}") from error
            continue
        if block["type"] == "list" and ("items" in block) == ("bind" in block):
            raise ArmadaPluginContractError(
                f"{path}: a list gives its rows as items or binds them, one of the two"
            )
        kind = BOUND_VALUE_KINDS.get(block["type"])
        bound = block.get("bind", "").removeprefix("data.")
        if kind is not None and bound in data:
            _check_bound_value(kind, data[bound], f"{path}.bind")
        if block["type"] == "list":
            rows = block.get("items", data.get(bound, []))
            _check_rows(rows, f"{path}.items" if "items" in block else f"$.data.{bound}")
    rest = document.get("rest")
    if rest is not None:
        figure = data.get(rest["figure"].removeprefix("data."))
        if isinstance(figure, list):
            raise ArmadaPluginContractError(
                "$.rest.figure: the nav's one figure is a string or a number, not a list"
            )
        if "news" in rest:
            news = data.get(rest["news"].removeprefix("data."))
            if news not in (None, "", *ARMADA_WIDGET_NEWS):
                raise ArmadaPluginContractError(
                    f"$.rest.news: {news!r} is not failed, needs or empty"
                )
    return document, encoded

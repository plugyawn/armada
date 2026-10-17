"""The check of a plugin being made (design/r9/plugins/ROUND2.md, section
C, step 4): what the agent runs on the plugin in its worktree before anyone
sees it, and runs again until nothing is wrong.

check_plugin:

1. reads the manifest in the plugin's directory and checks it against the
   contract. A wrong manifest ends the check, with the problem's place and
   plain words.
2. reserves the plugin's id, the first time, as a draft of the person the
   agent acts for, and refreshes the draft after that. The draft holds the
   settings and secrets the plugin is tried with, and only its maker sees
   it. A plugin that is already installed is checked as its next version,
   with the install's settings and secrets, and nothing of the install
   changes.
3. tries the program (zerver/lib/armada_plugin_trial.py), which shows what
   it pushes, posts and offers, to no one.
4. draws its widgets in the nav as the beta draws them, at rest and
   pressed, in light and dark (zerver/lib/armada_plugin_preview.py), into
   the worktree's .armada/previews/. What the page measured becomes
   problems: a figure wider than its cell, or a label that is cut.

It returns JSON for the agent. The agent's tool armada_plugin_check (PL12)
is this function, called for the person the run acts for, with the run's
worktree.
"""

import json
import subprocess
from pathlib import Path
from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.utils.timezone import now as timezone_now

from version import API_FEATURE_LEVEL, ZULIP_VERSION
from zerver.lib.armada_plugin_preview import (
    ArmadaPreviewError,
    Drawn,
    PreviewSite,
    draw_widget_previews,
)
from zerver.lib.armada_plugin_repository import ARMADA_PLUGIN_TEAM_DIRECTORY, team_plugin_repository
from zerver.lib.armada_plugin_trial import Problem, TrialResult, contract_problem, run_plugin_trial
from zerver.lib.armada_plugin_validation import (
    ARMADA_PLUGIN_MANIFEST_FILE,
    ArmadaPluginContractError,
    canonicalize_plugin_manifest,
    canonicalize_plugin_settings,
)
from zerver.lib.armada_plugins import (
    PluginOrigin,
    armada_plugin_secret_environment,
    check_plugin_member,
    install_plugin,
)
from zerver.lib.exceptions import JsonableError
from zerver.lib.timestamp import datetime_to_timestamp
from zerver.models import PluginInstall, PluginSecret, UserProfile
from zerver.views.armada_beta import armada_beta_page

# Where the pictures go in the worktree; its .git/info/exclude keeps
# .armada/ out of its changes.
ARMADA_PLUGIN_PREVIEW_DIRECTORY = ".armada/previews"


def _read_manifest(root: Path, where: str) -> tuple[dict[str, Any] | None, Problem | None]:
    try:
        text = (root / ARMADA_PLUGIN_MANIFEST_FILE).read_text()
    except OSError:
        return None, Problem(where, f"There is no {ARMADA_PLUGIN_MANIFEST_FILE} here.")
    try:
        value = json.loads(text)
    except ValueError as error:
        return None, Problem(where, f"It is not JSON: {error}.")
    try:
        return canonicalize_plugin_manifest(value), None
    except ArmadaPluginContractError as error:
        return None, contract_problem(where, str(error))


def _head(workdir: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workdir), "rev-parse", "--verify", "HEAD^{commit}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _reserve(
    user: UserProfile,
    manifest: dict[str, Any],
    *,
    workdir: Path,
    path: str,
    origin: PluginOrigin | None,
    notes: list[str],
) -> tuple[PluginInstall | None, Problem | None]:
    """The install the plugin is tried as: its install when it has one,
    else its draft, made or refreshed for this person."""
    existing = PluginInstall.objects.filter(realm=user.realm, plugin_id=manifest["id"]).first()
    if existing is not None and existing.state not in (PluginInstall.DRAFT, PluginInstall.REMOVED):
        return existing, None
    if existing is not None and existing.state == PluginInstall.REMOVED:
        assert existing.date_removed is not None
        return None, Problem(
            "$.id",
            f"{existing.name} was removed on {existing.date_removed:%d %b} and keeps its id for"
            " a week: undo the removal to change it, or give this plugin another id.",
        )
    if existing is not None and user.id not in (existing.made_by_id, existing.installed_by_id):
        maker = existing.made_by or existing.installed_by
        return None, Problem(
            "$.id",
            f"{existing.name} is being made by {maker.full_name if maker else 'someone else'},"
            " and holds its id: give this plugin another id.",
        )
    repository = team_plugin_repository(user.realm)
    sha = _head(workdir)
    if repository is None or not sha:
        notes.append(
            "No draft is kept: it needs the organization's plugins repository and a commit in"
            " the worktree. The plugin is tried without settings or secrets."
        )
        return None, None
    try:
        draft = install_plugin(
            user,
            manifest=manifest,
            source_kind=PluginInstall.LOCAL,
            repository_id=repository.id,
            path=path,
            sha=sha,
            plugin_settings=None if existing is None else _chosen_settings(existing),
            draft=True,
            origin=origin,
        )
    except JsonableError as error:
        return None, Problem("the draft", error.msg)
    return draft, None


def _chosen_settings(draft: PluginInstall) -> dict[str, Any]:
    """The draft's settings that someone chose. A draft keeps each default
    its manifest had as a value, and the plugin's next version may change
    that default: the trial then has the new one."""
    form = draft.manifest.get("settings", {}).get("properties", {})
    return {
        name: value
        for name, value in draft.settings.items()
        if "default" not in form.get(name, {}) or form[name]["default"] != value
    }


def _settings(
    install: PluginInstall | None, manifest: dict[str, Any]
) -> tuple[dict[str, Any], list[str], Problem | None]:
    """The settings it is tried with, and what it waits for: the required
    settings and secrets no one has set yet."""
    form = manifest.get("settings", {}).get("properties", {})
    given = {} if install is None else {k: v for k, v in install.settings.items() if k in form}
    problem = None
    try:
        values, missing = canonicalize_plugin_settings(manifest, given)
    except ArmadaPluginContractError as error:
        values, missing = canonicalize_plugin_settings(manifest, {})
        problem = contract_problem("its settings", str(error))
    stored = (
        set()
        if install is None
        else set(PluginSecret.objects.filter(install=install).values_list("name", flat=True))
    )
    secrets = manifest.get("secrets", {})
    missing += [
        name for name, secret in secrets.items() if secret.get("required") and name not in stored
    ]
    return values, missing, problem


def preview_browser() -> str | None:
    """The Chromium that draws previews: the one named for it, else the
    agent's (the newest Playwright installed in ARMADA_WORKER_BROWSER_DIR)."""
    if settings.ARMADA_PLUGIN_PREVIEW_BROWSER:
        return settings.ARMADA_PLUGIN_PREVIEW_BROWSER
    if not settings.ARMADA_WORKER_BROWSER_DIR:
        return None
    found = sorted(
        Path(settings.ARMADA_WORKER_BROWSER_DIR, "browsers").glob(
            "chromium-*/chrome-linux*/chrome"
        ),
        key=lambda chrome: int(chrome.parent.parent.name.removeprefix("chromium-") or 0),
    )
    return str(found[-1]) if found else None


def _beta_page(user: UserProfile) -> str:
    """The beta's page as the server renders it for this person."""
    request = HttpRequest()
    request.method = "GET"
    request.path = "/"
    request.META = {
        "HTTP_HOST": user.realm.host,
        "SERVER_NAME": user.realm.host,
        "SERVER_PORT": "443",
    }
    request.user = user
    return armada_beta_page(request, user).content.decode()


def preview_site(
    user: UserProfile,
    *,
    install_id: int,
    manifest: dict[str, Any],
    documents: dict[tuple[str, str], dict[str, Any]],
) -> PreviewSite:
    """The beta's page, for a browser that sees nothing but the plugin on
    trial: register's snapshot holds it alone, running, with every widget
    shown in the nav, and the documents it pushed."""
    now = datetime_to_timestamp(timezone_now())
    widgets = manifest["provides"].get("widgets", [])
    plugin = {
        "id": install_id,
        "plugin": manifest["id"],
        "name": manifest["name"],
        "glyph": manifest["glyph"],
        "version": manifest["version"],
        "state": "running",
        "disabled": False,
        "bot_user_id": None,
        "widgets": widgets,
        "widget_defaults": {
            widget["key"]: {"nav": "shown" if "nav" in widget["in"] else "off", "column": False}
            for widget in widgets
        },
        "links": [],
        "made_by": user.id,
        "made_in_stream_id": None,
        "made_in_topic": None,
        "made_in_message_id": None,
    }
    register = {
        "result": "success",
        "msg": "",
        "queue_id": "preview",
        "last_event_id": -1,
        "server_timestamp": now,
        "zulip_version": ZULIP_VERSION,
        "zulip_feature_level": API_FEATURE_LEVEL,
        "user_id": user.id,
        "full_name": user.full_name,
        "is_guest": False,
        "realm_users": [
            {"user_id": user.id, "full_name": user.full_name, "email": user.email, "is_bot": False}
        ],
        "cross_realm_bots": [],
        "presences": {},
        "muted_users": [],
        "realm_user_groups": [],
        "subscriptions": [],
        "repository_stream_ids": [],
        "recent_private_conversations": [],
        "unread_msgs": {"streams": [], "pms": [], "huddles": [], "mentions": []},
        "scheduled_messages": [],
        "reminders": [],
        "starred_messages": [],
        "armada_plugins": [plugin],
        "armada_widgets": [
            {
                "install_id": install_id,
                "widget": widget,
                "link_id": None,
                "item": item,
                "stream_id": None,
                "topic": None,
                "document": document,
                "version": 1,
                "fed_at": now,
                "stale": False,
            }
            for (widget, item), document in sorted(documents.items())
        ],
    }
    static_url = settings.STATIC_URL
    assert static_url is not None
    files: tuple[tuple[str, str], ...] = ()
    network: tuple[str, ...] = ()
    if settings.DEBUG:
        # A development server keeps its bundles in memory: they are
        # fetched from it.
        network = (static_url, static_url.removesuffix("static/") + "webpack/")
    elif settings.STATIC_ROOT:
        files = ((static_url, settings.STATIC_ROOT),)
    return PreviewSite(
        page_url=f"{user.realm.url}/",
        page_html=_beta_page(user),
        answers={
            "register": register,
            # Home asks for the runs that are live; there are none.
            "armada/runs": {"result": "success", "msg": "", "armada_runs": []},
        },
        files=files,
        network=network,
    )


def _drawn_problems(drawn: Drawn) -> list[Problem]:
    """What the page measured, as problems to fix."""
    problems = []
    for row in (drawn.rows or {}).get("rows", []):
        key = row["id"].split("/", 1)[1]
        if row["figure_cut"]:
            problems.append(
                Problem(
                    f"widget {key} at rest",
                    f"“{row['figure']}” is wider than its cell, which is as wide as its"
                    " rest.sizer: make the sizer as wide as the widest figure it shows.",
                )
            )
        if row["label_cut"]:
            problems.append(
                Problem(
                    f"widget {key} at rest",
                    f"The nav cuts its label, “{row['label']}”: give it a shorter label.",
                )
            )
    for widget, sheet in drawn.sheets.items():
        key = widget.split("/", 1)[1]
        for row in sheet["rows"]:
            if row["figure_cut"]:
                problems.append(
                    Problem(
                        f"widget {key} pressed, row {row['key']}",
                        f"Its figure, “{row['figure']}”, is cut: keep a row's figure short.",
                    )
                )
            if row["label_cut"]:
                problems.append(
                    Problem(
                        f"widget {key} pressed, row {row['key']}",
                        f"Its label, “{row['label']}”, is cut: keep it short, and say the rest"
                        " in its second line, sub.",
                    )
                )
    return problems


def check_plugin(
    user: UserProfile,
    *,
    workdir: Path,
    path: str,
    origin: PluginOrigin | None = None,
) -> dict[str, Any]:
    """Check the plugin in path, a directory of the worktree workdir, for
    the person the agent acts for. Returns {ok, plugin, install, problems:
    [{where, what}], waiting_for, widgets, tools, posts, asked, lines,
    previews: [{widget, state, theme, path}], notes, seconds}."""
    check_plugin_member(user)
    path = path.strip().strip("/")
    root = (workdir / path).resolve()
    where = f"{path}/{ARMADA_PLUGIN_MANIFEST_FILE}"
    result: dict[str, Any] = {
        "ok": False,
        "plugin": None,
        "install": None,
        "problems": [],
        "waiting_for": [],
        "widgets": [],
        "tools": None,
        "posts": [],
        "asked": [],
        "lines": [],
        "previews": [],
        "notes": [],
        "seconds": 0.0,
    }
    problems: list[Problem] = []
    notes: list[str] = result["notes"]
    if not root.is_relative_to(workdir.resolve()) or not root.is_dir():
        problems.append(Problem(path or ".", "That is not a directory of the worktree."))
        result["problems"] = [problem.as_dict() for problem in problems]
        return result
    manifest, problem = _read_manifest(root, where)
    if manifest is None:
        assert problem is not None
        result["problems"] = [problem.as_dict()]
        return result
    result["plugin"] = manifest["id"]
    if path != f"{ARMADA_PLUGIN_TEAM_DIRECTORY}/{manifest['id']}":
        problems.append(
            Problem(
                path,
                f"A plugin's code is in {ARMADA_PLUGIN_TEAM_DIRECTORY}/{manifest['id']}/,"
                " named after its id: move it there.",
            )
        )
    if "remote" in manifest:
        notes.append("A remote plugin runs where its author hosts it: it is not tried here.")
        result["problems"] = [problem.as_dict() for problem in problems]
        result["ok"] = not problems
        return result
    install, problem = _reserve(
        user, manifest, workdir=workdir, path=path, origin=origin, notes=notes
    )
    if problem is not None:
        problems.append(problem)
    if install is not None:
        result["install"] = {
            "id": install.id,
            "state": PluginInstall.STATE_NAMES[install.state],
            "version": install.version,
        }
    values, result["waiting_for"], problem = _settings(install, manifest)
    if problem is not None:
        problems.append(problem)
    trial: TrialResult = run_plugin_trial(
        root,
        manifest,
        plugin_settings=values,
        secret_environment=(
            {} if install is None else armada_plugin_secret_environment(install, manifest)
        ),
        email=install.bot.email if install is not None else f"{manifest['id']}-trial@localhost",
        plugin_dir=Path(settings.ARMADA_PLUGIN_DIR),
    )
    problems += trial.problems
    result.update(
        tools=trial.tools,
        posts=trial.posts,
        asked=trial.asked,
        lines=trial.lines,
        seconds=trial.seconds,
        widgets=[
            {
                "key": widget["key"],
                "label": widget["label"],
                "pushed": (widget["key"], "") in trial.documents,
            }
            for widget in manifest["provides"].get("widgets", [])
        ],
    )
    install_id = install.id if install is not None else 0
    shown = [
        f"{install_id}/{widget['key']}"
        for widget in manifest["provides"].get("widgets", [])
        if "nav" in widget["in"] and (widget["key"], "") in trial.documents
    ]
    browser = preview_browser()
    if shown and browser is None:
        notes.append(
            "No preview: this server has no browser to draw one (ARMADA_WORKER_BROWSER_DIR)."
        )
    elif shown and browser is not None:
        try:
            drawn = draw_widget_previews(
                preview_site(
                    user, install_id=install_id, manifest=manifest, documents=trial.documents
                ),
                browser=browser,
                widgets=shown,
                out_dir=workdir / ARMADA_PLUGIN_PREVIEW_DIRECTORY,
                name=manifest["id"],
            )
        except ArmadaPreviewError as error:
            notes.append(f"No preview: {error}")
        else:
            problems += _drawn_problems(drawn)
            notes += drawn.notes
            result["previews"] = [picture.as_dict() for picture in drawn.pictures]
    result["problems"] = [problem.as_dict() for problem in problems]
    result["ok"] = not problems
    return result

"""The scenarios `manage.py pear_demo_data` loads into channel `click`.

One function per topic.  Each reads as the conversation it produces:
`seeder.post` is a person talking, `seeder.request` is a person asking
Pear, and the script handed to `request` is what the stand-in agent does
during that turn (see `ScriptedTurn` in `pear_demo.py`).

To add a state: write a `seed_*` function and list it in `SCENARIOS`.
Topic names must start with `demo-` -- that prefix is how a load finds
and removes what an earlier load created.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zerver.models.bots import PearMonitor, PearWorkspaceNode
from zerver.views.pear import update_pear_run_control_event
from zilencer.lib.pear_demo import DemoSeeder, ScriptedTurn, png

COMPLETED = "demo-completed-run"
LIVE = "demo-live-run"
QUEUED = "demo-queued-run"
FAILED = "demo-failed-run"
CANCELLED = "demo-cancelled-run"
NEEDS_INPUT = "demo-needs-input"
PAUSED = "demo-paused-topic"
ARTIFACTS = "demo-artifacts"
WALL = "demo-wall-monitors"
THREADS = "demo-threads"
DIRTY = "demo-dirty-worktree"
LONG = "demo-long-topic"

Script = Callable[[ScriptedTurn], str]


# --- 1. a completed run ----------------------------------------------------------

COMPLETED_ANSWER = """\
Fixed. Zsh was dropping the description of every nested group because \
`ZshComplete.format_completion` emitted the `plain` form whenever the item \
came from a `Group` resolved through `Context.parent`.

**What was wrong**

`_resolve_context` builds the child context with `resilient_parsing=True`, \
and `Group.shell_complete` then read `command.short_help` instead of calling \
`command.get_short_help_str()`. For a nested group that attribute is `None`, \
so the item reached the formatter without help text:

```python
def format_completion(self, item: CompletionItem[str]) -> str:
    help_ = item.help or "_"
    # `_describe` splits value and help at the first unescaped colon.
    value = item.value.replace(":", r"\\:")
    return f"{item.type}\\n{value}\\n{help_}"
```

**What changed**

1. `Group.shell_complete` now asks each subcommand for \
`get_short_help_str(limit=45)`, the same call `format_commands` uses for `--help`.
2. `ZshComplete.format_completion` keeps the `_` placeholder only when the \
help text is genuinely empty.
3. Added `test_zsh_nested_group_descriptions`, which completes `cli sub <TAB>` \
two levels deep and checks all three descriptions.

**Verification**

- `pytest tests/test_shell_completion.py -q`: 61 passed
- `ruff check src tests`: clean
- Bash and Fish output is byte-identical to `main` for the same fixture.

Committed as `Carry group descriptions through nested completion` on \
`demo-completed-run`; nothing is pushed."""


def completed_fix(turn: ScriptedTurn) -> str:
    steps = [
        "Reproduce the missing descriptions",
        "Trace how nested groups build completion items",
        "Fix the short-help lookup",
        "Add a regression test and run the suite",
    ]

    def plan(done: int) -> list[tuple[str, str]]:
        return [
            (step, "completed" if index < done else "in_progress" if index == done else "pending")
            for index, step in enumerate(steps)
        ]

    turn.plan(plan(0))
    turn.command("rg -n 'format_completion|short_help' src/click/shell_completion.py", seconds=2)
    turn.command("sed -n '440,480p' src/click/shell_completion.py", seconds=2)
    turn.command("pytest tests/test_shell_completion.py -k zsh -q", seconds=21)
    turn.say(
        "The existing Zsh tests pass, but none of them completes a group nested "
        "inside another group. Reproducing with a two-level fixture."
    )
    turn.command(
        "python -c \"from examples.completion import cli; print(cli.commands['group'])\"",
        seconds=2,
        failed=True,
    )
    turn.command("_CLI_COMPLETE=zsh_complete COMP_WORDS='cli sub ' COMP_CWORD=2 python repro.py")
    turn.plan(plan(1))
    turn.command("rg -n 'def shell_complete' -A 30 src/click/core.py", seconds=1)
    turn.tool("workspace.read_file src/click/core.py")
    turn.usage(input=48_200, cached=31_400, output=2_150, reasoning=1_480)
    turn.plan(plan(2))
    turn.wait(24)
    turn.file_change("src/click/core.py", seconds=4)
    turn.file_change("src/click/shell_completion.py", seconds=3)
    turn.plan(plan(3))
    turn.file_change("tests/test_shell_completion.py")
    turn.wait(19)
    turn.command("pytest tests/test_shell_completion.py -q", seconds=38)
    turn.command("ruff check src tests", seconds=6)
    turn.command("git commit -am 'Carry group descriptions through nested completion'", seconds=1)
    turn.plan(plan(4))
    turn.usage(input=96_840, cached=71_260, output=5_930, reasoning=3_210)
    return COMPLETED_ANSWER


def quick(answer: str, *commands: str) -> Script:
    """A short turn: a few commands, then the answer."""

    def script(turn: ScriptedTurn) -> str:
        for command in commands:
            turn.command(command, seconds=2)
        turn.usage(
            input=9_000 + 4_000 * len(commands),
            cached=6_500 + 2_500 * len(commands),
            output=260 + len(answer) // 3,
            reasoning=120,
        )
        return answer

    return script


def seed_completed_run(seeder: DemoSeeder) -> None:
    seeder.minutes_ago(190)
    seeder.post(
        "desdemona",
        COMPLETED,
        "Zsh completion drops descriptions for nested groups. `cli <TAB>` shows them, "
        "`cli sub <TAB>` only shows bare names. Repro is in `tests/test_shell_completion.py`.",
    )
    seeder.post("hamlet", COMPLETED, "Same here on zsh 5.9. Bash and fish look fine for me.")
    seeder.request(
        "desdemona",
        COMPLETED,
        "find out why nested groups lose their descriptions in Zsh and fix it, with a test.",
        completed_fix,
    )
    node = seeder.workspace(COMPLETED)
    apply_completion_fix(node)
    seeder.git(
        node, "commit", "--quiet", "-am", "Carry group descriptions through nested completion"
    )
    seeder.refresh_workspace(node)
    seeder.post(
        "iago", COMPLETED, "Nice. Confirmed on my machine, descriptions are back two levels deep."
    )
    seeder.request(
        "hamlet",
        COMPLETED,
        "does this change what Bash users see?",
        quick(
            "No. Bash's completion protocol has no description channel, so "
            "`BashComplete.format_completion` still emits `type,value` and ignores `help`. "
            "I diffed the Bash and Fish output against `main` for the nested fixture: identical.",
            "git diff main -- src/click/shell_completion.py",
            "pytest tests/test_shell_completion.py -k 'bash or fish' -q",
        ),
    )


# --- worktree edits shared by the scenarios ------------------------------------------

NESTED_TEST = """

def test_zsh_nested_group_descriptions(runner):
    @click.group()
    def cli():
        pass

    @cli.group(help="Manage remotes.")
    def remote():
        pass

    @remote.command(help="Add a remote.")
    def add():
        pass

    assert _get_words(cli, ["remote"], "") == ["add"]
"""


def apply_completion_fix(node: PearWorkspaceNode) -> None:
    """The edits the completed and dirty scenarios talk about, made for real."""
    root = Path(node.workdir)
    source = root / "src/click/shell_completion.py"
    source.write_text(
        source.read_text().replace(
            '        help_ = item.help or "_"\n',
            "        # Keep the placeholder only when there is genuinely no help text.\n"
            '        help_ = item.help.strip() if item.help else "_"\n',
            1,
        )
    )
    tests = root / "tests/test_shell_completion.py"
    tests.write_text(tests.read_text() + NESTED_TEST)


def apply_uncommitted_work(seeder: DemoSeeder, node: PearWorkspaceNode) -> None:
    root = Path(node.workdir)
    changes = root / "CHANGES.md"
    changes.write_text(
        changes.read_text().replace(
            "Unreleased\n\n",
            "Unreleased\n\n- Zsh completion shows descriptions for groups nested inside\n"
            "  other groups. {pr}`3891`\n",
            1,
        )
    )
    (root / "docs/nested-completion.md").write_text(
        "# Completing nested groups\n\n"
        "Every group in the chain contributes its own short help to the\n"
        "completion items, so `cli remote <TAB>` reads like `cli <TAB>`.\n"
    )
    (root / "examples/README").unlink()
    seeder.git(node, "mv", "docs/shell-completion.md", "docs/completion.md")


# --- 2. a run that is live right now ----------------------------------------------------

LIVE_PLAN = [
    "Map every place option help is rendered",
    "Design the `OptionGroup` API",
    "Implement grouped help output",
    "Port the existing formatter tests",
    "Document option groups",
]

LIVE_PARTIAL = """\
Sections are rendering. `Command.format_options` now asks each parameter for its \
group and writes one definition list per group, in declaration order, with ungrouped \
options kept under the existing `Options:` heading so current output does not move.

Two things are still open before I call this done:

- `HelpFormatter.write_dl` measures the first column per call, so each section gets \
its own column width. I am threading a shared `col_max` through"""


def live_script(turn: ScriptedTurn) -> str:
    def plan(done: int) -> list[tuple[str, str]]:
        return [
            (step, "completed" if index < done else "in_progress" if index == done else "pending")
            for index, step in enumerate(LIVE_PLAN)
        ]

    turn.plan(plan(0))
    turn.command("rg -n 'format_options|write_dl|get_help_record' src/click", seconds=2)
    noether = turn.spawn("Noether", "explorer", "Catalogue every caller of HelpFormatter.write_dl")
    hopper = turn.spawn(
        "Hopper", "explorer", "Check how cloup and click-option-group model sections"
    )
    turn.command("rg -n 'write_dl' src tests", seconds=1, agent=noether)
    turn.tool("web.search click option groups prior art", seconds=4, failed=True, agent=hopper)
    turn.finish_agent(
        hopper,
        failed=True,
        error="Network access is disabled in this sandbox; cannot reach pypi.org.",
    )
    turn.command("sed -n '225,300p' src/click/formatting.py", seconds=1, agent=noether)
    turn.say(
        "`write_dl` has four callers: options, commands, and two in `examples/`. "
        "Only `Command.format_options` needs to change.",
        agent=noether,
    )
    turn.finish_agent(noether)
    turn.plan(plan(1))
    turn.say(
        "Help rendering funnels through `Command.format_options`; nothing else needs to "
        "know about groups. Going with a thin `OptionGroup` that parameters point at."
    )
    turn.usage(input=61_300, cached=40_100, output=2_900, reasoning=1_900)
    turn.plan(plan(2))
    lovelace = turn.spawn("Lovelace", "worker", "Port tests/test_formatting.py to grouped output")
    turing = turn.spawn("Turing", "reviewer", "Review the OptionGroup API for 9.0 compatibility")
    turn.file_change("src/click/core.py")
    turn.file_change("src/click/decorators.py")
    turn.command("sed -n '1,80p' tests/test_formatting.py", seconds=1, agent=lovelace)
    turn.say(
        "The API holds up for 9.0: `OptionGroup` is additive and `Option.group` defaults "
        "to `None`. One nit: `group=` should accept a plain string as shorthand.",
        agent=turing,
    )
    turn.finish_agent(turing)
    turn.steer("iago", "keep ungrouped options under the plain `Options:` heading, please.")
    turn.command("pytest tests/test_formatting.py -q", seconds=11, failed=True)
    turn.file_change("src/click/formatting.py")
    turn.file_change("tests/test_formatting.py", agent=lovelace)
    turn.command("pytest tests/test_formatting.py -k wrap -q", finish=False, agent=lovelace)
    turn.overview("Aligning columns across sections", step=LIVE_PLAN[2])
    turn.usage(input=148_900, cached=112_300, output=7_420, reasoning=4_050)
    turn.stream(LIVE_PARTIAL)
    turn.command("pytest tests/test_formatting.py tests/test_options.py -q", finish=False)
    turn.freeze()
    return ""


def seed_live_run(seeder: DemoSeeder) -> None:
    seeder.minutes_ago(34)
    seeder.post(
        "iago",
        LIVE,
        "For 8.6 I would like `--help` to group related options under headings, "
        "the way `git` and `cargo` do. Third-party packages do this today by "
        "subclassing `Command`.",
    )
    seeder.request(
        "desdemona",
        LIVE,
        "where is option help rendered today? One paragraph.",
        quick(
            "In one place: `Command.format_options` collects `param.get_help_record(ctx)` "
            "for every parameter and hands the rows to `HelpFormatter.write_dl` inside a "
            "single `Options` section. `Group.format_options` calls that and then appends "
            "the `Commands` section.",
            "rg -n 'def format_options' -A 12 src/click/core.py",
        ),
    )
    seeder.post("hamlet", LIVE, "That is a much smaller surface than I expected.")
    seeder.minutes_ago(3.2)
    seeder.request(
        "desdemona",
        LIVE,
        "implement option groups: an `OptionGroup` that options can point at, and "
        "`--help` printing one section per group. Keep existing output unchanged for "
        "commands that do not use groups.",
        live_script,
    )
    node = seeder.workspace(LIVE)
    formatting = Path(node.workdir) / "src/click/formatting.py"
    formatting.write_text(
        formatting.read_text()
        + "\n\n#: Shared first-column width for grouped definition lists.\nGROUP_COL_MAX = 30\n"
    )
    seeder.refresh_workspace(node)


# --- 3. queued, failed, cancelled, needs input, paused ------------------------------------


def seed_queued_run(seeder: DemoSeeder) -> None:
    seeder.minutes_ago(2.5)
    seeder.post(
        "othello",
        QUEUED,
        "Both worker slots are busy with the option-groups and deprecation runs, "
        "so this one should sit in the queue for a bit.",
    )
    seeder.request(
        "othello",
        QUEUED,
        "audit `docs/` for examples that still use `autocompletion=` and list them.",
    )


FAILED_ERROR = (
    "Codex app-server turn failed: stream disconnected before completion: "
    "error sending request for url (https://chatgpt.com/backend-api/codex/responses)"
)


def failed_script(turn: ScriptedTurn) -> str:
    turn.plan(
        [
            ("Find the Windows-only code paths", "completed"),
            ("Run the console tests under wine", "in_progress"),
            ("Summarize what breaks", "pending"),
        ]
    )
    turn.command("rg -n 'WIN|msvcrt' src/click/_winconsole.py", seconds=1)
    turn.command("pytest tests/test_compat.py -q", seconds=8)
    turn.say("The console shim only loads on `win32`; checking whether wine is available here.")
    turn.command("wine --version", seconds=2, failed=True)
    turn.usage(input=22_400, cached=14_100, output=640, reasoning=410)
    turn.fail(FAILED_ERROR)
    return ""


def seed_failed_run(seeder: DemoSeeder) -> None:
    seeder.minutes_ago(95)
    seeder.post("cordelia", FAILED, "The Windows console tests have been flaky since the 8.5 bump.")
    seeder.request(
        "cordelia",
        FAILED,
        "run the `_winconsole` tests and tell me which ones fail and why.",
        failed_script,
    )
    seeder.post(
        "cordelia", FAILED, "Looks like the upstream connection dropped. I will retry after lunch."
    )


def cancelled_script(turn: ScriptedTurn) -> str:
    turn.plan(
        [
            ("Collect every public name in `click.__init__`", "completed"),
            ("Rewrite imports across `docs/`", "in_progress"),
            ("Rebuild the docs", "pending"),
        ]
    )
    turn.command("rg -n '^from|^import' src/click/__init__.py", seconds=1)
    turn.file_change("docs/api.md")
    turn.file_change("docs/quickstart.md")
    turn.say("Rewriting imports in `docs/`; 14 files left.")
    turn.usage(input=18_700, cached=9_300, output=820, reasoning=300)
    turn.wait(20)
    turn.cancel("hamlet")
    turn.file_change("docs/options.md")
    return ""


def seed_cancelled_run(seeder: DemoSeeder) -> None:
    seeder.minutes_ago(70)
    seeder.request(
        "hamlet",
        CANCELLED,
        "rewrite every `import click` example in `docs/` to use explicit imports.",
        cancelled_script,
    )
    seeder.post(
        "hamlet",
        CANCELLED,
        "Stopped it: I asked for the wrong thing. The docs should keep `import click`, "
        "it is the style the tutorial teaches.",
    )
    seeder.post(
        "iago", CANCELLED, "Agreed. Please revert the two files it touched before it stopped."
    )


def needs_input_script(turn: ScriptedTurn) -> str:
    turn.plan(
        [
            ("Find every use of `human_readable_name`", "completed"),
            ("Decide the deprecation path", "in_progress"),
            ("Apply it and update CHANGES.md", "pending"),
        ]
    )
    turn.command("rg -n 'human_readable_name' src tests docs", seconds=2)
    turn.command("git log --oneline -5 -- src/click/core.py", seconds=1)
    turn.say(
        "`human_readable_name` has 9 call sites; 3 are in public error messages. "
        "The two reasonable paths differ in what users see during 8.x."
    )
    turn.usage(input=31_900, cached=20_500, output=1_240, reasoning=870)
    turn.ask(
        [
            {
                "id": "deprecation",
                "header": "Deprecation path",
                "question": "How should `human_readable_name` go away?",
                "options": [
                    {
                        "label": "warn-in-8.6",
                        "description": "Emit `DeprecationWarning` now, remove in 9.0",
                    },
                    {
                        "label": "silent-alias",
                        "description": "Keep it as an alias of `spec` until 9.0, no warning",
                    },
                    {"label": "remove-now", "description": "Remove it in 8.6 and note the break"},
                ],
            }
        ]
    )
    return ""


def seed_needs_input(seeder: DemoSeeder) -> None:
    seeder.minutes_ago(9)
    seeder.post(
        "iago",
        NEEDS_INPUT,
        "#3877 added `Parameter.spec`. We still need to decide what happens to the old name.",
    )
    seeder.request(
        "iago",
        NEEDS_INPUT,
        "deprecate `human_readable_name` in favour of `spec`. Ask before choosing how loud to be.",
        needs_input_script,
    )


def seed_paused_topic(seeder: DemoSeeder) -> None:
    seeder.minutes_ago(240)
    run = seeder.request(
        "desdemona",
        PAUSED,
        "list the open TODO comments in `src/click/termui.py`.",
        quick(
            "There are four:\n\n"
            "- `termui.py:412` — progress bar does not handle `SIGWINCH`\n"
            "- `termui.py:608` — pager falls back to `more` without checking `PAGER`\n"
            "- `termui.py:731` — `edit()` ignores `VISUAL` on Windows\n"
            "- `termui.py:842` — `getchar()` echo handling on old conhost",
            "rg -n 'TODO|FIXME' src/click/termui.py",
        ),
    )
    assert run is not None
    seeder.post("desdemona", PAUSED, "Pausing Pear here while we talk through the release plan.")
    update_pear_run_control_event(seeder.user("desdemona"), run.id, "pause", "Paused by user.")
    seeder.shift(60)
    seeder.post("othello", PAUSED, "Fine by me. I would cut 8.6 after the completion fix lands.")
    seeder.request("hamlet", PAUSED, "can you start on the `SIGWINCH` one?")
    seeder.post("hamlet", PAUSED, "Ah, still paused. I will wait.")


# --- 4. artifacts -------------------------------------------------------------------------

SCENE_ARTIFACT: dict[str, Any] = {
    "version": 1,
    "title": "How a completion request resolves",
    "blocks": [
        {
            "type": "copy",
            "eyebrow": "Shell completion",
            "heading": "Four hops from TAB to a list",
            "body": "The shell never imports Click. It re-runs your program with two "
            "environment variables set, and Click answers on stdout.",
        },
        {
            "type": "scene",
            "label": "Completion request flow",
            "aspect": "wide",
            "root": {
                "type": "group",
                "layout": "graph",
                "children": [
                    {"type": "shape", "id": "shell", "shape": "pill", "label": "zsh"},
                    {"type": "shape", "id": "program", "shape": "rectangle", "label": "cli"},
                    {
                        "type": "shape",
                        "id": "resolve",
                        "shape": "rectangle",
                        "label": "_resolve_context",
                    },
                    {
                        "type": "shape",
                        "id": "complete",
                        "shape": "rectangle",
                        "label": "shell_complete",
                    },
                    {"type": "shape", "id": "format", "shape": "diamond", "label": "format"},
                    {
                        "type": "connector",
                        "id": "c1",
                        "from": "shell",
                        "to": "program",
                        "directed": True,
                        "label": "COMP_WORDS",
                    },
                    {
                        "type": "connector",
                        "id": "c2",
                        "from": "program",
                        "to": "resolve",
                        "directed": True,
                    },
                    {
                        "type": "connector",
                        "id": "c3",
                        "from": "resolve",
                        "to": "complete",
                        "directed": True,
                    },
                    {
                        "type": "connector",
                        "id": "c4",
                        "from": "complete",
                        "to": "format",
                        "directed": True,
                    },
                    {
                        "type": "connector",
                        "id": "c5",
                        "from": "format",
                        "to": "shell",
                        "directed": True,
                        "label": "stdout",
                    },
                    {
                        "type": "text",
                        "id": "note",
                        "textRole": "note",
                        "text": "Press TAB to begin.",
                    },
                ],
            },
            "steps": [
                {
                    "label": "The shell re-invokes the program",
                    "changes": [
                        {"target": "shell", "state": "running"},
                        {"target": "c1", "state": "running"},
                        {"target": "note", "text": "_CLI_COMPLETE=zsh_complete is set."},
                    ],
                },
                {
                    "label": "Click walks the command tree",
                    "changes": [
                        {"target": "shell", "state": "success"},
                        {"target": "program", "state": "success"},
                        {"target": "resolve", "state": "running"},
                        {"target": "note", "text": "Each word resolves one group deeper."},
                    ],
                },
                {
                    "label": "The innermost object proposes items",
                    "changes": [
                        {"target": "resolve", "state": "success"},
                        {"target": "complete", "state": "running"},
                        {
                            "target": "note",
                            "text": "Groups offer subcommands, options offer values.",
                        },
                    ],
                },
                {
                    "label": "Items are formatted for the shell",
                    "changes": [
                        {"target": "complete", "state": "success"},
                        {"target": "format", "state": "warning", "label": "format (bug)"},
                        {"target": "c5", "state": "warning"},
                        {"target": "note", "text": "This is where nested help text was lost."},
                    ],
                },
            ],
        },
    ],
    "actions": [
        {"label": "Show the fix", "prompt": "Show the diff that fixed nested completion."},
        {"label": "Same for fish", "prompt": "Draw the same flow for fish completion."},
    ],
}

TABLE_ARTIFACT: dict[str, Any] = {
    "version": 1,
    "title": "Release readiness: 8.6.0",
    "data": {"passed": 1482, "coverage": 97.4},
    "blocks": [
        {
            "type": "key_value",
            "label": "Release checklist",
            "items": [
                {"key": "Branch", "value": "main @ 2247b35"},
                {"key": "Open blockers", "value": 2},
                {"key": "Changelog", "value": "11 entries, 3 deprecations"},
                {"key": "Docs build", "value": "clean"},
                {"key": "Python", "value": "3.10 – 3.14"},
                {"key": "Signed off", "value": False},
            ],
        },
        {
            "type": "metric",
            "label": "Tests passing",
            "value": 1482,
            "bind": "data.passed",
            "delta": 37,
            "state": "success",
        },
        {
            "type": "meter",
            "label": "Line coverage",
            "value": 97.4,
            "bind": "data.coverage",
            "total": 100,
            "unit": "%",
            "detail": "Target for a minor release is 97%",
            "state": "success",
        },
        {
            "type": "meter",
            "label": "Blockers closed",
            "value": 5,
            "total": 7,
            "detail": "#3866 and #3891 remain",
            "state": "warning",
        },
    ],
    "actions": [{"label": "List blockers", "prompt": "List the open 8.6.0 blockers with owners."}],
}

CHART_ARTIFACT: dict[str, Any] = {
    "version": 1,
    "title": "Test suite over the last 24 runs",
    "blocks": [
        {
            "type": "series",
            "label": "Suite duration",
            "unit": "s",
            "values": [
                41.2,
                40.8,
                42.1,
                41.7,
                43.0,
                42.6,
                44.9,
                47.3,
                46.8,
                48.1,
                52.4,
                51.9,
                53.2,
                55.0,
                54.1,
                49.7,
                46.2,
                44.8,
                43.9,
                43.1,
                42.7,
                42.2,
                41.9,
                41.6,
            ],
            "state": "neutral",
        },
        {
            "type": "series",
            "label": "Failures per run",
            "values": [0, 0, 1, 0, 0, 2, 3, 5, 4, 6, 9, 8, 7, 4, 2, 1, 0, 0, 1, 0, 0, 0, 0, 0],
            "state": "warning",
        },
        {
            "type": "log",
            "label": "Timeline",
            "lines": [
                "09:12  a59f7c6  Start 8.6.0",
                "10:40  b90faad  suite slows: parametrized deprecation cases added",
                "11:05  06b2a67  9 failures on 3.14t (free-threaded)",
                "13:30  9f0bcdf  fixture scope fixed, failures drop to 2",
                "14:18  2247b35  green on every interpreter",
            ],
        },
    ],
}


def media_artifact(source: str, source_dark: str) -> dict[str, Any]:
    return {
        "version": 1,
        "title": "Rendered help, before and after",
        "blocks": [
            {
                "type": "copy",
                "body": "Captured from `examples/naval` at 80 columns. Ungrouped options "
                "stay under the plain heading.",
            },
            {
                "type": "media",
                "kind": "image",
                "source": source,
                "sourceDark": source_dark,
                "alt": "Terminal capture of grouped --help output for the naval example",
                "caption": "naval ship move --help, with option groups",
                "aspect": "wide",
            },
        ],
    }


def terminal_capture(dark: bool) -> bytes:
    """A stand-in for a generated screenshot: text-like bars on a terminal card."""
    width, height = 640, 360
    background = (22, 24, 29) if dark else (247, 247, 245)
    card = (33, 36, 43) if dark else (255, 255, 255)
    ink = (201, 205, 214) if dark else (62, 66, 74)
    accent = (120, 196, 148) if dark else (38, 132, 88)
    rows = [
        (40, 150, False),
        (72, 420, False),
        (112, 90, True),
        (136, 300, False),
        (160, 360, False),
        (200, 130, True),
        (224, 280, False),
        (248, 330, False),
        (272, 250, False),
        (312, 180, False),
    ]

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        if not (24 <= x < width - 24 and 20 <= y < height - 20):
            return background
        for top, length, heading in rows:
            if top <= y < top + 10 and 48 <= x < 48 + length:
                return accent if heading else ink
        return card

    return png(width, height, pixel)


def artifact_turn(document: dict[str, Any], answer: str, *commands: str) -> Script:
    def script(turn: ScriptedTurn) -> str:
        for command in commands:
            turn.command(command, seconds=2)
        turn.artifact(document)
        turn.usage(input=27_300, cached=18_800, output=1_950, reasoning=640)
        return answer

    return script


def seed_artifacts(seeder: DemoSeeder) -> None:
    seeder.minutes_ago(150)
    seeder.post("othello", ARTIFACTS, "I keep forgetting how a TAB press ends up inside Click.")
    seeder.request(
        "othello",
        ARTIFACTS,
        "explain how a completion request resolves, as something I can step through.",
        artifact_turn(
            SCENE_ARTIFACT,
            "Here is the path a completion request takes. Step through it: the last "
            "step is where the nested-group bug lived.",
            "sed -n '520,600p' src/click/shell_completion.py",
        ),
    )
    seeder.request(
        "desdemona",
        ARTIFACTS,
        "are we ready to cut 8.6.0? Give me a checklist I can pin.",
        artifact_turn(
            TABLE_ARTIFACT,
            "Not yet: two blockers are open and nobody has signed off. Everything "
            "mechanical is green.",
            "pytest -q",
            "coverage report --format=total",
            "gh issue list --milestone 8.6.0 --state open",
        ),
    )
    seeder.request(
        "cordelia",
        ARTIFACTS,
        "chart how the test suite behaved today, with what changed when.",
        artifact_turn(
            CHART_ARTIFACT,
            "The slowdown and the failures were the same event: the parametrized "
            "deprecation cases landed with a module-scoped fixture. It recovered at `9f0bcdf`.",
            "git log --since=midnight --format='%h %s'",
            "python tools/suite_history.py --last 24",
        ),
    )
    for dark in (False, True):
        seeder.write_media(
            ARTIFACTS,
            f".pear/artifacts/help-capture{'-dark' if dark else ''}.png",
            terminal_capture(dark),
        )
    seeder.request(
        "iago",
        ARTIFACTS,
        "show me what grouped `--help` looks like in a real terminal.",
        artifact_turn(
            media_artifact(
                ".pear/artifacts/help-capture.png", ".pear/artifacts/help-capture-dark.png"
            ),
            "Captured from the `naval` example. Light and dark variants are attached; "
            "the artifact follows your theme.",
            "python examples/naval/naval.py ship move --help",
            "python tools/capture_terminal.py --out .pear/artifacts/help-capture.png",
        ),
    )
    seeder.post("iago", ARTIFACTS, "That is exactly the layout I had in mind.")


# --- 5. wall monitors --------------------------------------------------------------------------


def static_spec(title: str, cadence: int, artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": title,
        "cadence_seconds": cadence,
        "artifact": artifact,
        "recipe": {"parse": {"mode": "static"}},
    }


def seed_wall_monitors(seeder: DemoSeeder) -> None:
    seeder.minutes_ago(120)
    seeder.workspace(WALL)
    seeder.post(
        "desdemona",
        WALL,
        "Pinning a few monitors here so we can watch the 8.6 branch while the "
        "option-groups work lands. They live in the right rail.",
    )
    seeder.monitor(
        WALL,
        "watch the test suite on this branch",
        static_spec(
            "test suite",
            20,
            {
                "version": 1,
                "title": "Test suite",
                "data": {
                    "passed": 1482,
                    "failed": 0,
                    "duration": [41.2, 40.8, 42.1, 44.9, 47.3, 52.4, 49.7, 44.8, 43.1, 42.2, 41.6],
                },
                "blocks": [
                    {
                        "type": "metric",
                        "label": "passed",
                        "value": 1482,
                        "bind": "data.passed",
                        "delta": 37,
                        "state": "success",
                    },
                    {"type": "metric", "label": "failed", "value": 0, "bind": "data.failed"},
                    {
                        "type": "series",
                        "label": "duration",
                        "unit": "s",
                        "values": [41.2, 40.8, 42.1],
                        "bind": "data.duration",
                    },
                ],
                "actions": [{"label": "why slower?", "prompt": "Why did the suite get slower?"}],
            },
        ),
    )
    seeder.monitor(
        WALL,
        "coverage and open blockers for 8.6.0",
        static_spec(
            "release 8.6.0",
            300,
            {
                "version": 1,
                "title": "Release 8.6.0",
                "data": {"coverage": 97.4},
                "blocks": [
                    {
                        "type": "meter",
                        "label": "coverage",
                        "value": 97.4,
                        "bind": "data.coverage",
                        "total": 100,
                        "unit": "%",
                        "state": "success",
                    },
                    {
                        "type": "key_value",
                        "label": "blockers",
                        "items": [
                            {"key": "#3866", "value": "identifier deprecation"},
                            {"key": "#3891", "value": "nested completion"},
                        ],
                    },
                ],
            },
        ),
    )
    seeder.provisioning_monitor(WALL, "tail the docs build and flag new warnings")
    seeder.monitor(
        WALL,
        "tail the dev server log",
        static_spec(
            "server log",
            5,
            {
                "version": 1,
                "title": "Server log",
                "data": {"lines": ["waiting for first sample"]},
                "blocks": [
                    {"type": "log", "label": "logs/server.log", "lines": [], "bind": "data.lines"}
                ],
            },
        ),
        status=PearMonitor.ERROR,
        last_error="recipe exited 1: tail: cannot open 'logs/server.log' for reading: "
        "No such file or directory",
    )
    seeder.monitor(
        WALL,
        "count TODO comments in src/",
        static_spec(
            "todo count",
            3600,
            {
                "version": 1,
                "title": "TODOs in src/",
                "data": {"todos": 23},
                "blocks": [
                    {
                        "type": "metric",
                        "label": "open TODOs",
                        "value": 23,
                        "bind": "data.todos",
                        "delta": -4,
                    }
                ],
            },
        ),
        status=PearMonitor.PAUSED,
    )
    seeder.post(
        "hamlet", WALL, "The server-log one is red for me: there is no `logs/` in this repo."
    )
    seeder.post("desdemona", WALL, "Yes, wrong path. I will rebuild that one after standup.")


# --- 6. threads -----------------------------------------------------------------------------------

THREADS_ANSWER = """\
Three agents looked at this in parallel; they agree on the shape and disagree on one default.

- **Noether** (API): sections should be data on the command (`OptionGroup`), not a \
formatter subclass, so `to_info_dict()` can expose them.
- **Lovelace** (compat): default output must not change. 212 downstream projects \
snapshot-test `--help`.
- **Turing** (docs): grouping by default would also reorder `--help` and `--version`, \
which users expect last.

Recommendation: ship `OptionGroup` in 8.6 as opt-in, and revisit a default in 9.0."""


def threads_script(turn: ScriptedTurn) -> str:
    turn.plan(
        [
            ("Split the question three ways", "completed"),
            ("Collect the three reviews", "in_progress"),
            ("Reconcile and recommend", "pending"),
        ]
    )
    turn.command("rg -n 'def format_options' -A 12 src/click/core.py", seconds=2)
    turn.command("git log --oneline -8 -- src/click/formatting.py", seconds=1)
    noether = turn.spawn("Noether", "explorer", "Assess the API shape for option sections")
    lovelace = turn.spawn("Lovelace", "worker", "Measure downstream breakage of a new default")
    turing = turn.spawn("Turing", "reviewer", "Review the docs impact of grouped help")
    turn.plan(
        [("Inventory the public formatter API", "in_progress"), ("Recommend a shape", "pending")],
        agent=noether,
    )
    turn.command("rg -n 'class HelpFormatter' -A 20 src/click/formatting.py", agent=noether)
    turn.command("rg -n 'to_info_dict' src/click/core.py", agent=noether)
    turn.command("python tools/downstream_help_snapshots.py --top 500", seconds=18, agent=lovelace)
    turn.command("rg -n -- '--help' docs | wc -l", agent=turing)
    turn.tool("workspace.read_file docs/options.md", agent=turing)
    turn.say(
        "Sections belong on the command as data. A formatter subclass cannot be "
        "introspected, and `to_info_dict()` is how docs generators read Click apps.",
        agent=noether,
    )
    turn.say(
        "212 of the top 500 dependents snapshot `--help`. Any change to default "
        "output breaks their test suites on upgrade.",
        agent=lovelace,
    )
    turn.say(
        "The docs show `--help` output 61 times. Grouping by default moves `--help` "
        "itself out of last position in 40 of them.",
        agent=turing,
    )
    for agent in (noether, lovelace, turing):
        turn.finish_agent(agent)
    turn.wait_for_agents()
    turn.usage(input=84_600, cached=59_200, output=3_880, reasoning=2_140)
    return THREADS_ANSWER


def seed_threads(seeder: DemoSeeder) -> None:
    seeder.minutes_ago(170)
    root = seeder.post(
        "iago",
        THREADS,
        "Proposal: `--help` groups options by section **by default** in 9.0. "
        "Objections in the thread, please, so the topic stays readable.",
    )
    for sender, text in [
        ("hamlet", "Default-on worries me. Every snapshot test of `--help` output breaks."),
        ("othello", "Could it be default-on only when a command declares at least one group?"),
        ("cordelia", "That is opt-in with extra steps, which I like. No groups, no change."),
        ("iago", "Fair. Then the proposal is really just: add the API, keep the default."),
        ("desdemona", "+1 to Othello's version. I will ask Pear to pressure-test it."),
        ("hamlet", "Works for me. Can we get numbers on how many projects snapshot help text?"),
    ]:
        seeder.post(sender, THREADS, text, thread_root=root, wait=95)
    seeder.post(
        "othello", THREADS, "Summary of the thread above: add the API, keep default output."
    )
    run = seeder.request(
        "desdemona",
        THREADS,
        "pressure-test adding option sections to `--help`: API shape, downstream "
        "breakage, docs impact. Use a few agents.",
        threads_script,
    )
    assert run is not None
    answer = seeder.result_message(run)
    seeder.post(
        "hamlet",
        THREADS,
        "212 is higher than I guessed. That settles the default for me.",
        thread_root=answer,
    )
    seeder.request(
        "desdemona",
        THREADS,
        "quick aside: which of those 212 projects are the biggest?",
        quick(
            "By dependents: `flask` (CLI snapshots in its own test suite), `black`, "
            "`pip-tools`, `dbt-core` and `typer`. Typer matters most here because it "
            "re-renders Click's help with Rich and would need its own change.",
            "python tools/downstream_help_snapshots.py --top 500 --sort dependents | head -5",
        ),
        thread_root=answer,
    )
    seeder.post(
        "othello",
        THREADS,
        "Typer is the one to coordinate with. I will open an issue there.",
        thread_root=answer,
    )
    seeder.post("iago", THREADS, "Thanks all. Writing this up as the 8.6 plan.")


# --- 7. a dirty worktree ------------------------------------------------------------------------------


def dirty_script(turn: ScriptedTurn) -> str:
    turn.command("git status --short", seconds=1)
    turn.file_change("src/click/shell_completion.py")
    turn.file_change("tests/test_shell_completion.py")
    turn.command("git commit -am 'Carry group descriptions through nested completion'", seconds=1)
    turn.file_change("CHANGES.md")
    turn.file_change("docs/nested-completion.md")
    turn.command("git mv docs/shell-completion.md docs/completion.md", seconds=1)
    turn.command("git rm --cached examples/README", seconds=1)
    turn.usage(input=36_100, cached=24_900, output=1_420, reasoning=510)
    return (
        "The fix is committed; the follow-up is staged in the worktree but **not committed**, "
        "because it renames a docs page and I want a human to confirm the new URL:\n\n"
        "- `CHANGES.md` — changelog entry\n"
        "- `docs/nested-completion.md` — new page\n"
        "- `docs/shell-completion.md` → `docs/completion.md`\n"
        "- `examples/README` — removed, it duplicated `docs/`\n\n"
        "Open the workbench to review the diff."
    )


def seed_dirty_worktree(seeder: DemoSeeder) -> None:
    seeder.minutes_ago(55)
    seeder.request(
        "desdemona",
        DIRTY,
        "port the nested-completion fix here and draft the docs and changelog, "
        "but leave the docs changes uncommitted for review.",
        dirty_script,
    )
    node = seeder.workspace(DIRTY)
    apply_completion_fix(node)
    seeder.git(
        node, "commit", "--quiet", "-am", "Carry group descriptions through nested completion"
    )
    apply_uncommitted_work(seeder, node)
    seeder.refresh_workspace(node)
    seeder.post(
        "cordelia", DIRTY, "I will review the rename; `docs/completion.md` reads better to me."
    )


# --- 8. a long, busy topic ------------------------------------------------------------------------------

# (sender, text) is a person talking; (sender, question, answer, *commands) asks Pear.
LONG_CONVERSATION: list[tuple[str, ...]] = [
    ("iago", "Kicking off 8.6 triage. I will go through the milestone top to bottom."),
    ("hamlet", "I have the morning free, send things my way."),
    (
        "iago",
        "how many issues are open on the 8.6.0 milestone?",
        "14 open, 31 closed. Six of the open ones have a linked PR.",
        "gh issue list --milestone 8.6.0 --state open --json number",
    ),
    ("othello", "Six with PRs is better than I feared."),
    ("cordelia", "Can we start with the ones touching `core.py`? Those conflict with each other."),
    (
        "cordelia",
        "which open PRs touch `src/click/core.py`?",
        "Four: #3866 (identifier deprecation), #3877 (`Parameter.spec`, merged), #3884 (flag default typing) and #3891 (nested completion). #3884 and #3891 edit the same hunk in `Option.__init__`.",
        "gh pr list --search 'core.py' --json number,title",
        "git diff main...pr-3884 --stat",
    ),
    ("hamlet", "I will take the #3884 / #3891 conflict."),
    ("iago", "Thanks. Rebase #3884 on top, it is the smaller one."),
    ("othello", "Is the docs build still warning about duplicate labels?"),
    (
        "othello",
        "does `make -C docs html` still emit warnings?",
        "Two, both `duplicate label` from `docs/options.md` including `option-decorators.md`. Nothing new since `a59f7c6`.",
        "make -C docs html 2>&1 | rg -c WARNING",
    ),
    ("desdemona", "Those two have been there since the Markdown migration. Safe to defer."),
    ("iago", "Deferred to 8.7 then."),
    ("hamlet", "Rebased #3884. One test needed a tweak because `flag_value` typing got stricter."),
    ("cordelia", "Which test?"),
    ("hamlet", "`test_flag_default_map`. It was passing a string where a bool is now required."),
    (
        "hamlet",
        "run the options tests on the rebased branch.",
        "`pytest tests/test_options.py -q`: 214 passed in 3.1s. `test_flag_default_map` passes with the bool.",
        "pytest tests/test_options.py -q",
    ),
    ("iago", "Good. Merge when CI is green."),
    ("othello", "Unrelated: the free-threaded 3.14 job timed out twice overnight."),
    ("desdemona", "Timeout or hang?"),
    ("othello", "Not sure. The log just stops in `test_termui`."),
    (
        "othello",
        "look at last night's 3.14t CI log and tell me where it stops.",
        "It stops in `tests/test_termui.py::test_progressbar_update_with_item_show_func` after 6h. The last line is the progress bar's render lock being acquired; nothing releases it. That is a hang, not a slow test.",
        "gh run view --log --job 3.14t | tail -40",
    ),
    ("cordelia", "That lock is new in 8.5. I can look."),
    ("desdemona", "Please do, a hang in `termui` is a release blocker."),
    ("iago", "Adding it to the milestone as #3896."),
    ("hamlet", "Do we have a reproducer outside CI?"),
    (
        "cordelia",
        "try to reproduce the progress bar hang locally under `python3.14t`.",
        "Reproduced 3 times in 200 runs with `PYTHON_GIL=0`. Two threads call `ProgressBar.render_progress` and both wait on `_lock`; it is taken re-entrantly from `update()`. Switching it to `RLock` made 2,000 runs pass.",
        "for i in $(seq 200); do PYTHON_GIL=0 python3.14t -m pytest tests/test_termui.py -q -x; done",
        "python3.14t -X faulthandler repro_progress.py",
    ),
    ("cordelia", "`RLock` it is. PR in a minute."),
    ("othello", "Nice catch."),
    ("desdemona", "Back to the list. #3866 needs a changelog entry."),
    ("iago", "It has one, under 'Unreleased', second bullet."),
    ("desdemona", "So it does. Scratch that."),
    ("hamlet", "What about the `Parameter.spec` docs? The API page does not mention it."),
    (
        "hamlet",
        "is `Parameter.spec` documented anywhere under `docs/`?",
        "Only in `CHANGES.md`. `docs/api.md` autodocs `Parameter` but `spec` is missing because the directive lists members explicitly. Adding `spec` to that list is a one-line change.",
        "rg -n 'spec' docs/api.md docs/parameters.md",
    ),
    ("iago", "Please add it."),
    (
        "hamlet",
        "add `spec` to the `Parameter` members in `docs/api.md`.",
        "Done: added `spec` after `human_readable_name` in the member list. The docs build shows the new attribute and no new warnings.",
        "make -C docs html 2>&1 | rg -c WARNING",
        "git diff --stat",
    ),
    (
        "othello",
        "While we are in docs: the quickstart still installs with `pip install click==8.1`.",
    ),
    ("cordelia", "Ha. That pin is from 2022."),
    (
        "othello",
        "find hard-coded Click versions in `docs/`.",
        "Three: `docs/quickstart.md:14` (`click==8.1`), `docs/virtualenv.md:52` (`Click 8.0`), and `docs/upgrade-guides.md:9`, which is intentional.",
        "rg -n 'click==|Click 8\\\\.' docs",
    ),
    ("othello", "I will fix the first two by hand."),
    ("iago", "Thanks. What is left on the list?"),
    (
        "iago",
        "what is still open on the milestone, grouped by whether it has a PR?",
        "**With a PR (5):** #3866, #3884, #3891, #3896, #3879.\n**Without (7):** #3812, #3840, #3851, #3858, #3870, #3882, #3890.\nThree of the seven are labelled `docs`.",
        "gh issue list --milestone 8.6.0 --state open --json number,labels",
    ),
    ("desdemona", "Seven without PRs is too many for this week."),
    ("iago", "Agreed. I would move the three docs ones to 8.7."),
    ("hamlet", "And #3812 has been open for a year, it is not an 8.6 problem."),
    ("cordelia", "#3840 is mine, I can finish it tomorrow."),
    ("othello", "#3882 is a duplicate of #3866 as far as I can tell."),
    (
        "othello",
        "compare #3882 and #3866: same bug?",
        "Same root cause. Both report `TypeError` for an option named `class`; #3882 just hits it through `@click.option('--class')` instead of a `Parameter` subclass. #3866's fix covers it, and its test already parametrizes the decorator path.",
        "gh issue view 3882",
        "gh issue view 3866",
    ),
    ("iago", "Closing #3882 as a duplicate."),
    ("desdemona", "That leaves #3851, #3858, #3870, #3890."),
    ("hamlet", "I can take #3858 and #3870, both are small."),
    ("desdemona", "I will take #3851."),
    ("iago", "And #3890 goes to whoever finishes first."),
    ("cordelia", "PR for the `RLock` fix is up: #3897."),
    (
        "cordelia",
        "run the full suite on #3897 under 3.14t.",
        "1,482 passed, 12 skipped in 44.8s under `python3.14t` with `PYTHON_GIL=0`. I also ran `tests/test_termui.py` 2,000 times: no hang.",
        "PYTHON_GIL=0 python3.14t -m pytest -q",
    ),
    ("othello", "Approved."),
    ("iago", "Merging. That clears the blocker."),
    ("desdemona", "Quickstart pins are fixed and pushed, by the way."),
    ("hamlet", "#3884 is green and merged."),
    ("iago", "Good progress. Where does that leave the release?"),
    (
        "iago",
        "summarize the state of 8.6.0 in five lines.",
        "- **Blockers:** 0 open (#3896 fixed by #3897)\n- **Open with PR:** #3866, #3891, #3879\n- **Open without PR:** #3851, #3858, #3870, #3890, each with an owner except #3890\n- **Deferred to 8.7:** 3 docs issues and #3812\n- **CI:** green on every interpreter, including 3.14t",
        "gh issue list --milestone 8.6.0 --state open --json number,assignees",
        "gh run list --branch main --limit 1",
    ),
    ("desdemona", "That is a release we can cut on Friday."),
    ("othello", "Friday works. I will draft the announcement."),
    ("cordelia", "I will be around to help with the upload."),
    ("iago", "Thanks everyone. Same time tomorrow for the last four."),
]


def seed_long_topic(seeder: DemoSeeder) -> None:
    seeder.minutes_ago(60 * 26)
    for entry in LONG_CONVERSATION:
        sender, text, *rest = entry
        if rest:
            answer, *commands = rest
            seeder.request(sender, LONG, text, quick(answer, *commands))
            seeder.shift(240)
        else:
            seeder.post(sender, LONG, text, wait=170)


# --- the table of contents ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    topic: str
    shows: str
    seed: Callable[[DemoSeeder], None]


# Settled topics first.  A frozen or queued run holds one of the realm's
# three run slots (PEAR_WORKER_MAX_ACTIVE_RUNS_PER_REALM) from the moment it
# exists, and the three at the end fill them: nothing can run after those.
SCENARIOS = [
    Scenario(
        LONG,
        "77 messages (64 from five people, 13 agent answers with footers), for scroll and density",
        seed_long_topic,
    ),
    Scenario(
        COMPLETED,
        "completed run: long answer with code, lists and inline code; finished "
        "plan; 13 tool calls (one failed); footer with duration, tools and tokens; "
        "trace; a second short run; clean worktree, one commit ahead",
        seed_completed_run,
    ),
    Scenario(FAILED, "failed run with error text", seed_failed_run),
    Scenario(CANCELLED, "run cancelled from the panel mid-turn", seed_cancelled_run),
    Scenario(
        PAUSED,
        "paused topic: pause control on the last run, and Pear refusing a new request",
        seed_paused_topic,
    ),
    Scenario(
        ARTIFACTS,
        "four agent messages with artifacts: scene with steps; key-value + metric + "
        "meters; two series + log timeline; themed image media",
        seed_artifacts,
    ),
    Scenario(
        WALL,
        "wall monitors: two live (metrics + series; meter + key-value), one "
        "provisioning, one in error, one paused",
        seed_wall_monitors,
    ),
    Scenario(
        THREADS,
        "a human thread with six replies from four people; an agent turn with three "
        "sub-agents; a thread under the agent's answer containing a side (/btw) run",
        seed_threads,
    ),
    Scenario(
        DIRTY,
        "dirty worktree: 1 commit ahead plus modified, added, renamed and deleted files",
        seed_dirty_worktree,
    ),
    Scenario(
        LIVE,
        "a run that is live now: plan with done / current / pending steps, overview "
        "line, streaming partial answer, a tool call in flight, four sub-agents "
        "(one active, two idle, one failed), applied steering, ticking timer",
        seed_live_run,
    ),
    Scenario(
        NEEDS_INPUT,
        "live run blocked on a question to the humans (needs input)",
        seed_needs_input,
    ),
    Scenario(QUEUED, "queued run: admitted, 'Working' placeholder, no worker yet", seed_queued_run),
]

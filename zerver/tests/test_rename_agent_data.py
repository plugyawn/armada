from collections.abc import Callable
from importlib import import_module
from unittest import TestCase

# The migration that renamed the agent in stored text.  Its rewrites
# approximate Zulip's renderer, so they are tested here on the shapes a
# message can take; migrations are not in the coverage report.
migration = import_module("zerver.migrations.0812_rename_agent_data")
OLD_NAME, NEW_NAME = migration.AGENT_NAME
BOT_ID = 25
FORWARD = migration.AgentDataRewrite(to_new_names=True)
BACK = migration.AgentDataRewrite(to_new_names=False)


def shape(text: str) -> str:
    """The test's text with the old name written in: this file does not
    hold it, since the guard against it reads every file but the
    migrations."""
    return text.format(old=OLD_NAME, old_lower=OLD_NAME.lower())


def span(name: str, *, silent: bool = False) -> str:
    if silent:
        return f'<span class="user-mention silent" data-user-id="{BOT_ID}">{name}</span>'
    return f'<span class="user-mention" data-user-id="{BOT_ID}">@{name}</span>'


CODE_BLOCK = '<div class="codehilite"><pre><span></span><code>'

# A person's messages that mention the agent: (text, rendering, the two
# after the rename, or None where the message is left whole).
MENTIONS: list[tuple[str, str, tuple[str, str] | None]] = [
    (
        "@**{old_lower}** please look",
        f"<p>{span('{old}')} please look</p>",
        ("@**onix** please look", f"<p>{span('Onix')} please look</p>"),
    ),
    (
        "the syntax is `@**{old}**`, so @**{old}** do it",
        f"<p>the syntax is <code>@**{{old}}**</code>, so {span('{old}')} do it</p>",
        (
            "the syntax is `@**{old}**`, so @**Onix** do it",
            f"<p>the syntax is <code>@**{{old}}**</code>, so {span('Onix')} do it</p>",
        ),
    ),
    (
        "```\n@**{old}** in code\n```\n@**{old}** real",
        f"{CODE_BLOCK}@**{{old}}** in code\n</code></pre></div>\n<p>{span('{old}')} real</p>",
        (
            "```\n@**{old}** in code\n```\n@**Onix** real",
            f"{CODE_BLOCK}@**{{old}}** in code\n</code></pre></div>\n<p>{span('Onix')} real</p>",
        ),
    ),
    (
        "```quote\n@**{old}** quoted\n```",
        f"<blockquote>\n<p>{span('{old}')} quoted</p>\n</blockquote>",
        (
            "```quote\n@**Onix** quoted\n```",
            f"<blockquote>\n<p>{span('Onix')} quoted</p>\n</blockquote>",
        ),
    ),
    (
        "    @**{old}** indented\n\n@**{old}** real",
        f"{CODE_BLOCK}@**{{old}}** indented\n</code></pre></div>\n<p>{span('{old}')} real</p>",
        (
            "    @**{old}** indented\n\n@**Onix** real",
            f"{CODE_BLOCK}@**{{old}}** indented\n</code></pre></div>\n<p>{span('Onix')} real</p>",
        ),
    ),
    (
        "@_**{old}|25** said",
        f"<p>{span('{old}', silent=True)} said</p>",
        ("@_**Onix|25** said", f"<p>{span('Onix', silent=True)} said</p>"),
    ),
    # Mentioned by id only: the text names no one, the rendering does.
    (
        "    @**{old}** run tests\n\nthen @**|25** please",
        f"{CODE_BLOCK}@**{{old}}** run tests\n</code></pre></div>\n<p>then {span('{old}')} please</p>",
        (
            "    @**{old}** run tests\n\nthen @**|25** please",
            f"{CODE_BLOCK}@**{{old}}** run tests\n</code></pre></div>\n<p>then {span('Onix')} please</p>",
        ),
    ),
    (
        "@_**|25** and `@**{old}**`",
        f"<p>{span('{old}', silent=True)} and <code>@**{{old}}**</code></p>",
        (
            "@_**|25** and `@**{old}**`",
            f"<p>{span('Onix', silent=True)} and <code>@**{{old}}**</code></p>",
        ),
    ),
    # After a letter, the syntax is not a mention; with an id, the name
    # must be the bot's own, case and all.
    (
        "x@**{old}** and @**{old}**",
        f"<p>x@<strong>{{old}}</strong> and {span('{old}')}</p>",
        ("x@**{old}** and @**Onix**", f"<p>x@<strong>{{old}}</strong> and {span('Onix')}</p>"),
    ),
    (
        "@**{old_lower}|25** or @**{old}|25**",
        f"<p>@<strong>{{old_lower}}|25</strong> or {span('{old}')}</p>",
        (
            "@**{old_lower}|25** or @**Onix|25**",
            f"<p>@<strong>{{old_lower}}|25</strong> or {span('Onix')}</p>",
        ),
    ),
    # Code that is not recognised (here the rendering says the indented
    # line is a mention and the last line code) can balance a count: the
    # order of mentions and code in the rendering tells them apart.
    (
        "    @**{old}** one\n\n@**{old}** two",
        f"<p>{span('{old}')} one</p>\n<pre><code>@**{{old}}** two\n</code></pre>",
        None,
    ),
    # A mention the rendering does not show (the bot could not be found
    # when it was sent).
    ("@**{old}** and @**{old}**", f"<p>@<strong>{{old}}</strong> and {span('{old}')}</p>", None),
]

# The agent's own messages: (text, rendering, the two after the rename,
# or None where the message is left as it is).
AGENT_MESSAGES: list[tuple[str, str, tuple[str, str] | None]] = [
    (
        "{old} worker failed.\n\n"
        "Error: {old} refused to start Codex because no isolated topic worktree was selected.",
        "<p>{old} worker failed.</p>\n"
        "<p>Error: {old} refused to start Codex because no isolated topic worktree was selected.</p>",
        (
            "Onix failed.\n\n"
            "Error: Refused to start Codex because no isolated topic worktree was selected.",
            "<p>Onix failed.</p>\n"
            "<p>Error: Refused to start Codex because no isolated topic worktree was selected.</p>",
        ),
    ),
    ("Stopped. The tests are green.", "<p>Stopped. The tests are green.</p>", None),
    (
        "{old} worker is not starting.\n\n"
        "Reason: {old} already has the maximum number of active worker runs in this realm.",
        "<p>{old} worker is not starting.</p>\n"
        "<p>Reason: {old} already has the maximum number of active worker runs in this realm.</p>",
        (
            "Onix is not starting.\n\n"
            "Reason: Onix already has the maximum number of active runs in this realm.",
            "<p>Onix is not starting.</p>\n"
            "<p>Reason: Onix already has the maximum number of active runs in this realm.</p>",
        ),
    ),
    (
        "{old} worker failed after posting progress.\n\n"
        "Error: {old} Codex skills provisioning failed: {old} managed-skills manifest is invalid.",
        "<p>{old} worker failed after posting progress.</p>\n"
        "<p>Error: {old} Codex skills provisioning failed: {old} managed-skills manifest is invalid.</p>",
        (
            "Onix failed after posting progress.\n\n"
            "Error: Codex skills provisioning failed: Managed-skills manifest is invalid.",
            "<p>Onix failed after posting progress.</p>\n"
            "<p>Error: Codex skills provisioning failed: Managed-skills manifest is invalid.</p>",
        ),
    ),
    (
        "{old} worker failed.\n\nError: {old} worker run 12 attempt 3 lost its lease.",
        "<p>{old} worker failed.</p>\n<p>Error: {old} worker run 12 attempt 3 lost its lease.</p>",
        (
            "Onix failed.\n\nError: Run 12 attempt 3 lost its lease.",
            "<p>Onix failed.</p>\n<p>Error: Run 12 attempt 3 lost its lease.</p>",
        ),
    ),
    (
        "{old} couldn't determine whether that message was meant for it. "
        "Address it as `@{old}` to bypass ambient routing.",
        "<p>{old} couldn't determine whether that message was meant for it. "
        "Address it as <code>@{old}</code> to bypass ambient routing.</p>",
        (
            "Onix couldn't determine whether that message was meant for it. "
            "Address it as `@Onix` to bypass ambient routing.",
            "<p>Onix couldn't determine whether that message was meant for it. "
            "Address it as <code>@Onix</code> to bypass ambient routing.</p>",
        ),
    ),
    (
        "{old} needs your input to continue:\n\n**Which branch?**",
        "<p>{old} needs your input to continue:</p>\n<p><strong>Which branch?</strong></p>",
        (
            "Onix needs your input to continue:\n\n**Which branch?**",
            "<p>Onix needs your input to continue:</p>\n<p><strong>Which branch?</strong></p>",
        ),
    ),
    (
        "{old} monitor setup started.\n\nTopic: a > b\nMonitor: count TODOs",
        "<p>{old} monitor setup started.</p>\n<p>Topic: a &gt; b<br>\nMonitor: count TODOs</p>",
        (
            "Monitor setup started.\n\nTopic: a > b\nMonitor: count TODOs",
            "<p>Monitor setup started.</p>\n<p>Topic: a &gt; b<br>\nMonitor: count TODOs</p>",
        ),
    ),
    (
        "Some prose.\n{old} worker failed.\nMore prose.",
        "<p>Some prose.<br>\n{old} worker failed.<br>\nMore prose.</p>",
        (
            "Some prose.\nOnix failed.\nMore prose.",
            "<p>Some prose.<br>\nOnix failed.<br>\nMore prose.</p>",
        ),
    ),
    # The phrase does not fill the line.
    ("{old} worker failed. More prose.", "<p>{old} worker failed. More prose.</p>", None),
    # A log the agent quotes in a code block is left in the text, as its
    # rendering leaves it.
    (
        "Done.\n\n```\n{old} worker failed.\nError: {old} session is paused.\n```\n\n"
        "Error: {old} session is paused.",
        f"<p>Done.</p>\n{CODE_BLOCK}{{old}} worker failed.\nError: {{old}} session is paused.\n"
        "</code></pre></div>\n<p>Error: {old} session is paused.</p>",
        (
            "Done.\n\n```\n{old} worker failed.\nError: {old} session is paused.\n```\n\n"
            "Error: The session is paused.",
            f"<p>Done.</p>\n{CODE_BLOCK}{{old}} worker failed.\nError: {{old}} session is paused.\n"
            "</code></pre></div>\n<p>Error: The session is paused.</p>",
        ),
    ),
    # Where the rendering does not show a phrase the text holds (here a
    # quote the approximation reads as text), neither is rewritten.
    (
        "> {old} worker failed.",
        "<blockquote>\n<p>{old} worker failed.</p>\n</blockquote>",
        None,
    ),
]

# The notification bot's notices: (text, rendering, the two after).
NOTICES: list[tuple[str, str, tuple[str, str] | None]] = [
    (
        "{old} workspace warning: I did not move the {old} workspace context because the "
        "destination topic already has its own {old} context. I moved 2 wall monitor(s) with "
        "the topic. Review the sidebar before starting more Codex work.",
        "<p>{old} workspace warning: I did not move the {old} workspace context because the "
        "destination topic already has its own {old} context. I moved 2 wall monitor(s) with "
        "the topic. Review the sidebar before starting more Codex work.</p>",
        (
            "Workspace warning: I did not move the workspace context because the destination "
            "topic already has its own context. I moved 2 wall monitor(s) with the topic. Review "
            "the sidebar before starting more Codex work.",
            "<p>Workspace warning: I did not move the workspace context because the destination "
            "topic already has its own context. I moved 2 wall monitor(s) with the topic. Review "
            "the sidebar before starting more Codex work.</p>",
        ),
    ),
    (
        "{old} channel context renamed from `old` to `new` for 1 workspace layer(s) and 0 wall "
        "monitor(s).",
        "<p>{old} channel context renamed from <code>old</code> to <code>new</code> for 1 "
        "workspace layer(s) and 0 wall monitor(s).</p>",
        (
            "Channel context renamed from `old` to `new` for 1 workspace layer(s) and 0 wall "
            "monitor(s).",
            "<p>Channel context renamed from <code>old</code> to <code>new</code> for 1 "
            "workspace layer(s) and 0 wall monitor(s).</p>",
        ),
    ),
    (
        "{old} wall moved 3 monitor(s) with this topic.",
        "<p>{old} wall moved 3 monitor(s) with this topic.</p>",
        (
            "The wall moved 3 monitor(s) with this topic.",
            "<p>The wall moved 3 monitor(s) with this topic.</p>",
        ),
    ),
    # Text and rendering that disagree (here, a rendering renamed by
    # hand) are left as they are.
    (
        "{old} wall moved 3 monitor(s) with this topic.",
        "<p>The wall moved 3 monitor(s) with this topic.</p>",
        None,
    ),
]

# A value standing in for each "{}" of a phrase.
SAMPLE_PARTS = ["12", "3"]


def fill(phrase: str) -> str:
    return phrase.format(*SAMPLE_PARTS[: phrase.count("{}")])


class RenameAgentDataTest(TestCase):
    def assert_rewrites(
        self,
        rewrite_forward: Callable[[str, str], tuple[str, str | None] | None],
        rewrite_back: Callable[[str, str], tuple[str, str | None] | None],
        shapes: list[tuple[str, str, tuple[str, str] | None]],
    ) -> None:
        for text, rendering, expected in shapes:
            planted = (shape(text), shape(rendering))
            renamed = rewrite_forward(*planted)
            if expected is None:
                self.assertIn(renamed, [None, planted], planted)
                continue
            text_after, rendering_after = shape(expected[0]), shape(expected[1])
            self.assertEqual(renamed, (text_after, rendering_after))
            # Again, as after a failure: nothing more changes.
            self.assertEqual(rewrite_forward(text_after, rendering_after), renamed)
            # Back, twice.
            self.assertEqual(rewrite_back(text_after, rendering_after), planted)
            self.assertEqual(rewrite_back(*planted), planted)

    def test_mentions(self) -> None:
        self.assert_rewrites(FORWARD.mentions_of(BOT_ID), BACK.mentions_of(BOT_ID), MENTIONS)
        # Another user's mention is not the agent's.
        other = '<p><span class="user-mention" data-user-id="8">@Iago</span></p>'
        self.assertEqual(FORWARD.mentions_of(BOT_ID)("@**Iago**", other), ("@**Iago**", other))

    def test_agent_messages(self) -> None:
        self.assert_rewrites(FORWARD.agent_message, BACK.agent_message, AGENT_MESSAGES)
        self.assertEqual(
            FORWARD.agent_message(shape("{old} worker failed."), None), ("Onix failed.", None)
        )

    def test_notices(self) -> None:
        self.assert_rewrites(FORWARD.notice, BACK.notice, NOTICES)

    def test_every_phrase_both_ways(self) -> None:
        for rendered in (False, True):
            form = migration.rendered_form if rendered else str
            for pairs in (migration.ERROR_TEXTS, migration.NOTICE_TEXTS):
                forward = migration.fragment_rewrite(pairs, rendered=rendered)
                back = migration.fragment_rewrite(
                    [(new, old) for old, new in pairs], rendered=rendered
                )
                for old, new in pairs:
                    old_text, new_text = form(fill(old)), form(fill(new))
                    self.assertEqual(forward(old_text)[0], new_text, old)
                    self.assertEqual(forward(new_text)[0], new_text, old)
                    self.assertEqual(back(new_text)[0], old_text, new)
                    self.assertEqual(back(old_text)[0], old_text, new)

        for old, new in migration.LINE_PHRASES:
            line = f"Before.\n{old}\nAfter."
            renamed = f"Before.\n{new}\nAfter."
            self.assertEqual(FORWARD.agent_lines(line, rendered=False)[0], renamed)
            self.assertEqual(FORWARD.agent_lines(renamed, rendered=False)[0], renamed)
            self.assertEqual(BACK.agent_lines(renamed, rendered=False)[0], line)
            self.assertEqual(BACK.agent_lines(line, rendered=False)[0], line)
            html_line = f"<p>{migration.rendered_form(old)}</p>"
            html_renamed = f"<p>{migration.rendered_form(new)}</p>"
            self.assertEqual(FORWARD.agent_lines(html_line, rendered=True)[0], html_renamed)
            self.assertEqual(BACK.agent_lines(html_renamed, rendered=True)[0], html_line)
            self.assertEqual(BACK.agent_lines(html_line, rendered=True)[0], html_line)

        for old, new in [*migration.RUN_TEXT, *migration.ERROR_TEXTS]:
            old_text, new_text = fill(old), fill(new)
            self.assertEqual(FORWARD.run_text(old_text), new_text, old)
            self.assertEqual(FORWARD.run_text(new_text), new_text, old)
            self.assertEqual(BACK.run_text(new_text), old_text, new)
            self.assertEqual(BACK.run_text(old_text), old_text, new)
        for old, new in migration.RUN_EVENT_PREFIXES:
            self.assertEqual(FORWARD.run_text(old + "done"), new + "done")
            self.assertEqual(BACK.run_text(new + "done"), old + "done")
            self.assertEqual(BACK.run_text(old + "done"), old + "done")

    def test_run_tables(self) -> None:
        self.assertEqual(
            FORWARD.run_text(
                shape(
                    "{old} Codex skills provisioning failed: "
                    "{old} skills source directory is unavailable."
                )
            ),
            "Codex skills provisioning failed: Skills source directory is unavailable.",
        )
        payload = {
            "tool_results": [
                {
                    "label": "Verification",
                    "value": shape("{old} worker runtime accepted this session."),
                }
            ],
            "n": 1,
        }
        renamed = {
            "tool_results": [
                {"label": "Verification", "value": "Onix's runtime accepted this session."}
            ],
            "n": 1,
        }
        self.assertEqual(FORWARD.payload(payload), renamed)
        self.assertEqual(BACK.payload(renamed), payload)
        self.assertEqual(BACK.payload(payload), payload)

    def test_code_ranges(self) -> None:
        def code_text(content: str, *, inline: bool = True) -> list[str]:
            return [
                content[start:end] for start, end in migration.code_ranges(content, inline=inline)
            ]

        self.assertEqual(code_text("    indented\nnot a paragraph's end"), ["    indented\n"])
        self.assertEqual(code_text("text\n    continues the paragraph"), [])
        self.assertEqual(
            code_text("text\n\n\tcode\n\n    more code\nafter"),
            ["\tcode\n", "\n", "    more code\n"],
        )
        self.assertEqual(
            code_text("```\ncode\n```\n    code after a fence"),
            ["```\n", "code\n", "```\n", "    code after a fence"],
        )
        self.assertEqual(
            code_text("```quote\n    code in a quote\n```"),
            ["```quote\n", "    code in a quote\n", "```"],
        )
        self.assertEqual(code_text("a `b` c", inline=False), [])
        self.assertEqual(code_text("a `b` c"), ["`b`"])

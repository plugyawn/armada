import html
import re
from collections.abc import Callable, Iterable
from functools import reduce
from operator import or_
from typing import Any

from django.conf import settings
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import Q, TextField
from django.db.models.functions import Cast

# What people read about the agent, renamed with the code that writes
# it: (old, new).  The names the server looks up moved in 0811.
AGENT_NAME = ("Pear", "Onix")
AGENT_EMAIL_LOCAL_PART = ("pear-bot", "onix-bot")

# The server's fixed phrases, as the agent's bot wrote them into its own
# messages.  Each is matched only where it fills a whole line, so prose
# that merely contains the words is left alone, and the reverse undoes
# what the forward rewrote unless the agent itself wrote a line that is
# exactly one of the new phrases.
LINE_PHRASES = [
    ("Pear worker failed before posting progress.", "Onix failed before posting progress."),
    ("Pear worker failed after posting progress.", "Onix failed after posting progress."),
    ("Pear worker failed.", "Onix failed."),
    ("Pear worker cancelled.", "Stopped."),
    ("Pear worker is not starting.", "Onix is not starting."),
    (
        "Pear worker completed without a visible response.",
        "Onix completed without a visible response.",
    ),
    ("Pear needs your input to continue:", "Onix needs your input to continue:"),
    ("Pear monitor setup started.", "Monitor setup started."),
    (
        "Pear couldn't determine whether that message was meant for it. "
        "Address it as `@Pear` to bypass ambient routing.",
        "Onix couldn't determine whether that message was meant for it. "
        "Address it as `@Onix` to bypass ambient routing.",
    ),
]

# What the server wrote as the reason a run did not start or the error
# it failed with: in the agent's "Reason:" and "Error:" lines, in the
# run tables, and inside one another ("Codex skills provisioning failed:
# <the skills error>").  "{}" stands for a part that varies within the
# line.
ERROR_TEXTS = [
    ("Pear worker is not ready.", "Onix is not ready."),
    ("Pear stream is not accessible.", "The channel is not accessible."),
    (
        "Pear session is paused until a resume control is posted.",
        "The session is paused until a resume control is posted.",
    ),
    ("Pear session is paused.", "The session is paused."),
    (
        "No Pear request has been posted in this topic.",
        "No request for Onix has been posted in this topic.",
    ),
    (
        "Pear Codex runs currently require a channel topic.",
        "Codex runs currently require a channel topic.",
    ),
    ("Pear could not identify the requesting user.", "Could not identify the requesting user."),
    (
        "This user is not allowed to start Pear worker runs.",
        "This user is not allowed to start Onix's runs.",
    ),
    (
        "This user is not allowed to steer Pear worker runs.",
        "This user is not allowed to steer Onix's runs.",
    ),
    ("This topic already has an active Pear worker run.", "This topic already has an active run."),
    (
        "Pear already has the maximum number of active worker runs in this realm.",
        "Onix already has the maximum number of active runs in this realm.",
    ),
    (
        "Pear's run store does not support native side conversations.",
        "The run store does not support native side conversations.",
    ),
    (
        "Pear cannot start a side conversation without Codex app-server.",
        "Onix cannot start a side conversation without Codex app-server.",
    ),
    (
        "Pear's configured runtime does not support native side conversations.",
        "Onix's configured runtime does not support native side conversations.",
    ),
    ("Pear worker runtime accepted this session.", "Onix's runtime accepted this session."),
    (
        "Pear refused to start Codex because no isolated topic worktree was selected.",
        "Refused to start Codex because no isolated topic worktree was selected.",
    ),
    (
        "Pear refused to start Codex outside the Pear topic workspace root.",
        "Refused to start Codex outside the topic workspace root.",
    ),
    (
        "Pear per-topic OS-user isolation is enabled, but no isolation helper is configured.",
        "Per-topic OS-user isolation is enabled, but no isolation helper is configured.",
    ),
    (
        "Pear per-topic OS-user isolation is enabled, but sudo or the isolation helper is "
        "not installed.",
        "Per-topic OS-user isolation is enabled, but sudo or the isolation helper is "
        "not installed.",
    ),
    (
        "Pear per-topic OS-user isolation refused to prepare a workdir outside the Pear "
        "topic workspace root.",
        "Per-topic OS-user isolation refused to prepare a workdir outside the topic "
        "workspace root.",
    ),
    (
        "Pear per-topic OS-user isolation refused to launch from a shared Git worktree.",
        "Per-topic OS-user isolation refused to launch from a shared Git worktree.",
    ),
    (
        "Pear per-topic OS-user isolation setup failed: ",
        "Per-topic OS-user isolation setup failed: ",
    ),
    ("Pear Codex skills provisioning failed: ", "Codex skills provisioning failed: "),
    ("Pear managed-skills manifest ", "Managed-skills manifest "),
    ("Pear skills source ", "Skills source "),
    ("Pear worker agent runtime is unsupported: ", "The agent runtime is unsupported: "),
    (
        "message routing with PEAR_ADDRESSING_ROUTER_RUNTIME.",
        "message routing with ARMADA_ADDRESSING_ROUTER_RUNTIME.",
    ),
    (
        "Pear worker agent runtime is disabled. Set PEAR_WORKER_AGENT_RUNTIME ",
        "The agent runtime is disabled. Set ARMADA_WORKER_AGENT_RUNTIME ",
    ),
    ("Pear worker run {} has no active claim attempt.", "Run {} has no active claim attempt."),
    ("Pear worker run {} attempt {} lost its lease.", "Run {} attempt {} lost its lease."),
    (
        "Pear worker run for message {} lost attempt {}.",
        "Run for message {} lost attempt {}.",
    ),
    ("Pear worker is shutting down.", "The worker is shutting down."),
    (
        "Pear side run trigger message {} no longer exists.",
        "Side run trigger message {} no longer exists.",
    ),
    (
        "Pear has too many active native side conversations.",
        "Too many active native side conversations.",
    ),
    (
        "Codex thread {} already has an active Pear turn.",
        "Codex thread {} already has an active agent turn.",
    ),
    (
        "Pear side conversation has no durable message root.",
        "Side conversation has no durable message root.",
    ),
    ("Pear active session loader failed.", "Active session loader failed."),
    (
        "Pear progress message send returned no message id.",
        "Progress message send returned no message id.",
    ),
    (
        "Pear artifact could not be attached to the result message.",
        "The artifact could not be attached to the result message.",
    ),
    (
        "Pear message routing requires an injected worker dispatcher",
        "Message routing requires an injected worker dispatcher",
    ),
    ("Invalid Pear response policy: ", "Invalid response policy: "),
]

# The notices the notification bot posts when a topic or channel with a
# workspace moves.  Each starts its message, which is how it is found.
NOTICE_STARTS = [
    ("Pear workspace warning: ", "Workspace warning: "),
    ("Pear workspace label moved ", "The workspace label moved "),
    ("Pear wall moved ", "The wall moved "),
    ("Pear channel context renamed ", "Channel context renamed "),
]
NOTICE_TEXTS = [
    (
        "Pear workspace warning: I did not move the Pear workspace context because the "
        "destination topic already has its own Pear context.",
        "Workspace warning: I did not move the workspace context because the "
        "destination topic already has its own context.",
    ),
    (
        "Pear workspace warning: I did not move the Pear workspace context because only "
        "part of the topic was moved.",
        "Workspace warning: I did not move the workspace context because only "
        "part of the topic was moved.",
    ),
    (
        "this topic will get a fresh context when Pear is used.",
        "this topic will get a fresh context when Onix is used.",
    ),
    (
        "Pear workspace warning: I did not rename the Pear workspace context because the "
        "old topic still has an active Pear worker run. Wait for it to finish, then ask "
        "Pear to pin or migrate the workspace explicitly.",
        "Workspace warning: I did not rename the workspace context because the "
        "old topic still has an active run. Wait for it to finish, then ask "
        "Onix to pin or migrate the workspace explicitly.",
    ),
    ("Pear workspace label moved with this topic.", "The workspace label moved with this topic."),
    (
        "Pear wall moved {} monitor(s) with this topic.",
        "The wall moved {} monitor(s) with this topic.",
    ),
    (
        "Pear workspace warning: I did not rename Pear channel context because the "
        "destination channel name already has Pear context.",
        "Workspace warning: I did not rename the channel context because the "
        "destination channel name already has its own context.",
    ),
    ("Pear channel context renamed from ", "Channel context renamed from "),
]

# The same phrases in the run tables: whole values only.
RUN_TEXT = [
    ("Running Pear worker.", "Working."),
    ("Started Pear worker runtime.", "Started the agent runtime."),
    ("Pear worker returned results.", "The run returned results."),
    ("Queued Pear worker run.", "Queued the run."),
    ("Re-queued Pear worker run.", "Re-queued the run."),
    ("Claimed Pear worker run.", "Claimed the run."),
    ("Recovered stale Pear worker run.", "Recovered a stale run."),
    ("Dropped duplicate Pear worker delivery.", "Dropped a duplicate delivery."),
    ("Cancelled from the Pear panel.", "Cancelled from the run panel."),
    ("Cancelled after Not for Pear feedback.", "Cancelled after Not for Onix feedback."),
    ("Not for Pear.", "Not for Onix."),
    ("Pear worker run became stale and was recovered.", "The run became stale and was recovered."),
    ("Pear topic is paused.", "The topic is paused."),
    (
        "Pear worker run exceeded its stale-claim retry limit.",
        "The run exceeded its stale-claim retry limit.",
    ),
]
# Run event summaries that end in a status: (old prefix, new prefix).
RUN_EVENT_PREFIXES = [
    ("Recorded Pear worker status: ", "Recorded run status: "),
    ("Rejected Pear worker status transition: ", "Rejected run status transition: "),
]


EMBEDDED_BOT = 4
AGENT_SERVICE = "armada"
BATCH_SIZE = 500

# Where a paragraph or a line of one starts and ends in rendered HTML.
RENDERED_LINE_START = r"(?:(?<=<p>)|(?<=<br>\n))"
RENDERED_LINE_END = r"(?=</p>|<br>)"

# Zulip's fenced block opening line (FENCE_RE in
# zerver/lib/markdown/fenced_code.py); quote and spoiler blocks hold
# Markdown, every other block holds code.
FENCE_OPENING = re.compile(
    r"^(?P<fence>~{3,}|`{3,})[ ]*"
    r"(?:\{?\.?(?P<lang>[a-zA-Z0-9_+\-./#]+)[ ]*(?:[^ ~`][^~`]*)?\}?)?$"
)
MARKDOWN_FENCES = {"quote", "quoted", "spoiler"}
# A line of Markdown's indented code block, which starts after a blank line.
INDENTED_CODE = re.compile(r"(?: {4}|\t)")
INLINE_CODE = re.compile(r"(?<![\\`])(`+)(?!`)(?:(?!\n[ \t]*\n).)+?(?<!`)\1(?!`)", re.DOTALL)
# Where a mention may start (BEFORE_MENTION_ALLOWED_REGEX in
# zerver/lib/mention.py): at the start of the text, or after whitespace,
# a quote, an opening bracket, a slash or "<".
MENTION_START = re.compile(r"(?<![^\s\'\"\(\{\[\/<])@")
# What a message's rendering shows as code.
RENDERED_CODE = r"<code(?: [^>]*)?>(?P<code>.*?)</code>"

# Rewrites a text, and says how many phrases it rewrote.
Rewrite = Callable[[str], tuple[str, int]]


def rendered_form(text: str) -> str:
    """The HTML Zulip renders for one of the plain phrases above."""
    return re.sub(r"`([^`]*)`", r"<code>\1</code>", html.escape(text, quote=False))


def template_rewrite(old: str, new: str) -> Rewrite:
    """Rewrites a phrase in which "{}" stands for a varying part,
    writing the new phrase around the same parts.

    Where the old phrase is part of the new one ("Codex skills
    provisioning failed: " in "Pear Codex skills provisioning failed: ",
    on the way back), it is left alone where the new phrase already
    stands, so that a second pass changes nothing."""
    pattern = "([^\n]*?)".join(re.escape(part) for part in old.split("{}"))
    before, found, after = new.partition(old)
    if found and before:
        pattern = f"(?<!{re.escape(before)})" + pattern
    if found and after:
        pattern += f"(?!{re.escape(after)})"
    compiled = re.compile(pattern)
    new_parts = new.split("{}")

    def replace(match: re.Match[str]) -> str:
        return new_parts[0] + "".join(
            match[index] + part for index, part in enumerate(new_parts[1:], 1)
        )

    return lambda text: compiled.subn(replace, text)


def fragment_rewrite(pairs: Iterable[tuple[str, str]], *, rendered: bool) -> Rewrite:
    form: Callable[[str], str] = rendered_form if rendered else str
    rewrites = [template_rewrite(form(old), form(new)) for old, new in pairs]

    def rewrite(text: str) -> tuple[str, int]:
        total = 0
        for each in rewrites:
            text, count = each(text)
            total += count
        return text, total

    return rewrite


def holds_any(field: str, pairs: Iterable[tuple[str, str]]) -> Q:
    """Rows whose field holds the longest fixed part of an old phrase."""
    return reduce(
        or_,
        (Q(**{f"{field}__contains": max(old.split("{}"), key=len)}) for old, new in pairs),
    )


def code_ranges(content: str, *, inline: bool = True) -> list[tuple[int, int]]:
    """Where Zulip's Markdown shows a message's text as code: the lines of
    fenced and indented code blocks and, with inline, inline code spans.
    An approximation of the renderer; what relies on it checks its answer
    against the rendering."""
    ranges = []
    open_fences: list[tuple[str, bool]] = []
    masked = []
    offset = 0
    after_blank = True
    in_indented_code = False
    for line in content.splitlines(keepends=True):
        start, offset = offset, offset + len(line)
        in_fenced_code = bool(open_fences) and open_fences[-1][1]
        closes = bool(open_fences) and line.rstrip() == open_fences[-1][0]
        opening = None if in_fenced_code or closes else FENCE_OPENING.match(line.rstrip("\n"))
        if closes:
            open_fences.pop()
        elif opening is not None:
            is_code = (opening["lang"] or "").lower() not in MARKDOWN_FENCES
            open_fences.append((opening["fence"], is_code))
            in_fenced_code = in_fenced_code or is_code
        fence_line = closes or opening is not None
        blank = not line.strip()
        if in_fenced_code or fence_line:
            in_indented_code = False
        elif not blank:
            # Zulip ends a paragraph before and after a fenced block, so
            # a fence line counts as a blank one.
            in_indented_code = bool(INDENTED_CODE.match(line)) and (after_blank or in_indented_code)
        after_blank = blank or fence_line
        if in_fenced_code or fence_line or in_indented_code:
            ranges.append((start, offset))
            masked.append(re.sub(r"[^\n]", " ", line))
        else:
            masked.append(line)
    if inline:
        ranges.extend(match.span() for match in INLINE_CODE.finditer("".join(masked)))
    return ranges


def same_case(template: str, name: str) -> str:
    """name in the letter case of template, letter by letter: the two
    names have the same length, so the reverse restores the original."""
    return "".join(
        new.upper() if old.isupper() else new.lower()
        for old, new in zip(template, name, strict=True)
    )


def rename_mentions(
    content: str, rendered: str, bot_id: int, old_name: str, new_name: str
) -> tuple[str, str] | None:
    """A message's mentions of the agent's bot, in its text and in the
    mention spans they render to.  Zulip resolves `@**Name**` with the
    name in any letter case, `@**Name|id**` with the name exactly as it
    is, and `@**|id**`, each also silent (`@_**…**`), and only where a
    mention may start (zerver/lib/mention.py).  Mention syntax inside
    code is the person's own text and stays.

    code_ranges only approximates where the code is, so its answer is
    checked against the rendering: in order, every piece of mention
    syntax outside code must be a mention span there, and every piece
    inside code must be code there.  When they disagree, nothing is
    rewritten and the answer is None.  Both names are recognised and
    only the old one is rewritten, so a second pass changes nothing."""
    names = f"{re.escape(old_name)}|{re.escape(new_name)}"
    syntax = re.compile(
        rf"@_?\*\*(?:(?P<name>(?i:{names}))|(?P<exact_name>{names})\|{bot_id}|\|{bot_id})\*\*"
    )
    spans_and_code = re.compile(
        rf'<span class="user-mention(?: silent)?" data-user-id="{bot_id}">@?'
        rf"(?P<name>(?i:{names}))</span>|{RENDERED_CODE}",
        re.DOTALL,
    )
    code = code_ranges(content)
    in_text: list[tuple[str, re.Match[str] | None]] = []
    for match in syntax.finditer(content):
        if any(start <= match.start() < end for start, end in code):
            in_text.append(("code", match))
        elif MENTION_START.match(content, match.start()):
            in_text.append(("mention", match))
    in_rendering: list[tuple[str, re.Match[str] | None]] = []
    for match in spans_and_code.finditer(rendered):
        if match["code"] is None:
            in_rendering.append(("mention", match))
        else:
            shown = html.unescape(re.sub(r"<[^>]*>", "", match["code"]))
            in_rendering.extend(("code", None) for _ in syntax.finditer(shown))
    if [kind for kind, match in in_text] != [kind for kind, match in in_rendering]:
        return None

    def rename(text: str, events: list[tuple[str, re.Match[str] | None]]) -> str:
        for kind, match in reversed(events):
            group = None if match is None else match.lastgroup
            if kind == "mention" and match is not None and group is not None:
                name = match[group]
                if name.lower() == old_name.lower():
                    text = (
                        text[: match.start(group)]
                        + same_case(name, new_name)
                        + text[match.end(group) :]
                    )
        return text

    return rename(content, in_text), rename(rendered, in_rendering)


def rewrite_json(value: Any, rewrite: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return rewrite(value)
    if isinstance(value, list):
        return [rewrite_json(item, rewrite) for item in value]
    if isinstance(value, dict):
        return {key: rewrite_json(item, rewrite) for key, item in value.items()}
    return value


class AgentDataRewrite:
    """What this migration rewrites, one way: to the new names, or back."""

    def __init__(self, *, to_new_names: bool) -> None:
        def pick(pair: tuple[str, str]) -> tuple[str, str]:
            return pair if to_new_names else (pair[1], pair[0])

        self.old_name, self.new_name = pick(AGENT_NAME)
        self.old_email_local_part, self.new_email_local_part = pick(AGENT_EMAIL_LOCAL_PART)
        self.line_phrases = [pick(pair) for pair in LINE_PHRASES]
        self.error_texts = [pick(pair) for pair in ERROR_TEXTS]
        self.notice_starts = [pick(pair) for pair in NOTICE_STARTS]
        notice_texts = [pick(pair) for pair in NOTICE_TEXTS]
        self.whole_values = dict(pick(pair) for pair in RUN_TEXT)
        self.prefixes = [pick(pair) for pair in RUN_EVENT_PREFIXES]
        self.errors = {
            rendered: fragment_rewrite(self.error_texts, rendered=rendered)
            for rendered in (False, True)
        }
        self.notices = {
            rendered: fragment_rewrite(notice_texts, rendered=rendered)
            for rendered in (False, True)
        }

    def mentions_of(self, bot_id: int) -> Callable[[str, str | None], tuple[str, str] | None]:
        """Rewrites a message that mentions the agent's bot, as
        rename_mentions says."""

        def rewrite(content: str, rendered: str | None) -> tuple[str, str] | None:
            if rendered is None:
                return None
            return rename_mentions(content, rendered, bot_id, self.old_name, self.new_name)

        return rewrite

    def agent_lines(self, text: str, *, rendered: bool) -> tuple[str, int]:
        """The fixed phrases that fill a line of the agent's message, and
        the reasons and errors in its "Reason:" and "Error:" lines."""
        form: Callable[[str], str] = rendered_form if rendered else str
        start = RENDERED_LINE_START if rendered else "(?m)^"
        end = RENDERED_LINE_END if rendered else "$"
        total = 0
        for old, new in self.line_phrases:
            # A replacement string reads backslashes as escapes.
            text, count = re.subn(
                start + re.escape(form(old)) + end, form(new).replace("\\", "\\\\"), text
            )
            total += count
        errors = self.errors[rendered]

        def replace(match: re.Match[str]) -> str:
            nonlocal total
            reason, count = errors(match[2])
            total += count
            return match[1] + reason

        line_rest = r"([^\n]*?)" if rendered else "(.*)"
        text = re.sub(start + r"((?:Error|Reason): )" + line_rest + end, replace, text)
        return text, total

    def agent_message(self, content: str, rendered: str | None) -> tuple[str, str | None] | None:
        """One of the agent's own messages: its fixed phrases outside code
        blocks, which its rendering shows as paragraphs.  None when the
        text and the rendering do not hold the same number of them, so
        that neither is rewritten."""
        pieces = []
        count = 0
        offset = 0
        for start, end in [*code_ranges(content, inline=False), (len(content), len(content))]:
            text, found = self.agent_lines(content[offset:start], rendered=False)
            pieces += [text, content[start:end]]
            count += found
            offset = max(offset, end)
        content = "".join(pieces)
        if rendered is None:
            return content, None
        rendered, found_in_rendering = self.agent_lines(rendered, rendered=True)
        if found_in_rendering != count:
            return None
        return content, rendered

    def notice(self, content: str, rendered: str | None) -> tuple[str, str | None] | None:
        """One of the notification bot's workspace notices.  None when the
        text and the rendering do not hold the same number of phrases."""
        content, count = self.notices[False](content)
        if rendered is None:
            return content, None
        rendered, found_in_rendering = self.notices[True](rendered)
        if found_in_rendering != count:
            return None
        return content, rendered

    def run_text(self, text: str) -> str:
        """An activity, summary or error the server wrote into a run table."""
        text = self.whole_values.get(text, text)
        for old_prefix, new_prefix in self.prefixes:
            if text.startswith(old_prefix):
                text = new_prefix + text.removeprefix(old_prefix)
        return self.errors[False](text)[0]

    def payload(self, value: Any) -> Any:
        """A run event's payload: the errors in its values."""
        return rewrite_json(value, lambda text: self.errors[False](text)[0])


def rename_agent_data(apps: StateApps, *, to_new_names: bool) -> None:
    Service = apps.get_model("zerver", "Service")
    UserProfile = apps.get_model("zerver", "UserProfile")
    Message = apps.get_model("zerver", "Message")
    AgentRun = apps.get_model("zerver", "AgentRun")
    AgentRunEvent = apps.get_model("zerver", "AgentRunEvent")
    AgentMonitor = apps.get_model("zerver", "AgentMonitor")

    rewrite = AgentDataRewrite(to_new_names=to_new_names)
    old_name, new_name = rewrite.old_name, rewrite.new_name
    old_local, new_local = rewrite.old_email_local_part, rewrite.new_email_local_part

    bot_ids = list(
        Service.objects.filter(name=AGENT_SERVICE, user_profile__bot_type=EMBEDDED_BOT).values_list(
            "user_profile_id", flat=True
        )
    )

    for bot in UserProfile.objects.filter(id__in=bot_ids):
        update_fields = []
        if bot.full_name == old_name:
            bot.full_name = new_name
            update_fields.append("full_name")
        local, at, domain = bot.email.partition("@")
        if at and local == old_local:
            new_email = f"{new_local}@{domain}"
            taken = (
                UserProfile.objects.filter(realm_id=bot.realm_id)
                .filter(Q(email__iexact=new_email) | Q(delivery_email__iexact=new_email))
                .exclude(id=bot.id)
                .exists()
            )
            if taken:
                print(f"\nKept {bot.email} for bot {bot.id}: {new_email} is taken.")
            else:
                if bot.delivery_email == bot.email:
                    bot.delivery_email = new_email
                    update_fields.append("delivery_email")
                bot.email = new_email
                update_fields.append("email")
        if update_fields:
            bot.save(update_fields=update_fields)

    def save_changed(
        messages: Iterable[Any],
        rewrite_message: Callable[[str, str | None], tuple[str, str | None] | None],
    ) -> list[int]:
        """Saves the messages the rewrite changed, in batches, and returns
        the ids of those it left whole."""
        left_whole = []
        changed = []
        for message in messages:
            rewritten = rewrite_message(message.content, message.rendered_content)
            if rewritten is None:
                left_whole.append(message.id)
            elif rewritten != (message.content, message.rendered_content):
                message.content, message.rendered_content = rewritten
                changed.append(message)
            if len(changed) == BATCH_SIZE:
                Message.objects.bulk_update(changed, ["content", "rendered_content"])
                changed = []
        Message.objects.bulk_update(changed, ["content", "rendered_content"])
        return left_whole

    # Mentions of the agent, in anyone's messages.
    for bot_id in bot_ids:
        mentioning = Message.objects.filter(rendered_content__contains=f'data-user-id="{bot_id}"')
        left_whole = save_changed(
            mentioning.iterator(chunk_size=BATCH_SIZE), rewrite.mentions_of(bot_id)
        )
        if left_whole:
            print(
                f"\nLeft the mentions of bot {bot_id} in messages {left_whole}: "
                "their text and rendering disagree."
            )

    # The agent's own fixed phrases, in the messages its bot sent.
    sent = Message.objects.filter(sender_id__in=bot_ids).filter(
        holds_any("content", [*rewrite.line_phrases, *rewrite.error_texts])
    )
    left_whole = save_changed(sent.iterator(chunk_size=BATCH_SIZE), rewrite.agent_message)
    if left_whole:
        print(f"\nLeft the agent's messages {left_whole}: their text and rendering disagree.")

    # The workspace notices, in the notification bot's messages.
    notification_bots = UserProfile.objects.filter(
        realm__string_id=settings.SYSTEM_BOT_REALM, email__iexact=settings.NOTIFICATION_BOT
    )
    notices = Message.objects.filter(sender__in=notification_bots).filter(
        reduce(or_, (Q(content__startswith=old) for old, new in rewrite.notice_starts))
    )
    left_whole = save_changed(notices.iterator(chunk_size=BATCH_SIZE), rewrite.notice)
    if left_whole:
        print(f"\nLeft the notices {left_whole}: their text and rendering disagree.")

    # The run tables: activities, summaries, errors and event payloads
    # the server wrote.
    run_pairs = [*rewrite.whole_values.items(), *rewrite.prefixes, *rewrite.error_texts]
    for model, fields in (
        (AgentRun, ["summary", "current_activity", "error"]),
        (AgentRunEvent, ["summary"]),
        (AgentMonitor, ["last_error"]),
    ):
        for field in fields:
            rows = list(model.objects.filter(holds_any(field, run_pairs)))
            for row in rows:
                setattr(row, field, rewrite.run_text(getattr(row, field)))
            model.objects.bulk_update(rows, [field], batch_size=BATCH_SIZE)

    events = list(
        AgentRunEvent.objects.annotate(payload_text=Cast("payload", TextField())).filter(
            holds_any("payload_text", rewrite.error_texts)
        )
    )
    for event in events:
        event.payload = rewrite.payload(event.payload)
    AgentRunEvent.objects.bulk_update(events, ["payload"], batch_size=BATCH_SIZE)


def rename_agent_data_to_new_names(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    rename_agent_data(apps, to_new_names=True)


def rename_agent_data_to_old_names(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    rename_agent_data(apps, to_new_names=False)


class Migration(migrations.Migration):
    # Batched, so not one long transaction.  Each step rewrites only what
    # still carries the other name, so running it again after a failure
    # finishes the job.
    atomic = False

    dependencies = [
        ("zerver", "0811_rename_agent_stored_names"),
    ]

    operations = [
        migrations.RunPython(
            rename_agent_data_to_new_names,
            reverse_code=rename_agent_data_to_old_names,
            elidable=True,
        ),
    ]

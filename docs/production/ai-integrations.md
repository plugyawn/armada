# AI integrations

Zulip’s [topics](https://zulip.com/help/introduction-to-topics) organize
conversations within a channel, which is ideal for integrating with AI systems
and collaborating with AI agents. With Zulip's structure, it's easy to prompt an AI
with the appropriate context for what you want to accomplish.

You can connect your AI models of choice with Zulip using the [interactive bots
API](https://zulip.com/api/running-bots), which makes it convenient to have AI
models participate in conversations in whatever ways your organization finds
most effective as the technology evolves.

Future Zulip releases will also contain built-in AI features, such as topic
summarization. A major advantage of self-hosting your team chat system
in the age of AI is that you maintain full control over your internal
communications. It’s up to you how you allow third parties and AI models to
process messages.

Zulip Server 10.x includes a beta topic summarization feature that is available
for testing and experimentation. We appreciate any
[feedback](../contributing/suggesting-features.md)
on your experience, and what configuration options and additional features your
organization would find useful.

## Built-in AI features

### Pear pair-programming sessions

Pear uses Zulip's channel and topic model as the shared work surface for
pair-programming with an AI agent. The initial Pear contract reserves the
`pair-programming` channel and `pear/` topic prefix for agent-assisted coding
sessions. A typical session starts by mentioning `@Pear` in the
`pair-programming > pear/session` topic with a concrete coding task; Pear then
keeps its checklist and verification evidence in that same topic so the whole
team can steer and review the work.

The paired terminal client uses the same defaults and provides a `Meta p`
shortcut for opening a seeded Pear handoff message, plus `Meta P` for viewing
the shared Pear session checklist. Keep the server and terminal defaults
synchronized with the Pear compatibility check in the parent workspace.
Developers can ask `@Pear What can you access from this topic?`, or use
zulip-terminal's `Meta i` shortcut, to get an in-topic summary of Pear's topic
context, worker readiness, Codex runtime boundary, and terminal artifact actions
without starting a coding run.
Developers can use `Meta M`, or post `@Pear Remember:` with a `Memory:` line,
to record channel-scoped project facts and team preferences in the same topic.
The embedded bot acknowledges memory notes without starting a worker run, and
active-session summaries expose memory count and latest memory fields. The Pear
worker prompt also surfaces recorded memory in a dedicated channel-memory
section, so later Codex turns can use those topic preferences directly.
Developers can use `Meta L`, or ask `@Pear What do you remember about this
topic?`, to list recorded memory in-thread without starting a worker run.
Developers can use `Meta D`, or post `@Pear Definition of done:` with a
`Done:` line, to record success criteria in the shared topic. The embedded bot
acknowledges these done-criteria notes without starting a worker run, and the
Pear worker prompt includes them in a dedicated definition-of-done section for
later Codex turns.
Developers can use `Meta A`, or post `@Pear Response policy:` with an
`Ambient:` line, to control whether Pear runs on ordinary follow-up replies in
the active topic. The embedded bot acknowledges response-policy notes without
starting a worker run; `Ambient: mentions only` keeps Pear quiet until someone
explicitly mentions it again.
Developers can use `Meta K`, or post `@Pear Routine cadence:` with a `Routine
cadence:` line, to record whether standing routines are manual or should be
considered during future ambient watch windows. The embedded bot acknowledges
cadence notes without starting a worker run; this is durable topic metadata, not
a claim that a scheduler is already active.
Developers can use `Meta H`, or post `@Pear Disable routine:` with a
`Routine disabled:` line, to disable a standing routine without editing topic
history. Pear suppresses matching older `Routine:` artifacts from active
routine lists, while a newer matching `Routine:` line re-enables that routine.
Developers can use `Meta w` in zulip-terminal to post `Repo:`, `Branch:`, and
`Runtime:` artifact lines for the current Git checkout, making the shared topic
record the repository scope and server-selected Codex runtime boundary before
work starts.
When Pear posts a `Branch:` artifact, zulip-terminal can check it out, open a
compare page, or create a draft GitHub pull request with the GitHub CLI so the
agent's work moves directly into code review. The terminal reuses existing open
PRs for the branch and pushes unpublished local branches to `origin` before PR
creation. Created or reused PRs are recorded back to the Pear topic as `PR:`
artifact lines, keeping the shared Zulip thread as the source of truth for
review links; if the same branch already has a newer PR artifact, the terminal
edits that line in place instead of posting duplicates. The terminal can mark
the latest PR or branch artifact ready for review with `gh pr ready`, then post
a `Verification:` artifact for that transition. The terminal can also post a
`Verification:` artifact for the latest PR state, mergeability, review decision,
review requests, and GitHub check buckets with `gh pr view --json` and
`gh pr checks --json`. If the Pear topic includes a `Reviewers:` artifact, the
terminal can request those reviewers on the latest PR or branch with
`gh pr edit --add-reviewer` and post a verification artifact for the request.
It can also record a `Routine:` artifact for watching the latest PR or branch,
keeping standing follow-up intent in the shared topic while actual execution
continues to use the configured Codex runtime. Active-session summaries expose
routine count and latest routine fields so clients can show standing work
without searching the full artifact list. The terminal can run the latest
PR-watch routine on demand and post a fresh `Verification:` artifact with the
current PR check and review state. In Zulip, developers can also ask
`@Pear run routine` or `@Pear Run routine:` to run the latest recorded routine
through the configured Codex runtime; Pear includes the latest recorded cadence
in that worker task. Developers can use `Meta J`, or ask `@Pear What routines
do you have set up in this topic?`, to list recorded routines and the latest
cadence without starting a worker run. Developers can use `Meta H`, or ask
`@Pear disable latest routine`, to record a `Routine disabled:` artifact; the
embedded bot acknowledges the disable record and does not start a worker run.
For prompt-library-style starts, zulip-terminal exposes `Meta F`, which opens a
structured `@Pear Fix bug:` request with fields for observed and expected
behavior, useful files, verification, and workspace context. Posting that
starter is a worker-starting coding request, not a control message. It also
exposes `Meta T`, which opens a structured `@Pear Status check:` request for
what is completed, still in progress, blocked, next, and due for checks. `Meta
E` opens a structured `@Pear Handoff summary:` request for the next developer,
reviewer, or resumed agent turn. `Meta h` opens a structured `@Pear Triage
request:` starter for duplicate checks, settled topic-context answers,
owner/reviewer routing, scope classification, priority/risk, and next action.
`Meta Y` asks Pear to run and repair local verification, while `Meta X` drafts a
ticket, issue, spec, doc, or release note from the active topic.
The server exposes the same startup contract to clients through
`GET /json/pear/session` and `GET /api/v1/pear/session`. It also exposes
`GET /json/pear/session/active` and `GET /api/v1/pear/session/active`, which
summarize the latest checklist state from messages in the durable Pear topic.
The active-session response includes worker readiness fields so a Pear runtime
can tell when a topic is ready for work, paused, missing, or still waiting for a
request. It also includes first-class counts and latest values for recorded
memory, routines, routine cadence, done criteria, response policy, and the
latest `Session:` record. Session records are classified as URL, Codex thread,
or text, with `latest_session_record_url` set when the record can be opened in a
browser, so clients can show an Open session action without parsing generic
artifact text.
After a topic has an active Pear request, ordinary human replies in that same
topic are queued to the selected Pear embedded bot as follow-up worker turns
without requiring another `@Pear` mention. Control messages and artifact-only
posts remain non-worker events, and paused, untouched, or mention-only topics do
not trigger follow-up runs.
The server registers Pear as an embedded bot service so a configured Pear bot
can acknowledge `@Pear` mentions with the first in-thread checklist before the
full agent runtime takes over. The embedded bot dispatches real work to the
dedicated `pear_worker` queue, which posts in-thread startup progress before
invoking Codex, including durable `Runtime:` and `Verification:` artifact lines,
then posts a separate completion message with the configured runtime's
verification artifacts. The worker refuses to run when the active-session
readiness gate says the topic is paused or unavailable. Catch-up requests use
the same worker path to summarize recent
topic changes, decisions, artifacts, blockers, and next actions from durable
topic context. Review requests use that worker path to inspect the current
change for correctness, regressions, test coverage, and integration risk before
a human review request. Implementation-plan requests use the same context while
explicitly asking Pear not to edit files yet, so a team can agree on a plan
before execution. Checkpoint requests ask Pear to post the current branch, diff
summary, changed files, verification, blockers, and next step back into the
topic, then wait unless the topic explicitly says to keep going. Apply-plan
requests ask Pear to use the latest implementation plan in the topic, make the
scoped edits, run or explain verification, and post branch/diff/verification
state back to the thread. Save-work requests ask Pear to preserve current
progress as a branch, draft PR, patch, or explicit diff summary with artifact
lines the topic can recover from before it continues. The worker bridge reads
the durable Zulip topic history before making that decision, so pause/resume
and steering state come from the shared thread rather than only the trigger event. The embedded bot also maps
routine-run requests to the topic's latest `Routine:` and `Routine cadence:`
artifacts, and refuses to start the worker when no routine is recorded or the
readiness gate blocks execution. Routine-status questions are handled as
non-worker audit responses, so teams can see the standing work already recorded
in a topic before adding or running another routine.
Memory-forget controls are also handled as non-worker audit responses:
`@Pear Forget memory:` records a durable `Memory forgotten:` artifact, and
`@Pear forget latest memory` resolves to the latest active `Memory:` line in the
topic. Newer matching `Memory:` lines restore that memory in active views.
The worker uses a standard Codex agent runtime selected by
`PEAR_WORKER_AGENT_RUNTIME`. Prefer `codex-agents-mcp` for first-class tool
usage; it runs through the OpenAI Agents SDK with Codex exposed as an MCP
server. `codex-sdk` runs through the official `openai-codex` Python SDK, which
controls the local Codex app-server over JSON-RPC for direct Codex thread,
model, and sandbox control. `codex-exec` runs the official non-interactive
Codex CLI as a JSONL fallback for command, file-change, tool, and final-message
evidence. The worker prompt gives recorded memory, done criteria, standing
routines, disabled routines, forgotten memory, and routine cadence dedicated
sections before invoking the configured Codex runtime.
The Agents SDK path starts `codex mcp-server`, delegates repository work through
the Codex MCP tools, starts new Codex threads with structured sandbox,
approval-policy, cwd, and model arguments, and posts `Doc: Codex thread ...`
when the MCP response exposes a thread ID. Resumed Codex MCP turns use
`codex-reply` with the recorded thread ID; that documented tool accepts only
`prompt` and `threadId`, so runtime policy comes from the recorded Codex thread.
Pear wraps the MCP call in a named Agents SDK trace so tool calls and failures
are inspectable in the normal tracing workflow, and the adapter rejects an
Agents SDK turn that finishes without `codex` or `codex-reply` MCP tool-call
evidence. The shared Pear session contract also exposes the preferred official
runtime, required runtime package names, the published Codex SDK package names
(`@openai/codex-sdk` and `openai-codex`), and the Codex MCP tool names for
server/terminal drift checks. The CLI path posts the Codex
thread ID as a `Doc:` artifact and resumes that thread when later Pear turns in
the same Zulip topic include the artifact in topic history. The SDK path uses
Codex threads as well; when the installed SDK exposes a thread resume API, Pear
passes the documented sandbox and model arguments, plus cwd/resume arguments
when the installed SDK method supports them, and posts the thread ID again so
later turns keep the same continuity.
The parent Pear workspace includes `tools/pear_runtime_smoke.py` for live
runtime verification: it sends a uniquely marked `@Pear` request to a Pear
topic, waits for exactly one worker progress post with a `Runtime:` artifact and
an artifact line, and checks the active-session endpoint for the latest request
and verification.
For local development, `tools/pear_dev_smoke.py` starts Zulip's
`tools/run-dev --test` server with the disabled Pear worker runtime, including
the queue workers required for embedded-bot dispatch and the dedicated
`pear_worker` runtime queue. It provisions the Pear embedded bot and runs that
smoke check without invoking Codex or an external model provider.
For first-class tool orchestration, set
`PEAR_WORKER_AGENT_RUNTIME = "codex-agents-mcp"` and install `openai-agents` in
the worker environment. `Meta Y` in zulip-terminal opens a verify-changes
starter that asks Pear to run checks through that configured runtime, fix
focused failures, and post command/result evidence. `Meta X` opens a
draft-artifact starter that asks Pear to turn the active topic into a ticket,
issue, spec, doc, or release note, with `Doc:`, `Ticket:`, and `Issue:` artifact
lines recognized as durable outputs. For a local Codex CLI
worker, set
`PEAR_WORKER_AGENT_RUNTIME = "codex-exec"` and `PEAR_WORKER_WORKDIR` to the
Pear control-plane checkout. Normal Codex runs execute from isolated workdirs
under `PEAR_TOPIC_WORKDIR_ROOT`, not from the host Zulip checkout. Use
`PEAR_WORKER_SANDBOX = "workspace-write"` for topic work. Pear downgrades
`danger-full-access` for topic workdirs and refuses to launch Codex when the
resolved workdir is outside the Pear topic workspace root.

For hosted friend/multi-user servers, set
`PEAR_TOPIC_ISOLATION_BACKEND = "os-user"` and provision
`PEAR_TOPIC_ISOLATION_HELPER` as the root-owned `pear-topic-isolate` helper.
That backend creates a stable `pear-<topic digest>` Unix user per Zulip topic,
copies the worker Codex login into that user's private home, chowns the topic
workdir to that user, and launches Codex through `sudo -u <topic user>`.
Topic checkouts in this mode are independent clones/directories. Pear refuses
to launch from a legacy Git worktree whose `.git` file points back into a
shared checkout, because that shares Git admin/object state across topics.

Use
`PEAR_WORKER_APPROVAL_POLICY` and `PEAR_WORKER_MODEL` to make the agent's
command and model policy explicit. The approval-policy setting accepts
currently documented Codex policies: `never`, `on-request`, and `untrusted`.
Pear passes it through the Agents-MCP and CLI runtimes where the official Codex
surfaces expose that setting directly. For the SDK path, install `openai-codex`
in the worker environment and set
`PEAR_WORKER_AGENT_RUNTIME = "codex-sdk"`.
Pear records first-class database worker run rows for trigger message IDs,
tracking queued, running, done, and failed states so duplicate embedded-bot
queue deliveries do not create duplicate worker posts for the same request.
`GET /json/pear/config` and `GET /api/v1/pear/config` report whether embedded
bots are enabled and which active Pear embedded bot identity is selected for the
realm, including all matching Pear bot identities. The payload also includes
`setup_steps`, `worker_agent_runtime`, `worker_runtime_ready`,
`worker_runtime_auth_ready`, `worker_runtime_login_method`,
`worker_runtime_login_target`, `worker_runtime_login_command`, and
`worker_runtime_login_instructions`, so clients can show whether embedded bots,
bot selection, the configured Codex runtime, and worker login are all ready for
topic work. Runtime readiness checks concrete dependencies for the selected
official surface: `openai-agents` plus `codex mcp-server` for
`codex-agents-mcp`, `openai-codex` for `codex-sdk`, and the configured `codex`
executable for `codex-exec`. Runtime login is reported as a first-class setup
method: `codex-exec` and `codex-sdk` use Codex CLI login in the worker
environment, while `codex-agents-mcp` also needs `OPENAI_API_KEY` or
`OPENAI_ADMIN_KEY` for the Agents SDK orchestrator. Administrators can use
`POST /json/pear/config` or `POST /api/v1/pear/config` to provision the default
Pear embedded bot identity through Zulip's standard bot creation path or to
select one existing Pear bot with `selected_bot_user_id`. The organization bot
settings page surfaces the same setup flow so administrators can provision and
select Pear without calling the API manually.

### Data privacy

Making sure customer data is protected is [our highest
priority](https://zulip.com/security/). We don’t train LLMs on Zulip Cloud
customer data, and we have [no plans to do
so](https://blog.zulip.com/2024/05/23/self-hosting-keeps-your-private-data-out-of-ai-models/).

We are committed to keeping Zulip 100% open-source, so the source code that
defines how data is processed is available for third parties to review and
audit.

### General configurations

Self-hosted Zulip installations can choose whether to self-host their own AI
models or use a third-party AI model API provider of their choice. Zulip’s AI
integrations use the [OpenAI Python SDK](https://github.com/openai/openai-python),
which can be pointed at any OpenAI-compatible API endpoint, including those
offered by most popular AI model API providers.

- **Server settings**: You can control costs using `INPUT_COST_PER_GIGATOKEN`,
  `OUTPUT_COST_PER_GIGATOKEN`, and `MAX_PER_USER_MONTHLY_AI_COST` settings,
  which let you set a monthly per-user AI usage budget with whatever pricing is
  appropriate for your selected model.
- **Organization settings**: Administrators can specify who can use each AI
  feature that is enabled by the server. The permission can be assigned to any
  combination of roles, groups, and individual users.
- **Personal settings**: Users who find AI features intrusive or distracting can
  hide them from the UI with a **Hide AI features** personal preference setting.

### Topic summarization beta

:::{note}

Topic summarization is not yet available in Zulip Cloud.

:::

The Zulip server supports generating summaries of topics, with convenient
options for doing so in the web/desktop application’s topic actions menus.

#### How it works

:::{warning}

As with all features powered by LLMs, topic summaries may contain errors and
hallucinations.

:::

The topic summarization feature uses a Zulip-specific prompt with off-the-shelf
third-party large language models.

When a user asks for a summary of a given topic, the Zulip server fetches recent
messages in that conversation that are accessible to the acting user, and sends
them to the AI model to generate a summary.

Emoji reactions, images, and uploaded files are currently not included in what
is sent to the AI model, though some LLMs may have features that might follow
links in content they are asked to summarize. (Note that Zulip’s permissions
model for uploaded files will prevent the LLM from accessing them unless the
files have been posted to a channel with the [public access
option](https://zulip.com/help/public-access-option) enabled.)

#### Enabling topic summarization

:::{important}

If you use a third-party AI platform for topic summarization, you are trusting
the third party with the security and confidentiality of all the messages that
are sent for summarization.

:::

Enable topic summarization by configuring `TOPIC_SUMMARIZATION_MODEL`
and related configuration settings in `/etc/zulip/settings.py`. Topic
summarization and settings for controlling it will appear in the UI
only if your server is configured to enable it.

#### Choosing a model

When modeling the pricing for a given model provider, you’ll primarily want to
look at the cost per input token. Because useful summaries are short compared to
the messages being summarized, more than 90% of tokens used in generating topic
summaries end up being input tokens.

Our experience in early 2025 has been that midsize ~70B parameter models
generate considerably more useful and accurate summaries than smaller ~8B
parameter models.

/* INTERIM BETA (README.md): a plugin's Recent, in words (design/r9/plugins/DESIGN.md §5, ROUND2.md §F). The server keeps
   a line for each thing done to an install (GET /json/armada/plugins/<id>/recent: its type, who did it, when, the
   channel, and what it records); this says it as the design does: "Onix used compare_runs", "Iago linked
   runix/nextlat-scaling to nextlat-scaling", "Started 0.4.0". Names a plugin or a person gives (a tool, a project) are
   code; everything is escaped. Pure; tested by web/tests/armada/beta_plugin_recent.test.cjs. */
/* util.js reads the page when it loads, so this pure module escapes for itself, as util.js's esc does */
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const isObj = (x) => !!x && typeof x === "object" && !Array.isArray(x);
const code = (s) => `<code>${esc(String(s))}</code>`;
/* "a", "a and b", "a, b and c" */
const and = (parts) => (parts.length < 2 ? parts.join("") : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`);

/* what one change to the install's settings did, each part in words */
function changedWords(detail, secretTitle) {
  const parts = [];
  if (detail.settings) parts.push("changed its settings");
  if (Array.isArray(detail.secrets) && detail.secrets.length) parts.push(`set ${and(detail.secrets.map((name) => esc(secretTitle(name))))}`);
  if (isObj(detail.disabled)) parts.push(detail.disabled.new ? "turned it off" : "turned it on");
  if (detail.widget_defaults) parts.push("changed where it shows");
  if (isObj(detail.disabled_tools)) {
    const was = new Set(detail.disabled_tools.old || []), is = new Set(detail.disabled_tools.new || []);
    const off = [...is].filter((tool) => !was.has(tool)), on = [...was].filter((tool) => !is.has(tool));
    if (off.length) parts.push(`turned off ${and(off.map(code))}`);
    if (on.length) parts.push(`turned on ${and(on.map(code))}`);
  }
  return parts.length ? and(parts) : "changed it";
}

/* one line: its words (HTML), and the mark it stands behind (started, or a tool call that failed), else none, for the
   face of whoever did it. `nameOf` names a user; `secretTitle` a secret by its manifest's title; `streamName` a channel. */
function recentLine(line, {nameOf, secretTitle = (name) => name, streamName = () => ""}) {
  const d = isObj(line.detail) ? line.detail : {}, who = line.acting_user_id ? esc(nameOf(line.acting_user_id)) : "";
  const by = (words) => (who ? `${who} ${words}` : words.charAt(0).toUpperCase() + words.slice(1));
  const project = d.value ? code(d.value) : "a project", tool = d.tool ? code(d.tool) : "a tool";
  switch (line.type) {
    case "installed": return {mark: null, words: by(`installed ${d.version ? esc(d.version) : "it"}`)};
    case "made": return {mark: null, words: by(`made ${d.to_version ? esc(d.to_version) : "it"}${d.for_user_id && d.for_user_id !== line.acting_user_id ? ` for ${esc(nameOf(d.for_user_id))}` : ""}`)};
    case "updated": return {mark: null, words: by(d.from_version && d.to_version ? `updated it from ${esc(d.from_version)} to ${esc(d.to_version)}` : "updated it")};
    case "settings_changed": return {mark: null, words: by(changedWords(d, secretTitle))};
    case "linked": { const where = streamName(line.stream_id);
      return {mark: null, words: by(`linked ${project}${where ? ` to ${esc(where)}${d.topic ? ` › ${esc(d.topic)}` : ""}` : ""}`)}; }
    case "unlinked": return {mark: null, words: by(`unlinked ${project}`)};
    case "removed": return {mark: null, words: by("removed it")};
    case "restored": return {mark: null, words: by("put it back")};
    case "tool_called": return {mark: d.is_error ? "failed" : null, words: by(`used ${tool}${d.is_error ? ", which failed" : ""}`)};
    /* its own process came up and pushed: the plugin's line, not a person's */
    case "started": return {mark: "done", words: d.version ? `Started ${esc(d.version)}` : "Started"};
    default: return {mark: null, words: by(esc(String(line.type)).replaceAll("_", " "))};
  }
}

export {recentLine};

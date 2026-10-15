/* INTERIM BETA (README.md): the header: the repository's facts. */

import {$, $$, el, esc, R} from "./util.js";
import {state, streams} from "./state.js";
import {age} from "./time.js";
import {topicLabel} from "./routing.js";
import {nameOf, presOf} from "./faces.js";
import {runsSeen} from "./runs.js";
import {progOf} from "./checklist.js";
import {setTab} from "./views.js";
import {homeRows} from "./home.js";
import {me_} from "./partners.js";
import {pick, pickerHead} from "./partner_picker.js";

/* ---------- the header: the repository's facts, from the worktree's real state ---------- */
function repoPath(url, fallback) { const m = /([^/:]+\/[^/]+?)(?:\.git)?\/?$/.exec(url || ""); return m ? m[1] : fallback; }
function headCfg() { const p = state.place, c = state.P;
  if (p.kind === "partner") return {kind: "plain", path: esc(state.meName), title: "Choose your partner", say: "", checklist: {title: "Your partner"}, seg: pick.seg, has: !!(me_() && me_().species)};
  if (p.kind === "home") { const {needs} = homeRows(), n = needs.filter((x) => x.verb === "Answer").length, live = [...runsSeen.values()].filter((rs) => rs.some((r) => (r.status === "queued" || r.status === "running") && !r.waiting)).length;
    return {kind: "plain", title: "Home", say: (n ? `${n === 1 ? "One question waits" : n + " questions wait"} for you.` : needs.length ? "A run failed." : "Nothing waits for you.") + (live ? ` ${live === 1 ? "One run is" : live + " runs are"} live.` : ""), checklist: {title: "Running"}}; }
  if (p.kind === "dm") { const ids = p.ids.length ? p.ids : [state.ME], one = ids.length === 1 ? ids[0] : 0, pr = one ? presOf(one) : "";
    return {kind: "plain", title: esc(ids.map(nameOf).join(", ")), say: one ? (one === state.ME ? "Notes to yourself." : pr === "on" ? "Here now." : pr === "away" ? "Away." : "Not here just now.") : `${ids.length + 1} people`, checklist: {title: "Together"}}; }
  const s = streams.get(p.stream_id), title = esc(topicLabel(p.topic));
  if (!p.repo) return {kind: "plain", path: esc(s.name), title, say: esc(s.description || "A channel with no repository."), checklist: {title: "People"}};
  const ctx = c.ctx, g = ctx && ctx.git && ctx.git.available ? ctx.git : null, path = esc(repoPath(ctx && (ctx.repository_url || ctx.github_repo), s.name));
  if (ctx && !g) return {kind: "plain", path, title, say: ctx.workspace_uuid ? "The worktree's state is not available." : "No worktree yet. Onix makes one on its first run here.", checklist: progOf(c)};
  const lc = g && g.last_commit, base = (ctx && ctx.base_ref) || "main";
  const distance = !g ? "" : g.ahead && g.behind ? `${g.ahead} ahead, ${g.behind} behind` : g.ahead ? `${g.ahead} ahead of ${esc(base)}` : g.behind ? `${g.behind} behind ${esc(base)}` : `Even with ${esc(base)}`;
  return {path, title, branch: g && !ctx.branch_is_topic_default ? esc(g.branch) : "", dist: distance, commit: {who: lc ? esc(lc.author) : "", subj: lc ? esc(lc.subject) : "", sha: lc ? esc(lc.sha) : "", age: lc ? age(lc.timestamp) : ""}, tab: state.tab, changes: g ? g.changed_count : 0, checklist: progOf(c)}; }
function drawHeader() { const cfg = headCfg(), wt = cfg.kind !== "plain"; R.classList.toggle("wt", wt); if (!wt && state.tab !== "Chat") setTab("Chat", true);
  const sig = JSON.stringify(cfg); if (state.hd && state.hd._sig === sig) return;
  const t = el("template"); t.innerHTML = headHTML(cfg); const neu = t.content.firstElementChild; if (state.place.kind === "partner") pickerHead(neu); state.hd.replaceWith(neu); state.hd = neu; state.hd._sig = sig; host(state.hd); snap(state.hd);
  { const subj = $(".commit .subj", state.hd); if (subj) { let w0 = 0; new ResizeObserver(() => { const w = subj.parentElement.clientWidth; if (w !== w0) { w0 = w; wordcut(subj); } }).observe(subj.parentElement); } }   /* the subject is cut again when the window changes width */
  const sp = $("#side > .hdg"); if (sp) { sp.innerHTML = progHTML(cfg.checklist, "prog-side"); host(sp); }
  $$("[aria-label='More']", state.hd).forEach((b) => (b.dataset.tip = "More: in the old interface for now")); if (state.place.kind !== "partner") $$("a.repo, button.fact.branch, button.fact.commit", state.hd).forEach((b) => (b.dataset.tip = "Not in the beta yet")); $$("#side > .hdg button.prog").forEach((b) => (b.dataset.tip = "The checklist is below")); const pg = $("#prog", state.hd); if (pg) pg.dataset.tip = "The checklist is in the column at 1408 px and wider"; }

export {drawHeader};

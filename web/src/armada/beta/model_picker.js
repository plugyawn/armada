/* INTERIM BETA (README.md): the model and effort picker. */

import {$, $$, el, esc, press, T} from "./util.js";
import {state} from "./state.js";
import {api} from "./connection.js";
import {main, modeBtn} from "./frame.js";
import {drawComposer, note, ready} from "./composer.js";
import {liveRun} from "./runs.js";

/* ---------- the model and effort: a popover from the label, offering what the server's catalog offers for this worktree.
   The choice is the worktree's (POST armada/topic-context, the endpoint today's runtime menu uses). ---------- */
let pop = null;
const FALLBACK_EFFORTS = [{effort: "low", label: "Low"}, {effort: "medium", label: "Medium"}, {effort: "high", label: "High"}, {effort: "xhigh", label: "XHigh"}];
const effortsFor = (ctx, model) => { const e = (ctx.model_catalog || []).find((x) => x.slug === model); return e && e.supported_reasoning_levels && e.supported_reasoning_levels.length ? e.supported_reasoning_levels : ctx.reasoning_efforts && ctx.reasoning_efforts.length ? ctx.reasoning_efforts : FALLBACK_EFFORTS; };
/* a model named the way people say it, from its id: "gpt-6-astra" is "GPT-6 Astra", "gpt-5.6-sol" is "GPT-5.6 Sol" */
const modelName = (slug) => { const w = String(slug || "").split("-").filter(Boolean); if (!w.length) return String(slug || ""); const cap = (x) => (/^\d/.test(x) ? x : x[0].toUpperCase() + x.slice(1));
  return w[0].toLowerCase() === "gpt" && w.length > 1 ? ["GPT-" + w[1], ...w.slice(2).map(cap)].join(" ") : w.map(cap).join(" "); };
const modelsOf = (ctx) => { const cat = ctx.model_catalog || []; const list = cat.map((e) => ({slug: e.slug, name: modelName(e.slug), tiers: Array.isArray(e.service_tiers) ? e.service_tiers : [], dflt: e.default_reasoning_level}));
  if (!list.length) for (const sl of ctx.model_choices || []) list.push({slug: sl, name: modelName(sl), tiers: []}); if (ctx.model && !list.some((x) => x.slug === ctx.model)) list.unshift({slug: ctx.model, name: modelName(ctx.model), tiers: []}); return list; };
const tierNames = (tiers) => tiers.map((t) => (typeof t === "string" ? t : t.name || t.display_name || t.id || "")).filter(Boolean).join(", ");
/* the speed is the server's service tier (ARMADA_WORKER_SERVICE_TIER, one for every worktree), named on a model whose catalog
   offers it: "GPT-6 Astra · Ultrafast · Medium". Codex takes "fast" for its priority tier. */
const tierId = (t) => String(typeof t === "string" ? t : (t && (t.id || t.name || t.display_name)) || "").toLowerCase();
const tierOf = (ctx, model) => { const want = String((state.runtimeCfg && state.runtimeCfg.service_tier) || "").toLowerCase(); if (!want) return "";
  const id = want === "fast" ? "priority" : want, m = modelsOf(ctx).find((x) => x.slug === model), t = m && m.tiers.find((x) => tierId(x) === id || tierId(x) === want);
  return !t ? "" : (typeof t === "object" && (t.display_name || t.name)) || (id === "priority" ? "Fast" : id[0].toUpperCase() + id.slice(1)); };
const modeLabel = (ctx) => { const m = modelsOf(ctx).find((x) => x.slug === ctx.model), e = effortsFor(ctx, ctx.model).find((x) => x.effort === ctx.reasoning_effort), tier = tierOf(ctx, ctx.model); return `${m ? m.name : modelName(ctx.model)}${tier ? " · " + tier : ""} · ${e ? e.label : ctx.reasoning_effort}`; };
function togglePicker(o = {}) { if (pop) { closePicker({back: o.kbd}); return; } const ctx = state.P && state.P.ctx; if (!ctx || !state.place.repo || modeBtn.hidden) { ready(); return; }
  pop = el("div", "pop raised float"); const r = modeBtn.getBoundingClientRect(), mr = main.getBoundingClientRect(); pop.style.cssText = `position:absolute;right:${Math.max(8, Math.round(mr.right - r.right))}px;bottom:${Math.round(mr.bottom - r.top + 6)}px`;
  main.append(pop); drawPop(); modeBtn.classList.add("open"); modeBtn.setAttribute("aria-expanded", "true");
  press(pop, ".mi", (b, e, ptr) => { const ctx_ = state.P.ctx, model = b.dataset.model, ef = effortsFor(ctx_, model); let effort = ctx_.reasoning_effort; if (!ef.some((x) => x.effort === effort)) effort = (modelsOf(ctx_).find((x) => x.slug === model) || {}).dflt || ef[0].effort; saveMode(model, effort); closePicker({back: !ptr}); });
  press(pop, ".steps button", (b) => saveMode(state.P.ctx.model, b.dataset.effort));
  /* the keyboard: Up and Down through the models (and from the last one down to the efforts), Left and Right through the
     efforts, Tab between the two groups, Return or Space to choose, Esc back to the label */
  pop.addEventListener("keydown", (e) => { const t = e.target, rows = $$(".models .mi", pop), steps = $$(".steps button", pop), ri = rows.indexOf(t), si = steps.indexOf(t), cur = (l) => l.find((b) => b.tabIndex === 0) || l[0]; let n = null;
    if (e.key === "ArrowDown") n = ri >= 0 ? rows[ri + 1] || cur(steps) : null; else if (e.key === "ArrowUp") n = ri > 0 ? rows[ri - 1] : si >= 0 ? rows[rows.length - 1] : null;
    else if (e.key === "ArrowRight") n = si >= 0 ? steps[si + 1] : null; else if (e.key === "ArrowLeft") n = si > 0 ? steps[si - 1] : null;
    else if (e.key === "Tab") n = ri >= 0 ? cur(steps) : cur(rows); else return;
    e.preventDefault(); if (!n) return; for (const l of [rows, steps]) if (l.includes(n)) l.forEach((b) => (b.tabIndex = b === n ? 0 : -1)); M.focusKbd(n); });
  M.release(pop, {y: T["pane-rise"] || 4}, "touch"); if (o.kbd) focusPop(); else ready(); }
/* into the picker from the keyboard: onto the model in use */
function focusPop() { if (!pop) return; const rows = $$(".models .mi", pop), b = rows.find((x) => x.tabIndex === 0) || rows[0]; if (b) M.focusKbd(b); }
function drawPop() { if (!pop) return; const ctx = state.P.ctx, models = modelsOf(ctx), efforts = effortsFor(ctx, ctx.model), live = !!liveRun(state.P), tier = tierOf(ctx, ctx.model);
  const f = document.activeElement, had = f && pop.contains(f) ? (f.dataset.model ? `[data-model="${CSS.escape(f.dataset.model)}"]` : f.dataset.effort ? `[data-effort="${CSS.escape(f.dataset.effort)}"]` : "") : "";
  pop.innerHTML = `<div class="models" role="group" aria-label="Model">${models.map((m) => `<button class="mi${m.slug === ctx.model ? " hl" : ""}" data-model="${esc(m.slug)}" aria-pressed="${m.slug === ctx.model}" tabindex="${m.slug === ctx.model ? 0 : -1}"><span class="ml">${esc(m.name)}<small>${esc(m.slug)}${m.tiers.length ? " · " + esc(tierNames(m.tiers)) : ""}</small></span><span class="k">${m.slug === ctx.model ? ic("check") : ""}</span></button>`).join("")}</div><div class="steps t-text" role="group" aria-label="Effort">${efforts.map((e) => `<button data-effort="${esc(e.effort)}" aria-pressed="${e.effort === ctx.reasoning_effort}" class="${e.effort === ctx.reasoning_effort ? "on" : ""}" tabindex="${e.effort === ctx.reasoning_effort ? 0 : -1}">${esc(e.label)}</button>`).join("")}</div><span class="cap">${live ? "Applies from the next turn" : "This worktree\'s model and effort"}${tier ? `. ${esc(tier)} is the server's speed for every worktree` : ""}</span>`; host(pop);
  if (had) { const b = $(had, pop); if (b) M.focusKbd(b); } }
function closePicker(o = {}) { if (!pop) return; const p = pop, back = o.back || p.contains(document.activeElement); pop = null; p.inert = true; modeBtn.classList.remove("open"); modeBtn.setAttribute("aria-expanded", "false"); M.leave(p, () => p.remove()); if (back && !modeBtn.hidden) M.focusKbd(modeBtn); else ready(); }   /* from the keyboard, back to the label */
async function saveMode(model, effort) { const c = state.P, p = state.place, ctx = c.ctx; if (!ctx || (model === ctx.model && effort === ctx.reasoning_effort)) return; const prev = {model: ctx.model, reasoning_effort: ctx.reasoning_effort};
  Object.assign(ctx, {model, reasoning_effort: effort}); drawComposer(); drawPop(); S.play("tick");
  try { const r = await api("POST", "armada/topic-context", {stream: p.stream, topic: p.topic, model, reasoning_effort: effort, response_policy: ctx.response_policy || "ambient", include_git: false}); const n = r.armada_topic_context;
    if (n) { const keep = c.ctx.git; c.ctx = {...c.ctx, ...n}; if (!(n.git && n.git.available)) c.ctx.git = keep; } }
  catch (e) { Object.assign(ctx, prev); note(e.message); }
  if (state.P === c) { drawComposer(); drawPop(); } }

export {closePicker, focusPop, modeLabel, pop, togglePicker};

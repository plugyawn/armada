/* INTERIM BETA (README.md): a mention of someone not in the channel, said at the field (statement 53): "Mihir isn't in #ops,
   so they won't see this.", with Add them for someone the server lets add people (channel_rules.js decides both). The rules
   are Zulip's own (compose_validate.ts): no notice for a bot, me, someone deactivated, a silent mention, a wildcard or
   anything in code; for a group, when none of its members is in the channel, with no verb. It is one line in the 20 under
   the main field, which nothing else uses: whatever the band holds (a run's line, a question, Jump to latest) and whatever
   the conversation's foot, it covers nothing and moves nothing. A thread's field says so there too, for the field last
   written in. It goes when the mention does, when the field is sent, or with its ×, which dismisses it for those people
   until that field is empty again. Loaded with the channels chunk (channel_view.js), which hands it the page. */

import {addedWords, addRefusal, groupMembers, groupOutside, groupWords, mayAddPeople, mentionsIn, noticeWords, outsiders} from "./channel_rules.js";

export default function mentionNotice(P, addPeople) {
  const {$, el, esc, T, state, streams, members, people, groups, api, nameOf, loadMembers, holds, tx, wireOf, outsideCode, personByName} = P;
  const slot = tx.closest(".cz");   /* the main field: the notice stands under it */
  let note = null;                  /* {el, f (the field it is about), w, sig, done, adding, timer} */

  const fields = new WeakSet(), due = new Set(), dismissed = new WeakMap();
  /* a field is looked at after the frame its change landed in, never inside the key's own task */
  function soon(f) { if (!due.size) requestAnimationFrame(() => { const all = [...due]; due.clear(); for (const x of all) check(x); }); due.add(f); }
  function watch(f) { if (fields.has(f)) return; fields.add(f); new MutationObserver(() => soon(f)).observe(f, {childList: true, characterData: true, subtree: true}); }

  /* a group's name to its id, asked of the server the first time a field names a group (the page does not keep their names) */
  let groupIds = null, askingGroups = false;
  function loadGroups() { if (askingGroups) return; askingGroups = true;
    api("GET", "user_groups").then((r) => { groupIds = new Map(r.user_groups.map((g) => [g.name.toLowerCase(), g.id])); soon(tx); for (const x of [...document.querySelectorAll(".thr-in .tx")]) if (fields.has(x)) soon(x); }, () => { askingGroups = false; }); }

  /* what a field's words should say now: null, or {sid, ids} (people), or {sid, group} */
  function wanted(f) { const p = state.place; if (!p || p.kind !== "topic" || !f.isConnected || !f.textContent.includes("@")) return null;
    const set = members.get(p.stream_id); if (!(set instanceof Set)) { loadMembers(p.stream_id); return null; }   /* asked for; they come back through membersChanged */
    const {people: ids, groups: names} = mentionsIn(wireOf(f, state.agentMention), personByName, outsideCode), gone = dismissed.get(f) || new Set();
    const out = outsiders(ids, set, people, state.ME).filter((i) => !gone.has(i)); if (out.length) return {sid: p.stream_id, ids: out};
    if (names.length) { if (!groupIds) { loadGroups(); return null; }
      for (const name of names) { const id = groupIds.get(name.toLowerCase()); if (id != null && !gone.has("g" + id) && groupOutside(groupMembers(id, groups), set, people)) return {sid: p.stream_id, group: name, gid: id}; } }
    return null; }
  const first = (id) => nameOf(id).split(/[ ,]/)[0];
  const sigOf = (w) => (w ? `${w.sid}:${w.ids ? w.ids.join(",") : "g" + w.gid}` : "");

  /* a notice that says they are in now waits out its moment; any other goes as soon as its field's words would say nothing */
  function check(f) { if (!f.isConnected) return; if (!f.textContent.trim()) dismissed.delete(f);
    const w = wanted(f), sig = sigOf(w);
    if (!w) { if (note && note.f === f && !note.done) drop(); return; }
    if (note && note.f === f && note.sig === sig && !note.done) return;
    draw(f, w, sig); }
  function drop() { const n = note; if (!n) return; note = null; clearTimeout(n.timer); n.el.inert = true; M.leave(n.el); }
  function draw(f, w, sig) { const s = streams.get(w.sid), may = !!w.ids && mayAddPeople(s, state.realm, holds);
    const fresh = !note; if (fresh) { note = {el: el("div", "mnote"), adding: false}; note.el.setAttribute("role", "status");
      P.press(note.el, "[data-note-add]", add); P.press(note.el, "[data-note-x]", dismiss); slot.append(note.el); }
    const n = note; clearTimeout(n.timer); Object.assign(n, {sig, w, f, done: false});
    n.el.innerHTML = `<span class="say">${esc(w.ids ? noticeWords(w.ids.map(first), s.name) : groupWords(w.group, s.name))}</span>`
      + (may ? `<button class="lk" data-note-add><span>${n.adding ? "Adding" : "Add them"}</span><i aria-hidden="true">Add them</i></button>` : "")
      + `<button class="c sq" data-note-x aria-label="Dismiss" data-tip="Dismiss">${ic("x")}</button>`;
    host(n.el); if (fresh) { M.fade(n.el, 1); M.release(n.el, {y: -(T["pane-rise"] || 4)}, "touch"); } }
  /* Add them: the people the notice names. It says Adding while the server works, then that they are in, and goes. */
  function add() { const n = note; if (!n || n.adding || !n.w.ids) return; const {sid, ids} = n.w, verb = () => $("[data-note-add] > span", n.el);
    n.adding = true; verb().textContent = "Adding";
    addPeople(sid, ids).then(() => { n.adding = false; if (note !== n) return; n.done = true; $(".say", n.el).textContent = addedWords(ids.map(first), streams.get(sid).name);
      const a = $("[data-note-add]", n.el); if (a) a.remove(); S.play("tick"); n.timer = setTimeout(() => { if (note === n && n.done) drop(); }, 2400); },
    (e) => { n.adding = false; if (note !== n) return; const v = verb(), say = $(".say", n.el); if (v) v.textContent = "Add them"; say.textContent = addRefusal(e, streams.get(sid).name); say.dataset.bad = "";
      M.refuse(n.el); S.play("nope"); }); }
  /* ×: those people (or that group) are not mentioned to me again until that field is empty */
  function dismiss() { const n = note; if (!n) return; const gone = dismissed.get(n.f) || new Set(); for (const i of n.w.ids || ["g" + n.w.gid]) gone.add(i); dismissed.set(n.f, gone); drop(); P.ready(); }

  /* the main field always; a thread's field from its first word */
  watch(tx); document.addEventListener("input", (e) => { const f = e.target.closest && e.target.closest(".thr-in .tx"); if (f) { watch(f); soon(f); } });
  soon(tx);
  /* the place changed under the field, or who is in the channel did: every field is looked at again */
  const recheck = () => { if (note && !note.f.isConnected) drop(); soon(tx); for (const f of document.querySelectorAll(".thr-in .tx")) if (fields.has(f)) soon(f); };
  return {recheck};
}

/* INTERIM BETA (README.md): pure: who may add people to a channel, whom a channel's Add people offers, and whom a
   message's mentions will not reach (the notice at the field, mention_notice.js), in words. It imports nothing and reads
   no global, so node tests load it (web/tests/armada/beta_channel_rules.test.cjs); it is loaded with the channels chunk
   (channel_view.js). */

/* whether I may add people to a channel, by the server's rule (get_streams_to_which_user_cannot_add_subscribers): never a
   guest; an administrator always; else the realm's can_add_subscribers_group, or the channel's own can_add_subscribers_group
   or can_administer_channel_group. `me` is {admin, guest, addSubscribers} (the realm's group setting); `holds(setting)`
   says whether a group setting holds me. A channel the page does not hold, or one archived, takes no one. */
function mayAddPeople(sub, me, holds) {
  if (!sub || sub.is_archived || me.guest) return false;
  return !!me.admin || holds(me.addSubscribers) || holds(sub.can_add_subscribers_group) || holds(sub.can_administer_channel_group);
}

const fold = (s) => String(s).toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");
/* the people Add people offers: active people who are not bots and not in the channel, those whose name starts with the
   query first, then those with a word that does, then those that hold it anywhere (from two letters); among equals, who is
   here now, then who is away, then by name. `people` is [{id, name, bot, active, pres}]. */
const PRES = {on: 0, away: 1};
function addable(people, members, q = "") {
  const x = fold(q).trim(), score = (name) => { const n = fold(name); if (!x || n.startsWith(x)) return 0; if (n.split(/[\s._-]+/).some((w) => w.startsWith(x))) return 1; return x.length > 1 && n.includes(x) ? 2 : -1; };
  return people.filter((p) => p.active && !p.bot && !members.has(p.id)).map((p) => ({p, s: score(p.name)})).filter((r) => r.s >= 0)
    .sort((a, b) => a.s - b.s || (PRES[a.p.pres] ?? 2) - (PRES[b.p.pres] ?? 2) || a.p.name.localeCompare(b.p.name)).map((r) => r.p);
}
/* a channel's members as its sheet lists them: people (who is here now first, then who is away, then by name), then bots */
function memberOrder(people) {
  return [...people].sort((a, b) => (a.bot - b.bot) || (PRES[a.pres] ?? 2) - (PRES[b.pres] ?? 2) || a.name.localeCompare(b.name));
}
/* the server's refusal, in words */
const addRefusal = (e, channel) => (e.message === "Insufficient permission" ? `You can't add people to #${channel}.` : `Not added: ${e.message}`);

/* ---------- mentions that will not reach anyone (Zulip's compose_validate.ts, needs_subscribe_warning and
   warn_if_mentioning_unsubscribed_group): in a channel, a person mentioned who is not in it is not notified and cannot read
   it. Not a bot (a bot reads a channel it is not in), not me, not someone deactivated; never a silent mention (@_**…**,
   which notifies no one) or a wildcard (@**all**, @**topic**, ...); nothing inside code. A group, when none of its active
   members is in the channel. ---------- */
const WILDCARDS = new Set(["all", "everyone", "channel", "stream", "topic"]);
/* a mention where Zulip's renderer finds one: at the start, or after a space, a quote or a bracket */
const PERSON = /(?<![^\s'"({[\/<])@\*\*([^*\n]*?)(?:\|(\d+))?\*\*/g, GROUP = /(?<![^\s'"({[\/<])@\*(?!\*)([^*\n]+)\*(?!\*)/g;
/* the people and groups the words mention, in the order first written: {people: [id], groups: [name]}. `look(name)` names
   a person's id; `outsideCode(text, fn)` hands fn the words outside code. */
function mentionsIn(wire, look, outsideCode) { const people = [], groups = [];
  outsideCode(wire, (part) => {
    for (const m of part.matchAll(PERSON)) { const name = m[1].trim(), id = m[2] ? +m[2] : WILDCARDS.has(name.toLowerCase()) ? null : look(name); if (id != null && !people.includes(id)) people.push(id); }
    for (const m of part.matchAll(GROUP)) if (!groups.includes(m[1])) groups.push(m[1]);
    return part; });
  return {people, groups}; }
/* the people a mention will not reach: not in the channel, a person, active, not me. `people` maps an id to {bot, active}. */
const outsiders = (ids, members, people, me) => ids.filter((id) => { const p = people.get(id); return !!p && !p.bot && p.active && id !== me && !members.has(id); });
/* a group's members, through its subgroups however deep (Zulip's get_recursive_group_members); `groups` maps an id to
   {members, subgroups}, Sets */
function groupMembers(id, groups) { const out = new Set(), todo = [id], seen = new Set();
  while (todo.length) { const g = groups.get(todo.pop()); if (!g || seen.has(g)) continue; seen.add(g); for (const m of g.members) out.add(m); todo.push(...g.subgroups); }
  return out; }
/* whether no active member of a group is in the channel (a group with no one in it says nothing) */
const groupOutside = (ids, members, people) => { const active = [...ids].filter((i) => people.has(i) && people.get(i).active); return active.length > 0 && !active.some((i) => members.has(i)); };
/* what the notice says: who is not in the channel and that they will not see this; or, once added, that they are in it */
function namesWords(names) { return names.length < 4 ? (names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0]) : `${names.slice(0, 2).join(", ")} and ${names.length - 2} others`; }
const noticeWords = (names, channel) => `${namesWords(names)} ${names.length === 1 ? "isn't" : "aren't"} in #${channel}, so they won't see this.`;
const groupWords = (group, channel) => `No one in ${group} is in #${channel}, so no one there will see this.`;
const addedWords = (names, channel) => `${namesWords(names)} ${names.length === 1 ? "is" : "are"} in #${channel} now.`;

export {addable, addedWords, addRefusal, groupMembers, groupOutside, groupWords, mayAddPeople, memberOrder, mentionsIn, noticeWords, outsiders};

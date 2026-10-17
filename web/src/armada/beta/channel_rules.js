/* INTERIM BETA (README.md): pure: who may add people to a channel, and whom a channel's Add people offers. It imports
   nothing and reads no global, so node tests load it (web/tests/armada/beta_channel_rules.test.cjs); it is loaded with
   the channels chunk (channel_view.js). */

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

export {addable, addRefusal, mayAddPeople, memberOrder};

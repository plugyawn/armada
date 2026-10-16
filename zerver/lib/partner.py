"""Partners: the Pokémon a person chooses as their face, and how use of
Armada levels it up and evolves it (the r4 partner study,
var/armada-overhaul/design/r4/partner/PARTNER.md).

The pack. The species data and the sprites are not ours to publish, so
they live in the git-ignored sprite directory ($ARMADA_SPRITE_DIR, else
web/sprites/licensed/), and tools/armada/sprites.py writes species.json
there. Without it the feature is off: nobody can choose, and faces stay
initials.

Storage. Each person's row is a Partner (zerver/models/armada.py),
which only this module writes. The code works on the row as a dict
(new_row's keys, with times as Unix timestamps and the day as an ISO
date), read and written at the edges. A write holds the person's row
locked for its transaction, so two folds of one person cannot count the
same message twice; a choice also holds the agent's bot row, so two
people cannot take one evolution line.

Pushes. A change to a partner as the team sees it (a choice, an
evolution, a level crossed) goes to everyone who can see its person as
an armada_partner event. A level crossed by a message is pushed in that
message's transaction, ahead of the message's own event, so a page
knows the partner's new state before it draws the message.

Experience. fold_partner_xp brings a row up to date from what the server
already has: my messages, my runs that ended, reactions others put on my
messages. It reads only what is past the row's watermarks, so after the
first count (which reads my whole history) a fold reads a handful of rows.
Levels are computed from experience when read, never stored.
"""

import json
import logging
import math
import os
import zoneinfo
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.lib.armada_background_task import ArmadaBackgroundTask
from zerver.lib.armada_gate import get_selected_armada_bot_user_id_for_realm
from zerver.lib.exceptions import JsonableError
from zerver.lib.users import (
    check_can_access_user,
    check_user_can_access_all_users,
    get_accessible_user_ids,
    get_user_ids_who_can_access_user,
)
from zerver.models import AgentRun, Message, Partner, Reaction, UserProfile
from zerver.tornado.django_api import send_event_on_commit

# A person's experience is folded at most this often; a fold asked for
# sooner returns the row as it stands.
PARTNER_FOLD_SECONDS = 30
# Whether the agent has evolved is counted from the realm's finished runs
# at most this often (or again when a run finishes).
AGENT_EVOLUTION_CACHE_SECONDS = 60
# The agent's partner: Onix, which becomes Steelix and keeps its name.
AGENT_SPECIES = "95"
AGENT_EVOLVED_SPECIES = "208"

# Each type's stone: a person's bubbles, as others see them, take the stone
# of their partner's type. Citrine is the stone partners add, for bubbles
# only.
TYPE_STONES = {
    "Grass": "moss",
    "Bug": "moss",
    "Water": "larimar",
    "Ice": "larimar",
    "Flying": "lapis",
    "Dragon": "lapis",
    "Psychic": "amethyst",
    "Poison": "amethyst",
    "Ghost": "amethyst",
    "Fire": "rose",
    "Fighting": "rose",
    "Fairy": "rose",
    "Normal": "flint",
    "Rock": "flint",
    "Steel": "flint",
    "Dark": "flint",
    "Electric": "citrine",
    "Ground": "citrine",
}


def armada_sprite_dir() -> str:
    return os.environ.get("ARMADA_SPRITE_DIR") or os.path.join(
        settings.DEPLOY_ROOT, "web", "sprites", "licensed"
    )


@dataclass(frozen=True)
class Pack:
    species: dict[str, dict[str, Any]]
    version: str

    def drawable(self, species_id: str) -> bool:
        entry = self.species.get(species_id)
        return entry is not None and entry.get("sprite") is not None

    def has_shiny(self, species_id: str) -> bool:
        return self.drawable(species_id) and bool(self.species[species_id]["sprite"]["shiny"])

    def next_forms(self, species_id: str) -> list[str]:
        """The forms a species can evolve into that the pack can draw; an
        evolution with no sprite is skipped to the next drawable form."""
        out: list[str] = []
        for evo in self.species[species_id]["evos"]:
            if evo not in self.species:
                continue
            if self.drawable(evo):
                out.append(evo)
            else:
                out.extend(x for x in self.next_forms(evo) if x not in out)
        return out

    def steps_from(self, first: str, species_id: str) -> int:
        """How many evolutions lie between the chosen form and this one."""
        steps = 0
        current: str | None = species_id
        while current is not None and current != first:
            current = self.species[current]["prevo"]
            steps += 1
        return steps if current == first else 0


_pack_cache: dict[str, tuple[float, Pack]] = {}


def load_pack() -> Pack | None:
    """The pack's species, read once and again only when the file changes."""
    path = os.path.join(armada_sprite_dir(), "species.json")
    try:
        mtime = os.stat(path).st_mtime
    except FileNotFoundError:
        return None
    cached = _pack_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    pack = Pack(species=data["species"], version=str(int(mtime)))
    _pack_cache[path] = (mtime, pack)
    return pack


def stone_for_species(pack: Pack, species_id: str) -> str:
    """The partner's first type decides, except Normal, which yields to a
    second type (Normal and Flying is lapis)."""
    types = pack.species[species_id]["types"]
    first = types[1] if types[0] == "Normal" and len(types) > 1 else types[0]
    return TYPE_STONES.get(first, "flint")


def level_for_xp(xp: int) -> int:
    """Level L needs 4·L² experience; levels run from 1 to 100."""
    return max(1, min(100, math.isqrt(xp // 4)))


def xp_for_level(level: int) -> int:
    return 4 * level * level


def stages_for_level(level: int) -> int:
    return sum(1 for threshold in settings.PARTNER_EVOLVE_LEVELS if level >= threshold)


def get_agent_bot(realm_id: int) -> UserProfile | None:
    bot_id = get_selected_armada_bot_user_id_for_realm(realm_id)
    return None if bot_id is None else UserProfile.objects.get(id=bot_id)


def new_row() -> dict[str, Any]:
    return {
        "species": None,
        "chosen": None,
        "shiny": False,
        "held": False,
        "later": False,
        "chosen_at": None,
        "evolved_at": None,
        "evolved_kind": None,
        "xp": 0,
        "active_days": 0,
        "day": None,
        "day_active": False,
        "day_messages": 0,
        "day_runs": 0,
        "day_reactions": 0,
        "message_watermark": 0,
        "reaction_watermark": 0,
        "runs_watermark": None,
        "folded_at": None,
    }


# The row's times, kept as Unix timestamps in the dict.
PARTNER_TIMES = ("chosen_at", "evolved_at", "runs_watermark", "folded_at")


def row_of(partner: Partner) -> dict[str, Any]:
    row = {key: getattr(partner, key) for key in new_row()}
    for key in PARTNER_TIMES:
        if row[key] is not None:
            row[key] = row[key].timestamp()
    if row["day"] is not None:
        row["day"] = row["day"].isoformat()
    return row


def columns_of(row: dict[str, Any]) -> dict[str, Any]:
    columns = dict(row)
    for key in PARTNER_TIMES:
        if columns[key] is not None:
            columns[key] = datetime.fromtimestamp(columns[key], tz=timezone.utc)
    if columns["day"] is not None:
        columns["day"] = date.fromisoformat(columns["day"])
    return columns


def get_rows(realm_id: int) -> dict[int, dict[str, Any]]:
    return {
        partner.user_profile_id: row_of(partner)
        for partner in Partner.objects.filter(realm_id=realm_id)
    }


def get_row(user: UserProfile) -> dict[str, Any]:
    partner = Partner.objects.filter(user_profile=user).first()
    return row_of(partner) if partner is not None else new_row()


def save_row(user: UserProfile, row: dict[str, Any]) -> None:
    Partner.objects.update_or_create(
        user_profile=user, defaults={"realm_id": user.realm_id, **columns_of(row)}
    )


def lock_row(user: UserProfile) -> dict[str, Any]:
    """My row, held for the transaction: every write to it reads it this
    way first."""
    Partner.objects.get_or_create(user_profile=user, defaults={"realm_id": user.realm_id})
    return row_of(Partner.objects.select_for_update(no_key=True).get(user_profile=user))


def lock_lines(agent: UserProfile) -> None:
    """A choice holds this lock (the agent's bot row) for its transaction,
    so that two people cannot take one evolution line."""
    UserProfile.objects.select_for_update(no_key=True).filter(id=agent.id).first()


def local_date(moment: datetime, tz: zoneinfo.ZoneInfo | timezone) -> date:
    return moment.astimezone(tz).date()


def user_zone(user: UserProfile) -> zoneinfo.ZoneInfo | timezone:
    try:
        return zoneinfo.ZoneInfo(user.timezone) if user.timezone else timezone.utc
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def add_day(row: dict[str, Any], day: date, messages: int, runs: int, reactions: int) -> None:
    """Count one day's use into the row, with the day's caps. A day earlier
    than the row's open day is counted into the open day: experience only
    grows, and the open day's caps still hold."""
    open_day = date.fromisoformat(row["day"]) if row["day"] else None
    if open_day is None or day > open_day:
        row.update(
            day=day.isoformat(), day_active=False, day_messages=0, day_runs=0, day_reactions=0
        )
    gain = 0
    if messages and not row["day_active"]:
        row["day_active"] = True
        row["active_days"] += 1
        gain += settings.PARTNER_XP_DAY
    counted = min(messages, settings.PARTNER_CAP_MESSAGES - row["day_messages"])
    row["day_messages"] += counted
    gain += counted * settings.PARTNER_XP_MESSAGE
    counted = min(runs, settings.PARTNER_CAP_RUNS - row["day_runs"])
    row["day_runs"] += counted
    gain += counted * settings.PARTNER_XP_RUN
    counted = min(reactions, settings.PARTNER_CAP_REACTIONS - row["day_reactions"])
    row["day_reactions"] += counted
    gain += counted * settings.PARTNER_XP_REACTION
    row["xp"] += gain


def fold_partner_xp(
    user: UserProfile, row: dict[str, Any], now: datetime, *, every_time: bool = False
) -> bool:
    """Bring the row's experience up to date. Returns whether it read
    anything (False when the last fold was under PARTNER_FOLD_SECONDS ago,
    unless every_time).

    A day on which I sent a message is active. Messages count on the day
    they were sent and runs on the day they ended, in my time zone.
    Reaction rows have no time: on the first count each is dated by its
    message, and after that it counts on the day of the fold."""
    if (
        not every_time
        and row["folded_at"] is not None
        and now.timestamp() - row["folded_at"] < PARTNER_FOLD_SECONDS
    ):
        return False
    tz = user_zone(user)
    first_count = row["folded_at"] is None
    days: dict[date, list[int]] = {}

    def count(day: date, kind: int) -> None:
        days.setdefault(day, [0, 0, 0])[kind] += 1

    for message_id, date_sent in (
        Message.objects.filter(
            realm_id=user.realm_id, sender_id=user.id, id__gt=row["message_watermark"]
        )
        .order_by("id")
        .values_list("id", "date_sent")
    ):
        count(local_date(date_sent, tz), 0)
        row["message_watermark"] = message_id

    runs = AgentRun.objects.filter(
        requesting_user_id=user.id,
        status__in=[AgentRun.DONE, AgentRun.FAILED],
        date_finished__isnull=False,
    )
    if row["runs_watermark"] is not None:
        runs = runs.filter(
            date_finished__gt=datetime.fromtimestamp(row["runs_watermark"], tz=timezone.utc)
        )
    for (date_finished,) in runs.order_by("date_finished").values_list("date_finished"):
        assert date_finished is not None  # filtered above
        count(local_date(date_finished, tz), 1)
        row["runs_watermark"] = date_finished.timestamp()

    today = local_date(now, tz)
    for reaction_id, date_sent in (
        Reaction.objects.filter(message__sender_id=user.id, id__gt=row["reaction_watermark"])
        .exclude(user_profile_id=user.id)
        .order_by("id")
        .values_list("id", "message__date_sent")
    ):
        count(local_date(date_sent, tz) if first_count else today, 2)
        row["reaction_watermark"] = reaction_id

    for day in sorted(days):
        messages, runs_ended, reactions = days[day]
        add_day(row, day, messages, runs_ended, reactions)
    row["folded_at"] = now.timestamp()
    return True


def ready_change(pack: Pack, row: dict[str, Any]) -> dict[str, Any] | None:
    """What the partner may become now: its next forms, once the level
    allows another evolution counted from the chosen form; or its shiny
    self, at the last evolution level for a species that never evolves and
    at PARTNER_SHINY_LEVEL for every partner."""
    species = row["species"]
    if species is None or not pack.drawable(species):
        return None
    level = level_for_xp(row["xp"])
    choices = pack.next_forms(species)
    if choices and pack.steps_from(row["chosen"], species) < stages_for_level(level):
        return {"kind": "evolve", "choices": choices}
    never_evolves = not pack.next_forms(row["chosen"])
    shiny_level = (
        settings.PARTNER_EVOLVE_LEVELS[-1] if never_evolves else settings.PARTNER_SHINY_LEVEL
    )
    if not row["shiny"] and level >= shiny_level and pack.has_shiny(species):
        return {"kind": "shiny", "choices": [species]}
    return None


def arrival(pack: Pack, chosen: str, xp: int, picks: list[str]) -> tuple[str, bool]:
    """The form a newly chosen partner arrives in: as far along its line as
    my level allows, taking the branch I picked where the line branches
    (and stopping there if I picked none), shiny if my level says so. No
    moment plays for an arrival."""
    row = {**new_row(), "species": chosen, "chosen": chosen, "xp": xp}
    while True:
        change = ready_change(pack, row)
        if change is None:
            return row["species"], row["shiny"]
        if change["kind"] == "shiny":
            row["shiny"] = True
        elif len(change["choices"]) == 1:
            row["species"] = change["choices"][0]
        else:
            picked = [x for x in picks if x in change["choices"]]
            if not picked:
                return row["species"], row["shiny"]
            row["species"] = picked[0]


def agent_evolution_cache_key(realm_id: int) -> str:
    return f"armada:agent-partner-evolved:{realm_id}"


def invalidate_agent_evolution(realm_id: int) -> None:
    cache.delete(agent_evolution_cache_key(realm_id))


def agent_has_evolved(realm_id: int) -> bool:
    """Onix evolves into Steelix once the team's runs have done enough work
    in enough hands: PARTNER_AGENT_RUNS runs done, started by at least
    PARTNER_AGENT_HANDS people (or by every active person, in a smaller
    team). Counting reads every finished run, so the answer is kept for
    AGENT_EVOLUTION_CACHE_SECONDS, or until a run finishes."""
    key = agent_evolution_cache_key(realm_id)
    evolved = cache.get(key)
    if isinstance(evolved, bool):
        return evolved
    done = AgentRun.objects.filter(realm_id=realm_id, status=AgentRun.DONE)
    evolved = False
    if done.count() >= settings.PARTNER_AGENT_RUNS:
        people = UserProfile.objects.filter(realm_id=realm_id, is_active=True, is_bot=False)
        hands = min(settings.PARTNER_AGENT_HANDS, people.count())
        evolved = done.values("requesting_user_id").distinct().count() >= hands
    cache.set(key, evolved, AGENT_EVOLUTION_CACHE_SECONDS)
    return evolved


def agent_partner(pack: Pack, realm_id: int) -> dict[str, Any]:
    """The agent's partner; its name stays the agent's."""
    species = AGENT_EVOLVED_SPECIES if agent_has_evolved(realm_id) else AGENT_SPECIES
    return {"species": species if pack.drawable(species) else None, "shiny": False}


def team_view(pack: Pack, row: dict[str, Any]) -> dict[str, Any]:
    """A person's partner as the team sees it. may_change says the person
    can evolve it now: a page holds that person's next message until it
    knows whether they did, so the message carries its caption from its
    first frame."""
    return {
        "species": row["species"],
        "shiny": row["shiny"],
        "level": level_for_xp(row["xp"]),
        "stone": stone_for_species(pack, row["species"]),
        "evolved_at": row["evolved_at"],
        "evolved_kind": row["evolved_kind"],
        "may_change": ready_change(pack, row) is not None,
    }


def shown_view(pack: Pack, row: dict[str, Any]) -> dict[str, Any] | None:
    """The team's view of a partner the pack can draw, else None: a person
    without one is not listed."""
    if row["species"] is None or not pack.drawable(row["species"]):
        return None
    return team_view(pack, row)


def push_partner_change(
    pack: Pack, user: UserProfile, before: dict[str, Any] | None, row: dict[str, Any]
) -> None:
    """Tell everyone who can see this person that their partner changed,
    when the commit lands; the partner is the row GET
    /json/armada/partners lists, with its person's id."""
    after = shown_view(pack, row)
    if after is None or after == before:
        return
    event = {"type": "armada_partner", "op": "update", "partner": {"user_id": user.id, **after}}
    send_event_on_commit(user.realm, event, get_user_ids_who_can_access_user(user))


def fold_partners_of_senders(senders: list[UserProfile]) -> None:
    """Count the messages being sent into their senders' partners, inside
    the send's transaction. do_send_messages calls this before it queues
    the messages' events, so a level crossed is pushed ahead of the
    message that crossed it. Bots and people without a partner cost
    nothing, and without a pack, nobody costs a query."""
    people = {sender.id: sender for sender in senders if not sender.is_bot}
    if not people:
        return
    try:
        pack = load_pack()
    except (OSError, ValueError, KeyError):
        # A pack the server cannot read is for its operator to fix; it
        # never stops a message from being sent.
        logging.exception("The partner pack could not be read")
        return
    if pack is None:
        return
    now = timezone_now()
    for partner in (
        Partner.objects.select_for_update(no_key=True)
        .filter(user_profile_id__in=list(people), species__isnull=False)
        .order_by("user_profile_id")
    ):
        user = people[partner.user_profile_id]
        row = row_of(partner)
        before = shown_view(pack, row)
        fold_partner_xp(user, row, now, every_time=True)
        Partner.objects.filter(id=partner.id).update(**columns_of(row))
        push_partner_change(pack, user, before, row)


def my_view(
    pack: Pack, row: dict[str, Any], now: datetime, tz: zoneinfo.ZoneInfo | timezone
) -> dict[str, Any]:
    level = level_for_xp(row["xp"])
    today = row["day"] == local_date(now, tz).isoformat()
    return {
        "species": row["species"],
        "chosen": row["chosen"],
        "shiny": row["shiny"],
        "held": row["held"],
        "later": row["later"],
        "xp": row["xp"],
        "level": level,
        "level_xp": xp_for_level(level) if level > 1 else 0,
        "next_level_xp": xp_for_level(level + 1) if level < 100 else None,
        "active_days": row["active_days"],
        "today_xp": (
            (settings.PARTNER_XP_DAY if row["day_active"] else 0)
            + row["day_messages"] * settings.PARTNER_XP_MESSAGE
            + row["day_runs"] * settings.PARTNER_XP_RUN
            + row["day_reactions"] * settings.PARTNER_XP_REACTION
        )
        if today
        else 0,
        "ready": ready_change(pack, row),
        "evolved_at": row["evolved_at"],
        "evolved_kind": row["evolved_kind"],
        "evolve_levels": list(settings.PARTNER_EVOLVE_LEVELS),
    }


def fold_partner(user: UserProfile) -> None:
    """Fold my experience, at most once in PARTNER_FOLD_SECONDS, holding my
    row for the fold; my row is written only when the fold found
    something, and the team is told when what they see changed."""
    pack = load_pack()
    if pack is None:
        return
    with transaction.atomic(savepoint=False):
        mine = lock_row(user)
        unfolded = dict(mine)
        before = shown_view(pack, mine)
        if fold_partner_xp(user, mine, timezone_now()) and {**mine, "folded_at": None} != {
            **unfolded,
            "folded_at": None,
        }:
            save_row(user, mine)
            push_partner_change(pack, user, before, mine)


def fold_partner_of_user_id(user_id: int) -> None:
    user = UserProfile.objects.filter(id=user_id).first()
    if user is not None:
        fold_partner(user)


# A read of the partners asks for my fold, which runs in the background:
# a read never waits for the fold or holds a lock.
partner_fold = ArmadaBackgroundTask[int]("armada-partner-fold", fold_partner_of_user_id)


def fold_partner_after_run(user_id: int) -> None:
    """A run of mine ended: count it now, as a send counts a message, and
    count the agent's evolution again."""
    user = UserProfile.objects.filter(id=user_id).first()
    if user is None:
        return
    invalidate_agent_evolution(user.realm_id)
    with transaction.atomic(savepoint=False):
        fold_partners_of_senders([user])


def partners_state(user: UserProfile) -> dict[str, Any]:
    """Every person's partner as the team sees it, the agent's, and mine in
    full, as stored: a read writes nothing. When my experience is due a
    fold (PARTNER_FOLD_SECONDS), one is asked for in the background, once
    in that time across processes; a change it makes reaches the team as
    an armada_partner event."""
    pack = load_pack()
    agent = get_agent_bot(user.realm_id)
    if pack is None or agent is None:
        return {"on": False}
    now = timezone_now()
    rows = get_rows(user.realm_id)
    mine = rows.get(user.id, new_row())
    if (
        not user.is_bot
        and (
            mine["folded_at"] is None or now.timestamp() - mine["folded_at"] >= PARTNER_FOLD_SECONDS
        )
        and cache.add(f"armada:partner-fold:{user.id}", True, PARTNER_FOLD_SECONDS)
    ):
        partner_fold.request(user.id)
    shown = set(
        UserProfile.objects.filter(
            realm_id=user.realm_id, is_active=True, id__in=list(rows)
        ).values_list("id", flat=True)
    )
    # A partner and its level say who a person is and how much they do
    # here, so a person sees only the partners of people they can see
    # (a guest whose access the realm limits, as the users endpoints do).
    if not check_user_can_access_all_users(user):
        shown &= set(get_accessible_user_ids(user.realm, user))
    return {
        "on": True,
        "version": pack.version,
        "partners": {
            str(user_id): view
            for user_id, row in rows.items()
            if user_id in shown and (view := shown_view(pack, row)) is not None
        },
        "agent": {"user_id": agent.id, **agent_partner(pack, user.realm_id)},
        "me": my_view(pack, mine, now, user_zone(user)),
    }


def require_pack_and_agent(user: UserProfile) -> tuple[Pack, UserProfile]:
    if user.is_bot:
        raise JsonableError(_("Bots do not have partners."))
    pack = load_pack()
    agent = get_agent_bot(user.realm_id)
    if pack is None or agent is None:
        raise JsonableError(_("Partners are not set up on this server."))
    return pack, agent


def choose_partner(user: UserProfile, species: str, picks: list[str]) -> None:
    """Choose or change my partner. Only a first form the pack can draw; one
    person a line (a line held by a deactivated person is free, and is
    cleared here); the agent's line is the agent's. My level is mine: the
    partner arrives in the form it allows."""
    pack, agent = require_pack_and_agent(user)
    entry = pack.species.get(species)
    if entry is None or not pack.drawable(species):
        raise JsonableError(_("There is no such partner."))
    if not entry["first"]:
        raise JsonableError(_("Choose the first form of a line; your partner grows into the rest."))
    if entry["line"] == pack.species[AGENT_SPECIES]["line"]:
        raise JsonableError(_("This line is {name}'s.").format(name=agent.full_name))
    now = timezone_now()
    # An error is raised after the transaction, never inside it: the lock's
    # transaction may be the request's outermost one.
    taken_by: UserProfile | None = None
    with transaction.atomic(savepoint=False):
        lock_lines(agent)
        rows = get_rows(user.realm_id)
        holders = {
            user_id
            for user_id, row in rows.items()
            if user_id != user.id
            and row["species"] is not None
            and row["species"] in pack.species
            and pack.species[row["species"]]["line"] == entry["line"]
        }
        people = {
            p.id: p for p in UserProfile.objects.filter(id__in=holders, realm_id=user.realm_id)
        }
        taken_by = next(
            (people[h] for h in sorted(holders) if h in people and people[h].is_active), None
        )
        if taken_by is None:
            for holder_id in holders:
                freed = rows[holder_id]
                freed.update(species=None, chosen=None, shiny=False, held=False)
                save_row(people[holder_id], freed)
            mine = lock_row(user)
            before = shown_view(pack, mine)
            fold_partner_xp(user, mine, now)
            arrived, shiny = arrival(pack, species, mine["xp"], picks)
            mine.update(
                species=arrived,
                chosen=species,
                shiny=shiny,
                held=False,
                later=False,
                chosen_at=now.timestamp(),
                evolved_at=None,
                evolved_kind=None,
            )
            save_row(user, mine)
            push_partner_change(pack, user, before, mine)
    if taken_by is not None:
        if not check_can_access_user(taken_by, user):
            raise JsonableError(_("Someone else has this line."))
        raise JsonableError(_("{name} has this line.").format(name=taken_by.full_name))


def put_off_choice(user: UserProfile) -> None:
    """Later: the picker does not open by itself again, on any machine."""
    require_pack_and_agent(user)
    with transaction.atomic(savepoint=False):
        mine = lock_row(user)
        mine["later"] = True
        save_row(user, mine)


def evolve_partner(user: UserProfile, into: str | None, hold: bool) -> None:
    """Take the change the level allows (into one of its choices), or hold
    the partner as it is (Stop in the moment). A held partner is not
    offered the moment again; Evolve on the card takes the change later."""
    pack, _agent = require_pack_and_agent(user)
    now = timezone_now()
    error = None
    with transaction.atomic(savepoint=False):
        mine = lock_row(user)
        before = shown_view(pack, mine)
        fold_partner_xp(user, mine, now)
        change = ready_change(pack, mine)
        if change is None:
            error = _("Your partner cannot change yet.")
        elif hold:
            mine["held"] = True
        elif into not in change["choices"]:
            error = _("Your partner cannot become that.")
        elif change["kind"] == "shiny":
            mine.update(shiny=True, held=False, evolved_at=now.timestamp(), evolved_kind="shiny")
        else:
            mine.update(species=into, held=False, evolved_at=now.timestamp(), evolved_kind="evolve")
        save_row(user, mine)
        push_partner_change(pack, user, before, mine)
    if error is not None:
        raise JsonableError(error)

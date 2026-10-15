"""Partners: the Pokémon a person chooses as their face, and how use of
Armada levels it up and evolves it (the r4 partner study,
var/armada-overhaul/design/r4/partner/PARTNER.md).

The pack. The species data and the sprites are not ours to publish, so
they live in the git-ignored sprite directory ($ARMADA_SPRITE_DIR, else
web/sprites/licensed/), and tools/armada/sprites.py writes species.json
there. Without it the feature is off: nobody can choose, and faces stay
initials.

Storage, for now. Until the Partner model arrives with the rename step's
migration, each person's row is JSON in a BotStorageData row under the
agent's bot, key "partner:<user_id>" (the study's interim, section 4).
Only this module writes those rows, always under a lock on the agent's
bot row, so two people cannot take one evolution line and two folds of
one person cannot count the same message twice.

Experience. fold_partner_xp brings a row up to date from what the server
already has: my messages, my runs that ended, reactions others put on my
messages. It reads only what is past the row's watermarks, so after the
first count (which reads my whole history) a fold reads a handful of rows.
Levels are computed from experience when read, never stored.
"""

import json
import math
import os
import zoneinfo
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _

from zerver.lib.armada_gate import get_selected_pear_bot_user_id_for_realm
from zerver.lib.exceptions import JsonableError
from zerver.lib.users import (
    check_can_access_user,
    check_user_can_access_all_users,
    get_accessible_user_ids,
)
from zerver.models import BotStorageData, Message, PearWorkerRun, Reaction, UserProfile

PARTNER_KEY_PREFIX = "partner:"
# A person's experience is folded at most this often; a fold asked for
# sooner returns the row as it stands.
PARTNER_FOLD_SECONDS = 30
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
    bot_id = get_selected_pear_bot_user_id_for_realm(realm_id)
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


def get_rows(agent: UserProfile) -> dict[int, dict[str, Any]]:
    rows = {}
    for key, value in BotStorageData.objects.filter(
        bot_profile=agent, key__startswith=PARTNER_KEY_PREFIX
    ).values_list("key", "value"):
        rows[int(key.removeprefix(PARTNER_KEY_PREFIX))] = {**new_row(), **json.loads(value)}
    return rows


def get_row(agent: UserProfile, user_id: int) -> dict[str, Any]:
    value = (
        BotStorageData.objects.filter(bot_profile=agent, key=f"{PARTNER_KEY_PREFIX}{user_id}")
        .values_list("value", flat=True)
        .first()
    )
    return {**new_row(), **json.loads(value)} if value is not None else new_row()


def save_row(agent: UserProfile, user_id: int, row: dict[str, Any]) -> None:
    BotStorageData.objects.update_or_create(
        bot_profile=agent,
        key=f"{PARTNER_KEY_PREFIX}{user_id}",
        defaults={"value": json.dumps(row, sort_keys=True)},
    )


def lock_partners(agent: UserProfile) -> None:
    """Every write to partner rows holds this lock (the agent's bot row) for
    its transaction."""
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


def fold_partner_xp(user: UserProfile, row: dict[str, Any], now: datetime) -> bool:
    """Bring the row's experience up to date. Returns whether it read
    anything (False when the last fold was under PARTNER_FOLD_SECONDS ago).

    A day on which I sent a message is active. Messages count on the day
    they were sent and runs on the day they ended, in my time zone.
    Reaction rows have no time: on the first count each is dated by its
    message, and after that it counts on the day of the fold."""
    if row["folded_at"] is not None and now.timestamp() - row["folded_at"] < PARTNER_FOLD_SECONDS:
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

    runs = PearWorkerRun.objects.filter(
        requesting_user_id=user.id,
        status__in=[PearWorkerRun.DONE, PearWorkerRun.FAILED],
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


def agent_partner(pack: Pack, realm_id: int) -> dict[str, Any]:
    """Onix evolves into Steelix once the team's runs have done enough work
    in enough hands: PARTNER_AGENT_RUNS runs done, started by at least
    PARTNER_AGENT_HANDS people (or by every active person, in a smaller
    team). Its name stays Onix."""
    done = PearWorkerRun.objects.filter(realm_id=realm_id, status=PearWorkerRun.DONE)
    evolved = False
    if done.count() >= settings.PARTNER_AGENT_RUNS:
        people = UserProfile.objects.filter(realm_id=realm_id, is_active=True, is_bot=False)
        hands = min(settings.PARTNER_AGENT_HANDS, people.count())
        evolved = done.values("requesting_user_id").distinct().count() >= hands
    species = AGENT_EVOLVED_SPECIES if evolved else AGENT_SPECIES
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


def partners_state(user: UserProfile, *, fold: bool = True) -> dict[str, Any]:
    """Every person's partner as the team sees it, the agent's, and mine in
    full. My experience is folded first (at most once in
    PARTNER_FOLD_SECONDS)."""
    pack = load_pack()
    agent = get_agent_bot(user.realm_id)
    if pack is None or agent is None:
        return {"on": False}
    now = timezone_now()
    with transaction.atomic(savepoint=False):
        lock_partners(agent)
        rows = get_rows(agent)
        mine = rows.get(user.id, new_row())
        if fold and fold_partner_xp(user, mine, now):
            save_row(agent, user.id, mine)
            rows[user.id] = mine
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
            str(user_id): team_view(pack, row)
            for user_id, row in rows.items()
            if user_id in shown and row["species"] is not None and pack.drawable(row["species"])
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
        lock_partners(agent)
        rows = get_rows(agent)
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
                save_row(agent, holder_id, freed)
            mine = rows.get(user.id, new_row())
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
            save_row(agent, user.id, mine)
    if taken_by is not None:
        if not check_can_access_user(taken_by, user):
            raise JsonableError(_("Someone else has this line."))
        raise JsonableError(_("{name} has this line.").format(name=taken_by.full_name))


def put_off_choice(user: UserProfile) -> None:
    """Later: the picker does not open by itself again, on any machine."""
    _pack, agent = require_pack_and_agent(user)
    with transaction.atomic(savepoint=False):
        lock_partners(agent)
        mine = get_row(agent, user.id)
        mine["later"] = True
        save_row(agent, user.id, mine)


def evolve_partner(user: UserProfile, into: str | None, hold: bool) -> None:
    """Take the change the level allows (into one of its choices), or hold
    the partner as it is (Stop in the moment). A held partner is not
    offered the moment again; Evolve on the card takes the change later."""
    pack, agent = require_pack_and_agent(user)
    now = timezone_now()
    error = None
    with transaction.atomic(savepoint=False):
        lock_partners(agent)
        mine = get_row(agent, user.id)
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
        save_row(agent, user.id, mine)
    if error is not None:
        raise JsonableError(error)

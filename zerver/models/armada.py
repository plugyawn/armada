from django.db import models
from django.db.models import CASCADE

from zerver.models.realms import Realm
from zerver.models.users import UserProfile


class Partner(models.Model):
    """A person's partner: the creature they chose as their face, and the
    experience that levels it up (zerver/lib/partner.py, which is the
    only writer). Levels are computed from xp when read, never stored."""

    user_profile = models.OneToOneField(UserProfile, on_delete=CASCADE, related_name="+")
    realm = models.ForeignKey(Realm, on_delete=CASCADE, related_name="+")

    # Species are the pack's ids (species.json in the sprite directory).
    species = models.CharField(max_length=16, null=True)
    chosen = models.CharField(max_length=16, null=True)
    shiny = models.BooleanField(default=False)
    held = models.BooleanField(default=False)
    later = models.BooleanField(default=False)
    chosen_at = models.DateTimeField(null=True)
    evolved_at = models.DateTimeField(null=True)
    evolved_kind = models.CharField(max_length=16, null=True)

    xp = models.PositiveIntegerField(default=0)
    active_days = models.PositiveIntegerField(default=0)
    # The open day, in the person's time zone, and what it has counted
    # against the day's caps.
    day = models.DateField(null=True)
    day_active = models.BooleanField(default=False)
    day_messages = models.PositiveIntegerField(default=0)
    day_runs = models.PositiveIntegerField(default=0)
    day_reactions = models.PositiveIntegerField(default=0)

    # A fold reads only what is past these.
    message_watermark = models.BigIntegerField(default=0)
    reaction_watermark = models.BigIntegerField(default=0)
    runs_watermark = models.DateTimeField(null=True)
    folded_at = models.DateTimeField(null=True)

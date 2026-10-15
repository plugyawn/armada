from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.core.management.base import CommandError
from typing_extensions import override

from zerver.lib.management import ZulipBaseCommand
from zerver.models.realms import get_realm


class Command(ZulipBaseCommand):
    help = """Create or refresh the user the Armada checks log in as.

tools/armada-checks drives the development server as armada-checks@zulip.com,
so that nobody else's session shares its rate limit or posts into the pages it
measures.  This command creates that user, or brings it back in line: the
reference user's role and channels (desdemona's by default), onboarding
finished, a direct message from King Hamlet, and a rate limit no run reaches.

  ./manage.py armada_checks_user
  ./manage.py armada_checks_user --like iago@zulip.com

Idempotent.  Development only.
"""

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--like",
            metavar="EMAIL",
            help="The user whose role and channels the checks' user takes "
            "(default: desdemona@zulip.com).",
        )

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        if settings.PRODUCTION or not settings.DEVELOPMENT:
            raise CommandError("armada_checks_user only runs in a development environment.")

        from zilencer.lib.armada_checks_user import (
            ARMADA_CHECKS_REFERENCE_EMAIL,
            ensure_armada_checks_user,
        )

        user = ensure_armada_checks_user(
            get_realm("zulip"), options["like"] or ARMADA_CHECKS_REFERENCE_EMAIL
        )
        self.stdout.write(f"The checks log in as {user.delivery_email} (user {user.id}).")

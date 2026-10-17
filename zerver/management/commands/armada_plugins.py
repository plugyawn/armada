from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError
from typing_extensions import override

from zerver.lib.armada_plugin_repository import (
    init_team_plugin_repository,
    set_team_plugin_mirror,
    team_plugin_mirror,
)
from zerver.lib.exceptions import JsonableError
from zerver.lib.management import ZulipBaseCommand


class Command(ZulipBaseCommand):
    help = """Manage an organization's plugins (design/r9/plugins).

init-team-repo makes the `plugins` channel, with every member in it, and
its repository on this server (zerver/lib/armada_plugin_repository.py);
run again, it changes nothing that is there. --mirror sets the git remote
the plugin host keeps up to date with it, such as a private GitHub
repository, which pushes with the server's SSH key; --no-mirror turns
the mirror off.

Usage: ./manage.py armada_plugins init-team-repo -r <realm> [--mirror <address> | --no-mirror]
"""

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        # An action, not a subparser: manage.py appends --traceback after
        # every command's arguments, which only the command's own parser
        # knows.
        parser.add_argument(
            "action",
            choices=["init-team-repo"],
            help="init-team-repo: make the plugins channel and its repository.",
        )
        self.add_realm_args(parser, required=True)
        mirror = parser.add_mutually_exclusive_group()
        mirror.add_argument(
            "--mirror",
            metavar="<address>",
            help="Mirror the repository to this git remote, as git@github.com:runix/team-plugins.git.",
        )
        mirror.add_argument("--no-mirror", action="store_true", help="Stop mirroring it.")

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        realm = self.get_realm(options)
        assert realm is not None
        owner = realm.get_human_owner_users().order_by("id").first()
        if owner is None:
            raise CommandError(f"{realm.string_id} has no owner to make the channel as.")
        try:
            repository = init_team_plugin_repository(realm, acting_user=owner)
            if options["mirror"] is not None:
                set_team_plugin_mirror(repository, options["mirror"])
            elif options["no_mirror"]:
                set_team_plugin_mirror(repository, None)
        except JsonableError as error:
            raise CommandError(error.msg)
        mirror = team_plugin_mirror(repository)
        self.stdout.write(f"#{repository.stream.name}: {repository.repo_cache_path}")
        self.stdout.write(f"Mirrored to {mirror}" if mirror is not None else "Not mirrored")

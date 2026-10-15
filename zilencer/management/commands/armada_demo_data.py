import logging
import time
from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.core.management.base import CommandError
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.lib.management import ZulipBaseCommand
from zerver.models.realms import get_realm


class Command(ZulipBaseCommand):
    help = """Load agent-run fixtures into the development realm's `click` channel.

Armada's agent surfaces need a Codex run to have something to show, and the
Codex runtime is disabled in development.  This command replays scripted
turns through the real admission path, run store and worker turn, so the
topics it creates (all named `demo-*`) look to the web app exactly like the
output of real runs: completed, live, queued, failed, cancelled, waiting on a
human, paused; with artifacts, wall monitors, threads and worktrees.

It is idempotent: every load first removes what an earlier load created.

  ./manage.py armada_demo_data             (re)load the fixtures
  ./manage.py armada_demo_data --reset     remove them, including worktrees
  ./manage.py armada_demo_data --animate   keep emitting activity on the live run

No model is called and nothing touches the network.  Development only.
"""

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Remove the fixtures (messages, runs, monitors, worktrees) and stop.",
        )
        parser.add_argument(
            "--animate",
            action="store_true",
            help="After loading (or on its own with --no-load), emit a tool call on the "
            "live run every few seconds until interrupted, so live surfaces visibly move.",
        )
        parser.add_argument(
            "--no-load",
            action="store_true",
            help="With --animate: animate the already-loaded live run instead of reloading.",
        )

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        if settings.PRODUCTION or not settings.DEVELOPMENT:
            raise CommandError("armada_demo_data only runs in a development environment.")

        # Imported here: these modules pull in test-only dependencies and
        # the whole agent runtime, which no production command should load.
        import time_machine

        from zilencer.lib.armada_demo import DEMO_STREAM, DemoSeeder, remove_demo_data
        from zilencer.lib.armada_demo_content import LIVE, SCENARIOS

        realm = get_realm("zulip")
        if options["reset"]:
            counts = remove_demo_data(realm, remove_workspaces=True)
            self.stdout.write(f"Removed Armada demo data: {counts}")
            return

        if not options["no_load"]:
            started = time.monotonic()
            counts = remove_demo_data(realm, remove_workspaces=False)
            if any(counts.values()):
                self.stdout.write(f"Replacing earlier Armada demo data: {counts}")
            # Fixture time runs on its own clock: each scenario starts some
            # minutes in the past and scripts advance it, so messages, run
            # durations and event timestamps are mutually consistent without
            # editing rows after the fact.
            # The failed-run scenario fails on purpose; the turn logs that
            # with a traceback, which reads like a broken load.
            turn_logger = logging.getLogger("zerver.lib.armada")
            previous_level = turn_logger.level
            turn_logger.setLevel(logging.CRITICAL)
            # The fixtures end with three busy runs, which is exactly the
            # realm's limit; lift it for this process so an unrelated busy
            # run elsewhere in the realm cannot make the load fail.
            run_limit = settings.ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_REALM
            settings.ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_REALM = 0
            try:
                with time_machine.travel(timezone_now(), tick=False) as traveller:
                    seeder = DemoSeeder(realm, traveller)
                    seeder.subscribe_cast()
                    for scenario in SCENARIOS:
                        scenario.seed(seeder)
                    seeder.mark_everything_read()
            finally:
                settings.ARMADA_WORKER_MAX_ACTIVE_RUNS_PER_REALM = run_limit
                turn_logger.setLevel(previous_level)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Loaded Armada demo data in {time.monotonic() - started:.1f}s. "
                    f"Topics in #{DEMO_STREAM}:"
                )
            )
            stream_id = seeder.stream.id
            for scenario in sorted(SCENARIOS, key=lambda scenario: scenario.topic):
                self.stdout.write(
                    f"  /#narrow/channel/{stream_id}-{DEMO_STREAM}/topic/{scenario.topic}\n"
                    f"      {scenario.shows}"
                )

        if options["animate"]:
            from zilencer.lib.armada_demo import animate_live_run

            self.stdout.write("Animating the live run; Ctrl-C to stop.")
            try:
                animate_live_run(realm, LIVE)
            except KeyboardInterrupt:
                self.stdout.write("Stopped. The run stays live.")

import json
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import Any
from unittest import mock

import responses
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from typing_extensions import override

from zerver.lib.armada_plugin_host import ARMADA_PLUGIN_MIRROR_CHECK_SECONDS, ArmadaPluginHost
from zerver.lib.armada_plugin_repository import (
    TEAM_README,
    ArmadaPluginMirrorError,
    _refs,
    check_mirror_address,
    install_team_plugin,
    push_team_plugin_mirror,
    set_team_plugin_mirror,
    team_plugin_mirror,
    team_plugin_repository,
    update_team_plugin,
)
from zerver.lib.armada_plugins import install_plugin, remove_plugin
from zerver.lib.exceptions import JsonableError
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import (
    DefaultStream,
    PluginInstall,
    RealmAuditLog,
    Repository,
    Stream,
    Subscription,
    UserProfile,
)
from zerver.models.realm_audit_logs import AuditLogEventType
from zerver.tests.test_armada_plugin_host import FAKE_KEY, FIXTURE, ImmediateExecutor


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class TeamPluginRepositoryTest(ZulipTestCase):
    """The team's plugins repository (zerver/lib/armada_plugin_repository.py):
    the plugins channel and its repository, its mirror, and installing a
    plugin from a commit made in one of its worktrees, which moves main
    fast-forward and tags the version."""

    @override
    def setUp(self) -> None:
        super().setUp()
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        workdirs = override_settings(ARMADA_TOPIC_WORKDIR_ROOT=str(self.root / "workspaces"))
        workdirs.enable()
        self.addCleanup(workdirs.disable)
        self.hamlet = self.example_user("hamlet")
        self.realm = self.hamlet.realm

    def init(self, *args: str) -> str:
        out = StringIO()
        # As manage.py runs it, with --traceback after the arguments.
        call_command(
            "armada_plugins", "init-team-repo", "--realm", "zulip", *args, "--traceback", stdout=out
        )
        return out.getvalue()

    def origin(self) -> str:
        repository = team_plugin_repository(self.realm)
        assert repository is not None
        return repository.repo_cache_path

    def ref(self, name: str) -> str:
        return subprocess.run(
            ["git", "--git-dir", self.origin(), "rev-parse", "-q", "--verify", name],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()

    def worktree(self, name: str = "fake") -> Path:
        """A topic's worktree: a clone of the team's repository."""
        worktree = self.root / f"worktree-{name}"
        git("clone", "-q", self.origin(), str(worktree))
        return worktree

    def commit(
        self,
        worktree: Path,
        changes: dict[str, Any] | None = None,
        *,
        path: str = "plugins/fake",
        manifest_text: str | None = None,
    ) -> str:
        """The fake plugin in its directory, committed in the worktree."""
        directory = worktree / path
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURE / "armada_fake_plugin.py", directory)
        manifest = {**json.loads((FIXTURE / "armada-plugin.json").read_text()), **(changes or {})}
        (directory / "armada-plugin.json").write_text(manifest_text or json.dumps(manifest))
        git("-C", str(worktree), "add", "-A")
        git("-C", str(worktree), "commit", "-q", "-m", f"Change {path}.")
        return git("-C", str(worktree), "rev-parse", "HEAD")

    def test_init_makes_the_channel_and_its_repository(self) -> None:
        out = self.init()
        stream = Stream.objects.get(realm=self.realm, name="plugins")
        self.assertFalse(stream.invite_only)
        members = set(
            UserProfile.objects.filter(realm=self.realm, is_active=True, is_bot=False)
            .exclude(role=UserProfile.ROLE_GUEST)
            .values_list("id", flat=True)
        )
        subscribed = set(
            Subscription.objects.filter(recipient=stream.recipient, active=True).values_list(
                "user_profile_id", flat=True
            )
        )
        self.assertEqual(subscribed, members)
        self.assertNotIn(self.example_user("polonius").id, subscribed)
        self.assertTrue(DefaultStream.objects.filter(realm=self.realm, stream=stream).exists())
        repository = team_plugin_repository(self.realm)
        assert repository is not None
        self.assertEqual(
            (repository.stream_id, repository.repository_url, repository.clone_status),
            (stream.id, "", Repository.READY),
        )
        self.assertTrue(Path(repository.repo_cache_path).is_relative_to(self.root))
        self.assertEqual(out, f"#plugins: {repository.repo_cache_path}\nNot mirrored\n")
        origin = repository.repo_cache_path
        self.assertEqual(git("--git-dir", origin, "show", "main:README.md") + "\n", TEAM_README)
        self.assertEqual(git("--git-dir", origin, "config", "receive.denyNonFastForwards"), "true")
        # Run again, it changes nothing.
        main = self.ref("main")
        self.assertEqual(self.init(), out)
        self.assertEqual(self.ref("main"), main)
        self.assertEqual(Repository.objects.filter(realm=self.realm, stream=stream).count(), 1)

    def test_init_takes_an_empty_channel_and_refuses_another_repository(self) -> None:
        self.make_stream("plugins", self.realm)
        self.init()
        self.assertIsNotNone(team_plugin_repository(self.realm))
        Repository.objects.filter(realm=self.realm).update(
            repository_url="https://github.com/runix/other"
        )
        with self.assertRaisesRegex(
            CommandError,
            r"^#plugins is already the repository https://github.com/runix/other; rename that",
        ):
            self.init()
        with (
            mock.patch(
                "zerver.models.Realm.get_human_owner_users",
                return_value=UserProfile.objects.none(),
            ),
            self.assertRaisesRegex(CommandError, r"^zulip has no owner to make the channel as\.$"),
        ):
            self.init()

    def test_the_mirror_is_set_changed_and_turned_off(self) -> None:
        self.assertEqual(
            self.init("--mirror", "git@github.com:runix/team-plugins.git").splitlines()[1],
            "Mirrored to git@github.com:runix/team-plugins.git",
        )
        address = "ssh://git@github.com/runix/team-plugins.git"
        self.assertIn(f"Mirrored to {address}", self.init("--mirror", address))
        self.assertEqual(git("--git-dir", self.origin(), "config", "remote.mirror.url"), address)
        self.assertIn(f"Mirrored to {address}", self.init())
        for refused in (
            "https://runix:ghp_secret@github.com/runix/team-plugins.git",
            "https://ghp_secret@github.com/runix/team-plugins.git",
            "ssh://git:hunter2@github.com/runix/team-plugins.git",
        ):
            with self.assertRaisesRegex(CommandError, r"^A mirror's address holds no password"):
                self.init(f"--mirror={refused}")
        with override_settings(ARMADA_PLUGIN_LOCAL_SOURCES=False):
            for refused in (
                "",
                "-oProxyCommand=touch /tmp/pwned",
                "ext::sh -c touch% /tmp/pwned",
                "http://github.com/runix/team-plugins.git",
                "file:///srv/mirror.git",
                "/srv/mirror.git",
            ):
                with self.assertRaisesRegex(CommandError, r"^A mirror is an ssh or https address"):
                    self.init(f"--mirror={refused}")
        self.assertIn(f"Mirrored to {address}", self.init())
        with override_settings(ARMADA_PLUGIN_LOCAL_SOURCES=True):
            self.assertEqual(
                check_mirror_address(" /srv/mirrors/../mirror.git "), "/srv/mirror.git"
            )
        self.assertEqual(self.init("--no-mirror").splitlines()[1], "Not mirrored")
        repository = team_plugin_repository(self.realm)
        assert repository is not None
        self.assertIsNone(team_plugin_mirror(repository))
        # Turned off again, it stays off.
        set_team_plugin_mirror(repository, None)
        self.assertIsNone(team_plugin_mirror(repository))

    def test_a_repository_git_cannot_change_says_so(self) -> None:
        self.init()
        repository = team_plugin_repository(self.realm)
        assert repository is not None
        Repository.objects.filter(id=repository.id).update(
            repo_cache_path=str(self.root / "gone.git")
        )
        repository.refresh_from_db()
        with (
            self.assertLogs("zerver.lib.armada_plugin_repository", "WARNING") as logs,
            self.assertRaisesRegex(JsonableError, r"^The plugins repository could not be changed"),
        ):
            set_team_plugin_mirror(repository, "git@github.com:runix/team-plugins.git")
        self.assertRegex(logs.output[0], rf"git remote in {self.root}/gone\.git failed: ")
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 60)):
            self.assertIsNone(team_plugin_mirror(repository))
        with (
            mock.patch(
                "zerver.lib.armada_plugin_repository._git",
                return_value=subprocess.CompletedProcess([], 1, "", ""),
            ),
            self.assertRaisesRegex(ArmadaPluginMirrorError, r"^Could not list the branches"),
        ):
            _refs(repository.repo_cache_path)

    def test_install_releases_the_commit_tags_it_and_runs_it(self) -> None:
        self.init()
        worktree = self.worktree()
        sha = self.commit(worktree)
        install = install_team_plugin(
            self.hamlet, plugin_id="fake", sha=sha, workdir=worktree, secrets={"key": FAKE_KEY}
        )
        repository = team_plugin_repository(self.realm)
        assert repository is not None
        self.assertEqual(
            (install.source_kind, install.source_repository_id, install.source_path, install.sha),
            (PluginInstall.LOCAL, repository.id, "plugins/fake", sha),
        )
        self.assertEqual(install.installed_by, self.hamlet)
        self.assertEqual(self.ref("main"), sha)
        self.assertEqual(self.ref("refs/tags/fake/v1.0.0^{commit}"), sha)
        self.assertEqual(
            git(
                "--git-dir",
                self.origin(),
                "for-each-ref",
                "--format=%(objecttype)|%(taggername)|%(contents:lines=1)",
                "refs/tags/fake/v1.0.0",
            ),
            "tag|King Hamlet|Fake 1.0.0, released by King Hamlet.",
        )
        self.assertTrue(
            RealmAuditLog.objects.filter(
                modified_user=install.bot, event_type=AuditLogEventType.PLUGIN_INSTALLED
            ).exists()
        )
        # The plugin host runs it from its directory, at that commit.
        plugin_dir = self.root / "plugins-host"
        clock = [1000.0]
        host = ArmadaPluginHost(
            plugin_dir=plugin_dir, clock=lambda: clock[0], executor=ImmediateExecutor()
        )
        self.addCleanup(host.stop_all)
        network = responses.RequestsMock(assert_all_requests_are_fired=False)
        network.add_passthru(re.compile(r"^http://127\.0\.0\.1:[0-9]+/"))
        network.start()
        self.addCleanup(network.stop)

        def running() -> bool:
            install.refresh_from_db()
            return install.state == PluginInstall.RUNNING

        self.tick_until(host, clock, running)
        process = host.supervised[install.id].process
        assert process is not None
        self.assertEqual(
            Path(f"/proc/{process.pid}/cwd").readlink(),
            plugin_dir / f"fake-{install.id}" / sha / "plugins" / "fake",
        )
        # Once installed, it is updated, not installed again.
        with self.assertRaisesRegex(JsonableError, r"^Fake is already installed\.$"):
            install_team_plugin(self.hamlet, plugin_id="fake", sha=sha, workdir=worktree)

    def tick_until(
        self, host: ArmadaPluginHost, clock: list[float], done: Callable[[], bool]
    ) -> None:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            host.tick()
            if done():
                return
            clock[0] += 0.25
            time.sleep(0.02)
        raise AssertionError("the host did not get there")

    def test_a_commit_that_is_not_a_fast_forward_is_refused_until_rebased(self) -> None:
        self.init()
        fake_worktree = self.worktree()
        other_worktree = self.worktree("other")
        install_team_plugin(
            self.hamlet,
            plugin_id="fake",
            sha=self.commit(fake_worktree),
            workdir=fake_worktree,
            secrets={"key": FAKE_KEY},
        )
        main = self.ref("main")
        # The other plugin was made from main as it was before.
        other = self.commit(other_worktree, {"id": "other"}, path="plugins/other")
        with self.assertRaisesRegex(
            JsonableError,
            rf"^{other[:12]} is not a fast-forward of main, which has 1 commit it lacks\."
            r" Rebase onto main \(git fetch origin, then git rebase origin/main\), check the"
            r" plugin again, and install the new commit\.$",
        ):
            install_team_plugin(self.hamlet, plugin_id="other", sha=other, workdir=other_worktree)
        self.assertEqual((self.ref("main"), self.ref("refs/tags/other/v1.0.0")), (main, ""))
        self.assertFalse(PluginInstall.objects.filter(plugin_id="other").exists())
        # Doing what it says works.
        git("-C", str(other_worktree), "fetch", "-q", "origin")
        git("-C", str(other_worktree), "rebase", "-q", "origin/main")
        rebased = git("-C", str(other_worktree), "rev-parse", "HEAD")
        install_team_plugin(self.hamlet, plugin_id="other", sha=rebased, workdir=other_worktree)
        self.assertEqual(self.ref("main"), rebased)
        self.assertEqual(self.ref("refs/tags/other/v1.0.0^{commit}"), rebased)

    def test_a_new_version_is_released_and_a_tagged_one_is_not_moved(self) -> None:
        self.init()
        worktree = self.worktree()
        first = self.commit(worktree)
        install = install_team_plugin(
            self.hamlet, plugin_id="fake", sha=first, workdir=worktree, secrets={"key": FAKE_KEY}
        )
        unbumped = self.commit(worktree, {"description": "Changed, not bumped."})
        with self.assertRaisesRegex(
            JsonableError,
            rf"^Fake 1\.0\.0 is already tagged at {first[:12]}: raise the version in"
            r" plugins/fake/armada-plugin\.json\.$",
        ):
            update_team_plugin(self.hamlet, install, sha=unbumped, workdir=worktree)
        self.assertEqual(self.ref("main"), first)
        bumped = self.commit(worktree, {"version": "1.1.0"})
        cordelia = self.example_user("cordelia")
        update_team_plugin(cordelia, install, sha=bumped, workdir=worktree, for_user=self.hamlet)
        install.refresh_from_db()
        self.assertEqual((install.sha, install.version), (bumped, "1.1.0"))
        self.assertEqual(self.ref("main"), bumped)
        self.assertEqual(self.ref("refs/tags/fake/v1.1.0^{commit}"), bumped)
        self.assertEqual(self.ref("refs/tags/fake/v1.0.0^{commit}"), first)
        updated = RealmAuditLog.objects.get(
            modified_user=install.bot, event_type=AuditLogEventType.PLUGIN_UPDATED
        )
        self.assertEqual(
            (updated.acting_user, updated.extra_data["from_sha"], updated.extra_data["to_sha"]),
            (cordelia, first, bumped),
        )
        self.assertEqual(updated.extra_data["for_user_id"], self.hamlet.id)
        # A plugin from elsewhere is not updated from the team's repository,
        # and a removed one not at all.
        source = self.root / "elsewhere"
        shutil.copytree(FIXTURE, source)
        git("init", "-q", str(source))
        git("-C", str(source), "add", "-A")
        git("-C", str(source), "commit", "-q", "-m", "Elsewhere.")
        elsewhere = install_plugin(
            self.hamlet,
            manifest={**json.loads((FIXTURE / "armada-plugin.json").read_text()), "id": "away"},
            source_kind="local",
            source=str(source),
            sha=git("-C", str(source), "rev-parse", "HEAD"),
        )
        with self.assertRaisesRegex(
            JsonableError, r"^Fake does not come from the team's plugins repository\.$"
        ):
            update_team_plugin(self.hamlet, elsewhere, sha=bumped)
        remove_plugin(self.hamlet, install)
        with self.assertRaisesRegex(JsonableError, r"^Fake is removed; undo the removal first\.$"):
            update_team_plugin(self.hamlet, install, sha=bumped)

    def test_what_a_commit_must_hold(self) -> None:
        with self.assertRaisesRegex(JsonableError, r"^This organization has no plugins repository"):
            install_team_plugin(self.hamlet, plugin_id="fake", sha="a" * 40)
        self.init()
        worktree = self.worktree()
        main = self.ref("main")
        sha = self.commit(worktree)
        refusals: list[tuple[dict[str, Any], str]] = [
            ({"plugin_id": "Fake!", "sha": sha}, r"^A plugin's id is lowercase letters"),
            ({"plugin_id": "fake", "sha": "abc"}, r"^A plugin is installed at a commit"),
            (
                {"plugin_id": "fake", "sha": "a" * 40},
                r"^There is no commit aaaaaaaaaaaa in the worktree or the plugins repository",
            ),
            (
                {"plugin_id": "other", "sha": sha},
                rf"^{sha[:12]} has no plugins/other/armada-plugin\.json: a plugin's code is in"
                r" plugins/other/\.$",
            ),
        ]
        for arguments, message in refusals:
            with self.assertRaisesRegex(JsonableError, message):
                install_team_plugin(self.hamlet, workdir=worktree, **arguments)
        broken = self.commit(worktree, manifest_text="{")
        with self.assertRaisesRegex(
            JsonableError, r"^plugins/fake/armada-plugin\.json is not JSON: "
        ):
            install_team_plugin(self.hamlet, plugin_id="fake", sha=broken, workdir=worktree)
        invalid = self.commit(worktree, {"glyph": "nope"})
        with self.assertRaisesRegex(JsonableError, r"^The plugin's manifest is not valid: "):
            install_team_plugin(self.hamlet, plugin_id="fake", sha=invalid, workdir=worktree)
        misnamed = self.commit(worktree, {"id": "fake"}, path="plugins/other")
        with self.assertRaisesRegex(
            JsonableError,
            r"^plugins/other/armada-plugin\.json names the plugin fake; a plugin's directory is"
            r" named after it\.$",
        ):
            install_team_plugin(self.hamlet, plugin_id="other", sha=misnamed, workdir=worktree)
        with self.assertRaisesRegex(JsonableError, r"^Guests cannot see, install"):
            install_team_plugin(
                self.example_user("polonius"), plugin_id="fake", sha=sha, workdir=worktree
            )
        self.assertEqual(self.ref("main"), main)
        self.assertEqual(self.ref("refs/tags/fake/v1.0.0"), "")

    def test_someone_elses_draft_holds_the_id_and_ones_own_becomes_the_install(self) -> None:
        self.init()
        worktree = self.worktree()
        sha = self.commit(worktree)
        main = self.ref("main")
        repository = team_plugin_repository(self.realm)
        assert repository is not None
        manifest = json.loads((FIXTURE / "armada-plugin.json").read_text())
        draft = install_plugin(
            self.example_user("cordelia"),
            manifest=manifest,
            source_kind="local",
            repository_id=repository.id,
            path="plugins/fake",
            sha=main,
            draft=True,
        )
        with self.assertRaisesRegex(JsonableError, r"^Fake is being made, and holds its id\.$"):
            install_team_plugin(self.hamlet, plugin_id="fake", sha=sha, workdir=worktree)
        self.assertEqual((self.ref("main"), self.ref("refs/tags/fake/v1.0.0")), (main, ""))
        install = install_team_plugin(
            self.example_user("cordelia"),
            plugin_id="fake",
            sha=sha,
            workdir=worktree,
            secrets={"key": FAKE_KEY},
        )
        self.assertEqual(
            (install.id, install.bot_id, install.state, install.sha),
            (draft.id, draft.bot_id, PluginInstall.STARTING, sha),
        )

    def test_the_plugin_host_keeps_the_mirror_up_to_date(self) -> None:
        mirror = self.root / "mirror.git"
        git("init", "-q", "--bare", str(mirror))
        with override_settings(ARMADA_PLUGIN_LOCAL_SOURCES=True):
            self.init("--mirror", str(mirror))
        clock = [1000.0]
        host = ArmadaPluginHost(
            plugin_dir=self.root / "plugins-host",
            clock=lambda: clock[0],
            executor=ImmediateExecutor(),
        )
        self.addCleanup(host.stop_all)

        def mirrored(name: str) -> str:
            return subprocess.run(
                ["git", "--git-dir", str(mirror), "rev-parse", "-q", "--verify", name],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()

        with mock.patch(
            "zerver.lib.armada_plugin_host.push_team_plugin_mirror",
            wraps=push_team_plugin_mirror,
        ) as push:
            host.tick()
            self.assertEqual(mirrored("main"), self.ref("main"))
            # Nothing changed, nothing is pushed.
            for _ in range(3):
                clock[0] += ARMADA_PLUGIN_MIRROR_CHECK_SECONDS
                host.tick()
            self.assertEqual(push.call_count, 1)
            worktree = self.worktree()
            sha = self.commit(worktree)
            install = install_team_plugin(
                self.hamlet, plugin_id="fake", sha=sha, workdir=worktree, secrets={"key": FAKE_KEY}
            )
            # Only its repository is watched here, not its process.
            PluginInstall.objects.filter(id=install.id).update(disabled=True)
            # It is noticed within the half minute.
            host.tick()
            self.assertEqual(push.call_count, 1)
            clock[0] += ARMADA_PLUGIN_MIRROR_CHECK_SECONDS
            host.tick()
            self.assertEqual(push.call_count, 2)
            self.assertEqual(
                (mirrored("main"), mirrored("refs/tags/fake/v1.0.0^{commit}")), (sha, sha)
            )

            # A push that fails is tried again after a minute, then two.
            shutil.rmtree(mirror)
            bumped = self.commit(worktree, {"version": "1.0.1"})
            install.refresh_from_db()
            update_team_plugin(self.hamlet, install, sha=bumped, workdir=worktree)
            clock[0] += ARMADA_PLUGIN_MIRROR_CHECK_SECONDS
            host.tick()
            self.assertEqual(push.call_count, 3)
            with self.assertLogs("zerver.lib.armada_plugin_host", "WARNING") as logs:
                host.tick()
            self.assertRegex(
                logs.output[0],
                rf"Could not push the plugins repository {re.escape(self.origin())} to its mirror: ",
            )
            clock[0] += 59
            host.tick()
            self.assertEqual(push.call_count, 3)
            clock[0] += 1
            with self.assertLogs("zerver.lib.armada_plugin_host", "WARNING"):
                host.tick()
                self.assertEqual(push.call_count, 4)
                host.tick()
            self.assertEqual(host.mirrors[install.source_repository_id or 0].failures, 2)
            git("init", "-q", "--bare", str(mirror))
            clock[0] += 120
            host.tick()
            host.tick()
            self.assertEqual(push.call_count, 5)
            self.assertEqual(host.mirrors[install.source_repository_id or 0].failures, 0)
            self.assertEqual(mirrored("refs/tags/fake/v1.0.1^{commit}"), bumped)

        # A push still on its way is waited for; a repository whose refs
        # cannot be read is passed over, and said so.
        repository_id = install.source_repository_id or 0
        waiting = mock.Mock(done=mock.Mock(return_value=False))
        host.mirrors[repository_id].pushing = waiting
        clock[0] += 3600
        host.tick()
        self.assertIs(host.mirrors[repository_id].pushing, waiting)
        host.mirrors[repository_id].pushing = None
        with (
            mock.patch(
                "zerver.lib.armada_plugin_host.team_plugin_mirror_is_behind",
                side_effect=ArmadaPluginMirrorError("Could not list the branches and tags of it."),
            ),
            self.assertLogs("zerver.lib.armada_plugin_host", "WARNING") as logs,
        ):
            host.tick()
        self.assertEqual(
            logs.output,
            ["WARNING:zerver.lib.armada_plugin_host:Could not list the branches and tags of it."],
        )

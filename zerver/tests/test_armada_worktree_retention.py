import os
import subprocess
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID

from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.lib import armada_worktree_retention
from zerver.lib.armada import ARMADA_DEFAULT_STREAM, ARMADA_EMBEDDED_BOT_SERVICE
from zerver.lib.armada_worker import ensure_armada_workspace_node
from zerver.lib.armada_worktree_retention import (
    maybe_reap_retired_armada_worktrees,
    reap_retired_armada_worktrees,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.topic import RESOLVED_TOPIC_PREFIX
from zerver.models import AgentMonitor, AgentRun, UserProfile, Workspace


def git(workdir: str, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", workdir, *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


class ArmadaWorktreeRetentionTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.bot = self.create_test_bot(
            "onix",
            self.owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        self.subscribe(self.owner, ARMADA_DEFAULT_STREAM)
        self.subscribe(self.bot, ARMADA_DEFAULT_STREAM)
        topic_root = TemporaryDirectory()
        self.addCleanup(topic_root.cleanup)
        root_setting = self.settings(ARMADA_TOPIC_WORKDIR_ROOT=topic_root.name)
        root_setting.enable()
        self.addCleanup(root_setting.disable)

    def make_worktree(self, topic: str, *, message: bool = True) -> Workspace:
        if message:
            self.send_stream_message(self.owner, ARMADA_DEFAULT_STREAM, "hello", topic_name=topic)
        return ensure_armada_workspace_node(self.bot, stream=ARMADA_DEFAULT_STREAM, topic=topic)

    def commit(self, workspace_node: Workspace, name: str) -> str:
        Path(workspace_node.workdir, name).write_text("work\n")
        git(workspace_node.workdir, "add", name)
        git(workspace_node.workdir, "commit", "--quiet", "-m", f"Add {name}.")
        return git(workspace_node.workdir, "rev-parse", "HEAD")

    def resolve(self, workspace_node: Workspace) -> None:
        Workspace.objects.filter(id=workspace_node.id).update(
            topic_name=RESOLVED_TOPIC_PREFIX + workspace_node.topic_name
        )

    def age(self, workspace_node: Workspace, days: int = 8) -> None:
        Workspace.objects.filter(id=workspace_node.id).update(
            date_updated=timezone_now() - timedelta(days=days)
        )

    def origin_branch_head(self, workspace_node: Workspace) -> str:
        assert workspace_node.project is not None
        return subprocess.run(
            [
                "git",
                "--git-dir",
                workspace_node.project.repo_cache_path,
                "rev-parse",
                f"refs/heads/{workspace_node.branch_name}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_resolved_topic_loses_its_worktree_and_gets_it_back_on_its_next_run(self) -> None:
        workspace_node = self.make_worktree("retire me")
        work = self.commit(workspace_node, "notes.txt")
        # Ignored build outputs go with the worktree.
        Path(workspace_node.workdir, ".armada").mkdir()
        Path(workspace_node.workdir, ".armada", "cache").write_text("build output\n")
        self.resolve(workspace_node)
        self.age(workspace_node)

        with self.assertLogs("zerver.lib.armada_worktree_retention", level="INFO") as logs:
            self.assertEqual(reap_retired_armada_worktrees(), 1)

        self.assertEqual(
            logs.output,
            [
                "INFO:zerver.lib.armada_worktree_retention:Removed the worktree of "
                f"{ARMADA_DEFAULT_STREAM} > {RESOLVED_TOPIC_PREFIX}retire me (resolved): "
                f"{workspace_node.workdir}"
            ],
        )
        self.assertFalse(os.path.exists(workspace_node.workdir))
        workspace_node.refresh_from_db()
        self.assertEqual(workspace_node.lifecycle_status, Workspace.ARCHIVED)
        # The work is in the channel's repository...
        self.assertEqual(self.origin_branch_head(workspace_node), work)

        # ...and the Files view says what happened.
        self.login_user(self.owner)
        result = self.client_get(
            f"/json/armada/workspaces/{UUID(str(workspace_node.uuid))}",
            intentionally_undocumented=True,
        )
        self.assert_json_error(
            result,
            "This topic is done, so its worktree was removed. It comes back with the next run here.",
            status_code=409,
        )

        # A run in the topic brings the worktree back, with its work.
        restored = ensure_armada_workspace_node(
            self.bot, stream=ARMADA_DEFAULT_STREAM, topic=workspace_node.topic_name
        )
        self.assertEqual(restored.workdir, workspace_node.workdir)
        self.assertEqual(restored.lifecycle_status, Workspace.ACTIVE)
        self.assertEqual(git(restored.workdir, "rev-parse", "HEAD"), work)
        self.assertEqual(Path(restored.workdir, "notes.txt").read_text(), "work\n")

    def test_deleted_and_merged_topics_lose_their_worktrees(self) -> None:
        deleted = self.make_worktree("deleted topic", message=False)
        # Its branch never moved from the base: nothing in it to lose.
        merged = self.make_worktree("merged topic")
        self.age(deleted)
        self.age(merged)

        with self.assertLogs("zerver.lib.armada_worktree_retention", level="INFO") as logs:
            self.assertEqual(reap_retired_armada_worktrees(), 2)

        self.assertEqual(
            sorted(line.rsplit(" (", 1)[1].split(")")[0] for line in logs.output),
            ["deleted", "merged"],
        )
        self.assertFalse(os.path.exists(deleted.workdir))
        self.assertFalse(os.path.exists(merged.workdir))

    def test_worktrees_that_may_still_matter_stay(self) -> None:
        recent = self.make_worktree("resolved yesterday")
        self.resolve(recent)
        self.age(recent, days=1)

        dirty = self.make_worktree("dirty")
        self.resolve(dirty)
        self.age(dirty)
        Path(dirty.workdir, "draft.txt").write_text("not committed\n")

        running = self.make_worktree("running")
        self.resolve(running)
        self.age(running)
        AgentRun.objects.create(
            realm=self.owner.realm,
            bot_profile=self.bot,
            trigger_message_id=4242,
            stream_name=running.stream_name,
            topic_name=RESOLVED_TOPIC_PREFIX + running.topic_name,
            workspace_node=running,
            status=AgentRun.RUNNING,
        )
        AgentRun.objects.filter(trigger_message_id=4242).update(
            date_updated=timezone_now() - timedelta(days=30)
        )

        walled = self.make_worktree("walled")
        self.resolve(walled)
        self.age(walled)
        AgentMonitor.objects.create(
            realm=self.owner.realm,
            bot_profile=self.bot,
            stream_name=walled.stream_name,
            topic_name=RESOLVED_TOPIC_PREFIX + walled.topic_name,
            ask="Watch the build.",
            status=AgentMonitor.LIVE,
        )

        in_progress = self.make_worktree("in progress")
        before = self.origin_branch_head(in_progress)
        self.commit(in_progress, "feature.txt")
        self.age(in_progress)

        with self.settings(ARMADA_WORKTREE_RETENTION_DAYS=7):
            self.assertEqual(reap_retired_armada_worktrees(), 0)

        for workspace_node in (recent, dirty, running, walled, in_progress):
            self.assertTrue(os.path.isdir(workspace_node.workdir), workspace_node.topic_name)
            workspace_node.refresh_from_db()
            self.assertEqual(workspace_node.lifecycle_status, Workspace.ACTIVE)
        # A topic still in progress keeps its branch as it is: not pushed.
        self.assertEqual(self.origin_branch_head(in_progress), before)

    def test_retention_can_be_turned_off(self) -> None:
        workspace_node = self.make_worktree("kept for ever")
        self.resolve(workspace_node)
        self.age(workspace_node, days=365)

        with self.settings(ARMADA_WORKTREE_RETENTION_DAYS=0):
            self.assertEqual(reap_retired_armada_worktrees(), 0)
        self.assertTrue(os.path.isdir(workspace_node.workdir))

    def test_a_pass_runs_at_most_once_an_hour(self) -> None:
        with (
            patch.object(armada_worktree_retention, "armada_worktree_retention_last_pass", None),
            patch.object(armada_worktree_retention, "reap_retired_armada_worktrees") as reap,
        ):
            for seconds in (0, 60, 3599, 3600):
                maybe_reap_retired_armada_worktrees(current_time=seconds)
        self.assertEqual(reap.call_count, 2)

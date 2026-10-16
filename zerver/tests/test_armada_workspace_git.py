import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import orjson
from typing_extensions import override

from zerver.lib.armada import (
    ARMADA_DEFAULT_STREAM,
    ARMADA_DEFAULT_TOPIC,
    ARMADA_EMBEDDED_BOT_SERVICE,
)
from zerver.lib.armada_worker import ArmadaDatabaseWorkerRunStore
from zerver.lib.armada_workspace_git import (
    ARMADA_WORKSPACE_GIT_MAX_AGE_SECONDS,
    read_workspace_git_state,
    refresh_workspace_git_state,
    workspace_git_context,
    workspace_git_refresh,
)
from zerver.lib.armada_workspace_resources import refresh_workspace_snapshot
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserProfile
from zerver.models.bots import AgentRun, Workspace


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, timeout=10
    ).stdout.strip()


def commit(repo: Path, message: str) -> None:
    git(
        repo,
        "-c",
        "user.email=armada@example.com",
        "-c",
        "user.name=Onix",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        message,
    )


class GitCommands:
    """Counts the git commands a refresh runs, by their subcommand."""

    def __init__(self) -> None:
        self.real_run = subprocess.run
        self.commands: list[str] = []

    def __call__(self, command: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[str]":
        self.commands.append(command[3])
        return self.real_run(command, **kwargs)


class ArmadaWorkspaceGitTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.example_user("hamlet")
        self.subscribe(self.owner, ARMADA_DEFAULT_STREAM)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "click-option-groups"
        self.repo.mkdir()
        git(self.repo, "init", "--quiet", "--initial-branch=option-groups")
        (self.repo / "README.md").write_text("hello\n")
        git(self.repo, "add", "README.md")
        commit(self.repo, "First commit")
        commit(self.repo, "Second commit")
        self.node = Workspace.objects.create(
            realm=self.owner.realm,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            workdir=str(self.repo),
            branch_name="option-groups",
        )

    def refresh(self) -> list[str]:
        commands = GitCommands()
        with patch("zerver.lib.armada_worker.subprocess.run", side_effect=commands):
            refresh_workspace_git_state(self.node.id)
        self.node.refresh_from_db()
        return commands.commands

    def served(self) -> dict[str, object]:
        return workspace_git_context(self.node, workdir=str(self.repo), branch="Option-Groups")

    def test_facts_are_read_into_the_row_and_served_from_it(self) -> None:
        (self.repo / "README.md").write_text("changed\n")
        (self.repo / "new.txt").write_text("new\n")
        self.assertEqual(self.refresh(), ["status", "log", "rev-list"])

        head = git(self.repo, "rev-parse", "HEAD")
        served = self.served()
        last_commit = served.pop("last_commit")
        assert isinstance(last_commit, dict)
        self.assertEqual(
            served,
            {
                "available": True,
                "workdir": str(self.repo),
                "repo_name": "click-option-groups",
                "worktree_name": "click-option-groups",
                "branch": "option-groups",
                "head": head[:7],
                "head_short": head[:7],
                "upstream": "",
                "ahead": 0,
                "behind": 0,
                "dirty": True,
                "changed_count": 2,
                "changed_files": [
                    {"status": "M", "kind": "modified", "path": "README.md"},
                    {"status": "??", "kind": "untracked", "path": "new.txt"},
                ],
                "changed_files_truncated": False,
                "detached": False,
                "branch_matches_topic": True,
                "commit_count": 2,
            },
        )
        self.assertEqual(last_commit["sha"], head[:7])
        self.assertEqual(last_commit["subject"], "Second commit")
        self.assertEqual(last_commit["author"], "Onix")
        self.assertIsInstance(last_commit["timestamp"], int)

    def test_commits_are_counted_from_the_previous_count(self) -> None:
        self.refresh()
        # HEAD has not moved: only the status is read.
        (self.repo / "README.md").write_text("changed\n")
        self.assertEqual(self.refresh(), ["status"])
        self.assertEqual(self.node.git_state["changed_count"], 1)

        commit(self.repo, "Third commit")
        commit(self.repo, "Fourth commit")
        # The count comes from the commits between the two heads, never
        # from walking the whole history again.
        commands = self.refresh()
        self.assertEqual(commands, ["status", "log", "rev-list"])
        self.assertEqual(self.node.git_state["commit_count"], 4)
        self.assertEqual(self.node.git_state["last_commit"]["subject"], "Fourth commit")

        # A rewrite: two commits dropped, one added.
        git(self.repo, "reset", "--quiet", "--hard", "HEAD~2")
        commit(self.repo, "Another third commit")
        self.refresh()
        self.assertEqual(self.node.git_state["commit_count"], 3)
        self.assertEqual(int(git(self.repo, "rev-list", "--count", "HEAD")), 3)

        # A previous head git no longer knows: counted again in full.
        self.node.git_state = {**self.node.git_state, "head_oid": "0" * 40, "commit_count": 99}
        self.node.save(update_fields=["git_state"])
        with self.assertLogs("zerver.lib.armada_worker", level="WARNING"):
            self.refresh()
        self.assertEqual(self.node.git_state["commit_count"], 3)

    def test_unborn_head_has_no_commit_facts(self) -> None:
        empty = Path(self.temp.name) / "empty"
        empty.mkdir()
        git(empty, "init", "--quiet")
        commands = GitCommands()
        with patch("zerver.lib.armada_worker.subprocess.run", side_effect=commands):
            state = read_workspace_git_state(str(empty), isolation_username=None, previous={})
        self.assertEqual(commands.commands, ["status"])
        self.assertTrue(state["available"])
        self.assertNotIn("last_commit", state)
        self.assertNotIn("commit_count", state)

    def test_changed_files_are_structured_and_bounded(self) -> None:
        status_output = "\n".join(
            [
                "# branch.oid (initial)",
                "# branch.head topic-a",
                "1 .M N... 100644 100644 100644 a b modified.txt",
                "1 A. N... 000000 100644 100644 a b added.txt",
                "1 D. N... 100644 000000 000000 a b deleted.txt",
                "2 R. N... 100644 100644 100644 a b R100 renamed.txt\told-name.txt",
                "u UU N... 100644 100644 100644 100644 a b c conflicted.txt",
                *(f"? untracked-{i}.txt" for i in range(60)),
            ]
        )
        with patch("zerver.lib.armada_workspace_git.armada_git_output", return_value=status_output):
            state = read_workspace_git_state("/repo", isolation_username=None, previous={})
        self.assertEqual(state["changed_count"], 65)
        self.assertTrue(state["changed_files_truncated"])
        changed_files = state["changed_files"]
        assert isinstance(changed_files, list)
        self.assert_length(changed_files, 50)
        self.assertEqual(
            changed_files[:6],
            [
                {"status": "M", "kind": "modified", "path": "modified.txt"},
                {"status": "A", "kind": "added", "path": "added.txt"},
                {"status": "D", "kind": "deleted", "path": "deleted.txt"},
                {"status": "R", "kind": "renamed", "path": "renamed.txt"},
                {"status": "U", "kind": "changed", "path": "conflicted.txt"},
                {"status": "??", "kind": "untracked", "path": "untracked-0.txt"},
            ],
        )

    def test_a_worktree_git_cannot_read_is_unavailable(self) -> None:
        self.node.workdir = str(Path(self.temp.name) / "gone")
        self.node.save(update_fields=["workdir"])
        refresh_workspace_git_state(self.node.id)
        self.node.refresh_from_db()
        self.assertEqual(self.node.git_state["available"], False)
        self.assertEqual(
            workspace_git_context(self.node, workdir=self.node.workdir, branch="option-groups"),
            {"available": False, "workdir": self.node.workdir},
        )
        self.assertEqual(
            workspace_git_context(self.node, workdir="", branch="option-groups"),
            {"available": False},
        )
        self.assertFalse(refresh_workspace_git_state(0))

    def test_changed_facts_are_announced_and_older_ones_never_win(self) -> None:
        with patch("zerver.actions.armada.send_armada_workspace_changed_event") as announce:
            self.assertTrue(refresh_workspace_git_state(self.node.id))
            announce.assert_called_once()
            self.assertEqual(announce.call_args.args[1], self.node.content_revision)

            announce.reset_mock()
            self.assertFalse(refresh_workspace_git_state(self.node.id))
            announce.assert_not_called()

            # Facts some other process read later than this refresh did.
            self.node.refresh_from_db()
            later = {**self.node.git_state, "observed_at": time.time() + 60, "ahead": 7}
            Workspace.objects.filter(id=self.node.id).update(git_state=later)
            (self.repo / "README.md").write_text("changed\n")
            self.assertFalse(refresh_workspace_git_state(self.node.id))
            announce.assert_not_called()
        self.node.refresh_from_db()
        self.assertEqual(self.node.git_state, later)

    def test_topic_context_runs_no_git(self) -> None:
        self.refresh()
        self.login_user(self.owner)
        topic = {"stream": ARMADA_DEFAULT_STREAM, "topic": ARMADA_DEFAULT_TOPIC}
        with patch("subprocess.Popen.__init__", side_effect=AssertionError("a subprocess")):
            context = self.assert_json_success(
                self.client_get(
                    "/json/armada/topic-context", topic, intentionally_undocumented=True
                )
            )["armada_topic_context"]
            with_runs = self.assert_json_success(
                self.client_get(
                    "/json/armada/runs",
                    {**topic, "include_context": orjson.dumps(True).decode()},
                    intentionally_undocumented=True,
                )
            )["armada_topic_context"]
        self.assertEqual(context["git"], self.served())
        self.assertEqual(with_runs["git"], self.served())
        self.assertEqual(context["git"]["commit_count"], 2)

    def test_missing_or_old_facts_are_asked_for_once(self) -> None:
        self.login_user(self.owner)
        topic = {"stream": ARMADA_DEFAULT_STREAM, "topic": ARMADA_DEFAULT_TOPIC}

        def git_context() -> dict[str, object]:
            with self.captureOnCommitCallbacks(execute=True):
                result = self.client_get(
                    "/json/armada/topic-context", topic, intentionally_undocumented=True
                )
            return self.assert_json_success(result)["armada_topic_context"]["git"]

        with patch.object(workspace_git_refresh, "start") as start:
            self.assertEqual(git_context(), {"available": False, "workdir": str(self.repo)})
            start.assert_called_once_with(self.node.id)
            # Another reader within the gate asks for nothing more.
            git_context()
            start.assert_called_once_with(self.node.id)

        self.refresh()
        with (
            patch.object(workspace_git_refresh, "start") as start,
            patch("zerver.lib.armada_workspace_git.cache.add", return_value=True),
        ):
            self.assertTrue(git_context()["available"])
            start.assert_not_called()

            old = time.time() - ARMADA_WORKSPACE_GIT_MAX_AGE_SECONDS
            Workspace.objects.filter(id=self.node.id).update(
                git_state={**self.node.git_state, "observed_at": old}
            )
            self.assertTrue(git_context()["available"])
            start.assert_called_once_with(self.node.id)

    def test_changes_to_the_worktree_ask_for_a_refresh(self) -> None:
        agent = self.create_test_bot(
            "armada-git",
            self.owner,
            full_name="Onix",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name=ARMADA_EMBEDDED_BOT_SERVICE,
        )
        AgentRun.objects.create(
            realm=self.owner.realm,
            bot_profile=agent,
            requesting_user=self.owner,
            trigger_message_id=4242,
            stream_name=ARMADA_DEFAULT_STREAM,
            topic_name=ARMADA_DEFAULT_TOPIC,
            status=AgentRun.RUNNING,
            workspace_node=self.node,
        )
        store = ArmadaDatabaseWorkerRunStore()
        bot_handler = SimpleNamespace(user_profile=agent)
        with patch.object(workspace_git_refresh, "start") as start:
            with self.captureOnCommitCallbacks(execute=True):
                store.record_event(bot_handler, 4242, "transcript", summary="Thinking", payload={})
            start.assert_not_called()
            with self.captureOnCommitCallbacks(execute=True):
                store.record_event(
                    bot_handler,
                    4242,
                    "tool_results",
                    summary="Ran tools",
                    payload={"tool_count": 1},
                )
            start.assert_called_once_with(self.node.id)

            # A fresh look at the worktree, after a run or a write.
            start.reset_mock()
            with self.captureOnCommitCallbacks(execute=True):
                refresh_workspace_snapshot(self.node)
            start.assert_called_once_with(self.node.id)

import os
import shutil
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from types import ModuleType

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CI_DIR = os.path.join(ROOT, "tools/armada/ci")


def load_script(name: str) -> ModuleType:
    loader = SourceFileLoader(name.replace("-", "_"), os.path.join(CI_DIR, name))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


# Built at run time, so that this file holds no credential-shaped line.
PLANTED = {
    "GitHub token": "gh" + "p_" + "a1B2" * 9,
    "DigitalOcean token": "do" + "p_v1_" + "0f" * 32,
    "AWS access key": "AK" + "IA" + "Q" * 16,
    "private key": "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    "Anthropic key": "sk-" + "ant-api03-" + "x" * 90,
}


def patch_adding(path: str, lines: list[str], commit: str = "c" * 40) -> list[str]:
    return [
        "commit:" + commit,
        "",
        f"diff --git a/{path} b/{path}",
        f"--- a/{path}",
        f"+++ b/{path}",
        "@@ -1,0 +2,2 @@",
        *lines,
    ]


class SecretsScanTest(unittest.TestCase):
    scan = load_script("secrets-scan")

    def test_each_planted_credential_is_found_in_an_added_line(self) -> None:
        for kind, credential in PLANTED.items():
            patch = patch_adding("zproject/dev_settings.py", [f'+TOKEN = "{credential}"'])
            self.assertEqual(
                list(self.scan.scan_patch(patch)),
                [self.scan.Finding("c" * 40, "zproject/dev_settings.py", kind)],
            )

    def test_an_added_line_that_looks_like_a_header_keeps_the_path(self) -> None:
        patch = patch_adding(
            "docs/notes.md", ["+++ b/elsewhere.md", "+" + PLANTED["AWS access key"]]
        )
        self.assertEqual(
            list(self.scan.scan_patch(patch)),
            [self.scan.Finding("c" * 40, "docs/notes.md", "AWS access key")],
        )

    def test_removed_and_context_lines_and_lookalikes_pass(self) -> None:
        patch = patch_adding(
            "docs/armada/deploy.md",
            [
                "-" + PLANTED["GitHub token"],
                " " + PLANTED["DigitalOcean token"],
                "+The token lives in ~/.config/armada/digitalocean.token.",
                "+desk-proj-123 and task-ant-colony are not keys",
            ],
        )
        self.assertEqual(list(self.scan.scan_patch(patch)), [])


class LicensedGuardTest(unittest.TestCase):
    def run_guard(self, planted: list[str]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            index = os.path.join(directory, "index")
            git_index = subprocess.check_output(
                ["git", "rev-parse", "--path-format=absolute", "--git-path", "index"],
                cwd=ROOT,
                text=True,
            ).strip()
            shutil.copy(git_index, index)
            env = {**os.environ, "GIT_INDEX_FILE": index}
            blob = subprocess.check_output(
                ["git", "rev-parse", "HEAD:README.md"], cwd=ROOT, text=True
            ).strip()
            for path in planted:
                subprocess.check_call(
                    ["git", "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"],
                    cwd=ROOT,
                    env=env,
                )
            return subprocess.run(
                [os.path.join(CI_DIR, "licensed-guard")],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_the_tree_tracks_only_the_readmes_and_the_list(self) -> None:
        result = self.run_guard([])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_tracked_sprite_or_font_fails(self) -> None:
        planted = ["web/sprites/licensed/sv/0095.png", "static/fonts/soehne-buch.woff2"]
        result = self.run_guard(planted)
        self.assertEqual(result.returncode, 1)
        for path in planted:
            self.assertIn(path, result.stderr)


class BackendTestsTest(unittest.TestCase):
    modules = load_script("backend-tests")

    def test_changed_files_select_their_tests(self) -> None:
        changed = [
            "zerver/lib/topic.py",
            "zerver/views/streams.py",
            "zerver/lib/armada_worker.py",
            "zerver/tests/test_armada.py",
            "zerver/tests/fixtures_helper_that_is_not_a_test.py",
            "zerver/lib/upload/__init__.py",
        ]
        self.assertEqual(
            self.modules.select_test_files(changed),
            [
                "zerver/tests/test_armada.py",
                "zerver/tests/test_message_topics.py",
                "zerver/tests/test_subs.py",
                "zerver/tests/test_upload.py",
                "zerver/transaction_tests/test_armada_worker.py",
            ],
        )

    def test_shards_cover_every_module_once(self) -> None:
        paths = self.modules.select_test_files(self.modules.fork_changed_python_files())
        shards = [self.modules.shard(paths, index, 4) for index in range(1, 5)]
        self.assertEqual(sorted(path for shard in shards for path in shard), paths)
        self.assertTrue(all(shards))

    def test_only_unknown_failures_fail_and_passing_known_ones_are_reported(self) -> None:
        modules = ["zerver.tests.test_subs", "zerver.tests.test_users"]
        known = {
            "zerver.tests.test_subs.SubscriptionAPITest.test_known_and_failing",
            "zerver.tests.test_subs.SubscriptionAPITest.test_known_and_fixed",
            "zerver.tests.test_events.NormalActionsTest.test_known_in_another_shard",
        }
        failed = {
            "zerver.tests.test_subs.SubscriptionAPITest.test_known_and_failing",
            "zerver.tests.test_users.UserTest.test_newly_broken",
        }
        self.assertEqual(
            self.modules.judge(modules, failed, known),
            (
                ["zerver.tests.test_users.UserTest.test_newly_broken"],
                ["zerver.tests.test_subs.SubscriptionAPITest.test_known_and_fixed"],
            ),
        )

    def test_every_known_failure_names_a_selected_module(self) -> None:
        paths = self.modules.select_test_files(self.modules.fork_changed_python_files())
        modules = [self.modules.module_name(path) for path in paths]
        for test in self.modules.read_known_failures():
            self.assertTrue(any(test.startswith(module + ".") for module in modules), test)

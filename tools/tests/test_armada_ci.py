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

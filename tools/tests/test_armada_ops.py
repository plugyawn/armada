import os
import sys
import tempfile
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from types import ModuleType
from unittest import TestCase, mock

ARMADA_FILES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "puppet/armada/files",
)


def load_script(name: str) -> ModuleType:
    """The scripts have no .py suffix, since puppet installs them as commands."""
    loader = SourceFileLoader(name.replace("-", "_"), os.path.join(ARMADA_FILES, name))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


settings_apply = load_script("armada-settings-apply")


class SettingsBlockTests(TestCase):
    def test_block_is_appended_once_and_replaced_in_place(self) -> None:
        operator = "EXTERNAL_HOST = 'chat.example.com'\n"
        first = settings_apply.with_block(operator, "ARMADA_WORKER_MODEL = 'a'\n")
        self.assertTrue(first.startswith(operator))
        self.assertIn(
            settings_apply.BEGIN + "ARMADA_WORKER_MODEL = 'a'\n" + settings_apply.END, first
        )
        self.assertEqual(settings_apply.with_block(first, "ARMADA_WORKER_MODEL = 'a'\n"), first)

        edited = first + "SOCIAL_AUTH_GITHUB_KEY = 'k'\n"
        second = settings_apply.with_block(edited, "ARMADA_WORKER_MODEL = 'b'\n")
        self.assertEqual(second.count(settings_apply.BEGIN), 1)
        self.assertIn("ARMADA_WORKER_MODEL = 'b'", second)
        self.assertNotIn("ARMADA_WORKER_MODEL = 'a'", second)
        self.assertTrue(second.startswith(operator))
        self.assertTrue(second.endswith("SOCIAL_AUTH_GITHUB_KEY = 'k'\n"))

    def test_check_mode_reports_a_missing_or_stale_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            block = os.path.join(directory, "block")
            settings = os.path.join(directory, "settings.py")
            with open(block, "w") as f:
                f.write("ARMADA_WORKER_MODEL = 'a'\n")
            with open(settings, "w") as f:
                f.write("EXTERNAL_HOST = 'chat.example.com'\n")

            def run(*args: str) -> int:
                with mock.patch.object(sys, "argv", ["armada-settings-apply", *args]):
                    status: int = settings_apply.main()
                    return status

            self.assertEqual(run("--check", block, settings), 1)
            self.assertEqual(run(block, settings), 0)
            self.assertEqual(run("--check", block, settings), 0)
            with open(block, "w") as f:
                f.write("ARMADA_WORKER_MODEL = 'b'\n")
            self.assertEqual(run("--check", block, settings), 1)

from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "commit"


def load_script():
    loader = SourceFileLoader("usl_commit", str(SCRIPT))
    spec = spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CommitTests(unittest.TestCase):
    def test_message_has_real_newlines_and_exact_attribution(self) -> None:
        module = load_script()
        arguments = type("Args", (), {
            "type": "fix",
            "scope": "migration",
            "summary": "preserve source identity",
            "body": ["Allocate native identifiers deterministically."],
            "validation": ["focused migration tests passed"],
        })()
        message = module.build_message(arguments)
        self.assertNotIn("\\n", message)
        self.assertEqual(1, message.count("AI-generated commit"))
        self.assertEqual(1, message.count("Co-authored-by:"))
        self.assertIn("\n\nValidation:\n- focused migration tests passed\n", message)

    def test_literal_newline_escape_is_rejected(self) -> None:
        module = load_script()
        arguments = type("Args", (), {
            "type": "fix",
            "scope": "migration",
            "summary": "repair message",
            "body": ["first\\nsecond"],
            "validation": [],
        })()
        with self.assertRaisesRegex(module.CommitError, "escaped or embedded"):
            module.build_message(arguments)

    def test_private_staged_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(("git", "init", "-q"), cwd=root, check=True)
            (root / "private").mkdir()
            (root / "private" / "secret.txt").write_text("secret\n", encoding="utf-8")
            subprocess.run(("git", "add", "-f", "private/secret.txt"), cwd=root, check=True)
            module = load_script()
            original = module.ROOT
            module.ROOT = root
            try:
                with self.assertRaisesRegex(module.CommitError, "private staged paths"):
                    module.staged_paths()
            finally:
                module.ROOT = original


if __name__ == "__main__":
    unittest.main()

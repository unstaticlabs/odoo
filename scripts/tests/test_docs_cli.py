from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from operations.docs import cli

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "0801ff5b7cf6a1b2c3d4e5f60718293a4b5c6d7e"


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


class DocsCliTests(unittest.TestCase):
    def test_help_lists_every_verb(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            cli.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        for verb in ("check", "index", "generate", "reference", "mcp", "evidence"):
            self.assertIn(verb, out.getvalue())
        for argv in (["check", "--help"], ["generate", "--help"], ["evidence", "create", "--help"], ["mcp", "--help"]):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
                cli.main(argv)
            self.assertEqual(raised.exception.code, 0, argv)

    def test_the_entry_point_runs_from_the_shell(self):
        process = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "docs"), "reference", "--help"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("render", process.stdout)

    def test_index_prints_the_llms_map_of_the_repository(self):
        code, text = run(["index"])
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("# USL Odoo Distribution\n"))
        self.assertIn("(docs/users/TUTORIAL.md)", text)
        self.assertIn("(docs/product/decisions/0001-weekly-delivery-protocol.md)", text)

    def test_generate_check_dispatches_with_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run(["generate", "check", "--records", tmp])
        self.assertEqual(code, 0)
        self.assertIn("Generated documentation check: PASS", text)

    def test_evidence_recovery_and_verify_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "docs-evidence.json"
            code, _text = run(["evidence", "recovery", "--repository", "unstaticlabs/odoo", "--commit", COMMIT, "--output", str(output)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["mode"], "recovery")
            code, text = run(["evidence", "verify", str(output), "--qualified-commit", COMMIT])
        self.assertEqual(code, 0)
        self.assertIn("Docs evidence: valid", text)


class DocsWiringTests(unittest.TestCase):
    """The verbs CI and the Makefile call are the ones the dispatcher has."""

    def test_the_pull_request_gate_runs_the_check(self):
        qualification = (ROOT / ".github/workflows/qualification.yml").read_text(encoding="utf-8")
        compatibility = qualification.split("  compatibility:\n", 1)[1].split("\n  database:\n", 1)[0]
        self.assertIn("scripts/docs check\n", compatibility)

    def test_the_database_gate_proves_pages_and_creates_evidence(self):
        gate = (ROOT / "scripts/ci-product-database").read_text(encoding="utf-8")
        self.assertIn("python3 scripts/docs generate check --records artifacts/usl-docs", gate)
        self.assertIn("python3 scripts/docs reference check --reference artifacts/usl-docs/reference.json", gate)
        self.assertIn("python3 scripts/docs evidence create \\", gate)
        self.assertIn("--output artifacts/usl-docs/docs-evidence.json", gate)

    def test_the_release_verifies_evidence_and_the_mcp_page(self):
        workflow = (ROOT / ".github/workflows/product-image.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/docs evidence verify .release/docs-evidence/docs-evidence.json", workflow)
        self.assertIn('scripts/docs evidence recovery --repository "$GITHUB_REPOSITORY"', workflow)
        external = workflow.split("  external:\n", 1)[1].split("\n  component:\n", 1)[0]
        self.assertLess(
            external.index('scripts/odoo-mcp verify --repository "$GITHUB_WORKSPACE/.release/odoo-mcp"'),
            external.index('scripts/docs mcp check --repository "$GITHUB_WORKSPACE/.release/odoo-mcp"'),
        )

    def test_the_makefile_targets_call_the_dispatcher(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY: docs "))
        for target in ("docs", "docs-check", "docs-journeys", "docs-render", "docs-reference", "docs-mcp"):
            self.assertIn(f" {target}", phony + " ")
            self.assertIn(f"\n{target}:", makefile)
            self.assertIn(f"make {target}", makefile, "listed in make help")
        self.assertIn("scripts/docs check", makefile)
        self.assertIn("scripts/docs generate render", makefile)
        self.assertIn("scripts/docs reference render", makefile)
        self.assertIn("scripts/docs mcp render --repository $(ODOO_MCP_REPOSITORY)", makefile)
        self.assertIn("ODOO_MCP_REPOSITORY ?= ../odoo-mcp", makefile)

    def test_the_superseded_entry_points_are_gone(self):
        for name in ("check-docs", "docs-index", "docs-generate", "docs-evidence", "docs-reference"):
            self.assertFalse((ROOT / "scripts" / name).exists(), name)
        for name in ("user_docs.py", "docs_generate.py", "docs_evidence.py", "docs_reference.py"):
            self.assertFalse((ROOT / "operations" / name).exists(), name)
        self.assertFalse((ROOT / "docs" / "llms.txt").exists(), "indexes are derived at request time, not committed")
        self.assertFalse((ROOT / "docs" / "product" / "decisions" / "README.md").exists())


if __name__ == "__main__":
    unittest.main()

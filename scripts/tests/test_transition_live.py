from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
HELPER = ROOT / "scripts/transition_live.py"
PROJECT = "usl-odoo-transition-20260828"


class TransitionLiveTest(unittest.TestCase):
    def run_helper(self, state_root: Path, *arguments: str) -> subprocess.CompletedProcess:
        environment = os.environ.copy()
        environment["USL_TRANSITION_STATE_ROOT"] = str(state_root)
        return subprocess.run(
            ["python3", str(HELPER), "--root", str(ROOT), *arguments],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )

    def test_mark_is_private_and_guard_refuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_root = Path(temporary) / "state"
            marked = self.run_helper(
                state_root,
                "mark",
                "--project",
                PROJECT,
                "--commit",
                "a" * 40,
                "--confirm",
                PROJECT,
            )
            self.assertEqual(marked.returncode, 0, marked.stderr)
            state_path = state_root / f"{PROJECT}.json"
            self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(state_root.stat().st_mode & 0o777, 0o700)
            guard = self.run_helper(
                state_root,
                "guard",
                "--project",
                PROJECT,
                "--operation",
                "target reset",
            )
            self.assertNotEqual(guard.returncode, 0)
            self.assertIn("target reset is forbidden", guard.stderr)

    def test_freeze_is_irreversible_through_interface(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_root = Path(temporary) / "state"
            self.run_helper(
                state_root,
                "mark",
                "--project",
                PROJECT,
                "--commit",
                "b" * 40,
                "--confirm",
                PROJECT,
            )
            frozen = self.run_helper(
                state_root,
                "freeze",
                "--project",
                PROJECT,
                "--confirm",
                PROJECT,
            )
            self.assertEqual(frozen.returncode, 0, frozen.stderr)
            self.assertEqual(json.loads(frozen.stdout)["status"], "frozen-read-only")
            self.assertNotIn("unmark", HELPER.read_text(encoding="utf-8"))

    def test_malformed_state_refuses_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_root = Path(temporary) / "state"
            state_root.mkdir()
            state_root.chmod(0o700)
            state_path = state_root / f"{PROJECT}.json"
            state_path.write_text("{}", encoding="utf-8")
            state_path.chmod(0o600)
            guard = self.run_helper(
                state_root,
                "guard",
                "--project",
                PROJECT,
                "--operation",
                "QA bootstrap",
            )
            self.assertNotEqual(guard.returncode, 0)
            self.assertIn("invalid transition state identity", guard.stderr)

    def test_non_transition_project_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_helper(
                Path(temporary),
                "status",
                "--project",
                "usl-odoo-migration-qa-20260828",
            )
            self.assertNotEqual(result.returncode, 0)

    def test_mutating_entrypoints_install_transition_guard(self):
        expected = {
            "scripts/target-reconstruct": "canonical reconstruction",
            "scripts/qa-environment": "QA provisioning or reset",
            "scripts/accounting-compat": "Accounting target reset",
            "scripts/qa-seed": "QA seed $command_name",
            "scripts/odoo-dev": "Odoo development helper",
            "scripts/documents-stack": "Documents QA helper",
            "scripts/sign-pocketid-stack": "Sign QA helper",
        }
        for relative, operation in expected.items():
            with self.subTest(relative=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("transition-live.sh", content)
                self.assertIn("usl_refuse_protected_transition", content)
                self.assertIn(operation, content)

    def test_mutating_entrypoints_refuse_protected_project_before_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            state_root = temporary_path / "state"
            marked = self.run_helper(
                state_root,
                "mark",
                "--project",
                PROJECT,
                "--commit",
                "c" * 40,
                "--confirm",
                PROJECT,
            )
            self.assertEqual(marked.returncode, 0, marked.stderr)
            sso_env = temporary_path / "documents-sso.env"
            sso_env.write_text("# intentionally empty guard fixture\n", encoding="utf-8")
            base_environment = os.environ.copy()
            base_environment.update(
                {
                    "COMPOSE_PROJECT_NAME": PROJECT,
                    "USL_TRANSITION_STATE_ROOT": str(state_root),
                },
            )
            invocations = (
                ([str(ROOT / "scripts/target-reconstruct")], {}),
                ([str(ROOT / "scripts/qa-environment"), "full"], {}),
                ([str(ROOT / "scripts/accounting-compat"), "dev-reset"], {}),
                ([str(ROOT / "scripts/qa-seed"), "publish"], {}),
                (
                    [str(ROOT / "scripts/odoo-dev"), "reset"],
                    {"ODOO_SAAS_COMPOSE_PROJECT": PROJECT},
                ),
                (
                    [str(ROOT / "scripts/documents-stack"), "qa", "bootstrap"],
                    {
                        "USL_DOCUMENTS_QA_ISOLATED_OVERRIDE": "1",
                        "USL_DOCUMENTS_QA_PROJECT": PROJECT,
                        "USL_DOCUMENTS_QA_SSO_ENV": str(sso_env),
                    },
                ),
                (
                    [str(ROOT / "scripts/sign-pocketid-stack"), "bootstrap"],
                    {"USL_SIGN_POCKETID_PROJECT": PROJECT},
                ),
            )
            for command, additions in invocations:
                with self.subTest(command=command):
                    environment = base_environment | additions
                    completed = subprocess.run(
                        command,
                        capture_output=True,
                        check=False,
                        env=environment,
                        text=True,
                    )
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("is forbidden", completed.stderr)


if __name__ == "__main__":
    unittest.main()

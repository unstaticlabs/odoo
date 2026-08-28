from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "scripts" / "agent"


def execute(*arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def load_script(name: str):
    path = AGENT / name
    loader = SourceFileLoader(f"usl_agent_interface_{name}", str(path))
    spec = spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {name}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.github = load_script("github")
        cls.verify = load_script("verify")

    def test_context_json_is_structured_and_secret_free(self) -> None:
        result = execute(str(AGENT / "context"), "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual("origin/19-usl", value["git"]["base"])
        self.assertEqual("migration-transition", value["policy"]["workflow_phase"])
        self.assertNotIn("password", result.stdout.casefold())
        self.assertNotIn("token", result.stdout.casefold())

    def test_qa_status_matches_contract(self) -> None:
        result = execute(str(AGENT / "qa-status"), "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual("usl-qa-environment/v1", value["schema"])
        self.assertTrue(value["compose_project"].startswith("usl-odoo-qa-"))
        self.assertIn(value["ownership"]["state"], {"unused", "owned", "foreign", "mixed", "unavailable"})
        self.assertIsNone(value["urls"]["remote_https"])
        self.assertFalse(value["authentication"]["secrets_in_output"])

    def test_qa_dry_run_and_confirmation_guard(self) -> None:
        up = execute(str(AGENT / "qa-up"), "--profile", "clean-install", "--dry-run")
        branch = execute("git", "branch", "--show-current").stdout.strip()
        if branch and branch != "19-usl":
            self.assertEqual(0, up.returncode, up.stderr)
            self.assertIn("clean-install", up.stdout)
        else:
            self.assertEqual(2, up.returncode)
            self.assertIn("dedicated feature branch", up.stderr)
        wrong = execute(str(AGENT / "qa-down"), "--dry-run", "--confirm", "foreign-project")
        self.assertEqual(2, wrong.returncode)
        project = execute(str(AGENT / "qa-status"), "--project")
        self.assertEqual(0, project.returncode, project.stderr)
        down = execute(str(AGENT / "qa-down"), "--dry-run", "--confirm", project.stdout.strip())
        self.assertEqual(0, down.returncode, down.stderr)
        self.assertIn("only this worktree", down.stdout)

    def test_qa_cleanup_dry_run_rejects_foreign_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            docker = Path(directory) / "docker"
            docker.write_text(
                "#!/bin/sh\nprintf '%s\\n' 'foreign-odoo|running|Up 1 minute|odoo|/foreign/worktree'\n",
                encoding="utf-8",
            )
            docker.chmod(0o700)
            environment = os.environ.copy()
            environment["PATH"] = f"{directory}:{environment['PATH']}"
            project = execute(str(AGENT / "qa-status"), "--project", env=environment)
            result = execute(
                str(AGENT / "qa-down"), "--dry-run", "--confirm", project.stdout.strip(), env=environment
            )
        self.assertEqual(2, result.returncode)
        self.assertIn("foreign Compose ownership", result.stderr)

    def test_skill_symlinks_share_one_canonical_source(self) -> None:
        result = execute(str(AGENT / "verify"), "skills")
        self.assertEqual(0, result.returncode, result.stderr)
        for provider in (".agents", ".claude"):
            for exposure in (ROOT / provider / "skills").iterdir():
                if exposure.name.startswith(("usl-", "odoo-")):
                    self.assertTrue(exposure.is_symlink(), exposure)
                    self.assertEqual((ROOT / "agent-skills" / exposure.name).resolve(), exposure.resolve())

    def test_github_status_fails_closed_without_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["USL_AGENT_STATE_DIR"] = directory
            result = execute(str(AGENT / "github"), "status", env=environment)
        self.assertEqual(2, result.returncode)
        self.assertIn("Missing required file", result.stderr)

    def test_github_status_rejects_wrong_authenticated_login(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            identity = {
                "schema": "usl-agent-github-identity/v1",
                "repository": "unstaticlabs/odoo",
                "agent": {"login": "usl-agent", "name": "USL Agent", "email": "agent@example.invalid"},
                "driving_human": {"name": "Driver", "email": "driver@example.invalid"},
            }
            identity_path = state / "identity.json"
            identity_path.write_text(json.dumps(identity), encoding="utf-8")
            identity_path.chmod(0o600)
            binary = Path(directory) / "gh"
            binary.write_text("#!/bin/sh\nprintf '%s\\n' rogerxaic\n", encoding="utf-8")
            binary.chmod(0o700)
            environment = os.environ.copy()
            environment["USL_AGENT_STATE_DIR"] = str(state)
            environment["PATH"] = f"{directory}:{environment['PATH']}"
            required_policy = json.loads((ROOT / "agent" / "policy.json").read_text(encoding="utf-8"))
            required_policy["github"]["agent_login"] = "usl-agent"
            policy_path = Path(directory) / "policy.json"
            policy_path.write_text(json.dumps(required_policy), encoding="utf-8")
            environment["USL_AGENT_POLICY_PATH"] = str(policy_path)
            result = execute(str(AGENT / "github"), "status", env=environment)
        self.assertEqual(2, result.returncode)
        self.assertIn("expected dedicated agent", result.stderr)

    def test_feature_branch_guard_rejects_protected_and_detached(self) -> None:
        for branch in ("19-usl", "<detached>"):
            with self.subTest(branch=branch), mock.patch.object(self.github, "branch_name", return_value=branch):
                with self.assertRaises(self.github.AgentError):
                    self.github.ensure_feature_branch()

    def test_feature_start_refuses_protected_and_detached_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts" / "agent").mkdir(parents=True)
            (root / "agent").mkdir()
            shutil.copy2(AGENT / "verify", root / "scripts" / "agent" / "verify")
            shutil.copy2(AGENT / "lib.py", root / "scripts" / "agent" / "lib.py")
            shutil.copy2(ROOT / "agent" / "policy.json", root / "agent" / "policy.json")
            (root / "README.md").write_text("test\n", encoding="utf-8")
            for command in (
                ("git", "init", "-q"),
                ("git", "add", "README.md"),
                ("git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "test"),
                ("git", "branch", "-M", "19-usl"),
            ):
                process = subprocess.run(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.assertEqual(0, process.returncode, process.stderr)
            protected = subprocess.run(
                [str(root / "scripts" / "agent" / "verify"), "feature-start"],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            subprocess.run(("git", "checkout", "--detach", "-q"), cwd=root, check=True)
            detached = subprocess.run(
                [str(root / "scripts" / "agent" / "verify"), "feature-start"],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        self.assertEqual(1, protected.returncode)
        self.assertIn("forbidden", protected.stderr)
        self.assertEqual(1, detached.returncode)
        self.assertIn("attached branch", detached.stderr)

    def test_commit_verifier_accepts_required_agent_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts" / "agent").mkdir(parents=True)
            (root / "agent").mkdir()
            shutil.copy2(AGENT / "verify", root / "scripts" / "agent" / "verify")
            shutil.copy2(AGENT / "lib.py", root / "scripts" / "agent" / "lib.py")
            shutil.copy2(ROOT / "agent" / "policy.json", root / "agent" / "policy.json")
            (root / "README.md").write_text("base\n", encoding="utf-8")
            setup = (
                ("git", "init", "-q"),
                ("git", "add", "README.md"),
                ("git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "chore: base"),
                ("git", "branch", "-M", "19-usl"),
                ("git", "switch", "-qc", "feat/test-attribution"),
            )
            for command in setup:
                process = subprocess.run(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.assertEqual(0, process.returncode, process.stderr)
            (root / "README.md").write_text("feature\n", encoding="utf-8")
            subprocess.run(("git", "add", "README.md"), cwd=root, check=True)
            message = "feat(test): exercise attribution\n\nAI-generated commit\n\nCo-authored-by: Driver <driver@example.invalid>"
            subprocess.run(
                ("git", "-c", "user.name=Agent", "-c", "user.email=agent@example.invalid", "commit", "-qm", message),
                cwd=root,
                check=True,
            )
            result = subprocess.run(
                [str(root / "scripts" / "agent" / "verify"), "commits", "--base", "19-usl", "--strict"],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Validated 1", result.stdout)

    def test_skill_verifier_detects_symlink_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in self.verify.SKILLS:
                skill_dir = root / "agent-skills" / name
                skill_dir.mkdir(parents=True)
                (skill_dir / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: Test skill.\n---\n\n# Test\n",
                    encoding="utf-8",
                )
                for provider in (".agents", ".claude"):
                    exposure_dir = root / provider / "skills"
                    exposure_dir.mkdir(parents=True, exist_ok=True)
                    (exposure_dir / name).symlink_to(Path("../../agent-skills") / name)
            drift = root / ".agents" / "skills" / "usl-feature-developer"
            drift.unlink()
            drift.symlink_to(Path("../../agent-skills") / "usl-lead-developer")
            errors = self.verify.check_skills(root)
        self.assertTrue(any("does not resolve" in error for error in errors))

    def test_repository_verifier(self) -> None:
        result = execute(str(AGENT / "verify"), "repository")
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()

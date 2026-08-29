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
        cls.lib = load_script("lib.py")
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
            self.assertIn("dedicated topic branch", up.stderr)
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

    def test_topic_branch_guard_rejects_protected_and_detached(self) -> None:
        for branch in ("19-usl", "<detached>"):
            with self.subTest(branch=branch), mock.patch.object(self.github, "branch_name", return_value=branch):
                with self.assertRaises(self.github.AgentError):
                    self.github.ensure_topic_branch()

    def test_pull_request_base_supports_validated_stacks(self) -> None:
        self.assertEqual(
            "codex/production-release-foundation",
            self.github.pull_request_base("origin/codex/production-release-foundation"),
        )

    def test_pull_request_base_rejects_unsafe_value(self) -> None:
        with self.assertRaises(self.github.AgentError):
            self.github.pull_request_base("origin/../unsafe")

    def test_merge_queue_configuration_removes_compute_checks_and_preserves_static_rules(self) -> None:
        configuration = json.loads((ROOT / "agent" / "policy.json").read_text(encoding="utf-8"))["github"][
            "merge_queue"
        ]
        current = {
            "name": "USL Distribution",
            "target": "branch",
            "enforcement": "active",
            "bypass_actors": [{"actor_id": 5, "actor_type": "Team", "bypass_mode": "always"}],
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {"type": "pull_request", "parameters": {"allowed_merge_methods": ["merge", "squash"]}},
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "required_status_checks": [{"context": "Existing protected check"}],
                        "strict_required_status_checks_policy": True,
                    },
                },
            ],
        }
        desired = self.github.configure_merge_queue_ruleset(current, configuration)
        by_type = {rule["type"]: rule for rule in desired["rules"]}
        self.assertEqual(["merge"], by_type["pull_request"]["parameters"]["allowed_merge_methods"])
        self.assertNotIn("required_status_checks", by_type)
        self.assertEqual("MERGE", by_type["merge_queue"]["parameters"]["merge_method"])
        self.assertEqual(1, by_type["merge_queue"]["parameters"]["max_entries_to_merge"])
        self.assertEqual(2, by_type["merge_queue"]["parameters"]["max_entries_to_build"])
        self.assertEqual(desired, self.github.configure_merge_queue_ruleset(desired, configuration))

    def test_merge_queue_activation_requires_canonical_branch(self) -> None:
        with (
            mock.patch.object(self.github, "branch_name", return_value="codex/topic"),
            mock.patch.object(self.github, "dirty_entries", return_value=[]),
        ):
            with self.assertRaisesRegex(self.github.AgentError, "clean authoritative"):
                self.github.verify_merge_queue_prerequisite("unstaticlabs/odoo", "a" * 40)

    def test_merge_candidate_check_detects_a_real_content_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(("git", "init", "-q"), cwd=root, check=True)
            subprocess.run(("git", "config", "user.name", "Test"), cwd=root, check=True)
            subprocess.run(("git", "config", "user.email", "test@example.invalid"), cwd=root, check=True)
            tracked = root / "tracked.txt"
            tracked.write_text("base\n", encoding="utf-8")
            subprocess.run(("git", "add", "tracked.txt"), cwd=root, check=True)
            subprocess.run(("git", "commit", "-qm", "base"), cwd=root, check=True)
            subprocess.run(("git", "branch", "-M", "main"), cwd=root, check=True)
            subprocess.run(("git", "branch", "feature"), cwd=root, check=True)
            tracked.write_text("main\n", encoding="utf-8")
            subprocess.run(("git", "commit", "-qam", "main"), cwd=root, check=True)
            subprocess.run(("git", "switch", "-q", "feature"), cwd=root, check=True)
            tracked.write_text("feature\n", encoding="utf-8")
            subprocess.run(("git", "commit", "-qam", "feature"), cwd=root, check=True)
            with mock.patch.object(self.lib, "ROOT", root):
                error = self.lib.merge_candidate_error("main", "feature")
        self.assertIn("conflict-free merge candidate", error or "")

    def test_only_post_merge_oci_workflow_remains(self) -> None:
        workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        self.assertEqual(["product-image.yml"], [path.name for path in workflows])
        text = workflows[0].read_text(encoding="utf-8")
        self.assertNotIn("pull_request:", text)
        self.assertNotIn("merge_group:", text)
        self.assertNotIn("workflow_dispatch:", text)
        self.assertIn("push:\n    branches:\n      - 19-usl", text)

    def test_branch_start_refuses_protected_and_detached_repository(self) -> None:
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
                [str(root / "scripts" / "agent" / "verify"), "branch-start"],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            subprocess.run(("git", "checkout", "--detach", "-q"), cwd=root, check=True)
            detached = subprocess.run(
                [str(root / "scripts" / "agent" / "verify"), "branch-start"],
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

    def test_commit_helper_builds_real_newlines_and_exact_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent_dir = root / "scripts" / "agent"
            state = root / ".agent"
            agent_dir.mkdir(parents=True)
            state.mkdir(mode=0o700)
            shutil.copy2(AGENT / "commit", agent_dir / "commit")
            identity = {
                "schema": "usl-agent-github-identity/v1",
                "repository": "unstaticlabs/odoo",
                "agent": {
                    "login": "elio-usl",
                    "name": "Coding Agent",
                    "email": "agent@example.invalid",
                },
                "driving_human": {
                    "name": "ValentinViennot",
                    "email": "driver@example.invalid",
                },
            }
            (state / "identity.json").write_text(
                json.dumps(identity),
                encoding="utf-8",
            )
            for command in (
                ("git", "init", "-q"),
                ("git", "config", "extensions.worktreeConfig", "true"),
                ("git", "config", "--worktree", "user.name", "Coding Agent"),
                ("git", "config", "--worktree", "user.email", "agent@example.invalid"),
            ):
                subprocess.run(command, cwd=root, check=True)
            (root / "change.txt").write_text("change\n", encoding="utf-8")
            subprocess.run(("git", "add", "change.txt"), cwd=root, check=True)
            result = subprocess.run(
                (
                    str(agent_dir / "commit"),
                    "--type",
                    "fix",
                    "--scope",
                    "migration",
                    "--summary",
                    "preserve source identity",
                    "--body",
                    "Allocate native identifiers deterministically.",
                    "--validation",
                    "focused migration tests passed",
                ),
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            message = subprocess.check_output(
                ("git", "show", "-s", "--format=%B", "HEAD"),
                cwd=root,
                text=True,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("\\n", message)
        self.assertEqual(1, message.count("AI-generated commit"))
        self.assertEqual(1, message.count("Co-authored-by:"))
        self.assertIn("\n\nValidation:\n- focused migration tests passed\n", message)

    def test_commit_helper_rejects_literal_newline_escape(self) -> None:
        configured = {
            "agent": {"name": "Coding Agent", "email": "agent@example.invalid"},
            "driving_human": {
                "name": "ValentinViennot",
                "email": "driver@example.invalid",
            },
        }
        commit = load_script("commit")
        arguments = type(
            "Arguments",
            (),
            {
                "type": "fix",
                "scope": "migration",
                "summary": "repair message",
                "body": ["first\\nsecond"],
                "validation": [],
            },
        )()
        with self.assertRaisesRegex(commit.CommitError, "escaped or embedded"):
            commit.build_message(arguments, configured)

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
            drift = root / ".agents" / "skills" / "odoo-accounting-integrity"
            drift.unlink()
            drift.symlink_to(Path("../../agent-skills") / "odoo-access-control-safety")
            errors = self.verify.check_skills(root)
        self.assertTrue(any("does not resolve" in error for error in errors))

    def test_repository_verifier(self) -> None:
        result = execute(str(AGENT / "verify"), "repository")
        self.assertEqual(0, result.returncode, result.stderr)

    def test_dependabot_covers_all_maintained_dependency_boundaries(self) -> None:
        configuration = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        for ecosystem, directory in (
            ("pip", "/"),
            ("pip", "/docker"),
            ("npm", "/"),
            ("docker", "/"),
            ("docker", "/docker"),
            ("docker", "/deploy/documents/paperless-ngx"),
            ("docker-compose", "/"),
            ("docker-compose", "/deploy/odoo-backup"),
            ("github-actions", "/"),
        ):
            with self.subTest(ecosystem=ecosystem, directory=directory):
                self.assertIn(
                    f'package-ecosystem: "{ecosystem}"\n    directory: "{directory}"',
                    configuration,
                )

    def test_dependabot_does_not_trigger_product_builds_on_pull_requests(self) -> None:
        product_workflow = (ROOT / ".github" / "workflows" / "product-image.yml").read_text(encoding="utf-8")
        self.assertNotIn("dependabot[bot]", product_workflow)
        self.assertNotIn("pull_request:", product_workflow)

    def test_dependabot_ignores_repository_built_compose_images(self) -> None:
        configuration = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertIn('dependency-name: "usl-paperless-ngx"', configuration)
        self.assertIn('dependency-name: "unstaticlabs/usl-odoo"', configuration)

    def test_dependabot_holds_only_the_incompatible_html_clean_release(self) -> None:
        configuration = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertIn('dependency-name: "lxml-html-clean"', configuration)
        self.assertIn('- "0.4.5"', configuration)


if __name__ == "__main__":
    unittest.main()

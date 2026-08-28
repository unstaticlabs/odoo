from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
AGENT_SCRIPTS = ROOT / "scripts" / "agent"


def load_script(name: str):
    path = AGENT_SCRIPTS / name
    loader = SourceFileLoader(f"usl_agent_{name}", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.handoff = load_script("handoff")
        cls.lib = load_script("lib.py")

    def test_initial_handoff_matches_schema(self) -> None:
        value = self.handoff.initial_payload(
            "agent-contract-test",
            "Exercise the versioned agent handoff.",
            ["A valid machine-readable contract is generated."],
            "origin/19-usl",
        )
        self.assertEqual([], self.lib.validate_schema(value, self.lib.HANDOFF_SCHEMA_PATH))

    def test_schema_rejects_unknown_and_invalid_sha(self) -> None:
        value = self.handoff.initial_payload(
            "agent-contract-test", "Test invalid data.", ["Invalid data is rejected."], "origin/19-usl"
        )
        value["unexpected"] = True
        value["feature"]["head_sha"] = "stale"
        errors = self.lib.validate_schema(value, self.lib.HANDOFF_SCHEMA_PATH)
        self.assertTrue(any("unexpected property" in error for error in errors))
        self.assertTrue(any("head_sha" in error for error in errors))

    def test_ready_contract_rejects_failed_evidence(self) -> None:
        value = self.handoff.initial_payload(
            "agent-contract-test", "Test semantics.", ["Failures block readiness."], "origin/19-usl"
        )
        value["verification"]["automated"].append(
            {"command": "false", "result": "failed", "notes": "Intentional test evidence."}
        )
        value["readiness"] = {
            "status": "READY TO MERGE",
            "rationale": "This claim is deliberately unsupported.",
            "blockers": [],
        }
        errors = self.handoff.semantic_errors(value, check_repository=False)
        self.assertTrue(any("ready handoffs" in error for error in errors))

    def test_documented_follow_up_allows_optional_blocked_evidence(self) -> None:
        value = self.handoff.initial_payload(
            "agent-contract-test", "Test follow-up.", ["Optional limitations are explicit."], "origin/19-usl"
        )
        value["feature"]["developer_github_login"] = "usl-agent"
        value["verification"]["automated"].append(
            {"command": "python3 -m unittest", "result": "passed", "notes": "Required checks passed."}
        )
        value["verification"]["manual"].append(
            {"journey": "Optional client probe", "result": "blocked", "evidence": [], "notes": "Client not logged in."}
        )
        value["release"]["rollback_notes"] = ["Revert the merge; no data changes are involved."]
        value["release"]["post_merge_checks"] = ["Confirm CI passes."]
        value["unverified_assumptions"] = ["Optional client runtime discovery remains blocked."]
        value["readiness"] = {
            "status": "READY TO MERGE WITH DOCUMENTED FOLLOW-UP",
            "rationale": "Mandatory structural qualification passed.",
            "blockers": [],
        }
        self.assertEqual([], self.handoff.semantic_errors(value, check_repository=False))

    def test_repository_validation_detects_stale_sha(self) -> None:
        value = self.handoff.initial_payload(
            "agent-contract-test", "Test freshness.", ["A stale SHA is rejected."], "origin/19-usl"
        )
        value["feature"]["head_sha"] = "0" * 40
        errors = self.handoff.semantic_errors(value, check_repository=True)
        self.assertTrue(any("repository is" in error for error in errors))

    def test_repository_validation_accepts_advanced_conflict_free_base(self) -> None:
        value = self.handoff.initial_payload(
            "agent-contract-test", "Test queue eligibility.", ["A clean candidate is accepted."], "origin/19-usl"
        )
        value["feature"].update({"branch": "feat/example", "head_sha": "1" * 40, "base_sha": "2" * 40})
        with (
            mock.patch.object(self.handoff, "branch_name", return_value="feat/example"),
            mock.patch.object(self.handoff, "head_sha", return_value="1" * 40),
            mock.patch.object(
                self.handoff,
                "resolve_ref",
                side_effect=lambda ref: "3" * 40 if ref == "origin/19-usl" else ref,
            ),
            mock.patch.object(self.handoff, "merge_base", return_value="4" * 40),
            mock.patch.object(self.handoff, "merge_candidate_error", return_value=None),
        ):
            errors = self.handoff.semantic_errors(value, check_repository=True)
        self.assertFalse(any("base_sha" in error or "catch up" in error for error in errors), errors)

    def test_repository_validation_rejects_conflicting_queue_candidate(self) -> None:
        value = self.handoff.initial_payload(
            "agent-contract-test", "Test queue conflict.", ["A conflict is rejected."], "origin/19-usl"
        )
        with mock.patch.object(self.handoff, "merge_candidate_error", return_value="Git cannot construct a candidate"):
            errors = self.handoff.semantic_errors(value, check_repository=True)
        self.assertTrue(any("catch up before handoff" in error for error in errors), errors)

    def test_rendered_contract_round_trips(self) -> None:
        value = self.handoff.initial_payload(
            "agent-contract-test", "Test PR rendering.", ["JSON survives rendering."], "origin/19-usl"
        )
        rendered = self.handoff.render(value)
        self.assertEqual(value, self.handoff.extract_body(rendered))

    def test_rendered_contract_is_human_first_github_markdown(self) -> None:
        value = self.handoff.initial_payload(
            "agent-contract-test",
            "People can understand the delivered workflow before reading implementation details.",
            [
                "Users see the complete outcome in plain language.",
                "Reviewers still receive the technical evidence needed for integration.",
            ],
            "origin/19-usl",
        )
        value["changes"]["user_facing"] = True
        value["verification"]["automated"] = [
            {"command": "python3 -m unittest", "result": "passed", "notes": "Focused tests passed."}
        ]
        rendered = self.handoff.render(value)
        self.assertTrue(rendered.startswith("## What this delivers\n"))
        self.assertIn("### What users can expect", rendered)
        self.assertIn("## Review snapshot", rendered)
        self.assertIn("## Implementation scope", rendered)
        self.assertIn("| ✅ Passed | `python3 -m unittest` |", rendered)
        self.assertIn("<summary>Machine-readable handoff contract (v1)</summary>", rendered)
        self.assertIn("No database migration or feature QA environment is required", rendered)
        self.assertNotIn("### Known issues", rendered)
        self.assertLess(rendered.index("Users see the complete outcome"), rendered.index("**Source:**"))
        self.assertLess(rendered.index("Reviewers still receive"), rendered.index("**Source:**"))
        self.assertLess(rendered.index("## Validation"), rendered.index("```json"))

    def test_non_user_facing_pr_starts_with_operator_outcomes(self) -> None:
        value = self.handoff.initial_payload(
            "agent-contract-test",
            "Maintainers get a safer release workflow without changing the product interface.",
            ["Operators can identify and recover a failed release."],
            "origin/19-usl",
        )
        rendered = self.handoff.render(value)
        self.assertTrue(rendered.startswith("## What this delivers\n"))
        self.assertIn("### What operators and maintainers can expect", rendered)
        self.assertLess(rendered.index("Operators can identify"), rendered.index("**Source:**"))

    def test_rendered_tables_escape_github_markdown_cells(self) -> None:
        value = self.handoff.initial_payload(
            "agent-contract-test", "Preserve a | table cell.", ["Line one\nline two"], "origin/19-usl"
        )
        value["verification"]["automated"] = [
            {"command": "one | two", "result": "passed", "notes": "First\nsecond"}
        ]
        rendered = self.handoff.render(value)
        self.assertIn("Preserve a | table cell.", rendered)
        self.assertIn("`one \\| two`", rendered)
        self.assertIn("First<br>second", rendered)

    def test_pr_validation_is_advisory_but_strict_mode_blocks(self) -> None:
        value = self.handoff.initial_payload(
            "agent-contract-test", "Test PR enforcement.", ["Stale PR evidence is reported."], "origin/19-usl"
        )
        body = self.handoff.render(value)
        event = {
            "pull_request": {
                "body": body,
                "base": {"ref": "19-usl"},
                "head": {"sha": "0" * 40, "ref": "feat/example"},
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            event_path.write_text(json.dumps(event), encoding="utf-8")
            advisory = subprocess.run(
                [str(AGENT_SCRIPTS / "handoff"), "validate-pr-event", str(event_path)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            strict = subprocess.run(
                [str(AGENT_SCRIPTS / "handoff"), "validate-pr-event", str(event_path), "--strict"],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            required_policy = json.loads((ROOT / "agent" / "policy.json").read_text(encoding="utf-8"))
            required_policy["enforcement"]["feature_handoff"] = "required"
            required_path = Path(directory) / "required-policy.json"
            required_path.write_text(json.dumps(required_policy), encoding="utf-8")
            environment = os.environ.copy()
            environment["USL_AGENT_POLICY_PATH"] = str(required_path)
            required = subprocess.run(
                [str(AGENT_SCRIPTS / "handoff"), "validate-pr-event", str(event_path)],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        self.assertEqual(0, advisory.returncode, advisory.stderr)
        self.assertNotEqual(0, strict.returncode)
        self.assertNotEqual(0, required.returncode)
        self.assertIn("Advisory only", advisory.stdout)

    def test_missing_pr_handoff_is_advisory_during_transition(self) -> None:
        event = {
            "pull_request": {
                "body": "No contract yet.",
                "base": {"ref": "19-usl"},
                "head": {"sha": "0" * 40, "ref": "feat/example"},
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            event_path.write_text(json.dumps(event), encoding="utf-8")
            result = subprocess.run(
                [str(AGENT_SCRIPTS / "handoff"), "validate-pr-event", str(event_path)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Advisory only", result.stdout)


if __name__ == "__main__":
    unittest.main()

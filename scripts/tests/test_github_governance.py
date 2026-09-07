from __future__ import annotations

import copy
import unittest
from pathlib import Path

from operations.github_governance import GovernanceError, load, validate, validate_production


ROOT = Path(__file__).resolve().parents[2]
RULESET = ROOT / "operations/contracts/github-usl-distribution-ruleset.json"
PRODUCTION_RULESET = ROOT / "operations/contracts/github-usl-production-ruleset.json"
BACK_MERGE_WORKFLOW = ROOT / ".github/workflows/staging-back-merge.yml"
BACK_MERGE_SCRIPT = ROOT / "scripts/back-merge-production"


class GithubGovernanceTests(unittest.TestCase):
    def test_versioned_ruleset_is_valid(self):
        validate(load(RULESET))

    def test_staging_ruleset_may_not_target_production(self):
        value = copy.deepcopy(load(RULESET))
        value["conditions"]["ref_name"]["include"] = ["refs/heads/19-usl"]
        with self.assertRaisesRegex(GovernanceError, "only 19-usl-staging"):
            validate(value)

    def test_staging_intentionally_requires_zero_approving_reviews(self):
        value = copy.deepcopy(load(RULESET))
        for rule in value["rules"]:
            if rule["type"] == "pull_request":
                rule["parameters"]["required_approving_review_count"] = 1
        with self.assertRaisesRegex(GovernanceError, "zero approving reviews"):
            validate(value)

    def test_missing_stable_check_is_rejected(self):
        value = copy.deepcopy(load(RULESET))
        for rule in value["rules"]:
            if rule["type"] == "required_status_checks":
                rule["parameters"]["required_status_checks"] = []
        with self.assertRaisesRegex(GovernanceError, "qualification"):
            validate(value)

    def test_production_admission_ruleset_is_valid(self):
        validate_production(load(PRODUCTION_RULESET))

    def test_production_rejects_obsolete_required_deployment_gate(self):
        value = copy.deepcopy(load(PRODUCTION_RULESET))
        value["rules"].append(
            {
                "type": "required_deployments",
                "parameters": {"required_deployment_environments": ["staging-release"]},
            }
        )
        with self.assertRaisesRegex(GovernanceError, "production protection inventory"):
            validate_production(value)

    def test_production_intentionally_requires_zero_approving_reviews(self):
        value = copy.deepcopy(load(PRODUCTION_RULESET))
        for rule in value["rules"]:
            if rule["type"] == "pull_request":
                rule["parameters"]["required_approving_review_count"] = 1
        with self.assertRaisesRegex(GovernanceError, "zero approving reviews"):
            validate_production(value)

    def test_production_requires_signed_promotion_check(self):
        value = copy.deepcopy(load(PRODUCTION_RULESET))
        for rule in value["rules"]:
            if rule["type"] == "required_status_checks":
                rule["parameters"]["required_status_checks"] = [
                    item
                    for item in rule["parameters"]["required_status_checks"]
                    if item["context"] != "USL production promotion"
                ]
        with self.assertRaisesRegex(GovernanceError, "promotion"):
            validate_production(value)

    def test_production_advance_triggers_the_staging_back_merge(self):
        workflow = BACK_MERGE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("branches: [19-usl]", workflow)
        # The schedule is the safety net for a push-triggered run that failed.
        self.assertIn("schedule:", workflow)
        self.assertIn("scripts/back-merge-production", workflow)

    def test_back_merge_acts_only_while_staging_is_behind_production(self):
        script = BACK_MERGE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'git merge-base --is-ancestor "origin/$production" "origin/$staging"',
            script,
        )
        self.assertIn('--base "$staging" --head "$production"', script)
        # A conflicting back-merge must stop rather than leave staging behind.
        self.assertIn("::error::", script)
        # Merge commits are what restore the ancestry; never squash or rebase.
        self.assertIn("--auto --merge", script)

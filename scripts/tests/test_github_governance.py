from __future__ import annotations

import copy
import unittest
from pathlib import Path

from operations.github_governance import GovernanceError, load, validate, validate_production


ROOT = Path(__file__).resolve().parents[2]
RULESET = ROOT / "operations/contracts/github-usl-distribution-ruleset.json"
PRODUCTION_RULESET = ROOT / "operations/contracts/github-usl-production-ruleset.json"
URGENT_MIRROR = ROOT / ".github/workflows/urgent-staging-mirror.yml"


class GithubGovernanceTests(unittest.TestCase):
    def test_versioned_ruleset_is_valid(self):
        validate(load(RULESET))

    def test_missing_staging_branch_is_rejected(self):
        value = copy.deepcopy(load(RULESET))
        value["conditions"]["ref_name"]["include"] = ["refs/heads/19-usl"]
        with self.assertRaisesRegex(GovernanceError, "both permanent"):
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

    def test_production_requires_staging_deployment(self):
        value = copy.deepcopy(load(PRODUCTION_RULESET))
        for rule in value["rules"]:
            if rule["type"] == "required_deployments":
                rule["parameters"]["required_deployment_environments"] = []
        with self.assertRaisesRegex(GovernanceError, "staging-release"):
            validate_production(value)

    def test_rejected_urgent_fix_closes_its_staging_mirror(self):
        workflow = URGENT_MIRROR.read_text(encoding="utf-8")
        self.assertIn("SOURCE_MERGED: ${{ github.event.pull_request.merged }}", workflow)
        self.assertIn('if [ "$EVENT_ACTION" = closed ] && [ "$SOURCE_MERGED" != true ]', workflow)
        self.assertIn('gh pr close "$number"', workflow)
        self.assertIn('gh pr reopen "$number"', workflow)

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from operations.github_governance import GovernanceError, load, validate


ROOT = Path(__file__).resolve().parents[2]
RULESET = ROOT / "operations/contracts/github-usl-distribution-ruleset.json"


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

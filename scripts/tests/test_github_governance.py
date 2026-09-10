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
        self.assertIn('--base "$staging" --head "$branch"', script)
        # A conflicting back-merge must stop rather than leave staging behind.
        self.assertIn("::error::", script)
        # Merge commits are what restore the ancestry; never squash or rebase.
        self.assertIn("--auto --merge", script)

    def test_back_merge_never_opens_a_pull_request_headed_by_a_release_branch(self):
        # A pull request whose head is 19-usl inherits every push-event run on
        # that commit, and the check rollup takes the worst of them. One red
        # push run then blocks the back-merge for ever -- #227, 2026-09-10 --
        # because nothing the pull request owns can rerun the offending run.
        script = BACK_MERGE_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn('--head "$production"', script)
        self.assertIn('branch="chore/back-merge-production-', script)
        # The head must be built off staging and carry production as a parent,
        # or the merge would not restore the ancestry it exists to restore.
        self.assertIn('git checkout --quiet -B "$branch" "origin/$staging"', script)
        self.assertIn('merge --no-ff', script)
        self.assertIn('"origin/$production"', script)

    def test_back_merge_head_branch_is_never_force_updated(self):
        # The branch name pins both tips, so a rerun rebuilds an identical
        # branch. Force-updating a branch a pull request is open on would
        # rewrite history the queue may already be building.
        script = BACK_MERGE_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("push --force", script)
        self.assertNotIn("--force-with-lease", script)
        self.assertIn('git rev-parse --short=12 "origin/$production"', script)
        self.assertIn('git rev-parse --short=12 "origin/$staging"', script)

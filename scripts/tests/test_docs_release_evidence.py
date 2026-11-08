"""Which docs evidence a release adopts, with GitHub replaced by a script."""

import json
import unittest
from pathlib import Path

from operations.docs import evidence, release_evidence

COMMIT = "0801ff5b7cf6a1b2c3d4e5f60718293a4b5c6d7e"
MERGE_REF = "1111111111111111111111111111111111111111"
TREE = "2222222222222222222222222222222222222222"
PR_HEAD = "3333333333333333333333333333333333333333"


def _evidence(commit, run_id, tree=TREE):
    body = {
        "schema": evidence.SCHEMA, "repository": "unstaticlabs/odoo", "qualified_commit": commit,
        "workflow_run_id": run_id, "run_url": f"https://github.com/unstaticlabs/odoo/actions/runs/{run_id}",
        "tested_at": "2026-11-08T10:00:00Z", "chromium": "Chrome/156", "mode": "qualification",
        "journeys": {}, "qualified_tree": tree,
    }
    return {**body, "sha256": evidence._digest(body)}


class FakeGitHub:
    """Answers ``gh`` calls from a dict of run id -> evidence payload."""

    def __init__(self, artifacts, pulls=(), runs=()):
        self.artifacts = artifacts
        self.pulls = list(pulls)
        self.runs = list(runs)
        self.calls = []

    def __call__(self, arguments, *, cwd=None):
        self.calls.append(arguments)
        if arguments[:2] == ["run", "download"]:
            run_id = int(arguments[2])
            target = Path(arguments[arguments.index("--dir") + 1])
            if run_id not in self.artifacts:
                raise release_evidence.ReleaseEvidenceError("no artifact")
            (target / "docs-evidence.json").write_text(json.dumps(self.artifacts[run_id]), encoding="utf-8")
            return ""
        if arguments[0] == "api" and arguments[1].endswith("/pulls"):
            return json.dumps(self.pulls)
        if arguments[0] == "api" and "/actions/runs?" in arguments[1]:
            return json.dumps({"workflow_runs": self.runs})
        raise AssertionError(f"unexpected gh call {arguments}")


class ReleaseEvidenceTests(unittest.TestCase):
    def test_a_staging_release_adopts_its_own_run(self):
        gh = FakeGitHub({7: _evidence(COMMIT, 7)})
        payload = release_evidence.resolve(repository="unstaticlabs/odoo", commit=COMMIT, tree=TREE, workflow_run_id=7, gh=gh)
        self.assertEqual(payload["release_commit"], COMMIT)
        self.assertEqual(payload["workflow_run_id"], 7)
        evidence.validate(payload)

    def test_a_production_release_adopts_the_pull_request_run_by_tree(self):
        gh = FakeGitHub(
            {9: _evidence(MERGE_REF, 9)},
            pulls=[{"number": 390, "head": {"sha": PR_HEAD}}],
            runs=[{"id": 9, "name": "USL qualification"}, {"id": 8, "name": "Something else"}],
        )
        payload = release_evidence.resolve(repository="unstaticlabs/odoo", commit=COMMIT, tree=TREE, workflow_run_id=12, gh=gh)
        self.assertEqual(payload["qualified_commit"], MERGE_REF, "the run tested the merge ref")
        self.assertEqual(payload["release_commit"], COMMIT)
        self.assertIn(["run", "download", "12", "--repo", "unstaticlabs/odoo", "--name", "docs-evidence-12", "--dir", gh.calls[0][-1]], gh.calls)

    def test_unparseable_github_output_means_no_evidence_not_a_failed_release(self):
        def gh(arguments, *, cwd=None):
            if arguments[:2] == ["run", "download"]:
                Path(arguments[arguments.index("--dir") + 1], "docs-evidence.json").write_text("<html>502</html>", encoding="utf-8")
                return ""
            return "<html>502 Bad Gateway</html>"

        self.assertIsNone(release_evidence.resolve(repository="unstaticlabs/odoo", commit=COMMIT, tree=TREE, workflow_run_id=12, gh=gh))

    def test_a_different_tree_or_no_run_means_no_evidence(self):
        other_tree = _evidence(MERGE_REF, 9, tree="9" * 40)
        gh = FakeGitHub({9: other_tree}, pulls=[{"number": 1, "head": {"sha": PR_HEAD}}], runs=[{"id": 9, "name": "USL qualification"}])
        self.assertIsNone(release_evidence.resolve(repository="unstaticlabs/odoo", commit=COMMIT, tree=TREE, workflow_run_id=12, gh=gh))
        self.assertIsNone(release_evidence.resolve(repository="unstaticlabs/odoo", commit=COMMIT, tree=TREE, workflow_run_id=12, gh=FakeGitHub({})))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import artifact_build_plan  # noqa: E402
from continuous_operations_contracts import ARTIFACT_ROLES  # noqa: E402

FROM = "a" * 40
TO = "b" * 40


class ArtifactBuildPlanTest(unittest.TestCase):
    def test_missing_prior_builds_every_runtime(self) -> None:
        plan = artifact_build_plan.classify([], from_commit=None, to_commit=TO)
        self.assertEqual(plan["mode"], "build_all")
        self.assertEqual(plan["reason"], "prior_release_unavailable")
        self.assertEqual(plan["build_roles"], sorted(ARTIFACT_ROLES))
        self.assertEqual(plan["reuse_roles"], [])

    def test_owned_change_builds_only_affected_runtime(self) -> None:
        plan = artifact_build_plan.classify(
            ["deploy/documents/paperless-ngx/Dockerfile"],
            from_commit=FROM,
            to_commit=TO,
        )
        self.assertEqual(plan["build_roles"], ["paperless_overlay"])
        self.assertEqual(
            plan["reuse_roles"],
            sorted(set(ARTIFACT_ROLES) - {"paperless_overlay"}),
        )

    def test_foundation_or_ownership_change_builds_every_runtime(self) -> None:
        for path in (
            "scripts/artifact_build_plan.py",
            "operations/contracts/distribution-release-v3.schema.json",
        ):
            with self.subTest(path=path):
                plan = artifact_build_plan.classify(
                    [path], from_commit=FROM, to_commit=TO,
                )
                self.assertEqual(plan["reason"], "foundation_or_ownership_changed")
                self.assertEqual(plan["build_roles"], sorted(ARTIFACT_ROLES))

    def test_ambiguous_change_builds_every_runtime(self) -> None:
        plan = artifact_build_plan.classify(
            ["new-production-service/Dockerfile"],
            from_commit=FROM,
            to_commit=TO,
        )
        self.assertEqual(plan["reason"], "ambiguous_paths")
        self.assertEqual(plan["build_roles"], sorted(ARTIFACT_ROLES))

    def test_non_runtime_change_reuses_every_runtime(self) -> None:
        plan = artifact_build_plan.classify(
            ["docs/operations/continuous-releases.md"],
            from_commit=FROM,
            to_commit=TO,
        )
        self.assertEqual(plan["build_roles"], [])
        self.assertEqual(plan["reuse_roles"], sorted(ARTIFACT_ROLES))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import deployment_run  # noqa: E402
import distribution_release  # noqa: E402
import production_cohort  # noqa: E402
import retention_policy  # noqa: E402
import upgrade_plan  # noqa: E402
from continuous_operations_contracts import (  # noqa: E402
    ARTIFACT_ROLES,
    COHORT_UNITS,
    STAGES,
    canonical_sha256,
    with_checksum,
)
from release_identity import (  # noqa: E402
    PRODUCT_MODULES,
    expected_oca_pins,
    product_module_versions,
)

COMMIT = "a" * 40
PREVIOUS = "9" * 40
NOW = datetime(2026, 8, 29, 2, 0, tzinfo=UTC)  # 04:00 Europe/Paris


def release(commit: str = COMMIT) -> dict:
    versions = product_module_versions()
    artifacts = {}
    for index, role in enumerate(ARTIFACT_ROLES, 1):
        digest = f"sha256:{index:064x}"
        name = distribution_release.ARTIFACT_NAMES[role]
        artifacts[role] = {
            "name": name,
            "tag": f"sha-{commit}",
            "digest": digest,
            "digest_reference": f"{name}@{digest}",
            "source_commit_sha": commit,
            "origin": {"kind": "built_for_release", "release_commit_sha": commit},
            "attestations": {
                "oci_sbom": "generated",
                "buildkit_provenance": "generated",
                "github_provenance": "generated",
            },
        }
    return {
        "schema": "usl-distribution-release/v3",
        "source": {"repository": "unstaticlabs/odoo", "commit_sha": commit},
        "artifacts": artifacts,
        "product": {
            "modules": [
                {"name": name, "version": versions[name]} for name in sorted(versions)
            ],
            "oca": {"bundle_sha256": "b" * 64, "repositories": expected_oca_pins()},
            "action_risk": {"policy_sha256": "c" * 64},
        },
        "component_sources": {
            "document_renderer": {
                "repository": "unstaticlabs/usl-document-renderer",
                "commit_sha": "d" * 40,
            },
        },
        "build": {
            "workflow_run_id": 1,
            "workflow_run_attempt": 1,
            "workflow_url": "https://github.com/unstaticlabs/odoo/actions/runs/1",
        },
        "upgrade_plan": {
            "schema": "usl-upgrade-plan/v1",
            "from_commit_sha": None,
            "to_commit_sha": commit,
            "mode": "full_fallback",
            "reason": "prior_release_unavailable",
            "changed_modules": [],
            "upgrade_modules": sorted(PRODUCT_MODULES),
            "foundation_paths": [],
        },
    }


def cohort(deployed: dict | None = None, *, cohort_id: str = "cohort-20260829") -> dict:
    deployed = deployed or release()
    created = "2026-08-29T01:45:00Z"
    value = {
        "schema": "usl-production-cohort/v1",
        "cohort_id": cohort_id,
        "created_at": created,
        "release": {
            "source_commit_sha": deployed["source"]["commit_sha"],
            "release_contract_sha256": canonical_sha256(deployed),
            "artifacts": {
                role: deployed["artifacts"][role]["digest_reference"]
                for role in ARTIFACT_ROLES
            },
        },
        "storage": {
            name: {
                "snapshot_id": f"snap-{index:02d}",
                "sha256": f"{index:064x}",
                "size_bytes": index,
            }
            for index, name in enumerate(COHORT_UNITS, 1)
        },
        "models": [
            {
                "name": "usl-bge-m3",
                "digest": "sha256:" + "e" * 64,
                "archive_sha256": "f" * 64,
            },
        ],
        "queues": {
            name: {"state": "drained", "pending": 0, "authoritative": False}
            for name in ("odoo_jobs", "paperless_broker")
        },
        "restore_evidence": [
            {
                "component": name,
                "environment": "fresh_isolated_volumes",
                "restored_at": created,
                "verification_sha256": f"{index + 20:064x}",
                "status": "verified",
            }
            for index, name in enumerate(COHORT_UNITS)
        ],
        "secrets": {"provider": "infisical", "copied": False},
        "contract_sha256": "0" * 64,
    }
    return with_checksum(value)


class ProductionCohortTest(unittest.TestCase):
    def test_accepts_complete_coordinated_restore(self) -> None:
        self.assertEqual(
            production_cohort.validate(cohort())["schema"], "usl-production-cohort/v1",
        )

    def test_rejects_contract_tampering(self) -> None:
        value = cohort()
        value["storage"]["odoo_filestore"]["size_bytes"] += 1
        with self.assertRaisesRegex(production_cohort.CohortError, "contract_sha256"):
            production_cohort.validate(value)

    def test_rejects_missing_restore_evidence_and_authoritative_broker(self) -> None:
        value = cohort()
        value["restore_evidence"].pop()
        value = with_checksum(value)
        with self.assertRaisesRegex(production_cohort.CohortError, "incomplete"):
            production_cohort.validate(value)
        value = cohort()
        value["queues"]["paperless_broker"]["authoritative"] = True
        value = with_checksum(value)
        with self.assertRaisesRegex(production_cohort.CohortError, "non-authoritative"):
            production_cohort.validate(value)

    def test_rejects_secret_copy(self) -> None:
        value = cohort()
        value["secrets"]["copied"] = True
        value = with_checksum(value)
        with self.assertRaisesRegex(production_cohort.CohortError, "never copied"):
            production_cohort.validate(value)


class UpgradePlannerTest(unittest.TestCase):
    def _git(self, changed: str):
        def fake(*args: str) -> str:
            if args[0] == "cat-file":
                return ""
            if args[0] == "diff":
                return changed
            raise AssertionError(args)

        return fake

    def test_dependency_closure_includes_dependents(self) -> None:
        def manifest(_commit: str, module: str) -> dict:
            depends = ["usl_accounting"] if module == "usl_expense_batch" else []
            return {"depends": depends}

        with (
            mock.patch.object(
                upgrade_plan,
                "run_git",
                side_effect=self._git("custom-addons/usl_accounting/models/move.py\n"),
            ),
            mock.patch.object(upgrade_plan, "manifest_at", side_effect=manifest),
        ):
            value = upgrade_plan.plan(PREVIOUS, COMMIT)
        self.assertEqual(value["mode"], "dependency_closure")
        self.assertEqual(value["changed_modules"], ["usl_accounting"])
        self.assertIn("usl_expense_batch", value["upgrade_modules"])

    def test_foundation_and_ambiguous_changes_fall_back_full(self) -> None:
        for changed in ("requirements.txt\n", "custom-addons/unknown/models/x.py\n"):
            with (
                self.subTest(changed=changed),
                mock.patch.object(
                    upgrade_plan, "run_git", side_effect=self._git(changed),
                ),
            ):
                value = upgrade_plan.plan(PREVIOUS, COMMIT)
            self.assertEqual(value["mode"], "full_fallback")
            self.assertEqual(set(value["upgrade_modules"]), PRODUCT_MODULES)

    def test_missing_prior_release_falls_back_full(self) -> None:
        value = upgrade_plan.plan(None, COMMIT)
        self.assertEqual(value["reason"], "prior_release_unavailable")
        self.assertEqual(set(value["upgrade_modules"]), PRODUCT_MODULES)


class DeploymentControllerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.deployed = release(PREVIOUS)
        self.candidate = release(COMMIT)
        self._write("deployed.json", self.deployed)
        self._write("candidate.json", self.candidate)
        self._write("cohort.json", cohort(self.deployed))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, name: str, value: object) -> Path:
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def initial(self, mode: str = "release") -> dict:
        return deployment_run.initialize(
            run_id="run-20260829",
            mode=mode,
            deployed_release_path=self.root / "deployed.json",
            candidate_release_path=self.root / "candidate.json",
            cohort_path=self.root / "cohort.json",
            now=NOW - timedelta(minutes=15),
        )

    def advance(self, value: dict, stage: str, *, fail: str | None = None) -> dict:
        gitops = None
        if stage in {"upgrade_production", "admit"}:
            gitops = self._write(
                f"{stage}.json",
                {"expected_commit": "e" * 40, "observed_commit": "e" * 40},
            )
        return deployment_run.advance(
            value,
            stage,
            now=NOW,
            hook_dir=None,
            dry_run=True,
            fail_stage=fail,
            pins_output=self.root / "gitops-pins.json"
            if stage == "prepare_pins"
            else None,
            expected_gitops_commit=None,
            observed_gitops_commit=None,
            gitops_result=gitops,
            snapshot_result=None,
            upgrade_result=None,
        )

    def through(self, stop_before: str) -> dict:
        value = self.initial()
        for stage in STAGES:
            if stage == stop_before:
                return value
            value = self.advance(value, stage)
        raise AssertionError(stop_before)

    def test_full_repeated_dry_run_is_idempotent(self) -> None:
        value = self.initial()
        for stage in STAGES:
            value = self.advance(value, stage)
            repeated = self.advance(copy.deepcopy(value), stage)
            self.assertEqual(repeated, value)
        self.assertEqual(value["state"], "recorded")
        self.assertTrue(value["production_reopened"])
        self.assertEqual(
            value["pins"]["gitops"]["candidate"]["expected_commit"], "e" * 40,
        )

    def test_schedule_safe_run_id_and_existing_state_are_idempotent(self) -> None:
        value = deployment_run.initialize(
            run_id=None,
            mode="release",
            deployed_release_path=self.root / "deployed.json",
            candidate_release_path=self.root / "candidate.json",
            cohort_path=self.root / "cohort.json",
            now=NOW - timedelta(minutes=15),
        )
        self.assertEqual(value["run_id"], "usl-continuous-20260829")
        state = self.root / "deployment-run.json"
        self.assertTrue(deployment_run.initialize_state(state, value))
        self.assertFalse(deployment_run.initialize_state(state, copy.deepcopy(value)))
        incompatible = copy.deepcopy(value)
        incompatible["mode"] = "backup_only"
        incompatible = with_checksum(incompatible)
        with self.assertRaisesRegex(
            deployment_run.DeploymentRunError, "refusing to overwrite",
        ):
            deployment_run.initialize_state(state, incompatible)

    def test_no_release_path_skips_mutation(self) -> None:
        value = self.initial("backup_only")
        for stage in STAGES:
            value = self.advance(value, stage)
        self.assertFalse(value["mutation_started"])
        self.assertFalse(value["production_reopened"])
        self.assertEqual(value["state"], "recorded")

    def test_failure_injection_at_every_stage(self) -> None:
        for stage in STAGES:
            with self.subTest(stage=stage):
                value = self.through(stage)
                failed = self.advance(value, stage, fail=stage)
                deployment_run.validate(failed)
                if stage == "record":
                    self.assertEqual(failed["state"], "incident_required")
                    self.assertTrue(failed["incident"]["decision_required"])
                elif stage in STAGES[STAGES.index("upgrade_production") :]:
                    self.assertEqual(failed["state"], "rolled_back")
                    self.assertEqual(failed["rollback"]["status"], "completed")
                    self.assertEqual(failed["writers"], "open")
                else:
                    self.assertEqual(failed["state"], "failed_pre_mutation")
                    self.assertFalse(failed["mutation_started"])

    def test_mismatched_candidate_sync_is_recovered_inside_mutation_boundary(
        self,
    ) -> None:
        value = self.through("upgrade_production")
        result = self._write(
            "candidate-mismatch.json",
            {"expected_commit": "e" * 40, "observed_commit": "f" * 40},
        )
        value = deployment_run.advance(
            value,
            "upgrade_production",
            now=NOW,
            hook_dir=None,
            dry_run=True,
            fail_stage=None,
            pins_output=None,
            expected_gitops_commit=None,
            observed_gitops_commit=None,
            gitops_result=result,
            snapshot_result=None,
            upgrade_result=None,
        )
        self.assertTrue(value["mutation_started"])
        self.assertEqual(value["state"], "rolled_back")
        self.assertEqual(value["rollback"]["status"], "completed")
        deployment_run.validate(value)

    def test_alert_failure_cannot_discard_the_safety_transition(self) -> None:
        value = self.initial()
        environment = {
            "USL_ALERT_RELAY_URL": "https://relay.invalid/alert",
            "USL_ALERT_RELAY_SECRET": "synthetic-secret",
        }
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch("urllib.request.urlopen", side_effect=OSError("offline")),
        ):
            value = self.advance(value, "validate", fail="validate")
        self.assertEqual(value["state"], "failed_pre_mutation")
        self.assertTrue(value["incident"]["decision_required"])
        self.assertIn("alert delivery failed", value["incident"]["reason"])
        deployment_run.validate(value)

    def test_snapshot_result_updates_the_active_cohort_identity(self) -> None:
        value = self.through("snapshot")
        new_cohort = cohort(self.deployed, cohort_id="cohort-20260829-new")
        cohort_path = self._write("new-cohort.json", new_cohort)
        result = self._write(
            "snapshot-result.json",
            {
                "cohort_path": str(cohort_path),
                "cohort_id": new_cohort["cohort_id"],
                "cohort_contract_sha256": new_cohort["contract_sha256"],
            },
        )
        value = deployment_run.advance(
            value,
            "snapshot",
            now=NOW,
            hook_dir=None,
            dry_run=True,
            fail_stage=None,
            pins_output=None,
            expected_gitops_commit=None,
            observed_gitops_commit=None,
            gitops_result=None,
            snapshot_result=result,
            upgrade_result=None,
        )
        self.assertEqual(value["source"]["active_cohort_id"], new_cohort["cohort_id"])
        self.assertEqual(
            value["source"]["active_cohort_sha256"], new_cohort["contract_sha256"],
        )

    def test_failed_deploystack_readback_enters_recovery(self) -> None:
        value = self.through("upgrade_production")
        candidate = self._write(
            "candidate-sync.json",
            {"expected_commit": "e" * 40, "observed_commit": "e" * 40},
        )
        upgrade = self._write(
            "upgrade-result.json",
            {
                "expected_deployed_commit": "e" * 40,
                "observed_deployed_commit": "f" * 40,
                "odoo_upgrade_sha256": "a" * 64,
            },
        )
        value = deployment_run.advance(
            value,
            "upgrade_production",
            now=NOW,
            hook_dir=None,
            dry_run=True,
            fail_stage=None,
            pins_output=None,
            expected_gitops_commit=None,
            observed_gitops_commit=None,
            gitops_result=candidate,
            snapshot_result=None,
            upgrade_result=upgrade,
        )
        self.assertEqual(value["state"], "rolled_back")
        self.assertEqual(value["rollback"]["status"], "completed")

    def test_deadline_reopens_safely_before_mutation_and_recovers_after(self) -> None:
        value = self.through("restore")
        value = deployment_run.advance(
            value,
            "restore",
            now=NOW + timedelta(hours=3),
            hook_dir=None,
            dry_run=True,
            fail_stage=None,
            pins_output=None,
            expected_gitops_commit=None,
            observed_gitops_commit=None,
            gitops_result=None,
            snapshot_result=None,
            upgrade_result=None,
        )
        self.assertEqual(value["state"], "deferred")
        self.assertEqual(value["writers"], "open")
        self.assertEqual(value["rollback"]["status"], "pre_mutation_reopened")

        value = self.through("admit")
        value = deployment_run.advance(
            value,
            "admit",
            now=NOW + timedelta(hours=3),
            hook_dir=None,
            dry_run=True,
            fail_stage=None,
            pins_output=None,
            expected_gitops_commit=None,
            observed_gitops_commit=None,
            gitops_result=None,
            snapshot_result=None,
            upgrade_result=None,
        )
        self.assertEqual(value["state"], "rolled_back")
        self.assertEqual(value["rollback"]["status"], "completed")

    def test_cutoff_and_window_defer_before_mutation(self) -> None:
        late = NOW + timedelta(hours=4)
        value = deployment_run.initialize(
            run_id="run-late",
            mode="release",
            deployed_release_path=self.root / "deployed.json",
            candidate_release_path=self.root / "candidate.json",
            cohort_path=self.root / "cohort.json",
            now=late,
        )
        self.assertEqual(value["state"], "deferred")
        value = self.initial()
        value = deployment_run.advance(
            value,
            "validate",
            now=NOW + timedelta(hours=3),
            hook_dir=None,
            dry_run=True,
            fail_stage=None,
            pins_output=None,
            expected_gitops_commit=None,
            observed_gitops_commit=None,
            gitops_result=None,
            snapshot_result=None,
            upgrade_result=None,
        )
        self.assertEqual(value["state"], "deferred")


class RetentionPolicyTest(unittest.TestCase):
    def test_retention_requires_restore_and_append_only_expiry(self) -> None:
        entries = []
        base = datetime(2026, 8, 29, tzinfo=UTC)
        for index in range(500):
            value = cohort(cohort_id=f"cohort-{index:04d}")
            created = base - timedelta(days=index)
            value["created_at"] = created.isoformat().replace("+00:00", "Z")
            value = with_checksum(value)
            entries.append(
                {
                    "cohort": value,
                    "append_only_until": (created + timedelta(days=30))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "restore_verified": True,
                },
            )
        value = retention_policy.plan(entries, base + timedelta(days=1))
        self.assertEqual(value["policy"], {"daily": 14, "weekly": 8, "monthly": 12})
        self.assertTrue(value["blocked"])
        self.assertTrue(value["delete"])
        broken = copy.deepcopy(entries)
        broken[-1]["restore_verified"] = False
        with self.assertRaisesRegex(
            retention_policy.RetentionError, "pruning is forbidden",
        ):
            retention_policy.plan(broken, base)


class ContinuousOperationsBoundaryTest(unittest.TestCase):
    def test_compose_is_inert_and_uses_file_backed_dynamic_results(self) -> None:
        compose = (ROOT / "deploy/continuous-operations/compose.yaml").read_text(
            encoding="utf-8",
        )
        self.assertIn('profiles: ["operations"]', compose)
        self.assertIn("file: ${USL_ALERT_RELAY_SECRET_HOST_FILE:?", compose)
        self.assertIn("/evidence/candidate-gitops.json", compose)
        self.assertIn("/evidence/admitted-gitops.json", compose)
        self.assertIn("/evidence/upgrade-result.json", compose)
        self.assertNotIn("USL_EXPECTED_CANDIDATE_GITOPS_COMMIT", compose)

    def test_alert_uses_the_existing_relay_header(self) -> None:
        source = (ROOT / "scripts/deployment_run.py").read_text(encoding="utf-8")
        self.assertIn('"X-Relay-Secret": secret', source)
        self.assertNotIn('"Authorization": f"Bearer', source)

    def test_non_dry_run_preflight_requires_complete_audited_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hooks = root / "hooks"
            hooks.mkdir()
            with self.assertRaisesRegex(
                deployment_run.DeploymentRunError, "hooks are incomplete",
            ):
                deployment_run._preflight_runtime(  # noqa: SLF001
                    {"mode": "release"}, hooks,
                )
            for name in set(STAGES) | deployment_run.RECOVERY_HOOKS:
                path = hooks / name
                path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                path.chmod(0o700)
            mounts = {}
            for variable in (
                "USL_OPERATIONS_CONTRACT_ROOT",
                "USL_OPERATIONS_STATE_ROOT",
                "USL_OPERATIONS_EVIDENCE_ROOT",
                "USL_GITOPS_CHECKOUT",
            ):
                path = root / variable.lower()
                path.mkdir()
                mounts[variable] = str(path)
            mounts.update(
                {"USL_EINVOICE_LIVE_ENABLED": "0", "USL_EREPORTING_LIVE_ENABLED": "0"},
            )
            with mock.patch.dict("os.environ", mounts, clear=True):
                deployment_run._preflight_runtime({"mode": "release"}, hooks)  # noqa: SLF001

    def test_run_contract_rejects_state_and_order_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deployed = release(PREVIOUS)
            for name, value in (
                ("deployed", deployed),
                ("candidate", release()),
                ("cohort", cohort(deployed)),
            ):
                (root / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
            value = deployment_run.initialize(
                run_id="run-tamper",
                mode="release",
                deployed_release_path=root / "deployed.json",
                candidate_release_path=root / "candidate.json",
                cohort_path=root / "cohort.json",
                now=NOW - timedelta(minutes=15),
            )
            value["state"] = "invented"
            value = with_checksum(value)
            with self.assertRaisesRegex(
                deployment_run.DeploymentRunError, "state is invalid",
            ):
                deployment_run.validate(value)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import json
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from migration.cohort_promotion import cohort
from scripts.tests.test_distribution_release import artifact as distribution_artifact


FINGERPRINT = "1" * 64
RELEASE_COMMIT = "a" * 40
DOCUMENTS_MANIFEST_SHA = "2" * 64


class EvolvedCohortTest(unittest.TestCase):
    def write_json(self, root: Path, relative: str, value: dict) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path

    def write_text(self, root: Path, relative: str, value: str = "fixture\n") -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
        return path

    def write_archive(self, root: Path, relative: str, name: str = "state.json") -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        with tarfile.open(path, "w:gz") as archive:
            payload = b"state"
            information = tarfile.TarInfo(name)
            information.size = len(payload)
            archive.addfile(information, io.BytesIO(payload))
        path.chmod(0o600)
        return path

    def fixture(self, root: Path) -> dict:
        root.chmod(0o700)
        candidate = {
            "schema": "usl-production-migration-candidate-v2",
            "fingerprint": FINGERPRINT,
            # These pre-work counts are deliberately stale and must never be
            # compared to the evolved controls below.
            "qualification": {"accounting": {"move_count": 1}},
        }
        candidate_path = self.write_json(root, "evidence/source-candidate-manifest.json", candidate)
        release = {
            "schema": "usl-release-identity-v1",
            "tree_clean": True,
            "release_commit": RELEASE_COMMIT,
            "source": {"dump_sha256": "3" * 64},
        }
        release["identity_sha256"] = cohort.canonical_sha256(release)
        self.write_json(root, "evidence/release-identity.json", release)
        distribution_path = self.write_json(
            root,
            "evidence/distribution-release.json",
            distribution_artifact(),
        )
        distribution_sha256 = cohort.sha256_file(distribution_path)
        controls = {
            "schema": "usl-evolved-transition-controls-v1",
            "status": "passed",
            "source_candidate_fingerprint": FINGERPRINT,
            "source_candidate_manifest_sha256": cohort.sha256_file(candidate_path),
            "release_identity_sha256": release["identity_sha256"],
            "distribution_release_sha256": distribution_sha256,
            "documents_manifest_sha256": DOCUMENTS_MANIFEST_SHA,
            "accounting": {
                "balanced": True,
                "posted_debit": "42.00",
                "posted_credit": "42.00",
                "move_count": 99,
            },
            **{name: 0 for name in cohort.REQUIRED_ZERO_CONTROLS},
        }
        self.write_json(root, "evidence/current-controls.json", controls)
        controls_sha256 = cohort.canonical_sha256(controls)
        bindings = {
            "source_candidate_fingerprint": FINGERPRINT,
            "release_identity_sha256": release["identity_sha256"],
            "distribution_release_sha256": distribution_sha256,
            "documents_manifest_sha256": DOCUMENTS_MANIFEST_SHA,
            "current_controls_sha256": controls_sha256,
        }
        self.write_json(
            root,
            "evidence/sanitation.json",
            {
                "schema": "usl-evolved-cohort-sanitation-v1",
                "status": "passed",
                "source_mutated": False,
                "odoo_clone_sanitized": True,
                "paperless_clone_sanitized": True,
                "transient_identities_removed": True,
                "transfer_configuration_contains_secrets": False,
                **bindings,
            },
        )
        self.write_json(
            root,
            "evidence/security-gates.json",
            {
                "schema": "usl-evolved-cohort-security-gates-v1",
                "status": "passed",
                "release_identity_sha256": release["identity_sha256"],
                "distribution_release_sha256": distribution_sha256,
                "source_candidate_fingerprint": FINGERPRINT,
                "documents_manifest_sha256": DOCUMENTS_MANIFEST_SHA,
                "current_controls_sha256": controls_sha256,
                **{name: True for name in cohort.GATE_CHECKS},
            },
        )
        self.write_json(root, "documents/manifest.json", {"schema": "nested"})
        self.write_text(root, "documents/SHA256SUMS")
        sign_archives = {}
        for key, name in (
            ("step_ca", "step-ca.tgz"),
            ("dss", "dss.tgz"),
            ("evidence", "evidence.tgz"),
        ):
            path = self.write_archive(root, f"sign/{name}")
            sign_archives[key] = {
                "complete": True,
                "file_count": 1,
                "archive_sha256": cohort.sha256_file(path),
                "archive_size": path.stat().st_size,
            }
        sign_manifest = self.write_json(
            root,
            "sign/manifest.json",
            {
                "schema": "usl-sign-transfer-state-v1",
                "status": "passed",
                "release_identity_sha256": release["identity_sha256"],
                **sign_archives,
            },
        )
        components = {
            "candidate_fingerprint": FINGERPRINT,
            "source_candidate_manifest_sha256": cohort.sha256_file(candidate_path),
            "release_identity_sha256": release["identity_sha256"],
            "distribution_release_sha256": distribution_sha256,
            "release_commit": RELEASE_COMMIT,
            "documents_manifest_sha256": DOCUMENTS_MANIFEST_SHA,
            "sign_manifest_sha256": cohort.sha256_file(sign_manifest),
        }
        self.write_json(
            root,
            "evidence/independent-restore.json",
            {
                "schema": "usl-evolved-cohort-independent-restore-v1",
                "status": "passed",
                "fresh_volumes": True,
                "source_project_distinct": True,
                "accounting_equal": True,
                "documents_equal": True,
                "paperless_equal": True,
                "sign_equal": True,
                "vector_equal": True,
                "tantivy_equal": True,
                "ocr_submissions": 0,
                "reingestion_submissions": 0,
                "vector_rebuild": False,
                "model_download": False,
                "component_fingerprint": cohort.canonical_sha256(components),
                **bindings,
            },
        )
        self.write_json(
            root,
            "configuration/non-secret-runtime.json",
            {"schema": "runtime-v1", "odoo_url": "https://odoo.example.test"},
        )
        self.write_json(
            root,
            "configuration/required-secret-names.json",
            {"required": ["POSTGRES_PASSWORD", "PAPERLESS_SECRET_KEY"]},
        )
        for name in ("restore", "admission", "rollback"):
            self.write_text(root, f"configuration/{name}-instructions.md")
        return release

    def documents_manifest(self) -> dict:
        return {
            "manifest_sha256": DOCUMENTS_MANIFEST_SHA,
            "identity": {"git": {"odoo_commit": RELEASE_COMMIT}},
        }

    def test_seal_verify_and_accept_bind_evolved_controls_not_old_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            with (
                mock.patch.object(
                    cohort.documents_release_bundle,
                    "verify",
                    return_value=self.documents_manifest(),
                ),
                mock.patch.object(
                    cohort.documents_release_bundle,
                    "accept",
                    return_value=self.documents_manifest(),
                ),
            ):
                sealed = cohort.seal(root)
                self.assertEqual(sealed["identity"]["candidate_fingerprint"], FINGERPRINT)
                self.assertEqual(cohort.verify(root), sealed)
                self.assertEqual(cohort.accept(root), sealed)
            self.assertEqual((root / "manifest.json").stat().st_mode & 0o777, 0o600)

    def test_unbalanced_current_controls_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            path = root / "evidence/current-controls.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["accounting"]["posted_credit"] = "41.00"
            self.write_json(root, "evidence/current-controls.json", value)
            with mock.patch.object(
                cohort.documents_release_bundle,
                "verify",
                return_value=self.documents_manifest(),
            ):
                with self.assertRaisesRegex(cohort.CohortError, "not balanced"):
                    cohort.seal(root)

    def test_tamper_after_seal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            with mock.patch.object(
                cohort.documents_release_bundle,
                "verify",
                return_value=self.documents_manifest(),
            ):
                cohort.seal(root)
                path = root / "configuration/restore-instructions.md"
                path.write_text("changed\n", encoding="utf-8")
                path.chmod(0o600)
                with self.assertRaisesRegex(cohort.CohortError, "artifacts differ"):
                    cohort.verify(root)

    def transition_evidence(self, root: Path, action: str, **values: bool) -> Path:
        schemas = {
            "configure": "usl-evolved-cohort-configuration-v1",
            "gate": "usl-evolved-cohort-production-gate-v1",
            "admit": "usl-evolved-cohort-admission-v1",
        }
        return self.write_json(
            root,
            f"{action}.json",
            {
                "schema": schemas[action],
                "status": "passed",
                "cohort_fingerprint": FINGERPRINT,
                **values,
            },
        )

    def test_admission_state_requires_ordered_complete_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            state = root / "state.json"
            cohort.transition(state, FINGERPRINT, "preflight", None)
            cohort.transition(state, FINGERPRINT, "restore", None)
            configure = self.transition_evidence(
                root,
                "configure",
                identity_reconfigured=True,
                secrets_external=True,
                pocket_state_not_transferred=True,
                outbound_disabled=True,
            )
            cohort.transition(state, FINGERPRINT, "configure", configure)
            configured = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(
                configured["history"][-1]["evidence_sha256"],
                cohort.sha256_file(configure),
            )
            incomplete_gate = self.transition_evidence(root, "gate", release_identity=True)
            with self.assertRaisesRegex(cohort.CohortError, "incomplete"):
                cohort.transition(state, FINGERPRINT, "gate", incomplete_gate)
            gate = self.transition_evidence(
                root,
                "gate",
                **{name: True for name in cohort.GATE_CHECKS},
            )
            cohort.transition(state, FINGERPRINT, "gate", gate)
            admission = self.transition_evidence(
                root,
                "admit",
                backup_restore_proven=True,
                ingress_ready=True,
                go_no_go_approved=True,
                rollback_ready=True,
            )
            admitted = cohort.transition(state, FINGERPRINT, "admit", admission)
            self.assertEqual(admitted["status"], "admitted")
            self.assertFalse(admitted["reset_allowed"])
            with self.assertRaisesRegex(cohort.CohortError, "cannot preflight"):
                cohort.transition(state, FINGERPRINT, "preflight", None)

    def test_state_write_rejects_non_private_parent_without_changing_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o755)
            with self.assertRaisesRegex(cohort.CohortError, "mode 0700"):
                cohort.transition(root / "state.json", FINGERPRINT, "preflight", None)
            self.assertEqual(root.stat().st_mode & 0o777, 0o755)

    def test_configuration_rejects_secret_shaped_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            self.write_json(
                root,
                "configuration/non-secret-runtime.json",
                {"client_secret": "must-not-travel"},
            )
            with self.assertRaisesRegex(cohort.CohortError, "secret-shaped key"):
                cohort.artifact_manifest(root)

    def test_non_secret_token_endpoint_is_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            self.write_json(
                root,
                "configuration/non-secret-runtime.json",
                {"oidc_token_endpoint": "https://identity.example.test/token"},
            )
            artifacts = cohort.artifact_manifest(root)
            self.assertIn("configuration/non-secret-runtime.json", artifacts)

    def test_configuration_rejects_embedded_secret_value(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            self.write_text(
                root,
                "configuration/restore-instructions.md",
                "PAPERLESS_SECRET_KEY=real-secret-must-not-travel\n",
            )
            with self.assertRaisesRegex(cohort.CohortError, "secret-shaped value"):
                cohort.artifact_manifest(root)

    def test_secret_scanner_never_reads_non_configuration_payloads(self):
        with mock.patch.object(
            Path,
            "read_bytes",
            side_effect=AssertionError("large payload must not be scanned"),
        ):
            cohort.validate_configuration(
                Path("/does/not/exist"),
                "documents/odoo/database.dump",
            )

    def test_configuration_rejects_url_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            self.write_text(
                root,
                "configuration/restore-instructions.md",
                "postgresql://operator:must-not-travel@db/odoo\n",
            )
            with self.assertRaisesRegex(cohort.CohortError, "secret-shaped value"):
                cohort.artifact_manifest(root)

    def test_sign_capture_is_deterministic_private_and_nonempty(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = {
                "schema": "usl-release-identity-v1",
                "tree_clean": True,
                "release_commit": RELEASE_COMMIT,
            }
            release["identity_sha256"] = cohort.canonical_sha256(release)
            release_path = self.write_json(root, "release.json", release)
            distribution_path = self.write_json(
                root,
                "distribution-release.json",
                distribution_artifact(),
            )
            sources = []
            for name in ("step-ca", "dss", "evidence"):
                source = root / name
                source.mkdir(mode=0o700)
                self.write_text(source, "state/value.json", '{"value": 1}\n')
                sources.append(source)
            results = []
            for name in ("bundle-a", "bundle-b"):
                bundle = root / name
                bundle.mkdir(mode=0o700)
                results.append(
                    cohort.capture_sign(
                        bundle,
                        *sources,
                        release_path,
                        distribution_path,
                    )
                )
                self.assertEqual((bundle / "sign").stat().st_mode & 0o777, 0o700)
                for path in (bundle / "sign").iterdir():
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for key in ("step_ca", "dss", "evidence"):
                self.assertGreater(results[0][key]["file_count"], 0)
                self.assertEqual(
                    results[0][key]["archive_sha256"],
                    results[1][key]["archive_sha256"],
                )

    def test_sign_capture_rejects_empty_or_symlinked_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = {
                "schema": "usl-release-identity-v1",
                "tree_clean": True,
                "release_commit": RELEASE_COMMIT,
            }
            release["identity_sha256"] = cohort.canonical_sha256(release)
            release_path = self.write_json(root, "release.json", release)
            empty = root / "empty"
            empty.mkdir(mode=0o700)
            with self.assertRaisesRegex(cohort.CohortError, "empty"):
                cohort.archive_directory(empty, root / "empty.tgz")
            unsafe = root / "unsafe"
            unsafe.mkdir(mode=0o700)
            (unsafe / "link").symlink_to(release_path)
            with self.assertRaisesRegex(cohort.CohortError, "unsafe"):
                cohort.archive_directory(unsafe, root / "unsafe.tgz")

    def test_sign_restore_is_fresh_private_and_fingerprint_confirmed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            root.mkdir(mode=0o700)
            self.fixture(root)
            manifest = {"cohort_fingerprint": FINGERPRINT}
            destination = Path(temporary) / "restored-sign"
            with mock.patch.object(cohort, "accept", return_value=manifest):
                restored = cohort.restore_sign(root, destination, FINGERPRINT)
                self.assertEqual(restored["status"], "passed")
                self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
                for path in destination.rglob("*"):
                    expected = 0o700 if path.is_dir() else 0o600
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected)
                with self.assertRaisesRegex(cohort.CohortError, "fresh"):
                    cohort.restore_sign(root, destination, FINGERPRINT)

    def test_sign_runtime_restore_uses_exact_fresh_destinations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            root.mkdir(mode=0o700)
            self.fixture(root)
            destinations = [Path(temporary) / name for name in ("step", "dss", "evidence")]
            with mock.patch.object(
                cohort,
                "accept",
                return_value={"cohort_fingerprint": FINGERPRINT},
            ):
                restored = cohort.restore_sign_runtime(
                    root,
                    *destinations,
                    FINGERPRINT,
                )
            self.assertEqual(restored["status"], "passed")
            self.assertTrue(all(path.is_dir() for path in destinations))
            self.assertTrue(all((path / "state.json").is_file() for path in destinations))

    def test_preseal_component_rehearsal_uses_accepted_component_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            root.mkdir(mode=0o700)
            self.fixture(root)
            destination = Path(temporary) / "rehearsed-sign"
            with mock.patch.object(
                cohort.documents_release_bundle,
                "accept",
                return_value=self.documents_manifest(),
            ):
                identity = cohort.component_identity(root, accept_documents=True)
                restored = cohort.restore_sign_components(
                    root,
                    destination,
                    identity["component_fingerprint"],
                )
                self.assertEqual(restored["status"], "passed")
                self.assertEqual(
                    restored["component_fingerprint"],
                    identity["component_fingerprint"],
                )
                with self.assertRaisesRegex(cohort.CohortError, "component fingerprint"):
                    cohort.restore_sign_components(root, destination, "9" * 64)


if __name__ == "__main__":
    unittest.main()

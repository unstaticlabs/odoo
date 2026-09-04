import hashlib
import importlib.util
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location(
    "migration_candidate",
    ROOT / "migration/candidate.py",
)
migration_candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration_candidate)


class MigrationCandidateManifestTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        temporary = Path(self.temporary.name)
        self.root = temporary / "root"
        self.source = temporary / "source"
        self.candidate = temporary / "candidate"
        for directory in (self.root, self.source, self.candidate):
            directory.mkdir(mode=0o700)
        for relative in migration_candidate.digests.MIGRATION_INPUTS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if Path(relative).suffix:
                path.write_text(f"input:{relative}\n", encoding="utf-8")
                path.chmod(0o600)
            else:
                path.mkdir(parents=True, exist_ok=True, mode=0o700)
                item = path / "input.txt"
                item.write_text(f"input:{relative}\n", encoding="utf-8")
                item.chmod(0o600)
        self.dump = self.source / "dump.sql"
        self.dump.write_text("source database", encoding="utf-8")
        self.dump.chmod(0o600)
        filestore = self.source / "filestore"
        filestore.mkdir(mode=0o700)
        (filestore / "source-file").write_text("source", encoding="utf-8")
        (filestore / "source-file").chmod(0o600)

        self._write_file("odoo.dump", b"PGDMP-qualified")
        stored = b"file"
        stored_checksum = hashlib.sha1(  # noqa: S324 - Odoo filestore identity
            stored,
            usedforsecurity=False,
        ).hexdigest()
        stored_name = f"{stored_checksum[:2]}/{stored_checksum}"
        self._write_archive("odoo-filestore.tgz", stored_name, stored)
        self._write_archive("paperless-export.tgz", "manifest.json", b"[]")
        evidence = self.candidate / "evidence"
        evidence.mkdir(mode=0o700)
        (evidence / "controls.json").write_text("{}\n", encoding="utf-8")
        (evidence / "controls.json").chmod(0o600)
        inventory = evidence / "odoo-filestore-attachments.tsv"
        inventory.write_text(
            f"{stored_name}\t{stored_checksum}\t{len(stored)}\n",
            encoding="utf-8",
        )
        inventory.chmod(0o600)

        source_dump_sha = migration_candidate.digests.sha256_file(self.dump)
        source_filestore_sha = migration_candidate.digests.tree_digest(filestore)[0]
        migration_sha = migration_candidate.digests.migration_digest(self.root)
        self.release = {
            "schema": "usl-release-identity-v1",
            "release_commit": "a" * 40,
            "tree_clean": True,
            "upstream_saas_19_3_commit": "b" * 40,
            "source": {"dump_sha256": source_dump_sha},
            "oca": {"bundle_sha256": "c" * 64},
            "action_risk_policy_sha256": "e" * 64,
            "product_module_versions": {"usl_accounting": "19.0.1.0.0"},
            "image": {
                "reference": f"ghcr.io/usl/odoo@sha256:{'d' * 64}",
                "repo_digests": [f"ghcr.io/usl/odoo@sha256:{'d' * 64}"],
                "labels": {
                    "org.opencontainers.image.revision": "a" * 40,
                    "com.unstaticlabs.odoo.oca-bundle-sha256": "c" * 64,
                    "com.unstaticlabs.odoo.action-risk-policy-sha256": "e" * 64,
                    "com.unstaticlabs.odoo.runtime": "distribution",
                },
            },
        }
        self._refresh_release_digest()
        self.qualification = {
            "schema": migration_candidate.QUALIFICATION_SCHEMA,
            "status": "passed",
            "purpose": "production",
            "profile": "full",
            "regulatory_live_guards": "disabled",
            "release_commit": "a" * 40,
            "source_dump_sha256": source_dump_sha,
            "source_filestore_sha256": source_filestore_sha,
            "migration_sha256": migration_sha,
            "module_versions": self.release["product_module_versions"],
            "action_risk": {
                "status": "passed",
                "policy_sha256": self.release["action_risk_policy_sha256"],
            },
            "accounting": {
                "status": "passed",
                "controls": {"move_count": 1},
                "performance": {
                    "schema": "usl-accounting-import-run-performance-v1",
                    "stages": [{"name": "moves", "duration_seconds": 1}],
                },
            },
            "attachment_gate": {"status": "passed", "complete": True},
            "documents": {
                "status": "passed",
                "controls": {"odoo_document_count": 1},
                "paperless_document_count": 1,
                "paperless_image_digest": f"ghcr.io/usl/paperless@sha256:{'e' * 64}",
                "ollama_image_digest": f"docker.io/ollama/ollama@sha256:{'f' * 64}",
                "reconstruction": {
                    "downloaded_bytes": 100,
                    "ocr_submissions": 1,
                },
            },
            "migration_boundary": "passed",
            "multi_company": {"status": "passed"},
            "odoo_filestore": {
                "status": "passed",
                "distinct_store_file_count": 1,
                "archive_sha256": migration_candidate.digests.sha256_file(
                    self.candidate / "odoo-filestore.tgz",
                ),
            },
            "product_database_boundary": "passed",
            "source_gate": {"status": "passed", "complete": True},
            "sanitation": {
                "status": "passed",
                "odoo": {"status": "passed", "standard_neutralized": True},
                "paperless": {"status": "passed"},
            },
        }
        self._write_json("release-identity.json", self.release)
        self._write_json("qualification.json", self.qualification)

    def tearDown(self):
        self.temporary.cleanup()

    def _write_file(self, relative, content):
        path = self.candidate / relative
        path.write_bytes(content)
        path.chmod(0o600)

    def _write_json(self, relative, value):
        path = self.candidate / relative
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def _write_archive(self, relative, member, content):
        archive = self.candidate / relative
        with tarfile.open(archive, "w:gz") as stream:
            info = tarfile.TarInfo(member)
            info.size = len(content)
            info.mode = 0o600
            info.uid = 1000
            info.gid = 1000
            stream.addfile(info, io.BytesIO(content))
        archive.chmod(0o600)

    def _refresh_release_digest(self):
        unsigned = {
            key: value
            for key, value in self.release.items()
            if key != "identity_sha256"
        }
        self.release["identity_sha256"] = migration_candidate.canonical_sha256(
            unsigned,
        )

    def args(self, expected=None):
        return Namespace(
            candidate_dir=self.candidate,
            expected_fingerprint=expected,
            root=self.root,
            source_dir=self.source,
        )

    def test_seal_and_verify_with_independent_fingerprint(self):
        self.assertEqual(
            migration_candidate.SCHEMA,
            "usl-production-migration-candidate-v2",
        )
        self.assertEqual(
            migration_candidate.QUALIFICATION_SCHEMA,
            "usl-production-candidate-qualification-v2",
        )
        manifest = migration_candidate.seal(self.args())
        verified = migration_candidate.verify(self.args(manifest["fingerprint"]))

        self.assertEqual(verified["schema"], migration_candidate.SCHEMA)
        self.assertEqual(
            verified["identity"]["image_digest"],
            f"ghcr.io/usl/odoo@sha256:{'d' * 64}",
        )
        self.assertEqual((self.candidate / "manifest.json").stat().st_mode & 0o777, 0o600)

        offline = self.args(manifest["fingerprint"])
        offline.source_dir = None
        self.assertEqual(
            migration_candidate.verify(offline)["fingerprint"],
            manifest["fingerprint"],
        )

    def test_tampered_artifact_is_rejected(self):
        migration_candidate.seal(self.args())
        self._write_file("odoo.dump", b"PGDMP-tampered")

        with self.assertRaisesRegex(
            migration_candidate.CandidateError,
            "manifest or artifacts differ",
        ):
            migration_candidate.verify(self.args())

    def test_wrong_fingerprint_is_rejected(self):
        migration_candidate.seal(self.args())

        with self.assertRaisesRegex(
            migration_candidate.CandidateError,
            "not independently approved",
        ):
            migration_candidate.verify(self.args("0" * 64))

    def test_changed_source_package_is_rejected(self):
        migration_candidate.seal(self.args())
        self.dump.write_text("another source database", encoding="utf-8")

        with self.assertRaisesRegex(
            migration_candidate.CandidateError,
            "release identity refers to another source dump",
        ):
            migration_candidate.verify(self.args())

    def test_incomplete_whole_source_gate_is_rejected(self):
        self.qualification["source_gate"]["complete"] = False
        self._write_json("qualification.json", self.qualification)

        with self.assertRaisesRegex(
            migration_candidate.CandidateError,
            "source_gate is not complete",
        ):
            migration_candidate.seal(self.args())

    def test_incomplete_accounting_timings_are_rejected(self):
        self.qualification["accounting"]["performance"]["stages"] = []
        self._write_json("qualification.json", self.qualification)

        with self.assertRaisesRegex(
            migration_candidate.CandidateError,
            "Accounting controls/timings",
        ):
            migration_candidate.seal(self.args())

    def test_unsafe_archive_member_is_rejected(self):
        archive = self.candidate / "paperless-export.tgz"
        with tarfile.open(archive, "w:gz") as stream:
            info = tarfile.TarInfo("../../escape")
            info.size = 1
            stream.addfile(info, fileobj=__import__("io").BytesIO(b"x"))
        archive.chmod(0o600)

        with self.assertRaisesRegex(
            migration_candidate.CandidateError,
            "unsafe archive member",
        ):
            migration_candidate.seal(self.args())

    def test_symlink_artifact_is_rejected(self):
        (self.candidate / "evidence/link").symlink_to(
            self.candidate / "qualification.json",
        )

        with self.assertRaisesRegex(
            migration_candidate.CandidateError,
            "may not be symlinks",
        ):
            migration_candidate.seal(self.args())

    def test_non_private_mode_is_rejected(self):
        os.chmod(self.candidate / "qualification.json", 0o640)

        with self.assertRaisesRegex(
            migration_candidate.CandidateError,
            "group/other",
        ):
            migration_candidate.seal(self.args())

    def test_release_runtime_identity_is_fail_closed(self):
        invalid_cases = (
            ("upstream_saas_19_3_commit", None, "saas-19.3 base"),
            ("oca", {"bundle_sha256": "bad"}, "OCA bundle digest"),
            ("action_risk_policy_sha256", "bad", "action-risk policy digest"),
            ("product_module_versions", {}, "module versions"),
        )
        for key, value, message in invalid_cases:
            with self.subTest(key=key):
                original = self.release[key]
                self.release[key] = value
                self._refresh_release_digest()
                self._write_json("release-identity.json", self.release)
                with self.assertRaisesRegex(
                    migration_candidate.CandidateError,
                    message,
                ):
                    migration_candidate.seal(self.args())
                self.release[key] = original
        self._refresh_release_digest()
        self._write_json("release-identity.json", self.release)

    def test_release_image_labels_must_match_qualified_identity(self):
        self.release["image"]["labels"]["org.opencontainers.image.revision"] = (
            "e" * 40
        )
        self._refresh_release_digest()
        self._write_json("release-identity.json", self.release)

        with self.assertRaisesRegex(
            migration_candidate.CandidateError,
            "Distribution image",
        ):
            migration_candidate.seal(self.args())

    def test_release_image_action_risk_label_must_match_policy(self):
        self.release["image"]["labels"][
            "com.unstaticlabs.odoo.action-risk-policy-sha256"
        ] = "f" * 64
        self._refresh_release_digest()
        self._write_json("release-identity.json", self.release)

        with self.assertRaisesRegex(
            migration_candidate.CandidateError,
            "Distribution image",
        ):
            migration_candidate.seal(self.args())


if __name__ == "__main__":
    unittest.main()

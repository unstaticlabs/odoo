import tempfile
import unittest
from pathlib import Path

from migration.sign_restore.source import (
    identity_search_domains,
    match_exports,
    sha1,
    validate_source_structure,
)


class TestSignIdentityMatching(unittest.TestCase):
    def test_user_falls_back_from_source_login_to_linked_partner_email(self):
        self.assertEqual(
            identity_search_domains(
                "res.users",
                login="odoo@unstaticlabs.com",
                email="valentin@unstaticlabs.com",
            ),
            [
                ("login", [("login", "=ilike", "odoo@unstaticlabs.com")]),
                (
                    "linked partner email",
                    [("partner_id.email", "=ilike", "valentin@unstaticlabs.com")],
                ),
            ],
        )

    def test_partner_uses_email_without_a_user_login_domain(self):
        self.assertEqual(
            identity_search_domains(
                "res.partner",
                login="ignored",
                email="signer@example.com",
            ),
            [("email", [("email", "=ilike", "signer@example.com")])],
        )


class TestSignExportMatching(unittest.TestCase):
    def test_matches_signed_exports_by_bytes_not_ambiguous_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "same-name.pdf"
            second = root / "other.pdf"
            first.write_bytes(b"first signed PDF")
            second.write_bytes(b"second signed PDF")
            (root / "Certificate - same-name.pdf").write_bytes(b"first certificate")
            (root / "Certificate - other.pdf").write_bytes(b"second certificate")
            source = {
                "requests": [{"id": 1}, {"id": 2}],
                "attachments": [
                    {
                        "sign_request_id": 1,
                        "kind": "signed",
                        "checksum": sha1(first.read_bytes()),
                        "file_size": first.stat().st_size,
                    },
                    {
                        "sign_request_id": 2,
                        "kind": "signed",
                        "checksum": sha1(second.read_bytes()),
                        "file_size": second.stat().st_size,
                    },
                ],
            }

            matches = match_exports(source, root)

            self.assertEqual(matches[1]["signed"], first.resolve())
            self.assertEqual(matches[2]["signed"], second.resolve())
            self.assertNotEqual(matches[1]["signed_sha256"], matches[2]["signed_sha256"])

    def test_prohibits_reusing_one_export_for_two_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            signed = root / "shared.pdf"
            signed.write_bytes(b"same bytes")
            (root / "Certificate - shared.pdf").write_bytes(b"certificate")
            source = {
                "requests": [{"id": 1}, {"id": 2}],
                "attachments": [
                    {
                        "sign_request_id": 1,
                        "kind": "signed",
                        "checksum": sha1(signed.read_bytes()),
                        "file_size": signed.stat().st_size,
                    },
                    {
                        "sign_request_id": 2,
                        "kind": "signed",
                        "checksum": sha1(signed.read_bytes()),
                        "file_size": signed.stat().st_size,
                    },
                ],
            }
            with self.assertRaisesRegex(RuntimeError, "reused"):
                match_exports(source, root)

    def test_rejects_a_checksum_match_with_the_wrong_recorded_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            signed = root / "signed.pdf"
            signed.write_bytes(b"signed")
            (root / "Certificate - signed.pdf").write_bytes(b"certificate")
            source = {
                "requests": [{"id": 1}],
                "attachments": [
                    {
                        "sign_request_id": 1,
                        "kind": "signed",
                        "checksum": sha1(signed.read_bytes()),
                        "file_size": signed.stat().st_size + 1,
                    },
                ],
            }

            with self.assertRaisesRegex(RuntimeError, "0 signed export matches"):
                match_exports(source, root)

    def test_rejects_ambiguous_source_signed_attachments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            signed = root / "signed.pdf"
            signed.write_bytes(b"signed")
            (root / "Certificate - signed.pdf").write_bytes(b"certificate")
            source = {
                "requests": [{"id": 1}],
                "attachments": [
                    {
                        "sign_request_id": 1,
                        "kind": "signed",
                        "checksum": sha1(b"signed"),
                        "file_size": len(b"signed"),
                    },
                    {
                        "sign_request_id": 1,
                        "kind": "signed",
                        "checksum": sha1(b"signed"),
                        "file_size": len(b"signed"),
                    },
                ],
            }
            with self.assertRaisesRegex(RuntimeError, "ambiguous signed attachment"):
                match_exports(source, root)


class TestSignSourcePerimeter(unittest.TestCase):
    def test_accepts_exact_signed_external_archive_perimeter(self):
        source = {
            "requests": [{"id": 1, "state": "signed"}],
            "signers": [
                {"id": 2, "sign_request_id": 1, "state": "completed"},
            ],
            "attachments": [
                {"sign_request_id": 1, "kind": "signed"},
                {"sign_request_id": 1, "kind": "source_certificate"},
            ],
        }

        validate_source_structure(source)

    def test_rejects_missing_source_certificate(self):
        source = {
            "requests": [{"id": 1, "state": "signed"}],
            "signers": [
                {"id": 2, "sign_request_id": 1, "state": "completed"},
            ],
            "attachments": [{"sign_request_id": 1, "kind": "signed"}],
        }

        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            validate_source_structure(source)


if __name__ == "__main__":
    unittest.main()

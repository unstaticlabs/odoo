import hashlib
import urllib.error
from datetime import timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.mail.tests.common import mail_new_test_user
from odoo.addons.usl_documents.models.download_grant import (
    UslDocumentDownloadGrant,
)
from odoo.addons.usl_documents.models.paperless_client import PaperlessClient
from odoo.addons.usl_documents.models.paperless_client import (
    PaperlessCompatibilityError,
    PaperlessUnavailable,
)


class _HttpBinaryStream:
    def __init__(self, *, method="GET", range_header=None):
        self._content = b"%PDF" + b"a" * 4092
        self.status = 206 if range_header else 200
        self.headers = {
            "Content-Type": "application/pdf",
            "Accept-Ranges": "bytes",
            "ETag": '"original-sha"',
        }
        if range_header:
            self._content = self._content[:10]
            self.headers.update(
                {
                    "Content-Length": "10",
                    "Content-Range": "bytes 0-9/4096",
                },
            )
        else:
            self.headers["Content-Length"] = "4096"
        if method == "HEAD":
            self._content = b""

    def iter_chunks(self):
        yield self._content

    def close(self):
        return None


@tagged("post_install", "-at_install", "usl_documents", "download_grants")
class TestDocumentDownloadGrants(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create(
            {"name": "Download Grant Restricted"},
        )
        cls.user = mail_new_test_user(
            cls.env,
            login="document-download-agent",
            name="Document Download Agent",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
            groups="usl_documents.group_documents_user",
        )
        params = cls.env["ir.config_parameter"].sudo()
        params.set_str("web.base.url", "https://odoo.example.test")
        params.set_str("web.base.url.freeze", "True")

    def _document(self, paperless_id=88001, *, archive_checksum="archive-sha"):
        document = self.env["usl.document"].sudo().create(
            {
                "name": "Bound invoice",
                "paperless_id": paperless_id,
                "company_id": self.company.id,
                "confidentiality": "internal",
                "review_state": "classified",
                "availability_state": "available",
                "permission_sync_state": "synchronized",
                "original_filename": "supplier invoice.pdf",
                "mime_type": "application/pdf",
            },
        )
        version = self.env["usl.document.version"].sudo().create(
            {
                "document_id": document.id,
                "paperless_version_id": "version-1",
                "label": "Version 1",
                "original_filename": "supplier invoice.pdf",
                "mime_type": "application/pdf",
                "checksum": "original-sha",
                "archive_checksum": archive_checksum,
                "is_current": True,
            },
        )
        return document, version

    def _issue(self, document, **kwargs):
        with patch.object(
            PaperlessClient,
            "probe_download",
            return_value={
                "status": 200,
                "headers": {
                    "Content-Length": "4096",
                    "Content-Type": "application/pdf",
                    "ETag": '"original-sha"',
                },
            },
        ):
            return self.env["usl.document"].with_user(self.user).with_context(
                allowed_company_ids=self.company.ids,
            ).mcp_create_download_grant(document.id, **kwargs)

    def test_issuance_binds_version_and_stores_only_token_hash(self):
        document, version = self._document()
        result = self._issue(document)
        token = urlsplit(result["url"]).path.rsplit("/", 1)[-1]
        grant = self.env["usl.document.download.grant"].sudo().search(
            [("public_id", "=", result["grant_id"])],
        )

        self.assertEqual(len(token), 43)
        self.assertNotEqual(grant.token_hash, token)
        self.assertEqual(
            grant.token_hash,
            hashlib.sha256(token.encode("ascii")).hexdigest(),
        )
        self.assertNotIn(token, str(grant.read()[0]))
        self.assertEqual(grant.document_version_id, version)
        self.assertEqual(grant.paperless_version_id, "version-1")
        self.assertEqual(result["size_bytes"], 4096)
        self.assertEqual(result["ttl_seconds"], 300)
        self.assertRegex(result["expires_at"], r"^\d{4}-\d{2}-\d{2}T.*Z$")
        self.assertEqual(grant.issued_by_odoo_id, self.user.id)
        self.assertEqual(
            self.env["usl.document.download.grant.audit"].sudo().search_count(
                [("grant_id", "=", grant.id), ("event_type", "=", "issued")],
            ),
            1,
        )

    def test_explicit_version_remains_bound_when_current_version_changes(self):
        document, version = self._document(88002)
        result = self._issue(document, document_version_id=version.id)
        version.sudo().write({"is_current": False})
        self.env["usl.document.version"].sudo().create(
            {
                "document_id": document.id,
                "paperless_version_id": "version-2",
                "label": "Version 2",
                "checksum": "new-sha",
                "is_current": True,
            },
        )
        grant = self.env["usl.document.download.grant"].sudo().search(
            [("public_id", "=", result["grant_id"])],
        )

        descriptor = grant.with_user(self.user).with_context(
            allowed_company_ids=self.company.ids,
        )._authorize_redemption()
        self.assertEqual(descriptor["paperless_version_id"], "version-1")

    def test_underlying_access_revocation_invalidates_unexpired_grant(self):
        document, _version = self._document(88003)
        result = self._issue(document)
        grant = self.env["usl.document.download.grant"].sudo().search(
            [("public_id", "=", result["grant_id"])],
        )
        document.sudo().with_context(usl_documents_cache_write=True).write(
            {"company_id": self.other_company.id},
        )

        with self.assertRaises(AccessError):
            grant.with_user(self.user).with_context(
                allowed_company_ids=self.company.ids,
            )._authorize_redemption()

    def test_archive_and_ttl_validation(self):
        document, _version = self._document(88004, archive_checksum=False)
        for ttl in (29, 901):
            with self.assertRaisesRegex(ValidationError, "between 30 and 900"):
                self._issue(document, ttl_seconds=ttl)
        with self.assertRaisesRegex(ValidationError, "no archive binary"):
            self._issue(document, variant="archive")

    def test_historical_archive_grant_binds_the_explicit_variant(self):
        document, version = self._document(88008)
        with patch.object(
            PaperlessClient,
            "probe_download",
            return_value={
                "status": 200,
                "headers": {
                    "Content-Length": "2048",
                    "Content-Type": "application/pdf",
                    "ETag": '"archive-sha"',
                },
            },
        ):
            result = self.env["usl.document"].with_user(self.user).with_context(
                allowed_company_ids=self.company.ids,
            ).mcp_create_download_grant(
                document.id,
                document_version_id=version.id,
                variant="archive",
            )

        grant = self.env["usl.document.download.grant"].sudo().search(
            [("public_id", "=", result["grant_id"])],
        )
        self.assertEqual(grant.document_version_odoo_id, version.id)
        self.assertEqual(grant.variant, "archive")
        self.assertEqual(grant.checksum, "archive-sha")
        self.assertEqual(result["mime_type"], "application/pdf")

    def test_cross_company_document_is_denied_before_paperless(self):
        document, _version = self._document(88009)
        document.sudo().with_context(usl_documents_cache_write=True).write(
            {"company_id": self.other_company.id},
        )
        with patch.object(PaperlessClient, "probe_download") as probe:
            with self.assertRaises(AccessError):
                self.env["usl.document"].with_user(self.user).with_context(
                    allowed_company_ids=self.company.ids,
                ).mcp_create_download_grant(document.id)
        probe.assert_not_called()

    def test_linked_record_binary_rule_is_enforced_before_paperless(self):
        document, _version = self._document(88011)
        document.sudo().with_context(usl_documents_policy_write=True).write(
            {"access_scope": "linked_record"},
        )
        with patch.object(PaperlessClient, "probe_download") as probe:
            with self.assertRaisesRegex(AccessError, "unavailable"):
                self.env["usl.document"].with_user(self.user).with_context(
                    allowed_company_ids=self.company.ids,
                ).mcp_create_download_grant(document.id)
        probe.assert_not_called()

    def test_canonical_url_must_be_frozen_https_before_paperless(self):
        document, _version = self._document(88010)
        params = self.env["ir.config_parameter"].sudo()
        for base_url, frozen in (
            ("http://odoo.example.test", "True"),
            ("https://odoo.example.test", "False"),
            ("https://odoo.example.test/path", "True"),
        ):
            with self.subTest(base_url=base_url, frozen=frozen):
                params.set_str("web.base.url", base_url)
                params.set_str("web.base.url.freeze", frozen)
                with patch.object(PaperlessClient, "probe_download") as probe:
                    with self.assertRaisesRegex(ValidationError, "canonical HTTPS"):
                        self.env["usl.document"].with_user(self.user).with_context(
                            allowed_company_ids=self.company.ids,
                        ).mcp_create_download_grant(document.id)
                probe.assert_not_called()

    def test_issuance_rejects_a_paperless_checksum_mismatch(self):
        document, _version = self._document(88007)
        with patch.object(
            PaperlessClient,
            "probe_download",
            return_value={
                "status": 200,
                "headers": {
                    "Content-Length": "4096",
                    "Content-Type": "application/pdf",
                    "ETag": '"different-checksum"',
                },
            },
        ):
            with self.assertRaisesRegex(ValidationError, "does not match"):
                self.env["usl.document"].with_user(self.user).mcp_create_download_grant(
                    document.id,
                )

    def test_revoke_is_idempotent_and_preserves_audit(self):
        document, _version = self._document(88005)
        result = self._issue(document)
        model = self.env["usl.document"].with_user(self.user)

        first = model.mcp_revoke_download_grant(result["grant_id"], reason="Done")
        second = model.mcp_revoke_download_grant(result["grant_id"], reason="Again")
        grant = self.env["usl.document.download.grant"].sudo().search(
            [("public_id", "=", result["grant_id"])],
        )
        self.assertTrue(first["revoked"])
        self.assertEqual(second["revoked_at"], first["revoked_at"])
        self.assertRegex(first["revoked_at"], r"^\d{4}-\d{2}-\d{2}T.*Z$")
        self.assertEqual(grant.revocation_reason, "Done")
        self.assertEqual(
            self.env["usl.document.download.grant.audit"].sudo().search_count(
                [("grant_id", "=", grant.id), ("event_type", "=", "revoked")],
            ),
            1,
        )

    def test_mcp_metadata_never_materializes_a_bearer_url(self):
        document, _version = self._document(88006)
        values = self.env["usl.document"].with_user(self.user).mcp_get(document.id)
        versions = self.env["usl.document"].with_user(self.user).mcp_get_versions(
            document.id,
        )

        serialized = str({"document": values, "versions": versions})
        self.assertNotIn("download_path", serialized)
        self.assertNotIn("preview_path", serialized)
        self.assertNotIn("paperless_url", serialized)
        self.assertTrue(values["binary_available"])
        self.assertTrue(values["materialization_required"])


@tagged("post_install", "-at_install", "usl_documents", "download_grants")
class TestPaperlessDownloadStreaming(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        params = cls.env["ir.config_parameter"].sudo()
        params.set_str("usl_documents.paperless_url", "http://paperless:8000")
        params.set_str("usl_documents.paperless_token", "server-only-token")
        params.set_str("usl_documents.paperless_stream_timeout", "60")

    @staticmethod
    def _response(content=b"a" * (128 * 1024)):
        response = MagicMock()
        response.status = 206
        response.getcode.return_value = 206
        response.headers = {
            "Content-Type": "application/pdf",
            "Content-Length": str(len(content)),
            "Content-Range": f"bytes 0-{len(content) - 1}/{len(content)}",
            "Accept-Ranges": "bytes",
            "ETag": '"checksum"',
        }
        body = BytesIO(content)
        response.read.side_effect = body.read
        return response

    def test_open_download_forwards_only_validated_binary_headers_and_streams(self):
        client = PaperlessClient(self.env)
        response = self._response()
        client._opener.open = MagicMock(return_value=response)

        stream = client.open_download(
            19,
            version_id="version-2",
            original=True,
            range_header="bytes=0-131071",
            if_range='"checksum"',
        )
        content = b"".join(stream.iter_chunks())

        binary_request = client._opener.open.call_args.args[0]
        request_headers = dict(binary_request.header_items())
        self.assertIn("/api/documents/19/download/", binary_request.full_url)
        self.assertIn("version=version-2", binary_request.full_url)
        self.assertIn("original=true", binary_request.full_url)
        self.assertEqual(request_headers["Range"], "bytes=0-131071")
        self.assertEqual(request_headers["If-range"], '"checksum"')
        self.assertEqual(request_headers["Authorization"], "Token server-only-token")
        self.assertFalse(
            {"Cookie", "Origin", "Referer", "Host"}.intersection(request_headers),
        )
        self.assertEqual(content, b"a" * (128 * 1024))
        self.assertTrue(all(call.args[0] == 64 * 1024 for call in response.read.call_args_list))
        response.close.assert_called()

    def test_open_download_rejects_malformed_ranges_before_network_io(self):
        client = PaperlessClient(self.env)
        client._opener.open = MagicMock()
        for value in ("items=0-1", "bytes=", "bytes=0-1\r\nX-Test: injected"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.open_download(
                    19,
                    version_id="version-2",
                    original=True,
                    range_header=value,
                )
        client._opener.open.assert_not_called()

    def test_open_download_refuses_redirects_without_exposing_the_target(self):
        client = PaperlessClient(self.env)
        redirect = urllib.error.HTTPError(
            "http://paperless:8000/api/documents/19/download/",
            302,
            "Found",
            {"Location": "https://untrusted.example/file"},
            BytesIO(),
        )
        client._opener.open = MagicMock(side_effect=redirect)

        with self.assertRaisesRegex(PaperlessCompatibilityError, "unsafe binary redirect"):
            client.open_download(
                19,
                version_id="version-2",
                original=True,
            )

    def test_stream_detects_truncated_upstream_content(self):
        client = PaperlessClient(self.env)
        response = self._response(content=b"short")
        response.headers["Content-Length"] = "100"
        client._opener.open = MagicMock(return_value=response)

        stream = client.open_download(
            19,
            version_id="version-2",
            original=True,
        )
        with self.assertRaisesRegex(PaperlessUnavailable, "before completion"):
            b"".join(stream.iter_chunks())

    def test_multi_megabyte_stream_uses_bounded_chunks(self):
        client = PaperlessClient(self.env)
        content = b"a" * (3 * 1024 * 1024)
        response = self._response(content=content)
        client._opener.open = MagicMock(return_value=response)

        stream = client.open_download(
            19,
            version_id="version-2",
            original=True,
        )
        self.assertEqual(sum(map(len, stream.iter_chunks())), len(content))
        self.assertTrue(response.read.call_args_list)
        self.assertTrue(
            all(call.args[0] == 64 * 1024 for call in response.read.call_args_list),
        )

    def test_binary_timeout_and_outage_fail_closed(self):
        client = PaperlessClient(self.env)
        for error in (TimeoutError("timed out"), urllib.error.URLError("offline")):
            with self.subTest(error=type(error).__name__):
                client._opener.open = MagicMock(side_effect=error)
                with self.assertRaisesRegex(PaperlessUnavailable, "unavailable"):
                    client.open_download(
                        19,
                        version_id="version-2",
                        original=True,
                    )


@tagged("post_install", "-at_install", "usl_documents", "download_grants_http")
class TestDocumentDownloadGrantHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create(
            {"name": "HTTP Download Restricted"},
        )
        cls.user = mail_new_test_user(
            cls.env,
            login="document-download-http-agent",
            name="Document Download HTTP Agent",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
            groups="usl_documents.group_documents_user",
        )
        params = cls.env["ir.config_parameter"].sudo()
        params.set_str("web.base.url", "https://odoo.example.test")
        params.set_str("web.base.url.freeze", "True")

    def _document(self, paperless_id):
        document = self.env["usl.document"].sudo().create(
            {
                "name": "HTTP materialization",
                "paperless_id": paperless_id,
                "company_id": self.company.id,
                "confidentiality": "internal",
                "review_state": "classified",
                "availability_state": "available",
                "permission_sync_state": "synchronized",
                "original_filename": "safe-invoice.pdf",
                "mime_type": "application/pdf",
            },
        )
        self.env["usl.document.version"].sudo().create(
            {
                "document_id": document.id,
                "paperless_version_id": "http-version-1",
                "label": "HTTP Version 1",
                "original_filename": "safe-invoice.pdf",
                "mime_type": "application/pdf",
                "checksum": "original-sha",
                "archive_checksum": "archive-sha",
                "is_current": True,
            },
        )
        return document

    def _issue(self, document):
        with patch.object(
            PaperlessClient,
            "probe_download",
            return_value={
                "status": 200,
                "headers": {
                    "Content-Length": "4096",
                    "Content-Type": "application/pdf",
                    "ETag": '"original-sha"',
                },
            },
        ):
            result = self.env["usl.document"].with_user(self.user).with_context(
                allowed_company_ids=self.company.ids,
            ).mcp_create_download_grant(document.id)
        token = urlsplit(result["url"]).path.rsplit("/", 1)[-1]
        grant = self.env["usl.document.download.grant"].sudo().search(
            [("public_id", "=", result["grant_id"])], limit=1,
        )
        return result, token, grant

    @staticmethod
    def _open_stream(*_args, range_header=None, method="GET", **_kwargs):
        return _HttpBinaryStream(method=method, range_header=range_header)

    def test_sessionless_get_head_range_replay_and_revocation(self):
        document = self._document(88101)
        result, token, grant = self._issue(document)
        headers = {"X-USL-Document-Grant": token}

        with patch.object(PaperlessClient, "open_download", side_effect=self._open_stream):
            response = self.url_open("/usl_documents/materialize", headers=headers)
            head = self.url_open(
                "/usl_documents/materialize", headers=headers, method="HEAD",
            )
            partial = self.url_open(
                "/usl_documents/materialize",
                headers={**headers, "Range": "bytes=0-9"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content[:4], b"%PDF")
        self.assertEqual(response.headers["Content-Length"], "4096")
        self.assertTrue(response.headers["Content-Disposition"].startswith("attachment;"))
        self.assertIn("safe-invoice.pdf", response.headers["Content-Disposition"])
        self.assertNotIn("\r", response.headers["Content-Disposition"])
        self.assertNotIn("\n", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["Cache-Control"], "private, no-store, max-age=0")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, b"%PDFaaaaaa")
        self.assertEqual(partial.headers["Content-Range"], "bytes 0-9/4096")
        self.assertEqual(grant.redemption_count, 3)

        self.env["usl.document"].with_user(self.user).mcp_revoke_download_grant(
            result["grant_id"], reason="HTTP test complete",
        )
        with patch.object(PaperlessClient, "open_download") as open_download:
            revoked = self.url_open("/usl_documents/materialize", headers=headers)
        self.assertEqual(revoked.status_code, 404)
        open_download.assert_not_called()

    def test_unexpired_url_fails_after_current_access_is_revoked(self):
        document = self._document(88102)
        _result, token, _grant = self._issue(document)
        document.sudo().with_context(usl_documents_cache_write=True).write(
            {"company_id": self.other_company.id},
        )

        with patch.object(PaperlessClient, "open_download") as open_download:
            denied = self.url_open(
                "/usl_documents/materialize",
                headers={"X-USL-Document-Grant": token},
            )
        self.assertEqual(denied.status_code, 404)
        open_download.assert_not_called()

    def test_revocation_race_closes_upstream_before_response(self):
        document = self._document(88104)
        _result, token, _grant = self._issue(document)
        stream = _HttpBinaryStream()
        stream.close = MagicMock()

        with (
            patch.object(
                UslDocumentDownloadGrant,
                "_is_live_now",
                side_effect=[True, False],
            ),
            patch.object(PaperlessClient, "open_download", return_value=stream),
        ):
            denied = self.url_open(
                "/usl_documents/materialize",
                headers={"X-USL-Document-Grant": token},
            )

        self.assertEqual(denied.status_code, 404)
        stream.close.assert_called_once()

    def test_untrusted_binary_metadata_cannot_inject_response_headers(self):
        document = self._document(88105)
        _result, token, grant = self._issue(document)
        grant.write(
            {
                "mime_type": "application/pdf\r\nX-Injected: yes",
                "filename": 'invoice.pdf\r\nX-Filename: yes',
            },
        )

        with patch.object(PaperlessClient, "open_download", side_effect=self._open_stream):
            response = self.url_open(
                "/usl_documents/materialize",
                headers={"X-USL-Document-Grant": token},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/octet-stream")
        self.assertNotIn("X-Injected", response.headers)
        self.assertNotIn("X-Filename", response.headers)
        self.assertNotIn("\r", response.headers["Content-Disposition"])
        self.assertNotIn("\n", response.headers["Content-Disposition"])

    def test_expiry_inactive_user_and_random_tokens_fail_before_paperless(self):
        document = self._document(88103)
        _result, token, grant = self._issue(document)
        grant.write({"expires_at": fields.Datetime.now() - timedelta(seconds=1)})
        with patch.object(PaperlessClient, "open_download") as open_download:
            expired = self.url_open(
                "/usl_documents/materialize",
                headers={"X-USL-Document-Grant": token},
            )
            expired_replay = self.url_open(
                "/usl_documents/materialize",
                headers={"X-USL-Document-Grant": token},
            )
            random_token = self.url_open(
                "/usl_documents/materialize",
                headers={"X-USL-Document-Grant": "A" * 43},
            )
        self.assertEqual(expired.status_code, 404)
        self.assertEqual(expired_replay.status_code, 404)
        self.assertEqual(random_token.status_code, 404)
        open_download.assert_not_called()
        self.assertEqual(grant.denial_count, 2)
        self.assertEqual(
            self.env["usl.document.download.grant.audit"].sudo().search_count(
                [("grant_id", "=", grant.id), ("event_type", "=", "denied_expired")],
            ),
            1,
        )

        _result, active_token, _grant = self._issue(document)
        self.user.sudo().write({"active": False})
        with patch.object(PaperlessClient, "open_download") as open_download:
            inactive = self.url_open(
                "/usl_documents/materialize",
                headers={"X-USL-Document-Grant": active_token},
            )
        self.assertEqual(inactive.status_code, 404)
        open_download.assert_not_called()

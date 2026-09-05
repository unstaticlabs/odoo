import html
import re

from odoo import fields, http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request
from odoo.http.stream import content_disposition

from ..models.paperless_client import PaperlessError, PaperlessNotFound

SAFE_BROWSER_BINARY_TYPES = {
    "application/pdf",
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}

MATERIALIZATION_HEADERS = {
    "accept-ranges": "Accept-Ranges",
    "content-length": "Content-Length",
    "content-range": "Content-Range",
    "etag": "ETag",
    "last-modified": "Last-Modified",
}

_MIME_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


class DocumentsController(http.Controller):
    @staticmethod
    def _materialization_error(status, body=b""):
        return request.make_response(
            body,
            status=status,
            headers=[
                ("Content-Type", "text/plain"),
                ("Cache-Control", "private, no-store, max-age=0"),
                ("Pragma", "no-cache"),
                ("Referrer-Policy", "no-referrer"),
                ("X-Content-Type-Options", "nosniff"),
                ("X-Robots-Tag", "noindex, nofollow, noarchive"),
                ("Content-Security-Policy", "sandbox; default-src 'none'"),
            ],
        )

    @staticmethod
    def _materialization_not_found(grant=None, event_type=None, denial_code=None):
        if grant and event_type and denial_code:
            grant._record_denial(event_type, denial_code)
        return DocumentsController._materialization_error(404)

    @staticmethod
    def _materialization_response(stream, grant):
        upstream = {key.lower(): value for key, value in stream.headers.items()}
        mime_type = str(grant.mime_type or "").strip().lower()
        if not _MIME_TYPE.fullmatch(mime_type):
            mime_type = "application/octet-stream"
        headers = [
            ("Content-Type", mime_type),
            ("Content-Disposition", content_disposition(grant.filename)),
            ("Cache-Control", "private, no-store, max-age=0"),
            ("Pragma", "no-cache"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Robots-Tag", "noindex, nofollow, noarchive"),
            ("Content-Security-Policy", "sandbox; default-src 'none'"),
        ]
        for source, target in MATERIALIZATION_HEADERS.items():
            if upstream.get(source):
                headers.append((target, upstream[source]))
        if grant.checksum and not upstream.get("etag"):
            headers.append(("ETag", f'"{grant.checksum}"'))
        if stream.status == 416:
            stream.close()
            return request.make_response(b"", status=416, headers=headers)
        if stream.status not in (200, 206):
            stream.close()
            return DocumentsController._materialization_error(
                503, b"Document service unavailable.",
            )
        if request.httprequest.method == "HEAD":
            stream.close()
            return request.make_response(b"", status=stream.status, headers=headers)
        response = request.make_response(
            stream.iter_chunks(), status=stream.status, headers=headers,
        )
        response.direct_passthrough = True
        response.call_on_close(stream.close)
        return response

    @http.route(
        "/usl_documents/materialize",
        type="http",
        auth="public",
        methods=["GET", "HEAD"],
        save_session=False,
    )
    def materialize(self):
        raw_token = request.httprequest.headers.get("X-USL-Document-Grant", "")
        grant = request.env["usl.document.download.grant"]._find_token(raw_token)
        if not grant:
            return self._materialization_not_found()
        now = fields.Datetime.now()
        if grant.revoked_at:
            return self._materialization_not_found(
                grant, "denied_revoked", "revoked",
            )
        if grant.expires_at <= now:
            return self._materialization_not_found(
                grant, "denied_expired", "expired",
            )
        if grant.database_name != request.env.cr.dbname:
            return self._materialization_not_found(
                grant, "denied_authorization", "database_changed",
            )
        issuer = request.env["res.users"].sudo().browse(
            grant.issued_by_odoo_id,
        ).exists()
        if not issuer or not issuer.active:
            return self._materialization_not_found(
                grant, "denied_inactive", "issuer_inactive",
            )
        try:
            bound_company_ids = sorted(
                {
                    int(company_id)
                    for company_id in grant.allowed_company_ids_json or []
                    if not isinstance(company_id, bool) and int(company_id) > 0
                },
            )
        except (TypeError, ValueError):
            return self._materialization_not_found(
                grant, "denied_company", "company_context_invalid",
            )
        current_company_ids = set(issuer.company_ids.ids)
        if (
            not bound_company_ids
            or grant.current_company_id not in bound_company_ids
            or not set(bound_company_ids).issubset(current_company_ids)
        ):
            return self._materialization_not_found(
                grant, "denied_company", "company_access_changed",
            )
        ordered_company_ids = [grant.current_company_id] + [
            company_id
            for company_id in bound_company_ids
            if company_id != grant.current_company_id
        ]
        context = dict(request.env.context)
        context.update(
            {
                "allowed_company_ids": ordered_company_ids,
                "usl_agent_origin": "document-download-grant",
                "usl_correlation_id": grant.public_id,
            },
        )
        request.update_env(user=issuer.id, context=context, su=False)
        try:
            descriptor = grant.with_env(request.env)._authorize_redemption()
            document = descriptor["document"]
        except (AccessError, ValidationError, ValueError):
            return self._materialization_not_found(
                grant, "denied_authorization", "authorization_changed",
            )
        if not grant.with_env(request.env)._is_live_now():
            return self._materialization_not_found()
        try:
            stream = document._paperless().open_download(
                descriptor["paperless_document_id"],
                version_id=descriptor["paperless_version_id"],
                original=grant.variant == "original",
                range_header=request.httprequest.headers.get("Range"),
                if_range=request.httprequest.headers.get("If-Range"),
                method=request.httprequest.method,
            )
        except PaperlessNotFound:
            return self._materialization_not_found(
                grant, "denied_file", "paperless_file_missing",
            )
        except ValueError:
            return self._materialization_error(416)
        except PaperlessError:
            return self._materialization_error(
                503, b"Document service unavailable.",
            )
        upstream_headers = {
            key.lower(): value for key, value in stream.headers.items()
        }
        upstream_etag = upstream_headers.get("etag", "").strip('"')
        if grant.checksum and upstream_etag and upstream_etag != grant.checksum:
            stream.close()
            return self._materialization_not_found(
                grant, "denied_file", "paperless_checksum_changed",
            )
        if (
            not request.httprequest.headers.get("Range")
            and grant.size_bytes
            and upstream_headers.get("content-length")
            and str(grant.size_bytes) != upstream_headers["content-length"]
        ):
            stream.close()
            return self._materialization_not_found(
                grant, "denied_file", "paperless_size_changed",
            )
        if not grant.with_env(request.env)._is_live_now():
            stream.close()
            return self._materialization_not_found()
        if stream.status in (200, 206):
            grant._record_redemption()
        return self._materialization_response(stream, grant)

    @staticmethod
    def _preview_content(client, document_id, version, mime_type):
        if (mime_type or "").split(";", 1)[0].strip().lower().startswith("image/"):
            return client.download(
                document_id,
                version_id=version,
                original=True,
            )
        return client.preview(document_id, version_id=version)

    @staticmethod
    def _browser_preview(content, content_type):
        mime_type = (content_type or "").split(";", 1)[0].strip().lower()
        text_types = {
            "application/json",
            "application/xhtml+xml",
            "application/xml",
        }
        if not (mime_type.startswith("text/") or mime_type in text_types):
            return (
                (content, mime_type)
                if mime_type in SAFE_BROWSER_BINARY_TYPES
                else (content, "application/octet-stream")
            )
        escaped = html.escape(content.decode("utf-8", errors="replace"))
        page = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="color-scheme" content="light dark">
  <style>
    body {{ margin: 0; padding: 1.25rem; font: 14px/1.5 sans-serif; }}
    pre {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }}
  </style>
</head>
<body><pre>{escaped}</pre></body>
</html>"""
        return page.encode(), "text/html; charset=utf-8"

    def _document(self, document_id):
        document = (
            request.env["usl.document"]
            .sudo()
            .browse(int(document_id))
            .exists()
            .with_env(request.env)
        )
        if not document:
            return None
        # Do not let a guessed identifier distinguish an inaccessible archive
        # record from one that does not exist.  Permission-synchronization
        # failures remain explicit for users who may legitimately read the
        # document, because those require administrator action.
        try:
            document.check_access("read")
        except AccessError:
            return None
        if not document._check_archive_binary_access():
            return None
        return document

    def _version(self, document, version):
        if not version:
            return None
        cached = document.version_ids.filtered(
            lambda item: item.paperless_version_id == str(version),
        )
        return str(version) if cached else False

    @http.route(
        "/usl_documents/<int:document_id>/preview",
        type="http",
        auth="user",
        methods=["GET"],
    )
    def preview(self, document_id, version=None):
        document = self._document(document_id)
        if not document:
            return request.not_found()
        version = self._version(document, version)
        if version is False:
            return request.not_found()
        preview_mime = document.mime_type
        if version:
            cached_version = document.version_ids.filtered(
                lambda item: item.paperless_version_id == version,
            )
            preview_mime = cached_version[:1].mime_type or preview_mime
        try:
            content, headers = self._preview_content(
                document._paperless(),
                document.paperless_id,
                version,
                preview_mime,
            )
        except PaperlessError as error:
            return request.make_response(
                str(error), status=503, headers=[("Content-Type", "text/plain")],
            )
        content_type = (
            headers.get("Content-Type")
            or preview_mime
            or "application/octet-stream"
        )
        content, content_type = self._browser_preview(content, content_type)
        return request.make_response(
            content,
            headers=[
                ("Content-Type", content_type),
                ("Cache-Control", "private, no-store"),
                (
                    "Content-Security-Policy",
                    "sandbox; default-src 'none'; style-src 'unsafe-inline'",
                ),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )

    @http.route(
        "/usl_documents/<int:document_id>/thumbnail",
        type="http",
        auth="user",
        methods=["GET"],
    )
    def thumbnail(self, document_id):
        document = self._document(document_id)
        if not document:
            return request.not_found()
        try:
            content, headers = document._paperless().thumbnail(document.paperless_id)
        except PaperlessError:
            return request.not_found()
        content_type = (
            headers.get("Content-Type", "image/webp")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if content_type not in SAFE_BROWSER_BINARY_TYPES - {"application/pdf"}:
            content_type = "application/octet-stream"
        return request.make_response(
            content,
            headers=[
                ("Content-Type", content_type),
                ("Cache-Control", "private, no-store"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )

    @http.route(
        "/usl_documents/<int:document_id>/download",
        type="http",
        auth="user",
        methods=["GET", "HEAD"],
    )
    def download(self, document_id, version=None, original="1"):
        document = self._document(document_id)
        if not document:
            return request.not_found()
        version = self._version(document, version)
        if version is False:
            return request.not_found()
        cached_version = (
            document.version_ids.filtered(
                lambda item: item.paperless_version_id == version,
            )[:1]
            if version
            else document.version_ids.filtered("is_current")[:1]
        )
        if not cached_version:
            return request.not_found()
        variant = (
            "archive"
            if original == "0" and cached_version.archive_checksum
            else "original"
        )
        try:
            descriptor = document._authorized_binary_descriptor(
                document_version_id=cached_version.id,
                variant=variant,
            )
            stream = document._paperless().open_download(
                descriptor["paperless_document_id"],
                version_id=descriptor["paperless_version_id"],
                original=variant == "original",
                range_header=request.httprequest.headers.get("Range"),
                if_range=request.httprequest.headers.get("If-Range"),
                method=request.httprequest.method,
            )
        except (PaperlessError, AccessError, ValidationError) as error:
            return request.make_response(
                str(error), status=503, headers=[("Content-Type", "text/plain")],
            )
        grant_like = type(
            "BrowserDownload",
            (),
            {
                "mime_type": descriptor["mime_type"],
                "filename": descriptor["filename"],
                "checksum": descriptor["checksum"],
            },
        )
        return self._materialization_response(stream, grant_like)

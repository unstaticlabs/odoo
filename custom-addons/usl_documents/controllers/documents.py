import html

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request
from odoo.http.stream import content_disposition

from ..models.paperless_client import PaperlessError

SAFE_BROWSER_BINARY_TYPES = {
    "application/pdf",
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}


class DocumentsController(http.Controller):
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
        try:
            content, headers = document._paperless().preview(
                document.paperless_id, version_id=version,
            )
        except PaperlessError as error:
            return request.make_response(
                str(error), status=503, headers=[("Content-Type", "text/plain")],
            )
        preview_mime = document.mime_type
        if version:
            cached_version = document.version_ids.filtered(
                lambda item: item.paperless_version_id == version,
            )
            preview_mime = cached_version[:1].mime_type or preview_mime
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
        methods=["GET"],
    )
    def download(self, document_id, version=None, original="1"):
        document = self._document(document_id)
        if not document:
            return request.not_found()
        version = self._version(document, version)
        if version is False:
            return request.not_found()
        try:
            content, headers = document._paperless().download(
                document.paperless_id,
                version_id=version,
                original=original != "0",
            )
        except PaperlessError as error:
            return request.make_response(
                str(error), status=503, headers=[("Content-Type", "text/plain")],
            )
        cached_version = (
            document.version_ids.filtered(
                lambda item: item.paperless_version_id == version,
            )[:1]
            if version
            else document.version_ids.filtered("is_current")[:1]
        )
        filename = (
            cached_version.original_filename
            or document.original_filename
            or f"document-{document.paperless_id}"
        )
        return request.make_response(
            content,
            headers=[
                (
                    "Content-Type",
                    headers.get("Content-Type", "application/octet-stream"),
                ),
                ("Content-Disposition", content_disposition(filename)),
                ("Cache-Control", "private, no-store"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )

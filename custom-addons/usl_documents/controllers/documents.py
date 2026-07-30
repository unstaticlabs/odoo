import html

from odoo import http
from odoo.http import request
from odoo.http.stream import content_disposition

from ..models.paperless_client import PaperlessError


class DocumentsController(http.Controller):
    @staticmethod
    def _browser_preview(content, content_type):
        if not content_type.lower().startswith("text/"):
            return content, content_type
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
        document = request.env["usl.document"].browse(int(document_id)).exists()
        document.check_access("read")
        if not document or document.availability_state != "available":
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
        return request.make_response(
            content,
            headers=[
                ("Content-Type", headers.get("Content-Type", "image/webp")),
                ("Cache-Control", "private, max-age=300"),
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

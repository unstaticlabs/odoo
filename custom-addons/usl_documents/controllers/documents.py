from odoo import http
from odoo.http import request

from ..models.paperless_client import PaperlessError


class DocumentsController(http.Controller):
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
            lambda item: item.paperless_version_id == str(version)
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
                document.paperless_id, version_id=version
            )
        except PaperlessError as error:
            return request.make_response(
                str(error), status=503, headers=[("Content-Type", "text/plain")]
            )
        return request.make_response(
            content,
            headers=[
                ("Content-Type", headers.get("Content-Type", "application/pdf")),
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
                str(error), status=503, headers=[("Content-Type", "text/plain")]
            )
        filename = document.original_filename or f"document-{document.paperless_id}"
        return request.make_response(
            content,
            headers=[
                (
                    "Content-Type",
                    headers.get("Content-Type", "application/octet-stream"),
                ),
                ("Content-Disposition", http.content_disposition(filename)),
                ("Cache-Control", "private, no-store"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )

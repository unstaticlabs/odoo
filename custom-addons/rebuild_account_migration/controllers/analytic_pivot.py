import json

from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import UnprocessableEntity

from odoo import _, http
from odoo.http import request
from odoo.http.stream import content_disposition


class AnalyticPivotPdfController(http.Controller):
    @http.route(
        "/usl/accounting/analytic-pivot/pdf",
        type="http",
        auth="user",
        methods=["POST"],
        readonly=True,
    )
    def export_pdf(self, data, **_kwargs):
        try:
            payload = json.load(data) if isinstance(data, FileStorage) else json.loads(data)
        except (TypeError, ValueError) as error:
            raise UnprocessableEntity(_("Invalid analytic pivot request.")) from error
        result = request.env["account.analytic.line"]._usl_analytic_pivot_document(payload)
        return request.make_response(
            result["pdf"],
            headers=[
                ("Content-Type", "application/pdf"),
                (
                    "Content-Disposition",
                    content_disposition(_("Analytic analysis.pdf")),
                ),
                ("Cache-Control", "private, no-store"),
                ("X-Content-Type-Options", "nosniff"),
                ("X-USL-Template-Revision", result["template_revision"]),
                ("X-USL-Payload-SHA256", result["payload_sha256"]),
            ],
        )

from odoo import http
from odoo.http import request
from odoo.http.stream import content_disposition


class FecDownloadController(http.Controller):
    @http.route(
        '/usl/accounting/fec/<model("l10n_fr.fec.export.wizard"):wizard>',
        type="http",
        auth="user",
        methods=["GET"],
    )
    def download(self, wizard):
        wizard.check_access("read")
        result = wizard.generate_fec()
        return request.make_response(
            result["file_content"],
            headers=[
                ("Content-Type", "text/plain; charset=utf-8"),
                (
                    "Content-Disposition",
                    content_disposition(result["file_name"]),
                ),
                ("Cache-Control", "private, no-store"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )

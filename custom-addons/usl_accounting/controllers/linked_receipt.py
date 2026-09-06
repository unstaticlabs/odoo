from odoo import _, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request


class LinkedReceiptHandoffController(http.Controller):
    _CSP = (
        "default-src 'none'; style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; img-src 'self' data:; form-action 'self'; "
        "base-uri 'none'; object-src 'none'; frame-ancestors 'none'"
    )
    _HEADERS = {
        "Cache-Control": "no-store, max-age=0",
        "Content-Security-Policy": _CSP,
        "Cross-Origin-Opener-Policy": "same-origin",
        "Permissions-Policy": (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }

    @staticmethod
    def _retrieval(retrieval_id):
        retrieval = request.env["usl.mail.pdf.retrieval"].browse(retrieval_id).exists()
        if not retrieval:
            return None, None
        try:
            retrieval.check_access("read")
            retrieval._check_can_open_handoff()
        except AccessError:
            return None, None
        except UserError as error:
            return None, str(error)
        return retrieval, None

    @classmethod
    def _secure_response(cls, response):
        response.headers.update(cls._HEADERS)
        return response

    @classmethod
    def _not_found(cls):
        # Render one fixed response for missing and unauthorized records so the
        # route cannot be used to enumerate another employee's retrievals.
        return cls._secure_response(
            request.render(
                "usl_accounting.linked_receipt_handoff_unavailable",
                {"message": _("The receipt website is unavailable.")},
                status=404,
            ),
        )

    @http.route(
        "/usl/expenses/linked-receipt/<int:retrieval_id>/open",
        type="http",
        auth="user",
        methods=["GET"],
        sitemap=False,
    )
    def handoff_page(self, retrieval_id, **_kwargs):
        retrieval, error = self._retrieval(retrieval_id)
        if not retrieval:
            if error:
                return self._secure_response(
                    request.render(
                        "usl_accounting.linked_receipt_handoff_unavailable",
                        {"message": error},
                        status=409,
                    ),
                )
            return self._not_found()
        return self._secure_response(
            request.render(
                "usl_accounting.linked_receipt_handoff",
                {
                    "retrieval": retrieval,
                    "instruction": _(
                        "Continue to %(hostname)s and sign in there. Odoo does not receive or store your credentials.",
                        hostname=retrieval.starting_host,
                    ),
                    "generation": retrieval.generation,
                    "csrf_token": request.csrf_token(),
                },
            ),
        )

    @http.route(
        "/usl/expenses/linked-receipt/<int:retrieval_id>/continue",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=True,
        sitemap=False,
    )
    def handoff_continue(self, retrieval_id, generation=None, **_kwargs):
        retrieval, error = self._retrieval(retrieval_id)
        if not retrieval:
            if error:
                return self._secure_response(
                    request.render(
                        "usl_accounting.linked_receipt_handoff_unavailable",
                        {"message": error},
                        status=409,
                    ),
                )
            return self._secure_response(request.not_found())
        try:
            expected_generation = int(generation)
        except (TypeError, ValueError):
            expected_generation = -1
        try:
            url = retrieval._consume_handoff(
                expected_generation=expected_generation,
            )
        except AccessError:
            return self._not_found()
        except UserError as error:
            return self._secure_response(
                request.render(
                    "usl_accounting.linked_receipt_handoff_unavailable",
                    {"message": str(error)},
                    status=409,
                ),
            )
        response = request.redirect(url, code=303, local=False)
        return self._secure_response(response)

from email.message import EmailMessage
from unittest.mock import patch

from lxml import html

from odoo import Command
from odoo.tests import HttpCase, tagged

from odoo.addons.hr_expense.tests.common import TestExpenseCommon


@tagged(
    "post_install",
    "-at_install",
    "usl_accounting_linked_receipt_http",
)
class TestLinkedReceiptHandoffHttp(TestExpenseCommon, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        message = EmailMessage()
        message["From"] = cls.expense_user_employee.email
        message["To"] = "expenses@example.invalid"
        message["Subject"] = f"{cls.product_c.default_code} Taxi receipt EUR 24.50"
        message["Message-ID"] = "<linked-receipt-handoff-http@example.invalid>"
        message.set_content("Download receipt")
        message.add_alternative(
            """
            <html><body>
              <a href="https://receipts.example.com/trips/http-fixture/download?token=http-secret-token">
                Download PDF receipt
              </a>
            </body></html>
            """,
            subtype="html",
        )
        with patch.dict(
            "os.environ",
            {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"},
        ):
            cls.expense = cls.env["mail.thread"].message_process(
                "hr.expense",
                message.as_bytes(),
            )
        cls.retrieval = cls.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", cls.expense.id)],
        )
        candidate = cls.retrieval._extract_candidates(
            cls.retrieval.source_message_id,
        )[0]
        cls.retrieval._select_candidate(candidate["fingerprint"], teach=True)
        cls.retrieval.write(
            {
                "state": "needs_attention",
                "failure_code": "authentication_required",
                "failure_message": "The receipt page requires authentication.",
            },
        )
        cls.expense_user_manager.group_ids = [
            Command.link(cls.env.ref("account.group_account_manager").id),
        ]

    def _authenticate(self, user):
        self.authenticate(user.login, user.login)

    def test_owner_gets_safe_interstitial_and_post_redirect(self):
        self._authenticate(self.expense_user_employee)
        path = f"/usl/expenses/linked-receipt/{self.retrieval.id}/open"
        with patch.dict(
            "os.environ",
            {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"},
        ):
            page = self.url_open(path, allow_redirects=False)

        self.assertEqual(page.status_code, 200)
        self.assertNotIn("http-secret-token", page.text)
        self.assertNotIn("https://receipts.example.com", page.text)
        self.assertIn("receipts.example.com", page.text)
        self.assertEqual(page.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(page.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(page.headers["X-Frame-Options"], "DENY")
        document = html.fromstring(page.content)
        csrf_token = document.xpath("//input[@name='csrf_token']/@value")[0]
        generation = document.xpath("//input[@name='generation']/@value")[0]

        with patch.dict(
            "os.environ",
            {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"},
        ):
            redirect = self.url_open(
                f"/usl/expenses/linked-receipt/{self.retrieval.id}/continue",
                data={"csrf_token": csrf_token, "generation": generation},
                allow_redirects=False,
            )

        self.assertEqual(redirect.status_code, 303)
        self.assertEqual(
            redirect.headers["Location"],
            "https://receipts.example.com/trips/http-fixture/download?token=http-secret-token",
        )
        self.assertEqual(redirect.headers["Referrer-Policy"], "no-referrer")
        self.retrieval.invalidate_recordset()
        self.assertEqual(self.retrieval.handoff_open_count, 1)
        self.assertEqual(
            self.retrieval.last_handoff_user_id,
            self.expense_user_employee,
        )

    def test_manager_and_unknown_id_are_indistinguishable(self):
        self._authenticate(self.expense_user_manager)
        with patch.dict(
            "os.environ",
            {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"},
        ):
            denied = self.url_open(
                f"/usl/expenses/linked-receipt/{self.retrieval.id}/open",
                allow_redirects=False,
            )
            missing = self.url_open(
                "/usl/expenses/linked-receipt/2147483647/open",
                allow_redirects=False,
            )
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(denied.content, missing.content)

    def test_post_rejects_missing_csrf(self):
        self._authenticate(self.expense_user_employee)
        with patch.dict(
            "os.environ",
            {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"},
        ):
            response = self.url_open(
                f"/usl/expenses/linked-receipt/{self.retrieval.id}/continue",
                data={"generation": self.retrieval.generation},
                allow_redirects=False,
            )
        self.assertIn(response.status_code, (400, 403))
        self.retrieval.invalidate_recordset()
        self.assertFalse(self.retrieval.handoff_open_count)

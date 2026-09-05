from email.message import EmailMessage
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.hr_expense.tests.common import TestExpenseCommon


@tagged(
    "post_install",
    "-at_install",
    "usl_accounting_linked_receipt_browser",
)
class TestLinkedReceiptBrowser(TestExpenseCommon, HttpCase):
    browser_size = "390x844"

    def test_employee_scans_historical_receipt(self):
        for size in ("1440x1000", "390x844"):
            self.browser_size = size
            message = EmailMessage()
            message["From"] = self.expense_user_employee.email
            message["To"] = "expenses@example.invalid"
            message["Subject"] = f"{self.product_c.default_code} Taxi EUR 24.50"
            message["Message-ID"] = f"<historical-browser-{size}@example.invalid>"
            message.set_content("Download receipt")
            message.add_alternative(
                '<a href="https://receipts.example.com/receipt.pdf">Download PDF receipt</a>',
                subtype="html",
            )
            with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "0"}):
                expense = self.env["mail.thread"].message_process("hr.expense", message.as_bytes())
            with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"}):
                self.start_tour(
                    f"/odoo/hr.expense/{expense.id}", "usl_linked_receipt_historical_scan",
                    login=self.expense_user_employee.login,
                )
            retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search([("expense_id", "=", expense.id)])
            self.assertEqual(len(retrieval), 1)
            self.assertEqual(retrieval.state, "selection_required")

    def test_employee_teaches_and_recovers_linked_receipt(self):
        message = EmailMessage()
        message["From"] = self.expense_user_employee.email
        message["To"] = "expenses@example.invalid"
        message["Subject"] = f"{self.product_c.default_code} Taxi receipt EUR 24.50"
        message["Message-ID"] = "<linked-receipt-browser@example.invalid>"
        message.set_content("Download receipt")
        message.add_alternative(
            """
            <html><body>
              <p>Your ride is complete.</p>
              <a href="https://receipts.example.com/trips/browser-fixture/download?token=browser-secret">
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
            expense = self.env["mail.thread"].message_process(
                "hr.expense",
                message.as_bytes(),
            )
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )

        self.start_tour(
            f"/odoo/hr.expense/{expense.id}",
            "usl_linked_receipt_teach_and_recover",
            login=self.expense_user_employee.login,
        )

        self.assertEqual(retrieval.state, "needs_attention")
        self.assertTrue(expense.exists())
        self.assertEqual(expense.state, "draft")
        self.assertEqual(retrieval.pattern_id.positive_count, 1)
        self.assertEqual(retrieval.starting_host, "receipts.example.com")
        self.assertEqual(
            self.env["usl.mail.pdf.host"].sudo().search(
                [("hostname", "=", retrieval.starting_host)],
            ).state,
            "provisional",
        )
        self.assertNotIn("browser-secret", str(retrieval.candidate_features))

    def test_employee_sees_authenticated_handoff_journey(self):
        message = EmailMessage()
        message["From"] = self.expense_user_employee.email
        message["To"] = "expenses@example.invalid"
        message["Subject"] = f"{self.product_c.default_code} Taxi receipt EUR 24.50"
        message["Message-ID"] = "<linked-receipt-auth-browser@example.invalid>"
        message.set_content("Download receipt")
        message.add_alternative(
            """
            <html><body>
              <a href="https://receipts.example.com/trips/auth-browser/download?token=browser-auth-secret">
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
            expense = self.env["mail.thread"].message_process(
                "hr.expense",
                message.as_bytes(),
            )
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", expense.id)],
        )
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        retrieval.write(
            {
                "state": "needs_attention",
                "failure_code": "authentication_required",
                "failure_message": "The receipt page requires authentication.",
            }
        )

        with patch.dict(
            "os.environ",
            {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"},
        ):
            self.start_tour(
                f"/odoo/hr.expense/{expense.id}",
                "usl_linked_receipt_authentication_recovery",
                login=self.expense_user_employee.login,
            )

        self.assertEqual(retrieval.state, "needs_attention")
        self.assertFalse(retrieval.handoff_open_count)
        self.assertNotIn("browser-auth-secret", str(retrieval.candidate_features))

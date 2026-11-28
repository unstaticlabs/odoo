"""Source-message access also governs the rendered receipt and native download."""
from odoo.tests import HttpCase, tagged
from odoo.addons.hr_expense.tests.common import TestExpenseCommon

from .test_expense_email_receipt import TestExpenseEmailReceipt, REFERENCES


@tagged("post_install", "-at_install", "usl_expense_email_receipt_http")
class TestExpenseEmailReceiptHttp(TestExpenseCommon, HttpCase):
    _email = TestExpenseEmailReceipt._email
    _ingest = TestExpenseEmailReceipt._ingest
    _message = TestExpenseEmailReceipt._message

    def test_native_download_and_report_follow_current_source_visibility(self):
        expense = self._ingest(REFERENCES[0], token="native-download")
        source = self._message(expense)
        attachment = expense.message_main_attachment_id
        original_bytes = attachment.raw.content
        reader = self.expense_user_manager
        self.authenticate(reader.login, reader.login)
        download = f"/web/content/{attachment.id}?download=true"
        report = expense.with_user(reader).with_context(allowed_company_ids=expense.company_id.ids).receipt_email_url
        self.assertTrue(report.startswith(f"/report/html/usl_accounting.expense_receipt_email/{expense.id}?"))
        before = expense.read(["state", "company_id", "account_move_id", "total_amount_currency", "message_main_attachment_id"])
        self.assertEqual(self.url_open(download).content, original_bytes)
        rendered = self.url_open(report)
        self.assertEqual(rendered.status_code, 200)
        self.assertIn(REFERENCES[0], rendered.text)
        self.env["ir.rule"].sudo().create({
            "name": "Synthetic source-message visibility withdrawal", "model_id": self.env["ir.model"]._get_id("mail.message"),
            "domain_force": repr([("id", "!=", source.id)]),
            "perm_read": True, "perm_create": False, "perm_write": False, "perm_unlink": False,
        })
        self.env.invalidate_all()
        self.assertTrue(expense.with_user(reader).has_access("read"))
        self.assertFalse(source.with_user(reader).has_access("read"))
        self.assertFalse(expense.with_user(reader).receipt_email_url)
        store_response = self.url_open("/mail/data", json={
            "jsonrpc": "2.0", "method": "call", "id": 1,
            "params": {"fetch_params": [["mail.thread", {
                "thread_model": "hr.expense", "thread_id": expense.id, "request_list": ["attachments"],
            }]]},
        })
        self.assertEqual(store_response.status_code, 200)
        store = store_response.json()
        self.assertNotIn("error", store)
        self.assertNotIn(REFERENCES[0], store_response.text)
        source_rows = [item for item in store.get("result", {}).get("ir.attachment", []) if item.get("id") == attachment.id]
        self.assertFalse(any(item.get("raw_access_token") or item.get("thumbnail_access_token") for item in source_rows))
        refused = self.url_open(download)
        self.assertIn(refused.status_code, (403, 404))
        self.assertNotEqual(refused.content, original_bytes)
        refused_report = self.url_open(report)
        self.assertNotEqual(refused_report.status_code, 200)
        self.assertNotIn(REFERENCES[0], refused_report.text)
        self.assertEqual(expense.read(list(before[0].keys() - {"id"})), before)

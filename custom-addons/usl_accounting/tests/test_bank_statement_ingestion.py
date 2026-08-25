import datetime as dt
import hashlib
import zipfile
from email.message import EmailMessage
from io import BytesIO

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import file_open


@tagged(
    "post_install",
    "-at_install",
    "usl_accounting_bank_ingestion",
    "usl_accounting_unit",
)
class TestBankStatementIngestion(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.env.user.group_ids = [
            Command.link(cls.env.ref("account.group_account_manager").id),
        ]
        partner_bank = cls.env["res.partner.bank"].create(
            {
                "account_number": "FR7630001007941234567890185",
                "partner_id": cls.company.partner_id.id,
                "company_id": cls.company.id,
            }
        )
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Synthetic Shine",
                "code": "SHIQ",
                "type": "bank",
                "company_id": cls.company.id,
                "bank_account_id": partner_bank.id,
                "currency_id": cls.env.ref("base.EUR").id,
            }
        )
        cls.config = cls.env["account.bank.ingestion.config"].create(
            {
                "name": "Synthetic monthly Shine export",
                "company_id": cls.company.id,
                "journal_id": cls.journal.id,
                "provider": "shine",
                "source_account_identifier": "FR7630001007941234567890185",
                "allowed_senders": "hello@shine.example.invalid",
                "allowed_download_hosts": "files.shine.example.invalid",
                "responsible_user_id": cls.env.user.id,
                "automatic_start_date": dt.date(2026, 7, 1),
                "alias_name": "shine-synthetic-bank-export",
            }
        )
        with file_open(
            "usl_accounting/tests/fixtures/shine_month.ofx", "rb"
        ) as fixture:
            cls.ofx = fixture.read()
        with file_open(
            "usl_accounting/tests/fixtures/shine_statement.pdf", "rb"
        ) as fixture:
            cls.pdf = fixture.read()

    def _ingestion(self, message_id, *, ofx=None, pdf=None, sender=None):
        ingestion = self.env["account.bank.ingestion"].message_new(
            {
                "subject": "Export comptable Synthetic - du 01/07/2026 au 31/07/2026",
                "message_id": message_id,
                "email_from": sender or "Shine <hello@shine.example.invalid>",
                "to": self.config.alias_full_name,
                "body": "<p>Synthetic scheduled export.</p>",
            },
            {"config_id": self.config.id},
        )
        for filename, content, mimetype in (
            ("transactions.ofx", ofx, "application/x-ofx"),
            ("statement.pdf", pdf, "application/pdf"),
        ):
            if content is None:
                continue
            self.env["ir.attachment"].sudo().create(
                {
                    "name": filename,
                    "raw": content,
                    "mimetype": mimetype,
                    "res_model": ingestion._name,
                    "res_id": ingestion.id,
                    "company_id": self.company.id,
                }
            )
        return ingestion

    def _process_complete_month(self):
        ingestion = self._ingestion("<synthetic-complete@example.invalid>", ofx=self.ofx, pdf=self.pdf)
        ingestion.action_process_now()
        detail = " | ".join(ingestion.file_ids.exception_ids.mapped("detail"))
        self.assertEqual(ingestion.state, "done", ingestion.last_error or detail)
        statement = ingestion.statement_ids
        self.assertEqual(len(statement), 1)
        return ingestion, statement

    def test_ofx_creates_standard_monthly_statement_and_distinct_fitids(self):
        ingestion, statement = self._process_complete_month()

        self.assertEqual(statement.period_start, dt.date(2026, 7, 1))
        self.assertEqual(statement.period_end, dt.date(2026, 7, 31))
        self.assertEqual(len(statement.line_ids), 3)
        self.assertEqual(statement.movement_total, 200)
        self.assertEqual(statement.balance_start, 1000)
        self.assertEqual(statement.balance_end_real, 1200)
        self.assertEqual(statement.balance_difference, 0)
        twins = statement.line_ids.filtered(
            lambda line: line.payment_ref == "Identical-looking transfer"
        )
        self.assertEqual(len(twins), 2)
        self.assertEqual(
            set(twins.mapped("provider_transaction_id")),
            {"shine-synthetic-002", "shine-synthetic-003"},
        )
        self.assertEqual(statement.accepted_evidence_id.evidence_status, "accepted")
        self.assertEqual(
            statement.accepted_evidence_id.sha256,
            hashlib.sha256(self.pdf).hexdigest(),
        )
        self.assertTrue(ingestion.file_ids.filtered(lambda item: item.classification == "ofx"))

    def test_duplicate_message_and_forwarded_file_are_idempotent(self):
        first, statement = self._process_complete_month()
        duplicate_delivery = self._ingestion(
            "<synthetic-complete@example.invalid>", ofx=self.ofx, pdf=self.pdf
        )
        self.assertEqual(duplicate_delivery, first)
        self.assertEqual(first.duplicate_delivery_count, 1)

        forwarded = self._ingestion(
            "<synthetic-forward@example.invalid>", ofx=self.ofx
        )
        forwarded.action_process_now()
        self.assertEqual(forwarded.state, "done")
        self.assertEqual(statement.line_ids.search_count([
            ("journal_id", "=", self.journal.id),
            ("provider_code", "=", "shine"),
        ]), 3)
        forwarded_ofx = forwarded.file_ids.filtered(
            lambda item: item.classification == "ofx"
        )
        self.assertEqual(forwarded_ofx.processing_state, "duplicate")
        self.assertTrue(
            all(forwarded_ofx in line.ingestion_file_ids for line in statement.line_ids)
        )

    def test_mail_gateway_retains_exact_rfc822_and_short_circuits_redelivery(self):
        message = EmailMessage()
        message["From"] = "Shine <hello@shine.example.invalid>"
        message["To"] = self.config.alias_full_name
        message["Subject"] = "Export comptable Synthetic - du 01/07/2026 au 31/07/2026"
        message["Message-ID"] = "<synthetic-rfc822@example.invalid>"
        message.set_content("Synthetic scheduled export.")
        message.add_attachment(
            self.ofx,
            maintype="application",
            subtype="x-ofx",
            filename="transactions.ofx",
        )
        message.add_attachment(
            self.pdf,
            maintype="application",
            subtype="pdf",
            filename="statement.pdf",
        )
        raw = message.as_bytes()

        ingestion_id = self.env["mail.thread"].message_process(
            "account.bank.ingestion",
            raw,
            custom_values={"config_id": self.config.id},
        )
        ingestion = self.env["account.bank.ingestion"].browse(ingestion_id)
        original = self.env["ir.attachment"].sudo().search(
            [
                ("res_model", "=", ingestion._name),
                ("res_id", "=", ingestion.id),
                ("mimetype", "=", "message/rfc822"),
            ],
            limit=1,
        )
        self.assertTrue(original)
        self.assertEqual(bytes(original.raw), raw)
        attachment_count = self.env["ir.attachment"].sudo().search_count(
            [("res_model", "=", ingestion._name), ("res_id", "=", ingestion.id)]
        )

        duplicate_id = self.env["mail.thread"].message_process(
            "account.bank.ingestion",
            raw,
            custom_values={"config_id": self.config.id},
        )
        self.assertEqual(duplicate_id, ingestion.id)
        self.assertEqual(ingestion.duplicate_delivery_count, 1)
        self.assertEqual(
            self.env["ir.attachment"].sudo().search_count(
                [("res_model", "=", ingestion._name), ("res_id", "=", ingestion.id)]
            ),
            attachment_count,
        )

    def test_shine_archive_is_retained_and_safe_members_are_processed(self):
        archive_buffer = BytesIO()
        with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("transactions.ofx", self.ofx)
            archive.writestr("statement.pdf", self.pdf)
            archive.writestr("transactions.csv", "Date,Amount\n2026-07-05,300\n")
            archive.writestr("transactions.qif", "!Type:Bank\nD07/05/2026\nT300\n^")
        ingestion = self._ingestion("<synthetic-zip@example.invalid>")
        self.env["ir.attachment"].sudo().create(
            {
                "name": "scheduled-export.zip",
                "raw": archive_buffer.getvalue(),
                "mimetype": "application/zip",
                "res_model": ingestion._name,
                "res_id": ingestion.id,
                "company_id": self.company.id,
            }
        )

        ingestion.action_process_now()

        self.assertEqual(ingestion.state, "done")
        archive_file = ingestion.file_ids.filtered(
            lambda item: item.classification == "zip"
        )
        self.assertEqual(archive_file.processing_state, "processed")
        self.assertEqual(
            set(archive_file.extracted_file_ids.mapped("classification")),
            {"ofx", "pdf", "csv", "qif"},
        )
        self.assertEqual(len(ingestion.statement_ids.line_ids), 3)

    def test_missing_pdf_blocks_certification_without_blocking_import(self):
        ingestion = self._ingestion(
            "<synthetic-no-pdf@example.invalid>", ofx=self.ofx
        )
        ingestion.action_process_now()
        statement = ingestion.statement_ids
        self.assertFalse(statement.accepted_evidence_id)
        self.assertIn("PDF", statement.review_blocking_reason)
        with self.assertRaises(UserError):
            statement.action_certify()

    def test_balance_confirmation_certification_and_controlled_reopening(self):
        _ingestion, statement = self._process_complete_month()
        self.env["account.bank.statement.confirm"].create(
            {
                "statement_id": statement.id,
                "balance_start": 1000,
                "balance_end_real": 1200,
            }
        ).action_confirm()
        statement.action_confirm_cutover_baseline()
        lock_date = self.company._get_user_fiscal_lock_date(self.journal)

        statement.action_certify()
        statement.action_certify()
        self.assertEqual(statement.certification_state, "certified")
        self.assertEqual(len(statement.certification_ids), 1)
        self.assertEqual(self.company._get_user_fiscal_lock_date(self.journal), lock_date)
        with self.assertRaises(UserError):
            statement.write({"balance_end_real": 1201})
        with self.assertRaises(UserError):
            statement.line_ids[0].write({"amount": 301})

        self.env["account.bank.statement.reopen"].create(
            {"statement_id": statement.id, "reason": "Correct the official bank evidence."}
        ).action_reopen()
        self.assertEqual(statement.certification_state, "reopened")
        self.assertEqual(len(statement.certification_ids), 2)
        self.assertEqual(statement.certification_ids[0].event_type, "reopen")

    def test_balance_mismatch_is_saved_and_blocks_certification(self):
        _ingestion, statement = self._process_complete_month()
        self.env["account.bank.statement.confirm"].create(
            {
                "statement_id": statement.id,
                "balance_start": 1000,
                "balance_end_real": 1199,
            }
        ).action_confirm()
        statement.action_confirm_cutover_baseline()
        self.assertEqual(statement.balance_difference, -1)
        self.assertTrue(statement.balances_confirmed)
        self.assertFalse(statement.can_certify)

    def test_malformed_ofx_preserves_source_and_is_retryable(self):
        ingestion = self._ingestion(
            "<synthetic-malformed@example.invalid>",
            ofx=b"OFXHEADER:100\n<OFX><BROKEN>",
            pdf=self.pdf,
        )
        ingestion.action_process_now()

        self.assertIn(ingestion.state, ("attention", "failed"))
        source = ingestion.file_ids.filtered(lambda item: item.filename == "transactions.ofx")
        self.assertEqual(source.processing_state, "failed")
        self.assertEqual(source._content(), b"OFXHEADER:100\n<OFX><BROKEN>")
        self.assertTrue(ingestion.unresolved_exception_count)

    def test_operational_state_cannot_be_forged_by_an_accountant(self):
        ingestion, statement = self._process_complete_month()
        accountant = self.env["res.users"].with_context(no_reset_password=True).create(
            {
                "name": "Synthetic accountant",
                "login": "synthetic-accountant@example.invalid",
                "email": "synthetic-accountant@example.invalid",
                "company_id": self.company.id,
                "company_ids": [Command.set(self.company.ids)],
                "group_ids": [Command.set(self.env.ref("account.group_account_user").ids)],
            }
        )
        with self.assertRaises(AccessError):
            statement.with_user(accountant).write({"balances_confirmed": True})
        with self.assertRaises(AccessError):
            ingestion.file_ids[0].with_user(accountant).write(
                {"processing_state": "processed"}
            )
        with self.assertRaises(AccessError):
            self.env["account.bank.statement.certification"].with_user(accountant).create(
                {
                    "statement_id": statement.id,
                    "company_id": self.company.id,
                    "event_type": "certify",
                    "user_id": accountant.id,
                    "event_at": dt.datetime(2026, 7, 31, 12, 0),
                    "period_start": statement.period_start,
                    "period_end": statement.period_end,
                    "balance_start": 1000,
                    "movement_total": 200,
                    "balance_end_real": 1200,
                    "transaction_count": 3,
                    "transaction_identity_digest": "forged",
                    "evidence_attachment_id": statement.accepted_evidence_id.attachment_id.id,
                    "evidence_sha256": statement.accepted_evidence_id.sha256,
                }
            )

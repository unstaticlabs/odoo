import datetime as dt
import hashlib
import re
import zipfile
from email.message import EmailMessage
from io import BytesIO
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import BinaryBytes, file_open, mute_logger


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
            },
        )
        currency = cls.env.ref("base.EUR")
        currency.active = True
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Synthetic Shine",
                "code": "SHIQ",
                "type": "bank",
                "company_id": cls.company.id,
                "bank_account_id": partner_bank.id,
                "currency_id": currency.id,
            },
        )
        alias_domain = cls.env["mail.alias.domain"].search([], limit=1)
        if not alias_domain:
            alias_domain = cls.env["mail.alias.domain"].create(
                {"name": "bank-ingestion.example.invalid"},
            )
        cls.company.alias_domain_id = alias_domain
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
            },
        )
        with file_open(
            "usl_accounting/tests/fixtures/shine_month.ofx",
            "rb",
        ) as fixture:
            cls.ofx = fixture.read()
        with file_open(
            "usl_accounting/tests/fixtures/shine_statement.pdf",
            "rb",
        ) as fixture:
            cls.pdf = fixture.read()

    def _ingestion(self, message_id, *, ofx=None, pdf=None, sender=None, subject=None):
        ingestion = self.env["account.bank.ingestion"].message_new(
            {
                "subject": subject
                or "Export comptable Synthetic - du 01/07/2026 au 31/07/2026",
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
                },
            )
        return ingestion

    def _process_complete_month(self):
        ingestion = self._ingestion(
            "<synthetic-complete@example.invalid>",
            ofx=self.ofx,
            pdf=self.pdf,
        )
        ingestion.action_process_now()
        detail = " | ".join(ingestion.file_ids.exception_ids.mapped("detail"))
        self.assertEqual(ingestion.state, "done", ingestion.last_error or detail)
        statement = ingestion.statement_ids
        self.assertEqual(len(statement), 1)
        self._complete_documents_archive(statement)
        return ingestion, statement

    def _complete_documents_archive(self, statement):
        """Model the external Documents worker when that integration is installed."""
        evidence = statement.accepted_evidence_id
        if not evidence or "paperless_archive_state" not in evidence._fields:
            return

        banking_view = self.env.ref("usl_documents.smart_view_banking")
        banking_tags = banking_view.tag_ids
        if not banking_tags:
            banking_tags = (
                self.env["usl.paperless.tag"]
                .sudo()
                .with_context(usl_documents_cache_write=True)
                .create({
                    "name": "Banking",
                    "paperless_id": 970_000_000 + evidence.id,
                    "matching_algorithm": "6",
                    "active": True,
                })
            )
            banking_view.sudo().with_context(
                usl_documents_archive_view_sync=True,
            ).write({"tag_ids": [Command.set(banking_tags.ids)]})

        Link = self.env["usl.document.link"].sudo()
        link = Link.search([
            ("res_model", "=", statement._name),
            ("res_id", "=", statement.id),
            ("active", "=", True),
        ], limit=1)
        document = link.document_id
        if not document:
            document = self.env["usl.document"].sudo().create({
                "name": f"Archived bank statement {statement.display_name}",
                "paperless_id": 980_000_000 + evidence.id,
                "company_id": evidence.company_id.id,
                "confidentiality": "accounting",
                "accounting_evidence": True,
                "retention_hold": True,
                "review_state": "reviewed",
                "availability_state": "available",
                "permission_sync_state": "synchronized",
                "checksum": evidence.sha256,
                "tag_ids": [Command.set(banking_tags.ids)],
            })
        else:
            document.version_ids.sudo().write({"is_current": False})
            document.sudo().with_context(
                usl_documents_cache_write=True,
            ).write({
                "checksum": evidence.sha256,
                "permission_sync_state": "synchronized",
                "tag_ids": [Command.set(banking_tags.ids)],
            })
            document.sudo().write({"review_state": "reviewed"})

        version_id = f"bank-evidence-{evidence.id}"
        self.env["usl.document.version"].sudo().create({
            "document_id": document.id,
            "paperless_version_id": version_id,
            "label": "Received original",
            "checksum": evidence.sha256,
            "is_current": True,
            "is_received_original": True,
            "source": "odoo_attachment",
        })
        if link:
            link.write({"version_id": version_id})
        else:
            Link.create({
                "document_id": document.id,
                "res_model": statement._name,
                "res_id": statement.id,
                "record_name": statement.display_name,
                "company_id": evidence.company_id.id,
                "linked_by_id": self.env.user.id,
                "version_id": version_id,
            })
        evidence.sudo().write({
            "paperless_archive_state": "archived",
            "paperless_document_id": document.id,
            "paperless_version": version_id,
            "paperless_archive_error": False,
            "paperless_archived_at": fields.Datetime.now(),
        })
        statement.invalidate_recordset()

    def _confirm_and_certify(self, statement, balance_start, balance_end):
        self.env["account.bank.statement.confirm"].create(
            {
                "statement_id": statement.id,
                "balance_start": balance_start,
                "balance_end_real": balance_end,
            },
        ).action_confirm()
        if statement.continuity_status == "baseline":
            statement.action_confirm_cutover_baseline()
        statement.action_certify()

    def test_ofx_creates_standard_monthly_statement_and_distinct_fitids(self):
        ingestion, statement = self._process_complete_month()

        self.assertEqual(statement.period_start, dt.date(2026, 7, 1))
        self.assertEqual(statement.period_end, dt.date(2026, 7, 31))
        self.assertEqual(len(statement.line_ids), 3)
        self.assertEqual(statement.movement_total, 200)
        self.assertEqual(statement.balance_start, 1000)
        self.assertEqual(statement.balance_end_real, 1200)
        self.assertEqual(statement.balance_difference, 0)
        self.assertEqual(statement.evidence_check_status, "ready")
        self.assertEqual(statement.transaction_check_status, "ready")
        self.assertEqual(statement.balance_check_status, "unconfirmed")
        twins = statement.line_ids.filtered(
            lambda line: line.payment_ref == "Identical-looking transfer",
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
        self.assertTrue(
            ingestion.file_ids.filtered(lambda item: item.classification == "ofx"),
        )
        action = self.config.action_open_expected_statement()
        self.assertEqual(action["res_id"], statement.id)
        self.assertEqual(
            action["view_id"],
            self.env.ref("usl_accounting.view_bank_statement_form_review").id,
        )

    def test_french_ofx_components_match_the_configured_iban(self):
        split_account_ofx = self.ofx.replace(
            b"<BANKID>00001</BANKID>\n          <ACCTID>FR7630001007941234567890185</ACCTID>",
            b"<BANKID>30001</BANKID>\n          <BRANCHID>00794</BRANCHID>\n          <ACCTID>12345678901</ACCTID>",
        )
        ingestion = self._ingestion(
            "<synthetic-french-components@example.invalid>",
            ofx=split_account_ofx,
            pdf=self.pdf,
        )
        ingestion._retain_message_attachments()
        ingestion.file_ids.filtered(
            lambda item: item.classification == "ofx",
        )._ensure_exception(
            "account",
            "Prior account mismatch",
            "Created by the former complete-IBAN-only matcher.",
        )

        ingestion.action_process_now()

        detail = " | ".join(ingestion.exception_ids.mapped("detail"))
        self.assertEqual(ingestion.state, "done", ingestion.last_error or detail)
        self.assertEqual(len(ingestion.statement_ids.line_ids), 3)
        self.assertEqual(
            set(ingestion.statement_ids.line_ids.mapped("provider_account_id")),
            {"FR7630001007941234567890185"},
        )
        self.assertFalse(
            ingestion.exception_ids.filtered(
                lambda item: item.kind == "account" and item.state == "open",
            ),
        )

    def test_french_ofx_components_must_all_match_the_configured_iban(self):
        mismatched_ofx = self.ofx.replace(
            b"<BANKID>00001</BANKID>\n          <ACCTID>FR7630001007941234567890185</ACCTID>",
            b"<BANKID>30001</BANKID>\n          <BRANCHID>00795</BRANCHID>\n          <ACCTID>12345678901</ACCTID>",
        )
        ingestion = self._ingestion(
            "<synthetic-french-mismatch@example.invalid>",
            ofx=mismatched_ofx,
        )

        ingestion.action_process_now()

        self.assertEqual(ingestion.state, "attention")
        self.assertFalse(ingestion.statement_ids)
        self.assertTrue(
            ingestion.exception_ids.filtered(
                lambda item: item.kind == "account" and item.state == "open",
            ),
        )

    def test_unmanaged_manual_statement_remains_outside_scheduled_review(self):
        statement = self.env["account.bank.statement"].create(
            {
                "name": "Manual bank import",
                "journal_id": self.journal.id,
                "date": dt.date(2026, 6, 30),
                "balance_start": 100,
                "balance_end_real": 175,
            },
        )
        self.env["account.bank.statement.line"].create(
            {
                "name": "Manual movement",
                "payment_ref": "Manual movement",
                "journal_id": self.journal.id,
                "statement_id": statement.id,
                "date": dt.date(2026, 6, 15),
                "amount": 75,
            },
        )

        self.assertEqual(statement.movement_total, 75)
        self.assertFalse(statement.review_status)
        self.assertFalse(statement.review_blocking_reason)
        self.assertEqual(statement.balance_difference, 0)
        self.assertFalse(statement.can_certify)

    def test_duplicate_message_and_forwarded_file_are_idempotent(self):
        first, statement = self._process_complete_month()
        duplicate_delivery = self._ingestion(
            "<synthetic-complete@example.invalid>",
            ofx=self.ofx,
            pdf=self.pdf,
        )
        self.assertEqual(duplicate_delivery, first)
        self.assertEqual(first.duplicate_delivery_count, 1)

        forwarded = self._ingestion("<synthetic-forward@example.invalid>", ofx=self.ofx)
        forwarded.action_process_now()
        self.assertEqual(forwarded.state, "done")
        self.assertEqual(
            statement.line_ids.search_count(
                [
                    ("journal_id", "=", self.journal.id),
                    ("provider_code", "=", "shine"),
                ],
            ),
            3,
        )
        forwarded_ofx = forwarded.file_ids.filtered(
            lambda item: item.classification == "ofx",
        )
        self.assertEqual(forwarded_ofx.processing_state, "duplicate")
        self.assertTrue(
            all(
                forwarded_ofx in line.ingestion_file_ids for line in statement.line_ids
            ),
        )

    def test_retry_preserves_the_accepted_official_pdf(self):
        ingestion = self._ingestion(
            "<synthetic-retry-evidence@example.invalid>",
            ofx=self.ofx,
            pdf=self.pdf,
        )
        ingestion.action_process_now()
        statement = ingestion.statement_ids
        evidence = statement.accepted_evidence_id

        self.assertTrue(evidence)
        self.assertEqual(evidence.evidence_status, "accepted")

        ingestion.action_retry()

        self.assertEqual(statement.accepted_evidence_id, evidence)
        self.assertEqual(evidence.evidence_status, "accepted")
        self.assertEqual(evidence.processing_state, "processed")

    def test_partially_overlapping_export_adds_only_the_missing_identity(self):
        _first, statement = self._process_complete_month()
        additional_transaction = b"""
            <STMTTRN>
                <TRNTYPE>CREDIT</TRNTYPE>
                <DTPOSTED>20260720120000.000[+1:CET]</DTPOSTED>
                <TRNAMT>25.00</TRNAMT>
                <FITID>shine-synthetic-004</FITID>
                <NAME>Additional transfer</NAME>
            </STMTTRN>
        """
        overlapping = self.ofx.replace(
            b"</BANKTRANLIST>",
            additional_transaction + b"</BANKTRANLIST>",
        ).replace(b"<BALAMT>1200.00", b"<BALAMT>1225.00")
        ingestion = self._ingestion(
            "<synthetic-overlap@example.invalid>",
            ofx=overlapping,
        )

        ingestion.action_process_now()

        self.assertEqual(ingestion.state, "done")
        self.assertEqual(len(statement.line_ids), 4)
        self.assertEqual(statement.movement_total, 225)
        self.assertEqual(statement.balance_end_real, 1225)
        self.assertEqual(
            len(
                statement.line_ids.filtered(
                    lambda line: line.provider_transaction_id == "shine-synthetic-004",
                ),
            ),
            1,
        )

    def test_migrated_split_fee_identity_is_recovered_without_duplicates(self):
        migrated_statement = self.env["account.bank.statement"].create(
            {
                "name": "Migrated July history",
                "journal_id": self.journal.id,
                "date": dt.date(2026, 7, 31),
                "balance_start": 1000,
                "balance_end_real": 1249,
            },
        )
        existing = self.env["account.bank.statement.line"].sudo().create(
            [
                {
                    "name": "Synthetic client receipt",
                    "payment_ref": "Synthetic client receipt",
                    "journal_id": self.journal.id,
                    "statement_id": migrated_statement.id,
                    "date": dt.date(2026, 7, 5),
                    "amount": 300,
                    "transaction_details": {"extra": {"id": "shine-synthetic-001"}},
                },
                {
                    "name": "Card payment",
                    "payment_ref": "Card payment",
                    "journal_id": self.journal.id,
                    "statement_id": migrated_statement.id,
                    "date": dt.date(2026, 7, 10),
                    "amount": -50,
                    "transaction_details": {"extra": {"id": "shine-synthetic-002"}},
                },
                {
                    "name": "Card fee",
                    "payment_ref": "Card fee",
                    "journal_id": self.journal.id,
                    "statement_id": migrated_statement.id,
                    "date": dt.date(2026, 7, 10),
                    "amount": -1,
                    "transaction_details": {"extra": {"id": "shine-synthetic-002"}},
                },
            ],
        )
        split_fee_ofx = self.ofx.replace(
            b"<TRNAMT>-50.00</TRNAMT>\n            <FITID>shine-synthetic-003</FITID>",
            b"<TRNAMT>-1.00</TRNAMT>\n            <FITID>shine-fee-003</FITID>",
        ).replace(b"<BALAMT>1200.00", b"<BALAMT>1249.00")
        self.assertIn(b"<FITID>shine-fee-003</FITID>", split_fee_ofx)
        ingestion = self._ingestion(
            "<synthetic-split-fee@example.invalid>",
            ofx=split_fee_ofx,
            pdf=self.pdf,
        )

        ingestion.action_process_now()

        detail = " | ".join(ingestion.exception_ids.mapped("detail"))
        self.assertEqual(ingestion.state, "done", ingestion.last_error or detail)
        self.assertEqual(ingestion.statement_ids, migrated_statement)
        self.assertEqual(ingestion.statement_ids.line_ids, existing)
        self.assertEqual(
            set(existing.mapped("provider_transaction_id")),
            {"shine-synthetic-001", "shine-synthetic-002", "shine-fee-003"},
        )

    def test_provider_identity_constraint_is_the_concurrency_backstop(self):
        _ingestion, statement = self._process_complete_month()
        original = statement.line_ids.filtered(
            lambda line: line.provider_transaction_id == "shine-synthetic-001",
        )
        duplicate_values = {
            "name": "Concurrent duplicate",
            "payment_ref": "Concurrent duplicate",
            "journal_id": self.journal.id,
            "statement_id": statement.id,
            "date": original.date,
            "amount": original.amount,
            "provider_code": original.provider_code,
            "provider_account_id": original.provider_account_id,
            "provider_transaction_id": original.provider_transaction_id,
            "provider_identity_kind": "stable",
        }

        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint(), mute_logger("odoo.sql_db"):
                self.env["account.bank.statement.line"].sudo().create(duplicate_values)

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

        ingestion = self.env["mail.thread"].message_process(
            None,
            raw,
        )
        original = (
            self.env["ir.attachment"]
            .sudo()
            .search(
                [
                    ("res_model", "=", ingestion._name),
                    ("res_id", "=", ingestion.id),
                    ("mimetype", "=", "message/rfc822"),
                ],
                limit=1,
            )
        )
        self.assertTrue(original)
        self.assertEqual(bytes(original.raw), raw)
        attachment_count = (
            self.env["ir.attachment"]
            .sudo()
            .search_count(
                [("res_model", "=", ingestion._name), ("res_id", "=", ingestion.id)],
            )
        )

        duplicate = self.env["mail.thread"].message_process(
            None,
            raw,
        )
        self.assertEqual(duplicate, ingestion)
        self.assertEqual(ingestion.duplicate_delivery_count, 1)
        self.assertEqual(
            self.env["ir.attachment"]
            .sudo()
            .search_count(
                [("res_model", "=", ingestion._name), ("res_id", "=", ingestion.id)],
            ),
            attachment_count,
        )

    def test_configured_email_route_is_automatically_processed(self):
        self.config.processing_enabled = True
        message = EmailMessage()
        message["From"] = "Shine <hello@shine.example.invalid>"
        message["To"] = self.config.alias_full_name
        message["Subject"] = (
            "Export comptable Synthetic - du 01/07/2026 au 31/07/2026"
        )
        message["Message-ID"] = "<synthetic-automatic-route@example.invalid>"
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

        ingestion = self.env["mail.thread"].message_process(
            None,
            message.as_bytes(),
        )
        self.assertEqual(ingestion.state, "done", ingestion.last_error)
        self.assertTrue(ingestion.statement_ids.line_ids)
        self.assertTrue(ingestion.statement_ids.accepted_evidence_id)

    def test_email_processing_requires_a_complete_route(self):
        self.config.allowed_senders = False
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.config.processing_enabled = True

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
            },
        )

        ingestion.action_process_now()

        self.assertEqual(ingestion.state, "done")
        archive_file = ingestion.file_ids.filtered(
            lambda item: item.classification == "zip",
        )
        self.assertEqual(archive_file.processing_state, "processed")
        self.assertEqual(
            set(archive_file.extracted_file_ids.mapped("classification")),
            {"ofx", "pdf", "csv", "qif"},
        )
        self.assertEqual(len(ingestion.statement_ids.line_ids), 3)

    def test_allowlisted_shine_link_download_is_retained_and_processed(self):
        archive_buffer = BytesIO()
        with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("transactions.ofx", self.ofx)
            archive.writestr("statement.pdf", self.pdf)
        ingestion = self.env["account.bank.ingestion"].message_new(
            {
                "subject": "Export comptable Synthetic - du 01/07/2026 au 31/07/2026",
                "message_id": "<synthetic-download@example.invalid>",
                "email_from": "Shine <hello@shine.example.invalid>",
                "to": self.config.alias_full_name,
                "body": '<a href="https://files.shine.example.invalid/export.zip?Signature=secret">Download</a>',
            },
            {"config_id": self.config.id},
        )

        with patch.object(
            type(ingestion),
            "_download_https",
            autospec=True,
            return_value=(
                archive_buffer.getvalue(),
                "scheduled-export.zip",
                "application/zip",
            ),
        ) as download:
            ingestion.action_process_now()

        download.assert_called_once()
        self.assertEqual(download.call_args.args[2], "files.shine.example.invalid")
        retained_archive = ingestion.file_ids.filtered(
            lambda item: item.classification == "zip",
        )
        self.assertEqual(retained_archive.download_host, "files.shine.example.invalid")
        self.assertNotIn("Signature", retained_archive.processing_detail or "")
        self.assertEqual(ingestion.state, "done")
        self.assertEqual(len(ingestion.statement_ids.line_ids), 3)

    def test_recovered_archive_bypasses_an_expired_email_link(self):
        archive_buffer = BytesIO()
        with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("transactions.ofx", self.ofx)
            archive.writestr("statement.pdf", self.pdf)
        ingestion = self.env["account.bank.ingestion"].message_new(
            {
                "subject": "Export comptable Synthetic - du 01/07/2026 au 31/07/2026",
                "message_id": "<synthetic-expired-link@example.invalid>",
                "email_from": "Shine <hello@shine.example.invalid>",
                "to": self.config.alias_full_name,
                "body": '<a href="https://files.shine.example.invalid/expired.zip?Signature=secret">Download</a>',
            },
            {"config_id": self.config.id},
        )
        self.env["account.bank.ingestion.upload"].create(
            {
                "ingestion_id": ingestion.id,
                "source_file": BinaryBytes(archive_buffer.getvalue()),
                "source_filename": "recovered-export.zip",
            },
        ).action_add_file()

        ingestion.action_process_now()

        self.assertEqual(ingestion.state, "done")
        self.assertFalse(any(ingestion.file_ids.mapped("download_host")))
        self.assertEqual(len(ingestion.statement_ids.line_ids), 3)

    def test_unsafe_archive_path_is_rejected_without_extracting_members(self):
        archive_buffer = BytesIO()
        with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("../transactions.ofx", self.ofx)
        ingestion = self._ingestion("<synthetic-unsafe-zip@example.invalid>")
        self.env["ir.attachment"].sudo().create(
            {
                "name": "unsafe-export.zip",
                "raw": archive_buffer.getvalue(),
                "mimetype": "application/zip",
                "res_model": ingestion._name,
                "res_id": ingestion.id,
                "company_id": self.company.id,
            },
        )

        ingestion.action_process_now()

        self.assertEqual(ingestion.state, "failed")
        archive_file = ingestion.file_ids.filtered(
            lambda item: item.classification == "zip",
        )
        self.assertFalse(archive_file.extracted_file_ids)
        self.assertTrue(
            ingestion.exception_ids.filtered(lambda item: item.state == "open"),
        )

    def test_unsupported_attachment_is_visible_and_blocks_certification(self):
        ingestion = self._ingestion(
            "<synthetic-unsupported@example.invalid>",
            ofx=self.ofx,
            pdf=self.pdf,
        )
        self.env["ir.attachment"].sudo().create(
            {
                "name": "unexpected.bin",
                "raw": b"unexpected accounting export",
                "mimetype": "application/octet-stream",
                "res_model": ingestion._name,
                "res_id": ingestion.id,
                "company_id": self.company.id,
            },
        )

        ingestion.action_process_now()

        statement = ingestion.statement_ids
        self._complete_documents_archive(statement)
        self.assertEqual(ingestion.state, "attention")
        self.assertTrue(
            statement.exception_ids.filtered(lambda item: item.kind == "unsupported"),
        )
        self.env["account.bank.statement.confirm"].create(
            {
                "statement_id": statement.id,
                "balance_start": 1000,
                "balance_end_real": 1200,
            },
        ).action_confirm()
        statement.action_confirm_cutover_baseline()
        self.assertFalse(statement.can_certify)
        open_issue = statement.exception_ids.filtered(
            lambda item: item.kind == "unsupported" and item.state == "open",
        )
        self.assertEqual(len(open_issue), 1)
        self.assertIn(open_issue.name, statement.review_blocking_reason)
        self.assertEqual(statement.transaction_check_status, "attention")
        action = open_issue.action_open_resolution()
        self.assertEqual(action["res_id"], open_issue.id)

    def test_accounting_manager_can_resolve_issue_without_ingestion_write_access(self):
        ingestion = self._ingestion(
            "<synthetic-manager-resolution@example.invalid>",
            ofx=self.ofx,
            pdf=self.pdf,
        )
        self.env["ir.attachment"].sudo().create(
            {
                "name": "readme.txt",
                "raw": b"not part of the scheduled bank export",
                "mimetype": "text/plain",
                "res_model": ingestion._name,
                "res_id": ingestion.id,
                "company_id": self.company.id,
            },
        )
        ingestion.action_process_now()
        issue = ingestion.exception_ids.filtered(
            lambda item: item.kind == "unsupported" and item.state == "open",
        )
        manager = new_test_user(
            self.env,
            login="bank-ingestion-issue-manager",
            groups="account.group_account_manager",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        self.assertFalse(
            ingestion.with_user(manager).has_access("write"),
        )

        issue.with_user(manager).write(
            {
                "resolution": "not_relevant",
                "resolution_reason": "This text file is not part of the bank statement.",
            },
        )
        issue.with_user(manager).action_resolve()

        self.assertEqual(issue.state, "resolved")
        audit_message = ingestion.message_ids.filtered(
            lambda message: "Bank statement issue resolved" in (message.body or ""),
        )[:1]
        self.assertTrue(audit_message)
        self.assertEqual(audit_message.author_id, manager.partner_id)

    def test_monthly_review_view_prioritizes_visible_accounting_checks(self):
        architecture = self.env.ref(
            "usl_accounting.view_bank_statement_form_review",
        ).arch_db

        self.assertIn("What needs your attention", architecture)
        self.assertIn("Monthly checks", architecture)
        self.assertIn("Official statement", architecture)
        self.assertIn("Imported transactions", architecture)
        self.assertIn("Bank balances", architecture)
        self.assertIn("What to check", architecture)
        self.assertIn(
            'decoration-danger="evidence_check_status == \'missing\'"',
            architecture,
        )
        self.assertIn(
            'decoration-warning="balance_check_status == \'unconfirmed\'"',
            architecture,
        )
        self.assertIn(
            'decoration-success="continuity_status == \'valid\'"',
            architecture,
        )
        self.assertNotIn("Resolve the remaining bank export exceptions", architecture)
        self.assertIn(
            "Confirm that the official PDF in Documents, imported movements",
            architecture,
        )
        self.assertIn('string="Add official PDF"', architecture)
        self.assertIn('string="Resolve"', architecture)
        statement_list_architecture = self.env.ref(
            "usl_accounting.view_bank_statement_list_review",
        ).arch_db
        self.assertIn("decoration-danger", statement_list_architecture)
        self.assertIn("decoration-success", statement_list_architecture)

        config_architecture = self.env.ref(
            "usl_accounting.view_bank_ingestion_config_form",
        ).arch_db
        self.assertIn("Bank Statement Email Setup", config_architecture)
        self.assertIn("Send bank exports to", config_architecture)
        self.assertIn("Advanced safeguards", config_architecture)
        self.assertNotIn("Scheduled export address", config_architecture)
        config_list_architecture = self.env.ref(
            "usl_accounting.view_bank_ingestion_config_list",
        ).arch_db
        self.assertIn("decoration-danger", config_list_architecture)
        self.assertIn("decoration-success", config_list_architecture)

        setup_menu = self.env.ref("usl_accounting.menu_bank_ingestion_config")
        matching_menu = self.env.ref(
            "account_reconcile_oca.menu_account_reconcile_model",
        )
        self.assertEqual(setup_menu.parent_id, matching_menu.parent_id)
        self.assertEqual(setup_menu.sequence, matching_menu.sequence + 1)

    def test_bank_identifier_is_readable_but_credentials_remain_masked(self):
        config_architecture = self.env.ref(
            "usl_accounting.view_bank_ingestion_config_form",
        )._get_combined_arch()
        account_identifiers = config_architecture.xpath(
            "//field[@name='source_account_identifier']",
        )
        self.assertEqual(len(account_identifiers), 1)
        self.assertIsNone(account_identifiers[0].get("password"))
        self.assertNotEqual(account_identifiers[0].get("widget"), "password")

        incoming_mail_architecture = self.env.ref(
            "mail.view_email_server_form",
        )._get_combined_arch()
        credential_fields = incoming_mail_architecture.xpath(
            "//field[@name='password']",
        )
        self.assertTrue(credential_fields)
        self.assertTrue(
            all(field.get("password") == "True" for field in credential_fields),
        )

    def test_missing_pdf_blocks_certification_without_blocking_import(self):
        ingestion = self._ingestion("<synthetic-no-pdf@example.invalid>", ofx=self.ofx)
        ingestion.action_process_now()
        statement = ingestion.statement_ids
        self.assertFalse(statement.accepted_evidence_id)
        self.assertIn("PDF", statement.review_blocking_reason)
        with self.assertRaises(UserError):
            statement.action_certify()

        evidence_only = self._ingestion(
            "<synthetic-later-pdf@example.invalid>",
            pdf=self.pdf,
        )
        evidence_only.action_process_now()
        self.assertEqual(evidence_only.state, "done")
        self.assertEqual(evidence_only.statement_ids, statement)
        self.assertTrue(statement.accepted_evidence_id)
        self.assertFalse(
            evidence_only.exception_ids.filtered(
                lambda item: item.kind == "import" and item.state == "open",
            ),
        )

    def test_missing_fitids_require_manager_decisions_and_retry_converges(self):
        ambiguous_ofx = re.sub(rb"\s*<FITID>[^<]*</FITID>", b"", self.ofx)
        ingestion = self._ingestion(
            "<synthetic-no-fitids@example.invalid>",
            ofx=ambiguous_ofx,
            pdf=self.pdf,
        )

        ingestion.action_process_now()

        self.assertEqual(ingestion.state, "attention")
        self.assertFalse(ingestion.statement_ids)
        exceptions = ingestion.exception_ids.filtered(
            lambda item: item.kind == "identity" and item.state == "open",
        )
        self.assertEqual(
            len(exceptions),
            3,
            str(
                [
                    (item.kind, item.name, item.detail)
                    for item in ingestion.exception_ids
                ],
            ),
        )
        for exception in exceptions.sorted("id"):
            exception.write(
                {
                    "resolution": "approve_new",
                    "resolution_reason": "The manager verified the source row in the OFX export.",
                },
            )
            exception.action_resolve()

        statement = ingestion.statement_ids
        self.assertEqual(len(statement), 1)
        self.assertEqual(len(statement.line_ids), 3)
        self.assertEqual(
            set(statement.line_ids.mapped("provider_identity_kind")),
            {"approved_fallback"},
        )
        self.assertEqual(ingestion.state, "done")
        ingestion.action_retry()
        self.assertEqual(len(statement.line_ids), 3)
        self.assertFalse(
            ingestion.exception_ids.filtered(lambda item: item.state == "open"),
        )

    def test_balance_confirmation_certification_and_controlled_reopening(self):
        _ingestion, statement = self._process_complete_month()
        self.env["account.bank.statement.confirm"].create(
            {
                "statement_id": statement.id,
                "balance_start": 1000,
                "balance_end_real": 1200,
            },
        ).action_confirm()
        statement.action_confirm_cutover_baseline()
        lock_date = self.company._get_user_fiscal_lock_date(self.journal)

        statement.action_certify()
        statement.action_certify()
        self.assertEqual(statement.certification_state, "certified")
        self.assertEqual(len(statement.certification_ids), 1)
        self.assertEqual(
            self.company._get_user_fiscal_lock_date(self.journal),
            lock_date,
        )
        with self.assertRaises(UserError):
            statement.write({"balance_end_real": 1201})
        with self.assertRaises(UserError):
            statement.line_ids[0].write({"amount": 301})
        liquidity_line = statement.line_ids[0].move_id.line_ids.filtered(
            lambda line: line.account_id == self.journal.default_account_id,
        )
        self.assertEqual(len(liquidity_line), 1)
        with self.assertRaises(UserError):
            liquidity_line.write({"debit": liquidity_line.debit + 1})

        self.env["account.bank.statement.reopen"].create(
            {
                "statement_id": statement.id,
                "reason": "Correct the official bank evidence.",
            },
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
            },
        ).action_confirm()
        statement.action_confirm_cutover_baseline()
        self.assertEqual(statement.balance_difference, -1)
        self.assertTrue(statement.balances_confirmed)
        self.assertFalse(statement.can_certify)

    def test_continuity_uses_the_immediately_preceding_certified_month(self):
        _july_ingestion, july = self._process_complete_month()
        self._confirm_and_certify(july, 1000, 1200)
        august_ofx = (
            self.ofx.replace(b"202607", b"202608")
            .replace(b"<BALAMT>1200.00", b"<BALAMT>1400.00")
            .replace(b"shine-synthetic-", b"shine-august-")
        )
        august = self._ingestion(
            "<synthetic-august@example.invalid>",
            ofx=august_ofx,
            pdf=self.pdf,
            subject="Export comptable Synthetic - du 01/08/2026 au 31/08/2026",
        )
        august.action_process_now()
        statement = august.statement_ids
        self._complete_documents_archive(statement)

        self.env["account.bank.statement.confirm"].create(
            {
                "statement_id": statement.id,
                "balance_start": 1199,
                "balance_end_real": 1399,
            },
        ).action_confirm()
        self.assertEqual(statement.balance_difference, 0)
        self.assertEqual(statement.continuity_status, "broken")
        self.assertFalse(statement.can_certify)

        self.env["account.bank.statement.confirm"].create(
            {
                "statement_id": statement.id,
                "balance_start": 1200,
                "balance_end_real": 1400,
            },
        ).action_confirm()
        self.assertEqual(statement.continuity_status, "valid")
        self.assertTrue(statement.can_certify)

    def test_replacement_evidence_requires_reopening_and_preserves_history(self):
        _ingestion, statement = self._process_complete_month()
        self._confirm_and_certify(statement, 1000, 1200)
        original = statement.accepted_evidence_id
        replacement_pdf = self.pdf + b"\n% replacement version\n"
        replacement_ingestion = self._ingestion(
            "<synthetic-replacement@example.invalid>",
            ofx=self.ofx,
            pdf=replacement_pdf,
        )
        replacement_ingestion.action_process_now()
        replacement = replacement_ingestion.file_ids.filtered(
            lambda item: item.classification == "pdf",
        )

        self.assertEqual(original.evidence_status, "accepted")
        self.assertEqual(replacement.evidence_status, "candidate")
        with self.assertRaises(UserError):
            replacement.action_accept_evidence()
        self.env["account.bank.statement.reopen"].create(
            {
                "statement_id": statement.id,
                "reason": "Review a later official PDF version.",
            },
        ).action_reopen()
        replacement.action_accept_evidence()
        self._complete_documents_archive(statement)
        self.assertEqual(original.evidence_status, "superseded")
        self.assertEqual(statement.accepted_evidence_id, replacement)
        self.assertEqual(
            len(
                statement.bank_source_file_ids.filtered(
                    lambda item: item.classification == "pdf",
                ),
            ),
            2,
        )
        self.assertFalse(
            replacement_ingestion.exception_ids.filtered(
                lambda item: item.state == "open",
            ),
        )
        statement.action_certify()
        certifications = statement.certification_ids.filtered(
            lambda item: item.event_type == "certify",
        ).sorted("id")
        self.assertEqual(len(certifications), 2)
        self.assertEqual(certifications[0].evidence_sha256, original.sha256)
        self.assertEqual(certifications[1].evidence_sha256, replacement.sha256)

    def test_other_company_and_ordinary_users_cannot_read_bank_sources(self):
        ingestion, statement = self._process_complete_month()
        readonly_accountant = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Read-only synthetic accountant",
                    "login": "readonly-bank-user@example.invalid",
                    "email": "readonly-bank-user@example.invalid",
                    "company_id": self.company.id,
                    "company_ids": [Command.set(self.company.ids)],
                    "group_ids": [
                        Command.set(
                            self.env.ref("account.group_account_readonly").ids,
                        ),
                    ],
                },
            )
        )
        other_company = self.env["res.company"].create(
            {"name": "Other Synthetic Company"},
        )
        other_accountant = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Other company accountant",
                    "login": "other-company-accountant@example.invalid",
                    "email": "other-company-accountant@example.invalid",
                    "company_id": other_company.id,
                    "company_ids": [Command.set(other_company.ids)],
                    "group_ids": [
                        Command.set(self.env.ref("account.group_account_readonly").ids),
                    ],
                },
            )
        )
        ordinary = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Ordinary synthetic user",
                    "login": "ordinary-bank-user@example.invalid",
                    "email": "ordinary-bank-user@example.invalid",
                    "company_id": self.company.id,
                    "company_ids": [Command.set(self.company.ids)],
                    "group_ids": [Command.set(self.env.ref("base.group_user").ids)],
                },
            )
        )
        for record in (
            ingestion,
            ingestion.file_ids[0],
            statement.accepted_evidence_id.attachment_id,
        ):
            with self.assertRaises(AccessError):
                record.with_user(other_accountant).check_access("read")
        with self.assertRaises(AccessError):
            ingestion.with_user(ordinary).check_access("read")
        ingestion.with_user(readonly_accountant).check_access("read")
        ingestion.file_ids[0].with_user(readonly_accountant).check_access("read")
        statement.with_user(readonly_accountant).check_access("read")
        with self.assertRaises(AccessError):
            statement.with_user(readonly_accountant).action_certify()

    def test_malformed_ofx_preserves_source_and_is_retryable(self):
        ingestion = self._ingestion(
            "<synthetic-malformed@example.invalid>",
            ofx=b"OFXHEADER:100\n<OFX><BROKEN>",
            pdf=self.pdf,
        )
        ingestion.action_process_now()

        self.assertIn(ingestion.state, ("attention", "failed"))
        source = ingestion.file_ids.filtered(
            lambda item: item.filename == "transactions.ofx",
        )
        self.assertEqual(source.processing_state, "failed")
        self.assertEqual(source._content(), b"OFXHEADER:100\n<OFX><BROKEN>")
        self.assertTrue(ingestion.unresolved_exception_count)

        self.env["account.bank.ingestion.upload"].create(
            {
                "ingestion_id": ingestion.id,
                "source_file": BinaryBytes(self.ofx),
                "source_filename": "recovered-transactions.ofx",
            },
        ).action_add_file()
        ingestion.action_retry()

        self.assertEqual(ingestion.state, "done")
        self.assertEqual(len(ingestion.statement_ids.line_ids), 3)
        self.assertFalse(
            ingestion.exception_ids.filtered(
                lambda item: item.kind == "import" and item.state == "open",
            ),
        )
        self.assertTrue(
            ingestion.file_ids.filtered(
                lambda item: (
                    item.recovered_upload and item.processing_state == "processed"
                ),
            ),
        )

    def test_damaged_pdf_is_retained_but_never_accepted_as_evidence(self):
        damaged_pdf = b"%PDF-1.4\nthis file has no valid trailer"
        ingestion = self._ingestion(
            "<synthetic-damaged-pdf@example.invalid>",
            ofx=self.ofx,
            pdf=damaged_pdf,
        )

        ingestion.action_process_now()

        source_file = ingestion.file_ids.filtered(
            lambda item: item.classification == "pdf",
        )
        self.assertEqual(source_file._content(), damaged_pdf)
        self.assertEqual(source_file.processing_state, "failed")
        self.assertEqual(source_file.evidence_status, "candidate")
        self.assertIn("damaged or incomplete", source_file.processing_detail)
        self.assertFalse(ingestion.statement_ids.accepted_evidence_id)
        self.assertTrue(
            ingestion.exception_ids.filtered(
                lambda item: item.kind == "evidence" and item.state == "open",
            ),
        )

    def test_overlapping_failed_source_blocks_the_existing_statement(self):
        _complete, statement = self._process_complete_month()
        failed = self._ingestion(
            "<synthetic-overlapping-failure@example.invalid>",
            ofx=b"OFXHEADER:100\n<OFX><BROKEN>",
        )
        failed.action_process_now()

        self.assertTrue(failed.unresolved_exception_count)
        self.assertTrue(statement.unresolved_exception_count)
        self.assertFalse(statement.can_certify)
        self.assertEqual(
            failed.exception_ids.filtered(
                lambda item: item.state == "open",
            ).statement_id,
            statement,
        )

    def test_operational_state_cannot_be_forged_by_an_accountant(self):
        ingestion, statement = self._process_complete_month()
        accountant = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Synthetic accountant",
                    "login": "synthetic-accountant@example.invalid",
                    "email": "synthetic-accountant@example.invalid",
                    "company_id": self.company.id,
                    "company_ids": [Command.set(self.company.ids)],
                    "group_ids": [
                        Command.set(self.env.ref("account.group_account_user").ids),
                    ],
                },
            )
        )
        with self.assertRaises(AccessError):
            statement.with_user(accountant).write({"balances_confirmed": True})
        with self.assertRaises(AccessError):
            ingestion.file_ids[0].with_user(accountant).write(
                {"processing_state": "processed"},
            )
        with self.assertRaises(AccessError):
            self.env["account.bank.statement.certification"].with_user(
                accountant,
            ).create(
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
                },
            )

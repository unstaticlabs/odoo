import datetime as dt
from unittest.mock import patch

from odoo import Command
from odoo.tests import TransactionCase, tagged

from odoo.addons.usl_documents.models.document import UslDocument
from odoo.addons.usl_documents.models.paperless_client import PaperlessError


@tagged("post_install", "-at_install", "usl_documents_accounting")
class TestAccountingDocumentContexts(TransactionCase):
    def test_accountant_reviewer_receives_evidence_reader_role(self):
        profile = self.env["res.users"]._usl_pocketid_profile_definitions()[
            "accountant_reviewer"
        ]

        self.assertIn(
            "usl_documents.group_documents_accountant",
            profile["groups"],
        )
        self.assertNotIn(
            "usl_documents.group_documents_user",
            profile["groups"],
        )
        self.assertNotIn(
            "usl_documents.group_documents_manager",
            profile["groups"],
        )
        self.assertEqual(len(profile["groups"]), len(set(profile["groups"])))

    def test_tax_and_closing_records_support_archived_evidence(self):
        allowed = self.env["usl.document.link"]._allowed_models()
        self.assertIn("rebuild.account.declaration", allowed)
        self.assertIn("rebuild.account.closing.period", allowed)
        self.assertIn("account.bank.statement", allowed)
        for model_name in (
            "rebuild.account.declaration",
            "rebuild.account.closing.period",
        ):
            self.assertIn("archived_document_count", self.env[model_name]._fields)
            self.assertTrue(
                callable(
                    getattr(self.env[model_name], "action_open_documents_workspace"),
                ),
            )

    def test_accounting_forms_use_one_contextual_documents_entry_point(self):
        for xmlid in (
            "usl_documents_accounting.view_rebuild_account_declaration_documents",
            "usl_documents_accounting.view_rebuild_account_closing_documents",
        ):
            arch = self.env.ref(xmlid).arch_db
            self.assertEqual(arch.count('name="action_open_documents_workspace"'), 2)
            self.assertIn('string="Upload"', arch)
            self.assertIn('string="Documents"', arch)
            self.assertIn('invisible="archived_document_count != 0"', arch)
            self.assertIn('invisible="archived_document_count == 0"', arch)
            self.assertNotIn("Find / upload", arch)
            self.assertNotIn("Evidence", arch)
            self.assertNotIn("action_open_archived_documents", arch)

    def test_accepted_bank_evidence_archives_asynchronously_and_failure_is_visible(
        self,
    ):
        company = self.env.company
        bank_account = self.env["res.partner.bank"].create(
            {
                "account_number": "FR7630001007941234567890185",
                "partner_id": company.partner_id.id,
                "company_id": company.id,
            },
        )
        journal = self.env["account.journal"].create(
            {
                "name": "Bank evidence archive",
                "code": "BEA1",
                "type": "bank",
                "company_id": company.id,
                "bank_account_id": bank_account.id,
            },
        )
        config = self.env["account.bank.ingestion.config"].create(
            {
                "name": "Bank evidence archive route",
                "company_id": company.id,
                "journal_id": journal.id,
                "source_account_identifier": bank_account.account_number,
                "responsible_user_id": self.env.user.id,
                "automatic_start_date": dt.date(2026, 7, 1),
                "alias_name": "bank-evidence-archive-test",
            },
        )
        ingestion = (
            self.env["account.bank.ingestion"]
            .sudo()
            .create(
                {
                    "name": "July bank evidence",
                    "config_id": config.id,
                    "sender": "hello@shine.fr",
                    "period_start": dt.date(2026, 7, 1),
                    "period_end": dt.date(2026, 7, 31),
                },
            )
        )
        bank_line = self.env["account.bank.statement.line"].create(
            {
                "name": "Evidence fixture movement",
                "payment_ref": "Evidence fixture movement",
                "journal_id": journal.id,
                "date": dt.date(2026, 7, 15),
                "amount": 10,
            },
        )
        statement = self.env["account.bank.statement"].create(
            {
                "name": "July 2026",
                "date": dt.date(2026, 7, 31),
                "ingestion_config_id": config.id,
                "period_start": dt.date(2026, 7, 1),
                "period_end": dt.date(2026, 7, 31),
                "line_ids": [Command.set(bank_line.ids)],
            },
        )
        content = b"%PDF-1.4\nsynthetic bank evidence\n%%EOF\n"
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": "statement.pdf",
                    "raw": content,
                    "mimetype": "application/pdf",
                    "res_model": ingestion._name,
                    "res_id": ingestion.id,
                    "company_id": company.id,
                },
            )
        )
        source_file = (
            self.env["account.bank.ingestion.file"]
            .sudo()
            ._from_attachment(
                ingestion,
                attachment,
            )
        )
        source_file.sudo().write(
            {
                "statement_id": statement.id,
                "period_start": dt.date(2026, 7, 1),
                "period_end": dt.date(2026, 7, 31),
            },
        )

        source_file._accept_evidence()

        self.assertEqual(source_file.paperless_archive_state, "pending")
        self.assertEqual(statement.accepted_evidence_id, source_file)
        with patch.object(
            UslDocument,
            "upload_from_odoo",
            side_effect=PaperlessError("Archive offline"),
        ):
            self.env[
                "account.bank.ingestion.file"
            ]._cron_archive_accepted_bank_evidence()
        self.assertEqual(source_file.paperless_archive_state, "failed")
        self.assertIn("Archive offline", source_file.paperless_archive_error)
        self.assertEqual(statement.accepted_evidence_id, source_file)

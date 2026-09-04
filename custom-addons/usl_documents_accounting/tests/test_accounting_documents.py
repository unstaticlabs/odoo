import datetime as dt
import hashlib
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import BinaryBytes, file_open

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
            self.assertEqual(arch.count('name="action_open_documents_workspace"'), 1)
            self.assertNotIn('string="Upload"', arch)
            self.assertIn('<span class="o_stat_text">Documents</span>', arch)
            self.assertIn("document_archive_failure_count", arch)
            self.assertIn("document_archive_pending_count", arch)
            self.assertNotIn("Find / upload", arch)
            self.assertNotIn("Evidence", arch)
            self.assertNotIn("action_open_archived_documents", arch)

    def test_bank_statement_uses_one_first_class_documents_entry_point(self):
        arch = self.env.ref(
            "usl_documents_accounting.view_bank_statement_review_documents",
        ).arch_db

        self.assertIn("Official statement", arch)
        self.assertIn('name="bank_evidence_document_id"', arch)
        self.assertIn(
            "decoration-danger=\"bank_evidence_archive_state in "
            "('not_requested', 'failed', 'unavailable')\"",
            arch,
        )
        self.assertIn(
            'decoration-success="bank_evidence_archive_state == \'archived\'"',
            arch,
        )
        self.assertNotIn('string="Archive"', arch)
        self.assertNotIn('name="action_open_documents_workspace"', arch)

    def _bank_evidence_fixture(self, content=None):
        company = self.env.company
        banking_tag = (
            self.env["usl.paperless.tag"]
            .sudo()
            .with_context(usl_documents_cache_write=True)
            .create(
                {
                    "name": "Banking",
                    "paperless_id": 97991,
                    "matching_algorithm": "6",
                    "active": True,
                },
            )
        )
        self.env.ref("usl_documents.smart_view_banking").sudo().with_context(
            usl_documents_archive_view_sync=True,
        ).write({"tag_ids": [Command.set(banking_tag.ids)]})
        (
            self.env["usl.paperless.tag"]
            .sudo()
            .with_context(usl_documents_cache_write=True)
            .create(
                {
                    "name": "Bank statement",
                    "paperless_id": 97992,
                    "matching_algorithm": "6",
                    "active": True,
                },
            )
        )
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
                "provider_code": "shine",
                "provider_account_id": bank_account.account_number,
                "provider_transaction_id": "ARCHIVE-FITID-1",
            },
        )
        statement = self.env["account.bank.statement"].create(
            {
                "name": "July 2026",
                "date": dt.date(2026, 7, 31),
                "ingestion_config_id": config.id,
                "period_start": dt.date(2026, 7, 1),
                "period_end": dt.date(2026, 7, 31),
                "balance_start": 0,
                "balance_end_real": 10,
                "balances_confirmed": True,
                "cutover_baseline_confirmed": True,
                "line_ids": [Command.set(bank_line.ids)],
            },
        )
        if content is None:
            with file_open(
                "usl_accounting/tests/fixtures/shine_statement.pdf",
                "rb",
            ) as fixture:
                content = fixture.read()
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
        return statement, source_file, content

    def _archived_document(self, source_file, checksum=None, paperless_id=98001):
        checksum = checksum or source_file.sha256
        required_tags = self.env["usl.paperless.tag"].sudo().search(
            [("name", "in", ("Banking", "Bank statement"))],
        )
        document = self.env["usl.document"].sudo().create(
            {
                "name": "Archived bank statement",
                "paperless_id": paperless_id,
                "company_id": source_file.company_id.id,
                "confidentiality": "accounting",
                "accounting_evidence": True,
                "retention_hold": True,
                "review_state": "classified",
                "availability_state": "available",
                "permission_sync_state": "synchronized",
                "checksum": checksum,
                "tag_ids": [
                    Command.set(required_tags.ids),
                ],
            },
        )
        self.env["usl.document.version"].sudo().create(
            {
                "document_id": document.id,
                "paperless_version_id": "bank-version-1",
                "label": "Received original",
                "checksum": checksum,
                "is_current": True,
                "is_received_original": True,
                "source": "odoo_attachment",
            },
        )
        return document

    def test_archive_failure_is_visible_and_blocks_certification(self):
        statement, source_file, _content = self._bank_evidence_fixture()

        self.assertEqual(source_file.paperless_archive_state, "pending")
        self.assertEqual(statement.accepted_evidence_id, source_file)
        self.assertFalse(statement.can_certify)
        self.assertIn("Documents", statement.review_blocking_reason)
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
        statement.invalidate_recordset()
        self.assertFalse(statement.can_certify)
        self.assertIn("Archive offline", statement.review_blocking_reason)

    def test_retry_keeps_accepted_evidence_in_the_archive_queue(self):
        statement, source_file, _content = self._bank_evidence_fixture()

        self.assertEqual(source_file.evidence_status, "accepted")
        self.assertEqual(source_file.paperless_archive_state, "pending")

        source_file._associate_pdf()

        self.assertEqual(statement.accepted_evidence_id, source_file)
        self.assertEqual(source_file.evidence_status, "accepted")
        self.assertEqual(source_file.paperless_archive_state, "pending")
        queued = self.env["account.bank.ingestion.file"].search(
            [
                ("classification", "=", "pdf"),
                ("evidence_status", "=", "accepted"),
                ("paperless_archive_state", "in", ("pending", "processing")),
            ],
        )
        self.assertIn(source_file, queued)

    def test_accepted_bank_evidence_skips_generic_attachment_archive(self):
        statement, source_file, _content = self._bank_evidence_fixture()

        policy = statement._document_archive_policy(source_file.attachment_id)

        self.assertEqual(policy["archive_mode"], "never")
        self.assertEqual(policy["policy_reason"], "managed_bank_statement_evidence")
        self.assertTrue(policy["accounting_evidence"])

    def test_retry_reuses_exact_link_and_deactivates_conflicting_link(self):
        statement, source_file, _content = self._bank_evidence_fixture()
        wrong_document = self._archived_document(source_file, checksum="f" * 64)
        exact_document = self._archived_document(source_file, paperless_id=98002)
        Link = self.env["usl.document.link"].sudo()
        wrong_link = Link.create(
            {
                "document_id": wrong_document.id,
                "res_model": statement._name,
                "res_id": statement.id,
                "record_name": statement.display_name,
                "company_id": statement.company_id.id,
                "linked_by_id": self.env.user.id,
                "version_id": "bank-version-1",
            },
        )
        exact_link = Link.create(
            {
                "document_id": exact_document.id,
                "res_model": statement._name,
                "res_id": statement.id,
                "record_name": statement.display_name,
                "company_id": statement.company_id.id,
                "linked_by_id": self.env.user.id,
                "version_id": "bank-version-1",
            },
        )

        with patch.object(UslDocument, "action_sync_permissions", return_value=True):
            source_file._process_bank_evidence_archive()

        wrong_link.invalidate_recordset()
        exact_link.invalidate_recordset()
        self.assertFalse(wrong_link.active)
        self.assertTrue(exact_link.active)
        self.assertEqual(source_file.paperless_archive_state, "archived")
        self.assertEqual(source_file.paperless_document_id, exact_document)
        self.assertEqual(source_file.paperless_version, "bank-version-1")

    def test_damaged_pdf_explains_replacement_and_is_not_sent_to_documents(self):
        statement, source_file, _content = self._bank_evidence_fixture(
            b"%PDF-1.4\ntruncated bank statement",
        )

        with patch.object(UslDocument, "upload_from_odoo") as upload:
            statement.action_archive_bank_evidence()

        upload.assert_not_called()
        self.assertEqual(source_file.paperless_archive_state, "failed")
        self.assertIn("damaged or incomplete", source_file.paperless_archive_error)
        statement.invalidate_recordset()
        self.assertIn("damaged or incomplete", statement.review_blocking_reason)

    def test_accountant_can_replace_damaged_pdf_from_the_monthly_statement(self):
        statement, damaged_source, _content = self._bank_evidence_fixture(
            b"%PDF-1.4\ntruncated bank statement",
        )
        with file_open(
            "usl_accounting/tests/fixtures/shine_statement.pdf",
            "rb",
        ) as fixture:
            replacement_content = fixture.read()
        replacement_checksum = hashlib.sha256(replacement_content).hexdigest()
        document = self._archived_document(
            damaged_source,
            checksum=replacement_checksum,
        )
        action = statement.action_open_statement_pdf_upload()
        self.assertEqual(action["context"]["default_statement_id"], statement.id)

        with (
            patch.object(
                UslDocument,
                "upload_from_odoo",
                return_value={"state": "duplicate", "document_id": document.id},
            ),
            patch.object(UslDocument, "action_sync_permissions", return_value=True),
        ):
            result = self.env["account.bank.ingestion.upload"].create(
                {
                    "ingestion_id": damaged_source.ingestion_id.id,
                    "statement_id": statement.id,
                    "source_file": BinaryBytes(replacement_content),
                    "source_filename": "statement-original.pdf",
                },
            ).action_add_file()

        statement.invalidate_recordset()
        replacement = statement.accepted_evidence_id
        self.assertNotEqual(replacement, damaged_source)
        self.assertEqual(damaged_source.evidence_status, "superseded")
        self.assertEqual(replacement.sha256, replacement_checksum)
        self.assertEqual(replacement.paperless_archive_state, "archived")
        self.assertEqual(replacement.paperless_document_id, document)
        self.assertEqual(result["params"]["type"], "success")

    def test_exact_archive_version_is_pinned_before_certification(self):
        statement, source_file, content = self._bank_evidence_fixture()
        document = self._archived_document(source_file)

        with (
            patch.object(
                UslDocument,
                "upload_from_odoo",
                return_value={
                    "state": "duplicate",
                    "document_id": document.id,
                },
            ) as upload_from_odoo,
            patch.object(UslDocument, "action_sync_permissions", return_value=True),
        ):
            statement.action_archive_bank_evidence()

        self.assertEqual(source_file.paperless_archive_state, "archived")
        self.assertEqual(source_file.paperless_document_id, document)
        self.assertEqual(source_file.paperless_version, "bank-version-1")
        self.assertTrue(
            self.env.ref("usl_documents.smart_view_banking").tag_ids
            <= document.tag_ids,
        )
        self.assertIn("Bank statement", document.tag_ids.mapped("name"))
        self.assertEqual(
            set(document.tag_ids.ids),
            set(upload_from_odoo.call_args.kwargs["tag_ids"]),
        )
        self.assertEqual(
            {"Banking", "Bank statement"},
            set(statement._document_archive_context()["tags"]),
        )
        self.assertEqual(source_file.sha256, hashlib.sha256(content).hexdigest())
        link = self.env["usl.document.link"].sudo().search(
            [
                ("document_id", "=", document.id),
                ("res_model", "=", statement._name),
                ("res_id", "=", statement.id),
            ],
        )
        self.assertEqual(link.version_id, "bank-version-1")
        statement.invalidate_recordset()
        self.assertTrue(statement.can_certify)
        self.assertEqual(statement.bank_evidence_document_id, document)
        self.assertEqual(
            statement.bank_evidence_document_version,
            "bank-version-1",
        )
        action = statement.action_open_evidence()
        self.assertEqual(action["tag"], "usl_documents.workspace")
        self.assertEqual(action["params"]["initial_document_id"], document.id)
        self.assertEqual(
            action["params"]["initial_version_id"],
            "bank-version-1",
        )
        self.assertEqual(action["params"]["res_model"], statement._name)
        self.assertEqual(action["params"]["res_id"], statement.id)

        account_only = new_test_user(
            self.env,
            login="bank-archive-account-only",
            groups="account.group_account_user",
        )
        self.assertTrue(
            account_only.has_group("usl_documents.group_documents_accountant"),
        )
        self.assertEqual(
            statement.with_user(account_only)
            .action_open_evidence()["params"]["initial_document_id"],
            document.id,
        )
        evidence_reader = new_test_user(
            self.env,
            login="bank-archive-evidence-reader",
            groups=(
                "account.group_account_user,"
                "usl_documents.group_documents_accountant"
            ),
        )
        self.assertEqual(
            statement.with_user(evidence_reader)
            .action_open_evidence()["params"]["initial_document_id"],
            document.id,
        )
        ordinary_user = new_test_user(
            self.env,
            login="bank-archive-ordinary-user",
            groups="base.group_user",
        )
        with self.assertRaises(AccessError):
            statement.with_user(ordinary_user).action_open_evidence()

        statement.action_certify()

        certification = statement.certification_ids.filtered(
            lambda event: event.event_type == "certify",
        )
        self.assertEqual(certification.paperless_document_id, document)
        self.assertEqual(certification.paperless_version, "bank-version-1")
        self.assertEqual(certification.evidence_sha256, source_file.sha256)

    def test_archive_without_exact_checksum_never_becomes_certifiable(self):
        statement, source_file, _content = self._bank_evidence_fixture()
        document = self._archived_document(source_file, checksum="f" * 64)

        with patch.object(
            UslDocument,
            "upload_from_odoo",
            return_value={"state": "duplicate", "document_id": document.id},
        ):
            statement.action_archive_bank_evidence()

        self.assertEqual(source_file.paperless_archive_state, "failed")
        self.assertIn("exact official statement checksum", source_file.paperless_archive_error)
        self.assertFalse(source_file.paperless_document_id)
        self.assertFalse(
            self.env["usl.document.link"].sudo().search_count(
                [
                    ("res_model", "=", statement._name),
                    ("res_id", "=", statement.id),
                ],
            ),
        )
        statement.invalidate_recordset()
        self.assertFalse(statement.can_certify)

    def test_replacement_is_a_new_pinned_version_after_reopening(self):
        statement, source_file, content = self._bank_evidence_fixture()
        document = self._archived_document(source_file)
        duplicate = {"state": "duplicate", "document_id": document.id}
        with (
            patch.object(UslDocument, "upload_from_odoo", return_value=duplicate),
            patch.object(UslDocument, "action_sync_permissions", return_value=True),
        ):
            statement.action_archive_bank_evidence()
        statement.action_certify()

        document.sudo().with_context(usl_documents_cache_write=True).write(
            {"availability_state": "trashed"},
        )
        statement.invalidate_recordset()
        self.assertEqual(statement.review_status, "attention")
        self.assertEqual(statement.bank_evidence_archive_state, "unavailable")
        with self.assertRaisesRegex(UserError, "Restore the original in Documents"):
            statement.action_open_evidence()
        document.sudo().with_context(usl_documents_cache_write=True).write(
            {"availability_state": "available"},
        )

        self.env["account.bank.statement.reopen"].create(
            {
                "statement_id": statement.id,
                "reason": "The bank sent a corrected official PDF.",
            },
        ).action_reopen()
        replacement_content = content + b"\n% corrected synthetic statement\n"
        replacement_attachment = self.env["ir.attachment"].sudo().create(
            {
                "name": "statement-corrected.pdf",
                "raw": replacement_content,
                "mimetype": "application/pdf",
                "res_model": source_file.ingestion_id._name,
                "res_id": source_file.ingestion_id.id,
                "company_id": source_file.company_id.id,
            },
        )
        replacement = (
            self.env["account.bank.ingestion.file"]
            .sudo()
            ._from_attachment(source_file.ingestion_id, replacement_attachment)
        )
        replacement.sudo().write(
            {
                "statement_id": statement.id,
                "period_start": statement.period_start,
                "period_end": statement.period_end,
            },
        )
        replacement._accept_evidence()
        first_version = document.version_ids.filtered(
            lambda version: version.paperless_version_id == "bank-version-1",
        )
        first_version.sudo().write({"is_current": False})
        self.env["usl.document.version"].sudo().create(
            {
                "document_id": document.id,
                "paperless_version_id": "bank-version-2",
                "label": "Corrected official statement",
                "checksum": replacement.sha256,
                "is_current": True,
                "source": "odoo_attachment",
            },
        )
        document.invalidate_recordset(["version_ids"])

        with patch.object(
            UslDocument,
            "action_sync_permissions",
            return_value=True,
        ):
            statement.action_archive_bank_evidence()

        self.assertEqual(source_file.evidence_status, "superseded")
        self.assertEqual(replacement.paperless_document_id, document)
        self.assertEqual(replacement.paperless_version, "bank-version-2")
        self.assertEqual(
            document.link_ids.filtered(
                lambda link: (
                    link.res_model == statement._name and link.res_id == statement.id
                ),
            ).version_id,
            "bank-version-2",
        )
        statement.invalidate_recordset()
        self.assertTrue(statement.can_certify)
        statement.action_certify()
        certification_versions = set(
            statement.certification_ids.filtered(
                lambda event: event.event_type == "certify",
            ).mapped("paperless_version"),
        )
        self.assertEqual(
            certification_versions,
            {"bank-version-1", "bank-version-2"},
        )

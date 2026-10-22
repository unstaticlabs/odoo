"""A native preview is temporary state, not Accounting write delegation."""

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from ..models.readonly_accounting_report import _REPORT_SESSION_CONTEXT_KEY


@tagged("post_install", "-at_install", "usl_readonly_accounting_report")
class TestReadonlyAccountingReport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "[QA] Preview Company"})
        cls.foreign = cls.env["res.company"].create({"name": "[QA] Other Company"})
        group = cls.env.ref("account.group_account_readonly")
        cls.owner = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "[QA] Preview Accountant",
            "login": "qa.preview.accountant@example.invalid",
            "company_id": cls.company.id,
            "company_ids": [Command.set(cls.company.ids)],
            "group_ids": [Command.set(group.ids)],
        })
        cls.agent = cls.env["usl.agent"].with_user(cls.owner).create({
            "name": "[QA] Read-only Preview",
            "purpose": "[QA] Native temporary report qualification",
            "owner_id": cls.owner.id,
            "company_id": cls.company.id,
            "company_ids": [Command.set(cls.company.ids)],
            "delegated_group_ids": [Command.set(group.ids)],
            "access_mode": "read_only",
        })
        cls.account, cls.income = cls.env["account.account"].with_company(cls.company).create([
            {"name": "[QA] Bank", "code": "5123267", "account_type": "asset_cash"},
            {"name": "[QA] Income", "code": "7063267", "account_type": "income"},
        ])
        cls.journal = cls.env["account.journal"].create({
            "name": "[QA] Preview Ledger", "code": "QP67",
            "company_id": cls.company.id, "type": "general",
        })
        cls.move = cls.env["account.move"].create({
            "company_id": cls.company.id, "journal_id": cls.journal.id,
            "date": "2026-03-15",
            "line_ids": [
                Command.create({"account_id": cls.account.id, "debit": 73}),
                Command.create({"account_id": cls.income.id, "credit": 73}),
            ],
        })
        cls.move.action_post()
        cls.filters = {
            "company_id": cls.company.id, "company_ids": cls.company.ids,
            "period_preset": "custom", "date_from": "2026-01-01",
            "date_to": "2026-12-31", "group_by": "none",
        }

    def _reports(self, user=None):
        return self.env["rebuild.account.report.export.wizard"].with_user(
            user or self.agent.user_id,
        ).with_context(allowed_company_ids=self.company.ids)

    def _load(self, report_type="trial_balance", **filters):
        return self._reports().report_client_load(
            report_type, {**self.filters, **filters},
        )

    def test_preview_filters_and_sources_keep_native_ledger(self):
        before = self.move.line_ids.read(["debit", "credit", "account_id"])
        payload = self._load()
        line = next(row for row in payload["lines"] if row.get("account_code") == "5123267")
        self.assertEqual(line["debit"], 73)
        action = self._reports().report_client_open_sources(payload["wizard_id"], line["id"])
        source = self.env["account.move.line"].with_user(self.agent.user_id).search(action["domain"])
        self.assertEqual(source, self.move.line_ids.filtered(lambda row: row.debit))
        updated = self._reports().report_client_load(
            "trial_balance", {**self.filters, "date_to": "2026-11-30"}, payload["wizard_id"],
        )
        self.assertEqual(updated["wizard_id"], payload["wizard_id"])
        self.assertEqual(before, self.move.line_ids.read(["debit", "credit", "account_id"]))

    def test_fold_and_refresh_native_session(self):
        payload = self._load("general_ledger", group_by="account")
        group = next(row for row in payload["lines"] if row["is_group"])
        folded = self._reports().report_client_toggle_group(payload["wizard_id"], group["id"])
        self.assertEqual(folded["wizard_id"], payload["wizard_id"])
        self.assertLess(len(folded["lines"]), len(payload["lines"]))
        current = next(row for row in folded["lines"] if row.get("group_key") == group["group_key"])
        unfolded = self._reports().report_client_toggle_group(payload["wizard_id"], current["id"])
        self.assertEqual(len(unfolded["lines"]), len(payload["lines"]))

    def test_nonowned_session_follows_native_company_rules(self):
        human = self._reports(self.owner).report_client_load("trial_balance", self.filters)
        native = self._reports(self.owner).report_client_load(
            "trial_balance", self.filters, human["wizard_id"],
        )
        delegated = self._reports().report_client_load(
            "trial_balance", self.filters, human["wizard_id"],
        )
        self.assertEqual(native["wizard_id"], delegated["wizard_id"])
        self.assertEqual(
            [row["debit"] for row in native["lines"]],
            [row["debit"] for row in delegated["lines"]],
        )

    def test_foreign_company_and_wrong_session_line(self):
        with self.assertRaises(AccessError):
            self._load(company_id=self.foreign.id, company_ids=self.foreign.ids)
        first = self._load()
        second = self._load()
        with self.assertRaises(UserError):
            self._reports().report_client_toggle_group(first["wizard_id"], second["lines"][0]["id"])

    def test_client_markers_do_not_authorize_generic_session_writes(self):
        native = self._reports(self.owner).report_client_load("trial_balance", self.filters)
        for marker in (True, False, "approved", {}, {"agent_user_id": self.agent.user_id.id}):
            with self.subTest(marker=marker), self.assertRaises(AccessError):
                self._reports().with_context(**{
                    _REPORT_SESSION_CONTEXT_KEY: marker,
                }).browse(native["wizard_id"]).write({"search_text": "forged"})

    def test_session_scope_does_not_authorize_business_or_sudo_writes(self):
        scoped = self._reports()._usl_readonly_report_session()
        for elevated in (False, True):
            target = scoped.env["account.move"].browse(self.move.id)
            if elevated:
                target = target.sudo()
            with self.subTest(elevated=elevated), self.assertRaises(AccessError):
                target.write({"ref": "must not change"})
        self.assertFalse(self.move.ref)

    def test_unrelated_transient_and_nonempty_default_deletion_stay_denied(self):
        scoped = self._reports()._usl_readonly_report_session()
        with self.assertRaises(AccessError):
            scoped.env["res.config.settings"].create({})
        setting = self.env["ir.default"].create({
            "field_id": self.env["ir.model.fields"]._get("res.partner", "name").id,
            "json_value": '"[QA] Kept"',
        })
        with self.assertRaises(AccessError):
            scoped.env["ir.default"].sudo().browse(setting.id).unlink()
        self.assertTrue(setting.exists())

    def test_zero_id_cleanup_is_native_noop_only_inside_session(self):
        scoped = self._reports()._usl_readonly_report_session()
        before = self.env["ir.default"].search_count([])
        self.assertTrue(scoped.env["ir.default"].sudo().browse().unlink())
        self.assertTrue(scoped.env["ir.attachment"].sudo().browse().write({"name": "no rows"}))
        self.assertEqual(self.env["ir.default"].search_count([]), before)
        for context in ({}, {_REPORT_SESSION_CONTEXT_KEY: True}):
            empty = self._reports().env["ir.default"].sudo().browse().with_context(context)
            with self.subTest(context=context), self.assertRaises(AccessError):
                empty.unlink()
        with self.assertRaises(AccessError):
            scoped.env["ir.default"].sudo().browse(999999999).unlink()

    def test_xlsx_download_and_replacement_remain_session_artifacts(self):
        import zipfile
        from io import BytesIO

        payload = self._load()
        descriptor = self._reports().report_client_export(payload["wizard_id"], "xlsx")
        wizard = self.env[descriptor["model"]].browse(descriptor["id"])
        original = wizard.export_file.content
        self.assertIn("xl/workbook.xml", zipfile.ZipFile(BytesIO(original)).namelist())
        attachments = self.env["ir.attachment"].search([
            ("res_model", "=", wizard._name), ("res_id", "=", wizard.id),
            ("res_field", "=", "export_file"),
        ])
        self.assertEqual(len(attachments), 1)
        self._reports().report_client_export(wizard.id, "xlsx", {
            **self.filters, "date_from": "2027-01-01", "date_to": "2027-12-31",
        })
        self.assertNotEqual(wizard.export_file.content, original)
        self.assertEqual(attachments.res_model, wizard._name)
        self.assertEqual(attachments.res_id, wizard.id)
        self.assertEqual(self.move.line_ids.mapped("debit"), [73, 0])

    def test_export_scope_cannot_create_or_relink_business_evidence(self):
        from odoo.tools import BinaryBytes
        from ..models.readonly_accounting_report import _report_session

        payload = self._load()
        scoped = self._reports()._usl_readonly_report_session(payload["wizard_id"])
        _report_session(scoped).exporting = True
        with self.assertRaises(AccessError):
            scoped.env["ir.attachment"].create({
                "name": "[QA] Must not become bill evidence", "raw": BinaryBytes(b"QA"),
                "res_model": "account.move", "res_id": self.move.id,
            })
        self._reports().report_client_export(payload["wizard_id"], "xlsx")
        attachment = self.env["ir.attachment"].search([
            ("res_model", "=", self._reports()._name),
            ("res_id", "=", payload["wizard_id"]), ("res_field", "=", "export_file"),
        ])
        with self.assertRaises(AccessError):
            scoped.env["ir.attachment"].browse(attachment.id).write({
                "res_model": "account.move", "res_id": self.move.id, "res_field": False,
            })
        self.assertEqual(attachment.res_model, self._reports()._name)
        business = self.env["ir.attachment"].create({
            "name": "[QA] Retained receipt", "raw": BinaryBytes(b"retained QA receipt"),
            "res_model": "account.move", "res_id": self.move.id,
        })
        with self.assertRaises(AccessError):
            scoped.env["ir.attachment"].browse(business.id).unlink()
        self.assertEqual(business.raw.content, b"retained QA receipt")
        artifact_before = attachment.raw.content
        with self.assertRaises(AccessError):
            scoped.env["ir.attachment"].browse(business.id).write({
                "res_model": self._reports()._name,
                "res_id": payload["wizard_id"], "res_field": "export_file",
            })
        with self.assertRaises(AccessError):
            scoped.env["ir.attachment"].browse((attachment | business).ids).write({
                "raw": BinaryBytes(b"must not partially replace"),
            })
        self.assertEqual(attachment.raw.content, artifact_before)
        self.assertEqual(business.res_model, "account.move")


    def test_current_owner_role_loss_removes_preview_authority(self):
        self.owner.group_ids = [Command.set(self.env.ref("base.group_user").ids)]
        with self.assertRaises(AccessError):
            self._load()

    def _manager_readonly_agent(self):
        manager = self.env.ref("account.group_account_manager")
        self.owner.group_ids = [Command.link(manager.id)]
        return self.env["usl.agent"].with_user(self.owner).create({
            "name": "[QA] Read-only Manager Reports",
            "purpose": "[QA] Native closing export boundary",
            "owner_id": self.owner.id, "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "delegated_group_ids": [Command.set(manager.ids)],
            "access_mode": "read_only",
        })

    def test_linked_closing_export_preserves_native_business_write_boundary(self):
        readonly_manager = self._manager_readonly_agent()
        closing = self.env["rebuild.account.closing.period"].create({
            "name": "[QA] Closing Boundary", "company_id": self.company.id,
            "period_type": "annual", "date_from": "2026-01-01",
            "date_to": "2026-12-31", "fiscalyear_start": "2026-01-01",
            "fiscalyear_end": "2026-12-31",
        })
        wizard = self._reports(self.owner).create({
            "report_type": "closing_package", "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "date_from": "2026-01-01", "date_to": "2026-12-31",
            "period_preset": "custom", "closing_period_id": closing.id,
        })
        count = self.env["ir.attachment"].search_count([])
        with self.assertRaises(AccessError), self.cr.savepoint():
            self._reports(readonly_manager.user_id).report_client_export(wizard.id, "xlsx")
        self.assertFalse(wizard.export_file)
        self.assertFalse(closing.package_attachment_ids)
        self.assertEqual(self.env["ir.attachment"].search_count([]), count)
        native = self._reports(self.owner).report_client_export(wizard.id, "xlsx")
        self.assertEqual(native["id"], wizard.id)
        self.assertTrue(closing.package_attachment_ids)

    def test_official_fec_keeps_native_format_validation_and_locks(self):
        readonly_manager = self._manager_readonly_agent()
        wizard = self._reports(self.owner).create({
            "report_type": "fec", "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "date_from": "2026-01-01", "date_to": "2026-12-31",
            "period_preset": "custom", "fec_test_mode": False,
            "export_format": "txt",
        })
        before = self.company.read([
            "fiscalyear_lock_date", "hard_lock_date", "tax_lock_date",
            "purchase_lock_date", "sale_lock_date",
        ])
        with self.assertRaisesRegex(UserError, "PDF is disabled by the active"), self.cr.savepoint():
            self._reports(readonly_manager.user_id).report_client_export(wizard.id, "pdf")
        self.assertEqual(wizard.export_format, "txt")
        self.assertFalse(wizard.export_file)
        self.assertEqual(before, self.company.read(list(before[0].keys() - {"id"})))

    def test_mixed_delegation_uses_readonly_accounting_session(self):
        accounting = self.env.ref("account.group_account_readonly")
        project = self.env.ref("project.group_project_manager")
        self.owner.group_ids = [Command.link(project.id)]
        mixed = self.env["usl.agent"].with_user(self.owner).create({
            "name": "[QA] Mixed Report Collaborator",
            "purpose": "[QA] Read-only Accounting and writable Projects",
            "owner_id": self.owner.id, "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "delegated_group_ids": [Command.set((accounting | project).ids)],
            "read_only_group_ids": [Command.set(accounting.ids)],
            "access_mode": "mixed",
        })
        self.env.flush_all()  # Finish native identity/avatar setup before acting as the Agent.
        payload = self._reports(mixed.user_id).report_client_load("trial_balance", self.filters)
        self.assertTrue(payload["lines"])
        with self.assertRaises(AccessError):
            self.move.with_user(mixed.user_id).write({"ref": "not delegated"})
        self.assertFalse(self.move.ref)

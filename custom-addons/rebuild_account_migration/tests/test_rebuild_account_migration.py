import base64
import json

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.addons.rebuild_account_migration.controllers import user_docs
from odoo.tests import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install", "rebuild_account_migration_unit")
class TestRebuildAccountMigration(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.reviewer_group = cls.env.ref("rebuild_account_migration.group_rebuild_accountant_reviewer")
        cls.readonly_group = cls.env.ref("account.group_account_readonly")

    def _journal(self, journal_type="general"):
        journal = self.env["account.journal"].search([
            ("company_id", "=", self.company.id),
            ("type", "=", journal_type),
        ], limit=1)
        if journal:
            return journal
        return self.env["account.journal"].create({
            "name": f"Migration Test {journal_type.title()}",
            "code": f"TM{journal_type[:3].upper()}",
            "type": journal_type,
            "company_id": self.company.id,
        })

    def _account(self, code, name, account_type):
        account = self.env["account.account"].search([
            ("code", "=", code),
            ("company_ids", "in", self.company.id),
        ], limit=1)
        if account:
            return account
        vals = {
            "code": code,
            "name": name,
            "account_type": account_type,
            "company_ids": [Command.set([self.company.id])],
        }
        if account_type in {"asset_receivable", "liability_payable"}:
            vals["reconcile"] = True
        return self.env["account.account"].create(vals)

    def test_accounting_app_opens_dashboard(self):
        menu = self.env.ref("account.menu_finance")
        dashboard_action = self.env.ref("account.open_account_journal_dashboard_kanban")

        self.assertEqual(menu.name, "Accounting")
        self.assertEqual(menu.action, dashboard_action)
        self.assertEqual(dashboard_action.path, "accounting")

    def test_reconcile_shortcut_uses_compatible_kanban_workbench(self):
        action = self.env.ref("rebuild_account_migration.action_rebuild_account_reconcile_bank_transactions")
        reconcile_view = self.env.ref("account_reconcile_oca.bank_statement_line_reconcile_view")
        card_arch = reconcile_view.arch_db.partition("<templates>")[2]

        self.assertEqual(action.view_mode, "kanban,list")
        self.assertEqual(action.view_ids[0].view_id, reconcile_view)
        self.assertIn("'view_ref': 'account_reconcile_oca.bank_statement_line_form_reconcile_view'", action.context)
        self.assertNotIn("<field ", card_arch)
        self.assertIn("record.payment_ref.value", card_arch)

    def test_import_archives_empty_bootstrap_unaffected_earnings_accounts(self):
        company = self.env["res.company"].create({
            "name": "Unit retained earnings company",
            "currency_id": self.company.currency_id.id,
        })
        source_account = self.env["account.account"].create({
            "code": "999999",
            "name": "Source retained earnings",
            "account_type": "equity_unaffected",
            "company_ids": [Command.set([company.id])],
            "active": True,
            "rebuild_source_model": "account.account",
            "rebuild_source_id": 990707,
            "rebuild_source_snapshot": "unit-snapshot",
        })
        bootstrap_account = self.env["account.account"].create({
            "code": "999998",
            "name": "Template retained earnings",
            "account_type": "equity_unaffected",
            "company_ids": [Command.set([company.id])],
            "active": True,
        })
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Retained earnings cleanup",
            "source_snapshot_id": "unit-snapshot",
        })

        import_run._archive_empty_bootstrap_unaffected_earnings_accounts(
            [{
                "id": 990707,
                "account_type": "equity_unaffected",
                "company_ids": [990001],
            }],
            {"source_company_ids": [990001], "source_snapshot_id": "unit-snapshot"},
            {990001: company},
        )

        self.assertTrue(source_account.active)
        self.assertFalse(bootstrap_account.active)
        self.assertIn("source retained-earnings account", bootstrap_account.rebuild_import_note)

    def test_company_report_layout_defaults_do_not_overwrite_existing_layout(self):
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Company layout defaults",
            "source_snapshot_id": "unit-snapshot",
        })
        standard_layout = self.env.ref("web.external_layout_standard")

        missing_layout_company = self.env["res.company"].create({
            "name": "Unit missing layout company",
            "currency_id": self.company.currency_id.id,
            "external_report_layout_id": False,
        })
        existing_layout_company = self.env["res.company"].create({
            "name": "Unit existing layout company",
            "currency_id": self.company.currency_id.id,
            "external_report_layout_id": standard_layout.id,
        })

        self.assertEqual(
            import_run._company_report_layout_defaults(missing_layout_company),
            {"external_report_layout_id": standard_layout.id},
        )
        self.assertEqual(import_run._company_report_layout_defaults(existing_layout_company), {})

    def test_accountant_reviewer_is_read_only_for_discrepancies(self):
        self.assertIn(self.readonly_group, self.reviewer_group.implied_ids)
        reviewer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Migration Reviewer",
            "login": "migration.reviewer@example.invalid",
            "email": "migration.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        discrepancy = self.env["rebuild.account.discrepancy"].sudo().create({
            "name": "Read-only discrepancy",
            "severity": "P1",
            "classification": "missing_capability",
            "status": "open",
        })

        self.assertEqual(
            discrepancy.with_user(reviewer).read(["name"])[0]["name"],
            "Read-only discrepancy",
        )
        with self.assertRaises(AccessError):
            discrepancy.with_user(reviewer).write({"status": "resolved"})
        with self.assertRaises(AccessError):
            self.env["rebuild.account.discrepancy"].with_user(reviewer).create({
                "name": "Reviewer cannot create discrepancies",
                "severity": "P2",
                "classification": "unclassified",
                "status": "open",
            })

        decision = self.env["rebuild.account.review.decision"].with_user(reviewer).create({
            "gate": "discrepancy_acceptance",
            "conclusion": "pending",
            "required_authority": "accountant",
            "discrepancy_id": discrepancy.id,
            "decision_summary": "Reviewer records factual review notes without changing the discrepancy.",
        })
        self.assertEqual(decision.discrepancy_id, discrepancy)
        decision.with_user(reviewer).write({
            "decision_summary": "Reviewer can update the review decision record only.",
        })
        self.assertEqual(decision.decision_summary, "Reviewer can update the review decision record only.")

        external_value = self.env["rebuild.account.external.report.value"].with_user(reviewer).create({
            "name": "Reviewer external VAT value",
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
            "period_key": "USL benchmark 2024-01-10 to 2025-09-30",
            "form_code": "3517-S-SD",
            "field_code": "3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660",
            "value_kind": "accountant_supplied",
            "amount": 1960.00,
            "source_key": "unit-reviewer-external-vat",
            "review_status": "pending_review",
        })
        external_value.with_user(reviewer).write({
            "evidence": "Reviewer can maintain external declaration evidence without editing posted accounting.",
        })
        self.assertEqual(external_value.amount, 1960.00)
        other_company = self.env["res.company"].create({
            "name": "USL Media Unit",
            "currency_id": self.company.currency_id.id,
        })
        self.env["rebuild.account.external.report.value"].sudo().create({
            "name": "Other company external VAT value",
            "company_id": other_company.id,
            "currency_id": self.company.currency_id.id,
            "period_key": "USL Media full posted replay",
            "form_code": "3517-S-SD",
            "field_code": "3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660",
            "value_kind": "accountant_supplied",
            "amount": 42.00,
            "source_key": "unit-reviewer-hidden-external-vat",
            "review_status": "pending_review",
        })
        visible_external_values = self.env["rebuild.account.external.report.value"].with_user(reviewer).search([
            ("source_key", "in", ["unit-reviewer-external-vat", "unit-reviewer-hidden-external-vat"]),
        ])
        self.assertEqual(visible_external_values, external_value)

        move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": self._journal().id,
            "date": fields.Date.today(),
            "company_id": self.company.id,
        })
        accounting_attachment = self.env["ir.attachment"].sudo().create({
            "name": "Accounting evidence.txt",
            "res_model": "account.move",
            "res_id": move.id,
            "type": "binary",
            "raw": b"accounting evidence",
            "company_id": self.company.id,
            "rebuild_source_model": "ir.attachment",
            "rebuild_source_id": 990001,
        })
        self.assertEqual(accounting_attachment.with_user(reviewer).raw, b"accounting evidence")

        private_owner = self.env["ir.config_parameter"].sudo().create({
            "key": "rebuild.account_migration.private_attachment_probe",
            "value": "private review probe",
        })
        private_attachment = self.env["ir.attachment"].sudo().create({
            "name": "Private technical attachment.txt",
            "res_model": "ir.config_parameter",
            "res_id": private_owner.id,
            "type": "binary",
            "raw": b"private technical evidence",
            "public": False,
        })
        with self.assertRaises(AccessError):
            private_attachment.with_user(reviewer).read(["name", "file_size"])

    def test_review_decision_prefill_actions(self):
        discrepancy = self.env["rebuild.account.discrepancy"].create({
            "name": "VAT benchmark difference",
            "severity": "P1",
            "classification": "external_value_difference",
            "status": "open",
            "company_id": self.company.id,
            "period_key": "2024-01-10:2025-09-30",
            "source_value": "1960.00",
            "target_value": "3014.09",
            "difference": "1054.09",
            "recommendation": "Accountant review required.",
        })
        discrepancy_action = discrepancy.action_record_review_decision()
        discrepancy_context = discrepancy_action["context"]

        self.assertEqual(discrepancy_action["res_model"], "rebuild.account.review.decision")
        self.assertEqual(discrepancy_context["default_gate"], "tax_external_value")
        self.assertEqual(discrepancy_context["default_discrepancy_id"], discrepancy.id)
        self.assertEqual(discrepancy_context["default_source_value"], "1960.00")
        self.assertEqual(discrepancy_context["default_target_value"], "3014.09")
        self.assertEqual(discrepancy_context["default_difference"], "1054.09")

        source_report = self.env["rebuild.account.source.report"].create({
            "name": "Balance sheet for associations",
            "source_report_id": 3400,
            "active": True,
            "decision": "REMOVED_AS_UNUSED",
            "decision_basis": "USL is a SASU, not an association.",
            "target_status": "partial_target_equivalent",
            "target_evidence_key": "association_scope_excluded",
            "parity_level": "level_4_evidence_partial",
        })
        report_action = source_report.action_record_review_decision()
        report_context = report_action["context"]

        self.assertEqual(report_action["res_model"], "rebuild.account.review.decision")
        self.assertEqual(report_context["default_gate"], "scope_exclusion")
        self.assertEqual(report_context["default_conclusion"], "not_applicable")
        self.assertEqual(report_context["default_source_report_id"], source_report.id)
        self.assertEqual(report_context["default_evidence_key"], "association_scope_excluded")

        external_value = self.env["rebuild.account.external.report.value"].create({
            "name": "Benchmark VAT value",
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
            "period_key": "USL benchmark 2024-01-10 to 2025-09-30",
            "form_code": "3517-S-SD",
            "form_name": "TVA CA12/CA12E",
            "field_code": "3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660",
            "field_label": "CA12 - TVA déductible sur biens et services",
            "value_kind": "benchmark_acceptance_anchor",
            "amount": 1960.00,
            "source_key": "unit-benchmark-vat",
            "review_status": "pending_review",
            "discrepancy_id": discrepancy.id,
            "evidence": "Unit external benchmark evidence.",
        })
        external_action = external_value.action_record_review_decision()
        external_context = external_action["context"]

        self.assertEqual(external_action["res_model"], "rebuild.account.review.decision")
        self.assertEqual(external_context["default_gate"], "tax_external_value")
        self.assertEqual(external_context["default_external_value_id"], external_value.id)
        self.assertEqual(external_context["default_discrepancy_id"], discrepancy.id)
        self.assertEqual(external_context["default_source_value"], "1960.00")

        decision = self.env["rebuild.account.review.decision"].create({
            "gate": "scope_exclusion",
            "conclusion": "not_applicable",
            "required_authority": "accountant",
            "source_report_id": source_report.id,
            "external_value_id": external_value.id,
            "decision_summary": "Association reports are outside the USL SASU target scope.",
        })
        self.assertEqual(decision.name, "Report review - Balance sheet for associations")
        source_action = decision.action_open_source_report()
        self.assertEqual(source_action["res_model"], "rebuild.account.source.report")
        self.assertEqual(source_action["res_id"], source_report.id)
        external_value_action = decision.action_open_external_value()
        self.assertEqual(external_value_action["res_model"], "rebuild.account.external.report.value")
        self.assertEqual(external_value_action["res_id"], external_value.id)

    def test_recorded_review_decision_updates_linked_evidence(self):
        discrepancy = self.env["rebuild.account.discrepancy"].create({
            "name": "VAT benchmark difference",
            "severity": "P1",
            "classification": "external_value_difference",
            "status": "open",
            "company_id": self.company.id,
            "period_key": "2024-01-10:2025-09-30",
        })
        source_report = self.env["rebuild.account.source.report"].create({
            "name": "Trial Balance",
            "source_report_id": 100,
            "active": True,
            "decision": "MANDATORY_PARITY",
            "target_status": "partial_target_equivalent",
            "target_evidence_key": "trial_balance_2025_09_30",
            "parity_level": "level_4_evidence_partial",
            "parity_gap": "Accountant acceptance pending.",
        })
        external_value = self.env["rebuild.account.external.report.value"].create({
            "name": "Benchmark VAT value",
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
            "period_key": "USL benchmark 2024-01-10 to 2025-09-30",
            "form_code": "3517-S-SD",
            "field_code": "3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660",
            "value_kind": "benchmark_acceptance_anchor",
            "amount": 1960.00,
            "source_key": "unit-review-decision-vat",
            "review_status": "pending_review",
            "discrepancy_id": discrepancy.id,
        })
        pending_decision = self.env["rebuild.account.review.decision"].create({
            "gate": "report_parity",
            "conclusion": "pending",
            "required_authority": "accountant",
            "source_report_id": source_report.id,
            "decision_summary": "The report evidence has been read.",
        })
        with self.assertRaises(UserError):
            pending_decision.action_record()

        decision = self.env["rebuild.account.review.decision"].create({
            "gate": "tax_external_value",
            "conclusion": "accepted_with_difference",
            "required_authority": "accountant",
            "source_report_id": source_report.id,
            "external_value_id": external_value.id,
            "discrepancy_id": discrepancy.id,
            "decision_summary": "Accepted as a declaration-specific value while preserving the imported ledger.",
            "remaining_risk": "The accountant must retain the declaration package evidence.",
        })

        decision.action_record()

        self.assertEqual(decision.state, "recorded")
        self.assertEqual(source_report.parity_level, "level_4_accepted")
        self.assertEqual(source_report.latest_evidence_status, "recorded_review_decision:accepted_with_difference")
        self.assertIn("declaration-specific value", source_report.note)
        self.assertEqual(external_value.review_status, "accepted_with_difference")
        self.assertEqual(external_value.decision, decision.decision_summary)
        self.assertEqual(discrepancy.status, "accepted")
        self.assertEqual(discrepancy.decision, decision.decision_summary)
        self.assertEqual(discrepancy.approver, self.env.user.name)
        with self.assertRaises(UserError):
            decision.write({"decision_summary": "Recorded decisions cannot be edited in place."})
        decision.action_supersede()
        self.assertEqual(decision.state, "superseded")
        with self.assertRaises(UserError):
            decision.write({"conclusion": "rejected"})

    def test_reviewer_record_action_updates_discrepancy_without_direct_write(self):
        reviewer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Decision Reviewer",
            "login": "decision.reviewer@example.invalid",
            "email": "decision.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        discrepancy = self.env["rebuild.account.discrepancy"].sudo().create({
            "name": "Scope difference",
            "severity": "P2",
            "classification": "period_or_scope_difference",
            "status": "open",
            "company_id": self.company.id,
        })
        decision = self.env["rebuild.account.review.decision"].with_user(reviewer).create({
            "gate": "discrepancy_acceptance",
            "conclusion": "accepted",
            "required_authority": "accountant",
            "company_id": self.company.id,
            "discrepancy_id": discrepancy.id,
            "decision_summary": "Accepted because the excluded source records have no posted accounting effect.",
        })

        with self.assertRaises(AccessError):
            discrepancy.with_user(reviewer).write({"status": "accepted"})

        decision.with_user(reviewer).action_record()

        self.assertEqual(discrepancy.status, "accepted")
        self.assertEqual(discrepancy.approver, "Decision Reviewer")
        self.assertEqual(decision.state, "recorded")

    def test_report_export_metadata_and_empty_csv(self):
        wizard = self.env["rebuild.account.report.export.wizard"].create({
            "company_id": self.company.id,
            "report_type": "trial_balance",
            "date_from": "2099-01-01",
            "date_to": "2099-12-31",
            "target_move": "posted",
            "export_format": "csv",
        })

        action = wizard.action_generate_export()

        self.assertEqual(action["res_model"], "rebuild.account.report.export.wizard")
        self.assertEqual(action["res_id"], wizard.id)
        metadata = json.loads(wizard.export_metadata)
        self.assertEqual(metadata["report_type"], "trial_balance")
        self.assertEqual(metadata["report_name"], "Trial Balance")
        self.assertEqual(metadata["date_from"], "2099-01-01")
        self.assertEqual(metadata["date_to"], "2099-12-31")
        self.assertEqual(metadata["target_move"], "posted")
        self.assertEqual(metadata["format"], "csv")
        self.assertEqual(action["name"], "Trial Balance Export")
        payload = base64.b64decode(wizard.export_file).decode("utf-8")
        self.assertIn("metadata", payload)
        self.assertIn("empty_report", payload)

    def test_report_preview_metadata_and_empty_line(self):
        wizard = self.env["rebuild.account.report.export.wizard"].create({
            "company_id": self.company.id,
            "report_type": "trial_balance",
            "date_from": "2099-01-01",
            "date_to": "2099-12-31",
            "target_move": "posted",
            "export_format": "csv",
            "preview_limit": 10,
        })

        action = wizard.action_preview_report()

        self.assertEqual(action["res_model"], "rebuild.account.report.export.wizard")
        self.assertEqual(action["res_id"], wizard.id)
        self.assertEqual(action["name"], "Trial Balance Preview")
        self.assertEqual(wizard.preview_row_count, 0)
        self.assertFalse(wizard.preview_truncated)
        self.assertEqual(len(wizard.preview_line_ids), 1)
        self.assertEqual(wizard.preview_line_ids.label, "No rows for the selected report filters")
        metadata = json.loads(wizard.preview_metadata)
        self.assertEqual(metadata["report_type"], "trial_balance")
        self.assertEqual(metadata["report_name"], "Trial Balance")
        self.assertEqual(metadata["row_count"], 0)
        self.assertEqual(metadata["preview_limit"], 10)
        self.assertFalse(metadata["preview_truncated"])

        source_action = wizard.preview_line_ids.action_open_sources()
        self.assertEqual(source_action["type"], "ir.actions.act_window")
        self.assertEqual(source_action["res_model"], "account.move.line")
        self.assertIn(("company_id", "=", self.company.id), source_action["domain"])
        self.assertIn(("move_id.date", ">=", fields.Date.from_string("2099-01-01")), source_action["domain"])
        self.assertEqual(source_action["context"]["create"], False)
        self.assertEqual(source_action["context"]["delete"], False)

        preview_line = self.env["rebuild.account.report.preview.line"].create({
            "wizard_id": wizard.id,
            "sequence": 99,
            "label": "Specific imported line",
            "account_code": "401000",
            "row_json": json.dumps({
                "source_line_id": "900",
                "source_account_id": "123",
                "account_code": "401000",
            }),
        })
        line_action = preview_line.action_open_sources()
        self.assertEqual(line_action["res_model"], "account.move.line")
        self.assertIn(("rebuild_source_id", "=", 900), line_action["domain"])
        self.assertTrue([
            term
            for term in line_action["domain"]
            if term[0] == "account_id" and term[1] == "in"
        ])

    def test_report_launcher_actions_preselect_expected_report_types(self):
        expected_actions = {
            "action_rebuild_account_report_export_trial_balance": ("Trial Balance", "trial_balance", "xlsx"),
            "action_rebuild_account_report_export_general_ledger": ("General Ledger", "general_ledger", "xlsx"),
            "action_rebuild_account_report_export_partner_ledger": ("Partner Ledger", "partner_ledger", "xlsx"),
            "action_rebuild_account_report_export_customer_statement": ("Customer Statement", "customer_statement", "xlsx"),
            "action_rebuild_account_report_export_balance_sheet": ("Balance Sheet", "balance_sheet", "pdf"),
            "action_rebuild_account_report_export_tax_report": ("VAT and Tax Report", "tax_report", "xlsx"),
            "action_rebuild_account_report_export_tax_group_account_tax": ("Tax Report by Account then Tax", "tax_report_group_account_tax", "xlsx"),
            "action_rebuild_account_report_export_tax_group_tax_account": ("Tax Report by Tax then Account", "tax_report_group_tax_account", "xlsx"),
            "action_rebuild_account_report_export_french_tax_package": ("French Tax Package and CA12 Mapping", "french_tax_package", "pdf"),
            "action_rebuild_account_report_export_fec": ("FEC", "fec", "txt"),
            "action_rebuild_account_report_export_french_balance_sheet_2024": ("French Balance Sheet (2024 PCG)", "french_balance_sheet_2024", "pdf"),
            "action_rebuild_account_report_export_french_profit_loss_2024": ("French Profit and Loss (2024 PCG)", "french_profit_loss_2024", "pdf"),
            "action_rebuild_account_report_export_sig_caf_2024": ("SIG and CAF (2024 PCG)", "sig_caf_2024", "pdf"),
            "action_rebuild_account_report_export_fixed_assets": ("Fixed Asset Register", "fixed_assets", "pdf"),
            "action_rebuild_account_report_export_fixed_asset_group_account": ("Fixed Asset Register by Account", "fixed_asset_group_account", "xlsx"),
        }
        for xmlid, (name, report_type, export_format) in expected_actions.items():
            action = self.env.ref(f"rebuild_account_migration.{xmlid}")
            context = safe_eval(action.context or "{}")
            self.assertEqual(action.name, name)
            self.assertEqual(action.res_model, "rebuild.account.report.export.wizard")
            self.assertEqual(action.target, "new")
            self.assertEqual(context["default_report_type"], report_type)
            self.assertEqual(context["default_export_format"], export_format)

    def test_interactive_oca_report_actions_open_on_benchmark_period(self):
        expected_actions = {
            "account_financial_report.action_trial_balance_wizard": ("default_date_to", "default_target_move"),
            "account_financial_report.action_general_ledger_wizard": ("default_date_to", "default_target_move"),
            "account_financial_report.action_journal_ledger_wizard": ("default_date_to", "default_move_target"),
            "account_financial_report.action_vat_report_wizard": ("default_date_to", "default_target_move"),
            "account_financial_report.action_open_items_wizard": ("default_date_at", "default_target_move"),
            "account_financial_report.action_aged_partner_balance_wizard": ("default_date_at", "default_target_move"),
        }
        for xmlid, (closing_date_key, move_key) in expected_actions.items():
            action = self.env.ref(xmlid)
            context = safe_eval(action.context or "{}")

            self.assertEqual(context["default_date_from"], "2024-01-10")
            self.assertEqual(context[closing_date_key], "2025-09-30")
            self.assertEqual(context[move_key], "posted")

    def test_interactive_oca_report_wizards_default_to_benchmark_period(self):
        receivable = self._account("411900", "Unit receivable report default", "asset_receivable")
        payable = self._account("401900", "Unit payable report default", "liability_payable")

        period_wizards = [
            "trial.balance.report.wizard",
            "general.ledger.report.wizard",
            "vat.report.wizard",
        ]
        for model_name in period_wizards:
            values = self.env[model_name].default_get(["date_from", "date_to", "target_move"])
            self.assertEqual(str(values["date_from"]), "2024-01-10")
            self.assertEqual(str(values["date_to"]), "2025-09-30")
            self.assertEqual(values["target_move"], "posted")

        journal_values = self.env["journal.ledger.report.wizard"].default_get([
            "date_from",
            "date_to",
            "move_target",
        ])
        self.assertEqual(str(journal_values["date_from"]), "2024-01-10")
        self.assertEqual(str(journal_values["date_to"]), "2025-09-30")
        self.assertEqual(journal_values["move_target"], "posted")

        for model_name in ["open.items.report.wizard", "aged.partner.balance.report.wizard"]:
            values = self.env[model_name].default_get([
                "date_from",
                "date_at",
                "target_move",
                "account_ids",
            ])
            self.assertEqual(str(values["date_from"]), "2024-01-10")
            self.assertEqual(str(values["date_at"]), "2025-09-30")
            self.assertEqual(values["target_move"], "posted")
            self.assertIn(receivable.id, values["account_ids"][0][2])
            self.assertIn(payable.id, values["account_ids"][0][2])

    def test_empty_date_range_onchange_keeps_benchmark_dates(self):
        wizard = self.env["trial.balance.report.wizard"].create({})
        wizard.date_from = "2024-01-10"
        wizard.date_to = "2025-09-30"
        wizard.date_range_id = False

        wizard.onchange_date_range_id()

        self.assertEqual(str(wizard.date_from), "2024-01-10")
        self.assertEqual(str(wizard.date_to), "2025-09-30")

    def test_user_guide_action_and_markdown_renderer_are_available(self):
        action = self.env.ref("rebuild_account_migration.action_rebuild_account_user_guide")
        self.assertEqual(action.type, "ir.actions.act_url")
        self.assertEqual(action.url, "/usl/user-docs")
        self.assertEqual(action.target, "self")

        rendered = user_docs.render_markdown(
            "# Guide\n\nOpen [reports](how-to/generate-accounting-reports.md).\n\n| A | B |\n| --- | --- |\n| `one` | two |\n",
            "README.md",
        )
        self.assertIn("<h1", rendered)
        self.assertIn("/usl/user-docs/how-to/generate-accounting-reports.md", rendered)
        self.assertIn("<table>", rendered)
        self.assertIn("<code>one</code>", rendered)

    def test_source_report_parity_levels_are_explicit(self):
        mandatory = self.env["rebuild.account.source.report"].create({
            "name": "Trial Balance",
            "source_report_id": 100,
            "decision": "MANDATORY_PARITY",
            "target_status": "partial_target_equivalent",
            "target_action_xmlid": "rebuild_account_migration.action_rebuild_account_report_export_trial_balance",
            "target_evidence_key": "trial_balance",
            "parity_level": "level_3_semantic_partial",
            "latest_evidence_status": "technical_controls_passed_accountant_acceptance_pending",
            "parity_gap": "Line-by-line comparison and accountant acceptance pending.",
            "latest_evidence_json": {"target_evidence_key": "trial_balance", "status": "passed"},
        })
        missing = self.env["rebuild.account.source.report"].create({
            "name": "Missing Source Report",
            "source_report_id": 101,
            "decision": "MANDATORY_PARITY",
            "target_status": "missing_target_equivalent",
            "parity_level": "level_0_unmapped",
            "latest_evidence_status": "missing_target_equivalent",
            "parity_gap": "No target report equivalent is assigned.",
        })

        self.assertEqual(mandatory.parity_level, "level_3_semantic_partial")
        self.assertEqual(mandatory.target_evidence_key, "trial_balance")
        self.assertEqual(mandatory.latest_evidence_json["status"], "passed")
        self.assertIn("accountant", mandatory.latest_evidence_status)
        self.assertEqual(missing.parity_level, "level_0_unmapped")
        self.assertIn("No target", missing.parity_gap)

    def test_source_report_target_evidence_key_keeps_variants_explicit(self):
        helper = self.env["rebuild.account.import.run"]

        self.assertEqual(
            helper._source_report_target_evidence_key({
                "source_name": "Trial Balance",
                "localized_name": "Balance comptable",
                "country_code": "",
            }),
            "trial_balance",
        )
        self.assertEqual(
            helper._source_report_target_evidence_key({
                "source_name": "Balance sheet (2024)",
                "localized_name": "Bilan comptable (2024)",
                "country_code": "FR",
            }),
            "french_balance_sheet_2024",
        )
        self.assertEqual(
            helper._source_report_target_evidence_key({
                "source_name": "Balance sheet for associations",
                "localized_name": "Bilan comptable pour associations",
                "country_code": "FR",
            }),
            "association_scope_excluded",
        )
        self.assertEqual(
            helper._source_report_decision({
                "source_name": "Profit and loss account for associations",
                "localized_name": "Compte de résultats pour associations",
            }),
            "REMOVED_AS_UNUSED",
        )

    def test_review_summary_surfaces_blockers_and_user_actions(self):
        summary_company = self.env["res.company"].create({
            "name": "Unit Review Summary Company",
            "currency_id": self.company.currency_id.id,
            "rebuild_source_model": "res.company",
            "rebuild_source_id": 990001,
        })
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Unit import run",
            "status": "partial",
            "source_snapshot_id": "unit-snapshot",
            "source_dump_sha256": "abc123",
            "target_database": "unit-target",
            "company_ids": [Command.set([summary_company.id])],
        })
        self.env["rebuild.account.discrepancy"].sudo().create({
            "name": "Unit P0 blocker",
            "severity": "P0",
            "classification": "missing_capability",
            "status": "open",
            "company_id": summary_company.id,
            "import_run_id": import_run.id,
        })
        self.env["rebuild.account.source.report"].create({
            "name": "Trial Balance",
            "source_report_id": 990001,
            "active": True,
            "decision": "MANDATORY_PARITY",
            "target_status": "partial_target_equivalent",
            "target_action_xmlid": "rebuild_account_migration.action_rebuild_account_report_export_trial_balance",
            "parity_level": "level_3_semantic_partial",
        })
        self.env["rebuild.account.review.decision"].create({
            "name": "Unit pending review decision",
            "gate": "report_parity",
            "conclusion": "pending",
            "required_authority": "accountant",
            "company_id": summary_company.id,
            "period_key": "USL benchmark 2024-01-10 to 2025-09-30",
            "decision_summary": "Pending unit review.",
        })
        self.env["rebuild.account.external.report.value"].create({
            "name": "Unit external VAT value",
            "company_id": summary_company.id,
            "currency_id": summary_company.currency_id.id,
            "period_key": "USL benchmark 2024-01-10 to 2025-09-30",
            "form_code": "3517-S-SD",
            "field_code": "3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660",
            "value_kind": "benchmark_acceptance_anchor",
            "amount": 1960.00,
            "source_key": "unit-summary-vat",
            "review_status": "pending_review",
        })
        self.env.flush_all()

        summary = self.env["rebuild.account.review.summary"].search([
            ("company_id", "=", summary_company.id),
        ], limit=1)

        self.assertTrue(summary)
        self.assertEqual(summary.source_company_id, 990001)
        self.assertEqual(summary.latest_import_run_id, import_run)
        self.assertGreaterEqual(summary.open_p0_count, 1)
        self.assertEqual(summary.readiness_status, "blocked")
        self.assertGreaterEqual(summary.review_decision_count, 1)
        self.assertGreaterEqual(summary.pending_review_decision_count, 1)
        self.assertEqual(summary.recorded_review_decision_count, 0)
        self.assertEqual(summary.external_report_value_count, 1)
        self.assertEqual(summary.pending_external_report_value_count, 1)
        self.assertGreaterEqual(summary.source_report_count, 1)
        self.assertGreaterEqual(summary.mandatory_report_count, 1)
        self.assertGreaterEqual(summary.level_3_report_count, 1)
        self.assertGreaterEqual(summary.level_4_report_count, 0)

        discrepancy_action = summary.action_open_open_discrepancies()
        self.assertEqual(discrepancy_action["res_model"], "rebuild.account.discrepancy")
        self.assertIn(("company_id", "=", summary_company.id), discrepancy_action["domain"])
        self.assertEqual(discrepancy_action["context"]["create"], False)

        decision_action = summary.action_open_review_decisions()
        self.assertEqual(decision_action["res_model"], "rebuild.account.review.decision")
        self.assertIn(("company_id", "=", summary_company.id), decision_action["domain"])
        self.assertEqual(decision_action["context"]["default_company_id"], summary_company.id)
        self.assertEqual(decision_action["context"]["delete"], False)

        external_value_action = summary.action_open_external_report_values()
        self.assertEqual(external_value_action["res_model"], "rebuild.account.external.report.value")
        self.assertIn(("company_id", "=", summary_company.id), external_value_action["domain"])
        self.assertEqual(external_value_action["context"]["default_company_id"], summary_company.id)
        self.assertEqual(external_value_action["context"]["delete"], False)

        journal_action = summary.action_open_imported_journal_items()
        self.assertEqual(journal_action["res_model"], "account.move.line")
        self.assertIn(("company_id", "=", summary_company.id), journal_action["domain"])

        report_action = summary.action_open_report_export_wizard()
        self.assertEqual(report_action["res_model"], "rebuild.account.report.export.wizard")
        self.assertEqual(report_action["context"]["default_company_id"], summary_company.id)
        self.assertEqual(report_action["context"]["default_report_type"], "trial_balance")

        guide_action = summary.action_open_user_guide()
        self.assertEqual(guide_action["type"], "ir.actions.act_url")
        self.assertEqual(guide_action["url"], "/usl/user-docs")

    def test_import_run_discrepancy_upsert_resolves_duplicates(self):
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Unit import run",
            "status": "partial",
        })
        first = import_run._upsert_discrepancy({
            "name": "Stable discrepancy",
            "severity": "P1",
            "classification": "missing_capability",
            "status": "open",
            "period_key": "2024-01-10:open",
            "source_model": "account.move",
            "source_value": "1",
            "target_value": "1",
        })
        duplicate = self.env["rebuild.account.discrepancy"].create({
            "name": "Stable discrepancy",
            "severity": "P1",
            "classification": "missing_capability",
            "status": "open",
            "period_key": "2024-01-10:open",
            "source_model": "account.move",
            "source_value": "1",
            "target_value": "1",
        })

        updated = import_run._upsert_discrepancy({
            "name": "Stable discrepancy",
            "severity": "P1",
            "classification": "period_or_scope_difference",
            "status": "open",
            "period_key": "2024-01-10:open",
            "source_model": "account.move",
            "source_value": "2",
            "target_value": "2",
        })

        self.assertEqual(updated, first)
        self.assertEqual(first.classification, "period_or_scope_difference")
        self.assertEqual(first.source_value, "2")
        self.assertEqual(duplicate.status, "resolved")
        self.assertIn("Superseded", duplicate.decision)

    def test_import_run_external_value_upsert_is_idempotent(self):
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Unit import run",
            "status": "partial",
        })
        vals = {
            "name": "Unit benchmark VAT",
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
            "period_key": "USL benchmark 2024-01-10 to 2025-09-30",
            "form_code": "3517-S-SD",
            "field_code": "3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660",
            "value_kind": "benchmark_acceptance_anchor",
            "amount": 1960.00,
            "source_key": "unit-external-value-upsert",
            "review_status": "pending_review",
            "evidence": "Initial evidence.",
        }

        first = import_run._upsert_external_report_value(vals)
        updated = import_run._upsert_external_report_value({
            **vals,
            "amount": 1959.50,
            "evidence": "Updated evidence.",
        })

        self.assertEqual(updated, first)
        self.assertEqual(first.amount, 1959.50)
        self.assertEqual(first.evidence, "Updated evidence.")
        self.assertEqual(
            self.env["rebuild.account.external.report.value"].search_count([
                ("source_key", "=", "unit-external-value-upsert"),
            ]),
            1,
        )

    def test_report_export_rejects_invalid_statutory_scopes(self):
        journal = self._journal()
        fec_wizard = self.env["rebuild.account.report.export.wizard"].create({
            "company_id": self.company.id,
            "report_type": "fec",
            "date_from": "2024-01-10",
            "date_to": "2025-09-30",
            "target_move": "posted",
            "export_format": "csv",
        })
        with self.assertRaisesRegex(UserError, "FEC exports must use the FEC TXT format"):
            fec_wizard.action_generate_export()

        fec_wizard.write({"export_format": "txt", "target_move": "all"})
        with self.assertRaisesRegex(UserError, "Official FEC generation uses posted entries only"):
            fec_wizard.action_generate_export()

        fec_wizard.write({
            "target_move": "posted",
            "journal_ids": [Command.set([journal.id])],
        })
        with self.assertRaisesRegex(UserError, "FEC exports cannot be filtered"):
            fec_wizard.action_generate_export()

        tax_wizard = self.env["rebuild.account.report.export.wizard"].create({
            "company_id": self.company.id,
            "report_type": "french_tax_package",
            "date_from": "2024-01-10",
            "date_to": "2025-09-30",
            "target_move": "posted",
            "export_format": "csv",
            "journal_ids": [Command.set([journal.id])],
        })
        with self.assertRaisesRegex(UserError, "statutory benchmark mapping"):
            tax_wizard.action_generate_export()

        fec_wizard.write({"export_format": "txt", "target_move": "posted", "journal_ids": [Command.clear()]})
        with self.assertRaisesRegex(UserError, "Use Generate Export"):
            fec_wizard.action_preview_report()

    def test_accountant_reviewer_can_generate_fec_through_custom_export_only(self):
        reviewer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "FEC Reviewer",
            "login": "fec.reviewer@example.invalid",
            "email": "fec.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })

        with self.assertRaises(AccessError):
            self.env["l10n_fr.fec.export.wizard"].with_user(reviewer).create({
                "date_from": "2099-01-01",
                "date_to": "2099-12-31",
                "test_file": True,
                "export_type": "official",
            })

        fec_wizard = self.env["rebuild.account.report.export.wizard"].with_user(reviewer).create({
            "company_id": self.company.id,
            "report_type": "fec",
            "date_from": "2099-01-01",
            "date_to": "2099-12-31",
            "target_move": "posted",
            "export_format": "txt",
            "fec_test_mode": True,
        })
        action = fec_wizard.action_generate_export()

        self.assertEqual(action["res_model"], "rebuild.account.report.export.wizard")
        self.assertEqual(action["res_id"], fec_wizard.id)
        self.assertTrue(fec_wizard.export_file)
        self.assertTrue(fec_wizard.export_filename.endswith(".txt"))
        metadata = json.loads(fec_wizard.export_metadata)
        self.assertEqual(metadata["report_type"], "fec")
        self.assertEqual(metadata["format"], "txt")
        self.assertEqual(metadata["validation"], "not_official_dgfip_validation")

        fec_wizard.write({"fec_test_mode": False})
        with self.assertRaisesRegex(UserError, "Only an Accounting Manager"):
            fec_wizard.action_generate_export()

    def test_reconciliation_review_action_uses_only_source_traced_endpoints(self):
        expense_account = self._account("T600003", "Generated Endpoint Expense", "expense")
        payable_account = self._account("T401003", "Generated Endpoint Payable", "liability_payable")
        generated_move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": self._journal().id,
            "date": fields.Date.from_string("2025-10-31"),
            "company_id": self.company.id,
            "rebuild_source_model": "account.move.document_regeneration",
            "rebuild_source_id": 99,
            "rebuild_source_snapshot": "test-snapshot",
            "line_ids": [
                Command.create({
                    "name": "Generated missing endpoint",
                    "account_id": payable_account.id,
                    "credit": 12.34,
                    "rebuild_source_model": "account.move.line.document_regeneration",
                    "rebuild_source_id": 11,
                    "rebuild_source_snapshot": "test-snapshot",
                }),
                Command.create({
                    "name": "Generated counterpart",
                    "account_id": expense_account.id,
                    "debit": 12.34,
                    "rebuild_source_model": "account.move.line.document_regeneration",
                    "rebuild_source_id": 12,
                    "rebuild_source_snapshot": "test-snapshot",
                }),
            ],
        })
        review = self.env["rebuild.account.reconciliation.review"].create({
            "name": "Cross-boundary reconciliation",
            "reconciliation_kind": "partial",
            "company_id": self.company.id,
            "source_partial_reconcile_id": 42,
            "source_debit_line_id": 10,
            "source_credit_line_id": 11,
            "imported_source_line_ids": "10",
            "missing_source_line_ids": "11",
            "missing_source_move_ids": "99",
            "missing_source_move_states": "draft",
            "missing_source_move_dates": "2025-10-31",
            "generated_missing_line_count": 1,
            "generated_missing_source_line_ids": "11",
            "missing_endpoint_coverage": "all_generated_draft",
            "amount": 12.34,
            "max_date": "2025-10-31",
            "rebuild_source_model": "account.partial.reconcile",
            "rebuild_source_id": 42,
            "rebuild_source_snapshot": "test-snapshot",
        })

        action = review.action_open_imported_journal_items()

        self.assertEqual(action["res_model"], "account.move.line")
        self.assertEqual(action["context"]["create"], False)
        self.assertEqual(action["context"]["delete"], False)
        self.assertIn(("rebuild_source_snapshot", "=", "test-snapshot"), action["domain"])
        self.assertIn(("rebuild_source_id", "in", [10, 11]), action["domain"])

        generated_action = review.action_open_generated_missing_endpoint_items()

        self.assertEqual(generated_action["res_model"], "account.move.line")
        self.assertEqual(generated_action["context"]["create"], False)
        self.assertEqual(generated_action["context"]["delete"], False)
        self.assertIn(("rebuild_source_model", "=", "account.move.line.document_regeneration"), generated_action["domain"])
        self.assertIn(("rebuild_source_snapshot", "=", "test-snapshot"), generated_action["domain"])
        self.assertIn(("rebuild_source_id", "in", [11]), generated_action["domain"])
        self.assertEqual(generated_move.state, "draft")

    def test_reconciliation_review_native_partial_requires_recorded_decision(self):
        payable_account = self._account("T401004", "Native Boundary Payable", "liability_payable")
        expense_account = self._account("T600004", "Native Boundary Expense", "expense")
        bank_account = self._account("T512004", "Native Boundary Bank", "asset_cash")
        journal = self._journal()
        posted_move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": journal.id,
            "date": fields.Date.from_string("2025-09-30"),
            "company_id": self.company.id,
            "line_ids": [
                Command.create({
                    "name": "Imported payable endpoint",
                    "account_id": payable_account.id,
                    "debit": 100.00,
                    "rebuild_source_model": "account.move.line",
                    "rebuild_source_id": 210,
                    "rebuild_source_snapshot": "test-snapshot",
                }),
                Command.create({
                    "name": "Imported counterpart",
                    "account_id": bank_account.id,
                    "credit": 100.00,
                }),
            ],
        })
        posted_move.action_post()
        generated_move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": journal.id,
            "date": fields.Date.from_string("2025-10-31"),
            "company_id": self.company.id,
            "line_ids": [
                Command.create({
                    "name": "Generated draft endpoint",
                    "account_id": payable_account.id,
                    "credit": 100.00,
                    "rebuild_source_model": "account.move.line.document_regeneration",
                    "rebuild_source_id": 211,
                    "rebuild_source_snapshot": "test-snapshot",
                }),
                Command.create({
                    "name": "Generated draft counterpart",
                    "account_id": expense_account.id,
                    "debit": 100.00,
                }),
            ],
        })
        debit_line = posted_move.line_ids.filtered(lambda line: line.account_id == payable_account)
        credit_line = generated_move.line_ids.filtered(lambda line: line.account_id == payable_account)
        review = self.env["rebuild.account.reconciliation.review"].create({
            "name": "Native partial boundary reconciliation",
            "reconciliation_kind": "partial",
            "review_status": "represented_review_only",
            "company_id": self.company.id,
            "source_partial_reconcile_id": 4200,
            "source_debit_line_id": 210,
            "source_credit_line_id": 211,
            "source_debit_move_state": "posted",
            "source_credit_move_state": "draft",
            "imported_source_line_ids": "210",
            "missing_source_line_ids": "211",
            "generated_missing_line_count": 1,
            "generated_missing_source_line_ids": "211",
            "missing_endpoint_coverage": "all_generated_draft",
            "amount": 40.00,
            "debit_amount_currency": 40.00,
            "credit_amount_currency": 40.00,
            "max_date": "2025-10-31",
            "rebuild_source_database": "unit-source",
            "rebuild_source_model": "account.partial.reconcile",
            "rebuild_source_id": 4200,
            "rebuild_source_snapshot": "test-snapshot",
        })

        preview_action = review.action_preview_native_partial_reconciliation()
        self.assertEqual(preview_action["res_model"], "account.move.line")
        self.assertIn(("id", "in", [debit_line.id, credit_line.id]), preview_action["domain"])
        decision_action = review.action_record_review_decision()
        self.assertEqual(decision_action["res_model"], "rebuild.account.review.decision")
        self.assertEqual(decision_action["context"]["default_reconciliation_review_id"], review.id)
        self.assertEqual(decision_action["context"]["default_evidence_key"], "source_partial_reconcile:4200")

        reviewer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Boundary Reviewer",
            "login": "boundary.reviewer@example.invalid",
            "email": "boundary.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        with self.assertRaisesRegex(UserError, "Only an Accounting Manager"):
            review.with_user(reviewer).action_apply_native_partial_reconciliation()
        with self.assertRaisesRegex(UserError, "Record an accepted review decision"):
            review.action_apply_native_partial_reconciliation()

        decision = self.env["rebuild.account.review.decision"].create({
            "gate": "discrepancy_acceptance",
            "conclusion": "accepted_with_difference",
            "required_authority": "accountant",
            "company_id": self.company.id,
            "reconciliation_review_id": review.id,
            "decision_summary": "Accept native application for this rollback-free unit boundary case.",
        })
        decision.action_record()

        apply_action = review.action_apply_native_partial_reconciliation()
        partial = self.env["account.partial.reconcile"].search([
            ("rebuild_source_model", "=", "account.partial.reconcile"),
            ("rebuild_source_id", "=", 4200),
            ("rebuild_source_snapshot", "=", "test-snapshot"),
        ])
        self.assertEqual(len(partial), 1)
        self.assertEqual(partial.amount, 40.00)
        self.assertEqual(partial.debit_move_id, debit_line)
        self.assertEqual(partial.credit_move_id, credit_line)
        self.assertEqual(review.review_status, "native_reconciliation_applied")
        self.assertIn(("id", "=", partial.id), apply_action["domain"])

        second_action = review.action_apply_native_partial_reconciliation()
        self.assertEqual(self.env["account.partial.reconcile"].search_count([
            ("rebuild_source_model", "=", "account.partial.reconcile"),
            ("rebuild_source_id", "=", 4200),
            ("rebuild_source_snapshot", "=", "test-snapshot"),
        ]), 1)
        self.assertEqual(second_action["domain"], [("id", "=", partial.id)])

    def test_move_line_review_preserves_non_posted_source_amounts(self):
        move_review = self.env["rebuild.account.move.review"].create({
            "name": "Draft source move",
            "source_move_id": 500,
            "source_state": "draft",
            "state": "draft",
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
            "date": fields.Date.from_string("2025-10-31"),
            "move_type": "entry",
            "source_line_count": 1,
            "source_accounting_line_count": 1,
        })
        line_review = self.env["rebuild.account.move.line.review"].create({
            "name": "Draft source line",
            "source_move_line_id": 900,
            "source_move_id": 500,
            "imported_move_review_id": move_review.id,
            "company_id": self.company.id,
            "line_currency_id": self.company.currency_id.id,
            "date": fields.Date.from_string("2025-10-31"),
            "source_move_state": "draft",
            "source_move_type": "entry",
            "sequence": 1,
            "display_type": "product",
            "label": "Draft line kept for review",
            "accounting_effect": "none_non_posted_source_line",
            "review_status": "review_required",
            "debit": 100.00,
            "credit": 0.00,
            "balance": 100.00,
            "amount_currency": 100.00,
            "source_account_id": 123,
            "source_tax_ids": "1,2",
            "source_tax_tag_ids": "7",
            "rebuild_source_model": "account.move.line",
            "rebuild_source_id": 900,
            "rebuild_source_snapshot": "test-snapshot",
        })

        self.assertEqual(line_review.imported_move_review_id, move_review)
        self.assertFalse(line_review.imported_move_id)
        self.assertEqual(line_review.accounting_effect, "none_non_posted_source_line")
        self.assertEqual(line_review.source_move_state, "draft")
        self.assertEqual(line_review.debit, 100.00)
        self.assertEqual(line_review.source_tax_ids, "1,2")
        self.assertEqual(move_review.move_line_review_count, 1)

        action = move_review.action_open_source_line_reviews()

        self.assertEqual(action["res_model"], "rebuild.account.move.line.review")
        self.assertIn(("imported_move_review_id", "=", move_review.id), action["domain"])
        self.assertEqual(action["context"]["create"], False)
        self.assertEqual(action["context"]["delete"], False)
        self.assertEqual(action["context"]["search_default_non_posted"], 1)

    def test_document_regeneration_cases_classify_non_posted_source_moves(self):
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Document regeneration case sync",
            "source_snapshot_id": "test-snapshot",
        })
        expense_account = self._account("T600001", "Migration Test Expense", "expense")
        payable_account = self._account("T401001", "Migration Test Payable", "liability_payable")
        draft_review = self.env["rebuild.account.move.review"].create({
            "name": "Draft vendor bill",
            "source_name": "BILL/DRAFT/001",
            "source_move_id": 501,
            "source_state": "draft",
            "state": "draft",
            "company_id": self.company.id,
            "journal_id": self._journal("purchase").id,
            "currency_id": self.company.currency_id.id,
            "date": fields.Date.from_string("2025-11-15"),
            "move_type": "in_invoice",
            "source_line_count": 2,
            "source_accounting_line_count": 2,
            "amount_total_signed": -120.00,
            "amount_residual_signed": -120.00,
            "source_line_debit_total": 120.00,
            "source_line_credit_total": 120.00,
            "source_line_balance_total": 0.00,
            "rebuild_source_model": "account.move",
            "rebuild_source_id": 501,
            "rebuild_source_snapshot": "test-snapshot",
        })
        self.env["rebuild.account.move.line.review"].create({
            "name": "Draft vendor bill line",
            "source_move_line_id": 901,
            "source_move_id": 501,
            "imported_move_review_id": draft_review.id,
            "company_id": self.company.id,
            "line_currency_id": self.company.currency_id.id,
            "date": fields.Date.from_string("2025-11-15"),
            "source_move_state": "draft",
            "source_move_type": "in_invoice",
            "sequence": 1,
            "account_id": expense_account.id,
            "accounting_effect": "none_non_posted_source_line",
            "debit": 120.00,
            "credit": 0.00,
            "balance": 120.00,
            "amount_currency": 120.00,
            "source_account_id": 600001,
            "rebuild_source_model": "account.move.line",
            "rebuild_source_id": 901,
            "rebuild_source_snapshot": "test-snapshot",
        })
        self.env["rebuild.account.move.line.review"].create({
            "name": "Draft vendor bill payable line",
            "source_move_line_id": 902,
            "source_move_id": 501,
            "imported_move_review_id": draft_review.id,
            "company_id": self.company.id,
            "line_currency_id": self.company.currency_id.id,
            "date": fields.Date.from_string("2025-11-15"),
            "source_move_state": "draft",
            "source_move_type": "in_invoice",
            "sequence": 2,
            "account_id": payable_account.id,
            "display_type": "payment_term",
            "accounting_effect": "none_non_posted_source_line",
            "debit": 0.00,
            "credit": 120.00,
            "balance": -120.00,
            "amount_currency": -120.00,
            "source_account_id": 401001,
            "rebuild_source_model": "account.move.line",
            "rebuild_source_id": 902,
            "rebuild_source_snapshot": "test-snapshot",
        })
        cancelled_review = self.env["rebuild.account.move.review"].create({
            "name": "Cancelled source move",
            "source_name": "CANCEL/001",
            "source_move_id": 502,
            "source_state": "cancel",
            "state": "cancel",
            "company_id": self.company.id,
            "journal_id": self._journal().id,
            "currency_id": self.company.currency_id.id,
            "date": fields.Date.from_string("2025-11-16"),
            "move_type": "entry",
            "source_line_count": 0,
            "source_accounting_line_count": 0,
            "rebuild_source_model": "account.move",
            "rebuild_source_id": 502,
            "rebuild_source_snapshot": "test-snapshot",
        })

        stats = import_run._sync_document_regeneration_cases({"source_snapshot_id": "test-snapshot"})
        second_stats = import_run._sync_document_regeneration_cases({"source_snapshot_id": "test-snapshot"})

        self.assertEqual(stats["document_regeneration_case_count"], 2)
        self.assertEqual(stats["candidate_ready_count"], 1)
        self.assertEqual(stats["review_only_count"], 1)
        self.assertEqual(stats["blocked_count"], 0)
        self.assertEqual(second_stats["document_regeneration_case_count"], 2)
        draft_case = self.env["rebuild.account.document.regeneration.case"].search([
            ("move_review_id", "=", draft_review.id),
        ])
        cancelled_case = self.env["rebuild.account.document.regeneration.case"].search([
            ("move_review_id", "=", cancelled_review.id),
        ])
        self.assertEqual(draft_case.case_status, "candidate_ready")
        self.assertEqual(draft_case.generation_scope, "draft_business_document")
        self.assertEqual(draft_case.generation_status, "not_generated")
        self.assertEqual(draft_case.source_line_review_count, 2)
        self.assertEqual(cancelled_case.case_status, "review_only_cancelled_source")
        self.assertEqual(cancelled_case.generation_status, "not_applicable")

        generated_action = draft_case.action_generate_draft_move()
        generated_move = draft_case.target_move_id

        self.assertEqual(generated_action["res_model"], "account.move")
        self.assertEqual(generated_action["res_id"], generated_move.id)
        self.assertEqual(generated_move.state, "draft")
        self.assertEqual(generated_move.move_type, "in_invoice")
        self.assertEqual(generated_move.rebuild_source_model, "account.move.document_regeneration")
        self.assertEqual(generated_move.rebuild_source_id, 501)
        self.assertEqual(draft_case.generation_status, "validated")
        self.assertEqual(draft_case.generated_line_count, 2)
        self.assertEqual(draft_case.generated_debit_total, 120.00)
        self.assertEqual(draft_case.generated_credit_total, 120.00)
        self.assertEqual(draft_case.generated_balance_total, 0.00)
        self.assertEqual(
            set(generated_move.line_ids.mapped("rebuild_source_model")),
            {"account.move.line.document_regeneration"},
        )
        target_move_id = generated_move.id
        draft_case.action_generate_draft_move()
        self.assertEqual(draft_case.target_move_id.id, target_move_id)
        self.assertEqual(draft_case.generation_status, "validated")

        line_action = draft_case.action_open_source_line_reviews()
        self.assertEqual(line_action["res_model"], "rebuild.account.move.line.review")
        self.assertIn(("imported_move_review_id", "=", draft_review.id), line_action["domain"])
        move_action = draft_case.action_open_source_move_review()
        self.assertEqual(move_action["res_model"], "rebuild.account.move.review")
        self.assertEqual(move_action["res_id"], draft_review.id)

        reviewer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Document Case Reviewer",
            "login": "document.case.reviewer@example.invalid",
            "email": "document.case.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        other_company = self.env["res.company"].create({
            "name": "Document Case Hidden Company",
            "currency_id": self.company.currency_id.id,
        })
        other_review = self.env["rebuild.account.move.review"].sudo().create({
            "name": "Other company draft",
            "source_move_id": 503,
            "source_state": "draft",
            "state": "draft",
            "company_id": other_company.id,
            "currency_id": self.company.currency_id.id,
            "date": fields.Date.from_string("2025-11-17"),
            "move_type": "entry",
            "source_line_count": 1,
            "source_accounting_line_count": 1,
            "rebuild_source_model": "account.move",
            "rebuild_source_id": 503,
            "rebuild_source_snapshot": "test-snapshot",
        })
        self.env["rebuild.account.document.regeneration.case"].sudo().create({
            "name": "Other company document case",
            "move_review_id": other_review.id,
            "generation_scope": "draft_journal_entry",
            "case_status": "candidate_ready",
            "generation_status": "not_generated",
            "rebuild_source_model": "account.move",
            "rebuild_source_id": 503,
            "rebuild_source_snapshot": "test-snapshot",
        })
        visible_cases = self.env["rebuild.account.document.regeneration.case"].with_user(reviewer).search([
            ("rebuild_source_id", "in", [501, 502, 503]),
        ])
        self.assertEqual(visible_cases, draft_case | cancelled_case)

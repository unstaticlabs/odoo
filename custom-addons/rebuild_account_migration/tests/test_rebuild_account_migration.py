import base64
import hashlib
import json
import tempfile
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from lxml import etree

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.rebuild_account_migration.controllers import user_docs


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

    def _incoming_email(
        self,
        *,
        email_from,
        email_to,
        subject,
        message_id,
        filename,
    ):
        message = EmailMessage()
        message["From"] = email_from
        message["To"] = email_to
        message["Subject"] = subject
        message["Message-ID"] = message_id
        message.set_content("Milestone 13 native email-ingestion evidence.")
        message.add_attachment(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
                "AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
            ),
            maintype="image",
            subtype="png",
            filename=filename,
        )
        return message.as_bytes()

    def test_native_email_gateway_creates_draft_bill_with_source_evidence(self):
        supplier = self.env["res.partner"].create({
            "name": "Unit email supplier",
            "email": "supplier.email.gateway@example.invalid",
            "company_id": self.company.id,
        })
        purchase_journal = self._journal("purchase")
        thread_id = self.env["mail.thread"].sudo().message_process(
            "account.move",
            self._incoming_email(
                email_from=supplier.email,
                email_to="purchases@example.invalid",
                subject="Unit incoming supplier bill",
                message_id="<unit-m13-supplier-bill@example.invalid>",
                filename="unit-supplier-bill.png",
            ),
            custom_values={
                "move_type": "in_invoice",
                "journal_id": purchase_journal.id,
                "company_id": self.company.id,
            },
        )

        bill = self.env["account.move"].browse(thread_id)
        self.assertTrue(bill.exists())
        self.assertEqual(bill.move_type, "in_invoice")
        self.assertEqual(bill.state, "draft")
        self.assertEqual(bill.journal_id, purchase_journal)
        self.assertEqual(bill.partner_id, supplier)
        self.assertEqual(bill.invoice_source_email, supplier.email)
        incoming_messages = bill.message_ids.filtered(
            lambda message: message.message_type == "email",
        )
        self.assertEqual(len(incoming_messages), 1)
        attachment = incoming_messages.attachment_ids.filtered(
            lambda item: item.name == "unit-supplier-bill.png",
        )
        self.assertEqual(len(attachment), 1)
        self.assertEqual(bill.message_main_attachment_id, attachment)

    def test_native_email_gateway_creates_employee_expense_with_receipt(self):
        employee_user = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Unit email expense employee",
            "login": "unit.email.expense.employee@example.invalid",
            "email": "unit.email.expense.employee@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.env.ref("base.group_user").id])],
        })
        employee = self.env["hr.employee"].create({
            "name": employee_user.name,
            "user_id": employee_user.id,
            "work_email": employee_user.email,
            "company_id": self.company.id,
        })
        category = self.env["product.product"].create({
            "name": "Unit email expense category",
            "default_code": "UNITMAIL",
            "can_be_expensed": True,
        })
        thread_id = self.env["mail.thread"].sudo().message_process(
            "hr.expense",
            self._incoming_email(
                email_from=employee.work_email,
                email_to="expenses@example.invalid",
                subject=(
                    f"{category.default_code} Team lunch "
                    f"{self.company.currency_id.name} 42.50"
                ),
                message_id="<unit-m13-employee-expense@example.invalid>",
                filename="unit-expense-receipt.png",
            ),
        )

        expense = self.env["hr.expense"].browse(thread_id)
        self.assertTrue(expense.exists())
        self.assertEqual(expense.employee_id, employee)
        self.assertEqual(expense.product_id, category)
        self.assertEqual(expense.company_id, self.company)
        self.assertEqual(expense.currency_id, self.company.currency_id)
        self.assertAlmostEqual(expense.total_amount_currency, 42.50)
        incoming_messages = expense.message_ids.filtered(
            lambda message: message.message_type == "email",
        )
        self.assertEqual(len(incoming_messages), 1)
        attachment = incoming_messages.attachment_ids.filtered(
            lambda item: item.name == "unit-expense-receipt.png",
        )
        self.assertEqual(len(attachment), 1)
        self.assertEqual(attachment.res_model, "hr.expense")
        self.assertEqual(attachment.res_id, expense.id)

    def test_accounting_app_opens_operational_home_and_keeps_native_dashboard(self):
        menu = self.env.ref("account.menu_finance")
        home_client_action = self.env.ref(
            "rebuild_account_migration.action_rebuild_accounting_home",
        )
        dashboard_menu = self.env.ref("account.menu_board_journal_1")
        dashboard_action = self.env.ref("account.open_account_journal_dashboard_kanban")

        self.assertEqual(menu.name, "Accounting")
        self.assertEqual(menu.action, home_client_action)
        self.assertEqual(home_client_action.tag, "rebuild_accounting_home")
        self.assertEqual(dashboard_menu.action, dashboard_action)
        self.assertEqual(dashboard_action.path, "accounting")

        home_action = self.env[
            "rebuild.account.review.summary"
        ].action_open_accounting_home()
        home = self.env["rebuild.account.review.summary"].search([
            ("company_id", "=", self.company.id),
        ])
        self.assertTrue(home)
        self.assertEqual(home.name, f"{self.company.name} — Accounting Overview")
        self.assertEqual(home_action["res_model"], "rebuild.account.review.summary")
        self.assertEqual(home_action["res_id"], home.id)
        self.assertEqual(
            home_action["view_id"],
            self.env.ref(
                "rebuild_account_migration.view_rebuild_accounting_home_form",
            ).id,
        )
        self.assertEqual(
            home_action["views"],
            [
                (
                    self.env.ref(
                        "rebuild_account_migration.view_rebuild_accounting_home_form",
                    ).id,
                    "form",
                ),
            ],
        )
        review_action = self.env.ref(
            "rebuild_account_migration.action_rebuild_account_review_summary",
        )
        self.assertEqual(
            review_action.views,
            [
                (
                    self.env.ref(
                        "rebuild_account_migration.view_rebuild_account_review_summary_list",
                    ).id,
                    "list",
                ),
                (
                    self.env.ref(
                        "rebuild_account_migration.view_rebuild_account_review_summary_form",
                    ).id,
                    "form",
                ),
            ],
        )

    def test_accounting_home_summary_is_scoped_to_reviewer_companies(self):
        other_company = self.env["res.company"].create({
            "name": "Accounting Home Hidden Company",
            "currency_id": self.company.currency_id.id,
        })
        reviewer = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Accounting Home Reviewer",
            "login": "accounting.home.reviewer@example.invalid",
            "email": "accounting.home.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        self.env.flush_all()

        visible = self.env[
            "rebuild.account.review.summary"
        ].with_user(reviewer).search([])
        self.assertEqual(visible.mapped("company_id"), self.company)

        hidden = self.env["rebuild.account.review.summary"].browse(
            other_company.id,
        )
        with self.assertRaises(AccessError):
            hidden.with_user(reviewer).read(["company_id"])

    def test_accounting_manager_gets_the_full_accounting_application(self):
        manager_group = self.env.ref("account.group_account_manager")
        accounting_user_group = self.env.ref("account.group_account_user")

        self.assertIn(accounting_user_group, manager_group.implied_ids)

    def test_readonly_reviewer_gets_journals_without_upload_or_send_actions(self):
        journals_menu = self.env.ref("account.menu_board_journal_1")

        self.assertIn(self.readonly_group, journals_menu.group_ids)

        journal_arch = etree.fromstring(
            self.env.ref(
                "rebuild_account_migration."
                "view_rebuild_account_journal_dashboard_readonly",
            ).arch_db,
        )
        restricted_groups = journal_arch.xpath(
            "//attribute[@name='groups']/text()",
        )
        self.assertEqual(
            restricted_groups,
            ["account.group_account_invoice", "account.group_account_invoice"],
        )

        move_arch = etree.fromstring(
            self.env.ref(
                "rebuild_account_migration."
                "view_rebuild_account_move_readonly_actions",
            ).arch_db,
        )
        self.assertEqual(
            move_arch.xpath("//attribute[@name='groups']/text()"),
            ["account.group_account_invoice"],
        )

    def test_matched_items_route_uses_native_unreconcile_accounting_effect(self):
        clearing = self._account(
            "T471992",
            "Unit reconciliation clearing",
            "asset_current",
        )
        clearing.reconcile = True
        offset = self._account(
            "T580992",
            "Unit reconciliation offset",
            "asset_current",
        )
        journal = self._journal()
        moves = self.env["account.move"].create([
            {
                "move_type": "entry",
                "date": fields.Date.today(),
                "journal_id": journal.id,
                "line_ids": [
                    Command.create({
                        "name": "Clearing debit",
                        "account_id": clearing.id,
                        "debit": 100.0,
                    }),
                    Command.create({
                        "name": "Offset credit",
                        "account_id": offset.id,
                        "credit": 100.0,
                    }),
                ],
            },
            {
                "move_type": "entry",
                "date": fields.Date.today(),
                "journal_id": journal.id,
                "line_ids": [
                    Command.create({
                        "name": "Clearing credit",
                        "account_id": clearing.id,
                        "credit": 100.0,
                    }),
                    Command.create({
                        "name": "Offset debit",
                        "account_id": offset.id,
                        "debit": 100.0,
                    }),
                ],
            },
        ])
        moves.action_post()
        clearing_lines = moves.line_ids.filtered(
            lambda line: line.account_id == clearing,
        )
        clearing_lines.reconcile()
        self.assertTrue(all(clearing_lines.mapped("reconciled")))
        self.assertTrue(
            clearing_lines.matched_debit_ids
            | clearing_lines.matched_credit_ids,
        )

        self.env["account.move.line"].with_context(
            active_ids=[clearing_lines[0].id],
        ).action_unreconcile_match_entries()

        self.assertFalse(any(clearing_lines.mapped("reconciled")))
        self.assertFalse(
            clearing_lines.matched_debit_ids
            | clearing_lines.matched_credit_ids,
        )
        self.assertEqual(
            sorted(round(value, 2) for value in clearing_lines.mapped(
                "amount_residual",
            )),
            [-100.0, 100.0],
        )

    def test_general_reconciliation_keeps_full_partial_and_undo_results_visible(self):
        clearing = self._account(
            "T471993",
            "General reconciliation result",
            "asset_current",
        )
        clearing.reconcile = True
        offset = self._account(
            "T580993",
            "General reconciliation result offset",
            "asset_current",
        )
        journal = self._journal()
        moves = self.env["account.move"].create([
            {
                "move_type": "entry",
                "date": fields.Date.today(),
                "journal_id": journal.id,
                "line_ids": [
                    Command.create({
                        "name": "Reconciliation result debit",
                        "account_id": clearing.id,
                        "debit": 100.0,
                    }),
                    Command.create({
                        "name": "Reconciliation result offset",
                        "account_id": offset.id,
                        "credit": 100.0,
                    }),
                ],
            },
            {
                "move_type": "entry",
                "date": fields.Date.today(),
                "journal_id": journal.id,
                "line_ids": [
                    Command.create({
                        "name": "Reconciliation result credit",
                        "account_id": clearing.id,
                        "credit": 60.0,
                    }),
                    Command.create({
                        "name": "Reconciliation result offset",
                        "account_id": offset.id,
                        "debit": 60.0,
                    }),
                ],
            },
        ])
        moves.action_post()
        clearing_lines = moves.line_ids.filtered(
            lambda line: line.account_id == clearing,
        )
        self.env.flush_all()
        reconciliation_group = self.env["account.account.reconcile"].search([
            ("account_id", "=", clearing.id),
            ("company_id", "=", self.company.id),
        ], limit=1)
        reconciliation_group = reconciliation_group.with_context(
            default_account_move_lines=clearing_lines.ids,
        )

        result = reconciliation_group.reconcile()

        self.assertEqual(result["res_model"], "account.move.line")
        self.assertEqual(result["domain"], [("id", "in", clearing_lines.ids)])
        self.assertEqual(
            result["views"][0][0],
            self.env.ref(
                "rebuild_account_migration."
                "view_rebuild_account_move_line_reconciliation_result",
            ).id,
        )
        clearing_lines.invalidate_recordset()
        self.assertEqual(
            set(clearing_lines.mapped("rebuild_reconciliation_state")),
            {"partial", "full"},
        )
        self.assertEqual(
            sorted(round(value, 2) for value in clearing_lines.mapped(
                "amount_residual",
            )),
            [0.0, 40.0],
        )
        matching_reference = clearing_lines.mapped("matching_number")[0]
        self.assertTrue(matching_reference)
        self.assertEqual(
            len(set(clearing_lines.mapped("rebuild_matching_color"))),
            1,
        )

        undo_result = clearing_lines.action_rebuild_unreconcile()

        self.assertEqual(undo_result["tag"], "display_notification")
        undo_next = undo_result["params"]["next"]
        self.assertEqual(undo_next["res_model"], "account.move.line")
        self.assertEqual(undo_next["domain"], [("id", "in", clearing_lines.ids)])
        self.assertIn("Matching undone", undo_next["name"])
        clearing_lines.invalidate_recordset()
        self.assertEqual(
            set(clearing_lines.mapped("rebuild_reconciliation_state")),
            {"open"},
        )
        self.assertEqual(
            sorted(round(value, 2) for value in clearing_lines.mapped(
                "amount_residual",
            )),
            [-60.0, 100.0],
        )

    def test_monthly_revenue_spending_trend_uses_posted_native_ledger(self):
        revenue_account = self._account(
            "T707991",
            "Unit monthly revenue",
            "income",
        )
        spending_account = self._account(
            "T627991",
            "Unit monthly spending",
            "expense",
        )
        cash_account = self._account(
            "T512991",
            "Unit monthly cash",
            "asset_cash",
        )
        analytic_plan = self.env["account.analytic.plan"].create({
            "name": "Unit monthly plan",
        })
        analytic_account = self.env["account.analytic.account"].create({
            "name": "Unit monthly activity",
            "plan_id": analytic_plan.id,
        })
        journal = self._journal()

        for date, revenue, spending in (
            ("2026-01-15", 1000.0, 400.0),
            ("2026-02-15", 500.0, 700.0),
        ):
            move = self.env["account.move"].create({
                "move_type": "entry",
                "date": date,
                "journal_id": journal.id,
                "line_ids": [
                    Command.create({
                        "name": "Monthly revenue cash",
                        "account_id": cash_account.id,
                        "debit": revenue,
                    }),
                    Command.create({
                        "name": "Monthly revenue",
                        "account_id": revenue_account.id,
                        "credit": revenue,
                        "analytic_distribution": {
                            str(analytic_account.id): 100.0,
                        },
                    }),
                    Command.create({
                        "name": "Monthly spending",
                        "account_id": spending_account.id,
                        "debit": spending,
                    }),
                    Command.create({
                        "name": "Monthly spending cash",
                        "account_id": cash_account.id,
                        "credit": spending,
                    }),
                ],
            })
            move.action_post()

        rows = self.env["rebuild.account.revenue.spending.month"].search([
            ("company_id", "=", self.company.id),
            ("month", "in", ["2026-01-01", "2026-02-01"]),
        ])
        amounts = {}
        for row in rows:
            key = fields.Date.to_string(row.month)
            current = amounts.setdefault(key, [0.0, 0.0, 0.0, 0])
            current[0] += row.revenue
            current[1] += row.spending
            current[2] += row.net_contribution
            current[3] += row.line_count
        amounts = {
            key: (
                round(values[0], 2),
                round(values[1], 2),
                round(values[2], 2),
                values[3],
            )
            for key, values in amounts.items()
        }
        self.assertEqual(
            amounts,
            {
                "2026-01-01": (1000.0, 400.0, 600.0, 2),
                "2026-02-01": (500.0, 700.0, -200.0, 2),
            },
        )
        self.assertEqual(
            self.env["rebuild.account.revenue.spending.month"].search_count([
                ("company_id", "=", self.company.id),
                ("month", "in", ["2026-01-01", "2026-02-01"]),
                ("analytic_plan_ids", "in", analytic_plan.ids),
                ("analytic_account_ids", "in", analytic_account.ids),
            ]),
            2,
        )

        february_spending = rows.filtered(
            lambda row: (
                fields.Date.to_string(row.month) == "2026-02-01"
                and row.account_id == spending_account
            ),
        )
        drilldown = february_spending.action_open_journal_items()
        self.assertEqual(drilldown["res_model"], "account.move.line")
        self.assertEqual(
            self.env["account.move.line"].search_count(drilldown["domain"]),
            1,
        )
        action = self.env.ref(
            "rebuild_account_migration."
            "action_rebuild_account_revenue_spending_month",
        )
        self.assertEqual(action.view_mode, "graph,pivot,list")
        graph_view = self.env.ref(
            "rebuild_account_migration."
            "view_rebuild_account_revenue_spending_month_graph",
        )
        self.assertIn('type="line"', graph_view.arch)
        self.assertIn('stacked="0"', graph_view.arch)
        self.assertIn('name="net_contribution"', graph_view.arch)
        pivot_view = self.env.ref(
            "rebuild_account_migration."
            "view_rebuild_account_revenue_spending_month_pivot",
        )
        self.assertIn('name="net_contribution"', pivot_view.arch)
        self.assertNotIn('name="revenue" type="measure"', pivot_view.arch)
        self.assertNotIn('name="spending" type="measure"', pivot_view.arch)
        self.assertEqual(
            self.env.ref(
                "rebuild_account_migration."
                "menu_rebuild_account_revenue_spending_reporting",
            ).action,
            action,
        )

    def test_accounting_navigation_matches_the_operating_model(self):
        expected_top_level = {
            "rebuild_account_migration.menu_rebuild_accounting_overview":
                ("Overview", 0),
            "account.menu_board_journal_1": ("Journals", 1),
            "account.menu_finance_receivables": ("Customers", 2),
            "account.menu_finance_payables": ("Vendors", 3),
            "account.menu_finance_entries": ("Accounting", 4),
            "account.account_audit_menu": ("Review", 7),
            "account.menu_finance_reports": ("Reporting", 20),
            "account.menu_finance_configuration": ("Configuration", 35),
        }
        finance_menu = self.env.ref("account.menu_finance")

        for xmlid, (name, sequence) in expected_top_level.items():
            menu = self.env.ref(xmlid)
            self.assertEqual(menu.parent_id, finance_menu)
            self.assertEqual(menu.name, name)
            self.assertEqual(menu.sequence, sequence)

        self.assertFalse(
            self.env.ref(
                "rebuild_account_migration.menu_rebuild_account_closing_root",
            ).active,
        )
        self.assertEqual(
            self.env.ref(
                "account_asset_management.menu_finance_assets",
            ).parent_id,
            self.env.ref("account.menu_finance_entries"),
        )

        bank_matching_menu = self.env.ref(
            "rebuild_account_migration.menu_rebuild_account_reconcile_bank_transactions_priority",
        )
        general_reconciliation_menu = self.env.ref(
            "account_reconcile_oca.account_account_reconcile_menu",
        )
        self.assertEqual(bank_matching_menu.name, "Bank Matching")
        self.assertEqual(
            bank_matching_menu.parent_id,
            self.env.ref("account.account_transactions_menu"),
        )
        self.assertEqual(general_reconciliation_menu.name, "General Reconciliation")
        self.assertEqual(
            general_reconciliation_menu.action.name,
            "General Reconciliation",
        )
        self.assertEqual(
            general_reconciliation_menu.parent_id,
            self.env.ref("account.account_closing_menu"),
        )
        matched_items_menu = self.env.ref(
            "rebuild_account_migration.menu_rebuild_account_matched_items",
        )
        matched_items_action = self.env.ref(
            "rebuild_account_migration.action_rebuild_account_matched_items",
        )
        overview_action = self.env.ref(
            "rebuild_account_migration."
            "action_rebuild_account_reconciliation_overview",
        )
        self.assertFalse(matched_items_menu.active)
        self.assertEqual(general_reconciliation_menu.action, overview_action)
        self.assertEqual(
            safe_eval(overview_action.context)["search_default_unreconciled"],
            1,
        )
        self.assertNotIn(
            ("reconciled", "=", True),
            safe_eval(matched_items_action.domain),
        )
        self.assertIn(
            self.env.ref("account.group_account_user"),
            self.env.ref("account.action_account_unreconcile").group_ids,
        )

        configuration_routes = {
            "rebuild_account_migration.menu_rebuild_account_groups":
                "account.group",
            "rebuild_account_migration.menu_rebuild_tax_groups":
                "account.tax.group",
            "rebuild_account_migration.menu_rebuild_account_tags":
                "account.account.tag",
            "rebuild_account_migration.menu_rebuild_reconciliation_models":
                "account.reconcile.model",
            "rebuild_account_migration.menu_rebuild_incoterms":
                "account.incoterms",
        }
        manager_group = self.env.ref("account.group_account_manager")
        for xmlid, model_name in configuration_routes.items():
            menu = self.env.ref(xmlid)
            self.assertTrue(menu.active)
            self.assertEqual(menu.action.res_model, model_name)
            self.assertIn(manager_group, menu.group_ids)

        hygiene_menu = self.env.ref(
            "rebuild_account_migration.menu_rebuild_account_review_issues_priority",
        )
        hygiene_action = self.env.ref(
            "rebuild_account_migration.action_rebuild_account_hygiene",
        )
        self.assertEqual(hygiene_menu.name, "Accounting Hygiene")
        self.assertEqual(hygiene_menu.action, hygiene_action)
        self.assertEqual(
            hygiene_menu.parent_id,
            self.env.ref("account.account_audit_control_menu"),
        )
        self.assertEqual(
            hygiene_action.views,
            [
                (
                    self.env.ref(
                        "rebuild_account_migration.view_rebuild_account_hygiene_issue_list",
                    ).id,
                    "list",
                ),
                (
                    self.env.ref(
                        "rebuild_account_migration.view_rebuild_account_hygiene_issue_form",
                    ).id,
                    "form",
                ),
            ],
        )
        general_reconciliation_form = self.env.ref(
            "rebuild_account_migration.view_rebuild_account_general_reconciliation_form",
        )
        self.assertIn("General Reconciliation", general_reconciliation_form.arch_db)
        self.assertEqual(
            self.env["account.account.reconcile"]._description,
            "General Reconciliation",
        )
        boundary_action = self.env.ref(
            "rebuild_account_migration.action_rebuild_account_reconciliation_review",
        )
        self.assertEqual(
            safe_eval(boundary_action.context),
            {"search_default_pending_policy_review": 1},
        )
        boundary_list_arch = etree.fromstring(
            self.env.ref(
                "rebuild_account_migration.view_rebuild_account_reconciliation_review_list",
            ).arch_db,
        )
        boundary_form_arch = etree.fromstring(
            self.env.ref(
                "rebuild_account_migration.view_rebuild_account_reconciliation_review_form",
            ).arch_db,
        )
        for arch in (boundary_list_arch, boundary_form_arch):
            self.assertEqual(arch.get("create"), "0")
            self.assertEqual(arch.get("edit"), "0")
            self.assertEqual(arch.get("delete"), "0")
        full_preview_buttons = boundary_form_arch.xpath(
            ".//button[@name='action_preview_native_full_reconciliation_scope']",
        )
        self.assertEqual(len(full_preview_buttons), 1)
        apply_buttons = boundary_form_arch.xpath(
            ".//button[@name='action_apply_native_partial_reconciliation']",
        )
        self.assertEqual(len(apply_buttons), 1)
        self.assertEqual(
            apply_buttons[0].get("groups"),
            "account.group_account_manager",
        )

    def test_accounting_hygiene_refresh_requires_manager_access(self):
        reviewer = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Accounting Hygiene Reviewer",
            "login": "accounting.hygiene.reviewer@example.invalid",
            "email": "accounting.hygiene.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        hygiene = self.env["rebuild.account.review.summary"].search([
            ("company_id", "=", self.company.id),
        ], limit=1)
        hygiene_form = self.env.ref(
            "rebuild_account_migration.view_rebuild_account_hygiene_form",
        )
        refresh_buttons = hygiene_form._get_combined_arch().xpath(
            "//button[@name='action_refresh_hygiene']",
        )
        account_policy_fields = self.env.ref(
            "rebuild_account_migration.view_account_form_hygiene_balance_policy",
        )._get_combined_arch().xpath(
            "//field[@name='rebuild_hygiene_balance_policy']",
        )

        self.assertTrue(hygiene)
        self.assertEqual(len(refresh_buttons), 1)
        self.assertEqual(
            refresh_buttons[0].get("groups"),
            "account.group_account_manager",
        )
        self.assertEqual(len(account_policy_fields), 1)
        self.assertEqual(
            account_policy_fields[0].get("groups"),
            "account.group_account_manager",
        )
        backend_assets = self.env["ir.asset"]._get_asset_paths(
            "web.assets_backend",
            {},
        )
        self.assertTrue(
            any(
                path.endswith(
                    "/static/src/js/account_move_upload_controls.js",
                )
                for path, *_metadata in backend_assets
            ),
        )
        with self.assertRaises(AccessError):
            hygiene.with_user(reviewer).action_refresh_hygiene()

    def test_bank_matching_mutation_controls_require_full_accounting_access(self):
        view = self.env.ref(
            "account_reconcile_oca.bank_statement_line_form_reconcile_view",
        )
        combined_arch = view._get_combined_arch()
        mutation_button_names = {
            "reconcile_bank_line",
            "unreconcile_bank_line",
            "clean_reconcile",
            "action_to_check",
            "action_checked",
        }

        mutation_buttons = [
            button
            for button in combined_arch.xpath("//button")
            if button.get("name") in mutation_button_names
        ]
        self.assertEqual(
            {button.get("name") for button in mutation_buttons},
            mutation_button_names,
        )
        self.assertTrue(mutation_buttons)
        for button in mutation_buttons:
            self.assertEqual(button.get("groups"), "account.group_account_user")

        self.assertEqual(
            combined_arch.xpath("//notebook/page/@name"),
            ["reconcile_line", "chatter", "narration", "manual"],
        )
        expected_labels = {
            "unreconcile_bank_line": "Undo Match",
            "clean_reconcile": "Clear Selection",
            "action_to_check": "Mark for Review",
            "action_checked": "Mark Reviewed",
            "action_show_move": "Open Entry",
        }
        for button_name, label in expected_labels.items():
            buttons = combined_arch.xpath(
                f"//button[@name='{button_name}']",
            )
            self.assertTrue(buttons)
            self.assertEqual(
                {button.get("string") for button in buttons},
                {label},
            )
            self.assertTrue(all(button.get("title") for button in buttons))
        complete_match_buttons = combined_arch.xpath(
            "//button[@name='reconcile_bank_line']",
        )
        self.assertEqual(
            {button.get("string") for button in complete_match_buttons},
            {"Complete Match"},
        )
        self.assertTrue(all(
            button.get("title")
            for button in complete_match_buttons
        ))
        bank_matching_action = self.env.ref(
            "rebuild_account_migration."
            "action_rebuild_account_reconcile_bank_transactions",
        )
        self.assertFalse(safe_eval(bank_matching_action.context)["create"])

    def test_bank_matching_candidates_default_to_closest_amount_and_date_ranking(self):
        receivable = self._account(
            "T411230",
            "Closest amount receivable",
            "asset_receivable",
        )
        revenue = self._account(
            "T706230",
            "Closest amount revenue",
            "income",
        )
        partner = self.env["res.partner"].create({
            "name": "Closest amount customer",
        })
        journal = self._journal()
        candidate_lines = self.env["account.move.line"]
        today = fields.Date.today()
        for amount, date in (
            (70.0, fields.Date.subtract(today, days=1)),
            (99.0, fields.Date.subtract(today, days=10)),
            (99.0, fields.Date.subtract(today, days=2)),
            (130.0, today),
            (-100.0, today),
        ):
            debit = max(amount, 0.0)
            credit = max(-amount, 0.0)
            move = self.env["account.move"].create({
                "journal_id": journal.id,
                "date": date,
                "line_ids": [
                    Command.create({
                        "name": f"Open item {amount}",
                        "account_id": receivable.id,
                        "partner_id": partner.id,
                        "debit": debit,
                        "credit": credit,
                    }),
                    Command.create({
                        "name": f"Open item {amount}",
                        "account_id": revenue.id,
                        "debit": credit,
                        "credit": debit,
                    }),
                ],
            })
            move.action_post()
            candidate_lines |= move.line_ids.filtered(
                lambda line: line.account_id == receivable,
            )

        bank_line = self.env["account.bank.statement.line"].with_context(
            _test_account_reconcile_oca=True,
        ).create({
            "journal_id": self._journal("bank").id,
            "date": today,
            "partner_id": partner.id,
            "payment_ref": "Closest amount ranking",
            "amount": 100.0,
        })
        result = candidate_lines.with_context(
            reconcile_closest_amount=True,
            reconcile_closest_date=True,
            reconcile_statement_line_id=bank_line.id,
        ).web_search_read(
            [("id", "in", candidate_lines.ids)],
            {"amount_residual": {}, "date": {}},
        )

        residuals = [
            record["amount_residual"]
            for record in result["records"]
        ]
        self.assertEqual(residuals, [99.0, 99.0, 130.0, 70.0, -100.0])
        self.assertEqual(
            [record["date"] for record in result["records"]],
            [
                fields.Date.subtract(today, days=2),
                fields.Date.subtract(today, days=10),
                today,
                fields.Date.subtract(today, days=1),
                today,
            ],
        )
        self.assertEqual(result["length"], 5)

        bank_view = self.env.ref(
            "account_reconcile_oca.bank_statement_line_form_reconcile_view",
        )._get_combined_arch()
        candidate_field = bank_view.xpath(
            "//page[@name='reconcile_line']"
            "/field[@name='add_account_move_line_id']",
        )
        self.assertEqual(len(candidate_field), 1)
        self.assertIn(
            "'search_default_reconcile_closest_amount': 1",
            candidate_field[0].get("context"),
        )
        self.assertIn(
            "'search_default_reconcile_closest_date': 1",
            candidate_field[0].get("context"),
        )
        self.assertIn(
            "'reconcile_statement_line_id': id",
            candidate_field[0].get("context"),
        )

        search_view = self.env.ref(
            "account_reconcile_oca.account_move_line_search_reconcile_view",
        )._get_combined_arch()
        closest_filter = search_view.xpath(
            "//filter[@name='reconcile_closest_amount']",
        )
        self.assertEqual(len(closest_filter), 1)
        self.assertEqual(closest_filter[0].get("string"), "Closest amount")
        closest_date_filter = search_view.xpath(
            "//filter[@name='reconcile_closest_date']",
        )
        self.assertEqual(len(closest_date_filter), 1)
        self.assertEqual(closest_date_filter[0].get("string"), "Closest date")

    def test_transactions_list_explains_status_residual_and_linked_entry(self):
        bank_journal = self._journal("bank")
        bank_line = self.env["account.bank.statement.line"].with_context(
            _test_account_reconcile_oca=True,
        ).create({
            "journal_id": bank_journal.id,
            "date": fields.Date.today(),
            "payment_ref": "Transaction list unit match",
            "amount": 100.0,
        })
        counterpart = bank_line.move_id.line_ids.filtered(
            lambda line: (
                line.account_id != bank_journal.default_account_id
            ),
        )
        self.assertEqual(len(counterpart), 1)
        counterpart.account_id.reconcile = True
        bank_line.invalidate_recordset()
        self.assertIn(
            bank_line.rebuild_transaction_status,
            {"open", "review"},
        )
        self.assertEqual(bank_line.rebuild_remaining_amount, 100.0)
        self.assertFalse(bank_line.rebuild_matching_reference)

        matching_action = bank_line.action_rebuild_open_bank_matching()
        self.assertEqual(matching_action["domain"], [("id", "=", bank_line.id)])
        self.assertEqual(
            matching_action["res_model"],
            "account.bank.statement.line",
        )

        offset = self._account(
            "T580994",
            "Transaction list match offset",
            "asset_current",
        )
        counterpart_balance = counterpart.balance
        clearing_move = self.env["account.move"].create({
            "move_type": "entry",
            "date": fields.Date.today(),
            "journal_id": self._journal().id,
            "line_ids": [
                Command.create({
                    "name": "Transaction list counterpart",
                    "account_id": counterpart.account_id.id,
                    "debit": max(-counterpart_balance, 0.0),
                    "credit": max(counterpart_balance, 0.0),
                }),
                Command.create({
                    "name": "Transaction list offset",
                    "account_id": offset.id,
                    "debit": max(counterpart_balance, 0.0),
                    "credit": max(-counterpart_balance, 0.0),
                }),
            ],
        })
        clearing_move.action_post()
        clearing_line = clearing_move.line_ids.filtered(
            lambda line: line.account_id == counterpart.account_id,
        )
        (counterpart | clearing_line).reconcile()
        bank_line.invalidate_recordset()

        self.assertEqual(bank_line.rebuild_transaction_status, "matched")
        self.assertEqual(bank_line.rebuild_remaining_amount, 0.0)
        self.assertTrue(bank_line.rebuild_matching_reference)
        self.assertIn(
            clearing_move.display_name,
            bank_line.rebuild_linked_document,
        )

        action = self.env.ref(
            "account_statement_base.account_bank_statement_line_action",
        )
        transaction_view = self.env.ref(
            "rebuild_account_migration.view_rebuild_bank_transaction_list",
        )
        transaction_arch = etree.fromstring(transaction_view.arch_db)
        self.assertEqual(action.name, "Transactions")
        self.assertEqual(action.view_mode, "list,form")
        self.assertEqual(action.view_ids[0].view_id, transaction_view)
        self.assertEqual(transaction_arch.get("create"), "0")
        self.assertEqual(transaction_arch.get("edit"), "0")
        for field_name in (
            "date",
            "payment_ref",
            "partner_id",
            "amount",
            "journal_id",
            "rebuild_transaction_status",
            "rebuild_matching_reference",
            "rebuild_linked_document",
            "rebuild_remaining_amount",
        ):
            self.assertTrue(
                transaction_arch.xpath(f"//field[@name='{field_name}']"),
            )

    def test_native_expenses_are_available_from_accounting_payables(self):
        expenses_menu = self.env.ref("hr_expense.menu_hr_expense_account_employee_expenses")

        self.assertEqual(expenses_menu.parent_id, self.env.ref("account.menu_finance_payables"))
        self.assertEqual(expenses_menu.action, self.env.ref("hr_expense.action_hr_expense_account"))

    def test_accountant_reviewer_can_read_native_expenses_but_not_change_them(self):
        reviewer = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Native Expense Reviewer",
            "login": "native.expense.reviewer@example.invalid",
            "email": "native.expense.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        employee = self.env["hr.employee"].create({
            "name": "Unit Expense Employee",
            "company_id": self.company.id,
        })
        product = self.env["product.product"].create({
            "name": "Unit Expense Category",
            "can_be_expensed": True,
            "standard_price": 10.0,
        })
        expense = self.env["hr.expense"].create({
            "name": "Unit native expense evidence",
            "employee_id": employee.id,
            "product_id": product.id,
            "company_id": self.company.id,
            "payment_mode": "own_account",
            "total_amount_currency": 10.0,
        })
        reviewer_expenses = self.env["hr.expense"].with_user(reviewer)

        self.assertIn(expense, reviewer_expenses.search([
            ("id", "=", expense.id),
        ]))
        for view_type, view_xmlid in (
            ("list", "hr_expense.view_my_expenses_tree"),
            ("kanban", "hr_expense.hr_expense_kanban_view_header"),
            ("form", "hr_expense.hr_expense_view_form"),
        ):
            view = reviewer_expenses.get_view(
                self.env.ref(view_xmlid).id,
                view_type,
            )
            arch = etree.fromstring(view["arch"])
            self.assertEqual(arch.get("create"), "false")
            self.assertEqual(arch.get("edit"), "false")
            self.assertEqual(arch.get("delete"), "false")
            if view_type == "form":
                self.assertFalse(
                    arch.xpath("//header/button | //header/widget"),
                )

        with self.assertRaisesRegex(
            AccessError,
            "read-only for native expenses",
        ):
            reviewer_expenses.create({
                "name": "Forbidden reviewer expense",
                "employee_id": employee.id,
                "product_id": product.id,
                "company_id": self.company.id,
                "payment_mode": "own_account",
                "total_amount_currency": 10.0,
            })
        with self.assertRaisesRegex(
            AccessError,
            "read-only for native expenses",
        ):
            expense.with_user(reviewer).write({"name": "Changed"})
        with self.assertRaisesRegex(
            AccessError,
            "read-only for native expenses",
        ):
            expense.with_user(reviewer).unlink()

        backend_assets = self.env["ir.asset"]._get_asset_paths(
            "web.assets_backend",
            {},
        )
        self.assertTrue(
            any(
                path.endswith(
                    "/static/src/xml/hr_expense_reviewer_controls.xml",
                )
                for path, *_metadata in backend_assets
            ),
        )

    def test_native_asset_mutation_controls_require_accounting_write_access(self):
        view = self.env.ref(
            "account_asset_management.account_asset_view_form",
        )
        mutation_button_names = {
            "validate",
            "compute_depreciation_board",
            "create_move",
        }
        mutation_buttons = [
            button
            for button in view._get_combined_arch().xpath("//button")
            if button.get("name") in mutation_button_names
        ]

        self.assertEqual(
            {button.get("name") for button in mutation_buttons},
            mutation_button_names,
        )
        self.assertTrue(mutation_buttons)
        for button in mutation_buttons:
            self.assertEqual(
                button.get("groups"),
                "account.group_account_invoice,account.group_account_user",
            )

    def test_native_expense_records_have_source_trace_fields(self):
        trace_fields = {
            "rebuild_source_database",
            "rebuild_source_model",
            "rebuild_source_id",
            "rebuild_source_snapshot",
            "rebuild_import_run_id",
            "rebuild_import_status",
        }

        for model_name in ("hr.employee", "hr.expense", "product.product"):
            self.assertTrue(trace_fields.issubset(self.env[model_name]._fields))

        employee_trace_fields = {
            field_name
            for field_name in self.env["hr.employee"]._fields
            if field_name.startswith("rebuild_")
        }
        self.assertTrue(trace_fields.issubset(employee_trace_fields))
        for field_name in employee_trace_fields:
            self.assertEqual(
                self.env["hr.employee"]._fields[field_name].groups,
                "hr.group_hr_user",
            )

    def test_native_document_attachment_preserves_binary_and_main_selection(self):
        snapshot = "unit-native-document-attachment"
        source_move_id = 990021
        source_attachment_id = 990022
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Native document attachment replay",
            "source_snapshot_id": snapshot,
        })
        move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": self._journal().id,
            "date": fields.Date.today(),
            "company_id": self.company.id,
            "rebuild_source_model": "account.move.native_engine_replay",
            "rebuild_source_id": source_move_id,
            "rebuild_source_snapshot": snapshot,
        })
        raw = b"%PDF-1.4 native document evidence"
        checksum = hashlib.sha1(raw).hexdigest()

        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory, "aa", "native-document.pdf")
            source_path.parent.mkdir()
            source_path.write_bytes(raw)
            stats = import_run._import_attachments(
                None,
                {
                    "source_database": "unit_source",
                    "source_snapshot_id": snapshot,
                    "source_filestore_path": directory,
                    "date_from": "2025-10-01",
                    "date_to": "2026-06-30",
                    "attachment_target_trace_models": {
                        "account.move": [
                            "account.move.native_engine_replay",
                        ],
                    },
                },
                {990001: self.company},
                rows=[{
                    "id": source_attachment_id,
                    "res_model": "account.move",
                    "res_id": source_move_id,
                    "company_id": 990001,
                    "name": "native-document.pdf",
                    "res_field": None,
                    "type": "binary",
                    "url": None,
                    "store_fname": "aa/native-document.pdf",
                    "checksum": checksum,
                    "file_size": len(raw),
                    "mimetype": "application/pdf",
                    "description": "Source invoice evidence",
                    "public": False,
                    "is_main": True,
                }],
            )

        attachment = self.env["ir.attachment"].search([
            ("rebuild_source_model", "=", "ir.attachment"),
            ("rebuild_source_id", "=", source_attachment_id),
            ("rebuild_source_snapshot", "=", snapshot),
        ])
        self.assertEqual(stats["source_attachment_count"], 1)
        self.assertEqual(stats["imported_attachment_count"], 1)
        self.assertEqual(stats["source_main_attachment_count"], 1)
        self.assertEqual(stats["imported_main_attachment_count"], 1)
        self.assertEqual(import_run._attachment_issue_count(stats), 0)
        self.assertEqual(attachment.raw, raw)
        self.assertEqual(attachment.res_model, "account.move")
        self.assertEqual(attachment.res_id, move.id)
        self.assertEqual(move.message_main_attachment_id, attachment)
        reviewer = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Native document evidence reviewer",
            "login": "native.document.evidence.reviewer@example.invalid",
            "email": "native.document.evidence.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        self.assertEqual(attachment.with_user(reviewer).raw, raw)

    def test_company_import_preserves_source_legal_address(self):
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Company address replay",
            "source_snapshot_id": "unit-company-address",
        })
        france = self.env.ref("base.fr")
        source_row = {
            "id": 990001,
            "name": "Address Replay Company",
            "fiscalyear_last_day": 30,
            "fiscalyear_last_month": "9",
            "fiscalyear_lock_date": None,
            "tax_lock_date": None,
            "sale_lock_date": None,
            "purchase_lock_date": None,
            "hard_lock_date": None,
            "account_fiscal_country_id": 75,
            "tax_calculation_rounding_method": "round_per_line",
            "partner_country_id": 75,
            "vat": "FR48983982950",
            "company_registry": "99000100000001",
            "street": "60 RUE FRANCOIS PREMIER",
            "street2": "CHEZ LEGALPLACE",
            "zip": "75008",
            "city": "PARIS",
            "currency_name": self.company.currency_id.name,
        }
        options = {
            "source_database": "unit_source",
            "source_snapshot_id": "unit-company-address",
        }

        with patch.object(type(import_run), "_fetchall", return_value=[source_row]):
            companies, _rows = import_run._company_map(
                object(),
                options,
                {75: france},
            )

        company = companies[990001]
        self.assertEqual(company.street, source_row["street"])
        self.assertEqual(company.street2, source_row["street2"])
        self.assertEqual(company.zip, source_row["zip"])
        self.assertEqual(company.city, source_row["city"])
        self.assertEqual(company.country_id, france)

    def test_account_import_syncs_source_company_accounting_defaults(self):
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Company accounting defaults replay",
            "source_snapshot_id": "unit-company-defaults",
        })
        source_account_ids = {
            "income_currency_exchange_account_id": 990101,
            "expense_currency_exchange_account_id": 990102,
            "account_journal_suspense_account_id": 990103,
            "transfer_account_id": 990104,
        }
        accounts = {
            source_account_ids["income_currency_exchange_account_id"]:
                self._account("T766991", "Unit exchange income", "income"),
            source_account_ids["expense_currency_exchange_account_id"]:
                self._account("T666991", "Unit exchange expense", "expense"),
            source_account_ids["account_journal_suspense_account_id"]:
                self._account("T471991", "Unit journal suspense", "asset_current"),
            source_account_ids["transfer_account_id"]:
                self._account("T580991", "Unit transfer", "asset_current"),
        }
        source_row = {"id": 990001, **source_account_ids}
        options = {
            "source_company_ids": [990001],
            "source_snapshot_id": "unit-company-defaults",
        }

        with patch.object(type(import_run), "_fetchall", return_value=[source_row]):
            import_run._sync_company_accounting_defaults(
                object(),
                options,
                {990001: self.company},
                accounts,
            )

        for field_name, source_account_id in source_account_ids.items():
            self.assertEqual(
                self.company[field_name],
                accounts[source_account_id],
            )

    def test_journal_replay_preserves_payment_method_lines_when_currency_is_unchanged(self):
        usd = self.env.ref("base.USD")
        journal = self.env["account.journal"].create({
            "name": "Track B idempotent bank",
            "code": "TBID",
            "type": "bank",
            "company_id": self.company.id,
            "currency_id": usd.id,
            "rebuild_source_model": "account.journal",
            "rebuild_source_id": 990013,
            "rebuild_source_snapshot": "unit-validation-native-expenses",
        })
        method_lines = journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids
        self.assertTrue(method_lines)
        method_line_ids = method_lines.ids
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Track B journal idempotence",
            "source_snapshot_id": "unit-validation-native-expenses",
        })
        source_row = {
            "id": 990013,
            "name": "Track B idempotent bank",
            "code": "TBID",
            "type": "bank",
            "company_id": 990001,
            "default_account_id": False,
            "currency_id": 990002,
            "active": True,
            "sequence": journal.sequence,
            "refund_sequence": journal.refund_sequence,
            "restrict_mode_hash_table": journal.restrict_mode_hash_table,
        }
        options = {
            "source_company_ids": [990001],
            "source_snapshot_id": "unit-validation-native-expenses",
        }

        with patch.object(type(import_run), "_fetchall", return_value=[source_row]):
            mapped = import_run._journal_map(
                object(),
                options,
                {990001: self.company},
                {},
                {990002: usd},
            )

        self.assertEqual(mapped[990013], journal)
        self.assertEqual(
            (journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids).ids,
            method_line_ids,
        )
        self.assertEqual(self.env["account.payment.method.line"].browse(method_line_ids).journal_id, journal)

    def test_reconciliation_model_replay_preserves_native_oca_rule_semantics(self):
        snapshot = "unit-reconciliation-models"
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Reconciliation model replay",
            "source_snapshot_id": snapshot,
        })
        account = self._account(
            "T627891",
            "Unit bank fees",
            "expense",
        )
        journal = self._journal("bank")
        partner = self.env["res.partner"].create({
            "name": "Unit reconciliation partner",
        })
        tax = self.env["account.tax"].search([
            ("company_id", "=", self.company.id),
        ], limit=1)
        self.assertTrue(tax)
        model_rows = [{
            "id": 990201,
            "sequence": 15,
            "company_id": 990001,
            "trigger": "auto_reconcile",
            "match_amount": "lower",
            "match_amount_min": -5.0,
            "match_amount_max": 0.0,
            "match_label": "contains",
            "match_label_param": "SERVICE FEE",
            "name": "Unit service-fee rule",
            "active": True,
            "created_automatically": False,
            "use_count": 7,
            "is_asking_for_autopost": False,
        }]
        line_rows = [{
            "id": 990202,
            "model_id": 990201,
            "sequence": 10,
            "account_id": 990203,
            "partner_id": 990204,
            "amount_type": "percentage",
            "amount_string": "100",
            "analytic_distribution": None,
            "label": "Bank service fee",
        }]
        relation_rows = (
            [{"model_id": 990201, "journal_id": 990205}],
            [{"model_id": 990201, "partner_id": 990204}],
            [{"line_id": 990202, "tax_id": 990206}],
        )
        options = {
            "source_database": "unit-source",
            "source_company_ids": [990001],
            "source_snapshot_id": snapshot,
        }

        with patch.object(
            type(import_run),
            "_fetchall",
            side_effect=[
                model_rows,
                line_rows,
                *relation_rows,
            ],
        ):
            mapped, stats = import_run._reconciliation_model_map(
                object(),
                options,
                {990001: self.company},
                {990203: account},
                {990205: journal},
                {990204: partner},
                {990206: tax},
            )

        model = mapped[990201]
        self.assertEqual(model.trigger, "auto_reconcile")
        self.assertEqual(model.match_amount, "lower")
        self.assertEqual(model.match_label, "contains")
        self.assertEqual(model.match_label_param, "SERVICE FEE")
        self.assertEqual(model.match_journal_ids, journal)
        self.assertEqual(model.match_partner_ids, partner)
        self.assertEqual(model.rebuild_source_id, 990201)
        self.assertIn("use_count=7", model.rebuild_import_note)
        self.assertEqual(len(model.line_ids), 1)
        self.assertEqual(model.line_ids.account_id, account)
        self.assertEqual(model.line_ids.partner_id, partner)
        self.assertEqual(model.line_ids.tax_ids, tax)
        self.assertEqual(model.line_ids.amount_string, "100")
        self.assertEqual(model.line_ids.rebuild_source_id, 990202)
        self.assertEqual(stats["mapped_model_count"], 1)
        self.assertEqual(stats["mapped_line_count"], 1)
        self.assertEqual(stats["missing_reference_count"], 0)

    def test_reconciliation_model_replay_keeps_duplicate_source_names_distinct(self):
        snapshot = "unit-duplicate-reconciliation-model-names"
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Duplicate reconciliation model name replay",
            "source_snapshot_id": snapshot,
        })
        account = self._account(
            "T627892",
            "Unit duplicate-name bank fees",
            "expense",
        )
        model_rows = [
            {
                "id": source_id,
                "sequence": sequence,
                "company_id": 990001,
                "trigger": "manual",
                "match_amount": None,
                "match_amount_min": 0.0,
                "match_amount_max": 0.0,
                "match_label": "contains",
                "match_label_param": label,
                "name": "Unit duplicate source rule",
                "active": True,
                "created_automatically": False,
                "use_count": 0,
                "is_asking_for_autopost": False,
            }
            for source_id, sequence, label in (
                (990211, 10, "FEE A"),
                (990212, 20, "FEE B"),
            )
        ]
        line_rows = [
            {
                "id": source_id + 10,
                "model_id": source_id,
                "sequence": 10,
                "account_id": 990203,
                "partner_id": None,
                "amount_type": "percentage",
                "amount_string": "100",
                "analytic_distribution": None,
                "label": label,
            }
            for source_id, label in (
                (990211, "First source rule"),
                (990212, "Second source rule"),
            )
        ]
        options = {
            "source_database": "unit-source",
            "source_company_ids": [990001],
            "source_snapshot_id": snapshot,
        }

        with patch.object(
            type(import_run),
            "_fetchall",
            side_effect=[model_rows, line_rows, [], [], []],
        ):
            mapped, stats = import_run._reconciliation_model_map(
                object(),
                options,
                {990001: self.company},
                {990203: account},
                {},
                {},
                {},
            )

        self.assertEqual(len(mapped), 2)
        self.assertNotEqual(mapped[990211], mapped[990212])
        self.assertEqual(
            sorted([
                mapped[990211].rebuild_source_id,
                mapped[990212].rebuild_source_id,
            ]),
            [990211, 990212],
        )
        self.assertEqual(stats["mapped_model_count"], 2)
        self.assertEqual(stats["mapped_line_count"], 2)

    def test_exact_replay_reuses_only_validated_native_source_move_alias(self):
        snapshot = "unit-replacement-target-alias"
        debit_account = self._account(
            "T471991",
            "Replacement alias debit",
            "asset_current",
        )
        credit_account = self._account(
            "T455991",
            "Replacement alias credit",
            "liability_current",
        )
        move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": self._journal().id,
            "company_id": self.company.id,
            "date": "2025-09-30",
            "rebuild_source_model": "account.move.native_general_replay",
            "rebuild_source_id": 990100,
            "rebuild_source_snapshot": snapshot,
            "line_ids": [
                Command.create({
                    "name": "Replacement alias debit",
                    "account_id": debit_account.id,
                    "currency_id": self.company.currency_id.id,
                    "debit": 10.0,
                    "credit": 0.0,
                    "amount_currency": 10.0,
                    "rebuild_source_model": (
                        "account.move.line.native_general_replay"
                    ),
                    "rebuild_source_id": 990101,
                    "rebuild_source_snapshot": snapshot,
                }),
                Command.create({
                    "name": "Replacement alias credit",
                    "account_id": credit_account.id,
                    "currency_id": self.company.currency_id.id,
                    "debit": 0.0,
                    "credit": 10.0,
                    "amount_currency": -10.0,
                    "rebuild_source_model": (
                        "account.move.line.native_general_replay"
                    ),
                    "rebuild_source_id": 990102,
                    "rebuild_source_snapshot": snapshot,
                }),
            ],
        })
        move.action_post()
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Replacement target source alias validation",
            "source_snapshot_id": snapshot,
        })
        options = {
            "source_snapshot_id": snapshot,
            "source_trace_aliases": {
                "account.move": ["account.move.native_general_replay"],
                "account.move.line": [
                    "account.move.line.native_general_replay",
                ],
            },
        }
        source_move = {
            "id": 990100,
            "company_id": 990001,
            "journal_id": 990003,
            "date": fields.Date.from_string("2025-09-30"),
            "name": "OD000000003",
            "sequence_prefix": "OD",
            "sequence_number": 3,
        }
        source_lines = [
            {
                "id": 990101,
                "account_id": 990201,
                "partner_id": False,
                "currency_id": 990004,
                "debit": 10.0,
                "credit": 0.0,
                "amount_currency": 10.0,
            },
            {
                "id": 990102,
                "account_id": 990202,
                "partner_id": False,
                "currency_id": 990004,
                "debit": 0.0,
                "credit": 10.0,
                "amount_currency": -10.0,
            },
        ]

        move_map = import_run._source_trace_record_map(
            "account.move",
            [990100],
            options,
        )
        self.assertEqual(move_map[990100], move)
        result = import_run._validate_exact_replay_move_alias(
            move,
            source_move,
            source_lines,
            {990001: self.company},
            {990003: move.journal_id},
            {},
            {
                990201: debit_account,
                990202: credit_account,
            },
            {990004: self.company.currency_id},
            options,
        )
        self.assertEqual(result["source_move_id"], 990100)
        self.assertEqual(result["source_line_count"], 2)
        self.assertEqual(result["debit"], 10.0)
        self.assertEqual(result["credit"], 10.0)
        identity = (
            import_run._normalize_exact_replay_move_alias_identity(
                move,
                source_move,
            )
        )
        self.assertTrue(identity["identity_normalized"])
        self.assertEqual(move.name, "OD000000003")
        self.assertEqual(move.sequence_prefix, "OD")
        self.assertEqual(move.sequence_number, 3)

        invalid_lines = [dict(line) for line in source_lines]
        invalid_lines[1]["credit"] = 9.0
        with self.assertRaisesRegex(
            ValueError,
            "cannot replace exact source move 990100",
        ):
            import_run._validate_exact_replay_move_alias(
                move,
                source_move,
                invalid_lines,
                {990001: self.company},
                {990003: move.journal_id},
                {},
                {
                    990201: debit_account,
                    990202: credit_account,
                },
                {990004: self.company.currency_id},
                options,
            )

    def test_sequence_chronology_profile_keeps_source_exceptions_visible(self):
        profile = self.env[
            "rebuild.account.import.run"
        ]._sequence_chronology_profile([
            {
                "source_move_id": 1,
                "source_journal_id": 10,
                "move_name": "BNK/0001",
                "date": "2025-01-31",
                "sequence_prefix": "BNK/",
                "sequence_number": 1,
            },
            {
                "source_move_id": 2,
                "source_journal_id": 10,
                "move_name": "BNK/0003",
                "date": "2025-01-15",
                "sequence_prefix": "BNK/",
                "sequence_number": 3,
            },
            {
                "source_move_id": 3,
                "source_journal_id": 10,
                "move_name": "BNK/0003",
                "date": "2025-02-01",
                "sequence_prefix": "BNK/",
                "sequence_number": 3,
            },
        ])

        self.assertEqual(profile["move_count"], 3)
        self.assertEqual(profile["missing_name_count"], 0)
        self.assertEqual(profile["duplicate_name_group_count"], 1)
        self.assertEqual(
            profile["duplicate_sequence_number_group_count"],
            1,
        )
        self.assertEqual(profile["sequence_gap_count"], 1)
        self.assertEqual(profile["sequence_date_decrease_count"], 1)

    def test_native_expense_company_dependent_values_accept_source_key_shapes(self):
        import_run = self.env["rebuild.account.import.run"]

        self.assertEqual(import_run._native_expense_company_value(42.0, 1), 42.0)
        self.assertEqual(import_run._native_expense_company_value({"1": 19.5}, 1), 19.5)
        self.assertEqual(import_run._native_expense_company_value({1: 21.5}, 1), 21.5)
        self.assertIsNone(import_run._native_expense_company_value({"2": 24.0}, 1))

    def test_native_expense_settlement_preserves_source_partial_amount(self):
        import_run = self.env["rebuild.account.import.run"]

        class TargetLine:
            id = 42
            balance = -100.0

        class BankLine:
            manual_in_currency = True
            manual_reference = False
            manual_amount = 0.0
            manual_amount_in_currency = 0.0
            previous_manual_amount_in_currency = 100.0

            def __init__(self):
                self.calls = []
                self.reconcile_data_info = {
                    "data": [
                        {
                            "reference": "account.move.line;42",
                            "amount": 100.0,
                            "credit": 0.0,
                            "debit": 100.0,
                            "currency_amount": 100.0,
                        },
                    ],
                    "reconcile_auxiliary_id": 1,
                }

            def _add_account_move_line(self, target_line):
                self.calls.append(("add", target_line.id))

            def _onchange_manual_reconcile_reference(self):
                self.calls.append(("select", self.manual_reference))

            def _onchange_manual_reconcile_vals(self):
                self.calls.append(("amount", self.manual_amount))

            def _recompute_suspense_line(self, data, auxiliary_id, manual_reference):
                return {
                    "data": data,
                    "reconcile_auxiliary_id": auxiliary_id,
                }

        bank_line = BankLine()
        import_run._native_expense_settlement_add_edge(
            bank_line,
            TargetLine(),
            {
                "partial_amount": 70.0,
                "partial_amount_currency": 84.0,
            },
        )

        self.assertEqual(bank_line.manual_reference, "account.move.line;42")
        self.assertEqual(bank_line.manual_amount, 70.0)
        self.assertEqual(bank_line.manual_amount_in_currency, 84.0)
        self.assertEqual(bank_line.previous_manual_amount_in_currency, 84.0)
        self.assertEqual(
            bank_line.reconcile_data_info["data"][0],
            {
                "reference": "account.move.line;42",
                "amount": 70.0,
                "credit": 0.0,
                "debit": 70.0,
                "currency_amount": 84.0,
            },
        )
        self.assertEqual(
            bank_line.calls,
            [
                ("add", 42),
                ("select", "account.move.line;42"),
                ("amount", 70.0),
            ],
        )

    def test_native_expense_settlement_removes_generated_exchange_candidate(self):
        import_run = self.env["rebuild.account.import.run"]

        class TargetLine:
            id = 42

        class BankLine:
            manual_reference = False

            def __init__(self):
                self.reconcile_data_info = {
                    "data": [
                        {"reference": "account.move.line;42", "id": 42},
                        {
                            "reference": "reconcile_auxiliary;7",
                            "original_exchange_line_id": 42,
                        },
                    ],
                    "reconcile_auxiliary_id": 8,
                }

            def _recompute_suspense_line(self, data, auxiliary_id, manual_reference):
                return {
                    "data": data,
                    "reconcile_auxiliary_id": auxiliary_id,
                }

        bank_line = BankLine()
        import_run._native_expense_settlement_remove_exchange_candidates(
            bank_line,
            TargetLine(),
        )

        self.assertEqual(
            bank_line.reconcile_data_info["data"],
            [{"reference": "account.move.line;42", "id": 42}],
        )

    def test_native_expense_settlement_accepts_only_complete_auto_match(self):
        import_run = self.env["rebuild.account.import.run"]

        self.assertTrue(import_run._native_expense_settlement_auto_matched([[1], [2]]))
        self.assertFalse(import_run._native_expense_settlement_auto_matched([[], []]))
        with self.assertRaisesRegex(ValueError, "only part"):
            import_run._native_expense_settlement_auto_matched([[1], []])

    def test_native_general_reconciliation_requires_one_new_partial(self):
        import_run = self.env["rebuild.account.import.run"]
        created = [object()]

        self.assertIs(
            import_run._native_general_reconciliation_single_created_partial(
                created,
                990042,
            ),
            created,
        )
        with self.assertRaisesRegex(ValueError, "source 990042, got 0"):
            import_run._native_general_reconciliation_single_created_partial(
                [],
                990042,
            )
        with self.assertRaisesRegex(ValueError, "source 990042, got 2"):
            import_run._native_general_reconciliation_single_created_partial(
                [object(), object()],
                990042,
            )

    def test_native_general_reconciliation_selects_unrepresented_standalone_entries(self):
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Standalone general-entry selection",
            "source_snapshot_id": "unit-standalone-general",
        })
        source_rows = [
            {
                "id": 990041,
                "date": fields.Date.from_string("2025-10-01"),
                "code": "MISC",
                "ref": "Already selected through a reconciliation edge",
            },
            {
                "id": 990042,
                "date": fields.Date.from_string("2025-10-01"),
                "code": "OUV",
                "ref": "Standalone opening entry",
            },
            {
                "id": 990043,
                "date": fields.Date.from_string("2025-10-31"),
                "code": "PAIE",
                "ref": "Owned by external bank reconciliation",
            },
        ]
        options = {
            "source_company_ids": [990001],
            "source_snapshot_id": "unit-standalone-general",
            "date_from": "2025-10-01",
            "date_to": "2026-06-30",
        }

        with patch.object(type(import_run), "_fetchall", return_value=source_rows):
            move_ids, stats = (
                import_run._native_general_reconciliation_standalone_move_ids(
                    object(),
                    options,
                    [990041],
                    [990043],
                )
            )

        self.assertEqual(move_ids, [990042])
        self.assertEqual(stats["source_operator_general_entry_count"], 3)
        self.assertEqual(stats["edge_general_entry_count"], 1)
        self.assertEqual(stats["downstream_bank_general_entry_count"], 1)
        self.assertEqual(stats["standalone_general_entry_count"], 1)
        self.assertEqual(
            stats["standalone_general_entry_examples"],
            [source_rows[1]],
        )

    def test_native_bank_categorization_converts_partner_suspense_candidate(self):
        import_run = self.env["rebuild.account.import.run"]
        payable_account = self._account(
            "T401229",
            "Track B direct payable",
            "liability_payable",
        )
        partner = self.env["res.partner"].create({
            "name": "Track B direct bank supplier",
        })
        partner.with_company(self.company).property_account_payable_id = (
            payable_account
        )
        journal = self._journal("bank")
        journal.reconcile_mode = "edit"
        bank_line = self.env["account.bank.statement.line"].create({
            "journal_id": journal.id,
            "date": fields.Date.today(),
            "partner_id": partner.id,
            "payment_ref": "Direct supplier allocation",
            "amount": -25.0,
        })

        import_run._native_bank_categorization_apply(
            bank_line,
            {
                "id": 990229,
                "payment_ref": "Direct supplier allocation",
                "counterpart_name": "Direct supplier allocation",
                "counterpart_balance": 25.0,
                "counterpart_amount_currency": 25.0,
            },
            payable_account,
            partner,
            self.company.currency_id,
            False,
        )

        counterpart = bank_line.line_ids.filtered(
            lambda line: line.account_id != journal.default_account_id,
        )
        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(len(counterpart), 1)
        self.assertEqual(counterpart.account_id, payable_account)
        self.assertEqual(counterpart.partner_id, partner)
        self.assertEqual(counterpart.balance, 25.0)

    def test_native_external_bank_categorization_preserves_multiple_lines(self):
        import_run = self.env["rebuild.account.import.run"]
        first_account = self._account(
            "T627229",
            "Track B external bank first allocation",
            "expense",
        )
        second_account = self._account(
            "T658229",
            "Track B external bank second allocation",
            "expense",
        )
        journal = self._journal("bank")
        journal.reconcile_mode = "edit"
        bank_line = self.env["account.bank.statement.line"].create({
            "journal_id": journal.id,
            "date": fields.Date.today(),
            "payment_ref": "External multi-line allocation",
            "amount": -100.0,
        })
        source_lines = [
            {
                "source_bank_statement_line_id": 990230,
                "id": 990231,
                "account_id": 990241,
                "partner_id": False,
                "currency_id": 990251,
                "name": "First external allocation",
                "balance": 60.0,
                "amount_currency": 60.0,
                "analytic_distribution": False,
            },
            {
                "source_bank_statement_line_id": 990230,
                "id": 990232,
                "account_id": 990242,
                "partner_id": False,
                "currency_id": 990251,
                "name": "Second external allocation",
                "balance": 40.0,
                "amount_currency": 40.0,
                "analytic_distribution": False,
            },
        ]

        import_run._native_bank_external_categorize(
            bank_line,
            source_lines,
            {990241: first_account, 990242: second_account},
            {},
            {990251: self.company.currency_id},
            {},
        )

        counterpart = bank_line.line_ids.filtered(
            lambda line: line.account_id != journal.default_account_id,
        )
        self.assertTrue(bank_line.is_reconciled)
        self.assertEqual(len(counterpart), 2)
        self.assertEqual(set(counterpart.mapped("account_id")), {
            first_account,
            second_account,
        })
        self.assertEqual(sorted(counterpart.mapped("balance")), [40.0, 60.0])

    def test_native_external_bank_stages_and_restores_journal_suspense(self):
        source_suspense = self._account(
            "T471990",
            "Unit source journal suspense",
            "asset_current",
        )
        source_suspense.reconcile = True
        self.company.account_journal_suspense_account_id = source_suspense
        journal = self._journal("bank")
        journal.suspense_account_id = source_suspense
        import_run = self.env["rebuild.account.import.run"]

        staged = import_run._native_bank_external_stage_journal_suspense(
            {990013: journal},
            [{"journal_id": 990013}],
        )

        self.assertIn(journal.id, staged)
        self.assertEqual(staged[journal.id]["source_suspense"], source_suspense)
        self.assertEqual(journal.suspense_account_id.code, "TBSUSP")
        self.assertNotEqual(journal.suspense_account_id, source_suspense)
        self.assertTrue(journal.suspense_account_id.reconcile)
        import_run._native_bank_external_restore_journal_suspense(staged)
        self.assertEqual(journal.suspense_account_id, source_suspense)
        self.assertEqual(
            self.company.account_journal_suspense_account_id,
            source_suspense,
        )

    def test_native_external_bank_classifies_cutoff_boundaries(self):
        import_run = self.env["rebuild.account.import.run"]

        self.assertEqual(
            import_run._native_bank_external_boundary_kind(
                {
                    "endpoint_state": "draft",
                    "endpoint_move_type": "in_invoice",
                    "endpoint_date": fields.Date.to_date("2026-06-01"),
                    "endpoint_bank_statement_line_id": False,
                },
                [],
            ),
            "draft_document_prepayment",
        )
        self.assertEqual(
            import_run._native_bank_external_boundary_kind(
                {
                    "endpoint_state": "posted",
                    "endpoint_move_type": "out_invoice",
                    "endpoint_date": fields.Date.to_date("2026-07-01"),
                    "endpoint_bank_statement_line_id": False,
                },
                [],
            ),
            "future_document_prepayment",
        )

    def test_native_external_bank_reuses_exact_bounded_counterpart(self):
        import_run = self.env["rebuild.account.import.run"]
        edge = {
            "endpoint_state": "posted",
            "endpoint_move_type": "entry",
            "endpoint_date": fields.Date.to_date("2026-02-24"),
            "endpoint_bank_statement_line_id": 990301,
            "endpoint_source_line_id": 990302,
        }

        self.assertEqual(
            import_run._native_bank_external_boundary_kind(edge, []),
            "preexisting_bounded_bank_aggregate",
        )
        self.assertFalse(
            import_run._native_bank_external_boundary_kind(
                edge,
                [],
                {990302},
            ),
        )

    def test_reconcile_shortcut_uses_compatible_kanban_workbench(self):
        action = self.env.ref("rebuild_account_migration.action_rebuild_account_reconcile_bank_transactions")
        reconcile_view = self.env.ref("account_reconcile_oca.bank_statement_line_reconcile_view")
        card_arch = reconcile_view.arch_db.partition("<templates>")[2]

        self.assertEqual(action.view_mode, "kanban,list")
        self.assertEqual(action.view_ids[0].view_id, reconcile_view)
        self.assertIn("'view_ref': 'account_reconcile_oca.bank_statement_line_form_reconcile_view'", action.context)
        self.assertEqual(etree.fromstring(reconcile_view.arch_db).get("create"), "0")
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

    def test_import_enables_company_cash_basis_setting_for_cash_basis_taxes(self):
        france = self.env.ref("base.fr")
        company = self.env["res.company"].create({
            "name": "Unit cash basis company",
            "currency_id": self.company.currency_id.id,
            "country_id": france.id,
            "account_fiscal_country_id": france.id,
            "tax_exigibility": False,
        })
        tax_group = self.env["account.tax.group"].create({
            "name": "Unit VAT group",
            "company_id": company.id,
        })
        transition_account = self.env["account.account"].create({
            "code": "445UNIT",
            "name": "Unit VAT transition",
            "account_type": "asset_current",
            "reconcile": True,
            "company_ids": [Command.set([company.id])],
        })
        self.env["account.tax"].with_company(company).create({
            "name": "Unit cash basis VAT",
            "amount_type": "percent",
            "amount": 20.0,
            "type_tax_use": "sale",
            "tax_group_id": tax_group.id,
            "company_id": company.id,
            "country_id": france.id,
            "tax_exigibility": "on_payment",
            "cash_basis_transition_account_id": transition_account.id,
        })
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Cash basis settings sync",
            "source_snapshot_id": "unit-snapshot",
        })

        updated_companies = import_run._sync_company_cash_basis_flags({990001: company})

        self.assertEqual(updated_companies, company)
        self.assertTrue(company.tax_exigibility)
        self.assertIn("Tax definitions were not changed", company.rebuild_import_note)

    def test_import_currency_rates_preserves_native_source_rate_and_trace(self):
        eur = self.env.ref("base.EUR")
        usd = self.env.ref("base.USD")
        company = self.env["res.company"].create({
            "name": "Unit currency replay company",
            "currency_id": eur.id,
        })
        date = fields.Date.from_string("2098-01-15")
        retrieved_at = fields.Datetime.from_string("2098-01-16 08:30:00")
        existing_rate = self.env["res.currency.rate"].create({
            "name": date,
            "rate": 1.20,
            "currency_id": usd.id,
            "company_id": company.id,
        })
        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Currency rate replay",
            "source_snapshot_id": "unit-currency-snapshot",
        })
        options = {
            "source_database": "unit-source",
            "source_snapshot_id": "unit-currency-snapshot",
        }
        row = {
            "id": 990815,
            "name": date,
            "rate": 1.25,
            "currency_id": 990001,
            "company_id": 990101,
            "create_date": retrieved_at,
            "write_date": retrieved_at,
            "source_provider": "ecb",
        }

        stats = import_run._upsert_currency_rate_rows(
            [row],
            options,
            {990101: company},
            {990001: usd},
        )

        self.assertEqual(stats["source_currency_rate_count"], 1)
        self.assertEqual(stats["imported_currency_rate_count"], 1)
        self.assertEqual(stats["reused_natural_key_count"], 1)
        self.assertEqual(stats["providers"], ["ecb"])
        self.assertEqual(stats["currencies"], ["USD"])
        self.assertEqual(existing_rate.rebuild_source_model, "res.currency.rate")
        self.assertEqual(existing_rate.rebuild_source_id, 990815)
        self.assertEqual(existing_rate.rebuild_source_snapshot, "unit-currency-snapshot")
        self.assertEqual(existing_rate.rebuild_rate_provider, "ecb")
        self.assertEqual(existing_rate.rebuild_rate_retrieved_at, retrieved_at)
        self.assertAlmostEqual(existing_rate.rate, 1.25)
        self.assertAlmostEqual(usd._convert(125.0, eur, company, date), 100.0)

        updated_row = {**row, "rate": 1.30}
        repeated_stats = import_run._upsert_currency_rate_rows(
            [updated_row],
            options,
            {990101: company},
            {990001: usd},
        )
        self.assertEqual(repeated_stats["reused_natural_key_count"], 0)
        self.assertEqual(self.env["res.currency.rate"].search_count([
            ("rebuild_source_model", "=", "res.currency.rate"),
            ("rebuild_source_id", "=", 990815),
        ]), 1)
        self.assertAlmostEqual(existing_rate.rate, 1.30)

    def test_ecb_reference_rate_provider_is_idempotent_and_traced(self):
        eur = self.env.ref("base.EUR")
        usd = self.env.ref("base.USD")
        gbp = self.env.ref("base.GBP")
        usd.active = True
        gbp.active = True
        company = self.env["res.company"].create({
            "name": "Unit ECB reference-rate company",
            "currency_id": eur.id,
            "rebuild_currency_rate_provider": "ecb",
            "rebuild_currency_rate_auto_update": True,
        })
        retrieved_at = fields.Datetime.from_string(
            "2026-07-22 16:05:00",
        )
        payload = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope
    xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
    xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube>
    <Cube time="2026-07-22">
      <Cube currency="USD" rate="1.1408"/>
      <Cube currency="GBP" rate="0.85340"/>
    </Cube>
  </Cube>
</gesmes:Envelope>"""

        result = company._rebuild_update_ecb_currency_rates(
            payload=payload,
            retrieved_at=retrieved_at,
        )

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["provider"], "ecb")
        self.assertEqual(result["reference_date"], "2026-07-22")
        self.assertIn("USD", result["updated_currency_codes"])
        self.assertIn("GBP", result["updated_currency_codes"])
        usd_rate = self.env["res.currency.rate"].search([
            ("company_id", "=", company.id),
            ("currency_id", "=", usd.id),
            ("name", "=", "2026-07-22"),
        ])
        gbp_rate = self.env["res.currency.rate"].search([
            ("company_id", "=", company.id),
            ("currency_id", "=", gbp.id),
            ("name", "=", "2026-07-22"),
        ])
        self.assertEqual(len(usd_rate), 1)
        self.assertEqual(len(gbp_rate), 1)
        self.assertAlmostEqual(usd_rate.rate, 1.1408)
        self.assertAlmostEqual(gbp_rate.rate, 0.85340)
        self.assertEqual(usd_rate.rebuild_rate_provider, "ecb")
        self.assertEqual(
            usd_rate.rebuild_rate_retrieved_at,
            retrieved_at,
        )
        self.assertEqual(
            company.rebuild_currency_rate_last_sync_status,
            "passed",
        )
        self.assertEqual(
            company.rebuild_currency_rate_last_reference_date,
            fields.Date.from_string("2026-07-22"),
        )

        repeated = company._rebuild_update_ecb_currency_rates(
            payload=payload,
            retrieved_at=retrieved_at,
        )

        self.assertEqual(repeated["created_count"], 0)
        self.assertGreaterEqual(repeated["updated_count"], 2)
        self.assertEqual(self.env["res.currency.rate"].search_count([
            ("company_id", "=", company.id),
            ("name", "=", "2026-07-22"),
            ("currency_id", "in", [usd.id, gbp.id]),
        ]), 2)

    def test_ecb_reference_rate_provider_preserves_source_traced_rate(self):
        eur = self.env.ref("base.EUR")
        usd = self.env.ref("base.USD")
        usd.active = True
        company = self.env["res.company"].create({
            "name": "Unit ECB source-preservation company",
            "currency_id": eur.id,
            "rebuild_currency_rate_provider": "ecb",
        })
        source_rate = self.env["res.currency.rate"].create({
            "name": "2026-07-22",
            "currency_id": usd.id,
            "company_id": company.id,
            "rate": 1.1394,
            "rebuild_source_model": "res.currency.rate",
            "rebuild_source_id": 990022,
            "rebuild_source_snapshot": "unit-source-rate",
            "rebuild_rate_provider": "ecb",
        })
        payload = b"""<gesmes:Envelope
    xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
    xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube><Cube time="2026-07-22">
    <Cube currency="USD" rate="1.1408"/>
  </Cube></Cube>
</gesmes:Envelope>"""

        result = company._rebuild_update_ecb_currency_rates(
            payload=payload,
            retrieved_at="2026-07-22 16:05:00",
        )

        self.assertEqual(result["preserved_source_count"], 1)
        self.assertAlmostEqual(source_rate.rate, 1.1394)
        self.assertEqual(source_rate.rebuild_source_id, 990022)

    def test_currency_rate_automation_menu_and_permissions(self):
        menu = self.env.ref(
            "rebuild_account_migration.menu_rebuild_currency_rate_update",
        )
        action = self.env.ref(
            "rebuild_account_migration."
            "action_rebuild_currency_rate_update_wizard",
        )
        cron = self.env.ref(
            "rebuild_account_migration."
            "ir_cron_rebuild_currency_rate_provider",
        )
        self.assertEqual(
            menu.parent_id,
            self.env.ref("account.menu_finance_configuration"),
        )
        self.assertEqual(menu.action, action)
        self.assertTrue(cron.active)
        self.assertEqual(cron.interval_number, 1)
        self.assertEqual(cron.interval_type, "days")
        reviewer = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Currency Rate Reviewer",
            "login": "currency.rate.reviewer@example.invalid",
            "email": "currency.rate.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        wizard_model = self.env[
            "rebuild.currency.rate.update.wizard"
        ].with_user(reviewer)
        self.assertFalse(wizard_model.has_access("create"))
        self.assertFalse(wizard_model.has_access("write"))

    def test_native_replay_uses_supported_manual_tax_metadata(self):
        self.company.tax_calculation_rounding_method = "round_globally"
        expense_account = self._account("T606441", "Track B expense", "expense")
        payable_account = self._account("T401266", "Track B payable", "liability_payable")
        tax_account = self._account("T445321", "Track B input VAT", "asset_current")
        expense_account.write({
            "rebuild_source_model": "account.account",
            "rebuild_source_id": 441,
            "rebuild_source_snapshot": "unit-track-b",
        })
        payable_account.write({
            "rebuild_source_model": "account.account",
            "rebuild_source_id": 266,
            "rebuild_source_snapshot": "unit-track-b",
        })
        tax_account.write({
            "rebuild_source_model": "account.account",
            "rebuild_source_id": 321,
            "rebuild_source_snapshot": "unit-track-b",
        })
        partner = self.env["res.partner"].create({"name": "Track B supplier"})
        partner.with_company(self.company).property_account_payable_id = payable_account
        tax_group = self.env["account.tax.group"].create({
            "name": "Track B VAT",
            "company_id": self.company.id,
        })

        def make_tax(source_id, amount):
            return self.env["account.tax"].create({
                "name": f"Track B {amount}%",
                "company_id": self.company.id,
                "tax_group_id": tax_group.id,
                "type_tax_use": "purchase",
                "amount_type": "percent",
                "amount": amount,
                "invoice_repartition_line_ids": [
                    Command.create({"repartition_type": "base"}),
                    Command.create({
                        "repartition_type": "tax",
                        "factor_percent": 100.0,
                        "account_id": tax_account.id,
                    }),
                ],
                "refund_repartition_line_ids": [
                    Command.create({"repartition_type": "base"}),
                    Command.create({
                        "repartition_type": "tax",
                        "factor_percent": 100.0,
                        "account_id": tax_account.id,
                    }),
                ],
                "rebuild_source_model": "account.tax",
                "rebuild_source_id": source_id,
                "rebuild_source_snapshot": "unit-track-b",
            })

        import_run = self.env["rebuild.account.import.run"].create({
            "name": "Track B native tax replay",
            "source_snapshot_id": "unit-track-b",
        })
        journal = self._journal("purchase")

        def make_move(source_move_id, source_line_id, price_unit, tax):
            return self.env["account.move"].create({
                "move_type": "in_invoice",
                "journal_id": journal.id,
                "company_id": self.company.id,
                "partner_id": partner.id,
                "invoice_date": fields.Date.from_string("2026-02-25"),
                "invoice_line_ids": [
                    Command.create({
                        "name": "Track B taxable line",
                        "account_id": expense_account.id,
                        "quantity": 1.0,
                        "price_unit": price_unit,
                        "tax_ids": [Command.set(tax.ids)],
                        "rebuild_source_model": "account.move.line.native_engine_input",
                        "rebuild_source_id": source_line_id,
                        "rebuild_source_snapshot": "unit-track-b",
                    }),
                    Command.create({
                        "name": "Accountless note",
                        "display_type": "line_note",
                    }),
                ],
                "rebuild_source_model": "account.move.native_engine_replay",
                "rebuild_source_id": source_move_id,
                "rebuild_source_snapshot": "unit-track-b",
            })

        tax_20 = make_tax(5, 20.0)
        rounding_move = make_move(5860, 20715, 49.42, tax_20)
        self.assertEqual(rounding_move.amount_tax, 9.88)
        evidence = import_run._native_replay_apply_manual_tax_override(
            rounding_move,
            [{
                "id": 20715,
                "move_id": 5860,
                "display_type": "product",
                "quantity": 1.0,
                "price_unit": 49.42,
                "discount": 0.0,
                "price_subtotal": 49.42,
                "price_total": 59.30,
                "balance": 49.42,
                "tax_ids": [5],
            }],
            {5: {"balance": 9.90, "amount_currency": 9.90, "tax_base_amount": 49.42}},
            {5: tax_20},
        )
        self.assertEqual(evidence["classification"], "supported_native_manual_tax_override")
        self.assertEqual(rounding_move.amount_untaxed, 49.42)
        self.assertEqual(rounding_move.amount_tax, 9.90)
        self.assertEqual(rounding_move.amount_total, 59.32)
        rounding_move.action_post()
        self.assertNotIn("0", import_run._native_replay_target_account_totals(rounding_move))

        tax_5_5 = make_tax(8, 5.5)
        included_move = make_move(5391, 11052, 37.00, tax_5_5)
        import_run._native_replay_apply_manual_tax_override(
            included_move,
            [{
                "id": 11052,
                "move_id": 5391,
                "display_type": "product",
                "quantity": 1.0,
                "price_unit": 37.00,
                "discount": 0.0,
                "price_subtotal": 35.07,
                "price_total": 37.00,
                "balance": 35.07,
                "tax_ids": [8],
            }],
            {8: {"balance": 1.93, "amount_currency": 1.93, "tax_base_amount": 35.07}},
            {8: tax_5_5},
        )
        self.assertEqual(included_move.amount_untaxed, 35.07)
        self.assertEqual(included_move.amount_tax, 1.93)
        self.assertEqual(included_move.amount_total, 37.00)

    def test_non_manager_fec_roles_are_limited_to_complete_test_files(self):
        reviewer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "FEC Reviewer",
            "login": "fec.reviewer@example.invalid",
            "email": "fec.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        operator = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "FEC Finance Operator",
            "login": "fec.operator@example.invalid",
            "email": "fec.operator@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([
                self.env.ref("account.group_account_user").id,
            ])],
        })
        journal = self._journal()

        for user in (reviewer, operator):
            Wizard = self.env["l10n_fr.fec.export.wizard"].with_user(user)
            defaults = Wizard.default_get([
                "date_from",
                "date_to",
                "test_file",
                "export_type",
            ])
            wizard = Wizard.create({
                "date_from": "2024-01-10",
                "date_to": "2025-09-30",
                "test_file": False,
                "export_type": "nonofficial",
                "excluded_journal_ids": [Command.set([journal.id])],
            })

            self.assertTrue(defaults["test_file"])
            self.assertEqual(defaults["export_type"], "official")
            self.assertLessEqual(
                fields.Date.to_date(defaults["date_from"]),
                fields.Date.context_today(Wizard),
            )
            self.assertGreaterEqual(
                fields.Date.to_date(defaults["date_to"]),
                fields.Date.context_today(Wizard),
            )
            self.assertTrue(wizard.test_file)
            self.assertEqual(wizard.export_type, "official")
            self.assertFalse(wizard.excluded_journal_ids)
            self.assertFalse(wizard.rebuild_can_generate_official_fec)
            with self.assertRaisesRegex(
                UserError,
                "Only Accounting Managers",
            ):
                wizard.write({"test_file": False})

            self.env.cr.execute(
                """
                UPDATE l10n_fr_fec_export_wizard
                   SET test_file = false
                 WHERE id = %s
                """,
                [wizard.id],
            )
            wizard.invalidate_recordset(["test_file"])
            with self.assertRaisesRegex(
                UserError,
                "complete posted-entries FEC in test mode",
            ):
                wizard.with_user(user).generate_fec()

        fec_view = self.env.ref(
            "l10n_fr_account.fec_export_wizard_view",
        )._get_combined_arch()
        for field_name in (
            "test_file",
            "export_type",
        ):
            self.assertEqual(
                fec_view.xpath(f"//field[@name='{field_name}']")[0].get(
                    "readonly",
                ),
                "not rebuild_can_generate_official_fec",
            )
        self.assertEqual(
            fec_view.xpath("//field[@name='excluded_journal_ids']")[0].get(
                "readonly",
            ),
            "not rebuild_can_generate_official_fec or not test_file",
        )

        manager = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "FEC Accounting Manager",
            "login": "fec.manager@example.invalid",
            "email": "fec.manager@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([
                self.env.ref("account.group_account_manager").id,
            ])],
        })
        manager_wizard = self.env[
            "l10n_fr.fec.export.wizard"
        ].with_user(manager).create({
            "date_from": "2024-01-10",
            "date_to": "2025-09-30",
            "test_file": False,
            "export_type": "nonofficial",
            "excluded_journal_ids": [Command.set([journal.id])],
        })
        self.assertFalse(manager_wizard.test_file)
        self.assertEqual(manager_wizard.export_type, "official")
        self.assertFalse(manager_wizard.excluded_journal_ids)
        self.assertTrue(manager_wizard.rebuild_can_generate_official_fec)

    def test_accountant_reviewer_can_read_native_assets_but_not_change_them(self):
        reviewer = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Native Asset Reviewer",
            "login": "native.asset.reviewer@example.invalid",
            "email": "native.asset.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        profile = self.env["account.asset.profile"].create({
            "name": "Unit native asset profile",
            "account_asset_id": self._account(
                "T218301",
                "Unit native asset account",
                "asset_fixed",
            ).id,
            "account_depreciation_id": self._account(
                "T281831",
                "Unit native accumulated depreciation",
                "asset_fixed",
            ).id,
            "account_expense_depreciation_id": self._account(
                "T681131",
                "Unit native depreciation expense",
                "expense_depreciation",
            ).id,
            "journal_id": self._journal().id,
            "company_id": self.company.id,
            "method": "linear",
            "method_time": "number",
            "method_number": 36,
            "method_period": "month",
        })
        asset = self.env["account.asset"].create({
            "name": "Unit native asset",
            "purchase_value": 1200.0,
            "profile_id": profile.id,
            "date_start": "2025-01-01",
            "company_id": self.company.id,
        })

        self.assertEqual(
            asset.with_user(reviewer).read(["name"])[0]["name"],
            "Unit native asset",
        )
        self.assertEqual(
            profile.with_user(reviewer).read(["name"])[0]["name"],
            "Unit native asset profile",
        )
        self.assertEqual(
            self.env.ref(
                "account_asset_management.account_asset_action",
            ).res_model,
            "account.asset",
        )
        with self.assertRaises(AccessError):
            asset.with_user(reviewer).write({"name": "Changed"})
        with self.assertRaises(AccessError):
            self.env["account.asset"].with_user(reviewer).create({
                "name": "Forbidden asset",
                "purchase_value": 100.0,
                "profile_id": profile.id,
                "date_start": "2025-01-01",
                "company_id": self.company.id,
            })

    def test_native_deferral_posts_balanced_entry_and_analytic_item(self):
        journal = self._journal()
        expense_account = self._account(
            "T613210",
            "Unit deferred expense recognition",
            "expense",
        )
        deferral_account = self._account(
            "T486010",
            "Unit prepaid expenses",
            "asset_current",
        )
        original_move = self.env["account.move"].create({
            "move_type": "entry",
            "date": "2026-04-30",
            "journal_id": journal.id,
            "company_id": self.company.id,
            "ref": "Unit source bill",
        })
        plan = self.env["account.analytic.plan"].create({
            "name": "Unit deferral plan",
        })
        analytic_account = self.env["account.analytic.account"].create({
            "name": "Unit deferral analytic account",
            "plan_id": plan.id,
            "company_id": self.company.id,
        })
        deferral = self.env["rebuild.account.deferral"].create({
            "name": "Unit deferred expense",
            "schedule_type": "expense",
            "company_id": self.company.id,
            "original_move_id": original_move.id,
            "journal_id": journal.id,
            "deferral_account_id": deferral_account.id,
            "start_date": "2026-04-30",
            "end_date": "2026-05-31",
        })
        schedule_line = self.env["rebuild.account.deferral.line"].create({
            "name": "Unit deferral opening",
            "deferral_id": deferral.id,
            "date": "2026-04-30",
            "phase": "initial_deferral",
            "recognition_account_id": expense_account.id,
            "recognition_balance": -120.0,
            "recognition_amount_currency": -120.0,
            "deferral_balance": 120.0,
            "deferral_amount_currency": 120.0,
            "analytic_distribution": {
                str(analytic_account.id): 100.0,
            },
        })
        future_line = self.env["rebuild.account.deferral.line"].create({
            "name": "Unit deferral recognition",
            "deferral_id": deferral.id,
            "date": "2026-05-31",
            "phase": "recognition",
            "recognition_account_id": expense_account.id,
            "recognition_balance": 120.0,
            "recognition_amount_currency": 120.0,
            "deferral_balance": -120.0,
            "deferral_amount_currency": -120.0,
            "analytic_distribution": {
                str(analytic_account.id): 100.0,
            },
        })

        deferral.action_start()
        schedule_line.action_post()

        self.assertEqual(deferral.state, "running")
        self.assertEqual(deferral.posted_line_count, 1)
        self.assertEqual(deferral.remaining_line_count, 1)
        self.assertEqual(schedule_line.state, "posted")
        self.assertEqual(schedule_line.move_id.state, "posted")
        self.assertEqual(
            round(sum(schedule_line.move_id.line_ids.mapped("balance")), 2),
            0.0,
        )
        recognition_line = schedule_line.move_id.line_ids.filtered(
            lambda line: line.account_id == expense_account,
        )
        self.assertEqual(len(recognition_line), 1)
        self.assertEqual(recognition_line.balance, -120.0)
        self.assertEqual(
            recognition_line.analytic_distribution,
            {str(analytic_account.id): 100.0},
        )
        analytic_lines = self.env["account.analytic.line"].search([
            ("move_line_id", "=", recognition_line.id),
        ])
        self.assertIn(
            analytic_account,
            analytic_lines._get_analytic_accounts(),
        )
        future_line.action_post()
        self.assertEqual(deferral.state, "closed")
        self.assertEqual(deferral.posted_line_count, 2)
        self.assertEqual(deferral.remaining_line_count, 0)

    def test_native_deferrals_are_read_only_for_accountant_reviewer(self):
        reviewer = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Native Deferral Reviewer",
            "login": "native.deferral.reviewer@example.invalid",
            "email": "native.deferral.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        journal = self._journal()
        deferral = self.env["rebuild.account.deferral"].create({
            "name": "Reviewer unit deferral",
            "schedule_type": "expense",
            "company_id": self.company.id,
            "original_move_id": self.env["account.move"].create({
                "move_type": "entry",
                "date": "2026-05-01",
                "journal_id": journal.id,
                "company_id": self.company.id,
            }).id,
            "journal_id": journal.id,
            "deferral_account_id": self._account(
                "T486011",
                "Reviewer unit prepaid expenses",
                "asset_current",
            ).id,
            "start_date": "2026-05-01",
            "end_date": "2026-06-30",
        })
        schedule_line = self.env["rebuild.account.deferral.line"].create({
            "name": "Reviewer unit schedule",
            "deferral_id": deferral.id,
            "date": "2026-05-31",
            "recognition_account_id": self._account(
                "T613211",
                "Reviewer unit recognition",
                "expense",
            ).id,
            "recognition_balance": 50.0,
            "recognition_amount_currency": 50.0,
            "deferral_balance": -50.0,
            "deferral_amount_currency": -50.0,
        })

        self.assertEqual(
            deferral.with_user(reviewer).read(["name"])[0]["name"],
            "Reviewer unit deferral",
        )
        self.assertEqual(
            schedule_line.with_user(reviewer).read(["name"])[0]["name"],
            "Reviewer unit schedule",
        )
        with self.assertRaises(AccessError):
            deferral.with_user(reviewer).action_start()
        with self.assertRaises(AccessError):
            schedule_line.with_user(reviewer).action_post()
        with self.assertRaises(AccessError):
            deferral.with_user(reviewer).write({"name": "Changed"})

    def test_native_deferral_navigation_and_mutation_controls(self):
        menu = self.env.ref(
            "rebuild_account_migration.menu_rebuild_account_deferral",
        )
        self.assertEqual(
            menu.parent_id,
            self.env.ref("account.account_closing_menu"),
        )
        self.assertEqual(menu.name, "Deferrals")
        self.assertEqual(
            menu.action,
            self.env.ref(
                "rebuild_account_migration.action_rebuild_account_deferral",
            ),
        )

        view = self.env.ref(
            "rebuild_account_migration.view_rebuild_account_deferral_form",
        )
        mutation_button_names = {
            "action_start",
            "action_post_due",
            "action_post",
        }
        mutation_buttons = [
            button
            for button in view._get_combined_arch().xpath("//button")
            if button.get("name") in mutation_button_names
        ]
        self.assertEqual(
            {button.get("name") for button in mutation_buttons},
            mutation_button_names,
        )
        for button in mutation_buttons:
            self.assertEqual(
                button.get("groups"),
                "account.group_account_manager",
            )

    def test_native_analytic_corrections_are_a_read_only_audit_surface(self):
        menu = self.env.ref(
            "rebuild_account_migration.menu_rebuild_account_analytic_override",
        )
        self.assertEqual(
            menu.parent_id,
            self.env.ref(
                "rebuild_account_migration.menu_rebuild_account_migration",
            ),
        )
        self.assertEqual(
            menu.action.res_model,
            "rebuild.account.analytic.override",
        )
        reviewer = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Native Analytic Reviewer",
            "login": "native.analytic.reviewer@example.invalid",
            "email": "native.analytic.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        model = self.env[
            "rebuild.account.analytic.override"
        ].with_user(reviewer)
        self.assertTrue(model.has_access("read"))
        self.assertFalse(model.has_access("write"))
        self.assertFalse(model.has_access("create"))
        self.assertFalse(model.has_access("unlink"))
        for model_name in (
            "account.analytic.plan",
            "account.analytic.account",
            "account.analytic.line",
        ):
            analytic_model = self.env[model_name].with_user(reviewer)
            self.assertTrue(analytic_model.has_access("read"))
            self.assertFalse(analytic_model.has_access("write"))
            self.assertFalse(analytic_model.has_access("create"))
            self.assertFalse(analytic_model.has_access("unlink"))

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
        self.assertEqual(action["name"], "Trial Balance")
        self.assertEqual(action["target"], "current")
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
        self.assertIn(("company_id", "in", [self.company.id]), source_action["domain"])
        self.assertNotIn(
            ("move_id.date", ">=", fields.Date.from_string("2099-01-01")),
            source_action["domain"],
        )
        self.assertIn(
            ("move_id.date", "<=", fields.Date.from_string("2099-12-31")),
            source_action["domain"],
        )
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

    def test_dynamic_report_workbench_period_comparison_and_native_scope(self):
        expense_account = self._account(
            "T625100",
            "Dynamic report expense",
            "expense",
        )
        payable_account = self._account(
            "T401100",
            "Dynamic report payable",
            "liability_payable",
        )
        journal = self._journal()

        def create_move(move_date, amount, *, posted):
            move = self.env["account.move"].create({
                "move_type": "entry",
                "journal_id": journal.id,
                "date": move_date,
                "company_id": self.company.id,
                "line_ids": [
                    Command.create({
                        "name": "Dynamic expense",
                        "account_id": expense_account.id,
                        "debit": amount,
                    }),
                    Command.create({
                        "name": "Dynamic payable",
                        "account_id": payable_account.id,
                        "credit": amount,
                    }),
                ],
            })
            if posted:
                move.action_post()
            return move

        create_move("2025-01-15", 80.0, posted=True)
        create_move("2026-01-15", 100.0, posted=True)
        create_move("2026-01-20", 50.0, posted=False)
        wizard = self.env[
            "rebuild.account.report.export.wizard"
        ].create({
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "report_type": "trial_balance",
            "period_preset": "month",
            "period_anchor_date": "2026-01-15",
            "comparison_mode": "previous_year",
            "target_move": "posted",
            "data_scope": "native",
            "group_by": "account",
            "export_format": "xlsx",
        })

        action = wizard.action_apply_period()

        self.assertEqual(action["target"], "current")
        self.assertEqual(str(wizard.date_from), "2026-01-01")
        self.assertEqual(str(wizard.date_to), "2026-01-31")
        self.assertEqual(
            str(wizard.comparison_date_from),
            "2025-01-01",
        )
        self.assertEqual(
            str(wizard.comparison_date_to),
            "2025-01-31",
        )
        self.assertEqual(wizard.draft_entry_count, 1)
        self.assertIn("excluded", wizard.preview_warning)
        expense_group = wizard.preview_line_ids.filtered(
            lambda line: line.is_group
            and line.account_code == "T625100",
        )
        self.assertEqual(len(expense_group), 1)
        self.assertAlmostEqual(expense_group.opening_balance, 80.0)
        self.assertAlmostEqual(expense_group.debit, 100.0)
        self.assertAlmostEqual(expense_group.credit, 0.0)
        self.assertAlmostEqual(expense_group.movement, 100.0)
        self.assertAlmostEqual(expense_group.closing_balance, 180.0)
        self.assertAlmostEqual(expense_group.balance, 180.0)
        self.assertAlmostEqual(
            expense_group.comparison_value,
            80.0,
        )
        self.assertAlmostEqual(expense_group.difference, 100.0)

        wizard.write({"target_move": "all"})
        wizard.action_preview_report()
        expense_group = wizard.preview_line_ids.filtered(
            lambda line: line.is_group
            and line.account_code == "T625100",
        )
        self.assertAlmostEqual(expense_group.debit, 150.0)
        self.assertAlmostEqual(expense_group.movement, 150.0)
        self.assertAlmostEqual(expense_group.closing_balance, 230.0)
        self.assertAlmostEqual(expense_group.balance, 230.0)
        self.assertIn("included", wizard.preview_warning)

        wizard.write({
            "target_move": "posted",
            "data_scope": "imported",
        })
        imported_rows = wizard._report_rows()
        self.assertFalse([
            row
            for row in imported_rows
            if row.get("account_code") == "T625100"
        ])

    def test_dynamic_report_workbench_multi_company_metadata(self):
        second_company = self.env["res.company"].create({
            "name": "Dynamic Report Second Company",
            "currency_id": self.company.currency_id.id,
        })
        self.env.user.write({
            "company_ids": [Command.link(second_company.id)],
        })
        wizard = self.env[
            "rebuild.account.report.export.wizard"
        ].create({
            "company_id": self.company.id,
            "company_ids": [
                Command.set([self.company.id, second_company.id]),
            ],
            "report_type": "trial_balance",
            "date_from": "2099-01-01",
            "date_to": "2099-12-31",
            "target_move": "posted",
            "data_scope": "native",
            "export_format": "csv",
        })

        wizard.action_preview_report()

        metadata = json.loads(wizard.preview_metadata)
        self.assertEqual(
            {company["name"] for company in metadata["companies"]},
            {self.company.name, second_company.name},
        )

    def test_accountant_reviewer_can_preview_and_export_dynamic_reports(self):
        reviewer = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Dynamic Report Reviewer",
            "login": "dynamic.report.reviewer@example.invalid",
            "email": "dynamic.report.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        Wizard = self.env[
            "rebuild.account.report.export.wizard"
        ].with_user(reviewer)
        wizard = Wizard.create({
            "company_id": self.company.id,
            "report_type": "trial_balance",
            "date_from": "2099-01-01",
            "date_to": "2099-12-31",
            "target_move": "posted",
            "data_scope": "native",
            "export_format": "xlsx",
        })

        wizard.action_preview_report()
        wizard.action_preview_report()
        wizard.action_generate_export()

        self.assertEqual(wizard.preview_line_ids.label, "No rows for the selected report filters")
        self.assertTrue(base64.b64decode(wizard.export_file).startswith(b"PK"))
        analytic_wizard = Wizard.create({
            "company_id": self.company.id,
            "report_type": "analytic_report",
            "date_from": "2099-01-01",
            "date_to": "2099-12-31",
            "target_move": "posted",
            "data_scope": "native",
            "export_format": "csv",
        })
        analytic_wizard.action_preview_report()
        self.assertEqual(
            analytic_wizard.preview_line_ids.label,
            "No rows for the selected report filters",
        )

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
            self.assertEqual(action.target, "current")
            self.assertEqual(context["default_report_type"], report_type)
            self.assertEqual(context["default_export_format"], export_format)
        fec_action = self.env.ref(
            "rebuild_account_migration.action_rebuild_account_report_export_fec",
        )
        self.assertEqual(fec_action.name, "FEC")
        self.assertEqual(fec_action.res_model, "l10n_fr.fec.export.wizard")
        self.assertEqual(fec_action.target, "new")
        self.assertEqual(
            fec_action.view_id,
            self.env.ref("l10n_fr_account.fec_export_wizard_view"),
        )

    def test_canonical_report_client_loads_filters_and_downloads(self):
        Report = self.env["rebuild.account.report.export.wizard"]
        trial = Report.report_client_load(
            "trial_balance",
            {
                "date_from": "2099-01-01",
                "date_to": "2099-12-31",
            },
        )

        self.assertEqual(trial["report_type"], "trial_balance")
        self.assertEqual(trial["filters"]["group_by"], "section")
        self.assertEqual(
            trial["filters"]["company_id"],
            self.env.company.id,
        )
        self.assertTrue(trial["options"]["journals"])
        self.assertIn("analytic_accounts", trial["options"])
        self.assertTrue(trial["capabilities"]["period_presets"])
        self.assertTrue(trial["capabilities"]["comparison"])
        self.assertTrue(trial["capabilities"]["analytics"])
        self.assertEqual(
            trial["lines"][0]["label"],
            "No rows for the selected report filters",
        )
        self.assertFalse(trial["lines"][0]["can_drilldown"])

        journal = self._journal()
        filtered = Report.report_client_load(
            "trial_balance",
            {
                "journal_ids": [journal.id],
                "target_move": "all",
            },
            trial["wizard_id"],
        )
        self.assertEqual(filtered["filters"]["journal_ids"], [journal.id])
        self.assertEqual(filtered["filters"]["target_move"], "all")

        for export_format, signature in (
            ("pdf", b"%PDF"),
            ("xlsx", b"PK"),
        ):
            download = Report.report_client_export(
                filtered["wizard_id"],
                export_format,
            )
            wizard = Report.browse(filtered["wizard_id"])
            self.assertEqual(download["field"], "export_file")
            self.assertTrue(
                base64.b64decode(wizard.export_file).startswith(signature),
            )

        aged = Report.report_client_load(
            "aged_receivable",
            {
                "date_from": "2099-01-01",
                "date_to": "2099-12-31",
            },
        )
        self.assertEqual(aged["filters"]["group_by"], "none")
        self.assertEqual(
            [column["key"] for column in aged["columns"]],
            [
                "not_due",
                "bucket_1_30",
                "bucket_31_60",
                "bucket_61_90",
                "bucket_over_90",
                "total",
            ],
        )
        self.assertFalse(aged["capabilities"]["comparison"])
        self.assertFalse(aged["capabilities"]["group_by"])
        self.assertEqual(
            Report._tax_tag_display_name("08_base_rc"),
            "08 - taxable base (reverse charge)",
        )
        self.assertEqual(
            Report._tax_tag_display_name("I1_taxe"),
            "I1 - tax amount",
        )

        french_balance = Report.report_client_load(
            "french_balance_sheet_2024",
            {
                "date_from": "2099-01-01",
                "date_to": "2099-12-31",
            },
        )
        control = next(
            card
            for card in french_balance["summary"]["cards"]
            if card["label"] == "Balance control"
        )
        self.assertAlmostEqual(control["value"], 0.0, places=2)
        self.assertEqual(control["status"], "success")

    def test_canonical_asset_reports_use_native_assets_and_drill_down(self):
        asset_account = self._account(
            "T218399",
            "Unit native report asset",
            "asset_fixed",
        )
        profile = self.env["account.asset.profile"].create({
            "name": "T218399 — straight-line, 36 monthly periods",
            "account_asset_id": asset_account.id,
            "account_depreciation_id": self._account(
                "T281899",
                "Unit native report depreciation",
                "asset_fixed",
            ).id,
            "account_expense_depreciation_id": self._account(
                "T681199",
                "Unit native report depreciation expense",
                "expense_depreciation",
            ).id,
            "journal_id": self._journal().id,
            "company_id": self.company.id,
            "method": "linear",
            "method_time": "number",
            "method_number": 36,
            "method_period": "month",
        })
        asset = self.env["account.asset"].create({
            "name": "Unit native report asset",
            "purchase_value": 1200.0,
            "profile_id": profile.id,
            "date_start": "2025-01-01",
            "company_id": self.company.id,
            "rebuild_source_model": "account.asset",
            "rebuild_source_id": 991001,
            "rebuild_source_snapshot": "unit-native-asset-report",
            "rebuild_source_book_value": 1100.0,
        })
        self.env["account.asset.line"].create({
            "name": "Depreciation before migration",
            "asset_id": asset.id,
            "amount": 100.0,
            "line_date": "2025-09-30",
            "type": "depreciate",
            "init_entry": True,
            "rebuild_source_model": (
                "account.asset.imported_depreciation"
            ),
            "rebuild_source_id": 991001,
            "rebuild_source_snapshot": "unit-native-asset-report",
        })
        self.env["account.asset.line"].create({
            "name": "Unit planned depreciation",
            "asset_id": asset.id,
            "amount": 100.0,
            "line_date": "2025-10-31",
            "type": "depreciate",
            "rebuild_source_model": (
                "account.move.asset_depreciation_schedule"
            ),
            "rebuild_source_id": 991101,
            "rebuild_source_snapshot": "unit-native-asset-report",
        })
        Report = self.env["rebuild.account.report.export.wizard"]
        register = Report.report_client_load(
            "fixed_assets",
            {
                "date_from": "2025-10-01",
                "date_to": "2025-10-31",
            },
        )
        self.assertEqual(len(register["lines"]), 1)
        self.assertEqual(
            register["lines"][0]["label"],
            "Unit native report asset",
        )
        self.assertEqual(
            register["lines"][0]["values"]["accumulated_depreciation"],
            "100.00",
        )
        self.assertEqual(
            register["lines"][0]["values"]["imported_period_net_value"],
            "1100.00",
        )
        register_wizard = Report.browse(register["wizard_id"])
        register_action = register_wizard._preview_source_action(
            register_wizard.preview_line_ids,
        )
        self.assertEqual(register_action["res_model"], "account.asset")
        self.assertEqual(register_action["res_id"], asset.id)

        schedule = Report.report_client_load(
            "depreciation_schedule",
            {
                "date_from": "2025-10-01",
                "date_to": "2025-10-31",
            },
        )
        self.assertEqual(len(schedule["lines"]), 1)
        self.assertEqual(
            schedule["lines"][0]["values"]["representation_status"],
            "Planned",
        )
        schedule_wizard = Report.browse(schedule["wizard_id"])
        schedule_action = schedule_wizard._preview_source_action(
            schedule_wizard.preview_line_ids,
        )
        self.assertEqual(schedule_action["res_model"], "account.asset")
        self.assertEqual(schedule_action["res_id"], asset.id)

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

    def test_interactive_aged_receivable_payable_shortcuts_are_scoped(self):
        expected_actions = {
            "action_rebuild_oca_aged_receivable_wizard": (True, False),
            "action_rebuild_oca_aged_payable_wizard": (False, True),
        }
        for xmlid, (receivable_only, payable_only) in expected_actions.items():
            action = self.env.ref(f"rebuild_account_migration.{xmlid}")
            context = safe_eval(action.context or "{}")

            self.assertEqual(action.res_model, "aged.partner.balance.report.wizard")
            self.assertEqual(action.target, "new")
            self.assertEqual(context["default_date_from"], "2024-01-10")
            self.assertEqual(context["default_date_at"], "2025-09-30")
            self.assertEqual(context["default_target_move"], "posted")
            self.assertEqual(context["default_receivable_accounts_only"], receivable_only)
            self.assertEqual(context["default_payable_accounts_only"], payable_only)

    def test_primary_report_menus_open_canonical_interactive_reports(self):
        expected_menus = {
            "menu_rebuild_account_report_trial_balance_launcher": "rebuild_account_migration.action_rebuild_interactive_trial_balance",
            "menu_rebuild_account_report_general_ledger_launcher": "rebuild_account_migration.action_rebuild_interactive_general_ledger",
            "menu_rebuild_account_report_journal_report_launcher": "rebuild_account_migration.action_rebuild_interactive_journal_report",
            "menu_rebuild_account_report_open_items_launcher": "rebuild_account_migration.action_rebuild_interactive_open_items",
            "menu_rebuild_account_report_aged_receivable_launcher": "rebuild_account_migration.action_rebuild_interactive_aged_receivable",
            "menu_rebuild_account_report_aged_payable_launcher": "rebuild_account_migration.action_rebuild_interactive_aged_payable",
            "menu_rebuild_account_report_tax_launcher": "rebuild_account_migration.action_rebuild_interactive_tax_report",
        }
        for menu_xmlid, action_xmlid in expected_menus.items():
            menu = self.env.ref(f"rebuild_account_migration.{menu_xmlid}")

            self.assertEqual(menu.action, self.env.ref(action_xmlid))
            self.assertEqual(menu.action.type, "ir.actions.client")
            self.assertEqual(menu.action.tag, "rebuild_accounting_report")
        self.assertFalse(
            self.env.ref(
                "account_financial_report.menu_oca_reports",
            ).active,
        )
        hidden_competitors = [
            "account.menu_action_analytic_reporting",
            "rebuild_account_migration.menu_rebuild_account_report_fixed_asset_group_account_launcher",
            "rebuild_account_migration.menu_rebuild_account_report_tax_group_account_tax_launcher",
            "rebuild_account_migration.menu_rebuild_account_report_tax_group_tax_account_launcher",
            "rebuild_account_migration.menu_rebuild_account_report_ec_sales_launcher",
            "rebuild_account_migration.menu_rebuild_account_report_oss_sales_launcher",
            "rebuild_account_migration.menu_rebuild_account_report_oss_imports_launcher",
        ]
        for menu_xmlid in hidden_competitors:
            self.assertFalse(self.env.ref(menu_xmlid).active)

    def test_legal_statement_menu_uses_canonical_interactive_reports(self):
        balance_export_menu = self.env.ref("rebuild_account_migration.menu_rebuild_account_report_balance_sheet_launcher")
        profit_export_menu = self.env.ref("rebuild_account_migration.menu_rebuild_account_report_profit_loss_launcher")

        self.assertEqual(balance_export_menu.name, "Balance Sheet")
        self.assertEqual(profit_export_menu.name, "Profit and Loss")
        self.assertEqual(
            balance_export_menu.action,
            self.env.ref(
                "rebuild_account_migration.action_rebuild_interactive_balance_sheet",
            ),
        )
        self.assertEqual(
            profit_export_menu.action,
            self.env.ref(
                "rebuild_account_migration.action_rebuild_interactive_profit_loss",
            ),
        )
        self.assertEqual(balance_export_menu.sequence, 4)
        self.assertEqual(profit_export_menu.sequence, 5)

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
                "receivable_accounts_only",
                "payable_accounts_only",
            ])
            self.assertEqual(str(values["date_from"]), "2024-01-10")
            self.assertEqual(str(values["date_at"]), "2025-09-30")
            self.assertEqual(values["target_move"], "posted")
            self.assertTrue(values["receivable_accounts_only"])
            self.assertTrue(values["payable_accounts_only"])

            wizard = self.env[model_name].create(values)
            wizard.onchange_type_accounts_only()
            self.assertIn(receivable, wizard.account_ids)
            self.assertIn(payable, wizard.account_ids)

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
            "# Guide\n\nOpen [reports](reference/reports-and-filters.md).\n\n| A | B |\n| --- | --- |\n| `one` | two |\n",
            "README.md",
        )
        self.assertIn("<h1", rendered)
        self.assertIn("/usl/user-docs/reference/reports-and-filters.md", rendered)
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
        self.assertGreaterEqual(summary.journal_count, 0)
        self.assertEqual(summary.bank_transaction_count, 0)
        self.assertEqual(summary.open_receivable_count, 0)
        self.assertEqual(summary.open_payable_count, 0)

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
        self.assertEqual(report_action["context"]["default_company_ids"], [summary_company.id])
        self.assertEqual(report_action["context"]["default_report_type"], "trial_balance")
        self.assertEqual(report_action["context"]["default_data_scope"], "native")
        self.assertEqual(report_action["context"]["default_period_preset"], "year_to_date")
        self.assertEqual(report_action["context"]["default_target_move"], "posted")
        self.assertEqual(report_action["views"], [(False, "form")])

        guide_action = summary.action_open_user_guide()
        self.assertEqual(guide_action["type"], "ir.actions.act_url")
        self.assertEqual(guide_action["url"], "/usl/user-docs")

    def test_accounting_home_surfaces_daily_work_and_company_actions(self):
        home_company = self.env["res.company"].create({
            "name": "Operational Accounting Home Company",
            "currency_id": self.company.currency_id.id,
        })
        sales_journal = self.env["account.journal"].create({
            "name": "Home Customer Documents",
            "code": "HCU",
            "type": "sale",
            "company_id": home_company.id,
        })
        purchase_journal = self.env["account.journal"].create({
            "name": "Home Vendor Documents",
            "code": "HVE",
            "type": "purchase",
            "company_id": home_company.id,
        })
        self.env["account.move"].create({
            "move_type": "out_invoice",
            "journal_id": sales_journal.id,
            "company_id": home_company.id,
            "date": "2020-01-01",
        })
        self.env["account.move"].create({
            "move_type": "in_invoice",
            "journal_id": purchase_journal.id,
            "company_id": home_company.id,
            "date": "2020-01-01",
        })
        self.env.flush_all()

        home = self.env["rebuild.account.review.summary"].search([
            ("company_id", "=", home_company.id),
        ])
        self.assertTrue(home)
        self.assertGreaterEqual(home.journal_count, 2)
        self.assertEqual(home.draft_customer_document_count, 1)
        self.assertEqual(home.draft_vendor_document_count, 1)
        self.assertEqual(home.incomplete_document_count, 2)
        self.assertEqual(home.missing_vendor_attachment_count, 1)
        self.assertEqual(home.missing_expense_attachment_count, 0)
        self.assertEqual(home.stale_draft_document_count, 2)
        self.assertEqual(home.stale_draft_expense_count, 0)
        self.assertGreaterEqual(home.hygiene_attention_count, 5)
        self.assertEqual(home.hygiene_status, "attention")

        customer_action = home.action_open_customer_documents()
        self.assertIn(("company_id", "=", home_company.id), customer_action["domain"])
        self.assertIn(
            ("move_type", "in", ["out_invoice", "out_refund", "out_receipt"]),
            customer_action["domain"],
        )
        bank_action = home.action_open_bank_transactions()
        self.assertIn(("company_id", "=", home_company.id), bank_action["domain"])
        closing_action = home.action_open_closing_workspaces()
        self.assertIn(("company_id", "=", home_company.id), closing_action["domain"])
        declaration_action = home.action_open_declarations()
        self.assertIn(("company_id", "=", home_company.id), declaration_action["domain"])
        missing_vendor_action = home.action_open_missing_vendor_attachments()
        self.assertIn(
            ("company_id", "=", home_company.id),
            missing_vendor_action["domain"],
        )
        self.assertIn(
            ("message_main_attachment_id", "=", False),
            missing_vendor_action["domain"],
        )
        stale_document_action = home.action_open_stale_draft_documents()
        self.assertIn(
            ("company_id", "=", home_company.id),
            stale_document_action["domain"],
        )
        self.assertTrue(
            any(
                term[0] == "date" and term[1] == "<"
                for term in stale_document_action["domain"]
            ),
        )

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

    def test_french_tax_package_export_preserves_quantity_semantics(self):
        wizard = self.env["rebuild.account.report.export.wizard"].create({
            "company_id": self.company.id,
            "report_type": "french_tax_package",
            "date_from": "2024-01-10",
            "date_to": "2025-09-30",
            "target_move": "posted",
            "export_format": "xlsx",
            "group_by": "none",
        })
        columns = dict(wizard._report_export_columns([
            {
                "form_code": "2033-C-SD",
                "field_code": "2033_C_NOMBRE_IMMOBILISATIONS_SOURCE",
                "field_label": "Nombre d’immobilisations source représentées",
                "quantity": "3",
                "amount": "0.00",
                "rounded_amount": "0.00",
                "value_text": "3",
                "review_status": "ledger_derived",
                "source_reference": "Registre des immobilisations importé",
            },
        ]))

        self.assertEqual(columns["quantity"], "Quantity")
        self.assertEqual(columns["value_text"], "Value / note")

    def test_accountant_reviewer_can_prepare_test_fec_through_standard_and_custom_paths(self):
        reviewer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "FEC Reviewer",
            "login": "fec.reviewer@example.invalid",
            "email": "fec.reviewer@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([self.reviewer_group.id])],
        })
        operator = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "FEC Finance Operator",
            "login": "fec.operator@example.invalid",
            "email": "fec.operator@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([
                self.env.ref("account.group_account_user").id,
            ])],
        })

        for user in (reviewer, operator):
            standard_wizard = self.env[
                "l10n_fr.fec.export.wizard"
            ].with_user(user).create({
                "date_from": "2099-01-01",
                "date_to": "2099-12-31",
                "test_file": False,
                "export_type": "nonofficial",
            })
            self.assertTrue(standard_wizard.test_file)
            self.assertEqual(standard_wizard.export_type, "official")

            fec_wizard = self.env[
                "rebuild.account.report.export.wizard"
            ].with_user(user).create({
                "company_id": self.company.id,
                "report_type": "fec",
                "date_from": "2099-01-01",
                "date_to": "2099-12-31",
                "target_move": "posted",
                "export_format": "txt",
                "fec_test_mode": False,
            })
            action = fec_wizard.action_generate_export()

            self.assertEqual(
                action["res_model"],
                "rebuild.account.report.export.wizard",
            )
            self.assertEqual(action["res_id"], fec_wizard.id)
            self.assertTrue(fec_wizard.fec_test_mode)
            self.assertFalse(fec_wizard.can_generate_official_fec)
            self.assertTrue(fec_wizard.export_file)
            self.assertTrue(fec_wizard.export_filename.endswith(".txt"))
            metadata = json.loads(fec_wizard.export_metadata)
            self.assertEqual(metadata["report_type"], "fec")
            self.assertEqual(metadata["format"], "txt")
            self.assertEqual(
                metadata["validation"],
                "not_official_dgfip_validation",
            )

            with self.assertRaisesRegex(
                UserError,
                "Only an Accounting Manager",
            ):
                fec_wizard.write({"fec_test_mode": False})

        report_view = self.env.ref(
            "rebuild_account_migration."
            "view_rebuild_account_report_export_wizard_form",
        )._get_combined_arch()
        self.assertEqual(
            report_view.xpath("//field[@name='fec_test_mode']")[0].get(
                "readonly",
            ),
            "not can_generate_official_fec",
        )
        self.assertEqual(
            len(
                report_view.xpath(
                    "//notebook[@invisible='not export_file']"
                    "/page[@string='Download']",
                ),
            ),
            1,
        )
        self.assertFalse(
            report_view.xpath(
                "//notebook[@invisible='not preview_generated_at']"
                "/page[@string='Download']",
            ),
        )

        manager = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "FEC Export Manager",
            "login": "fec.export.manager@example.invalid",
            "email": "fec.export.manager@example.invalid",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([
                self.env.ref("account.group_account_manager").id,
            ])],
        })
        manager_wizard = self.env[
            "rebuild.account.report.export.wizard"
        ].with_user(manager).create({
            "company_id": self.company.id,
            "report_type": "fec",
            "date_from": "2099-01-01",
            "date_to": "2099-12-31",
            "target_move": "posted",
            "export_format": "txt",
            "fec_test_mode": False,
        })
        manager_action = manager_wizard.action_generate_export()
        self.assertEqual(
            manager_action["res_model"],
            "rebuild.account.report.export.wizard",
        )
        self.assertFalse(manager_wizard.fec_test_mode)
        self.assertTrue(manager_wizard.can_generate_official_fec)
        self.assertTrue(manager_wizard.export_file)
        self.assertEqual(
            self.company.fiscalyear_lock_date,
            fields.Date.from_string("2099-12-31"),
        )

    def test_reconciliation_review_action_uses_only_source_traced_endpoints(self):
        expense_account = self._account("T600003", "Generated Endpoint Expense", "expense")
        payable_account = self._account("T401003", "Generated Endpoint Payable", "liability_payable")
        imported_move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": self._journal().id,
            "date": fields.Date.from_string("2025-09-30"),
            "company_id": self.company.id,
            "line_ids": [
                Command.create({
                    "name": "Imported endpoint",
                    "account_id": payable_account.id,
                    "debit": 12.34,
                    "rebuild_source_model": "account.move.line",
                    "rebuild_source_id": 10,
                    "rebuild_source_snapshot": "test-snapshot",
                }),
                Command.create({
                    "name": "Imported counterpart",
                    "account_id": expense_account.id,
                    "credit": 12.34,
                }),
            ],
        })
        imported_move.action_post()
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

        full_review = self.env[
            "rebuild.account.reconciliation.review"
        ].create({
            "name": "Full cross-boundary reconciliation",
            "reconciliation_kind": "full",
            "company_id": self.company.id,
            "source_full_reconcile_id": 77,
            "source_line_ids": "10,11",
            "imported_source_line_ids": "10",
            "missing_source_line_ids": "11",
            "generated_missing_line_count": 1,
            "generated_missing_source_line_ids": "11",
            "missing_endpoint_coverage": "all_generated_draft",
            "total_line_count": 2,
            "imported_line_count": 1,
            "missing_line_count": 1,
            "max_date": "2025-10-31",
            "rebuild_source_model": "account.full.reconcile",
            "rebuild_source_id": 77,
            "rebuild_source_snapshot": "test-snapshot",
        })
        full_preview = (
            full_review.action_preview_native_full_reconciliation_scope()
        )
        imported_endpoint = imported_move.line_ids.filtered(
            lambda line: line.rebuild_source_id == 10,
        )
        generated_endpoint = generated_move.line_ids.filtered(
            lambda line: line.rebuild_source_id == 11,
        )
        self.assertEqual(full_preview["res_model"], "account.move.line")
        self.assertEqual(full_preview["context"]["create"], False)
        self.assertEqual(full_preview["context"]["edit"], False)
        self.assertEqual(full_preview["context"]["delete"], False)
        self.assertEqual(
            full_preview["context"]["rebuild_source_full_reconcile_id"],
            77,
        )
        self.assertIn(
            (
                "id",
                "in",
                sorted((imported_endpoint | generated_endpoint).ids),
            ),
            full_preview["domain"],
        )
        full_decision = full_review.action_record_review_decision()
        self.assertEqual(
            full_decision["context"]["default_evidence_key"],
            "source_full_reconcile:77",
        )
        self.assertIn(
            "review-only treatment",
            full_decision["context"]["default_decision_summary"],
        )
        self.assertIn(
            "separately authorized workflow",
            full_decision["context"]["default_remaining_risk"],
        )

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

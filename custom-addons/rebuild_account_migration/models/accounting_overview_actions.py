"""Navigation actions of the Accounting overview cockpit."""

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import date_utils
from odoo.tools.safe_eval import safe_eval


class RebuildAccountOverview(models.Model):
    _inherit = "rebuild.account.overview"

    @api.model
    def _selected_overviews(self):
        """Return one overview for every company selected in the Odoo switcher."""
        return self.search(
            [("company_id", "in", self.env.companies.ids)],
            order="company_id",
        )

    def _overview_form_action(self):
        self.ensure_one()
        form_view = self.env.ref(
            "rebuild_account_migration.view_rebuild_accounting_home_form",
        )
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Accounting — %s", self.company_id.display_name),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": form_view.id,
            "views": [(form_view.id, "form")],
            "target": "current",
            "context": {"create": False, "delete": False},
        }

    @api.model
    def action_open_accounting_home(self):
        if not self.has_access("read"):
            return self.env["ir.actions.actions"]._for_xml_id(
                "account.open_account_journal_dashboard_kanban",
            )
        overviews = self._selected_overviews()
        if not overviews:
            return self.env["ir.actions.actions"]._for_xml_id(
                "account.open_account_journal_dashboard_kanban",
            )
        if len(overviews) == 1:
            return overviews._overview_form_action()
        kanban_view = self.env.ref(
            "rebuild_account_migration.view_rebuild_accounting_home_kanban",
        )
        form_view = self.env.ref(
            "rebuild_account_migration.view_rebuild_accounting_home_form",
        )
        return {
            "type": "ir.actions.act_window",
            "name": self.env._(
                "Accounting — %s selected companies",
                len(overviews),
            ),
            "res_model": self._name,
            "view_mode": "kanban,form",
            "view_id": kanban_view.id,
            "views": [(kanban_view.id, "kanban"), (form_view.id, "form")],
            "domain": [("id", "in", overviews.ids)],
            "target": "current",
            "context": {"create": False, "delete": False},
        }

    def action_open_company_overview(self):
        return self._overview_form_action()

    def _standard_company_action(self, xmlid, domain=None):
        """Return an upstream action with only its non-removable scope.

        Statuses presented as ``search_default_*`` facets belong in the
        context only. Repeating them here makes a removed facet ineffective
        and leaves the user trapped in an invisible filter.
        """
        if not self:
            raise UserError(self.env._("No selected company is available."))
        action = self.env["ir.actions.actions"]._for_xml_id(xmlid)
        if domain is not None:
            company_ids = self.company_id.ids
            company_domain = (
                ("company_id", "=", company_ids[0])
                if len(company_ids) == 1
                else ("company_id", "in", company_ids)
            )
            action["domain"] = [
                company_domain,
                *domain,
            ]
            context = action.get("context") or {}
            if isinstance(context, str):
                context = safe_eval(context)
            if len(company_ids) == 1:
                context = {**context, "default_company_id": company_ids[0]}
            action["context"] = context
        return action

    def action_open_journal_dashboard(self):
        return self._standard_company_action(
            "account.open_account_journal_dashboard_kanban",
            [],
        )

    def action_open_bank_transactions(self):
        return self._standard_company_action(
            "account_statement_base.account_bank_statement_line_action",
            [],
        )

    def action_open_bank_review(self):
        action = self._standard_company_action(
            "account_statement_base.account_bank_statement_line_action",
            [],
        )
        action["name"] = "Pending Review"
        context = action.get("context") or {}
        if isinstance(context, str):
            context = safe_eval(context)
        action["context"] = {
            **context,
            "search_default_to_review": 1,
            "create": False,
        }
        return action

    def action_open_bank_attention(self):
        action = self._standard_company_action(
            "account_statement_base.account_bank_statement_line_action",
            self._bank_attention_domain()[1:],
        )
        action["name"] = self.env._("Bank Items to Review")
        context = action.get("context") or {}
        if isinstance(context, str):
            context = safe_eval(context)
        action["context"] = {**context, "create": False}
        return action

    def action_open_bank_matching(self):
        return self._standard_company_action(
            "rebuild_account_migration.action_rebuild_account_reconcile_bank_transactions",
            [],
        )

    def action_open_customer_documents(self):
        action = self._standard_company_action(
            "account.action_move_out_invoice_type",
            [
                ("move_type", "in", ["out_invoice", "out_refund", "out_receipt"]),
            ],
        )
        context = action.get("context") or {}
        if isinstance(context, str):
            context = safe_eval(context)
        action["context"] = {
            **context,
            "search_default_draft": 1,
        }
        return action

    def action_open_vendor_documents(self):
        action = self._standard_company_action(
            "account.action_move_in_invoice_type",
            [
                ("move_type", "in", ["in_invoice", "in_refund", "in_receipt"]),
            ],
        )
        context = action.get("context") or {}
        if isinstance(context, str):
            context = safe_eval(context)
        action["context"] = {
            **context,
            "search_default_draft": 1,
        }
        return action

    def action_open_expenses(self):
        # The stat button above this action shows `draft_expense_count`, which
        # counts exactly these three states.  Opening every expense ever
        # recorded would not be the list the number promises.
        action = self._standard_company_action(
            "hr_expense.hr_expense_actions_all",
            [("state", "in", ["draft", "submitted", "approved"])],
        )
        action.update({
            "view_mode": "list,form,graph,pivot",
            "views": [(False, "list"), (False, "form"), (False, "graph"), (False, "pivot")],
        })
        return action

    def action_open_missing_vendor_attachments(self):
        return self._standard_company_action(
            "account.action_move_in_invoice_type",
            self._missing_vendor_attachments_domain(),
        )

    def action_open_missing_expense_attachments(self):
        action = self._standard_company_action(
            "hr_expense.hr_expense_actions_all",
            self._missing_expense_attachments_domain(),
        )
        context = action.get("context") or {}
        if isinstance(context, str):
            context = safe_eval(context)
        context.pop("searchpanel_default_state", None)
        action.update({
            "view_mode": "list,form",
            "views": [(False, "list"), (False, "form")],
            "context": {**context, "create": False},
        })
        return action

    def action_open_stale_draft_documents(self):
        cutoff = date_utils.subtract(fields.Date.context_today(self), days=30)
        return self._standard_company_action(
            "account.action_move_journal_line",
            [
                ("state", "=", "draft"),
                (
                    "move_type",
                    "in",
                    [
                        "out_invoice",
                        "out_refund",
                        "out_receipt",
                        "in_invoice",
                        "in_refund",
                        "in_receipt",
                    ],
                ),
                ("date", "<", cutoff),
            ],
        )

    def action_open_stale_expenses(self):
        cutoff = date_utils.subtract(fields.Date.context_today(self), days=30)
        return self._standard_company_action(
            "hr_expense.hr_expense_actions_all",
            [
                ("state", "in", ["draft", "submitted", "approved"]),
                ("date", "<", cutoff),
            ],
        )

    def action_open_latest_closing_controls(self):
        periods = self.mapped("latest_closing_period_id")
        if not periods:
            message = "No closing controls are available for this company."
            raise UserError(message)
        return {
            "type": "ir.actions.act_window",
            "name": "Current Accounting Controls",
            "res_model": "rebuild.account.closing.control",
            "view_mode": "list,form",
            "views": [(False, "list"), (False, "form")],
            "domain": [
                (
                    "closing_period_id",
                    "in",
                    periods.ids,
                ),
            ],
            "context": {
                "create": False,
                "delete": False,
                "search_default_group_category": 1,
            },
        }

    def action_open_unusual_balances(self):
        self.ensure_one()
        control = self.latest_closing_period_id.control_line_ids.filtered(
            lambda line: line.code == "unusual_balances",
        )[:1]
        if not control:
            message = (
                "No unusual-balance control is available. Ask an Accounting "
                "Manager to refresh the current controls."
            )
            raise UserError(message)
        return control.action_open_records()

    def action_refresh_hygiene(self):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_manager"):
            message = "Only an Accounting Manager can refresh accounting controls."
            raise AccessError(message)
        if self.latest_closing_period_id:
            self.latest_closing_period_id.action_refresh_controls()
        else:
            self.env["rebuild.account.hygiene.issue"].sync_for_company(
                self.company_id,
            )
        return self._hygiene_issues_action()

    def action_open_hygiene_issues(self):
        if len(self) > 1:
            return self._hygiene_issues_action()
        self.ensure_one()
        if self.env.user.has_group("account.group_account_manager"):
            return self.action_refresh_hygiene()
        return self._hygiene_issues_action()

    def _hygiene_issues_action(self):
        if not self:
            raise UserError(self.env._("Accounting Hygiene is not available."))
        company_ids = self.company_id.ids
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Hygiene",
            "res_model": "rebuild.account.hygiene.issue",
            "view_mode": "list,form",
            "views": [(False, "list"), (False, "form")],
            "domain": [("company_id", "in", company_ids)],
            "context": {
                "create": False,
                "delete": False,
                "search_default_open": 1,
            },
        }

    @api.model
    def action_open_current_company_hygiene(self):
        overviews = self._selected_overviews()
        if not overviews:
            message = "Accounting Hygiene is not available for the selected companies."
            raise UserError(message)
        return overviews.action_open_hygiene_issues()

    def action_open_open_receivables(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Open Receivables",
            "res_model": "account.move.line",
            "view_mode": "list,pivot,graph",
            "views": [(False, "list"), (False, "pivot"), (False, "graph")],
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("parent_state", "=", "posted"),
                ("account_id.account_type", "=", "asset_receivable"),
                ("reconciled", "=", False),
                ("amount_residual", "!=", 0),
            ],
            "context": {
                "create": False,
                "delete": False,
                "search_default_group_by_partner": 1,
            },
        }

    def action_open_open_payables(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Open Payables",
            "res_model": "account.move.line",
            "view_mode": "list,pivot,graph",
            "views": [(False, "list"), (False, "pivot"), (False, "graph")],
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("parent_state", "=", "posted"),
                ("account_id.account_type", "=", "liability_payable"),
                ("reconciled", "=", False),
                ("amount_residual", "!=", 0),
            ],
            "context": {
                "create": False,
                "delete": False,
                "search_default_group_by_partner": 1,
            },
        }

    def action_open_latest_closing(self):
        self.ensure_one()
        if not self.latest_closing_period_id:
            raise UserError(
                "No closing workspace is available for this company.",
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Current Closing Workspace",
            "res_model": "rebuild.account.closing.period",
            "res_id": self.latest_closing_period_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
            "context": {"create": False, "delete": False},
        }

    def action_open_next_declaration(self):
        self.ensure_one()
        if not self.next_declaration_id:
            raise UserError(
                "No pending declaration is available for this company.",
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Next Declaration",
            "res_model": "rebuild.account.declaration",
            "res_id": self.next_declaration_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
            "context": {"create": False, "delete": False},
        }

    def action_open_closing_workspaces(self):
        return self._standard_company_action(
            "rebuild_account_migration.action_rebuild_account_closing_period",
            [],
        )

    def action_open_declarations(self):
        return self._standard_company_action(
            "rebuild_account_migration.action_rebuild_account_declaration",
            [],
        )

    def action_open_valentin_actions(self):
        if not self:
            raise UserError(self.env._("No selected company is available."))
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Manager Decisions",
            "res_model": "rebuild.account.assurance.decision",
            "view_mode": "list,form,pivot",
            "views": [(False, "list"), (False, "form"), (False, "pivot")],
            "domain": [
                "|",
                ("company_id", "=", False),
                ("company_id", "in", self.company_id.ids),
                ("state", "=", "draft"),
                ("required_authority", "in", ["valentin", "joint"]),
            ],
            "context": {"delete": False},
        }

    def action_open_accountant_actions(self):
        if not self:
            raise UserError(self.env._("No selected company is available."))
        return {
            "type": "ir.actions.act_window",
            "name": "Accountant Review Decisions",
            "res_model": "rebuild.account.assurance.decision",
            "view_mode": "list,form,pivot",
            "views": [(False, "list"), (False, "form"), (False, "pivot")],
            "domain": [
                "|",
                ("company_id", "=", False),
                ("company_id", "in", self.company_id.ids),
                ("state", "=", "draft"),
                ("required_authority", "in", ["accountant", "joint"]),
            ],
            "context": {"delete": False},
        }

    def action_open_accounting_settings(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "account.action_account_config",
        )
        context = action.get("context") or {}
        if isinstance(context, str):
            context = safe_eval(context)
        action["context"] = {
            **context,
            "allowed_company_ids": [self.company_id.id],
            "default_company_id": self.company_id.id,
        }
        return action

    def action_open_review_decisions(self):
        if not self:
            raise UserError(self.env._("No selected company is available."))
        company_ids = self.company_id.ids
        context = {"delete": False}
        if len(company_ids) == 1:
            context["default_company_id"] = company_ids[0]
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Review Decisions",
            "res_model": "rebuild.account.assurance.decision",
            "view_mode": "list,form,pivot",
            "views": [(False, "list"), (False, "form"), (False, "pivot")],
            "domain": [
                "|",
                ("company_id", "=", False),
                ("company_id", "in", company_ids),
                ("state", "!=", "superseded"),
            ],
            "context": context,
        }

    def action_open_external_report_values(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "External Report Values",
            "res_model": "rebuild.account.external.report.value",
            "view_mode": "list,form,pivot",
            "views": [(False, "list"), (False, "form"), (False, "pivot")],
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("active", "=", True),
            ],
            "context": {
                "default_company_id": self.company_id.id,
                "delete": False,
            },
        }

    def action_open_posted_journal_items(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Posted Journal Items",
            "res_model": "account.move.line",
            "view_mode": "list,form,pivot",
            "views": [(False, "list"), (False, "form"), (False, "pivot")],
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("move_id.state", "=", "posted"),
            ],
            "context": {"create": False, "delete": False},
        }

    def action_open_report_export_wizard(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        fiscal_from, fiscal_to = (
            self.company_id.rebuild_compute_fiscalyear_dates(today)
        )
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Report Workbench",
            "res_model": "rebuild.account.report.export.wizard",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
            "context": {
                "default_company_id": self.company_id.id,
                "default_company_ids": [self.company_id.id],
                "default_report_type": "trial_balance",
                "default_period_preset": "year_to_date",
                "default_period_anchor_date": today,
                "default_date_from": fiscal_from,
                "default_date_to": min(today, fiscal_to),
                "default_export_format": "xlsx",
                "default_target_move": "posted",
            },
        }

    def action_open_user_guide(self):
        return {
            "type": "ir.actions.act_url",
            "name": "USL Odoo User Guide",
            "url": "/usl/user-docs",
            "target": "self",
        }

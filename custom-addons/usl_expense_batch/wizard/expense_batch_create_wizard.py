from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date


class UslExpenseBatchCreateWizard(models.TransientModel):
    _name = "usl.expense.batch.create.wizard"
    _description = "Create Expense Batch"

    name = fields.Char()
    purpose = fields.Text()
    expense_ids = fields.Many2many(
        "hr.expense",
        string="Expenses",
        required=True,
        domain=[
            ("state", "=", "draft"),
            ("expense_batch_id", "=", False),
        ],
    )
    company_id = fields.Many2one(
        "res.company",
        compute="_compute_preview",
        readonly=True,
    )
    employee_id = fields.Many2one(
        "hr.employee",
        compute="_compute_preview",
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        readonly=True,
    )
    date_from = fields.Date(compute="_compute_preview")
    date_to = fields.Date(compute="_compute_preview")
    expense_count = fields.Integer(compute="_compute_preview")
    total_amount = fields.Monetary(
        compute="_compute_preview",
        currency_field="currency_id",
    )
    employee_paid_total = fields.Monetary(
        compute="_compute_preview",
        currency_field="currency_id",
    )
    company_paid_total = fields.Monetary(
        compute="_compute_preview",
        currency_field="currency_id",
    )
    incomplete_expense_ids = fields.Many2many(
        "hr.expense",
        compute="_compute_preview",
    )
    incomplete_count = fields.Integer(compute="_compute_preview")
    main_analytic_activity = fields.Char(compute="_compute_preview")

    @api.model_create_multi
    def create(self, values_list):
        records = super().create(values_list)
        for wizard in records.filtered(lambda record: record.expense_ids):
            defaults = wizard._suggest_batch_values()
            wizard.write({
                key: value
                for key, value in defaults.items()
                if not wizard[key]
            })
        return records

    @api.depends(
        "expense_ids",
        "expense_ids.date",
        "expense_ids.total_amount",
        "expense_ids.payment_mode",
        "expense_ids.batch_readiness",
        "expense_ids.analytic_distribution",
    )
    def _compute_preview(self):
        analytic_account_model = self.env["account.analytic.account"]
        for wizard in self:
            expenses = wizard.expense_ids
            wizard.company_id = expenses.company_id[:1]
            wizard.employee_id = expenses.employee_id[:1]
            dates = expenses.mapped("date")
            wizard.date_from = min(dates) if dates else False
            wizard.date_to = max(dates) if dates else False
            wizard.expense_count = len(expenses)
            wizard.total_amount = sum(expenses.mapped("total_amount"))
            wizard.employee_paid_total = sum(
                expenses.filtered(
                    lambda expense: expense.payment_mode == "own_account",
                ).mapped("total_amount"),
            )
            wizard.company_paid_total = sum(
                expenses.filtered(
                    lambda expense: expense.payment_mode == "company_account",
                ).mapped("total_amount"),
            )
            incomplete = expenses.filtered(
                lambda expense: expense.batch_readiness != "ready",
            )
            wizard.incomplete_expense_ids = incomplete
            wizard.incomplete_count = len(incomplete)

            analytic_weights = defaultdict(float)
            for distribution in expenses.mapped("analytic_distribution"):
                for account_keys, weight in (distribution or {}).items():
                    for account_key in account_keys.split(","):
                        if account_key.isdigit():
                            analytic_weights[int(account_key)] += weight
            if analytic_weights:
                account_id = max(
                    analytic_weights,
                    key=lambda analytic_id: analytic_weights[analytic_id],
                )
                wizard.main_analytic_activity = (
                    analytic_account_model.browse(account_id).display_name
                )
            else:
                wizard.main_analytic_activity = False

    def _suggest_batch_values(self):
        self.ensure_one()
        expenses = self.expense_ids
        dates = expenses.mapped("date")
        employee = expenses.employee_id[:1]
        if not dates or not employee:
            return {}
        date_from = min(dates)
        date_to = max(dates)
        if date_from.year == date_to.year and date_from.month == date_to.month:
            period = format_date(self.env, date_from, date_format="MMMM y")
        else:
            period = _(
                "%(date_from)s to %(date_to)s",
                date_from=format_date(self.env, date_from),
                date_to=format_date(self.env, date_to),
            )
        name = _("%(employee)s — %(period)s", employee=employee.name, period=period)
        return {
            "name": name,
            "purpose": self.main_analytic_activity or name,
        }

    def _check_compatible_selection(self):
        self.ensure_one()
        if not self.expense_ids:
            raise UserError(_("Select at least one expense."))
        if len(self.expense_ids.company_id) != 1:
            raise UserError(_("A batch cannot mix expenses from different companies."))
        if len(self.expense_ids.employee_id) != 1:
            raise UserError(_("A batch cannot mix expenses from different employees."))
        invalid = self.expense_ids.filtered(
            lambda expense: expense.state != "draft" or expense.expense_batch_id,
        )
        if invalid:
            raise UserError(_("Only unbatched draft expenses can be selected."))

    def _create_batch(self):
        self.ensure_one()
        self._check_compatible_selection()
        if not self.name or not self.purpose:
            raise UserError(_("Enter a batch name and a common business purpose."))
        return self.env["usl.expense.batch"].create({
            "name": self.name,
            "purpose": self.purpose,
            "employee_id": self.expense_ids.employee_id.id,
            "company_id": self.expense_ids.company_id.id,
            "expense_ids": [Command.set(self.expense_ids.ids)],
        })

    def action_create_batch(self):
        batch = self._create_batch()
        return {
            "name": batch.name,
            "type": "ir.actions.act_window",
            "res_model": "usl.expense.batch",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": batch.id,
        }

    def action_create_and_submit(self):
        batch = self._create_batch()
        batch.action_submit()
        return {
            "name": batch.name,
            "type": "ir.actions.act_window",
            "res_model": "usl.expense.batch",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": batch.id,
        }

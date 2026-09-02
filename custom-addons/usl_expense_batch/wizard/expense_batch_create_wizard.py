from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date

from odoo.addons.usl_expense_batch.models.hr_expense import (
    EXPENSE_BATCH_ELIGIBLE_STATES,
)


class UslExpenseBatchCreateWizard(models.TransientModel):
    _name = "usl.expense.batch.create.wizard"
    _description = "Create Expense Batch"
    _inherit = ["analytic.mixin"]

    name = fields.Char()
    purpose = fields.Text()
    mode = fields.Selection(
        selection=[
            ("existing", "Add to an existing Batch"),
            ("new", "Create a new Batch"),
        ],
        default="new",
        required=True,
    )
    batch_id = fields.Many2one(
        "usl.expense.batch",
        string="Existing Batch",
        domain="[('employee_id', '=', employee_id), ('company_id', '=', company_id), ('active', '=', True)]",
    )
    context_type = fields.Selection(
        selection=[
            ("travel", "Travel"),
            ("production_event", "Production or event"),
            ("project", "Project"),
            ("period", "Periodic claim"),
            ("other", "Other"),
        ],
        default="other",
        required=True,
    )
    account_override_id = fields.Many2one(
        "account.account",
        string="Shared expense account",
        check_company=True,
        domain="[('account_type', 'not in', ('asset_receivable', 'liability_payable', 'asset_cash', 'liability_credit_card'))]",
    )
    expense_ids = fields.Many2many(
        "hr.expense",
        string="Expenses",
        required=True,
        domain=[
            ("state", "in", list(EXPENSE_BATCH_ELIGIBLE_STATES)),
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
        string="Incomplete expenses",
    )
    incomplete_count = fields.Integer(
        compute="_compute_preview",
        string="Expenses needing information",
    )
    draft_count = fields.Integer(
        compute="_compute_preview",
        string="Draft expenses",
    )
    draft_incomplete_count = fields.Integer(
        compute="_compute_preview",
        string="Draft expenses needing information",
    )
    readiness_state = fields.Selection(
        selection=[
            ("ready", "Ready"),
            ("incomplete", "Needs information"),
        ],
        compute="_compute_preview",
        string="Batch readiness",
    )
    main_analytic_activity = fields.Char(compute="_compute_preview")
    candidate_count = fields.Integer(compute="_compute_preview")
    duplicate_batch_warning = fields.Char(compute="_compute_preview")
    outside_date_warning = fields.Char(compute="_compute_preview")
    context_change_count = fields.Integer(compute="_compute_preview")
    context_exception_count = fields.Integer(compute="_compute_preview")
    context_skipped_count = fields.Integer(compute="_compute_preview")

    @api.model_create_multi
    def create(self, values_list):
        records = super().create(values_list)
        for wizard in records.filtered(lambda record: record.expense_ids):
            defaults = wizard._suggest_batch_values()
            candidates = wizard.env["hr.expense"].get_expense_batch_candidates(
                wizard.expense_ids.ids,
            )
            if candidates and candidates[0]["score"] >= 100:
                defaults.update({
                    "mode": "existing",
                    "batch_id": candidates[0]["id"],
                })
            suggested_values = {
                key: value
                for key, value in defaults.items()
                if not wizard[key]
            }
            if defaults.get("batch_id"):
                suggested_values.update({
                    "mode": "existing",
                    "batch_id": defaults["batch_id"],
                })
            wizard.write(suggested_values)
        return records

    @api.depends(
        "expense_ids",
        "expense_ids.date",
        "expense_ids.total_amount",
        "expense_ids.payment_mode",
        "expense_ids.batch_readiness",
        "expense_ids.analytic_distribution",
        "expense_ids.account_context_source",
        "expense_ids.analytic_context_source",
        "mode",
        "batch_id",
        "batch_id.context_date_from",
        "batch_id.context_date_to",
        "analytic_distribution",
        "account_override_id",
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
            wizard.draft_count = len(
                expenses.filtered(lambda expense: expense.state == "draft"),
            )
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
                lambda expense: expense.batch_readiness == "incomplete",
            )
            wizard.incomplete_expense_ids = incomplete
            wizard.incomplete_count = len(incomplete)
            wizard.draft_incomplete_count = len(
                incomplete.filtered(lambda expense: expense.state == "draft"),
            )
            wizard.readiness_state = "incomplete" if incomplete else "ready"

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

            candidates = (
                self.env["hr.expense"].get_expense_batch_candidates(expenses.ids)
                if expenses
                else []
            )
            wizard.candidate_count = len(candidates)
            overlap = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate["date_overlap"] and candidate["analytic_overlap"]
                ),
                False,
            )
            wizard.duplicate_batch_warning = (
                _(
                    "%(batch)s already covers similar dates and analytics.",
                    batch=overlap["name"],
                )
                if overlap and wizard.mode == "new"
                else False
            )
            outside_dates = (
                expenses.filtered(
                    lambda expense: expense.date
                    and (
                        (
                            wizard.batch_id.context_date_from
                            and expense.date < wizard.batch_id.context_date_from
                        )
                        or (
                            wizard.batch_id.context_date_to
                            and expense.date > wizard.batch_id.context_date_to
                        )
                    ),
                )
                if wizard.mode == "existing" and wizard.batch_id
                else self.env["hr.expense"]
            )
            wizard.outside_date_warning = (
                _(
                    "%(count)s selected expense(s) fall outside this Batch's intended "
                    "dates. You can still add and process them.",
                    count=len(outside_dates),
                )
                if outside_dates
                else False
            )
            context_configured = bool(
                (wizard.batch_id and wizard.mode == "existing")
                or wizard.account_override_id
                or wizard.analytic_distribution,
            )
            wizard.context_change_count = (
                len(expenses.filtered(lambda expense: expense.state == "draft"))
                if context_configured
                else 0
            )
            configured_account = (
                wizard.batch_id.account_override_id
                if wizard.mode == "existing" and wizard.batch_id
                else wizard.account_override_id
            )
            configured_analytics = (
                wizard.batch_id.analytic_distribution
                if wizard.mode == "existing" and wizard.batch_id
                else wizard.analytic_distribution
            )
            wizard.context_exception_count = len(
                expenses.filtered(
                    lambda expense: expense.state == "draft"
                    and (
                        (
                            configured_account
                            and expense.account_context_source == "explicit"
                        )
                        or (
                            configured_analytics
                            and expense.analytic_context_source == "explicit"
                        )
                    ),
                ),
            )
            wizard.context_skipped_count = len(
                expenses.filtered(lambda expense: expense.state != "draft"),
            )

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
            "analytic_distribution": (
                expenses[0].analytic_distribution
                if expenses
                and all(
                    expense.analytic_distribution
                    == expenses[0].analytic_distribution
                    for expense in expenses
                )
                else False
            ),
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
            lambda expense: (
                expense.state not in EXPENSE_BATCH_ELIGIBLE_STATES
                or expense.expense_batch_id
            ),
        )
        if invalid:
            raise UserError(
                _(
                    "Only unbatched draft, approved, or posted expenses "
                    "can be selected.",
                ),
            )

    def _create_batch(self):
        self.ensure_one()
        self._check_compatible_selection()
        if self.mode == "existing":
            if not self.batch_id:
                raise UserError(_("Select an existing Batch."))
            if (
                self.batch_id.company_id != self.company_id
                or self.batch_id.employee_id != self.employee_id
                or not self.batch_id.active
            ):
                raise UserError(
                    _("The selected Batch is no longer compatible with these expenses."),
                )
            self.batch_id.add_expenses(self.expense_ids.ids)
            return self.batch_id
        if not self.name or not self.purpose:
            raise UserError(_("Enter a batch name and a common business purpose."))
        return self.env["usl.expense.batch"].create({
            "name": self.name,
            "purpose": self.purpose,
            "context_type": self.context_type,
            "context_date_from": self.date_from,
            "context_date_to": self.date_to,
            "analytic_distribution": self.analytic_distribution,
            "account_override_id": self.account_override_id.id,
            "employee_id": self.expense_ids.employee_id.id,
            "company_id": self.expense_ids.company_id.id,
            "expense_ids": [Command.set(self.expense_ids.ids)],
        })

    def _success_action(self, batch, submitted=False):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": (
                    _("%(batch)s was created and submitted.", batch=batch.display_name)
                    if submitted
                    else _("Expenses added to %(batch)s.", batch=batch.display_name)
                ),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_create_batch(self):
        batch = self._create_batch()
        return self._success_action(batch)

    def action_create_and_submit(self):
        if not self.draft_count:
            raise UserError(_("This selection has no draft expenses to submit."))
        batch = self._create_batch()
        batch.action_submit()
        return self._success_action(batch, submitted=True)


class UslExpenseBatchContextApplyWizard(models.TransientModel):
    _name = "usl.expense.batch.context.apply.wizard"
    _description = "Apply Expense Batch Context"

    batch_id = fields.Many2one(
        "usl.expense.batch",
        required=True,
        readonly=True,
    )
    expected_revision = fields.Integer(required=True, readonly=True)
    force_expense_ids = fields.Many2many(
        "hr.expense",
        string="Replace selected exceptions",
        domain="[('expense_batch_id', '=', batch_id), ('state', '=', 'draft'), '|', ('account_context_source', '=', 'explicit'), ('analytic_context_source', '=', 'explicit')]",
        help=(
            "Selected explicit expense choices will be replaced by the current "
            "Batch context. This action is limited to Expense or Accounting Managers."
        ),
    )
    changed_count = fields.Integer(compute="_compute_preview")
    unchanged_count = fields.Integer(compute="_compute_preview")
    exception_count = fields.Integer(compute="_compute_preview")
    skipped_count = fields.Integer(compute="_compute_preview")
    preview_line = fields.Char(compute="_compute_preview")

    @api.depends("batch_id", "force_expense_ids")
    def _compute_preview(self):
        for wizard in self:
            if not wizard.batch_id:
                wizard.changed_count = 0
                wizard.unchanged_count = 0
                wizard.exception_count = 0
                wizard.skipped_count = 0
                wizard.preview_line = False
                continue
            preview = wizard.batch_id.preview_context_application(
                force_expense_ids=wizard.force_expense_ids.ids,
            )
            wizard.changed_count = preview["changed"]
            wizard.unchanged_count = preview["unchanged"]
            wizard.exception_count = preview["exceptions"]
            wizard.skipped_count = preview["skipped"]
            wizard.preview_line = _(
                "%(changed)s line(s) will change, %(unchanged)s already match, "
                "%(exceptions)s explicit exception(s) remain, and %(skipped)s "
                "later-stage line(s) are preserved.",
                changed=preview["changed"],
                unchanged=preview["unchanged"],
                exceptions=preview["exceptions"],
                skipped=preview["skipped"],
            )

    def action_apply(self):
        self.ensure_one()
        result = self.batch_id.apply_context(
            force_expense_ids=self.force_expense_ids.ids,
            expected_revision=self.expected_revision,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _(
                    "Shared context applied to %(count)s expense(s).",
                    count=result["applied"],
                ),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

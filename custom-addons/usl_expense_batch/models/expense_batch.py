import json
from collections import defaultdict

from lxml import etree

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_date


class UslExpenseBatch(models.Model):
    _name = "usl.expense.batch"
    _description = "Expense Batch"
    _inherit = ["mail.thread", "mail.activity.mixin", "analytic.mixin"]
    _order = "date_from desc, id desc"
    _check_company_auto = True

    active = fields.Boolean(default=True, tracking=True)
    name = fields.Char(required=True, tracking=True)
    purpose = fields.Text(required=True, tracking=True)
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
        tracking=True,
    )
    context_date_from = fields.Date(string="Context from", tracking=True)
    context_date_to = fields.Date(string="Context to", tracking=True)
    notes = fields.Html(string="Shared justification and notes", tracking=True)
    account_override_id = fields.Many2one(
        "account.account",
        string="Shared expense account",
        check_company=True,
        tracking=True,
        domain="[('account_type', 'not in', ('asset_receivable', 'liability_payable', 'asset_cash', 'liability_credit_card'))]",
        help=(
            "When configured, this account replaces category defaults on draft "
            "expenses unless the expense has an explicit account exception."
        ),
    )
    context_revision = fields.Integer(default=1, readonly=True, copy=False)
    employee_id = fields.Many2one(
        "hr.employee",
        required=True,
        check_company=True,
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        readonly=True,
    )
    expense_ids = fields.One2many(
        "hr.expense",
        "expense_batch_id",
        string="Expenses",
        copy=False,
    )
    expense_progress = fields.Selection(
        selection=[
            ("empty", "No expenses"),
            ("draft", "Has drafts"),
            ("submitted", "Waiting approval"),
            ("approved", "Ready to post"),
            ("posted", "All posted"),
            ("paid", "All paid"),
            ("refused", "Returned only"),
        ],
        compute="_compute_expense_progress",
        store=True,
        index=True,
        string="Expense progress",
    )
    expense_progress_summary = fields.Char(
        compute="_compute_expense_progress_summary",
        string="Progress breakdown",
    )
    expense_progress_breakdown = fields.Char(
        compute="_compute_expense_progress_summary",
        string="Progress segments",
    )
    batch_state = fields.Selection(
        selection=[
            ("open", "Open"),
            ("archived", "Archived"),
        ],
        compute="_compute_batch_state",
        string="Batch state",
    )
    submitted_by_id = fields.Many2one(
        "res.users",
        readonly=True,
        copy=False,
        tracking=True,
    )
    submitted_date = fields.Datetime(readonly=True, copy=False, tracking=True)
    approved_by_id = fields.Many2one(
        "res.users",
        readonly=True,
        copy=False,
        tracking=True,
    )
    approved_date = fields.Datetime(readonly=True, copy=False, tracking=True)
    date_from = fields.Date(compute="_compute_summary", store=True)
    date_to = fields.Date(compute="_compute_summary", store=True)
    expense_count = fields.Integer(compute="_compute_summary", store=True)
    attachment_count = fields.Integer(
        string="Receipt Count",
        compute="_compute_summary",
        store=True,
    )
    total_amount = fields.Monetary(
        compute="_compute_summary",
        store=True,
        currency_field="currency_id",
    )
    employee_paid_total = fields.Monetary(
        compute="_compute_summary",
        store=True,
        currency_field="currency_id",
    )
    company_paid_total = fields.Monetary(
        compute="_compute_summary",
        store=True,
        currency_field="currency_id",
    )
    incomplete_expense_ids = fields.Many2many(
        "hr.expense",
        compute="_compute_review_context",
        string="Incomplete expenses",
    )
    incomplete_count = fields.Integer(
        compute="_compute_review_context",
        string="Expenses needing information",
    )
    attention_count = fields.Integer(
        compute="_compute_review_context",
        string="Expenses needing attention",
    )
    has_incomplete_expenses = fields.Boolean(
        compute="_compute_review_context",
        search="_search_has_incomplete_expenses",
    )
    readiness_state = fields.Selection(
        selection=[
            ("ready", "Ready"),
            ("incomplete", "Needs information"),
        ],
        compute="_compute_review_context",
        string="Batch readiness",
    )
    readiness_summary = fields.Char(compute="_compute_review_context")
    main_analytic_activity = fields.Char(compute="_compute_review_context")
    analytic_context_summary = fields.Char(compute="_compute_review_context")
    exception_count = fields.Integer(compute="_compute_review_context")
    has_exceptions = fields.Boolean(
        compute="_compute_review_context",
        search="_search_has_exceptions",
    )
    period_summary = fields.Char(string="Period", compute="_compute_period_summary")
    stale_context_count = fields.Integer(compute="_compute_review_context")
    warning_count = fields.Integer(compute="_compute_review_context")
    employee_paid_open_count = fields.Integer(compute="_compute_review_context")
    company_paid_open_count = fields.Integer(compute="_compute_review_context")
    draft_expense_count = fields.Integer(compute="_compute_review_context")
    submitted_expense_count = fields.Integer(compute="_compute_review_context")
    approved_expense_count = fields.Integer(compute="_compute_review_context")
    accounted_expense_count = fields.Integer(compute="_compute_accounting_reconciliation")
    accounting_reconciliation_state = fields.Selection(
        selection=[
            ("pending", "Accounting pending"),
            ("matched", "Accounting reconciles"),
            ("difference", "Accounting difference"),
        ],
        compute="_compute_accounting_reconciliation",
    )
    accounting_difference = fields.Monetary(
        compute="_compute_accounting_reconciliation",
        currency_field="currency_id",
    )
    move_ids = fields.Many2many(
        "account.move",
        compute="_compute_moves",
        string="Journal Entries",
    )
    move_count = fields.Integer(compute="_compute_moves")

    @api.depends("active")
    def _compute_batch_state(self):
        for batch in self:
            batch.batch_state = "open" if batch.active else "archived"

    @api.depends("expense_ids.state")
    def _compute_expense_progress_summary(self):
        labels = {
            "draft": _("draft"),
            "submitted": _("submitted"),
            "approved": _("approved"),
            "posted": _("posted"),
            "in_payment": _("in payment"),
            "paid": _("paid"),
            "refused": _("refused"),
        }
        order = tuple(labels)
        for batch in self:
            counts = {
                state: len(
                    batch.expense_ids.filtered(
                        lambda expense, state=state: expense.state == state,
                    ),
                )
                for state in order
            }
            parts = [
                _("%(count)s %(state)s", count=counts[state], state=labels[state])
                for state in order
                if counts[state]
            ]
            batch.expense_progress_summary = " · ".join(parts) or _("No expenses")
            batch.expense_progress_breakdown = json.dumps(
                {state: counts[state] for state in order if counts[state]},
                separators=(",", ":"),
            )

    @api.depends("expense_ids.state")
    def _compute_expense_progress(self):
        state_rank = {
            "draft": 0,
            "submitted": 1,
            "approved": 2,
            "posted": 3,
            "in_payment": 3,
            "paid": 4,
        }
        rank_state = {
            0: "draft",
            1: "submitted",
            2: "approved",
            3: "posted",
            4: "paid",
        }
        for batch in self:
            active_expenses = batch.expense_ids.filtered(
                lambda expense: expense.state != "refused",
            )
            if not active_expenses:
                batch.expense_progress = (
                    "refused" if batch.expense_ids else "empty"
                )
                continue
            lowest_rank = min(
                state_rank.get(expense.state, 0) for expense in active_expenses
            )
            batch.expense_progress = rank_state[lowest_rank]

    @api.depends(
        "expense_ids",
        "expense_ids.date",
        "expense_ids.total_amount",
        "expense_ids.payment_mode",
        "expense_ids.nb_attachment",
    )
    def _compute_summary(self):
        for batch in self:
            dates = batch.expense_ids.mapped("date")
            batch.date_from = min(dates) if dates else False
            batch.date_to = max(dates) if dates else False
            batch.expense_count = len(batch.expense_ids)
            batch.attachment_count = sum(batch.expense_ids.mapped("nb_attachment"))
            batch.total_amount = sum(batch.expense_ids.mapped("total_amount"))
            batch.employee_paid_total = sum(
                batch.expense_ids.filtered(
                    lambda expense: expense.payment_mode == "own_account",
                ).mapped("total_amount"),
            )
            batch.company_paid_total = sum(
                batch.expense_ids.filtered(
                    lambda expense: expense.payment_mode == "company_account",
                ).mapped("total_amount"),
            )

    @api.depends(
        "expense_ids",
        "expense_ids.batch_readiness",
        "expense_ids.batch_incomplete_reason",
        "expense_ids.analytic_distribution",
        "expense_ids.product_id",
        "expense_ids.payment_mode",
        "expense_ids.state",
        "expense_ids.batch_context_status",
        "expense_ids.batch_attention_level",
        "expense_ids.batch_warning_reason",
        "analytic_distribution",
    )
    def _compute_review_context(self):
        analytic_account_model = self.env["account.analytic.account"].sudo()
        for batch in self:
            incomplete = batch.expense_ids.filtered(
                lambda expense: bool(expense.batch_incomplete_reason),
            )
            batch.incomplete_expense_ids = incomplete
            batch.incomplete_count = len(incomplete)
            batch.has_incomplete_expenses = bool(incomplete)
            batch.readiness_state = "incomplete" if incomplete else "ready"

            analytic_weights = defaultdict(float)
            distributions = (
                [batch.analytic_distribution]
                if batch.analytic_distribution
                else batch.expense_ids.mapped("analytic_distribution")
            )
            for distribution in distributions:
                for account_keys, weight in (distribution or {}).items():
                    for account_key in account_keys.split(","):
                        if account_key.isdigit():
                            analytic_weights[int(account_key)] += weight
            if not analytic_weights:
                batch.main_analytic_activity = False
                batch.analytic_context_summary = False
            else:
                main_account_id = max(
                    analytic_weights,
                    key=lambda account_id: analytic_weights[account_id],
                )
                accounts = analytic_account_model.browse(analytic_weights).exists()
                batch.main_analytic_activity = (
                    analytic_account_model.browse(main_account_id).display_name
                )
                by_plan = defaultdict(list)
                for account in accounts.sorted(
                    key=lambda item: (item.plan_id.display_name, item.display_name),
                ):
                    by_plan[account.plan_id.display_name].append(account.display_name)
                batch.analytic_context_summary = " · ".join(
                    f"{plan}: {', '.join(names)}"
                    for plan, names in by_plan.items()
                )

            batch.exception_count = len(
                batch.expense_ids.filtered(
                    lambda expense: expense.batch_context_status == "exception",
                ),
            )
            batch.has_exceptions = batch.exception_count > 0
            batch.stale_context_count = len(
                batch.expense_ids.filtered(
                    lambda expense: expense.batch_context_status == "stale",
                ),
            )
            batch.warning_count = len(
                batch.expense_ids.filtered("batch_warning_reason"),
            )
            batch.attention_count = len(
                batch.expense_ids.filtered(
                    lambda expense: expense.batch_attention_level == "warning",
                ),
            )
            batch.readiness_summary = (
                _(
                    "Needs attention · %(count)s issue",
                    count=batch.attention_count,
                )
                if batch.attention_count == 1
                else _(
                    "Needs attention · %(count)s issues",
                    count=batch.attention_count,
                )
                if batch.attention_count
                else _("Ready")
            )
            batch.employee_paid_open_count = len(
                batch.expense_ids.filtered(
                    lambda expense: expense.payment_mode == "own_account"
                    and expense.state not in ("paid", "refused"),
                ),
            )
            batch.company_paid_open_count = len(
                batch.expense_ids.filtered(
                    lambda expense: expense.payment_mode == "company_account"
                    and expense.state not in ("paid", "refused"),
                ),
            )
            batch.draft_expense_count = len(
                batch.expense_ids.filtered(lambda expense: expense.state == "draft"),
            )
            batch.submitted_expense_count = len(
                batch.expense_ids.filtered(
                    lambda expense: expense.state == "submitted",
                ),
            )
            batch.approved_expense_count = len(
                batch.expense_ids.filtered(
                    lambda expense: expense.state == "approved",
                ),
            )

    @api.model
    def _search_has_exceptions(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Exception filtering expects a true or false value."))
        matching_ids = self.search([]).filtered("has_exceptions").ids
        include_matches = (operator == "=") == value
        return [("id", "in" if include_matches else "not in", matching_ids)]

    @api.model
    def _search_has_incomplete_expenses(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Readiness filtering expects a true or false value."))
        matching_ids = self.search([]).filtered("has_incomplete_expenses").ids
        include_matches = (operator == "=") == value
        return [("id", "in" if include_matches else "not in", matching_ids)]

    @api.depends("date_from", "date_to")
    def _compute_period_summary(self):
        for batch in self:
            if not batch.date_from:
                batch.period_summary = False
            elif not batch.date_to or batch.date_from == batch.date_to:
                batch.period_summary = format_date(self.env, batch.date_from)
            else:
                batch.period_summary = _(
                    "%(start)s → %(end)s",
                    start=format_date(self.env, batch.date_from),
                    end=format_date(self.env, batch.date_to),
                )

    @api.model
    def get_batch_dashboard_counts(self):
        """Return record-rule-aware counts for the operational quick filters."""
        return {
            "all": self.search_count([]),
            "open_batches": self.search_count([("active", "=", True)]),
            "needs_information": self.search_count(
                [("has_incomplete_expenses", "=", True)],
            ),
            "my_batches": self.search_count(
                [("employee_id.user_id", "=", self.env.uid)],
            ),
            "exceptions": self.search_count([("has_exceptions", "=", True)]),
        }

    @api.depends("expense_ids.account_move_id")
    def _compute_moves(self):
        for batch in self:
            batch.move_ids = batch.expense_ids.account_move_id
            batch.move_count = len(batch.move_ids)

    @api.depends(
        "expense_ids.account_move_id",
        "expense_ids.account_move_id.state",
        "expense_ids.account_move_id.line_ids.debit",
        "expense_ids.total_amount",
    )
    def _compute_accounting_reconciliation(self):
        for batch in self:
            active_expenses = batch.expense_ids.filtered(
                lambda expense: expense.state != "refused",
            )
            accounted = batch.expense_ids.filtered(
                lambda expense: expense.account_move_id.state == "posted",
            )
            batch.accounted_expense_count = len(accounted)
            if not active_expenses or len(accounted) != len(active_expenses):
                batch.accounting_reconciliation_state = "pending"
                batch.accounting_difference = 0.0
                continue
            expected_total = sum(accounted.mapped("total_amount"))
            ledger_total = sum(
                self.env["account.move.line"]
                .sudo()
                .search([("move_id", "in", accounted.account_move_id.ids)])
                .mapped("debit"),
            )
            difference = batch.currency_id.round(ledger_total - expected_total)
            batch.accounting_difference = difference
            batch.accounting_reconciliation_state = (
                "matched" if batch.currency_id.is_zero(difference) else "difference"
            )

    @api.constrains("expense_ids", "employee_id", "company_id")
    def _check_expense_compatibility(self):
        for batch in self:
            wrong_company = batch.expense_ids.filtered(
                lambda expense: expense.company_id != batch.company_id,
            )
            if wrong_company:
                raise ValidationError(
                    _("A batch cannot contain expenses from different companies."),
                )
            wrong_employee = batch.expense_ids.filtered(
                lambda expense: expense.employee_id != batch.employee_id,
            )
            if wrong_employee:
                raise ValidationError(
                    _("A batch cannot contain expenses from different employees."),
                )

    @api.constrains("context_date_from", "context_date_to")
    def _check_context_dates(self):
        for batch in self:
            if (
                batch.context_date_from
                and batch.context_date_to
                and batch.context_date_from > batch.context_date_to
            ):
                raise ValidationError(
                    _("The context start date must be on or before the end date."),
                )

    def _check_readonly_accountant_mutation(self):
        if (
            not self.env.su
            and self.env.user.has_group("account.group_account_readonly")
            and not self.env.user.has_group("account.group_account_user")
        ):
            raise AccessError(
                _("Read-only accountants cannot change expense batches."),
            )

    def _check_account_override_access(self, values=None, force=False):
        values = values or {}
        if not force:
            if "account_override_id" not in values:
                return
            if not values["account_override_id"] and not self.account_override_id:
                return
        if self.env.su or self.env.user.has_group(
            "hr_expense.group_hr_expense_manager",
        ) or self.env.user.has_group("account.group_account_manager"):
            return
        raise AccessError(
            _("Only Expense or Accounting Managers can override accounts."),
        )

    @api.model
    def get_view(self, view_id=None, view_type="form", **options):
        result = super().get_view(view_id, view_type, **options)
        if (
            self.env.user.has_group("account.group_account_readonly")
            and not self.env.user.has_group("account.group_account_user")
        ):
            arch = etree.fromstring(result["arch"])
            arch.set("create", "false")
            arch.set("edit", "false")
            arch.set("delete", "false")
            if view_type == "form":
                for control in arch.xpath("//header/button"):
                    control.getparent().remove(control)
                for control in arch.xpath(
                    "//button[@name='action_return_from_batch']",
                ):
                    control.getparent().remove(control)
            result["arch"] = etree.tostring(arch, encoding="unicode")
        return result

    @api.model_create_multi
    def create(self, values_list):
        self._check_readonly_accountant_mutation()
        for values in values_list:
            self._check_account_override_access(values)
        clean_values_list = []
        expense_ids_list = []
        for values in values_list:
            values = dict(values)
            expense_ids = self._expense_ids_from_create_commands(
                values.pop("expense_ids", []),
            )
            expenses = self.env["hr.expense"].browse(expense_ids).exists()
            dates = expenses.mapped("date")
            if dates:
                values.setdefault("context_date_from", min(dates))
                values.setdefault("context_date_to", max(dates))
            expense_ids_list.append(expense_ids)
            clean_values_list.append(values)

        batches = super().create(clean_values_list)
        for batch, expense_ids in zip(batches, expense_ids_list):
            if expense_ids:
                batch.add_expenses(expense_ids)
        return batches

    def add_expenses(self, expense_ids):
        self.ensure_one()
        self._check_readonly_accountant_mutation()
        self.check_access("write")
        self._ensure_active()
        expenses = self.env["hr.expense"].browse(expense_ids).exists()
        if len(expenses) != len(set(expense_ids)):
            raise ValidationError(_("Every selected expense must still exist."))
        expenses.check_access("read")
        invalid = expenses.filtered(
            lambda expense: (
                expense.state not in ("draft", "approved", "posted")
                or (
                    expense.expense_batch_id
                    and expense.expense_batch_id != self
                )
            ),
        )
        if invalid:
            raise ValidationError(
                _(
                    "Only unbatched draft, approved, or posted expenses "
                    "can be added to an expense batch.",
                ),
            )
        if expenses.filtered(lambda expense: expense.company_id != self.company_id):
            raise ValidationError(
                _("A batch cannot contain expenses from different companies."),
            )
        if expenses.filtered(lambda expense: expense.employee_id != self.employee_id):
            raise ValidationError(
                _("A batch cannot contain expenses from different employees."),
            )
        new_expenses = expenses.filtered(lambda expense: not expense.expense_batch_id)
        if new_expenses:
            # Native expense rules deliberately make approved and posted
            # records read-only for their employee. Linking the optional
            # grouping context is safe after the explicit access and
            # compatibility checks above.
            new_expenses.sudo().write({"expense_batch_id": self.id})
        return {
            "batch_id": self.id,
            "added": len(new_expenses),
            "unchanged": len(expenses - new_expenses),
        }

    def action_open_add_expenses_wizard(self):
        self.ensure_one()
        self._ensure_active()
        wizard = self.env["usl.expense.batch.add.wizard"].create({
            "batch_id": self.id,
        })
        return {
            "name": _("Add expenses"),
            "type": "ir.actions.act_window",
            "res_model": "usl.expense.batch.add.wizard",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": wizard.id,
            "target": "new",
        }

    @api.model
    def _expense_ids_from_create_commands(self, commands):
        expense_ids = []
        for command in commands:
            operation = command[0]
            if operation == Command.SET:
                expense_ids = list(command[2])
            elif operation == Command.LINK:
                expense_ids.append(command[1])
            elif operation == Command.CLEAR:
                expense_ids = []
            else:
                raise ValidationError(
                    _(
                        "Only existing expenses can be added when creating "
                        "an expense batch.",
                    ),
                )
        return list(dict.fromkeys(expense_ids))

    def _link_existing_moves(self):
        for batch in self:
            for move in batch.expense_ids.account_move_id.filtered(
                lambda candidate: not candidate.expense_batch_id,
            ):
                if move.expense_ids and all(
                    expense.expense_batch_id == batch
                    for expense in move.expense_ids
                ):
                    move.sudo().expense_batch_id = batch

    def write(self, values):
        self._check_readonly_accountant_mutation()
        self._check_account_override_access(values)
        editable_fields = {
            "name",
            "purpose",
            "context_type",
            "context_date_from",
            "context_date_to",
            "notes",
            "account_override_id",
            "analytic_distribution",
            "employee_id",
            "company_id",
        }
        if editable_fields.intersection(values):
            archived = self.filtered(lambda batch: not batch.active)
            if archived:
                raise UserError(
                    _("Reopen this Expense Batch before changing its information."),
                )
        structural_fields = {"employee_id", "company_id"}.intersection(values)
        if structural_fields:
            changed_structural_batch = self.filtered(
                lambda batch: batch.expense_ids
                and any(
                    values[field_name] != batch[field_name].id
                    for field_name in structural_fields
                ),
            )
            if changed_structural_batch:
                raise UserError(
                    _(
                        "The employee and company cannot change after expenses "
                        "have been added to a Batch.",
                    ),
                )
        context_fields = {
            "context_type",
            "context_date_from",
            "context_date_to",
            "purpose",
            "notes",
            "account_override_id",
            "analytic_distribution",
        }
        if context_fields.intersection(values) and "context_revision" not in values:
            for batch in self:
                super(UslExpenseBatch, batch).write({
                    **values,
                    "context_revision": batch.context_revision + 1,
                })
            return True
        return super().write(values)

    def unlink(self):
        self._check_readonly_accountant_mutation()
        return super().unlink()

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(
            expense.state != "draft"
            for batch in self
            for expense in batch.expense_ids
        ):
            raise UserError(
                _("A submitted expense batch must be preserved for auditability."),
            )

    def _ensure_active(self):
        if any(not batch.active for batch in self):
            raise UserError(
                _("Reopen this Expense Batch before changing or processing it."),
            )

    def _get_actionable_expenses(self, expected_state):
        self.ensure_one()
        expenses = self.expense_ids.filtered(lambda expense: expense.state != "refused")
        if not expenses:
            raise UserError(_("Add at least one expense before continuing."))
        actionable = expenses.filtered(lambda expense: expense.state == expected_state)
        if not actionable:
            raise UserError(
                _(
                    "This batch has no %(state)s expenses to process.",
                    state=dict(self.env["hr.expense"]._fields["state"].selection)[
                        expected_state
                    ],
                ),
            )
        return actionable

    def _context_expenses(self, expense_ids=None):
        self.ensure_one()
        expenses = (
            self.env["hr.expense"].browse(expense_ids).exists()
            if expense_ids is not None
            else self.expense_ids
        )
        if expenses - self.expense_ids:
            raise ValidationError(
                _("Every expense must already belong to this batch."),
            )
        expenses.check_access("read")
        return expenses

    def preview_context_application(self, expense_ids=None, force_expense_ids=None):
        """Return an RPC-safe, side-effect-free application preview."""
        self.ensure_one()
        self._ensure_active()
        expenses = self._context_expenses(expense_ids)
        force_ids = set(force_expense_ids or [])
        unknown_force_ids = force_ids - set(expenses.ids)
        if unknown_force_ids:
            raise ValidationError(_("Forced exceptions must belong to the preview."))

        lines = []
        counts = defaultdict(int)
        for expense in expenses.sorted(lambda item: (item.date, item.id)):
            line = {
                "expense_id": expense.id,
                "name": expense.display_name,
                "state": expense.state,
                "account": "unchanged",
                "analytics": "unchanged",
                "status": "unchanged",
                "reason": False,
                "has_exception": False,
            }
            if expense.state != "draft":
                line.update({
                    "status": "skipped",
                    "reason": _("Only draft expenses can receive shared context."),
                })
                counts["skipped"] += 1
                lines.append(line)
                continue

            forced = expense.id in force_ids
            actions = []
            if self.account_override_id:
                account_differs = expense.account_id != self.account_override_id
                if (
                    expense.account_context_source == "explicit"
                    and account_differs
                    and not forced
                ):
                    line["account"] = "exception"
                    actions.append("exception")
                elif (
                    account_differs
                    or expense.account_context_source != "batch"
                    or expense.batch_context_revision != self.context_revision
                ) and (expense.account_context_source != "explicit" or forced):
                    line["account"] = "replace" if forced else "inherit"
                    actions.append("change")
            if self.analytic_distribution:
                analytics_differ = not expense._analytic_distributions_equal(
                    expense.analytic_distribution,
                    self.analytic_distribution,
                )
                if (
                    expense.analytic_context_source == "explicit"
                    and analytics_differ
                    and not forced
                ):
                    line["analytics"] = "exception"
                    actions.append("exception")
                elif (
                    analytics_differ
                    or expense.analytic_context_source != "batch"
                    or expense.batch_context_revision != self.context_revision
                ) and (expense.analytic_context_source != "explicit" or forced):
                    line["analytics"] = "replace" if forced else "inherit"
                    actions.append("change")

            if "change" in actions:
                line["status"] = "change"
                counts["changed"] += 1
            elif "exception" in actions:
                line["status"] = "exception"
            if "exception" in actions:
                line["has_exception"] = True
                counts["exceptions"] += 1
            if not actions:
                counts["unchanged"] += 1
            lines.append(line)

        return {
            "batch_id": self.id,
            "context_revision": self.context_revision,
            "changed": counts["changed"],
            "unchanged": counts["unchanged"],
            "exceptions": counts["exceptions"],
            "skipped": counts["skipped"],
            "lines": lines,
        }

    def apply_context(
        self,
        expense_ids=None,
        force_expense_ids=None,
        expected_revision=None,
    ):
        """Apply shared context atomically while preserving explicit choices."""
        self.ensure_one()
        self._check_readonly_accountant_mutation()
        self._ensure_active()
        if expected_revision is not None and expected_revision != self.context_revision:
            raise UserError(
                _("The batch context changed. Refresh the preview before applying it."),
            )
        force_ids = set(force_expense_ids or [])
        if force_ids:
            self._check_account_override_access(force=True)
        expenses = self._context_expenses(expense_ids)
        preview = self.preview_context_application(
            expense_ids=expenses.ids,
            force_expense_ids=list(force_ids),
        )
        changed = self.env["hr.expense"]
        for expense in expenses:
            if expense.state != "draft":
                continue
            values = {}
            forced = expense.id in force_ids
            apply_account = self.account_override_id and (
                expense.account_context_source != "explicit" or forced
            ) and (
                expense.account_id != self.account_override_id
                or expense.account_context_source != "batch"
                or expense.batch_context_revision != self.context_revision
            )
            if apply_account:
                if not expense.batch_account_baseline_captured:
                    values.update({
                        "pre_batch_account_id": expense.account_id.id,
                        "pre_batch_account_context_source": (
                            expense.account_context_source
                        ),
                        "batch_account_baseline_captured": True,
                    })
                values.update({
                    "account_id": self.account_override_id.id,
                    "account_context_source": "batch",
                    "batch_applied_account_id": self.account_override_id.id,
                })
            apply_analytics = self.analytic_distribution and (
                expense.analytic_context_source != "explicit" or forced
            ) and (
                not expense._analytic_distributions_equal(
                    expense.analytic_distribution,
                    self.analytic_distribution,
                )
                or expense.analytic_context_source != "batch"
                or expense.batch_context_revision != self.context_revision
            )
            if apply_analytics:
                if not expense.batch_analytic_baseline_captured:
                    values.update({
                        "pre_batch_analytic_distribution": (
                            expense.analytic_distribution or {}
                        ),
                        "pre_batch_analytic_context_source": (
                            expense.analytic_context_source
                        ),
                        "batch_analytic_baseline_captured": True,
                    })
                values.update({
                    "analytic_distribution": self.analytic_distribution,
                    "analytic_context_source": "batch",
                    "batch_applied_analytic_distribution": self.analytic_distribution,
                })
            if values:
                values["batch_context_revision"] = self.context_revision
                expense.with_context(usl_batch_context_internal=True).write(values)
                changed |= expense
        if changed and not self.env.context.get("usl_batch_context_defer_audit"):
            self.message_post(
                body=_(
                    "Shared context revision %(revision)s applied to %(changed)s "
                    "expense(s); %(exceptions)s explicit exception(s) were preserved "
                    "and %(skipped)s later-stage expense(s) were skipped.",
                    revision=self.context_revision,
                    changed=len(changed),
                    exceptions=preview["exceptions"],
                    skipped=preview["skipped"],
                ),
            )
        return preview | {"applied": len(changed)}

    def get_review_summary(self):
        self.ensure_one()
        analytics = defaultdict(list)
        account_ids = self._get_analytic_account_ids_from_distributions(
            self.analytic_distribution,
        )
        for account in self.env["account.analytic.account"].sudo().browse(
            account_ids,
        ).exists():
            analytics[account.plan_id.display_name].append({
                "id": account.id,
                "name": account.display_name,
                "code": account.code,
            })
        products = defaultdict(lambda: {"count": 0, "total": 0.0})
        for expense in self.expense_ids:
            key = expense.product_id.display_name or _("Uncategorized")
            products[key]["count"] += 1
            products[key]["total"] += expense.total_amount
        return {
            "id": self.id,
            "active": self.active,
            "name": self.name,
            "purpose": self.purpose,
            "context_type": self.context_type,
            "context_revision": self.context_revision,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "context_date_from": self.context_date_from,
            "context_date_to": self.context_date_to,
            "total": self.total_amount,
            "expense_count": self.expense_count,
            "employee_paid_total": self.employee_paid_total,
            "company_paid_total": self.company_paid_total,
            "employee_paid_open_count": self.employee_paid_open_count,
            "company_paid_open_count": self.company_paid_open_count,
            "incomplete_count": self.incomplete_count,
            "exception_count": self.exception_count,
            "stale_context_count": self.stale_context_count,
            "warning_count": self.warning_count,
            "expense_progress": self.expense_progress,
            "attention": [
                {
                    "expense_id": expense.id,
                    "level": expense.batch_attention_level,
                    "message": expense.batch_attention_message,
                }
                for expense in self.expense_ids
                if expense.batch_attention_message
            ],
            "analytics": dict(analytics),
            "products": dict(products),
            "account_override": (
                {
                    "id": self.account_override_id.id,
                    "name": self.account_override_id.display_name,
                }
                if self.account_override_id
                else False
            ),
            "move_ids": self.move_ids.ids,
            "accounting": {
                "state": self.accounting_reconciliation_state,
                "accounted_expense_count": self.accounted_expense_count,
                "difference": self.accounting_difference,
            },
        }

    def action_open_context_wizard(self):
        self.ensure_one()
        self._ensure_active()
        wizard = self.env["usl.expense.batch.context.apply.wizard"].create({
            "batch_id": self.id,
            "expected_revision": self.context_revision,
        })
        return {
            "name": _("Apply shared Batch context"),
            "type": "ir.actions.act_window",
            "res_model": "usl.expense.batch.context.apply.wizard",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": wizard.id,
            "target": "new",
        }

    def action_submit(self):
        self.ensure_one()
        self._check_readonly_accountant_mutation()
        self._ensure_active()
        expenses = self._get_actionable_expenses("draft")
        incomplete = expenses.filtered(
            lambda expense: bool(expense.batch_incomplete_reason),
        )
        if incomplete:
            details = "\n".join(
                f"• {expense.name}: {expense.batch_incomplete_reason}"
                for expense in incomplete
            )
            raise UserError(
                _("Complete the following expenses before submission:\n%s", details),
            )
        expenses.action_submit()
        self.write({
            "submitted_by_id": self.env.user.id,
            "submitted_date": fields.Datetime.now(),
        })
        self.message_post(
            body=_(
                "Batch submitted with %(count)s expenses for manager review.",
                count=len(expenses),
            ),
        )
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_approve(self):
        self.ensure_one()
        self._check_readonly_accountant_mutation()
        self._ensure_active()
        expenses = self._get_actionable_expenses("submitted")
        result = expenses.with_context(validate_analytic=True).action_approve()
        if not result and all(expense.state == "approved" for expense in expenses):
            self.write({
                "approved_by_id": self.env.user.id,
                "approved_date": fields.Datetime.now(),
            })
            self.message_post(
                body=_(
                    "Batch approved with %(count)s expenses.",
                    count=len(expenses),
                ),
            )
            return {"type": "ir.actions.client", "tag": "reload"}
        return result

    def action_post(self):
        self.ensure_one()
        self._check_readonly_accountant_mutation()
        self._ensure_active()
        expenses = self._get_actionable_expenses("approved")
        result = expenses.action_post()
        if not result:
            self.message_post(
                body=_(
                    "Accounting entries posted for %(count)s expenses.",
                    count=len(expenses),
                ),
            )
            return {"type": "ir.actions.client", "tag": "reload"}
        return result

    def action_open_expenses(self):
        self.ensure_one()
        return self.expense_ids._get_records_action(name=self.name)

    def action_open_moves(self):
        self.ensure_one()
        action = {
            "name": _("Journal Entries"),
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "list,form",
            "views": [(False, "list"), (False, "form")],
            "domain": [("id", "in", self.move_ids.ids)],
        }
        if len(self.move_ids) == 1:
            action.update({
                "view_mode": "form",
                "views": [(False, "form")],
                "res_id": self.move_ids.id,
            })
        return action

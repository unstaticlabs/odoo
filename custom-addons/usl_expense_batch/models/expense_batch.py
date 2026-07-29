from collections import defaultdict

from lxml import etree

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class UslExpenseBatch(models.Model):
    _name = "usl.expense.batch"
    _description = "Expense Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_from desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    purpose = fields.Text(required=True, tracking=True)
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
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ("posted", "Posted"),
            ("paid", "Paid"),
            ("refused", "Returned"),
        ],
        compute="_compute_state",
        store=True,
        tracking=True,
        index=True,
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
    readiness_state = fields.Selection(
        selection=[
            ("ready", "Ready"),
            ("incomplete", "Needs information"),
        ],
        compute="_compute_review_context",
        string="Batch readiness",
    )
    main_analytic_activity = fields.Char(compute="_compute_review_context")
    move_ids = fields.Many2many(
        "account.move",
        compute="_compute_moves",
        string="Journal Entries",
    )
    move_count = fields.Integer(compute="_compute_moves")

    @api.depends("expense_ids.state")
    def _compute_state(self):
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
                batch.state = "refused" if batch.expense_ids else "draft"
                continue
            lowest_rank = min(
                state_rank.get(expense.state, 0) for expense in active_expenses
            )
            batch.state = rank_state[lowest_rank]

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
    )
    def _compute_review_context(self):
        analytic_account_model = self.env["account.analytic.account"]
        for batch in self:
            incomplete = batch.expense_ids.filtered(
                lambda expense: bool(expense.batch_incomplete_reason),
            )
            batch.incomplete_expense_ids = incomplete
            batch.incomplete_count = len(incomplete)
            batch.readiness_state = "incomplete" if incomplete else "ready"

            analytic_weights = defaultdict(float)
            for distribution in batch.expense_ids.mapped("analytic_distribution"):
                for account_keys, weight in (distribution or {}).items():
                    for account_key in account_keys.split(","):
                        if account_key.isdigit():
                            analytic_weights[int(account_key)] += weight
            if not analytic_weights:
                batch.main_analytic_activity = False
                continue
            main_account_id = max(
                analytic_weights,
                key=lambda account_id: analytic_weights[account_id],
            )
            batch.main_analytic_activity = (
                analytic_account_model.browse(main_account_id).display_name
            )

    @api.depends("expense_ids.account_move_id")
    def _compute_moves(self):
        for batch in self:
            batch.move_ids = batch.expense_ids.account_move_id
            batch.move_count = len(batch.move_ids)

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

    def _check_readonly_accountant_mutation(self):
        if (
            not self.env.su
            and self.env.user.has_group("account.group_account_readonly")
            and not self.env.user.has_group("account.group_account_user")
        ):
            raise AccessError(
                _("Read-only accountants cannot change expense batches."),
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
            result["arch"] = etree.tostring(arch, encoding="unicode")
        return result

    @api.model_create_multi
    def create(self, values_list):
        self._check_readonly_accountant_mutation()
        clean_values_list = []
        expense_ids_list = []
        for values in values_list:
            values = dict(values)
            expense_ids_list.append(
                self._expense_ids_from_create_commands(
                    values.pop("expense_ids", []),
                ),
            )
            clean_values_list.append(values)

        batches = super().create(clean_values_list)
        for batch, expense_ids in zip(batches, expense_ids_list):
            expenses = self.env["hr.expense"].browse(expense_ids).exists()
            if len(expenses) != len(expense_ids):
                raise ValidationError(_("Every selected expense must still exist."))
            expenses.check_access("read")
            invalid = expenses.filtered(
                lambda expense: (
                    expense.state not in ("draft", "approved", "posted")
                    or expense.expense_batch_id
                ),
            )
            if invalid:
                raise ValidationError(
                    _(
                        "Only unbatched draft, approved, or posted expenses "
                        "can be added to an expense batch.",
                    ),
                )
            if expenses.filtered(
                lambda expense: expense.company_id != batch.company_id,
            ):
                raise ValidationError(
                    _("A batch cannot contain expenses from different companies."),
                )
            if expenses.filtered(
                lambda expense: expense.employee_id != batch.employee_id,
            ):
                raise ValidationError(
                    _("A batch cannot contain expenses from different employees."),
                )
            if expenses:
                # Native expense rules deliberately make approved and posted
                # records read-only for their employee. Linking the optional
                # grouping context is safe after the explicit access and
                # compatibility checks above.
                expenses.sudo().write({"expense_batch_id": batch.id})
        batches._link_existing_moves()
        return batches

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
        return super().write(values)

    def unlink(self):
        self._check_readonly_accountant_mutation()
        return super().unlink()

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(batch.state != "draft" for batch in self):
            raise UserError(
                _("A submitted expense batch must be preserved for auditability."),
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

    def action_submit(self):
        self.ensure_one()
        self._check_readonly_accountant_mutation()
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

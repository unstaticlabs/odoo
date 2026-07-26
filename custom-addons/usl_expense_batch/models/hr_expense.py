from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class HrExpense(models.Model):
    _inherit = "hr.expense"

    expense_batch_id = fields.Many2one(
        "usl.expense.batch",
        string="Expense Batch",
        check_company=True,
        copy=False,
        index=True,
        ondelete="set null",
        tracking=True,
    )
    batch_readiness = fields.Selection(
        selection=[
            ("ready", "Ready to submit"),
            ("incomplete", "Needs information"),
            ("batched", "Already in a batch"),
        ],
        compute="_compute_batch_readiness",
        search="_search_batch_readiness",
        string="Batch readiness",
    )
    batch_incomplete_reason = fields.Char(compute="_compute_batch_readiness")

    def _is_batch_receipt_required(self):
        self.ensure_one()
        receipt_policy = self.product_id._fields.get("rebuild_receipt_required")
        if receipt_policy:
            return bool(self.product_id[receipt_policy.name])
        return True

    def _get_batch_incomplete_reasons(self):
        self.ensure_one()
        reasons = []
        if not self.name:
            reasons.append(_("description"))
        if not self.product_id:
            reasons.append(_("category"))
        if (
            self.company_currency_id.is_zero(self.total_amount)
            or self.currency_id.is_zero(self.total_amount_currency)
        ):
            reasons.append(_("non-zero amount"))
        if (
            self.product_id
            and self._is_batch_receipt_required()
            and not self.message_main_attachment_id
        ):
            reasons.append(_("receipt"))
        return reasons

    @api.depends(
        "expense_batch_id",
        "state",
        "name",
        "product_id",
        "total_amount",
        "total_amount_currency",
        "message_main_attachment_id",
    )
    def _compute_batch_readiness(self):
        for expense in self:
            reasons = (
                expense._get_batch_incomplete_reasons()
                if expense.state == "draft" or expense.expense_batch_id
                else []
            )
            if expense.expense_batch_id:
                expense.batch_readiness = "batched"
            elif expense.state != "draft":
                expense.batch_readiness = False
            else:
                expense.batch_readiness = "incomplete" if reasons else "ready"
            expense.batch_incomplete_reason = (
                _("Missing: %s", ", ".join(reasons)) if reasons else False
            )

    @api.model
    def _search_batch_readiness(self, operator, value):
        if operator not in ("=", "!=") or value not in (
            "ready",
            "incomplete",
            "batched",
        ):
            raise NotImplementedError
        if value == "batched":
            domain = [("expense_batch_id", "!=", False)]
        else:
            candidate_ids = self.search([
                ("state", "=", "draft"),
                ("expense_batch_id", "=", False),
            ]).filtered(
                lambda expense: expense.batch_readiness == value,
            ).ids
            domain = [("id", "in", candidate_ids)]
        if operator == "!=":
            return ["!"] + domain
        return domain

    @api.constrains("expense_batch_id", "employee_id", "company_id")
    def _check_expense_batch_compatibility(self):
        for expense in self.filtered("expense_batch_id"):
            if expense.company_id != expense.expense_batch_id.company_id:
                raise ValidationError(
                    _("The expense and its batch must belong to the same company."),
                )
            if expense.employee_id != expense.expense_batch_id.employee_id:
                raise ValidationError(
                    _("The expense and its batch must belong to the same employee."),
                )

    @api.model
    def action_open_expense_batch_wizard(self, expense_ids=None):
        expenses = self.browse(expense_ids or []).exists()
        if not expenses:
            expenses = self.search([
                ("employee_id.user_id", "=", self.env.user.id),
                ("company_id", "=", self.env.company.id),
                ("state", "=", "draft"),
                ("expense_batch_id", "=", False),
            ]).filtered(lambda expense: expense.batch_readiness == "ready")
        if not expenses:
            raise UserError(
                _(
                    "Select draft expenses, or complete at least one expense "
                    "that is ready to submit.",
                ),
            )
        invalid = expenses.filtered(
            lambda expense: expense.state != "draft" or expense.expense_batch_id,
        )
        if invalid:
            raise UserError(
                _("Only unbatched draft expenses can be added to a new batch."),
            )
        if len(expenses.company_id) != 1 or len(expenses.employee_id) != 1:
            raise UserError(
                _(
                    "Create a separate expense batch for each employee and company.",
                ),
            )
        wizard = self.env["usl.expense.batch.create.wizard"].create({
            "expense_ids": [Command.set(expenses.ids)],
        })
        return {
            "name": _("Create expense batch"),
            "type": "ir.actions.act_window",
            "res_model": "usl.expense.batch.create.wizard",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": wizard.id,
            "target": "new",
        }

    def action_return_from_batch(self):
        for expense in self:
            batch = expense.expense_batch_id
            if not batch:
                continue
            if expense.state == "draft":
                expense.expense_batch_id = False
                continue
            if expense.state not in ("submitted", "approved"):
                raise UserError(
                    _("Only submitted or approved expenses can be returned."),
                )
            expense._check_can_reset_approval()
            batch.message_post(
                body=_(
                    "%(expense)s was returned for individual correction by %(user)s.",
                    expense=expense.display_name,
                    user=self.env.user.display_name,
                ),
            )
            expense.expense_batch_id = False
            expense._do_reset_approval()
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_open_expense_batch(self):
        self.ensure_one()
        if not self.expense_batch_id:
            return False
        return {
            "name": self.expense_batch_id.name,
            "type": "ir.actions.act_window",
            "res_model": "usl.expense.batch",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": self.expense_batch_id.id,
        }

    @staticmethod
    def _expense_ids_from_move_vals(move_vals):
        expense_ids = []
        for command in move_vals.get("expense_ids", []):
            if command[0] == Command.SET:
                expense_ids.extend(command[2])
            elif command[0] == Command.LINK:
                expense_ids.append(command[1])
        return expense_ids

    def _prepare_receipts_vals(self):
        values_list = super()._prepare_receipts_vals()
        for values in values_list:
            expenses = self.browse(self._expense_ids_from_move_vals(values))
            batches = expenses.expense_batch_id
            if len(batches) == 1 and len(expenses) == len(
                expenses.filtered(lambda expense: expense.expense_batch_id == batches),
            ):
                values.update({
                    "expense_batch_id": batches.id,
                    "ref": batches.name,
                })
        return values_list

    def _prepare_payments_vals(self):
        move_values, payment_values = super()._prepare_payments_vals()
        if self.expense_batch_id:
            move_values.update({
                "expense_batch_id": self.expense_batch_id.id,
                "ref": self.expense_batch_id.name,
            })
            payment_values["memo"] = self.expense_batch_id.name
        return move_values, payment_values

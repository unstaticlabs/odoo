from odoo import _, models
from odoo.exceptions import AccessError

# Continuing an expense without a receipt is a judgement about evidence that
# will be missing for good, so it is recorded as a decision with a name
# against it rather than as ordinary data entry.  An Agent has no judgement
# of its own to record, which is why it used to be refused outright.  The
# owner can now accept that responsibility explicitly on the Agent record,
# and the Agent is then held to the same shape a person follows: it is shown
# exactly what would be written, told to take it to a person, and has to come
# back and say so before anything is stored.


# Native financial inputs and the saved mappings used when leaving a batch.
# Group membership itself remains ordinary metadata; its native restoration may
# only use a baseline that the Agent could not replace after approval.
_AGENT_EXPENSE_FINANCIAL_FIELDS = frozenset({
    "date", "company_id", "employee_id", "product_id", "product_uom_id",
    "account_id", "analytic_distribution", "tax_ids", "quantity", "price_unit",
    "total_amount", "total_amount_currency", "untaxed_amount", "untaxed_amount_currency",
    "tax_amount", "tax_amount_currency", "currency_id", "payment_mode",
    "payment_method_line_id", "vendor_id", "existing_bill_id", "has_existing_bill",
    "account_context_source", "analytic_context_source", "pre_batch_account_id",
    "pre_batch_account_context_source", "pre_batch_analytic_distribution",
    "pre_batch_analytic_context_source", "batch_applied_account_id",
    "batch_applied_analytic_distribution", "batch_account_baseline_captured",
    "batch_analytic_baseline_captured",
})
_AGENT_EXPENSE_LIFECYCLE_FIELDS = frozenset({"state", "approval_state", "approval_date", "account_move_id"})
# These fixed native roots accept no caller-supplied expense field values.
# Their own permission/state/company checks remain responsible for the action.
_AGENT_EXPENSE_LIFECYCLE_ROOTS = {
    ("hr.expense", "action_submit"): {"approval_state", "approval_date"},
    ("hr.expense", "action_approve"): {"approval_state", "approval_date"},
    ("hr.expense", "action_reset"): {"approval_state", "approval_date", "account_move_id"},
    ("hr.expense", "action_return_from_batch"): {"approval_state", "approval_date", "account_move_id"},
    ("hr.expense", "action_post"): {"account_move_id"},
    ("hr.expense.refuse.wizard", "action_refuse"): {"approval_state", "account_move_id"},
    ("hr.expense.approve.duplicate", "action_approve"): {"approval_state", "approval_date"},
    ("hr.expense.approve.duplicate", "action_refuse"): {"approval_state", "account_move_id"},
    ("hr.expense.post.wizard", "action_post_entry"): {"account_move_id"},
    ("usl.expense.batch", "action_submit"): {"approval_state", "approval_date"},
    ("usl.expense.batch", "action_approve"): {"approval_state", "approval_date"},
    ("usl.expense.batch", "action_post"): {"account_move_id"},
    ("usl.expense.bank.match.wizard", "action_confirm"): {"approval_state", "approval_date", "account_move_id"},
}


class HrExpense(models.Model):
    _inherit = "hr.expense"

    def write(self, vals):
        agent = self._usl_managed_agent()
        financial = _AGENT_EXPENSE_FINANCIAL_FIELDS.intersection(vals)
        lifecycle = _AGENT_EXPENSE_LIFECYCLE_FIELDS.intersection(vals)
        if agent and self and (financial or lifecycle):
            self.check_access("write")
            # Check every original state before any member or native delegate
            # changes. A stale cached Draft must not authorize a later edit.
            self.flush_recordset(["state", "approval_state", "account_move_id"])
            self.env.cr.execute(
                "SELECT id FROM hr_expense WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                (self.ids,),
            )
            self.invalidate_recordset(["state", "approval_state", "account_move_id", "company_id"])
            self.check_access("write")
            allowed_companies = (
                set(agent.company_ids.ids) & set(agent.owner_id.company_ids.ids)
                & set(self.env.context.get("allowed_company_ids") or agent.company_ids.ids)
            )
            if any(expense.company_id.id not in allowed_companies for expense in self) or (
                "company_id" in vals and vals["company_id"] not in allowed_companies
            ):
                raise AccessError(_("Select an authorized expense company in your company context."))
            scope = self._usl_agent_operation_scope(agent)
            root = (scope.root_model, scope.root_method) if scope else None
            if lifecycle - _AGENT_EXPENSE_LIFECYCLE_ROOTS.get(root, set()):
                raise AccessError(_("Use the native expense actions to submit, approve, refuse, reset or post an expense."))
            if financial and any(expense.state != "draft" or expense.account_move_id for expense in self):
                # Bank matching validates the manager, exact candidate, company
                # and current amount before performing only this conversion.
                bank_conversion = (
                    root == ("usl.expense.bank.match.wizard", "action_confirm")
                    and financial <= {"payment_mode", "payment_method_line_id", "vendor_id"}
                )
                if not bank_conversion:
                    raise AccessError(_("Agents can correct financial fields only on draft expenses without a journal entry. Use the native reset action first."))
        return super().write(vals)

    def _rebuild_receipt_waiver_note(self, reason):
        """Name the Agent and the human authority it acted on in the Chatter."""
        note = super()._rebuild_receipt_waiver_note(reason)
        agent = self._usl_managed_agent()
        if not agent:
            return note
        return _(
            "%(note)s Recorded by the Agent %(agent)s on the receipt-decision "
            "authority granted by %(owner)s, who remains accountable for it.",
            note=note,
            agent=agent.name,
            owner=agent.owner_id.name,
        )

    def _usl_receipt_waiver_preview(self, agent):
        """The dry run an Agent gets instead of recording the decision.

        It states plainly that nothing has been written, shows every fact a
        person needs to agree or object, and names the exact second call that
        records it.
        """
        return {
            "confirmation_required": True,
            "model": "hr.expense",
            "method": "action_rebuild_waive_receipt",
            "ids": self.ids,
            "warning": _(
                "Nothing has been recorded. Continuing without a receipt is a "
                "documented decision: the expense is submitted, approved and "
                "posted with no evidence behind it, and the reason below is "
                "what a reviewer or an auditor will read instead.",
            ),
            "ask_a_person": _(
                "Show this to the employee or an Expense Manager and get their "
                "agreement before confirming. The decision is recorded in the "
                "name of %(agent)s on the authority %(owner)s granted it.",
                agent=agent.name,
                owner=agent.owner_id.name,
            ),
            "expenses": [
                {
                    "id": expense.id,
                    "name": expense.name,
                    "employee": expense.employee_id.name,
                    "date": expense.date and str(expense.date) or False,
                    "amount": expense.total_amount_currency,
                    "currency": expense.currency_id.name,
                    "category": expense.product_id.display_name,
                    "reason": expense.rebuild_receipt_waiver_reason.strip(),
                }
                for expense in self
            ],
            "confirm_with": {
                "method": "action_rebuild_waive_receipt",
                "kwargs": {"agent_confirmed": True},
            },
        }

    def action_rebuild_waive_receipt(self, agent_confirmed=False):
        """Refuse, preview or record the decision depending on who is calling.

        A person calling this action records the decision directly, exactly
        as before. An Agent needs its owner's standing permission, and then
        two calls: the first returns the preview above and writes nothing,
        the second states that a person agreed.
        """
        agent = self._usl_managed_agent()
        if not agent:
            return super().action_rebuild_waive_receipt()
        if not agent.expense_receipt_waiver_authority:
            raise AccessError(
                _(
                    "Continuing without a receipt is a documented decision that "
                    "a person must record. Ask the employee or an Expense "
                    "Manager to confirm it, or ask %(owner)s to allow this "
                    "Agent to record receipt decisions on its Agent record.",
                    owner=agent.owner_id.name,
                ),
            )
        # The preview is only honest if it fails wherever the confirmation
        # would, so the access and the expense conditions are checked here
        # and not left to the recording call.
        self.check_access("write")
        self._rebuild_check_receipt_waiver_ready()
        if not agent_confirmed:
            return self._usl_receipt_waiver_preview(agent)
        return super().action_rebuild_waive_receipt()

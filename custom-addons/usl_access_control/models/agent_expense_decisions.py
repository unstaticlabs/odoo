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


class HrExpense(models.Model):
    _inherit = "hr.expense"

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

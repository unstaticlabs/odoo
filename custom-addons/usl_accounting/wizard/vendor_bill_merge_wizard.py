from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import format_amount

from ..models.vendor_bill_merge import _WIZARD_INIT, _WIZARD_INIT_KEY


class VendorBillMergeWizard(models.TransientModel):
    _name = "usl.vendor.bill.merge.wizard"
    _description = "Choose the Vendor Bill to Keep"

    bill_ids = fields.Many2many("account.move", readonly=True, required=True)
    snapshot = fields.Char(readonly=True, required=True)
    company_id = fields.Many2one("res.company", readonly=True, required=True)
    accountable_user_id = fields.Many2one("res.users", readonly=True, required=True)
    winning_bill_id = fields.Many2one("account.move", string="Bill to keep")
    winning_state = fields.Selection(related="winning_bill_id.state", readonly=True)
    winning_details = fields.Text(string="Bill selected", compute="_compute_winning_details")

    @api.depends("winning_bill_id")
    def _compute_winning_details(self):
        for wizard in self:
            bill = wizard.winning_bill_id
            wizard.winning_details = False if not bill else _(
                "Record %(id)s · %(reference)s\n%(amount)s · %(state)s",
                id=bill.id, reference=bill.ref or bill.name,
                amount=format_amount(wizard.env, bill.amount_total, bill.currency_id),
                state=dict(bill._fields["state"]._description_selection(wizard.env))[bill.state],
            )

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(_WIZARD_INIT_KEY) is not _WIZARD_INIT:
            raise AccessError(_("Open this choice from the selected bills' Merge action."))
        agent = self._usl_managed_agent() if hasattr(self, "_usl_managed_agent") else False
        vals_list = [dict(vals, accountable_user_id=agent.owner_id.id if agent else self.env.uid)
                     for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        self._check_merge_creator()
        if set(vals) - {"winning_bill_id"}:
            raise AccessError(_("The reviewed bill selection cannot be changed. Reopen Merge to refresh it."))
        if vals.get("winning_bill_id") and any(vals["winning_bill_id"] not in w.bill_ids.ids for w in self):
            raise UserError(_("Choose one of the selected bills to keep."))
        return super().write(vals)

    @api.private
    def _check_merge_creator(self):
        if any(wizard.create_uid.id != self.env.uid for wizard in self):
            raise AccessError(_("Only the person or Agent who opened this Merge dialog may change its choice or confirm it. Open your own Merge selection."))

    def action_merge(self):
        self.ensure_one()
        self.check_access("write")
        self._check_merge_creator()
        with self.env.cr.savepoint():
            bills = self.bill_ids
            bills._lock_vendor_bill_merge()
            if self.snapshot != bills._vendor_bill_merge_snapshot():
                raise UserError(_("These bills changed after you opened Merge. Close this dialog and review a fresh selection; no bills have changed."))
            return bills._apply_vendor_bill_merge(self.winning_bill_id, conflicting=True)

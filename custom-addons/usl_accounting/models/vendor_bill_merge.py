import hashlib
import json

from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import SQL


# Private Python identity initializes an immutable native choice wizard. It is
# not an ACL/accounting bypass and cannot be supplied through JSON RPC.
_WIZARD_INIT = object()
_WIZARD_INIT_KEY = "usl_vendor_bill_merge_init"
_HEADERS = (
    "partner_id", "company_id", "move_type", "state", "currency_id", "journal_id",
    "invoice_date", "date", "invoice_date_due", "ref", "payment_reference",
    "fiscal_position_id", "invoice_payment_term_id", "invoice_cash_rounding_id",
    "invoice_currency_rate", "narration", "payment_state", "auto_post", "auto_post_origin_id",
)
_LINE_FACTS = (
    "display_type", "name", "product_id", "product_uom_id", "account_id",
    "partner_id", "currency_id", "quantity", "price_unit", "discount", "tax_ids",
    "tax_line_id", "tax_repartition_line_id", "analytic_distribution",
    "balance", "amount_currency", "date_maturity", "debit", "credit",
)


def _facts(record, names):
    values = {}
    for name in names:
        value = record[name]
        if isinstance(value, models.BaseModel):
            value = value.ids
        values[name] = value
    return values


class AccountMove(models.Model):
    _inherit = "account.move"

    vendor_bill_merge_target_id = fields.Many2one(
        "account.move", string="Merged into", readonly=True, copy=False,
        check_company=True, index=True, ondelete="restrict",
    )
    vendor_bill_merge_original_count = fields.Integer(compute="_compute_vendor_bill_merge_original_count")

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("default_vendor_bill_merge_target_id") or any(
            vals.get("vendor_bill_merge_target_id") for vals in vals_list
        ):
            raise AccessError(_("Merge history can only be recorded by the Merge Bills action."))
        return super().create(vals_list)

    def write(self, vals):
        if "vendor_bill_merge_target_id" in vals:
            raise AccessError(_("Merge history can only be recorded by the Merge Bills action."))
        return super().write(vals)

    @api.private
    def _set_vendor_bill_merge_target(self, target):
        # Bypass only this field's public-input refusal. All inherited native
        # ACLs, record rules, financial guards and write hooks still execute.
        return super(AccountMove, self.with_context(skip_is_manually_modified=True)).write({
            "vendor_bill_merge_target_id": target.id if target else False,
        })

    def button_draft(self):
        recovered = self.filtered("vendor_bill_merge_target_id")
        with self.env.cr.savepoint():
            result = super().button_draft()
            recovered._set_vendor_bill_merge_target(False)
            for bill in recovered:
                bill.message_post(body=_("Restored to draft. This bill is no longer an active merged duplicate."),
                                  subtype_xmlid="mail.mt_note")
        return result

    @api.private
    def _vendor_bill_merge_originals(self):
        originals = self.browse()
        frontier, seen = self, set(self.ids)
        while frontier:
            children = self.search([
                ("vendor_bill_merge_target_id", "in", frontier.ids),
                ("state", "=", "cancel"), ("id", "not in", list(seen)),
            ])
            originals |= children
            seen.update(children.ids)
            frontier = children
        return originals

    def _compute_vendor_bill_merge_original_count(self):
        for bill in self:
            bill.vendor_bill_merge_original_count = len(bill._vendor_bill_merge_originals())

    def action_open_vendor_bill_merge_originals(self):
        self.ensure_one()
        self.check_access("read")
        return {"type": "ir.actions.act_window", "name": _("Original Bills"),
                "res_model": "account.move", "view_mode": "list,form",
                "domain": [("id", "in", self._vendor_bill_merge_originals().ids)]}

    def action_open_vendor_bill_merge_target(self):
        self.ensure_one()
        self.check_access("read")
        target = self.vendor_bill_merge_target_id
        if not target:
            raise UserError(_("This bill is not an active merged duplicate."))
        target.check_access("read")
        return target.get_formview_action()

    @api.private
    def _check_vendor_bill_merge(self):
        if len(self) < 2 or len(self) > 100:
            raise UserError(_("Select between two and 100 vendor bills to merge."))
        self.check_access("read")
        self.check_access("write")
        if len(self.exists()) != len(self):
            raise UserError(_("A selected bill is no longer available. Refresh and select the bills again."))
        if any(b.move_type != "in_invoice" or b.state not in ("draft", "posted") for b in self):
            raise UserError(_("Only draft or eligible posted unpaid vendor bills can be merged."))
        if len(self.company_id) != 1 or self.company_id not in self.env.companies:
            raise UserError(_("Select bills from one company you can currently access."))
        if any(not b.partner_id for b in self):
            raise UserError(_("Choose a supplier on every bill before merging."))
        if any(b.payment_ids or b.payment_state in ("paid", "in_payment", "partial")
               or any(l.reconciled or l.matched_debit_ids or l.matched_credit_ids for l in b.line_ids)
               for b in self):
            raise UserError(_("Bills with payments or reconciliations cannot be merged. Use the native payment recovery workflow first."))
        posted = self.filtered(lambda b: b.state == "posted")
        if any(b.need_cancel_request for b in posted):
            raise UserError(_("A selected bill requires native cancellation approval before it can be merged."))
        posted._check_draftable()
        posted._check_fiscal_lock_dates()
        posted.line_ids._check_tax_lock_date()
        # Match native Reset to Draft's exact immediate-next recurrence query.
        # No hidden recurrence identifiers or names are returned to the actor.
        recurring = posted.filtered("auto_post_origin_id")
        if recurring:
            self.flush_model(["date", "auto_post_origin_id", "state"])
            if self.env.execute_query(SQL("""
                SELECT 1 FROM account_move current_move JOIN LATERAL (
                    SELECT move.state FROM account_move move
                    WHERE move.auto_post_origin_id = current_move.auto_post_origin_id
                      AND move.date > current_move.date
                    ORDER BY move.date, move.id LIMIT 1
                ) next_move ON TRUE
                WHERE current_move.id IN %s AND next_move.state = 'draft' LIMIT 1
            """, tuple(recurring.ids))):
                raise UserError(_("Native cancellation would delete a future recurring draft. Resolve that recurrence before merging; no bills have changed."))

    @api.private
    def _lock_vendor_bill_merge(self):
        self._check_vendor_bill_merge()
        self.flush_recordset()
        self.line_ids.flush_recordset()
        self.env.execute_query(SQL("SELECT id FROM account_move WHERE id IN %s ORDER BY id FOR UPDATE", tuple(self.ids)))
        self.env.execute_query(SQL("SELECT id FROM account_move_line WHERE move_id IN %s ORDER BY id FOR UPDATE", tuple(self.ids)))
        self.invalidate_recordset()
        self.line_ids.invalidate_recordset()
        self._check_vendor_bill_merge()

    @api.private
    def _vendor_bill_merge_facts(self, *, snapshot=False):
        result = []
        for bill in self.sorted("id"):
            facts = _facts(bill, _HEADERS)
            facts["lines"] = [_facts(line, _LINE_FACTS) for line in bill.line_ids.sorted(lambda l: (l.sequence, l.id))]
            if snapshot:
                facts["id"] = bill.id
                facts["line_ids"] = bill.line_ids.ids
                facts["review_state"] = bill.review_state
                facts["main_attachment"] = bill.message_main_attachment_id.id
                attachments = self.env["ir.attachment"].search([
                    ("res_model", "=", self._name), ("res_id", "=", bill.id),
                ], order="id")
                facts["evidence"] = attachments.read(["id", "checksum", "name", "mimetype", "res_field"])
            result.append(facts)
        return result

    @api.private
    def _vendor_bill_merge_snapshot(self):
        payload = json.dumps(self._vendor_bill_merge_facts(snapshot=True), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def action_merge_vendor_bills(self):
        with self.env.cr.savepoint():
            self._lock_vendor_bill_merge()
            same_state = len(set(self.mapped("state"))) == 1
            empty = same_state and len(self.partner_id) == 1 and all(not b.invoice_line_ids for b in self)
            facts = self._vendor_bill_merge_facts()
            if empty or all(f == facts[0] for f in facts):
                winner = self.sorted(lambda b: (b.create_date, b.id))[:1]
                return self._apply_vendor_bill_merge(winner, conflicting=False)
            wizard = self.env["usl.vendor.bill.merge.wizard"].with_context(
                **{_WIZARD_INIT_KEY: _WIZARD_INIT},
            ).create({"bill_ids": [Command.set(self.ids)], "company_id": self.company_id.id,
                      "snapshot": self._vendor_bill_merge_snapshot()})
            return {"type": "ir.actions.act_window", "name": _("Choose the bill to keep"),
                    "res_model": wizard._name, "res_id": wizard.id, "view_mode": "form", "target": "new"}

    def action_choose_vendor_bill_merge_winner(self):
        """Select from an owned comparison; final merge is a separate action."""
        self.ensure_one()
        self.check_access("read")
        self.check_access("write")
        wizard_id = self.env.context.get("vendor_bill_merge_wizard_id")
        if isinstance(wizard_id, bool) or not isinstance(wizard_id, int):
            raise UserError(_("Choose a bill from the open Merge dialog."))
        wizard = self.env["usl.vendor.bill.merge.wizard"].browse(wizard_id)
        wizard.check_access("write")
        wizard.bill_ids._check_vendor_bill_merge()
        wizard.write({"winning_bill_id": self.id})
        return {"type": "ir.actions.act_window", "name": _("Choose the bill to keep"),
                "res_model": wizard._name, "res_id": wizard.id, "view_mode": "form", "target": "new"}

    @api.private
    def _apply_vendor_bill_merge(self, winner, *, conflicting):
        if len(winner) != 1 or winner not in self:
            raise UserError(_("Choose one of the selected bills to keep."))
        losers = self - winner
        records = self._usl_agent_collaboration(operation="write") if hasattr(
            self, "_usl_agent_collaboration",
        ) else self
        native_losers = records.browse(losers.ids)
        native_losers.button_cancel()
        native_losers._set_vendor_bill_merge_target(winner)
        if conflicting:
            records.browse(winner.ids).write({"review_state": "todo"})
        for original in native_losers:
            original.message_post(body=Markup("<p>%s %s</p>") % (
                _("Merged as a duplicate. Bill kept:"), winner._get_html_link()), subtype_xmlid="mail.mt_note")
            records.browse(winner.ids).message_post(body=Markup("<p>%s %s</p>") % (
                _("Original bill retained with its files and history:"), original._get_html_link()), subtype_xmlid="mail.mt_note")
        # Return the original, ordinary context; never serialize a private token.
        return winner.get_formview_action()

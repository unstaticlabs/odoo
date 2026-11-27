from contextlib import nullcontext

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_date


def _compatible(records, company):
    """Use the related model's own branch, company and shared-record rules."""
    return records.with_company(company).filtered_domain(records._check_company_domain(company))


class AccountMove(models.Model):
    _inherit = "account.move"

    def _check_draft_company_transfer(self):
        self.ensure_one()
        self.check_access("write")
        if self.move_type not in ("in_invoice", "in_refund") or self.state != "draft":
            raise UserError(_("Open a draft vendor bill or vendor credit note to change its company."))

    def action_change_bill_company(self):
        self._check_draft_company_transfer()
        wizard = self.env["usl.bill.company.transfer"].create({"bill_id": self.id})
        return wizard._dialog()


class BillCompanyTransfer(models.TransientModel):
    _name = "usl.bill.company.transfer"
    _description = "Change the company of a draft bill"

    bill_id = fields.Many2one("account.move", string="Bill", required=True, readonly=True, ondelete="cascade")
    source_company_id = fields.Many2one(related="bill_id.company_id", string="Current company", compute_sudo=False)
    source_journal_id = fields.Many2one(related="bill_id.journal_id", string="Current journal", compute_sudo=False)
    stage = fields.Selection([("choose", "Choose company"), ("map", "Review accounts"), ("done", "Transferred")],
                             default="choose", required=True, readonly=True)
    target_company_id = fields.Many2one("res.company", string="Destination company")
    journal_id = fields.Many2one("account.journal", string="Destination journal")
    fiscal_position_id = fields.Many2one("account.fiscal.position", string="Destination fiscal position")
    source_fiscal_position_id = fields.Many2one(related="bill_id.fiscal_position_id", string="Current fiscal position", compute_sudo=False)
    remove_fiscal_position = fields.Boolean(string="Use no fiscal position")
    term_id = fields.Many2one("account.payment.term", string="Destination payment terms")
    source_term_id = fields.Many2one(related="bill_id.invoice_payment_term_id", string="Current payment terms", compute_sudo=False)
    mapping_ids = fields.One2many("usl.bill.company.transfer.line", "transfer_id", string="Invoice lines")
    snapshot = fields.Char(readonly=True)
    warning = fields.Text(readonly=True)
    consequences = fields.Text(compute="_compute_consequences")

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            self.env["account.move"].browse(values.get("bill_id"))._check_draft_company_transfer()
        return super().create(vals_list)

    def write(self, vals):
        if "bill_id" in vals and any(wizard.bill_id.id != vals["bill_id"] for wizard in self):
            raise UserError(_("Open a new transfer from the bill you want to change."))
        return super().write(vals)

    def _dialog(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "name": _("Change company"), "view_mode": "form", "target": "new"}

    def _check_transfer(self):
        self.ensure_one()
        self.check_access("write")
        bill = self.bill_id
        bill._check_draft_company_transfer()
        target = self.target_company_id
        if not target or not bill.company_id or target not in self.env.companies or bill.company_id not in self.env.companies:
            raise AccessError(_("Select the original and destination companies in your active companies."))
        target.check_access("read")
        if target == bill.company_id:
            raise UserError(_("Choose a different destination company."))
        return bill, target

    @api.depends_context("lang")
    @api.depends("bill_id", "bill_id.line_ids", "bill_id.matched_payment_ids", "target_company_id")
    def _compute_consequences(self):
        for wizard in self:
            bill = wizard.bill_id
            due_dates = sorted(line.date_maturity for line in bill.line_ids
                               if line.display_type == "payment_term" and line.date_maturity)
            lines = [_("The bill keeps its reference, currency, quantities and prices. Original files stay with their current company.")]
            if due_dates:
                lines.append(_("Current due dates: %(dates)s.", dates=", ".join(format_date(self.env, date) for date in due_dates)))
            if bill.matched_payment_ids or bill.line_ids.matched_debit_ids or bill.line_ids.matched_credit_ids:
                lines.append(_("A payment or reconciliation is linked. Review it in Accounting before changing company."))
            wizard.consequences = "\n".join(lines)

    def action_prepare_mapping(self):
        bill, target = self._check_transfer()
        if self.stage == "done":
            raise UserError(_("Open a new transfer from the current bill."))
        journal = self.env["account.journal"].with_company(target).search(
            [("type", "=", "purchase"), ("company_id", "=", target.id)], order="sequence, id", limit=1)
        mapped = [Command.clear()]
        for line in bill.invoice_line_ids.filtered(lambda item: item.display_type == "product"):
            account = _compatible(line.account_id, target)
            if not account and line.product_id:
                account = line.product_id.product_tmpl_id.with_company(target).get_product_accounts().get("expense")
            taxes = _compatible(line.tax_ids, target)
            mapped.append(Command.create({
                "invoice_line_id": line.id,
                "target_account_id": account.id if account else False,
                "target_tax_ids": [Command.set(taxes.ids)] if taxes == line.tax_ids else [],
                "analytic_distribution": line.analytic_distribution,
            }))
        self.write({"stage": "map", "journal_id": journal.id,
                    "fiscal_position_id": _compatible(bill.fiscal_position_id, target).id, "remove_fiscal_position": False,
                    "term_id": _compatible(bill.invoice_payment_term_id, target).id,
                    "snapshot": bill._vendor_bill_merge_snapshot(), "mapping_ids": mapped, "warning": False})
        return self._dialog()

    def action_choose_company(self):
        self.ensure_one()
        self.check_access("write")
        self.write({"stage": "choose", "warning": False})
        return self._dialog()

    def _move_values(self, bill, target):
        if not self.journal_id or self.journal_id.company_id != target or self.journal_id.type != "purchase":
            raise UserError(_("Choose a purchase journal belonging to the destination company."))
        for record in (self.journal_id, self.fiscal_position_id, self.term_id):
            record.check_access("read")
            if record != _compatible(record, target):
                raise UserError(_("Select a compatible destination journal, fiscal position and payment terms."))
        if bill.invoice_payment_term_id and not self.term_id:
            raise UserError(_("Choose the destination payment terms so the installments can be recalculated."))
        if bill.fiscal_position_id and not self.fiscal_position_id and not self.remove_fiscal_position:
            raise UserError(_("Select the destination fiscal position or explicitly choose no fiscal position."))
        product_lines = bill.invoice_line_ids.filtered(lambda ln: ln.display_type == "product")
        selected = self.mapping_ids.invoice_line_id
        if len(self.mapping_ids) != len(product_lines) or set(selected.ids) != set(product_lines.ids):
            raise UserError(_("The invoice lines changed. Open a fresh preview."))
        values = {"company_id": target.id, "journal_id": self.journal_id.id,
                "fiscal_position_id": self.fiscal_position_id.id, "invoice_payment_term_id": self.term_id.id,
                "currency_id": bill.currency_id.id,
                "invoice_line_ids": [mapping._line_command(target) for mapping in self.mapping_ids]}
        if not self.term_id:
            values["invoice_date_due"] = bill.invoice_date_due
        return values

    def _bill_action(self):
        bill = self.bill_id
        bill.check_access("read")
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": bill.id,
                "name": bill.display_name, "view_mode": "form", "target": "current"}

    def action_apply(self):
        self.ensure_one()
        self.check_access("write")
        if self.stage == "done" and self.bill_id.company_id == self.target_company_id:
            return self._bill_action()
        bill, target = self._check_transfer()
        if not bill.try_lock_for_update() or len(bill.line_ids.try_lock_for_update()) != len(bill.line_ids):
            raise UserError(_("The bill is being edited. Try again after the other edit finishes."))
        if self.stage != "map" or self.snapshot != bill._vendor_bill_merge_snapshot():
            raise UserError(_("The bill changed since this preview. Review the current bill and its accounts again."))
        if (bill.origin_payment_id or bill.matched_payment_ids or bill.line_ids.matched_debit_ids
                or bill.line_ids.matched_credit_ids or bill.line_ids.full_reconcile_id or bill.statement_line_id):
            raise UserError(_("A payment, bank statement or reconciliation is linked. Open the related transaction in Accounting and resolve its company before retrying. The bill and payment remain linked."))
        values = self._move_values(bill, target)
        bill._check_fiscal_lock_dates()
        origin = bill.company_id
        documents = self.env["usl.document"] if "usl.document" in self.env else None
        with (documents._track_paperless_permission_writes() if documents is not None else nullcontext(set())) as touched:
            try:
                with self.env.cr.savepoint():
                    bill = bill.with_company(target)
                    bill.write(values)
                    bill.line_ids.filtered(lambda ln: ln.display_type == "payment_term")._compute_account_id()
                    bill._check_fiscal_lock_dates()
                    bill.line_ids.flush_recordset()
                    bill.message_post(body=_("Draft transferred from %(source)s to %(target)s.",
                                             source=origin.display_name, target=target.display_name),
                                      subtype_xmlid="mail.mt_note")
                    self.write({"stage": "done", "warning": False})
            except UserError as error:
                if not touched:
                    raise
                failed = documents._restore_paperless_permission_writes(touched)
                recovery = (_("Archive access still needs synchronization. Documents will retry the affected permissions.")
                            if failed else _("The archive permissions have been restored."))
                self.warning = _("%(reason)s The bill remains in its original company. %(recovery)s",
                                 reason=str(error), recovery=recovery)
                return self._dialog()
        return self._bill_action()


class BillCompanyTransferLine(models.TransientModel):
    _name = "usl.bill.company.transfer.line"
    _description = "Bill line accounts in the destination company"

    transfer_id = fields.Many2one("usl.bill.company.transfer", ondelete="cascade", required=True)
    invoice_line_id = fields.Many2one("account.move.line", required=True, readonly=True, ondelete="cascade")
    label = fields.Char(related="invoice_line_id.name", string="Item", compute_sudo=False)
    source_account_id = fields.Many2one(related="invoice_line_id.account_id", string="Current account", compute_sudo=False)
    source_tax_ids = fields.Many2many(related="invoice_line_id.tax_ids", string="Current taxes", compute_sudo=False)
    target_account_id = fields.Many2one("account.account", string="Destination account")
    target_tax_ids = fields.Many2many("account.tax", string="Destination taxes")
    remove_taxes = fields.Boolean(string="Use no tax")
    company_id = fields.Many2one(related="transfer_id.target_company_id", compute_sudo=False)
    analytic_distribution = fields.Json(string="Analytic distribution")

    @api.constrains("transfer_id", "invoice_line_id")
    def _check_selected_line(self):
        for mapping in self:
            mapping.transfer_id.check_access("write")
            mapping.invoice_line_id.check_access("read")
            if mapping.invoice_line_id.move_id != mapping.transfer_id.bill_id:
                raise UserError(_("Select a line from the bill being transferred."))

    def _line_command(self, target):
        self.ensure_one()
        line = self.invoice_line_id
        line.check_access("write")
        account, taxes = self.target_account_id, self.target_tax_ids
        if not account or not account.active or account != _compatible(account, target):
            raise UserError(_("Choose a compatible destination account for %(line)s.", line=line.name))
        if taxes != _compatible(taxes, target) or any(tax.type_tax_use != "purchase" or not tax.active for tax in taxes):
            raise UserError(_("Select the destination purchase taxes for %(line)s.", line=line.name))
        if line.tax_ids and not taxes and not self.remove_taxes:
            raise UserError(_("Select destination taxes or explicitly choose no tax for %(line)s.", line=line.name))
        for records in (account, taxes):
            records.check_access("read")
        distribution = self.analytic_distribution or {}
        self.transfer_id.bill_id._validate_draft_vendor_bill_line_values({"analytic_distribution": distribution})
        try:
            analytic_ids = line._get_analytic_account_ids_from_distributions(distribution)
        except (ValueError, TypeError) as error:
            raise UserError(_("Select valid analytic accounts for %(line)s.", line=line.name)) from error
        analytics = self.env["account.analytic.account"].browse(list(analytic_ids))
        if analytics != _compatible(analytics.exists(), target):
            raise UserError(_("Review the analytic distribution for %(line)s in the destination company.", line=line.name))
        analytics.check_access("read")
        return Command.update(line.id, {"account_id": account.id, "tax_ids": [Command.set(taxes.ids)],
                                        "analytic_distribution": self.analytic_distribution,
                                        "quantity": line.quantity, "price_unit": line.price_unit,
                                        "discount": line.discount})

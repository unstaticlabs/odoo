"""Dialog around `account.move.change_bill_company`: it holds choices, the method decides."""
from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date


class BillCompanyTransfer(models.TransientModel):
    _name = "usl.bill.company.transfer"
    _description = "Change the company of a draft bill"

    bill_id = fields.Many2one("account.move", string="Bill", required=True, readonly=True, ondelete="cascade")
    source_company_id = fields.Many2one(related="bill_id.company_id", string="Current company", compute_sudo=False)
    source_journal_id = fields.Many2one(related="bill_id.journal_id", string="Current journal", compute_sudo=False)
    stage = fields.Selection([("choose", "Choose company"), ("map", "Review accounts"), ("done", "Transferred")],
                             default="choose", required=True, readonly=True)
    target_company_id = fields.Many2one("res.company", string="Destination company record")
    target_company_choice = fields.Selection(selection="_target_company_choices", string="Destination company",
                                             compute="_compute_target_company_choice",
                                             inverse="_inverse_target_company_choice")
    journal_id = fields.Many2one("account.journal", string="Destination journal")
    fiscal_position_id = fields.Many2one("account.fiscal.position", string="Destination fiscal position")
    source_fiscal_position_id = fields.Many2one(related="bill_id.fiscal_position_id", string="Current fiscal position", compute_sudo=False)
    remove_fiscal_position = fields.Boolean(string="Use no fiscal position")
    term_id = fields.Many2one("account.payment.term", string="Destination payment terms")
    source_term_id = fields.Many2one(related="bill_id.invoice_payment_term_id", string="Current payment terms", compute_sudo=False)
    mapping_ids = fields.One2many("usl.bill.company.transfer.line", "transfer_id", string="Invoice lines")
    scope_company_ids = fields.Json(compute="_compute_scope_company_ids",
                                    help="Both companies, so the destination's records can be read and chosen.")
    active_company_ids = fields.Json(readonly=True, help="The companies that were active when the dialog opened.")
    snapshot = fields.Char(readonly=True)
    warning = fields.Text(readonly=True)
    destination_summary = fields.Text(readonly=True)
    blockers_html = fields.Html(compute="_compute_blockers", sanitize=False)
    has_blockers = fields.Boolean(compute="_compute_blockers")
    consequences = fields.Text(compute="_compute_consequences")

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            self.env["account.move"].browse(values.get("bill_id"))._check_draft_company_transfer()
            values.setdefault("active_company_ids", self.env.companies.ids)
        return super().create(vals_list)

    def _both_companies(self):
        """The bill's company and the destination, when the operator belongs to both."""
        self.ensure_one()
        operator = self.env["res.users"].sudo().browse(self.env.uid)
        return (self.bill_id.company_id | self.target_company_id) & operator.company_ids

    def _in_both_companies(self):
        """These records, read and written with the current and destination companies in scope.

        The web client keeps the companies active in the selector, which hides the destination's accounts and taxes.
        """
        companies = self.env["res.company"]
        for wizard in self:
            companies |= wizard._both_companies()
        return self.with_context(allowed_company_ids=companies.ids) if companies else self

    def web_read(self, specification):
        return super(BillCompanyTransfer, self._in_both_companies()).web_read(specification)

    def write(self, vals):
        if "bill_id" in vals and any(wizard.bill_id.id != vals["bill_id"] for wizard in self):
            raise UserError(_("Open a new transfer from the bill you want to change."))
        return super(BillCompanyTransfer, self._in_both_companies()).write(vals)

    def _dialog(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "name": _("Change company"), "view_mode": "form", "target": "new"}

    @api.depends("bill_id", "target_company_id")
    def _compute_scope_company_ids(self):
        for wizard in self:
            wizard.scope_company_ids = wizard._both_companies().ids

    @api.model
    def _target_company_choices(self):
        """Every company of the operator, not only the ones active in the selector."""
        operator = self.env["res.users"].sudo().browse(self.env.uid)
        return [(str(company.id), company.display_name) for company in operator.company_ids]

    @api.depends("target_company_id")
    def _compute_target_company_choice(self):
        for wizard in self:
            wizard.target_company_choice = str(wizard.target_company_id.id) if wizard.target_company_id else False

    def _inverse_target_company_choice(self):
        for wizard in self:
            wizard.sudo().target_company_id = int(wizard.target_company_choice) if wizard.target_company_choice else False

    @api.depends_context("lang")
    @api.depends("bill_id", "bill_id.line_ids", "bill_id.matched_payment_ids", "target_company_id")
    def _compute_blockers(self):
        for wizard in self:
            blockers = wizard.bill_id._company_transfer_blockers(wizard.target_company_id or None) if wizard.bill_id else []
            wizard.has_blockers = bool(blockers)
            items = Markup("").join(
                Markup('<li class="mb-1"><strong>%s</strong> %s<br/>%s</li>') % (
                    item["message"],
                    Markup(" ").join(
                        Markup('<a href="%s" target="_blank">%s</a>') % (record["url"], record["display_name"])
                        for record in item["records"]),
                    item["next_step"])
                for item in blockers)
            wizard.blockers_html = Markup('<ul class="mb-0">%s</ul>') % items if blockers else False

    @api.depends_context("lang")
    @api.depends("bill_id", "bill_id.line_ids")
    def _compute_consequences(self):
        for wizard in self:
            bill = wizard.bill_id
            due_dates = sorted(line.date_maturity for line in bill.line_ids
                               if line.display_type == "payment_term" and line.date_maturity)
            lines = [_("The bill keeps its reference, currency, quantities and prices. Original files stay with their current company.")]
            if due_dates:
                lines.append(_("Current due dates: %(dates)s.", dates=", ".join(format_date(self.env, date) for date in due_dates)))
            wizard.consequences = "\n".join(lines)

    def _scoped_bill(self):
        self.ensure_one()
        self.check_access("write")
        self.bill_id._check_draft_company_transfer()
        if not self.target_company_id:
            raise UserError(_("Choose the destination company."))
        return self.bill_id

    def action_prepare_mapping(self):
        bill = self._scoped_bill()
        if self.stage == "done":
            raise UserError(_("Open a new transfer from the current bill."))
        result = bill.change_bill_company(self.target_company_id.id)
        if result["blockers"]:
            raise UserError("\n".join("%s %s" % (item["message"], item["next_step"]) for item in result["blockers"]))
        proposal = result["proposal"]
        mapped = [Command.clear()]
        for line in proposal["lines"]:
            source_line = self.env["account.move.line"].browse(line["line_id"])
            mapped.append(Command.create({
                "invoice_line_id": line["line_id"],
                "target_account_id": line["to"]["account"]["id"] if line["to"]["account"] else False,
                "target_tax_ids": [Command.set([tax["id"] for tax in line["to"]["taxes"]])],
                "analytic_distribution": source_line.analytic_distribution,
            }))
        destination = result["destination"] or {}
        summary = []
        if destination:
            summary.append(_("Destination total: %(total)s %(currency)s, of which taxes %(tax)s.",
                             total=destination["amount_total"], currency=destination["currency"],
                             tax=destination["amount_tax"]))
            if destination["due_dates"]:
                summary.append(_("Destination due dates: %(dates)s.", dates=", ".join(
                    "%s (%s)" % (due["date"], due["amount"]) for due in destination["due_dates"])))
        payable = proposal["payable_account"]
        if payable:
            summary.append(_("Payable account: %(account)s.", account=payable["display_name"]))
        self.write({
            "stage": "map", "journal_id": proposal["journal"]["id"] if proposal["journal"] else False,
            "fiscal_position_id": proposal["fiscal_position"]["id"] if proposal["fiscal_position"] else False,
            "remove_fiscal_position": False,
            "term_id": proposal["payment_term"]["id"] if proposal["payment_term"] else False,
            "snapshot": result["preview_id"], "mapping_ids": mapped,
            "destination_summary": "\n".join(summary),
            "warning": "\n".join(item["message"] for item in result["unresolved"]) or False,
        })
        return self._dialog()

    def action_choose_company(self):
        self.ensure_one()
        self.check_access("write")
        self.write({"stage": "choose", "warning": False})
        return self._dialog()

    def _mapping(self, bill):
        """The choices on screen, sent explicitly so the method never fills in a different value."""
        if not self.journal_id:
            raise UserError(_("Choose the destination journal."))
        mapping = {"journal_id": self.journal_id.id}
        if bill.invoice_payment_term_id and not self.term_id:
            raise UserError(_("Choose the destination payment terms so the installments can be recalculated."))
        if self.term_id:
            mapping["payment_term_id"] = self.term_id.id
        if self.fiscal_position_id:
            mapping["fiscal_position_id"] = self.fiscal_position_id.id
        elif self.remove_fiscal_position or not bill.fiscal_position_id:
            mapping["fiscal_position_id"] = False
        else:
            raise UserError(_("Select the destination fiscal position or explicitly choose no fiscal position."))
        product_lines = bill.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        if set(self.mapping_ids.invoice_line_id.ids) != set(product_lines.ids) or len(self.mapping_ids) != len(product_lines):
            raise UserError(_("The invoice lines changed. Open a fresh preview."))
        lines = []
        for item in self.mapping_ids:
            if item.invoice_line_id.tax_ids and not item.target_tax_ids and not item.remove_taxes:
                raise UserError(_("Select destination taxes or explicitly choose no tax for %(line)s.",
                                  line=item.invoice_line_id.name))
            if not item.target_account_id:
                raise UserError(_("Choose a destination account for %(line)s.", line=item.invoice_line_id.name))
            lines.append({"line_id": item.invoice_line_id.id, "account_id": item.target_account_id.id,
                          "tax_ids": item.target_tax_ids.ids, "analytic_distribution": item.analytic_distribution or {}})
        mapping["lines"] = lines
        return mapping

    def _bill_action(self):
        bill = self.bill_id
        if bill.company_id.id not in (self.active_company_ids or []):
            # The form behind this dialog shows a bill the web client can no longer read: go to the bill list.
            vendor_bills = self.env["ir.actions.act_window"]._for_xml_id("account.action_move_in_invoice_type")
            return {"type": "ir.actions.client", "tag": "display_notification", "params": {
                "type": "success", "sticky": True,
                "message": _("The bill is now in %(company)s. Activate it in the company selector to open it.",
                             company=bill.sudo().company_id.display_name),
                "next": {**vendor_bills, "target": "main"}}}
        bill.check_access("read")
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": bill.id,
                "name": bill.display_name, "view_mode": "form", "target": "current"}

    def action_apply(self):
        self.ensure_one()
        self.check_access("write")
        if self.stage == "done" and self.bill_id.company_id == self.target_company_id:
            return self._bill_action()
        # Read the destination's accounts and taxes with both companies in scope, whatever is active.
        wizard = self._in_both_companies()
        bill = wizard._scoped_bill()
        if wizard.stage != "map":
            raise UserError(_("Review the destination accounts first."))
        bill.change_bill_company(wizard.target_company_id.id, wizard._mapping(bill), confirm=True,
                                 preview_id=wizard.snapshot)
        self.write({"stage": "done", "warning": False})
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
    scope_company_ids = fields.Json(related="transfer_id.scope_company_ids")
    analytic_distribution = fields.Json(string="Analytic distribution")
    analytic_precision = fields.Integer(store=False, default=lambda self: self.env["decimal.precision"].precision_get("Percentage Analytic"))

    def _in_both_companies(self):
        companies = self.env["res.company"]
        for item in self:
            companies |= item.transfer_id._both_companies()
        return self.with_context(allowed_company_ids=companies.ids) if companies else self

    def web_read(self, specification):
        return super(BillCompanyTransferLine, self._in_both_companies()).web_read(specification)

    def write(self, vals):
        return super(BillCompanyTransferLine, self._in_both_companies()).write(vals)

    @api.constrains("transfer_id", "invoice_line_id")
    def _check_selected_line(self):
        for mapping in self:
            mapping.transfer_id.check_access("write")
            mapping.invoice_line_id.check_access("read")
            if mapping.invoice_line_id.move_id != mapping.transfer_id.bill_id:
                raise UserError(_("Select a line from the bill being transferred."))

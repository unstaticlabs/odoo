"""Read the posted documents behind potential declarations, independently of filing state."""

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import format_date


class DeclarationCandidateReview(models.TransientModel):
    _name = "rebuild.account.declaration.candidate.review"
    _description = "DES and DAS2 documents to review"
    _explanation = "Use the source dates and linked accounting documents to decide which items need a declaration review."

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
        help="Active company whose accounting documents you want to review.",
    )
    kind = fields.Selection(
        [("des", "DES"), ("das2", "DAS2")], string="Declaration", required=True, default="des",
        help="Review DES by invoice month or DAS2 by payment year and beneficiary.",
    )
    date_from = fields.Date(
        string="From", required=True, default=lambda self: fields.Date.context_today(self).replace(month=1, day=1),
        help="First invoice or payment date to include.",
    )
    date_to = fields.Date(
        string="To", required=True, default=lambda self: fields.Date.context_today(self).replace(month=12, day=31),
        help="Last invoice or payment date to include.",
    )
    source_rows = fields.Json(
        compute="_compute_sources", string="Source documents",
        help="Source move IDs, dates, periods and reasons. DAS2 rows also give the settlement, the 622 share in company currency and the date used by the calendar.",
    )
    line_ids = fields.One2many(
        "rebuild.account.declaration.candidate.review.line", "review_id", string="Documents to review",
        help="One line per source document, listed when you show the documents.",
    )

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        if any(review.date_from > review.date_to for review in self):
            raise ValidationError(self.env._("Choose an end date on or after the start date."))

    @api.depends("company_id", "kind", "date_from", "date_to")
    @api.depends_context("uid", "allowed_company_ids", "lang")
    def _compute_sources(self):
        for review in self:
            complete = review.company_id and review.date_from and review.date_to and review.kind
            if complete and review.company_id not in self.env.companies:
                raise AccessError(self.env._("Select an active company to see its source documents."))
            rows = []
            if complete and review.date_from <= review.date_to and review._is_french():
                scoped = review.with_company(review.company_id)
                rows = scoped._des_rows() if review.kind == "des" else scoped._das2_rows()
            rows.sort(key=lambda row: (row["date"], row["source_id"], row.get("payment_id", 0)), reverse=True)
            review.source_rows = rows

    def _is_french(self):
        return (self.company_id.account_fiscal_country_id or self.company_id.country_id).code == "FR"

    def action_show(self):
        """List the source documents natively: groups, totals, and a click opens the document."""
        self.ensure_one()
        if not self._is_french():
            raise UserError(self.env._("Choose a company with France as its fiscal country to review DES or DAS2 sources."))
        des = self.kind == "des"
        # One year is the usual DAS2 review: a second, folded level would only add a click.
        one_year = self.date_from.year == self.date_to.year
        self.line_ids = [Command.clear()] + [Command.create({
            "date": row["date"], "period": row["period"], "partner_id": row["partner_id"] or False,
            "move_id": row["source_id"], "payment_move_id": row["payment_id"] or False, "reason": row["reason"],
            "amount": row["amount_untaxed"] if des else row["payment_amount"],
            "amount_622": 0.0 if des else row["native_622_amount"],
            "calendar_date": not des and row.get("calendar_622_date") != row["date"] and row.get("calendar_622_date") or False,
        }) for row in self.source_rows]
        view = "view_declaration_candidate_line_des" if des else "view_declaration_candidate_line_das2"
        return {
            "type": "ir.actions.act_window",
            "name": self.env._(
                "%(kind)s documents, %(start)s to %(end)s", kind=self.kind.upper(),
                start=format_date(self.env, self.date_from), end=format_date(self.env, self.date_to),
            ),
            "res_model": "rebuild.account.declaration.candidate.review.line",
            "views": [(self.env.ref(f"rebuild_account_migration.{view}").id, "list")],
            "search_view_id": [self.env.ref("rebuild_account_migration.view_declaration_candidate_line_search").id],
            "domain": [("review_id", "=", self.id)],
            # A DES return has one line per customer VAT number, so the list adds up per customer.
            "context": {"group_by": ["partner_id"] if des or one_year else ["period", "partner_id"]},
            "help": self.env._("No source documents listed. If you opened this list more than an hour ago, show the documents again from the declaration calendar."),
            "target": "current",
        }

    def _source_row(self, move, date, reason, payment=None):
        return {
            "date": fields.Date.to_string(date),
            "period": date.strftime("%Y-%m" if self.kind == "des" else "%Y"),
            "partner": move.commercial_partner_id.display_name or move.partner_id.display_name or "",
            "partner_id": move.commercial_partner_id.id or move.partner_id.id or 0,
            "source": move.display_name, "source_id": move.id, "reason": reason,
            "payment": payment.display_name if payment else "", "payment_id": payment.id if payment else 0,
        }

    def _des_rows(self):
        moves, service_taxes = self.env["rebuild.account.declaration"]._des_period_sources(
            self.company_id, self.date_from, self.date_to,
        )
        rows = []
        for move in moves:
            taxes = move.invoice_line_ids.tax_ids & service_taxes
            if taxes:
                rows.append({
                    **self._source_row(move, move.invoice_date, self.env._(
                        "EU customer with a VAT number. Service tax on the invoice: %s", ", ".join(taxes.mapped("name")),
                    )),
                    # Signed, so a credit note reduces the month it belongs to.
                    "amount_untaxed": move.amount_untaxed_signed, "credit_note": move.move_type == "out_refund",
                })
        return rows

    def _das2_rows(self):
        # Follow the payment date, including split payments and payments on older bills.
        paid_in_period = Domain("date", ">=", self.date_from) & Domain("date", "<=", self.date_to)
        # An advance and its bill can straddle a year. Also surface the source
        # in the period where the native 622 calculator picked up that match.
        match_dates = [("max_date", ">=", self.date_from), ("max_date", "<=", self.date_to)]
        in_period = paid_in_period | Domain("matched_debit_ids", "any", match_dates) | Domain("matched_credit_ids", "any", match_dates)
        lines = self.env["account.move.line"].search(in_period & Domain([
            ("company_id", "=", self.company_id.id), ("parent_state", "=", "posted"),
            "|", "|", ("journal_id.type", "in", ["bank", "cash"]),
            ("move_id.origin_payment_id", "!=", False), ("move_id.statement_line_id", "!=", False),
            "|", "|", ("account_id.code", "=like", "604%"), ("account_id.code", "=like", "622%"),
            ("account_id.account_type", "=", "liability_payable"),
        ]))
        rows = {}
        for line in lines:
            payment = line.move_id
            prefix = (line.account_id.code or "")[:3]
            paid_now = self.date_from <= line.date <= self.date_to
            if prefix in ("604", "622"):
                if not paid_now:
                    continue
                partner = line.partner_id.commercial_partner_id
                if prefix == "622" and (not partner or line.journal_id.type not in ("bank", "cash")):
                    continue
                key = payment.id, 0, partner.id
                if key not in rows:
                    rows[key] = {
                        **self._source_row(payment, line.date, ""), "partner": partner.display_name or "",
                        "partner_id": partner.id or 0, "payment_amount": 0.0, "native_622_amount": 0.0,
                        "account_prefixes": set(),
                    }
                row = rows[key]
                row["account_prefixes"].add(prefix)
                row["payment_amount"] += line.balance
                if prefix == "622":
                    row["native_622_amount"] += line.balance
                    row["calendar_622_date"] = fields.Date.to_string(line.date)
                    row["calendar_622_in_period"] = True
                continue

            for partial in line.matched_debit_ids | line.matched_credit_ids:
                other = partial.credit_move_id if partial.debit_move_id == line else partial.debit_move_id
                bill = other.move_id
                if bill.company_id != self.company_id or bill.state != "posted" or bill.move_type not in ("in_invoice", "in_refund"):
                    continue
                key = bill.id, payment.id, bill.commercial_partner_id.id
                if key not in rows:
                    payable_lines = bill.line_ids.filtered(lambda item: item.account_id.account_type == "liability_payable")
                    ratio = self.env["rebuild.account.declaration"]._das2_622_bill_ratio(bill, payable_lines)
                    prefixes = {"622"} if ratio else set()
                    if any((entry.account_id.code or "").startswith("604") for entry in bill.invoice_line_ids):
                        prefixes.add("604")
                    calendar_now = bool(ratio and self.date_from <= partial.max_date <= self.date_to)
                    if not prefixes or (not paid_now and not calendar_now):
                        continue
                    rows[key] = {
                        **self._source_row(bill, line.date, "", payment), "payment_amount": 0.0, "native_622_amount": 0.0,
                        "account_prefixes": prefixes, "ratio": ratio,
                        "calendar_622_date": fields.Date.to_string(partial.max_date) if ratio else "",
                        "calendar_622_in_period": calendar_now,
                        "period": (line.date if paid_now else partial.max_date).strftime("%Y"),
                    }
                row = rows[key]
                settled = partial.amount if bill.move_type == "in_invoice" else -partial.amount
                row["payment_amount"] += settled
                row["native_622_amount"] += settled * row["ratio"]
        reasons = {
            ("604",): self.env._("Service booked to 604. Confirm its nature with your accountant."),
            ("622",): self.env._("Fees booked to 622, the account used by the declaration calendar."),
            ("604", "622"): self.env._("622 fees and a 604 service on the same document. Review the 604 service separately."),
        }
        for row in rows.values():
            row.pop("ratio", None)
            row["account_prefixes"] = sorted(row["account_prefixes"])
            row["reason"] = reasons[tuple(row["account_prefixes"])]
        return list(rows.values())


class DeclarationCandidateReviewLine(models.TransientModel):
    _name = "rebuild.account.declaration.candidate.review.line"
    _description = "DES or DAS2 document to review"
    _explanation = "One source document behind a potential DES or DAS2 declaration, with the reason it needs a review."
    _order = "date desc, move_id desc, payment_move_id desc"

    review_id = fields.Many2one(
        "rebuild.account.declaration.candidate.review", string="Source review", required=True, ondelete="cascade", index=True,
        help="Review that listed this document.",
    )
    company_id = fields.Many2one(related="review_id.company_id", help="Company whose accounting holds the document.")
    currency_id = fields.Many2one(related="company_id.currency_id", help="Company currency of the amounts.")
    date = fields.Date(help="Invoice date for DES, payment date for DAS2.")
    period = fields.Char(help="Declaration period the document belongs to: a month for DES, a year for DAS2.")
    partner_id = fields.Many2one("res.partner", string="Partner", help="Customer of the invoice or beneficiary of the payment.")
    partner_vat = fields.Char(
        string="VAT number", related="move_id.commercial_partner_id.vat",
        help="VAT number of the customer, the key of its line in the DES return.",
    )
    move_id = fields.Many2one("account.move", string="Document", help="Posted invoice, bill or payment entry to review.")
    move_type = fields.Selection(related="move_id.move_type", string="Type", help="Tells an invoice from a credit note.")
    payment_move_id = fields.Many2one("account.move", string="Payment", help="Payment entry that settled the bill, when the document is a bill.")
    amount = fields.Monetary(help="Signed amount excluding tax for DES, amount settled for DAS2, in company currency.")
    amount_622 = fields.Monetary(string="622 share", help="Part of the settlement the declaration calendar allocates to 622 fees.")
    calendar_date = fields.Date(
        string="Calendar date (622)", help="Date used by the declaration calendar for the 622 share, when it differs from the payment date.",
    )
    reason = fields.Char(string="Reason for review", help="Why this document may belong in the declaration.")

    def _open(self, move):
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": move.id, "views": [(False, "form")], "target": "current"}

    def action_open_document(self):
        self.ensure_one()
        return self._open(self.move_id)

    def action_open_payment(self):
        self.ensure_one()
        return self._open(self.payment_move_id)

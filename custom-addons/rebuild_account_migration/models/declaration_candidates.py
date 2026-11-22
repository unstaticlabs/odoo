"""Read the posted documents behind potential declarations, independently of filing state."""

import re

from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import format_amount, format_date

from .declaration import EU_COUNTRY_CODES


DES_LABEL = re.compile(r"\bdes\s+(?:requis(?:e)?|required)\b", re.I)


class DeclarationCandidateReview(models.TransientModel):
    _name = "rebuild.account.declaration.candidate.review"
    _description = "DES and DAS2 source review"
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
        help="Source move IDs, dates, periods and reasons. DAS2 rows also identify the payment and its matched amount in company currency.",
    )
    evidence_html = fields.Html(
        compute="_compute_sources", string="Items to review",
        help="Source documents grouped by declaration period for the accountant to review.",
    )

    @api.depends("company_id", "kind", "date_from", "date_to")
    @api.depends_context("uid", "allowed_company_ids", "lang")
    def _compute_sources(self):
        for review in self:
            complete = review.company_id and review.date_from and review.date_to and review.kind
            if complete and review.company_id not in self.env.companies:
                raise AccessError(self.env._("Select an active company to see its source documents."))
            valid = complete and review.date_from <= review.date_to
            country = review.company_id.account_fiscal_country_id or review.company_id.country_id
            french = country.code == "FR"
            rows = []
            if valid and french:
                scoped = review.with_company(review.company_id)
                rows = scoped._des_rows() if review.kind == "des" else scoped._das2_rows()
            rows.sort(key=lambda row: (row["date"], row["source_id"], row.get("payment_id", 0)), reverse=True)
            groups = {}
            for row in rows:
                if review.kind == "des":
                    group = row["period"], 0
                    label = format_date(self.env, row["date"], date_format="MMMM y")
                else:
                    group = row["period"], row["partner_id"]
                    label = f"{row['period']} · {row['partner']}" if row["partner"] else row["period"]
                    row["amount_label"] = format_amount(self.env, row["payment_amount"], review.company_id.currency_id)
                groups.setdefault(group, {"label": label, "rows": []})["rows"].append(row)
            review.source_rows = rows
            review.evidence_html = self.env["ir.qweb"]._render(
                "rebuild_account_migration.declaration_candidate_evidence",
                {"groups": list(groups.values()), "kind": review.kind, "complete": complete, "valid": valid, "french": french},
            )

    def _source_row(self, move, date, reason, payment=None):
        return {
            "date": fields.Date.to_string(date), "date_label": format_date(self.env, date),
            "period": date.strftime("%Y-%m" if self.kind == "des" else "%Y"),
            "partner": move.commercial_partner_id.display_name or move.partner_id.display_name or "",
            "partner_id": move.commercial_partner_id.id or move.partner_id.id or 0,
            "source": move.display_name, "source_id": move.id, "reason": reason,
            "payment": payment.display_name if payment else "", "payment_id": payment.id if payment else 0,
        }

    def _des_rows(self):
        moves = self.env["account.move"].with_context(active_test=False).search([
            ("company_id", "=", self.company_id.id), ("state", "=", "posted"),
            ("move_type", "in", ["out_invoice", "out_refund"]),
            ("invoice_date", ">=", self.date_from), ("invoice_date", "<=", self.date_to),
            ("commercial_partner_id.country_id.code", "in", sorted(EU_COUNTRY_CODES - {"FR"})),
            ("commercial_partner_id.vat", "!=", False),
        ])
        native = self.env["account.chart.template"].ref("tva_sale_service_intra_0", raise_if_not_found=False)
        identified = set()
        for tax in moves.invoice_line_ids.tax_ids:
            if tax == native or any(
                DES_LABEL.search(" ".join(filter(None, (translated.name, translated.description))))
                for translated in (tax, tax.with_context(lang="fr_FR"), tax.with_context(lang="en_US"))
            ):
                identified.add(tax.id)
        rows = []
        for move in moves:
            taxes = move.invoice_line_ids.tax_ids.filtered(lambda tax: tax.id in identified)
            if taxes:
                rows.append(self._source_row(move, move.invoice_date, self.env._(
                    "EU customer with a VAT number. Service tax on the invoice: %s", ", ".join(taxes.mapped("name")),
                )))
        return rows

    def _das2_rows(self):
        # Follow the payment date, including split payments and payments on older bills.
        domain = [("company_id", "=", self.company_id.id), ("parent_state", "=", "posted"),
                  ("date", ">=", self.date_from), ("date", "<=", self.date_to)]
        lines = self.env["account.move.line"].search(domain + [
            "|", "|", ("journal_id.type", "in", ["bank", "cash"]),
            ("move_id.origin_payment_id", "!=", False), ("move_id.statement_line_id", "!=", False),
            "|", ("account_id.code", "=like", "604%"), ("account_id.account_type", "=", "liability_payable"),
        ])
        rows = {}
        for line in lines:
            payment = line.move_id
            if (line.account_id.code or "").startswith("604"):
                partner = line.partner_id.commercial_partner_id
                key = payment.id, 0, partner.id
                if key not in rows:
                    rows[key] = {
                        **self._source_row(payment, line.date, self.env._("Payment posted directly to a 604 account.")),
                        "partner": partner.display_name or "", "partner_id": partner.id or 0, "payment_amount": 0.0,
                    }
                rows[key]["payment_amount"] += line.balance
                continue
            for partial in line.matched_debit_ids | line.matched_credit_ids:
                other = partial.credit_move_id if partial.debit_move_id == line else partial.debit_move_id
                bill = other.move_id
                if (bill.company_id == self.company_id and bill.state == "posted" and
                    bill.move_type in ("in_invoice", "in_refund") and
                    any((entry.account_id.code or "").startswith("604") for entry in bill.invoice_line_ids)):
                    key = bill.id, payment.id, bill.commercial_partner_id.id
                    if key not in rows:
                        rows[key] = self._source_row(
                            bill, line.date, self.env._("Payment matched to a supplier document containing a 604 line."), payment,
                        )
                        rows[key]["payment_amount"] = 0.0
                    rows[key]["payment_amount"] += partial.amount if bill.move_type == "in_invoice" else -partial.amount
        return list(rows.values())

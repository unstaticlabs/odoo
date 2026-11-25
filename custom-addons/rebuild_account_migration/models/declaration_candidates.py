"""Read the posted documents behind potential declarations, independently of filing state."""

import re

from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.fields import Domain
from odoo.tools import format_amount, format_date

from .declaration import EU_COUNTRY_CODES


DES_LABEL = re.compile(r"\bdes\s+(?:requis(?:e)?|required)\b", re.I)


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
                    label = label[:1].upper() + label[1:]
                    row["amount_label"] = format_amount(self.env, row["amount_untaxed"], review.company_id.currency_id)
                else:
                    group = row["period"], row["partner_id"]
                    label = f"{row['period']} · {row['partner']}" if row["partner"] else row["period"]
                    row["amount_label"] = format_amount(self.env, row["payment_amount"], review.company_id.currency_id)
                    calendar_date = row.get("calendar_622_date")
                    if calendar_date and calendar_date != row["date"]:
                        row["calendar_date_label"] = format_date(self.env, calendar_date)
                    if row["native_622_amount"]:
                        row["native_622_label"] = format_amount(self.env, row["native_622_amount"], review.company_id.currency_id)
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
        # Whatever opens a DES in the calendar must also appear here, or the
        # declaration would point at an empty review.
        identified = set(self.env["rebuild.account.declaration"]._des_signal_taxes(moves.invoice_line_ids.tax_ids).ids)
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

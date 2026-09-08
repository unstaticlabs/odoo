import math
from collections.abc import Mapping, Sequence

from odoo import Command, _, api, models
from odoo.exceptions import AccessError, UserError, ValidationError

HEADER_FIELDS = {
    "partner_id",
    "invoice_date",
    "date",
    "invoice_date_due",
    "ref",
    "fiscal_position_id",
    "currency_id",
    "narration",
    "payment_reference",
    "review_state",
}
LINE_FIELDS = {
    "name",
    "account_id",
    "quantity",
    "price_unit",
    "discount",
    "tax_ids",
    "analytic_distribution",
}
CREATE_LINE_FIELDS = LINE_FIELDS | {"product_id"}
MAX_LINE_PATCHES = 100
MAX_LINE_CREATES = 100
MAX_TAXES_PER_LINE = 50
MAX_ANALYTIC_ENTRIES = 100
MAX_RESULT_INVOICE_LINES = 500
MAX_RESULT_TAX_LINES = 500
MAX_RESULT_PAYABLE_LINES = 100


def _positive_id(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(_("%(label)s must be a positive record ID.", label=label))
    return value


def _finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(_("%(label)s must be a finite number.", label=label))
    return value


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.private
    def _validate_draft_vendor_bill_header(self, values):
        if values is None:
            return {}
        if not isinstance(values, Mapping):
            raise ValidationError(_("Vendor bill header values must be an object."))
        unknown = set(values) - HEADER_FIELDS
        if unknown:
            raise ValidationError(
                _("Unsupported vendor bill header fields: %(fields)s", fields=", ".join(sorted(unknown))),
            )
        result = dict(values)
        for field_name in ("partner_id", "fiscal_position_id", "currency_id"):
            if field_name in result:
                _positive_id(result[field_name], field_name)
        for field_name, maximum in (
            ("ref", 500),
            ("narration", 50_000),
            ("payment_reference", 500),
        ):
            if field_name in result and (
                not isinstance(result[field_name], str) or len(result[field_name]) > maximum
            ):
                raise ValidationError(
                    _("%(field)s must be text no longer than %(maximum)s characters.", field=field_name, maximum=maximum),
                )
        if "review_state" in result and result["review_state"] not in dict(
            self._fields["review_state"].selection,
        ):
            raise ValidationError(_("Unsupported vendor bill review state."))
        return result

    @api.private
    def _validate_draft_vendor_bill_line_patches(self, patches):
        if patches is None:
            return []
        if isinstance(patches, (str, bytes, Mapping)) or not isinstance(patches, Sequence):
            raise ValidationError(_("Vendor bill line patches must be a list."))
        if len(patches) > MAX_LINE_PATCHES:
            raise ValidationError(
                _("At most %(maximum)s vendor bill lines may be patched at once.", maximum=MAX_LINE_PATCHES),
            )

        normalized = []
        seen_line_ids = set()
        for index, patch in enumerate(patches):
            if not isinstance(patch, Mapping):
                raise ValidationError(_("Vendor bill line patch %(index)s must be an object.", index=index + 1))
            unknown = set(patch) - ({"line_id"} | LINE_FIELDS)
            if unknown:
                raise ValidationError(
                    _("Unsupported vendor bill line fields: %(fields)s", fields=", ".join(sorted(unknown))),
                )
            line_id = _positive_id(patch.get("line_id"), "line_id")
            if line_id in seen_line_ids:
                raise ValidationError(_("Each vendor bill line may be patched only once."))
            seen_line_ids.add(line_id)
            values = {key: value for key, value in patch.items() if key != "line_id"}
            if not values:
                raise ValidationError(_("Each vendor bill line patch must change at least one field."))
            normalized.append((line_id, self._validate_draft_vendor_bill_line_values(values)))
        return normalized

    @api.private
    def _validate_draft_vendor_bill_line_values(self, values):
        """Validate the curated product-line fields shared by patches and new lines."""
        if "name" in values and (
            not isinstance(values["name"], str) or not values["name"].strip() or len(values["name"]) > 2_000
        ):
            raise ValidationError(_("Vendor bill line labels must contain 1 to 2,000 characters."))
        for field_name in ("account_id", "product_id"):
            if field_name in values:
                _positive_id(values[field_name], field_name)
        for field_name in ("quantity", "price_unit", "discount"):
            if field_name in values:
                _finite_number(values[field_name], field_name)
        if "discount" in values and not 0 <= values["discount"] <= 100:
            raise ValidationError(_("Vendor bill line discounts must be between 0 and 100."))
        if "tax_ids" in values:
            tax_ids = values["tax_ids"]
            if isinstance(tax_ids, (str, bytes, Mapping)) or not isinstance(tax_ids, Sequence):
                raise ValidationError(_("tax_ids must be a list of record IDs."))
            if len(tax_ids) > MAX_TAXES_PER_LINE:
                raise ValidationError(
                    _("At most %(maximum)s taxes may be selected per line.", maximum=MAX_TAXES_PER_LINE),
                )
            values["tax_ids"] = list(dict.fromkeys(
                _positive_id(tax_id, "tax_ids") for tax_id in tax_ids
            ))
        if "analytic_distribution" in values:
            distribution = values["analytic_distribution"]
            if not isinstance(distribution, Mapping) or len(distribution) > MAX_ANALYTIC_ENTRIES:
                raise ValidationError(_("analytic_distribution must be a bounded object."))
            for analytic_ids, percentage in distribution.items():
                if not isinstance(analytic_ids, str) or not analytic_ids or len(analytic_ids) > 200:
                    raise ValidationError(_("Analytic distribution keys must be non-empty record ID strings."))
                _finite_number(percentage, "analytic percentage")
            values["analytic_distribution"] = dict(distribution)
        return values

    @api.private
    def _validate_draft_vendor_bill_line_creates(self, creates):
        if creates is None:
            return []
        if isinstance(creates, (str, bytes, Mapping)) or not isinstance(creates, Sequence):
            raise ValidationError(_("Vendor bill lines to create must be a list."))
        if len(creates) > MAX_LINE_CREATES:
            raise ValidationError(
                _("At most %(maximum)s vendor bill lines may be created at once.", maximum=MAX_LINE_CREATES),
            )

        normalized = []
        for index, line in enumerate(creates):
            if not isinstance(line, Mapping):
                raise ValidationError(_("Vendor bill line %(index)s must be an object.", index=index + 1))
            unknown = set(line) - CREATE_LINE_FIELDS
            if unknown:
                raise ValidationError(
                    _("Unsupported vendor bill line fields: %(fields)s", fields=", ".join(sorted(unknown))),
                )
            values = dict(line)
            # A new line carries what the source document states. Odoo derives
            # the rest from the product, the partner and the fiscal position.
            if not isinstance(values.get("name"), str) or not values["name"].strip():
                raise ValidationError(_("Every vendor bill line to create needs a label."))
            if "price_unit" not in values:
                raise ValidationError(_("Every vendor bill line to create needs a unit price."))
            normalized.append(self._validate_draft_vendor_bill_line_values(values))
        return normalized

    @api.private
    def _draft_vendor_bill_configuration_result(self):
        self.ensure_one()
        invoice_lines = self.invoice_line_ids.filtered(lambda line: line.display_type == "product").sorted("id")
        tax_lines = self.line_ids.filtered(
            lambda line: line.display_type in {"tax", "non_deductible_tax"},
        ).sorted("id")
        payable_lines = self.line_ids.filtered(lambda line: line.display_type == "payment_term").sorted("id")
        if (
            len(invoice_lines) > MAX_RESULT_INVOICE_LINES
            or len(tax_lines) > MAX_RESULT_TAX_LINES
            or len(payable_lines) > MAX_RESULT_PAYABLE_LINES
        ):
            raise ValidationError(
                _("The recomputed vendor bill is too large to return through this action."),
            )

        def relation(record):
            return {"id": record.id, "name": record.display_name} if record else None

        return {
            "bill": {
                "id": self.id,
                "display_name": self.display_name,
                "move_type": self.move_type,
                "state": self.state,
                "company": relation(self.company_id),
                "partner": relation(self.partner_id),
                "currency": relation(self.currency_id),
                "invoice_date": self.invoice_date.isoformat() if self.invoice_date else None,
                "accounting_date": self.date.isoformat() if self.date else None,
                "invoice_date_due": self.invoice_date_due.isoformat() if self.invoice_date_due else None,
                "reference": self.ref or None,
                "review_state": self.review_state,
                "amount_untaxed": self.amount_untaxed,
                "amount_tax": self.amount_tax,
                "amount_total": self.amount_total,
            },
            "invoice_lines": [
                {
                    "id": line.id,
                    "name": line.name or "",
                    "product": relation(line.product_id),
                    "account": relation(line.account_id),
                    "quantity": line.quantity,
                    "price_unit": line.price_unit,
                    "discount": line.discount,
                    "tax_ids": line.tax_ids.ids,
                    "analytic_distribution": line.analytic_distribution or {},
                    "price_subtotal": line.price_subtotal,
                    "price_total": line.price_total,
                }
                for line in invoice_lines
            ],
            "tax_lines": [
                {
                    "id": line.id,
                    "name": line.name or "",
                    "account": relation(line.account_id),
                    "tax": relation(line.tax_line_id),
                    "balance": line.balance,
                    "amount_currency": line.amount_currency,
                }
                for line in tax_lines
            ],
            "payable_lines": [
                {
                    "id": line.id,
                    "name": line.name or "",
                    "account": relation(line.account_id),
                    "date_maturity": line.date_maturity.isoformat() if line.date_maturity else None,
                    "balance": line.balance,
                    "amount_currency": line.amount_currency,
                }
                for line in payable_lines
            ],
        }

    @api.private
    def _resolve_draft_vendor_bill_line_taxes(self, values):
        """Turn validated tax IDs into a Command, once they are proven usable."""
        taxes = self.env["account.tax"].browse(values["tax_ids"]).exists()
        if len(taxes) != len(values["tax_ids"]):
            raise ValidationError(_("Every selected tax must exist."))
        taxes.check_access("read")
        if any(tax.company_id and tax.company_id != self.company_id for tax in taxes):
            raise ValidationError(_("Every selected tax must belong to the vendor bill company."))
        values["tax_ids"] = [Command.set(taxes.ids)]

    @api.private
    def _resolve_draft_vendor_bill_line_records(self, values):
        """Prove the account and product of a new line before it is written."""
        if "account_id" in values:
            account = self.env["account.account"].browse(values["account_id"]).exists()
            if not account:
                raise ValidationError(_("The selected account must exist."))
            account.check_access("read")
            if self.company_id not in account.company_ids:
                raise ValidationError(_("The selected account must belong to the vendor bill company."))
        if "product_id" in values:
            product = self.env["product.product"].browse(values["product_id"]).exists()
            if not product:
                raise ValidationError(_("The selected product must exist."))
            product.check_access("read")
            if product.company_id and product.company_id != self.company_id:
                raise ValidationError(_("The selected product must belong to the vendor bill company."))

    def configure_draft_vendor_bill(self, header_values=None, line_patches=None, line_creates=None):
        """Atomically configure curated fields on one draft vendor bill.

        `line_creates` appends product lines from the source document. A bill
        whose import produced no line at all is completed here rather than
        through a generic write, and existing lines are never removed.
        """
        self.ensure_one()
        self.check_access("write")
        if self.company_id not in self.env.companies:
            raise AccessError(_("The vendor bill company is outside the active company scope."))
        if self.move_type not in {"in_invoice", "in_refund"}:
            raise UserError(_("Only vendor bills and vendor credit notes can be configured."))
        if self.state != "draft":
            raise UserError(_("Only draft vendor bills can be configured."))
        self._check_fiscal_lock_dates()

        header_values = self._validate_draft_vendor_bill_header(header_values)
        line_patches = self._validate_draft_vendor_bill_line_patches(line_patches)
        line_creates = self._validate_draft_vendor_bill_line_creates(line_creates)
        if not header_values and not line_patches and not line_creates:
            raise ValidationError(_("Supply at least one vendor bill field, line patch, or new line."))

        lines = self.env["account.move.line"].browse([line_id for line_id, _values in line_patches]).exists()
        if len(lines) != len(line_patches):
            raise ValidationError(_("Every patched vendor bill line must exist."))
        lines.check_access("write")
        by_id = {line.id: line for line in lines}
        for line_id, values in line_patches:
            line = by_id[line_id]
            if line.move_id != self or line.display_type != "product":
                raise ValidationError(_("Only existing product lines belonging to this vendor bill may be patched."))
            if "tax_ids" in values:
                self._resolve_draft_vendor_bill_line_taxes(values)

        for values in line_creates:
            self._resolve_draft_vendor_bill_line_records(values)
            if "tax_ids" in values:
                self._resolve_draft_vendor_bill_line_taxes(values)

        if header_values:
            self.write(header_values)
            self._check_fiscal_lock_dates()
        for line_id, values in line_patches:
            by_id[line_id].write(values)
        if line_creates:
            # Writing through the bill lets Odoo apply the product, the partner
            # and the fiscal position to each new line and recompute the taxes
            # and the payable line in the same transaction.
            self.write({"invoice_line_ids": [Command.create(values) for values in line_creates]})
        self.flush_recordset()
        return self._draft_vendor_bill_configuration_result()

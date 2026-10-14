import math
from collections.abc import Mapping

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


DRAFT_FIELDS = {
    "name", "description", "date", "company_id", "employee_id", "product_id",
    "account_id", "tax_ids", "analytic_distribution", "quantity", "price_unit",
    "total_amount", "payment_mode", "expense_batch_id", "vendor_id",
    "existing_bill_id", "has_existing_bill",
}
RELATION_FIELDS = {
    "company_id", "employee_id", "product_id", "account_id", "expense_batch_id",
    "vendor_id", "existing_bill_id",
}
RESULT_FIELDS = [
    "display_name", "state", "company_id", "employee_id", "product_id", "account_id",
    "tax_ids", "analytic_distribution", "expense_batch_id", "vendor_id",
    "existing_bill_id", "has_existing_bill", "date", "quantity", "price_unit",
    "payment_mode", "currency_id", "total_amount_currency", "total_amount",
    "message_main_attachment_id",
]


class HrExpense(models.Model):
    _inherit = "hr.expense"

    @api.private
    def _draft_configuration_values(self, values):
        if not isinstance(values, Mapping) or not values or set(values) - DRAFT_FIELDS:
            raise ValidationError(_("Supply supported draft expense fields."))
        values = dict(values)
        for field in RELATION_FIELDS & values.keys():
            value = values[field]
            if value is False and field not in {"company_id", "employee_id"}:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValidationError(_("%(field)s must be a positive record ID.", field=field))
        for field in {"quantity", "price_unit", "total_amount"} & values.keys():
            value = values[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValidationError(_("%(field)s must be a finite number.", field=field))
        if "quantity" in values and values["quantity"] <= 0:
            raise ValidationError(_("Quantity must be positive."))
        for field, maximum in (("name", 500), ("description", 20_000)):
            if field in values and (not isinstance(values[field], str) or len(values[field]) > maximum):
                raise ValidationError(_("%(field)s must be bounded text.", field=field))
        if "name" in values and not values["name"].strip():
            raise ValidationError(_("The expense needs a name."))
        if "payment_mode" in values and values["payment_mode"] not in {"own_account", "company_account"}:
            raise ValidationError(_("Choose employee or company payment."))
        if "has_existing_bill" in values and not isinstance(values["has_existing_bill"], bool):
            raise ValidationError(_("has_existing_bill must be a boolean."))
        if values.get("has_existing_bill") is False:
            if values.get("existing_bill_id"):
                raise ValidationError(_("Clear existing_bill_id when has_existing_bill is false."))
            # Match the native flag onchange without retaining a hidden payable reference.
            values["existing_bill_id"] = False
        if "date" in values:
            try:
                if not isinstance(values["date"], str) or len(values["date"]) != 10:
                    raise ValueError
                values["date"] = fields.Date.to_date(values["date"])
                if not values["date"]:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValidationError(_("Use a valid expense date in YYYY-MM-DD format.")) from None
        if "tax_ids" in values:
            ids = values["tax_ids"]
            if not isinstance(ids, list) or len(ids) > 50 or any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in ids
            ):
                raise ValidationError(_("Choose at most 50 tax record IDs."))
            values["tax_ids"] = list(dict.fromkeys(ids))
        if "analytic_distribution" in values:
            distribution = values["analytic_distribution"]
            if not isinstance(distribution, Mapping) or len(distribution) > 100:
                raise ValidationError(_("Analytic distribution must be a bounded object."))
            for key, percentage in distribution.items():
                if not isinstance(key, str) or len(key) > 200 or any(
                    not component.isdecimal() or int(component) <= 0 for component in key.split(",")
                ):
                    raise ValidationError(_("Analytic keys must contain positive account IDs."))
                if isinstance(percentage, bool) or not isinstance(percentage, (int, float)) or not math.isfinite(percentage) or percentage < 0:
                    raise ValidationError(_("Analytic percentages must be finite nonnegative numbers."))
            values["analytic_distribution"] = dict(distribution)
        return values

    @api.private
    def _draft_configuration_record(self, field, value, company):
        records = self.env[self._fields[field].comodel_name].browse(value).exists() if value else self.env[self._fields[field].comodel_name]
        if value and not records:
            raise ValidationError(_("The selected %(field)s must exist.", field=field))
        if records:
            records.check_access("read")
        if records and not records.filtered_domain(records._check_company_domain(company)):
            raise ValidationError(_("Replace or clear %(field)s for the target company.", field=field))
        return records

    def configure_draft_expense(self, values):
        """Prepare one authorized draft and return its native state in this transaction."""
        self.ensure_one()
        self.check_access("write")
        values = self._draft_configuration_values(values)
        with self.env.cr.savepoint():
            self.flush_recordset()
            self.env.cr.execute("SELECT id FROM hr_expense WHERE id = %s FOR UPDATE", (self.id,))
            self.invalidate_recordset()
            self.check_access("write")
            if self.state != "draft" or self.account_move_id:
                raise UserError(_("Only draft expenses without a journal entry can be corrected."))
            company = self.env["res.company"].browse(values.get("company_id", self.company_id.id)).exists()
            if not company or company not in self.env.companies or company not in self.env.user.company_ids:
                raise AccessError(_("Select an authorized target company in your company context."))
            employee = self.env["hr.employee"].browse(values.get("employee_id", self.employee_id.id)).exists()
            # Native employee reads fall back to the public profile for ordinary employees.
            self.env["hr.employee.public"].browse(employee.ids).check_access("read")
            if not employee or employee.company_id != company:
                raise ValidationError(_("Choose an employee belonging to the target company."))
            company_changed = company != self.company_id
            identity_changed = company_changed or employee != self.employee_id
            if identity_changed and (self.split_expense_origin_id or self.split_expense_count):
                raise UserError(_("This expense shares an origin and receipt with other split expenses. Open Split Expenses to inspect the family. Its company and employee must be corrected together in one transaction so categories, batches, evidence and history remain consistent. This action currently prepares one expense; its other draft fields remain available."))
            target = self.with_company(company)
            batch_id = values.get("expense_batch_id", self.expense_batch_id.id)
            batch = target._draft_configuration_record("expense_batch_id", batch_id, company)
            if batch and (batch.company_id != company or batch.employee_id != employee):
                raise ValidationError(_("Replace or clear the batch to match the target company and employee."))
            if "expense_batch_id" in values and self.expense_batch_id and self.expense_batch_id != batch:
                self.write({"expense_batch_id": False})
            # Native removal restores the old baseline. Validate that baseline before changing ownership.
            for field in ("product_id", "account_id", "vendor_id", "existing_bill_id"):
                if company_changed or field in values:
                    target._draft_configuration_record(field, values.get(field, self[field].id), company)
            if company_changed or {"existing_bill_id", "vendor_id"} & values.keys():
                bill = target._draft_configuration_record("existing_bill_id", values.get("existing_bill_id", self.existing_bill_id.id), company)
                if bill and (bill.move_type != "in_invoice" or bill.state not in {"draft", "posted"}):
                    raise ValidationError(_("Choose a draft or posted supplier bill."))
                vendor_id = values.get("vendor_id", self.vendor_id.id)
                if bill and vendor_id and bill.partner_id.id != vendor_id:
                    raise ValidationError(_("Choose the supplier belonging to the selected bill, or clear the supplier."))
            if company_changed or "product_id" in values:
                product = target._draft_configuration_record("product_id", values.get("product_id", self.product_id.id), company)
                if product and not product.can_be_expensed:
                    raise ValidationError(_("Choose an expense category."))
            if company_changed or "tax_ids" in values:
                taxes = target.env["account.tax"].browse(values.get("tax_ids", self.tax_ids.ids)).exists()
                if "tax_ids" in values and len(taxes) != len(values["tax_ids"]):
                    raise ValidationError(_("Every selected tax must exist."))
                if taxes:
                    taxes.check_access("read")
                if len(taxes.filtered_domain(taxes._check_company_domain(company))) != len(taxes):
                    raise ValidationError(_("Replace or clear taxes for the target company."))
            if company_changed or "analytic_distribution" in values:
                distribution = values.get("analytic_distribution", self.analytic_distribution or {})
                analytic_ids = {int(part) for key in distribution for part in key.split(",")}
                analytics = target.env["account.analytic.account"].browse(analytic_ids).exists()
                if analytics:
                    analytics.check_access("read")
                if len(analytics) != len(analytic_ids) or len(analytics.filtered_domain(analytics._check_company_domain(company))) != len(analytics):
                    raise ValidationError(_("Replace or clear analytics for the target company."))
            attachments = self.attachment_ids
            if identity_changed:
                # Scope the maintained collaboration delegate to this expense's own evidence.
                collaboration = self._usl_agent_collaboration("write") if hasattr(self, "_usl_agent_collaboration") else self
                attachments = attachments.with_context(collaboration.env.context)
                if attachments:
                    attachments.check_access("write")
                if "usl.mail.pdf.retrieval" in self.env:
                    collaboration.env["usl.mail.pdf.retrieval"]._supersede_for_expense(self)
            patch = dict(values)
            previous_company = self.company_id
            if "tax_ids" in patch:
                patch["tax_ids"] = [Command.set(patch["tax_ids"])]
            explicit_amounts = {}
            if company != self.company_id:
                # Use the target company's dated native rate, retaining the document's currency and amount.
                patch.update({"currency_id": self.currency_id.id, "total_amount_currency": self.total_amount_currency})
                explicit_amounts = {field: patch.pop(field) for field in ("quantity", "price_unit", "total_amount") if field in patch}
            if "company_id" in patch:
                patch["employee_id"] = employee.id
            target.write(patch)
            if explicit_amounts:
                target.write(explicit_amounts)
            if identity_changed:
                changed_attachments = attachments.filtered(lambda attachment: attachment.company_id != company)
                if changed_attachments:
                    changed_attachments.write({"company_id": company.id})
            target.check_access("write")
            target.check_access("read")
            if previous_company != company:
                target.message_post(body=_("Draft expense company corrected from %(source)s to %(target)s.", source=previous_company.display_name, target=company.display_name), subtype_xmlid="mail.mt_note")
            return {"result": True, "expense": target.read(RESULT_FIELDS)[0]}

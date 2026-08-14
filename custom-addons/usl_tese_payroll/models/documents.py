from odoo import fields, models


class UslTesePayslip(models.Model):
    _inherit = "usl.tese.payslip"

    def _document_related_records(self, attachment=None):
        self.ensure_one()
        records = super()._document_related_records(attachment)
        if self.move_id:
            records.append({"model": "account.move", "id": self.move_id.id})
        return records

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values.update(
            {
                "confidentiality": "hr",
                "accounting_evidence": True,
                "tags": ["HR", "Payroll"],
                "document_type": "Payroll record",
                "correspondent_partner_id": self.employee_partner_id.id or False,
                "document_date": fields.Date.to_string(
                    self.payslip_date or self.period_end,
                ),
            },
        )
        return values

    def _document_access_trigger_fields(self):
        return super()._document_access_trigger_fields() | {
            "employee_id",
            "state",
        }


class UslDocumentLink(models.Model):
    _inherit = "usl.document.link"

    def _allowed_models(self):
        return super()._allowed_models() | {"usl.tese.payslip"}

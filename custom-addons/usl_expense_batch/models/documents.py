from odoo import fields, models


class UslExpenseBatch(models.Model):
    _name = "usl.expense.batch"
    _inherit = ["usl.expense.batch", "usl.document.link.mixin"]

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never":
            return policy
        return {
            **policy,
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "expense_batch_evidence",
            "confidentiality": "accounting",
            "accounting_evidence": True,
        }

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values.update(
            {
                "confidentiality": "accounting",
                "accounting_evidence": True,
                "tags": ["Accounting", "Expenses"],
                "document_type": "Expense batch evidence",
                "document_date": fields.Date.to_string(
                    self.date_to or self.date_from,
                ),
            },
        )
        return values

    def _document_access_trigger_fields(self):
        return super()._document_access_trigger_fields() | {
            "employee_id",
        }


class UslDocumentLink(models.Model):
    _inherit = "usl.document.link"

    def _allowed_models(self):
        return super()._allowed_models() | {"usl.expense.batch"}

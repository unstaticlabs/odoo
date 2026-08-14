from odoo import fields, models


class RebuildAccountDeclaration(models.Model):
    _name = "rebuild.account.declaration"
    _inherit = ["rebuild.account.declaration", "usl.document.link.mixin"]

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values.update(
            {
                "confidentiality": "accounting",
                "accounting_evidence": True,
                "tags": ["Accounting", "Tax & reporting"],
                "document_type": "Tax declaration evidence",
                "document_date": fields.Date.to_string(self.period_end),
            },
        )
        return values


class RebuildAccountClosingPeriod(models.Model):
    _name = "rebuild.account.closing.period"
    _inherit = ["rebuild.account.closing.period", "usl.document.link.mixin"]

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values.update(
            {
                "confidentiality": "accounting",
                "accounting_evidence": True,
                "tags": ["Accounting", "Closing"],
                "document_type": "Closing evidence",
                "document_date": fields.Date.to_string(self.date_to),
            },
        )
        return values


class AccountAsset(models.Model):
    _name = "account.asset"
    _inherit = ["account.asset", "usl.document.link.mixin"]

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values.update(
            {
                "confidentiality": "accounting",
                "accounting_evidence": True,
                "tags": ["Accounting", "Assets"],
                "document_type": "Asset evidence",
                "document_date": fields.Date.to_string(self.date_start),
            },
        )
        return values


class UslDocumentLink(models.Model):
    _inherit = "usl.document.link"

    def _allowed_models(self):
        return super()._allowed_models() | {
            "account.asset",
            "rebuild.account.declaration",
            "rebuild.account.closing.period",
        }

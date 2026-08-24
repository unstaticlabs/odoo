from odoo import fields, models

from odoo.addons.usl_documents.models.attachment_bridge import ORIGIN_CAPTURE_TOKEN


class RebuildAccountReportExportWizard(models.TransientModel):
    _inherit = "rebuild.account.report.export.wizard"

    def _attach_generated_closing_package(self, payload, filename):
        contextual = self.with_context(
            usl_documents_origin_token=ORIGIN_CAPTURE_TOKEN,
            usl_documents_attachment_origin="generated_final",
        )
        return super(
            RebuildAccountReportExportWizard,
            contextual,
        )._attach_generated_closing_package(payload, filename)


class RebuildAccountDeclaration(models.Model):
    _name = "rebuild.account.declaration"
    _inherit = ["rebuild.account.declaration", "usl.document.link.mixin"]

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never":
            return policy
        return {
            **policy,
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "tax_declaration_evidence",
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
                "tags": ["Accounting", "Tax & reporting"],
                "document_type": "Tax declaration evidence",
                "document_date": fields.Date.to_string(self.period_end),
            },
        )
        return values


class RebuildAccountClosingPeriod(models.Model):
    _name = "rebuild.account.closing.period"
    _inherit = ["rebuild.account.closing.period", "usl.document.link.mixin"]

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never":
            return policy
        return {
            **policy,
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "closing_evidence",
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
                "tags": ["Accounting", "Closing"],
                "document_type": "Closing evidence",
                "document_date": fields.Date.to_string(self.date_to),
            },
        )
        return values


class AccountAsset(models.Model):
    _name = "account.asset"
    _inherit = ["account.asset", "usl.document.link.mixin"]

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never":
            return policy
        return {
            **policy,
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "asset_evidence",
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

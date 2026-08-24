from odoo import fields, models


def _platform_descriptor(platform):
    return {
        "namespace": "platform",
        "model": "usl.platform.billing.platform",
        "id": platform.id,
        "name": platform.name,
        "parent": "Platform billing",
    }


class UslPlatformBillingPlatform(models.Model):
    _name = "usl.platform.billing.platform"
    _inherit = [
        "usl.platform.billing.platform",
        "usl.document.link.mixin",
    ]

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never":
            return policy
        return {
            **policy,
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "platform_configuration_evidence",
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
                "tags": ["Accounting", "Platform billing"],
                "entity_tags": [_platform_descriptor(self)],
                "document_type": "Platform agreement or statement",
                "correspondent_partner_id": self.partner_id.id,
            },
        )
        return values


class UslPlatformBillingSession(models.Model):
    _name = "usl.platform.billing.session"
    _inherit = ["usl.platform.billing.session", "usl.document.link.mixin"]

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never":
            return policy
        return {
            **policy,
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "platform_session_evidence",
            "confidentiality": "accounting",
            "accounting_evidence": True,
        }

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        platforms = self.payout_ids.mapped("platform_id")
        values.update(
            {
                "confidentiality": "accounting",
                "accounting_evidence": True,
                "tags": ["Accounting", "Platform billing"],
                "entity_tags": [
                    _platform_descriptor(platform) for platform in platforms
                ],
                "document_type": "Platform billing statement",
                "document_date": fields.Date.to_string(
                    self.invoice_date or self.period_month,
                ),
            },
        )
        return values


class UslPlatformBillingPayout(models.Model):
    _name = "usl.platform.billing.payout"
    _inherit = ["usl.platform.billing.payout", "usl.document.link.mixin"]

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never":
            return policy
        return {
            **policy,
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "platform_payout_evidence",
            "confidentiality": "accounting",
            "accounting_evidence": True,
        }

    def _document_related_records(self, attachment=None):
        self.ensure_one()
        records = super()._document_related_records(attachment)
        if self.session_id:
            records.append(
                {"model": "usl.platform.billing.session", "id": self.session_id.id},
            )
        return records

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values.update(
            {
                "confidentiality": "accounting",
                "accounting_evidence": True,
                "tags": ["Accounting", "Platform billing"],
                "entity_tags": (
                    [_platform_descriptor(self.platform_id)]
                    if self.platform_id
                    else []
                ),
                "document_type": "Platform payout evidence",
                "correspondent_partner_id": (
                    self.platform_id.partner_id.id if self.platform_id else False
                ),
                "document_date": fields.Date.to_string(self.payout_date),
            },
        )
        return values


class UslDocumentLink(models.Model):
    _inherit = "usl.document.link"

    def _allowed_models(self):
        return super()._allowed_models() | {
            "usl.platform.billing.platform",
            "usl.platform.billing.session",
            "usl.platform.billing.payout",
        }

from odoo import fields, models


class B2cOrder(models.Model):
    _name = "b2c.order"
    _inherit = ["b2c.order", "usl.document.link.mixin"]


class B2cPaymentEvent(models.Model):
    _name = "b2c.payment.event"
    _inherit = ["b2c.payment.event", "usl.document.link.mixin"]


class B2cFulfilmentEvent(models.Model):
    _name = "b2c.fulfilment.event"
    _inherit = ["b2c.fulfilment.event", "usl.document.link.mixin"]


class B2cAccountingSession(models.Model):
    _name = "b2c.accounting.session"
    _inherit = ["b2c.accounting.session", "usl.document.link.mixin"]


class B2cProviderEvidence(models.Model):
    _inherit = "b2c.provider.evidence"

    archived_document_id = fields.Many2one(
        "usl.document",
        string="Archived Source Document",
        check_company=True,
        ondelete="restrict",
        copy=False,
        readonly=True,
        groups="usl_b2c.group_b2c_sensitive_evidence",
        help=(
            "Restricted archived file supporting this immutable provider row. "
            "The relationship does not expose the provider payload."
        ),
    )


class UslDocumentLink(models.Model):
    _inherit = "usl.document.link"

    def _allowed_models(self):
        return super()._allowed_models() | {
            "b2c.order",
            "b2c.payment.event",
            "b2c.fulfilment.event",
            "b2c.accounting.session",
        }

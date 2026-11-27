"""One sale, however many times a channel has been read.

A sale can reach Odoo twice: once as a header the older shop exported, and
later in full from the shop that replaced it.  Both are true records of the
same sale, so neither is deleted; what is needed is for one of them to stop
answering for it.
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.addons.usl_b2c.models.native_history import B2cOrderProvenance


class B2cOrderSupersession(models.Model):
    _inherit = "b2c.order"

    _usl_materialization_fields = (
        B2cOrderProvenance._usl_materialization_fields | {"medusa_native_order_id"}
    )

    medusa_native_order_id = fields.Char(
        string="Native Medusa order ID", readonly=True, copy=False, index=True,
        help="Retained native identity for new Medusa imports. Historical orders are left unchanged.",
    )
    _medusa_native_order_unique = models.Constraint(
        "UNIQUE(company_id, medusa_native_order_id)",
        "This native Medusa order is already imported in this company. Read the batch again to reuse the existing sale.",
    )

    @api.constrains("medusa_native_order_id", "external_order_id", "source_provider")
    def _check_medusa_native_identity(self):
        for order in self:
            if order.medusa_native_order_id and (
                order.source_provider != "medusa" or order.external_order_id != order.medusa_native_order_id
            ):
                raise ValidationError(self.env._("The retained Medusa identity must match the native source order ID."))

    superseded_by_id = fields.Many2one(
        "b2c.order",
        string="Superseded by",
        ondelete="restrict",
        index=True,
        copy=False,
        help="The order that states this same sale in full. A sale recorded "
             "twice is one sale, and the poorer record stops counting.",
    )

    @api.constrains("superseded_by_id", "state")
    def _check_superseded(self):
        for order in self:
            if not order.superseded_by_id:
                continue
            if order.superseded_by_id == order:
                raise ValidationError(
                    self.env._("An order cannot supersede itself."),
                )
            if order.state != "cancelled":
                raise ValidationError(
                    self.env._(
                        "A superseded order is cancelled: it is the same sale as "
                        "the one that supersedes it.",
                    ),
                )

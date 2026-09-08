"""One sale, however many times a channel has been read.

A sale can reach Odoo twice: once as a header the older shop exported, and
later in full from the shop that replaced it.  Both are true records of the
same sale, so neither is deleted; what is needed is for one of them to stop
answering for it.
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class B2cOrderSupersession(models.Model):
    _inherit = "b2c.order"

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

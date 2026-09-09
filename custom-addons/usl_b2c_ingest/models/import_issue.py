"""Findings an operator has to look at before a batch can be applied."""

from odoo import Command, fields, models
from odoo.exceptions import UserError

SEVERITIES = [
    ("blocking", "Blocking"),
    ("advisory", "Advisory"),
]

ISSUE_KINDS = [
    ("unreadable_file", "Unrecognised file"),
    ("identity_conflict", "Order identity belongs to another channel"),
    ("duplicate_order", "Odoo holds the same order twice"),
    ("payload_conflict", "Files disagree about the same order"),
    ("net_identity", "Channel totals no longer add up"),
    ("orphan_line", "Line without an order"),
    ("order_money_missing", "Order-level money is not in the export"),
    ("unknown_product", "The channel sold something Odoo does not know"),
    ("missing_variant", "The product exists but not in this variant"),
    ("ambiguous_product", "The name points at more than one product"),
    ("alias_derived", "A mapping was derived from the catalogue"),
    ("fulfilment_unmatched", "A supplier fulfilment belongs to no known sale"),
    ("destination_unknown", "The export does not say where the goods went"),
    ("delivery_incomplete", "The delivery could not be completed"),
    ("shadowed_destination", "A destination is taxed by the wrong fiscal position"),
    ("product_tax_unclear", "A product does not state one sales tax"),
    ("tax_added_to_price", "A tax would be added to the price already paid"),
    ("carriage_unaccounted", "Carriage has nowhere of its own to go"),
    ("revenue_unaccounted", "Revenue has nowhere of its own to go"),
    ("destination_vat_misplaced", "Destination VAT does not accrue where it belongs"),
    ("operator_untaxed", "A channel operator has no tax number"),
    ("retired_rate", "A fiscal position states a retired rate"),
    ("cancelled_after_delivery", "A cancelled order has already shipped"),
    ("part_refund_unallocated", "Part of an order was refunded, without saying which part"),
    ("wallet_overdrawn", "The supplier drew more than the wallet holds"),
    ("fee_source_missing", "A channel's own account of what it kept is all there is"),
    ("payout_unattributed", "A payout left an account nothing names"),
    ("transfer_amount_missing", "A movement is stated without an amount"),
    ("wallet_disagrees", "The supplier's account of a month is not the one Odoo holds"),
]


class B2cImportIssue(models.Model):
    _name = "b2c.import.issue"
    _description = "B2C Import Finding"
    _order = "severity, kind, external_order_id"

    batch_id = fields.Many2one(
        "b2c.import.batch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    row_id = fields.Many2one("b2c.import.row", ondelete="cascade", index=True)
    severity = fields.Selection(SEVERITIES, required=True, default="blocking", index=True)
    kind = fields.Selection(ISSUE_KINDS, required=True, index=True)
    name = fields.Char(required=True)
    external_order_id = fields.Char(index=True)
    note = fields.Text()
    proposal = fields.Json(
        readonly=True,
        help="What a resolution action needs: the product and the attribute it lacks.",
    )
    proposed_value = fields.Char(
        help="The attribute value to add, spelled the way the catalogue spells its siblings.",
    )

    def action_supersede_duplicate(self):
        """Keep the fuller of two records of one sale and retire the other.

        Which record is fuller is a matter of evidence, not preference: the one
        stating the lines wins, and where both state them, the one stating more
        of the money.  The retired record is cancelled and points at the one
        that replaced it, so nothing is deleted and the ledger, which was never
        posted from either, is untouched.
        """
        self.ensure_one()
        if self.kind != "duplicate_order":
            raise UserError(
                self.env._("This finding does not name two records of one sale."),
            )
        batch = self.batch_id
        duplicates = batch._known_orders({self.external_order_id}).get(
            self.external_order_id,
        )
        if not duplicates or len(duplicates) < 2:
            raise UserError(self.env._("Odoo no longer holds this sale twice."))
        kept = max(
            duplicates,
            key=lambda order: (len(order.line_ids), order.total_amount, order.id),
        )
        retired = duplicates - kept
        retired.sale_order_id.with_context(**batch._context()).sudo()._action_cancel()
        retired.with_context(**batch._context()).sudo().write(
            {"state": "cancelled", "superseded_by_id": kept.id},
        )
        batch.action_resolve()
        return {
            "type": "ir.actions.act_window",
            "res_model": "b2c.order",
            "view_mode": "list,form",
            "domain": [("id", "in", (kept | retired).ids)],
            "name": self.env._("One sale, two records"),
        }

    def action_add_missing_value(self):
        """Add the attribute value a channel sold, so the variant can exist.

        A channel selling a size the catalogue does not list is a catalogue
        that has fallen behind, not a mystery: the template, the attribute and
        the value are all named in the finding.  Nothing else is invented.
        """
        self.ensure_one()
        proposal = self.proposal or {}
        template = self.env["product.template"].browse(proposal.get("template_id") or 0)
        attribute_line = self.env["product.template.attribute.line"].browse(
            proposal.get("attribute_line_id") or 0,
        )
        name = (self.proposed_value or "").strip()
        if not (template.exists() and attribute_line.exists() and name):
            raise UserError(
                self.env._("This finding does not name a value that can be added."),
            )
        value = self.env["product.attribute.value"].search(
            [
                ("attribute_id", "=", attribute_line.attribute_id.id),
                ("name", "=", name),
            ],
            limit=1,
        ) or self.env["product.attribute.value"].create(
            {"attribute_id": attribute_line.attribute_id.id, "name": name},
        )
        attribute_line.value_ids = [Command.link(value.id)]
        batch = self.batch_id
        # Resolving again drops this finding, so nothing may touch it afterwards.
        batch.action_resolve()
        return {
            "type": "ir.actions.act_window",
            "res_model": "b2c.import.batch",
            "res_id": batch.id,
            "view_mode": "form",
        }

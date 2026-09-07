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

"""One normalised row from a dropped file, and how it resolves against Odoo."""

from odoo import fields, models

from odoo.addons.usl_b2c.models.constants import SOURCE_PROVIDERS

GRAINS = [
    ("order", "Order"),
    ("line", "Order line"),
]

MAPPINGS = [
    ("mapped", "A confirmed alias says so"),
    ("derived", "Matched exactly, alias not written yet"),
    ("unmapped", "No product yet"),
    ("not_applicable", "Not a line"),
]

RESOLUTIONS = [
    ("new", "Not in Odoo yet"),
    ("known", "Already in Odoo"),
    ("conflicting", "Conflicts with what Odoo holds"),
    ("orphan_line", "Line without an order"),
]


class B2cImportRow(models.Model):
    _name = "b2c.import.row"
    _description = "B2C Import Source Row"
    _order = "occurred_at desc, external_order_id, external_line_id"

    batch_id = fields.Many2one(
        "b2c.import.batch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    file_id = fields.Many2one("b2c.import.file", required=True, ondelete="cascade", index=True)
    format_id = fields.Char(required=True, readonly=True, index=True)
    provider = fields.Selection(SOURCE_PROVIDERS, required=True, readonly=True, index=True)
    grain = fields.Selection(GRAINS, required=True, readonly=True, index=True)
    external_order_id = fields.Char(required=True, readonly=True, index=True)
    external_line_id = fields.Char(readonly=True, index=True)
    source_row_number = fields.Integer(readonly=True)
    occurred_at = fields.Datetime(readonly=True, index=True)
    payload_digest = fields.Char(required=True, readonly=True, index=True)
    values = fields.Json(readonly=True, string="Normalised values")
    payload = fields.Json(
        readonly=True,
        string="Source payload",
        groups="usl_b2c.group_b2c_sensitive_evidence",
        help="The unmodified source row. It can contain customer personal data.",
    )
    resolution = fields.Selection(RESOLUTIONS, readonly=True, index=True)
    mapping = fields.Selection(MAPPINGS, readonly=True, index=True)
    product_id = fields.Many2one("product.product", readonly=True, ondelete="set null")
    alias_id = fields.Many2one("b2c.product.alias", readonly=True, ondelete="set null")
    order_id = fields.Many2one("b2c.order", readonly=True, ondelete="set null", index=True)
    note = fields.Char(readonly=True)

    _batch_identity_unique = models.Constraint(
        "UNIQUE(batch_id, format_id, external_order_id, external_line_id, grain)",
        "A batch reads each source row once per format.",
    )

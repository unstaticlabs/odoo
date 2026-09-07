"""Findings an operator has to look at before a batch can be applied."""

from odoo import fields, models

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

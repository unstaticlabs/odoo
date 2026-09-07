"""One file dropped into an import batch."""

import hashlib

from odoo import api, fields, models

from odoo.addons.usl_b2c.models.constants import SOURCE_PROVIDERS
from odoo.addons.usl_b2c_ingest import parsers

FILE_STATES = [
    ("pending", "Not read yet"),
    ("recognised", "Recognised"),
    ("unreadable", "Unrecognised"),
]


class B2cImportFile(models.Model):
    _name = "b2c.import.file"
    _description = "B2C Import Source File"
    _order = "provider, name"

    batch_id = fields.Many2one(
        "b2c.import.batch",
        required=True,
        ondelete="cascade",
        index=True,
    )
    name = fields.Char(required=True)
    content = fields.Binary(required=True, attachment=True)
    checksum = fields.Char(readonly=True, index=True, copy=False)
    schema_digest = fields.Char(readonly=True, copy=False)
    format_id = fields.Selection(
        selection="_selection_format",
        readonly=True,
        copy=False,
        string="Recognised as",
    )
    provider = fields.Selection(SOURCE_PROVIDERS, readonly=True, copy=False)
    state = fields.Selection(FILE_STATES, required=True, default="pending", readonly=True)
    row_count = fields.Integer(readonly=True)
    order_count = fields.Integer(readonly=True)
    line_count = fields.Integer(readonly=True)
    note = fields.Text(readonly=True)

    @api.model
    def _selection_format(self):
        return [(fmt.format_id, fmt.label) for fmt in parsers.CSV_FORMATS]

    def _raw_content(self):
        """Return the bytes of the uploaded file."""
        self.ensure_one()
        return self.content.content if self.content else b""

    def _recognise(self):
        """Read each file once, and record what it is or why it is not readable.

        Returns the parsed rows keyed by file, so the batch never has to decode
        the same upload twice.
        """
        parsed = {}
        for record in self:
            content = record._raw_content()
            values = {
                "checksum": hashlib.sha256(content).hexdigest(),
                "row_count": 0,
                "order_count": 0,
                "line_count": 0,
            }
            try:
                fmt = parsers.detect(content)
                rows, document = parsers.parse(fmt, record.name, content)
            except parsers.SchemaError as error:
                record.write(values | {"state": "unreadable", "note": str(error)})
                continue
            parsed[record.id] = (fmt, rows)
            record.write(
                values
                | {
                    "state": "recognised",
                    "note": False,
                    "format_id": fmt.format_id,
                    "schema_digest": fmt.signature,
                    "provider": fmt.provider,
                    "row_count": len(document.rows),
                    "order_count": sum(1 for row in rows if row.grain == parsers.ORDER_GRAIN),
                    "line_count": sum(1 for row in rows if row.grain == parsers.LINE_GRAIN),
                },
            )
        return parsed

"""Keep native UBL/CII line imports usable without optional deferred fields."""

from odoo import models


class AccountEdiCommon(models.AbstractModel):
    _inherit = "account.edi.common"

    def _retrieve_invoice_line_vals(self, record, tree, document_type=False, qty_factor=1):
        values = super()._retrieve_invoice_line_vals(record, tree, document_type, qty_factor)
        if values is not None:
            # saas~19.3 emits these empty Enterprise keys even when absent from
            # the registry. Keep supported fields and unsupported real values:
            # native validation must still reject data we cannot represent.
            line_fields = self.env["account.move.line"]._fields
            for name in ("deferred_start_date", "deferred_end_date"):
                if name not in line_fields and values.get(name) is None:
                    values.pop(name, None)
        return values

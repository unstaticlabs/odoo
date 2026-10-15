from odoo import models
from odoo.addons.web.controllers.utils import clean_action


class ValidateAccountMove(models.TransientModel):
    _inherit = "validate.account.move"

    def validate_move(self):
        result = super().validate_move()
        bill_id = self.env.context.get("usl_post_match_bill_id")
        if not bill_id or len(self) != 1 or self.move_ids.ids != [bill_id]:
            return result
        # Native Cancel/X never calls this method. This marker coordinates the
        # UI only; matching still rechecks current authority and reviewed facts.
        self.move_ids.invalidate_recordset(["state"])
        return {
            "type": "ir.actions.act_window_close",
            "infos": {
                "usl_post_match_validated": True,
                "bill_id": bill_id,
                "posted_ids": self.move_ids.filtered(lambda move: move.state == "posted").ids,
                "native_action": clean_action(result, self.env) if isinstance(result, dict)
                    and result.get("type") != "ir.actions.act_window_close" else False,
            },
        }

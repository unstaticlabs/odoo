from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL


class PurchaseMailGroupingReview(models.TransientModel):
    _name = "usl.purchase.mail.grouping.review"
    _description = "Review Purchase Mail Attachments"

    move_id = fields.Many2one("account.move", string="Bill", required=True, readonly=True)
    fingerprint = fields.Char(string="Attachment fingerprint", required=True, readonly=True)
    attachment_ids = fields.Many2many("ir.attachment", string="Current attachments", readonly=True)
    main_attachment_id = fields.Many2one("ir.attachment", string="Main document")
    summary = fields.Text(related="move_id.usl_mail_grouping_summary", readonly=True, compute_sudo=False)

    def action_keep_together(self):
        self.ensure_one()
        move = self.move_id
        move.check_access("write")
        self.env.cr.execute(SQL("SELECT id FROM account_move WHERE id = %s FOR UPDATE", move.id))
        move.invalidate_recordset()
        current, attachments = move._mail_grouping_fingerprint()
        if current != self.fingerprint:
            raise UserError(self.env._("The attachments changed. Close this review and open it again to review the current files."))
        if self.main_attachment_id:
            self.main_attachment_id.check_access("read")
            if self.main_attachment_id not in attachments:
                raise UserError(self.env._("Choose a readable attachment belonging to this bill."))
            if self.main_attachment_id != move.message_main_attachment_id:
                self.main_attachment_id.register_as_main_attachment(force=True)
        metadata = dict(move.usl_mail_grouping or {})
        metadata["confirmed_fingerprint"] = current
        move.with_context(skip_is_manually_modified=True).write({
            "usl_mail_grouping": metadata,
            "usl_mail_grouping_needs_review": False,
            "usl_mail_grouping_reviewed_by": self.env.user.id,
            "usl_mail_grouping_reviewed_at": fields.Datetime.now(),
        })
        return {"type": "ir.actions.act_window_close"}

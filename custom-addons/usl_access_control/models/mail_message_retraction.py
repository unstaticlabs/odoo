from odoo import models
from odoo.exceptions import AccessError, UserError


class MailMessage(models.Model):
    _inherit = "mail.message"

    def action_retract_from_chatter(self):
        """Retract one comment from an accessible business record's Chatter."""
        self.ensure_one()
        # As in /mail/message/update_content, lookup preserves the caller's UID.
        # Author, message access and business record access precede any update.
        message = self.sudo().exists()
        unavailable = self.env._("This Chatter message is unavailable for retraction.")
        if not message or not message.res_id or not message.model or message.model == "discuss.channel":
            raise AccessError(unavailable)
        model = self.env.get(message.model)
        if model is None or not hasattr(model, "_message_update_content"):
            raise AccessError(unavailable)
        thread = model.browse(message.res_id).exists()
        if (
            not thread or not thread.has_access("read")
            or not message._get_with_access(mode="write")
            or not (message.is_current_user_or_guest_author or self.env.user._is_admin())
        ):
            raise AccessError(unavailable)
        if message.has_poll:
            raise UserError(self.env._("Use the poll action to remove a poll."))
        thread = thread._usl_agent_collaboration()
        # The normal Remove action supplies these four values. Odoo checks the
        # message type and performs attachment, translation and bus updates.
        thread._message_update_content(
            message.with_context(thread.env.context),
            body="", attachment_ids=[], partner_ids=[], subject="",
        )
        return {"message_id": message.id, "model": thread._name, "res_id": thread.id}

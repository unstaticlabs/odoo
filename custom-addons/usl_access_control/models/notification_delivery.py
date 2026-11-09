from odoo import models


# Only the native task/activity assignment workflows may suppress their Inbox
# notices. A serializable context flag cannot suppress ordinary conversations.
_ASSIGNMENT_NOTICE_TOKEN = object()
_ASSIGNMENT_NOTICE_KEY = "usl_assignment_notice_token"


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _notify_get_recipients(self, message, msg_vals=False, **kwargs):
        recipients = super()._notify_get_recipients(message, msg_vals, **kwargs)
        values = msg_vals or {}
        author_id = values.get("author_id") or message.sudo().author_id.id
        # Delivery policy reads identity markers only. It neither changes the
        # recipient's roles nor grants access to the message's business record.
        users = self.env["res.users"].sudo()
        agent_uids = set(users.search([
            ("id", "in", [recipient["uid"] for recipient in recipients if recipient["uid"]]),
            ("usl_is_ai_agent", "=", True),
        ]).ids)
        agent_author = bool(author_id and users.search_count([
            ("partner_id", "=", author_id), ("usl_is_ai_agent", "=", True),
        ], limit=1))
        for recipient in recipients:
            if recipient["type"] == "user" and (
                recipient["uid"] in agent_uids or agent_author
            ):
                recipient["notif"] = "inbox"
        if self.env.context.get(_ASSIGNMENT_NOTICE_KEY) is _ASSIGNMENT_NOTICE_TOKEN:
            recipients = [recipient for recipient in recipients if recipient["notif"] != "inbox"]
        return recipients


class ProjectTask(models.Model):
    _inherit = "project.task"

    def _task_message_auto_subscribe_notify(self, users_per_task):
        scoped = {
            task.with_context(**{_ASSIGNMENT_NOTICE_KEY: _ASSIGNMENT_NOTICE_TOKEN}): users
            for task, users in users_per_task.items()
        }
        return super()._task_message_auto_subscribe_notify(scoped)


class MailActivity(models.Model):
    _inherit = "mail.activity"

    def action_notify(self):
        activities = self.with_context(**{_ASSIGNMENT_NOTICE_KEY: _ASSIGNMENT_NOTICE_TOKEN})
        return super(MailActivity, activities).action_notify()

from odoo import Command
from odoo.tests import tagged

from .test_notification_preferences import TestNotificationPreferences


@tagged('post_install', '-at_install', 'usl_access_control')
class TestNotificationDelivery(TestNotificationPreferences):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = cls.env['usl.agent'].with_user(cls.user).create({
            'name': '[QA] Inbox assistant', 'purpose': 'Exercise notification delivery',
            'owner_id': cls.user.id, 'company_id': cls.env.company.id,
            'company_ids': [Command.set(cls.env.company.ids)],
            'delegated_group_ids': [Command.set(cls.env.ref('base.group_user').ids)],
        })
        cls.external = cls.env['res.partner'].create({
            'name': '[QA] External recipient', 'email': 'qa.external@example.test',
        })
        cls.task = cls.env['project.task'].with_context(tracking_disable=True).create({
            'name': '[QA] Notification delivery',
        })

    def _notify(self, author, recipients, **context):
        return self.task.with_context(mail_notify_force_send=False, **context).message_notify(
            body='[QA] Review this task', author_id=author.id, partner_ids=recipients.ids,
        )

    def _channels(self, message):
        return {n.res_partner_id.id: n.notification_type for n in message.notification_ids}

    def test_agent_conversation_keeps_external_email(self):
        message = self._notify(self.agent.user_id.partner_id, self.other.partner_id | self.external)
        self.assertEqual(self._channels(message), {
            self.other.partner_id.id: 'inbox', self.external.id: 'email',
        })
        self.assertEqual(self.other.notification_type, 'email')

    def test_agent_recipient_uses_inbox_without_role_changes(self):
        before = self.agent.user_id.group_ids
        message = self._notify(self.user.partner_id, self.agent.user_id.partner_id)
        self.assertEqual(self._channels(message), {self.agent.user_id.partner_id.id: 'inbox'})
        self.assertEqual(self.agent.user_id.group_ids, before)

    def test_agent_conversation_keeps_portal_email(self):
        portal = self._create_user("qa.notifications.delivery.portal")
        portal.write({"group_ids": [Command.set(self.env.ref("base.group_portal").ids)]})
        message = self._notify(self.agent.user_id.partner_id, portal.partner_id)
        self.assertEqual(self._channels(message), {portal.partner_id.id: "email"})

    def test_human_preferences_and_forged_context(self):
        message = self._notify(self.user.partner_id, self.other.partner_id, usl_assignment_notice_token=True)
        self.assertEqual(self._channels(message), {self.other.partner_id.id: 'email'})
        self.other.with_user(self.other).write({'notification_type': 'inbox'})
        message = self._notify(self.user.partner_id, self.other.partner_id, usl_assignment_notice_token=True)
        self.assertEqual(self._channels(message), {self.other.partner_id.id: 'inbox'})

    def test_assignment_filters_effective_inbox_after_routing(self):
        task = self.task.with_context(mail_notify_force_send=False)
        task._task_message_auto_subscribe_notify({task: self.agent.user_id})
        notices = self.env['mail.message'].search([
            ('model', '=', 'project.task'), ('res_id', '=', task.id),
            ('message_type', '=', 'user_notification'),
        ])
        self.assertTrue(notices)
        self.assertFalse(notices.notification_ids)
        message = self._notify(self.user.partner_id, self.agent.user_id.partner_id)
        self.assertEqual(self._channels(message), {self.agent.user_id.partner_id.id: 'inbox'})

    def test_task_assignment_preserves_email_and_assignee(self):
        task = self.env['project.task'].with_context(mail_notify_force_send=False).create({
            'name': '[QA] Email assignment', 'user_ids': [Command.set(self.other.ids)],
        })
        self.assertEqual(task.user_ids, self.other)
        notices = self.env['mail.message'].search([
            ('model', '=', 'project.task'), ('res_id', '=', task.id),
            ('message_type', '=', 'user_notification'),
        ])
        self.assertTrue(notices.notification_ids.filtered(
            lambda n: n.res_partner_id == self.other.partner_id and n.notification_type == 'email'
        ))

    def test_activity_assignment_retains_activity_without_inbox_notice(self):
        self.other.with_user(self.other).write({'notification_type': 'inbox'})
        activity = self.env['mail.activity'].create({
            'res_model_id': self.env['ir.model']._get_id('project.task'),
            'res_id': self.task.id, 'user_id': self.other.id,
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'summary': '[QA] Activity remains visible',
        })
        self.assertEqual(activity.user_id, self.other)
        notices = self.env['mail.message'].search([
            ('model', '=', 'project.task'), ('res_id', '=', self.task.id),
            ('message_type', '=', 'user_notification'),
        ])
        self.assertTrue(notices)
        self.assertFalse(notices.notification_ids)

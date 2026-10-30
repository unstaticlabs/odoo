from odoo import SUPERUSER_ID, Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..models.res_users import _NOTIFICATION_PREFERENCE_TOKEN


@tagged("post_install", "-at_install", "usl_access_control")
class TestNotificationPreferences(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.inbox_group = cls.env.ref("mail.group_mail_notification_type_inbox")
        cls.user = cls._create_user("qa.notifications.owner")
        cls.other = cls._create_user("qa.notifications.other")

    @classmethod
    def _create_user(cls, login):
        return cls.env["res.users"].with_user(SUPERUSER_ID).with_context(
            no_reset_password=True, usl_governed_identity_provisioning=True,
        ).create({
            "name": "[QA] Notification reviewer",
            "login": login,
            "email": f"{login}@example.test",
            "group_ids": [Command.set(cls.env.ref("base.group_user").ids)],
            "company_id": cls.env.company.id,
            "company_ids": [Command.set(cls.env.company.ids)],
        })

    def test_real_preference_change_round_trip_preserves_authority(self):
        self.assertEqual(self.user.notification_type, "email")
        roles = self.user.all_group_ids - self.inbox_group
        for choice in ("inbox", "email", "inbox"):
            field = self.user._fields["notification_type"]
            self.env.add_to_compute(field, self.user)
            self.assertTrue(self.env.is_to_compute(field, self.user))
            result = self.user.with_user(self.user).web_save(
                {"notification_type": choice}, {"notification_type": {}},
            )
            self.assertEqual(result[0]["notification_type"], choice)
            self.env.flush_all()
            self.user.invalidate_recordset(["notification_type", "group_ids", "all_group_ids"])
            self.assertEqual(self.user.notification_type, choice)
            self.assertEqual(self.user.all_group_ids - self.inbox_group, roles)
            self.assertEqual(self.inbox_group in self.user.group_ids, choice == "inbox")

    def test_preference_does_not_authorize_other_identity_or_groups(self):
        original = self.other.notification_type
        with self.assertRaises(AccessError):
            self.other.with_user(self.user).write({"notification_type": "inbox"})
        with self.assertRaises(AccessError):
            self.user.with_user(self.user).write({
                "group_ids": [Command.link(self.env.ref("base.group_system").id)],
            })
        with self.assertRaises(AccessError):
            self.user.with_user(self.user).with_context(
                usl_notification_preference_token=True,
            ).write({"group_ids": [Command.link(self.inbox_group.id)]})
        self.assertEqual(self.other.notification_type, original)
        self.assertFalse(self.user.has_group("base.group_system"))

    def test_mixed_preference_and_role_change_is_still_protected(self):
        with self.assertRaises(AccessError):
            self.user.with_user(self.user).write({
                "notification_type": "inbox",
                "group_ids": [Command.link(self.env.ref("base.group_system").id)],
            })
        self.assertEqual(self.user.notification_type, "email")
        self.assertFalse(self.user.has_group("base.group_system"))

    def test_portal_user_keeps_native_email_only_constraint(self):
        portal = self._create_user("qa.notifications.portal")
        portal.with_user(SUPERUSER_ID).write({
            "group_ids": [Command.set(self.env.ref("base.group_portal").ids)],
        })
        self.assertTrue(portal.share)
        with self.assertRaises((AccessError, ValidationError)):
            portal.with_user(portal).write({"notification_type": "inbox"})
            self.env.flush_all()
        self.assertEqual(portal.notification_type, "email")

    def test_native_token_does_not_authorize_extra_commands_or_fields(self):
        own = self.user.with_user(self.user).with_context(
            usl_notification_preference_token=_NOTIFICATION_PREFERENCE_TOKEN,
        )
        for values in (
            {"group_ids": [Command.link(self.inbox_group.id), Command.link(self.env.ref("base.group_system").id)]},
            {"group_ids": [Command.link(self.inbox_group.id)], "login": "qa.notifications.changed"},
        ):
            with self.assertRaises(AccessError):
                own.write(values)
        self.assertEqual(self.user.login, "qa.notifications.owner")
        self.assertFalse(self.user.has_group("base.group_system"))

    def test_managed_agent_identity_still_requires_its_owner_workflow(self):
        agent = self.env["usl.agent"].with_user(self.user).create({
            "name": "[QA] Notification Agent",
            "purpose": "Exercise managed identity boundaries",
            "owner_id": self.user.id,
            "company_id": self.env.company.id,
            "company_ids": [Command.set(self.env.company.ids)],
            "delegated_group_ids": [Command.set(self.env.ref("base.group_user").ids)],
        })
        before = agent.user_id.notification_type
        changed = "inbox" if before == "email" else "email"
        with self.assertRaises(AccessError):
            agent.user_id.with_user(self.user).write({"notification_type": changed})
        with self.assertRaises(AccessError):
            agent.user_id.with_user(agent.user_id).with_context(
                usl_notification_preference_token=_NOTIFICATION_PREFERENCE_TOKEN,
            ).write({"notification_type": changed})
        self.assertEqual(agent.user_id.notification_type, before)

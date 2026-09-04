from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("usl_pocketid", "usl_sender_alias", "post_install", "-at_install")
class TestPersonalSenderAliases(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env,
            login="sender.alias.user@example.invalid",
            email="sender.alias.user@example.invalid",
            groups="base.group_user,project.group_project_user",
            context={"no_reset_password": True},
        )
        cls.employee = cls.env["hr.employee"].create(
            {
                "name": "Personal sender employee",
                "user_id": cls.user.id,
                "work_contact_id": cls.user.partner_id.id,
                "work_email": cls.user.email,
                "company_id": cls.user.company_id.id,
            },
        )
        cls.env["ir.config_parameter"].sudo().set_str(
            "web.base.url",
            "https://odoo.example.invalid",
        )

    def _new_alias(self, email="personal.sender@example.invalid"):
        return (
            self.env["usl.mail.sender.alias"]
            .with_user(self.user)
            .with_context(usl_sender_alias_skip_automatic_verification=True)
            .create(
                {
                    "partner_id": self.user.partner_id.id,
                    "email": email,
                },
            )
        )

    def _verify(self, alias):
        raw_token, _link, _mail = alias._issue_verification(send=False)
        self.assertTrue(alias.sudo()._verify_token(raw_token))
        return alias

    def test_pending_address_does_not_change_sender_identity(self):
        alias = self._new_alias()

        partner = self.env["res.partner"]._usl_verified_sender_partner(alias.email)

        self.assertFalse(partner)
        self.assertEqual(alias.state, "pending")

    def test_user_preferences_can_register_own_address(self):
        with patch.object(
            type(self.env["mail.mail"]),
            "send",
            autospec=True,
            return_value=True,
        ) as send:
            self.user.with_user(self.user).write(
                {
                    "usl_sender_alias_ids": [
                        Command.create(
                            {"email": "Preferences <prefs@example.invalid>"},
                        ),
                    ],
                },
            )

        self.assertEqual(
            self.user.usl_sender_alias_ids.email,
            "prefs@example.invalid",
        )
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.user.usl_sender_alias_ids.state, "pending")
        self.assertTrue(self.user.usl_sender_alias_ids.verification_sent_at)
        self.assertTrue(self.user.usl_sender_alias_ids.verification_token_digest)
        self.assertTrue(self.user.usl_sender_alias_ids.verification_expires_at)

    def test_adding_address_does_not_resend_existing_addresses(self):
        verified = self._verify(
            self._new_alias("verified.personal@example.invalid"),
        )
        pending = self._new_alias("pending.personal@example.invalid")
        verified_sent_at = verified.sudo().verification_sent_at

        with patch.object(
            type(self.env["mail.mail"]),
            "send",
            autospec=True,
            return_value=True,
        ) as send:
            self.user.with_user(self.user).write(
                {
                    "usl_sender_alias_ids": [
                        Command.create({"email": "new.personal@example.invalid"}),
                    ],
                },
            )

        created = self.user.usl_sender_alias_ids.filtered(
            lambda alias: alias.email == "new.personal@example.invalid",
        )
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[0].email_to, created.email)
        self.assertEqual(verified.state, "verified")
        self.assertEqual(verified.sudo().verification_sent_at, verified_sent_at)
        self.assertEqual(pending.state, "pending")
        self.assertFalse(pending.sudo().verification_sent_at)

    def test_unchanged_verified_address_is_not_reset_or_resent(self):
        alias = self._verify(self._new_alias())
        verified_at = alias.verified_at
        verification_sent_at = alias.sudo().verification_sent_at

        with patch.object(
            type(self.env["mail.mail"]),
            "send",
            autospec=True,
            return_value=True,
        ) as send:
            alias.with_context(
                usl_sender_alias_skip_automatic_verification=False,
            ).write({"email": alias.email})

        self.assertEqual(send.call_count, 0)
        self.assertEqual(alias.state, "verified")
        self.assertEqual(alias.verified_at, verified_at)
        self.assertEqual(
            alias.sudo().verification_sent_at,
            verification_sent_at,
        )

    def test_verified_address_rejects_manual_resend(self):
        alias = self._verify(self._new_alias())

        with self.assertRaisesRegex(ValidationError, "already verified"):
            alias.action_send_verification()

        self.assertEqual(alias.state, "verified")

    def test_verified_address_resolves_contact_user_and_employee(self):
        alias = self._verify(self._new_alias())

        partner = self.env["mail.thread"]._partner_find_from_emails_single(
            [f"Personal Name <{alias.email}>"],
            no_create=True,
        )
        aligned = self.env["mail.thread"]._mail_find_partner_from_emails(
            [alias.email],
        )
        employee = self.env["hr.expense"]._get_employee_from_email(alias.email)

        self.assertEqual(partner, self.user.partner_id)
        self.assertEqual(aligned, [self.user.partner_id])
        self.assertEqual(employee, self.employee)

    def test_verified_address_overrides_an_external_duplicate_contact(self):
        external = self.env["res.partner"].create(
            {
                "name": "Old external duplicate",
                "email": "duplicate.personal@example.invalid",
            },
        )
        alias = self._verify(self._new_alias(external.email))

        partner = self.env["mail.thread"]._partner_find_from_emails_single(
            [alias.email],
            no_create=True,
        )

        self.assertEqual(partner, self.user.partner_id)
        self.assertNotEqual(partner, external)

    def test_todo_from_verified_personal_address_assigns_sender(self):
        if not self.env["ir.module.module"].search_count(
            [("name", "=", "usl_project"), ("state", "=", "installed")],
        ):
            self.skipTest("The To-Do assignment behavior belongs to usl_project.")
        alias_domain = self.env["mail.alias.domain"].create(
            {"name": "sender-alias.example.invalid"},
        )
        todo_alias = self.env["mail.alias"].create(
            {
                "alias_name": "todo",
                "alias_domain_id": alias_domain.id,
                "alias_model_id": self.env["ir.model"]._get_id("project.task"),
            },
        )
        sender_alias = self._verify(self._new_alias())

        task = self.env["project.task"].message_new(
            {
                "subject": "Personal email task",
                "email_from": f"Personal Name <{sender_alias.email}>",
                "to": todo_alias.alias_full_name,
            },
        )

        self.assertFalse(task.project_id)
        self.assertEqual(task.user_ids, self.user)
        self.assertEqual(task.partner_id, self.user.partner_id)

    def test_employee_only_alias_accepts_verified_personal_address(self):
        sender_alias = self._verify(self._new_alias())
        destination = self.env["mail.alias"].create(
            {
                "alias_name": "employee-expenses",
                "alias_domain_id": self.env["mail.alias.domain"].create(
                    {"name": "employee-alias.example.invalid"},
                ).id,
                "alias_model_id": self.env["ir.model"]._get_id("hr.expense"),
                "alias_contact": "employees",
            },
        )

        error = self.env["hr.expense"]._alias_get_error(
            None,
            {"author_id": self.user.partner_id.id, "email_from": sender_alias.email},
            destination,
        )

        self.assertFalse(error)

    def test_verification_is_expiring_and_one_time(self):
        alias = self._new_alias()
        raw_token, link, mail = alias._issue_verification(send=False)

        self.assertEqual(mail.email_to, alias.email_normalized)
        self.assertIn(raw_token, link)
        self.assertTrue(alias._verify_token(raw_token))
        self.assertEqual(alias.state, "verified")
        self.assertFalse(alias._verify_token(raw_token))

        expired = self._new_alias("expired.sender@example.invalid")
        expired_token, _link, _mail = expired._issue_verification(send=False)
        expired.sudo().with_context(usl_sender_alias_internal=True).write(
            {"verification_expires_at": fields.Datetime.now() - timedelta(seconds=1)},
        )
        self.assertFalse(expired._verify_token(expired_token))

    def test_changing_address_requires_fresh_verification(self):
        alias = self._verify(self._new_alias()).with_context(
            usl_sender_alias_skip_automatic_verification=False,
        )

        with patch.object(
            type(self.env["mail.mail"]),
            "send",
            autospec=True,
            return_value=True,
        ) as send:
            alias.write({"email": "changed.sender@example.invalid"})

        self.assertEqual(alias.state, "pending")
        self.assertFalse(alias.verified_at)
        self.assertEqual(send.call_count, 1)
        self.assertTrue(alias.sudo().verification_sent_at)
        self.assertTrue(alias.sudo().verification_token_digest)
        self.assertFalse(
            self.env["res.partner"]._usl_verified_sender_partner(alias.email),
        )

    def test_user_cannot_register_an_address_for_someone_else(self):
        other = self.env["res.partner"].create(
            {"name": "Other contact", "email": "other@example.invalid"},
        )

        with self.assertRaises(AccessError):
            self.env["usl.mail.sender.alias"].with_user(self.user).create(
                {
                    "partner_id": other.id,
                    "email": "other.personal@example.invalid",
                },
            )

    def test_internal_identity_and_destination_aliases_cannot_be_claimed(self):
        other_user = new_test_user(
            self.env,
            login="other.internal@example.invalid",
            email="other.internal@example.invalid",
            groups="base.group_user",
            context={"no_reset_password": True},
        )
        with self.assertRaises(ValidationError):
            self._new_alias(other_user.email)

        alias_domain = self.env["mail.alias.domain"].create(
            {"name": "destinations.example.invalid"},
        )
        destination = self.env["mail.alias"].create(
            {
                "alias_name": "project-safe",
                "alias_domain_id": alias_domain.id,
                "alias_model_id": self.env["ir.model"]._get_id("project.task"),
            },
        )
        with self.assertRaises(ValidationError):
            self._new_alias(destination.alias_full_name)

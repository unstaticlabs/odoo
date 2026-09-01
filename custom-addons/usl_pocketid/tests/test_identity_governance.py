import datetime
from unittest.mock import patch

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessDenied, ValidationError
from odoo.tests import TransactionCase, tagged

from ..exceptions import PocketIDAccessDenied
from ..policy import LOGIN_POLICY_PARAMETER, LOGIN_POLICY_SSO_ONLY


@tagged("post_install", "-at_install", "usl_pocketid")
class TestPocketIDIdentityGovernance(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("usl_pocketid.provider_pocketid")
        cls.provider._usl_pocketid_environment_write(
            {
                "enabled": True,
                "client_id": "odoo-client",
                "auth_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
                "usl_oidc_issuer": "https://id.example.test",
                "usl_public_base_url": "https://odoo.example.test",
                "usl_required_group": "odoo-preprod",
            },
        )
        cls.user = cls._user(
            login="existing.person@example.test",
            name="Existing Imported Person",
            email="existing.person@example.test",
        )

    @classmethod
    def _user(cls, *, login, name, email):
        return cls.env["res.users"].with_context(
            no_reset_password=True,
        ).create(
            {
                "login": login,
                "name": name,
                "email": email,
                "password": "local-password-must-not-work",
                "company_id": cls.env.company.id,
                "company_ids": [Command.set(cls.env.company.ids)],
                "group_ids": [
                    Command.set(cls.env.ref("base.group_user").ids),
                ],
                "usl_pocketid_access": True,
                "usl_identity_classification": "active",
            },
        )

    def _claims(self, *, subject="immutable-subject", email=None, **values):
        claims = {
            "iss": self.provider.usl_oidc_issuer,
            "sub": subject,
            "email": email or self.user.email,
            "email_verified": True,
            "name": "Updated Pocket ID Name",
            "groups": ["odoo-preprod"],
        }
        claims.update(values)
        return claims

    def _identity(self, *, user=None, subject="immutable-subject"):
        return self.env["usl.oidc.identity"].create(
            {
                "issuer": self.provider.usl_oidc_issuer,
                "subject": subject,
                "provider_id": self.provider.id,
                "user_id": (user or self.user).id,
            },
        )

    def test_explicit_link_reuses_existing_user_and_preserves_profile(self):
        identity = self._identity()
        original_email = self.user.email
        user_count = self.env["res.users"].sudo().search_count([])
        db, login, token, returned_identity = self.env[
            "res.users"
        ]._usl_pocketid_login(
            self.provider,
            self._claims(email="new-profile-email@example.test"),
            "opaque-access-token",
        )
        self.assertEqual(db, self.env.cr.dbname)
        self.assertEqual(login, self.user.login)
        self.assertEqual(token, "opaque-access-token")
        self.assertEqual(returned_identity, identity)
        self.assertEqual(self.user.email, original_email)
        self.assertEqual(
            identity.last_email,
            "new-profile-email@example.test",
        )
        self.assertEqual(
            self.env["res.users"].sudo().search_count([]),
            user_count,
        )

    def test_preferences_summarize_the_governed_pocketid_identity(self):
        identity = self._identity()
        signed_in_at = datetime.datetime(2026, 9, 1, 8, 30)
        identity.write(
            {
                "last_email": "pocketid@example.test",
                "last_login_at": signed_in_at,
            },
        )

        self.assertEqual(self.user.usl_pocketid_security_state, "connected")
        self.assertEqual(
            self.user.usl_pocketid_security_email,
            "pocketid@example.test",
        )
        self.assertEqual(self.user.usl_pocketid_last_login_at, signed_in_at)
        self.assertEqual(
            self.user.action_open_pocketid_account(),
            {
                "type": "ir.actions.act_url",
                "url": "https://id.example.test",
                "target": "new",
            },
        )

        identity.active = False
        self.assertEqual(self.user.usl_pocketid_security_state, "ready")
        self.user.usl_pocketid_access = False
        self.assertEqual(self.user.usl_pocketid_security_state, "unavailable")

    def test_preferences_replace_local_sign_in_controls_with_pocketid(self):
        view = self.env.ref("base.view_users_form_simple_modif")
        arch = etree.fromstring(
            self.env["res.users"].get_view(
                view_id=view.id,
                view_type="form",
            )["arch"],
        )

        pocketid_sections = arch.xpath("//div[@name='usl_pocketid_security']")
        self.assertEqual(len(pocketid_sections), 1)
        self.assertEqual(
            pocketid_sections[0].get("invisible"),
            "not usl_sso_only_login",
        )
        totp_field = arch.xpath("//field[@name='totp_enabled']")[0]
        totp_row = next(
            parent
            for parent in totp_field.iterancestors("div")
            if "d-flex" in (parent.get("class") or "").split()
        )
        self.assertEqual(totp_row.get("invisible"), "usl_sso_only_login")

    def test_unknown_identity_never_creates_an_odoo_user(self):
        user_count = self.env["res.users"].sudo().search_count([])
        with self.assertRaisesRegex(PocketIDAccessDenied, "Pocket ID"):
            self.env["res.users"]._usl_pocketid_login(
                self.provider,
                self._claims(subject="unknown-subject"),
                "opaque-access-token",
            )
        self.assertEqual(
            self.env["res.users"].sudo().search_count([]),
            user_count,
        )
        self.assertFalse(
            self.env["usl.oidc.identity"].search(
                [("subject", "=", "unknown-subject")],
            ),
        )

    def test_verified_unique_email_link_requires_explicit_preapproval(self):
        self.provider._usl_pocketid_environment_write(
            {"usl_allow_unique_email_link": True},
        )
        with self.assertRaises(PocketIDAccessDenied):
            self.env["res.users"]._usl_pocketid_login(
                self.provider,
                self._claims(subject="email-subject"),
                "opaque-access-token",
            )
        self.user.usl_pocketid_email_link = True
        _, login, _, identity = self.env["res.users"]._usl_pocketid_login(
            self.provider,
            self._claims(subject="email-subject"),
            "opaque-access-token",
        )
        self.assertEqual(login, self.user.login)
        self.assertEqual(identity.user_id, self.user)
        self.assertEqual(identity.link_method, "verified_unique_email")

    def test_ambiguous_verified_email_is_refused_without_link(self):
        self.provider._usl_pocketid_environment_write(
            {"usl_allow_unique_email_link": True},
        )
        self.user.usl_pocketid_email_link = True
        second = self._user(
            login="second-user@example.test",
            name="Second Person",
            email=self.user.email,
        )
        second.usl_pocketid_email_link = True
        with self.assertRaises(PocketIDAccessDenied) as denied:
            self.env["res.users"]._usl_pocketid_login(
                self.provider,
                self._claims(subject="ambiguous-subject"),
                "opaque-access-token",
            )
        self.assertEqual(denied.exception.reason, "identity_conflict")
        self.assertFalse(
            self.env["usl.oidc.identity"].search(
                [("subject", "=", "ambiguous-subject")],
            ),
        )

    def test_archived_user_disabled_identity_and_access_flag_block_login(self):
        identity = self._identity()
        identity.active = False
        with self.assertRaises(PocketIDAccessDenied) as denied:
            self.env["res.users"]._usl_pocketid_login(
                self.provider,
                self._claims(),
                "opaque-access-token",
            )
        self.assertEqual(denied.exception.reason, "identity_disabled")
        identity.active = True
        self.user.usl_pocketid_access = False
        with self.assertRaises(PocketIDAccessDenied) as denied:
            self.env["res.users"]._usl_pocketid_login(
                self.provider,
                self._claims(),
                "opaque-access-token",
            )
        self.assertEqual(denied.exception.reason, "user_disabled")
        self.user.usl_pocketid_access = True
        self.user.active = False
        with self.assertRaises(PocketIDAccessDenied) as denied:
            self.env["res.users"]._usl_pocketid_login(
                self.provider,
                self._claims(),
                "opaque-access-token",
            )
        self.assertEqual(denied.exception.reason, "user_disabled")

    def test_disabling_identity_clears_oauth_credential_but_not_user(self):
        identity = self._identity()
        self.assertEqual(identity.linked_by_id, self.env.user)
        with self.assertRaises(ValidationError):
            identity.write({"linked_by_id": self.user.id})
        self.env["res.users"]._usl_pocketid_login(
            self.provider,
            self._claims(),
            "opaque-access-token",
        )
        identity.active = False
        self.assertFalse(self.user.oauth_provider_id)
        self.assertFalse(self.user.oauth_uid)
        self.assertTrue(self.user.active)
        identity.active = True
        self.assertTrue(
            self.env["usl.oidc.audit.event"].search(
                [
                    ("identity_id", "=", identity.id),
                    ("event_type", "=", "identity_enabled"),
                ],
            ),
        )
        identity.active = False
        with self.assertRaises(ValidationError):
            identity.unlink()

    def test_historical_profile_preserves_user_identity_and_attribution(self):
        identity = self._identity()
        attributed_message = self.env["mail.message"].sudo().create(
            {
                "model": "res.partner",
                "res_id": self.user.partner_id.id,
                "message_type": "comment",
                "body": "Preserved historical chatter authorship",
                "author_id": self.user.partner_id.id,
            },
        )
        original_user_id = self.user.id
        original_partner_id = self.user.partner_id.id
        user_count = self.env["res.users"].with_context(
            active_test=False,
        ).search_count([])
        configuration = [
            {
                "login": self.user.login,
                "email": self.user.email,
                "profile": "historical",
            },
            {
                "login": "historical-test-break-glass",
                "name": "Historical Test Break Glass",
                "email": "historical-test-break-glass@example.invalid",
                "profile": "break_glass",
                "companies": "all",
                "create_if_missing": True,
            },
        ]

        self.env["res.users"]._usl_pocketid_apply_user_configuration(
            configuration,
            break_glass_password="safe-local-password-12345",
            strict=False,
        )

        archived_user = self.env["res.users"].with_context(
            active_test=False,
        ).browse(original_user_id)
        archived_identity = self.env["usl.oidc.identity"].with_context(
            active_test=False,
        ).browse(identity.id)
        self.assertTrue(archived_user.exists())
        self.assertFalse(archived_user.active)
        self.assertEqual(archived_user.usl_identity_classification, "historical")
        self.assertFalse(archived_user.usl_pocketid_access)
        self.assertEqual(archived_user.partner_id.id, original_partner_id)
        self.assertTrue(archived_identity.exists())
        self.assertFalse(archived_identity.active)
        self.assertEqual(archived_identity.user_id, archived_user)
        self.assertEqual(attributed_message.author_id, archived_user.partner_id)
        self.assertEqual(
            self.env["res.users"].with_context(
                active_test=False,
            ).search_count([]),
            user_count + 1,
        )

    def test_sso_managed_user_cannot_use_or_register_local_credentials(self):
        credentials = [
            {
                "type": "password",
                "password": "local-password-must-not-work",
            },
            {
                "type": "webauthn",
                "credential": {"id": "odoo-local-passkey"},
            },
        ]
        for credential in credentials:
            with self.subTest(credential_type=credential["type"]):
                with self.assertRaises(AccessDenied):
                    self.user.with_user(self.user)._check_credentials(
                        credential,
                        {"interactive": True},
                    )
        self.user.write(
            {
                "oauth_provider_id": self.env.ref(
                    "auth_oauth.provider_openerp",
                ).id,
                "oauth_uid": "alternate-oauth-subject",
                "oauth_access_token": "alternate-oauth-token",
            },
        )
        with self.assertRaises(AccessDenied):
            self.user.with_user(self.user)._check_credentials(
                {
                    "type": "oauth_token",
                    "token": "alternate-oauth-token",
                },
                {"interactive": True},
            )
        self.user.write(
            {
                "oauth_provider_id": False,
                "oauth_uid": False,
                "oauth_access_token": False,
            },
        )
        self._identity()
        self.user.write(
            {
                "usl_pocketid_access": False,
                "oauth_provider_id": self.provider.id,
                "oauth_uid": "immutable-subject",
                "oauth_access_token": "disabled-pocketid-token",
            },
        )
        with self.assertRaises(AccessDenied):
            self.user.with_user(self.user)._check_credentials(
                {
                    "type": "oauth_token",
                    "token": "disabled-pocketid-token",
                },
                {"interactive": True},
            )
        with self.assertRaisesRegex(ValidationError, "Pocket ID"):
            self.user.with_user(self.user).action_create_passkey()

    def test_break_glass_user_cannot_be_pocket_id_managed(self):
        with self.assertRaises(ValidationError):
            self.user.write(
                {
                    "usl_local_break_glass": True,
                    "usl_pocketid_access": True,
                },
            )

    def test_portal_user_can_use_an_explicit_governed_identity(self):
        portal_user = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create(
            {
                "login": "portal.pocketid@example.test",
                "name": "Portal Pocket ID User",
                "email": "portal.pocketid@example.test",
                "password": "portal-password-must-not-work",
                "company_id": self.env.company.id,
                "company_ids": [Command.set(self.env.company.ids)],
                "group_ids": [
                    Command.set(self.env.ref("base.group_portal").ids),
                ],
                "usl_pocketid_access": True,
                "usl_identity_classification": "portal",
            },
        )
        identity = self._identity(
            user=portal_user,
            subject="portal-subject",
        )
        user, resolved = self.env["res.users"]._usl_pocketid_resolve_user(
            self.provider,
            self._claims(
                subject="portal-subject",
                email=portal_user.email,
            ),
        )
        self.assertEqual(user, portal_user)
        self.assertEqual(resolved, identity)

    def test_sso_only_rejects_password_but_accepts_api_key(self):
        self.env["ir.config_parameter"].sudo().set_str(
            LOGIN_POLICY_PARAMETER,
            LOGIN_POLICY_SSO_ONLY,
        )
        with self.assertRaises(AccessDenied):
            self.user.with_user(self.user)._check_credentials(
                {
                    "type": "password",
                    "password": "local-password-must-not-work",
                },
                {"interactive": True},
            )
        expiration = datetime.datetime.now() + datetime.timedelta(days=1)
        api_key = self.env["res.users.apikeys"].with_user(self.user).sudo()._generate(
            None,
            "Pocket ID test integration",
            expiration,
        )
        result = self.user.with_user(self.user)._check_credentials(
            {"type": "password", "password": api_key},
            {"interactive": False},
        )
        self.assertEqual(result["auth_method"], "apikey")

    def test_policy_activation_is_idempotent_and_rotates_once(self):
        configuration = self._governed_user_configuration()
        self.env["res.users"]._usl_pocketid_apply_user_configuration(
            configuration,
            break_glass_password="safe-local-password-12345",
            strict=False,
        )
        exempt = self.env["res.users"]._usl_pocketid_policy_exempt_users()
        unconfigured = self.env["res.users"].sudo().search(
            [
                ("active", "=", True),
                ("id", "not in", (self.user | exempt).ids),
                ("usl_local_break_glass", "=", False),
            ],
        )
        unconfigured.write({"active": False})
        mail_count = self.env["mail.mail"].sudo().search_count([])
        with patch.dict(
            "os.environ",
            {"USL_POCKET_ID_LOGIN_POLICY": "sso_only"},
            clear=False,
        ):
            first = self.env["res.users"]._usl_pocketid_apply_login_policy()
            self.env.cr.execute(
                "SELECT password FROM res_users WHERE id = %s",
                [self.user.id],
            )
            password_after_first = self.env.cr.fetchone()[0]
            second = self.env["res.users"]._usl_pocketid_apply_login_policy()
        self.assertEqual(first, LOGIN_POLICY_SSO_ONLY)
        self.assertEqual(second, LOGIN_POLICY_SSO_ONLY)
        self.assertEqual(
            self.env["mail.mail"].sudo().search_count([]),
            mail_count,
        )
        self.env.cr.execute(
            "SELECT password FROM res_users WHERE id = %s",
            [self.user.id],
        )
        self.assertEqual(self.env.cr.fetchone()[0], password_after_first)
        self.assertFalse(
            self.env["ir.config_parameter"].sudo().get_bool(
                "auth_signup.reset_password",
            ),
        )

    def _governed_user_configuration(self):
        return [
            {
                "login": self.user.login,
                "email": self.user.email,
                "profile": "collaborator",
                "companies": [self.env.company.name],
                "subject": "configured-collaborator-subject",
            },
            {
                "login": "local.break.glass",
                "name": "Local Break Glass",
                "email": "local.break.glass@example.invalid",
                "profile": "break_glass",
                "companies": "all",
                "create_if_missing": True,
            },
        ]

    def test_named_profiles_are_least_privilege_and_idempotent(self):
        configuration = self._governed_user_configuration()
        mails_before = self.env["mail.mail"].sudo().search_count([])
        notifications_before = self.env["mail.notification"].sudo().search_count([])
        summary = self.env[
            "res.users"
        ]._usl_pocketid_apply_user_configuration(
            configuration,
            break_glass_password="safe-local-password-12345",
            strict=False,
        )
        self.assertEqual(summary["configured_count"], 2)
        self.assertTrue(self.user.usl_pocketid_access)
        self.assertTrue(self.user.has_group("project.group_project_user"))
        self.assertFalse(self.user.has_group("base.group_system"))
        self.assertEqual(
            self.user.usl_oidc_identity_ids.subject,
            "configured-collaborator-subject",
        )
        break_glass = self.env["res.users"].search(
            [("login", "=", "local.break.glass")],
        )
        self.assertTrue(break_glass.usl_local_break_glass)
        self.assertFalse(break_glass.usl_pocketid_access)
        self.assertTrue(break_glass.has_group("base.group_system"))
        break_glass.with_user(break_glass)._check_credentials(
            {
                "type": "password",
                "password": "safe-local-password-12345",
            },
            {"interactive": True},
        )
        user_count = self.env["res.users"].with_context(
            active_test=False,
        ).search_count([])
        configured_write_date = self.user.write_date
        self.env["res.users"]._usl_pocketid_apply_user_configuration(
            configuration,
            break_glass_password="safe-local-password-12345",
            strict=False,
        )
        self.assertEqual(
            self.env["res.users"].with_context(
                active_test=False,
            ).search_count([]),
            user_count,
        )
        self.assertEqual(
            self.user.write_date,
            configured_write_date,
            "Reapplying an unchanged Pocket ID profile must not rewrite its user",
        )
        self.assertEqual(
            self.env["mail.mail"].sudo().search_count([]),
            mails_before,
        )
        self.assertEqual(
            self.env["mail.notification"].sudo().search_count([]),
            notifications_before,
        )

    def test_optional_historical_profile_does_not_create_a_clean_install_user(self):
        prepared = self.env["res.users"]._usl_pocketid_prepare_user_configuration(
            [
                {
                    "login": "absent.historical@example.invalid",
                    "profile": "historical",
                    "optional_if_missing": True,
                },
            ],
        )
        self.assertFalse(prepared)
        self.assertFalse(
            self.env["res.users"].with_context(active_test=False).search(
                [("login", "=", "absent.historical@example.invalid")],
            ),
        )

    def test_strict_configuration_refuses_unclassified_human_users(self):
        with self.assertRaisesRegex(ValidationError, "explicitly classified"):
            self.env["res.users"]._usl_pocketid_apply_user_configuration(
                self._governed_user_configuration(),
                break_glass_password="safe-local-password-12345",
                strict=True,
            )

    def test_configuration_refuses_login_email_mismatch(self):
        configuration = self._governed_user_configuration()
        configuration[0]["email"] = "different@example.invalid"
        with self.assertRaisesRegex(ValidationError, "does not match"):
            self.env["res.users"]._usl_pocketid_apply_user_configuration(
                configuration,
                break_glass_password="safe-local-password-12345",
                strict=False,
            )

from odoo import Command
from odoo.exceptions import AccessDenied, ValidationError
from odoo.tests import TransactionCase, tagged

from ..exceptions import PocketIDAccessDenied


@tagged("post_install", "-at_install", "usl_pocketid")
class TestPocketIDIdentityGovernance(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("usl_pocketid.provider_pocketid")
        cls.provider.write(
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
        self.provider.usl_allow_unique_email_link = True
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
        self.provider.usl_allow_unique_email_link = True
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
        self.env["res.users"]._usl_pocketid_login(
            self.provider,
            self._claims(),
            "opaque-access-token",
        )
        identity.active = False
        self.assertFalse(self.user.oauth_provider_id)
        self.assertFalse(self.user.oauth_uid)
        self.assertTrue(self.user.active)
        with self.assertRaises(ValidationError):
            identity.unlink()

    def test_sso_managed_user_cannot_use_local_password(self):
        with self.assertRaises(AccessDenied):
            self.user.with_user(self.user)._check_credentials(
                {
                    "type": "password",
                    "password": "local-password-must-not-work",
                },
                {"interactive": True},
            )

    def test_break_glass_user_cannot_be_pocket_id_managed(self):
        with self.assertRaises(ValidationError):
            self.user.write(
                {
                    "usl_local_break_glass": True,
                    "usl_pocketid_access": True,
                },
            )


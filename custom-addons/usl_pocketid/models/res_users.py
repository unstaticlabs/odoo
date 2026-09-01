import secrets
import time

from odoo import SUPERUSER_ID, Command, _, api, fields, models
from odoo.exceptions import AccessDenied, AccessError, UserError, ValidationError
from odoo.http import request

from ..exceptions import PocketIDAccessDenied, PocketIDReason
from ..policy import (
    EMERGENCY_SESSION_KEY,
    LOGIN_POLICY_PARAMETER,
    LOGIN_POLICY_SSO_ONLY,
    REAUTH_SESSION_KEY,
    SUPPORTED_LOGIN_POLICIES,
    desired_login_policy,
    emergency_window_active,
    is_sso_only,
)

_BASE_PROFILE_DEFINITIONS = {
    "administrator": {
        "classification": "active",
        "active": True,
        "groups": (
            "base.group_system",
            "account.group_account_manager",
            "hr.group_hr_manager",
            "hr_expense.group_hr_expense_manager",
            "project.group_project_manager",
        ),
        "pocketid": True,
    },
    "collaborator": {
        "classification": "active",
        "active": True,
        "groups": (
            "base.group_user",
            "project.group_project_user",
        ),
        "pocketid": True,
    },
    "break_glass": {
        "classification": "break_glass",
        "active": True,
        "groups": ("base.group_system",),
        "pocketid": False,
    },
    "portal": {
        "classification": "portal",
        "active": True,
        "groups": ("base.group_portal",),
        "pocketid": True,
    },
    "historical": {
        "classification": "historical",
        "active": False,
        "groups": None,
        "pocketid": False,
    },
    "decision": {
        "classification": "decision",
        "active": False,
        "groups": None,
        "pocketid": False,
    },
}
_CONFIGURATION_KEYS = {
    "login",
    "name",
    "email",
    "profile",
    "companies",
    "create_if_missing",
    "optional_if_missing",
    "subject",
    "email_link",
}


class ResUsers(models.Model):
    _inherit = "res.users"

    def _notify_security_setting_update(
        self,
        subject,
        content,
        mail_values=None,
        **kwargs,
    ):
        """Keep governed non-interactive provisioning free of outbound mail.

        Environment provisioning rotates credentials and applies identity
        policy before the target is released. Those changes are recorded in
        the immutable Pocket ID audit log; sending account-security messages
        while provisioning could contact addresses restored from the source.
        """
        if self.env.context.get("usl_governed_identity_provisioning"):
            return self.env["mail.mail"]
        return super()._notify_security_setting_update(
            subject,
            content,
            mail_values=mail_values,
            **kwargs,
        )

    usl_pocketid_access = fields.Boolean(
        string="Pocket ID login enabled",
        copy=False,
        help="Allow an already governed Pocket ID identity to log in as this user.",
    )
    usl_pocketid_email_link = fields.Boolean(
        string="Pre-approve verified email link",
        copy=False,
        help=(
            "Allow first-login linking only when the verified Pocket ID email "
            "uniquely and exactly matches this active governed user."
        ),
    )
    usl_identity_classification = fields.Selection(
        [
            ("active", "Active Pocket ID-managed user"),
            ("historical", "Historical identity; login disabled"),
            ("portal", "Portal or external identity"),
            ("decision", "Obsolete or duplicate; decision required"),
            ("break_glass", "Sealed emergency administrator"),
        ],
        string="Identity classification",
        copy=False,
    )
    usl_local_break_glass = fields.Boolean(
        string="Sealed emergency administrator",
        copy=False,
        help=(
            "Independent administrator accepted only on the audited emergency "
            "route during a short deployment-approved incident window."
        ),
    )
    usl_oidc_identity_ids = fields.One2many(
        "usl.oidc.identity",
        "user_id",
        string="OIDC identities",
    )
    usl_sso_only_login = fields.Boolean(
        string="SSO-only login",
        compute="_compute_usl_sso_only_login",
    )
    usl_pocketid_security_state = fields.Selection(
        [
            ("connected", "Connected"),
            ("ready", "Ready for first sign-in"),
            ("unavailable", "Not enabled"),
        ],
        string="Pocket ID status",
        compute="_compute_usl_pocketid_security_summary",
        compute_sudo=True,
    )
    usl_pocketid_security_email = fields.Char(
        string="Pocket ID email",
        compute="_compute_usl_pocketid_security_summary",
        compute_sudo=True,
    )
    usl_pocketid_last_login_at = fields.Datetime(
        string="Last Pocket ID sign-in",
        compute="_compute_usl_pocketid_security_summary",
        compute_sudo=True,
    )

    def _compute_usl_sso_only_login(self):
        enabled = is_sso_only(self.env)
        for user in self:
            user.usl_sso_only_login = enabled

    @api.depends(
        "email",
        "usl_pocketid_access",
        "usl_oidc_identity_ids.active",
        "usl_oidc_identity_ids.last_email",
        "usl_oidc_identity_ids.last_login_at",
    )
    def _compute_usl_pocketid_security_summary(self):
        for user in self:
            identities = user.usl_oidc_identity_ids.filtered("active").sorted(
                key=lambda identity: identity.last_login_at or identity.linked_at,
                reverse=True,
            )
            identity = identities[:1]
            if identity:
                user.usl_pocketid_security_state = "connected"
            elif user.usl_pocketid_access:
                user.usl_pocketid_security_state = "ready"
            else:
                user.usl_pocketid_security_state = "unavailable"
            user.usl_pocketid_security_email = (
                identity.last_email if identity and identity.last_email else user.email
            )
            user.usl_pocketid_last_login_at = (
                identity.last_login_at if identity else False
            )

    def action_open_pocketid_account(self):
        self.ensure_one()
        if self != self.env.user and not self.env.user.has_group("base.group_system"):
            raise AccessError(_("You can only open Pocket ID for your own account."))
        provider = self.env.ref("usl_pocketid.provider_pocketid").sudo()
        if not provider.enabled or not provider.usl_oidc_issuer:
            raise UserError(_("Pocket ID is not available. Contact an administrator."))
        return {
            "type": "ir.actions.act_url",
            "url": provider.usl_oidc_issuer,
            "target": "new",
        }

    @api.model
    def _usl_pocketid_profile_definitions(self):
        """Return profiles owned by this module.

        Product modules may extend this mapping with roles they own. Keeping
        those XML IDs out of the base SSO module prevents an undeclared reverse
        dependency and preserves independent installation.
        """
        return {
            name: {
                **definition,
                "groups": (
                    tuple(definition["groups"])
                    if definition["groups"] is not None
                    else None
                ),
            }
            for name, definition in _BASE_PROFILE_DEFINITIONS.items()
        }

    @api.constrains(
        "active",
        "usl_identity_classification",
        "usl_pocketid_access",
        "usl_pocketid_email_link",
        "usl_local_break_glass",
        "share",
    )
    def _check_pocketid_user_policy(self):
        for user in self:
            if user.share and user.usl_pocketid_access and (
                user.usl_identity_classification != "portal"
            ):
                raise ValidationError(
                    _("Pocket ID portal users require the portal identity classification."),
                )
            if user.usl_local_break_glass and (
                user.usl_pocketid_access or user.usl_pocketid_email_link
            ):
                raise ValidationError(
                    _("The break-glass administrator must remain independent of Pocket ID."),
                )
            if user.usl_pocketid_email_link and not user.usl_pocketid_access:
                raise ValidationError(
                    _("Verified-email linking requires Pocket ID login access."),
                )
            if (
                user.usl_pocketid_access
                and user.usl_identity_classification not in ("active", "portal")
            ):
                raise ValidationError(
                    _("Pocket ID login requires an active identity classification."),
                )
            if user.usl_local_break_glass != (
                user.usl_identity_classification == "break_glass"
            ):
                raise ValidationError(
                    _("Break-glass classification and local access must match."),
                )
            if (
                user.active
                and user.usl_identity_classification in ("historical", "decision")
            ):
                raise ValidationError(
                    _("Historical and decision-required users must remain archived."),
                )
            if (
                user.active
                and user.usl_local_break_glass
                and self.sudo().search_count(
                    [
                        ("active", "=", True),
                        ("usl_local_break_glass", "=", True),
                    ],
                )
                > 1
            ):
                raise ValidationError(
                    _("Only one active local break-glass administrator is allowed."),
                )

    @api.model
    def _usl_pocketid_configuration_error(self, message, *, login=None):
        if login:
            message = _("%(message)s User login: %(login)s.", message=message, login=login)
        raise ValidationError(message)

    @api.model
    def _usl_pocketid_resolve_companies(self, specification, *, login):
        companies = self.env["res.company"].sudo().with_context(
            active_test=False,
        )
        if specification == "all":
            result = companies.search([("active", "=", True)])
        elif isinstance(specification, list) and specification:
            result = companies.browse()
            for company_name in specification:
                if not isinstance(company_name, str) or not company_name.strip():
                    self._usl_pocketid_configuration_error(
                        _("Company names must be non-empty strings."),
                        login=login,
                    )
                matches = companies.search(
                    [
                        ("name", "=", company_name.strip()),
                        ("active", "=", True),
                    ],
                )
                if len(matches) != 1:
                    self._usl_pocketid_configuration_error(
                        _("Each configured company name must match exactly one company."),
                        login=login,
                    )
                result |= matches
        else:
            self._usl_pocketid_configuration_error(
                _("Active users require companies='all' or a non-empty company list."),
                login=login,
            )
        if not result:
            self._usl_pocketid_configuration_error(
                _("No active allowed company was resolved."),
                login=login,
            )
        return result

    @api.model
    def _usl_pocketid_resolve_groups(self, xmlids, *, login):
        groups = self.env["res.groups"].sudo().browse()
        for xmlid in xmlids or ():
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if not group:
                self._usl_pocketid_configuration_error(
                    _("A required authorization group is not installed: %s") % xmlid,
                    login=login,
                )
            groups |= group
        return groups

    @api.model
    def _usl_pocketid_find_configured_user(self, configuration):
        login = configuration["login"]
        email = configuration.get("email")
        users = self.sudo().with_context(active_test=False)
        login_matches = users.search([("login", "=", login)])
        if len(login_matches) > 1:
            self._usl_pocketid_configuration_error(
                _("The configured login is ambiguous."),
                login=login,
            )
        email_matches = users.browse()
        if email:
            email_matches = users.search([("email", "=ilike", email)]).filtered(
                lambda user: (user.email or "").strip().casefold()
                == email.strip().casefold(),
            )
            if len(email_matches) > 1:
                self._usl_pocketid_configuration_error(
                    _("The configured email is ambiguous."),
                    login=login,
                )
        if login_matches and email_matches and login_matches != email_matches:
            self._usl_pocketid_configuration_error(
                _("The configured login and email resolve to different users."),
                login=login,
            )
        if login_matches and email and not email_matches:
            self._usl_pocketid_configuration_error(
                _("The configured email does not match the user with this login."),
                login=login,
            )
        return login_matches or email_matches

    @api.model
    def _usl_pocketid_prepare_user_configuration(self, configurations):
        if not isinstance(configurations, list) or not configurations:
            raise ValidationError(_("Pocket ID user configuration must be a non-empty list."))
        profile_definitions = self._usl_pocketid_profile_definitions()
        prepared = []
        seen_logins = set()
        seen_subjects = set()
        for configuration in configurations:
            if not isinstance(configuration, dict):
                raise ValidationError(_("Each Pocket ID user entry must be an object."))
            unknown_keys = set(configuration) - _CONFIGURATION_KEYS
            if unknown_keys:
                raise ValidationError(
                    _("Unsupported Pocket ID user keys: %s")
                    % ", ".join(sorted(unknown_keys)),
                )
            login = configuration.get("login")
            profile = configuration.get("profile")
            if not isinstance(login, str) or not login.strip():
                raise ValidationError(_("Each Pocket ID user requires a login."))
            login = login.strip()
            normalized_login = login.casefold()
            if normalized_login in seen_logins:
                self._usl_pocketid_configuration_error(
                    _("The login appears more than once in the configuration."),
                    login=login,
                )
            seen_logins.add(normalized_login)
            if profile not in profile_definitions:
                self._usl_pocketid_configuration_error(
                    _("The configured identity profile is unsupported."),
                    login=login,
                )
            definition = profile_definitions[profile]
            name = configuration.get("name")
            if name is not None and (
                not isinstance(name, str) or not name.strip()
            ):
                self._usl_pocketid_configuration_error(
                    _("The configured user name is invalid."),
                    login=login,
                )
            create_if_missing = configuration.get("create_if_missing", False)
            if not isinstance(create_if_missing, bool):
                self._usl_pocketid_configuration_error(
                    _("create_if_missing must be true or false."),
                    login=login,
                )
            optional_if_missing = configuration.get("optional_if_missing", False)
            if not isinstance(optional_if_missing, bool):
                self._usl_pocketid_configuration_error(
                    _("optional_if_missing must be true or false."),
                    login=login,
                )
            if optional_if_missing and (create_if_missing or definition["active"]):
                self._usl_pocketid_configuration_error(
                    _(
                        "optional_if_missing is only supported for inactive "
                        "historical or decision profiles.",
                    ),
                    login=login,
                )
            email = configuration.get("email")
            if email is not None and (
                not isinstance(email, str)
                or not email.strip()
                or len(email.strip()) > 320
            ):
                self._usl_pocketid_configuration_error(
                    _("The configured email is invalid."),
                    login=login,
                )
            subject = configuration.get("subject")
            if subject is not None and (
                not isinstance(subject, str)
                or not subject.strip()
                or len(subject.strip()) > 255
            ):
                self._usl_pocketid_configuration_error(
                    _("The configured Pocket ID subject is invalid."),
                    login=login,
                )
            if subject:
                subject = subject.strip()
                if subject in seen_subjects:
                    self._usl_pocketid_configuration_error(
                        _("The Pocket ID subject appears more than once."),
                        login=login,
                    )
                seen_subjects.add(subject)
            email_link = configuration.get("email_link", False)
            if not isinstance(email_link, bool):
                self._usl_pocketid_configuration_error(
                    _("email_link must be true or false."),
                    login=login,
                )
            if subject and email_link:
                self._usl_pocketid_configuration_error(
                    _(
                        "Choose either an immutable subject or verified-email "
                        "first-link approval, not both.",
                    ),
                    login=login,
                )
            if definition["pocketid"] and not subject and not email_link:
                self._usl_pocketid_configuration_error(
                    _(
                        "An active Pocket ID user requires an immutable subject "
                        "or explicit verified-email first-link approval.",
                    ),
                    login=login,
                )
            if not definition["pocketid"] and (subject or email_link):
                self._usl_pocketid_configuration_error(
                    _("This non-SSO profile cannot define a Pocket ID link."),
                    login=login,
                )
            if email_link and not email:
                self._usl_pocketid_configuration_error(
                    _("Verified-email first-link approval requires an exact email."),
                    login=login,
                )
            companies = (
                self._usl_pocketid_resolve_companies(
                    configuration.get("companies"),
                    login=login,
                )
                if definition["active"]
                else self.env["res.company"].browse()
            )
            groups = (
                self._usl_pocketid_resolve_groups(
                    definition["groups"],
                    login=login,
                )
                if definition["groups"] is not None
                else self.env["res.groups"].browse()
            )
            normalized = {
                **configuration,
                "login": login,
                "name": name.strip() if name else False,
                "email": email.strip() if email else False,
                "subject": subject or False,
                "profile": profile,
                "definition": definition,
                "companies_recordset": companies,
                "groups_recordset": groups,
            }
            normalized["user_recordset"] = self._usl_pocketid_find_configured_user(
                normalized,
            )
            if not normalized["user_recordset"] and optional_if_missing:
                continue
            if (
                not normalized["user_recordset"]
                and not create_if_missing
            ):
                self._usl_pocketid_configuration_error(
                    _("No existing Odoo user matched and creation was not approved."),
                    login=login,
                )
            if not normalized["user_recordset"] and not definition["active"]:
                self._usl_pocketid_configuration_error(
                    _("Inactive historical and decision profiles cannot create users."),
                    login=login,
                )
            if not normalized["user_recordset"] and (
                not normalized.get("name") or not normalized["email"]
            ):
                self._usl_pocketid_configuration_error(
                    _("Creating a user requires an explicit name and email."),
                    login=login,
                )
            prepared.append(normalized)
        return prepared

    @api.model
    def _usl_pocketid_create_configured_user(self, configuration):
        partners = self.env["res.partner"].sudo().with_context(
            active_test=False,
        ).search(
            [("email", "=ilike", configuration["email"])],
        ).filtered(
            lambda partner: (partner.email or "").strip().casefold()
            == configuration["email"].casefold(),
        )
        if len(partners) > 1:
            self._usl_pocketid_configuration_error(
                _("The configured email matches more than one contact."),
                login=configuration["login"],
            )
        values = {
            "login": configuration["login"],
            "name": configuration["name"],
            "email": configuration["email"],
            "active": True,
            "company_id": configuration["companies_recordset"][0].id,
            "company_ids": [Command.set(configuration["companies_recordset"].ids)],
            "group_ids": [Command.set(configuration["groups_recordset"].ids)],
            "password": secrets.token_urlsafe(48),
        }
        if partners:
            values["partner_id"] = partners.id
        return self.sudo().with_context(no_reset_password=True).create(values)

    @api.model
    def _usl_pocketid_apply_identity_configuration(
        self,
        *,
        provider,
        user,
        configuration,
    ):
        definition = configuration["definition"]
        identities = self.env["usl.oidc.identity"].with_context(
            active_test=False,
        ).search([("user_id", "=", user.id)])
        if not definition["pocketid"]:
            identities.filtered("active").write({"active": False})
            user.write(
                {
                    "oauth_provider_id": False,
                    "oauth_uid": False,
                    "oauth_access_token": False,
                },
            )
            return
        subject = configuration["subject"]
        if subject:
            subject_matches = self.env["usl.oidc.identity"].with_context(
                active_test=False,
            ).search(
                [
                    ("issuer", "=", provider.usl_oidc_issuer),
                    ("subject", "=", subject),
                ],
            )
            if subject_matches and subject_matches.user_id != user:
                self._usl_pocketid_configuration_error(
                    _("The Pocket ID subject is already linked to another user."),
                    login=configuration["login"],
                )
            user_issuer_identities = identities.filtered(
                lambda identity: identity.issuer == provider.usl_oidc_issuer,
            )
            if user_issuer_identities and user_issuer_identities != subject_matches:
                self._usl_pocketid_configuration_error(
                    _("The user already has a different identity for this issuer."),
                    login=configuration["login"],
                )
            if subject_matches:
                if not subject_matches.active:
                    subject_matches.write({"active": True})
            else:
                self.env["usl.oidc.identity"].with_context(
                    mail_auto_subscribe_no_notify=True,
                ).create(
                    {
                        "issuer": provider.usl_oidc_issuer,
                        "subject": subject,
                        "provider_id": provider.id,
                        "user_id": user.id,
                        "link_method": "explicit",
                    },
                )
        elif not provider.usl_allow_unique_email_link:
            self._usl_pocketid_configuration_error(
                _("Verified-email linking is not enabled on the Pocket ID provider."),
                login=configuration["login"],
            )

    @api.model
    def _usl_pocketid_apply_user_configuration(
        self,
        configurations,
        *,
        break_glass_password,
        strict=True,
    ):
        prepared = self._usl_pocketid_prepare_user_configuration(configurations)
        provider = self.env.ref("usl_pocketid.provider_pocketid").sudo()
        if not provider.enabled or not provider.usl_oidc_issuer:
            raise ValidationError(
                _("Pocket ID environment configuration must be applied first."),
            )
        if not isinstance(break_glass_password, str) or len(break_glass_password) < 20:
            raise ValidationError(
                _("The local break-glass password must contain at least 20 characters."),
            )
        configured_users = self.sudo().browse()
        summary = []
        for configuration in prepared:
            definition = configuration["definition"]
            user = configuration["user_recordset"]
            created = not user
            if not user:
                user = self._usl_pocketid_create_configured_user(configuration)
            was_pocketid_managed = user.usl_pocketid_access
            desired_values = {
                "active": definition["active"],
                "usl_identity_classification": definition["classification"],
                "usl_pocketid_access": definition["pocketid"],
                "usl_pocketid_email_link": bool(
                    definition["pocketid"] and configuration.get("email_link"),
                ),
                "usl_local_break_glass": configuration["profile"] == "break_glass",
            }
            values = {
                field_name: value
                for field_name, value in desired_values.items()
                if user[field_name] != value
            }
            if definition["active"]:
                companies = configuration["companies_recordset"]
                groups = configuration["groups_recordset"]
                if user.company_id != companies[0]:
                    values["company_id"] = companies[0].id
                if set(user.company_ids.ids) != set(companies.ids):
                    values["company_ids"] = [Command.set(companies.ids)]
                if set(user.group_ids.ids) != set(groups.ids):
                    values["group_ids"] = [Command.set(groups.ids)]
            if definition["pocketid"] and (
                created or not was_pocketid_managed
            ):
                values["password"] = secrets.token_urlsafe(48)
            elif configuration["profile"] == "break_glass":
                values["password"] = break_glass_password
            provisioned_user = user.with_context(
                usl_governed_identity_provisioning=True,
                mail_auto_subscribe_no_notify=True,
            )
            if values:
                provisioned_user.write(values)
            provisioned_user._usl_pocketid_apply_identity_configuration(
                provider=provider,
                user=provisioned_user,
                configuration=configuration,
            )
            configured_users |= user
            self.env["usl.oidc.audit.event"]._record(
                event_type="user_policy",
                reason_code=configuration["profile"],
                provider_id=provider.id,
                user_id=user.id,
            )
            summary.append(
                {
                    "login": user.login,
                    "profile": configuration["profile"],
                    "created": created,
                    "active": user.active,
                },
            )

        protected = self.browse()
        for xmlid in (
            "base.user_root",
            "base.public_user",
            "base.template_portal_user_id",
        ):
            protected_user = self.env.ref(xmlid, raise_if_not_found=False)
            if protected_user:
                protected |= protected_user
        unconfigured = self.sudo().with_context(active_test=False).search(
            [
                ("id", "not in", (configured_users | protected).ids),
            ],
        )
        if strict and unconfigured:
            logins = ", ".join(sorted(unconfigured.mapped("login")))
            raise ValidationError(
                _("Every non-system Odoo user must be explicitly classified: %s")
                % logins,
            )
        break_glass_users = self.sudo().with_context(active_test=False).search(
            [
                ("active", "=", True),
                ("usl_local_break_glass", "=", True),
            ],
        )
        if len(break_glass_users) != 1:
            raise ValidationError(
                _("Exactly one active local break-glass administrator is required."),
            )
        return {
            "configured_count": len(configured_users),
            "break_glass_login": break_glass_users.login,
            "users": summary,
        }

    @api.model
    def _usl_pocketid_policy_exempt_users(self):
        users = self.browse()
        for xmlid in (
            "base.user_root",
            "base.public_user",
            "base.template_portal_user_id",
        ):
            user = self.env.ref(xmlid, raise_if_not_found=False)
            if user:
                users |= user
        return users

    @api.model
    def _usl_pocketid_validate_sso_only(self):
        provider = self.env.ref("usl_pocketid.provider_pocketid").sudo()
        if not provider.enabled or not provider.usl_oidc_issuer:
            raise ValidationError(
                _("Pocket ID must be completely configured before SSO-only login."),
            )
        other_providers = self.env["auth.oauth.provider"].sudo().search(
            [
                ("id", "!=", provider.id),
                ("enabled", "=", True),
            ],
        )
        if other_providers:
            raise ValidationError(
                _("Pocket ID must be the only enabled interactive login provider."),
            )
        exempt = self._usl_pocketid_policy_exempt_users()
        users = self.sudo().with_context(active_test=False).search(
            [
                ("active", "=", True),
                ("id", "not in", exempt.ids),
            ],
        )
        break_glass = users.filtered("usl_local_break_glass")
        if len(break_glass) != 1:
            raise ValidationError(
                _("SSO-only login requires exactly one sealed emergency administrator."),
            )
        ungoverned = users - break_glass
        invalid = ungoverned.filtered(
            lambda user: not user.usl_pocketid_access
            or user.usl_identity_classification not in ("active", "portal")
            or (
                not user.usl_pocketid_email_link
                and not user.usl_oidc_identity_ids.filtered("active")
            ),
        )
        if invalid:
            raise ValidationError(
                _("Every active interactive user must have governed Pocket ID access: %s")
                % ", ".join(sorted(invalid.mapped("login"))),
            )
        return users, break_glass

    @api.model
    def _usl_pocketid_apply_login_policy(self):
        policy = desired_login_policy()
        if policy not in SUPPORTED_LOGIN_POLICIES:
            raise ValidationError(
                _("Unsupported Pocket ID login policy: %s") % policy,
            )
        parameters = self.env["ir.config_parameter"].sudo()
        previous = parameters.get_str(LOGIN_POLICY_PARAMETER, "standard")
        if policy == LOGIN_POLICY_SSO_ONLY:
            users, break_glass = self._usl_pocketid_validate_sso_only()
            if previous != policy:
                for user in users - break_glass:
                    user.with_context(
                        no_reset_password=True,
                        usl_governed_identity_provisioning=True,
                    ).write(
                        {"password": secrets.token_urlsafe(48)},
                    )
            parameters.set_bool("auth_signup.reset_password", False)
            parameters.set_str(LOGIN_POLICY_PARAMETER, policy)
        else:
            parameters.set_str(LOGIN_POLICY_PARAMETER, policy)
        if previous != policy:
            self.env["usl.oidc.audit.event"]._record(
                event_type="configuration",
                reason_code=f"login_policy_{policy}",
            )
        return policy

    @api.model
    def _usl_pocketid_resolve_user(self, provider, claims):
        issuer = claims["iss"]
        subject = claims["sub"]
        identities = self.env["usl.oidc.identity"].sudo().with_context(
            active_test=False,
        ).search(
            [
                ("issuer", "=", issuer),
                ("subject", "=", subject),
            ],
        )
        if len(identities) > 1:
            raise PocketIDAccessDenied(PocketIDReason.IDENTITY_CONFLICT)
        identity = identities[:1]
        if not identity:
            identity = self._usl_pocketid_match_verified_email(
                provider,
                claims,
            )
        if not identity.active:
            raise PocketIDAccessDenied(PocketIDReason.IDENTITY_DISABLED)
        user = identity.user_id.sudo()
        if (
            not user.active
            or not user.usl_pocketid_access
            or user.usl_local_break_glass
            or (
                user.share
                and user.usl_identity_classification != "portal"
            )
        ):
            raise PocketIDAccessDenied(PocketIDReason.USER_DISABLED)
        return user, identity

    @api.model
    def _usl_pocketid_match_verified_email(self, provider, claims):
        if not provider.usl_allow_unique_email_link:
            raise PocketIDAccessDenied(PocketIDReason.IDENTITY_UNLINKED)
        email = claims.get("email")
        if (
            claims.get("email_verified") is not True
            or not isinstance(email, str)
            or not email.strip()
            or len(email) > 320
        ):
            raise PocketIDAccessDenied(PocketIDReason.IDENTITY_UNLINKED)
        email = email.strip()
        candidates = self.sudo().search(
            [
                ("active", "=", True),
                ("usl_pocketid_access", "=", True),
                ("usl_pocketid_email_link", "=", True),
                ("usl_identity_classification", "in", ("active", "portal")),
                ("email", "=ilike", email),
            ],
        ).filtered(lambda user: (user.email or "").strip().casefold() == email.casefold())
        if len(candidates) != 1:
            reason = (
                PocketIDReason.IDENTITY_CONFLICT
                if candidates
                else PocketIDReason.IDENTITY_UNLINKED
            )
            raise PocketIDAccessDenied(reason)
        user = candidates
        if user.usl_oidc_identity_ids.filtered(
            lambda identity: identity.issuer == provider.usl_oidc_issuer,
        ):
            raise PocketIDAccessDenied(PocketIDReason.IDENTITY_CONFLICT)
        # The verified OIDC callback is a bounded internal identity-linking
        # service, not an administrator editing a security binding over RPC.
        # Use Odoo's internal service identity so the distribution's generic
        # irreversible-action guard remains enforced for direct model writes.
        return self.env["usl.oidc.identity"].with_user(SUPERUSER_ID).create(
            {
                "issuer": provider.usl_oidc_issuer,
                "subject": claims["sub"],
                "provider_id": provider.id,
                "user_id": user.id,
                "link_method": "verified_unique_email",
                "last_email": email,
                "last_display_name": claims.get("name"),
            },
        )

    @api.model
    def _usl_pocketid_login(self, provider, claims, access_token):
        user, identity = self._usl_pocketid_resolve_user(provider, claims)
        if (
            user.oauth_provider_id
            and (
                user.oauth_provider_id != provider
                or user.oauth_uid != claims["sub"]
            )
        ):
            raise PocketIDAccessDenied(PocketIDReason.IDENTITY_CONFLICT)
        user.write(
            {
                "oauth_provider_id": provider.id,
                "oauth_uid": claims["sub"],
                "oauth_access_token": access_token,
            },
        )
        # Claims have already passed issuer, audience, nonce, group and subject
        # validation. Persist only bounded login metadata as the internal
        # service; direct identity administration stays protected.
        identity.with_user(SUPERUSER_ID).write(
            {
                "last_login_at": fields.Datetime.now(),
                "last_email": claims.get("email"),
                "last_display_name": claims.get("name"),
            },
        )
        return self.env.cr.dbname, user.login, access_token, identity

    def _check_credentials(self, credential, env):
        self.ensure_one()
        pocketid_provider = self.env.ref(
            "usl_pocketid.provider_pocketid",
            raise_if_not_found=False,
        ).sudo()
        sso_only = is_sso_only(self.env)
        is_pocketid_governed = (
            self.usl_pocketid_access
            or self.usl_identity_classification == "active"
            or self.oauth_provider_id == pocketid_provider
        )
        is_pocketid_oauth = (
            credential.get("type") == "oauth_token"
            and self.usl_pocketid_access
            and pocketid_provider.enabled
            and self.oauth_provider_id == pocketid_provider
        )
        if sso_only and credential.get("type") == "usl_pocketid":
            proof = request and request.session.get(REAUTH_SESSION_KEY)
            if (
                not proof
                or proof.get("uid") != self.id
                or proof.get("expires_at", 0) < time.time()
            ):
                raise AccessDenied()
            request.session.pop(REAUTH_SESSION_KEY, None)
            return {
                "uid": self.id,
                "auth_method": "usl_pocketid",
                "mfa": "skip",
            }
        noninteractive_api_attempt = (
            sso_only
            and credential.get("type") == "password"
            and not env.get("interactive", True)
        )
        if (
            sso_only
            and credential.get("type") == "oauth_token"
            and not is_pocketid_oauth
        ):
            raise AccessDenied()
        if sso_only and credential.get("type") not in (
            "oauth_token",
            "password",
            "usl_pocketid",
        ):
            raise AccessDenied()
        if (
            sso_only
            and credential.get("type") == "password"
            and not noninteractive_api_attempt
        ):
            emergency = request and request.session.get(EMERGENCY_SESSION_KEY)
            if not (
                env.get("interactive", True)
                and self.usl_local_break_glass
                and emergency
                and emergency.get("uid") == self.id
                and emergency_window_active()
            ):
                raise AccessDenied()
        if (
            is_pocketid_governed
            and not is_pocketid_oauth
            and not noninteractive_api_attempt
        ):
            raise AccessDenied()
        result = super()._check_credentials(credential, env)
        if sso_only and not env.get("interactive", True):
            if result.get("auth_method") != "apikey":
                raise AccessDenied()
        return result

    def _rpc_api_keys_only(self):
        return is_sso_only(self.env) or super()._rpc_api_keys_only()

    def _get_auth_methods(self):
        self.ensure_one()
        if not is_sso_only(self.env):
            return super()._get_auth_methods()
        if self.usl_local_break_glass:
            if (
                request
                and request.session.get(EMERGENCY_SESSION_KEY)
                and emergency_window_active()
            ):
                return ["password"]
            return []
        return ["usl_pocketid"] if self.usl_pocketid_access else []

    def action_create_passkey(self):
        pocketid_provider = self.env.ref(
            "usl_pocketid.provider_pocketid",
            raise_if_not_found=False,
        ).sudo()
        if is_sso_only(self.env) or any(
            user.usl_pocketid_access
            or user.usl_identity_classification == "active"
            or user.oauth_provider_id == pocketid_provider
            for user in self
        ):
            raise ValidationError(
                _(
                    "Pocket ID-managed users must register and use passkeys "
                    "in Pocket ID, not in Odoo.",
                ),
            )
        return super().action_create_passkey()

    def action_reset_password(self):
        if is_sso_only(self.env):
            raise UserError(
                _("Passwords are disabled. Manage this user's Pocket ID access instead."),
            )
        return super().action_reset_password()

    def preference_change_password(self):
        if is_sso_only(self.env):
            raise UserError(_("Passwords are disabled by the SSO-only login policy."))
        return super().preference_change_password()

    def action_change_password_wizard(self):
        if is_sso_only(self.env):
            raise UserError(_("Passwords are disabled by the SSO-only login policy."))
        return super().action_change_password_wizard()

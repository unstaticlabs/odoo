import secrets

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessDenied, ValidationError

from ..exceptions import PocketIDAccessDenied, PocketIDReason

_PROFILE_DEFINITIONS = {
    "administrator": {
        "classification": "active",
        "active": True,
        "groups": (
            "base.group_system",
            "account.group_account_manager",
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
    "accountant_reviewer": {
        "classification": "active",
        "active": True,
        "groups": (
            "base.group_user",
            "rebuild_account_migration.group_rebuild_accountant_reviewer",
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
        "pocketid": False,
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
    "subject",
    "email_link",
}


class ResUsers(models.Model):
    _inherit = "res.users"

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
            "uniquely and exactly matches this active internal user."
        ),
    )
    usl_identity_classification = fields.Selection(
        [
            ("active", "Active Pocket ID-managed user"),
            ("historical", "Historical identity; login disabled"),
            ("portal", "Portal or external identity"),
            ("decision", "Obsolete or duplicate; decision required"),
            ("break_glass", "Local break-glass administrator"),
        ],
        string="Identity classification",
        copy=False,
    )
    usl_local_break_glass = fields.Boolean(
        string="Local break-glass administrator",
        copy=False,
        help="Emergency local administrator independent of Pocket ID.",
    )
    usl_oidc_identity_ids = fields.One2many(
        "usl.oidc.identity",
        "user_id",
        string="OIDC identities",
    )

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
            if user.share and (
                user.usl_pocketid_access or user.usl_pocketid_email_link
            ):
                raise ValidationError(
                    _("Pocket ID internal SSO cannot be enabled for portal users."),
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
                and user.usl_identity_classification != "active"
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
            if profile not in _PROFILE_DEFINITIONS:
                self._usl_pocketid_configuration_error(
                    _("The configured identity profile is unsupported."),
                    login=login,
                )
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
            definition = _PROFILE_DEFINITIONS[profile]
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
                self.env["usl.oidc.identity"].create(
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
            values = {
                "active": definition["active"],
                "usl_identity_classification": definition["classification"],
                "usl_pocketid_access": definition["pocketid"],
                "usl_pocketid_email_link": bool(
                    definition["pocketid"] and configuration.get("email_link"),
                ),
                "usl_local_break_glass": configuration["profile"] == "break_glass",
            }
            if definition["active"]:
                values.update(
                    {
                        "company_id": configuration["companies_recordset"][0].id,
                        "company_ids": [
                            Command.set(configuration["companies_recordset"].ids),
                        ],
                        "group_ids": [
                            Command.set(configuration["groups_recordset"].ids),
                        ],
                    },
                )
            if definition["pocketid"] and (
                created or not was_pocketid_managed
            ):
                values["password"] = secrets.token_urlsafe(48)
            elif configuration["profile"] == "break_glass":
                values["password"] = break_glass_password
            user.write(values)
            self._usl_pocketid_apply_identity_configuration(
                provider=provider,
                user=user,
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
            or user.share
            or not user.usl_pocketid_access
            or user.usl_local_break_glass
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
                ("share", "=", False),
                ("usl_pocketid_access", "=", True),
                ("usl_pocketid_email_link", "=", True),
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
        return self.env["usl.oidc.identity"].sudo().create(
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
        identity.sudo().write(
            {
                "last_login_at": fields.Datetime.now(),
                "last_email": claims.get("email"),
                "last_display_name": claims.get("name"),
            },
        )
        return self.env.cr.dbname, user.login, access_token, identity

    def _check_credentials(self, credential, env):
        self.ensure_one()
        if (
            credential.get("type") == "password"
            and self.usl_pocketid_access
            and not self.usl_local_break_glass
        ):
            raise AccessDenied()
        return super()._check_credentials(credential, env)

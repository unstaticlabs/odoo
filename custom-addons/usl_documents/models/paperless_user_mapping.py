"""Mapping between Odoo users and Paperless accounts."""

import os

from odoo import (
    _,
    api,
    fields,
    models,
)
from odoo.exceptions import AccessError, UserError

from .paperless_client import PaperlessError


class UslPaperlessUserMapping(models.Model):
    _name = "usl.paperless.user.mapping"
    _description = "Odoo to Paperless Individual Identity"
    _order = "user_id"

    user_id = fields.Many2one(
        "res.users", required=True, index=True, ondelete="cascade",
    )
    paperless_user_id = fields.Integer(required=True, index=True)
    paperless_username = fields.Char(required=True)
    oidc_identity_id = fields.Many2one(
        "usl.oidc.identity",
        string="Pocket ID identity",
        ondelete="restrict",
        groups="base.group_system",
        help=(
            "Immutable Pocket ID identity used by this Odoo user. Paperless "
            "still keeps its own numeric user identity for object permissions."
        ),
    )
    oidc_subject_fingerprint = fields.Char(
        related="oidc_identity_id.subject_fingerprint",
        string="Pocket identity",
        readonly=True,
    )
    qa_local_identity = fields.Boolean(
        string="QA local login",
        groups="base.group_system",
        help=(
            "Explicit exception for the isolated QA environment's documented "
            "username/admin test accounts. It is ignored everywhere else."
        ),
    )
    sync_state = fields.Selection(
        [
            ("pending", "Pending verification"),
            ("synchronized", "Verified"),
            ("failed", "Failed"),
        ],
        required=True,
        default="pending",
    )
    last_verified_at = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)
    active = fields.Boolean(default=True)

    _odoo_user_unique = models.Constraint(
        "UNIQUE(user_id)", "An Odoo user may have only one Paperless identity.",
    )
    _paperless_user_unique = models.Constraint(
        "UNIQUE(paperless_user_id)",
        "A Paperless identity may be mapped to only one Odoo user.",
    )

    def _mapped_user_documents(self):
        self.ensure_one()
        visible = self.user_id._documents_visible_for_permission_sync()
        return self.env["usl.document"].browse(visible[self.user_id.id])

    @api.model
    def _pocket_provider(self):
        return self.env.ref(
            "usl_pocketid.provider_pocketid",
            raise_if_not_found=False,
        ).sudo()

    def _identity_error(self):
        self.ensure_one()
        provider = self._pocket_provider()
        if not provider or not provider.enabled:
            return False
        if (
            self.qa_local_identity
            and os.getenv("USL_DEPLOYMENT_ENV", "").strip() == "qa"
        ):
            return False
        identity = self.oidc_identity_id.sudo()
        if not identity:
            return _(
                "Link this user to their governed Pocket ID identity before "
                "granting direct Paperless access.",
            )
        if (
            not identity.active
            or identity.provider_id != provider
            or identity.issuer != provider.usl_oidc_issuer
            or identity.user_id != self.user_id
            or not self.user_id.active
            or not self.user_id.usl_pocketid_access
        ):
            return _(
                "The Pocket ID identity is disabled, mismatched, or no longer "
                "authorized for this Odoo user.",
            )
        return False

    def _identity_is_safe(self):
        self.ensure_one()
        return not self._identity_error()

    def _remote_identity_error(self, payload):
        self.ensure_one()
        if not payload:
            return _(
                "Paperless user %(id)s no longer exists or is not visible.",
                id=self.paperless_user_id,
            )
        remote_username = payload.get("username")
        if remote_username != self.paperless_username:
            return _(
                "Paperless user %(id)s is %(actual)s, not %(expected)s.",
                id=self.paperless_user_id,
                actual=remote_username or _("unnamed"),
                expected=self.paperless_username,
            )
        if payload.get("is_active") is not True:
            return _(
                "Paperless user %(id)s (%(username)s) is inactive. Run the "
                "governed Paperless user reconciliation before verifying it again.",
                id=self.paperless_user_id,
                username=self.paperless_username,
            )
        return False

    @api.model
    def _reconcile_remote_identity_state(self, *, client=None):
        """Fail closed when a previously verified Paperless user drifts."""
        mappings = self.sudo().search(
            [("active", "=", True), ("sync_state", "=", "synchronized")],
        )
        if not mappings:
            return 0
        client = client or self.env["usl.document"]._paperless()
        remote_users = {
            int(payload["id"]): payload
            for payload in client.list_users()
            if isinstance(payload, dict) and payload.get("id") is not None
        }
        failures = 0
        for mapping in mappings:
            message = mapping._remote_identity_error(
                remote_users.get(mapping.paperless_user_id),
            )
            if not message:
                continue
            mapping.with_context(
                usl_documents_mapping_verification=True,
            ).write(
                {
                    "sync_state": "failed",
                    "last_error": message,
                },
            )
            failures += 1
        return failures

    @api.model_create_multi
    def create(self, values_list):
        trusted_seed = (
            self.env.context.get("usl_documents_mapping_no_sync")
            and self.env.su
        )
        normalized = []
        for values in values_list:
            values = dict(values)
            if values.get("qa_local_identity") and (
                not trusted_seed
                or os.getenv("USL_DEPLOYMENT_ENV", "").strip() != "qa"
            ):
                raise AccessError(
                    _("Local Paperless identities are allowed only in isolated QA."),
                )
            if values.get("user_id") and not values.get("oidc_identity_id"):
                provider = self._pocket_provider()
                if provider and provider.enabled:
                    identities = self.env["usl.oidc.identity"].sudo().search(
                        [
                            ("user_id", "=", values["user_id"]),
                            ("issuer", "=", provider.usl_oidc_issuer),
                            ("active", "=", True),
                        ],
                        limit=2,
                    )
                    if len(identities) == 1:
                        values["oidc_identity_id"] = identities.id
            if not trusted_seed:
                values.update(
                    {
                        "sync_state": "pending",
                        "last_verified_at": False,
                        "last_error": False,
                    },
                )
            normalized.append(values)
        return super().create(normalized)

    def write(self, values):
        values = dict(values)
        trusted_seed = (
            self.env.context.get("usl_documents_mapping_no_sync")
            and self.env.su
        )
        verified_write = (
            self.env.context.get("usl_documents_mapping_verification")
            and self.env.su
        )
        if values.get("qa_local_identity") and (
            not trusted_seed
            or os.getenv("USL_DEPLOYMENT_ENV", "").strip() != "qa"
        ):
            raise AccessError(
                _("Local Paperless identities are allowed only in isolated QA."),
            )
        protected_fields = {"sync_state", "last_verified_at", "last_error"}
        if (
            protected_fields.intersection(values)
            and not (trusted_seed or verified_write)
        ):
            raise AccessError(
                _("Paperless verification state can only be changed by verification."),
            )
        identity_fields = {
            "user_id",
            "paperless_user_id",
            "paperless_username",
            "oidc_identity_id",
            "qa_local_identity",
        }
        if identity_fields.intersection(values) and not (trusted_seed or verified_write):
            values.update(
                {
                    "sync_state": "pending",
                    "last_verified_at": False,
                    "last_error": False,
                },
            )
        sync_fields = {
            "user_id",
            "paperless_user_id",
            "paperless_username",
            "oidc_identity_id",
            "qa_local_identity",
            "sync_state",
            "active",
        }
        effective_sync_fields = {
            field_name
            for field_name in sync_fields.intersection(values)
            if any(
                (
                    mapping[field_name].id
                    if mapping._fields[field_name].type == "many2one"
                    else mapping[field_name]
                )
                != values[field_name]
                for mapping in self
            )
        }
        if (
            not effective_sync_fields
            or trusted_seed
        ):
            return super().write(values)
        before_documents = {
            mapping.id: mapping._mapped_user_documents()
            for mapping in self
            if mapping.active and mapping.sync_state == "synchronized"
        }
        revoking = any(
            mapping.id in before_documents
            and (
                values.get("active", mapping.active) is False
                or values.get("sync_state", mapping.sync_state) != "synchronized"
                or "user_id" in effective_sync_fields
                or "paperless_user_id" in effective_sync_fields
                or "oidc_identity_id" in effective_sync_fields
                or "qa_local_identity" in effective_sync_fields
            )
            for mapping in self
        )
        result = super().write(values)
        documents = self.env["usl.document"].browse(
            list(
                set().union(
                    *(set(items.ids) for items in before_documents.values()),
                    *(
                        set(mapping._mapped_user_documents().ids)
                        for mapping in self
                        if mapping.active
                        and mapping.sync_state == "synchronized"
                    ),
                ),
            ),
        )
        if documents:
            documents.with_user(
                self.env.ref("base.user_root"),
            ).action_sync_permissions()
        if revoking and documents.filtered(
            lambda document: document.permission_sync_state == "failed",
        ):
            raise UserError(
                _(
                    "The identity change was not saved because Paperless could "
                    "not safely revoke existing document permissions.",
                ),
            )
        return result

    def unlink(self):
        documents = self.env["usl.document"].browse(
            list(
                set().union(
                    *(
                        set(mapping._mapped_user_documents().ids)
                        for mapping in self
                        if mapping.active
                        and mapping.sync_state == "synchronized"
                    ),
                )
                if self
                else set(),
            ),
        )
        result = super().unlink()
        if documents:
            documents.with_user(
                self.env.ref("base.user_root"),
            ).action_sync_permissions()
            if documents.filtered(
                lambda document: document.permission_sync_state == "failed",
            ):
                raise UserError(
                    _(
                        "The identity was not removed because Paperless could "
                        "not safely revoke existing document permissions.",
                    ),
                )
        return result

    def action_mark_verified(self):
        if not self.env.user.has_group(
            "usl_documents.group_documents_manager",
        ):
            raise AccessError(_("Only Documents administrators verify identities."))
        errors = []
        for mapping in self:
            identity_error = mapping._identity_error()
            if identity_error:
                mapping.sudo().with_context(
                    usl_documents_mapping_verification=True,
                ).write(
                    {
                        "sync_state": "failed",
                        "last_error": identity_error,
                    },
                )
                errors.append(
                    _("%(identity)s: %(error)s")
                    % {
                        "identity": mapping.display_name,
                        "error": identity_error,
                    },
                )
                continue
            try:
                payload = self.env["usl.document"]._paperless().get_user(
                    mapping.paperless_user_id,
                )
            except PaperlessError as error:
                message = str(error)
                mapping.sudo().with_context(
                    usl_documents_mapping_verification=True,
                ).write(
                    {
                        "sync_state": "failed",
                        "last_error": message,
                    },
                )
                errors.append(
                    _("%(identity)s: %(error)s")
                    % {"identity": mapping.display_name, "error": message},
                )
                continue
            message = mapping._remote_identity_error(payload)
            if message:
                mapping.sudo().with_context(
                    usl_documents_mapping_verification=True,
                ).write(
                    {
                        "sync_state": "failed",
                        "last_error": message,
                    },
                )
                errors.append(
                    _("%(identity)s: %(error)s")
                    % {"identity": mapping.display_name, "error": message},
                )
                continue
            mapping.sudo().with_context(
                usl_documents_mapping_verification=True,
            ).write(
                {
                    "sync_state": "synchronized",
                    "last_verified_at": fields.Datetime.now(),
                    "last_error": False,
                },
            )
        if errors:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Identity verification needs attention"),
                    "message": "\n".join(errors),
                    "type": "danger",
                    "sticky": True,
                    "next": {"type": "ir.actions.client", "tag": "reload"},
                },
            }
        return True

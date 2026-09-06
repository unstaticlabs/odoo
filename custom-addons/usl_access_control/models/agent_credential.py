"""Governed Agent API credentials, key and transfer wizards and API-key enforcement."""

import datetime

from psycopg2 import errors as pgerrors

from odoo import (
    SUPERUSER_ID,
    _,
    api,
    fields,
    models,
)
from odoo.exceptions import (
    AccessDenied,
    AccessError,
    UserError,
    ValidationError,
)
from odoo.http import request

from ..exceptions import AgentAuthenticationError, AgentPolicyAccessError
from .agent import (
    _AGENT_CREDENTIAL_TOUCH_LOCK_NAMESPACE,
    _AGENT_KEY_MAX_DAYS,
    _agent_key_path_allowed,
)
from odoo.addons.base.models.res_users import KEY_CRYPT_CONTEXT, check_identity


class UslAgentCredential(models.Model):
    _name = "usl.agent.credential"
    _description = "Agent API Credential"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, readonly=True)
    agent_id = fields.Many2one("usl.agent", required=True, readonly=True, ondelete="cascade", index=True)
    native_key_id = fields.Integer(required=True, readonly=True, copy=False, index=True)
    expiration_date = fields.Datetime(required=True, readonly=True)
    last_used_at = fields.Datetime(readonly=True, copy=False)
    revoked_at = fields.Datetime(readonly=True, copy=False)
    revoked_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    status = fields.Selection(
        [("active", "Active"), ("expired", "Expired"), ("revoked", "Revoked")],
        compute="_compute_status",
        search="_search_status",
    )

    _native_key_unique = models.Constraint(
        "UNIQUE(native_key_id)",
        "A native API key can belong to only one Agent credential.",
    )

    @api.depends("expiration_date", "revoked_at")
    def _compute_status(self):
        now = fields.Datetime.now()
        for credential in self:
            credential.status = (
                "revoked"
                if credential.revoked_at
                else "expired" if credential.expiration_date <= now else "active"
            )

    def _search_status(self, operator, value):
        if operator not in ("=", "!="):
            raise NotImplementedError()
        now = fields.Datetime.now()
        domains = {
            "revoked": [("revoked_at", "!=", False)],
            "expired": [("revoked_at", "=", False), ("expiration_date", "<=", now)],
            "active": [("revoked_at", "=", False), ("expiration_date", ">", now)],
        }
        matching_ids = self.sudo().search(domains[value]).ids
        return [("id", "in" if operator == "=" else "not in", matching_ids)]

    def _touch_last_used_at(self, used_at):
        """Best-effort usage telemetry that cannot contend with Agent calls."""
        self.ensure_one()
        cutoff = used_at - datetime.timedelta(minutes=1)
        if self.last_used_at and self.last_used_at >= cutoff:
            return False
        self.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock(%s, %s)",
            [_AGENT_CREDENTIAL_TOUCH_LOCK_NAMESPACE, self.id],
        )
        if not self.env.cr.fetchone()[0]:
            return False
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute(
                    """
                    WITH candidate AS (
                        SELECT id FROM usl_agent_credential
                         WHERE id = %s
                           AND (last_used_at IS NULL OR last_used_at < %s)
                         FOR UPDATE SKIP LOCKED
                    )
                    UPDATE usl_agent_credential
                       SET last_used_at = %s,
                           write_date = %s,
                           write_uid = %s
                     WHERE id IN (SELECT id FROM candidate)
                    """,
                    [self.id, cutoff, used_at, used_at, SUPERUSER_ID],
                )
                updated = bool(self.env.cr.rowcount)
        except pgerrors.SerializationFailure:
            # A peer may have committed after this request's repeatable-read
            # snapshot. Usage telemetry must not restart the business call.
            return False
        self.invalidate_recordset(["last_used_at", "write_date", "write_uid"], flush=False)
        return updated

    def _check_caller_can_manage(self):
        self.mapped("agent_id")._check_caller_can_manage()

    @check_identity
    def action_revoke(self):
        self.ensure_one()
        self._check_caller_can_manage()
        if self.revoked_at:
            return True
        self.env["res.users.apikeys"].with_user(SUPERUSER_ID).browse(self.native_key_id)._remove()
        self.sudo().with_context(usl_agent_credential_internal=True).write(
            {"revoked_at": fields.Datetime.now(), "revoked_by_id": self.env.uid},
        )
        return True

    def action_create_replacement(self):
        self.ensure_one()
        self._check_caller_can_manage()
        return {
            "type": "ir.actions.act_window",
            "name": _("Create replacement API key"),
            "res_model": "usl.agent.key.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_agent_id": self.agent_id.id,
                "default_name": _("Replacement for %(name)s", name=self.name),
                "default_duration": "365",
            },
        }

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("usl_agent_credential_internal"):
            raise AccessError(_("Agent credentials can be created only through the protected key action."))
        return super().create(vals_list)

    def write(self, values):
        if not self.env.context.get("usl_agent_credential_internal"):
            raise AccessError(_("Agent credential metadata is managed by the security service."))
        return super().write(values)

    def unlink(self):
        raise UserError(_("Revoke Agent credentials instead of deleting their audit history."))


class UslAgentKeyWizard(models.TransientModel):
    _name = "usl.agent.key.wizard"
    _description = "Create Agent API Key"

    agent_id = fields.Many2one("usl.agent", required=True, readonly=True)
    name = fields.Char(required=True, default=lambda self: _("MCP integration"))
    duration = fields.Selection(
        [("90", "90 days"), ("365", "1 year"), ("1826", "5 years"), ("custom", "Custom date")],
        required=True,
        default="365",
    )
    expiration_date = fields.Datetime(compute="_compute_expiration_date", store=True, readonly=False)

    @api.depends("duration")
    def _compute_expiration_date(self):
        for wizard in self:
            if wizard.duration != "custom":
                wizard.expiration_date = fields.Datetime.now() + datetime.timedelta(days=int(wizard.duration))

    @api.constrains("expiration_date")
    def _check_expiration_date(self):
        now = fields.Datetime.now()
        maximum = now + datetime.timedelta(days=_AGENT_KEY_MAX_DAYS)
        for wizard in self:
            if not wizard.expiration_date or not now < wizard.expiration_date <= maximum:
                raise ValidationError(_("Agent API keys must expire within five years."))

    @check_identity
    def action_generate(self):
        self.ensure_one()
        self.agent_id._check_caller_can_manage()
        if self.agent_id.state != "active":
            raise UserError(_("Reactivate this Agent before creating an API key."))
        key = self.env["res.users.apikeys"].with_user(self.agent_id.user_id)._generate(
            "rpc",
            self.name,
            self.expiration_date,
        )
        self.env.cr.execute(
            """
            SELECT id
              FROM res_users_apikeys
             WHERE user_id = %s AND index = %s
             ORDER BY id DESC
             LIMIT 1
            """,
            [self.agent_id.user_id.id, key[:8]],
        )
        row = self.env.cr.fetchone()
        if not row:
            raise UserError(_("The Agent API key was created but could not be registered safely."))
        self.env["usl.agent.credential"].sudo().with_context(
            usl_agent_credential_internal=True,
        ).create(
            {
                "name": self.name,
                "agent_id": self.agent_id.id,
                "native_key_id": row[0],
                "expiration_date": self.expiration_date,
            },
        )
        self.unlink()
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.users.apikeys.show",
            "name": _("Agent API Key Ready"),
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_key": key},
        }


class UslAgentTransferWizard(models.TransientModel):
    _name = "usl.agent.transfer.wizard"
    _description = "Transfer Agent Ownership"

    agent_id = fields.Many2one("usl.agent", required=True, readonly=True)
    new_owner_id = fields.Many2one(
        "res.users",
        required=True,
        domain="[('active', '=', True), ('share', '=', False), ('usl_is_ai_agent', '=', False)]",
    )
    consequence = fields.Text(compute="_compute_consequence")

    @api.depends("new_owner_id")
    def _compute_consequence(self):
        for wizard in self:
            retained_groups = wizard.agent_id.delegated_group_ids & wizard.new_owner_id.all_group_ids
            retained_companies = wizard.agent_id.company_ids & wizard.new_owner_id.company_ids
            wizard.consequence = _(
                "%(groups)s access groups and %(companies)s companies will remain.",
                groups=len(retained_groups),
                companies=len(retained_companies),
            )

    @check_identity
    def action_transfer(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only an administrator can transfer Agent ownership."))
        if (
            not self.agent_id._caller_may_manage_owner(self.new_owner_id)
            or not self.new_owner_id.active
            or self.new_owner_id.share
            or self.new_owner_id.usl_is_ai_agent
        ):
            raise ValidationError(_("Select an active internal human owner."))
        self.agent_id.with_user(SUPERUSER_ID).with_context(usl_agent_internal=True).write(
            {"owner_id": self.new_owner_id.id},
        )
        self.agent_id._reconcile_authority()
        return {"type": "ir.actions.act_window_close"}


class ResUsersApikeys(models.Model):
    _inherit = "res.users.apikeys"

    def _check_credentials(self, *, scope, key):
        uid = super()._check_credentials(scope=scope, key=key)
        if not uid:
            # Native API-key authentication deliberately ignores inactive
            # users. Governed Agent users are inactive while suspended, so
            # verify only matching, unexpired governed-key candidates to
            # return the stable suspension code. Never authenticate through
            # this fallback: an exact match can only be denied.
            self.env.cr.execute(
                """
                SELECT native_key.user_id, native_key.key
                  FROM res_users_apikeys AS native_key
                  JOIN usl_agent_credential AS credential
                    ON credential.native_key_id = native_key.id
                  JOIN usl_agent AS agent
                    ON agent.id = credential.agent_id
                 WHERE native_key.index = %s
                   AND (native_key.scope IS NULL OR native_key.scope = %s)
                   AND (
                        native_key.expiration_date IS NULL
                        OR native_key.expiration_date >= now() at time zone 'utc'
                   )
                   AND credential.revoked_at IS NULL
                   AND credential.expiration_date > now() at time zone 'utc'
                """,
                [key[:8], scope],
            )
            exact_governed_key = any(
                KEY_CRYPT_CONTEXT.verify(key, candidate)
                for _user_id, candidate in self.env.cr.fetchall()
            )
            if exact_governed_key:
                raise AgentAuthenticationError(_("This Agent is suspended."), "agent_suspended")
            return uid
        # Bearer authentication runs before ``request.update_env(user=uid)``.
        # At that point the transaction may not yet have a default user
        # environment, which relational group computations require. Establish
        # the governance environment explicitly on the same transaction; the
        # returned actor remains ``uid`` and subsequent business calls still
        # execute with that Agent's ordinary ACLs and record rules.
        governance_env = api.Environment(self.env.cr, SUPERUSER_ID, {})
        # ``Environment`` may return an already cached superuser environment;
        # in that case it does not repopulate the transaction default that the
        # relational-field engine uses for safe sudo command evaluation.
        transaction = governance_env.transaction
        previous_default_env = transaction.default_env
        transaction.default_env = governance_env
        try:
            agent = governance_env["usl.agent"].search([("user_id", "=", uid)], limit=1)
            user = governance_env["res.users"].browse(uid)
            if not agent and not user.usl_is_ai_agent:
                return uid
            if not agent:
                raise AgentAuthenticationError(
                    _("This Agent identity is not governed."),
                    "agent_principal_required",
                )
            agent._reconcile_authority()
            if agent.state != "active" or not agent.owner_id.active or not agent.user_id.active:
                raise AgentAuthenticationError(_("This Agent is suspended."), "agent_suspended")
            governance_env.cr.execute(
                "SELECT id FROM res_users_apikeys WHERE user_id = %s AND index = %s LIMIT 1",
                [uid, key[:8]],
            )
            row = governance_env.cr.fetchone()
            credential = governance_env["usl.agent.credential"].search(
                [
                    ("native_key_id", "=", row[0] if row else 0),
                    ("agent_id", "=", agent.id),
                ],
                limit=1,
            )
            if not credential or credential.status != "active":
                raise AccessDenied(_("This Agent credential is not active."))
            if request:
                path = request.httprequest.path or ""
                if not _agent_key_path_allowed(path):
                    raise AgentAuthenticationError(
                        _(
                            "Agent API keys may be used only with JSON-2 and API documentation.",
                        ),
                        "agent_transport_denied",
                    )
            now = fields.Datetime.now()
            credential._touch_last_used_at(now)
            if request:
                request.usl_agent_reconciled_id = agent.id
                request.usl_agent_credential_id = credential.id
            return uid
        finally:
            transaction.default_env = previous_default_env

    @api.model
    def generate(self, key, scope, name, expiration_date):
        if self.env.user.usl_is_ai_agent:
            raise AgentPolicyAccessError(
                _("Agents cannot create or rotate their own credentials."),
                "approval_required",
            )
        return super().generate(key, scope, name, expiration_date)

    @api.model
    def revoke(self, key):
        if self.env.user.usl_is_ai_agent:
            raise AgentPolicyAccessError(
                _("Agents cannot revoke their own credentials."),
                "approval_required",
            )
        return super().revoke(key)

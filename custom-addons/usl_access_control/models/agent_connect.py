"""One-time grants that hand a new Agent credential to the MCP OAuth server.

The browser flow never carries an API key. The owner approves one Agent on
``/usl/agent-access/connect`` and the browser returns to the MCP with a code.
The MCP then redeems the code on ``/usl/agent-access/token`` together with the
verifier of the S256 challenge that it sent at the start. Only that call
creates the key, so an approval that the MCP never redeems creates nothing.
"""

import base64
import datetime
import hashlib
import hmac
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

CONNECT_CODE_TTL_SECONDS = 60
CONNECT_KEY_DAYS_PARAMETER = "usl_access_control.connect_key_days"
CONNECT_KEY_DEFAULT_DAYS = 365
_CONNECT_KEY_MAX_DAYS = 5 * 365
_CONNECT_GRANT_RETENTION_DAYS = 30


def _code_hash(code):
    return hashlib.sha256(code.encode()).hexdigest()


def _s256(verifier):
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class UslAgentConnectGrant(models.Model):
    _name = "usl.agent.connect.grant"
    _description = "Agent Connection Grant"
    _order = "id desc"
    _rec_name = "credential_name"

    code_hash = fields.Char(required=True, readonly=True, copy=False, index=True)
    code_challenge = fields.Char(required=True, readonly=True, copy=False)
    owner_id = fields.Many2one(
        "res.users",
        required=True,
        readonly=True,
        ondelete="cascade",
        index=True,
    )
    agent_id = fields.Many2one(
        "usl.agent",
        required=True,
        readonly=True,
        ondelete="cascade",
        index=True,
    )
    credential_name = fields.Char(required=True, readonly=True)
    return_url = fields.Char(required=True, readonly=True)
    expires_at = fields.Datetime(required=True, readonly=True)
    consumed_at = fields.Datetime(readonly=True, copy=False)
    credential_id = fields.Many2one(
        "usl.agent.credential",
        readonly=True,
        copy=False,
        ondelete="set null",
    )

    _code_hash_unique = models.Constraint(
        "UNIQUE(code_hash)",
        "A connection code can belong to only one grant.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("usl_agent_connect_internal"):
            raise AccessError(_("Agent connection grants are created only by the connection page."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get("usl_agent_connect_internal"):
            raise AccessError(_("Agent connection grants are managed by the security service."))
        return super().write(vals)

    @api.model
    def _credential_name(self, client_name):
        label = " ".join(str(client_name or "").split())[:80] or _("MCP client")
        return _(
            "%(client)s (OAuth, %(date)s)",
            client=label,
            date=fields.Date.to_string(fields.Date.context_today(self)),
        )

    @api.model
    def _key_days(self):
        days = self.env["ir.config_parameter"].sudo().get_int(
            CONNECT_KEY_DAYS_PARAMETER,
            CONNECT_KEY_DEFAULT_DAYS,
        )
        return min(max(days, 1), _CONNECT_KEY_MAX_DAYS)

    @api.model
    def _issue(self, agent, *, code_challenge, return_url, client_name):
        """Record the owner's approval and return the one-time code.

        The caller is the connection page. It checked a fresh identity before
        this call. The table keeps only a hash of the code.
        """
        agent.ensure_one()
        agent._check_caller_can_manage()
        if agent.owner_id != self.env.user:
            raise AccessError(_("You can connect only Agents you own."))
        if agent.state != "active":
            raise UserError(_("Reactivate this Agent before you connect it."))
        code = secrets.token_urlsafe(32)
        self.sudo().with_context(usl_agent_connect_internal=True).create(
            {
                "code_hash": _code_hash(code),
                "code_challenge": code_challenge,
                "owner_id": self.env.uid,
                "agent_id": agent.id,
                "credential_name": self._credential_name(client_name),
                "return_url": return_url,
                "expires_at": fields.Datetime.now()
                + datetime.timedelta(seconds=CONNECT_CODE_TTL_SECONDS),
            },
        )
        return code

    @api.model
    def _redeem(self, code, code_verifier):
        """Exchange a code for a new Agent API key, one time.

        Return ``None`` for every refusal so the caller gives one answer for an
        unknown, expired, used, or mismatched code. A wrong verifier uses up
        the code. Call this in a superuser environment: the caller holds no
        Odoo session, and the code plus its verifier are the authority.
        """
        self.env.cr.execute(
            "SELECT id FROM usl_agent_connect_grant WHERE code_hash = %s FOR UPDATE",
            [_code_hash(code)],
        )
        row = self.env.cr.fetchone()
        if not row:
            return None
        grant = self.browse(row[0]).with_context(usl_agent_connect_internal=True)
        now = fields.Datetime.now()
        if grant.consumed_at or grant.expires_at <= now:
            return None
        grant.write({"consumed_at": now})
        if not hmac.compare_digest(_s256(code_verifier), grant.code_challenge):
            return None
        agent = grant.agent_id
        owner = grant.owner_id
        if (
            agent.owner_id != owner
            or agent.state != "active"
            or not agent.user_id.active
            or not owner.active
            or owner.share
            or owner.usl_is_ai_agent
        ):
            return None
        credential, key = agent._issue_credential(
            grant.credential_name,
            now + datetime.timedelta(days=self._key_days()),
        )
        grant.write({"credential_id": credential.id})
        return {
            "api_key": key,
            "database": self.env.cr.dbname,
            "agent": {"id": agent.id, "name": agent.name},
            "credential": {
                "id": credential.id,
                "name": credential.name,
                "expires_at": fields.Datetime.to_string(credential.expiration_date),
            },
        }

    @api.autovacuum
    def _gc_connect_grants(self):
        limit = fields.Datetime.now() - datetime.timedelta(days=_CONNECT_GRANT_RETENTION_DAYS)
        self.sudo().search([("expires_at", "<", limit)]).unlink()

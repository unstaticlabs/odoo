"""Company-scoped own-account consent, with single-use browser return state."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import secrets
from urllib.parse import urlparse

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.translate import LazyTranslate

from ..services.client import EnableBankingClient
from ..services.protocol import ProviderDataError, account_identity

_lt = LazyTranslate(__name__)

ERROR_MESSAGES = {
    'consent_renewal_required': _lt('The bank requires a new authorization. Renew access on the connection, then retry this account. Existing imported transactions are kept.'),
    'unknown_data': _lt('Odoo could not import the bank response reliably. Nothing from this import was saved. Ask support to check the response.'),
    'connection_not_started': _lt('The bank connection could not be started reliably. Check the application configuration and try again.'),
    'consent_declined': _lt('Bank authorization was cancelled or did not finish. Existing access is kept while it remains valid. Connect again to renew access.'),
    'account_unavailable': _lt('This account is absent from the current bank session. Renew access on the connection and check the accounts offered by the bank. Existing imported history is kept.'),
    'personal_bank_unavailable': _lt('Fortuneo personal-account access is unavailable in the provider discovery response. Try again later.'),
    'invalid_currency_precision': _lt("A booked amount cannot be represented at the account currency's precision. Nothing from this import was saved. Check the bank response before retrying."),
    'signing_not_configured': _lt('The application signing key is not configured. Ask your administrator to mount the company key, then reconnect.'),
    'consent_expired': _lt('Bank access has expired. Reconnect to renew access; existing journals and imported transactions stay in place.'),
    'provider_unavailable': _lt('The bank service could not complete the request. Try again later. The import checkpoint has not advanced.'),
    'missing_entry_reference': _lt("The bank returned a booked transaction without a stable entry reference. Nothing from this import was saved. Use the journal's native manual or file import while support checks the bank response before retrying."),
    'identity_conflict': _lt('A bank entry reference has conflicting financial details. Nothing from this import was saved. Check the source transaction before retrying.'),
    'currency_mismatch': _lt('The bank account and transaction currencies differ. Nothing from this import was saved. Check the account and journal currencies.'),
}

MANAGED = {"session_id", "consent_until", "state_hash", "state_user_id", "state_until", "last_error_code"}


class EnableBankingConnection(models.Model):
    _name = "usl.enablebanking.connection"
    _description = "Enable Banking connection"
    _check_company_auto = True
    _order = "name, id"

    name = fields.Char(required=True, default="Fortuneo")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    app_id = fields.Char(string="Application ID", required=True, groups="account.group_account_manager", copy=False,
                         help="Application registered in Enable Banking. Its private key is mounted by your administrator, never entered here.")
    bank_name = fields.Char(string="Bank", default="Fortuneo", readonly=True, required=True)
    country = fields.Selection([("FR", "France")], default="FR", readonly=True, required=True)
    account_ids = fields.One2many("usl.enablebanking.account", "connection_id", string="Accounts")
    account_count = fields.Integer(compute="_compute_account_count")
    session_id = fields.Char(groups="base.group_system", copy=False)
    consent_until = fields.Datetime(string="Access expires", readonly=True, copy=False)
    state_hash = fields.Char(groups="base.group_system", copy=False)
    state_user_id = fields.Many2one("res.users", groups="base.group_system", copy=False)
    state_until = fields.Datetime(groups="base.group_system", copy=False)
    last_error_code = fields.Char(readonly=True, copy=False)
    last_error = fields.Text(compute="_compute_status")
    status = fields.Selection([("setup", "Setup needed"), ("connected", "Connected"), ("expired", "Access expired"), ("error", "Action needed")], compute="_compute_status")
    signing_ready = fields.Boolean(compute="_compute_status")
    callback_url = fields.Char(string="Callback URL", help="Register this exact HTTPS return URL on the Enable Banking application.", compute="_compute_callback_url")

    @api.depends("account_ids")
    def _compute_account_count(self):
        for connection in self:
            connection.account_count = len(connection.account_ids)

    @api.depends("consent_until", "last_error_code", "company_id")
    def _compute_status(self):
        for connection in self:
            connection.signing_ready = bool(os.environ.get(f"USL_ENABLEBANKING_KEY_FILE_{connection.company_id.id}"))
            expired = connection.consent_until and connection.consent_until <= fields.Datetime.now()
            connection.status = "expired" if expired else "error" if connection.last_error_code else "connected" if connection.consent_until else "setup"
            connection.last_error = self.env._(ERROR_MESSAGES.get(connection.last_error_code, ERROR_MESSAGES["unknown_data"])) if connection.last_error_code else False

    def _compute_callback_url(self):
        base = self.env["ir.config_parameter"].sudo().get_str("web.base.url", "")
        for connection in self:
            connection.callback_url = base.rstrip("/") + "/enablebanking/callback"

    def write(self, vals):
        if not self.env.su and MANAGED.intersection(vals):
            raise AccessError(self.env._("Bank session state is managed by the connection workflow."))
        if "company_id" in vals and any(c.account_ids for c in self):
            raise ValidationError(self.env._("A connection with discovered accounts must stay in its original company."))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(MANAGED.intersection(v) for v in vals_list):
            raise AccessError(self.env._("Bank session state is managed by the connection workflow."))
        return super().create(vals_list)

    def _check_company_access(self, operation="read"):
        self.ensure_one()
        self.check_access(operation)
        if self.company_id not in self.env.companies:
            raise AccessError(self.env._("Switch to the connection's company to continue."))

    def _client(self):
        self._check_company_access()
        # Only the application id is elevated: non-manager accountants may run
        # a configured provider, but cannot read or change its app/key settings.
        return EnableBankingClient(self.sudo().app_id, self.company_id.id)

    def _set_error(self, code):
        self._check_company_access()
        self.sudo().write({"last_error_code": code})

    def action_connect(self):
        self._check_company_access("write")
        parsed = urlparse(self.callback_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise UserError(self.env._("Set Odoo's public HTTPS base URL before connecting a bank."))
        if not self.try_lock_for_update():
            raise UserError(self.env._("This connection is already being updated. Try again shortly."))
        client = self._client()
        try:
            banks = client.aspsps().get("aspsps", [])
            matches = [b for b in banks if b.get("name") == self.bank_name and b.get("country") == self.country and "personal" in b.get("psu_types", [])]
            if len(matches) != 1:
                raise ProviderDataError("personal_bank_unavailable")
            lifetime = int(matches[0]["maximum_consent_validity"])
            if lifetime <= 0:
                raise ProviderDataError("invalid_consent_validity")
            valid_until = fields.Datetime.now() + timedelta(seconds=lifetime)
            state = secrets.token_urlsafe(32)
            redirect = client.authorize({
                "access": {"valid_until": valid_until.replace(tzinfo=timezone.utc).isoformat(), "balances": True, "transactions": True},
                "aspsp": {"name": self.bank_name, "country": self.country},
                "state": state, "redirect_url": self.callback_url, "psu_type": "personal",
            })
        except (ProviderDataError, KeyError, TypeError, ValueError) as error:
            code = str(error) if isinstance(error, ProviderDataError) else "invalid_response"
            raise UserError(self.env._(ERROR_MESSAGES.get(code, ERROR_MESSAGES["connection_not_started"]))) from error
        self.sudo().write({"state_hash": hashlib.sha256(state.encode()).hexdigest(), "state_user_id": self.env.uid,
                           "state_until": fields.Datetime.now() + timedelta(minutes=15), "last_error_code": False})
        return {"type": "ir.actions.act_url", "url": redirect, "target": "self"}

    def _consume_state(self, state):
        self._check_company_access("write")
        if not self.try_lock_for_update():
            raise UserError(self.env._("This connection is already being updated. Try again shortly."))
        secret = self.sudo()
        if (not isinstance(state, str) or not secret.state_hash or secret.state_user_id.id != self.env.uid
                or not secret.state_until or secret.state_until < fields.Datetime.now()
                or not hmac.compare_digest(secret.state_hash, hashlib.sha256(state.encode()).hexdigest())):
            raise AccessError(self.env._("This bank connection return has expired or belongs to another user. Start Connect again."))
        secret.write({"state_hash": False, "state_user_id": False, "state_until": False})

    def _apply_session(self, value):
        self._check_company_access("write")
        accounts = value.get("accounts")
        session_id = value.get("session_id")
        valid_until = (value.get("access") or {}).get("valid_until")
        if not isinstance(accounts, list) or not isinstance(session_id, str) or not session_id or not valid_until:
            raise ProviderDataError("invalid_session")
        try:
            expiry = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
            if not expiry.tzinfo:
                raise ValueError
            expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
        except (AttributeError, TypeError, ValueError) as error:
            raise ProviderDataError("invalid_session") from error
        aspsp = value.get("aspsp")
        if (not isinstance(aspsp, dict) or aspsp.get("name") != self.bank_name
                or aspsp.get("country") != self.country or value.get("psu_type") != "personal"):
            raise ProviderDataError("invalid_session_scope")
        if expiry <= fields.Datetime.now():
            raise ProviderDataError("consent_expired")
        parsed = [(resource, *account_identity(resource)) for resource in accounts]
        Account = self.env["usl.enablebanking.account"].with_company(self.company_id)
        seen = self.env["usl.enablebanking.account"]
        for resource, uid, aliases in parsed:
            currency = self.env["res.currency"].with_context(active_test=False).search([("name", "=", resource.get("currency"))], limit=1)
            if not currency:
                raise ProviderDataError("invalid_account_currency")
            matches = self.env["usl.enablebanking.account.alias"].search([
                ("company_id", "=", self.company_id.id), ("currency_id", "=", currency.id), ("identity_hash", "in", list(aliases)),
            ]).mapped("account_id")
            if len(matches) > 1 or (matches and matches.connection_id != self):
                raise ProviderDataError("ambiguous_account_identity")
            account = matches or Account.sudo().create({
                "connection_id": self.id, "company_id": self.company_id.id,
                "name": resource.get("name") or resource.get("product") or "Fortuneo account", "currency_id": currency.id,
            })
            if account.currency_id != currency or account in seen:
                raise ProviderDataError("ambiguous_account_identity")
            number = resource.get("account_id") or {}
            account.sudo().write({"session_uid": uid, "available": True,
                                  "account_number": number.get("iban") or (number.get("other") or {}).get("identification") or False,
                                  "product": resource.get("product") or False, "account_type": resource.get("cash_account_type") or False})
            existing = set(account.alias_ids.mapped("identity_hash"))
            for alias in set(aliases) - existing:
                self.env["usl.enablebanking.account.alias"].sudo().create({"account_id": account.id, "identity_hash": alias})
            seen |= account.with_env(self.env)
        (self.account_ids - seen).sudo().write({"available": False, "session_uid": False})
        self.sudo().write({"session_id": session_id, "consent_until": expiry, "last_error_code": False})

    def action_accounts(self):
        self._check_company_access()
        return {"type": "ir.actions.act_window", "name": self.env._("Bank accounts"), "res_model": "usl.enablebanking.account",
                "view_mode": "list,kanban,form", "mobile_view_mode": "kanban", "domain": [("connection_id", "=", self.id)], "context": {"default_connection_id": self.id}}

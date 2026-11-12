"""Stable account identity and native journal mapping; session UIDs may change."""
import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

ACCOUNT_MANAGED = {"connection_id", "company_id", "stable_key", "session_uid", "available", "currency_id", "account_number", "product", "account_type", "last_import_at", "last_imported_count", "last_duplicate_count", "last_pending_count", "last_error_code", "balance_error_code"}


class EnableBankingAccount(models.Model):
    _name = "usl.enablebanking.account"
    _description = "Connected bank account"
    _check_company_auto = True
    _order = "name, id"

    name = fields.Char(required=True)
    connection_id = fields.Many2one("usl.enablebanking.connection", required=True, ondelete="restrict", check_company=True, readonly=True)
    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    currency_id = fields.Many2one("res.currency", required=True, readonly=True)
    stable_key = fields.Char(default=lambda self: uuid.uuid4().hex, required=True, copy=False, readonly=True)
    session_uid = fields.Char(groups="base.group_system", copy=False)
    available = fields.Boolean(default=True, readonly=True)
    account_number = fields.Char(string="Account number", readonly=True)
    product = fields.Char(string="Bank product", readonly=True)
    account_type = fields.Char(readonly=True)
    alias_ids = fields.One2many("usl.enablebanking.account.alias", "account_id", readonly=True)
    journal_id = fields.Many2one("account.journal", check_company=True, domain="[('company_id', '=', company_id), ('type', '=', 'bank')]",
                                 help="Use the account's existing bank journal, or create one through the native journal form.")
    provider_ids = fields.One2many("online.bank.statement.provider", "enablebanking_account_id", readonly=True, groups="account.group_account_user")
    import_ids = fields.One2many("usl.enablebanking.import", "account_id", string="Import provenance", readonly=True)
    connection_status = fields.Selection(related="connection_id.status")
    balance_ids = fields.One2many("usl.enablebanking.balance", "account_id", readonly=True)
    consent_until = fields.Datetime(related="connection_id.consent_until")
    last_import_at = fields.Datetime(string="Last successful import", readonly=True, copy=False)
    last_imported_count = fields.Integer(readonly=True, copy=False)
    last_duplicate_count = fields.Integer(readonly=True, copy=False)
    last_pending_count = fields.Integer(readonly=True, copy=False)
    last_error_code = fields.Char(readonly=True, copy=False)
    balance_error_code = fields.Char(readonly=True, copy=False)
    balance_warning = fields.Text(compute="_compute_balance_warning")
    last_error = fields.Text(compute="_compute_last_error")
    import_from = fields.Date(string="First booking date", default=lambda self: fields.Date.today(), required=True,
                              help="First booking date to request. Repeating an earlier period is safe when the bank supplies stable entry references.")

    _stable_key_unique = models.Constraint("UNIQUE(stable_key)", "The connected account identity must be unique.")
    _journal_unique = models.Constraint("UNIQUE(journal_id)", "A bank journal can be mapped to only one connected account.")

    @api.depends("last_error_code")
    def _compute_last_error(self):
        from .connection import ERROR_MESSAGES
        for account in self:
            message = ERROR_MESSAGES.get(account.last_error_code, ERROR_MESSAGES["unknown_data"])
            account.last_error = self.env._("%(bank)s / %(account)s: %(message)s", bank=account.connection_id.name, account=account.name, message=self.env._(message)) if account.last_error_code else False

    @api.depends("balance_error_code")
    def _compute_balance_warning(self):
        for account in self:
            warning = self.env._("Booked transactions were imported, but bank balances could not be refreshed. Previously retrieved balances, if any, keep their original dates. Try the import again to refresh balances.") if account.balance_error_code else False
            if account.balance_error_code in {"consent_expired", "consent_renewal_required"}:
                warning += " " + self.env._("Renew bank access on the connection before retrying.")
            account.balance_warning = warning

    @api.model_create_multi
    def create(self, values_list):
        if not self.env.su:
            raise AccessError(self.env._("Accounts are discovered through the bank connection."))
        return super().create(values_list)

    def write(self, values):
        if not self.env.su and ACCOUNT_MANAGED.intersection(values):
            raise AccessError(self.env._("Bank identity and import results are managed by the connection workflow."))
        result = super().write(values)
        if "journal_id" in values:
            self._ensure_provider()
        return result

    @api.constrains("journal_id", "currency_id", "company_id", "connection_id")
    def _check_journal(self):
        for account in self:
            journal = account.journal_id
            if journal and (journal.type != "bank" or journal.company_id != account.company_id
                            or (journal.currency_id or journal.company_id.currency_id) != account.currency_id):
                raise ValidationError(self.env._("Choose a bank journal in this account's company and currency."))
            if account.connection_id.company_id != account.company_id:
                raise ValidationError(self.env._("A bank account must belong to its connection's company."))

    def _check_account_access(self, operation="write"):
        self.ensure_one()
        self.check_access(operation)
        self.connection_id._check_company_access()

    def _ensure_provider(self):
        for account in self:
            account._check_account_access()
            if not account.journal_id:
                continue
            provider = account.provider_ids
            values = {"service": "enablebanking", "journal_id": account.journal_id.id,
                      "enablebanking_account_id": account.id, "create_statement": False, "tz": "UTC"}
            if provider:
                changed = {}
                for name, value in values.items():
                    current = provider[name]
                    if provider._fields[name].type == "many2one":
                        current = current.id
                    if current != value:
                        changed[name] = value
                if changed:
                    provider.write(changed)
            else:
                self.env["online.bank.statement.provider"].with_company(account.company_id).create(values)

    def action_import(self):
        self._check_account_access()
        if not self.journal_id:
            raise UserError(self.env._("Choose or create a bank journal for this account first."))
        self._ensure_provider()
        self.provider_ids._pull_bank_account(self.import_from, fields.Date.today())
        message = self.last_error or self.env._("Import complete: %(count)s new transactions, %(duplicates)s already imported.", count=self.last_imported_count, duplicates=self.last_duplicate_count)
        if not self.last_error and self.balance_warning:
            message += " " + self.balance_warning
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": self.env._("Bank import"), "message": message,
            "type": "warning" if self.last_error or self.balance_warning else "success", "sticky": bool(self.last_error or self.balance_warning),
            "next": {"type": "ir.actions.client", "tag": "soft_reload"},
        }}

    def action_provider(self):
        self._check_account_access()
        self._ensure_provider()
        return {"type": "ir.actions.act_window", "name": self.env._("Scheduled bank import"),
                "res_model": "online.bank.statement.provider", "res_id": self.provider_ids.id, "view_mode": "form"}

    def action_transactions(self):
        self._check_account_access("read")
        return {"type": "ir.actions.act_window", "name": self.env._("Imported bank transactions"),
                "res_model": "account.bank.statement.line", "view_mode": "list,form",
                "domain": [("id", "in", self.env["usl.enablebanking.import"].search([("account_id", "=", self.id)]).mapped("line_id").ids)],
                "context": {"default_journal_id": self.journal_id.id}}


class EnableBankingAccountAlias(models.Model):
    _name = "usl.enablebanking.account.alias"
    _description = "Bank account identification alias"
    _check_company_auto = True
    account_id = fields.Many2one("usl.enablebanking.account", required=True, ondelete="cascade", check_company=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="account_id.currency_id", store=True, index=True)
    identity_hash = fields.Char(required=True)
    _company_hash_unique = models.Constraint("UNIQUE(company_id, currency_id, identity_hash)", "A bank account identification must resolve to one account in each company.")


class EnableBankingBalance(models.Model):
    _name = "usl.enablebanking.balance"
    _description = "Provider-reported bank balance"
    _check_company_auto = True
    account_id = fields.Many2one("usl.enablebanking.account", required=True, ondelete="cascade", check_company=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True, index=True)
    name = fields.Char(required=True)
    balance_type = fields.Char(required=True)
    amount = fields.Monetary(currency_field="currency_id", required=True)
    currency_id = fields.Many2one("res.currency", required=True)
    reference_date = fields.Date(string="Reference date")
    changed_at = fields.Datetime(string="Changed at")
    retrieved_at = fields.Datetime(string="Retrieved at", required=True)

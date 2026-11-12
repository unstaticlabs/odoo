"""OCA scheduler/import integration. Native journal lines remain authoritative."""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.protocol import ProviderDataError, collect_transaction_pages, financial_fingerprint, import_identity, normalise_transactions


class OnlineBankStatementProvider(models.Model):
    _inherit = "online.bank.statement.provider"

    enablebanking_account_id = fields.Many2one("usl.enablebanking.account", string="Connected account", check_company=True, ondelete="restrict", copy=False)
    _enablebanking_account_unique = models.Constraint("UNIQUE(enablebanking_account_id)", "A connected account can have only one online provider.")

    @api.model
    def _get_available_services(self):
        return super()._get_available_services() + [("enablebanking", "Enable Banking")]

    @api.constrains("enablebanking_account_id", "journal_id", "service", "tz", "create_statement")
    def _check_enablebanking_provider(self):
        for provider in self.filtered(lambda p: p.service == "enablebanking" or p.enablebanking_account_id):
            account = provider.enablebanking_account_id
            if not account:
                raise ValidationError(self.env._("Map a discovered bank account before configuring its provider."))
            account._check_account_access()
            if provider.service != "enablebanking" or provider.journal_id != account.journal_id or provider.tz != "UTC" or provider.create_statement:
                raise ValidationError(self.env._("Enable Banking imports booked bank lines in their bank booking date. Keep its mapped journal, UTC date handling and automatic statements disabled."))

    def _adjust_schedule(self):
        if self.service != "enablebanking":
            return super()._adjust_schedule()
        # The inherited adjustment runs before the network request. Leave this
        # provider's schedule/checkpoint untouched until the whole import commits.

    def _pull(self, date_since, date_until):
        debug_data = []
        for provider in self:
            if provider.service != "enablebanking":
                debug_data.extend(super(OnlineBankStatementProvider, provider)._pull(date_since, date_until))
                continue
            scheduled = self.env.context.get("scheduled")
            start = provider.enablebanking_account_id.import_from if scheduled else fields.Date.to_date(date_since)
            end = fields.Date.today() if scheduled else fields.Date.to_date(date_until)
            if self.env.context.get("account_statement_online_import_debug"):
                provider.enablebanking_account_id._check_account_access()
                try:
                    result, balances = provider._fetch_bank_period(start, end)
                except ProviderDataError as error:
                    raise UserError(self.env._("The bank preview could not be fetched reliably. No transaction or import checkpoint was changed.")) from error
                debug_data.append({"booked_transactions": result.transactions, "provider_balances": balances,
                                   "repeated_entries": result.duplicate_count, "nonbooked_excluded": result.pending_count})
            else:
                provider._pull_bank_account(start, end)
        return debug_data

    def _pull_bank_account(self, date_since, date_until):
        self.ensure_one()
        account = self.enablebanking_account_id
        account._check_account_access()
        if not account.connection_id.try_lock_for_update() or not account.try_lock_for_update():
            raise UserError(self.env._("This bank account is already importing. Try again shortly."))
        try:
            with self.env.cr.savepoint():
                self._import_complete_period(fields.Date.to_date(date_since), fields.Date.to_date(date_until))
        except ProviderDataError as exception:
            code = str(exception)
            account.sudo().write({"last_error_code": code})
            if code in {"consent_expired", "consent_renewal_required"}:
                account.connection_id._set_error(code)
            return False
        return True

    def _fetch_bank_period(self, start, end):
        self.ensure_one()
        account = self.enablebanking_account_id
        connection = account.connection_id
        if not start or not end or start > end:
            raise ProviderDataError("invalid_period")
        if not account.available or not account.sudo().session_uid:
            raise ProviderDataError("account_unavailable")
        if not connection.consent_until or connection.consent_until <= fields.Datetime.now():
            raise ProviderDataError("consent_expired")
        client = connection._client()
        uid = account.sudo().session_uid
        def page(cursor):
            params = {"date_from": start.isoformat(), "date_to": end.isoformat(), "transaction_status": "BOOK"}
            if cursor is not None:
                params["continuation_key"] = cursor
            return client.transactions(uid, params)
        result = normalise_transactions(collect_transaction_pages(page), account.currency_id.name, date_since=start.isoformat(), date_until=end.isoformat())
        balances = client.balances(uid).get("balances")
        return result, self._balance_values(balances)

    def _import_complete_period(self, start, end):
        self.ensure_one()
        account = self.enablebanking_account_id
        result, balance_values = self._fetch_bank_period(start, end)
        receipts = self.env["usl.enablebanking.import"]
        Line = self.env["account.bank.statement.line"]
        new_rows, matched_rows, skipped = [], [], result.duplicate_count
        for transaction in result.transactions:
            if Decimal(str(account.currency_id.round(transaction["amount"]))) != Decimal(str(transaction["amount"])):
                raise ProviderDataError("invalid_currency_precision")
            fingerprint = financial_fingerprint(transaction)
            existing = receipts.search([("account_id", "=", account.id), ("entry_reference", "=", transaction["entry_reference"])], limit=1)
            if existing:
                if existing.financial_sha256 != fingerprint:
                    raise ProviderDataError("identity_conflict")
                if not existing.line_id:
                    raise ProviderDataError("imported_line_missing")
                skipped += 1
                continue
            key = import_identity(account.company_id.id, account.stable_key, transaction["entry_reference"])
            line = {
                "date": datetime.combine(fields.Date.to_date(transaction["booking_date"]), time.min),
                "amount": transaction["amount"], "payment_ref": transaction["payment_ref"],
                "unique_import_id": key,
            }
            # OCA's native import-id prefix uses the journal/account number.
            # Freeze the complete resulting id in the immutable receipt too.
            probe = dict(line)
            self.journal_id._statement_line_import_update_unique_import_id(probe, self.account_number)
            native = Line.search([("unique_import_id", "=", probe["unique_import_id"])], limit=2)
            if native:
                if (len(native) != 1 or native.journal_id != self.journal_id
                        or native.company_id != account.company_id or native.currency_id != account.currency_id
                        or native.date != fields.Date.to_date(transaction["booking_date"])
                        or Decimal(str(native.amount)) != Decimal(str(transaction["amount"]))):
                    raise ProviderDataError("identity_conflict")
                matched_rows.append((transaction, fingerprint, native, probe["unique_import_id"], "matched_existing"))
                skipped += 1
                continue
            # Native import IDs are globally unique. Check only existence when
            # the caller cannot read the row; never adopt a foreign-company row.
            if Line.sudo().search_count([("unique_import_id", "=", probe["unique_import_id"])]):
                raise ProviderDataError("identity_conflict")
            new_rows.append((transaction, fingerprint, line, probe["unique_import_id"]))
        self._create_or_update_statement(([row[2] for row in new_rows], {}),
                                         datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min))
        for transaction, fingerprint, _line, key in new_rows:
            native = Line.search([("journal_id", "=", self.journal_id.id), ("unique_import_id", "=", key)], limit=2)
            if len(native) != 1:
                raise ProviderDataError("native_import_incomplete")
            matched_rows.append((transaction, fingerprint, native, key, "created"))
        for transaction, fingerprint, native, key, origin in matched_rows:
            receipts.sudo().create({"account_id": account.id, "line_origin": origin, "entry_reference": transaction["entry_reference"],
                                    "financial_sha256": fingerprint, "unique_import_id": key, "line_id": native.id,
                                    "booking_date": transaction["booking_date"], "value_date": transaction["value_date"],
                                    "amount": transaction["amount"], "currency_id": account.currency_id.id,
                                    "imported_at": fields.Datetime.now()})
        account.balance_ids.sudo().unlink()
        self.env["usl.enablebanking.balance"].sudo().create(balance_values)
        moment = fields.Datetime.now()
        account.sudo().write({"last_import_at": moment, "last_imported_count": len(new_rows),
                              "last_duplicate_count": skipped, "last_pending_count": result.pending_count, "last_error_code": False})
        self.write({"last_successful_run": moment, "next_run": moment + self._get_next_run_period()})

    def _balance_values(self, balances):
        if not isinstance(balances, list):
            raise ProviderDataError("invalid_balance")
        values = []
        for balance in balances:
            try:
                amount = Decimal(balance["balance_amount"]["amount"])
                currency = self.env["res.currency"].with_context(active_test=False).search([("name", "=", balance["balance_amount"]["currency"])], limit=1)
                if not amount.is_finite() or not currency or not balance.get("name") or not balance.get("balance_type"):
                    raise ValueError
                changed = balance.get("last_change_date_time")
                changed = datetime.fromisoformat(changed.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None) if changed else False
                reference = fields.Date.to_date(balance.get("reference_date"))
            except (KeyError, TypeError, ValueError, InvalidOperation) as error:
                raise ProviderDataError("invalid_balance") from error
            values.append({"account_id": self.enablebanking_account_id.id, "name": balance["name"], "balance_type": balance["balance_type"],
                           "amount": float(amount), "currency_id": currency.id, "reference_date": reference,
                           "changed_at": changed, "retrieved_at": fields.Datetime.now()})
        return values


class EnableBankingImport(models.Model):
    _name = "usl.enablebanking.import"
    _description = "Bank transaction import provenance"
    _check_company_auto = True
    _order = "booking_date desc, id desc"
    account_id = fields.Many2one("usl.enablebanking.account", required=True, ondelete="restrict", check_company=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True, index=True)
    line_origin = fields.Selection([("created", "Created by import"), ("matched_existing", "Matched existing")], string="Origin", required=True, default="created", help="An exact existing import identity may be linked without changing its native bank transaction.")
    entry_reference = fields.Char(string="Bank reference", required=True, index=True)
    financial_sha256 = fields.Char(required=True)
    unique_import_id = fields.Char(required=True, index=True)
    line_id = fields.Many2one("account.bank.statement.line", string="Bank transaction", ondelete="set null", check_company=True)
    booking_date = fields.Date(string="Booking date", required=True)
    value_date = fields.Date(string="Value date")
    amount = fields.Monetary(currency_field="currency_id", required=True)
    currency_id = fields.Many2one("res.currency", required=True)
    imported_at = fields.Datetime(string="Imported at", required=True)
    _entry_unique = models.Constraint("UNIQUE(account_id, entry_reference)", "A bank entry can be imported only once for an account.")
    _native_line_unique = models.Constraint("UNIQUE(line_id)", "A native bank line can have only one provider provenance record.")


class OnlineBankStatementPullWizard(models.TransientModel):
    _inherit = "online.bank.statement.pull.wizard"

    def action_pull(self):
        self.ensure_one()
        provider = self._get_provider()
        if provider.service != "enablebanking":
            return super().action_pull()
        provider._pull(self.date_since, self.date_until)
        account = provider.enablebanking_account_id
        if account.last_error_code:
            return {"type": "ir.actions.act_window", "res_model": "usl.enablebanking.account", "res_id": account.id, "view_mode": "form"}
        return account.action_transactions()

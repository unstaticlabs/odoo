import json
import logging
from decimal import Decimal, InvalidOperation

import requests
from lxml import etree
from requests.exceptions import RequestException

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

ECB_DAILY_RATE_URL = (
    "https://www.ecb.europa.eu/stats/eurofxref/"
    "eurofxref-daily.xml"
)
ECB_RATE_NAMESPACE = (
    "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"
)
ECB_RESPONSE_LIMIT = 1_000_000


class ResCompany(models.Model):
    _inherit = "res.company"

    rebuild_currency_rate_provider = fields.Selection(
        [
            ("ecb", "European Central Bank"),
            ("manual", "Manual Rates"),
        ],
        string="Reference Rate Provider",
        required=True,
        default="ecb",
    )
    rebuild_currency_rate_auto_update = fields.Boolean(
        string="Automatic Reference Rates",
        default=True,
    )
    rebuild_currency_rate_last_sync_at = fields.Datetime(
        string="Last Rate Retrieval",
        readonly=True,
        copy=False,
    )
    rebuild_currency_rate_last_reference_date = fields.Date(
        string="Latest Reference Date",
        readonly=True,
        copy=False,
    )
    rebuild_currency_rate_last_sync_status = fields.Selection(
        [
            ("never", "Never Retrieved"),
            ("passed", "Retrieved"),
            ("failed", "Failed"),
        ],
        string="Rate Retrieval Status",
        required=True,
        default="never",
        readonly=True,
        copy=False,
    )
    rebuild_currency_rate_last_sync_message = fields.Text(
        string="Rate Retrieval Details",
        readonly=True,
        copy=False,
    )

    @api.model
    def _rebuild_parse_ecb_daily_xml(self, payload):
        if not payload:
            raise UserError(_("The ECB response was empty."))
        if len(payload) > ECB_RESPONSE_LIMIT:
            raise UserError(
                _("The ECB response exceeded the accepted size limit."),
            )
        try:
            parser = etree.XMLParser(
                resolve_entities=False,
                no_network=True,
                recover=False,
            )
            root = etree.fromstring(payload, parser=parser)
        except (TypeError, ValueError, etree.XMLSyntaxError) as error:
            raise UserError(
                _("The ECB response was not valid XML."),
            ) from error

        dated_nodes = root.xpath(
            ".//ecb:Cube[@time]",
            namespaces={"ecb": ECB_RATE_NAMESPACE},
        )
        if len(dated_nodes) != 1:
            raise UserError(
                _(
                    "The ECB response must contain exactly one "
                    "reference-rate date.",
                ),
            )
        try:
            reference_date = fields.Date.to_date(
                dated_nodes[0].get("time"),
            )
        except (TypeError, ValueError) as error:
            raise UserError(
                _("The ECB response contained an invalid reference date."),
            ) from error
        if not reference_date:
            raise UserError(
                _("The ECB response did not contain a reference date."),
            )

        rates = {"EUR": Decimal("1")}
        for node in dated_nodes[0].xpath(
            "./ecb:Cube[@currency][@rate]",
            namespaces={"ecb": ECB_RATE_NAMESPACE},
        ):
            code = (node.get("currency") or "").strip().upper()
            if len(code) != 3 or not code.isalpha() or code in rates:
                raise UserError(
                    _("The ECB response contained an invalid currency code."),
                )
            try:
                value = Decimal(node.get("rate"))
            except (InvalidOperation, TypeError, ValueError) as error:
                raise UserError(
                    _(
                        "The ECB response contained an invalid rate "
                        "for %(currency)s.",
                        currency=code,
                    ),
                ) from error
            if not value.is_finite() or value <= 0:
                raise UserError(
                    _(
                        "The ECB rate for %(currency)s must be positive.",
                        currency=code,
                    ),
                )
            rates[code] = value
        if len(rates) == 1:
            raise UserError(
                _("The ECB response did not contain any currency rates."),
            )
        return reference_date, rates

    @api.model
    def _rebuild_fetch_ecb_daily_xml(self):
        try:
            response = requests.get(
                ECB_DAILY_RATE_URL,
                headers={
                    "Accept": "application/xml,text/xml",
                    "User-Agent": "USL-Odoo-Accounting/19.0",
                },
                timeout=15,
            )
            response.raise_for_status()
        except RequestException as error:
            raise UserError(
                _(
                    "The European Central Bank reference-rate feed "
                    "could not be retrieved: %(error)s",
                    error=str(error),
                ),
            ) from error
        payload = response.content
        if len(payload) > ECB_RESPONSE_LIMIT:
            raise UserError(
                _("The ECB response exceeded the accepted size limit."),
            )
        return payload, fields.Datetime.now()

    def _rebuild_record_currency_rate_failure(self, message):
        self.ensure_one()
        self.sudo().write({
            "rebuild_currency_rate_last_sync_at": fields.Datetime.now(),
            "rebuild_currency_rate_last_sync_status": "failed",
            "rebuild_currency_rate_last_sync_message": message,
        })

    def _rebuild_update_ecb_currency_rates(
        self,
        payload=None,
        retrieved_at=None,
    ):
        self.ensure_one()
        if self.rebuild_currency_rate_provider != "ecb":
            raise UserError(
                _(
                    "Select the European Central Bank provider before "
                    "retrieving reference rates.",
                ),
            )
        if payload is None:
            payload, fetched_at = self._rebuild_fetch_ecb_daily_xml()
            retrieved_at = retrieved_at or fetched_at
        retrieved_at = (
            fields.Datetime.to_datetime(retrieved_at)
            if retrieved_at
            else fields.Datetime.now()
        )
        reference_date, ecb_rates = self._rebuild_parse_ecb_daily_xml(
            payload,
        )
        base_code = self.currency_id.name
        base_rate = ecb_rates.get(base_code)
        if not base_rate:
            raise UserError(
                _(
                    "The ECB feed does not provide the company currency "
                    "%(currency)s, so cross-rates cannot be calculated.",
                    currency=base_code,
                ),
            )

        Currency = self.env["res.currency"].with_context(active_test=False)
        Rate = self.env["res.currency.rate"].sudo().with_company(self)
        currencies = Currency.search([
            ("active", "=", True),
            ("id", "!=", self.currency_id.id),
        ])
        created_count = 0
        updated_count = 0
        preserved_source_count = 0
        unavailable_currency_codes = []
        updated_currency_codes = []
        for currency in currencies:
            provider_rate = ecb_rates.get(currency.name)
            if not provider_rate:
                unavailable_currency_codes.append(currency.name)
                continue
            technical_rate = provider_rate / base_rate
            existing = Rate.search([
                ("currency_id", "=", currency.id),
                ("company_id", "=", self.id),
                ("name", "=", reference_date),
            ], limit=1)
            if existing and existing.rebuild_source_model:
                preserved_source_count += 1
                continue
            values = {
                "name": reference_date,
                "currency_id": currency.id,
                "company_id": self.id,
                "rate": float(technical_rate),
                "rebuild_rate_provider": "ecb",
                "rebuild_rate_retrieved_at": retrieved_at,
            }
            if existing:
                existing.write(values)
                updated_count += 1
            else:
                Rate.create(values)
                created_count += 1
            updated_currency_codes.append(currency.name)

        message = _(
            "ECB reference rates for %(date)s: %(created)s created, "
            "%(updated)s updated, %(preserved)s source-traced rates "
            "preserved.",
            date=fields.Date.to_string(reference_date),
            created=created_count,
            updated=updated_count,
            preserved=preserved_source_count,
        )
        if unavailable_currency_codes:
            message += " " + _(
                "No ECB rate was available for: %(currencies)s.",
                currencies=", ".join(sorted(unavailable_currency_codes)),
            )
        result = {
            "status": "passed",
            "provider": "ecb",
            "provider_url": ECB_DAILY_RATE_URL,
            "retrieved_at": fields.Datetime.to_string(retrieved_at),
            "reference_date": fields.Date.to_string(reference_date),
            "company_id": self.id,
            "company_currency": base_code,
            "created_count": created_count,
            "updated_count": updated_count,
            "preserved_source_count": preserved_source_count,
            "updated_currency_codes": sorted(updated_currency_codes),
            "unavailable_currency_codes": sorted(
                unavailable_currency_codes,
            ),
            "message": message,
        }
        self.sudo().write({
            "rebuild_currency_rate_last_sync_at": retrieved_at,
            "rebuild_currency_rate_last_reference_date": reference_date,
            "rebuild_currency_rate_last_sync_status": "passed",
            "rebuild_currency_rate_last_sync_message": message,
        })
        return result

    @api.model
    def _cron_rebuild_update_currency_rates(self):
        companies = self.sudo().search([
            ("rebuild_currency_rate_auto_update", "=", True),
            ("rebuild_currency_rate_provider", "=", "ecb"),
        ])
        for company in companies:
            try:
                company._rebuild_update_ecb_currency_rates()
            except Exception as error:  # noqa: BLE001
                message = str(error)
                company._rebuild_record_currency_rate_failure(message)
                _logger.exception(
                    "ECB currency-rate update failed for %s",
                    company.display_name,
                )
        return True


class RebuildCurrencyRateUpdateWizard(models.TransientModel):
    _name = "rebuild.currency.rate.update.wizard"
    _description = "ECB Reference Rate Automation"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    provider = fields.Selection(
        [
            ("ecb", "European Central Bank"),
            ("manual", "Manual Rates"),
        ],
        required=True,
        default="ecb",
    )
    automatic_update = fields.Boolean(
        string="Retrieve Daily",
        default=True,
    )
    provider_url = fields.Char(
        string="Official Provider URL",
        default=ECB_DAILY_RATE_URL,
        readonly=True,
    )
    last_sync_at = fields.Datetime(readonly=True)
    last_reference_date = fields.Date(readonly=True)
    last_sync_status = fields.Selection(
        [
            ("never", "Never Retrieved"),
            ("passed", "Retrieved"),
            ("failed", "Failed"),
        ],
        readonly=True,
    )
    last_sync_message = fields.Text(readonly=True)
    result_json = fields.Text(readonly=True)

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        company = self.env["res.company"].browse(
            values.get("company_id"),
        ) or self.env.company
        values.update(self._rebuild_company_values(company))
        return values

    @api.model
    def _rebuild_company_values(self, company):
        return {
            "provider": company.rebuild_currency_rate_provider,
            "automatic_update": (
                company.rebuild_currency_rate_auto_update
            ),
            "last_sync_at": company.rebuild_currency_rate_last_sync_at,
            "last_reference_date": (
                company.rebuild_currency_rate_last_reference_date
            ),
            "last_sync_status": (
                company.rebuild_currency_rate_last_sync_status
            ),
            "last_sync_message": (
                company.rebuild_currency_rate_last_sync_message
            ),
        }

    @api.onchange("company_id")
    def _onchange_company_id(self):
        for wizard in self:
            if wizard.company_id:
                wizard.update(
                    wizard._rebuild_company_values(wizard.company_id),
                )

    def _rebuild_check_manager(self):
        if not self.env.user.has_group(
            "account.group_account_manager",
        ):
            raise AccessError(
                _(
                    "Only Accounting Managers can configure or retrieve "
                    "reference rates.",
                ),
            )

    def _rebuild_save_configuration(self):
        self.ensure_one()
        self._rebuild_check_manager()
        self.company_id.sudo().write({
            "rebuild_currency_rate_provider": self.provider,
            "rebuild_currency_rate_auto_update": self.automatic_update,
        })

    def _rebuild_reload_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Currency Rate Automation"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_save_configuration(self):
        self._rebuild_save_configuration()
        return self._rebuild_reload_action()

    def action_update_now(self):
        self.ensure_one()
        self._rebuild_save_configuration()
        try:
            result = (
                self.company_id._rebuild_update_ecb_currency_rates()
            )
        except UserError as error:
            self.company_id._rebuild_record_currency_rate_failure(
                str(error),
            )
            result = {
                "status": "failed",
                "provider": self.provider,
                "provider_url": ECB_DAILY_RATE_URL,
                "company_id": self.company_id.id,
                "message": str(error),
            }
        self.write({
            **self._rebuild_company_values(self.company_id),
            "result_json": json.dumps(
                result,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            ),
        })
        return self._rebuild_reload_action()

    def action_open_currency_rates(self):
        self.ensure_one()
        self._rebuild_check_manager()
        return {
            "type": "ir.actions.act_window",
            "name": _("Currency Rates"),
            "res_model": "res.currency.rate",
            "view_mode": "list,form",
            "domain": [("company_id", "=", self.company_id.id)],
            "context": {
                "default_company_id": self.company_id.id,
                "create": True,
            },
        }

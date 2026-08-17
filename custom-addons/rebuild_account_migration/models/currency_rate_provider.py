import json
import logging
from decimal import Decimal, InvalidOperation

import requests
from lxml import etree
from requests.exceptions import RequestException

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)

ECB_RECENT_RATE_URL = (
    "https://www.ecb.europa.eu/stats/eurofxref/"
    "eurofxref-hist-90d.xml"
)
ECB_FULL_RATE_URL = (
    "https://www.ecb.europa.eu/stats/eurofxref/"
    "eurofxref-hist.xml"
)
ECB_RATE_NAMESPACE = (
    "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"
)
ECB_RESPONSE_LIMIT = 12_000_000


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
    rebuild_currency_rate_share_same_base = fields.Boolean(
        string="Share ECB Rates with Same-Currency Companies",
        default=True,
        help=(
            "Keep provider-controlled reference rates aligned across allowed "
            "companies that use the same base currency. Restored, manual and "
            "transaction-specific non-ECB rates remain company-specific and "
            "are never overwritten."
        ),
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
    rebuild_currency_rate_coverage_start = fields.Date(
        string="Automated Coverage From",
        readonly=True,
        copy=False,
        help=(
            "First ECB publication date governed by automation. Restored and "
            "manual rates before this boundary remain unchanged."
        ),
    )

    @api.model
    def _rebuild_parse_ecb_xml(self, payload):
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
        if not dated_nodes:
            raise UserError(
                _("The ECB response did not contain any reference-rate dates."),
            )

        observations = []
        seen_dates = set()
        for dated_node in dated_nodes:
            try:
                reference_date = fields.Date.to_date(
                    dated_node.get("time"),
                )
            except (TypeError, ValueError) as error:
                raise UserError(
                    _("The ECB response contained an invalid reference date."),
                ) from error
            if not reference_date or reference_date in seen_dates:
                raise UserError(
                    _(
                        "The ECB response contained a missing or duplicate "
                        "reference date.",
                    ),
                )
            seen_dates.add(reference_date)

            rates = {"EUR": Decimal("1")}
            for node in dated_node.xpath(
                "./ecb:Cube[@currency][@rate]",
                namespaces={"ecb": ECB_RATE_NAMESPACE},
            ):
                code = (node.get("currency") or "").strip().upper()
                if len(code) != 3 or not code.isalpha() or code in rates:
                    raise UserError(
                        _(
                            "The ECB response contained an invalid currency "
                            "code.",
                        ),
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
                    _(
                        "The ECB response did not contain currency rates for "
                        "%(date)s.",
                        date=fields.Date.to_string(reference_date),
                    ),
                )
            observations.append((reference_date, rates))
        return sorted(observations, key=lambda item: item[0])

    @api.model
    def _rebuild_fetch_ecb_xml(self, *, backfill=False):
        provider_url = (
            ECB_FULL_RATE_URL if backfill else ECB_RECENT_RATE_URL
        )
        try:
            response = requests.get(
                provider_url,
                headers={
                    "Accept": "application/xml,text/xml",
                    "User-Agent": "USL-Odoo-Accounting/saas-19.2",
                },
                timeout=30 if backfill else 15,
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
        return payload, fields.Datetime.now(), provider_url

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
        *,
        backfill=False,
        provider_url=None,
        coverage_start=None,
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
            payload, fetched_at, provider_url = self._rebuild_fetch_ecb_xml(
                backfill=backfill,
            )
            retrieved_at = retrieved_at or fetched_at
        provider_url = provider_url or (
            ECB_FULL_RATE_URL if backfill else ECB_RECENT_RATE_URL
        )
        retrieved_at = (
            fields.Datetime.to_datetime(retrieved_at)
            if retrieved_at
            else fields.Datetime.now()
        )
        observations = self._rebuild_parse_ecb_xml(
            payload,
        )
        reference_date = observations[-1][0]
        base_code = self.currency_id.name
        if not observations[-1][1].get(base_code):
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
        coverage_start = coverage_start or self._rebuild_currency_rate_start_date(
            currencies,
            reference_date,
        )
        if self.rebuild_currency_rate_coverage_start != coverage_start:
            self.sudo().rebuild_currency_rate_coverage_start = coverage_start
        observations = [
            observation
            for observation in observations
            if observation[0] >= coverage_start
        ]
        existing_rates = Rate.search([
            ("currency_id", "in", currencies.ids),
            ("company_id", "=", self.id),
            ("name", ">=", coverage_start),
            ("name", "<=", reference_date),
        ])
        existing_by_key = {
            (rate.currency_id.id, rate.name): rate
            for rate in existing_rates
        }
        created_count = 0
        updated_count = 0
        unchanged_count = 0
        preserved_manual_count = 0
        unavailable_currency_codes = []
        covered_currency_codes = set()
        for observed_date, ecb_rates in observations:
            base_rate = ecb_rates.get(base_code)
            if not base_rate:
                continue
            for currency in currencies:
                provider_rate = ecb_rates.get(currency.name)
                if not provider_rate:
                    continue
                covered_currency_codes.add(currency.name)
                technical_rate = float(provider_rate / base_rate)
                existing = existing_by_key.get((currency.id, observed_date))
                if existing and existing.rebuild_rate_provider != "ecb":
                    preserved_manual_count += 1
                    continue
                values = {
                    "name": observed_date,
                    "currency_id": currency.id,
                    "company_id": self.id,
                    "rate": technical_rate,
                    "rebuild_rate_provider": "ecb",
                    "rebuild_rate_retrieved_at": retrieved_at,
                }
                if existing:
                    if float_compare(
                        existing.rate,
                        technical_rate,
                        precision_digits=12,
                    ):
                        existing.write(values)
                        updated_count += 1
                    else:
                        unchanged_count += 1
                else:
                    existing_by_key[currency.id, observed_date] = (
                        Rate.create(values)
                    )
                    created_count += 1

        latest_rates = observations[-1][1] if observations else {}
        unavailable_currency_codes = sorted(
            currency.name
            for currency in currencies
            if currency.name not in latest_rates
        )

        message = _(
            "ECB rates through %(date)s, covering published days from "
            "%(coverage_start)s: %(dates)s day(s) checked, %(created)s "
            "missing rate(s) created, %(updated)s corrected, %(unchanged)s "
            "already current.",
            date=fields.Date.to_string(reference_date),
            coverage_start=fields.Date.to_string(coverage_start),
            dates=len(observations),
            created=created_count,
            updated=updated_count,
            unchanged=unchanged_count,
        )
        if preserved_manual_count:
            message += " " + _(
                "%(manual)s non-ECB rate(s) preserved.",
                manual=preserved_manual_count,
            )
        if unavailable_currency_codes:
            message += " " + _(
                "No ECB rate was available for: %(currencies)s.",
                currencies=", ".join(sorted(unavailable_currency_codes)),
            )
        result = {
            "status": "passed",
            "provider": "ecb",
            "provider_url": provider_url,
            "retrieved_at": fields.Datetime.to_string(retrieved_at),
            "reference_date": fields.Date.to_string(reference_date),
            "coverage_start_date": fields.Date.to_string(coverage_start),
            "processed_reference_date_count": len(observations),
            "company_id": self.id,
            "company_currency": base_code,
            "created_count": created_count,
            "updated_count": updated_count,
            "unchanged_count": unchanged_count,
            "preserved_manual_count": preserved_manual_count,
            "covered_currency_codes": sorted(covered_currency_codes),
            "unavailable_currency_codes": unavailable_currency_codes,
            "message": message,
        }
        self.sudo().write({
            "rebuild_currency_rate_last_sync_at": retrieved_at,
            "rebuild_currency_rate_last_reference_date": reference_date,
            "rebuild_currency_rate_last_sync_status": "passed",
            "rebuild_currency_rate_last_sync_message": message,
        })
        return result

    def _rebuild_shared_rate_companies(self, *, automatic_only=False):
        self.ensure_one()
        if not self.rebuild_currency_rate_share_same_base:
            return self
        domain = [
            ("currency_id", "=", self.currency_id.id),
            ("rebuild_currency_rate_provider", "=", "ecb"),
            ("rebuild_currency_rate_share_same_base", "=", True),
        ]
        if automatic_only:
            domain.append(("rebuild_currency_rate_auto_update", "=", True))
        return self.env["res.company"].search(domain, order="id") or self

    def _rebuild_shared_currency_rate_start_date(
        self,
        companies,
        latest_reference_date,
    ):
        self.ensure_one()
        Currency = self.env["res.currency"].with_context(active_test=False)
        currencies = Currency.search([
            ("active", "=", True),
            ("id", "!=", self.currency_id.id),
        ])
        Rate = self.env["res.currency.rate"].sudo()
        configured_starts = companies.mapped(
            "rebuild_currency_rate_coverage_start",
        )
        automated_rate = Rate.search([
            ("company_id", "in", companies.ids),
            ("currency_id", "in", currencies.ids),
            ("rebuild_rate_provider", "=", "ecb"),
        ], order="name asc", limit=1)
        candidates = [date for date in configured_starts if date]
        if automated_rate:
            candidates.append(automated_rate.name)
        if candidates:
            return min(candidates)

        protected_rate = Rate.search([
            ("company_id", "in", companies.ids),
            ("currency_id", "in", currencies.ids),
            ("rebuild_rate_provider", "!=", "ecb"),
        ], order="name desc", limit=1)
        return (
            fields.Date.add(protected_rate.name, days=1)
            if protected_rate
            else latest_reference_date
        )

    def _rebuild_update_shared_ecb_currency_rates(
        self,
        payload=None,
        retrieved_at=None,
        *,
        backfill=False,
        provider_url=None,
        automatic_only=False,
    ):
        self.ensure_one()
        companies = self._rebuild_shared_rate_companies(
            automatic_only=automatic_only,
        )
        if payload is None:
            payload, fetched_at, provider_url = self._rebuild_fetch_ecb_xml(
                backfill=backfill,
            )
            retrieved_at = retrieved_at or fetched_at
        observations = self._rebuild_parse_ecb_xml(payload)
        coverage_start = self._rebuild_shared_currency_rate_start_date(
            companies,
            observations[-1][0],
        )
        results = []
        for company in companies:
            results.append(company._rebuild_update_ecb_currency_rates(
                payload=payload,
                retrieved_at=retrieved_at,
                backfill=backfill,
                provider_url=provider_url,
                coverage_start=coverage_start,
            ))
        totals = {
            key: sum(result[key] for result in results)
            for key in (
                "created_count",
                "updated_count",
                "unchanged_count",
                "preserved_manual_count",
            )
        }
        return {
            "status": "passed",
            "provider": "ecb",
            "provider_url": provider_url,
            "reference_date": results[0]["reference_date"],
            "coverage_start_date": fields.Date.to_string(coverage_start),
            "company_ids": companies.ids,
            "company_names": companies.mapped("display_name"),
            "company_results": results,
            **totals,
            "message": _(
                "ECB rates were synchronized through %(date)s for "
                "%(companies)s.",
                date=results[0]["reference_date"],
                companies=", ".join(companies.mapped("display_name")),
            ),
        }

    def _rebuild_synchronize_existing_shared_ecb_rates(self):
        """Align already retrieved provider rows without touching manual truth."""
        processed = set()
        Rate = self.env["res.currency.rate"].sudo()
        for company in self:
            if company.id in processed:
                continue
            companies = company._rebuild_shared_rate_companies()
            processed.update(companies.ids)
            if len(companies) < 2:
                continue
            source_rates = Rate.search([
                ("company_id", "in", companies.ids),
                ("rebuild_rate_provider", "=", "ecb"),
            ], order="rebuild_rate_retrieved_at, id")
            canonical_by_key = {
                (rate.currency_id.id, rate.name): rate
                for rate in source_rates
            }
            for (currency_id, rate_date), source in canonical_by_key.items():
                for target_company in companies:
                    target = Rate.search([
                        ("company_id", "=", target_company.id),
                        ("currency_id", "=", currency_id),
                        ("name", "=", rate_date),
                    ], limit=1)
                    if target and target.rebuild_rate_provider != "ecb":
                        continue
                    values = {
                        "currency_id": currency_id,
                        "company_id": target_company.id,
                        "name": rate_date,
                        "rate": source.rate,
                        "rebuild_rate_provider": "ecb",
                        "rebuild_rate_retrieved_at": (
                            source.rebuild_rate_retrieved_at
                        ),
                    }
                    if target:
                        target.write(values)
                    else:
                        Rate.create(values)
            starts = [
                start
                for start in companies.mapped(
                    "rebuild_currency_rate_coverage_start",
                )
                if start
            ]
            starts += source_rates.mapped("name")
            if starts:
                companies.sudo().write({
                    "rebuild_currency_rate_coverage_start": min(starts),
                })
        return True

    def _rebuild_currency_rate_start_date(
        self,
        currencies,
        latest_reference_date,
    ):
        self.ensure_one()
        if self.rebuild_currency_rate_coverage_start:
            return self.rebuild_currency_rate_coverage_start

        Rate = self.env["res.currency.rate"].sudo().with_company(self)
        protected_rate = Rate.search([
            ("company_id", "=", self.id),
            ("currency_id", "in", currencies.ids),
            ("rebuild_rate_provider", "!=", "ecb"),
        ], order="name desc", limit=1)
        if protected_rate:
            coverage_start = fields.Date.add(
                protected_rate.name,
                days=1,
            )
        else:
            first_automated_rate = Rate.search([
                ("company_id", "=", self.id),
                ("currency_id", "in", currencies.ids),
                ("rebuild_rate_provider", "=", "ecb"),
            ], order="name asc", limit=1)
            coverage_start = (
                first_automated_rate.name
                if first_automated_rate
                else latest_reference_date
            )
        self.sudo().rebuild_currency_rate_coverage_start = coverage_start
        return coverage_start

    @api.model
    def _cron_rebuild_update_currency_rates(self):
        companies = self.sudo().search([
            ("rebuild_currency_rate_auto_update", "=", True),
            ("rebuild_currency_rate_provider", "=", "ecb"),
        ])
        try:
            payload, retrieved_at, provider_url = (
                self._rebuild_fetch_ecb_xml()
            )
        except Exception as error:  # noqa: BLE001
            message = str(error)
            for company in companies:
                company._rebuild_record_currency_rate_failure(message)
            _logger.exception("ECB currency-rate retrieval failed")
            return True
        processed_company_ids = set()
        for company in companies:
            if company.id in processed_company_ids:
                continue
            try:
                result = company._rebuild_update_shared_ecb_currency_rates(
                    payload=payload,
                    retrieved_at=retrieved_at,
                    provider_url=provider_url,
                    automatic_only=True,
                )
                processed_company_ids.update(result["company_ids"])
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
    share_same_base = fields.Boolean(
        string="Share across same-currency companies",
        default=True,
    )
    shared_company_ids = fields.Many2many(
        "res.company",
        string="Companies kept in sync",
        readonly=True,
    )
    provider_url = fields.Char(
        string="Official Provider URL",
        default=ECB_FULL_RATE_URL,
        readonly=True,
    )
    coverage_start_date = fields.Date(
        string="Automated Coverage From",
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
            "share_same_base": (
                company.rebuild_currency_rate_share_same_base
            ),
            "shared_company_ids": [Command.set(
                company._rebuild_shared_rate_companies().ids,
            )],
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
            "coverage_start_date": (
                company.rebuild_currency_rate_coverage_start
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
            "rebuild_currency_rate_share_same_base": self.share_same_base,
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
            result = self.company_id._rebuild_update_shared_ecb_currency_rates(
                backfill=True,
            )
        except UserError as error:
            self.company_id._rebuild_record_currency_rate_failure(
                str(error),
            )
            result = {
                "status": "failed",
                "provider": self.provider,
                "provider_url": ECB_FULL_RATE_URL,
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
        companies = self.company_id._rebuild_shared_rate_companies()
        return {
            "type": "ir.actions.act_window",
            "name": _("Currency Rates"),
            "res_model": "res.currency.rate",
            "view_mode": "list,form",
            "domain": [("company_id", "in", companies.ids)],
            "context": {
                "default_company_id": self.company_id.id,
                "create": True,
            },
        }

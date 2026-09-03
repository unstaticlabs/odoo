import hashlib
import html
import json
import os
import re
from collections import Counter, defaultdict
from decimal import Decimal

import psycopg2
import psycopg2.extras
from dateutil.relativedelta import relativedelta

from odoo import Command, fields, models
from odoo.tools import float_compare, float_is_zero

RESTORE_REVISION = 3
BOOTSTRAP_SHA256 = (
    "a7617a282cb812ae051f41b5a6c15047c950bf3e8b85ef3a4014757345053791"
)
TRACE_MODELS = {
    "company": ("res.company", "res.company"),
    "partner": ("res.partner", "res.partner"),
    "product": ("product.product", "product.product"),
    "journal": ("account.journal", "account.journal"),
    "account": ("account.account", "account.account"),
    "tax": ("account.tax", "account.tax"),
    "analytic": ("account.analytic.account", "account.analytic.account"),
    "move": ("account.move", "account.move"),
    "bank_line": (
        "account.bank.statement.line",
        "account.bank.statement.line",
    ),
    "attachment": ("ir.attachment", "ir.attachment"),
}


def validate_source_identity(options):
    """Require a real SHA-256 and a snapshot derived from that exact dump."""
    source_dump_sha256 = options.get("source_dump_sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", source_dump_sha256):
        message = "The Platform Billing source dump SHA-256 is invalid."
        raise RuntimeError(message)
    if options.get("snapshot") != f"source-{source_dump_sha256[:12]}":
        message = "The Platform Billing source snapshot is not dump-bound."
        raise RuntimeError(message)
    return source_dump_sha256


def _normalized(value):
    if value is False or value is None:
        return None
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        return format(Decimal(str(value)).normalize(), "f")
    if isinstance(value, dict):
        return {
            str(key): _normalized(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set)):
        return [_normalized(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat(sep=" ")
        except TypeError:
            return value.isoformat()
    return value


def canonical_digest(value):
    return hashlib.sha256(
        json.dumps(
            _normalized(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode(),
    ).hexdigest()


def _record_id(record):
    return record.id if record else False


def _source_text(value):
    if not isinstance(value, dict):
        return value
    return (
        value.get("en_US")
        or value.get("fr_FR")
        or next((item for item in value.values() if item), None)
    )


def _source_property_ids(value):
    if not isinstance(value, dict):
        return {int(value)} if value else set()
    return {int(item) for item in value.values() if item}


class UslPlatformBillingRestoreRun(models.Model):
    _name = "usl.platform.billing.restore.run"
    _description = "USL Platform Billing Historical Restore Run"
    _order = "started_at desc, id desc"

    name = fields.Char(required=True, default="Platform billing restoration")
    status = fields.Selection(
        [
            ("running", "Running"),
            ("passed", "Passed"),
            ("failed", "Failed"),
        ],
        required=True,
        default="running",
        index=True,
    )
    source_database = fields.Char(required=True, index=True)
    source_snapshot = fields.Char(required=True, index=True)
    source_dump_sha256 = fields.Char(required=True)
    bootstrap_sha256 = fields.Char(required=True)
    target_database = fields.Char(required=True, index=True)
    started_at = fields.Datetime(default=fields.Datetime.now, required=True)
    finished_at = fields.Datetime()
    platform_count = fields.Integer(readonly=True)
    session_count = fields.Integer(readonly=True)
    payout_count = fields.Integer(readonly=True)
    move_count = fields.Integer(readonly=True)
    attachment_count = fields.Integer(readonly=True)
    issue_count = fields.Integer(readonly=True)
    statistics_json = fields.Json(readonly=True)
    issue_ids = fields.One2many(
        "usl.platform.billing.restore.issue",
        "run_id",
        readonly=True,
    )

    def _issue(self, source_model, source_id, description, severity="error"):
        self.ensure_one()
        return self.env["usl.platform.billing.restore.issue"].create(
            {
                "run_id": self.id,
                "severity": severity,
                "source_model": source_model,
                "source_id": source_id or 0,
                "description": description,
            },
        )

    def _trace_values(self, source_model, source_id, snapshot):
        return {
            "rebuild_source_database": self.source_database,
            "rebuild_source_model": source_model,
            "rebuild_source_id": source_id,
            "rebuild_source_snapshot": snapshot,
            "rebuild_import_status": "imported",
            "rebuild_import_note": (
                f"Restored by Platform Billing run {self.id}, revision "
                f"{RESTORE_REVISION} from {self.source_database}."
            ),
        }

    def _traced(self, target_model, source_model, source_ids):
        ids = sorted({source_id for source_id in source_ids if source_id})
        if not ids:
            return {}
        records = (
            self.env[target_model]
            .sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("rebuild_source_model", "=", source_model),
                    ("rebuild_source_id", "in", ids),
                ],
                order="id",
            )
        )
        result = {}
        for record in records:
            source_id = record.rebuild_source_id
            if source_id in result:
                self._issue(
                    source_model,
                    source_id,
                    "Multiple target records carry this source identity.",
                )
            else:
                result[source_id] = record
        return result

    def _dependency_maps(self, payload):
        maps = {}
        product_rows = payload.get("products", [])
        user_rows = payload["users"]
        source_ids = {
            "company": ({
                row["x_company_id"]
                for row in payload["sessions"]
                if row["x_company_id"]
            } | {
                value
                for row in user_rows
                for value in (
                    row.get("company_id"),
                    *(row.get("company_ids") or []),
                )
                if value
            }),
            "partner": ({
                value
                for row in payload["platforms"]
                for value in (
                    row["x_partner_id"],
                    row["x_customer_partner_id"],
                    row["x_supplier_partner_id"],
                )
                if value
            } | {
                row["partner_id"] for row in user_rows if row.get("partner_id")
            }),
            "product": {
                value
                for row in payload["platforms"]
                for value in (
                    row["x_revenue_product_id"],
                    row["x_commission_product_id"],
                )
                if value
            },
            "account": {
                source_id
                for row in product_rows
                for field_name in (
                    "property_account_income_id",
                    "property_account_expense_id",
                )
                for source_id in _source_property_ids(row.get(field_name))
            },
            "tax": {
                source_id
                for row in product_rows
                for field_name in ("tax_ids", "supplier_tax_ids")
                for source_id in (row.get(field_name) or [])
            },
            "journal": {
                value
                for row in payload["platforms"]
                for value in (
                    row["x_sale_journal_id"],
                    row["x_purchase_journal_id"],
                    row["x_compensation_journal_id"],
                    row["x_bank_journal_id"],
                )
                if value
            },
            "analytic": {
                row["x_analytic_account_id"]
                for row in payload["platforms"]
                if row["x_analytic_account_id"]
            },
            "move": {row["id"] for row in payload["moves"]},
            "bank_line": {
                row["x_bank_statement_line_id"]
                for row in payload["payouts"]
                if row["x_bank_statement_line_id"]
            },
            "attachment": {
                row["attachment_id"] for row in payload["attachment_links"]
            },
        }
        for key, (target_model, source_model) in TRACE_MODELS.items():
            maps[key] = self._traced(
                target_model,
                source_model,
                source_ids[key],
            )
            if key == "product":
                continue
            for source_id in sorted(source_ids[key] - set(maps[key])):
                self._issue(
                    source_model,
                    source_id,
                    f"No reconstructed {target_model} dependency carries this source identity.",
                )
        currency_names = {
            row["id"]: row["name"] for row in payload["currencies"]
        }
        currency_ids = {
            value
            for row in payload["platforms"]
            for value in (row["x_currency_id"],)
            if value
        } | {
            row["x_bank_currency_id"]
            for row in payload["sessions"]
            if row["x_bank_currency_id"]
        } | {
            row["x_platform_currency_id"]
            for row in payload["payouts"]
            if row["x_platform_currency_id"]
        }
        maps["currency"] = {}
        for source_id in sorted(currency_ids):
            name = currency_names.get(source_id)
            currency = (
                self.env["res.currency"]
                .sudo()
                .with_context(active_test=False)
                .search([("name", "=", name)], limit=1)
                if name
                else self.env["res.currency"]
            )
            if currency:
                maps["currency"][source_id] = currency
            else:
                self._issue(
                    "res.currency",
                    source_id,
                    f"No target currency matches source currency {name!r}.",
                )
        self._restore_product_dependencies(
            product_rows,
            source_ids["product"],
            maps,
        )
        maps["user"] = {}
        for row in user_rows:
            user = (
                self.env["res.users"]
                .sudo()
                .with_context(active_test=False)
                .search([("login", "=", row["login"])], limit=1)
            )
            partner = maps["partner"].get(row.get("partner_id"))
            if not user and partner:
                partner_users = (
                    self.env["res.users"]
                    .sudo()
                    .with_context(active_test=False)
                    .search([("partner_id", "=", partner.id)])
                )
                if len(partner_users) > 1:
                    self._issue(
                        "res.users",
                        row["id"],
                        (
                            "Multiple target users are linked to source "
                            f"partner {row['partner_id']}."
                        ),
                    )
                elif partner_users:
                    user = partner_users
            if not user and partner:
                company = maps["company"].get(row.get("company_id"))
                allowed_companies = self.env["res.company"].browse(
                    [
                        maps["company"][source_id].id
                        for source_id in row.get("company_ids") or []
                        if source_id in maps["company"]
                    ],
                )
                if not company and allowed_companies:
                    company = allowed_companies[0]
                if company and not allowed_companies:
                    allowed_companies = company
                if company:
                    user = (
                        self.env["res.users"]
                        .sudo()
                        .with_context(
                            active_test=False,
                            no_reset_password=True,
                            mail_create_nosubscribe=True,
                            tracking_disable=True,
                        )
                        .create(
                            {
                                "login": row["login"],
                                "partner_id": partner.id,
                                # res.users.create synchronizes the user's
                                # active flag to the reused partner.  Create
                                # active first so a contact already used by
                                # another active user is never archived, then
                                # immediately archive only this historical
                                # user below.
                                "active": True,
                                "share": False,
                                "company_id": company.id,
                                "company_ids": [
                                    Command.set(allowed_companies.ids),
                                ],
                                "group_ids": [Command.clear()],
                            },
                        )
                    )
                    user.write({"active": False})
            if user:
                maps["user"][row["id"]] = user
            else:
                self._issue(
                    "res.users",
                    row["id"],
                    f"No target user matches source login {row['login']!r}.",
                )
        return maps

    def _restore_product_dependencies(self, rows, required_ids, maps):
        row_by_id = {row["id"]: row for row in rows}
        Product = (
            self.env["product.product"]
            .sudo()
            .with_context(active_test=False, tracking_disable=True)
        )
        for source_id in sorted(required_ids - set(maps["product"])):
            row = row_by_id.get(source_id)
            if not row:
                self._issue(
                    "product.product",
                    source_id,
                    "The referenced source product was not extracted.",
                )
                continue
            uom = self.env.ref(
                f"{row['uom_module']}.{row['uom_xml_name']}",
                raise_if_not_found=False,
            )
            category = self.env.ref(
                f"{row['category_module']}.{row['category_xml_name']}",
                raise_if_not_found=False,
            )
            if not uom or not category:
                self._issue(
                    "product.product",
                    source_id,
                    "The source product UoM or category XML identity is unresolved.",
                )
                continue
            account_ids = _source_property_ids(
                row.get("property_account_income_id"),
            ) | _source_property_ids(row.get("property_account_expense_id"))
            tax_ids = set(row.get("tax_ids") or []) | set(
                row.get("supplier_tax_ids") or [],
            )
            if account_ids - set(maps["account"]) or tax_ids - set(maps["tax"]):
                continue
            values = {
                "name": _source_text(row["name"])
                or f"Source platform product {source_id}",
                "default_code": row.get("default_code"),
                "type": row.get("type") or "service",
                "uom_id": uom.id,
                "categ_id": category.id,
                "sale_ok": bool(row.get("sale_ok")),
                "purchase_ok": bool(row.get("purchase_ok")),
                "active": bool(row.get("active")),
                **self._trace_values(
                    "product.product",
                    source_id,
                    self.source_snapshot,
                ),
            }
            product = Product.create(values)
            product.write(
                {
                    "taxes_id": [
                        Command.set(
                            [
                                maps["tax"][tax_id].id
                                for tax_id in row.get("tax_ids") or []
                            ],
                        ),
                    ],
                    "supplier_taxes_id": [
                        Command.set(
                            [
                                maps["tax"][tax_id].id
                                for tax_id in row.get("supplier_tax_ids") or []
                            ],
                        ),
                    ],
                },
            )
            for field_name in (
                "property_account_income_id",
                "property_account_expense_id",
            ):
                for company_source_id, account_source_id in (
                    row.get(field_name) or {}
                ).items():
                    company = maps["company"].get(int(company_source_id))
                    account = maps["account"].get(account_source_id)
                    if company and account:
                        product.product_tmpl_id.with_company(company)[
                            field_name
                        ] = account
            maps["product"][source_id] = product

    def _preflight(self, payload):
        for model_name, rows in (
            ("x_content_platform", payload["platforms"]),
            ("x_content_billing_session", payload["sessions"]),
            ("x_content_payout_line", payload["payouts"]),
        ):
            duplicates = [
                source_id
                for source_id, count in Counter(row["id"] for row in rows).items()
                if count > 1
            ]
            for source_id in duplicates:
                self._issue(
                    model_name,
                    source_id,
                    "The source identity occurs more than once.",
                )
        payout_refs = Counter(
            (
                row["x_platform_id"],
                (row["x_platform_reference"] or "").strip(),
            )
            for row in payload["payouts"]
            if (row["x_platform_reference"] or "").strip()
        )
        for (platform_id, reference), count in payout_refs.items():
            if count > 1:
                self._issue(
                    "x_content_payout_line",
                    0,
                    (
                        f"Reference {reference!r} occurs {count} times for "
                        f"source platform {platform_id}."
                    ),
                )
        session_ids = {row["id"] for row in payload["sessions"]}
        platform_ids = {row["id"] for row in payload["platforms"]}
        for row in payload["payouts"]:
            if row["x_session_id"] not in session_ids:
                self._issue(
                    "x_content_payout_line",
                    row["id"],
                    "The source session is missing.",
                )
            if row["x_platform_id"] not in platform_ids:
                self._issue(
                    "x_content_payout_line",
                    row["id"],
                    "The source platform is missing.",
                )
            if not (row["x_platform_reference"] or "").strip():
                self._issue(
                    "x_content_payout_line",
                    row["id"],
                    "The source platform reference is empty.",
                )
            if not 0.0 < (row["x_commission_rate_snapshot"] or 0.0) < 100.0:
                self._issue(
                    "x_content_payout_line",
                    row["id"],
                    "The source commission snapshot is outside 0%–100%.",
                )
            if (row["x_net_platform_amount"] or 0.0) <= 0:
                self._issue(
                    "x_content_payout_line",
                    row["id"],
                    "The source net amount is not positive.",
                )
        for row in payload["platforms"]:
            if not 0.0 < (row["x_commission_rate"] or 0.0) < 100.0:
                self._issue(
                    "x_content_platform",
                    row["id"],
                    "The source commission rate is outside 0%–100%.",
                )

    def _preflight_bank_allocations(self, payload, maps):
        session_rows = {row["id"]: row for row in payload["sessions"]}
        allocations_by_bank_line = defaultdict(list)
        for row in payload["payouts"]:
            if row["x_bank_statement_line_id"]:
                allocations_by_bank_line[row["x_bank_statement_line_id"]].append(
                    row,
                )
        for source_bank_line_id, rows in allocations_by_bank_line.items():
            bank_line = maps["bank_line"].get(source_bank_line_id)
            if not bank_line:
                continue
            if bank_line.amount <= 0:
                self._issue(
                    "account.bank.statement.line",
                    source_bank_line_id,
                    "The mapped bank transaction is not incoming.",
                )
            total = 0.0
            for row in rows:
                amount = row["x_bank_received_amount"] or 0.0
                if amount <= 0:
                    self._issue(
                        "x_content_payout_line",
                        row["id"],
                        "A linked payout has no positive bank allocation.",
                    )
                total += amount
                session_row = session_rows.get(row["x_session_id"])
                if not session_row:
                    continue
                company = maps["company"].get(session_row["x_company_id"])
                currency = maps["currency"].get(session_row["x_bank_currency_id"])
                if company and bank_line.company_id != company:
                    self._issue(
                        "x_content_payout_line",
                        row["id"],
                        "The mapped bank transaction belongs to another company.",
                    )
                if currency and bank_line.currency_id != currency:
                    self._issue(
                        "x_content_payout_line",
                        row["id"],
                        "The mapped bank transaction uses another bank currency.",
                    )
            if (
                float_compare(
                    total,
                    bank_line.amount,
                    precision_rounding=bank_line.currency_id.rounding,
                )
                > 0
            ):
                self._issue(
                    "account.bank.statement.line",
                    source_bank_line_id,
                    (
                        f"Source payout allocations total {total}, above the "
                        f"mapped bank transaction amount {bank_line.amount}."
                    ),
                )

    def _has_errors(self):
        return bool(
            self.env["usl.platform.billing.restore.issue"].search_count(
                [
                    ("run_id", "=", self.id),
                    ("severity", "=", "error"),
                    ("resolved", "=", False),
                ],
            ),
        )

    @staticmethod
    def _target_value(record, field_name):
        field = record._fields[field_name]
        value = record[field_name]
        if field.type == "many2one":
            return value.id or False
        if field.type in {"many2many", "one2many"}:
            return sorted(value.ids)
        return value

    def _adopt_finalized_record(
        self,
        target_model,
        source_model,
        row,
        domain,
        expected_values,
    ):
        model = (
            self.env[target_model]
            .sudo()
            .with_context(active_test=False, tracking_disable=True)
        )
        records = model.search(
            [*domain, ("rebuild_source_id", "=", False)],
            order="id",
        )
        if len(records) > 1:
            self._issue(
                source_model,
                row["id"],
                (
                    "Multiple finalized target records match the durable "
                    f"business identity {domain!r}."
                ),
            )
            return model.browse()
        if not records:
            return None
        record = records
        differences = []
        for field_name, expected in expected_values.items():
            current = self._target_value(record, field_name)
            field = record._fields[field_name]
            if field.type in {"json", "many2many", "one2many"} and not (
                current or expected
            ):
                continue
            if _normalized(current) != _normalized(expected):
                differences.append(field_name)
        if differences:
            self._issue(
                source_model,
                row["id"],
                (
                    "A finalized target record has the same durable business "
                    "identity but differs in protected fields: "
                    f"{', '.join(sorted(differences))}."
                ),
            )
            return model.browse()
        record.write(
            self._trace_values(
                source_model,
                row["id"],
                self.source_snapshot,
            ),
        )
        return record

    def _upsert(
        self,
        target_model,
        source_model,
        row,
        values,
        *,
        finalized_domain=None,
        finalized_values=None,
    ):
        record = self._traced(target_model, source_model, [row["id"]]).get(
            row["id"],
        )
        adopted = False
        if not record and finalized_domain:
            finalized_record = self._adopt_finalized_record(
                target_model,
                source_model,
                row,
                finalized_domain,
                finalized_values or {},
            )
            if finalized_record is not None:
                if not finalized_record:
                    return finalized_record
                record = finalized_record
                adopted = True
        values = {
            **values,
            **self._trace_values(source_model, row["id"], self.source_snapshot),
        }
        model = (
            self.env[target_model]
            .sudo()
            .with_context(
                active_test=False,
                tracking_disable=True,
                mail_create_nolog=True,
                mail_create_nosubscribe=True,
            )
        )
        if adopted:
            return record
        if record:
            protected_by_model = {
                "usl.platform.billing.session": {
                    "company_id",
                    "period_month",
                    "invoice_date",
                    "due_date",
                    "bank_currency_id",
                },
                "usl.platform.billing.payout": {
                    "session_id",
                    "platform_id",
                    "payout_date",
                    "platform_reference",
                    "platform_currency_id",
                    "net_platform_amount",
                    "commission_rate_snapshot",
                },
            }
            protected_fields = protected_by_model.get(target_model, set())
            if protected_fields and record.state not in {"draft", "ready"}:
                for field_name in protected_fields:
                    if field_name not in values:
                        continue
                    current = record[field_name]
                    current_value = (
                        current.id
                        if record._fields[field_name].type == "many2one"
                        else current
                    )
                    if _normalized(current_value) != _normalized(
                        values[field_name],
                    ):
                        self._issue(
                            source_model,
                            row["id"],
                            (
                                f"Revision {RESTORE_REVISION} would change "
                                f"protected field {field_name!r} on an "
                                "already-generated record."
                            ),
                        )
                    values.pop(field_name)
            if target_model in {
                "usl.platform.billing.session",
                "usl.platform.billing.payout",
            }:
                record._workflow_write(values)
            else:
                record.write(values)
        else:
            record = model.create(values)
        return record

    @staticmethod
    def _platform_companies(payload):
        session_company = {
            row["id"]: row["x_company_id"] for row in payload["sessions"]
        }
        result = defaultdict(set)
        for row in payload["payouts"]:
            result[row["x_platform_id"]].add(
                session_company.get(row["x_session_id"]),
            )
        return result

    def _analytic_distribution(self, row, maps):
        distribution = {}
        raw = row.get("x_analytic_distribution_json")
        if raw:
            try:
                source_distribution = json.loads(raw)
            except (TypeError, ValueError):
                self._issue(
                    "x_content_platform",
                    row["id"],
                    "The source analytic distribution JSON is invalid.",
                )
                return {}
            for source_id, percentage in source_distribution.items():
                target = maps["analytic"].get(int(source_id))
                if not target:
                    self._issue(
                        "x_content_platform",
                        row["id"],
                        f"Analytic account {source_id} is not mapped.",
                    )
                else:
                    distribution[str(target.id)] = percentage
        elif row.get("x_analytic_account_id"):
            target = maps["analytic"].get(row["x_analytic_account_id"])
            if target:
                distribution[str(target.id)] = 100.0
        return distribution

    def _restore_platforms(self, payload, maps):
        company_sources = self._platform_companies(payload)
        restored = {}
        for row in payload["platforms"]:
            companies = company_sources[row["id"]] - {None}
            if len(companies) != 1:
                self._issue(
                    "x_content_platform",
                    row["id"],
                    (
                        "The platform cannot be assigned to exactly one source "
                        f"company: {sorted(companies)}."
                    ),
                )
                continue
            company = maps["company"].get(next(iter(companies)))
            values = {
                "name": row["x_name"],
                "active": (
                    row["x_active"] if row["x_active"] is not None else True
                ),
                "company_id": company.id if company else False,
                "partner_id": _record_id(
                    maps["partner"].get(row["x_partner_id"]),
                ),
                "customer_partner_id": _record_id(
                    maps["partner"].get(row["x_customer_partner_id"]),
                ),
                "supplier_partner_id": _record_id(
                    maps["partner"].get(row["x_supplier_partner_id"]),
                ),
                "commission_rate": row["x_commission_rate"],
                "currency_id": _record_id(
                    maps["currency"].get(row["x_currency_id"]),
                ),
                "revenue_product_id": _record_id(
                    maps["product"].get(row["x_revenue_product_id"]),
                ),
                "commission_product_id": _record_id(
                    maps["product"].get(row["x_commission_product_id"]),
                ),
                "sale_journal_id": _record_id(
                    maps["journal"].get(row["x_sale_journal_id"]),
                ),
                "purchase_journal_id": _record_id(
                    maps["journal"].get(row["x_purchase_journal_id"]),
                ),
                "compensation_journal_id": _record_id(
                    maps["journal"].get(row["x_compensation_journal_id"]),
                ),
                "bank_journal_id": _record_id(
                    maps["journal"].get(row["x_bank_journal_id"]),
                ),
                "bank_label_pattern": row["x_bank_label_pattern"],
                "bank_label_keywords": row["x_bank_label_keywords"],
                "bank_match_days_tolerance": (
                    row["x_bank_match_days_tolerance"] or 15
                ),
                "bank_match_amount_tolerance": (
                    row["x_bank_match_amount_tolerance"] or 0.0
                ),
                "analytic_distribution": self._analytic_distribution(row, maps),
                "vendor_bill_grouping_mode": (
                    row["x_vendor_bill_grouping_mode"] or "monthly"
                ),
                "auto_post_invoices": bool(row["x_auto_post_invoices"]),
                "auto_create_compensation": bool(
                    row["x_auto_create_compensation"],
                ),
            }
            record = self._upsert(
                "usl.platform.billing.platform",
                "x_content_platform",
                row,
                values,
                finalized_domain=[
                    ("company_id", "=", company.id),
                    ("name", "=", row["x_name"]),
                ],
                finalized_values=values,
            )
            if record:
                restored[row["id"]] = record
        return restored

    def _restore_sessions(self, payload, maps):
        restored = {}
        for row in payload["sessions"]:
            company = maps["company"].get(row["x_company_id"])
            currency = maps["currency"].get(row["x_bank_currency_id"])
            if not company or not currency:
                continue
            values = {
                "name": row["x_name"],
                "company_id": company.id,
                "period_month": row["x_period_month"],
                "invoice_date": row["x_invoice_date"],
                "bank_currency_id": currency.id,
                "state": "draft",
                "generated_at": row["x_generated_at"],
                "generated_by_id": _record_id(
                    maps["user"].get(row["x_generated_by_id"]),
                ),
            }
            # Preserve an explicit legacy override. When none exists, let each
            # generated document use its partner's native payment term.
            values["due_date"] = row["x_due_date"] or False
            finalized_domain = [
                ("company_id", "=", company.id),
                ("period_month", "=", row["x_period_month"]),
                ("name", "=", row["x_name"]),
            ]
            existing = self._traced(
                "usl.platform.billing.session",
                "x_content_billing_session",
                [row["id"]],
            ).get(row["id"])
            if not existing:
                existing = (
                    self.env["usl.platform.billing.session"]
                    .sudo()
                    .with_context(active_test=False)
                    .search(
                        [
                            *finalized_domain,
                            ("rebuild_source_id", "=", False),
                        ],
                        limit=2,
                    )
                )
            if not row["x_due_date"] and len(existing) == 1 and existing.due_date:
                legacy_due_date = row["x_period_month"] + relativedelta(
                    months=1,
                    days=-1,
                )
                if existing.due_date == legacy_due_date:
                    existing._workflow_write({"due_date": False})
                else:
                    self._issue(
                        "x_content_billing_session",
                        row["id"],
                        (
                            "The source has no due-date override, but the "
                            "target due date is not the retired month-end "
                            "default."
                        ),
                    )
                    continue
            record = self._upsert(
                "usl.platform.billing.session",
                "x_content_billing_session",
                row,
                values,
                finalized_domain=finalized_domain,
                finalized_values={
                    field_name: value
                    for field_name, value in values.items()
                    if field_name != "state"
                },
            )
            if record:
                restored[row["id"]] = record
        return restored

    def _restore_payouts(self, payload, maps, platforms, sessions):
        restored = {}
        for row in payload["payouts"]:
            session = sessions.get(row["x_session_id"])
            platform = platforms.get(row["x_platform_id"])
            currency = maps["currency"].get(row["x_platform_currency_id"])
            if not session or not platform or not currency:
                continue
            bank_line = maps["bank_line"].get(
                row["x_bank_statement_line_id"],
            )
            values = {
                "session_id": session.id,
                "platform_id": platform.id,
                "payout_date": row["x_payout_date"],
                "platform_reference": (
                    row["x_platform_reference"] or ""
                ).strip(),
                "platform_currency_id": currency.id,
                "net_platform_amount": row["x_net_platform_amount"],
                "commission_rate_snapshot": row[
                    "x_commission_rate_snapshot"
                ],
                "state": "draft",
            }
            record = self._upsert(
                "usl.platform.billing.payout",
                "x_content_payout_line",
                row,
                values,
                finalized_domain=[
                    ("company_id", "=", session.company_id.id),
                    ("platform_id", "=", platform.id),
                    (
                        "platform_reference",
                        "=",
                        values["platform_reference"],
                    ),
                ],
                finalized_values={
                    field_name: value
                    for field_name, value in values.items()
                    if field_name != "state"
                },
            )
            if record:
                if bank_line:
                    payout_amount = (
                        row["x_net_platform_amount"]
                        if row.get("x_bank_match_status") == "reconciled"
                        else (
                            row["x_bank_received_amount"]
                            if currency == session.bank_currency_id
                            else row["x_net_platform_amount"]
                        )
                    )
                    allocation = self.env[
                        "usl.platform.billing.bank.allocation"
                    ].sudo().search(
                        [
                            ("payout_id", "=", record.id),
                            ("bank_statement_line_id", "=", bank_line.id),
                        ],
                        limit=1,
                    )
                    allocation_values = {
                        "bank_amount": row["x_bank_received_amount"],
                        "payout_amount": payout_amount,
                        "score": int(row["x_bank_match_score"] or 0),
                        "amount_difference": row[
                            "x_bank_amount_difference"
                        ],
                        "date_difference": row[
                            "x_bank_date_difference"
                        ],
                        "detection_reason": row[
                            "x_bank_detection_reason"
                        ],
                    }
                    if allocation:
                        allocation.sudo().write(allocation_values)
                    else:
                        self.env[
                            "usl.platform.billing.bank.allocation"
                        ].sudo()._action_create(
                            {
                                "payout_id": record.id,
                                "bank_statement_line_id": bank_line.id,
                                **allocation_values,
                            },
                        )
                restored[row["id"]] = record
        return restored

    @staticmethod
    def _ledger_rows(moves):
        return [
            {
                "source_id": move.rebuild_source_id,
                "name": move.name,
                "date": move.date,
                "move_type": move.move_type,
                "state": move.state,
                "currency": move.currency_id.name,
                "lines": [
                    {
                        "source_id": line.rebuild_source_id,
                        "account": line.account_id.code,
                        "partner": line.partner_id.rebuild_source_id,
                        "debit": line.debit,
                        "credit": line.credit,
                        "balance": line.balance,
                        "currency": line.currency_id.name,
                        "amount_currency": line.amount_currency,
                    }
                    for line in move.line_ids.sorted(
                        key=lambda item: (
                            item.rebuild_source_id or 0,
                            item.id,
                        ),
                    )
                ],
            }
            for move in moves.sorted(key=lambda item: item.rebuild_source_id)
        ]

    def _link_documents(self, payload, maps, platforms, sessions, payouts):
        payout_by_source_move = defaultdict(set)
        move_roles = {
            "x_customer_invoice_id": "customer_invoice_id",
            "x_vendor_bill_id": "vendor_bill_id",
            "x_compensation_move_id": "compensation_move_id",
        }
        for row in payload["payouts"]:
            payout = payouts.get(row["id"])
            if not payout:
                continue
            values = {}
            for source_field, target_field in move_roles.items():
                source_move_id = row[source_field]
                move = maps["move"].get(source_move_id)
                if source_move_id and not move:
                    self._issue(
                        "x_content_payout_line",
                        row["id"],
                        f"Generated move {source_move_id} is not mapped.",
                    )
                values[target_field] = _record_id(move)
                if move:
                    payout_by_source_move[source_move_id].add(payout.id)
            payout.with_context(tracking_disable=True).write(values)

        session_rows = {row["id"]: row for row in payload["sessions"]}
        platform_rows = {row["id"]: row for row in payload["platforms"]}
        for row in payload["moves"]:
            move = maps["move"].get(row["id"])
            if not move:
                continue
            session = sessions.get(row["x_content_billing_session_id"])
            platform = platforms.get(row["x_content_platform_id"])
            if not session:
                self._issue(
                    "account.move",
                    row["id"],
                    "The generated move has no mapped platform-billing session.",
                )
                continue
            if not platform:
                self._issue(
                    "account.move",
                    row["id"],
                    "The generated move has no mapped content platform.",
                )
                continue
            if row["x_content_billing_session_id"] not in session_rows:
                continue
            if row["x_content_platform_id"] not in platform_rows:
                continue
            move.write(
                {
                    "platform_billing_session_id": session.id,
                    "platform_billing_platform_id": platform.id,
                    "platform_billing_payout_ids": [
                        Command.set(
                            sorted(payout_by_source_move.get(row["id"], set())),
                        ),
                    ],
                },
            )

    def _restore_attachments(self, payload, maps, payouts):
        links = defaultdict(list)
        attachment_rows = {
            row["id"]: row for row in payload["attachments"]
        }
        for row in payload["attachment_links"]:
            attachment = maps["attachment"].get(row["attachment_id"])
            payout = payouts.get(row["x_payout_line_id"])
            if not attachment or not payout:
                continue
            source = attachment_rows.get(row["attachment_id"])
            if source and source["checksum"] != attachment.checksum:
                self._issue(
                    "ir.attachment",
                    source["id"],
                    (
                        f"Attachment checksum differs: source "
                        f"{source['checksum']}, target {attachment.checksum}."
                    ),
                )
                continue
            links[payout.id].append(attachment.id)
        for payout_id, attachment_ids in links.items():
            (
                self.env["usl.platform.billing.payout"]
                .sudo()
                .browse(payout_id)
                .write(
                    {"attachment_ids": [Command.set(sorted(attachment_ids))]},
                )
            )

    @staticmethod
    def _document_paid(move):
        return bool(
            move
            and move.state == "posted"
            and (
                move.payment_state in {"paid", "reversed"}
                or float_is_zero(
                    move.amount_residual,
                    precision_rounding=move.currency_id.rounding,
                )
            ),
        )

    def _derive_states(self, payload, sessions, payouts):
        session_rows = {row["id"]: row for row in payload["sessions"]}
        payout_rows = {row["id"]: row for row in payload["payouts"]}
        for source_id, payout in payouts.items():
            row = payout_rows[source_id]
            legacy_context = {
                row.get("x_state"),
                row.get("x_bank_match_status"),
                row.get("x_validation_status"),
            }
            recognized_states = {
                "draft",
                "generated",
                "posted",
                "paid",
                "cancelled",
                "reconciled",
            }
            for value in sorted(legacy_context - recognized_states - {None, ""}):
                self._issue(
                    "x_content_payout_line",
                    source_id,
                    f"Legacy status {value!r} was retained only as context.",
                    severity="info",
                )
            documents = (
                payout.customer_invoice_id
                | payout.vendor_bill_id
                | payout.compensation_move_id
            )
            if row.get("x_state") == "cancelled":
                state = "cancelled"
            elif not documents:
                state = "draft"
            elif documents.filtered(lambda move: move.state == "draft"):
                state = "generated"
            elif all(move.state == "posted" for move in documents):
                required_paid = self._document_paid(
                    payout.customer_invoice_id,
                ) and self._document_paid(payout.vendor_bill_id)
                bank_paid = bool(
                    payout.bank_match_status == "reconciled",
                )
                state = "paid" if required_paid and bank_paid else "posted"
            else:
                state = "draft"
            payout.with_context(tracking_disable=True).write({"state": state})
            bank_lines_reconciled = bool(payout.bank_allocation_ids) and all(
                payout.bank_allocation_ids.bank_statement_line_id.mapped(
                    "is_reconciled",
                ),
            )
            if (
                row.get("x_bank_match_status") == "reconciled"
                and bank_lines_reconciled
                and payout.bank_match_status != "reconciled"
            ):
                self._issue(
                    "x_content_payout_line",
                    source_id,
                    (
                        "The source payout is reconciled but the restored bank "
                        f"status is {payout.bank_match_status!r}."
                    ),
                )
            if (
                row.get("x_state") == "reconciled"
                and bank_lines_reconciled
                and self._document_paid(payout.customer_invoice_id)
                and self._document_paid(payout.vendor_bill_id)
                and state != "paid"
            ):
                self._issue(
                    "x_content_payout_line",
                    source_id,
                    (
                        "The source payout is reconciled but the restored "
                        f"workflow state is {state!r}."
                    ),
                )
        for source_id, session in sessions.items():
            row = session_rows[source_id]
            documents = session.generated_move_ids
            if row.get("x_state") == "cancelled":
                state = "cancelled"
            elif not documents:
                state = "ready" if session.payout_ids else "draft"
            elif documents.filtered(lambda move: move.state == "draft"):
                state = "generated"
            elif all(move.state == "posted" for move in documents):
                state = (
                    "paid"
                    if session.payout_ids
                    and all(
                        payout.state == "paid" for payout in session.payout_ids
                    )
                    else "posted"
                )
            else:
                state = "draft"
            session.with_context(tracking_disable=True).write({"state": state})

    def _post_legacy_notes(self, payload, sessions):
        payouts_by_session = defaultdict(list)
        for row in payload["payouts"]:
            payouts_by_session[row["x_session_id"]].append(row)
        for row in payload["sessions"]:
            session = sessions.get(row["id"])
            if not session:
                continue
            subject = (
                "Imported platform billing history "
                f"[x_content_billing_session:{row['id']}:r{RESTORE_REVISION}]"
            )
            subject_marker = f"[x_content_billing_session:{row['id']}:"
            message = self.env["mail.message"].sudo().search(
                [
                    ("model", "=", session._name),
                    ("res_id", "=", session.id),
                    ("subject", "ilike", subject_marker),
                ],
                limit=1,
            )
            sections = []
            for label, source_field in (
                ("Legacy state", "x_state"),
                ("Last error", "x_last_error"),
                ("Validation log", "x_validation_log"),
                ("Warning summary", "x_warning_summary"),
                ("Bank blocker", "x_bank_reconcile_blocker"),
            ):
                if row.get(source_field):
                    sections.append(
                        f"<p><strong>{html.escape(label)}:</strong><br/>"
                        f"{html.escape(str(row[source_field])).replace(chr(10), '<br/>')}</p>",
                    )
            payout_context = [
                (
                    f"{item['x_platform_reference']}: state={item['x_state']}, "
                    f"bank={item['x_bank_match_status']}, "
                    f"validation={item['x_validation_status']}"
                    + (
                        f", message={item['x_validation_message']}"
                        if item["x_validation_message"]
                        else ""
                    )
                )
                for item in payouts_by_session[row["id"]]
            ]
            if payout_context:
                sections.append(
                    "<p><strong>Payout legacy context:</strong><br/>"
                    + "<br/>".join(html.escape(item) for item in payout_context)
                    + "</p>",
                )
            body = (
                "<p><strong>Imported internal note.</strong> This text is "
                "historical context from the retired Studio application; it "
                "does not define the current workflow state.</p>"
                + "".join(sections)
            )
            if message:
                message.write({"body": body, "subject": subject})
            else:
                session.message_post(
                    body=body,
                    subject=subject,
                    subtype_xmlid="mail.mt_note",
                )

    def _stamp_audit(self, record, row, user_map):
        create_user = user_map.get(row.get("create_uid")) or self.env.user
        write_user = user_map.get(row.get("write_uid")) or create_user
        self.env.cr.execute(
            f"""
                UPDATE {record._table}
                   SET create_date = COALESCE(%s, create_date),
                       write_date = COALESCE(%s, write_date),
                       create_uid = %s,
                       write_uid = %s
                 WHERE id = %s
            """,
            (
                row.get("create_date"),
                row.get("write_date"),
                create_user.id,
                write_user.id,
                record.id,
            ),
        )
        record.invalidate_recordset(["create_date", "write_date"])

    def _finish(self, payload, platforms, sessions, payouts, ledger_before):
        moves = self._traced(
            "account.move",
            "account.move",
            [row["id"] for row in payload["moves"]],
        )
        move_records = self.env["account.move"].sudo().browse(
            [move.id for move in moves.values()],
        )
        ledger_after = canonical_digest(self._ledger_rows(move_records))
        target_counts = {
            "platforms": len(platforms),
            "sessions": len(sessions),
            "payouts": len(payouts),
            "moves": len(moves),
            "attachments": sum(
                len(payout.attachment_ids) for payout in payouts.values()
            ),
            "bank_candidates": 0,
        }
        source_counts = payload["counts"]
        for key in ("platforms", "sessions", "payouts", "moves", "attachments"):
            if target_counts[key] != source_counts[key]:
                self._issue(
                    "usl.platform.billing.restore.run",
                    self.id,
                    (
                        f"{key} count differs: source {source_counts[key]}, "
                        f"target {target_counts[key]}."
                    ),
                )
        if ledger_before != ledger_after:
            self._issue(
                "account.move",
                0,
                (
                    "The canonical digest of the reconstructed generated moves "
                    "changed while application links were restored."
                ),
            )
        linked_move_count = sum(
            bool(
                move.platform_billing_session_id
                and move.platform_billing_platform_id
                and move.platform_billing_payout_ids,
            )
            for move in move_records
        )
        if linked_move_count != len(move_records):
            self._issue(
                "account.move",
                0,
                (
                    f"Only {linked_move_count}/{len(move_records)} generated "
                    "moves have complete application relations."
                ),
            )
        stats = {
            "revision": RESTORE_REVISION,
            "bootstrap_sha256": self.bootstrap_sha256,
            "source_dump_sha256": self.source_dump_sha256,
            "source": source_counts,
            "target": target_counts,
            "linked_move_count": linked_move_count,
            "ledger_digest_before": ledger_before,
            "ledger_digest_after": ledger_after,
            "canonical_digest": canonical_digest(
                {
                    "platforms": [
                        (
                            source_id,
                            record.name,
                            record.company_id.id,
                            record.commission_rate,
                        )
                        for source_id, record in sorted(platforms.items())
                    ],
                    "sessions": [
                        (
                            source_id,
                            record.name,
                            record.period_month,
                            record.state,
                        )
                        for source_id, record in sorted(sessions.items())
                    ],
                    "payouts": [
                        (
                            source_id,
                            record.platform_reference,
                            record.net_platform_amount,
                            record.state,
                            record.bank_match_status,
                            tuple(
                                sorted(
                                    (
                                        allocation.bank_statement_line_id.id,
                                        allocation.bank_amount,
                                        allocation.payout_amount,
                                    )
                                    for allocation in record.bank_allocation_ids
                                ),
                            ),
                            record.customer_invoice_id.id,
                            record.vendor_bill_id.id,
                            record.compensation_move_id.id,
                            tuple(
                                sorted(
                                    record.bank_statement_line_ids.ids,
                                ),
                            ),
                        )
                        for source_id, record in sorted(payouts.items())
                    ],
                },
            ),
        }
        previous = self.search(
            [
                ("id", "!=", self.id),
                ("source_database", "=", self.source_database),
                ("source_snapshot", "=", self.source_snapshot),
                ("status", "=", "passed"),
            ],
            order="id desc",
            limit=1,
        )
        if (
            previous
            and previous.statistics_json
            and previous.statistics_json.get("canonical_digest")
            != stats["canonical_digest"]
        ):
            self._issue(
                "usl.platform.billing.restore.run",
                self.id,
                "The repeated-run canonical digest is not stable.",
            )
        issue_count = self.env[
            "usl.platform.billing.restore.issue"
        ].search_count([("run_id", "=", self.id)])
        self.write(
            {
                "status": "failed" if self._has_errors() else "passed",
                "finished_at": fields.Datetime.now(),
                "platform_count": len(platforms),
                "session_count": len(sessions),
                "payout_count": len(payouts),
                "move_count": len(moves),
                "attachment_count": target_counts["attachments"],
                "issue_count": issue_count,
                "statistics_json": stats,
            },
        )
        return stats

    def restore_from_payload(self, payload):
        self.ensure_one()
        self._preflight(payload)
        maps = self._dependency_maps(payload)
        self._preflight_bank_allocations(payload, maps)
        move_records = self.env["account.move"].sudo().browse(
            [record.id for record in maps["move"].values()],
        )
        ledger_before = canonical_digest(self._ledger_rows(move_records))
        if self._has_errors():
            return self._finish(payload, {}, {}, {}, ledger_before)

        platforms = self._restore_platforms(payload, maps)
        sessions = self._restore_sessions(payload, maps)
        payouts = self._restore_payouts(
            payload,
            maps,
            platforms,
            sessions,
        )
        self._link_documents(payload, maps, platforms, sessions, payouts)
        self._restore_attachments(payload, maps, payouts)
        self._derive_states(payload, sessions, payouts)
        self._post_legacy_notes(payload, sessions)
        platform_rows = {row["id"]: row for row in payload["platforms"]}
        session_rows = {row["id"]: row for row in payload["sessions"]}
        payout_rows = {row["id"]: row for row in payload["payouts"]}
        for source_id, record in platforms.items():
            self._stamp_audit(record, platform_rows[source_id], maps["user"])
        for source_id, record in sessions.items():
            self._stamp_audit(record, session_rows[source_id], maps["user"])
        for source_id, record in payouts.items():
            self._stamp_audit(record, payout_rows[source_id], maps["user"])
        return self._finish(
            payload,
            platforms,
            sessions,
            payouts,
            ledger_before,
        )

    @classmethod
    def restore_from_source(cls, env, options):
        source_dump_sha256 = validate_source_identity(options)
        payload = PlatformBillingSourceReader(options).read()
        run = env["usl.platform.billing.restore.run"].create(
            {
                "source_database": options["database"],
                "source_snapshot": options["snapshot"],
                "source_dump_sha256": source_dump_sha256,
                "bootstrap_sha256": options.get(
                    "bootstrap_sha256",
                    BOOTSTRAP_SHA256,
                ),
                "target_database": env.cr.dbname,
            },
        )
        return run, run.restore_from_payload(payload)


class UslPlatformBillingRestoreIssue(models.Model):
    _name = "usl.platform.billing.restore.issue"
    _description = "USL Platform Billing Historical Restore Issue"
    _order = "severity, source_model, source_id, id"

    run_id = fields.Many2one(
        "usl.platform.billing.restore.run",
        required=True,
        ondelete="cascade",
        index=True,
    )
    severity = fields.Selection(
        [("error", "Error"), ("warning", "Warning"), ("info", "Information")],
        required=True,
        default="error",
        index=True,
    )
    source_model = fields.Char(required=True, index=True)
    source_id = fields.Integer(index=True)
    description = fields.Text(required=True)
    resolved = fields.Boolean(default=False, index=True)
    resolution = fields.Text()


class PlatformBillingSourceReader:
    def __init__(self, options):
        self.options = options

    def _connect(self):
        return psycopg2.connect(
            host=self.options["host"],
            port=self.options.get("port", 5432),
            user=self.options["user"],
            password=self.options["password"],
            dbname=self.options["database"],
            connect_timeout=10,
            options="-c default_transaction_read_only=on",
        )

    @staticmethod
    def _rows(cursor, query, params=None):
        cursor.execute(query, params or ())
        return [dict(row) for row in cursor.fetchall()]

    def read(self):
        with self._connect() as connection:
            with connection.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor,
            ) as cursor:
                platforms = self._rows(
                    cursor,
                    "SELECT * FROM x_content_platform ORDER BY id",
                )
                sessions = self._rows(
                    cursor,
                    "SELECT * FROM x_content_billing_session ORDER BY id",
                )
                payouts = self._rows(
                    cursor,
                    "SELECT * FROM x_content_payout_line ORDER BY id",
                )
                attachment_links = self._rows(
                    cursor,
                    """
                        SELECT x_payout_line_id, attachment_id
                          FROM x_content_payout_attachment_rel
                         ORDER BY x_payout_line_id, attachment_id
                    """,
                )
                attachments = self._rows(
                    cursor,
                    """
                        SELECT attachment.id, attachment.name,
                               attachment.checksum, attachment.file_size,
                               attachment.mimetype
                          FROM ir_attachment attachment
                         WHERE attachment.id IN (
                            SELECT attachment_id
                              FROM x_content_payout_attachment_rel
                         )
                         ORDER BY attachment.id
                    """,
                )
                moves = self._rows(
                    cursor,
                    """
                        SELECT id, name, date, state, move_type, currency_id,
                               x_content_billing_session_id,
                               x_content_platform_id,
                               x_content_payout_refs
                          FROM account_move
                         WHERE x_content_billing_session_id IS NOT NULL
                            OR x_content_platform_id IS NOT NULL
                            OR x_content_payout_refs IS NOT NULL
                         ORDER BY id
                    """,
                )
                currency_ids = {
                    value
                    for row in platforms
                    for value in (row["x_currency_id"],)
                    if value
                } | {
                    row["x_bank_currency_id"]
                    for row in sessions
                    if row["x_bank_currency_id"]
                } | {
                    row["x_platform_currency_id"]
                    for row in payouts
                    if row["x_platform_currency_id"]
                }
                currencies = (
                    self._rows(
                        cursor,
                        """
                            SELECT id, name
                              FROM res_currency
                             WHERE id = ANY(%s)
                             ORDER BY id
                        """,
                        (sorted(currency_ids),),
                    )
                    if currency_ids
                    else []
                )
                product_ids = {
                    value
                    for row in platforms
                    for value in (
                        row["x_revenue_product_id"],
                        row["x_commission_product_id"],
                    )
                    if value
                }
                products = (
                    self._rows(
                        cursor,
                        """
                            SELECT product.id, product.default_code,
                                   product.active, template.name,
                                   template.type, template.sale_ok,
                                   template.purchase_ok, template.uom_id,
                                   template.categ_id,
                                   template.property_account_income_id,
                                   template.property_account_expense_id,
                                   uom_xmlid.module AS uom_module,
                                   uom_xmlid.name AS uom_xml_name,
                                   category_xmlid.module AS category_module,
                                   category_xmlid.name AS category_xml_name,
                                   COALESCE((
                                       SELECT array_agg(relation.tax_id
                                                        ORDER BY relation.tax_id)
                                         FROM product_taxes_rel relation
                                        WHERE relation.prod_id = template.id
                                   ), ARRAY[]::integer[]) AS tax_ids,
                                   COALESCE((
                                       SELECT array_agg(relation.tax_id
                                                        ORDER BY relation.tax_id)
                                         FROM product_supplier_taxes_rel relation
                                        WHERE relation.prod_id = template.id
                                   ), ARRAY[]::integer[]) AS supplier_tax_ids
                              FROM product_product product
                              JOIN product_template template
                                ON template.id = product.product_tmpl_id
                         LEFT JOIN LATERAL (
                                   SELECT data.module, data.name
                                     FROM ir_model_data data
                                    WHERE data.model = 'uom.uom'
                                      AND data.res_id = template.uom_id
                                 ORDER BY data.id
                                    LIMIT 1
                                   ) uom_xmlid ON TRUE
                         LEFT JOIN LATERAL (
                                   SELECT data.module, data.name
                                     FROM ir_model_data data
                                    WHERE data.model = 'product.category'
                                      AND data.res_id = template.categ_id
                                 ORDER BY data.id
                                    LIMIT 1
                                   ) category_xmlid ON TRUE
                             WHERE product.id = ANY(%s)
                          ORDER BY product.id
                        """,
                        (sorted(product_ids),),
                    )
                    if product_ids
                    else []
                )
                user_ids = {
                    value
                    for rows in (platforms, sessions, payouts)
                    for row in rows
                    for value in (
                        row.get("create_uid"),
                        row.get("write_uid"),
                        row.get("x_generated_by_id"),
                    )
                    if value
                }
                users = (
                    self._rows(
                        cursor,
                        """
                            SELECT source_user.id, source_user.login,
                                   source_user.partner_id,
                                   source_user.company_id,
                                   COALESCE((
                                       SELECT array_agg(relation.cid
                                                        ORDER BY relation.cid)
                                         FROM res_company_users_rel relation
                                        WHERE relation.user_id = source_user.id
                                   ), ARRAY[]::integer[]) AS company_ids
                              FROM res_users source_user
                             WHERE source_user.id = ANY(%s)
                          ORDER BY source_user.id
                        """,
                        (sorted(user_ids),),
                    )
                    if user_ids
                    else []
                )
                cursor.execute("SELECT count(*) AS count FROM x_content_bank_candidate")
                bank_candidate_count = cursor.fetchone()["count"]
                return {
                    "platforms": platforms,
                    "sessions": sessions,
                    "payouts": payouts,
                    "moves": moves,
                    "attachment_links": attachment_links,
                    "attachments": attachments,
                    "currencies": currencies,
                    "products": products,
                    "users": users,
                    "counts": {
                        "platforms": len(platforms),
                        "sessions": len(sessions),
                        "payouts": len(payouts),
                        "moves": len(moves),
                        "attachments": len(attachment_links),
                        "bank_candidates": bank_candidate_count,
                    },
                }


def default_source_options():
    if os.getenv("USL_EINVOICE_LIVE_ENABLED", "0") != "0":
        message = "USL_EINVOICE_LIVE_ENABLED must remain 0 during restore."
        raise RuntimeError(message)
    if os.getenv("USL_EREPORTING_LIVE_ENABLED", "0") != "0":
        message = "USL_EREPORTING_LIVE_ENABLED must remain 0 during restore."
        raise RuntimeError(message)
    return {
        "host": os.getenv(
            "PLATFORM_BILLING_SOURCE_DB_HOST",
            "accounting-source-db",
        ),
        "port": int(os.getenv("PLATFORM_BILLING_SOURCE_DB_PORT", "5432")),
        "user": os.getenv("PLATFORM_BILLING_SOURCE_DB_USER", "odoo"),
        "password": os.getenv("PLATFORM_BILLING_SOURCE_DB_PASSWORD", "odoo"),
        "database": os.getenv(
            "PLATFORM_BILLING_SOURCE_DATABASE",
            "odoo_online_source_saas_19_3",
        ),
        "snapshot": os.getenv(
            "PLATFORM_BILLING_SOURCE_SNAPSHOT",
            "odoo-online-saas-19.3-platform-billing",
        ),
        "source_dump_sha256": os.getenv(
            "PLATFORM_BILLING_SOURCE_DUMP_SHA256",
            "",
        ),
        "bootstrap_sha256": os.getenv(
            "PLATFORM_BILLING_BOOTSTRAP_SHA256",
            BOOTSTRAP_SHA256,
        ),
    }

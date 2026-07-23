import base64
import calendar
import csv
import hashlib
import io
import json
from datetime import date, timedelta
from decimal import Decimal

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError

ACCOUNT_CODE_SQL = (
    "COALESCE("
    "account.code_store->>company.rebuild_source_id::text, "
    "account.code_store->>company.id::text, "
    "account.code_store->>'1', "
    "account.code_store::text"
    ")"
)
ACCOUNT_NAME_SQL = "COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text)"


def _amount(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def _amount_text(value):
    return f"{_amount(value):.2f}"


def _fec_amount(value):
    return Decimal((value or "0").strip().replace(" ", "").replace(",", ".") or "0").quantize(Decimal("0.01"))


def _matches(row, prefixes):
    return any((row.get("account_code") or "").startswith(prefix) for prefix in prefixes)


class RebuildAccountReportExportWizard(models.TransientModel):
    _name = "rebuild.account.report.export.wizard"
    _description = "USL Dynamic Accounting Report Workbench"

    report_type = fields.Selection(
        [
            ("trial_balance", "Trial Balance"),
            ("general_ledger", "General Ledger"),
            ("journal_report", "Journal Report"),
            ("partner_ledger", "Partner Ledger"),
            ("customer_statement", "Customer Statement"),
            ("open_items", "Open Items"),
            ("aged_receivable", "Aged Receivable"),
            ("aged_payable", "Aged Payable"),
            ("balance_sheet", "Balance Sheet"),
            ("profit_loss", "Profit and Loss"),
            ("tax_report", "VAT and Tax Report"),
            ("tax_report_group_account_tax", "Tax Report by Account then Tax"),
            ("tax_report_group_tax_account", "Tax Report by Tax then Account"),
            ("ec_sales_list", "EC Sales List"),
            ("oss_sales", "OSS Sales"),
            ("oss_imports", "OSS Imports"),
            ("bank_reconciliation", "Bank Reconciliation"),
            ("currency_report", "Currency Gain, Loss and Exposure"),
            ("cash_flow", "Cash Flow Statement"),
            ("executive_summary", "Executive Summary"),
            ("analytic_report", "Analytic Distribution"),
            ("fixed_assets", "Fixed Asset Register"),
            ("fixed_asset_group_account", "Fixed Asset Register by Account"),
            ("depreciation_schedule", "Depreciation Schedule"),
            ("deferred_schedule", "Deferred Expense and Revenue Schedule"),
            ("french_annual", "French Annual Statements"),
            ("french_balance_sheet_2024", "French Balance Sheet (2024 PCG)"),
            ("french_profit_loss_2024", "French Profit and Loss (2024 PCG)"),
            ("sig_caf_2024", "SIG and CAF (2024 PCG)"),
            ("french_tax_package", "French Tax Package Mapping"),
            ("closing_package", "Closing Review Package"),
            ("fec", "FEC"),
        ],
        required=True,
        default="trial_balance",
    )
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    company_ids = fields.Many2many(
        "res.company",
        string="Companies",
        default=lambda self: self.env.company,
    )
    data_scope = fields.Selection(
        [
            ("native", "All Native Accounting"),
            ("imported", "Imported Historical Replay Only"),
        ],
        required=True,
        default="native",
        help=(
            "All Native Accounting is the operational report scope. "
            "Imported Historical Replay Only is an advanced audit scope."
        ),
    )
    period_preset = fields.Selection(
        [
            ("custom", "Custom Dates"),
            ("month", "Month"),
            ("quarter", "Quarter"),
            ("fiscal_year", "Fiscal Year"),
            ("year_to_date", "Fiscal Year to Date"),
        ],
        required=True,
        default="custom",
    )
    period_anchor_date = fields.Date(
        string="Period Containing",
        default=fields.Date.context_today,
    )
    date_from = fields.Date(required=True, default="2024-01-10")
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    comparison_mode = fields.Selection(
        [
            ("none", "No Comparison"),
            ("previous_period", "Previous Period"),
            ("previous_year", "Same Period Last Year"),
            ("custom", "Custom Comparison"),
        ],
        required=True,
        default="none",
    )
    comparison_date_from = fields.Date()
    comparison_date_to = fields.Date()
    target_move = fields.Selection(
        [
            ("posted", "Posted Entries Only"),
            ("all", "Posted and Draft Entries"),
        ],
        required=True,
        default="posted",
    )
    export_format = fields.Selection(
        [
            ("csv", "CSV"),
            ("xlsx", "XLSX"),
            ("pdf", "PDF"),
            ("txt", "FEC TXT"),
        ],
        required=True,
        default="csv",
    )
    fec_test_mode = fields.Boolean(string="FEC Test Mode", default=True)
    can_generate_official_fec = fields.Boolean(
        compute="_compute_can_generate_official_fec",
    )
    journal_ids = fields.Many2many("account.journal", string="Journals")
    account_ids = fields.Many2many("account.account", string="Accounts")
    partner_ids = fields.Many2many("res.partner", string="Partners")
    analytic_plan_ids = fields.Many2many(
        "account.analytic.plan",
        "rebuild_report_analytic_plan_rel",
        "wizard_id",
        "plan_id",
        string="Analytic Plans",
    )
    analytic_account_ids = fields.Many2many(
        "account.analytic.account",
        "rebuild_report_analytic_account_rel",
        "wizard_id",
        "analytic_account_id",
        string="Analytic Accounts",
    )
    group_by = fields.Selection(
        [
            ("none", "No Grouping"),
            ("section", "Section"),
            ("account", "Account"),
            ("partner", "Partner"),
            ("journal", "Journal"),
            ("month", "Month"),
            ("analytic", "Analytic Account"),
        ],
        required=True,
        default="none",
    )
    search_text = fields.Char(string="Search Report")
    show_details = fields.Boolean(default=True)
    collapsed_group_keys = fields.Text(default="[]")
    export_file = fields.Binary(readonly=True, attachment=True)
    export_filename = fields.Char(readonly=True)
    export_metadata = fields.Text(readonly=True)
    preview_limit = fields.Integer(default=500, required=True)
    preview_line_ids = fields.One2many(
        "rebuild.account.report.preview.line",
        "wizard_id",
        readonly=True,
    )
    preview_row_count = fields.Integer(readonly=True)
    preview_truncated = fields.Boolean(readonly=True)
    preview_generated_at = fields.Datetime(readonly=True)
    preview_metadata = fields.Text(readonly=True)
    draft_entry_count = fields.Integer(readonly=True)
    preview_warning = fields.Text(readonly=True)

    def _can_generate_official_fec(self):
        return self.env.user.has_group("account.group_account_manager")

    @api.depends_context("uid")
    def _compute_can_generate_official_fec(self):
        allowed = self._can_generate_official_fec()
        for wizard in self:
            wizard.can_generate_official_fec = allowed

    @api.model_create_multi
    def create(self, vals_list):
        if not self._can_generate_official_fec():
            for values in vals_list:
                report_type = (
                    values.get("report_type")
                    or self.env.context.get("default_report_type")
                    or "trial_balance"
                )
                if report_type == "fec":
                    values["fec_test_mode"] = True
        return super().create(vals_list)

    def write(self, values):
        if (
            not self._can_generate_official_fec()
            and values.get("fec_test_mode") is False
            and any(
                values.get("report_type", wizard.report_type) == "fec"
                for wizard in self
            )
        ):
            raise UserError(
                self.env._(
                    "Only an Accounting Manager can generate an official "
                    "non-test FEC because it may update lock dates.",
                ),
            )
        return super().write(values)

    def action_apply_period(self):
        self.ensure_one()
        self._apply_period_values()
        return self.action_preview_report()

    def action_expand_all(self):
        self.ensure_one()
        self.write({
            "show_details": True,
            "collapsed_group_keys": "[]",
        })
        return self.action_preview_report()

    def action_collapse_all(self):
        self.ensure_one()
        self.write({"show_details": False})
        return self.action_preview_report()

    def action_preview_report(self):
        self.ensure_one()
        self._prepare_dynamic_filters()
        self._validate_filter_scope()
        if self.report_type == "fec":
            message = (
                "Use Generate Export to create and download the FEC file. "
                "FEC preview is limited to generated export metadata."
            )
            raise UserError(message)
        rows = self._report_rows()
        limit = max(1, min(self.preview_limit or 500, 5000))
        visible_rows = self._visible_preview_rows(rows)
        preview_rows = (
            visible_rows[:limit]
            if visible_rows
            else [{"empty_report": "true"}]
        )
        draft_count, warning = self._draft_entry_warning()
        metadata = self._export_metadata(len(rows))
        metadata.update({
            "preview_limit": limit,
            "previewed_row_count": len(preview_rows) if visible_rows else 0,
            "preview_visible_row_count": len(visible_rows),
            "preview_truncated": len(visible_rows) > limit,
            "draft_entry_count": draft_count,
            "warning": warning,
        })
        self.preview_line_ids.sudo().unlink()
        self.write({
            "preview_limit": limit,
            "preview_line_ids": [
                *[
                    Command.create(self._preview_line_values(sequence, row))
                    for sequence, row in enumerate(preview_rows, start=1)
                ],
            ],
            "preview_row_count": len(rows),
            "preview_truncated": len(visible_rows) > limit,
            "preview_generated_at": fields.Datetime.now(),
            "preview_metadata": json.dumps(metadata, indent=2, sort_keys=True),
            "draft_entry_count": draft_count,
            "preview_warning": warning,
        })
        return {
            "type": "ir.actions.act_window",
            "name": self._report_type_label(),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_generate_export(self):
        self.ensure_one()
        self._prepare_dynamic_filters()
        self._validate_filter_scope()
        if self.report_type == "fec":
            payload, filename, metadata = self._fec_export_payload()
        else:
            rows = self._report_rows()
            payload = self._export_payload(rows)
            filename = self._export_filename()
            metadata = self._export_metadata(len(rows))
        self.write({
            "export_file": base64.b64encode(payload),
            "export_filename": filename,
            "export_metadata": json.dumps(metadata, indent=2, sort_keys=True),
        })
        return {
            "type": "ir.actions.act_window",
            "name": f"{self._report_type_label()} Export",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def _prepare_dynamic_filters(self):
        self.ensure_one()
        if not self.date_from or not self.date_to:
            message = "Select a report start and end date."
            raise UserError(message)
        if self.date_from > self.date_to:
            message = "The start date must be before or equal to the end date."
            raise UserError(message)
        if self.comparison_mode != "none":
            self._apply_comparison_values()
            if (
                not self.comparison_date_from
                or not self.comparison_date_to
            ):
                message = "Select comparison start and end dates."
                raise UserError(message)
            if self.comparison_date_from > self.comparison_date_to:
                message = (
                    "The comparison start date must be before or equal "
                    "to the comparison end date."
                )
                raise UserError(message)

    def _apply_period_values(self):
        self.ensure_one()
        if self.period_preset == "custom":
            self._apply_comparison_values()
            return
        anchor = fields.Date.to_date(
            self.period_anchor_date
            or fields.Date.context_today(self),
        )
        if self.period_preset == "month":
            date_from = anchor.replace(day=1)
            date_to = anchor.replace(
                day=calendar.monthrange(anchor.year, anchor.month)[1],
            )
        elif self.period_preset == "quarter":
            first_month = ((anchor.month - 1) // 3) * 3 + 1
            date_from = anchor.replace(month=first_month, day=1)
            last_month = first_month + 2
            date_to = anchor.replace(
                month=last_month,
                day=calendar.monthrange(anchor.year, last_month)[1],
            )
        else:
            fiscal_from, fiscal_to = self._fiscal_year_dates(anchor)
            date_from = fiscal_from
            date_to = (
                anchor
                if self.period_preset == "year_to_date"
                else fiscal_to
            )
        self.write({
            "date_from": date_from,
            "date_to": date_to,
        })
        self._apply_comparison_values()

    def _fiscal_year_dates(self, anchor):
        self.ensure_one()
        company = self.company_id
        end_month = int(company.fiscalyear_last_month or 12)
        end_day = min(
            company.fiscalyear_last_day or 31,
            calendar.monthrange(anchor.year, end_month)[1],
        )
        fiscal_end = date(anchor.year, end_month, end_day)
        if anchor > fiscal_end:
            next_year = anchor.year + 1
            fiscal_end = date(
                next_year,
                end_month,
                min(
                    company.fiscalyear_last_day or 31,
                    calendar.monthrange(next_year, end_month)[1],
                ),
            )
        previous_end_year = fiscal_end.year - 1
        previous_end = date(
            previous_end_year,
            end_month,
            min(
                company.fiscalyear_last_day or 31,
                calendar.monthrange(previous_end_year, end_month)[1],
            ),
        )
        return previous_end + timedelta(days=1), fiscal_end

    def _apply_comparison_values(self):
        self.ensure_one()
        if self.comparison_mode == "none":
            self.write({
                "comparison_date_from": False,
                "comparison_date_to": False,
            })
            return
        if self.comparison_mode == "custom":
            return
        if not self.date_from or not self.date_to:
            return
        date_from = fields.Date.to_date(self.date_from)
        date_to = fields.Date.to_date(self.date_to)
        if self.comparison_mode == "previous_period":
            period_days = (date_to - date_from).days + 1
            comparison_to = date_from - timedelta(days=1)
            comparison_from = comparison_to - timedelta(
                days=period_days - 1,
            )
        else:
            comparison_from = self._previous_year_date(date_from)
            comparison_to = self._previous_year_date(date_to)
        self.write({
            "comparison_date_from": comparison_from,
            "comparison_date_to": comparison_to,
        })

    @staticmethod
    def _previous_year_date(value):
        try:
            return value.replace(year=value.year - 1)
        except ValueError:
            return value.replace(year=value.year - 1, day=28)

    def _selected_companies(self):
        self.ensure_one()
        companies = self.company_ids | self.company_id
        unauthorized = companies - self.env.companies
        if unauthorized:
            message = (
                "You cannot report on a company outside your allowed "
                "companies."
            )
            raise AccessError(message)
        return companies.sorted(lambda company: (company.name, company.id))

    def _draft_entry_warning(self):
        self.ensure_one()
        domain = [
            ("company_id", "in", self._selected_companies().ids),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
            ("state", "=", "draft"),
            ("line_ids", "!=", False),
        ]
        if self.data_scope == "imported":
            domain.append(("rebuild_source_model", "=", "account.move"))
        count = self.env["account.move"].search_count(domain)
        if not count:
            return 0, ""
        treatment = (
            "included in this report"
            if self.target_move == "all"
            else "excluded because Posted Entries Only is selected"
        )
        entry_grammar = "entry is" if count == 1 else "entries are"
        return (
            count,
            f"{count} draft accounting {entry_grammar} {treatment}.",
        )

    def action_open_journal_items(self):
        self.ensure_one()
        self._validate_filter_scope(for_drilldown=True)
        if self.report_type == "analytic_report":
            return {
                "type": "ir.actions.act_window",
                "name": "Imported Analytic Lines",
                "res_model": "account.analytic.line",
                "view_mode": "list,form,pivot",
                "domain": self._analytic_line_domain(),
                "context": {"create": False, "delete": False},
            }
        return {
            "type": "ir.actions.act_window",
            "name": "Imported Journal Items",
            "res_model": "account.move.line",
            "view_mode": "list,form,pivot",
            "domain": self._journal_item_domain(),
            "context": {"create": False, "delete": False},
        }

    def _export_filename(self):
        company_key = (
            "multi-company"
            if len(self._selected_companies()) > 1
            else str(
                self.company_id.rebuild_source_id
                or self.company_id.id,
            )
        )
        return "%s-%s-%s-%s.%s" % (
            self.report_type.replace("_", "-"),
            company_key,
            fields.Date.to_string(self.date_from),
            fields.Date.to_string(self.date_to),
            self.export_format,
        )

    def _report_type_label(self):
        return dict(self._fields["report_type"].selection).get(self.report_type, self.report_type)

    def _report_variant_key(self):
        if self.report_type in {"french_balance_sheet_2024", "french_profit_loss_2024", "sig_caf_2024"}:
            return "pcg_2024_pre_2025_opening_year"
        return ""

    def _report_variant_basis(self):
        if self._report_variant_key():
            return (
                "ANC regulation 2022-06 is applicable to financial years opened from 2025-01-01, "
                "with early application possible. USL's benchmark year opened on 2024-01-10, so "
                "the pre-2025 French statement family remains an explicit review variant."
            )
        return ""

    def _preview_line_values(self, sequence, row):
        if row.get("empty_report") == "true":
            label = "No rows for the selected report filters"
        else:
            label = (
                row.get("label")
                or row.get("details")
                or row.get("line_name")
                or row.get("field_label")
                or row.get("asset_name")
                or row.get("account_name")
                or row.get("partner_name")
                or row.get("move_name")
                or row.get("journal_name")
                or row.get("report_section")
                or self._report_type_label()
            )
        return {
            "sequence": sequence,
            "company_id": (
                row.get("report_company_id")
                or self.company_id.id
            ),
            "date": row.get("date") or row.get("due_date") or row.get("deferred_date"),
            "section": row.get("section") or row.get("statement_name") or row.get("report_section") or row.get("form_code") or row.get("journal_code"),
            "line_code": row.get("line_code") or row.get("field_code") or row.get("account_code") or row.get("journal_code"),
            "label": label,
            "account_code": row.get("account_code"),
            "account_name": row.get("account_name"),
            "partner_name": row.get("partner_name"),
            "move_name": row.get("move_name"),
            "opening_balance": _amount(row.get("opening_balance")),
            "debit": _amount(row.get("debit")),
            "credit": _amount(row.get("credit")),
            "movement": _amount(
                row.get("movement")
                or row.get("balance"),
            ),
            "closing_balance": _amount(row.get("closing_balance")),
            "balance": _amount(
                row.get("period_value")
                or row.get("balance")
                or row.get("amount")
                or row.get("net_amount")
                or row.get("statement_balance"),
            ),
            "residual": _amount(row.get("presented_residual") or row.get("residual") or row.get("amount_residual") or row.get("imported_period_net_value")),
            "comparison_value": _amount(row.get("comparison_value")),
            "difference": _amount(row.get("difference")),
            "record_count": int(row.get("record_count") or 0),
            "is_group": row.get("is_group") in (True, "true"),
            "level": int(row.get("row_level") or 0),
            "group_key": row.get("group_key"),
            "parent_group_key": row.get("parent_group_key"),
            "currency_id": (
                row.get("report_currency_id")
                or self.company_id.currency_id.id
            ),
            "row_json": json.dumps(row, indent=2, sort_keys=True, default=str),
        }

    def _visible_preview_rows(self, rows):
        self.ensure_one()
        if self.group_by == "none":
            return rows
        collapsed = self._collapsed_group_key_set()
        visible = []
        for row in rows:
            if row.get("is_group") in (True, "true"):
                visible.append(row)
                continue
            parent_key = row.get("parent_group_key")
            if not self.show_details or parent_key in collapsed:
                continue
            visible.append(row)
        return visible

    def _collapsed_group_key_set(self):
        self.ensure_one()
        try:
            values = json.loads(self.collapsed_group_keys or "[]")
        except json.JSONDecodeError:
            return set()
        return {
            str(value)
            for value in values
            if value not in (None, "")
        }

    def _toggle_preview_group(self, group_key):
        self.ensure_one()
        if not group_key:
            return self.action_preview_report()
        collapsed = self._collapsed_group_key_set()
        if group_key in collapsed:
            collapsed.remove(group_key)
        else:
            collapsed.add(group_key)
        self.write({
            "show_details": True,
            "collapsed_group_keys": json.dumps(sorted(collapsed)),
        })
        return self.action_preview_report()

    def _journal_item_domain(self, company_ids=None):
        companies = (
            self.env["res.company"].browse(company_ids)
            if company_ids
            else self._selected_companies()
        )
        domain = [
            ("company_id", "in", companies.ids),
            ("move_id.date", "<=", self.date_to),
        ]
        if self.report_type != "trial_balance":
            domain.append(("move_id.date", ">=", self.date_from))
        if self.data_scope == "imported":
            domain.extend([
                ("rebuild_source_model", "=", "account.move.line"),
                ("move_id.rebuild_source_model", "=", "account.move"),
            ])
        if self.target_move == "posted":
            domain.append(("move_id.state", "=", "posted"))
        if self.journal_ids:
            domain.append(("journal_id", "in", self.journal_ids.ids))
        if self.account_ids:
            domain.append(("account_id", "in", self.account_ids.ids))
        if self.partner_ids:
            domain.append(("partner_id", "in", self.partner_ids.ids))
        if self.report_type == "partner_ledger":
            domain.append(("partner_id", "!=", False))
        elif self.report_type == "customer_statement":
            domain.extend([
                ("partner_id", "!=", False),
                ("account_id.account_type", "=", "asset_receivable"),
            ])
            if not self.partner_ids:
                domain.append(("partner_id.customer_rank", ">", 0))
        elif self.report_type in ("open_items", "aged_receivable", "aged_payable"):
            domain.extend([
                ("account_id.account_type", "in", ["asset_receivable", "liability_payable"]),
                "|",
                ("reconciled", "=", False),
                ("amount_residual", "!=", 0),
            ])
            if self.report_type == "aged_receivable":
                domain.append(("account_id.account_type", "=", "asset_receivable"))
            elif self.report_type == "aged_payable":
                domain.append(("account_id.account_type", "=", "liability_payable"))
        elif self.report_type == "bank_reconciliation":
            domain.append(("move_id.statement_line_id", "!=", False))
        elif self.report_type == "currency_report":
            domain.extend([
                ("currency_id", "!=", False),
                ("currency_id", "!=", self.company_id.currency_id.id),
            ])
        elif self.report_type == "deferred_schedule":
            schedules = self.env["rebuild.account.deferred.schedule.line"].search([
                ("company_id", "=", self.company_id.id),
                ("deferred_date", ">=", self.date_from),
                ("deferred_date", "<=", self.date_to),
            ])
            source_move_ids = sorted(set(
                schedules.mapped("source_original_move_id")
                + schedules.mapped("source_deferred_move_id"),
            ))
            domain.append(("move_id.rebuild_source_id", "in", source_move_ids or [0]))
        return domain

    def _analytic_line_domain(self, company_ids=None):
        companies = (
            self.env["res.company"].browse(company_ids)
            if company_ids
            else self._selected_companies()
        )
        domain = [
            ("company_id", "in", companies.ids),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ]
        if self.data_scope == "imported":
            domain.append(
                ("rebuild_source_model", "=", "account.analytic.line"),
            )
        if self.journal_ids:
            domain.append(("move_line_id.journal_id", "in", self.journal_ids.ids))
        if self.account_ids:
            domain.append(("general_account_id", "in", self.account_ids.ids))
        if self.partner_ids:
            domain.append(("partner_id", "in", self.partner_ids.ids))
        if self.analytic_plan_ids:
            domain.append(
                ("account_id.plan_id", "in", self.analytic_plan_ids.ids),
            )
        if self.analytic_account_ids:
            domain.append(
                ("account_id", "in", self.analytic_account_ids.ids),
            )
        if self.target_move == "posted":
            domain.extend([
                "|",
                ("move_line_id", "=", False),
                ("move_line_id.move_id.state", "=", "posted"),
            ])
        return domain

    def _preview_source_action(self, preview_line):
        self.ensure_one()
        self._validate_filter_scope(for_drilldown=True)
        row = preview_line._row_payload()
        if self.report_type == "analytic_report":
            domain = self._preview_analytic_line_domain(row)
            return {
                "type": "ir.actions.act_window",
                "name": self._preview_source_action_name(preview_line, "Analytic Sources"),
                "res_model": "account.analytic.line",
                "view_mode": "list,form,pivot",
                "domain": domain,
                "context": {"create": False, "delete": False},
            }
        domain = self._preview_journal_item_domain(row)
        return {
            "type": "ir.actions.act_window",
            "name": self._preview_source_action_name(preview_line, "Journal Item Sources"),
            "res_model": "account.move.line",
            "view_mode": "list,form,pivot",
            "domain": domain,
            "context": {"create": False, "delete": False},
        }

    def _preview_source_action_name(self, preview_line, fallback):
        label = preview_line.label or preview_line.line_code or self._report_type_label()
        return f"{fallback} - {label}"[:120]

    def _preview_journal_item_domain(self, row):
        row_company_id = self._row_int(row, "report_company_id")
        domain = list(
            self._journal_item_domain(
                company_ids=[row_company_id] if row_company_id else None,
            ),
        )
        refinements = []

        source_line_id = self._row_int(row, "source_line_id")
        if source_line_id:
            domain.append(("rebuild_source_id", "=", source_line_id))
            refinements.append("source_line_id")

        source_move_ids = self._row_int_values(
            row,
            "source_move_id",
            "imported_source_move_id",
            "source_original_move_id",
            "source_deferred_move_id",
        )
        if source_move_ids:
            domain.append(("move_id.rebuild_source_id", "in", source_move_ids))
            refinements.append("source_move_id")

        source_statement_line_id = self._row_int(row, "source_statement_line_id")
        if source_statement_line_id:
            domain.append(("move_id.statement_line_id.rebuild_source_id", "=", source_statement_line_id))
            refinements.append("source_statement_line_id")

        source_partner_id = self._row_int(row, "source_partner_id")
        if source_partner_id:
            domain.append(("partner_id.rebuild_source_id", "=", source_partner_id))
            refinements.append("source_partner_id")
        elif row.get("partner_name"):
            domain.append(("partner_id.name", "=", row["partner_name"]))
            refinements.append("partner_name")

        source_account_ids = self._row_int_values(row, "source_account_id")
        accounts = self._preview_accounts(row, source_account_ids=source_account_ids)
        if accounts:
            domain.append(("account_id", "in", accounts.ids))
            refinements.append("account")
        elif self._row_has_account_ref(row):
            domain.append(("account_id", "in", [0]))
            refinements.append("missing_account")

        source_tax_tag_id = self._row_int(row, "source_tax_tag_id")
        if source_tax_tag_id:
            tax_tag = self.env["account.account.tag"].search([
                ("rebuild_source_id", "=", source_tax_tag_id),
            ], limit=1)
            domain.append(("tax_tag_ids", "in", tax_tag.ids or [0]))
            refinements.append("tax_tag")

        journal_code = row.get("journal_code")
        if journal_code:
            journal = self.env["account.journal"].search([
                ("company_id", "in", [False, self.company_id.id]),
                ("code", "=", journal_code),
            ], limit=1)
            domain.append(("journal_id", "=", journal.id or 0))
            refinements.append("journal_code")

        account_type = row.get("account_type")
        if account_type and not accounts:
            domain.append(("account_id.account_type", "=", account_type))
            refinements.append("account_type")

        report_month = row.get("report_month")
        if report_month and len(str(report_month)) == 7:
            year, month = map(int, str(report_month).split("-"))
            month_from = date(year, month, 1)
            month_to = date(
                year,
                month,
                calendar.monthrange(year, month)[1],
            )
            domain.extend([
                ("move_id.date", ">=", month_from),
                ("move_id.date", "<=", month_to),
            ])
            refinements.append("report_month")

        if not refinements:
            return domain
        return domain

    def _preview_analytic_line_domain(self, row):
        row_company_id = self._row_int(row, "report_company_id")
        domain = list(
            self._analytic_line_domain(
                company_ids=[row_company_id] if row_company_id else None,
            ),
        )
        analytic_key = self._row_int(row, "analytic_key")
        if analytic_key:
            if self.data_scope == "imported":
                domain.append(
                    (
                        "rebuild_source_analytic_account_id",
                        "=",
                        analytic_key,
                    ),
                )
            else:
                domain.extend([
                    "|",
                    ("account_id", "=", analytic_key),
                    (
                        "rebuild_source_analytic_account_id",
                        "=",
                        analytic_key,
                    ),
                ])
        elif row.get("analytic_name"):
            domain.append(
                ("account_id.name", "=", row["analytic_name"]),
            )
        source_partner_id = self._row_int(row, "source_partner_id")
        if source_partner_id:
            domain.append(("partner_id.rebuild_source_id", "=", source_partner_id))
        source_account_ids = self._row_int_values(row, "source_account_id")
        accounts = self._preview_accounts(row, source_account_ids=source_account_ids)
        if accounts:
            domain.append(("general_account_id", "in", accounts.ids))
        elif self._row_has_account_ref(row):
            domain.append(("general_account_id", "in", [0]))
        return domain

    def _preview_accounts(self, row, source_account_ids=None):
        Account = self.env["account.account"]
        accounts = Account.browse()
        if source_account_ids:
            accounts |= Account.search([
                ("company_ids", "in", self.company_id.id),
                ("rebuild_source_id", "in", source_account_ids),
            ])
        exact_codes = {
            code
            for code in self._row_account_codes(row)
            if code and any(character.isdigit() for character in code)
        }
        prefixes = [
            prefix.strip()
            for prefix in (row.get("drilldown_account_prefixes") or "").split(",")
            if prefix.strip()
        ]
        if exact_codes or prefixes:
            for account in Account.search([("company_ids", "in", self.company_id.id)]):
                code = self._account_code_for_company(account)
                if code in exact_codes or any(code.startswith(prefix) for prefix in prefixes):
                    accounts |= account
        return accounts

    def _row_has_account_ref(self, row):
        return bool(self._row_account_codes(row) or row.get("drilldown_account_prefixes") or row.get("source_account_id"))

    @staticmethod
    def _row_account_codes(row):
        codes = []
        for key in (
            "account_code",
            "asset_account",
            "depreciation_account",
            "depreciation_expense_account",
            "deferred_account_code",
        ):
            value = row.get(key)
            if value:
                codes.append(str(value).strip())
        return [code for code in codes if code]

    def _account_code_for_company(self, account):
        code_store = account.code_store
        if isinstance(code_store, dict):
            source_company_id = str(self.company_id.rebuild_source_id or "")
            return (
                code_store.get(source_company_id)
                or code_store.get("1")
                or next(iter(code_store.values()), "")
                or ""
            )
        return getattr(account, "code", False) or str(code_store or "")

    @staticmethod
    def _row_int(row, key):
        value = row.get(key)
        if value in (None, "", False):
            return False
        try:
            return int(value)
        except (TypeError, ValueError):
            return False

    def _row_int_values(self, row, *keys):
        values = []
        for key in keys:
            value = self._row_int(row, key)
            if value and value not in values:
                values.append(value)
        return values

    def _export_metadata(self, row_count=None):
        partner = self.company_id.partner_id
        companies = self._selected_companies()
        return {
            "report_type": self.report_type,
            "report_name": self._report_type_label(),
            "company": ", ".join(companies.mapped("display_name")),
            "companies": [
                {
                    "id": company.id,
                    "name": company.display_name,
                    "currency": company.currency_id.name,
                }
                for company in companies
            ],
            "legal_name": self.company_id.name,
            "company_registry": self.company_id.company_registry or "",
            "vat_number": self.company_id.vat or "",
            "address": ", ".join(filter(None, [
                partner.street,
                partner.street2,
                " ".join(filter(None, [partner.zip, partner.city])),
                partner.country_id.name,
            ])),
            "source_company_id": self.company_id.rebuild_source_id,
            "date_from": fields.Date.to_string(self.date_from),
            "date_to": fields.Date.to_string(self.date_to),
            "currency": self.company_id.currency_id.name,
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
            "target_move": self.target_move,
            "data_scope": self.data_scope,
            "period_preset": self.period_preset,
            "comparison_mode": self.comparison_mode,
            "comparison_date_from": (
                fields.Date.to_string(self.comparison_date_from)
                if self.comparison_date_from
                else None
            ),
            "comparison_date_to": (
                fields.Date.to_string(self.comparison_date_to)
                if self.comparison_date_to
                else None
            ),
            "group_by": self.group_by,
            "search_text": self.search_text or "",
            "row_count": row_count,
            "format": self.export_format,
            "report_variant": self._report_variant_key(),
            "report_variant_basis": self._report_variant_basis(),
            "fec_test_mode": self.fec_test_mode if self.report_type == "fec" else None,
            "journal_filter": [
                {
                    "id": journal.id,
                    "source_id": journal.rebuild_source_id,
                    "code": journal.code,
                    "name": journal.display_name,
                }
                for journal in self.journal_ids.sorted("code")
            ],
            "account_filter": [
                {
                    "id": account.id,
                    "source_id": account.rebuild_source_id,
                    "name": account.display_name,
                }
                for account in self.account_ids.sorted("display_name")
            ],
            "partner_filter": [
                {
                    "id": partner.id,
                    "source_id": partner.rebuild_source_id,
                    "name": partner.display_name,
                }
                for partner in self.partner_ids.sorted("display_name")
            ],
            "analytic_plan_filter": [
                {
                    "id": plan.id,
                    "name": plan.display_name,
                }
                for plan in self.analytic_plan_ids.sorted("display_name")
            ],
            "analytic_account_filter": [
                {
                    "id": account.id,
                    "name": account.display_name,
                }
                for account in self.analytic_account_ids.sorted(
                    "display_name",
                )
            ],
        }

    @api.onchange("report_type")
    def _onchange_report_type(self):
        if self.report_type == "fec":
            self.export_format = "txt"
            self.target_move = "posted"
            self.fec_test_mode = True
        elif self.export_format == "txt":
            self.export_format = "csv"

    def _export_payload(self, rows):
        if self.export_format == "csv":
            return self._csv_payload(rows)
        if self.export_format == "xlsx":
            return self._xlsx_payload(rows)
        if self.export_format == "pdf":
            return self._pdf_payload(rows)
        message = "Unsupported export format."
        raise UserError(message)

    def _csv_payload(self, rows):
        output = io.StringIO()
        metadata = self._export_metadata(len(rows))
        fieldnames = sorted({key for row in rows for key in row}) or ["empty_report"]
        writer = csv.DictWriter(output, fieldnames=["metadata", *fieldnames], extrasaction="ignore")
        writer.writeheader()
        metadata_text = json.dumps(metadata, sort_keys=True)
        if rows:
            for row in rows:
                writer.writerow({"metadata": metadata_text, **row})
        else:
            writer.writerow({"metadata": metadata_text, "empty_report": "true"})
        return output.getvalue().encode("utf-8")

    def _report_export_columns(self, rows):
        available = {key for row in rows for key in row}
        labels = {
            "report_company_name": "Company",
            "section": "Section",
            "statement_name": "Statement",
            "report_section": "Section",
            "form_code": "Form",
            "line_code": "Code",
            "field_code": "Field",
            "date": "Date",
            "due_date": "Due date",
            "journal_code": "Journal",
            "journal_name": "Journal name",
            "move_name": "Entry",
            "piece_reference": "Document reference",
            "account_code": "Account",
            "account_name": "Account name",
            "partner_name": "Partner",
            "label": "Description",
            "line_name": "Description",
            "field_label": "Field description",
            "asset_name": "Asset",
            "opening_balance": "Opening",
            "debit": "Debit",
            "credit": "Credit",
            "balance": "Balance",
            "closing_balance": "Closing",
            "amount": "Amount",
            "gross_amount": "Gross",
            "depreciation_amount": "Depreciation",
            "net_amount": "Net",
            "residual": "Residual",
            "presented_residual": "Residual",
            "amount_residual": "Residual",
            "imported_period_net_value": "Net book value",
            "currency": "Currency",
            "status": "Status",
            "validation": "Validation",
            "review_status": "Review status",
            "record_count": "Count",
            "quantity": "Quantity",
            "value_text": "Value / note",
            "period_value": "Selected Period",
            "comparison_value": "Comparison Period",
            "difference": "Difference",
            "details": "Details",
            "next_action": "Next action",
            "evidence": "Evidence",
            "source_reference": "Source reference",
        }
        preferred = {
            "trial_balance": ["account_code", "account_name", "opening_balance", "debit", "credit", "closing_balance"],
            "general_ledger": ["date", "journal_code", "move_name", "account_code", "account_name", "partner_name", "debit", "credit", "balance"],
            "journal_report": ["journal_code", "journal_name", "debit", "credit", "balance"],
            "partner_ledger": ["partner_name", "account_code", "debit", "credit", "balance"],
            "customer_statement": ["date", "due_date", "move_name", "partner_name", "debit", "credit", "residual"],
            "open_items": ["date", "due_date", "move_name", "account_code", "partner_name", "balance", "residual"],
            "aged_receivable": ["partner_name", "not_due", "bucket_1", "bucket_2", "bucket_3", "bucket_4", "bucket_5", "residual"],
            "aged_payable": ["partner_name", "not_due", "bucket_1", "bucket_2", "bucket_3", "bucket_4", "bucket_5", "residual"],
            "balance_sheet": ["section", "line_code", "label", "opening_balance", "movement", "closing_balance"],
            "profit_loss": ["section", "line_code", "label", "amount"],
            "cash_flow": ["section", "line_code", "label", "amount", "statement_balance"],
            "executive_summary": ["section", "line_code", "label", "amount", "details"],
            "tax_report": ["account_code", "account_name", "tax_name", "debit", "credit", "balance"],
            "tax_report_group_account_tax": ["account_code", "account_name", "tax_name", "debit", "credit", "balance"],
            "tax_report_group_tax_account": ["tax_name", "account_code", "account_name", "debit", "credit", "balance"],
            "bank_reconciliation": ["date", "journal_code", "payment_ref", "partner_name", "amount", "residual", "status"],
            "currency_report": ["currency", "account_code", "partner_name", "amount_currency", "balance", "residual"],
            "analytic_report": ["date", "analytic_plan_name", "analytic_account_name", "account_code", "partner_name", "debit", "credit", "balance"],
            "fixed_assets": ["asset_name", "account_code", "acquisition_date", "original_value", "depreciation_amount", "imported_period_net_value", "state"],
            "fixed_asset_group_account": ["account_code", "account_name", "original_value", "depreciation_amount", "imported_period_net_value"],
            "depreciation_schedule": ["asset_name", "depreciation_date", "depreciation_amount", "accumulated_depreciation", "imported_period_net_value", "status"],
            "deferred_schedule": ["deferred_date", "deferred_account_code", "partner_name", "amount", "residual", "review_status"],
            "french_annual": ["statement_name", "line_code", "label", "gross_amount", "depreciation_amount", "net_amount", "amount"],
            "french_balance_sheet_2024": ["statement_name", "line_code", "label", "gross_amount", "depreciation_amount", "net_amount"],
            "french_profit_loss_2024": ["statement_name", "line_code", "label", "amount"],
            "sig_caf_2024": ["statement_name", "line_code", "label", "amount"],
            "french_tax_package": [
                "form_code",
                "field_code",
                "field_label",
                "quantity",
                "amount",
                "rounded_amount",
                "value_text",
                "review_status",
                "source_reference",
            ],
            "closing_package": ["section", "line_code", "label", "status", "validation", "record_count", "amount", "details", "next_action", "evidence"],
        }.get(self.report_type, [])
        if self.group_by != "none":
            chosen = [
                key
                for key in (
                    "report_company_name",
                    "label",
                    "record_count",
                    "period_value",
                    "comparison_value",
                    "difference",
                )
                if key in available
            ]
        else:
            chosen = [key for key in preferred if key in available]
            if len(self._selected_companies()) > 1:
                chosen.insert(0, "report_company_name")
            if self.comparison_mode != "none":
                chosen.extend([
                    key
                    for key in (
                        "period_value",
                        "comparison_value",
                        "difference",
                    )
                    if key in available and key not in chosen
                ])
        if not chosen:
            excluded = {
                key for key in available
                if key.endswith("_id") or key.startswith("source_") or key in {"row_json"}
            }
            chosen = sorted(available - excluded)[:9]
        if not chosen:
            chosen = ["label"]
        return [(key, labels.get(key, key.replace("_", " ").title())) for key in chosen]

    @staticmethod
    def _xlsx_write_value(worksheet, row, column, value, formats, fieldname):
        numeric_fields = {
            "opening_balance", "debit", "credit", "balance", "closing_balance", "movement",
            "amount", "gross_amount", "depreciation_amount", "net_amount", "residual",
            "presented_residual", "amount_residual", "imported_period_net_value", "original_value",
            "amount_currency", "rounded_amount", "statement_balance", "record_count",
            "quantity", "period_value", "comparison_value", "difference",
        }
        if value in (None, "", False):
            worksheet.write_blank(row, column, None, formats["body"])
            return
        if fieldname in numeric_fields:
            try:
                worksheet.write_number(row, column, float(value), formats["number"])
                return
            except (TypeError, ValueError):
                pass
        worksheet.write(row, column, str(value), formats["body"])

    def _xlsx_payload(self, rows):
        try:
            import xlsxwriter  # noqa: PLC0415
        except ImportError as exc:
            message = "XLSX export requires the xlsxwriter Python package in the Odoo runtime."
            raise UserError(message) from exc

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {
            "in_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        })
        workbook.set_properties({
            "title": self._report_type_label(),
            "subject": f"{self.company_id.display_name} - {self.date_from} to {self.date_to}",
            "company": self.company_id.display_name,
            "comments": "Generated from Odoo Community by the USL accounting report exporter.",
        })
        formats = {
            "title": workbook.add_format({"bold": True, "font_size": 18, "font_color": "#17324D"}),
            "subtitle": workbook.add_format({"font_size": 10, "font_color": "#52606D"}),
            "header": workbook.add_format({
                "bold": True, "font_color": "#FFFFFF", "bg_color": "#17324D",
                "border": 1, "border_color": "#17324D", "text_wrap": True,
                "valign": "vcenter",
            }),
            "metadata_key": workbook.add_format({"bold": True, "bg_color": "#E8EDF2", "border": 1}),
            "metadata_value": workbook.add_format({
                "border": 1,
                "text_wrap": True,
                "valign": "top",
                "align": "left",
                "num_format": "@",
            }),
            "body": workbook.add_format({"border": 1, "border_color": "#D5DBE1", "valign": "top", "text_wrap": True}),
            "number": workbook.add_format({
                "border": 1, "border_color": "#D5DBE1", "num_format": "#,##0.00;[Red]-#,##0.00;-",
                "align": "right", "valign": "top",
            }),
        }
        metadata = self._export_metadata(len(rows))

        metadata_sheet = workbook.add_worksheet("Metadata")
        metadata_sheet.hide_gridlines(2)
        metadata_sheet.write(0, 0, self._report_type_label(), formats["title"])
        metadata_sheet.merge_range(0, 0, 0, 1, self._report_type_label(), formats["title"])
        for row_idx, (key, value) in enumerate(metadata.items(), start=2):
            metadata_sheet.write(row_idx, 0, key.replace("_", " ").title(), formats["metadata_key"])
            display_value = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (list, dict)) else value
            metadata_sheet.write(row_idx, 1, "" if display_value is None else str(display_value), formats["metadata_value"])
        metadata_sheet.set_column(0, 0, 28)
        metadata_sheet.set_column(1, 1, 88)
        metadata_sheet.set_landscape()
        metadata_sheet.fit_to_pages(1, 0)

        report_sheet = workbook.add_worksheet("Report")
        report_sheet.hide_gridlines(2)
        columns = self._report_export_columns(rows)
        last_column = max(0, len(columns) - 1)
        subtitle = f"{metadata['company']} | {metadata['date_from']} to {metadata['date_to']} | {metadata['currency']} | {metadata['target_move']}"
        if last_column:
            report_sheet.merge_range(0, 0, 0, last_column, self._report_type_label(), formats["title"])
            report_sheet.merge_range(1, 0, 1, last_column, subtitle, formats["subtitle"])
        else:
            report_sheet.write(0, 0, self._report_type_label(), formats["title"])
            report_sheet.write(1, 0, subtitle, formats["subtitle"])
        header_row = 3
        for column_idx, (fieldname, label) in enumerate(columns):
            report_sheet.write(header_row, column_idx, label, formats["header"])
            width = 15
            if fieldname in {
                "label", "line_name", "field_label", "account_name", "partner_name",
                "details", "evidence", "next_action", "source_reference",
            }:
                width = 30
            report_sheet.set_column(column_idx, column_idx, width)
        data_rows = rows or [{"label": "No rows for the selected filters"}]
        for row_idx, row in enumerate(data_rows, start=header_row + 1):
            for column_idx, (fieldname, _label) in enumerate(columns):
                self._xlsx_write_value(report_sheet, row_idx, column_idx, row.get(fieldname), formats, fieldname)
        report_sheet.freeze_panes(header_row + 1, 0)
        report_sheet.autofilter(header_row, 0, header_row + len(data_rows), last_column)
        report_sheet.set_landscape()
        report_sheet.fit_to_pages(1, 0)
        report_sheet.repeat_rows(header_row, header_row)
        report_sheet.set_header(f"&L{self.company_id.display_name}&C{self._report_type_label()}&R{self.date_to}")
        report_sheet.set_footer("&LOdoo Community accounting export&CPage &P of &N&RGenerated &D &T")

        raw_sheet = workbook.add_worksheet("Audit Data")
        raw_sheet.hide_gridlines(2)
        fieldnames = sorted({key for row in rows for key in row}) or ["empty_report"]
        for column_idx, fieldname in enumerate(fieldnames):
            raw_sheet.write(0, column_idx, fieldname, formats["header"])
            width = max(12, min(42, len(fieldname) + 3))
            if fieldname in {"details", "evidence", "label", "next_action", "source_reference"}:
                width = 38
            raw_sheet.set_column(column_idx, column_idx, width)
        for row_idx, row in enumerate(rows or [{"empty_report": "true"}], start=1):
            for column_idx, fieldname in enumerate(fieldnames):
                self._xlsx_write_value(raw_sheet, row_idx, column_idx, row.get(fieldname), formats, fieldname)
        raw_sheet.freeze_panes(1, 0)
        raw_sheet.autofilter(0, 0, max(1, len(rows)), max(0, len(fieldnames) - 1))

        workbook.close()
        return output.getvalue()

    def _pdf_payload(self, rows):
        try:
            from reportlab.lib import colors  # noqa: PLC0415
            from reportlab.lib.enums import (  # noqa: PLC0415
                TA_CENTER,
                TA_LEFT,
                TA_RIGHT,
            )
            from reportlab.lib.pagesizes import A4, landscape  # noqa: PLC0415
            from reportlab.lib.styles import (  # noqa: PLC0415
                ParagraphStyle,
                getSampleStyleSheet,
            )
            from reportlab.lib.units import mm  # noqa: PLC0415
            from reportlab.pdfbase import pdfmetrics  # noqa: PLC0415
            from reportlab.pdfbase.ttfonts import TTFError, TTFont  # noqa: PLC0415
            from reportlab.platypus import (  # noqa: I001, PLC0415
                BaseDocTemplate,
                Frame,
                PageBreak,
                PageTemplate,
                Paragraph,
                Spacer,
                Table,
                TableStyle,
            )
        except ImportError as exc:
            message = "PDF export requires the reportlab Python package in the Odoo runtime."
            raise UserError(message) from exc

        font_name = "Helvetica"
        bold_font_name = "Helvetica-Bold"
        try:
            pdfmetrics.registerFont(TTFont("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
            font_name = "DejaVuSans"
            bold_font_name = "DejaVuSans-Bold"
        except (OSError, TTFError):
            font_name = "Helvetica"
            bold_font_name = "Helvetica-Bold"

        output = io.BytesIO()
        metadata = self._export_metadata(len(rows))
        date_from_display = fields.Date.to_date(metadata["date_from"]).strftime("%d/%m/%Y")
        date_to_display = fields.Date.to_date(metadata["date_to"]).strftime("%d/%m/%Y")
        columns = self._report_export_columns(rows)
        wide_report = self.report_type in {
            "general_ledger", "closing_package", "french_tax_package", "depreciation_schedule",
        } or len(columns) > 7
        page_size = landscape(A4) if wide_report else A4
        document = BaseDocTemplate(
            output,
            pagesize=page_size,
            leftMargin=13 * mm,
            rightMargin=13 * mm,
            topMargin=23 * mm,
            bottomMargin=17 * mm,
            title=self._report_type_label(),
            author=self.company_id.display_name,
            subject=f"Accounting report for {self.date_from} to {self.date_to}",
        )
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(
            name="USLTitle",
            parent=styles["Title"],
            fontName=bold_font_name,
            fontSize=20,
            leading=24,
            textColor=colors.HexColor("#17324D"),
            alignment=TA_LEFT,
            spaceAfter=3 * mm,
        ))
        styles.add(ParagraphStyle(
            name="USLSubtitle",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#52606D"),
            spaceAfter=5 * mm,
        ))
        styles.add(ParagraphStyle(
            name="USLSection",
            parent=styles["Heading2"],
            fontName=bold_font_name,
            fontSize=12,
            leading=15,
            textColor=colors.HexColor("#17324D"),
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
        ))
        styles.add(ParagraphStyle(
            name="USLBody",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=7.2,
            leading=9,
            alignment=TA_LEFT,
        ))
        styles.add(ParagraphStyle(
            name="USLBodyRight",
            parent=styles["USLBody"],
            alignment=TA_RIGHT,
        ))
        styles.add(ParagraphStyle(
            name="USLHeaderCell",
            parent=styles["USLBody"],
            fontName=bold_font_name,
            textColor=colors.white,
            alignment=TA_CENTER,
        ))
        styles.add(ParagraphStyle(
            name="USLNote",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#374151"),
            backColor=colors.HexColor("#F3F5F7"),
            borderColor=colors.HexColor("#C9D2DB"),
            borderWidth=0.5,
            borderPadding=7,
            spaceAfter=4 * mm,
        ))

        def clean_text(value):
            text = "" if value is None else str(value)
            if font_name == "Helvetica":
                return text.encode("latin-1", "replace").decode("latin-1")
            return text

        def amount_text(value):
            if value in (None, "", False):
                return ""
            try:
                return f"{Decimal(str(value)):,.2f}".replace(",", " ").replace(".", ",")
            except (ArithmeticError, TypeError, ValueError):
                return clean_text(value)

        numeric_fields = {
            "opening_balance", "debit", "credit", "balance", "closing_balance", "movement",
            "amount", "gross_amount", "depreciation_amount", "net_amount", "residual",
            "presented_residual", "amount_residual", "imported_period_net_value", "original_value",
            "amount_currency", "rounded_amount", "statement_balance",
            "period_value", "comparison_value", "difference",
        }
        status_labels = {
            "pass": "Conforme",
            "warning": "Alerte",
            "block": "Bloquant",
            "not_applicable": "Sans objet",
            "blocked": "Bloqué",
            "ready": "Prêt",
            "open": "Ouvert",
            "internal_review": "Revue interne",
            "accountant_review": "Revue comptable",
            "data_missing": "Données manquantes",
            "matched": "Concordant",
            "mismatch": "Ecart",
            "review": "A revoir",
            "prefilled": "Prérempli",
            "unresolved": "Non résolu",
            "posted": "Ecritures comptabilisées",
        }

        def cell(value, fieldname=""):
            if fieldname in {"status", "validation", "review_status"}:
                display = status_labels.get(str(value or ""), clean_text(value))
            else:
                display = amount_text(value) if fieldname in numeric_fields else clean_text(value)
            style = styles["USLBodyRight"] if fieldname in numeric_fields else styles["USLBody"]
            return Paragraph(display.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), style)

        def table_style(extra=None):
            commands = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), bold_font_name),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B8C2CC")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F8FA")]),
            ]
            return TableStyle([*commands, *(extra or [])])

        def append_chunked_table(
            section_title,
            header_labels,
            body_rows,
            column_widths,
            *,
            chunk_size,
            status_values=None,
            emphasis_values=None,
        ):
            chunks = [body_rows[index:index + chunk_size] for index in range(0, len(body_rows), chunk_size)] or [[]]
            for chunk_index, chunk in enumerate(chunks):
                story.append(PageBreak())
                continuation = " (suite)" if chunk_index else ""
                story.append(Paragraph(clean_text(section_title + continuation), styles["USLSection"]))
                header = [Paragraph(clean_text(label), styles["USLHeaderCell"]) for label in header_labels]
                extra_style = []
                if status_values:
                    status_colors = {
                        "pass": "#E7F4EC",
                        "warning": "#FFF4D6",
                        "block": "#FDE8E8",
                        "not_applicable": "#EEF1F4",
                    }
                    chunk_statuses = status_values[
                        chunk_index * chunk_size:(chunk_index * chunk_size) + len(chunk)
                    ]
                    for row_index, status in enumerate(chunk_statuses, start=1):
                        if status in status_colors:
                            extra_style.append((
                                "BACKGROUND", (0, row_index), (0, row_index),
                                colors.HexColor(status_colors[status]),
                            ))
                if emphasis_values:
                    chunk_emphasis = emphasis_values[
                        chunk_index * chunk_size:(chunk_index * chunk_size) + len(chunk)
                    ]
                    for row_index, emphasized in enumerate(chunk_emphasis, start=1):
                        if emphasized:
                            extra_style.extend([
                                ("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#E8EDF2")),
                                ("FONTNAME", (0, row_index), (-1, row_index), bold_font_name),
                            ])
                chunk_table = Table([header, *chunk], colWidths=column_widths, repeatRows=1)
                chunk_table.setStyle(table_style(extra_style))
                story.append(chunk_table)

        def draw_page(canvas, doc):
            width, height = page_size
            canvas.saveState()
            canvas.setStrokeColor(colors.HexColor("#17324D"))
            canvas.setLineWidth(1.2)
            canvas.line(doc.leftMargin, height - 13 * mm, width - doc.rightMargin, height - 13 * mm)
            canvas.setFont(bold_font_name, 8.5)
            canvas.setFillColor(colors.HexColor("#17324D"))
            canvas.drawString(doc.leftMargin, height - 10.5 * mm, clean_text(self.company_id.display_name.upper()))
            canvas.setFont(font_name, 8)
            canvas.setFillColor(colors.HexColor("#52606D"))
            canvas.drawRightString(width - doc.rightMargin, height - 10.5 * mm, clean_text(f"Arrêté au {date_to_display}"))
            canvas.setStrokeColor(colors.HexColor("#AAB4BE"))
            canvas.setLineWidth(0.5)
            canvas.line(doc.leftMargin, 11 * mm, width - doc.rightMargin, 11 * mm)
            canvas.setFont(font_name, 7.5)
            canvas.drawString(doc.leftMargin, 7.5 * mm, clean_text("Odoo Community - Dossier comptable reproductible"))
            canvas.drawCentredString(width / 2, 7.5 * mm, clean_text(f"Généré le {metadata['generated_at']}"))
            canvas.drawRightString(width - doc.rightMargin, 7.5 * mm, clean_text(f"Page {doc.page}"))
            canvas.restoreState()

        title = "Dossier de clôture" if self.report_type == "closing_package" else self._report_type_label()
        story = [
            Paragraph(clean_text(title), styles["USLTitle"]),
            Paragraph(
                clean_text(
                    f"{metadata['company']} - Exercice du {date_from_display} au {date_to_display} - "
                    f"Monnaie {metadata['currency']} - {metadata['target_move']}",
                ),
                styles["USLSubtitle"],
            ),
        ]
        identity_data = [
            [Paragraph("Société", styles["USLHeaderCell"]), cell(metadata["legal_name"]),
             Paragraph("Identifiant", styles["USLHeaderCell"]), cell(metadata["company_registry"] or metadata["vat_number"] or "Non renseigné")],
            [Paragraph("Adresse", styles["USLHeaderCell"]), cell(metadata["address"] or "Non renseignée"),
             Paragraph("Périmètre", styles["USLHeaderCell"]), cell(f"{len(rows)} ligne(s), {metadata['target_move']}")],
        ]
        identity_value_width = (document.width - (49 * mm)) / 2
        identity_table = Table(identity_data, colWidths=[24 * mm, identity_value_width, 25 * mm, identity_value_width])
        identity_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#17324D")),
            ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#17324D")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8C2CC")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.extend([identity_table, Spacer(1, 4 * mm)])
        if metadata.get("report_variant_basis"):
            story.append(Paragraph(clean_text(metadata["report_variant_basis"]), styles["USLNote"]))

        if self.report_type == "closing_package":
            story.append(Paragraph(
                "Ce dossier prépare la revue de clôture et centralise les contrôles, obligations, sources et actions. "
                "Il ne constitue ni une attestation professionnelle ni une déclaration déposée. Les décisions d'acceptation "
                "restent enregistrées séparément par l'autorité habilitée.",
                styles["USLNote"],
            ))
            overview = next((row for row in rows if row.get("line_code") == "CLOSE_STATUS"), {})
            story.append(Paragraph("Synthèse de préparation", styles["USLSection"]))
            overview_data = [
                [Paragraph("Etat", styles["USLHeaderCell"]), Paragraph("Validation", styles["USLHeaderCell"]),
                 Paragraph("Contrôles", styles["USLHeaderCell"]), Paragraph("Synthèse", styles["USLHeaderCell"])],
                [cell(overview.get("status"), "status"), cell(overview.get("validation"), "validation"),
                 cell(overview.get("record_count")), cell(overview.get("details"))],
            ]
            overview_table = Table(overview_data, colWidths=[28 * mm, 28 * mm, 22 * mm, document.width - 78 * mm])
            overview_table.setStyle(table_style())
            story.extend([overview_table, Spacer(1, 3 * mm)])

            control_rows = [row for row in rows if str(row.get("section") or "").startswith("Closing control")]
            control_data = []
            for row in control_rows:
                control_data.append([
                    cell(row.get("status"), "status"), cell(row.get("label")), cell(row.get("record_count")),
                    cell(row.get("amount"), "amount"), cell(row.get("details")), cell(row.get("next_action")),
                ])
            control_widths = [22 * mm, 38 * mm, 12 * mm, 22 * mm, (document.width - 94 * mm) * 0.45, (document.width - 94 * mm) * 0.55]
            append_chunked_table(
                "Contrôles de clôture",
                ["Statut", "Contrôle", "Nb", "Montant", "Constat", "Action suivante"],
                control_data,
                control_widths,
                chunk_size=8,
                status_values=[row.get("status") for row in control_rows],
            )

            declaration_rows = [row for row in rows if row.get("section") == "Declaration schedule"]
            declaration_data = [[
                cell(row.get("line_code")), cell(row.get("label")), cell(row.get("status"), "status"),
                cell(row.get("validation"), "validation"), cell(row.get("amount"), "amount"), cell(row.get("evidence")),
            ] for row in declaration_rows]
            append_chunked_table(
                "Calendrier déclaratif",
                ["Formulaire", "Obligation", "Statut", "Contrôle", "Montant", "Echéance et source"],
                declaration_data,
                [24 * mm, 48 * mm, 25 * mm, 27 * mm, 25 * mm, document.width - 149 * mm],
                chunk_size=7,
            )

            field_rows = [row for row in rows if str(row.get("section") or "").startswith("Declaration fields")]
            field_data = []
            for row in field_rows:
                form_code = str(row.get("section") or "").replace("Declaration fields - ", "")
                source_action = " - ".join(filter(None, [str(row.get("evidence") or ""), str(row.get("next_action") or "")]))
                field_data.append([
                    cell(form_code), cell(f"{row.get('line_code', '')} - {row.get('label', '')}"),
                    cell(row.get("validation"), "validation"), cell(row.get("amount"), "amount"),
                    cell(row.get("details")), cell(source_action),
                ])
            append_chunked_table(
                "Traçabilité des champs déclaratifs",
                ["Formulaire", "Champ", "Statut", "Montant", "Valeur ou formule", "Source / action"],
                field_data,
                [22 * mm, 47 * mm, 25 * mm, 23 * mm, (document.width - 117 * mm) * 0.45, (document.width - 117 * mm) * 0.55],
                chunk_size=8,
            )

            lock_row = next((row for row in rows if row.get("line_code") == "LOCK_EVIDENCE"), {})
            lock_details = lock_row.get("details") or ""
            if lock_details.strip() == "previous=; final=":
                lock_details = "Aucune preuve de verrouillage final n'est encore enregistrée."
            story.extend([
                PageBreak(),
                Paragraph("Dates de verrouillage et responsabilité", styles["USLSection"]),
                Paragraph(clean_text(lock_details or "Aucune preuve de verrouillage enregistrée."), styles["USLNote"]),
                Paragraph(clean_text(lock_row.get("next_action") or ""), styles["USLNote"]),
            ])
        else:
            long_fields = {"label", "line_name", "field_label", "account_name", "partner_name", "details", "source_reference"}
            fixed_widths = []
            for fieldname, _label in columns:
                if fieldname in numeric_fields or fieldname in {
                    "date", "due_date", "depreciation_date", "acquisition_date",
                    "account_code", "journal_code", "line_code", "field_code", "form_code",
                    "currency", "status", "review_status",
                }:
                    fixed_widths.append(23 * mm)
                elif fieldname in long_fields:
                    fixed_widths.append(0)
                else:
                    fixed_widths.append(27 * mm)
            remaining = max(25 * mm, document.width - sum(fixed_widths))
            flexible_count = max(1, fixed_widths.count(0))
            column_widths = [width or (remaining / flexible_count) for width in fixed_widths]
            data_rows = rows or [{"label": "Aucune ligne pour les filtres sélectionnés"}]
            table_rows = [
                [cell(row.get(fieldname), fieldname) for fieldname, _label in columns]
                for row in data_rows
            ]
            emphasis_values = []
            for row in data_rows:
                label = " ".join(str(row.get(key) or "") for key in ("line_code", "label", "line_name", "field_label"))
                emphasis_values.append("TOTAL" in label.upper() or "RESULT" in label.upper())
            append_chunked_table(
                "Détail du rapport",
                [label for _field, label in columns],
                table_rows,
                column_widths,
                chunk_size=20,
                emphasis_values=emphasis_values,
            )

        document.addPageTemplates([PageTemplate(
            id="USLAccountingReport",
            pagesize=page_size,
            frames=[Frame(
                document.leftMargin,
                document.bottomMargin,
                document.width,
                document.height,
                id="USLAccountingReportFrame",
            )],
            onPage=draw_page,
        )])
        document.build(story)
        return output.getvalue()

    def _report_rows(self):
        self.ensure_one()
        current_rows = self._raw_report_rows(
            self.date_from,
            self.date_to,
        )
        current_rows = self._search_report_rows(current_rows)
        current_rows = self._group_report_rows(current_rows)
        comparison_rows = []
        if self.comparison_mode != "none":
            comparison_rows = self._raw_report_rows(
                self.comparison_date_from,
                self.comparison_date_to,
            )
            comparison_rows = self._search_report_rows(
                comparison_rows,
            )
            comparison_rows = self._group_report_rows(
                comparison_rows,
            )
        return self._attach_comparison_values(
            current_rows,
            comparison_rows,
        )

    def _raw_report_rows(self, date_from, date_to):
        self.ensure_one()
        rows = []
        for company in self._selected_companies():
            clone = self.env[self._name].with_company(company).create(
                self._report_clone_values(
                    company,
                    date_from,
                    date_to,
                ),
            )
            try:
                company_rows = clone._report_rows_single()
            finally:
                clone.sudo().unlink()
            for row in company_rows:
                row = dict(row)
                row.update({
                    "report_company_id": company.id,
                    "report_company_name": company.display_name,
                    "report_currency_id": company.currency_id.id,
                    "report_currency": company.currency_id.name,
                })
                rows.append(row)
        return rows

    def _report_clone_values(self, company, date_from, date_to):
        journals = self.journal_ids.filtered(
            lambda journal: not journal.company_id
            or journal.company_id == company,
        )
        accounts = self.account_ids.filtered(
            lambda account: company in account.company_ids,
        )
        values = {
            "report_type": self.report_type,
            "company_id": company.id,
            "company_ids": [Command.set([company.id])],
            "data_scope": self.data_scope,
            "period_preset": "custom",
            "date_from": date_from,
            "date_to": date_to,
            "comparison_mode": "none",
            "target_move": self.target_move,
            "export_format": self.export_format,
            "fec_test_mode": self.fec_test_mode,
            "journal_ids": [Command.set(journals.ids)],
            "account_ids": [Command.set(accounts.ids)],
            "partner_ids": [Command.set(self.partner_ids.ids)],
            "group_by": "none",
            "preview_limit": self.preview_limit,
        }
        if self.analytic_plan_ids:
            values["analytic_plan_ids"] = [
                Command.set(self.analytic_plan_ids.ids),
            ]
        if self.analytic_account_ids:
            analytic_accounts = self.analytic_account_ids.filtered(
                lambda account: not account.company_id
                or account.company_id == company,
            )
            values["analytic_account_ids"] = [
                Command.set(analytic_accounts.ids),
            ]
        return values

    def _search_report_rows(self, rows):
        self.ensure_one()
        query = (self.search_text or "").strip().casefold()
        if not query:
            return rows
        result = []
        for row in rows:
            searchable = " ".join(
                str(value)
                for key, value in row.items()
                if value not in (None, False)
                and not key.startswith("source_")
                and not key.endswith("_id")
            ).casefold()
            if query in searchable:
                result.append(row)
        return result

    def _group_report_rows(self, rows):
        self.ensure_one()
        if self.group_by == "none":
            return rows
        groups = {}
        for row in rows:
            group_key, label, group_values = self._report_group(row)
            bucket = groups.setdefault(
                group_key,
                {
                    "label": label,
                    "values": group_values,
                    "rows": [],
                },
            )
            bucket["rows"].append(row)
        result = []
        for group_key, bucket in groups.items():
            children = bucket["rows"]
            group_row = {
                **bucket["values"],
                "is_group": "true",
                "row_level": 0,
                "group_key": group_key,
                "label": bucket["label"],
                "record_count": str(len(children)),
            }
            for field_name in self._summable_report_fields():
                values = [
                    _amount(row.get(field_name))
                    for row in children
                    if row.get(field_name) not in (None, "")
                ]
                if values:
                    group_row[field_name] = _amount_text(sum(values))
            result.append(group_row)
            result.extend([
                {
                    **row,
                    "is_group": "false",
                    "row_level": 1,
                    "parent_group_key": group_key,
                }
                for row in children
            ])
        return result

    def _report_group(self, row):
        company_key = str(row.get("report_company_id") or "")
        company_name = row.get("report_company_name") or ""
        field_map = {
            "section": (
                "section",
                row.get("section")
                or row.get("statement_name")
                or row.get("report_section")
                or row.get("form_code"),
            ),
            "account": (
                "account_code",
                row.get("account_code")
                or row.get("asset_account")
                or row.get("deferred_account_code"),
            ),
            "partner": ("partner_name", row.get("partner_name")),
            "journal": (
                "journal_code",
                row.get("journal_code")
                or row.get("journal_name"),
            ),
            "analytic": (
                "analytic_name",
                row.get("analytic_name")
                or row.get("analytic_account_name")
                or row.get("analytic_code"),
            ),
        }
        if self.group_by == "month":
            raw_date = (
                row.get("date")
                or row.get("due_date")
                or row.get("deferred_date")
                or row.get("depreciation_date")
                or ""
            )
            value = str(raw_date)[:7] or "No date"
            field_name = "report_month"
        else:
            field_name, value = field_map.get(
                self.group_by,
                ("section", ""),
            )
            value = str(value or "Not specified")
        group_key = f"{company_key}|{self.group_by}|{value}"
        label = (
            f"{company_name} — {value}"
            if len(self.company_ids or self.company_id) > 1
            else value
        )
        values = {
            "report_company_id": row.get("report_company_id"),
            "report_company_name": company_name,
            "report_currency_id": row.get("report_currency_id"),
            "report_currency": row.get("report_currency"),
            field_name: value,
        }
        if field_name == "account_code":
            values["account_name"] = row.get("account_name") or ""
        return group_key, label, values

    @staticmethod
    def _summable_report_fields():
        return {
            "opening_balance",
            "debit",
            "credit",
            "balance",
            "closing_balance",
            "movement",
            "amount",
            "gross_amount",
            "depreciation_amount",
            "net_amount",
            "residual",
            "presented_residual",
            "amount_residual",
            "imported_period_net_value",
            "original_value",
            "amount_currency",
            "rounded_amount",
            "statement_balance",
            "not_due",
            "bucket_1_30",
            "bucket_31_60",
            "bucket_61_90",
            "bucket_over_90",
            "total",
            "allocated_debit",
            "allocated_credit",
            "allocated_balance",
        }

    def _attach_comparison_values(self, current_rows, comparison_rows):
        self.ensure_one()
        comparison_by_key = {
            self._comparison_key(row): row
            for row in comparison_rows
            if not (
                self.group_by != "none"
                and row.get("is_group") not in (True, "true")
            )
        }
        result = []
        seen_keys = set()
        for row in current_rows:
            row = dict(row)
            key = self._comparison_key(row)
            seen_keys.add(key)
            period_value = self._row_period_value(row)
            row["period_value"] = _amount_text(period_value)
            if self.comparison_mode == "none":
                result.append(row)
                continue
            if (
                self.group_by != "none"
                and row.get("is_group") not in (True, "true")
            ):
                row.update({
                    "comparison_value": "",
                    "difference": "",
                })
                result.append(row)
                continue
            comparison_row = comparison_by_key.get(key, {})
            comparison_value = self._row_period_value(comparison_row)
            row.update({
                "comparison_value": _amount_text(comparison_value),
                "difference": _amount_text(
                    period_value - comparison_value,
                ),
            })
            result.append(row)
        for key, comparison_row in comparison_by_key.items():
            if key in seen_keys:
                continue
            comparison_value = self._row_period_value(comparison_row)
            row = {
                **comparison_row,
                "period_value": "0.00",
                "comparison_value": _amount_text(comparison_value),
                "difference": _amount_text(-comparison_value),
                "comparison_only": "true",
            }
            result.append(row)
        return result

    def _comparison_key(self, row):
        if row.get("group_key"):
            return ("group", row["group_key"])
        values = [
            row.get("report_company_id"),
            row.get("section")
            or row.get("statement_name")
            or row.get("report_section")
            or row.get("form_code"),
            row.get("line_code") or row.get("field_code"),
            row.get("account_code")
            or row.get("asset_account")
            or row.get("deferred_account_code"),
            row.get("journal_code"),
            row.get("source_asset_id"),
            row.get("source_partner_id") or row.get("partner_name"),
            row.get("analytic_key")
            or row.get("analytic_name")
            or row.get("analytic_account_name"),
            row.get("source_line_id")
            or row.get("source_statement_line_id")
            or row.get("move_name"),
        ]
        return tuple(str(value or "") for value in values)

    def _row_period_value(self, row):
        if not row:
            return Decimal("0.00")
        preferred = {
            "aged_receivable": ("total", "residual"),
            "aged_payable": ("total", "residual"),
            "fixed_assets": (
                "imported_period_net_value",
                "net_amount",
            ),
            "fixed_asset_group_account": (
                "imported_period_net_value",
                "net_amount",
            ),
            "depreciation_schedule": (
                "depreciation_amount",
                "amount",
            ),
            "analytic_report": (
                "allocated_balance",
                "balance",
            ),
        }.get(self.report_type, ())
        keys = (
            *preferred,
            "closing_balance",
            "amount",
            "net_amount",
            "balance",
            "statement_balance",
            "presented_residual",
            "residual",
            "total",
        )
        for key in keys:
            if row.get(key) not in (None, ""):
                return _amount(row[key])
        debit = _amount(row.get("debit"))
        credit = _amount(row.get("credit"))
        return debit - credit

    def _report_rows_single(self):
        if self.report_type == "trial_balance":
            return self._trial_balance_rows()
        if self.report_type == "general_ledger":
            return self._general_ledger_rows()
        if self.report_type == "journal_report":
            return self._journal_report_rows()
        if self.report_type == "partner_ledger":
            return self._partner_ledger_rows()
        if self.report_type == "customer_statement":
            return self._customer_statement_rows()
        if self.report_type == "open_items":
            return self._open_item_rows()
        if self.report_type in ("aged_receivable", "aged_payable"):
            return self._aged_partner_rows(self.report_type == "aged_receivable")
        if self.report_type == "balance_sheet":
            return self._balance_sheet_rows()
        if self.report_type == "profit_loss":
            return self._profit_loss_rows()
        if self.report_type == "tax_report":
            return self._tax_report_rows()
        if self.report_type == "tax_report_group_account_tax":
            return self._tax_report_group_rows("account_tax")
        if self.report_type == "tax_report_group_tax_account":
            return self._tax_report_group_rows("tax_account")
        if self.report_type in ("ec_sales_list", "oss_sales", "oss_imports"):
            return self._eu_tax_report_rows()
        if self.report_type == "bank_reconciliation":
            return self._bank_reconciliation_rows()
        if self.report_type == "currency_report":
            return self._currency_report_rows()
        if self.report_type == "cash_flow":
            return self._management_summary_rows("cash_flow")
        if self.report_type == "executive_summary":
            return self._management_summary_rows("executive_summary")
        if self.report_type == "analytic_report":
            return self._analytic_report_rows()
        if self.report_type == "fixed_assets":
            return self._fixed_asset_rows()
        if self.report_type == "fixed_asset_group_account":
            return self._fixed_asset_group_account_rows()
        if self.report_type == "depreciation_schedule":
            return self._depreciation_schedule_rows()
        if self.report_type == "deferred_schedule":
            return self._deferred_schedule_rows()
        if self.report_type == "french_annual":
            return self._french_annual_rows()
        if self.report_type == "french_balance_sheet_2024":
            return self._french_annual_rows(
                statement_keys={"bilan_actif", "bilan_passif"},
                report_variant=self._report_variant_key(),
            )
        if self.report_type == "french_profit_loss_2024":
            return self._french_annual_rows(
                statement_keys={"compte_resultat"},
                report_variant=self._report_variant_key(),
            )
        if self.report_type == "sig_caf_2024":
            return self._french_annual_rows(
                statement_keys={"sig_caf"},
                report_variant=self._report_variant_key(),
            )
        if self.report_type == "french_tax_package":
            return self._french_tax_package_rows()
        if self.report_type == "closing_package":
            return self._closing_package_rows()
        message = "Unsupported report type."
        raise UserError(message)

    def _state_sql(self):
        return "" if self.target_move == "all" else "AND move.state = 'posted'"

    def _ledger_scope_sql(self):
        if self.data_scope == "imported":
            return (
                "AND line.rebuild_source_model = 'account.move.line' "
                "AND move.rebuild_source_model = 'account.move'"
            )
        return ""

    def _bank_scope_sql(self):
        if self.data_scope == "imported":
            return (
                "AND bsl.rebuild_source_model = "
                "'account.bank.statement.line' "
                "AND move.rebuild_source_model = 'account.move'"
            )
        return ""

    def _analytic_scope_sql(self):
        if self.data_scope == "imported":
            return (
                "AND analytic.rebuild_source_model = "
                "'account.analytic.line'"
            )
        return ""

    def _validate_filter_scope(self, for_drilldown=False):
        if for_drilldown:
            return
        companies = self._selected_companies()
        if self.company_id not in companies:
            message = "The primary company must be included in Companies."
            raise UserError(message)
        if (
            self.analytic_plan_ids or self.analytic_account_ids
        ) and self.report_type != "analytic_report":
            message = (
                "Analytic plan and account filters are available on the "
                "Analytic Report."
            )
            raise UserError(message)
        if self.report_type == "fec":
            if len(companies) != 1:
                message = "Generate one FEC per company."
                raise UserError(message)
            if self.company_id not in self.env.companies:
                message = (
                    "You cannot export a FEC for a company outside your "
                    "allowed companies."
                )
                raise AccessError(message)
            if not self.fec_test_mode and not self.env.user.has_group("account.group_account_manager"):
                message = (
                    "Only an Accounting Manager can generate an official "
                    "non-test FEC because it may update lock dates."
                )
                raise UserError(message)
            if self.export_format != "txt":
                message = "FEC exports must use the FEC TXT format."
                raise UserError(message)
            if self.target_move != "posted":
                message = "Official FEC generation uses posted entries only."
                raise UserError(message)
            if self.journal_ids or self.account_ids or self.partner_ids:
                message = (
                    "FEC exports cannot be filtered by journal, account or "
                    "partner. Use General Ledger for filtered review."
                )
                raise UserError(message)
            return
        if self.report_type in {
            "french_tax_package",
            "closing_package",
        } and len(companies) != 1:
            message = (
                "French statutory and closing packages must be generated "
                "for one company at a time."
            )
            raise UserError(message)
        if self.export_format == "txt":
            message = "The FEC TXT format is only available for FEC exports."
            raise UserError(message)
        if self.report_type in {"french_tax_package", "closing_package"} and (self.journal_ids or self.account_ids or self.partner_ids):
            message = (
                "French statutory benchmark mapping and closing packages "
                "use company and period filters only."
            )
            raise UserError(message)
        if self.report_type in ("fixed_assets", "fixed_asset_group_account", "depreciation_schedule") and (self.journal_ids or self.partner_ids):
            message = (
                "Journal and partner filters are not applicable to "
                "fixed-asset and depreciation-schedule exports."
            )
            raise UserError(message)
        if self.report_type == "bank_reconciliation" and self.account_ids:
            message = (
                "Account filters are not applicable to bank reconciliation "
                "exports. Use the journal, partner and period filters."
            )
            raise UserError(message)

    def _fec_export_payload(self):
        if "l10n_fr.fec.export.wizard" not in self.env:
            message = (
                "French FEC generation requires the l10n_fr_account module."
            )
            raise UserError(message)
        Wizard = self.env["l10n_fr.fec.export.wizard"].sudo().with_company(self.company_id).with_context(
            allowed_company_ids=self.company_id.ids,
            fec_test_mode=self.fec_test_mode,
        )
        fec_wizard = Wizard.create({
            "date_from": self.date_from,
            "date_to": self.date_to,
            "test_file": self.fec_test_mode,
            "export_type": "official",
        })
        result = fec_wizard.with_context(
            allowed_company_ids=self.company_id.ids,
            # The native generator otherwise opens a second cursor while
            # lazily streaming the file. This wrapper creates the transient
            # and consumes that stream in one request, so the second cursor
            # cannot see the uncommitted wizard. Official/test behavior is
            # governed by test_file; this context only keeps stream reads on
            # the current transaction cursor.
            fec_test_mode=True,
        ).generate_fec()
        content = b"".join(result["file_content"])
        stats = self._fec_file_stats(content)
        metadata = self._export_metadata(stats["row_count"])
        metadata.update({
            "file_name": result["file_name"],
            "file_type": result["file_type"],
            "sha256": hashlib.sha256(content).hexdigest(),
            "debit": stats["debit"],
            "credit": stats["credit"],
            "header": stats["header"],
            "validation": "not_official_dgfip_validation",
        })
        return content, result["file_name"], metadata

    def _fec_file_stats(self, content):
        decoded = content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(decoded), delimiter="|"))
        header = rows[0] if rows else []
        data_rows = rows[1:]
        debit = sum(_fec_amount(row[11]) for row in data_rows if len(row) > 12)
        credit = sum(_fec_amount(row[12]) for row in data_rows if len(row) > 12)
        return {
            "header": header,
            "row_count": len(data_rows),
            "debit": _amount_text(debit),
            "credit": _amount_text(credit),
        }

    def _line_filter_sql(self):
        clauses = []
        params = []
        if self.journal_ids:
            clauses.append("AND move.journal_id IN %s")
            params.append(tuple(self.journal_ids.ids))
        if self.account_ids:
            clauses.append("AND line.account_id IN %s")
            params.append(tuple(self.account_ids.ids))
        if self.partner_ids:
            clauses.append("AND line.partner_id IN %s")
            params.append(tuple(self.partner_ids.ids))
        return "\n               ".join(clauses), params

    def _analytic_filter_sql(self):
        clauses = []
        params = []
        if self.journal_ids:
            clauses.append("AND line.journal_id IN %s")
            params.append(tuple(self.journal_ids.ids))
        if self.account_ids:
            clauses.append("AND analytic.general_account_id IN %s")
            params.append(tuple(self.account_ids.ids))
        if self.partner_ids:
            clauses.append("AND analytic.partner_id IN %s")
            params.append(tuple(self.partner_ids.ids))
        if self.analytic_plan_ids:
            clauses.append("AND analytic_account.plan_id IN %s")
            params.append(tuple(self.analytic_plan_ids.ids))
        if self.analytic_account_ids:
            clauses.append("AND analytic.account_id IN %s")
            params.append(tuple(self.analytic_account_ids.ids))
        return "\n               ".join(clauses), params

    def _analytic_state_sql(self):
        return "" if self.target_move == "all" else "AND (move.id IS NULL OR move.state = 'posted')"

    def _asset_account_filter_sql(self):
        if not self.account_ids:
            return "", []
        account_ids = tuple(self.account_ids.ids)
        return (
            "AND (asset.asset_account_id IN %s "
            "OR asset.depreciation_account_id IN %s "
            "OR asset.depreciation_expense_account_id IN %s)"
        ), [account_ids, account_ids, account_ids]

    def _bank_filter_sql(self):
        clauses = []
        params = []
        if self.journal_ids:
            clauses.append("AND bsl.journal_id IN %s")
            params.append(tuple(self.journal_ids.ids))
        if self.partner_ids:
            clauses.append("AND bsl.partner_id IN %s")
            params.append(tuple(self.partner_ids.ids))
        return "\n               ".join(clauses), params

    def _deferred_schedule_filter_sql(self):
        clauses = []
        params = []
        if self.journal_ids:
            clauses.append("AND schedule.journal_id IN %s")
            params.append(tuple(self.journal_ids.ids))
        if self.partner_ids:
            clauses.append("AND schedule.partner_id IN %s")
            params.append(tuple(self.partner_ids.ids))
        if self.account_ids:
            account_codes = [
                account.code_store.get(str(self.company_id.rebuild_source_id))
                or account.code_store.get("1")
                or next(iter(account.code_store.values()), "")
                if isinstance(account.code_store, dict)
                else str(account.code_store or "")
                for account in self.account_ids
            ]
            code_clauses = []
            for code in account_codes:
                if not code:
                    continue
                code_clauses.append("(schedule.deferred_account_code LIKE %s OR schedule.counterpart_account_codes LIKE %s)")
                params.extend([f"%{code}%", f"%{code}%"])
            if code_clauses:
                clauses.append("AND (" + " OR ".join(code_clauses) + ")")
        return "\n               ".join(clauses), params

    def _closing_package_rows(self):
        closing = self.env["rebuild.account.closing.period"].search([
            ("company_id", "=", self.company_id.id),
            ("date_from", "=", self.date_from),
            ("date_to", "=", self.date_to),
        ], order="period_type desc, id desc", limit=1)
        if not closing:
            message = (
                "No closing workspace matches the selected company and exact dates. "
                "Open Closing Workspaces, synchronize the company profile and use "
                "Generate Package from that period."
            )
            raise UserError(message)
        rows = [{
            "section": "Closing overview",
            "line_code": "CLOSE_STATUS",
            "label": closing.name,
            "status": closing.state,
            "validation": closing.readiness_status,
            "record_count": str(len(closing.control_line_ids)),
            "amount": "0.00",
            "details": closing.readiness_summary or "",
            "next_action": closing.actions_awaiting_valentin or "",
            "evidence": closing.package_reference or "",
        }]
        rows.extend({
            "section": f"Closing control - {control.category}",
            "line_code": control.code,
            "label": control.name,
            "status": control.status,
            "validation": control.status,
            "record_count": str(control.record_count),
            "amount": _amount_text(control.amount),
            "details": control.summary or "",
            "next_action": control.next_action or "",
            "evidence": f"owner={control.owner}; accountant_visible={control.accountant_visible}",
        } for control in closing.control_line_ids.sorted(lambda line: (line.category, line.code)))
        declarations = self.env["rebuild.account.declaration"].search([
            ("company_id", "=", self.company_id.id),
            ("fiscalyear_start", "=", closing.fiscalyear_start),
            ("fiscalyear_end", "=", closing.fiscalyear_end),
        ])
        for declaration in declarations:
            rows.append({
                "section": "Declaration schedule",
                "line_code": declaration.form_code,
                "label": declaration.name,
                "status": declaration.status,
                "validation": declaration.validation_status,
                "record_count": str(declaration.prefilled_line_count),
                "amount": _amount_text(declaration.amount_due),
                "details": declaration.validation_summary or "",
                "next_action": declaration.unresolved_information or "",
                "evidence": (
                    f"rule={declaration.rule_id.code}/{declaration.rule_version}; "
                    f"deadline={fields.Date.to_string(declaration.deadline_date)}; "
                    f"official_source={declaration.official_url}; filing_reference={declaration.external_filing_reference or ''}"
                ),
            })
            rows.extend({
                "section": f"Declaration fields - {declaration.form_code}",
                "line_code": field.field_code,
                "label": field.field_label,
                "status": "unresolved" if field.is_unresolved else "prefilled",
                "validation": field.validation_status,
                "record_count": "1",
                "amount": _amount_text(field.amount),
                "details": field.value_text or field.source_formula or "",
                "next_action": field.unresolved_reason or "",
                "evidence": f"{field.source_kind}: {field.source_reference or ''}",
            } for field in declaration.field_line_ids)
        rows.append({
            "section": "Lock dates",
            "line_code": "LOCK_EVIDENCE",
            "label": "Standard Odoo lock-date evidence",
            "status": closing.state,
            "validation": "recorded" if closing.final_lock_dates else "pending",
            "record_count": "0",
            "amount": "0.00",
            "details": f"previous={closing.previous_lock_dates or ''}; final={closing.final_lock_dates or ''}",
            "next_action": "Final lock dates are applied only by an Accounting Manager after closing controls and reviewer approval.",
            "evidence": f"closed_by={closing.closed_by_id.name or ''}; closed_at={closing.closed_at or ''}",
        })
        return rows

    def _trial_balance_rows(self):
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT {ACCOUNT_CODE_SQL} AS account_code,
                   {ACCOUNT_NAME_SQL} AS account_name,
                   account.account_type AS account_type,
                   account.rebuild_source_id::text AS source_account_id,
                   count(line.id) FILTER (
                       WHERE move.date BETWEEN %s AND %s
                   )::text AS move_line_count,
                   round(sum(
                       CASE
                           WHEN move.date < %s THEN line.balance
                           ELSE 0
                       END
                   )::numeric, 2)::text AS opening_balance,
                   round(sum(
                       CASE
                           WHEN move.date BETWEEN %s AND %s
                           THEN line.debit
                           ELSE 0
                       END
                   )::numeric, 2)::text AS debit,
                   round(sum(
                       CASE
                           WHEN move.date BETWEEN %s AND %s
                           THEN line.credit
                           ELSE 0
                       END
                   )::numeric, 2)::text AS credit,
                   round(sum(
                       CASE
                           WHEN move.date BETWEEN %s AND %s
                           THEN line.balance
                           ELSE 0
                       END
                   )::numeric, 2)::text AS movement,
                   round(sum(
                       CASE
                           WHEN move.date BETWEEN %s AND %s
                           THEN line.balance
                           ELSE 0
                       END
                   )::numeric, 2)::text AS balance,
                   round(sum(line.balance)::numeric, 2)::text AS closing_balance
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
              JOIN res_company company ON company.id = line.company_id
              JOIN account_account account ON account.id = line.account_id
             WHERE line.company_id = %s
               AND move.date <= %s
               {self._ledger_scope_sql()}
               {self._state_sql()}
               {filter_sql}
             GROUP BY account.id, company.rebuild_source_id, {ACCOUNT_CODE_SQL}, {ACCOUNT_NAME_SQL}, account.account_type, account.rebuild_source_id
             ORDER BY {ACCOUNT_CODE_SQL}
            """,
            [
                self.date_from,
                self.date_to,
                self.date_from,
                self.date_from,
                self.date_to,
                self.date_from,
                self.date_to,
                self.date_from,
                self.date_to,
                self.date_from,
                self.date_to,
                self.company_id.id,
                self.date_to,
                *filter_params,
            ],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _general_ledger_rows(self):
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT {ACCOUNT_CODE_SQL} AS account_code,
                   {ACCOUNT_NAME_SQL} AS account_name,
                   move.date::text AS date,
                   journal.code AS journal_code,
                   move.name AS move_name,
                   move.ref AS move_ref,
                   line.rebuild_source_id::text AS source_line_id,
                   move.rebuild_source_id::text AS source_move_id,
                   COALESCE(partner.name::text, '') AS partner_name,
                   COALESCE(line.name::text, '') AS label,
                   round(line.debit::numeric, 2)::text AS debit,
                   round(line.credit::numeric, 2)::text AS credit,
                   round(line.balance::numeric, 2)::text AS balance,
                   COALESCE(currency.name::text, '') AS currency,
                   round(line.amount_currency::numeric, 2)::text AS amount_currency,
                   COALESCE(line.matching_number::text, '') AS matching_number
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
              JOIN res_company company ON company.id = line.company_id
              JOIN account_account account ON account.id = line.account_id
              JOIN account_journal journal ON journal.id = move.journal_id
              LEFT JOIN res_partner partner ON partner.id = line.partner_id
              LEFT JOIN res_currency currency ON currency.id = line.currency_id
             WHERE line.company_id = %s
               AND move.date BETWEEN %s AND %s
               {self._ledger_scope_sql()}
               {self._state_sql()}
               {filter_sql}
             ORDER BY {ACCOUNT_CODE_SQL}, move.date, move.name, line.rebuild_source_id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _journal_report_rows(self):
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT journal.code AS journal_code,
                   COALESCE(journal.name->>'fr_FR', journal.name->>'en_US', journal.name::text) AS journal_name,
                   journal.type AS journal_type,
                   count(DISTINCT move.id)::text AS move_count,
                   count(line.id)::text AS move_line_count,
                   round(sum(line.debit)::numeric, 2)::text AS debit,
                   round(sum(line.credit)::numeric, 2)::text AS credit,
                   round(sum(line.balance)::numeric, 2)::text AS balance
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
              JOIN account_journal journal ON journal.id = move.journal_id
             WHERE line.company_id = %s
               AND move.date BETWEEN %s AND %s
               {self._ledger_scope_sql()}
               {self._state_sql()}
               {filter_sql}
             GROUP BY journal.id, journal.code, journal.name, journal.type
             ORDER BY journal.code
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _partner_ledger_rows(self):
        rows = self._general_ledger_rows()
        return [row for row in rows if row.get("partner_name")]

    def _customer_statement_rows(self):
        filter_sql, filter_params = self._line_filter_sql()
        customer_filter_sql = "" if self.partner_ids else "AND partner.customer_rank > 0"
        self.env.cr.execute(
            f"""
            SELECT COALESCE(partner.name::text, '') AS partner_name,
                   COALESCE(partner.rebuild_source_id::text, '') AS source_partner_id,
                   move.date::text AS date,
                   COALESCE(line.date_maturity::text, '') AS due_date,
                   journal.code AS journal_code,
                   move.name AS move_name,
                   move.ref AS move_ref,
                   line.rebuild_source_id::text AS source_line_id,
                   move.rebuild_source_id::text AS source_move_id,
                   {ACCOUNT_CODE_SQL} AS account_code,
                   {ACCOUNT_NAME_SQL} AS account_name,
                   account.account_type AS account_type,
                   COALESCE(line.name::text, '') AS label,
                   round(line.debit::numeric, 2)::text AS debit,
                   round(line.credit::numeric, 2)::text AS credit,
                   round(line.balance::numeric, 2)::text AS balance,
                   round(line.amount_residual::numeric, 2)::text AS residual,
                   round(
                       sum(line.balance) OVER (
                           PARTITION BY partner.id
                           ORDER BY move.date, move.name, line.rebuild_source_id
                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                       )::numeric,
                       2
                   )::text AS running_balance,
                   COALESCE(currency.name::text, '') AS currency,
                   round(line.amount_currency::numeric, 2)::text AS amount_currency,
                   COALESCE(line.matching_number::text, '') AS matching_number,
                   CASE WHEN line.reconciled THEN 'reconciled' ELSE 'open' END AS payment_status
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
              JOIN res_company company ON company.id = line.company_id
              JOIN account_account account ON account.id = line.account_id
              JOIN account_journal journal ON journal.id = move.journal_id
              JOIN res_partner partner ON partner.id = line.partner_id
              LEFT JOIN res_currency currency ON currency.id = line.currency_id
             WHERE line.company_id = %s
               AND move.date BETWEEN %s AND %s
               AND account.account_type = 'asset_receivable'
               {customer_filter_sql}
               {self._ledger_scope_sql()}
               {self._state_sql()}
               {filter_sql}
             ORDER BY partner.name, move.date, move.name, line.rebuild_source_id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _open_item_rows(self):
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT move.date::text AS date,
                   COALESCE(line.date_maturity::text, '') AS due_date,
                   {ACCOUNT_CODE_SQL} AS account_code,
                   {ACCOUNT_NAME_SQL} AS account_name,
                   account.account_type AS account_type,
                   move.name AS move_name,
                   line.rebuild_source_id::text AS source_line_id,
                   COALESCE(partner.name::text, '') AS partner_name,
                   round(line.amount_residual::numeric, 2)::text AS residual,
                   CASE
                       WHEN account.account_type = 'liability_payable' THEN round((-line.amount_residual)::numeric, 2)::text
                       ELSE round(line.amount_residual::numeric, 2)::text
                   END AS presented_residual,
                   COALESCE(line.matching_number::text, '') AS matching_number
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
              JOIN res_company company ON company.id = line.company_id
              JOIN account_account account ON account.id = line.account_id
              LEFT JOIN res_partner partner ON partner.id = line.partner_id
             WHERE line.company_id = %s
               AND move.date BETWEEN %s AND %s
               AND account.account_type IN ('asset_receivable', 'liability_payable')
               AND (line.reconciled IS NOT TRUE OR abs(line.amount_residual) > 0.004)
               {self._ledger_scope_sql()}
               {self._state_sql()}
               {filter_sql}
             ORDER BY line.date_maturity, partner.name, move.name, line.rebuild_source_id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _aged_partner_rows(self, receivable):
        account_type = "asset_receivable" if receivable else "liability_payable"
        sign_sql = "line.amount_residual" if receivable else "-line.amount_residual"
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            WITH open_lines AS (
                SELECT COALESCE(partner.name::text, '') AS partner_name,
                       COALESCE(partner.rebuild_source_id::text, '') AS source_partner_id,
                       (%s::date - COALESCE(line.date_maturity, move.date)) AS age_days,
                       ({sign_sql})::numeric AS residual
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN account_account account ON account.id = line.account_id
                  LEFT JOIN res_partner partner ON partner.id = line.partner_id
                 WHERE line.company_id = %s
                   AND move.date BETWEEN %s AND %s
                   AND account.account_type = %s
                   AND (line.reconciled IS NOT TRUE OR abs(line.amount_residual) > 0.004)
                   {self._ledger_scope_sql()}
                   {self._state_sql()}
                   {filter_sql}
            )
            SELECT partner_name,
                   source_partner_id,
                   count(*)::text AS open_item_count,
                   round(sum(CASE WHEN age_days <= 0 THEN residual ELSE 0 END)::numeric, 2)::text AS not_due,
                   round(sum(CASE WHEN age_days BETWEEN 1 AND 30 THEN residual ELSE 0 END)::numeric, 2)::text AS bucket_1_30,
                   round(sum(CASE WHEN age_days BETWEEN 31 AND 60 THEN residual ELSE 0 END)::numeric, 2)::text AS bucket_31_60,
                   round(sum(CASE WHEN age_days BETWEEN 61 AND 90 THEN residual ELSE 0 END)::numeric, 2)::text AS bucket_61_90,
                   round(sum(CASE WHEN age_days > 90 THEN residual ELSE 0 END)::numeric, 2)::text AS bucket_over_90,
                   round(sum(residual)::numeric, 2)::text AS total
              FROM open_lines
             GROUP BY partner_name, source_partner_id
             ORDER BY partner_name
            """,
            [self.date_to, self.company_id.id, self.date_from, self.date_to, account_type, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _balance_sheet_rows(self):
        rows = []
        for row in self._trial_balance_rows():
            account_type = row["account_type"] or ""
            if account_type.startswith(("income", "expense")):
                continue
            amount = _amount(row["balance"])
            rows.append({
                "statement": "Balance Sheet",
                "section": self._balance_sheet_section(account_type),
                "account_code": row["account_code"],
                "account_name": row["account_name"],
                "account_type": account_type,
                "amount": _amount_text(amount if not account_type.startswith(("liability", "equity")) else -amount),
            })
        result = sum(
            -_amount(row["balance"])
            for row in self._trial_balance_rows()
            if (row["account_type"] or "").startswith(("income", "expense"))
        )
        rows.append({
            "statement": "Balance Sheet",
            "section": "Current-year result",
            "account_code": "RESULT",
            "account_name": "Current-year result",
            "account_type": "equity_current_year_result",
            "amount": _amount_text(result),
        })
        return rows

    def _profit_loss_rows(self):
        rows = []
        for row in self._trial_balance_rows():
            account_type = row["account_type"] or ""
            if not account_type.startswith(("income", "expense")):
                continue
            balance = _amount(row["balance"])
            amount = -balance if account_type.startswith("income") else balance
            rows.append({
                "statement": "Profit and Loss",
                "section": "Income" if account_type.startswith("income") else "Expenses",
                "account_code": row["account_code"],
                "account_name": row["account_name"],
                "account_type": account_type,
                "amount": _amount_text(amount),
            })
        rows.append({
            "statement": "Profit and Loss",
            "section": "Result",
            "account_code": "RESULT",
            "account_name": "Net result",
            "account_type": "result",
            "amount": _amount_text(sum(_amount(row["amount"]) for row in rows if row["section"] == "Income") - sum(_amount(row["amount"]) for row in rows if row["section"] == "Expenses")),
        })
        return rows

    @staticmethod
    def _balance_sheet_section(account_type):
        if account_type in ("asset_fixed", "asset_non_current"):
            return "Fixed assets"
        if account_type.startswith("asset"):
            return "Current assets"
        if account_type.startswith("equity"):
            return "Equity"
        if account_type.startswith("liability"):
            return "Liabilities"
        return "Other"

    def _tax_report_rows(self):
        return [
            {
                "report_section": "VAT accounts",
                "account_code": row["account_code"],
                "account_name": row["account_name"],
                "debit": row["debit"],
                "credit": row["credit"],
                "balance": row["balance"],
                "move_line_count": row["move_line_count"],
            }
            for row in self._trial_balance_rows()
            if (row.get("account_code") or "").startswith("445")
        ]

    def _tax_report_group_rows(self, group_mode):
        filter_sql, filter_params = self._line_filter_sql()
        order_sql = (
            "account_code, COALESCE(tax_tag_name, ''), report_section"
            if group_mode == "account_tax"
            else "COALESCE(tax_tag_name, ''), account_code, report_section"
        )
        grouping_label = "Account > Tax" if group_mode == "account_tax" else "Tax > Account"
        self.env.cr.execute(
            f"""
            WITH tax_grid_lines AS (
                SELECT 'Tax grid tags' AS report_section,
                       tag.rebuild_source_id AS source_tax_tag_id,
                       COALESCE(tag.name->>'fr_FR', tag.name->>'en_US', tag.name::text) AS tax_tag_name,
                       account.rebuild_source_id AS source_account_id,
                       {ACCOUNT_CODE_SQL} AS account_code,
                       {ACCOUNT_NAME_SQL} AS account_name,
                       count(line.id) AS move_line_count,
                       sum(line.debit) AS debit,
                       sum(line.credit) AS credit,
                       sum(line.balance) AS balance,
                       sum(line.tax_base_amount) AS tax_base_amount
                  FROM account_account_tag_account_move_line_rel rel
                  JOIN account_account_tag tag ON tag.id = rel.account_account_tag_id
                  JOIN account_move_line line ON line.id = rel.account_move_line_id
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                 WHERE line.company_id = %s
                   AND move.date BETWEEN %s AND %s
                   {self._ledger_scope_sql()}
                   {self._state_sql()}
                   {filter_sql}
                 GROUP BY tag.rebuild_source_id,
                          COALESCE(tag.name->>'fr_FR', tag.name->>'en_US', tag.name::text),
                          account.rebuild_source_id,
                          {ACCOUNT_CODE_SQL},
                          {ACCOUNT_NAME_SQL}
            ),
            vat_account_lines AS (
                SELECT 'VAT accounts' AS report_section,
                       NULL::integer AS source_tax_tag_id,
                       NULL::text AS tax_tag_name,
                       account.rebuild_source_id AS source_account_id,
                       {ACCOUNT_CODE_SQL} AS account_code,
                       {ACCOUNT_NAME_SQL} AS account_name,
                       count(line.id) AS move_line_count,
                       sum(line.debit) AS debit,
                       sum(line.credit) AS credit,
                       sum(line.balance) AS balance,
                       sum(line.tax_base_amount) AS tax_base_amount
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                 WHERE line.company_id = %s
                   AND move.date BETWEEN %s AND %s
                   AND {ACCOUNT_CODE_SQL} LIKE '445%%'
                   {self._ledger_scope_sql()}
                   {self._state_sql()}
                   {filter_sql}
                 GROUP BY account.rebuild_source_id,
                          {ACCOUNT_CODE_SQL},
                          {ACCOUNT_NAME_SQL}
            ),
            combined AS (
                SELECT * FROM tax_grid_lines
                UNION ALL
                SELECT * FROM vat_account_lines
            )
            SELECT %s AS grouping,
                   report_section,
                   COALESCE(source_tax_tag_id::text, '') AS source_tax_tag_id,
                   COALESCE(tax_tag_name, '') AS tax_tag_name,
                   source_account_id::text AS source_account_id,
                   account_code,
                   account_name,
                   sum(move_line_count)::text AS move_line_count,
                   round(sum(debit)::numeric, 2)::text AS debit,
                   round(sum(credit)::numeric, 2)::text AS credit,
                   round(sum(balance)::numeric, 2)::text AS balance,
                   round(sum(tax_base_amount)::numeric, 2)::text AS tax_base_amount
              FROM combined
             GROUP BY report_section,
                      source_tax_tag_id,
                      tax_tag_name,
                      source_account_id,
                      account_code,
                      account_name
             ORDER BY {order_sql}
            """,
            [
                self.company_id.id,
                self.date_from,
                self.date_to,
                *filter_params,
                self.company_id.id,
                self.date_from,
                self.date_to,
                *filter_params,
                grouping_label,
            ],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _eu_tax_report_rows(self):
        period_keys = []
        if self.company_id.rebuild_source_id == 8:
            period_keys.append("USL Media full posted replay")
        else:
            if fields.Date.to_date(self.date_from) <= fields.Date.to_date("2025-09-30"):
                period_keys.append("USL benchmark 2024-01-10 to 2025-09-30")
            if fields.Date.to_date(self.date_to) >= fields.Date.to_date("2025-10-01"):
                period_keys.append("USL current from 2025-10-01")
        if not period_keys:
            period_keys = ["Other imported posted replay"]
        clauses = [
            "company_id = %s",
            "report_type = %s",
            "period_key IN %s",
        ]
        params = [self.company_id.id, self.report_type, tuple(period_keys)]
        if self.account_ids:
            clauses.append("account_id IN %s")
            params.append(tuple(self.account_ids.ids))
        if self.journal_ids:
            clauses.append("journal_id IN %s")
            params.append(tuple(self.journal_ids.ids))
        if self.partner_ids:
            clauses.append("partner_id IN %s")
            params.append(tuple(self.partner_ids.ids))
        self.env.cr.execute(
            f"""
            SELECT report_type,
                   report_name,
                   period_key,
                   country_code,
                   country_name,
                   partner_name,
                   vat_number,
                   tax_name,
                   tax_tag_name,
                   journal_code,
                   account_code,
                   account_name,
                   move_count::text AS move_count,
                   move_line_count::text AS move_line_count,
                   round(taxable_amount::numeric, 2)::text AS taxable_amount,
                   round(tax_amount::numeric, 2)::text AS tax_amount,
                   round(balance::numeric, 2)::text AS balance,
                   review_status
              FROM rebuild_account_eu_tax_report_line
             WHERE {" AND ".join(clauses)}
             ORDER BY period_key, country_code, partner_name, tax_name, tax_tag_name, journal_code, account_code
            """,
            params,
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _bank_reconciliation_rows(self):
        filter_sql, filter_params = self._bank_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT move.date::text AS date,
                   journal.code AS journal_code,
                   move.name AS move_name,
                   bsl.rebuild_source_id::text AS source_statement_line_id,
                   COALESCE(bsl.payment_ref::text, '') AS payment_ref,
                   COALESCE(partner.name::text, bsl.partner_name::text, '') AS partner_name,
                   COALESCE(bsl.transaction_type::text, '') AS transaction_type,
                   COALESCE(bsl.account_number::text, '') AS account_number,
                   COALESCE(bsl.internal_index::text, '') AS internal_index,
                   round(bsl.amount::numeric, 2)::text AS amount,
                   COALESCE(currency.name::text, '') AS currency,
                   COALESCE(foreign_currency.name::text, '') AS foreign_currency,
                   round(bsl.amount_currency::numeric, 2)::text AS amount_currency,
                   round(bsl.amount_residual::numeric, 2)::text AS amount_residual,
                   bsl.is_reconciled::text AS is_reconciled,
                   CASE
                       WHEN bsl.is_reconciled THEN 'Reconciled'
                       WHEN abs(bsl.amount_residual) > 0.004 THEN 'Open residual'
                       ELSE 'Not reconciled'
                   END AS reconciliation_status,
                   count(line.id)::text AS move_line_count
              FROM account_bank_statement_line bsl
              JOIN account_move move ON move.id = bsl.move_id
              JOIN account_journal journal ON journal.id = bsl.journal_id
              LEFT JOIN res_partner partner ON partner.id = bsl.partner_id
              LEFT JOIN res_currency currency ON currency.id = bsl.currency_id
              LEFT JOIN res_currency foreign_currency ON foreign_currency.id = bsl.foreign_currency_id
              LEFT JOIN account_move_line line ON line.move_id = move.id
             WHERE bsl.company_id = %s
               AND move.date BETWEEN %s AND %s
               {self._bank_scope_sql()}
               {self._state_sql()}
               {filter_sql}
             GROUP BY bsl.id,
                      move.date,
                      journal.code,
                      move.name,
                      bsl.rebuild_source_id,
                      bsl.payment_ref,
                      COALESCE(partner.name::text, bsl.partner_name::text, ''),
                      bsl.transaction_type,
                      bsl.account_number,
                      bsl.internal_index,
                      bsl.amount,
                      currency.name,
                      foreign_currency.name,
                      bsl.amount_currency,
                      bsl.amount_residual,
                      bsl.is_reconciled
             ORDER BY journal.code, move.date, bsl.rebuild_source_id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _currency_report_rows(self):
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            WITH base_lines AS (
                SELECT 'Foreign currency ledger' AS report_section,
                       currency.name::text AS currency,
                       {ACCOUNT_CODE_SQL} AS account_code,
                       {ACCOUNT_NAME_SQL} AS account_name,
                       account.account_type,
                       COALESCE(partner.name::text, '') AS partner_name,
                       line.id,
                       line.debit,
                       line.credit,
                       line.balance,
                       line.amount_currency,
                       line.amount_residual,
                       line.amount_residual_currency
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                  JOIN res_currency currency ON currency.id = line.currency_id
                  LEFT JOIN res_partner partner ON partner.id = line.partner_id
                 WHERE line.company_id = %s
                   AND move.date BETWEEN %s AND %s
                   AND line.currency_id IS NOT NULL
                   AND line.currency_id != company.currency_id
                   {self._ledger_scope_sql()}
                   {self._state_sql()}
                   {filter_sql}
                UNION ALL
                SELECT 'Realized exchange gains and losses',
                       COALESCE(currency.name::text, '') AS currency,
                       {ACCOUNT_CODE_SQL},
                       {ACCOUNT_NAME_SQL},
                       account.account_type,
                       COALESCE(partner.name::text, ''),
                       line.id,
                       line.debit,
                       line.credit,
                       line.balance,
                       line.amount_currency,
                       line.amount_residual,
                       line.amount_residual_currency
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                  LEFT JOIN res_currency currency ON currency.id = line.currency_id
                  LEFT JOIN res_partner partner ON partner.id = line.partner_id
                 WHERE line.company_id = %s
                   AND move.date BETWEEN %s AND %s
                   AND ({ACCOUNT_CODE_SQL} LIKE '666%%' OR {ACCOUNT_CODE_SQL} LIKE '766%%')
                   {self._ledger_scope_sql()}
                   {self._state_sql()}
                   {filter_sql}
                UNION ALL
                SELECT 'Unrealized foreign-currency open items',
                       currency.name::text AS currency,
                       {ACCOUNT_CODE_SQL},
                       {ACCOUNT_NAME_SQL},
                       account.account_type,
                       COALESCE(partner.name::text, ''),
                       line.id,
                       line.debit,
                       line.credit,
                       line.balance,
                       line.amount_currency,
                       line.amount_residual,
                       line.amount_residual_currency
                  FROM account_move_line line
                  JOIN account_move move ON move.id = line.move_id
                  JOIN res_company company ON company.id = line.company_id
                  JOIN account_account account ON account.id = line.account_id
                  JOIN res_currency currency ON currency.id = line.currency_id
                  LEFT JOIN res_partner partner ON partner.id = line.partner_id
                 WHERE line.company_id = %s
                   AND move.date BETWEEN %s AND %s
                   AND line.currency_id IS NOT NULL
                   AND line.currency_id != company.currency_id
                   AND account.account_type IN ('asset_receivable', 'liability_payable')
                   AND (line.reconciled IS NOT TRUE OR abs(line.amount_residual) > 0.004 OR abs(line.amount_residual_currency) > 0.004)
                   {self._ledger_scope_sql()}
                   {self._state_sql()}
                   {filter_sql}
            )
            SELECT report_section,
                   currency,
                   account_code,
                   account_name,
                   account_type,
                   partner_name,
                   count(id)::text AS move_line_count,
                   round(sum(debit)::numeric, 2)::text AS debit,
                   round(sum(credit)::numeric, 2)::text AS credit,
                   round(sum(balance)::numeric, 2)::text AS balance,
                   round(sum(amount_currency)::numeric, 2)::text AS amount_currency,
                   round(sum(amount_residual)::numeric, 2)::text AS amount_residual,
                   round(sum(amount_residual_currency)::numeric, 2)::text AS amount_residual_currency
              FROM base_lines
             GROUP BY report_section, currency, account_code, account_name, account_type, partner_name
             ORDER BY report_section, currency, account_code, partner_name
            """,
            [
                self.company_id.id,
                self.date_from,
                self.date_to,
                *filter_params,
                self.company_id.id,
                self.date_from,
                self.date_to,
                *filter_params,
                self.company_id.id,
                self.date_from,
                self.date_to,
                *filter_params,
            ],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _management_summary_rows(self, report_key):
        filter_sql, filter_params = self._line_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT count(line.id)::integer AS all_line_count,
                   greatest(COALESCE(max(move.date) - min(move.date) + 1, 0), 1)::numeric AS day_count,
                   count(line.id) FILTER (
                       WHERE account.account_type IN ('asset_cash', 'liability_credit_card')
                   )::integer AS cash_line_count,
                   round(COALESCE(sum(line.debit) FILTER (
                       WHERE account.account_type IN ('asset_cash', 'liability_credit_card')
                   ), 0)::numeric, 2) AS cash_received,
                   round(COALESCE(sum(line.credit) FILTER (
                       WHERE account.account_type IN ('asset_cash', 'liability_credit_card')
                   ), 0)::numeric, 2) AS cash_spent,
                   round(COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type IN ('asset_cash', 'liability_credit_card')
                   ), 0)::numeric, 2) AS closing_cash,
                   count(line.id) FILTER (
                       WHERE account.account_type IN ('income', 'income_other')
                   )::integer AS revenue_line_count,
                   round(-COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type IN ('income', 'income_other')
                   ), 0)::numeric, 2) AS revenue,
                   count(line.id) FILTER (
                       WHERE account.account_type = 'expense_direct_cost'
                   )::integer AS cost_line_count,
                   round(COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type = 'expense_direct_cost'
                   ), 0)::numeric, 2) AS cost_of_revenue,
                   count(line.id) FILTER (
                       WHERE account.account_type IN ('expense', 'expense_depreciation')
                   )::integer AS expense_line_count,
                   round(COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type IN ('expense', 'expense_depreciation')
                   ), 0)::numeric, 2) AS expenses,
                   count(line.id) FILTER (
                       WHERE account.account_type IN ('income', 'income_other', 'expense', 'expense_direct_cost', 'expense_depreciation')
                   )::integer AS profit_loss_line_count,
                   round(-COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type IN ('income', 'income_other', 'expense', 'expense_direct_cost', 'expense_depreciation')
                   ), 0)::numeric, 2) AS net_profit,
                   count(line.id) FILTER (
                       WHERE account.account_type = 'asset_receivable'
                   )::integer AS receivable_line_count,
                   round(COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type = 'asset_receivable'
                   ), 0)::numeric, 2) AS receivables,
                   count(line.id) FILTER (
                       WHERE account.account_type = 'liability_payable'
                   )::integer AS payable_line_count,
                   round(-COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type = 'liability_payable'
                   ), 0)::numeric, 2) AS payables,
                   count(line.id) FILTER (
                       WHERE account.account_type LIKE 'asset%%'
                          OR account.account_type LIKE 'liability%%'
                   )::integer AS net_asset_line_count,
                   round((
                       COALESCE(sum(line.balance) FILTER (WHERE account.account_type LIKE 'asset%%'), 0)
                       + COALESCE(sum(line.balance) FILTER (WHERE account.account_type LIKE 'liability%%'), 0)
                   )::numeric, 2) AS net_assets,
                   count(line.id) FILTER (
                       WHERE account.account_type IN ('asset_current', 'asset_receivable', 'asset_cash')
                          OR account.account_type IN ('liability_current', 'liability_payable', 'liability_credit_card')
                   )::integer AS current_line_count,
                   round(COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type IN ('asset_current', 'asset_receivable', 'asset_cash')
                   ), 0)::numeric, 2) AS current_assets,
                   round(-COALESCE(sum(line.balance) FILTER (
                       WHERE account.account_type IN ('liability_current', 'liability_payable', 'liability_credit_card')
                   ), 0)::numeric, 2) AS current_liabilities
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
              JOIN account_account account ON account.id = line.account_id
             WHERE line.company_id = %s
               AND move.date BETWEEN %s AND %s
               {self._ledger_scope_sql()}
               {self._state_sql()}
               {filter_sql}
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        data = dict(self.env.cr.dictfetchone() or {})

        def decimal_value(key):
            return _amount(data.get(key))

        def count_value(key):
            return int(data.get(key) or 0)

        def metric_text(value):
            return f"{Decimal(str(value or '0')).quantize(Decimal('0.0001')):.4f}"

        def safe_ratio(numerator, denominator, multiplier=Decimal("1")):
            numerator = Decimal(str(numerator or "0"))
            denominator = Decimal(str(denominator or "0"))
            if not denominator:
                return Decimal("0")
            return numerator / denominator * multiplier

        day_count = Decimal(str(data.get("day_count") or "1"))
        cash_received = decimal_value("cash_received")
        cash_spent = decimal_value("cash_spent")
        closing_cash = decimal_value("closing_cash")
        revenue = decimal_value("revenue")
        cost_of_revenue = decimal_value("cost_of_revenue")
        expenses = decimal_value("expenses")
        net_profit = decimal_value("net_profit")
        receivables = decimal_value("receivables")
        payables = decimal_value("payables")
        net_assets = decimal_value("net_assets")
        current_assets = decimal_value("current_assets")
        current_liabilities = decimal_value("current_liabilities")

        rows = []

        def add(line_code, line_name, metric_type, source_formula, move_line_count, amount, metric_value=None):
            rows.append({
                "report_key": report_key,
                "report_name": "Cash Flow Statement" if report_key == "cash_flow" else "Executive Summary",
                "line_code": line_code,
                "line_name": line_name,
                "metric_type": metric_type,
                "source_formula": source_formula,
                "move_line_count": str(move_line_count),
                "amount": _amount_text(amount),
                "metric_value": metric_text(metric_value if metric_value is not None else amount),
            })

        if report_key == "cash_flow":
            add("CASH_RECEIVED", "Cash received", "currency", "Debit movements on cash and credit-card accounts", count_value("cash_line_count"), cash_received)
            add("CASH_SPENT", "Cash spent", "currency", "Credit movements on cash and credit-card accounts", count_value("cash_line_count"), cash_spent)
            add("CASH_SURPLUS", "Cash surplus", "currency", "Cash received minus cash spent", count_value("cash_line_count"), cash_received - cash_spent)
            add("CLOSING_CASH", "Closing bank balance", "currency", "Closing balance of cash and credit-card accounts", count_value("cash_line_count"), closing_cash)
            return rows

        gross_profit = revenue - cost_of_revenue
        add("REVENUE", "Total income", "currency", "Income and other income account balances with management sign", count_value("revenue_line_count"), revenue)
        add("COST_OF_REVENUE", "Cost of revenue", "currency", "Direct-cost expense account balances", count_value("cost_line_count"), cost_of_revenue)
        add("GROSS_PROFIT", "Gross profit", "currency", "Revenue minus cost of revenue", count_value("profit_loss_line_count"), gross_profit)
        add("EXPENSES", "Expenses", "currency", "Operating, depreciation and other expense account balances excluding direct costs", count_value("expense_line_count"), expenses)
        add("NET_PROFIT", "Net profit", "currency", "Net balance of income and expense accounts with management sign", count_value("profit_loss_line_count"), net_profit)
        add("RECEIVABLES", "Receivables", "currency", "Receivable account balances", count_value("receivable_line_count"), receivables)
        add("PAYABLES", "Payables", "currency", "Payable account balances with liability sign", count_value("payable_line_count"), payables)
        add("NET_ASSETS", "Net assets", "currency", "Asset balances minus liability balances", count_value("net_asset_line_count"), net_assets)
        add("GROSS_PROFIT_MARGIN", "Gross profit margin", "percent", "(Gross profit / revenue) * 100", count_value("profit_loss_line_count"), 0, safe_ratio(gross_profit, revenue, Decimal("100")))
        add("NET_PROFIT_MARGIN", "Net profit margin", "percent", "(Net profit / revenue) * 100", count_value("profit_loss_line_count"), 0, safe_ratio(net_profit, revenue, Decimal("100")))
        add("RETURN_ON_INVESTMENT", "Return on investments", "percent", "(Net profit / current assets) * 100", count_value("current_line_count"), 0, safe_ratio(net_profit, current_assets, Decimal("100")))
        add("AVERAGE_DEBTORS_DAYS", "Average debtors days", "days", "(Receivables / revenue) * days in selected period", count_value("receivable_line_count"), 0, safe_ratio(receivables, revenue, day_count))
        add("AVERAGE_CREDITORS_DAYS", "Average creditors days", "days", "(Payables / (cost of revenue + expenses)) * days in selected period", count_value("payable_line_count"), 0, safe_ratio(payables, cost_of_revenue + expenses, day_count))
        add("SHORT_TERM_CASH_FORECAST", "Short term cash forecast", "currency", "Receivables less payables", count_value("current_line_count"), receivables - payables)
        add("CURRENT_ASSETS_TO_LIABILITIES", "Current assets to liabilities", "ratio", "Current assets / current liabilities", count_value("current_line_count"), 0, safe_ratio(current_assets, current_liabilities))
        return rows

    def _analytic_report_rows(self):
        filter_sql, filter_params = self._analytic_filter_sql()
        self.env.cr.execute(
            f"""
            WITH analytic_lines AS (
                SELECT COALESCE(
                           analytic_account.rebuild_source_id::text,
                           analytic.rebuild_source_analytic_account_id::text,
                           analytic_account.id::text,
                           ''
                       ) AS analytic_key,
                       COALESCE(analytic_account.code::text, '') AS analytic_code,
                       COALESCE(analytic_account.name->>'fr_FR', analytic_account.name->>'en_US', analytic_account.name::text, analytic.name::text) AS analytic_name,
                       {ACCOUNT_CODE_SQL} AS account_code,
                       {ACCOUNT_NAME_SQL} AS account_name,
                       analytic.id,
                       analytic.amount
                  FROM account_analytic_line analytic
                  JOIN res_company company ON company.id = analytic.company_id
                  LEFT JOIN account_analytic_account analytic_account
                    ON analytic_account.id = COALESCE(
                        analytic.account_id,
                        analytic.rebuild_analytic_account_id
                    )
                  LEFT JOIN account_account account ON account.id = analytic.general_account_id
                  LEFT JOIN account_move_line line ON line.id = analytic.move_line_id
                  LEFT JOIN account_move move ON move.id = line.move_id
                 WHERE analytic.company_id = %s
                   AND analytic.date BETWEEN %s AND %s
                   {self._analytic_scope_sql()}
                   {self._analytic_state_sql()}
                   {filter_sql}
            )
            SELECT analytic_key,
                   analytic_code,
                   analytic_name,
                   account_code,
                   account_name,
                   count(id)::text AS move_line_count,
                   '100.0000' AS percentage,
                   round(sum(CASE WHEN amount > 0 THEN amount ELSE 0 END)::numeric, 2)::text AS allocated_debit,
                   round(sum(CASE WHEN amount < 0 THEN -amount ELSE 0 END)::numeric, 2)::text AS allocated_credit,
                   round(sum(amount)::numeric, 2)::text AS allocated_balance
              FROM analytic_lines
             GROUP BY analytic_key, analytic_code, analytic_name, account_code, account_name
             ORDER BY analytic_name, account_code
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _fixed_asset_rows(self):
        filter_sql, filter_params = self._asset_account_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT asset.rebuild_source_id::text AS source_asset_id,
                   asset.name,
                   COALESCE(asset.acquisition_date::text, '') AS acquisition_date,
                   asset.state,
                   asset.asset_group_name,
                   round(asset.original_value::numeric, 2)::text AS original_value,
                   round(asset.already_depreciated_amount_import::numeric, 2)::text AS accumulated_depreciation,
                   round(asset.imported_period_net_value::numeric, 2)::text AS imported_period_net_value,
                   round(asset.book_value::numeric, 2)::text AS source_book_value,
                   COALESCE(asset_account.code_store->>company.rebuild_source_id::text, asset_account.code_store->>'1', asset_account.code_store::text, '') AS asset_account,
                   COALESCE(depreciation_account.code_store->>company.rebuild_source_id::text, depreciation_account.code_store->>'1', depreciation_account.code_store::text, '') AS depreciation_account,
                   COALESCE(expense_account.code_store->>company.rebuild_source_id::text, expense_account.code_store->>'1', expense_account.code_store::text, '') AS depreciation_expense_account
              FROM rebuild_account_asset asset
              JOIN res_company company ON company.id = asset.company_id
              LEFT JOIN account_account asset_account ON asset_account.id = asset.asset_account_id
              LEFT JOIN account_account depreciation_account ON depreciation_account.id = asset.depreciation_account_id
              LEFT JOIN account_account expense_account ON expense_account.id = asset.depreciation_expense_account_id
             WHERE asset.company_id = %s
               {filter_sql}
             ORDER BY asset.rebuild_source_id
            """,
            [self.company_id.id, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _fixed_asset_group_account_rows(self):
        filter_sql, filter_params = self._asset_account_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT asset_account.rebuild_source_id::text AS source_account_id,
                   COALESCE(asset_account.code_store->>company.rebuild_source_id::text, asset_account.code_store->>'1', asset_account.code_store::text, '') AS account_code,
                   COALESCE(asset_account.name->>'fr_FR', asset_account.name->>'en_US', asset_account.name::text, '') AS account_name,
                   count(asset.id)::text AS asset_count,
                   string_agg(asset.name, '; ' ORDER BY asset.rebuild_source_id) AS asset_names,
                   round(sum(asset.original_value)::numeric, 2)::text AS original_value,
                   round(sum(asset.already_depreciated_amount_import)::numeric, 2)::text AS accumulated_depreciation,
                   round(sum(asset.imported_period_net_value)::numeric, 2)::text AS imported_period_net_value,
                   round(sum(asset.book_value)::numeric, 2)::text AS source_book_value
              FROM rebuild_account_asset asset
              JOIN res_company company ON company.id = asset.company_id
              LEFT JOIN account_account asset_account ON asset_account.id = asset.asset_account_id
             WHERE asset.company_id = %s
               {filter_sql}
             GROUP BY asset_account.id,
                      asset_account.rebuild_source_id,
                      COALESCE(asset_account.code_store->>company.rebuild_source_id::text, asset_account.code_store->>'1', asset_account.code_store::text, ''),
                      COALESCE(asset_account.name->>'fr_FR', asset_account.name->>'en_US', asset_account.name::text, '')
             ORDER BY account_code
            """,
            [self.company_id.id, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _depreciation_schedule_rows(self):
        filter_sql, filter_params = self._asset_account_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT schedule.source_asset_id::text AS source_asset_id,
                   asset.name AS asset_name,
                   schedule.depreciation_date::text AS depreciation_date,
                   schedule.source_move_id::text AS source_move_id,
                   COALESCE(schedule.source_move_name::text, '') AS source_move_name,
                   COALESCE(schedule.source_move_state::text, '') AS source_move_state,
                   schedule.representation_status,
                   COALESCE(schedule.move_ref::text, '') AS move_ref,
                   round(schedule.expense_amount::numeric, 2)::text AS expense_amount,
                   round(schedule.depreciation_amount::numeric, 2)::text AS depreciation_amount,
                   round(schedule.accumulated_depreciation_amount::numeric, 2)::text AS accumulated_depreciation_amount,
                   round(schedule.net_book_value_after_line::numeric, 2)::text AS net_book_value_after_line,
                   COALESCE(imported_move.name::text, '') AS imported_move_name,
                   COALESCE(imported_move.rebuild_source_id::text, '') AS imported_source_move_id
              FROM rebuild_account_asset_depreciation_schedule_line schedule
              JOIN rebuild_account_asset asset ON asset.id = schedule.asset_id
              LEFT JOIN account_move imported_move ON imported_move.id = schedule.imported_move_id
             WHERE schedule.company_id = %s
               AND schedule.depreciation_date BETWEEN %s AND %s
               {filter_sql}
             ORDER BY asset.rebuild_source_id, schedule.depreciation_date, schedule.source_move_id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _deferred_schedule_rows(self):
        filter_sql, filter_params = self._deferred_schedule_filter_sql()
        self.env.cr.execute(
            f"""
            SELECT schedule.source_original_move_id::text AS source_original_move_id,
                   schedule.source_deferred_move_id::text AS source_deferred_move_id,
                   COALESCE(schedule.source_original_name::text, '') AS source_original_name,
                   COALESCE(schedule.source_deferred_name::text, '') AS source_deferred_name,
                   schedule.source_original_state,
                   schedule.source_deferred_state,
                   schedule.source_original_move_type,
                   schedule.source_deferred_move_type,
                   schedule.original_date::text AS original_date,
                   schedule.deferred_date::text AS deferred_date,
                   COALESCE(schedule.deferred_start_date::text, '') AS deferred_start_date,
                   COALESCE(schedule.deferred_end_date::text, '') AS deferred_end_date,
                   schedule.schedule_type,
                   schedule.schedule_phase,
                   schedule.representation_status,
                   schedule.review_status,
                   COALESCE(schedule.deferred_account_code::text, '') AS deferred_account_code,
                   COALESCE(schedule.deferred_account_name::text, '') AS deferred_account_name,
                   COALESCE(schedule.counterpart_account_codes::text, '') AS counterpart_account_codes,
                   COALESCE(schedule.counterpart_account_names::text, '') AS counterpart_account_names,
                   round(schedule.amount::numeric, 2)::text AS amount,
                   round(schedule.deferred_account_balance::numeric, 2)::text AS deferred_account_balance,
                   round(schedule.counterpart_balance::numeric, 2)::text AS counterpart_balance,
                   COALESCE(original_move.name::text, '') AS imported_original_move_name,
                   COALESCE(deferred_move.name::text, '') AS imported_deferred_move_name
              FROM rebuild_account_deferred_schedule_line schedule
              LEFT JOIN account_move original_move ON original_move.id = schedule.original_move_id
              LEFT JOIN account_move deferred_move ON deferred_move.id = schedule.deferred_move_id
             WHERE schedule.company_id = %s
               AND schedule.deferred_date BETWEEN %s AND %s
               {filter_sql}
             ORDER BY schedule.deferred_date, schedule.source_original_move_id, schedule.source_deferred_move_id
            """,
            [self.company_id.id, self.date_from, self.date_to, *filter_params],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]

    def _french_annual_rows(self, statement_keys=None, report_variant=""):
        tb = self._trial_balance_rows()

        def sum_bal(prefixes, account_types=None, positive=None, negative=None):
            total = Decimal("0.00")
            count = 0
            for row in tb:
                balance = _amount(row["balance"])
                if not _matches(row, prefixes):
                    continue
                if account_types and row["account_type"] not in account_types:
                    continue
                if positive and balance <= 0:
                    continue
                if negative and balance >= 0:
                    continue
                total += balance
                count += int(row.get("move_line_count") or 0)
            return total, count

        def row(statement_key, line_code, line_name, amount, formula, prefixes, count=0, gross=0, depreciation=0):
            return {
                "statement_key": statement_key,
                "line_code": line_code,
                "line_name": line_name,
                "source_formula": formula,
                "drilldown_account_prefixes": ",".join(prefixes),
                "move_line_count": str(count),
                "gross_amount": _amount_text(gross),
                "depreciation_amount": _amount_text(depreciation),
                "net_amount": _amount_text(amount),
                "amount": _amount_text(amount),
            }

        fixed_gross, fixed_gross_count = sum_bal(["21"])
        fixed_depr_balance, fixed_depr_count = sum_bal(["28"])
        other_receivables, other_receivable_count = sum_bal(["4"], ["asset_current", "asset_receivable"], positive=True)
        cash, cash_count = sum_bal(["5"], ["asset_cash"])
        fixed_net = fixed_gross + fixed_depr_balance
        total_assets = fixed_net + other_receivables + cash
        depreciation = -fixed_depr_balance

        capital, capital_count = sum_bal(["101"])
        result_balance, result_count = sum_bal(["6", "7"])
        shareholder_debt, shareholder_count = sum_bal(["455"], negative=True)
        tax_social_debt, tax_social_count = sum_bal(["42", "43", "44"], negative=True)
        current_result = -result_balance
        equity = -capital + current_result
        total_debt = -shareholder_debt - tax_social_debt
        total_passif = equity + total_debt

        goods_sales, goods_sales_count = sum_bal(["701"])
        service_sales, service_sales_count = sum_bal(["706"])
        turnover = -sum_bal(["70"])[0]
        operating_income = -sum_bal(["70", "758"])[0]
        goods_purchases, goods_purchases_count = sum_bal(["607"])
        external_charges, external_charges_count = sum_bal(["606", "61", "62"])
        taxes, taxes_count = sum_bal(["631", "633"])
        salaries, salaries_count = sum_bal(["641"])
        social_charges, social_charges_count = sum_bal(["645"])
        depreciation_expense, depreciation_expense_count = sum_bal(["681"])
        other_expenses, other_expenses_count = sum_bal(["658"])
        operating_expenses = sum_bal(["60", "61", "62", "63", "64", "658", "681"])[0]
        operating_result = operating_income - operating_expenses
        financial_income, financial_income_count = sum_bal(["76"])
        financial_charges, financial_charges_count = sum_bal(["66"])
        financial_result = -financial_income - financial_charges
        current_result_before_tax = operating_result + financial_result
        income_tax, income_tax_count = sum_bal(["695"])
        net_result = current_result_before_tax - income_tax

        value_added = operating_income - goods_purchases - external_charges - other_expenses
        ebe = value_added - taxes - salaries - social_charges
        caf = net_result + depreciation_expense

        rows = [
            row("bilan_actif", "ACTIF_IMMO_CORP", "Immobilisations corporelles", fixed_net, "21 - 28", ["21", "28"], fixed_gross_count + fixed_depr_count, fixed_gross, depreciation),
            row("bilan_actif", "ACTIF_AUTRES_CREANCES", "Autres créances", other_receivables, "Débiteurs de classe 4", ["4"], other_receivable_count),
            row("bilan_actif", "ACTIF_DISPONIBILITES", "Disponibilités", cash, "Trésorerie 5", ["5"], cash_count),
            row("bilan_actif", "ACTIF_TOTAL", "Total actif", total_assets, "Immobilisations nettes + créances + disponibilités", ["21", "28", "4", "5"], fixed_gross_count + fixed_depr_count + other_receivable_count + cash_count, fixed_gross + other_receivables + cash, depreciation),
            row("bilan_passif", "PASSIF_CAPITAL", "Capital social", -capital, "101", ["101"], capital_count),
            row("bilan_passif", "PASSIF_RESULTAT", "Résultat de l’exercice", current_result, "6 et 7", ["6", "7"], result_count),
            row("bilan_passif", "PASSIF_CAPITAUX_PROPRES", "Capitaux propres", equity, "101 + résultat", ["101", "6", "7"], capital_count + result_count),
            row("bilan_passif", "PASSIF_COMPTE_COURANT_ASSOCIE", "Compte courant d’associé", -shareholder_debt, "455 créditeurs", ["455"], shareholder_count),
            row("bilan_passif", "PASSIF_DETTES_FISCALES_SOCIALES", "Dettes fiscales et sociales", -tax_social_debt, "42/43/44 créditeurs", ["42", "43", "44"], tax_social_count),
            row("bilan_passif", "PASSIF_TOTAL_DETTES", "Total dettes", total_debt, "455 + 42/43/44", ["455", "42", "43", "44"], shareholder_count + tax_social_count),
            row("bilan_passif", "PASSIF_TOTAL", "Total passif", total_passif, "Capitaux propres + dettes", ["101", "6", "7", "455", "42", "43", "44"], capital_count + result_count + shareholder_count + tax_social_count),
            row("compte_resultat", "CR_VENTES_PRODUITS", "Ventes de biens et produits", -goods_sales, "701", ["701"], goods_sales_count),
            row("compte_resultat", "CR_SERVICES", "Prestations de services", -service_sales, "706", ["706"], service_sales_count),
            row("compte_resultat", "CR_CHIFFRE_AFFAIRES", "Chiffre d’affaires net", turnover, "70", ["70"], goods_sales_count + service_sales_count),
            row("compte_resultat", "CR_TOTAL_PRODUITS_EXPLOITATION", "Total produits d’exploitation", operating_income, "70 + 758", ["70", "758"]),
            row("compte_resultat", "CR_ACHATS_MARCHANDISES", "Achats de marchandises", goods_purchases, "607", ["607"], goods_purchases_count),
            row("compte_resultat", "CR_CHARGES_EXTERNES", "Autres achats et charges externes", external_charges, "606 + 61 + 62", ["606", "61", "62"], external_charges_count),
            row("compte_resultat", "CR_IMPOTS_TAXES", "Impôts, taxes et versements assimilés", taxes, "631 + 633", ["631", "633"], taxes_count),
            row("compte_resultat", "CR_SALAIRES", "Salaires et traitements", salaries, "641", ["641"], salaries_count),
            row("compte_resultat", "CR_CHARGES_SOCIALES", "Charges sociales", social_charges, "645", ["645"], social_charges_count),
            row("compte_resultat", "CR_DOTATIONS_AMORTISSEMENTS", "Dotations aux amortissements", depreciation_expense, "681", ["681"], depreciation_expense_count),
            row("compte_resultat", "CR_AUTRES_CHARGES_EXPLOITATION", "Autres charges d’exploitation", other_expenses, "658", ["658"], other_expenses_count),
            row("compte_resultat", "CR_TOTAL_CHARGES_EXPLOITATION", "Total charges d’exploitation", operating_expenses, "60/61/62/63/64/658/681", ["60", "61", "62", "63", "64", "658", "681"]),
            row("compte_resultat", "CR_RESULTAT_EXPLOITATION", "Résultat d’exploitation", operating_result, "Produits d’exploitation - charges d’exploitation", ["70", "758", "60", "61", "62", "63", "64", "658", "681"]),
            row("compte_resultat", "CR_PRODUITS_FINANCIERS", "Produits financiers", -financial_income, "76", ["76"], financial_income_count),
            row("compte_resultat", "CR_CHARGES_FINANCIERES", "Charges financières", financial_charges, "66", ["66"], financial_charges_count),
            row("compte_resultat", "CR_RESULTAT_FINANCIER", "Résultat financier", financial_result, "76 - 66", ["76", "66"], financial_income_count + financial_charges_count),
            row("compte_resultat", "CR_RESULTAT_COURANT_AVANT_IMPOT", "Résultat courant avant impôts", current_result_before_tax, "Résultat exploitation + résultat financier", ["70", "758", "60", "61", "62", "63", "64", "658", "681", "76", "66"]),
            row("compte_resultat", "CR_IMPOTS_BENEFICES", "Impôts sur les bénéfices", income_tax, "695", ["695"], income_tax_count),
            row("compte_resultat", "CR_RESULTAT_NET", "Résultat net comptable", net_result, "Solde 6 et 7", ["6", "7"], result_count),
            row("sig_caf", "SIG_VALEUR_AJOUTEE", "Valeur ajoutée", value_added, "Produits - achats - charges externes", ["70", "758", "607", "606", "61", "62", "658"]),
            row("sig_caf", "SIG_EBE", "Excédent brut d’exploitation", ebe, "VA - impôts - personnel", ["70", "758", "607", "606", "61", "62", "658", "631", "633", "641", "645"]),
            row("sig_caf", "SIG_RESULTAT_EXPLOITATION", "Résultat d’exploitation", operating_result, "EBE - dotations", ["70", "758", "60", "61", "62", "63", "64", "658", "681"]),
            row("sig_caf", "SIG_RESULTAT_COURANT_AVANT_IMPOT", "Résultat courant avant impôts", current_result_before_tax, "Résultat exploitation + financier", ["70", "758", "60", "61", "62", "63", "64", "658", "681", "76", "66"]),
            row("sig_caf", "SIG_RESULTAT_NET", "Résultat net comptable", net_result, "Solde 6 et 7", ["6", "7"], result_count),
            row("sig_caf", "SIG_CAPACITE_AUTOFINANCEMENT", "Capacité d’autofinancement", caf, "Résultat net + dotations", ["6", "7", "681"], result_count + depreciation_expense_count),
        ]
        if statement_keys:
            rows = [item for item in rows if item["statement_key"] in statement_keys]
        if report_variant:
            for item in rows:
                item["report_variant"] = report_variant
                item["applicability_basis"] = self._report_variant_basis()
        return rows

    def _french_tax_package_rows(self):
        period_key = "USL benchmark 2024-01-10 to 2025-09-30"
        if fields.Date.to_string(self.date_from) != "2024-01-10" or fields.Date.to_string(self.date_to) != "2025-09-30":
            return []
        self.env.cr.execute(
            """
            SELECT form_code,
                   form_name,
                   field_code,
                   field_label,
                   source_kind,
                   source_formula,
                   COALESCE(source_report_line_code, '') AS source_report_line_code,
                   COALESCE(drilldown_account_prefixes, '') AS drilldown_account_prefixes,
                   move_line_count::text AS move_line_count,
                   quantity::text AS quantity,
                   round(amount::numeric, 2)::text AS amount,
                   round(rounded_amount::numeric, 2)::text AS rounded_amount,
                   COALESCE(round(benchmark_amount::numeric, 2)::text, '') AS benchmark_amount,
                   COALESCE(round(ledger_amount::numeric, 2)::text, '') AS ledger_amount,
                   COALESCE(round(difference_amount::numeric, 2)::text, '') AS difference_amount,
                   COALESCE(difference_classification, '') AS difference_classification,
                   COALESCE(value_text, '') AS value_text,
                   review_status
              FROM rebuild_account_french_tax_package_line
             WHERE company_id = %s
               AND period_key = %s
             ORDER BY form_code, line_sequence, field_code
            """,
            [self.company_id.id, period_key],
        )
        return [dict(row) for row in self.env.cr.dictfetchall()]


class RebuildAccountReportPreviewLine(models.TransientModel):
    _name = "rebuild.account.report.preview.line"
    _description = "USL Dynamic Accounting Report Preview Line"
    _order = "sequence, id"

    wizard_id = fields.Many2one(
        "rebuild.account.report.export.wizard",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    date = fields.Date(readonly=True)
    section = fields.Char(readonly=True)
    line_code = fields.Char(readonly=True)
    label = fields.Char(readonly=True)
    account_code = fields.Char(readonly=True)
    account_name = fields.Char(readonly=True)
    partner_name = fields.Char(readonly=True)
    move_name = fields.Char(readonly=True)
    opening_balance = fields.Monetary(readonly=True)
    debit = fields.Monetary(readonly=True)
    credit = fields.Monetary(readonly=True)
    movement = fields.Monetary(readonly=True)
    closing_balance = fields.Monetary(readonly=True)
    balance = fields.Monetary(readonly=True)
    residual = fields.Monetary(readonly=True)
    comparison_value = fields.Monetary(readonly=True)
    difference = fields.Monetary(readonly=True)
    record_count = fields.Integer(readonly=True)
    is_group = fields.Boolean(readonly=True)
    level = fields.Integer(readonly=True)
    group_key = fields.Char(readonly=True)
    parent_group_key = fields.Char(readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    row_json = fields.Text(readonly=True)

    def action_open_sources(self):
        self.ensure_one()
        if not self.wizard_id:
            message = (
                "Preview source drill-down requires the report wizard context."
            )
            raise UserError(message)
        return self.wizard_id._preview_source_action(self)

    def action_toggle_group(self):
        self.ensure_one()
        if not self.is_group:
            return self.wizard_id.action_preview_report()
        return self.wizard_id._toggle_preview_group(self.group_key)

    def _row_payload(self):
        self.ensure_one()
        if not self.row_json:
            return {}
        try:
            payload = json.loads(self.row_json)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

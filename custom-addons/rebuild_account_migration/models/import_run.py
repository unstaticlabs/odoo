import hashlib
import json
import os
from collections import defaultdict

import psycopg2
import psycopg2.extras

from odoo import Command, fields, models


class RebuildAccountImportRun(models.Model):
    _name = "rebuild.account.import.run"
    _description = "USL Accounting Import Run"
    _order = "started_at desc, id desc"

    name = fields.Char(required=True, default="Accounting import")
    mode = fields.Selection(
        [
            ("exact_ledger_replay", "Exact Ledger Replay"),
            ("document_regeneration", "Document Regeneration"),
            ("controls_only", "Controls Only"),
        ],
        required=True,
        default="exact_ledger_replay",
    )
    status = fields.Selection(
        [
            ("draft", "Draft"),
            ("running", "Running"),
            ("partial", "Partial"),
            ("passed", "Passed"),
            ("failed", "Failed"),
            ("blocked", "Blocked"),
        ],
        required=True,
        default="draft",
        index=True,
    )
    source_database = fields.Char(index=True)
    source_dump_sha256 = fields.Char(index=True)
    source_snapshot_id = fields.Char(index=True)
    source_version = fields.Char()
    target_database = fields.Char(index=True)
    started_at = fields.Datetime(default=fields.Datetime.now)
    finished_at = fields.Datetime()
    company_ids = fields.Many2many("res.company", string="Companies")
    imported_company_count = fields.Integer(readonly=True)
    imported_account_count = fields.Integer(readonly=True)
    imported_journal_count = fields.Integer(readonly=True)
    imported_partner_count = fields.Integer(readonly=True)
    imported_move_count = fields.Integer(readonly=True)
    imported_move_line_count = fields.Integer(readonly=True)
    imported_move_review_count = fields.Integer(readonly=True)
    imported_move_line_review_count = fields.Integer(readonly=True)
    document_regeneration_case_count = fields.Integer(readonly=True)
    document_regeneration_candidate_count = fields.Integer(readonly=True)
    document_regeneration_review_only_count = fields.Integer(readonly=True)
    document_regeneration_blocked_count = fields.Integer(readonly=True)
    imported_payment_count = fields.Integer(readonly=True)
    imported_payment_review_count = fields.Integer(readonly=True)
    imported_bank_statement_line_count = fields.Integer(readonly=True)
    imported_analytic_line_count = fields.Integer(readonly=True)
    imported_attachment_count = fields.Integer(readonly=True)
    imported_reconciliation_count = fields.Integer(readonly=True)
    imported_reconciliation_review_count = fields.Integer(readonly=True)
    imported_source_report_count = fields.Integer(readonly=True)
    imported_deferred_schedule_line_count = fields.Integer(readonly=True)
    external_report_value_count = fields.Integer(readonly=True)
    warning_count = fields.Integer(readonly=True)
    discrepancy_count = fields.Integer(readonly=True)
    statistics_json = fields.Json(copy=False)
    notes = fields.Text()

    discrepancy_ids = fields.One2many("rebuild.account.discrepancy", "import_run_id")

    def _upsert_external_report_value(self, vals):
        ExternalValue = self.env["rebuild.account.external.report.value"]
        domain = [
            ("company_id", "=", vals["company_id"]),
            ("period_key", "=", vals["period_key"]),
            ("form_code", "=", vals["form_code"]),
            ("field_code", "=", vals["field_code"]),
            ("value_kind", "=", vals["value_kind"]),
            ("source_key", "=", vals["source_key"]),
        ]
        record = ExternalValue.search(domain, limit=1)
        vals = {"import_run_id": self.id, **vals}
        if record:
            if record.review_status in {"accepted", "accepted_with_difference", "rejected"}:
                for field_name in ("review_status", "decision", "reviewer_name", "reviewed_at"):
                    vals.pop(field_name, None)
            record.write(vals)
            return record
        return ExternalValue.create(vals)

    def _seed_benchmark_external_report_values(self, companies):
        company = companies.get(1)
        if not company:
            return self.env["rebuild.account.external.report.value"]
        common = {
            "name": "Benchmark deductible VAT goods/services value",
            "active": True,
            "company_id": company.id,
            "source_company_id": 1,
            "currency_id": company.currency_id.id,
            "period_key": "USL benchmark 2024-01-10 to 2025-09-30",
            "value_kind": "benchmark_acceptance_anchor",
            "amount": 1960.00,
            "source_key": "benchmark_tax_package_2025_09_30:deductible_vat_goods_services",
            "source_document": "Supplied benchmark tax package for period ended 2025-09-30",
            "source_reference": "2033-D / CA12 support data and source CA12 clearing entry: deductible VAT on goods and services",
            "review_status": "pending_review",
            "evidence": (
                "External acceptance anchor supplied in the Milestone 13 mandate. The French tax-package "
                "view reconciles it to the posted source CA12 clearing entry when that entry is present; "
                "it must not alter source journal items. Final declaration acceptance remains subject to "
                "accountant review."
            ),
        }
        records = self.env["rebuild.account.external.report.value"]
        for vals in (
            {
                **common,
                "form_code": "2033-D-SD",
                "form_name": "TVA et taxes",
                "field_code": "2033_D_TVA_DEDUCTIBLE_BIENS_SERVICES_445660",
                "field_label": "TVA déductible sur autres biens et services",
            },
            {
                **common,
                "form_code": "3517-S-SD",
                "form_name": "TVA CA12/CA12E",
                "field_code": "3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660",
                "field_label": "CA12 - TVA déductible sur biens et services",
            },
        ):
            records |= self._upsert_external_report_value(vals)
        return records

    @staticmethod
    def _source_text(value):
        if isinstance(value, dict):
            return value.get("fr_FR") or value.get("en_US") or next(iter(value.values()), "")
        return value or ""

    @staticmethod
    def _source_account_code(value, company_id):
        if isinstance(value, dict):
            return value.get(str(company_id)) or next(iter(value.values()), "")
        return value or ""

    @staticmethod
    def _amount(value):
        return float(value or 0.0)

    @staticmethod
    def _source_ids_text(values):
        return ",".join(str(value) for value in (values or []) if value is not None)

    def _upsert_discrepancy(self, vals):
        Discrepancy = self.env["rebuild.account.discrepancy"]
        domain = [("name", "=", vals["name"])]
        if vals.get("period_key"):
            domain.append(("period_key", "=", vals["period_key"]))
        if vals.get("source_model"):
            domain.append(("source_model", "=", vals["source_model"]))
        if vals.get("company_id"):
            domain.append(("company_id", "=", vals["company_id"]))
        records = Discrepancy.search(domain, order="id")
        record = records[:1]
        vals = {"import_run_id": self.id, **vals}
        if record:
            record.write(vals)
            duplicates = records[1:]
            if duplicates:
                duplicates.write({
                    "import_run_id": self.id,
                    "status": "resolved",
                    "decision": f"Superseded by discrepancy {record.id} during idempotent import rerun.",
                })
            return record
        return Discrepancy.create(vals)

    @staticmethod
    def _source_company_ids(options):
        return options.get("source_company_ids") or [1, 8]

    @staticmethod
    def _source_report_decision(row):
        normalized = " ".join(
            str(value or "").lower()
            for value in (row.get("source_name"), row.get("localized_name"))
        )
        if "association" in normalized:
            return "REMOVED_AS_UNUSED"
        mandatory_keywords = [
            "trial balance",
            "balance comptable",
            "general ledger",
            "grand livre",
            "balance sheet",
            "bilan",
            "profit and loss",
            "profit and loss account",
            "compte de résultat",
            "compte de résultats",
            "partner ledger",
            "aged receivable",
            "aged payable",
            "échéancier",
            "journal report",
            "rapport du journal",
            "open items",
            "bank reconciliation",
            "rapprochements bancaires",
            "unrealized",
            "écarts de conversion",
            "tax report",
            "rapport de taxes",
            "déclaration fiscale",
            "fiscal report",
            "annual",
            "comptes annuels",
            "intermediate management",
            "soldes intermédiaires",
            "fec",
        ]
        operational_keywords = [
            "cash flow",
            "flux de trésorerie",
            "executive summary",
            "résumé général",
            "invoice analysis",
            "ec sales",
            "relevé intracommunautaire",
            "oss",
            "deferred",
            "constatées d'avance",
            "depreciation",
            "amortissement",
            "asset",
            "immobilisations",
            "customer statement",
            "relevé client",
            "rapport de relance",
        ]
        if any(keyword in normalized for keyword in mandatory_keywords):
            return "MANDATORY_PARITY"
        if any(keyword in normalized for keyword in operational_keywords):
            return "OPERATIONAL_PARITY"
        return "ACCOUNTANT_REQUESTED"

    @staticmethod
    def _source_report_target_action(row):
        normalized = " ".join(
            str(value or "").lower()
            for value in (row.get("source_name"), row.get("localized_name"))
        )
        country_code = row.get("country_code") or ""
        module = "rebuild_account_migration"
        if "trial balance" in normalized or "balance comptable" in normalized:
            return f"{module}.action_rebuild_account_trial_balance_line"
        if "general ledger" in normalized or "grand livre général" in normalized:
            return f"{module}.action_rebuild_account_general_ledger_line"
        if "journal report" in normalized or "rapport du journal" in normalized:
            return f"{module}.action_rebuild_account_journal_report_line"
        if "partner ledger" in normalized or "grand livre partenaires" in normalized:
            return f"{module}.action_rebuild_account_partner_ledger_line"
        if "customer statement" in normalized or "relevé client" in normalized:
            return f"{module}.action_rebuild_account_report_export_customer_statement"
        if "open items" in normalized:
            return f"{module}.action_rebuild_account_open_item_line"
        if "aged receivable" in normalized or "échéancier clients" in normalized:
            return f"{module}.action_rebuild_account_aged_receivable_line"
        if "aged payable" in normalized or "échéancier fournisseurs" in normalized:
            return f"{module}.action_rebuild_account_aged_payable_line"
        if "bank reconciliation" in normalized or "rapprochements bancaires" in normalized:
            return f"{module}.action_rebuild_account_bank_reconciliation_line"
        if "unrealized" in normalized or "écarts de conversion" in normalized:
            return f"{module}.action_rebuild_account_currency_report_line"
        if "cash flow" in normalized or "flux de trésorerie" in normalized:
            return f"{module}.action_rebuild_account_cash_flow_line"
        if "executive summary" in normalized or "résumé général" in normalized:
            return f"{module}.action_rebuild_account_executive_summary_line"
        if "2024" in normalized and (
            "soldes intermédiaires" in normalized or "intermediate management" in normalized or "imb -" in normalized
        ):
            return f"{module}.action_rebuild_account_report_export_sig_caf_2024"
        if "2024" in normalized and (
            "balance sheet" in normalized or "bilan comptable" in normalized or normalized.strip() == "bilan"
        ):
            return f"{module}.action_rebuild_account_report_export_french_balance_sheet_2024"
        if "2024" in normalized and (
            "profit and loss" in normalized or "compte de résultat" in normalized or "compte de résultats" in normalized
        ):
            return f"{module}.action_rebuild_account_report_export_french_profit_loss_2024"
        if "soldes intermédiaires" in normalized or "intermediate management" in normalized or "imb -" in normalized:
            return f"{module}.action_rebuild_account_sig_caf_line"
        if "comptes annuels" in normalized or "annual statements" in normalized:
            return f"{module}.action_rebuild_account_french_statement_line"
        if "fiscal report" in normalized or "charges non déductibles" in normalized:
            return f"{module}.action_rebuild_account_french_tax_package_line"
        if "group by: account > tax" in normalized or "regrouper par : compte > taxe" in normalized:
            return f"{module}.action_rebuild_account_tax_report_group_account_tax_line"
        if "group by: tax > account" in normalized or "regrouper par : taxe > compte" in normalized:
            return f"{module}.action_rebuild_account_tax_report_group_tax_account_line"
        if "ec sales" in normalized or "relevé intracommunautaire" in normalized:
            return f"{module}.action_rebuild_account_ec_sales_report_line"
        if "oss sales" in normalized or "ventes oss" in normalized:
            return f"{module}.action_rebuild_account_oss_sales_report_line"
        if "oss imports" in normalized or "importations oss" in normalized:
            return f"{module}.action_rebuild_account_oss_imports_report_line"
        if "tax report" in normalized or "rapport de taxes" in normalized or "déclaration fiscale" in normalized:
            return f"{module}.action_rebuild_account_tax_report_line"
        if "deferred expense" in normalized or "charges constatées d'avance" in normalized:
            return f"{module}.action_rebuild_account_deferred_expense_line"
        if "deferred revenue" in normalized or "produits constatés d'avance" in normalized:
            return f"{module}.action_rebuild_account_deferred_revenue_line"
        if "depreciation" in normalized or "amortissement" in normalized:
            return f"{module}.action_rebuild_account_asset_depreciation_schedule_line"
        if "group by: account" in normalized or "regrouper par : compte" in normalized:
            return f"{module}.action_rebuild_account_asset_group_account"
        if "asset group" in normalized or "immobilisations" in normalized:
            return f"{module}.action_rebuild_account_asset"
        if "balance sheet" in normalized or "bilan comptable" in normalized or normalized.strip() == "bilan":
            if country_code == "FR" or "bilan comptable" in normalized:
                return f"{module}.action_rebuild_account_french_balance_sheet_line"
            return f"{module}.action_rebuild_account_balance_sheet_line"
        if "profit and loss" in normalized or "compte de résultat" in normalized or "compte de résultats" in normalized:
            if country_code == "FR" or "compte de résultat" in normalized or "compte de résultats" in normalized:
                return f"{module}.action_rebuild_account_french_profit_loss_line"
            return f"{module}.action_rebuild_account_profit_loss_line"
        return ""

    @staticmethod
    def _source_report_target_evidence_key(row):
        normalized = " ".join(
            str(value or "").lower()
            for value in (row.get("source_name"), row.get("localized_name"))
        )
        country_code = row.get("country_code") or ""
        if "association" in normalized:
            return "association_scope_excluded"
        if "2024" in normalized and (
            "soldes intermédiaires" in normalized or "intermediate management" in normalized or "imb -" in normalized
        ):
            return "sig_caf_2024"
        if "2024" in normalized and (
            "balance sheet" in normalized or "bilan comptable" in normalized or normalized.strip() == "bilan"
        ):
            return "french_balance_sheet_2024"
        if "2024" in normalized and (
            "profit and loss" in normalized or "compte de résultat" in normalized or "compte de résultats" in normalized
        ):
            return "french_profit_and_loss_2024"
        if "trial balance" in normalized or "balance comptable" in normalized:
            return "trial_balance"
        if "general ledger" in normalized or "grand livre général" in normalized:
            return "general_ledger"
        if "journal report" in normalized or "rapport du journal" in normalized:
            return "journal_report"
        if "partner ledger" in normalized or "grand livre partenaires" in normalized:
            return "partner_ledger"
        if "customer statement" in normalized or "relevé client" in normalized:
            return "customer_statement"
        if "open items" in normalized:
            return "open_items"
        if "aged receivable" in normalized or "échéancier clients" in normalized:
            return "aged_receivable"
        if "aged payable" in normalized or "échéancier fournisseurs" in normalized:
            return "aged_payable"
        if "bank reconciliation" in normalized or "rapprochements bancaires" in normalized:
            return "bank_reconciliation"
        if "unrealized" in normalized or "écarts de conversion" in normalized:
            return "currency_report"
        if "cash flow" in normalized or "flux de trésorerie" in normalized:
            return "cash_flow"
        if "executive summary" in normalized or "résumé général" in normalized:
            return "executive_summary"
        if "soldes intermédiaires" in normalized or "intermediate management" in normalized or "imb -" in normalized:
            return "sig_caf"
        if "comptes annuels" in normalized or "annual statements" in normalized:
            return "french_annual_statements"
        if "fiscal report" in normalized or "charges non déductibles" in normalized:
            return "french_tax_package"
        if "group by: account > tax" in normalized or "regrouper par : compte > taxe" in normalized:
            return "tax_report_group_account_tax"
        if "group by: tax > account" in normalized or "regrouper par : taxe > compte" in normalized:
            return "tax_report_group_tax_account"
        if "ec sales" in normalized or "relevé intracommunautaire" in normalized:
            return "ec_sales_list"
        if "oss sales" in normalized or "ventes oss" in normalized:
            return "oss_sales"
        if "oss imports" in normalized or "importations oss" in normalized:
            return "oss_imports"
        if "tax report" in normalized or "rapport de taxes" in normalized or "déclaration fiscale" in normalized:
            return "vat_tax_report"
        if "deferred expense" in normalized or "charges constatées d'avance" in normalized:
            return "deferred_expense"
        if "deferred revenue" in normalized or "produits constatés d'avance" in normalized:
            return "deferred_revenue"
        if "depreciation" in normalized or "amortissement" in normalized:
            return "depreciation_schedule"
        if "group by: account" in normalized or "regrouper par : compte" in normalized:
            return "fixed_asset_group_account"
        if "asset group" in normalized or "immobilisations" in normalized:
            return "fixed_asset_register"
        if "balance sheet" in normalized or "bilan comptable" in normalized or normalized.strip() == "bilan":
            if country_code == "FR" or "bilan comptable" in normalized:
                return "french_balance_sheet"
            return "balance_sheet"
        if "profit and loss" in normalized or "compte de résultat" in normalized or "compte de résultats" in normalized:
            if country_code == "FR" or "compte de résultat" in normalized or "compte de résultats" in normalized:
                return "french_profit_and_loss"
            return "profit_and_loss"
        return ""

    @staticmethod
    def _source_report_evidence_required(decision):
        if decision == "MANDATORY_PARITY":
            return (
                "Availability in Odoo, line-value comparison, drill-down membership, "
                "exports, source-target controls and accountant review where statutory."
            )
        if decision == "OPERATIONAL_PARITY":
            return "Workflow need evidence, target equivalent output and classified material differences."
        if decision == "REMOVED_AS_UNUSED":
            return "Legal-form/company-scope evidence, explicit non-parity decision and stakeholder review."
        return "Accountant usage decision, explicit deferral or approved removal as unused."

    @staticmethod
    def _source_report_parity_evidence(decision, target_action_xmlid, target_evidence_key):
        if not target_action_xmlid:
            return {
                "parity_level": "level_0_unmapped",
                "parity_gap": (
                    "No target report equivalent is assigned. This blocks mandatory or operational parity "
                    "until a target report is implemented or the source report is explicitly deferred or removed."
                ),
                "latest_evidence_status": "missing_target_equivalent",
                "latest_evidence_json": {"target_evidence_key": target_evidence_key or ""},
            }
        if target_evidence_key in {"scope_variant_association_pending", "pcg_2024_variant_pending"}:
            return {
                "parity_level": "level_3_semantic_partial" if decision == "MANDATORY_PARITY" else "level_2_ledger_controls",
                "parity_gap": (
                    "A target report family exists, but this source report variant remains below Level 4 because "
                    "its legal-form or PCG-version scope requires explicit accountant confirmation before acceptance."
                ),
                "latest_evidence_status": "scope_or_version_variant_acceptance_pending",
                "latest_evidence_json": {"target_evidence_key": target_evidence_key},
            }
        if decision == "MANDATORY_PARITY":
            return {
                "parity_level": "level_3_semantic_partial",
                "parity_gap": (
                    "A user-facing target report/export action exists and current technical harness checks cover "
                    "ledger-backed row counts, exports and sampled drill-down. Full Level 4 parity still requires "
                    "line-by-line source formula comparison, drill-down membership comparison, statutory/export "
                    "layout review where applicable and accountant acceptance."
                ),
                "latest_evidence_status": "technical_controls_passed_accountant_acceptance_pending",
                "latest_evidence_json": {"target_evidence_key": target_evidence_key or ""},
            }
        if decision == "OPERATIONAL_PARITY":
            return {
                "parity_level": "level_2_ledger_controls",
                "parity_gap": (
                    "A user-facing target equivalent exists with technical harness coverage. Operational acceptance "
                    "still requires confirmed source usage, line-level comparison for the selected workflow and "
                    "explicit acceptance or deferral."
                ),
                "latest_evidence_status": "technical_controls_passed_operational_acceptance_pending",
                "latest_evidence_json": {"target_evidence_key": target_evidence_key or ""},
            }
        return {
            "parity_level": "level_1_available",
            "parity_gap": (
                "A target equivalent exists, but accountant/product scope confirmation is still required before "
                "this report can be treated as accepted parity or deliberately removed from scope."
            ),
            "latest_evidence_status": "scope_decision_pending",
            "latest_evidence_json": {"target_evidence_key": target_evidence_key or ""},
        }

    def _source_connection(self, options):
        conn = psycopg2.connect(
            host=options.get("source_host") or os.environ.get("ACCOUNTING_SOURCE_DB_HOST", "accounting-source-db"),
            port=options.get("source_port") or os.environ.get("ACCOUNTING_SOURCE_DB_PORT", "5432"),
            dbname=options.get("source_database") or "odoo_online_source_saas_19_2",
            user=options.get("source_user") or os.environ.get("ACCOUNTING_SOURCE_POSTGRES_USER", "odoo"),
            password=options.get("source_password") or os.environ.get("ACCOUNTING_SOURCE_POSTGRES_PASSWORD", "odoo"),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        with conn.cursor() as cr:
            cr.execute("SET ROLE accounting_source_ro")
        return conn

    @staticmethod
    def _fetchall(conn, query, params=None):
        with conn.cursor() as cr:
            cr.execute(query, params or {})
            return list(cr.fetchall())

    def _source_table_exists(self, conn, table):
        rows = self._fetchall(
            conn,
            """
            SELECT EXISTS (
                SELECT 1
                  FROM information_schema.tables
                 WHERE table_schema = 'public'
                   AND table_name = %(table)s
            ) AS exists
            """,
            {"table": table},
        )
        return bool(rows and rows[0]["exists"])

    def _trace_values(self, source_model, source_id, options):
        return {
            "rebuild_source_database": options.get("source_database"),
            "rebuild_source_model": source_model,
            "rebuild_source_id": source_id,
            "rebuild_source_snapshot": options.get("source_snapshot_id"),
            "rebuild_import_run_id": self.id,
            "rebuild_import_status": "imported",
        }

    def _source_report_rows(self, conn):
        if not self._source_table_exists(conn, "account_report"):
            return []
        return self._fetchall(
            conn,
            """
            SELECT r.id,
                   COALESCE(r.name->>'en_US', r.name->>'fr_FR', r.name::text) AS source_name,
                   COALESCE(r.name->>'fr_FR', r.name->>'en_US', r.name::text) AS localized_name,
                   r.active,
                   r.sequence,
                   r.country_id AS source_country_id,
                   country.code AS country_code,
                   r.chart_template,
                   r.root_report_id AS source_root_report_id,
                   COALESCE(root.name->>'en_US', root.name->>'fr_FR', root.name::text) AS root_report_name,
                   handler.model AS source_custom_handler_model,
                   r.availability_condition,
                   r.integer_rounding,
                   r.default_opening_date_filter,
                   r.currency_translation,
                   r.filter_multi_company,
                   r.filter_hide_0_lines,
                   r.filter_hierarchy,
                   r.filter_account_type,
                   r.filter_date_range,
                   r.filter_show_draft,
                   r.filter_unreconciled,
                   r.filter_unfold_all,
                   r.filter_period_comparison,
                   r.filter_growth_comparison,
                   r.filter_journals,
                   r.filter_partner,
                   r.filter_aml_ir_filters,
                   r.filter_budgets,
                   r.filter_analytic_groupby,
                   r.filter_cash_basis,
                   r.use_sections,
                   r.only_tax_exigible,
                   r.use_fiscal_periods,
                   r.allow_foreign_vat,
                   COALESCE(line_counts.line_count, 0)::integer AS line_count,
                   COALESCE(column_counts.column_count, 0)::integer AS column_count,
                   COALESCE(expression_counts.expression_count, 0)::integer AS expression_count,
                   COALESCE(external_value_counts.external_value_count, 0)::integer AS external_value_count,
                   COALESCE(line_codes.line_code_sample, '') AS line_code_sample,
                   COALESCE(engine_counts.expression_engine_summary, '{}'::jsonb) AS expression_engine_summary
              FROM account_report r
              LEFT JOIN res_country country ON country.id = r.country_id
              LEFT JOIN account_report root ON root.id = r.root_report_id
              LEFT JOIN ir_model handler ON handler.id = r.custom_handler_model_id
              LEFT JOIN (
                    SELECT report_id, count(*) AS line_count
                      FROM account_report_line
                     GROUP BY report_id
              ) line_counts ON line_counts.report_id = r.id
              LEFT JOIN (
                    SELECT report_id, count(*) AS column_count
                      FROM account_report_column
                     GROUP BY report_id
              ) column_counts ON column_counts.report_id = r.id
              LEFT JOIN (
                    SELECT line.report_id, count(expression.id) AS expression_count
                      FROM account_report_line line
                      JOIN account_report_expression expression ON expression.report_line_id = line.id
                     GROUP BY line.report_id
              ) expression_counts ON expression_counts.report_id = r.id
              LEFT JOIN (
                    SELECT line.report_id, count(external_value.id) AS external_value_count
                      FROM account_report_line line
                      JOIN account_report_expression expression ON expression.report_line_id = line.id
                      JOIN account_report_external_value external_value ON external_value.target_report_expression_id = expression.id
                     GROUP BY line.report_id
              ) external_value_counts ON external_value_counts.report_id = r.id
              LEFT JOIN (
                    SELECT line.report_id, string_agg(line.code, ', ' ORDER BY line.sequence, line.id) AS line_code_sample
                      FROM account_report_line line
                     WHERE line.code IS NOT NULL
                     GROUP BY line.report_id
              ) line_codes ON line_codes.report_id = r.id
              LEFT JOIN (
                    SELECT q.report_id, jsonb_object_agg(q.engine, q.expression_count ORDER BY q.engine) AS expression_engine_summary
                      FROM (
                            SELECT line.report_id,
                                   COALESCE(expression.engine, 'unknown') AS engine,
                                   count(*) AS expression_count
                              FROM account_report_line line
                              JOIN account_report_expression expression ON expression.report_line_id = line.id
                             GROUP BY line.report_id, COALESCE(expression.engine, 'unknown')
                      ) q
                     GROUP BY q.report_id
              ) engine_counts ON engine_counts.report_id = r.id
             ORDER BY r.id
            """,
        )

    def _source_report_line_rows(self, conn):
        if not self._source_table_exists(conn, "account_report_line"):
            return []
        return self._fetchall(
            conn,
            """
            SELECT line.id,
                   line.report_id,
                   line.parent_id,
                   line.hierarchy_level,
                   line.sequence,
                   line.action_id,
                   line.groupby,
                   line.user_groupby,
                   line.code,
                   line.horizontal_split_side,
                   COALESCE(line.name->>'en_US', line.name->>'fr_FR', line.name::text) AS source_name,
                   COALESCE(line.name->>'fr_FR', line.name->>'en_US', line.name::text) AS localized_name,
                   line.foldable,
                   line.print_on_new_page,
                   line.hide_if_zero,
                   COALESCE(expression_counts.expression_count, 0)::integer AS expression_count
              FROM account_report_line line
              LEFT JOIN (
                    SELECT report_line_id, count(*) AS expression_count
                      FROM account_report_expression
                     GROUP BY report_line_id
              ) expression_counts ON expression_counts.report_line_id = line.id
             ORDER BY line.report_id, line.sequence, line.id
            """,
        )

    def _source_report_expression_rows(self, conn):
        if not self._source_table_exists(conn, "account_report_expression"):
            return []
        return self._fetchall(
            conn,
            """
            SELECT expression.id,
                   expression.report_line_id,
                   line.report_id,
                   line.code AS line_code,
                   COALESCE(line.name->>'en_US', line.name->>'fr_FR', line.name::text) AS line_name,
                   expression.label,
                   expression.engine,
                   expression.formula,
                   expression.subformula,
                   expression.date_scope,
                   expression.figure_type,
                   expression.carryover_target,
                   expression.green_on_positive,
                   expression.blank_if_zero,
                   expression.auditable
              FROM account_report_expression expression
              JOIN account_report_line line ON line.id = expression.report_line_id
             ORDER BY line.report_id, line.sequence, line.id, expression.id
            """,
        )

    def _source_report_column_rows(self, conn):
        if not self._source_table_exists(conn, "account_report_column"):
            return []
        return self._fetchall(
            conn,
            """
            SELECT column_record.id,
                   column_record.report_id,
                   column_record.sequence,
                   column_record.expression_label,
                   column_record.figure_type,
                   COALESCE(column_record.name->>'en_US', column_record.name->>'fr_FR', column_record.name::text) AS source_name,
                   column_record.sortable,
                   column_record.blank_if_zero
              FROM account_report_column column_record
             ORDER BY column_record.report_id, column_record.sequence, column_record.id
            """,
        )

    def _import_source_reports(self, conn, options):
        rows = self._source_report_rows(conn)
        SourceReport = self.env["rebuild.account.source.report"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        imported_reports = SourceReport.browse()
        seen_source_ids = set()
        for row in rows:
            decision = self._source_report_decision(row)
            target_action_xmlid = self._source_report_target_action(row)
            target_evidence_key = self._source_report_target_evidence_key(row)
            parity_evidence = self._source_report_parity_evidence(decision, target_action_xmlid, target_evidence_key)
            vals = {
                "name": row["source_name"] or row["localized_name"] or f"Source report {row['id']}",
                "source_report_id": row["id"],
                "source_name": row["source_name"],
                "localized_name": row["localized_name"],
                "active": bool(row["active"]),
                "sequence": row["sequence"] or 0,
                "country_code": row["country_code"],
                "source_country_id": row["source_country_id"],
                "chart_template": row["chart_template"],
                "source_root_report_id": row["source_root_report_id"],
                "root_report_name": row["root_report_name"],
                "source_custom_handler_model": row["source_custom_handler_model"],
                "decision": decision,
                "decision_basis": "rule_based_v2_from_source_report_names_and_structure",
                "target_status": "partial_target_equivalent" if target_action_xmlid else (
                    "missing_target_equivalent" if decision in {"MANDATORY_PARITY", "OPERATIONAL_PARITY"} else "decision_pending"
                ),
                "target_action_xmlid": target_action_xmlid,
                "target_evidence_key": target_evidence_key,
                "target_strategy": (
                    "Source report is retained in the parity catalogue but removed from the USL SASU target "
                    "scope because it is an association-specific French statement variant. The standard French "
                    "statement family remains available for USL."
                    if decision == "REMOVED_AS_UNUSED"
                    else (
                        "Mapped to the closest USL target report view/export action. This is a partial "
                        "target equivalent until source report formulas, line hierarchy and drill-down "
                        "membership have all been compared."
                        if target_action_xmlid
                        else "Source report is catalogued for accountant/product decision; no target report equivalent is assigned yet."
                    )
                ),
                "acceptance_evidence_required": self._source_report_evidence_required(decision),
                **parity_evidence,
                "line_count": row["line_count"],
                "column_count": row["column_count"],
                "expression_count": row["expression_count"],
                "external_value_count": row["external_value_count"],
                "line_code_sample": row["line_code_sample"],
                "expression_engine_summary": row["expression_engine_summary"],
                "availability_condition": row["availability_condition"],
                "integer_rounding": row["integer_rounding"],
                "default_opening_date_filter": row["default_opening_date_filter"],
                "currency_translation": row["currency_translation"],
                "filter_multi_company": row["filter_multi_company"],
                "filter_hide_0_lines": row["filter_hide_0_lines"],
                "filter_hierarchy": row["filter_hierarchy"],
                "filter_account_type": row["filter_account_type"],
                "filter_date_range": bool(row["filter_date_range"]),
                "filter_show_draft": bool(row["filter_show_draft"]),
                "filter_unreconciled": bool(row["filter_unreconciled"]),
                "filter_unfold_all": bool(row["filter_unfold_all"]),
                "filter_period_comparison": bool(row["filter_period_comparison"]),
                "filter_growth_comparison": bool(row["filter_growth_comparison"]),
                "filter_journals": bool(row["filter_journals"]),
                "filter_partner": bool(row["filter_partner"]),
                "filter_aml_ir_filters": bool(row["filter_aml_ir_filters"]),
                "filter_budgets": bool(row["filter_budgets"]),
                "filter_analytic_groupby": bool(row["filter_analytic_groupby"]),
                "filter_cash_basis": bool(row["filter_cash_basis"]),
                "use_sections": bool(row["use_sections"]),
                "only_tax_exigible": bool(row["only_tax_exigible"]),
                "use_fiscal_periods": bool(row["use_fiscal_periods"]),
                "allow_foreign_vat": bool(row["allow_foreign_vat"]),
                "note": (
                    "Imported from source account.report metadata for parity tracking only. "
                    "No Enterprise implementation code is copied into the target."
                ),
                **self._trace_values("account.report", row["id"], options),
            }
            report = SourceReport.search([
                ("rebuild_source_model", "=", "account.report"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if report:
                report.write(vals)
            else:
                report = SourceReport.create(vals)
            imported_reports |= report
            seen_source_ids.add(row["id"])

        stale_reports = SourceReport.search([
            ("rebuild_source_model", "=", "account.report"),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("rebuild_source_id", "not in", list(seen_source_ids) or [0]),
        ])
        stale_reports.unlink()
        return {
            "source_report_count": len(rows),
            "active_source_report_count": sum(1 for row in rows if row["active"]),
            "imported_source_report_count": len(imported_reports),
            "mandatory_parity_count": len(imported_reports.filtered(lambda report: report.decision == "MANDATORY_PARITY")),
            "operational_parity_count": len(imported_reports.filtered(lambda report: report.decision == "OPERATIONAL_PARITY")),
            "accountant_requested_count": len(imported_reports.filtered(lambda report: report.decision == "ACCOUNTANT_REQUESTED")),
            "partial_target_equivalent_count": len(imported_reports.filtered(lambda report: report.target_status == "partial_target_equivalent")),
            "missing_target_equivalent_count": len(imported_reports.filtered(lambda report: report.target_status == "missing_target_equivalent")),
            "level_0_unmapped_count": len(imported_reports.filtered(lambda report: report.parity_level == "level_0_unmapped")),
            "level_1_available_count": len(imported_reports.filtered(lambda report: report.parity_level == "level_1_available")),
            "level_2_ledger_controls_count": len(imported_reports.filtered(lambda report: report.parity_level == "level_2_ledger_controls")),
            "level_3_semantic_partial_count": len(imported_reports.filtered(lambda report: report.parity_level == "level_3_semantic_partial")),
            "level_4_evidence_partial_count": len(imported_reports.filtered(lambda report: report.parity_level == "level_4_evidence_partial")),
            "level_4_accepted_count": len(imported_reports.filtered(lambda report: report.parity_level == "level_4_accepted")),
        }

    def _import_source_report_structure(self, conn, options):
        SourceReport = self.env["rebuild.account.source.report"].with_context(active_test=False)
        SourceLine = self.env["rebuild.account.source.report.line"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        SourceExpression = self.env["rebuild.account.source.report.expression"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        SourceColumn = self.env["rebuild.account.source.report.column"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        reports_by_source_id = {
            report.source_report_id: report
            for report in SourceReport.search([
                ("rebuild_source_model", "=", "account.report"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ])
        }

        line_rows = self._source_report_line_rows(conn)
        imported_lines = SourceLine.browse()
        line_by_source_id = {}
        seen_line_source_ids = set()
        for row in line_rows:
            report = reports_by_source_id.get(row["report_id"])
            if not report:
                continue
            vals = {
                "name": row["source_name"] or row["localized_name"] or f"Source report line {row['id']}",
                "source_line_id": row["id"],
                "source_report_id": row["report_id"],
                "source_parent_line_id": row["parent_id"],
                "source_action_id": row["action_id"],
                "report_id": report.id,
                "parent_line_id": False,
                "hierarchy_level": row["hierarchy_level"] or 0,
                "sequence": row["sequence"] or 0,
                "code": row["code"],
                "localized_name": row["localized_name"],
                "groupby": row["groupby"],
                "user_groupby": row["user_groupby"],
                "horizontal_split_side": row["horizontal_split_side"],
                "foldable": bool(row["foldable"]),
                "print_on_new_page": bool(row["print_on_new_page"]),
                "hide_if_zero": bool(row["hide_if_zero"]),
                "expression_count": row["expression_count"],
                **self._trace_values("account.report.line", row["id"], options),
            }
            line = SourceLine.search([
                ("rebuild_source_model", "=", "account.report.line"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if line:
                line.write(vals)
            else:
                line = SourceLine.create(vals)
            imported_lines |= line
            line_by_source_id[row["id"]] = line
            seen_line_source_ids.add(row["id"])
        for row in line_rows:
            parent_line = line_by_source_id.get(row["parent_id"])
            line = line_by_source_id.get(row["id"])
            if line and parent_line and line.parent_line_id != parent_line:
                line.parent_line_id = parent_line.id

        expression_rows = self._source_report_expression_rows(conn)
        imported_expressions = SourceExpression.browse()
        seen_expression_source_ids = set()
        for row in expression_rows:
            report = reports_by_source_id.get(row["report_id"])
            line = line_by_source_id.get(row["report_line_id"])
            if not report or not line:
                continue
            label = row["label"] or ""
            vals = {
                "name": f"{line.code or line.name} / {label or row['engine'] or row['id']}",
                "source_expression_id": row["id"],
                "source_report_id": row["report_id"],
                "source_report_line_id": row["report_line_id"],
                "report_id": report.id,
                "line_id": line.id,
                "line_code": row["line_code"],
                "line_name": row["line_name"],
                "label": row["label"],
                "engine": row["engine"],
                "formula": row["formula"],
                "subformula": row["subformula"],
                "date_scope": row["date_scope"],
                "figure_type": row["figure_type"],
                "carryover_target": row["carryover_target"],
                "green_on_positive": bool(row["green_on_positive"]),
                "blank_if_zero": bool(row["blank_if_zero"]),
                "auditable": bool(row["auditable"]),
                **self._trace_values("account.report.expression", row["id"], options),
            }
            expression = SourceExpression.search([
                ("rebuild_source_model", "=", "account.report.expression"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if expression:
                expression.write(vals)
            else:
                expression = SourceExpression.create(vals)
            imported_expressions |= expression
            seen_expression_source_ids.add(row["id"])

        column_rows = self._source_report_column_rows(conn)
        imported_columns = SourceColumn.browse()
        seen_column_source_ids = set()
        for row in column_rows:
            report = reports_by_source_id.get(row["report_id"])
            if not report:
                continue
            vals = {
                "name": row["source_name"] or row["expression_label"] or f"Source report column {row['id']}",
                "source_column_id": row["id"],
                "source_report_id": row["report_id"],
                "report_id": report.id,
                "sequence": row["sequence"] or 0,
                "expression_label": row["expression_label"],
                "figure_type": row["figure_type"],
                "sortable": bool(row["sortable"]),
                "blank_if_zero": bool(row["blank_if_zero"]),
                **self._trace_values("account.report.column", row["id"], options),
            }
            column = SourceColumn.search([
                ("rebuild_source_model", "=", "account.report.column"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if column:
                column.write(vals)
            else:
                column = SourceColumn.create(vals)
            imported_columns |= column
            seen_column_source_ids.add(row["id"])

        for model, source_model, seen_source_ids in (
            (SourceLine, "account.report.line", seen_line_source_ids),
            (SourceExpression, "account.report.expression", seen_expression_source_ids),
            (SourceColumn, "account.report.column", seen_column_source_ids),
        ):
            model.search([
                ("rebuild_source_model", "=", source_model),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "not in", list(seen_source_ids) or [0]),
            ]).unlink()

        for report in reports_by_source_id.values():
            report.write({
                "imported_line_count": SourceLine.search_count([("report_id", "=", report.id)]),
                "imported_expression_count": SourceExpression.search_count([("report_id", "=", report.id)]),
                "imported_column_count": SourceColumn.search_count([("report_id", "=", report.id)]),
            })

        return {
            "source_report_line_count": len(line_rows),
            "imported_source_report_line_count": len(imported_lines),
            "source_report_expression_count": len(expression_rows),
            "imported_source_report_expression_count": len(imported_expressions),
            "source_report_column_count": len(column_rows),
            "imported_source_report_column_count": len(imported_columns),
        }

    def _company_map(self, conn, options, countries):
        rows = self._fetchall(
            conn,
            """
            SELECT c.id, c.name, c.fiscalyear_last_day, c.fiscalyear_last_month,
                   c.fiscalyear_lock_date, c.tax_lock_date, c.sale_lock_date,
                   c.purchase_lock_date, c.hard_lock_date, c.account_fiscal_country_id,
                   rp.country_id AS partner_country_id, rp.vat, rp.company_registry,
                   rc.name AS currency_name
            FROM res_company c
            LEFT JOIN res_partner rp ON rp.id = c.partner_id
            LEFT JOIN res_currency rc ON rc.id = c.currency_id
            ORDER BY c.id
            """,
        )
        companies = {}
        Company = self.env["res.company"].with_context(
            install_demo=True,
            chart_template_load=True,
            tracking_disable=True,
            mail_create_nolog=True,
        )
        existing_main_company = Company.search([], order="id", limit=1)
        for row in rows:
            company = Company.search([
                ("rebuild_source_model", "=", "res.company"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            if not company and row["id"] == 1 and existing_main_company:
                company = existing_main_company
            currency = self.env["res.currency"].search([("name", "=", row["currency_name"] or "EUR")], limit=1)
            vals = {
                "name": row["name"],
                "currency_id": currency.id if currency else False,
                "fiscalyear_last_day": row["fiscalyear_last_day"] or 31,
                "fiscalyear_last_month": row["fiscalyear_last_month"] or "12",
                **self._trace_values("res.company", row["id"], options),
            }
            if row["account_fiscal_country_id"] in countries:
                vals["account_fiscal_country_id"] = countries[row["account_fiscal_country_id"]].id
            if row["partner_country_id"] in countries:
                vals["country_id"] = countries[row["partner_country_id"]].id
            if row["vat"]:
                vals["vat"] = row["vat"]
            if row["company_registry"]:
                vals["company_registry"] = row["company_registry"]
            if "iap_enrich_auto_done" in Company._fields:
                vals["iap_enrich_auto_done"] = True
            vals.update(self._company_report_layout_defaults(company))
            if company:
                company.write(vals)
            else:
                company = Company.create(vals)
            companies[row["id"]] = company
        return companies, rows

    def _company_report_layout_defaults(self, company=False):
        """Avoid diverting accounting report users into document-layout setup."""
        if "external_report_layout_id" not in self.env["res.company"]._fields:
            return {}
        if company and company.external_report_layout_id:
            return {}
        layout = self.env.ref("web.external_layout_standard", raise_if_not_found=False)
        if not layout:
            return {}
        return {"external_report_layout_id": layout.id}

    def _currency_map(self, conn):
        rows = self._fetchall(conn, "SELECT id, name, active FROM res_currency ORDER BY id")
        currencies = {}
        for row in rows:
            currency = self.env["res.currency"].with_context(active_test=False).search([("name", "=", row["name"])], limit=1)
            if currency:
                if row["active"] and not currency.active:
                    currency.active = True
                currencies[row["id"]] = currency
        return currencies

    def _partner_map(self, conn, options):
        rows = self._fetchall(
            conn,
            """
            SELECT DISTINCT rp.id, rp.name, rp.ref, rp.vat, rp.company_registry, rp.email,
                   rp.phone, rp.is_company, rp.company_id, rp.active
            FROM res_partner rp
            WHERE rp.id IN (
                SELECT partner_id FROM account_move
                WHERE company_id = ANY(%(source_company_ids)s) AND state = 'posted'
                  AND date BETWEEN %(date_from)s AND %(date_to)s
                  AND partner_id IS NOT NULL
                UNION
                SELECT aml.partner_id
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                WHERE am.company_id = ANY(%(source_company_ids)s) AND am.state = 'posted'
                  AND am.date BETWEEN %(date_from)s AND %(date_to)s
                  AND aml.partner_id IS NOT NULL
                UNION
                SELECT partner_id
                FROM account_analytic_line
                WHERE company_id = ANY(%(source_company_ids)s)
                  AND date BETWEEN %(date_from)s AND %(date_to)s
                  AND partner_id IS NOT NULL
                UNION
                SELECT partner_id
                FROM account_move
                WHERE company_id = ANY(%(source_company_ids)s)
                  AND date >= %(date_from)s
                  AND state <> 'posted'
                  AND partner_id IS NOT NULL
                UNION
                SELECT commercial_partner_id
                FROM account_move
                WHERE company_id = ANY(%(source_company_ids)s)
                  AND date >= %(date_from)s
                  AND state <> 'posted'
                  AND commercial_partner_id IS NOT NULL
                UNION
                SELECT aml.partner_id
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                WHERE am.company_id = ANY(%(source_company_ids)s)
                  AND am.date >= %(date_from)s
                  AND am.state <> 'posted'
                  AND aml.partner_id IS NOT NULL
                UNION
                SELECT partner_id
                FROM account_payment
                WHERE company_id = ANY(%(source_company_ids)s)
                  AND date BETWEEN %(date_from)s AND %(date_to)s
                  AND partner_id IS NOT NULL
            )
            ORDER BY rp.id
            """,
            options,
        )
        partners = {}
        for row in rows:
            partner = self.env["res.partner"].with_context(active_test=False).search([
                ("rebuild_source_model", "=", "res.partner"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            vals = {
                "name": row["name"] or f"Source partner {row['id']}",
                "ref": row["ref"],
                "vat": row["vat"],
                "company_registry": row["company_registry"],
                "email": row["email"],
                "phone": row["phone"],
                "is_company": row["is_company"],
                "active": row["active"],
                **self._trace_values("res.partner", row["id"], options),
            }
            if partner:
                partner.write(vals)
            else:
                partner = self.env["res.partner"].create(vals)
            partners[row["id"]] = partner
        return partners

    def _account_map(self, conn, options, companies, currencies):
        rows = self._fetchall(
            conn,
            """
            WITH source_account_ids AS (
                SELECT DISTINCT aml.account_id AS id
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                WHERE am.company_id = ANY(%(source_company_ids)s) AND am.state = 'posted'
                  AND am.date BETWEEN %(date_from)s AND %(date_to)s
                UNION
                SELECT DISTINCT aml.account_id AS id
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                WHERE am.company_id = ANY(%(source_company_ids)s) AND am.state != 'posted'
                  AND am.date BETWEEN %(date_from)s AND %(date_to)s
                  AND aml.account_id IS NOT NULL
                UNION
                SELECT DISTINCT default_account_id AS id
                FROM account_journal
                WHERE company_id = ANY(%(source_company_ids)s) AND default_account_id IS NOT NULL
                UNION
                SELECT DISTINCT account_id AS id
                FROM account_tax_repartition_line
                WHERE account_id IS NOT NULL
                UNION
                SELECT DISTINCT cash_basis_transition_account_id AS id
                FROM account_tax
                WHERE cash_basis_transition_account_id IS NOT NULL
                UNION
                SELECT DISTINCT tax_payable_account_id AS id
                FROM account_tax_group
                WHERE tax_payable_account_id IS NOT NULL
                UNION
                SELECT DISTINCT tax_receivable_account_id AS id
                FROM account_tax_group
                WHERE tax_receivable_account_id IS NOT NULL
                UNION
                SELECT DISTINCT advance_tax_payment_account_id AS id
                FROM account_tax_group
                WHERE advance_tax_payment_account_id IS NOT NULL
                UNION
                SELECT DISTINCT outstanding_account_id AS id
                FROM account_payment
                WHERE company_id = ANY(%(source_company_ids)s)
                  AND date BETWEEN %(date_from)s AND %(date_to)s
                  AND outstanding_account_id IS NOT NULL
                UNION
                SELECT DISTINCT destination_account_id AS id
                FROM account_payment
                WHERE company_id = ANY(%(source_company_ids)s)
                  AND date BETWEEN %(date_from)s AND %(date_to)s
                  AND destination_account_id IS NOT NULL
                UNION
                SELECT DISTINCT general_account_id AS id
                FROM account_analytic_line
                WHERE company_id = ANY(%(source_company_ids)s)
                  AND date BETWEEN %(date_from)s AND %(date_to)s
                  AND general_account_id IS NOT NULL
                UNION
                SELECT DISTINCT account_asset_id AS id
                FROM account_asset
                WHERE account_asset_id IS NOT NULL
                UNION
                SELECT DISTINCT account_depreciation_id AS id
                FROM account_asset
                WHERE account_depreciation_id IS NOT NULL
                UNION
                SELECT DISTINCT account_depreciation_expense_id AS id
                FROM account_asset
                WHERE account_depreciation_expense_id IS NOT NULL
                UNION
                SELECT DISTINCT aa.id AS id
                FROM account_account aa
                JOIN account_account_res_company_rel rel ON rel.account_account_id = aa.id
                WHERE aa.account_type = 'equity_unaffected'
                  AND rel.res_company_id = ANY(%(source_company_ids)s)
            )
            SELECT aa.id, aa.name, aa.code_store, aa.account_type, aa.active, aa.reconcile,
                   aa.non_trade, aa.currency_id,
                   array_remove(array_agg(rel.res_company_id ORDER BY rel.res_company_id), NULL) AS company_ids
            FROM account_account aa
            LEFT JOIN account_account_res_company_rel rel ON rel.account_account_id = aa.id
            WHERE aa.id IN (SELECT id FROM source_account_ids)
            GROUP BY aa.id
            ORDER BY aa.id
            """,
            options,
        )
        self._quarantine_bootstrap_account_code_collisions(rows, options, companies)
        accounts = {}
        archive_after_post = []
        Account = self.env["account.account"].with_context(active_test=False, import_file=True)
        for row in rows:
            source_company_ids = [company_id for company_id in row["company_ids"] or self._source_company_ids(options) if company_id in companies]
            if not source_company_ids:
                continue
            company_id = 1 if 1 in source_company_ids else source_company_ids[0]
            company = companies[company_id]
            target_company_ids = [companies[source_company_id].id for source_company_id in source_company_ids]
            code = self._source_account_code(row["code_store"], company_id)
            account = Account.search([
                ("rebuild_source_model", "=", "account.account"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            if not account and code:
                account = Account.with_company(company).search([
                    ("code", "=", code),
                    ("company_ids", "in", company.id),
                ], limit=1)
            account_type = row["account_type"] or "asset_current"
            reconcile = bool(row["reconcile"] or account_type in ("asset_receivable", "liability_payable"))
            target_currency_id = currencies[row["currency_id"]].id if row["currency_id"] in currencies else False
            has_imported_lines = bool(account and self.env["account.move.line"].search_count([
                ("account_id", "=", account.id),
                ("rebuild_source_model", "=", "account.move.line"),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1))
            vals = {
                "name": self._source_text(row["name"]) or code or f"Source account {row['id']}",
                "code": code,
                "account_type": account_type,
                "reconcile": reconcile,
                "non_trade": bool(row["non_trade"]),
                "active": True,
                "company_ids": [Command.set(target_company_ids)],
                **self._trace_values("account.account", row["id"], options),
            }
            if has_imported_lines:
                for structural_field in ("code", "account_type", "reconcile", "non_trade", "company_ids"):
                    vals.pop(structural_field, None)
            if not has_imported_lines:
                vals["currency_id"] = target_currency_id
            elif account.currency_id.id != target_currency_id:
                vals["rebuild_import_note"] = (
                    (vals.get("rebuild_import_note") or "") +
                    " Account currency was not rewritten on idempotent import because posted imported lines exist."
                ).strip()
            if not row["active"]:
                archive_after_post.append(row["id"])
                vals["rebuild_import_note"] = (
                    "Source account was archived but is used by posted history; "
                    "the exact ledger replay temporarily keeps it active for posting "
                    "and archives it after historical moves are posted."
                )
            if account:
                account.with_company(company).write(vals)
            else:
                account = Account.with_company(company).create(vals)
            accounts[row["id"]] = account
        self._archive_empty_bootstrap_unaffected_earnings_accounts(rows, options, companies)
        return accounts, archive_after_post

    def _archive_empty_bootstrap_unaffected_earnings_accounts(self, rows, options, companies):
        """Keep one source-traced unaffected earnings account per imported company.

        Odoo's clean generic chart can create template-only retained-earnings
        accounts. OCA financial reports require exactly one active unaffected
        earnings account per company, so empty template accounts must not stay
        active beside the source-traced account imported from production.
        """
        Account = self.env["account.account"].with_context(
            active_test=False,
            import_file=True,
            tracking_disable=True,
            mail_create_nolog=True,
        )
        MoveLine = self.env["account.move.line"].with_context(active_test=False)
        source_company_ids_by_company = {}
        for row in rows:
            if row["account_type"] != "equity_unaffected":
                continue
            for source_company_id in row["company_ids"] or self._source_company_ids(options):
                if source_company_id in companies:
                    source_company_ids_by_company.setdefault(source_company_id, []).append(row["id"])
        for source_company_id in source_company_ids_by_company:
            company = companies[source_company_id]
            bootstrap_accounts = Account.with_company(company).search([
                ("account_type", "=", "equity_unaffected"),
                ("company_ids", "in", company.id),
                ("rebuild_source_model", "=", False),
                ("active", "=", True),
            ])
            for account in bootstrap_accounts:
                if MoveLine.search_count([("account_id", "=", account.id)], limit=1):
                    continue
                account.with_company(company).write({
                    "active": False,
                    "rebuild_import_note": (
                        "Archived empty clean-target bootstrap unaffected earnings "
                        "account because exact ledger replay imported the source "
                        "retained-earnings account for this company."
                    ),
                })

    def _quarantine_bootstrap_account_code_collisions(self, rows, options, companies):
        """Move empty chart-template accounts out of the source chart namespace.

        A clean Community target is initialized with a generic chart. Exact ledger replay
        must not mutate those bootstrap accounts into unrelated source accounts, because
        they can already be journal defaults and Odoo correctly rejects some type changes.
        """
        Account = self.env["account.account"].with_context(
            active_test=False,
            import_file=True,
            tracking_disable=True,
            mail_create_nolog=True,
        )
        for row in rows:
            source_company_ids = [
                company_id
                for company_id in row["company_ids"] or self._source_company_ids(options)
                if company_id in companies
            ]
            if not source_company_ids:
                continue
            company_id = 1 if 1 in source_company_ids else source_company_ids[0]
            company = companies[company_id]
            code = self._source_account_code(row["code_store"], company_id)
            if not code:
                continue
            bootstrap_accounts = Account.with_company(company).search([
                ("code", "=", code),
                ("company_ids", "in", company.id),
                ("rebuild_source_model", "=", False),
            ])
            for account in bootstrap_accounts:
                replacement_code = f"BOOT.{account.id}.{code}"
                if account.with_company(company).code == replacement_code:
                    continue
                account.with_company(company).write({
                    "code": replacement_code,
                    "active": False,
                    "rebuild_import_note": (
                        f"Quarantined clean-target bootstrap account code {code} before "
                        "USL exact ledger replay; source accounting identity owns that code."
                    ),
                })

    def _country_map(self, conn):
        rows = self._fetchall(conn, "SELECT id, code FROM res_country ORDER BY id")
        countries = {}
        Country = self.env["res.country"]
        for row in rows:
            if not row["code"]:
                continue
            country = Country.search([("code", "=", row["code"])], limit=1)
            if country:
                countries[row["id"]] = country
        return countries

    def _tax_tag_map(self, conn, options, countries):
        rows = self._fetchall(
            conn,
            """
            SELECT id, name, applicability, color, active, country_id
            FROM account_account_tag
            ORDER BY id
            """,
        )
        tags = {}
        Tag = self.env["account.account.tag"].with_context(active_test=False)
        for row in rows:
            country = countries.get(row["country_id"])
            name = self._source_text(row["name"]) or f"Source tag {row['id']}"
            tag = Tag.search([
                ("rebuild_source_model", "=", "account.account.tag"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            if not tag:
                tag = Tag.search([
                    ("name", "=", name),
                    ("applicability", "=", row["applicability"]),
                    ("country_id", "=", country.id if country else False),
                ], limit=1)
            vals = {
                "name": name,
                "applicability": row["applicability"] or "accounts",
                "color": row["color"] or 0,
                "active": bool(row["active"]),
                "country_id": country.id if country else False,
                **self._trace_values("account.account.tag", row["id"], options),
            }
            if tag:
                tag.write(vals)
            else:
                tag = Tag.create(vals)
            tags[row["id"]] = tag
        return tags

    def _tax_group_map(self, conn, options, companies, accounts, countries):
        rows = self._fetchall(
            conn,
            """
            SELECT id, name, sequence, company_id, country_id, pos_receipt_label,
                   preceding_subtotal, tax_payable_account_id, tax_receivable_account_id,
                   advance_tax_payment_account_id
            FROM account_tax_group
            ORDER BY company_id, sequence, id
            """,
        )
        groups = {}
        TaxGroup = self.env["account.tax.group"]
        for row in rows:
            company = companies[row["company_id"]]
            country = countries.get(row["country_id"])
            name = self._source_text(row["name"]) or f"Source tax group {row['id']}"
            group = TaxGroup.search([
                ("rebuild_source_model", "=", "account.tax.group"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            if not group:
                group = TaxGroup.search([
                    ("name", "=", name),
                    ("company_id", "=", company.id),
                    ("country_id", "=", country.id if country else False),
                ], limit=1)
            vals = {
                "name": name,
                "sequence": row["sequence"] or 10,
                "company_id": company.id,
                "country_id": country.id if country else False,
                "pos_receipt_label": row["pos_receipt_label"],
                "preceding_subtotal": self._source_text(row["preceding_subtotal"]),
                **self._trace_values("account.tax.group", row["id"], options),
            }
            account_fields = {
                "tax_payable_account_id": row["tax_payable_account_id"],
                "tax_receivable_account_id": row["tax_receivable_account_id"],
                "advance_tax_payment_account_id": row["advance_tax_payment_account_id"],
            }
            for field_name, source_account_id in account_fields.items():
                vals[field_name] = accounts[source_account_id].id if source_account_id in accounts else False
            if group:
                group.write(vals)
            else:
                group = TaxGroup.create(vals)
            groups[row["id"]] = group
        return groups

    def _tax_map(self, conn, options, companies, accounts, tax_groups, tax_tags, countries):
        tax_rows = self._fetchall(
            conn,
            """
            SELECT id, name, description, invoice_label, invoice_legal_notes,
                   type_tax_use, tax_scope, amount_type, price_include_override,
                   tax_exigibility, sequence, amount, is_domestic, active,
                   include_base_amount, is_base_affected, analytic, company_id,
                   tax_group_id, country_id, cash_basis_transition_account_id,
                   ubl_cii_tax_category_code, ubl_cii_tax_exemption_reason_code
            FROM account_tax
            ORDER BY company_id, sequence, id
            """,
        )
        repartition_rows = self._fetchall(
            conn,
            """
            SELECT id, tax_id, account_id, sequence, repartition_type, document_type,
                   factor_percent, use_in_tax_closing
            FROM account_tax_repartition_line
            ORDER BY tax_id, document_type, repartition_type, sequence, id
            """,
        )
        repartition_tag_rows = self._fetchall(
            conn,
            """
            SELECT account_tax_repartition_line_id AS repartition_line_id,
                   account_account_tag_id AS tag_id
            FROM account_account_tag_account_tax_repartition_line_rel
            ORDER BY account_tax_repartition_line_id, account_account_tag_id
            """,
        )
        child_rows = self._fetchall(
            conn,
            "SELECT parent_tax, child_tax FROM account_tax_filiation_rel ORDER BY parent_tax, child_tax",
        )
        alternative_rows = self._fetchall(
            conn,
            "SELECT dest_tax_id, src_tax_id FROM account_tax_alternatives ORDER BY dest_tax_id, src_tax_id",
        )
        repartitions_by_tax = defaultdict(list)
        for row in repartition_rows:
            repartitions_by_tax[row["tax_id"]].append(row)
        tags_by_repartition = defaultdict(list)
        for row in repartition_tag_rows:
            if row["tag_id"] in tax_tags:
                tags_by_repartition[row["repartition_line_id"]].append(tax_tags[row["tag_id"]].id)

        taxes = {}
        Tax = self.env["account.tax"].with_context(active_test=False, tracking_disable=True)
        for row in tax_rows:
            company = companies[row["company_id"]]
            country = countries.get(row["country_id"])
            name = self._source_text(row["name"]) or f"Source tax {row['id']}"
            tax = Tax.search([
                ("rebuild_source_model", "=", "account.tax"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            if not tax:
                tax = Tax.search([
                    ("name", "=", name),
                    ("type_tax_use", "=", row["type_tax_use"]),
                    ("tax_scope", "=", row["tax_scope"] or False),
                    ("country_id", "=", country.id if country else False),
                    ("company_id", "=", company.id),
                ], limit=1)
            vals = {
                "name": name,
                "description": self._source_text(row["description"]),
                "invoice_label": self._source_text(row["invoice_label"]),
                "invoice_legal_notes": self._source_text(row["invoice_legal_notes"]),
                "type_tax_use": row["type_tax_use"] or "sale",
                "tax_scope": row["tax_scope"],
                "amount_type": row["amount_type"] or "percent",
                "price_include_override": row["price_include_override"],
                "tax_exigibility": row["tax_exigibility"] or "on_invoice",
                "sequence": row["sequence"] or 1,
                "amount": self._amount(row["amount"]),
                "active": True,
                "include_base_amount": bool(row["include_base_amount"]),
                "is_base_affected": bool(row["is_base_affected"]),
                "analytic": bool(row["analytic"]),
                "company_id": company.id,
                "tax_group_id": tax_groups[row["tax_group_id"]].id,
                "country_id": country.id if country else False,
                "cash_basis_transition_account_id": (
                    accounts[row["cash_basis_transition_account_id"]].id
                    if row["cash_basis_transition_account_id"] in accounts else False
                ),
                "ubl_cii_tax_category_code": row["ubl_cii_tax_category_code"],
                "ubl_cii_tax_exemption_reason_code": row["ubl_cii_tax_exemption_reason_code"],
                **self._trace_values("account.tax", row["id"], options),
            }
            if tax:
                tax.write(vals)
            else:
                tax = Tax.create(vals)
            taxes[row["id"]] = tax

            existing_repartition_by_source = {
                line.rebuild_source_id: line
                for line in tax.repartition_line_ids
                if line.rebuild_source_model == "account.tax.repartition.line"
                and line.rebuild_source_snapshot == options.get("source_snapshot_id")
            }
            commands = [
                Command.delete(line.id)
                for line in tax.repartition_line_ids
                if not line.rebuild_source_model
            ]
            for repartition in repartitions_by_tax[row["id"]]:
                repartition_vals = {
                    "sequence": repartition["sequence"] or 1,
                    "repartition_type": repartition["repartition_type"],
                    "document_type": repartition["document_type"],
                    "factor_percent": self._amount(repartition["factor_percent"]),
                    "account_id": accounts[repartition["account_id"]].id if repartition["account_id"] in accounts else False,
                    "use_in_tax_closing": bool(repartition["use_in_tax_closing"]),
                    "tag_ids": [Command.set(tags_by_repartition[repartition["id"]])],
                    **self._trace_values("account.tax.repartition.line", repartition["id"], options),
                }
                existing_repartition = existing_repartition_by_source.get(repartition["id"])
                if existing_repartition:
                    commands.append(Command.update(existing_repartition.id, repartition_vals))
                else:
                    commands.append(Command.create(repartition_vals))
            tax.write({"repartition_line_ids": commands})
            if not row["active"]:
                tax.active = False

        children_by_parent = defaultdict(list)
        for row in child_rows:
            if row["parent_tax"] in taxes and row["child_tax"] in taxes:
                children_by_parent[row["parent_tax"]].append(taxes[row["child_tax"]].id)
        for source_parent_id, child_ids in children_by_parent.items():
            taxes[source_parent_id].children_tax_ids = [Command.set(child_ids)]

        alternatives_by_dest = defaultdict(list)
        for row in alternative_rows:
            if row["dest_tax_id"] in taxes and row["src_tax_id"] in taxes:
                alternatives_by_dest[row["dest_tax_id"]].append(taxes[row["src_tax_id"]].id)
        for source_dest_id, source_tax_ids in alternatives_by_dest.items():
            taxes[source_dest_id].original_tax_ids = [Command.set(source_tax_ids)]

        tax_repartition_lines = {
            line.rebuild_source_id: line
            for line in self.env["account.tax.repartition.line"].search([
                ("rebuild_source_model", "=", "account.tax.repartition.line"),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ])
        }

        return taxes, tax_repartition_lines, {
            "tax_count": len(taxes),
            "tax_group_count": len(tax_groups),
            "tax_tag_count": len(tax_tags),
            "tax_repartition_line_count": len(repartition_rows),
            "tax_repartition_tag_relation_count": len(repartition_tag_rows),
            "tax_child_relation_count": len(child_rows),
            "tax_alternative_relation_count": len(alternative_rows),
        }

    def _journal_map(self, conn, options, companies, accounts, currencies):
        rows = self._fetchall(
            conn,
            """
            SELECT DISTINCT aj.id, aj.name, aj.code, aj.type, aj.company_id, aj.default_account_id,
                   aj.currency_id, aj.active, aj.sequence, aj.refund_sequence, aj.restrict_mode_hash_table
            FROM account_journal aj
            WHERE aj.id IN (
                SELECT DISTINCT journal_id
                FROM account_move
                WHERE company_id = ANY(%(source_company_ids)s) AND state = 'posted'
                  AND date BETWEEN %(date_from)s AND %(date_to)s
                UNION
                SELECT DISTINCT journal_id
                FROM account_move
                WHERE company_id = ANY(%(source_company_ids)s) AND state <> 'posted'
                  AND date >= %(date_from)s
                UNION
                SELECT DISTINCT journal_id
                FROM account_asset
                WHERE journal_id IS NOT NULL
            )
            ORDER BY aj.company_id, aj.id
            """,
            options,
        )
        journals = {}
        Journal = self.env["account.journal"].with_context(active_test=False, import_file=True)
        for row in rows:
            company = companies[row["company_id"]]
            journal = Journal.search([
                ("rebuild_source_model", "=", "account.journal"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            if not journal:
                journal = Journal.search([
                    ("code", "=", row["code"]),
                    ("company_id", "=", company.id),
                ], limit=1)
            vals = {
                "name": self._source_text(row["name"]) or row["code"],
                "code": row["code"],
                "type": row["type"],
                "company_id": company.id,
                "sequence": row["sequence"] or 10,
                "active": True,
                "refund_sequence": bool(row["refund_sequence"]),
                "restrict_mode_hash_table": bool(row["restrict_mode_hash_table"]),
                **self._trace_values("account.journal", row["id"], options),
            }
            if row["default_account_id"] in accounts:
                vals["default_account_id"] = accounts[row["default_account_id"]].id
            if row["currency_id"] in currencies:
                vals["currency_id"] = currencies[row["currency_id"]].id
            if journal:
                journal.write(vals)
            else:
                journal = Journal.create(vals)
            journals[row["id"]] = journal
        return journals

    def _analytic_plan_map(self, conn, options):
        rows = self._fetchall(
            conn,
            """
            SELECT id, name, parent_id, sequence
            FROM account_analytic_plan
            ORDER BY COALESCE(parent_id, 0), id
            """,
        )
        plans = {}
        Plan = self.env["account.analytic.plan"].with_context(active_test=False)
        existing_project_plan_id = int(
            self.env["ir.config_parameter"].sudo().get_param("analytic.project_plan", "0") or 0
        )
        existing_project_plan = Plan.browse(existing_project_plan_id).exists()
        for row in rows:
            plan = Plan.search([
                ("rebuild_source_model", "=", "account.analytic.plan"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            if not plan and row["id"] == 1 and existing_project_plan:
                plan = existing_project_plan
            vals = {
                "name": self._source_text(row["name"]) or f"Source analytic plan {row['id']}",
                "sequence": row["sequence"] or 10,
                **self._trace_values("account.analytic.plan", row["id"], options),
            }
            if row["parent_id"] in plans:
                vals["parent_id"] = plans[row["parent_id"]].id
            if plan:
                plan.write(vals)
            else:
                plan = Plan.create(vals)
            plans[row["id"]] = plan
        if plans:
            self.env["account.analytic.plan"].browse([plan.id for plan in plans.values()])._sync_all_plan_column()
        return plans

    def _analytic_account_map(self, conn, options, companies, partners, analytic_plans):
        rows = self._fetchall(
            conn,
            """
            SELECT id, name, code, plan_id, company_id, partner_id, active
            FROM account_analytic_account
            WHERE company_id IS NULL
               OR company_id = ANY(%(source_company_ids)s)
               OR id IN (
                   SELECT account_id
                   FROM account_analytic_line
                   WHERE company_id = ANY(%(source_company_ids)s)
                     AND date BETWEEN %(date_from)s AND %(date_to)s
               )
            ORDER BY id
            """,
            options,
        )
        analytic_accounts = {}
        AnalyticAccount = self.env["account.analytic.account"].with_context(active_test=False)
        for row in rows:
            plan = analytic_plans.get(row["plan_id"])
            if not plan:
                continue
            analytic_account = AnalyticAccount.search([
                ("rebuild_source_model", "=", "account.analytic.account"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            vals = {
                "name": self._source_text(row["name"]) or f"Source analytic account {row['id']}",
                "code": row["code"],
                "plan_id": plan.id,
                "company_id": companies[row["company_id"]].id if row["company_id"] in companies else False,
                "partner_id": partners[row["partner_id"]].id if row["partner_id"] in partners else False,
                "active": bool(row["active"]),
                **self._trace_values("account.analytic.account", row["id"], options),
            }
            if analytic_account:
                analytic_account.write(vals)
            else:
                analytic_account = AnalyticAccount.create(vals)
            analytic_accounts[row["id"]] = analytic_account
        return analytic_accounts

    def _import_analytic_lines(self, conn, options, companies, partners, accounts, analytic_plans, analytic_accounts):
        rows = self._fetchall(
            conn,
            """
            SELECT id, account_id, partner_id, company_id, currency_id, name, category,
                   date, amount, unit_amount, general_account_id, journal_id,
                   move_line_id, code, ref
            FROM account_analytic_line
            WHERE company_id = ANY(%(source_company_ids)s)
              AND date BETWEEN %(date_from)s AND %(date_to)s
            ORDER BY date, id
            """,
            options,
        )
        AnalyticLine = self.env["account.analytic.line"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        source_move_line_ids = [row["move_line_id"] for row in rows if row["move_line_id"]]
        target_lines = self.env["account.move.line"].search([
            ("rebuild_source_model", "=", "account.move.line"),
            ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ("rebuild_source_id", "in", source_move_line_ids or [0]),
        ])
        move_lines_by_source_id = {
            line.rebuild_source_id: line
            for line in target_lines
        }
        imported_lines = self.env["account.analytic.line"]
        linked_to_move_line_count = 0
        skipped_missing_account = []
        seen_source_ids = set()
        for row in rows:
            analytic_account = analytic_accounts.get(row["account_id"])
            if not analytic_account:
                skipped_missing_account.append(row["id"])
                continue
            vals = {
                "name": row["name"] or row["ref"] or f"Source analytic line {row['id']}",
                "date": row["date"],
                "amount": self._amount(row["amount"]),
                "unit_amount": row["unit_amount"] or 0.0,
                "category": row["category"] or "other",
                "company_id": companies[row["company_id"]].id,
                "partner_id": partners[row["partner_id"]].id if row["partner_id"] in partners else False,
                "general_account_id": accounts[row["general_account_id"]].id if row["general_account_id"] in accounts else False,
                "code": row["code"],
                "ref": row["ref"],
                "rebuild_analytic_account_id": analytic_account.id,
                "rebuild_source_analytic_account_id": row["account_id"],
                "rebuild_source_move_line_id": row["move_line_id"],
                "rebuild_source_general_account_id": row["general_account_id"],
                "rebuild_source_journal_id": row["journal_id"],
                analytic_account.plan_id._column_name(): analytic_account.id,
                **self._trace_values("account.analytic.line", row["id"], options),
            }
            target_move_line = move_lines_by_source_id.get(row["move_line_id"])
            if target_move_line:
                vals["move_line_id"] = target_move_line.id
                linked_to_move_line_count += 1
            existing = AnalyticLine.search([
                ("rebuild_source_model", "=", "account.analytic.line"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            if existing:
                existing.write(vals)
                analytic_line = existing
            else:
                analytic_line = AnalyticLine.create(vals)
            imported_lines |= analytic_line
            seen_source_ids.add(row["id"])
        stale_lines = AnalyticLine.search([
            ("rebuild_source_model", "=", "account.analytic.line"),
            ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ("rebuild_source_id", "not in", list(seen_source_ids) or [0]),
        ])
        stale_lines.unlink()
        if skipped_missing_account:
            self.env["rebuild.account.discrepancy"].create({
                "name": "Source analytic lines could not be imported because analytic accounts are missing",
                "import_run_id": self.id,
                "severity": "P1",
                "classification": "missing_capability",
                "status": "open",
                "period_key": f"{options['date_from']}:{options['date_to']}",
                "source_model": "account.analytic.line",
                "source_value": json.dumps({"source_line_ids": skipped_missing_account[:50]}, sort_keys=True),
                "accounting_impact": "The statutory ledger is unchanged, but management reporting would lose source analytic attribution.",
                "recommendation": "Import or map the missing source analytic accounts and rerun the exact replay.",
            })
        return {
            "source_analytic_plan_count": len(analytic_plans),
            "source_analytic_account_count": len(analytic_accounts),
            "source_analytic_line_count": len(rows),
            "imported_analytic_line_count": len(imported_lines),
            "linked_to_move_line_count": linked_to_move_line_count,
            "unlinked_source_analytic_line_count": len(rows) - linked_to_move_line_count - len(skipped_missing_account),
            "skipped_missing_account_count": len(skipped_missing_account),
        }

    @staticmethod
    def _target_payment_state(source_state):
        return {
            "reconciled": "paid",
            "posted": "paid",
            "canceled": "canceled",
        }.get(source_state, source_state or "draft")

    def _payment_method_line_map(self, conn, journals, accounts):
        rows = self._fetchall(
            conn,
            """
            SELECT pml.id, pml.name, pml.sequence, pml.journal_id,
                   pml.payment_account_id, apm.code AS payment_method_code,
                   apm.payment_type AS payment_method_type
            FROM account_payment_method_line pml
            JOIN account_payment_method apm ON apm.id = pml.payment_method_id
            ORDER BY pml.id
            """,
        )
        method_lines = {}
        PaymentMethod = self.env["account.payment.method"]
        PaymentMethodLine = self.env["account.payment.method.line"]
        for row in rows:
            journal = journals.get(row["journal_id"])
            if not journal:
                continue
            method = PaymentMethod.search([
                ("code", "=", row["payment_method_code"]),
                ("payment_type", "=", row["payment_method_type"]),
            ], limit=1)
            if not method:
                continue
            line = PaymentMethodLine.search([
                ("journal_id", "=", journal.id),
                ("payment_method_id", "=", method.id),
            ], limit=1)
            if not line:
                vals = {
                    "name": row["name"] or method.name,
                    "sequence": row["sequence"] or 10,
                    "journal_id": journal.id,
                    "payment_method_id": method.id,
                }
                if row["payment_account_id"] in accounts:
                    vals["payment_account_id"] = accounts[row["payment_account_id"]].id
                line = PaymentMethodLine.create(vals)
            method_lines[row["id"]] = line
        return method_lines

    def _move_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT id, name, ref, state, move_type, journal_id, company_id, partner_id,
                   currency_id, date, invoice_date, invoice_date_due, payment_reference,
                   sequence_prefix, sequence_number, secure_sequence_number
            FROM account_move
            WHERE company_id = ANY(%(source_company_ids)s) AND state = 'posted'
              AND date BETWEEN %(date_from)s AND %(date_to)s
            ORDER BY date, id
            """,
            options,
        )

    def _move_review_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT am.id, am.name, am.ref, am.state, am.move_type, am.journal_id,
                   am.company_id, am.partner_id, am.commercial_partner_id,
                   am.currency_id, am.date, am.invoice_date, am.invoice_date_due,
                   am.payment_reference, am.payment_state,
                   am.amount_untaxed_signed, am.amount_total_signed,
                   am.amount_residual_signed, am.create_date, am.write_date,
                   count(aml.id)::integer AS source_line_count,
                   count(aml.id) FILTER (WHERE aml.account_id IS NOT NULL)::integer AS source_accounting_line_count,
                   round(COALESCE(sum(aml.debit), 0)::numeric, 2) AS source_line_debit_total,
                   round(COALESCE(sum(aml.credit), 0)::numeric, 2) AS source_line_credit_total,
                   round(COALESCE(sum(aml.balance), 0)::numeric, 2) AS source_line_balance_total
            FROM account_move am
            LEFT JOIN account_move_line aml ON aml.move_id = am.id
            WHERE am.company_id = ANY(%(source_company_ids)s)
              AND am.state <> 'posted'
              AND am.date >= %(date_from)s
            GROUP BY am.id
            ORDER BY am.date, am.id
            """,
            options,
        )

    def _line_rows_by_move(self, conn, options):
        rows = self._fetchall(
            conn,
            """
            SELECT aml.id, aml.move_id, aml.sequence, aml.account_id, aml.currency_id,
                   aml.partner_id, aml.name, aml.ref, aml.date_maturity, aml.debit,
                   aml.credit, aml.amount_currency, aml.tax_base_amount, aml.display_type,
                   aml.tax_line_id, aml.tax_group_id, aml.tax_repartition_line_id,
                   COALESCE((
                       SELECT array_agg(rel.account_tax_id ORDER BY rel.account_tax_id)
                       FROM account_move_line_account_tax_rel rel
                       WHERE rel.account_move_line_id = aml.id
                   ), ARRAY[]::integer[]) AS tax_ids,
                   COALESCE((
                       SELECT array_agg(rel.account_account_tag_id ORDER BY rel.account_account_tag_id)
                       FROM account_account_tag_account_move_line_rel rel
                       WHERE rel.account_move_line_id = aml.id
                   ), ARRAY[]::integer[]) AS tax_tag_ids
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            WHERE am.company_id = ANY(%(source_company_ids)s) AND am.state = 'posted'
              AND am.date BETWEEN %(date_from)s AND %(date_to)s
            ORDER BY aml.move_id, aml.sequence, aml.id
            """,
            options,
        )
        by_move = defaultdict(list)
        for row in rows:
            by_move[row["move_id"]].append(row)
        return by_move

    def _non_account_line_review_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT aml.id, aml.move_id, aml.sequence, aml.account_id, aml.currency_id,
                   aml.partner_id, aml.name, aml.ref, aml.date_maturity, aml.debit,
                   aml.credit, aml.balance, aml.amount_currency, aml.tax_base_amount,
                   aml.display_type, aml.tax_line_id, aml.tax_group_id,
                   aml.tax_repartition_line_id,
                   COALESCE((
                       SELECT array_agg(rel.account_tax_id ORDER BY rel.account_tax_id)
                       FROM account_move_line_account_tax_rel rel
                       WHERE rel.account_move_line_id = aml.id
                   ), ARRAY[]::integer[]) AS tax_ids,
                   COALESCE((
                       SELECT array_agg(rel.account_account_tag_id ORDER BY rel.account_account_tag_id)
                       FROM account_account_tag_account_move_line_rel rel
                       WHERE rel.account_move_line_id = aml.id
                   ), ARRAY[]::integer[]) AS tax_tag_ids,
                   am.name AS move_name, am.state AS move_state, am.move_type,
                   am.journal_id, am.company_id, am.date
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            WHERE am.company_id = ANY(%(source_company_ids)s)
              AND (
                    (
                        am.state = 'posted'
                        AND am.date BETWEEN %(date_from)s AND %(date_to)s
                        AND aml.account_id IS NULL
                    )
                    OR
                    (
                        am.state <> 'posted'
                        AND am.date >= %(date_from)s
                    )
              )
            ORDER BY am.date, aml.move_id, aml.sequence, aml.id
            """,
            options,
        )

    def _payment_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT id, move_id, journal_id, company_id, partner_bank_id,
                   paired_internal_transfer_payment_id, payment_method_line_id,
                   currency_id, partner_id, outstanding_account_id,
                   destination_account_id, name, state, payment_type,
                   partner_type, memo, payment_reference, date, amount,
                   amount_company_currency_signed, is_reconciled, is_matched,
                   is_sent
            FROM account_payment
            WHERE company_id = ANY(%(source_company_ids)s)
              AND date BETWEEN %(date_from)s AND %(date_to)s
            ORDER BY id
            """,
            options,
        )

    def _bank_statement_line_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT bsl.id, bsl.move_id, bsl.journal_id, bsl.company_id,
                   bsl.statement_id, bsl.sequence, bsl.partner_id,
                   bsl.currency_id, bsl.foreign_currency_id,
                   bsl.account_number, bsl.partner_name,
                   bsl.transaction_type, bsl.payment_ref,
                   bsl.internal_index, bsl.transaction_details,
                   bsl.amount, bsl.amount_currency, bsl.is_reconciled,
                   bsl.amount_residual
            FROM account_bank_statement_line bsl
            JOIN account_move am ON am.id = bsl.move_id
            WHERE bsl.company_id = ANY(%(source_company_ids)s)
              AND am.state = 'posted'
              AND am.date BETWEEN %(date_from)s AND %(date_to)s
            ORDER BY bsl.id
            """,
            options,
        )

    def _attachment_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT ia.id, ia.res_model, ia.res_id, ia.company_id, ia.name,
                   ia.res_field, ia.type, ia.url, ia.store_fname,
                   ia.checksum, ia.file_size, ia.mimetype,
                   ia.description, ia.public
            FROM ir_attachment ia
            LEFT JOIN account_move am
                   ON ia.res_model = 'account.move'
                  AND ia.res_id = am.id
            LEFT JOIN account_asset asset
                   ON ia.res_model = 'account.asset'
                  AND ia.res_id = asset.id
            WHERE ia.type = 'binary'
              AND (
                    (
                        ia.res_model = 'account.move'
                        AND am.company_id = ANY(%(source_company_ids)s)
                        AND am.state = 'posted'
                        AND am.date BETWEEN %(date_from)s AND %(date_to)s
                    )
                    OR
                    (
                        ia.res_model = 'account.asset'
                        AND asset.company_id = ANY(%(source_company_ids)s)
                    )
              )
            ORDER BY ia.id
            """,
            options,
        )

    def _target_for_attachment(self, row, options):
        if row["res_model"] == "account.move":
            target = self.env["account.move"].with_context(active_test=False).search([
                ("rebuild_source_model", "=", "account.move"),
                ("rebuild_source_id", "=", row["res_id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            return "account.move", target
        if row["res_model"] == "account.asset":
            target = self.env["rebuild.account.asset"].with_context(active_test=False).search([
                ("rebuild_source_model", "=", "account_asset"),
                ("rebuild_source_id", "=", row["res_id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            return "rebuild.account.asset", target
        return None, self.env["ir.attachment"]

    def _import_attachments(self, conn, options, companies):
        rows = self._attachment_rows(conn, options)
        filestore_path = options.get("source_filestore_path") or "/mnt/accounting-source/filestore"
        Attachment = self.env["ir.attachment"].sudo().with_context(
            image_no_postprocess=True,
            tracking_disable=True,
            mail_create_nolog=True,
        )
        imported = self.env["ir.attachment"]
        missing_files = []
        unmapped_targets = []
        checksum_mismatches = []
        duplicate_traces = []
        for row in rows:
            target_model, target_record = self._target_for_attachment(row, options)
            if not target_model or not target_record:
                unmapped_targets.append(row)
                continue
            if not row["store_fname"]:
                missing_files.append({**row, "missing_reason": "source attachment has no store_fname"})
                continue
            source_path = os.path.join(filestore_path, row["store_fname"])
            if not os.path.isfile(source_path):
                missing_files.append({**row, "missing_reason": f"file not found at {source_path}"})
                continue
            with open(source_path, "rb") as handle:
                raw = handle.read()
            actual_checksum = hashlib.sha1(raw).hexdigest()
            actual_size = len(raw)
            if row["checksum"] and actual_checksum != row["checksum"]:
                checksum_mismatches.append({
                    **row,
                    "expected_checksum": row["checksum"],
                    "actual_checksum": actual_checksum,
                    "actual_size": actual_size,
                })
                continue
            if row["file_size"] is not None and actual_size != row["file_size"]:
                checksum_mismatches.append({
                    **row,
                    "expected_size": row["file_size"],
                    "actual_size": actual_size,
                    "actual_checksum": actual_checksum,
                })
                continue

            self.env.cr.execute(
                """
                SELECT id
                FROM ir_attachment
                WHERE rebuild_source_model = 'ir.attachment'
                  AND rebuild_source_id = %s
                  AND rebuild_source_snapshot = %s
                ORDER BY id
                """,
                [row["id"], options["source_snapshot_id"]],
            )
            attachment_ids = [result[0] for result in self.env.cr.fetchall()]
            if len(attachment_ids) > 1:
                duplicate_traces.append({
                    "source_attachment_id": row["id"],
                    "target_attachment_ids": attachment_ids,
                })
            attachment = Attachment.browse(attachment_ids[:1])
            vals = {
                "name": row["name"] or f"Source attachment {row['id']}",
                "res_model": target_model,
                "res_id": target_record.id,
                "type": "binary",
                "mimetype": row["mimetype"],
                "description": row["description"],
                "public": bool(row["public"]),
                "company_id": (
                    companies[row["company_id"]].id if row["company_id"] in companies
                    else getattr(target_record, "company_id", self.env.company).id
                ),
                "rebuild_import_note": (
                    f"Imported from source filestore path {row['store_fname']}; "
                    f"source checksum {row['checksum']} and size {row['file_size']} verified before import."
                ),
                **self._trace_values("ir.attachment", row["id"], options),
            }
            if row["res_field"]:
                vals["res_field"] = row["res_field"]
            if not attachment or attachment.checksum != actual_checksum or attachment.file_size != actual_size:
                vals["raw"] = raw
            if attachment:
                attachment.write(vals)
            else:
                attachment = Attachment.create(vals)
            if attachment.checksum != actual_checksum or attachment.file_size != actual_size:
                checksum_mismatches.append({
                    **row,
                    "target_attachment_id": attachment.id,
                    "expected_checksum": actual_checksum,
                    "target_checksum": attachment.checksum,
                    "expected_size": actual_size,
                    "target_size": attachment.file_size,
                })
                continue
            imported |= attachment

        if duplicate_traces:
            self.env["rebuild.account.discrepancy"].create({
                "name": "Duplicate target accounting attachments share the same source identity",
                "import_run_id": self.id,
                "severity": "P0",
                "classification": "attachment_difference",
                "status": "open",
                "period_key": f"{options['date_from']}:{options['date_to']}",
                "evidence": json.dumps(duplicate_traces[:50], ensure_ascii=False, sort_keys=True),
                "accounting_impact": "Supporting evidence identity is not deterministic until the duplicate target attachments are rebuilt from a clean target.",
                "recommended_action": "Reset the disposable target database and rerun the import with the fixed attachment trace lookup.",
            })

        if missing_files:
            self.env["rebuild.account.discrepancy"].create({
                "name": "Source accounting attachment files are missing from the mounted filestore",
                "import_run_id": self.id,
                "severity": "P0",
                "classification": "attachment_difference",
                "status": "open",
                "period_key": f"{options['date_from']}:{options['date_to']}",
                "evidence": json.dumps([
                    {
                        "source_attachment_id": row["id"],
                        "res_model": row["res_model"],
                        "res_id": row["res_id"],
                        "store_fname": row["store_fname"],
                        "missing_reason": row["missing_reason"],
                    }
                    for row in missing_files[:50]
                ], ensure_ascii=False, sort_keys=True),
                "accounting_impact": "Supporting evidence cannot be opened from the target for these source records.",
                "recommendation": "Recover the missing source filestore file or classify the lost evidence with accountant approval.",
            })
        if unmapped_targets:
            self.env["rebuild.account.discrepancy"].create({
                "name": "Source accounting attachments point to records outside the imported replay scope",
                "import_run_id": self.id,
                "severity": "P0",
                "classification": "period_or_scope_difference",
                "status": "open",
                "period_key": f"{options['date_from']}:{options['date_to']}",
                "evidence": json.dumps([
                    {
                        "source_attachment_id": row["id"],
                        "res_model": row["res_model"],
                        "res_id": row["res_id"],
                    }
                    for row in unmapped_targets[:50]
                ], ensure_ascii=False, sort_keys=True),
                "accounting_impact": "Supporting evidence cannot be linked to the imported accounting record.",
                "recommendation": "Import the referenced record or explicitly exclude the attachment from the accepted replay scope.",
            })
        if checksum_mismatches:
            self.env["rebuild.account.discrepancy"].create({
                "name": "Source accounting attachment checksum or size mismatch",
                "import_run_id": self.id,
                "severity": "P0",
                "classification": "attachment_difference",
                "status": "open",
                "period_key": f"{options['date_from']}:{options['date_to']}",
                "evidence": json.dumps([
                    {
                        "source_attachment_id": row["id"],
                        "res_model": row["res_model"],
                        "res_id": row["res_id"],
                        "expected_checksum": row.get("expected_checksum"),
                        "actual_checksum": row.get("actual_checksum"),
                        "target_checksum": row.get("target_checksum"),
                        "expected_size": row.get("expected_size"),
                        "actual_size": row.get("actual_size"),
                        "target_size": row.get("target_size"),
                    }
                    for row in checksum_mismatches[:50]
                ], ensure_ascii=False, sort_keys=True),
                "accounting_impact": "The target evidence binary is not byte-equivalent to the verified source evidence.",
                "recommendation": "Stop using the target evidence until the source file and import storage path have been verified.",
            })
        return {
            "source_attachment_count": len(rows),
            "imported_attachment_count": len(imported),
            "missing_file_count": len(missing_files),
            "unmapped_target_count": len(unmapped_targets),
            "checksum_mismatch_count": len(checksum_mismatches),
            "source_total_bytes": sum(int(row["file_size"] or 0) for row in rows),
        }

    def _import_move_reviews(self, conn, options, companies, partners, journals, currencies):
        rows = self._move_review_rows(conn, options)
        MoveReview = self.env["rebuild.account.move.review"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        imported_reviews = self.env["rebuild.account.move.review"]
        seen_source_ids = set()
        for row in rows:
            source_state = row["state"] or "unknown"
            review_vals = {
                "name": row["name"] or row["ref"] or f"Source move {row['id']}",
                "source_name": row["name"],
                "source_move_id": row["id"],
                "source_state": source_state,
                "state": source_state if source_state in {"draft", "cancel"} else "unknown",
                "review_status": "review_required" if source_state == "cancel" else "represented_no_ledger_effect",
                "accounting_effect": "none_non_posted_source_move",
                "company_id": companies[row["company_id"]].id,
                "journal_id": journals[row["journal_id"]].id if row["journal_id"] in journals else False,
                "partner_id": partners[row["partner_id"]].id if row["partner_id"] in partners else False,
                "commercial_partner_id": (
                    partners[row["commercial_partner_id"]].id
                    if row["commercial_partner_id"] in partners
                    else False
                ),
                "currency_id": currencies[row["currency_id"]].id,
                "date": row["date"],
                "invoice_date": row["invoice_date"],
                "invoice_date_due": row["invoice_date_due"],
                "move_type": row["move_type"],
                "ref": row["ref"],
                "payment_reference": row["payment_reference"],
                "payment_state": row["payment_state"],
                "amount_untaxed_signed": self._amount(row["amount_untaxed_signed"]),
                "amount_total_signed": self._amount(row["amount_total_signed"]),
                "amount_residual_signed": self._amount(row["amount_residual_signed"]),
                "source_line_count": row["source_line_count"],
                "source_accounting_line_count": row["source_accounting_line_count"],
                "source_line_debit_total": self._amount(row["source_line_debit_total"]),
                "source_line_credit_total": self._amount(row["source_line_credit_total"]),
                "source_line_balance_total": self._amount(row["source_line_balance_total"]),
                "source_create_date": row["create_date"],
                "source_write_date": row["write_date"],
                "note": (
                    "Source account.move is not posted. It is represented for workflow review only "
                    "and does not create a target posted journal entry."
                ),
                **self._trace_values("account.move", row["id"], options),
            }
            review = MoveReview.search([
                ("rebuild_source_model", "=", "account.move"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if review:
                review.write(review_vals)
            else:
                review = MoveReview.create(review_vals)
            imported_reviews |= review
            seen_source_ids.add(row["id"])

        stale_reviews = MoveReview.search([
            ("rebuild_source_model", "=", "account.move"),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("accounting_effect", "=", "none_non_posted_source_move"),
            ("rebuild_source_id", "not in", list(seen_source_ids) or [0]),
        ])
        stale_reviews.unlink()
        return {
            "source_move_review_count": len(rows),
            "imported_move_review_count": len(imported_reviews),
            "review_required_count": len(imported_reviews.filtered(lambda review: review.review_status == "review_required")),
            "represented_no_ledger_effect_count": len(imported_reviews.filtered(lambda review: review.review_status == "represented_no_ledger_effect")),
        }

    def _import_move_line_reviews(self, conn, options, companies, partners, accounts, journals, currencies):
        rows = self._non_account_line_review_rows(conn, options)
        source_move_ids = [row["move_id"] for row in rows]
        move_map = {
            move.rebuild_source_id: move
            for move in self.env["account.move"].with_context(active_test=False).search([
                ("rebuild_source_model", "=", "account.move"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", source_move_ids or [0]),
            ])
        }
        move_review_map = {
            review.rebuild_source_id: review
            for review in self.env["rebuild.account.move.review"].with_context(active_test=False).search([
                ("rebuild_source_model", "=", "account.move"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", source_move_ids or [0]),
            ])
        }
        LineReview = self.env["rebuild.account.move.line.review"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        imported_reviews = self.env["rebuild.account.move.line.review"]
        seen_source_ids = set()
        missing_imported_moves = []
        missing_move_reviews = []
        posted_non_account_line_count = 0
        non_posted_line_count = 0
        non_posted_accounting_line_count = 0
        for row in rows:
            source_state = row["move_state"] or "unknown"
            is_posted_display_line = source_state == "posted"
            imported_move = move_map.get(row["move_id"]) if is_posted_display_line else False
            imported_move_review = move_review_map.get(row["move_id"]) if not is_posted_display_line else False
            if is_posted_display_line:
                posted_non_account_line_count += 1
            else:
                non_posted_line_count += 1
                if row["account_id"]:
                    non_posted_accounting_line_count += 1
            if is_posted_display_line and not imported_move:
                missing_imported_moves.append(row)
            if not is_posted_display_line and not imported_move_review:
                missing_move_reviews.append(row)
            accounting_effect = (
                "none_non_account_display_line"
                if is_posted_display_line
                else "none_non_posted_source_line"
            )
            review_status = "represented_no_ledger_effect"
            if source_state == "cancel" or (is_posted_display_line and not imported_move) or (
                not is_posted_display_line and not imported_move_review
            ):
                review_status = "review_required"
            source_tax_ids = ",".join(str(tax_id) for tax_id in (row["tax_ids"] or []))
            source_tax_tag_ids = ",".join(str(tag_id) for tag_id in (row["tax_tag_ids"] or []))
            line_currency = currencies.get(row["currency_id"]) or companies[row["company_id"]].currency_id
            review_vals = {
                "name": row["name"] or f"Source display line {row['id']}",
                "source_move_line_id": row["id"],
                "source_move_id": row["move_id"],
                "imported_move_id": imported_move.id if imported_move else False,
                "imported_move_review_id": imported_move_review.id if imported_move_review else False,
                "company_id": companies[row["company_id"]].id,
                "line_currency_id": line_currency.id if line_currency else False,
                "journal_id": journals[row["journal_id"]].id if row["journal_id"] in journals else False,
                "partner_id": partners[row["partner_id"]].id if row["partner_id"] in partners else False,
                "account_id": accounts[row["account_id"]].id if row["account_id"] in accounts else False,
                "date": row["date"],
                "date_maturity": row["date_maturity"],
                "source_move_name": row["move_name"],
                "source_move_state": source_state,
                "source_move_type": row["move_type"],
                "sequence": row["sequence"],
                "display_type": row["display_type"],
                "label": row["name"],
                "ref": row["ref"],
                "review_status": review_status,
                "accounting_effect": accounting_effect,
                "debit": self._amount(row["debit"]),
                "credit": self._amount(row["credit"]),
                "balance": self._amount(row["balance"]),
                "amount_currency": self._amount(row["amount_currency"]),
                "tax_base_amount": self._amount(row["tax_base_amount"]),
                "source_account_id": row["account_id"],
                "source_tax_ids": source_tax_ids,
                "source_tax_tag_ids": source_tax_tag_ids,
                "source_tax_line_id": row["tax_line_id"],
                "source_tax_group_id": row["tax_group_id"],
                "source_tax_repartition_line_id": row["tax_repartition_line_id"],
                "note": (
                    "Source account.move.line has no account_id. It is represented for document context "
                    "only and does not create a target accounting journal item."
                    if is_posted_display_line else
                    "Source account.move.line belongs to a non-posted source move. It is represented "
                    "for workflow and evidence review only and does not affect the posted target ledger."
                ),
                **self._trace_values("account.move.line", row["id"], options),
            }
            review = LineReview.search([
                ("rebuild_source_model", "=", "account.move.line"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if review:
                review.write(review_vals)
            else:
                review = LineReview.create(review_vals)
            imported_reviews |= review
            seen_source_ids.add(row["id"])

        stale_reviews = LineReview.search([
            ("rebuild_source_model", "=", "account.move.line"),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("accounting_effect", "in", ["none_non_account_display_line", "none_non_posted_source_line"]),
            ("rebuild_source_id", "not in", list(seen_source_ids) or [0]),
        ])
        stale_reviews.unlink()
        if missing_imported_moves:
            self.env["rebuild.account.discrepancy"].create({
                "name": "Non-account source display lines could not be linked to imported moves",
                "import_run_id": self.id,
                "severity": "P1",
                "classification": "transfer_defect",
                "status": "open",
                "period_key": f"{options['date_from']}:{options['date_to']}",
                "evidence": json.dumps([
                    {"source_line_id": row["id"], "source_move_id": row["move_id"]}
                    for row in missing_imported_moves[:50]
                ], ensure_ascii=False, sort_keys=True),
                "accounting_impact": "Source document context lines are not traceable to their imported posted move.",
                "recommendation": "Fix posted move import scope before accepting document-context parity.",
            })
        if missing_move_reviews:
            self.env["rebuild.account.discrepancy"].create({
                "name": "Non-posted source move lines could not be linked to source move reviews",
                "import_run_id": self.id,
                "severity": "P1",
                "classification": "transfer_defect",
                "status": "open",
                "period_key": f"{options['date_from']}:open",
                "evidence": json.dumps([
                    {"source_line_id": row["id"], "source_move_id": row["move_id"]}
                    for row in missing_move_reviews[:50]
                ], ensure_ascii=False, sort_keys=True),
                "accounting_impact": (
                    "Source draft/cancelled document lines are present, but they cannot be navigated "
                    "from their source move review records."
                ),
                "recommendation": "Fix non-posted move review import before accepting workflow-document parity.",
            })
        return {
            "source_move_line_review_count": len(rows),
            "imported_move_line_review_count": len(imported_reviews),
            "source_posted_non_account_line_count": posted_non_account_line_count,
            "source_non_posted_line_count": non_posted_line_count,
            "source_non_posted_accounting_line_count": non_posted_accounting_line_count,
            "missing_imported_move_count": len(missing_imported_moves),
            "missing_move_review_count": len(missing_move_reviews),
        }

    def _sync_document_regeneration_cases(self, options):
        Case = self.env["rebuild.account.document.regeneration.case"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        MoveReview = self.env["rebuild.account.move.review"].with_context(active_test=False)
        reviews = MoveReview.search([
            ("rebuild_source_model", "=", "account.move"),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("accounting_effect", "=", "none_non_posted_source_move"),
        ])
        imported_cases = Case
        seen_source_ids = set()
        status_counts = defaultdict(int)
        scope_counts = defaultdict(int)
        generation_status_counts = defaultdict(int)
        for review in reviews:
            classification = Case._classification_from_review(review)
            case_vals = {
                "name": f"Document regeneration case - {review.source_name or review.name}",
                "active": True,
                "move_review_id": review.id,
                "target_move_id": False,
                "validation_note": "",
                **classification,
                "rebuild_source_database": review.rebuild_source_database,
                "rebuild_source_model": review.rebuild_source_model,
                "rebuild_source_id": review.rebuild_source_id,
                "rebuild_source_xmlid": review.rebuild_source_xmlid,
                "rebuild_source_snapshot": review.rebuild_source_snapshot,
                "rebuild_import_run_id": self.id,
                "rebuild_import_status": "transformed",
                "rebuild_import_note": (
                    "Document-regeneration workbench case generated from the non-posted source move review. "
                    "No native target draft is created in exact ledger replay mode."
                ),
            }
            case = Case.search([
                ("rebuild_source_model", "=", review.rebuild_source_model),
                ("rebuild_source_id", "=", review.rebuild_source_id),
                ("rebuild_source_snapshot", "=", review.rebuild_source_snapshot),
            ], limit=1)
            if case:
                preserved_status = {}
                if case.generation_status in {"generated", "validated", "mismatch"}:
                    preserved_status = {
                        "generation_status": case.generation_status,
                        "target_move_id": case.target_move_id.id,
                        "validation_note": case.validation_note,
                    }
                case.write({**case_vals, **preserved_status})
            else:
                case = Case.create(case_vals)
            imported_cases |= case
            seen_source_ids.add(review.rebuild_source_id)
            status_counts[case.case_status] += 1
            scope_counts[case.generation_scope] += 1
            generation_status_counts[case.generation_status] += 1

        stale_cases = Case.search([
            ("rebuild_source_model", "=", "account.move"),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("rebuild_source_id", "not in", list(seen_source_ids) or [0]),
        ])
        stale_cases.write({
            "active": False,
            "generation_status": "blocked",
            "blocker_reason": "Source move review is no longer present in the imported source snapshot.",
            "rebuild_import_run_id": self.id,
            "rebuild_import_status": "skipped",
        })
        candidate_count = status_counts.get("candidate_ready", 0)
        review_only_count = generation_status_counts.get("not_applicable", 0)
        blocked_count = generation_status_counts.get("blocked", 0)
        return {
            "source_move_review_count": len(reviews),
            "document_regeneration_case_count": len(imported_cases),
            "candidate_ready_count": candidate_count,
            "review_only_count": review_only_count,
            "blocked_count": blocked_count,
            "generated_count": generation_status_counts.get("generated", 0),
            "validated_count": generation_status_counts.get("validated", 0),
            "mismatch_count": generation_status_counts.get("mismatch", 0),
            "stale_case_count": len(stale_cases),
            "case_status_counts": dict(sorted(status_counts.items())),
            "generation_scope_counts": dict(sorted(scope_counts.items())),
            "generation_status_counts": dict(sorted(generation_status_counts.items())),
        }

    def _import_bank_statement_lines(self, conn, options, companies, partners, journals, currencies):
        rows = self._bank_statement_line_rows(conn, options)
        source_move_ids = [row["move_id"] for row in rows if row["move_id"]]
        move_map = {
            move.rebuild_source_id: move
            for move in self.env["account.move"].with_context(active_test=False).search([
                ("rebuild_source_model", "=", "account.move"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", source_move_ids or [0]),
            ])
        }
        StatementLine = self.env["account.bank.statement.line"].with_context(
            active_test=False,
            tracking_disable=True,
            mail_create_nolog=True,
            skip_account_move_synchronization=True,
            skip_invoice_sync=True,
        )
        imported_lines = self.env["account.bank.statement.line"]
        skipped_without_imported_move = []
        source_statement_ids = {row["statement_id"] for row in rows if row["statement_id"]}
        residual_updates = []
        now = fields.Datetime.now()
        for row in rows:
            move = move_map.get(row["move_id"])
            if not move:
                skipped_without_imported_move.append(row)
                continue
            statement_line = StatementLine.search([
                ("rebuild_source_model", "=", "account.bank.statement.line"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if statement_line and statement_line.move_id != move:
                self.env["rebuild.account.discrepancy"].create({
                    "name": "Imported bank statement line conflicts with an existing target move link",
                    "import_run_id": self.id,
                    "severity": "P0",
                    "classification": "import_defect",
                    "status": "open",
                    "source_model": "account.bank.statement.line",
                    "source_id": row["id"],
                    "target_model": "account.bank.statement.line",
                    "target_id": statement_line.id,
                    "period_key": f"{options['date_from']}:{options['date_to']}",
                    "evidence": json.dumps({
                        "source_statement_line_id": row["id"],
                        "source_move_id": row["move_id"],
                        "existing_target_move_id": statement_line.move_id.id,
                        "expected_target_move_id": move.id,
                    }, ensure_ascii=False, sort_keys=True),
                    "accounting_impact": "The bank transaction would point to a different journal entry than the source statement line.",
                    "recommendation": "Reset the disposable target and rerun import; if repeated, inspect source identity mapping.",
                })
                continue

            sql_values = {
                "move_id": move.id,
                "journal_id": journals[row["journal_id"]].id,
                "company_id": companies[row["company_id"]].id,
                "statement_id": None,
                "sequence": 1 if row["sequence"] is None else row["sequence"],
                "partner_id": partners[row["partner_id"]].id if row["partner_id"] in partners else None,
                "currency_id": currencies[row["currency_id"]].id if row["currency_id"] in currencies else None,
                "foreign_currency_id": currencies[row["foreign_currency_id"]].id if row["foreign_currency_id"] in currencies else None,
                "account_number": row["account_number"],
                "partner_name": row["partner_name"],
                "transaction_type": row["transaction_type"],
                "payment_ref": row["payment_ref"],
                "internal_index": row["internal_index"],
                "transaction_details": psycopg2.extras.Json(row["transaction_details"]) if row["transaction_details"] is not None else None,
                "amount": self._amount(row["amount"]),
                "amount_currency": self._amount(row["amount_currency"]),
                "is_reconciled": bool(row["is_reconciled"]),
                "amount_residual": self._amount(row["amount_residual"]),
                "write_uid": self.env.uid,
                "write_date": now,
                "rebuild_source_database": options.get("source_database"),
                "rebuild_source_model": "account.bank.statement.line",
                "rebuild_source_id": row["id"],
                "rebuild_source_snapshot": options.get("source_snapshot_id"),
                "rebuild_import_run_id": self.id,
                "rebuild_import_status": "imported",
                "rebuild_import_note": (
                    "Imported as an _inherits bridge to an existing replayed account.move. "
                    "The normal account.bank.statement.line create() path is intentionally not used because it generates statement move lines."
                ),
            }
            if statement_line:
                self.env.cr.execute(
                    """
                    UPDATE account_bank_statement_line
                       SET move_id = %(move_id)s,
                           journal_id = %(journal_id)s,
                           company_id = %(company_id)s,
                           statement_id = %(statement_id)s,
                           sequence = %(sequence)s,
                           partner_id = %(partner_id)s,
                           currency_id = %(currency_id)s,
                           foreign_currency_id = %(foreign_currency_id)s,
                           account_number = %(account_number)s,
                           partner_name = %(partner_name)s,
                           transaction_type = %(transaction_type)s,
                           payment_ref = %(payment_ref)s,
                           internal_index = %(internal_index)s,
                           transaction_details = %(transaction_details)s,
                           amount = %(amount)s,
                           amount_currency = %(amount_currency)s,
                           is_reconciled = %(is_reconciled)s,
                           amount_residual = %(amount_residual)s,
                           write_uid = %(write_uid)s,
                           write_date = %(write_date)s,
                           rebuild_source_database = %(rebuild_source_database)s,
                           rebuild_source_model = %(rebuild_source_model)s,
                           rebuild_source_id = %(rebuild_source_id)s,
                           rebuild_source_snapshot = %(rebuild_source_snapshot)s,
                           rebuild_import_run_id = %(rebuild_import_run_id)s,
                           rebuild_import_status = %(rebuild_import_status)s,
                           rebuild_import_note = %(rebuild_import_note)s
                     WHERE id = %(statement_line_id)s
                    """,
                    {**sql_values, "statement_line_id": statement_line.id},
                )
            else:
                self.env.cr.execute(
                    """
                    INSERT INTO account_bank_statement_line (
                        move_id, journal_id, company_id, statement_id, sequence,
                        partner_id, currency_id, foreign_currency_id, account_number,
                        partner_name, transaction_type, payment_ref, internal_index,
                        transaction_details, amount, amount_currency, is_reconciled,
                        amount_residual, create_uid, create_date, write_uid, write_date,
                        rebuild_source_database, rebuild_source_model, rebuild_source_id,
                        rebuild_source_snapshot, rebuild_import_run_id,
                        rebuild_import_status, rebuild_import_note
                    ) VALUES (
                        %(move_id)s, %(journal_id)s, %(company_id)s, %(statement_id)s, %(sequence)s,
                        %(partner_id)s, %(currency_id)s, %(foreign_currency_id)s, %(account_number)s,
                        %(partner_name)s, %(transaction_type)s, %(payment_ref)s, %(internal_index)s,
                        %(transaction_details)s, %(amount)s, %(amount_currency)s, %(is_reconciled)s,
                        %(amount_residual)s, %(write_uid)s, %(write_date)s, %(write_uid)s, %(write_date)s,
                        %(rebuild_source_database)s, %(rebuild_source_model)s, %(rebuild_source_id)s,
                        %(rebuild_source_snapshot)s, %(rebuild_import_run_id)s,
                        %(rebuild_import_status)s, %(rebuild_import_note)s
                    )
                    RETURNING id
                    """,
                    sql_values,
                )
                statement_line = StatementLine.browse(self.env.cr.fetchone()[0])
            if move.statement_line_id and move.statement_line_id != statement_line:
                self.env["rebuild.account.discrepancy"].create({
                    "name": "Imported move already has a different statement line",
                    "import_run_id": self.id,
                    "severity": "P0",
                    "classification": "import_defect",
                    "status": "open",
                    "source_model": "account.move",
                    "source_id": row["move_id"],
                    "target_model": "account.move",
                    "target_id": move.id,
                    "period_key": f"{options['date_from']}:{options['date_to']}",
                    "evidence": json.dumps({
                        "source_statement_line_id": row["id"],
                        "target_statement_line_id": statement_line.id,
                        "existing_statement_line_id": move.statement_line_id.id,
                    }, ensure_ascii=False, sort_keys=True),
                    "accounting_impact": "The bank transaction-to-entry relationship is ambiguous.",
                    "recommendation": "Reset the disposable target and rerun import; if repeated, inspect duplicate bank statement line mappings.",
                })
            elif move.statement_line_id != statement_line:
                move.with_context(
                    skip_account_move_synchronization=True,
                    skip_invoice_sync=True,
                    skip_readonly_check=True,
                    tracking_disable=True,
                ).write({"statement_line_id": statement_line.id})
            self.env.cr.execute(
                """
                UPDATE account_bank_statement_line
                   SET is_reconciled = %(is_reconciled)s,
                       amount_residual = %(amount_residual)s,
                       write_uid = %(write_uid)s,
                       write_date = %(write_date)s
                 WHERE id = %(statement_line_id)s
                """,
                {
                    "is_reconciled": bool(row["is_reconciled"]),
                    "amount_residual": self._amount(row["amount_residual"]),
                    "write_uid": self.env.uid,
                    "write_date": now,
                    "statement_line_id": statement_line.id,
                },
            )
            residual_updates.append({
                "is_reconciled": bool(row["is_reconciled"]),
                "amount_residual": self._amount(row["amount_residual"]),
                "write_uid": self.env.uid,
                "write_date": now,
                "statement_line_id": statement_line.id,
            })
            imported_lines |= statement_line

        self.env.flush_all()
        if residual_updates:
            self.env.cr.executemany(
                """
                UPDATE account_bank_statement_line
                   SET is_reconciled = %(is_reconciled)s,
                       amount_residual = %(amount_residual)s,
                       write_uid = %(write_uid)s,
                       write_date = %(write_date)s
                 WHERE id = %(statement_line_id)s
                """,
                residual_updates,
            )
        StatementLine.invalidate_model()
        self.env["account.move"].invalidate_model(["statement_line_id"])
        self.env["account.move.line"].invalidate_model(["statement_line_id", "statement_id"])

        if skipped_without_imported_move:
            self.env["rebuild.account.discrepancy"].create({
                "name": "Source bank statement lines point to journal entries outside the imported replay scope",
                "import_run_id": self.id,
                "severity": "P0",
                "classification": "period_or_scope_difference",
                "status": "open",
                "period_key": f"{options['date_from']}:{options['date_to']}",
                "evidence": json.dumps([
                    {"source_statement_line_id": row["id"], "source_move_id": row["move_id"]}
                    for row in skipped_without_imported_move[:50]
                ], ensure_ascii=False, sort_keys=True),
                "accounting_impact": "A source bank transaction with accounting impact cannot be linked to its target journal entry.",
                "recommendation": "Expand the replay scope or import the missing statement move before declaring bank-statement parity.",
            })
        if source_statement_ids:
            self.env["rebuild.account.discrepancy"].create({
                "name": "Source bank statement headers are not yet represented",
                "import_run_id": self.id,
                "severity": "P1",
                "classification": "missing_capability",
                "status": "open",
                "period_key": f"{options['date_from']}:{options['date_to']}",
                "evidence": json.dumps(sorted(source_statement_ids), ensure_ascii=False),
                "accounting_impact": "Statement line accounting is imported, but source statement checkpoint headers and balances still need a dedicated representation.",
                "recommendation": "Import account.bank.statement headers before declaring full bank statement workflow parity.",
            })
        return {
            "source_bank_statement_line_count": len(rows),
            "imported_bank_statement_line_count": len(imported_lines),
            "skipped_without_imported_move_count": len(skipped_without_imported_move),
            "source_statement_header_count": len(source_statement_ids),
            "direct_sql_bridge": True,
        }

    def _import_payments(self, conn, options, companies, partners, accounts, journals, currencies):
        rows = self._payment_rows(conn, options)
        method_lines = self._payment_method_line_map(conn, journals, accounts)

        source_move_ids = [row["move_id"] for row in rows if row["move_id"]]
        move_map = {
            move.rebuild_source_id: move
            for move in self.env["account.move"].with_context(active_test=False).search([
                ("rebuild_source_model", "=", "account.move"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", source_move_ids or [0]),
            ])
        }

        Payment = self.env["account.payment"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
            skip_account_move_synchronization=True,
            skip_invoice_sync=True,
        )
        PaymentReview = self.env["rebuild.account.payment.review"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        imported_payments = self.env["account.payment"]
        imported_payment_reviews = self.env["rebuild.account.payment.review"]
        seen_review_source_ids = set()
        skipped_without_imported_move = []
        missing_method_lines = []
        state_transformations = defaultdict(int)

        def raw_bool(value):
            return "null" if value is None else str(value).lower()

        for row in rows:
            if not row["move_id"]:
                source_state = row["state"] or "unknown"
                review_vals = {
                    "name": row["name"] or row["memo"] or f"Source payment {row['id']}",
                    "source_payment_id": row["id"],
                    "source_state": source_state,
                    "state": source_state if source_state in {"draft", "reconciled", "canceled"} else "unknown",
                    "review_status": "review_required" if source_state == "reconciled" else "represented_no_ledger_effect",
                    "company_id": companies[row["company_id"]].id,
                    "currency_id": currencies[row["currency_id"]].id,
                    "journal_id": journals[row["journal_id"]].id if row["journal_id"] in journals else False,
                    "partner_id": partners[row["partner_id"]].id if row["partner_id"] in partners else False,
                    "partner_bank_source_id": row["partner_bank_id"],
                    "payment_method_line_source_id": row["payment_method_line_id"],
                    "paired_internal_transfer_payment_source_id": row["paired_internal_transfer_payment_id"],
                    "outstanding_account_id": accounts[row["outstanding_account_id"]].id if row["outstanding_account_id"] in accounts else False,
                    "destination_account_id": accounts[row["destination_account_id"]].id if row["destination_account_id"] in accounts else False,
                    "date": row["date"],
                    "amount": self._amount(row["amount"]),
                    "amount_company_currency_signed": self._amount(row["amount_company_currency_signed"]),
                    "payment_type": row["payment_type"],
                    "partner_type": row["partner_type"],
                    "memo": row["memo"],
                    "payment_reference": row["payment_reference"],
                    "source_is_reconciled": bool(row["is_reconciled"]),
                    "source_is_matched": bool(row["is_matched"]),
                    "source_is_sent": bool(row["is_sent"]),
                    "source_is_reconciled_raw": raw_bool(row["is_reconciled"]),
                    "source_is_matched_raw": raw_bool(row["is_matched"]),
                    "source_is_sent_raw": raw_bool(row["is_sent"]),
                    "accounting_effect": "none_no_source_move",
                    "note": (
                        "Source account.payment has no source journal entry (move_id is null). "
                        "It is represented for workflow review only and does not create target ledger debit or credit."
                    ),
                    **self._trace_values("account.payment", row["id"], options),
                }
                review = PaymentReview.search([
                    ("rebuild_source_model", "=", "account.payment"),
                    ("rebuild_source_id", "=", row["id"]),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ], limit=1)
                if review:
                    review.write(review_vals)
                else:
                    review = PaymentReview.create(review_vals)
                imported_payment_reviews |= review
                seen_review_source_ids.add(row["id"])
                continue
            move = move_map.get(row["move_id"])
            if not move:
                skipped_without_imported_move.append(row)
                continue
            payment_method_line = method_lines.get(row["payment_method_line_id"])
            if not payment_method_line:
                missing_method_lines.append(row)
                continue
            expected_outstanding_account = accounts.get(row["outstanding_account_id"])
            if expected_outstanding_account:
                if not payment_method_line.payment_account_id:
                    payment_method_line.write({"payment_account_id": expected_outstanding_account.id})
                elif payment_method_line.payment_account_id != expected_outstanding_account:
                    self.env["rebuild.account.discrepancy"].create({
                        "name": "Source payment outstanding account conflicts with target payment method line",
                        "import_run_id": self.id,
                        "severity": "P0",
                        "classification": "import_defect",
                        "status": "open",
                        "source_model": "account.payment",
                        "source_id": row["id"],
                        "target_model": "account.payment.method.line",
                        "target_id": payment_method_line.id,
                        "period_key": f"{options['date_from']}:{options['date_to']}",
                        "evidence": json.dumps({
                            "source_payment_id": row["id"],
                            "source_outstanding_account_id": row["outstanding_account_id"],
                            "target_payment_method_line_id": payment_method_line.id,
                            "target_payment_method_line_account_source_id": payment_method_line.payment_account_id.rebuild_source_id,
                        }, ensure_ascii=False, sort_keys=True),
                        "accounting_impact": "The target payment would classify its temporary outstanding account differently than the source payment.",
                        "recommendation": "Split payment method lines or reconcile journal configuration before declaring payment parity.",
                    })
                    continue
            source_state = row["state"]
            target_state = self._target_payment_state(source_state)
            if source_state != target_state:
                state_transformations[f"{source_state}->{target_state}"] += 1
            vals = {
                "move_id": move.id,
                "journal_id": journals[row["journal_id"]].id,
                "company_id": companies[row["company_id"]].id,
                "payment_method_line_id": payment_method_line.id,
                "payment_type": row["payment_type"] or "inbound",
                "partner_type": row["partner_type"] or "customer",
                "date": row["date"],
                "amount": self._amount(row["amount"]),
                "memo": row["memo"],
                "payment_reference": row["payment_reference"],
                "state": target_state,
                "is_sent": bool(row["is_sent"]),
                "rebuild_import_note": (
                    f"Source payment state {source_state!r} imported as target state {target_state!r}; "
                    f"source payment method line {row['payment_method_line_id']} mapped by method code and journal."
                ),
                **self._trace_values("account.payment", row["id"], options),
            }
            if row["currency_id"] in currencies:
                vals["currency_id"] = currencies[row["currency_id"]].id
            if row["partner_id"] in partners:
                vals["partner_id"] = partners[row["partner_id"]].id
            if row["outstanding_account_id"] in accounts:
                vals["outstanding_account_id"] = accounts[row["outstanding_account_id"]].id
            if row["destination_account_id"] in accounts:
                vals["destination_account_id"] = accounts[row["destination_account_id"]].id

            payment = Payment.search([
                ("rebuild_source_model", "=", "account.payment"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if payment and payment.move_id and payment.move_id != move:
                self.env["rebuild.account.discrepancy"].create({
                    "name": "Imported payment conflicts with an existing target move link",
                    "import_run_id": self.id,
                    "severity": "P0",
                    "classification": "import_defect",
                    "status": "open",
                    "source_model": "account.payment",
                    "source_id": row["id"],
                    "target_model": "account.payment",
                    "target_id": payment.id,
                    "period_key": f"{options['date_from']}:{options['date_to']}",
                    "evidence": json.dumps({
                        "source_payment_id": row["id"],
                        "source_move_id": row["move_id"],
                        "existing_target_move_id": payment.move_id.id,
                        "expected_target_move_id": move.id,
                    }, ensure_ascii=False, sort_keys=True),
                    "accounting_impact": "The imported payment would point to a different journal entry than the source payment.",
                    "recommendation": "Reset the disposable target and rerun import; if repeated, inspect source identity mapping.",
                })
                continue
            if payment:
                payment.write(vals)
            else:
                payment = Payment.create(vals)
            if expected_outstanding_account:
                payment._compute_outstanding_account_id()

            if move.origin_payment_id and move.origin_payment_id != payment:
                self.env["rebuild.account.discrepancy"].create({
                    "name": "Imported move already has a different origin payment",
                    "import_run_id": self.id,
                    "severity": "P0",
                    "classification": "import_defect",
                    "status": "open",
                    "source_model": "account.move",
                    "source_id": row["move_id"],
                    "target_model": "account.move",
                    "target_id": move.id,
                    "period_key": f"{options['date_from']}:{options['date_to']}",
                    "evidence": json.dumps({
                        "source_payment_id": row["id"],
                        "target_payment_id": payment.id,
                        "existing_origin_payment_id": move.origin_payment_id.id,
                    }, ensure_ascii=False, sort_keys=True),
                    "accounting_impact": "The payment-to-entry relationship is ambiguous.",
                    "recommendation": "Reset the disposable target and rerun import; if repeated, inspect duplicate payment origin mappings.",
                })
            elif move.origin_payment_id != payment:
                move.with_context(
                    skip_account_move_synchronization=True,
                    skip_invoice_sync=True,
                    skip_readonly_check=True,
                    tracking_disable=True,
                ).write({"origin_payment_id": payment.id})
            imported_payments |= payment

        stale_reviews = PaymentReview.search([
            ("rebuild_source_model", "=", "account.payment"),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("accounting_effect", "=", "none_no_source_move"),
            ("rebuild_source_id", "not in", list(seen_review_source_ids) or [0]),
        ])
        stale_reviews.unlink()
        if skipped_without_imported_move:
            self.env["rebuild.account.discrepancy"].create({
                "name": "Source payments point to journal entries outside the imported replay scope",
                "import_run_id": self.id,
                "severity": "P0",
                "classification": "period_or_scope_difference",
                "status": "open",
                "period_key": f"{options['date_from']}:{options['date_to']}",
                "evidence": json.dumps([
                    {"source_payment_id": row["id"], "source_move_id": row["move_id"]}
                    for row in skipped_without_imported_move[:50]
                ], ensure_ascii=False, sort_keys=True),
                "accounting_impact": "A source payment with accounting impact cannot be linked to its target journal entry.",
                "recommendation": "Expand the replay scope or import the missing payment move before declaring payment parity.",
            })
        if missing_method_lines:
            self.env["rebuild.account.discrepancy"].create({
                "name": "Source payment method lines could not be mapped",
                "import_run_id": self.id,
                "severity": "P0",
                "classification": "missing_capability",
                "status": "open",
                "period_key": f"{options['date_from']}:{options['date_to']}",
                "evidence": json.dumps([
                    {
                        "source_payment_id": row["id"],
                        "source_payment_method_line_id": row["payment_method_line_id"],
                        "source_journal_id": row["journal_id"],
                    }
                    for row in missing_method_lines[:50]
                ], ensure_ascii=False, sort_keys=True),
                "accounting_impact": "The target cannot represent the source payment method relationship for these payments.",
                "recommendation": "Import or map the missing payment method lines before declaring payment parity.",
            })

        return {
            "source_payment_count": len(rows),
            "source_move_backed_payment_count": len(source_move_ids),
            "imported_payment_count": len(imported_payments),
            "no_entry_payment_review_count": len(imported_payment_reviews),
            "skipped_without_imported_move_count": len(skipped_without_imported_move),
            "missing_method_line_count": len(missing_method_lines),
            "state_transformations": dict(state_transformations),
        }

    def _partial_reconcile_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            WITH imported AS (
                SELECT aml.id
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                WHERE am.company_id = ANY(%(source_company_ids)s)
                  AND am.state = 'posted'
                  AND am.date BETWEEN %(date_from)s AND %(date_to)s
            )
            SELECT pr.id, pr.debit_move_id, pr.credit_move_id, pr.full_reconcile_id,
                   pr.exchange_move_id, pr.company_id, pr.max_date, pr.draft_caba_move_vals,
                   pr.amount, pr.debit_amount_currency, pr.credit_amount_currency
            FROM account_partial_reconcile pr
            WHERE pr.debit_move_id IN (SELECT id FROM imported)
              AND pr.credit_move_id IN (SELECT id FROM imported)
            ORDER BY pr.id
            """,
            options,
        )

    def _full_reconcile_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            WITH imported AS (
                SELECT aml.id
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                WHERE am.company_id = ANY(%(source_company_ids)s)
                  AND am.state = 'posted'
                  AND am.date BETWEEN %(date_from)s AND %(date_to)s
            ),
            full_lines AS (
                SELECT full_reconcile_id,
                       count(*) AS total_line_count,
                       count(*) FILTER (WHERE id IN (SELECT id FROM imported)) AS imported_line_count,
                       array_agg(id ORDER BY id) FILTER (WHERE id IN (SELECT id FROM imported)) AS imported_line_ids
                FROM account_move_line
                WHERE full_reconcile_id IS NOT NULL
                GROUP BY full_reconcile_id
            ),
            contained_fulls AS (
                SELECT full_reconcile_id, imported_line_ids
                FROM full_lines
                WHERE imported_line_count > 0
                  AND imported_line_count = total_line_count
            )
            SELECT cf.full_reconcile_id AS id,
                   cf.imported_line_ids AS line_ids,
                   array_remove(array_agg(pr.id ORDER BY pr.id), NULL) AS partial_ids
            FROM contained_fulls cf
            LEFT JOIN account_partial_reconcile pr ON pr.full_reconcile_id = cf.full_reconcile_id
            GROUP BY cf.full_reconcile_id, cf.imported_line_ids
            ORDER BY cf.full_reconcile_id
            """,
            options,
        )

    def _reconciliation_scope_summary(self, conn, options):
        rows = self._fetchall(
            conn,
            """
            WITH imported AS (
                SELECT aml.id
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                WHERE am.company_id = ANY(%(source_company_ids)s)
                  AND am.state = 'posted'
                  AND am.date BETWEEN %(date_from)s AND %(date_to)s
            ),
            full_lines AS (
                SELECT full_reconcile_id,
                       count(*) AS total_line_count,
                       count(*) FILTER (WHERE id IN (SELECT id FROM imported)) AS imported_line_count
                FROM account_move_line
                WHERE full_reconcile_id IS NOT NULL
                GROUP BY full_reconcile_id
            )
            SELECT
                count(pr.id) FILTER (
                    WHERE pr.debit_move_id IN (SELECT id FROM imported)
                      AND pr.credit_move_id IN (SELECT id FROM imported)
                ) AS partials_fully_contained,
                count(pr.id) FILTER (
                    WHERE (pr.debit_move_id IN (SELECT id FROM imported)
                        OR pr.credit_move_id IN (SELECT id FROM imported))
                      AND NOT (
                        pr.debit_move_id IN (SELECT id FROM imported)
                        AND pr.credit_move_id IN (SELECT id FROM imported)
                      )
                ) AS partials_cross_boundary,
                (SELECT count(*)
                   FROM full_lines
                  WHERE imported_line_count > 0
                    AND imported_line_count = total_line_count) AS fulls_fully_contained,
                (SELECT count(*)
                   FROM full_lines
                  WHERE imported_line_count > 0
                    AND imported_line_count < total_line_count) AS fulls_cross_boundary
            FROM account_partial_reconcile pr
            """,
            options,
        )
        return rows[0] if rows else {}

    def _reconciliation_review_rows(self, conn, options):
        partial_rows = self._fetchall(
            conn,
            """
            WITH imported AS (
                SELECT aml.id
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                WHERE am.company_id = ANY(%(source_company_ids)s)
                  AND am.state = 'posted'
                  AND am.date BETWEEN %(date_from)s AND %(date_to)s
            )
            SELECT 'partial' AS reconciliation_kind,
                   pr.id AS source_partial_reconcile_id,
                   pr.full_reconcile_id AS source_full_reconcile_id,
                   pr.debit_move_id AS source_debit_line_id,
                   pr.credit_move_id AS source_credit_line_id,
                   debit_move.id AS source_debit_move_id,
                   credit_move.id AS source_credit_move_id,
                   debit_move.date AS source_debit_move_date,
                   credit_move.date AS source_credit_move_date,
                   debit_move.state AS source_debit_move_state,
                   credit_move.state AS source_credit_move_state,
                   debit_move.company_id AS source_debit_company_id,
                   credit_move.company_id AS source_credit_company_id,
                   pr.company_id AS source_company_id,
                   ARRAY(
                       SELECT DISTINCT company_id
                       FROM (VALUES (debit_move.company_id), (credit_move.company_id)) AS companies(company_id)
                       WHERE company_id IS NOT NULL
                       ORDER BY company_id
                   ) AS source_company_ids,
                   (pr.debit_move_id IN (SELECT id FROM imported)) AS debit_line_imported,
                   (pr.credit_move_id IN (SELECT id FROM imported)) AS credit_line_imported,
                   pr.exchange_move_id AS source_exchange_move_id,
                   pr.max_date,
                   pr.amount,
                   pr.debit_amount_currency,
                   pr.credit_amount_currency,
                   0 AS total_line_count,
                   0 AS imported_line_count,
                   0 AS missing_line_count,
                   ARRAY[]::integer[] AS source_line_ids,
                   ARRAY[]::integer[] AS imported_source_line_ids,
                   ARRAY[]::integer[] AS missing_source_line_ids,
                   ARRAY[]::integer[] AS missing_source_move_ids,
                   ARRAY[]::text[] AS missing_source_move_states,
                   ARRAY[]::text[] AS missing_source_move_dates,
                   ARRAY[]::integer[] AS missing_source_company_ids,
                   ARRAY[]::integer[] AS source_partial_reconcile_ids
            FROM account_partial_reconcile pr
            JOIN account_move_line debit_line ON debit_line.id = pr.debit_move_id
            JOIN account_move debit_move ON debit_move.id = debit_line.move_id
            JOIN account_move_line credit_line ON credit_line.id = pr.credit_move_id
            JOIN account_move credit_move ON credit_move.id = credit_line.move_id
            WHERE (pr.debit_move_id IN (SELECT id FROM imported)
                OR pr.credit_move_id IN (SELECT id FROM imported))
              AND NOT (
                    pr.debit_move_id IN (SELECT id FROM imported)
                AND pr.credit_move_id IN (SELECT id FROM imported)
              )
            ORDER BY pr.id
            """,
            options,
        )
        full_rows = self._fetchall(
            conn,
            """
            WITH imported AS (
                SELECT aml.id
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                WHERE am.company_id = ANY(%(source_company_ids)s)
                  AND am.state = 'posted'
                  AND am.date BETWEEN %(date_from)s AND %(date_to)s
            ),
            full_lines AS (
                SELECT aml.full_reconcile_id,
                       count(*) AS total_line_count,
                       count(*) FILTER (WHERE aml.id IN (SELECT id FROM imported)) AS imported_line_count,
                       array_agg(aml.id ORDER BY aml.id) AS source_line_ids,
                       array_agg(aml.id ORDER BY aml.id) FILTER (WHERE aml.id IN (SELECT id FROM imported)) AS imported_source_line_ids,
                       array_agg(aml.id ORDER BY aml.id) FILTER (WHERE aml.id NOT IN (SELECT id FROM imported)) AS missing_source_line_ids,
                       array_agg(am.id ORDER BY aml.id) FILTER (WHERE aml.id NOT IN (SELECT id FROM imported)) AS missing_source_move_ids,
                       array_agg(am.state ORDER BY aml.id) FILTER (WHERE aml.id NOT IN (SELECT id FROM imported)) AS missing_source_move_states,
                       array_agg(am.date::text ORDER BY aml.id) FILTER (WHERE aml.id NOT IN (SELECT id FROM imported)) AS missing_source_move_dates,
                       array_agg(am.company_id ORDER BY aml.id) FILTER (WHERE aml.id NOT IN (SELECT id FROM imported)) AS missing_source_company_ids,
                       min(am.company_id) FILTER (WHERE aml.id IN (SELECT id FROM imported)) AS source_company_id,
                       array_agg(DISTINCT am.company_id ORDER BY am.company_id) AS source_company_ids
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                WHERE aml.full_reconcile_id IS NOT NULL
                GROUP BY aml.full_reconcile_id
            )
            SELECT 'full' AS reconciliation_kind,
                   NULL::integer AS source_partial_reconcile_id,
                   fl.full_reconcile_id AS source_full_reconcile_id,
                   NULL::integer AS source_debit_line_id,
                   NULL::integer AS source_credit_line_id,
                   NULL::integer AS source_debit_move_id,
                   NULL::integer AS source_credit_move_id,
                   NULL::date AS source_debit_move_date,
                   NULL::date AS source_credit_move_date,
                   NULL::text AS source_debit_move_state,
                   NULL::text AS source_credit_move_state,
                   NULL::integer AS source_debit_company_id,
                   NULL::integer AS source_credit_company_id,
                   fl.source_company_id,
                   fl.source_company_ids,
                   false AS debit_line_imported,
                   false AS credit_line_imported,
                   NULL::integer AS source_exchange_move_id,
                   max(pr.max_date) AS max_date,
                   COALESCE(sum(pr.amount), 0) AS amount,
                   COALESCE(sum(pr.debit_amount_currency), 0) AS debit_amount_currency,
                   COALESCE(sum(pr.credit_amount_currency), 0) AS credit_amount_currency,
                   fl.total_line_count,
                   fl.imported_line_count,
                   fl.total_line_count - fl.imported_line_count AS missing_line_count,
                   fl.source_line_ids,
                   fl.imported_source_line_ids,
                   fl.missing_source_line_ids,
                   fl.missing_source_move_ids,
                   fl.missing_source_move_states,
                   fl.missing_source_move_dates,
                   fl.missing_source_company_ids,
                   array_remove(array_agg(pr.id ORDER BY pr.id), NULL) AS source_partial_reconcile_ids
            FROM full_lines fl
            LEFT JOIN account_partial_reconcile pr ON pr.full_reconcile_id = fl.full_reconcile_id
            WHERE fl.imported_line_count > 0
              AND fl.imported_line_count < fl.total_line_count
            GROUP BY fl.full_reconcile_id,
                     fl.source_company_id,
                     fl.source_company_ids,
                     fl.total_line_count,
                     fl.imported_line_count,
                     fl.source_line_ids,
                     fl.imported_source_line_ids,
                     fl.missing_source_line_ids,
                     fl.missing_source_move_ids,
                     fl.missing_source_move_states,
                     fl.missing_source_move_dates,
                     fl.missing_source_company_ids
            ORDER BY fl.full_reconcile_id
            """,
            options,
        )
        return partial_rows + full_rows

    def _import_reconciliation_reviews(self, conn, options, companies):
        rows = self._reconciliation_review_rows(conn, options)
        Review = self.env["rebuild.account.reconciliation.review"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        imported_reviews = Review.browse()
        seen_partial_ids = set()
        seen_full_ids = set()
        source_line_ids = set()
        source_exchange_move_ids = set()
        for row in rows:
            source_line_ids.update(row["imported_source_line_ids"] or [])
            for key in ("source_debit_line_id", "source_credit_line_id"):
                if row.get(key):
                    source_line_ids.add(row[key])
            if row.get("source_exchange_move_id"):
                source_exchange_move_ids.add(row["source_exchange_move_id"])

        Line = self.env["account.move.line"].with_context(active_test=False)
        Move = self.env["account.move"].with_context(active_test=False)
        line_map = {
            line.rebuild_source_id: line
            for line in Line.search([
                ("rebuild_source_model", "=", "account.move.line"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", list(source_line_ids) or [0]),
            ])
        }
        move_map = {
            move.rebuild_source_id: move
            for move in Move.search([
                ("rebuild_source_model", "=", "account.move"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", list(source_exchange_move_ids) or [0]),
            ])
        }

        for row in rows:
            kind = row["reconciliation_kind"]
            source_model = "account.partial.reconcile" if kind == "partial" else "account.full.reconcile"
            source_id = row["source_partial_reconcile_id"] if kind == "partial" else row["source_full_reconcile_id"]
            source_company_id = row["source_company_id"]
            if source_company_id not in companies:
                source_company_ids = row["source_company_ids"] or []
                source_company_id = next((company_id for company_id in source_company_ids if company_id in companies), None)
            if source_company_id not in companies:
                continue
            vals = {
                "name": (
                    f"Source partial reconciliation {source_id}"
                    if kind == "partial"
                    else f"Source full reconciliation {source_id}"
                ),
                "reconciliation_kind": kind,
                "review_status": "review_required",
                "accounting_effect": "review_only_cross_boundary",
                "company_id": companies[source_company_id].id,
                "source_company_id": source_company_id,
                "source_company_ids": self._source_ids_text(row["source_company_ids"]),
                "source_partial_reconcile_id": row["source_partial_reconcile_id"],
                "source_full_reconcile_id": row["source_full_reconcile_id"],
                "source_debit_line_id": row["source_debit_line_id"],
                "source_credit_line_id": row["source_credit_line_id"],
                "source_debit_move_id": row["source_debit_move_id"],
                "source_credit_move_id": row["source_credit_move_id"],
                "source_debit_move_date": row["source_debit_move_date"],
                "source_credit_move_date": row["source_credit_move_date"],
                "source_debit_move_state": row["source_debit_move_state"],
                "source_credit_move_state": row["source_credit_move_state"],
                "source_debit_company_id": row["source_debit_company_id"],
                "source_credit_company_id": row["source_credit_company_id"],
                "debit_line_imported": bool(row["debit_line_imported"]),
                "credit_line_imported": bool(row["credit_line_imported"]),
                "source_exchange_move_id": row["source_exchange_move_id"],
                "exchange_move_imported": row["source_exchange_move_id"] in move_map,
                "max_date": row["max_date"],
                "amount": self._amount(row["amount"]),
                "debit_amount_currency": self._amount(row["debit_amount_currency"]),
                "credit_amount_currency": self._amount(row["credit_amount_currency"]),
                "total_line_count": row["total_line_count"],
                "imported_line_count": row["imported_line_count"],
                "missing_line_count": row["missing_line_count"],
                "source_line_ids": self._source_ids_text(row["source_line_ids"]),
                "imported_source_line_ids": self._source_ids_text(row["imported_source_line_ids"]),
                "missing_source_line_ids": self._source_ids_text(row["missing_source_line_ids"]),
                "missing_source_move_ids": self._source_ids_text(row["missing_source_move_ids"]),
                "missing_source_move_states": self._source_ids_text(row["missing_source_move_states"]),
                "missing_source_move_dates": self._source_ids_text(row["missing_source_move_dates"]),
                "missing_source_company_ids": self._source_ids_text(row["missing_source_company_ids"]),
                "source_partial_reconcile_ids": self._source_ids_text(row["source_partial_reconcile_ids"]),
                "note": (
                    "Source reconciliation crosses the exact replay boundary. Imported endpoints are visible "
                    "for review, but the target reconciliation graph is not completed until the missing "
                    "source endpoints are imported or explicitly excluded."
                ),
                **self._trace_values(source_model, source_id, options),
            }
            if row["source_debit_line_id"] in line_map:
                vals["debit_move_line_id"] = line_map[row["source_debit_line_id"]].id
            if row["source_credit_line_id"] in line_map:
                vals["credit_move_line_id"] = line_map[row["source_credit_line_id"]].id
            if row["source_exchange_move_id"] in move_map:
                vals["exchange_move_id"] = move_map[row["source_exchange_move_id"]].id

            review = Review.search([
                ("rebuild_source_model", "=", source_model),
                ("rebuild_source_id", "=", source_id),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if review:
                review.write(vals)
            else:
                review = Review.create(vals)
            imported_reviews |= review
            if kind == "partial":
                seen_partial_ids.add(source_id)
            else:
                seen_full_ids.add(source_id)

        stale_partial_reviews = Review.search([
            ("rebuild_source_model", "=", "account.partial.reconcile"),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("accounting_effect", "=", "review_only_cross_boundary"),
            ("rebuild_source_id", "not in", list(seen_partial_ids) or [0]),
        ])
        stale_full_reviews = Review.search([
            ("rebuild_source_model", "=", "account.full.reconcile"),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("accounting_effect", "=", "review_only_cross_boundary"),
            ("rebuild_source_id", "not in", list(seen_full_ids) or [0]),
        ])
        (stale_partial_reviews | stale_full_reviews).unlink()
        return {
            "source_partial_review_count": sum(1 for row in rows if row["reconciliation_kind"] == "partial"),
            "source_full_review_count": sum(1 for row in rows if row["reconciliation_kind"] == "full"),
            "source_reconciliation_review_count": len(rows),
            "imported_partial_review_count": len(imported_reviews.filtered(lambda review: review.reconciliation_kind == "partial")),
            "imported_full_review_count": len(imported_reviews.filtered(lambda review: review.reconciliation_kind == "full")),
            "imported_reconciliation_review_count": len(imported_reviews),
        }

    def _import_reconciliations(self, conn, options, companies):
        partial_rows = self._partial_reconcile_rows(conn, options)
        full_rows = self._full_reconcile_rows(conn, options)
        scope_summary = self._reconciliation_scope_summary(conn, options)
        review_stats = self._import_reconciliation_reviews(conn, options, companies)

        Line = self.env["account.move.line"].with_context(active_test=False)
        Move = self.env["account.move"].with_context(active_test=False)
        Partial = self.env["account.partial.reconcile"].with_context(
            tracking_disable=True,
            check_move_validity=False,
        )
        Full = self.env["account.full.reconcile"].with_context(tracking_disable=True)

        source_line_ids = {
            source_id
            for row in partial_rows
            for source_id in (row["debit_move_id"], row["credit_move_id"])
        }
        for row in full_rows:
            source_line_ids.update(row["line_ids"] or [])
        source_exchange_move_ids = {row["exchange_move_id"] for row in partial_rows if row["exchange_move_id"]}

        line_map = {
            line.rebuild_source_id: line
            for line in Line.search([
                ("rebuild_source_model", "=", "account.move.line"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", list(source_line_ids) or [0]),
            ])
        }
        move_map = {
            move.rebuild_source_id: move
            for move in Move.search([
                ("rebuild_source_model", "=", "account.move"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", list(source_exchange_move_ids) or [0]),
            ])
        }

        partial_map = {}
        for row in partial_rows:
            partial = Partial.search([
                ("rebuild_source_model", "=", "account.partial.reconcile"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if partial:
                partial_map[row["id"]] = partial
                continue
            debit_line = line_map.get(row["debit_move_id"])
            credit_line = line_map.get(row["credit_move_id"])
            if not debit_line or not credit_line:
                raise ValueError(
                    "Cannot import partial reconciliation %s because one endpoint was not imported." % row["id"]
                )
            vals = {
                "debit_move_id": debit_line.id,
                "credit_move_id": credit_line.id,
                "amount": self._amount(row["amount"]),
                "debit_amount_currency": self._amount(row["debit_amount_currency"]),
                "credit_amount_currency": self._amount(row["credit_amount_currency"]),
                "max_date": row["max_date"],
                "draft_caba_move_vals": row["draft_caba_move_vals"],
                "company_id": companies[row["company_id"]].id if row["company_id"] in companies else debit_line.company_id.id,
                **self._trace_values("account.partial.reconcile", row["id"], options),
            }
            if row["exchange_move_id"] in move_map:
                vals["exchange_move_id"] = move_map[row["exchange_move_id"]].id
            partial = Partial.create(vals)
            partial_map[row["id"]] = partial

        full_map = {}
        for row in full_rows:
            full = Full.search([
                ("rebuild_source_model", "=", "account.full.reconcile"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if full:
                full_map[row["id"]] = full
                continue
            target_line_ids = [line_map[source_line_id].id for source_line_id in row["line_ids"] or []]
            target_partial_ids = [partial_map[source_partial_id].id for source_partial_id in row["partial_ids"] or []]
            full = Full.create({
                "reconciled_line_ids": [Command.set(target_line_ids)],
                "partial_reconcile_ids": [Command.set(target_partial_ids)],
                **self._trace_values("account.full.reconcile", row["id"], options),
            })
            full_map[row["id"]] = full

        return {
            "source_partial_reconcile_count": len(partial_rows),
            "source_full_reconcile_count": len(full_rows),
            "imported_partial_reconcile_count": len(partial_map),
            "imported_full_reconcile_count": len(full_map),
            "scope_summary": {
                key: int(value or 0)
                for key, value in dict(scope_summary).items()
            },
            "reviews": review_stats,
        }

    def _import_assets(self, conn, options, companies, accounts, journals, currencies):
        rows = self._fetchall(
            conn,
            """
            SELECT asset.id, asset.company_id, asset.currency_id, asset.account_asset_id,
                   asset.asset_group_id, asset.account_depreciation_id,
                   asset.account_depreciation_expense_id, asset.journal_id,
                   asset.model_id, asset.parent_id, asset.name, asset.state,
                   asset.prorata_computation_type, asset.prorata_date,
                   asset.acquisition_date, asset.disposal_date,
                   asset.original_value, asset.book_value, asset.salvage_value,
                   asset.non_deductible_tax_value, asset.already_depreciated_amount_import,
                   asset.net_gain_on_sale, asset.active, asset.asset_paused_days,
                   asset_group.name AS asset_group_name,
                   COALESCE(array_remove(array_agg(move.id ORDER BY move.date, move.id), NULL), ARRAY[]::integer[]) AS source_depreciation_move_ids
            FROM account_asset asset
            LEFT JOIN account_asset_group asset_group ON asset_group.id = asset.asset_group_id
            LEFT JOIN account_move move ON move.asset_id = asset.id
            GROUP BY asset.id, asset_group.name
            ORDER BY asset.id
            """,
        )
        schedule_rows = self._fetchall(
            conn,
            """
            SELECT asset.id AS asset_id,
                   move.id AS move_id,
                   move.name,
                   move.state,
                   move.date,
                   move.ref,
                   round(sum(CASE
                       WHEN line.account_id = asset.account_depreciation_expense_id
                       THEN line.debit - line.credit
                       ELSE 0
                   END)::numeric, 2) AS expense_amount,
                   round(sum(CASE
                       WHEN line.account_id = asset.account_depreciation_id
                       THEN line.credit - line.debit
                       ELSE 0
                   END)::numeric, 2) AS depreciation_amount,
                   count(line.id) AS source_line_count
              FROM account_asset asset
              JOIN account_move move ON move.asset_id = asset.id
              LEFT JOIN account_move_line line ON line.move_id = move.id
             GROUP BY asset.id, move.id
             ORDER BY asset.id, move.date, move.id
            """,
        )
        schedule_by_asset = defaultdict(list)
        for schedule_row in schedule_rows:
            schedule_by_asset[schedule_row["asset_id"]].append(schedule_row)
        Asset = self.env["rebuild.account.asset"].with_context(active_test=False)
        ScheduleLine = self.env["rebuild.account.asset.depreciation.schedule.line"].with_context(active_test=False)
        Move = self.env["account.move"].with_context(active_test=False)
        imported_assets = self.env["rebuild.account.asset"]
        imported_depreciation_move_count = 0
        imported_schedule_line_count = 0
        for row in rows:
            source_move_ids = row["source_depreciation_move_ids"] or []
            target_moves = Move.search([
                ("rebuild_source_model", "=", "account.move"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", source_move_ids or [0]),
            ])
            imported_depreciation_move_count += len(target_moves)
            asset = Asset.search([
                ("rebuild_source_model", "=", "account_asset"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            vals = {
                "name": self._source_text(row["name"]) or f"Source asset {row['id']}",
                "company_id": companies[row["company_id"]].id,
                "currency_id": currencies[row["currency_id"]].id,
                "state": row["state"],
                "active": bool(row["active"]),
                "asset_group_name": row["asset_group_name"],
                "source_asset_group_id": row["asset_group_id"],
                "prorata_computation_type": row["prorata_computation_type"],
                "prorata_date": row["prorata_date"],
                "acquisition_date": row["acquisition_date"],
                "disposal_date": row["disposal_date"],
                "original_value": self._amount(row["original_value"]),
                "book_value": self._amount(row["book_value"]),
                "salvage_value": self._amount(row["salvage_value"]),
                "non_deductible_tax_value": self._amount(row["non_deductible_tax_value"]),
                "already_depreciated_amount_import": self._amount(row["already_depreciated_amount_import"]),
                "net_gain_on_sale": self._amount(row["net_gain_on_sale"]),
                "asset_paused_days": row["asset_paused_days"] or 0.0,
                "asset_account_id": accounts[row["account_asset_id"]].id if row["account_asset_id"] in accounts else False,
                "depreciation_account_id": accounts[row["account_depreciation_id"]].id if row["account_depreciation_id"] in accounts else False,
                "depreciation_expense_account_id": (
                    accounts[row["account_depreciation_expense_id"]].id
                    if row["account_depreciation_expense_id"] in accounts else False
                ),
                "journal_id": journals[row["journal_id"]].id if row["journal_id"] in journals else False,
                "source_model_id": row["model_id"],
                "source_parent_id": row["parent_id"],
                "source_depreciation_move_count": len(source_move_ids),
                "imported_depreciation_move_count": len(target_moves),
                "depreciation_move_ids": [Command.set(target_moves.ids)],
                **self._trace_values("account_asset", row["id"], options),
            }
            if len(target_moves) != len(source_move_ids):
                vals["rebuild_import_note"] = (
                    "Source asset depreciation moves are not all present in this benchmark replay slice; "
                    "they are mostly current-period or future draft depreciation entries."
                )
            if asset:
                asset.write(vals)
            else:
                asset = Asset.create(vals)
            imported_assets |= asset
            target_moves_by_source_id = {
                move.rebuild_source_id: move
                for move in target_moves
            }
            seen_schedule_source_ids = set()
            accumulated_depreciation = 0.0
            for schedule_row in schedule_by_asset.get(row["id"], []):
                source_move_id = schedule_row["move_id"]
                target_move = target_moves_by_source_id.get(source_move_id)
                expense_amount = self._amount(schedule_row["expense_amount"])
                depreciation_amount = self._amount(schedule_row["depreciation_amount"])
                accumulated_depreciation = round(accumulated_depreciation + depreciation_amount, 2)
                representation_status = "source_not_replayed"
                if target_move:
                    representation_status = "imported_posted_entry"
                elif schedule_row["state"] == "draft":
                    representation_status = "source_draft_forecast"
                schedule_vals = {
                    "asset_id": asset.id,
                    "company_id": asset.company_id.id,
                    "currency_id": asset.currency_id.id,
                    "imported_move_id": target_move.id if target_move else False,
                    "source_asset_id": row["id"],
                    "source_move_id": source_move_id,
                    "source_move_name": schedule_row["name"],
                    "source_move_state": schedule_row["state"],
                    "depreciation_date": schedule_row["date"],
                    "move_ref": schedule_row["ref"],
                    "expense_amount": expense_amount,
                    "depreciation_amount": depreciation_amount,
                    "accumulated_depreciation_amount": accumulated_depreciation,
                    "net_book_value_after_line": round(self._amount(row["original_value"]) - accumulated_depreciation, 2),
                    "source_line_count": schedule_row["source_line_count"],
                    "representation_status": representation_status,
                    **self._trace_values("account_move", source_move_id, options),
                }
                existing_schedule = ScheduleLine.search([
                    ("asset_id", "=", asset.id),
                    ("rebuild_source_model", "=", "account_move"),
                    ("rebuild_source_id", "=", source_move_id),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ], limit=1)
                if existing_schedule:
                    existing_schedule.write(schedule_vals)
                else:
                    ScheduleLine.create(schedule_vals)
                seen_schedule_source_ids.add(source_move_id)
                imported_schedule_line_count += 1
            stale_schedule_lines = ScheduleLine.search([
                ("asset_id", "=", asset.id),
                ("rebuild_source_model", "=", "account_move"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "not in", list(seen_schedule_source_ids) or [0]),
            ])
            stale_schedule_lines.unlink()
        return {
            "asset_count": len(rows),
            "imported_asset_count": len(imported_assets),
            "source_depreciation_move_count": sum(len(row["source_depreciation_move_ids"] or []) for row in rows),
            "imported_depreciation_move_count": imported_depreciation_move_count,
            "imported_depreciation_schedule_line_count": imported_schedule_line_count,
        }

    def _deferred_schedule_rows(self, conn, options):
        if not self._source_table_exists(conn, "account_move_deferred_rel"):
            return []
        return self._fetchall(
            conn,
            """
            WITH original_deferred_dates AS (
                SELECT move_id AS original_move_id,
                       min(deferred_start_date) AS deferred_start_date,
                       max(deferred_end_date) AS deferred_end_date,
                       count(*) FILTER (
                           WHERE deferred_start_date IS NOT NULL
                              OR deferred_end_date IS NOT NULL
                       )::integer AS source_original_deferred_line_count
                  FROM account_move_line
                 WHERE move_id IN (SELECT original_move_id FROM account_move_deferred_rel)
                 GROUP BY move_id
            ),
            line_summary AS (
                SELECT rel.original_move_id,
                       rel.deferred_move_id,
                       count(line.id)::integer AS source_line_count,
                       round(COALESCE(sum(CASE
                           WHEN COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '486%%'
                             OR COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '487%%'
                           THEN line.balance ELSE 0
                       END), 0)::numeric, 2) AS deferred_account_balance,
                       round(COALESCE(sum(CASE
                           WHEN COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '486%%'
                             OR COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '487%%'
                           THEN 0 ELSE line.balance
                       END), 0)::numeric, 2) AS counterpart_balance,
                       string_agg(DISTINCT
                           COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text),
                           ', ' ORDER BY COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text)
                       ) FILTER (
                           WHERE COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '486%%'
                              OR COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '487%%'
                       ) AS deferred_account_codes,
                       string_agg(DISTINCT
                           COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text),
                           ', ' ORDER BY COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text)
                       ) FILTER (
                           WHERE COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '486%%'
                              OR COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '487%%'
                       ) AS deferred_account_names,
                       string_agg(DISTINCT
                           COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text),
                           ', ' ORDER BY COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text)
                       ) FILTER (
                           WHERE NOT (
                               COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '486%%'
                               OR COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '487%%'
                           )
                       ) AS counterpart_account_codes,
                       string_agg(DISTINCT
                           COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text),
                           ', ' ORDER BY COALESCE(account.name->>'fr_FR', account.name->>'en_US', account.name::text)
                       ) FILTER (
                           WHERE NOT (
                               COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '486%%'
                               OR COALESCE(account.code_store->>deferred.company_id::text, account.code_store->>'1', account.code_store::text) LIKE '487%%'
                           )
                       ) AS counterpart_account_names
                  FROM account_move_deferred_rel rel
                  JOIN account_move deferred ON deferred.id = rel.deferred_move_id
                  LEFT JOIN account_move_line line ON line.move_id = deferred.id
                  LEFT JOIN account_account account ON account.id = line.account_id
                 GROUP BY rel.original_move_id, rel.deferred_move_id
            )
            SELECT rel.original_move_id,
                   rel.deferred_move_id,
                   original.name AS original_name,
                   original.ref AS original_ref,
                   original.state AS original_state,
                   original.move_type AS original_move_type,
                   original.company_id,
                   original.partner_id,
                   original.currency_id,
                   original.journal_id AS original_journal_id,
                   original.date AS original_date,
                   deferred.name AS deferred_name,
                   deferred.ref AS deferred_ref,
                   deferred.state AS deferred_state,
                   deferred.move_type AS deferred_move_type,
                   deferred.journal_id AS deferred_journal_id,
                   deferred.date AS deferred_date,
                   dates.deferred_start_date,
                   dates.deferred_end_date,
                   COALESCE(dates.source_original_deferred_line_count, 0)::integer AS source_original_deferred_line_count,
                   COALESCE(summary.source_line_count, 0)::integer AS source_line_count,
                   COALESCE(summary.deferred_account_balance, 0) AS deferred_account_balance,
                   COALESCE(summary.counterpart_balance, 0) AS counterpart_balance,
                   COALESCE(summary.deferred_account_codes, '') AS deferred_account_codes,
                   COALESCE(summary.deferred_account_names, '') AS deferred_account_names,
                   COALESCE(summary.counterpart_account_codes, '') AS counterpart_account_codes,
                   COALESCE(summary.counterpart_account_names, '') AS counterpart_account_names
              FROM account_move_deferred_rel rel
              JOIN account_move original ON original.id = rel.original_move_id
              JOIN account_move deferred ON deferred.id = rel.deferred_move_id
              LEFT JOIN original_deferred_dates dates ON dates.original_move_id = rel.original_move_id
              LEFT JOIN line_summary summary
                     ON summary.original_move_id = rel.original_move_id
                    AND summary.deferred_move_id = rel.deferred_move_id
             WHERE original.company_id = ANY(%(source_company_ids)s)
               AND original.date >= %(date_from)s
             ORDER BY original.date, rel.original_move_id, deferred.date, rel.deferred_move_id
            """,
            options,
        )

    @staticmethod
    def _deferred_schedule_type(row):
        codes = row["deferred_account_codes"] or ""
        if any(code.strip().startswith("486") for code in codes.split(",")):
            return "expense"
        if any(code.strip().startswith("487") for code in codes.split(",")):
            return "revenue"
        if row["original_move_type"] in {"in_invoice", "in_refund", "in_receipt"}:
            return "expense"
        if row["original_move_type"] in {"out_invoice", "out_refund", "out_receipt"}:
            return "revenue"
        return "unknown"

    @staticmethod
    def _deferred_schedule_phase(schedule_type, deferred_account_balance):
        if schedule_type == "expense":
            if deferred_account_balance > 0:
                return "initial_deferral"
            if deferred_account_balance < 0:
                return "recognition"
        if schedule_type == "revenue":
            if deferred_account_balance < 0:
                return "initial_deferral"
            if deferred_account_balance > 0:
                return "recognition"
        return "unknown"

    def _import_deferred_schedules(self, conn, options, companies, partners, journals, currencies):
        rows = self._deferred_schedule_rows(conn, options)
        DeferredLine = self.env["rebuild.account.deferred.schedule.line"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        Move = self.env["account.move"].with_context(active_test=False)
        MoveReview = self.env["rebuild.account.move.review"].with_context(active_test=False)
        source_move_ids = {
            row["original_move_id"]
            for row in rows
        } | {
            row["deferred_move_id"]
            for row in rows
        }
        move_map = {
            move.rebuild_source_id: move
            for move in Move.search([
                ("rebuild_source_model", "=", "account.move"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", list(source_move_ids) or [0]),
            ])
        }
        move_review_map = {
            review.rebuild_source_id: review
            for review in MoveReview.search([
                ("rebuild_source_model", "=", "account.move"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", list(source_move_ids) or [0]),
            ])
        }
        imported_lines = self.env["rebuild.account.deferred.schedule.line"]
        seen_source_ids = set()
        representation_counts = defaultdict(int)
        schedule_type_counts = defaultdict(int)
        for row in rows:
            schedule_type = self._deferred_schedule_type(row)
            deferred_account_balance = self._amount(row["deferred_account_balance"])
            schedule_phase = self._deferred_schedule_phase(schedule_type, deferred_account_balance)
            original_move = move_map.get(row["original_move_id"])
            deferred_move = move_map.get(row["deferred_move_id"])
            deferred_review = move_review_map.get(row["deferred_move_id"])
            if deferred_move:
                representation_status = "imported_posted_entry"
            elif row["deferred_state"] != "posted":
                representation_status = "source_draft_forecast"
            else:
                representation_status = "source_not_replayed"
            review_status = "represented"
            if not original_move or representation_status == "source_not_replayed":
                review_status = "review_required"
            vals = {
                "name": "%s -> %s" % (
                    row["original_name"] or f"Source move {row['original_move_id']}",
                    row["deferred_name"] or f"Source move {row['deferred_move_id']}",
                ),
                "schedule_type": schedule_type,
                "schedule_phase": schedule_phase,
                "representation_status": representation_status,
                "review_status": review_status,
                "company_id": companies[row["company_id"]].id,
                "source_company_id": row["company_id"],
                "currency_id": currencies[row["currency_id"]].id,
                "journal_id": journals[row["deferred_journal_id"]].id if row["deferred_journal_id"] in journals else False,
                "partner_id": partners[row["partner_id"]].id if row["partner_id"] in partners else False,
                "original_move_id": original_move.id if original_move else False,
                "deferred_move_id": deferred_move.id if deferred_move else False,
                "deferred_move_review_id": deferred_review.id if deferred_review else False,
                "original_move_imported": bool(original_move),
                "deferred_move_imported": bool(deferred_move),
                "source_original_move_id": row["original_move_id"],
                "source_deferred_move_id": row["deferred_move_id"],
                "source_original_name": row["original_name"],
                "source_deferred_name": row["deferred_name"],
                "source_original_state": row["original_state"],
                "source_deferred_state": row["deferred_state"],
                "source_original_move_type": row["original_move_type"],
                "source_deferred_move_type": row["deferred_move_type"],
                "original_date": row["original_date"],
                "deferred_date": row["deferred_date"],
                "deferred_start_date": row["deferred_start_date"],
                "deferred_end_date": row["deferred_end_date"],
                "deferred_account_code": row["deferred_account_codes"],
                "deferred_account_name": row["deferred_account_names"],
                "counterpart_account_codes": row["counterpart_account_codes"],
                "counterpart_account_names": row["counterpart_account_names"],
                "source_line_count": row["source_line_count"],
                "source_original_deferred_line_count": row["source_original_deferred_line_count"],
                "amount": abs(deferred_account_balance),
                "deferred_account_balance": deferred_account_balance,
                "counterpart_balance": self._amount(row["counterpart_balance"]),
                "note": (
                    "Source account_move_deferred_rel imported as a review/report schedule line. "
                    "Posted deferred moves already present in the ledger are linked; source draft "
                    "forecast entries are linked to workflow review records where available."
                ),
                **self._trace_values("account_move_deferred_rel", row["deferred_move_id"], options),
            }
            existing = DeferredLine.search([
                ("rebuild_source_model", "=", "account_move_deferred_rel"),
                ("rebuild_source_id", "=", row["deferred_move_id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if existing:
                existing.write(vals)
                line = existing
            else:
                line = DeferredLine.create(vals)
            imported_lines |= line
            seen_source_ids.add(row["deferred_move_id"])
            representation_counts[representation_status] += 1
            schedule_type_counts[schedule_type] += 1

        stale_lines = DeferredLine.search([
            ("rebuild_source_model", "=", "account_move_deferred_rel"),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("rebuild_source_id", "not in", list(seen_source_ids) or [0]),
        ])
        stale_lines.unlink()
        return {
            "source_deferred_schedule_line_count": len(rows),
            "imported_deferred_schedule_line_count": len(imported_lines),
            "expense_schedule_line_count": schedule_type_counts.get("expense", 0),
            "revenue_schedule_line_count": schedule_type_counts.get("revenue", 0),
            "unknown_schedule_line_count": schedule_type_counts.get("unknown", 0),
            "imported_posted_entry_count": representation_counts.get("imported_posted_entry", 0),
            "source_draft_forecast_count": representation_counts.get("source_draft_forecast", 0),
            "source_not_replayed_count": representation_counts.get("source_not_replayed", 0),
        }

    def run_exact_ledger_replay_from_source(self, options):
        self.ensure_one()
        options = {
            "source_database": "odoo_online_source_saas_19_2",
            "source_snapshot_id": "source-unknown",
            "source_dump_sha256": "",
            "source_version": "Odoo Online Enterprise saas~19.2",
            "target_database": self.env.cr.dbname,
            "date_from": "2024-01-10",
            "date_to": "2025-09-30",
            "source_company_ids": [1, 8],
            **(options or {}),
        }
        options["source_company_ids"] = self._source_company_ids(options)
        self.write({
            "status": "running",
            "mode": "exact_ledger_replay",
            "source_database": options["source_database"],
            "source_dump_sha256": options.get("source_dump_sha256"),
            "source_snapshot_id": options["source_snapshot_id"],
            "source_version": options.get("source_version"),
            "target_database": options["target_database"],
        })
        conn = self._source_connection(options)
        stats = {}
        warnings = []
        try:
            currencies = self._currency_map(conn)
            countries = self._country_map(conn)
            companies, company_rows = self._company_map(conn, options, countries)
            source_report_stats = self._import_source_reports(conn, options)
            source_report_structure_stats = self._import_source_report_structure(conn, options)
            source_report_stats["structure"] = source_report_structure_stats
            partners = self._partner_map(conn, options)
            accounts, account_ids_to_archive_after_post = self._account_map(conn, options, companies, currencies)
            tax_tags = self._tax_tag_map(conn, options, countries)
            tax_groups = self._tax_group_map(conn, options, companies, accounts, countries)
            taxes, tax_repartition_lines, tax_stats = self._tax_map(conn, options, companies, accounts, tax_groups, tax_tags, countries)
            journals = self._journal_map(conn, options, companies, accounts, currencies)
            analytic_plans = self._analytic_plan_map(conn, options)
            analytic_accounts = self._analytic_account_map(conn, options, companies, partners, analytic_plans)
            move_rows = self._move_rows(conn, options)
            line_rows_by_move = self._line_rows_by_move(conn, options)
            Move = self.env["account.move"].with_context(
                check_move_validity=False,
                tracking_disable=True,
                mail_create_nolog=True,
                skip_account_move_synchronization=True,
                skip_invoice_sync=True,
            )
            imported_moves = self.env["account.move"]
            imported_line_count = 0
            skipped_non_account_lines = [
                line
                for lines in line_rows_by_move.values()
                for line in lines
                if not line["account_id"]
            ]
            skipped_non_account_line_count = len(skipped_non_account_lines)
            skipped_non_account_line_examples = [
                {
                    "source_move_id": line["move_id"],
                    "source_line_id": line["id"],
                    "display_type": line["display_type"],
                    "name": line["name"],
                }
                for line in skipped_non_account_lines[:20]
            ]
            for move_row in move_rows:
                existing = Move.search([
                    ("rebuild_source_model", "=", "account.move"),
                    ("rebuild_source_id", "=", move_row["id"]),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ], limit=1)
                if existing:
                    imported_moves |= existing
                    imported_line_count += len(existing.line_ids)
                    continue
                line_commands = []
                for line in line_rows_by_move[move_row["id"]]:
                    if not line["account_id"]:
                        continue
                    line_vals = {
                        "sequence": line["sequence"] or 10,
                        "account_id": accounts[line["account_id"]].id,
                        "name": line["name"] or "/",
                        "ref": line["ref"],
                        "partner_id": partners[line["partner_id"]].id if line["partner_id"] in partners else False,
                        "date_maturity": line["date_maturity"],
                        "debit": self._amount(line["debit"]),
                        "credit": self._amount(line["credit"]),
                        "amount_currency": self._amount(line["amount_currency"]),
                        "tax_base_amount": self._amount(line["tax_base_amount"]),
                        "display_type": line["display_type"] or "product",
                        **self._trace_values("account.move.line", line["id"], options),
                    }
                    if line["currency_id"] in currencies:
                        line_vals["currency_id"] = currencies[line["currency_id"]].id
                    if line["tax_ids"]:
                        line_vals["tax_ids"] = [Command.set([
                            taxes[source_tax_id].id
                            for source_tax_id in line["tax_ids"]
                            if source_tax_id in taxes
                        ])]
                    if line["tax_tag_ids"]:
                        line_vals["tax_tag_ids"] = [Command.set([
                            tax_tags[source_tag_id].id
                            for source_tag_id in line["tax_tag_ids"]
                            if source_tag_id in tax_tags
                        ])]
                    if line["tax_line_id"] in taxes:
                        line_vals["tax_line_id"] = taxes[line["tax_line_id"]].id
                    if line["tax_repartition_line_id"] in tax_repartition_lines:
                        line_vals["tax_repartition_line_id"] = tax_repartition_lines[line["tax_repartition_line_id"]].id
                    if line["tax_group_id"] in tax_groups:
                        line_vals["tax_group_id"] = tax_groups[line["tax_group_id"]].id
                    line_commands.append(Command.create(line_vals))
                move_vals = {
                    "journal_id": journals[move_row["journal_id"]].id,
                    "company_id": companies[move_row["company_id"]].id,
                    "date": move_row["date"],
                    "name": move_row["name"] or "/",
                    "ref": move_row["ref"],
                    "move_type": "entry",
                    "rebuild_source_move_type": move_row["move_type"],
                    "partner_id": partners[move_row["partner_id"]].id if move_row["partner_id"] in partners else False,
                    "payment_reference": move_row["payment_reference"],
                    "line_ids": line_commands,
                    **self._trace_values("account.move", move_row["id"], options),
                }
                if move_row["currency_id"] in currencies:
                    move_vals["currency_id"] = currencies[move_row["currency_id"]].id
                move = Move.create(move_vals)
                move.action_post()
                if move.name != (move_row["name"] or "/"):
                    warnings.append(f"Move {move_row['id']} imported with name {move.name} instead of {move_row['name']}.")
                imported_moves |= move
                imported_line_count += len(line_commands)

            for source_account_id in account_ids_to_archive_after_post:
                accounts[source_account_id].active = False

            move_review_stats = self._import_move_reviews(conn, options, companies, partners, journals, currencies)
            move_line_review_stats = self._import_move_line_reviews(
                conn, options, companies, partners, accounts, journals, currencies
            )
            document_regeneration_stats = self._sync_document_regeneration_cases(options)
            reconciliation_stats = self._import_reconciliations(conn, options, companies)
            payment_stats = self._import_payments(conn, options, companies, partners, accounts, journals, currencies)
            deferred_schedule_stats = self._import_deferred_schedules(
                conn, options, companies, partners, journals, currencies
            )
            bank_statement_line_stats = self._import_bank_statement_lines(
                conn, options, companies, partners, journals, currencies
            )
            asset_stats = self._import_assets(conn, options, companies, accounts, journals, currencies)
            analytic_stats = self._import_analytic_lines(
                conn, options, companies, partners, accounts, analytic_plans, analytic_accounts
            )
            attachment_stats = self._import_attachments(conn, options, companies)
            external_report_values = self._seed_benchmark_external_report_values(companies)

            for row in company_rows:
                company = companies[row["id"]]
                lock_vals = {}
                for field_name in ("fiscalyear_lock_date", "tax_lock_date", "sale_lock_date", "purchase_lock_date", "hard_lock_date"):
                    if row[field_name]:
                        lock_vals[field_name] = row[field_name]
                if lock_vals:
                    company.write(lock_vals)

            missing_domains = [
                ("User-facing report suite awaits final report-variant and accountant acceptance", "P0"),
            ]
            for name, severity in missing_domains:
                self._upsert_discrepancy({
                    "name": name,
                    "severity": severity,
                    "classification": "legal_or_accounting_uncertainty",
                    "status": "open",
                    "period_key": f"{options['date_from']}:{options['date_to']}",
                    "source_model": "account.report",
                    "source_value": str(source_report_stats["active_source_report_count"]),
                    "target_model": "rebuild.account.source.report",
                    "target_value": str(source_report_stats["partial_target_equivalent_count"]),
                    "difference": str(source_report_stats["missing_target_equivalent_count"]),
                    "evidence": json.dumps(source_report_stats, ensure_ascii=False, sort_keys=True),
                    "accounting_impact": (
                        "This import run preserves every active source report catalogue record and assigns "
                        "a target equivalent. The report evidence stage now exercises exports, preview "
                        "drill-down and explicit 2024/legal-form scope handling, but final source formula "
                        "comparison and accountant acceptance are still required before the report suite can "
                        "be treated as final parity."
                    ),
                    "recommendation": (
                        "Review the Source Report Catalogue Level 4 evidence, compare source formulas, hierarchy, "
                        "drill-down membership and exports for every mandatory report, then record accountant "
                        "acceptance or approved differences. Do not close this gate from technical evidence alone."
                    ),
                })
            self.env["rebuild.account.discrepancy"].search([
                ("name", "=", "User-facing report suite is not yet complete account.report parity"),
                ("status", "!=", "resolved"),
                ("source_model", "=", "account.report"),
            ]).write({
                "import_run_id": self.id,
                "status": "resolved",
                "decision": "Superseded by the report-suite acceptance discrepancy with current post-export evidence wording.",
            })
            if move_review_stats["source_move_review_count"] != move_review_stats["imported_move_review_count"]:
                self._upsert_discrepancy({
                    "name": "Non-posted source move review records are incomplete",
                    "severity": "P1",
                    "classification": "transfer_defect",
                    "status": "open",
                    "period_key": f"{options['date_from']}:open",
                    "source_value": str(move_review_stats["source_move_review_count"]),
                    "target_value": str(move_review_stats["imported_move_review_count"]),
                    "accounting_impact": "Draft/cancelled/future source workflow records are not fully represented for review.",
                    "recommendation": "Fix the move review import before using the target for operational accounting review.",
                })
            else:
                all_regeneration_candidates_validated = (
                    document_regeneration_stats["candidate_ready_count"]
                    == document_regeneration_stats["validated_count"]
                    and not document_regeneration_stats["mismatch_count"]
                    and not document_regeneration_stats["generation_status_counts"].get("not_generated")
                )
                document_regeneration_discrepancy = "Non-posted source moves have regeneration cases but native generation remains incomplete"
                if all_regeneration_candidates_validated and not document_regeneration_stats["blocked_count"]:
                    self.env["rebuild.account.discrepancy"].search([
                        ("name", "=", document_regeneration_discrepancy),
                        ("status", "!=", "resolved"),
                    ]).write({
                        "import_run_id": self.id,
                        "status": "resolved",
                        "classification": "period_or_scope_difference",
                        "severity": "P2",
                        "source_value": str(move_review_stats["source_move_review_count"]),
                        "target_value": (
                            f"{document_regeneration_stats['validated_count']} candidate drafts validated; "
                            f"{document_regeneration_stats['review_only_count']} review-only cases marked not applicable"
                        ),
                        "difference": "No candidate-ready document-regeneration case remains unvalidated or blocked.",
                        "evidence": json.dumps({
                            "move_reviews": move_review_stats,
                            "document_regeneration_cases": document_regeneration_stats,
                        }, ensure_ascii=False, sort_keys=True),
                        "accounting_impact": (
                            "Every candidate-ready non-posted source move is represented by a native target draft with matching preserved source line counts and debit/credit totals. "
                            "Cancelled or empty non-posted records are retained as review evidence and explicitly marked not applicable for native draft generation."
                        ),
                        "legal_or_tax_impact": "No posted closed-year ledger effect is introduced by generated drafts.",
                        "recommendation": "Keep review-only cases visible in the document-regeneration workbench; no technical generation blocker remains for the current source perimeter.",
                    })
                else:
                    self._upsert_discrepancy({
                        "name": document_regeneration_discrepancy,
                        "severity": "P2" if all_regeneration_candidates_validated else "P1",
                        "classification": "period_or_scope_difference" if all_regeneration_candidates_validated else "missing_capability",
                        "status": "open",
                        "period_key": f"{options['date_from']}:open",
                        "source_value": str(move_review_stats["source_move_review_count"]),
                        "target_value": (
                            (
                                f"{document_regeneration_stats['validated_count']} candidate drafts validated; "
                                f"{document_regeneration_stats['blocked_count']} blocked and "
                                f"{document_regeneration_stats['review_only_count']} review-only not applicable of "
                                f"{document_regeneration_stats['document_regeneration_case_count']}"
                            )
                            if all_regeneration_candidates_validated
                            else (
                                f"{document_regeneration_stats['validated_count']} validated of "
                                f"{document_regeneration_stats['document_regeneration_case_count']}"
                            )
                        ),
                        "difference": (
                            f"{document_regeneration_stats['blocked_count']} cancelled or line-incomplete "
                            "cases remain outside native draft generation"
                            if all_regeneration_candidates_validated
                            else (
                                f"{document_regeneration_stats['document_regeneration_case_count'] - document_regeneration_stats['validated_count'] - document_regeneration_stats['review_only_count']} "
                                "cases are not validated as native generated drafts or classified as not applicable"
                            )
                        ),
                        "evidence": json.dumps({
                            "move_reviews": move_review_stats,
                            "document_regeneration_cases": document_regeneration_stats,
                        }, ensure_ascii=False, sort_keys=True),
                        "accounting_impact": (
                            "All candidate-ready non-posted source moves are represented by native target drafts with matching preserved source line counts and debit/credit totals. Remaining blocked cases require review-only acceptance or an explicit source-line/cancelled-record scope decision."
                            if all_regeneration_candidates_validated
                            else (
                                "Non-posted source records are visible, traceable and classified for regeneration, "
                                "but native draft generation and generated-line comparison are not complete for every ready case."
                            )
                        ),
                        "legal_or_tax_impact": (
                            "No posted closed-year ledger effect is introduced by generated drafts. Blocked cancelled or source-line-incomplete records remain a workflow-review risk until accepted or explicitly regenerated."
                            if all_regeneration_candidates_validated
                            else False
                        ),
                        "recommendation": (
                            f"Review the {document_regeneration_stats['blocked_count']} blocked document-regeneration cases and record whether they remain review-only or require a separate scenario."
                            if all_regeneration_candidates_validated
                            else "Use the document-regeneration case workbench to select supported draft records, then implement isolated native draft generation and generated-line comparison outside the exact replay baseline."
                        ),
                    })
                self.env["rebuild.account.discrepancy"].search([
                    ("name", "in", [
                        "Non-posted source moves are review-only, not regenerated as target documents",
                        "Non-posted source moves have regeneration cases but no generated target drafts",
                    ]),
                    ("status", "!=", "resolved"),
                    ("classification", "=", "missing_capability"),
                ]).write({
                    "import_run_id": self.id,
                    "status": "resolved",
                    "decision": "Superseded by document-regeneration case workbench discrepancy with native generation progress counts.",
                })
            if reconciliation_stats["scope_summary"].get("partials_cross_boundary") or reconciliation_stats["scope_summary"].get("fulls_cross_boundary"):
                self._upsert_discrepancy({
                    "name": "Cross-boundary reconciliations are review-only until missing endpoints are in scope",
                    "severity": "P1",
                    "classification": "period_or_scope_difference",
                    "status": "open",
                    "period_key": f"{options['date_from']}:{options['date_to']}",
                    "source_value": str(
                        reconciliation_stats["reviews"]["source_reconciliation_review_count"]
                    ),
                    "target_value": str(
                        reconciliation_stats["reviews"]["imported_reconciliation_review_count"]
                    ),
                    "evidence": json.dumps(reconciliation_stats["reviews"], ensure_ascii=False, sort_keys=True),
                    "accounting_impact": (
                        "Some reconciliation relationships touch benchmark lines and source lines outside the "
                        "selected posted replay scope. The cross-boundary source relationships are now visible "
                        "as review records, but they are not applied to the target reconciliation graph."
                    ),
                    "recommendation": "Classify each missing reconciliation endpoint and import or explicitly exclude draft/future records before declaring full reconciliation parity.",
                })
            if (
                move_line_review_stats["source_move_line_review_count"]
                != move_line_review_stats["imported_move_line_review_count"]
            ):
                self._upsert_discrepancy({
                    "name": "Source non-account display lines are not fully represented",
                    "severity": "P1",
                    "classification": "transfer_defect",
                    "status": "open",
                    "period_key": f"{options['date_from']}:{options['date_to']}",
                    "source_value": str(move_line_review_stats["source_move_line_review_count"]),
                    "target_value": str(move_line_review_stats["imported_move_line_review_count"]),
                    "evidence": json.dumps(move_line_review_stats, ensure_ascii=False, sort_keys=True),
                    "accounting_impact": "Source display/note lines with no account are not fully traceable in the target review layer.",
                    "recommendation": "Fix the move-line review import before accepting document-context parity.",
                })
            if deferred_schedule_stats["source_not_replayed_count"]:
                self._upsert_discrepancy({
                    "name": "Posted source deferred schedule entries are not fully represented",
                    "severity": "P1",
                    "classification": "transfer_defect",
                    "status": "open",
                    "period_key": f"{options['date_from']}:open",
                    "source_value": str(deferred_schedule_stats["source_deferred_schedule_line_count"]),
                    "target_value": str(deferred_schedule_stats["imported_deferred_schedule_line_count"]),
                    "evidence": json.dumps(deferred_schedule_stats, ensure_ascii=False, sort_keys=True),
                    "accounting_impact": (
                        "At least one posted source deferred schedule entry is not linked to an imported "
                        "target journal entry or explicit source draft review record."
                    ),
                    "recommendation": "Expand the replay scope or repair the deferred schedule mapping before declaring deferred report parity.",
                })

            vat_deductible_line = self.env["rebuild.account.french.tax.package.line"].search([
                ("source_company_id", "=", 1),
                ("period_key", "=", "USL benchmark 2024-01-10 to 2025-09-30"),
                ("field_code", "=", "3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660"),
            ], limit=1)
            vat_external_value = self.env["rebuild.account.external.report.value"].search([
                ("company_id", "=", vat_deductible_line.company_id.id if vat_deductible_line else False),
                ("period_key", "=", "USL benchmark 2024-01-10 to 2025-09-30"),
                ("form_code", "=", "3517-S-SD"),
                ("field_code", "=", "3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660"),
                ("value_kind", "=", "benchmark_acceptance_anchor"),
                ("active", "=", True),
            ], limit=1)
            if vat_deductible_line and vat_external_value and round(vat_deductible_line.amount, 2) != round(vat_external_value.amount, 2):
                vat_difference = vat_deductible_line.amount - vat_external_value.amount
                discrepancy = self._upsert_discrepancy({
                    "name": "Deductible VAT goods/services ledger amount differs from benchmark tax package",
                    "severity": "P1",
                    "classification": "external_value_difference",
                    "status": "open",
                    "company_id": vat_deductible_line.company_id.id,
                    "period_key": vat_deductible_line.period_key,
                    "source_model": vat_external_value._name,
                    "source_id": vat_external_value.id,
                    "source_value": f"{vat_external_value.amount:.2f}",
                    "target_model": vat_deductible_line._name,
                    "target_id": vat_deductible_line.id,
                    "target_value": f"{vat_deductible_line.amount:.2f}",
                    "difference": f"{vat_difference:.2f}",
                    "accounting_impact": (
                        "The imported ledger is not altered. Account 445660 carries a ledger-derived debit "
                        "total that differs from the explicit external benchmark tax-package value."
                    ),
                    "legal_or_tax_impact": (
                        "CA12/2033-D deductible VAT values cannot be accepted until the accountant confirms "
                        "whether the benchmark packet contains an external adjustment, scope difference or "
                        "declaration-specific treatment."
                    ),
                    "evidence": json.dumps({
                        "target_report_model": vat_deductible_line._name,
                        "target_report_id": vat_deductible_line.id,
                        "field_code": vat_deductible_line.field_code,
                        "source_formula": vat_deductible_line.source_formula,
                        "ledger_account_prefix": vat_deductible_line.drilldown_account_prefixes,
                        "external_value_id": vat_external_value.id,
                        "external_value_source_key": vat_external_value.source_key,
                        "external_value_review_status": vat_external_value.review_status,
                    }, ensure_ascii=False, sort_keys=True),
                    "likely_cause": "Declaration package may include externally supplied values, VAT report scope differences or accountant adjustments not present as source journal items.",
                    "recommendation": "Ask the accountant to reconcile account 445660 ledger detail against the external CA12/2033-D deductible VAT value and record whether this value is accepted, rejected or superseded.",
                    "owner": "accountant",
                })
                vat_external_value.discrepancy_id = discrepancy.id
                stale_vat_discrepancies = self.env["rebuild.account.discrepancy"].search([
                    ("name", "=", "Deductible VAT goods/services ledger amount differs from benchmark tax package"),
                    ("period_key", "=", vat_deductible_line.period_key),
                    ("company_id", "=", vat_deductible_line.company_id.id),
                    ("source_model", "=", "benchmark_tax_package"),
                    ("status", "in", ["open", "investigating"]),
                ])
                stale_vat_discrepancies.write({
                    "status": "resolved",
                    "decision": f"Superseded by external report value discrepancy {discrepancy.id} during idempotent import rerun.",
                })
            elif vat_deductible_line and vat_external_value:
                stale_vat_discrepancies = self.env["rebuild.account.discrepancy"].search([
                    ("name", "=", "Deductible VAT goods/services ledger amount differs from benchmark tax package"),
                    ("period_key", "=", vat_deductible_line.period_key),
                    ("company_id", "=", vat_deductible_line.company_id.id),
                    ("status", "in", ["open", "investigating"]),
                ])
                stale_vat_discrepancies.write({
                    "status": "resolved",
                    "decision": (
                        "Resolved by the imported source CA12 clearing entry: the tax-package amount "
                        f"{vat_deductible_line.amount:.2f} now matches the benchmark value "
                        f"{vat_external_value.amount:.2f}. Gross account 445660 turnover remains "
                        "traceable through the VAT report and tax-package evidence text."
                    ),
                })

            stats = {
                "date_from": options["date_from"],
                "date_to": options["date_to"],
                "source_company_ids": options["source_company_ids"],
                "source_move_count": len(move_rows),
                "source_move_line_count": sum(len(lines) for lines in line_rows_by_move.values()),
                "imported_move_count": len(imported_moves),
                "imported_move_line_count": imported_line_count,
                "skipped_non_account_line_count": skipped_non_account_line_count,
                "skipped_non_account_line_examples": skipped_non_account_line_examples,
                "account_count": len(accounts),
                "journal_count": len(journals),
                "partner_count": len(partners),
                "company_count": len(companies),
                "tax_configuration": tax_stats,
                "reconciliations": reconciliation_stats,
                "payments": payment_stats,
                "move_reviews": move_review_stats,
                "move_line_reviews": move_line_review_stats,
                "document_regeneration_cases": document_regeneration_stats,
                "bank_statement_lines": bank_statement_line_stats,
                "assets": asset_stats,
                "analytics": analytic_stats,
                "attachments": attachment_stats,
                "external_report_values": {
                    "count": len(external_report_values),
                    "source_keys": sorted(set(external_report_values.mapped("source_key"))),
                },
                "source_reports": source_report_stats,
                "deferred_schedules": deferred_schedule_stats,
                "warnings": warnings,
            }
            self.write({
                "status": "partial",
                "finished_at": fields.Datetime.now(),
                "company_ids": [Command.set([company.id for company in companies.values()])],
                "imported_company_count": len(companies),
                "imported_account_count": len(accounts),
                "imported_journal_count": len(journals),
                "imported_partner_count": len(partners),
                "imported_move_count": len(imported_moves),
                "imported_move_line_count": imported_line_count,
                "imported_move_review_count": move_review_stats["imported_move_review_count"],
                "imported_move_line_review_count": move_line_review_stats["imported_move_line_review_count"],
                "document_regeneration_case_count": document_regeneration_stats["document_regeneration_case_count"],
                "document_regeneration_candidate_count": document_regeneration_stats["candidate_ready_count"],
                "document_regeneration_review_only_count": document_regeneration_stats["review_only_count"],
                "document_regeneration_blocked_count": document_regeneration_stats["blocked_count"],
                "imported_payment_count": payment_stats["imported_payment_count"],
                "imported_payment_review_count": payment_stats["no_entry_payment_review_count"],
                "imported_bank_statement_line_count": bank_statement_line_stats["imported_bank_statement_line_count"],
                "imported_analytic_line_count": analytic_stats["imported_analytic_line_count"],
                "imported_attachment_count": attachment_stats["imported_attachment_count"],
                "imported_reconciliation_count": (
                    reconciliation_stats["imported_partial_reconcile_count"]
                    + reconciliation_stats["imported_full_reconcile_count"]
                ),
                "imported_reconciliation_review_count": (
                    reconciliation_stats["reviews"]["imported_reconciliation_review_count"]
                ),
                "imported_source_report_count": source_report_stats["imported_source_report_count"],
                "imported_deferred_schedule_line_count": deferred_schedule_stats["imported_deferred_schedule_line_count"],
                "external_report_value_count": len(external_report_values),
                "warning_count": len(warnings),
                "discrepancy_count": len(self.discrepancy_ids),
                "statistics_json": stats,
                "notes": "Posted source accounting replay through the selected source snapshot date. Ledger entries, contained reconciliation graph, cross-boundary reconciliation review records, move-backed payment records, no-entry payment workflow review records, non-posted move workflow review records, document-regeneration workbench cases, non-account display-line review records, bank statement line links, source tax configuration, source report catalogue records, deferred expense/revenue schedule review lines, analytic accounts and analytic lines, asset register and scoped accounting attachments are imported; native generated draft documents for draft/cancelled/future operational records and complete account.report semantic parity remain open.",
            })
            return stats
        except Exception:
            self.write({
                "status": "failed",
                "finished_at": fields.Datetime.now(),
                "statistics_json": stats,
            })
            raise
        finally:
            conn.close()

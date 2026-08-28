import hashlib
import json
import os
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date

import psycopg2
import psycopg2.extras

from odoo import Command, _, fields, models
from odoo.addons.account.models.account_move import BYPASS_LOCK_CHECK
from odoo.exceptions import UserError, ValidationError


# The Online source contains six misleading account names.  Account codes and
# ledger relationships remain authoritative; these are explicit target chart
# translations, not report-only aliases.
USL_ACCOUNT_NAME_TRANSLATIONS = {
    "281540": {
        "en_US": "Depreciation of industrial equipment",
        "fr_FR": "Amortissements du matériel industriel",
    },
    "281830": {
        "en_US": "Depreciation of office and IT equipment",
        "fr_FR": "Amortissements du matériel de bureau et informatique",
    },
    "511100": {
        "en_US": "Platform transfers receivable — Etsy",
        "fr_FR": "Virements de plateformes à encaisser — Etsy",
    },
    "627100": {
        "en_US": "Bank charges",
        "fr_FR": "Frais bancaires",
    },
    "631200": {
        "en_US": "Apprenticeship tax",
        "fr_FR": "Taxe d’apprentissage",
    },
    "768000": {
        "en_US": "Other financial income",
        "fr_FR": "Autres produits financiers",
    },
}

# These source methods are supplied by Odoo Enterprise payment modules which
# are not installed in the Community distribution.  They may be classified as
# an intentional capability difference only while they are unused.  A payment
# or expense using one is a reconstruction blocker rather than a silent
# fallback to ``manual``.
ENTERPRISE_ONLY_PAYMENT_METHOD_CODES = frozenset({
    "batch_payment",
    "iso20022",
    "iso20022_us",
    "sepa_ct",
})


# These profiles translate verified legal documents and tax-return settings
# from the Online snapshot.  The SIREN is the durable company identity; source
# database IDs are reconstruction details and must not decide tax behavior.
FRENCH_DECLARATION_PROFILES_BY_SIREN = {
    "983982950": {
        "rebuild_legal_form": "sasu",
        "rebuild_corporate_tax_regime": "is",
        "rebuild_corporate_tax_projection_profile": "fr_sme_15_25",
        "rebuild_profit_tax_regime": "bic_simplified",
        "rebuild_first_fiscalyear_start": "2024-01-10",
        "rebuild_first_fiscalyear_end": "2025-09-30",
        "evidence": (
            "Confirmed Milestone 13 facts and the supplied 2025 BIC/RS/IS "
            "tax package: Unstatic Labs is a French SASU subject to IS, "
            "using the simplified BIC/IS package."
        ),
    },
    "106928831": {
        "rebuild_legal_form": "sasu",
        "rebuild_corporate_tax_regime": "is",
        "rebuild_corporate_tax_projection_profile": "fr_sme_15_25",
        "rebuild_profit_tax_regime": "bic_simplified",
        "rebuild_first_fiscalyear_start": "2026-06-01",
        "rebuild_first_fiscalyear_end": "2027-09-30",
        "evidence": (
            "The restored USL MEDIA Kbis identifies a French single-shareholder "
            "SAS, activity from 1 June 2026 and a first fiscal close on "
            "30 September 2027. The company is subject to IS; the simplified "
            "BIC/IS package and French SME projection remain reviewable at the "
            "annual 2065 preparation."
        ),
    },
}

# Odoo Online did not expose these governed document fields when the source
# snapshot was taken.  Keep the enrichment offline and keyed by SIREN so a
# reconstruction remains deterministic and cannot drift with a live registry
# response.  Evidence reviewed for Unstatic Labs: the 24 September 2025 Paris
# Kbis and updated statutes stored in the source dump.  Evidence reviewed for
# USL MEDIA: BODACC creation notice A202601301643 and the restored Kbis facts
# already governing its French declaration profile.  APE codes were
# cross-checked against the public INSEE-backed company register.
FRENCH_DOCUMENT_IDENTITIES_BY_SIREN = {
    "983982950": {
        "usl_document_legal_form": "SASU à capital variable",
        "usl_document_share_capital": 1_000.0,
        "usl_document_rcs_city": "Paris",
        "ape": "62.01Z",
    },
    "106928831": {
        "usl_document_legal_form": "SASU",
        "usl_document_share_capital": 1_000.0,
        "usl_document_rcs_city": "Paris",
        "ape": "74.20Z",
    },
}

VAT_REGIME_BY_SOURCE_RETURN_PERIODICITY = {
    "fiscalyear": "simplified",
    "monthly": "normal",
    "quarterly": "normal",
    "trimester": "normal",
}


class RebuildAccountImportRun(models.Model):
    _name = "rebuild.account.import.run"
    _description = "USL Accounting Import Run"
    _order = "started_at desc, id desc"

    name = fields.Char(required=True, default="Accounting import")
    mode = fields.Selection(
        [
            ("exact_ledger_replay", "Exact Ledger Replay"),
            ("native_engine_replay", "Native Engine Replay"),
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
    imported_currency_rate_count = fields.Integer(readonly=True)
    imported_account_count = fields.Integer(readonly=True)
    imported_journal_count = fields.Integer(readonly=True)
    imported_partner_count = fields.Integer(readonly=True)
    imported_move_count = fields.Integer(readonly=True)
    imported_move_line_count = fields.Integer(readonly=True)
    imported_non_posted_move_count = fields.Integer(readonly=True)
    imported_context_line_count = fields.Integer(readonly=True)
    imported_payment_count = fields.Integer(readonly=True)
    imported_no_entry_payment_count = fields.Integer(readonly=True)
    imported_bank_statement_line_count = fields.Integer(readonly=True)
    imported_analytic_line_count = fields.Integer(readonly=True)
    imported_attachment_count = fields.Integer(readonly=True)
    imported_reconciliation_count = fields.Integer(readonly=True)
    imported_source_report_count = fields.Integer(readonly=True)
    imported_deferred_schedule_line_count = fields.Integer(readonly=True)
    external_report_value_count = fields.Integer(readonly=True)
    warning_count = fields.Integer(readonly=True)
    discrepancy_count = fields.Integer(readonly=True)
    statistics_json = fields.Json(copy=False)
    notes = fields.Text()

    discrepancy_ids = fields.One2many("rebuild.account.discrepancy", "import_run_id")

    _EXACT_REPLAY_BATCH_SIZE = 250
    _RELATION_BATCH_SIZE = 500

    @staticmethod
    def _batched(values, size):
        """Yield bounded lists without changing their deterministic order."""
        for index in range(0, len(values), size):
            yield values[index:index + size]

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
            "period_key": "Fiscal year 2024-01-10 to 2025-09-30",
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

    @staticmethod
    def _source_trace_models(model_name, options):
        aliases = (options.get("source_trace_aliases") or {}).get(model_name, [])
        return list(dict.fromkeys([model_name, *aliases]))

    def _source_trace_record_map(self, target_model, source_ids, options):
        source_ids = list(source_ids or [])
        if not source_ids:
            return {}
        trace_models = self._source_trace_models(target_model, options)
        # ``ir.attachment`` hides binary-field attachments from ordinary
        # searches unless this context is explicit.  Source identity lookup
        # must cover those rows too (for example ``ubl_cii_xml_file``), or a
        # repeated import attempts to recreate an existing traced attachment.
        records = self.env[target_model].with_context(
            active_test=False,
            skip_res_field_check=True,
        ).search([
            ("rebuild_source_model", "in", trace_models),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("rebuild_source_id", "in", source_ids),
        ])
        priority = {
            trace_model: index
            for index, trace_model in enumerate(trace_models)
        }
        result = {}
        for record in records.sorted(
            key=lambda item: (
                priority.get(item.rebuild_source_model, len(priority)),
                item.id,
            ),
        ):
            source_id = record.rebuild_source_id
            if source_id in result:
                raise ValueError(
                    "Source %s %s has multiple target representations: %s and %s."
                    % (
                        target_model,
                        source_id,
                        result[source_id].display_name,
                        record.display_name,
                    ),
                )
            result[source_id] = record
        return result

    def _validate_exact_replay_move_alias(
        self,
        move,
        source_move,
        source_lines,
        companies,
        journals,
        partners,
        accounts,
        currencies,
        options,
    ):
        source_accounting_lines = {
            line["id"]: line
            for line in source_lines
            if line["account_id"]
        }
        target_lines = self._source_trace_record_map(
            "account.move.line",
            source_accounting_lines,
            options,
        )
        issues = []
        expected_company = companies.get(source_move["company_id"])
        expected_journal = journals.get(source_move["journal_id"])
        if move.state != "posted":
            issues.append(f"state {move.state!r} is not posted")
        if move.date != source_move["date"]:
            issues.append(
                f"date {move.date} differs from source {source_move['date']}",
            )
        if expected_company and move.company_id != expected_company:
            issues.append(
                f"company {move.company_id.id} differs from {expected_company.id}",
            )
        if expected_journal and move.journal_id != expected_journal:
            issues.append(
                f"journal {move.journal_id.id} differs from "
                f"{expected_journal.id}",
            )
        if set(target_lines) != set(source_accounting_lines):
            issues.append(
                "source line identities differ: expected %s, got %s"
                % (
                    sorted(source_accounting_lines),
                    sorted(target_lines),
                ),
            )
        if len(move.line_ids) != len(source_accounting_lines):
            issues.append(
                f"line count {len(move.line_ids)} differs from "
                f"{len(source_accounting_lines)}",
            )

        for source_line_id, source_line in source_accounting_lines.items():
            target_line = target_lines.get(source_line_id)
            if not target_line:
                continue
            expected_account = accounts.get(source_line["account_id"])
            expected_partner = partners.get(source_line["partner_id"])
            expected_currency = currencies.get(source_line["currency_id"])
            if expected_account and target_line.account_id != expected_account:
                issues.append(
                    f"line {source_line_id} account "
                    f"{target_line.account_id.id} differs from "
                    f"{expected_account.id}",
                )
            if target_line.partner_id.id != (
                expected_partner.id if expected_partner else False
            ):
                issues.append(
                    f"line {source_line_id} partner "
                    f"{target_line.partner_id.id} differs from "
                    f"{expected_partner.id if expected_partner else False}",
                )
            if expected_currency and target_line.currency_id != expected_currency:
                issues.append(
                    f"line {source_line_id} currency "
                    f"{target_line.currency_id.id} differs from "
                    f"{expected_currency.id if expected_currency else False}",
                )
            for field_name in ("debit", "credit", "amount_currency"):
                source_amount = round(self._amount(source_line[field_name]), 2)
                target_amount = round(target_line[field_name], 2)
                if target_amount != source_amount:
                    issues.append(
                        f"line {source_line_id} {field_name} "
                        f"{target_amount:.2f} differs from "
                        f"{source_amount:.2f}",
                    )
        if issues:
            raise ValueError(
                "Native target move %s cannot replace exact source move %s: %s"
                % (
                    move.display_name,
                    source_move["id"],
                    "; ".join(issues),
                ),
            )
        return {
            "source_move_id": source_move["id"],
            "target_move_id": move.id,
            "target_trace_model": move.rebuild_source_model,
            "source_line_count": len(source_accounting_lines),
            "debit": round(sum(self._amount(line["debit"]) for line in source_accounting_lines.values()), 2),
            "credit": round(sum(self._amount(line["credit"]) for line in source_accounting_lines.values()), 2),
        }

    def _normalize_exact_replay_move_alias_identity(self, move, source_move):
        expected_name = source_move["name"] or "/"
        if expected_name == "/":
            raise ValueError(
                "Posted source move %s has no historical entry reference."
                % source_move["id"],
            )
        collision = self.env["account.move"].search([
            ("journal_id", "=", move.journal_id.id),
            ("name", "=", expected_name),
            ("state", "=", "posted"),
            ("id", "!=", move.id),
        ], limit=1)
        if collision:
            raise ValueError(
                "Historical entry reference %s already belongs to target "
                "move %s in journal %s."
                % (
                    expected_name,
                    collision.id,
                    move.journal_id.display_name,
                ),
            )
        normalized = move.name != expected_name
        if normalized:
            move.with_context(
                bypass_lock_check=BYPASS_LOCK_CHECK,
                skip_readonly_check=True,
                tracking_disable=True,
            ).write({"name": expected_name})
        move.invalidate_recordset([
            "name",
            "sequence_prefix",
            "sequence_number",
        ])
        expected_prefix = source_move["sequence_prefix"] or ""
        expected_number = source_move["sequence_number"] or 0
        identity_differences = {
            field_name: {
                "source": expected,
                "target": actual,
            }
            for field_name, expected, actual in (
                ("name", expected_name, move.name or "/"),
                (
                    "sequence_prefix",
                    expected_prefix,
                    move.sequence_prefix or "",
                ),
                (
                    "sequence_number",
                    expected_number,
                    move.sequence_number or 0,
                ),
            )
            if expected != actual
        }
        if identity_differences:
            raise ValueError(
                "Native target move %s cannot preserve exact source move %s "
                "identity: %s"
                % (
                    move.display_name,
                    source_move["id"],
                    json.dumps(
                        identity_differences,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
        return {
            "source_move_name": expected_name,
            "target_move_name": move.name,
            "source_sequence_prefix": expected_prefix,
            "target_sequence_prefix": move.sequence_prefix or "",
            "source_sequence_number": expected_number,
            "target_sequence_number": move.sequence_number or 0,
            "identity_normalized": normalized,
        }

    @staticmethod
    def _sequence_chronology_profile(rows):
        missing_names = []
        names = defaultdict(list)
        sequence_numbers = defaultdict(list)
        sequence_groups = defaultdict(list)
        for row in rows:
            move_name = row.get("move_name") or "/"
            source_move_id = row.get("source_move_id")
            journal_id = row.get("source_journal_id")
            prefix = row.get("sequence_prefix") or ""
            number = row.get("sequence_number") or 0
            move_date = fields.Date.to_date(row.get("date"))
            if move_name == "/":
                missing_names.append(source_move_id)
            names[journal_id, move_name].append(source_move_id)
            if number:
                sequence_numbers[journal_id, prefix, number].append(
                    source_move_id,
                )
                sequence_groups[journal_id, prefix].append({
                    "source_move_id": source_move_id,
                    "move_name": move_name,
                    "date": move_date,
                    "sequence_number": number,
                })

        duplicate_names = [
            {
                "source_journal_id": journal_id,
                "move_name": move_name,
                "source_move_ids": source_move_ids,
            }
            for (journal_id, move_name), source_move_ids in names.items()
            if move_name != "/" and len(source_move_ids) > 1
        ]
        duplicate_numbers = [
            {
                "source_journal_id": journal_id,
                "sequence_prefix": prefix,
                "sequence_number": number,
                "source_move_ids": source_move_ids,
            }
            for (
                journal_id,
                prefix,
                number,
            ), source_move_ids in sequence_numbers.items()
            if len(source_move_ids) > 1
        ]
        gaps = []
        date_decreases = []
        for (journal_id, prefix), group_rows in sequence_groups.items():
            previous = None
            for row in sorted(
                group_rows,
                key=lambda item: (
                    item["sequence_number"],
                    item["source_move_id"],
                ),
            ):
                if previous:
                    if (
                        row["sequence_number"]
                        > previous["sequence_number"] + 1
                    ):
                        gaps.append({
                            "source_journal_id": journal_id,
                            "sequence_prefix": prefix,
                            "previous_source_move_id": (
                                previous["source_move_id"]
                            ),
                            "previous_move_name": previous["move_name"],
                            "previous_sequence_number": (
                                previous["sequence_number"]
                            ),
                            "source_move_id": row["source_move_id"],
                            "move_name": row["move_name"],
                            "sequence_number": row["sequence_number"],
                        })
                    if row["date"] < previous["date"]:
                        date_decreases.append({
                            "source_journal_id": journal_id,
                            "sequence_prefix": prefix,
                            "previous_source_move_id": (
                                previous["source_move_id"]
                            ),
                            "previous_move_name": previous["move_name"],
                            "previous_date": str(previous["date"]),
                            "source_move_id": row["source_move_id"],
                            "move_name": row["move_name"],
                            "date": str(row["date"]),
                        })
                previous = row
        return {
            "move_count": len(rows),
            "missing_name_count": len(missing_names),
            "missing_name_examples": missing_names[:10],
            "duplicate_name_group_count": len(duplicate_names),
            "duplicate_name_examples": duplicate_names[:10],
            "duplicate_sequence_number_group_count": len(duplicate_numbers),
            "duplicate_sequence_number_examples": duplicate_numbers[:10],
            "sequence_gap_count": len(gaps),
            "sequence_gap_examples": gaps[:10],
            "sequence_date_decrease_count": len(date_decreases),
            "sequence_date_decrease_examples": date_decreases[:10],
        }

    def _sequence_chronology_stats(self, source_rows, target_moves):
        source_profile_rows = []
        target_profile_rows = []
        identity_mismatches = []
        sequence_date_mismatch_count = 0
        for source_row in source_rows:
            source_move_id = source_row["id"]
            target_move = target_moves.get(source_move_id)
            source_profile = {
                "source_move_id": source_move_id,
                "source_journal_id": source_row["journal_id"],
                "move_name": source_row["name"] or "/",
                "date": source_row["date"],
                "sequence_prefix": source_row["sequence_prefix"] or "",
                "sequence_number": source_row["sequence_number"] or 0,
            }
            source_profile_rows.append(source_profile)
            if not target_move:
                identity_mismatches.append({
                    "source_move_id": source_move_id,
                    "differences": {"target_move": "missing"},
                })
                continue
            target_profile = {
                "source_move_id": source_move_id,
                "source_journal_id": source_row["journal_id"],
                "move_name": target_move.name or "/",
                "date": target_move.date,
                "sequence_prefix": target_move.sequence_prefix or "",
                "sequence_number": target_move.sequence_number or 0,
            }
            target_profile_rows.append(target_profile)
            differences = {
                field_name: {
                    "source": source_profile[field_name],
                    "target": target_profile[field_name],
                }
                for field_name in (
                    "move_name",
                    "date",
                    "sequence_prefix",
                    "sequence_number",
                )
                if source_profile[field_name] != target_profile[field_name]
            }
            if differences:
                identity_mismatches.append({
                    "source_move_id": source_move_id,
                    "differences": differences,
                })
            if not target_move._sequence_matches_date():
                sequence_date_mismatch_count += 1

        source_profile = self._sequence_chronology_profile(
            source_profile_rows,
        )
        target_profile = self._sequence_chronology_profile(
            target_profile_rows,
        )
        target_matches_source = (
            not identity_mismatches
            and source_profile == target_profile
        )
        return {
            "target_matches_source": target_matches_source,
            "identity_mismatch_count": len(identity_mismatches),
            "identity_mismatch_examples": identity_mismatches[:10],
            "target_sequence_date_format_mismatch_count": (
                sequence_date_mismatch_count
            ),
            "source": source_profile,
            "target": target_profile,
        }

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
            return f"{module}.action_rebuild_interactive_deferred_schedule"
        if "deferred revenue" in normalized or "produits constatés d'avance" in normalized:
            return f"{module}.action_rebuild_interactive_deferred_schedule"
        if "depreciation" in normalized or "amortissement" in normalized:
            return f"{module}.action_rebuild_interactive_depreciation_schedule"
        if "group by: account" in normalized or "regrouper par : compte" in normalized:
            return f"{module}.action_rebuild_account_report_export_fixed_asset_group_account"
        if "asset group" in normalized or "immobilisations" in normalized:
            return f"{module}.action_rebuild_interactive_fixed_assets"
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
                "exports and reproducible source-target controls."
            )
        if decision == "OPERATIONAL_PARITY":
            return "Workflow need evidence, target equivalent output and classified material differences."
        if decision == "REMOVED_AS_UNUSED":
            return "Legal-form/company-scope evidence and an explicit product-scope decision."
        return "Usage evidence, explicit deferral or approved removal as unused."

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
                    "its legal-form or PCG-version scope still needs reproducible classification evidence."
                ),
                "latest_evidence_status": "scope_or_version_evidence_pending",
                "latest_evidence_json": {"target_evidence_key": target_evidence_key},
            }
        if decision == "MANDATORY_PARITY":
            return {
                "parity_level": "level_3_semantic_partial",
                "parity_gap": (
                    "A user-facing target report/export action exists and current technical harness checks cover "
                    "ledger-backed row counts, exports and sampled drill-down. Full Level 4 parity still requires "
                    "line-by-line source formula comparison, drill-down membership comparison, statutory/export "
                    "layout validation where applicable."
                ),
                "latest_evidence_status": "full_technical_evidence_pending",
                "latest_evidence_json": {"target_evidence_key": target_evidence_key or ""},
            }
        if decision == "OPERATIONAL_PARITY":
            return {
                "parity_level": "level_2_ledger_controls",
                "parity_gap": (
                    "A user-facing target equivalent exists with technical harness coverage. Operational acceptance "
                    "still requires confirmed source usage and line-level comparison for the selected workflow."
                ),
                "latest_evidence_status": "full_operational_evidence_pending",
                "latest_evidence_json": {"target_evidence_key": target_evidence_key or ""},
            }
        return {
            "parity_level": "level_1_available",
            "parity_gap": (
                "A target equivalent exists, but the product-scope evidence has not yet classified it as "
                "accepted parity, deferred or deliberately removed."
            ),
            "latest_evidence_status": "product_scope_evidence_pending",
            "latest_evidence_json": {"target_evidence_key": target_evidence_key or ""},
        }

    def _source_connection(self, options):
        conn = psycopg2.connect(
            host=options.get("source_host") or os.environ.get("ACCOUNTING_SOURCE_DB_HOST", "accounting-source-db"),
            port=options.get("source_port") or os.environ.get("ACCOUNTING_SOURCE_DB_PORT", "5432"),
            dbname=options.get("source_database") or "odoo_online_source_saas_19_3",
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

    def _source_column_exists(self, conn, table, column):
        rows = self._fetchall(
            conn,
            """
            SELECT EXISTS (
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = %(table)s
                   AND column_name = %(column)s
            ) AS exists
            """,
            {
                "table": table,
                "column": column,
            },
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
        if self._source_column_exists(conn, "account_report_line", "foldability"):
            foldability_expression = "line.foldability"
        else:
            foldability_expression = (
                "CASE WHEN line.foldable THEN 'foldable' ELSE 'always_unfolded' END"
            )
        return self._fetchall(
            conn,
            f"""
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
                   {foldability_expression} AS foldability,
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
                "foldability": row["foldability"],
                "foldable": row["foldability"] == "foldable",
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
                   c.tax_calculation_rounding_method, c.account_return_periodicity,
                   rp.country_id AS partner_country_id, rp.vat, rp.company_registry,
                   rp.street, rp.street2, rp.zip, rp.city,
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
                "tax_calculation_rounding_method": (
                    row["tax_calculation_rounding_method"] or "round_per_line"
                ),
                **self._trace_values("res.company", row["id"], options),
            }
            vals.update(self._french_declaration_profile_values(row))
            vals.update(self._french_document_identity_values(row))
            if row["account_fiscal_country_id"] in countries:
                vals["account_fiscal_country_id"] = countries[row["account_fiscal_country_id"]].id
            if row["partner_country_id"] in countries:
                vals["country_id"] = countries[row["partner_country_id"]].id
            if row["vat"]:
                vals["vat"] = row["vat"]
            if row["company_registry"]:
                vals["company_registry"] = row["company_registry"]
            for address_field in ("street", "street2", "zip", "city"):
                vals[address_field] = row[address_field] or False
            if "iap_enrich_auto_done" in Company._fields:
                vals["iap_enrich_auto_done"] = True
            vals.update(self._company_report_layout_defaults(company))
            if company:
                company.write(vals)
            else:
                company = Company.create(vals)
            companies[row["id"]] = company
        return companies, rows

    @staticmethod
    def _source_company_siren(row):
        registry_digits = "".join(
            character
            for character in (row.get("company_registry") or "")
            if character.isdigit()
        )
        if len(registry_digits) >= 9:
            return registry_digits[:9]
        vat_digits = "".join(
            character
            for character in (row.get("vat") or "")
            if character.isdigit()
        )
        return vat_digits[-9:] if len(vat_digits) >= 9 else ""

    @classmethod
    def _french_declaration_profile_values(cls, row):
        siren = cls._source_company_siren(row)
        profile = FRENCH_DECLARATION_PROFILES_BY_SIREN.get(siren)
        if not profile:
            return {}

        periodicity = row.get("account_return_periodicity") or ""
        vat_regime = VAT_REGIME_BY_SOURCE_RETURN_PERIODICITY.get(
            periodicity,
            "unknown",
        )
        return {
            "rebuild_declaration_profile_active": True,
            "rebuild_legal_form": profile["rebuild_legal_form"],
            "rebuild_corporate_tax_regime": profile[
                "rebuild_corporate_tax_regime"
            ],
            "rebuild_corporate_tax_projection_profile": profile[
                "rebuild_corporate_tax_projection_profile"
            ],
            "rebuild_profit_tax_regime": profile[
                "rebuild_profit_tax_regime"
            ],
            "rebuild_vat_regime": vat_regime,
            "rebuild_first_fiscalyear_start": profile[
                "rebuild_first_fiscalyear_start"
            ],
            "rebuild_first_fiscalyear_end": profile[
                "rebuild_first_fiscalyear_end"
            ],
            "rebuild_declaration_profile_evidence": (
                f"{profile['evidence']} The Online source configures tax returns "
                f"with {periodicity or 'an unclassified'} periodicity, translated "
                f"to the {vat_regime} VAT profile."
            ),
        }

    @classmethod
    def _french_document_identity_values(cls, row):
        """Return reviewed legal mentions absent from the Online schema."""
        return dict(
            FRENCH_DOCUMENT_IDENTITIES_BY_SIREN.get(
                cls._source_company_siren(row),
                {},
            )
        )

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

    def _sync_company_cash_basis_flags(self, companies):
        """Keep company settings consistent with imported cash-basis taxes.

        The source tax exigibility is the accounting fact. If any imported tax is
        payable on payment, Odoo's own settings model expects the company-level
        cash-basis option to be enabled; otherwise opening Settings raises a
        blocking warning and tries to re-enable it interactively.
        """
        Tax = self.env["account.tax"].with_context(active_test=False)
        updated_companies = self.env["res.company"]
        for company in companies.values():
            has_cash_basis_tax = Tax.with_company(company).search_count([
                ("company_id", "=", company.id),
                ("tax_exigibility", "=", "on_payment"),
            ], limit=1)
            if not has_cash_basis_tax or company.tax_exigibility:
                continue
            company.write({
                "tax_exigibility": True,
                "rebuild_import_note": (
                    (company.rebuild_import_note or "") + "\n"
                    "Enabled the company cash-basis VAT setting because imported source taxes "
                    "include tax_exigibility=on_payment. Tax definitions were not changed."
                ).strip(),
            })
            updated_companies |= company
        return updated_companies

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

    def _import_currency_rates(self, conn, options, companies, currencies):
        rows = self._fetchall(
            conn,
            """
            SELECT rate.id, rate.name, rate.rate, rate.currency_id, rate.company_id,
                   rate.create_date, rate.write_date,
                   company.currency_provider AS source_provider
            FROM res_currency_rate rate
            LEFT JOIN res_company company ON company.id = rate.company_id
            WHERE rate.company_id IS NULL
               OR rate.company_id = ANY(%(source_company_ids)s)
            ORDER BY rate.name, rate.currency_id, rate.company_id, rate.id
            """,
            options,
        )
        return self._upsert_currency_rate_rows(rows, options, companies, currencies)

    def _upsert_currency_rate_rows(self, rows, options, companies, currencies):
        """Replay exact source rates for native currency calculations.

        Odoo stores the technical rate as foreign-currency units per one unit of
        the company currency.  Source and target both use that native model, so
        copying ``rate`` is the lossless option; inverting or recomputing it
        would change invoice, payment and exchange-difference behavior.
        """
        Rate = self.env["res.currency.rate"].with_context(tracking_disable=True)
        imported_rates = Rate.browse()
        seen_source_ids = set()
        reused_natural_key_count = 0
        skipped_rows = []
        provider_names = set()
        currency_names = set()
        source_dates = []

        for row in rows:
            currency = currencies.get(row["currency_id"])
            company = companies.get(row["company_id"]) if row["company_id"] else False
            if not currency or (row["company_id"] and not company):
                skipped_rows.append({
                    "source_rate_id": row["id"],
                    "source_currency_id": row["currency_id"],
                    "source_company_id": row["company_id"],
                })
                continue

            rate = Rate.search([
                ("rebuild_source_model", "=", "res.currency.rate"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            if not rate:
                rate = Rate.search([
                    ("name", "=", row["name"]),
                    ("currency_id", "=", currency.id),
                    ("company_id", "=", company.id if company else False),
                ], limit=1)
                reused_natural_key_count += bool(rate)

            provider = row.get("source_provider") or "source_odoo_online"
            retrieved_at = row.get("write_date") or row.get("create_date")
            vals = {
                "name": row["name"],
                "rate": self._amount(row["rate"]),
                "currency_id": currency.id,
                "company_id": company.id if company else False,
                "rebuild_rate_provider": provider,
                "rebuild_rate_retrieved_at": retrieved_at,
                "rebuild_import_note": (
                    "Exact native rate copied from the Odoo Online source. The source record's "
                    "write timestamp is retained as the best available provider-retrieval evidence."
                ),
                **self._trace_values("res.currency.rate", row["id"], options),
            }
            if rate:
                rate.write(vals)
            else:
                rate = Rate.create(vals)
            imported_rates |= rate
            seen_source_ids.add(row["id"])
            provider_names.add(provider)
            currency_names.add(currency.name)
            source_dates.append(row["name"])

        stale_rates = Rate.search([
            ("rebuild_source_database", "=", options.get("source_database")),
            ("rebuild_source_model", "=", "res.currency.rate"),
            ("company_id", "in", [False, *[company.id for company in companies.values()]]),
            ("rebuild_source_id", "not in", list(seen_source_ids) or [0]),
        ])
        stale_rate_count = len(stale_rates)
        stale_rates.unlink()

        return {
            "source_currency_rate_count": len(rows),
            "imported_currency_rate_count": len(imported_rates),
            "skipped_currency_rate_count": len(skipped_rows),
            "skipped_currency_rate_examples": skipped_rows[:20],
            "reused_natural_key_count": reused_natural_key_count,
            "removed_stale_currency_rate_count": stale_rate_count,
            "currencies": sorted(currency_names),
            "providers": sorted(provider_names),
            "first_rate_date": str(min(source_dates)) if source_dates else False,
            "last_rate_date": str(max(source_dates)) if source_dates else False,
        }

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
                UNION
                SELECT line.partner_id
                FROM account_reconcile_model_line line
                JOIN account_reconcile_model model ON model.id = line.model_id
                WHERE model.company_id = ANY(%(source_company_ids)s)
                  AND line.partner_id IS NOT NULL
                UNION
                SELECT relation.res_partner_id
                FROM account_reconcile_model_res_partner_rel relation
                JOIN account_reconcile_model model
                  ON model.id = relation.account_reconcile_model_id
                WHERE model.company_id = ANY(%(source_company_ids)s)
            )
            ORDER BY rp.id
            """,
            options,
        )
        Partner = self.env["res.partner"].with_context(active_test=False)
        existing_partners = self._source_trace_record_map(
            "res.partner",
            [row["id"] for row in rows],
            options,
        )
        partners = {}
        pending = []
        for row in rows:
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
            partner = existing_partners.get(row["id"])
            if partner:
                partner.write(vals)
                partners[row["id"]] = partner
            else:
                pending.append((row["id"], vals))
        for batch in self._batched(pending, self._RELATION_BATCH_SIZE):
            created = Partner.create([vals for _source_id, vals in batch])
            for (source_id, _vals), partner in zip(batch, created, strict=True):
                partners[source_id] = partner
        return partners

    def _account_group_map(self, conn, options, companies):
        """Import the source chart hierarchy before accounts are evaluated.

        Odoo derives ``account.account.group_id`` from the most specific
        matching prefix range.  Reusing an existing native group with the same
        company and range keeps the import compatible with targets where the
        localization already installed the French hierarchy.
        """
        if not self._source_table_exists(conn, "account_group"):
            target_companies = self.env["res.company"].browse(
                [company.id for company in companies.values()],
            )
            self.env[
                "account.group"
            ]._ensure_french_compatibility_groups(target_companies)
            return {}

        rows = self._fetchall(
            conn,
            """
            SELECT id, name, code_prefix_start, code_prefix_end, company_id
              FROM account_group
             WHERE company_id = ANY(%(source_company_ids)s)
             ORDER BY char_length(code_prefix_start), code_prefix_start, id
            """,
            options,
        )
        Group = self.env["account.group"].sudo().with_context(
            delay_account_group_sync=True,
        )
        groups = {}
        source_languages = {
            language
            for row in rows
            if isinstance(row["name"], dict)
            for language, translated_name in row["name"].items()
            if translated_name
        }
        if source_languages:
            self.env["res.lang"].sudo().with_context(active_test=False).search([
                ("code", "in", list(source_languages)),
                ("active", "=", False),
            ]).write({"active": True})
        for row in rows:
            company = companies.get(row["company_id"])
            if not company:
                continue
            group = Group.search([
                ("rebuild_source_model", "=", "account.group"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            if not group:
                group = Group.search([
                    ("company_id", "=", company.id),
                    ("code_prefix_start", "=", row["code_prefix_start"]),
                    ("code_prefix_end", "=", row["code_prefix_end"]),
                ], limit=1)
            vals = {
                "name": self._source_text(row["name"])
                or f"Source account group {row['id']}",
                "code_prefix_start": row["code_prefix_start"],
                "code_prefix_end": row["code_prefix_end"],
                "company_id": company.id,
                **self._trace_values("account.group", row["id"], options),
            }
            if group:
                group.write(vals)
            else:
                group = Group.create(vals)
            if isinstance(row["name"], dict):
                group.update_field_translations(
                    "name",
                    {
                        language: translated_name
                        for language, translated_name in row["name"].items()
                        if translated_name
                    },
                )
            groups[row["id"]] = group

        imported_groups = Group.browse([
            group.id for group in groups.values()
        ])
        if imported_groups:
            imported_groups.with_context(
                delay_account_group_sync=False,
            )._adapt_parent_account_group(
                self.env["res.company"].browse(
                    list({group.company_id.id for group in imported_groups}),
                ),
            )
        return groups

    @staticmethod
    def _target_account_name_translations(source_name, account_code):
        translations = {
            language: translated_name
            for language, translated_name in (
                source_name.items()
                if isinstance(source_name, dict)
                else []
            )
            if translated_name
        }
        translations.update(
            USL_ACCOUNT_NAME_TRANSLATIONS.get(account_code, {}),
        )
        return translations

    def _account_map(self, conn, options, companies, currencies):
        self._account_group_map(conn, options, companies)
        rows = self._fetchall(
            conn,
            """
            SELECT aa.id, aa.name, aa.code_store, aa.account_type, aa.active, aa.reconcile,
                   aa.non_trade, aa.currency_id,
                   array_remove(array_agg(rel.res_company_id ORDER BY rel.res_company_id), NULL) AS company_ids
            FROM account_account aa
            JOIN account_account_res_company_rel selected_rel
              ON selected_rel.account_account_id = aa.id
             AND selected_rel.res_company_id = ANY(%(source_company_ids)s)
            LEFT JOIN account_account_res_company_rel rel
              ON rel.account_account_id = aa.id
            GROUP BY aa.id
            ORDER BY aa.id
            """,
            options,
        )
        self._quarantine_bootstrap_account_code_collisions(rows, options, companies)
        account_languages = {
            language
            for row in rows
            for language, translated_name in (
                row["name"].items()
                if isinstance(row["name"], dict)
                else []
            )
            if translated_name
        }
        account_languages.update({"en_US", "fr_FR"})
        if account_languages:
            self.env["res.lang"].sudo().with_context(active_test=False).search([
                ("code", "in", sorted(account_languages)),
                ("active", "=", False),
            ]).write({"active": True})
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
            elif account:
                # Avoid needless structural recomputations on an idempotent
                # pass.  In Odoo 19 ``code`` recomputes ``account_type``;
                # flushing that unchanged value still runs the journal-default
                # constraint and rejects legitimate payable/receivable
                # defaults.
                if account.with_company(company).code == code:
                    vals.pop("code")
                if account.account_type == account_type:
                    vals.pop("account_type")
                if account.reconcile == reconcile:
                    vals.pop("reconcile")
                if account.non_trade == bool(row["non_trade"]):
                    vals.pop("non_trade")
                if set(account.company_ids.ids) == set(target_company_ids):
                    vals.pop("company_ids")
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
            translations = self._target_account_name_translations(
                row["name"],
                code,
            )
            if translations:
                account.update_field_translations("name", translations)
            accounts[row["id"]] = account
        self._archive_empty_bootstrap_unaffected_earnings_accounts(rows, options, companies)
        self._sync_company_accounting_defaults(
            conn,
            options,
            companies,
            accounts,
        )
        return accounts, archive_after_post

    def _sync_company_accounting_defaults(
        self,
        conn,
        options,
        companies,
        accounts,
    ):
        rows = self._fetchall(
            conn,
            """
            SELECT id,
                   income_currency_exchange_account_id,
                   expense_currency_exchange_account_id,
                   account_journal_suspense_account_id,
                   transfer_account_id
            FROM res_company
            WHERE id = ANY(%(source_company_ids)s)
            ORDER BY id
            """,
            options,
        )
        field_names = (
            "income_currency_exchange_account_id",
            "expense_currency_exchange_account_id",
            "account_journal_suspense_account_id",
            "transfer_account_id",
        )
        for row in rows:
            company = companies.get(row["id"])
            if not company:
                continue
            values = {
                field_name: accounts[source_account_id].id
                for field_name in field_names
                if (
                    (source_account_id := row[field_name])
                    and source_account_id in accounts
                )
            }
            if values:
                company.write(values)

    def _sync_partner_accounting_properties(
        self,
        conn,
        options,
        companies,
        partners,
        accounts,
        fiscal_positions,
    ):
        default_rows = self._fetchall(
            conn,
            """
            SELECT defaults.company_id, fields.name AS field_name,
                   defaults.json_value::integer AS source_record_id
            FROM ir_default defaults
            JOIN ir_model_fields fields ON fields.id = defaults.field_id
            WHERE fields.model = 'res.partner'
              AND fields.name IN (
                  'property_account_receivable_id',
                  'property_account_payable_id'
              )
              AND defaults.company_id = ANY(%(source_company_ids)s)
              AND defaults.user_id IS NULL
              AND COALESCE(defaults.condition, '') = ''
            ORDER BY defaults.company_id, fields.name
            """,
            options,
        )
        specific_rows = self._fetchall(
            conn,
            """
            SELECT partner.id AS partner_id,
                   source_company_id AS company_id,
                   NULLIF(partner.property_account_receivable_id ->> source_company_id::text, '')::integer
                       AS receivable_account_id,
                   NULLIF(partner.property_account_payable_id ->> source_company_id::text, '')::integer
                       AS payable_account_id,
                   NULLIF(partner.property_account_position_id ->> source_company_id::text, '')::integer
                       AS fiscal_position_id
            FROM res_partner partner
            CROSS JOIN unnest(%(source_company_ids)s::integer[]) source_company_id
            WHERE partner.property_account_receivable_id ? source_company_id::text
               OR partner.property_account_payable_id ? source_company_id::text
               OR partner.property_account_position_id ? source_company_id::text
            ORDER BY partner.id, source_company_id
            """,
            options,
        )
        default_count = 0
        for row in default_rows:
            company = companies.get(row["company_id"])
            account = accounts.get(row["source_record_id"])
            if not company or not account:
                continue
            self.env["ir.default"].set(
                "res.partner",
                row["field_name"],
                account.id,
                company_id=company.id,
            )
            default_count += 1

        specific_count = 0
        for row in specific_rows:
            partner = partners.get(row["partner_id"])
            company = companies.get(row["company_id"])
            if not partner or not company:
                continue
            vals = {}
            if row["receivable_account_id"] in accounts:
                vals["property_account_receivable_id"] = accounts[row["receivable_account_id"]].id
            if row["payable_account_id"] in accounts:
                vals["property_account_payable_id"] = accounts[row["payable_account_id"]].id
            if row["fiscal_position_id"] in fiscal_positions:
                vals["property_account_position_id"] = fiscal_positions[row["fiscal_position_id"]].id
            if vals:
                partner.with_company(company).write(vals)
                specific_count += 1
        return {
            "company_default_account_property_count": default_count,
            "partner_specific_property_count": specific_count,
        }

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
            WHERE company_id = ANY(%(source_company_ids)s)
            ORDER BY company_id, sequence, id
            """,
            options,
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

    def _ensure_cash_basis_transition_account_reconcile(self, account):
        if not account or account.reconcile:
            return False
        account.write({
            "reconcile": True,
            "rebuild_import_note": (
                (account.rebuild_import_note or "")
                + "\nEnabled reconciliation because this account is used "
                "as a cash-basis tax transition account."
            ).strip(),
        })
        return True

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
            WHERE company_id = ANY(%(source_company_ids)s)
            ORDER BY company_id, sequence, id
            """,
            options,
        )
        repartition_rows = self._fetchall(
            conn,
            """
            SELECT line.id, line.tax_id, line.account_id, line.sequence,
                   line.repartition_type, line.document_type,
                   line.factor_percent, line.use_in_tax_closing
            FROM account_tax_repartition_line line
            JOIN account_tax tax ON tax.id = line.tax_id
            WHERE tax.company_id = ANY(%(source_company_ids)s)
            ORDER BY line.tax_id, line.document_type, line.repartition_type,
                     line.sequence, line.id
            """,
            options,
        )
        repartition_tag_rows = self._fetchall(
            conn,
            """
            SELECT account_tax_repartition_line_id AS repartition_line_id,
                   account_account_tag_id AS tag_id
            FROM account_account_tag_account_tax_repartition_line_rel relation
            JOIN account_tax_repartition_line line
              ON line.id = relation.account_tax_repartition_line_id
            JOIN account_tax tax ON tax.id = line.tax_id
            WHERE tax.company_id = ANY(%(source_company_ids)s)
            ORDER BY relation.account_tax_repartition_line_id,
                     relation.account_account_tag_id
            """,
            options,
        )
        child_rows = self._fetchall(
            conn,
            """
            SELECT relation.parent_tax, relation.child_tax
            FROM account_tax_filiation_rel relation
            JOIN account_tax parent ON parent.id = relation.parent_tax
            JOIN account_tax child ON child.id = relation.child_tax
            WHERE parent.company_id = ANY(%(source_company_ids)s)
              AND child.company_id = ANY(%(source_company_ids)s)
            ORDER BY relation.parent_tax, relation.child_tax
            """,
            options,
        )
        alternative_rows = self._fetchall(
            conn,
            """
            SELECT relation.dest_tax_id, relation.src_tax_id
            FROM account_tax_alternatives relation
            JOIN account_tax destination ON destination.id = relation.dest_tax_id
            JOIN account_tax source ON source.id = relation.src_tax_id
            WHERE destination.company_id = ANY(%(source_company_ids)s)
              AND source.company_id = ANY(%(source_company_ids)s)
            ORDER BY relation.dest_tax_id, relation.src_tax_id
            """,
            options,
        )
        repartitions_by_tax = defaultdict(list)
        for row in repartition_rows:
            repartitions_by_tax[row["tax_id"]].append(row)
        tags_by_repartition = defaultdict(list)
        for row in repartition_tag_rows:
            if row["tag_id"] in tax_tags:
                tags_by_repartition[row["repartition_line_id"]].append(tax_tags[row["tag_id"]].id)

        taxes = {}
        Tax = self.env["account.tax"].with_context(
            active_test=False,
            tracking_disable=True,
        )
        for row in tax_rows:
            company = companies[row["company_id"]]
            country = countries.get(row["country_id"])
            name = self._source_text(row["name"]) or f"Source tax {row['id']}"
            cash_basis_transition_account = accounts.get(
                row["cash_basis_transition_account_id"],
            )
            if cash_basis_transition_account:
                # Odoo validates this invariant again whenever a tax is
                # updated. Keep idempotent configuration replays valid even
                # when a preceding historical stage temporarily left the
                # account without its source reconciliation flag.
                self._ensure_cash_basis_transition_account_reconcile(
                    cash_basis_transition_account,
                )
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
                    cash_basis_transition_account.id
                    if cash_basis_transition_account else False
                ),
                "ubl_cii_tax_category_code": row["ubl_cii_tax_category_code"],
                "ubl_cii_tax_exemption_reason_code": row["ubl_cii_tax_exemption_reason_code"],
                **self._trace_values("account.tax", row["id"], options),
            }
            # This is the same temporary setup context used by Odoo's chart
            # loader. It permits taxes and their transition accounts to be
            # restored in either source order; the explicit invariant below
            # still fails the import if the final native configuration is not
            # valid.
            if tax:
                tax.with_context(chart_template_load=True).write(vals)
            else:
                tax = Tax.with_context(chart_template_load=True).create(vals)
            # Creating/updating the tax can invalidate and recompute the
            # account's stored ``reconcile`` value after the earlier account
            # import. Enforce the invariant on the account actually linked by
            # the resulting tax, then read it back for the final assertion.
            self._ensure_cash_basis_transition_account_reconcile(
                tax.cash_basis_transition_account_id,
            )
            tax.invalidate_recordset([
                "tax_exigibility",
                "cash_basis_transition_account_id",
            ])
            if (
                tax.tax_exigibility == "on_payment"
                and not tax.cash_basis_transition_account_id.reconcile
            ):
                raise ValidationError(
                    _(
                        "Cash-basis tax %(tax)s (source %(source)s) was "
                        "restored with a transition account that does not "
                        "allow reconciliation.",
                        tax=tax.display_name,
                        source=row["id"],
                    ),
                )
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
                ("tax_id", "in", [tax.id for tax in taxes.values()]),
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

    def _payment_term_map(self, conn, options, companies):
        term_rows = self._fetchall(
            conn,
            """
            SELECT id, company_id, sequence, discount_days,
                   early_pay_discount_computation, name, note, active,
                   display_on_invoice, early_discount, discount_percentage
            FROM account_payment_term
            WHERE company_id IS NULL OR company_id = ANY(%(source_company_ids)s)
            ORDER BY company_id NULLS FIRST, sequence, id
            """,
            options,
        )
        line_rows = self._fetchall(
            conn,
            """
            SELECT line.id, line.payment_id, line.nb_days, line.value,
                   line.delay_type, line.days_next_month, line.value_amount
            FROM account_payment_term_line line
            JOIN account_payment_term term ON term.id = line.payment_id
            WHERE term.company_id IS NULL OR term.company_id = ANY(%(source_company_ids)s)
            ORDER BY line.payment_id, line.id
            """,
            options,
        )
        lines_by_term = defaultdict(list)
        for row in line_rows:
            lines_by_term[row["payment_id"]].append(row)

        terms = {}
        PaymentTerm = self.env["account.payment.term"].with_context(active_test=False)
        for row in term_rows:
            company = companies.get(row["company_id"]) if row["company_id"] else False
            name = self._source_text(row["name"]) or f"Source payment term {row['id']}"
            term = PaymentTerm.search([
                ("rebuild_source_model", "=", "account.payment.term"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            if not term:
                term = PaymentTerm.search([
                    ("name", "=", name),
                    ("company_id", "=", company.id if company else False),
                ], limit=1)
            vals = {
                "name": name,
                "company_id": company.id if company else False,
                "sequence": row["sequence"] or 10,
                "note": self._source_text(row["note"]),
                "active": bool(row["active"]),
                "display_on_invoice": bool(row["display_on_invoice"]),
                "early_discount": bool(row["early_discount"]),
                "discount_percentage": self._amount(row["discount_percentage"]),
                "discount_days": row["discount_days"] or 0,
                "early_pay_discount_computation": row["early_pay_discount_computation"] or "included",
                **self._trace_values("account.payment.term", row["id"], options),
            }
            source_lines = lines_by_term[row["id"]]
            line_commands = [
                Command.create({
                    "value": line["value"] or "percent",
                    "value_amount": self._amount(line["value_amount"]),
                    "delay_type": line["delay_type"] or "days_after",
                    "days_next_month": line["days_next_month"] or "10",
                    "nb_days": line["nb_days"] or 0,
                    **self._trace_values("account.payment.term.line", line["id"], options),
                })
                for line in source_lines
            ]
            if term:
                term.write(vals)
                term.write({"line_ids": [Command.clear(), *line_commands]})
            else:
                term = PaymentTerm.create({**vals, "line_ids": line_commands})
            terms[row["id"]] = term
        return terms, {
            "payment_term_count": len(terms),
            "payment_term_line_count": len(line_rows),
        }

    def _fiscal_position_map(self, conn, options, companies, accounts, taxes, countries):
        position_rows = self._fetchall(
            conn,
            """
            SELECT position.id, position.sequence, position.company_id,
                   position.country_id, position.country_group_id,
                   position.zip_from, position.zip_to, position.foreign_vat,
                   position.name, position.note, position.active,
                   position.auto_apply, position.vat_required,
                   CASE WHEN group_data.id IS NOT NULL
                        THEN group_data.module || '.' || group_data.name
                        ELSE NULL
                   END AS country_group_xmlid
            FROM account_fiscal_position position
            LEFT JOIN ir_model_data group_data
                   ON group_data.model = 'res.country.group'
                  AND group_data.res_id = position.country_group_id
            WHERE position.company_id = ANY(%(source_company_ids)s)
            ORDER BY position.company_id, position.sequence, position.id
            """,
            options,
        )
        account_rows = self._fetchall(
            conn,
            """
            SELECT mapping.id, mapping.position_id,
                   mapping.account_src_id, mapping.account_dest_id
            FROM account_fiscal_position_account mapping
            JOIN account_fiscal_position position ON position.id = mapping.position_id
            WHERE position.company_id = ANY(%(source_company_ids)s)
            ORDER BY mapping.position_id, mapping.id
            """,
            options,
        )
        tax_rows = self._fetchall(
            conn,
            """
            SELECT relation.account_fiscal_position_id AS position_id,
                   relation.account_tax_id AS tax_id
            FROM account_fiscal_position_account_tax_rel relation
            JOIN account_fiscal_position position
              ON position.id = relation.account_fiscal_position_id
            WHERE position.company_id = ANY(%(source_company_ids)s)
            ORDER BY relation.account_fiscal_position_id, relation.account_tax_id
            """,
            options,
        )
        accounts_by_position = defaultdict(list)
        for row in account_rows:
            accounts_by_position[row["position_id"]].append(row)
        taxes_by_position = defaultdict(list)
        for row in tax_rows:
            if row["tax_id"] in taxes:
                taxes_by_position[row["position_id"]].append(taxes[row["tax_id"]].id)

        positions = {}
        FiscalPosition = self.env["account.fiscal.position"].with_context(active_test=False)
        for row in position_rows:
            company = companies[row["company_id"]]
            country = countries.get(row["country_id"])
            country_group = (
                self.env.ref(row["country_group_xmlid"], raise_if_not_found=False)
                if row["country_group_xmlid"]
                else False
            )
            name = self._source_text(row["name"]) or f"Source fiscal position {row['id']}"
            position = FiscalPosition.search([
                ("rebuild_source_model", "=", "account.fiscal.position"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ], limit=1)
            if not position:
                position = FiscalPosition.search([
                    ("name", "=", name),
                    ("company_id", "=", company.id),
                ], limit=1)
            vals = {
                "name": name,
                "sequence": row["sequence"] or 10,
                "company_id": company.id,
                "country_id": country.id if country else False,
                "country_group_id": country_group.id if country_group else False,
                "zip_from": row["zip_from"],
                "zip_to": row["zip_to"],
                "foreign_vat": row["foreign_vat"],
                "note": self._source_text(row["note"]),
                "active": bool(row["active"]),
                "auto_apply": bool(row["auto_apply"]),
                "vat_required": bool(row["vat_required"]),
                **self._trace_values("account.fiscal.position", row["id"], options),
            }
            if position:
                position.write(vals)
            else:
                position = FiscalPosition.create(vals)
            position.tax_ids = [Command.set(taxes_by_position[row["id"]])]
            position.account_ids = [Command.clear()]
            for mapping in accounts_by_position[row["id"]]:
                source_account = accounts.get(mapping["account_src_id"])
                destination_account = accounts.get(mapping["account_dest_id"])
                if source_account and destination_account:
                    position.account_ids = [Command.create({
                        "account_src_id": source_account.id,
                        "account_dest_id": destination_account.id,
                        **self._trace_values(
                            "account.fiscal.position.account",
                            mapping["id"],
                            options,
                        ),
                    })]
            positions[row["id"]] = position
        return positions, {
            "fiscal_position_count": len(positions),
            "fiscal_position_account_mapping_count": len(account_rows),
            "fiscal_position_tax_link_count": len(tax_rows),
        }

    def _journal_map(self, conn, options, companies, accounts, currencies):
        rows = self._fetchall(
            conn,
            """
            SELECT DISTINCT aj.id, aj.name, aj.code, aj.type, aj.company_id, aj.default_account_id,
                   aj.currency_id, aj.active, aj.sequence, aj.refund_sequence, aj.restrict_mode_hash_table
            FROM account_journal aj
            WHERE aj.company_id = ANY(%(source_company_ids)s)
            ORDER BY aj.company_id, aj.id
            """,
            options,
        )
        journals = {}
        archive_after_post = []
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
                # Historical moves can only be posted while their journal is
                # active. Restore the source archive state after replay.
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
                # Odoo recreates payment method lines when either dependency is
                # present in write(), even if the value itself did not change.
                if journal.type == vals["type"]:
                    vals.pop("type")
                if "currency_id" in vals and journal.currency_id.id == vals["currency_id"]:
                    vals.pop("currency_id")
                journal.write(vals)
            else:
                journal = Journal.create(vals)
            journals[row["id"]] = journal
            if not row["active"]:
                archive_after_post.append(row["id"])
        self._sync_company_einvoice_configuration(
            conn,
            options,
            companies,
            journals,
        )
        return journals, archive_after_post

    def _company_configuration_parity(
        self,
        conn,
        options,
        companies,
        method_lines=None,
    ):
        """Prove complete operational Accounting configuration per company.

        The target can contain additional native bootstrap records.  Parity is
        therefore measured only on source-traced records and on their active
        state. Target-only operational defaults are reported separately and do
        not weaken the source mapping checks.
        """
        account_group_count_sql = (
            """(SELECT COUNT(*)
                      FROM account_group model
                     WHERE model.company_id = company.id)"""
            if self._source_table_exists(conn, "account_group")
            else "0::bigint"
        )
        source_rows = self._fetchall(
            conn,
            f"""
            SELECT company.id AS company_id,
                   currency.name AS currency_name,
                   fiscal_country.code AS fiscal_country_code,
                   company.fiscalyear_last_day,
                   company.fiscalyear_last_month,
                   company.fiscalyear_lock_date,
                   company.tax_lock_date,
                   company.sale_lock_date,
                   company.purchase_lock_date,
                   company.hard_lock_date,
                   company.tax_calculation_rounding_method,
                   company.tax_exigibility,
                   company.expense_journal_id,
                   (SELECT COUNT(*)
                      FROM account_payment_method_line_res_company_rel relation
                     WHERE relation.res_company_id = company.id)
                       AS expense_allowed_payment_method_line_count,
                   {account_group_count_sql} AS account_group_count,
                   (SELECT COUNT(*)
                      FROM account_account_res_company_rel relation
                     WHERE relation.res_company_id = company.id) AS account_count,
                   (SELECT COUNT(*)
                      FROM account_account_res_company_rel relation
                      JOIN account_account account
                        ON account.id = relation.account_account_id
                     WHERE relation.res_company_id = company.id
                       AND account.active) AS active_account_count,
                   (SELECT COUNT(*) FROM account_journal model
                     WHERE model.company_id = company.id) AS journal_count,
                   (SELECT COUNT(*) FROM account_journal model
                     WHERE model.company_id = company.id AND model.active)
                       AS active_journal_count,
                   (SELECT COUNT(*)
                      FROM account_payment_method_line method_line
                      JOIN account_journal journal
                        ON journal.id = method_line.journal_id
                     WHERE journal.company_id = company.id)
                       AS payment_method_line_count,
                   (SELECT COUNT(*) FROM account_tax_group model
                     WHERE model.company_id = company.id) AS tax_group_count,
                   (SELECT COUNT(*) FROM account_tax model
                     WHERE model.company_id = company.id) AS tax_count,
                   (SELECT COUNT(*) FROM account_tax model
                     WHERE model.company_id = company.id AND model.active)
                       AS active_tax_count,
                   (SELECT COUNT(*)
                      FROM account_tax_repartition_line line
                      JOIN account_tax tax ON tax.id = line.tax_id
                     WHERE tax.company_id = company.id)
                       AS tax_repartition_line_count,
                   (SELECT COUNT(*) FROM account_fiscal_position model
                     WHERE model.company_id = company.id)
                       AS fiscal_position_count,
                   (SELECT COUNT(*) FROM account_fiscal_position model
                     WHERE model.company_id = company.id AND model.active)
                       AS active_fiscal_position_count,
                   (SELECT COUNT(*)
                      FROM account_fiscal_position_account mapping
                      JOIN account_fiscal_position position
                        ON position.id = mapping.position_id
                     WHERE position.company_id = company.id)
                       AS fiscal_position_account_mapping_count,
                   (SELECT COUNT(*)
                      FROM account_fiscal_position_account_tax_rel relation
                      JOIN account_fiscal_position position
                        ON position.id = relation.account_fiscal_position_id
                     WHERE position.company_id = company.id)
                       AS fiscal_position_tax_link_count,
                   (SELECT COUNT(*) FROM account_reconcile_model model
                     WHERE model.company_id = company.id)
                       AS reconcile_model_count,
                   (SELECT COUNT(*) FROM account_reconcile_model model
                     WHERE model.company_id = company.id AND model.active)
                       AS active_reconcile_model_count,
                   (SELECT COUNT(*)
                      FROM account_reconcile_model_line line
                      JOIN account_reconcile_model model
                        ON model.id = line.model_id
                     WHERE model.company_id = company.id)
                       AS reconcile_model_line_count,
                   (SELECT COUNT(*) FROM account_analytic_account model
                     WHERE model.company_id = company.id)
                       AS analytic_account_count,
                   (SELECT COUNT(*) FROM account_analytic_account model
                     WHERE model.company_id = company.id AND model.active)
                       AS active_analytic_account_count,
                   (SELECT COUNT(*) FROM res_currency_rate model
                     WHERE model.company_id = company.id)
                       AS currency_rate_count,
                   (SELECT COUNT(*) FROM account_payment_term model
                     WHERE model.company_id IS NULL) AS shared_payment_term_count,
                   (SELECT COUNT(*)
                      FROM account_payment_term_line line
                      JOIN account_payment_term term ON term.id = line.payment_id
                     WHERE term.company_id IS NULL)
                       AS shared_payment_term_line_count,
                   (SELECT COUNT(*) FROM account_analytic_plan)
                       AS shared_analytic_plan_count,
                   (SELECT COUNT(*) FROM account_account_tag)
                       AS shared_tax_tag_count
              FROM res_company company
              LEFT JOIN res_currency currency ON currency.id = company.currency_id
              LEFT JOIN res_country fiscal_country
                     ON fiscal_country.id = company.account_fiscal_country_id
             WHERE company.id = ANY(%(source_company_ids)s)
             ORDER BY company.id
            """,
            options,
        )
        source_by_company = {
            row["company_id"]: row
            for row in source_rows
        }
        snapshot = options.get("source_snapshot_id")
        def traced(model_name, source_model, snapshot, domain=()):
            return self.env[model_name].sudo().with_context(
                active_test=False,
            ).search([
                ("rebuild_source_model", "=", source_model),
                ("rebuild_source_snapshot", "=", snapshot),
                *domain,
            ])

        payment_method_compatibility = (
            self._payment_method_line_compatibility(
                conn,
                options,
                method_lines,
            )
            if method_lines is not None
            else {}
        )
        company_results = []
        mismatch_count = 0
        for source_company_id in self._source_company_ids(options):
            company = companies[source_company_id]
            source = source_by_company.get(source_company_id, {})
            imported = {
                "account_groups": traced(
                    "account.group", "account.group", snapshot,
                    [("company_id", "=", company.id)],
                ),
                "accounts": traced(
                    "account.account", "account.account", snapshot,
                    [("company_ids", "in", company.id)],
                ),
                "journals": traced(
                    "account.journal", "account.journal", snapshot,
                    [("company_id", "=", company.id)],
                ),
                "tax_groups": traced(
                    "account.tax.group", "account.tax.group", snapshot,
                    [("company_id", "=", company.id)],
                ),
                "taxes": traced(
                    "account.tax", "account.tax", snapshot,
                    [("company_id", "=", company.id)],
                ),
                "tax_repartition_lines": traced(
                    "account.tax.repartition.line",
                    "account.tax.repartition.line",
                    snapshot,
                    [("tax_id.company_id", "=", company.id)],
                ),
                "fiscal_positions": traced(
                    "account.fiscal.position",
                    "account.fiscal.position",
                    snapshot,
                    [("company_id", "=", company.id)],
                ),
                "fiscal_position_accounts": traced(
                    "account.fiscal.position.account",
                    "account.fiscal.position.account",
                    snapshot,
                    [("position_id.company_id", "=", company.id)],
                ),
                "reconcile_models": traced(
                    "account.reconcile.model",
                    "account.reconcile.model",
                    snapshot,
                    [("company_id", "=", company.id)],
                ),
                "reconcile_model_lines": traced(
                    "account.reconcile.model.line",
                    "account.reconcile.model.line",
                    snapshot,
                    [("model_id.company_id", "=", company.id)],
                ),
                "analytic_accounts": traced(
                    "account.analytic.account",
                    "account.analytic.account",
                    snapshot,
                    [("company_id", "=", company.id)],
                ),
                "currency_rates": traced(
                    "res.currency.rate", "res.currency.rate", snapshot,
                    [("company_id", "=", company.id)],
                ),
            }
            imported_journal_methods = self.env[
                "account.payment.method.line"
            ].browse()
            for journal in imported["journals"]:
                imported_journal_methods |= (
                    journal.inbound_payment_method_line_ids
                    | journal.outbound_payment_method_line_ids
                )
            method_compatibility = payment_method_compatibility.get(
                source_company_id,
                {},
            )
            expected_candidates = {
                "account_group_count": source.get("account_group_count"),
                "account_count": source.get("account_count"),
                "active_account_count": source.get("active_account_count"),
                "journal_count": source.get("journal_count"),
                "active_journal_count": source.get("active_journal_count"),
                "payment_method_line_count": (
                    method_compatibility.get("mapped_count")
                    if method_compatibility
                    else source.get("payment_method_line_count")
                ),
                "tax_group_count": source.get("tax_group_count"),
                "tax_count": source.get("tax_count"),
                "active_tax_count": source.get("active_tax_count"),
                "tax_repartition_line_count": source.get(
                    "tax_repartition_line_count",
                ),
                "fiscal_position_count": source.get(
                    "fiscal_position_count",
                ),
                "active_fiscal_position_count": source.get(
                    "active_fiscal_position_count",
                ),
                "fiscal_position_account_mapping_count": source.get(
                    "fiscal_position_account_mapping_count",
                ),
                "fiscal_position_tax_link_count": source.get(
                    "fiscal_position_tax_link_count",
                ),
                "reconcile_model_count": source.get(
                    "reconcile_model_count",
                ),
                "active_reconcile_model_count": source.get(
                    "active_reconcile_model_count",
                ),
                "reconcile_model_line_count": source.get(
                    "reconcile_model_line_count",
                ),
                "analytic_account_count": source.get(
                    "analytic_account_count",
                ),
                "active_analytic_account_count": source.get(
                    "active_analytic_account_count",
                ),
                "currency_rate_count": source.get("currency_rate_count"),
                "expense_allowed_payment_method_line_count": source.get(
                    "expense_allowed_payment_method_line_count",
                ),
                "shared_payment_term_count": source.get(
                    "shared_payment_term_count",
                ),
                "shared_payment_term_line_count": source.get(
                    "shared_payment_term_line_count",
                ),
                "shared_analytic_plan_count": source.get(
                    "shared_analytic_plan_count",
                ),
                "shared_tax_tag_count": source.get("shared_tax_tag_count"),
            }
            expected = {
                key: value
                for key, value in expected_candidates.items()
                if value is not None
            }
            imported_positions = imported["fiscal_positions"]
            actual_candidates = {
                "account_group_count": len(imported["account_groups"]),
                "account_count": len(imported["accounts"]),
                "active_account_count": len(
                    imported["accounts"].filtered("active"),
                ),
                "journal_count": len(imported["journals"]),
                "active_journal_count": len(
                    imported["journals"].filtered("active"),
                ),
                "payment_method_line_count": len(imported_journal_methods),
                "tax_group_count": len(imported["tax_groups"]),
                "tax_count": len(imported["taxes"]),
                "active_tax_count": len(imported["taxes"].filtered("active")),
                "tax_repartition_line_count": len(
                    imported["tax_repartition_lines"],
                ),
                "fiscal_position_count": len(imported_positions),
                "active_fiscal_position_count": len(
                    imported_positions.filtered("active"),
                ),
                "fiscal_position_account_mapping_count": len(
                    imported["fiscal_position_accounts"],
                ),
                "fiscal_position_tax_link_count": sum(
                    len(position.tax_ids) for position in imported_positions
                ),
                "reconcile_model_count": len(imported["reconcile_models"]),
                "active_reconcile_model_count": len(
                    imported["reconcile_models"].filtered("active"),
                ),
                "reconcile_model_line_count": len(
                    imported["reconcile_model_lines"],
                ),
                "analytic_account_count": len(imported["analytic_accounts"]),
                "active_analytic_account_count": len(
                    imported["analytic_accounts"].filtered("active"),
                ),
                "currency_rate_count": len(imported["currency_rates"]),
                "expense_allowed_payment_method_line_count": len(
                    company.company_expense_allowed_payment_method_line_ids,
                ),
                "shared_payment_term_count": len(traced(
                    "account.payment.term", "account.payment.term", snapshot,
                    [("company_id", "=", False)],
                )),
                "shared_payment_term_line_count": len(traced(
                    "account.payment.term.line",
                    "account.payment.term.line",
                    snapshot,
                    [("payment_id.company_id", "=", False)],
                )),
                "shared_analytic_plan_count": len(traced(
                    "account.analytic.plan",
                    "account.analytic.plan",
                    snapshot,
                )),
                "shared_tax_tag_count": len(traced(
                    "account.account.tag",
                    "account.account.tag",
                    snapshot,
                )),
            }
            actual = {key: actual_candidates[key] for key in expected}
            checks = {
                key: actual[key] == expected[key]
                for key in expected
            }
            company_settings_expected = {
                key: source[key]
                for key in (
                    "currency_name",
                    "fiscal_country_code",
                    "fiscalyear_last_day",
                    "fiscalyear_last_month",
                    "fiscalyear_lock_date",
                    "tax_lock_date",
                    "sale_lock_date",
                    "purchase_lock_date",
                    "hard_lock_date",
                    "tax_calculation_rounding_method",
                    "tax_exigibility",
                )
                if key in source
            }
            company_settings_actual = {
                "currency_name": company.currency_id.name,
                "fiscal_country_code": company.account_fiscal_country_id.code,
                "fiscalyear_last_day": company.fiscalyear_last_day,
                "fiscalyear_last_month": company.fiscalyear_last_month,
                "fiscalyear_lock_date": company.fiscalyear_lock_date,
                "tax_lock_date": company.tax_lock_date,
                "sale_lock_date": company.sale_lock_date,
                "purchase_lock_date": company.purchase_lock_date,
                "hard_lock_date": company.hard_lock_date,
                "tax_calculation_rounding_method": (
                    company.tax_calculation_rounding_method
                ),
                "tax_exigibility": company.tax_exigibility,
            }
            for key, expected_value in company_settings_expected.items():
                actual_value = company_settings_actual[key]
                checks[f"company_{key}"] = (
                    (actual_value or False) == (expected_value or False)
                )
            if method_compatibility:
                checks["payment_method_lines_fully_classified"] = (
                    not method_compatibility["blocking"]
                    and method_compatibility["classified_count"]
                    == method_compatibility["source_count"]
                )
            source_expense_journal_id = source.get("expense_journal_id")
            if "expense_journal_id" in source:
                checks["source_expense_journal"] = (
                    not source_expense_journal_id
                    or company.expense_journal_id.rebuild_source_id
                    == source_expense_journal_id
                )
                checks["operational_expense_journal"] = bool(
                    company.expense_journal_id,
                )
            mismatch_count += int(not all(checks.values()))
            company_results.append({
                "source_company_id": source_company_id,
                "target_company_id": company.id,
                "company_name": company.name,
                "expected": expected,
                "actual": actual,
                "checks": checks,
                "company_settings_expected": company_settings_expected,
                "company_settings_actual": company_settings_actual,
                "expense_journal": {
                    "source_journal_id": source_expense_journal_id,
                    "target_journal_id": company.expense_journal_id.id,
                    "target_source_journal_id": (
                        company.expense_journal_id.rebuild_source_id
                    ),
                    "target_created_operational_default": bool(
                        company.expense_journal_id
                        and not company.expense_journal_id.rebuild_source_id
                    ),
                },
                "payment_method_compatibility": method_compatibility,
            })
        return {
            "status": "passed" if not mismatch_count else "failed",
            "mismatch_count": mismatch_count,
            "companies": company_results,
        }

    def _sync_company_einvoice_configuration(
        self,
        conn,
        options,
        companies,
        journals,
    ):
        """Carry safe business setup forward without copying a live identity."""
        required_columns = (
            ("res_company", "account_peppol_contact_email"),
            ("res_company", "account_peppol_phone_number"),
            ("res_company", "peppol_purchase_journal_id"),
            ("res_company", "account_peppol_proxy_state"),
            ("res_partner", "peppol_eas"),
            ("res_partner", "peppol_endpoint"),
        )
        if not all(
            self._source_column_exists(conn, table, column)
            for table, column in required_columns
        ):
            return

        rows = self._fetchall(
            conn,
            """
            SELECT company.id,
                   company.account_peppol_contact_email,
                   company.account_peppol_phone_number,
                   company.peppol_purchase_journal_id,
                   company.account_peppol_proxy_state,
                   partner.peppol_eas,
                   partner.peppol_endpoint
              FROM res_company company
              JOIN res_partner partner ON partner.id = company.partner_id
             WHERE company.id = ANY(%(source_company_ids)s)
             ORDER BY company.id
            """,
            options,
        )
        for row in rows:
            company = companies.get(row["id"])
            if not company:
                continue
            registry_digits = "".join(
                character
                for character in (company.company_registry or "")
                if character.isdigit()
            )
            values = {
                "rebuild_einvoice_provider": "odoo_pdp",
                "rebuild_einvoice_environment": "development",
                "rebuild_einvoice_activation_approved": False,
                "rebuild_einvoice_approved_by_id": False,
                "rebuild_einvoice_approved_at": False,
                "rebuild_einvoice_exchange_enabled": False,
                "account_peppol_proxy_state": "not_registered",
                "l10n_fr_pdp_send_to_ppf": False,
                "l10n_fr_pdp_pilot_phase": False,
            }
            if row["account_peppol_contact_email"]:
                values["account_peppol_contact_email"] = row[
                    "account_peppol_contact_email"
                ]
            if row["account_peppol_phone_number"]:
                values["account_peppol_phone_number"] = row[
                    "account_peppol_phone_number"
                ]
            if row["peppol_purchase_journal_id"] in journals:
                values["peppol_purchase_journal_id"] = journals[
                    row["peppol_purchase_journal_id"]
                ].id
            if (
                company.account_fiscal_country_id.code == "FR"
                and len(registry_digits) >= 9
            ):
                values.update({
                    "peppol_eas": "0225",
                    "peppol_endpoint": registry_digits[:9],
                })
            company.sudo().write(values)

    def _reconciliation_model_map(
        self,
        conn,
        options,
        companies,
        accounts,
        journals,
        partners,
        taxes,
        analytic_accounts=None,
    ):
        model_rows = self._fetchall(
            conn,
            """
            SELECT model.id, model.sequence, model.company_id, model.trigger,
                   model.match_amount, model.match_amount_min,
                   model.match_amount_max, model.match_label,
                   model.match_label_param, model.name, model.active,
                   model.created_automatically, model.use_count,
                   model.is_asking_for_autopost
            FROM account_reconcile_model model
            WHERE model.company_id = ANY(%(source_company_ids)s)
            ORDER BY model.company_id, model.sequence, model.id
            """,
            options,
        )
        line_rows = self._fetchall(
            conn,
            """
            SELECT line.id, line.model_id, line.sequence, line.account_id,
                   line.partner_id, line.amount_type, line.amount_string,
                   line.analytic_distribution, line.label
            FROM account_reconcile_model_line line
            JOIN account_reconcile_model model ON model.id = line.model_id
            WHERE model.company_id = ANY(%(source_company_ids)s)
            ORDER BY line.model_id, line.sequence, line.id
            """,
            options,
        )
        journal_rows = self._fetchall(
            conn,
            """
            SELECT relation.account_reconcile_model_id AS model_id,
                   relation.account_journal_id AS journal_id
            FROM account_journal_account_reconcile_model_rel relation
            JOIN account_reconcile_model model
              ON model.id = relation.account_reconcile_model_id
            WHERE model.company_id = ANY(%(source_company_ids)s)
            ORDER BY relation.account_reconcile_model_id,
                     relation.account_journal_id
            """,
            options,
        )
        partner_rows = self._fetchall(
            conn,
            """
            SELECT relation.account_reconcile_model_id AS model_id,
                   relation.res_partner_id AS partner_id
            FROM account_reconcile_model_res_partner_rel relation
            JOIN account_reconcile_model model
              ON model.id = relation.account_reconcile_model_id
            WHERE model.company_id = ANY(%(source_company_ids)s)
            ORDER BY relation.account_reconcile_model_id,
                     relation.res_partner_id
            """,
            options,
        )
        tax_rows = self._fetchall(
            conn,
            """
            SELECT relation.account_reconcile_model_line_id AS line_id,
                   relation.account_tax_id AS tax_id
            FROM account_reconcile_model_line_account_tax_rel relation
            JOIN account_reconcile_model_line line
              ON line.id = relation.account_reconcile_model_line_id
            JOIN account_reconcile_model model ON model.id = line.model_id
            WHERE model.company_id = ANY(%(source_company_ids)s)
            ORDER BY relation.account_reconcile_model_line_id,
                     relation.account_tax_id
            """,
            options,
        )
        lines_by_model = defaultdict(list)
        for row in line_rows:
            lines_by_model[row["model_id"]].append(row)
        journals_by_model = defaultdict(list)
        for row in journal_rows:
            journals_by_model[row["model_id"]].append(row["journal_id"])
        partners_by_model = defaultdict(list)
        for row in partner_rows:
            partners_by_model[row["model_id"]].append(row["partner_id"])
        taxes_by_line = defaultdict(list)
        for row in tax_rows:
            taxes_by_line[row["line_id"]].append(row["tax_id"])

        source_model_ids = {row["id"] for row in model_rows}
        source_line_ids = {row["id"] for row in line_rows}
        missing_references = []
        for row in line_rows:
            if row["account_id"] and row["account_id"] not in accounts:
                missing_references.append(
                    f"line {row['id']} account {row['account_id']}",
                )
            if row["partner_id"] and row["partner_id"] not in partners:
                missing_references.append(
                    f"line {row['id']} partner {row['partner_id']}",
                )
            for source_tax_id in taxes_by_line[row["id"]]:
                if source_tax_id not in taxes:
                    missing_references.append(
                        f"line {row['id']} tax {source_tax_id}",
                    )
        for source_model_id, source_journal_ids in journals_by_model.items():
            for source_journal_id in source_journal_ids:
                if source_journal_id not in journals:
                    missing_references.append(
                        f"model {source_model_id} journal {source_journal_id}",
                    )
        for source_model_id, source_partner_ids in partners_by_model.items():
            for source_partner_id in source_partner_ids:
                if source_partner_id not in partners:
                    missing_references.append(
                        f"model {source_model_id} partner {source_partner_id}",
                    )
        if missing_references:
            raise ValueError(
                "Reconciliation-model references were not reconstructed: "
                + "; ".join(missing_references),
            )

        mapped = {}
        Model = self.env["account.reconcile.model"].with_context(
            active_test=False,
            tracking_disable=True,
        )
        for row in model_rows:
            company = companies[row["company_id"]]
            name = (
                self._source_text(row["name"])
                or f"Source reconciliation model {row['id']}"
            )
            model = Model.search([
                ("rebuild_source_model", "=", "account.reconcile.model"),
                ("rebuild_source_id", "=", row["id"]),
                (
                    "rebuild_source_snapshot",
                    "=",
                    options.get("source_snapshot_id"),
                ),
            ], limit=1)
            if not model:
                model = Model.search([
                    ("name", "=", name),
                    ("company_id", "=", company.id),
                    ("rebuild_source_model", "=", False),
                ], limit=1)
            vals = {
                "name": name,
                "sequence": row["sequence"] or 10,
                "company_id": company.id,
                "active": bool(row["active"]),
                "trigger": row["trigger"] or "manual",
                "match_amount": row["match_amount"] or False,
                "match_amount_min": self._amount(row["match_amount_min"]),
                "match_amount_max": self._amount(row["match_amount_max"]),
                "match_label": row["match_label"] or False,
                "match_label_param": row["match_label_param"] or False,
                "match_journal_ids": [
                    Command.set([
                        journals[source_id].id
                        for source_id in journals_by_model[row["id"]]
                    ]),
                ],
                "match_partner_ids": [
                    Command.set([
                        partners[source_id].id
                        for source_id in partners_by_model[row["id"]]
                    ]),
                ],
                **self._trace_values(
                    "account.reconcile.model",
                    row["id"],
                    options,
                ),
                "rebuild_import_note": (
                    "Native/OCA functional rule imported. Source-only "
                    f"created_automatically={bool(row['created_automatically'])}, "
                    f"use_count={row['use_count'] or 0}, "
                    "is_asking_for_autopost="
                    f"{bool(row['is_asking_for_autopost'])}; these UI/runtime "
                    "counters are evidence, not executable configuration."
                ),
                "rebuild_source_use_count": row["use_count"] or 0,
                "rebuild_source_created_automatically": bool(
                    row["created_automatically"],
                ),
                "rebuild_source_asked_for_autopost": bool(
                    row["is_asking_for_autopost"],
                ),
            }
            if model:
                model.write(vals)
            else:
                model = Model.create(vals)

            line_commands = [Command.clear()]
            for line in lines_by_model[row["id"]]:
                analytic_distribution = (
                    self._native_replay_analytic_distribution(
                        line["analytic_distribution"],
                        analytic_accounts or {},
                    )
                    if line["analytic_distribution"]
                    else False
                )
                line_commands.append(Command.create({
                    "sequence": line["sequence"] or 10,
                    "account_id": (
                        accounts[line["account_id"]].id
                        if line["account_id"]
                        else False
                    ),
                    "partner_id": (
                        partners[line["partner_id"]].id
                        if line["partner_id"]
                        else False
                    ),
                    "label": self._source_text(line["label"]),
                    "amount_type": line["amount_type"] or "percentage",
                    "amount_string": line["amount_string"] or "100",
                    "analytic_distribution": analytic_distribution,
                    "tax_ids": [
                        Command.set([
                            taxes[source_id].id
                            for source_id in taxes_by_line[line["id"]]
                        ]),
                    ],
                    **self._trace_values(
                        "account.reconcile.model.line",
                        line["id"],
                        options,
                    ),
                }))
            model.write({"line_ids": line_commands})
            mapped[row["id"]] = model

        return mapped, {
            "source_model_count": len(source_model_ids),
            "source_line_count": len(source_line_ids),
            "journal_relation_count": len(journal_rows),
            "partner_relation_count": len(partner_rows),
            "tax_relation_count": len(tax_rows),
            "mapped_model_count": len(mapped),
            "mapped_line_count": self.env[
                "account.reconcile.model.line"
            ].search_count([
                (
                    "rebuild_source_model",
                    "=",
                    "account.reconcile.model.line",
                ),
                (
                    "rebuild_source_snapshot",
                    "=",
                    options.get("source_snapshot_id"),
                ),
                (
                    "model_id",
                    "in",
                    [model.id for model in mapped.values()] or [0],
                ),
            ]),
            "missing_reference_count": len(missing_references),
            "source_runtime_metadata_classification": (
                "NOT_MIGRATED_NON_EXECUTABLE_EVIDENCE"
            ),
        }

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
            self.env["ir.config_parameter"].sudo().get_str("analytic.project_plan", "0") or 0
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

    def _import_analytic_lines(
        self,
        conn,
        options,
        companies,
        partners,
        accounts,
        analytic_plans,
        analytic_accounts,
        products,
    ):
        source_plan_columns = [
            row["column_name"]
            for row in self._fetchall(
                conn,
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'account_analytic_line'
                   AND column_name ~ '^x_plan[0-9]+_id$'
                 ORDER BY ordinal_position
                """,
            )
        ]
        # Column names come only from information_schema and are constrained by
        # the expression above.  Selecting them dynamically is required because
        # Odoo assigns x_planN_id names according to each database's plan order.
        source_plan_select = "".join(
            f', analytic."{column_name}"'
            for column_name in source_plan_columns
        )
        rows = self._fetchall(
            conn,
            f"""
            SELECT analytic.id, analytic.account_id, analytic.partner_id,
                   analytic.company_id, analytic.currency_id, analytic.name,
                   analytic.category, analytic.date, analytic.amount,
                   analytic.unit_amount, analytic.general_account_id,
                   analytic.journal_id, analytic.move_line_id, analytic.code,
                   analytic.ref, analytic.product_id{source_plan_select}
              FROM account_analytic_line analytic
             WHERE analytic.company_id = ANY(%(source_company_ids)s)
               AND analytic.date BETWEEN %(date_from)s AND %(date_to)s
             ORDER BY analytic.date, analytic.id
            """,
            options,
        )
        AnalyticLine = self.env["account.analytic.line"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        source_move_line_ids = [row["move_line_id"] for row in rows if row["move_line_id"]]
        move_lines_by_source_id = self._source_trace_record_map(
            "account.move.line",
            source_move_line_ids,
            options,
        )
        existing_lines = self._source_trace_record_map(
            "account.analytic.line",
            [row["id"] for row in rows],
            options,
        )
        imported_line_ids = []
        pending = []
        linked_to_move_line_count = 0
        linked_product_count = 0
        skipped_missing_account = []
        missing_dimension_accounts = []
        conflicting_plan_accounts = []
        missing_products = []
        seen_source_ids = set()
        target_plan_columns = set(
            self.env["account.analytic.line"]._get_plan_fnames(),
        )
        for row in rows:
            analytic_account = analytic_accounts.get(row["account_id"])
            if not analytic_account:
                skipped_missing_account.append(row["id"])
                continue
            source_dimension_ids = [
                row[column_name]
                for column_name in ["account_id", *source_plan_columns]
                if row.get(column_name)
            ]
            target_dimensions = {}
            for source_account_id in source_dimension_ids:
                target_account = analytic_accounts.get(source_account_id)
                if not target_account:
                    missing_dimension_accounts.append({
                        "source_line_id": row["id"],
                        "source_account_id": source_account_id,
                    })
                    continue
                target_column = target_account.plan_id._column_name()
                existing_target = target_dimensions.get(target_column)
                if existing_target and existing_target != target_account.id:
                    conflicting_plan_accounts.append({
                        "source_line_id": row["id"],
                        "target_column": target_column,
                        "source_account_ids": source_dimension_ids,
                    })
                    continue
                target_dimensions[target_column] = target_account.id
            product = products.get(row["product_id"])
            if row["product_id"] and not product:
                missing_products.append({
                    "source_line_id": row["id"],
                    "source_product_id": row["product_id"],
                })
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
                "product_id": product.id if product else False,
                **self._trace_values("account.analytic.line", row["id"], options),
            }
            # Clear every restored plan column before assigning the source
            # dimensions so repeated imports also reproduce removed values.
            vals.update(dict.fromkeys(target_plan_columns, False))
            vals.update(target_dimensions)
            if product:
                linked_product_count += 1
            target_move_line = move_lines_by_source_id.get(row["move_line_id"])
            if target_move_line:
                vals["move_line_id"] = target_move_line.id
                linked_to_move_line_count += 1
            existing = existing_lines.get(row["id"])
            if existing:
                existing.write(vals)
                imported_line_ids.append(existing.id)
            else:
                pending.append((row["id"], vals))
            seen_source_ids.add(row["id"])
        for batch in self._batched(pending, self._RELATION_BATCH_SIZE):
            created = AnalyticLine.create([vals for _source_id, vals in batch])
            imported_line_ids.extend(created.ids)
        stale_lines = AnalyticLine.search([
            ("rebuild_source_model", "=", "account.analytic.line"),
            ("rebuild_source_snapshot", "=", options.get("source_snapshot_id")),
            ("rebuild_source_id", "not in", list(seen_source_ids) or [0]),
        ])
        stale_lines.unlink()
        # When business-document fields are preserved, posting the imported
        # moves correctly creates native analytic items from each journal
        # item's distribution.  The source analytic items imported above are
        # the authoritative historical records, so retaining both sets would
        # double management reporting.  Remove only untraced native items
        # attached to this snapshot's traced journal items.  The context keeps
        # their distributions intact for normal document usability.
        generated_duplicate_lines = AnalyticLine.search([
            ("rebuild_source_model", "=", False),
            (
                "move_line_id.rebuild_source_model",
                "=",
                "account.move.line",
            ),
            (
                "move_line_id.rebuild_source_snapshot",
                "=",
                options.get("source_snapshot_id"),
            ),
            (
                "company_id",
                "in",
                [company.id for company in companies.values()],
            ),
        ])
        generated_duplicate_count = len(generated_duplicate_lines)
        generated_duplicate_lines.with_context(
            skip_analytic_sync=True,
        ).unlink()
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
        parity_blockers = {
            "missing_dimension_accounts": missing_dimension_accounts[:50],
            "conflicting_plan_accounts": conflicting_plan_accounts[:50],
            "missing_products": missing_products[:50],
        }
        if any(parity_blockers.values()):
            self.env["rebuild.account.discrepancy"].create({
                "name": "Source analytic dimensions or products could not be restored exactly",
                "import_run_id": self.id,
                "severity": "P1",
                "classification": "missing_capability",
                "status": "open",
                "period_key": f"{options['date_from']}:{options['date_to']}",
                "source_model": "account.analytic.line",
                "source_value": json.dumps(parity_blockers, sort_keys=True),
                "accounting_impact": (
                    "The statutory ledger is unchanged, but management reporting "
                    "would lose source analytic or product attribution."
                ),
                "recommendation": (
                    "Restore every referenced analytic account and product, resolve "
                    "same-plan conflicts, and rerun exact replay."
                ),
            })
        return {
            "source_analytic_plan_count": len(analytic_plans),
            "source_analytic_account_count": len(analytic_accounts),
            "source_analytic_line_count": len(rows),
            "imported_analytic_line_count": len(imported_line_ids),
            "linked_to_move_line_count": linked_to_move_line_count,
            "source_product_link_count": len([
                row for row in rows if row["product_id"]
            ]),
            "linked_product_count": linked_product_count,
            "unlinked_source_analytic_line_count": len(rows) - linked_to_move_line_count - len(skipped_missing_account),
            "skipped_missing_account_count": len(skipped_missing_account),
            "missing_dimension_account_count": len(missing_dimension_accounts),
            "conflicting_plan_account_count": len(conflicting_plan_accounts),
            "missing_product_count": len(missing_products),
            "removed_generated_duplicate_count": (
                generated_duplicate_count
            ),
        }

    @staticmethod
    def _target_payment_state(source_state):
        return {
            "reconciled": "reconciled",
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
            method = PaymentMethod.search([
                ("code", "=", row["payment_method_code"]),
                ("payment_type", "=", row["payment_method_type"]),
            ], limit=1)
            if not method:
                continue
            line_domain = [("payment_method_id", "=", method.id)]
            if journal:
                line_domain.append(("journal_id", "=", journal.id))
            payment_account = accounts.get(row["payment_account_id"])
            if payment_account:
                line_domain.append(("payment_account_id", "=", payment_account.id))
            line = PaymentMethodLine.search(line_domain, order="id", limit=1)
            if not line:
                if not journal:
                    continue
                vals = {
                    "name": row["name"] or method.name,
                    "sequence": row["sequence"] or 10,
                    "journal_id": journal.id,
                    "payment_method_id": method.id,
                }
                if payment_account:
                    vals["payment_account_id"] = payment_account.id
                line = PaymentMethodLine.create(vals)
            method_lines[row["id"]] = line
        return method_lines

    def _payment_method_line_compatibility(
        self,
        conn,
        options,
        method_lines,
    ):
        """Classify every source method line without inventing capabilities.

        Native Community methods are mapped by ``_payment_method_line_map``.
        Unused Enterprise-only methods remain explicit reconstruction evidence.
        Any unrecognised or used-but-unavailable method blocks parity.
        """
        rows = self._fetchall(
            conn,
            """
            SELECT pml.id, journal.company_id, method.code,
                   method.payment_type,
                   (SELECT COUNT(*) FROM account_payment payment
                     WHERE payment.payment_method_line_id = pml.id)
                       AS payment_usage_count,
                   (SELECT COUNT(*) FROM hr_expense expense
                     WHERE expense.payment_method_line_id = pml.id)
                       AS expense_usage_count
              FROM account_payment_method_line pml
              JOIN account_payment_method method
                ON method.id = pml.payment_method_id
              JOIN account_journal journal ON journal.id = pml.journal_id
             WHERE journal.company_id = ANY(%(source_company_ids)s)
             ORDER BY journal.company_id, pml.id
            """,
            options,
        )
        results = {}
        for source_company_id in self._source_company_ids(options):
            company_rows = [
                row for row in rows
                if row["company_id"] == source_company_id
            ]
            mapped = []
            unavailable = []
            blocking = []
            for row in company_rows:
                detail = {
                    "source_payment_method_line_id": row["id"],
                    "code": row["code"],
                    "payment_type": row["payment_type"],
                    "payment_usage_count": row["payment_usage_count"],
                    "expense_usage_count": row["expense_usage_count"],
                }
                if row["id"] in method_lines:
                    mapped.append({
                        **detail,
                        "target_payment_method_line_id": (
                            method_lines[row["id"]].id
                        ),
                        "classification": "native_mapped",
                    })
                    continue
                is_unused = not (
                    row["payment_usage_count"]
                    or row["expense_usage_count"]
                )
                if (
                    row["code"] in ENTERPRISE_ONLY_PAYMENT_METHOD_CODES
                    and is_unused
                ):
                    unavailable.append({
                        **detail,
                        "classification": "unused_enterprise_only",
                    })
                else:
                    blocking.append({
                        **detail,
                        "classification": (
                            "used_unavailable"
                            if not is_unused
                            else "unknown_unavailable"
                        ),
                    })
            results[source_company_id] = {
                "source_count": len(company_rows),
                "mapped_count": len(mapped),
                "unavailable_unused_count": len(unavailable),
                "classified_count": len(mapped) + len(unavailable),
                "mapped": mapped,
                "unavailable_unused": unavailable,
                "blocking": blocking,
            }
        return results

    def _move_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT id, name, ref, state, move_type, journal_id, company_id, partner_id,
                   currency_id, date, invoice_date, invoice_date_due, payment_reference,
                   fiscal_position_id, invoice_payment_term_id,
                   sequence_prefix, sequence_number, secure_sequence_number
            FROM account_move
            WHERE company_id = ANY(%(source_company_ids)s)
            ORDER BY date, id
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
                   aml.quantity, aml.price_unit, aml.discount,
                   aml.analytic_distribution,
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
            WHERE am.company_id = ANY(%(source_company_ids)s)
            ORDER BY aml.move_id, aml.sequence, aml.id
            """,
            options,
        )
        by_move = defaultdict(list)
        for row in rows:
            by_move[row["move_id"]].append(row)
        return by_move


    def _payment_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT payment.id, payment.move_id, payment.journal_id,
                   payment.company_id, payment.partner_bank_id,
                   paired_internal_transfer_payment_id, payment_method_line_id,
                   currency_id, partner_id, outstanding_account_id,
                   destination_account_id, name, state, payment_type,
                   partner_type, memo, payment_reference, date, amount,
                   amount_company_currency_signed, is_reconciled, is_matched,
                   is_sent,
                   COALESCE((
                       SELECT array_agg(relation.invoice_id ORDER BY relation.invoice_id)
                       FROM account_move__account_payment relation
                       WHERE relation.payment_id = payment.id
                   ), ARRAY[]::integer[]) AS invoice_ids
            FROM account_payment payment
            WHERE payment.company_id = ANY(%(source_company_ids)s)
            ORDER BY payment.id
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
            WITH direct_attachments AS (
                SELECT ia.id,
                       ia.res_model,
                       ia.res_id,
                       ia.res_model AS source_attachment_res_model,
                       ia.res_id AS source_attachment_res_id,
                       ia.company_id,
                       ia.name,
                       ia.res_field,
                       ia.type,
                       ia.url,
                       ia.store_fname,
                       ia.checksum,
                       ia.file_size,
                       ia.mimetype,
                       ia.description,
                       ia.public,
                       (
                           ia.res_model = 'account.move'
                           AND ia.id = move.message_main_attachment_id
                       ) AS is_main,
                       source_message.id AS source_message_id,
                       source_message.date AS source_message_date,
                       source_message.subject AS source_message_subject
                  FROM ir_attachment ia
                  LEFT JOIN account_move move
                    ON ia.res_model = 'account.move'
                   AND ia.res_id = move.id
                  LEFT JOIN account_asset asset
                    ON ia.res_model = 'account.asset'
                   AND ia.res_id = asset.id
                  LEFT JOIN LATERAL (
                       SELECT message.id, message.date, message.subject
                         FROM message_attachment_rel relation
                         JOIN mail_message message
                           ON message.id = relation.message_id
                        WHERE relation.attachment_id = ia.id
                          AND message.model = ia.res_model
                          AND message.res_id = ia.res_id
                        ORDER BY message.date, message.id
                        LIMIT 1
                  ) source_message ON TRUE
                 WHERE ia.type = 'binary'
                   AND (
                        (
                            ia.res_model = 'account.move'
                            AND move.company_id = ANY(%(source_company_ids)s)
                            AND (
                                (
                                    move.state = 'posted'
                                    AND move.date BETWEEN %(date_from)s AND %(date_to)s
                                )
                                OR (
                                    move.state <> 'posted'
                                    AND move.date >= %(date_from)s
                                )
                            )
                        )
                        OR (
                            ia.res_model = 'account.asset'
                            AND asset.company_id = ANY(%(source_company_ids)s)
                        )
                   )
            ),
            chatter_only_attachments AS (
                SELECT ia.id,
                       message.model AS res_model,
                       message.res_id,
                       ia.res_model AS source_attachment_res_model,
                       ia.res_id AS source_attachment_res_id,
                       ia.company_id,
                       ia.name,
                       ia.res_field,
                       ia.type,
                       ia.url,
                       ia.store_fname,
                       ia.checksum,
                       ia.file_size,
                       ia.mimetype,
                       ia.description,
                       ia.public,
                       FALSE AS is_main,
                       message.id AS source_message_id,
                       message.date AS source_message_date,
                       message.subject AS source_message_subject
                  FROM ir_attachment ia
                  JOIN LATERAL (
                       SELECT candidate.id,
                              candidate.model,
                              candidate.res_id,
                              candidate.date,
                              candidate.subject
                         FROM message_attachment_rel relation
                         JOIN mail_message candidate
                           ON candidate.id = relation.message_id
                         JOIN account_move move
                           ON candidate.model = 'account.move'
                          AND candidate.res_id = move.id
                        WHERE relation.attachment_id = ia.id
                          AND move.company_id = ANY(%(source_company_ids)s)
                          AND (
                              (
                                  move.state = 'posted'
                                  AND move.date BETWEEN %(date_from)s AND %(date_to)s
                              )
                              OR (
                                  move.state <> 'posted'
                                  AND move.date >= %(date_from)s
                              )
                          )
                        ORDER BY candidate.date, candidate.id
                        LIMIT 1
                  ) message ON TRUE
                 WHERE ia.type = 'binary'
                   AND ia.res_model IS NULL
            )
            SELECT *
              FROM direct_attachments
            UNION ALL
            SELECT *
              FROM chatter_only_attachments
             ORDER BY id
            """,
            options,
        )

    def _target_for_attachment(self, row, options):
        trace_models = options.get("attachment_target_trace_models") or {}
        if row["res_model"] == "account.move":
            account_move_trace_models = trace_models.get(
                "account.move",
                [
                    "account.move.native_engine_replay",
                    "account.move.native_expense_replay",
                    "account.move",
                ],
            )
            targets = self.env["account.move"].with_context(active_test=False).search([
                ("rebuild_source_model", "in", account_move_trace_models),
                ("rebuild_source_id", "=", row["res_id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ])
            target = self.env["account.move"]
            for trace_model in account_move_trace_models:
                candidates = targets.filtered(
                    lambda move, model=trace_model: (
                        move.rebuild_source_model == model
                    ),
                )
                if len(candidates) > 1:
                    raise ValueError(
                        "Source account.move %s has multiple %s attachment targets."
                        % (row["res_id"], trace_model),
                    )
                if candidates:
                    target = candidates
                    break
            if target:
                return "account.move", target
            return None, self.env["account.move"]
        if row["res_model"] == "hr.expense":
            expense_trace_models = trace_models.get(
                "hr.expense",
                ["hr.expense"],
            )
            target = self.env["hr.expense"].with_context(active_test=False).search([
                ("rebuild_source_model", "in", expense_trace_models),
                ("rebuild_source_id", "=", row["res_id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            return "hr.expense", target
        if row["res_model"] == "account.asset":
            target = self.env["account.asset"].with_context(active_test=False).search([
                ("rebuild_source_model", "=", "account.asset"),
                ("rebuild_source_id", "=", row["res_id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if target:
                return "account.asset", target
            snapshot = self.env["rebuild.account.asset"].with_context(
                active_test=False,
            ).search([
                ("rebuild_source_model", "=", "account_asset"),
                ("rebuild_source_id", "=", row["res_id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            return "rebuild.account.asset", snapshot
        return None, self.env["ir.attachment"]

    def repair_final_account_move_attachment_targets(self):
        """Attach source evidence to the one surviving native account move.

        Native document materialization can replace an intermediate move after
        the exact attachment replay.  Odoo deliberately keeps the binary in
        that case but clears its ``res_model``/``res_id``.  At the end of the
        one-shot migration the source identity must resolve to exactly one
        durable move; anything else is unsafe to guess.
        """
        self.ensure_one()
        snapshot = self.source_snapshot_id
        if not snapshot:
            raise ValueError("Attachment target repair requires a source snapshot.")

        Attachment = self.env["ir.attachment"].sudo()
        attachments = Attachment.search([
            ("rebuild_source_model", "=", "ir.attachment"),
            ("rebuild_source_snapshot", "=", snapshot),
            ("rebuild_source_attachment_res_model", "=", "account.move"),
            ("rebuild_source_attachment_res_id", "!=", False),
        ])
        source_move_ids = set(
            attachments.mapped("rebuild_source_attachment_res_id"),
        )
        moves = self.env["account.move"].sudo().with_context(
            active_test=False,
        ).search([
            ("rebuild_source_snapshot", "=", snapshot),
            ("rebuild_source_id", "in", list(source_move_ids) or [0]),
        ])
        moves_by_source = defaultdict(lambda: self.env["account.move"])
        for move in moves:
            moves_by_source[move.rebuild_source_id] |= move

        missing = []
        ambiguous = []
        repaired = self.env["ir.attachment"]
        main_repaired = 0
        for attachment in attachments:
            source_move_id = attachment.rebuild_source_attachment_res_id
            candidates = moves_by_source[source_move_id]
            if not candidates:
                missing.append(source_move_id)
                continue
            if len(candidates) != 1:
                ambiguous.append({
                    "source_move_id": source_move_id,
                    "target_move_ids": candidates.ids,
                    "target_trace_models": candidates.mapped(
                        "rebuild_source_model",
                    ),
                })
                continue
            move = candidates
            if (
                attachment.res_model != "account.move"
                or attachment.res_id != move.id
            ):
                attachment.write({
                    "res_model": "account.move",
                    "res_id": move.id,
                })
                repaired |= attachment
            if (
                attachment.rebuild_source_is_main
                and move.message_main_attachment_id != attachment
            ):
                move._message_set_main_attachment_id(
                    attachment,
                    force=True,
                    filter_xml=False,
                )
                main_repaired += 1

        if missing or ambiguous:
            raise ValueError(
                "Final Accounting attachment targets are incomplete: %s"
                % json.dumps(
                    {
                        "ambiguous": ambiguous[:20],
                        "missing_source_move_ids": sorted(set(missing))[:20],
                    },
                    sort_keys=True,
                ),
            )
        return {
            "checked_attachment_count": len(attachments),
            "repaired_attachment_count": len(repaired),
            "repaired_main_attachment_count": main_repaired,
        }

    def _import_attachments(self, conn, options, companies, rows=None):
        rows = self._attachment_rows(conn, options) if rows is None else rows
        filestore_path = options.get("source_filestore_path") or "/mnt/accounting-source/filestore"
        Attachment = self.env["ir.attachment"].sudo().with_context(
            image_no_postprocess=True,
            tracking_disable=True,
            mail_create_nolog=True,
        )
        existing_attachments = self._source_trace_record_map(
            "ir.attachment",
            [row["id"] for row in rows],
            options,
        )
        imported_ids = []
        missing_files = []
        unmapped_targets = []
        checksum_mismatches = []
        duplicate_traces = []
        main_attachment_mismatches = []
        imported_main_attachment_count = 0
        imported_chatter_attachment_count = 0
        chatter_groups = defaultdict(list)
        for row in rows:
            target_model, target_record = self._target_for_attachment(row, options)
            if not target_model or not target_record:
                unmapped_targets.append(row)
                continue
            raw = None
            actual_checksum = row["checksum"]
            actual_size = row["file_size"]
            if row["type"] == "binary":
                if not row["store_fname"]:
                    missing_files.append({
                        **row,
                        "missing_reason": (
                            "source attachment has no store_fname"
                        ),
                    })
                    continue
                source_path = os.path.join(
                    filestore_path,
                    row["store_fname"],
                )
                if not os.path.isfile(source_path):
                    missing_files.append({
                        **row,
                        "missing_reason": (
                            f"file not found at {source_path}"
                        ),
                    })
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
                if (
                    row["file_size"] is not None
                    and actual_size != row["file_size"]
                ):
                    checksum_mismatches.append({
                        **row,
                        "expected_size": row["file_size"],
                        "actual_size": actual_size,
                        "actual_checksum": actual_checksum,
                    })
                    continue

            attachment = existing_attachments.get(row["id"])
            vals = {
                "name": row["name"] or f"Source attachment {row['id']}",
                "res_model": target_model,
                "res_id": target_record.id,
                "type": row["type"],
                "url": row["url"] if row["type"] == "url" else False,
                "mimetype": row["mimetype"],
                "description": row["description"],
                "public": bool(row["public"]),
                "rebuild_source_attachment_res_model": (
                    row.get("source_attachment_res_model")
                ),
                "rebuild_source_attachment_res_id": (
                    row.get("source_attachment_res_id")
                ),
                "rebuild_source_message_id": row.get("source_message_id"),
                "rebuild_source_message_date": row.get("source_message_date"),
                "rebuild_source_message_subject": (
                    row.get("source_message_subject")
                ),
                "rebuild_source_is_main": bool(row.get("is_main")),
                "company_id": (
                    companies[row["company_id"]].id if row["company_id"] in companies
                    else getattr(target_record, "company_id", self.env.company).id
                ),
                "rebuild_import_note": (
                    (
                        "Imported from source URL attachment."
                        if row["type"] == "url"
                        else (
                            "Imported from source filestore path "
                            f"{row['store_fname']}; source checksum "
                            f"{row['checksum']} and size "
                            f"{row['file_size']} verified before import."
                        )
                    )
                ),
                **self._trace_values("ir.attachment", row["id"], options),
            }
            if row["res_field"]:
                vals["res_field"] = row["res_field"]
            if (
                row["type"] == "binary"
                and (
                    not attachment
                    or attachment.checksum != actual_checksum
                    or attachment.file_size != actual_size
                )
            ):
                vals["raw"] = raw
            if attachment:
                attachment.write(vals)
            else:
                attachment = Attachment.create(vals)
            if (
                row["type"] == "binary"
                and (
                    attachment.checksum != actual_checksum
                    or attachment.file_size != actual_size
                )
            ):
                checksum_mismatches.append({
                    **row,
                    "target_attachment_id": attachment.id,
                    "expected_checksum": actual_checksum,
                    "target_checksum": attachment.checksum,
                    "expected_size": actual_size,
                    "target_size": attachment.file_size,
                })
                continue
            if row.get("is_main"):
                if not hasattr(target_record, "_message_set_main_attachment_id"):
                    main_attachment_mismatches.append({
                        **row,
                        "target_model": target_model,
                        "target_id": target_record.id,
                        "missing_reason": "target model has no main-attachment support",
                    })
                    continue
                target_record.sudo()._message_set_main_attachment_id(
                    attachment,
                    force=True,
                    filter_xml=False,
                )
                if target_record.message_main_attachment_id != attachment:
                    main_attachment_mismatches.append({
                        **row,
                        "target_model": target_model,
                        "target_id": target_record.id,
                        "target_main_attachment_id": (
                            target_record.message_main_attachment_id.id
                        ),
                        "missing_reason": "source main attachment was not selected",
                    })
                    continue
                imported_main_attachment_count += 1
            if row.get("source_message_id"):
                chatter_groups[
                    target_model,
                    target_record.id,
                    row["source_message_id"],
                ].append(attachment)
            imported_ids.append(attachment.id)

        if options.get("defer_attachment_chatter_to_collaboration"):
            chatter_groups.clear()

        for (
            target_model,
            target_id,
            _source_message_id,
        ), attachments in chatter_groups.items():
            target_record = self.env[target_model].browse(target_id).exists()
            if not target_record or not hasattr(target_record, "message_post"):
                continue
            attachment_recordset = self.env["ir.attachment"].concat(
                *attachments,
            )
            existing_message = self.env["mail.message"].sudo().search([
                ("model", "=", target_model),
                ("res_id", "=", target_id),
                ("attachment_ids", "in", attachment_recordset[:1].ids),
            ], limit=1)
            if existing_message:
                missing_attachments = attachment_recordset - existing_message.attachment_ids
                if missing_attachments:
                    existing_message.attachment_ids = [
                        Command.link(attachment.id)
                        for attachment in missing_attachments
                    ]
            else:
                representative = attachment_recordset[:1]
                existing_message = target_record.sudo().with_context(
                    tracking_disable=True,
                    mail_create_nolog=True,
                    mail_create_nosubscribe=True,
                    mail_notify_force_send=False,
                ).message_post(
                    body=_(
                        "Supporting file restored from the source record's "
                        "chatter.",
                    ),
                    subject=representative.rebuild_source_message_subject
                    or _("Restored accounting evidence"),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                    attachment_ids=attachment_recordset.ids,
                )
                if representative.rebuild_source_message_date:
                    existing_message.sudo().write({
                        "date": representative.rebuild_source_message_date,
                    })
            imported_chatter_attachment_count += len(attachment_recordset)

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
        if main_attachment_mismatches:
            self.env["rebuild.account.discrepancy"].create({
                "name": "Source main accounting attachments are not selected on target records",
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
                        "target_model": row.get("target_model"),
                        "target_id": row.get("target_id"),
                        "target_main_attachment_id": row.get(
                            "target_main_attachment_id",
                        ),
                        "missing_reason": row["missing_reason"],
                    }
                    for row in main_attachment_mismatches[:50]
                ], ensure_ascii=False, sort_keys=True),
                "accounting_impact": (
                    "The evidence binary exists, but the native document preview "
                    "does not open the same main evidence selected in the source."
                ),
                "recommendation": (
                    "Repair main-attachment selection before accepting the native "
                    "document and expense evidence journey."
                ),
            })
        return {
            "source_attachment_count": len(rows),
            "imported_attachment_count": len(set(imported_ids)),
            "source_main_attachment_count": sum(
                bool(row.get("is_main"))
                for row in rows
            ),
            "imported_main_attachment_count": imported_main_attachment_count,
            "missing_file_count": len(missing_files),
            "unmapped_target_count": len(unmapped_targets),
            "checksum_mismatch_count": len(checksum_mismatches),
            "duplicate_trace_count": len(duplicate_traces),
            "main_attachment_mismatch_count": len(main_attachment_mismatches),
            "source_total_bytes": sum(int(row["file_size"] or 0) for row in rows),
            "source_chatter_attachment_count": sum(
                bool(row.get("source_message_id"))
                for row in rows
            ),
            "imported_chatter_attachment_count": (
                imported_chatter_attachment_count
            ),
            "deferred_chatter_attachment_count": (
                sum(bool(row.get("source_message_id")) for row in rows)
                if options.get("defer_attachment_chatter_to_collaboration")
                else 0
            ),
        }

    def _native_replay_document_attachment_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT attachment.id,
                   attachment.res_model,
                   attachment.res_id,
                   attachment.res_model AS source_attachment_res_model,
                   attachment.res_id AS source_attachment_res_id,
                   attachment.company_id,
                   attachment.name,
                   attachment.res_field,
                   attachment.type,
                   attachment.url,
                   attachment.store_fname,
                   attachment.checksum,
                   attachment.file_size,
                   attachment.mimetype,
                   attachment.description,
                   attachment.public,
                   (
                       attachment.id = move.message_main_attachment_id
                   ) AS is_main,
                   source_message.id AS source_message_id,
                   source_message.date AS source_message_date,
                   source_message.subject AS source_message_subject
              FROM ir_attachment attachment
              JOIN account_move move
               ON attachment.res_model = 'account.move'
               AND attachment.res_id = move.id
              LEFT JOIN LATERAL (
                   SELECT message.id, message.date, message.subject
                     FROM message_attachment_rel relation
                     JOIN mail_message message
                       ON message.id = relation.message_id
                    WHERE relation.attachment_id = attachment.id
                      AND message.model = 'account.move'
                      AND message.res_id = move.id
                    ORDER BY message.date, message.id
                    LIMIT 1
              ) source_message ON TRUE
             WHERE attachment.type = 'binary'
               AND move.company_id = ANY(%(source_company_ids)s)
               AND move.state = 'posted'
               AND move.move_type IN (
                   'out_invoice',
                   'out_refund',
                   'in_invoice',
                   'in_refund',
                   'out_receipt',
                   'in_receipt'
               )
               AND move.date BETWEEN %(date_from)s AND %(date_to)s
             ORDER BY attachment.id
            """,
            options,
        )

    def _native_replay_expense_attachment_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT attachment.id,
                   attachment.res_model,
                   attachment.res_id,
                   attachment.res_model AS source_attachment_res_model,
                   attachment.res_id AS source_attachment_res_id,
                   attachment.company_id,
                   attachment.name,
                   attachment.res_field,
                   attachment.type,
                   attachment.url,
                   attachment.store_fname,
                   attachment.checksum,
                   attachment.file_size,
                   attachment.mimetype,
                   attachment.description,
                   attachment.public,
                   (
                       attachment.id = expense.message_main_attachment_id
                   ) AS is_main,
                   source_message.id AS source_message_id,
                   source_message.date AS source_message_date,
                   source_message.subject AS source_message_subject
              FROM ir_attachment attachment
              JOIN hr_expense expense
               ON attachment.res_model = 'hr.expense'
               AND attachment.res_id = expense.id
              LEFT JOIN LATERAL (
                   SELECT message.id, message.date, message.subject
                     FROM message_attachment_rel relation
                     JOIN mail_message message
                       ON message.id = relation.message_id
                    WHERE relation.attachment_id = attachment.id
                      AND message.model = 'hr.expense'
                      AND message.res_id = expense.id
                    ORDER BY message.date, message.id
                    LIMIT 1
              ) source_message ON TRUE
             WHERE attachment.type IN ('binary', 'url')
               AND expense.company_id = ANY(%(source_company_ids)s)
               AND expense.date BETWEEN %(date_from)s AND %(date_to)s
             ORDER BY attachment.id
            """,
            options,
        )

    @staticmethod
    def _attachment_issue_count(stats):
        return sum(
            int(stats.get(field_name, 0))
            for field_name in (
                "missing_file_count",
                "unmapped_target_count",
                "checksum_mismatch_count",
                "duplicate_trace_count",
                "main_attachment_mismatch_count",
            )
        )


    def _import_bank_statement_lines(self, conn, options, companies, partners, journals, currencies):
        rows = self._bank_statement_line_rows(conn, options)
        source_move_ids = [row["move_id"] for row in rows if row["move_id"]]
        move_map = self._source_trace_record_map(
            "account.move",
            source_move_ids,
            options,
        )
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
        source_invoice_ids = {
            source_invoice_id
            for row in rows
            for source_invoice_id in (row["invoice_ids"] or [])
        }
        invoice_map = self._source_trace_record_map(
            "account.move",
            source_invoice_ids,
            options,
        )

        source_move_ids = [row["move_id"] for row in rows if row["move_id"]]
        move_map = self._source_trace_record_map(
            "account.move",
            source_move_ids,
            options,
        )

        Payment = self.env["account.payment"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
            skip_account_move_synchronization=True,
            skip_invoice_sync=True,
        )
        imported_payments = self.env["account.payment"]
        imported_no_entry_payments = self.env["account.payment"]
        skipped_without_imported_move = []
        missing_method_lines = []
        state_transformations = defaultdict(int)

        def raw_bool(value):
            return "null" if value is None else str(value).lower()

        def mismatched_values(record, values, field_names):
            mismatches = {}
            for field_name in field_names:
                if field_name not in values:
                    continue
                field = record._fields[field_name]
                actual = record[field_name]
                expected = values[field_name]
                if field.type == "many2one":
                    actual = actual.id or False
                    expected = expected or False
                elif field.type == "date":
                    actual = fields.Date.to_string(actual) if actual else False
                    expected = (
                        fields.Date.to_string(expected)
                        if expected
                        else False
                    )
                elif field.type in {"float", "monetary"}:
                    actual = self._amount(actual)
                    expected = self._amount(expected)
                elif field.type == "boolean":
                    actual = bool(actual)
                    expected = bool(expected)
                else:
                    actual = actual or False
                    expected = expected or False
                if actual != expected:
                    mismatches[field_name] = {
                        "actual": actual,
                        "expected": expected,
                    }
            return mismatches

        for row in rows:
            if not row["move_id"]:
                source_state = row["state"] or "unknown"
                payment_method_line = method_lines.get(row["payment_method_line_id"])
                if not payment_method_line:
                    missing_method_lines.append(row)
                    continue
                invoice_ids = [
                    invoice_map[source_id].id
                    for source_id in (row["invoice_ids"] or [])
                    if source_id in invoice_map
                ]
                vals = {
                    "name": row["name"] or row["memo"] or f"Source payment {row['id']}",
                    "company_id": companies[row["company_id"]].id,
                    "currency_id": currencies[row["currency_id"]].id,
                    "journal_id": journals[row["journal_id"]].id if row["journal_id"] in journals else False,
                    "partner_id": partners[row["partner_id"]].id if row["partner_id"] in partners else False,
                    "payment_method_line_id": payment_method_line.id,
                    "destination_account_id": accounts[row["destination_account_id"]].id if row["destination_account_id"] in accounts else False,
                    "date": row["date"],
                    "amount": self._amount(row["amount"]),
                    "payment_type": row["payment_type"] or "inbound",
                    "partner_type": row["partner_type"] or "customer",
                    "memo": row["memo"],
                    "payment_reference": row["payment_reference"],
                    "state": "draft",
                    "is_sent": bool(row["is_sent"]),
                    "invoice_ids": [Command.set(invoice_ids)],
                    "usl_historical_no_ledger_effect": True,
                    "usl_source_is_reconciled": bool(row["is_reconciled"]),
                    "usl_source_is_matched": bool(row["is_matched"]),
                    "usl_source_is_sent": bool(row["is_sent"]),
                    "usl_source_is_reconciled_raw": raw_bool(
                        row["is_reconciled"],
                    ),
                    "usl_source_is_matched_raw": raw_bool(row["is_matched"]),
                    "usl_source_is_sent_raw": raw_bool(row["is_sent"]),
                    "usl_source_outstanding_account_id": row[
                        "outstanding_account_id"
                    ],
                    "usl_source_destination_account_id": row[
                        "destination_account_id"
                    ],
                    "usl_source_amount_company_currency_signed": self._amount(
                        row["amount_company_currency_signed"],
                    ),
                    "rebuild_import_note": (
                        "Native historical payment imported without a journal "
                        "entry because the source payment also had no move_id. "
                        "The source state and invoice links are preserved "
                        "without duplicating any ledger effect."
                    ),
                    **self._trace_values("account.payment", row["id"], options),
                }
                payment = Payment.search([
                    ("rebuild_source_model", "=", "account.payment"),
                    ("rebuild_source_id", "=", row["id"]),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ], limit=1)
                historical_payment = Payment.with_context(
                    usl_historical_payment_maintenance=True,
                )
                if payment:
                    payment.with_context(
                        usl_historical_payment_maintenance=True,
                    ).write(vals)
                else:
                    payment = historical_payment.create(vals)
                target_state = self._target_payment_state(source_state)
                payment.flush_recordset([
                    "name",
                    "state",
                    "outstanding_account_id",
                    "is_reconciled",
                    "is_matched",
                    "is_sent",
                ])
                self.env.cr.execute(
                    """
                    UPDATE account_payment
                       SET name = %s,
                           state = %s,
                           outstanding_account_id = NULL,
                           is_reconciled = %s,
                           is_matched = %s,
                           is_sent = %s
                     WHERE id = %s
                    """,
                    (
                        row["name"] or row["memo"] or f"Source payment {row['id']}",
                        target_state,
                        bool(row["is_reconciled"]),
                        bool(row["is_matched"]),
                        bool(row["is_sent"]),
                        payment.id,
                    ),
                )
                payment.invalidate_recordset([
                    "name",
                    "state",
                    "outstanding_account_id",
                    "is_reconciled",
                    "is_matched",
                    "is_sent",
                ], flush=False)
                imported_payments |= payment
                imported_no_entry_payments |= payment
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
            if payment and payment.expense_ids:
                protected_fields = {
                    "date",
                    "amount",
                    "payment_type",
                    "partner_type",
                    "payment_reference",
                    "currency_id",
                    "partner_id",
                    "destination_account_id",
                    "journal_id",
                    "memo",
                    "payment_method_line_id",
                }
                protected_mismatches = mismatched_values(
                    payment,
                    vals,
                    protected_fields,
                )
                if protected_mismatches:
                    raise ValueError(
                        "Source payment %s is already linked to expense(s) "
                        "%s and differs from the immutable target payment: %s"
                        % (
                            row["id"],
                            payment.expense_ids.ids,
                            json.dumps(
                                protected_mismatches,
                                sort_keys=True,
                                default=str,
                            ),
                        ),
                    )
                payment.write({
                    field_name: value
                    for field_name, value in vals.items()
                    if field_name not in protected_fields
                })
            elif payment:
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
            "native_no_entry_payment_count": len(imported_no_entry_payments),
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


    def _import_reconciliations(self, conn, options, companies):
        partial_rows = self._partial_reconcile_rows(conn, options)
        full_rows = self._full_reconcile_rows(conn, options)
        scope_summary = self._reconciliation_scope_summary(conn, options)
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

        line_map = self._source_trace_record_map(
            "account.move.line",
            source_line_ids,
            options,
        )
        move_map = self._source_trace_record_map(
            "account.move",
            source_exchange_move_ids,
            options,
        )

        partial_map = self._source_trace_record_map(
            "account.partial.reconcile",
            [row["id"] for row in partial_rows],
            options,
        )
        pending_partials = []
        for row in partial_rows:
            if row["id"] in partial_map:
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
            pending_partials.append((row["id"], vals))
        for batch in self._batched(
            pending_partials,
            self._RELATION_BATCH_SIZE,
        ):
            created = Partial.create([vals for _source_id, vals in batch])
            for (source_id, _vals), partial in zip(batch, created, strict=True):
                partial_map[source_id] = partial

        full_map = self._source_trace_record_map(
            "account.full.reconcile",
            [row["id"] for row in full_rows],
            options,
        )
        pending_fulls = []
        for row in full_rows:
            if row["id"] in full_map:
                continue
            target_line_ids = [line_map[source_line_id].id for source_line_id in row["line_ids"] or []]
            target_partial_ids = [partial_map[source_partial_id].id for source_partial_id in row["partial_ids"] or []]
            pending_fulls.append((row["id"], {
                "reconciled_line_ids": [Command.set(target_line_ids)],
                "partial_reconcile_ids": [Command.set(target_partial_ids)],
                **self._trace_values("account.full.reconcile", row["id"], options),
            }))
        for batch in self._batched(pending_fulls, self._RELATION_BATCH_SIZE):
            created = Full.create([vals for _source_id, vals in batch])
            for (source_id, _vals), full in zip(batch, created, strict=True):
                full_map[source_id] = full

        return {
            "source_partial_reconcile_count": len(partial_rows),
            "source_full_reconcile_count": len(full_rows),
            "imported_partial_reconcile_count": len(partial_map),
            "imported_full_reconcile_count": len(full_map),
            "scope_summary": {
                key: int(value or 0)
                for key, value in dict(scope_summary).items()
            },
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
            target_move_map = self._source_trace_record_map(
                "account.move",
                source_move_ids,
                options,
            )
            target_moves = Move.browse(
                [move.id for move in target_move_map.values()],
            )
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
        source_move_ids = {
            row["original_move_id"]
            for row in rows
        } | {
            row["deferred_move_id"]
            for row in rows
        }
        move_map = self._source_trace_record_map(
            "account.move",
            source_move_ids,
            options,
        )
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
            if row["deferred_state"] != "posted":
                representation_status = "source_draft_forecast"
            elif deferred_move:
                representation_status = "imported_posted_entry"
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

    def _native_replay_document_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT move.id, move.name, move.ref, move.move_type,
                   move.journal_id, move.company_id, move.partner_id,
                   move.currency_id, move.date, move.invoice_date,
                   move.invoice_date_due, move.payment_reference,
                   move.fiscal_position_id, move.invoice_payment_term_id,
                   move.invoice_currency_rate,
                   move.amount_untaxed, move.amount_tax, move.amount_total,
                   move.amount_untaxed_signed, move.amount_tax_signed,
                   move.amount_total_signed, move.payment_state
            FROM account_move move
            WHERE move.company_id = ANY(%(source_company_ids)s)
              AND move.state = 'posted'
              AND move.move_type IN (
                  'out_invoice', 'out_refund', 'in_invoice',
                  'in_refund', 'out_receipt', 'in_receipt'
              )
              AND move.date BETWEEN %(date_from)s AND %(date_to)s
            ORDER BY move.date, move.id
            """,
            options,
        )

    def _native_replay_line_rows_by_move(self, conn, options):
        rows = self._fetchall(
            conn,
            """
            SELECT line.id, line.move_id, line.sequence, line.account_id,
                   line.name, line.display_type, line.quantity,
                   line.price_unit, line.discount, line.price_subtotal,
                   line.price_total, line.balance, line.amount_currency,
                   line.analytic_distribution,
                   COALESCE((
                       SELECT array_agg(relation.account_tax_id ORDER BY relation.account_tax_id)
                       FROM account_move_line_account_tax_rel relation
                       WHERE relation.account_move_line_id = line.id
                   ), ARRAY[]::integer[]) AS tax_ids
            FROM account_move_line line
            JOIN account_move move ON move.id = line.move_id
            WHERE move.company_id = ANY(%(source_company_ids)s)
              AND move.state = 'posted'
              AND move.move_type IN (
                  'out_invoice', 'out_refund', 'in_invoice',
                  'in_refund', 'out_receipt', 'in_receipt'
              )
              AND move.date BETWEEN %(date_from)s AND %(date_to)s
              AND line.display_type IN ('product', 'line_section', 'line_note')
            ORDER BY line.move_id, line.sequence, line.id
            """,
            options,
        )
        rows_by_move = defaultdict(list)
        for row in rows:
            rows_by_move[row["move_id"]].append(row)
        return rows_by_move

    def _native_replay_source_account_totals(self, conn, options):
        rows = self._fetchall(
            conn,
            """
            SELECT line.move_id, line.account_id,
                   round(sum(line.debit)::numeric, 2) AS debit,
                   round(sum(line.credit)::numeric, 2) AS credit,
                   round(sum(line.balance)::numeric, 2) AS balance,
                   round(sum(line.amount_currency)::numeric, 2) AS amount_currency
            FROM account_move_line line
            JOIN account_move move ON move.id = line.move_id
            WHERE move.company_id = ANY(%(source_company_ids)s)
              AND move.state = 'posted'
              AND move.move_type IN (
                  'out_invoice', 'out_refund', 'in_invoice',
                  'in_refund', 'out_receipt', 'in_receipt'
              )
              AND move.date BETWEEN %(date_from)s AND %(date_to)s
              AND line.account_id IS NOT NULL
            GROUP BY line.move_id, line.account_id
            ORDER BY line.move_id, line.account_id
            """,
            options,
        )
        totals_by_move = defaultdict(dict)
        for row in rows:
            totals_by_move[row["move_id"]][str(row["account_id"])] = {
                "debit": round(self._amount(row["debit"]), 2),
                "credit": round(self._amount(row["credit"]), 2),
                "balance": round(self._amount(row["balance"]), 2),
                "amount_currency": round(self._amount(row["amount_currency"]), 2),
            }
        return totals_by_move

    def _native_replay_source_tax_totals(self, conn, options):
        rows = self._fetchall(
            conn,
            """
            SELECT line.move_id, line.tax_line_id AS tax_id,
                   round(sum(line.balance)::numeric, 2) AS balance,
                   round(sum(line.amount_currency)::numeric, 2) AS amount_currency,
                   round(max(abs(line.tax_base_amount))::numeric, 2) AS tax_base_amount
            FROM account_move_line line
            JOIN account_move move ON move.id = line.move_id
            WHERE move.company_id = ANY(%(source_company_ids)s)
              AND move.state = 'posted'
              AND move.move_type IN (
                  'out_invoice', 'out_refund', 'in_invoice',
                  'in_refund', 'out_receipt', 'in_receipt'
              )
              AND move.date BETWEEN %(date_from)s AND %(date_to)s
              AND line.display_type = 'tax'
              AND line.tax_line_id IS NOT NULL
            GROUP BY line.move_id, line.tax_line_id
            ORDER BY line.move_id, line.tax_line_id
            """,
            options,
        )
        totals_by_move = defaultdict(dict)
        for row in rows:
            totals_by_move[row["move_id"]][row["tax_id"]] = {
                "balance": round(self._amount(row["balance"]), 2),
                "amount_currency": round(self._amount(row["amount_currency"]), 2),
                "tax_base_amount": round(self._amount(row["tax_base_amount"]), 2),
            }
        return totals_by_move

    def _native_replay_analytic_distribution(self, distribution, analytic_accounts):
        if not distribution:
            return False
        translated = {}
        for source_key, percentage in distribution.items():
            target_ids = []
            for source_id_text in str(source_key).split(","):
                try:
                    source_id = int(source_id_text)
                except (TypeError, ValueError):
                    continue
                target = analytic_accounts.get(source_id)
                if target:
                    target_ids.append(str(target.id))
            if target_ids:
                translated[",".join(target_ids)] = percentage
        return translated or False

    def _native_replay_target_account_totals(self, move):
        totals = defaultdict(lambda: {
            "debit": 0.0,
            "credit": 0.0,
            "balance": 0.0,
            "amount_currency": 0.0,
        })
        for line in move.line_ids:
            if not line.account_id:
                continue
            source_account_id = line.account_id.rebuild_source_id
            key = str(source_account_id or 0)
            totals[key]["debit"] += line.debit
            totals[key]["credit"] += line.credit
            totals[key]["balance"] += line.balance
            totals[key]["amount_currency"] += line.amount_currency
        return {
            key: {field: round(value, 2) for field, value in amounts.items()}
            for key, amounts in sorted(totals.items())
        }

    def _native_replay_apply_manual_tax_override(
        self,
        move,
        source_lines,
        source_tax_totals,
        taxes,
    ):
        """Replay an unambiguous source tax edit through native tax metadata.

        The source predates ``extra_tax_data`` persistence, but a few documents
        contain user-edited base or tax totals that cannot be derived from their
        price, quantity, discount and tax definition.  A single taxable product
        line gives an unambiguous allocation, so reconstruct Odoo's supported
        business-level override and still let ``action_post`` generate the entry.
        Multi-line cases deliberately remain mismatches instead of being guessed.
        """
        product_lines = [
            line for line in source_lines if line["display_type"] == "product"
        ]
        if len(product_lines) != 1:
            return False
        source_line = product_lines[0]
        source_tax_ids = list(source_line["tax_ids"])
        if not source_tax_ids or set(source_tax_ids) != set(source_tax_totals):
            return False

        target_line = move.invoice_line_ids.filtered(
            lambda line: (
                line.rebuild_source_model == "account.move.line.native_engine_input"
                and line.rebuild_source_id == source_line["id"]
            ),
        )
        if len(target_line) != 1:
            return False

        source_subtotal = abs(self._amount(source_line["price_subtotal"]))
        source_total = abs(self._amount(source_line["price_total"]))
        source_tax_total = round(sum(
            abs(values["amount_currency"])
            for values in source_tax_totals.values()
        ), 2)
        computed_matches = (
            move.currency_id.compare_amounts(target_line.price_subtotal, source_subtotal) == 0
            and move.currency_id.compare_amounts(target_line.price_total, source_total) == 0
            and move.currency_id.compare_amounts(move.amount_tax, source_tax_total) == 0
        )
        if computed_matches:
            return False

        manual_tax_amounts = {}
        for source_tax_id in source_tax_ids:
            target_tax = taxes.get(source_tax_id)
            source_tax = source_tax_totals[source_tax_id]
            if not target_tax:
                return False
            manual_tax_amounts[str(target_tax.id)] = {
                "tax_amount_currency": abs(source_tax["amount_currency"]),
                "tax_amount": abs(source_tax["balance"]),
                "base_amount_currency": source_subtotal,
                "base_amount": abs(source_tax["tax_base_amount"]),
            }

        extra_tax_data = {
            "currency_id": move.currency_id.id,
            "price_unit": self._amount(source_line["price_unit"]),
            "discount": self._amount(source_line["discount"]),
            "quantity": self._amount(source_line["quantity"]),
            "rate": move.invoice_currency_rate,
            "manual_total_excluded_currency": source_subtotal,
            "manual_total_excluded": abs(self._amount(source_line["balance"])),
            "manual_tax_amounts": manual_tax_amounts,
        }
        target_tax_ids = target_line.tax_ids.ids
        target_line.write({
            "extra_tax_data": extra_tax_data,
            "tax_ids": [Command.clear()],
        })
        target_line.write({"tax_ids": [Command.set(target_tax_ids)]})
        return {
            "source_move_id": source_line["move_id"],
            "source_line_id": source_line["id"],
            "classification": "supported_native_manual_tax_override",
            "source_subtotal": source_subtotal,
            "source_total": source_total,
            "source_tax_total": source_tax_total,
            "source_tax_ids": source_tax_ids,
        }

    def _native_expense_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT expense.id, expense.name, expense.state, expense.approval_state,
                   expense.payment_mode, expense.date, expense.employee_id,
                   expense.department_id, expense.manager_id,
                   expense.company_id, expense.product_id, expense.product_uom_id,
                   expense.currency_id, expense.payment_method_line_id,
                   expense.account_move_id, expense.vendor_id, expense.account_id,
                   expense.split_expense_origin_id, expense.former_sheet_id,
                   expense.quantity, expense.tax_amount_currency, expense.tax_amount,
                   expense.total_amount_currency, expense.total_amount,
                   expense.untaxed_amount_currency, expense.untaxed_amount,
                   expense.price_unit, expense.analytic_distribution,
                   expense.description, expense.approval_date,
                   expense.last_notification_date,
                   COALESCE((
                       SELECT array_agg(relation.tax_id ORDER BY relation.tax_id)
                       FROM expense_tax relation
                       WHERE relation.expense_id = expense.id
                   ), ARRAY[]::integer[]) AS tax_ids
            FROM hr_expense expense
            WHERE expense.company_id = ANY(%(source_company_ids)s)
              AND expense.date BETWEEN %(date_from)s AND %(date_to)s
            ORDER BY expense.date, expense.id
            """,
            options,
        )

    def _native_expense_move_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT move.id, move.name, move.ref, move.state, move.move_type,
                   move.journal_id, move.company_id, move.partner_id,
                   move.currency_id, move.date, move.invoice_date,
                   move.invoice_date_due,
                   move.amount_untaxed, move.amount_tax, move.amount_total,
                   payment.id AS payment_id, payment.date AS payment_date,
                   payment.journal_id AS payment_journal_id,
                   payment.company_id AS payment_company_id,
                   payment.currency_id AS payment_currency_id,
                   payment.partner_id AS payment_partner_id,
                   payment.payment_method_line_id,
                   payment.outstanding_account_id,
                   payment.destination_account_id,
                   payment.amount AS payment_amount,
                   payment.payment_type, payment.partner_type,
                   payment.memo, payment.payment_reference,
                   payment.state AS payment_state
            FROM account_move move
            LEFT JOIN LATERAL (
                SELECT source_payment.*
                FROM account_payment source_payment
                WHERE source_payment.move_id = move.id
                ORDER BY source_payment.id
                LIMIT 1
            ) payment ON TRUE
            WHERE move.id IN (
                SELECT DISTINCT expense.account_move_id
                FROM hr_expense expense
                WHERE expense.company_id = ANY(%(source_company_ids)s)
                  AND expense.date BETWEEN %(date_from)s AND %(date_to)s
                  AND expense.account_move_id IS NOT NULL
            )
            ORDER BY move.date, move.id
            """,
            options,
        )

    def _native_expense_expected_state(self, source_expense, expense):
        """Translate the Enterprise accountant payment label to Community.

        ``accountant`` overrides ``account.move._get_invoice_in_payment_state``
        to return ``in_payment``.  Community intentionally returns ``paid``
        for the same partially reconciled move.  Preserve the native target
        lifecycle and classify the display-state translation explicitly.
        """
        expected = source_expense["state"]
        if (
            expected == "in_payment"
            and expense.account_move_id.payment_state == "partial"
        ):
            target_state = self.env[
                "account.move"
            ]._get_invoice_in_payment_state()
            if target_state != expected:
                return target_state, {
                    "classification": (
                        "enterprise_accountant_payment_state_translation"
                    ),
                    "source_expense_id": source_expense["id"],
                    "source_state": expected,
                    "target_state": target_state,
                }
        return expected, None

    def _native_expense_expected_untaxed(self, source_expense, currency=False):
        suffix = "_currency" if currency else ""
        untaxed_key = f"untaxed_amount{suffix}"
        total_key = f"total_amount{suffix}"
        tax_key = f"tax_amount{suffix}"
        stored = self._amount(source_expense[untaxed_key])
        arithmetic = self._amount(
            source_expense[total_key],
        ) - self._amount(source_expense[tax_key])
        # Preserve the source's stored rounding.  The 19.3 source contains
        # legitimate one-cent differences between the stored subtotal and
        # ``total - tax``.  Only translate an actually stale zero subtotal
        # when the stored total proves that the expense has a material base.
        if round(stored, 2) == 0 and round(arithmetic, 2) != 0:
            return arithmetic, {
                "classification": "stale_source_expense_untaxed_compute",
                "source_expense_id": source_expense["id"],
                "field": untaxed_key,
                "source_stored_value": stored,
                "source_arithmetic_value": arithmetic,
            }
        return stored, None

    def _native_expense_source_account_totals(self, conn, options):
        rows = self._fetchall(
            conn,
            """
            SELECT line.move_id, line.account_id,
                   round(sum(line.debit)::numeric, 2) AS debit,
                   round(sum(line.credit)::numeric, 2) AS credit,
                   round(sum(line.balance)::numeric, 2) AS balance,
                   round(sum(line.amount_currency)::numeric, 2) AS amount_currency
            FROM account_move_line line
            WHERE line.move_id IN (
                SELECT DISTINCT expense.account_move_id
                FROM hr_expense expense
                WHERE expense.company_id = ANY(%(source_company_ids)s)
                  AND expense.date BETWEEN %(date_from)s AND %(date_to)s
                  AND expense.account_move_id IS NOT NULL
            )
              AND line.account_id IS NOT NULL
            GROUP BY line.move_id, line.account_id
            ORDER BY line.move_id, line.account_id
            """,
            options,
        )
        totals_by_move = defaultdict(dict)
        for row in rows:
            totals_by_move[row["move_id"]][str(row["account_id"])] = {
                "debit": round(self._amount(row["debit"]), 2),
                "credit": round(self._amount(row["credit"]), 2),
                "balance": round(self._amount(row["balance"]), 2),
                "amount_currency": round(self._amount(row["amount_currency"]), 2),
            }
        return totals_by_move

    def _native_expense_extend_account_map(
        self,
        conn,
        options,
        companies,
        currencies,
        accounts,
    ):
        """Add accounts needed only by unposted source expenses.

        The shared ledger mapper intentionally starts from journal effects.
        Approved expenses without an entry can reference valid expense accounts
        that do not occur in the selected posted period, so Track B must extend
        the configuration map before those draft documents can be recreated.
        """
        rows = self._fetchall(
            conn,
            """
            SELECT account.id, account.name, account.code_store,
                   account.account_type, account.active, account.reconcile,
                   account.non_trade, account.currency_id,
                   array_remove(array_agg(relation.res_company_id
                                          ORDER BY relation.res_company_id), NULL) AS company_ids
            FROM account_account account
            LEFT JOIN account_account_res_company_rel relation
                   ON relation.account_account_id = account.id
            WHERE account.id IN (
                SELECT DISTINCT expense.account_id
                FROM hr_expense expense
                WHERE expense.company_id = ANY(%(source_company_ids)s)
                  AND expense.date BETWEEN %(date_from)s AND %(date_to)s
                  AND expense.account_id IS NOT NULL
            )
            GROUP BY account.id
            ORDER BY account.id
            """,
            options,
        )
        missing_rows = [row for row in rows if row["id"] not in accounts]
        self._quarantine_bootstrap_account_code_collisions(missing_rows, options, companies)
        Account = self.env["account.account"].with_context(
            active_test=False,
            import_file=True,
        )
        imported_count = 0
        archive_after_post = []
        for row in missing_rows:
            source_company_ids = [
                source_company_id
                for source_company_id in row["company_ids"] or options["source_company_ids"]
                if source_company_id in companies
            ]
            if not source_company_ids:
                continue
            source_company_id = 1 if 1 in source_company_ids else source_company_ids[0]
            company = companies[source_company_id]
            code = self._source_account_code(row["code_store"], source_company_id)
            account = Account.search([
                ("rebuild_source_model", "=", "account.account"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if not account and code:
                account = Account.with_company(company).search([
                    ("code", "=", code),
                    ("company_ids", "in", company.id),
                ], limit=1)
            vals = {
                "name": self._source_text(row["name"]) or code or f"Source account {row['id']}",
                "code": code,
                "account_type": row["account_type"] or "expense",
                "reconcile": bool(row["reconcile"]),
                "non_trade": bool(row["non_trade"]),
                "currency_id": (
                    currencies[row["currency_id"]].id
                    if row["currency_id"] in currencies
                    else False
                ),
                "active": True,
                "company_ids": [Command.set([
                    companies[company_id].id for company_id in source_company_ids
                ])],
                **self._trace_values("account.account", row["id"], options),
            }
            if not row["active"]:
                archive_after_post.append(row["id"])
            if account:
                account.with_company(company).write(vals)
            else:
                account = Account.with_company(company).create(vals)
            accounts[row["id"]] = account
            imported_count += 1
        return {
            "source_expense_account_count": len(rows),
            "additional_expense_account_count": imported_count,
            "archive_after_post_source_ids": archive_after_post,
        }

    def _native_expense_department_map(self, conn, options, companies):
        rows = self._fetchall(
            conn,
            """
            SELECT department.id, department.name, department.company_id
            FROM hr_department department
            WHERE department.id IN (
                SELECT DISTINCT expense.department_id
                FROM hr_expense expense
                WHERE expense.company_id = ANY(%(source_company_ids)s)
                  AND expense.date BETWEEN %(date_from)s AND %(date_to)s
                  AND expense.department_id IS NOT NULL
            )
            ORDER BY department.id
            """,
            options,
        )
        departments = {}
        Department = self.env["hr.department"].sudo().with_context(
            active_test=False,
            tracking_disable=True,
            mail_create_nolog=True,
        )
        for row in rows:
            company = companies.get(row["company_id"])
            name = self._source_text(row["name"])
            if not company or not name:
                continue
            department = Department.search([
                ("company_id", "=", company.id),
                ("name", "=", name),
            ], limit=1)
            if not department:
                department = Department.create({
                    "name": name,
                    "company_id": company.id,
                })
            departments[row["id"]] = department
        return departments

    def _native_expense_user_map(
        self,
        conn,
        options,
        companies,
        partners,
    ):
        rows = self._fetchall(
            conn,
            """
            SELECT source_user.id, source_user.login, source_user.partner_id,
                   source_user.company_id, source_user.active,
                   COALESCE((
                       SELECT array_agg(relation.cid ORDER BY relation.cid)
                       FROM res_company_users_rel relation
                       WHERE relation.user_id = source_user.id
                   ), ARRAY[]::integer[]) AS company_ids
            FROM res_users source_user
            WHERE source_user.id IN (
                SELECT expense.manager_id
                FROM hr_expense expense
                WHERE expense.company_id = ANY(%(source_company_ids)s)
                  AND expense.date BETWEEN %(date_from)s AND %(date_to)s
                  AND expense.manager_id IS NOT NULL
                UNION
                SELECT employee.user_id
                FROM hr_employee employee
                WHERE employee.id IN (
                    SELECT DISTINCT expense.employee_id
                    FROM hr_expense expense
                    WHERE expense.company_id = ANY(%(source_company_ids)s)
                      AND expense.date BETWEEN %(date_from)s AND %(date_to)s
                )
                  AND employee.user_id IS NOT NULL
                UNION
                SELECT employee.expense_manager_id
                FROM hr_employee employee
                WHERE employee.id IN (
                    SELECT DISTINCT expense.employee_id
                    FROM hr_expense expense
                    WHERE expense.company_id = ANY(%(source_company_ids)s)
                      AND expense.date BETWEEN %(date_from)s AND %(date_to)s
                )
                  AND employee.expense_manager_id IS NOT NULL
            )
            ORDER BY source_user.id
            """,
            options,
        )
        users = {}
        Users = self.env["res.users"].sudo().with_context(
            no_reset_password=True,
            mail_create_nosubscribe=True,
            tracking_disable=True,
        )
        base_group = self.env.ref("base.group_user")
        expense_manager_group = self.env.ref(
            "hr_expense.group_hr_expense_manager",
        )
        for row in rows:
            partner = partners.get(row["partner_id"])
            company = companies.get(row["company_id"])
            if not partner or not company:
                continue
            allowed_companies = self.env["res.company"].browse()
            for source_company_id in row["company_ids"]:
                if source_company_id in companies:
                    allowed_companies |= companies[source_company_id]
            if not allowed_companies:
                allowed_companies = company
            user = Users.search([
                ("partner_id", "=", partner.id),
            ], limit=1)
            if not user:
                user = Users.search([
                    ("login", "=", row["login"]),
                ], limit=1)
            values = {
                "partner_id": partner.id,
                "active": bool(row["active"]),
                "share": False,
                "company_id": company.id,
                "company_ids": [Command.set(allowed_companies.ids)],
            }
            if user:
                # Identity restoration owns the canonical target login.  Do
                # not rewrite it while merely resolving an expense user: even
                # an unchanged ``login`` in ``vals`` triggers Odoo's security
                # notification email and makes a migration replay observable.
                user.write(values)
            else:
                values["login"] = row["login"]
                values["group_ids"] = [
                    Command.set((
                        base_group | expense_manager_group
                    ).ids),
                ]
                user = Users.create(values)
            users[row["id"]] = user
        return users

    def _native_expense_employee_map(
        self,
        conn,
        options,
        companies,
        partners,
        departments,
        users,
    ):
        rows = self._fetchall(
            conn,
            """
            SELECT employee.id, employee.name, employee.company_id,
                   employee.work_contact_id, employee.work_email,
                   employee.user_id, employee.expense_manager_id,
                   version.department_id, employee.active
            FROM hr_employee employee
            LEFT JOIN hr_version version
              ON version.id = employee.current_version_id
            WHERE employee.id IN (
                SELECT DISTINCT expense.employee_id
                FROM hr_expense expense
                WHERE expense.company_id = ANY(%(source_company_ids)s)
                  AND expense.date BETWEEN %(date_from)s AND %(date_to)s
            )
            ORDER BY employee.id
            """,
            options,
        )
        employees = {}
        Employee = self.env["hr.employee"].sudo().with_context(active_test=False)
        for row in rows:
            company = companies.get(row["company_id"])
            work_contact = partners.get(row["work_contact_id"])
            department = departments.get(row["department_id"])
            user = users.get(row["user_id"])
            expense_manager = users.get(row["expense_manager_id"])
            if not company or not work_contact:
                continue
            employee = Employee.search([
                ("rebuild_source_model", "=", "hr.employee"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if not employee:
                employee = Employee.search([
                    ("company_id", "=", company.id),
                    ("work_contact_id", "=", work_contact.id),
                ], limit=1)
            vals = {
                "name": row["name"] or f"Source employee {row['id']}",
                "company_id": company.id,
                "work_contact_id": work_contact.id,
                "work_email": row["work_email"],
                "department_id": department.id if department else False,
                "user_id": user.id if user else False,
                "expense_manager_id": (
                    expense_manager.id if expense_manager else False
                ),
                "active": bool(row["active"]),
                **self._trace_values("hr.employee", row["id"], options),
            }
            if employee:
                employee.write(vals)
            else:
                employee = Employee.create(vals)
            employees[row["id"]] = employee
        return employees

    @staticmethod
    def _native_expense_company_value(value, source_company_id):
        if not isinstance(value, dict):
            return value
        return value.get(str(source_company_id), value.get(source_company_id))

    def _native_expense_product_map(self, conn, options, companies, accounts):
        rows = self._fetchall(
            conn,
            """
            SELECT product.id, product.default_code, product.standard_price,
                   product.active, template.name, template.company_id,
                   template.uom_id, template.type, template.service_type,
                   template.can_be_expensed, template.purchase_ok,
                   template.sale_ok, template.property_account_expense_id,
                   uom_xmlid.module AS uom_module,
                   uom_xmlid.name AS uom_xml_name
            FROM product_product product
            JOIN product_template template ON template.id = product.product_tmpl_id
            LEFT JOIN LATERAL (
                SELECT data.module, data.name
                FROM ir_model_data data
                WHERE data.model = 'uom.uom'
                  AND data.res_id = template.uom_id
                ORDER BY data.id
                LIMIT 1
            ) uom_xmlid ON TRUE
            WHERE product.id IN (
                    SELECT DISTINCT expense.product_id
                    FROM hr_expense expense
                    WHERE expense.company_id = ANY(%(source_company_ids)s)
                      AND expense.date BETWEEN %(date_from)s AND %(date_to)s
                      AND expense.product_id IS NOT NULL
                  )
               OR product.id IN (
                    SELECT DISTINCT analytic.product_id
                      FROM account_analytic_line analytic
                     WHERE analytic.company_id = ANY(%(source_company_ids)s)
                       AND analytic.date BETWEEN %(date_from)s AND %(date_to)s
                       AND analytic.product_id IS NOT NULL
                  )
               OR (
                    product.active
                    AND template.active
                    AND template.can_be_expensed
                    AND product.default_code IN ('TRANS', 'FOOD', 'GIFT_NOVAT')
                  )
            ORDER BY product.id
            """,
            options,
        )
        products = {}
        current_standard_prices = {}
        Product = self.env["product.product"].sudo().with_context(active_test=False)
        default_uom = self.env.ref("uom.product_uom_unit")
        for row in rows:
            product = Product.search([
                ("rebuild_source_model", "=", "product.product"),
                ("rebuild_source_id", "=", row["id"]),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ], limit=1)
            if not product and row["default_code"]:
                product = Product.search([
                    ("default_code", "=", row["default_code"]),
                ], limit=1)
            uom = default_uom
            if row["uom_module"] and row["uom_xml_name"]:
                uom = self.env.ref(
                    f"{row['uom_module']}.{row['uom_xml_name']}",
                    raise_if_not_found=False,
                ) or default_uom
            vals = {
                "name": self._source_text(row["name"]) or f"Source expense product {row['id']}",
                "default_code": row["default_code"],
                "type": row["type"] or "service",
                "uom_id": uom.id,
                "can_be_expensed": bool(row["can_be_expensed"]),
                "purchase_ok": bool(row["purchase_ok"]),
                "sale_ok": bool(row["sale_ok"]),
                "active": bool(row["active"]),
                **self._trace_values("product.product", row["id"], options),
            }
            if "service_type" in Product._fields:
                vals["service_type"] = row["service_type"] or "manual"
            if product:
                product.write(vals)
            else:
                product = Product.create(vals)
            products[row["id"]] = product

            source_company_ids = (
                [row["company_id"]]
                if row["company_id"] in companies
                else options["source_company_ids"]
            )
            for source_company_id in source_company_ids:
                company = companies.get(source_company_id)
                if not company:
                    continue
                standard_price = self._amount(self._native_expense_company_value(
                    row["standard_price"],
                    source_company_id,
                ))
                expense_account_id = self._native_expense_company_value(
                    row["property_account_expense_id"],
                    source_company_id,
                )
                product.with_company(company).standard_price = standard_price
                if expense_account_id in accounts:
                    product.product_tmpl_id.with_company(company).property_account_expense_id = (
                        accounts[expense_account_id]
                    )
                current_standard_prices[row["id"], source_company_id] = standard_price
        return products, current_standard_prices

    def _native_expense_configure_companies(
        self,
        conn,
        options,
        companies,
        journals,
        method_lines,
        accounts,
        move_rows,
    ):
        company_rows = self._fetchall(
            conn,
            """
            SELECT company.id, company.expense_journal_id,
                   COALESCE((
                       SELECT array_agg(relation.account_payment_method_line_id
                                        ORDER BY relation.account_payment_method_line_id)
                       FROM account_payment_method_line_res_company_rel relation
                       WHERE relation.res_company_id = company.id
                   ), ARRAY[]::integer[]) AS allowed_payment_method_line_ids
            FROM res_company company
            WHERE company.id = ANY(%(source_company_ids)s)
            ORDER BY company.id
            """,
            options,
        )
        configured_method_line_ids = set()
        operational_default_journals = self.env["account.journal"]
        for source_move in move_rows:
            source_method_line_id = source_move["payment_method_line_id"]
            source_outstanding_account_id = source_move["outstanding_account_id"]
            method_line = method_lines.get(source_method_line_id)
            outstanding_account = accounts.get(source_outstanding_account_id)
            if method_line and outstanding_account:
                method_line.payment_account_id = outstanding_account
                configured_method_line_ids.add(method_line.id)

        for row in company_rows:
            company = companies.get(row["id"])
            if not company:
                continue
            expense_journal = journals.get(row["expense_journal_id"])
            if not expense_journal:
                expense_journal = self.env["account.journal"].sudo().with_company(
                    company,
                ).search([
                    ("company_id", "=", company.id),
                    ("code", "=", "NDF"),
                ], limit=1)
                if not expense_journal:
                    expense_account = self.env[
                        "account.account"
                    ].sudo().with_company(company).search([
                        ("company_ids", "in", company.id),
                        ("active", "=", True),
                        ("account_type", "=", "expense"),
                        ("code", "=", "625100"),
                    ], limit=1)
                    expense_journal = self.env[
                        "account.journal"
                    ].sudo().with_company(company).create({
                        "name": "Notes de frais",
                        "code": "NDF",
                        "type": "purchase",
                        "company_id": company.id,
                        "default_account_id": expense_account.id,
                    })
                operational_default_journals |= expense_journal
            allowed_method_lines = [
                method_lines[source_id].id
                for source_id in row["allowed_payment_method_line_ids"]
                if source_id in method_lines
            ]
            vals = {
                "company_expense_allowed_payment_method_line_ids": [
                    Command.set(allowed_method_lines),
                ],
            }
            if expense_journal:
                vals["expense_journal_id"] = expense_journal.id
            company.write(vals)
            configured_method_line_ids.update(allowed_method_lines)
        operational_default_journals |= self.env["res.company"].browse(
            [company.id for company in companies.values()],
        )._usl_ensure_operational_accounting_journals()
        return {
            "configured_company_count": len(company_rows),
            "configured_payment_method_line_count": len(configured_method_line_ids),
            "operational_default_expense_journal_count": len(
                operational_default_journals,
            ),
            "operational_default_expense_journals": [
                {
                    "company_id": journal.company_id.id,
                    "company_name": journal.company_id.name,
                    "journal_id": journal.id,
                    "journal_code": journal.code,
                }
                for journal in operational_default_journals
            ],
        }

    def _native_expense_restore_context(
        self,
        expense_rows,
        expenses_by_source_id,
        departments,
        users,
        blocked_cases,
    ):
        context_restored_count = 0
        split_link_count = 0
        for source_expense in expense_rows:
            expense = expenses_by_source_id.get(source_expense["id"])
            if not expense:
                continue
            department = departments.get(source_expense["department_id"])
            manager = users.get(source_expense["manager_id"])
            expense.with_context(
                tracking_disable=True,
                mail_create_nolog=True,
            ).write({
                "department_id": department.id if department else False,
                "manager_id": manager.id if manager else False,
                "approval_date": source_expense["approval_date"] or False,
                "former_sheet_id": source_expense["former_sheet_id"],
                "last_notification_date": (
                    source_expense["last_notification_date"]
                ),
            })
            context_restored_count += 1

        for source_expense in expense_rows:
            source_origin_id = source_expense["split_expense_origin_id"]
            if not source_origin_id:
                continue
            expense = expenses_by_source_id.get(source_expense["id"])
            origin = expenses_by_source_id.get(source_origin_id)
            if not expense or not origin:
                blocked_cases.append({
                    "source_expense_id": source_expense["id"],
                    "source_split_expense_origin_id": source_origin_id,
                    "classification": "missing_split_expense_origin",
                })
                continue
            expense.with_context(
                tracking_disable=True,
                mail_create_nolog=True,
            ).write({"split_expense_origin_id": origin.id})
            split_link_count += 1
        return {
            "context_restored_count": context_restored_count,
            "split_link_count": split_link_count,
        }

    def _native_expense_bank_match_cache_rows(self, conn, options):
        """Read every legacy suggestion surface without importing its schema."""
        rows = []
        if self._source_table_exists(conn, "x_sl_expense_bank_candidate"):
            rows.extend(self._fetchall(
                conn,
                """
                SELECT 'candidate' AS cache_kind,
                       candidate.id AS cache_id,
                       candidate.x_expense_id AS source_expense_id,
                       candidate.x_bank_statement_line_id
                           AS source_bank_statement_line_id,
                       candidate.x_sequence AS source_sequence,
                       candidate.x_score AS source_score,
                       candidate.x_is_best AS source_is_best
                  FROM x_sl_expense_bank_candidate candidate
                  JOIN hr_expense expense
                    ON expense.id = candidate.x_expense_id
                 WHERE expense.company_id
                       = ANY(%(source_company_ids)s)
                   AND expense.date
                       BETWEEN %(date_from)s AND %(date_to)s
                 ORDER BY candidate.id
                """,
                options,
            ))
        relation_table = "x_hr_expense_bank_statement_line_rel"
        if self._source_table_exists(conn, relation_table):
            rows.extend(self._fetchall(
                conn,
                """
                SELECT 'many2many' AS cache_kind,
                       row_number() OVER (
                           ORDER BY relation.expense_id,
                                    relation.bank_statement_line_id
                       ) AS cache_id,
                       relation.expense_id AS source_expense_id,
                       relation.bank_statement_line_id
                           AS source_bank_statement_line_id,
                       NULL::integer AS source_sequence,
                       NULL::integer AS source_score,
                       NULL::boolean AS source_is_best
                  FROM x_hr_expense_bank_statement_line_rel relation
                  JOIN hr_expense expense
                    ON expense.id = relation.expense_id
                 WHERE expense.company_id
                       = ANY(%(source_company_ids)s)
                   AND expense.date
                       BETWEEN %(date_from)s AND %(date_to)s
                 ORDER BY relation.expense_id,
                          relation.bank_statement_line_id
                """,
                options,
            ))
        selected_field = "x_selected_bank_statement_line_id"
        if self._source_column_exists(conn, "hr_expense", selected_field):
            rows.extend(self._fetchall(
                conn,
                """
                SELECT 'selected' AS cache_kind,
                       expense.id AS cache_id,
                       expense.id AS source_expense_id,
                       expense.x_selected_bank_statement_line_id
                           AS source_bank_statement_line_id,
                       NULL::integer AS source_sequence,
                       NULL::integer AS source_score,
                       TRUE AS source_is_best
                  FROM hr_expense expense
                 WHERE expense.company_id
                       = ANY(%(source_company_ids)s)
                   AND expense.date
                       BETWEEN %(date_from)s AND %(date_to)s
                   AND expense.x_selected_bank_statement_line_id IS NOT NULL
                 ORDER BY expense.id
                """,
                options,
            ))
        return rows

    def _native_expense_legacy_bank_match_schema(self):
        legacy_model_names = ["x_sl_expense_bank_candidate"]
        legacy_field_names = [
            "x_bank_match_candidate_ids",
            "x_candidate_bank_statement_line_ids",
            "x_selected_bank_statement_line_id",
            "x_selected_bank_statement_line_preview",
        ]
        legacy_action_names = [
            "SL - Dépense - Chercher débits candidats",
            "SL - Dépense - Associer meilleur débit bancaire",
            "SL - Candidat bancaire - Associer à la dépense",
        ]
        legacy_view_names = ["SL - Expense bank debit matching"]
        Model = self.env["ir.model"].sudo()
        Field = self.env["ir.model.fields"].sudo()
        legacy_models = Model.search([
            ("model", "in", legacy_model_names),
        ])
        legacy_fields = Field.search([
            "|",
            ("model", "in", legacy_model_names),
            "&",
            ("model", "=", "hr.expense"),
            ("name", "in", legacy_field_names),
        ])
        legacy_actions = self.env["ir.actions.server"].sudo().search([
            ("name", "in", legacy_action_names),
        ])
        legacy_views = self.env["ir.ui.view"].sudo().search([
            "|",
            ("model", "in", legacy_model_names),
            ("name", "in", legacy_view_names),
        ])
        legacy_access = self.env["ir.model.access"].sudo().search([
            ("model_id", "in", legacy_models.ids or [0]),
        ])
        counts = {
            "model_count": len(legacy_models),
            "field_count": len(legacy_fields),
            "action_count": len(legacy_actions),
            "view_count": len(legacy_views),
            "access_count": len(legacy_access),
        }
        return {
            **counts,
            "absent": not any(counts.values()),
        }

    @staticmethod
    def _native_expense_bank_match_business_counts(env):
        return {
            "expense_count": env["hr.expense"].sudo().search_count([]),
            "move_count": env["account.move"].sudo().search_count([]),
            "move_line_count": env[
                "account.move.line"
            ].sudo().search_count([]),
            "payment_count": env["account.payment"].sudo().search_count([]),
            "partial_reconcile_count": env[
                "account.partial.reconcile"
            ].sudo().search_count([]),
            "full_reconcile_count": env[
                "account.full.reconcile"
            ].sudo().search_count([]),
        }

    def _native_expense_recompute_bank_matches(
        self,
        cache_rows,
        expenses_by_source_id,
        options,
    ):
        """Recompute operational suggestions and classify source cache facts."""
        Candidate = self.env["usl.expense.bank.match.candidate"].sudo()
        Expense = self.env["hr.expense"].sudo()
        expense_ids = [
            expense.id for expense in expenses_by_source_id.values()
        ]
        eligible_expenses = Expense.browse(
            expense_ids,
        ).filtered(
            lambda expense: (
                expense._usl_bank_match_is_eligible()
                and expense.date
                and not expense.currency_id.is_zero(
                    expense._usl_bank_match_amount(),
                )
            ),
        )
        counts_before = self._native_expense_bank_match_business_counts(
            self.env,
        )
        refresh_errors = []
        first_signature = []
        second_signature = []
        if eligible_expenses:
            try:
                with self.env.cr.savepoint():
                    eligible_expenses.with_context(
                        usl_expense_bank_match_migration=True,
                    )._usl_refresh_bank_match_candidates()
                    candidates = Candidate.search([
                        ("expense_id", "in", eligible_expenses.ids),
                    ])
                    first_signature = sorted(
                        (
                            candidate.id,
                            candidate.expense_id.id,
                            candidate.bank_statement_line_id.id,
                            candidate.state,
                            candidate.rank,
                            candidate.fingerprint,
                        )
                        for candidate in candidates
                    )
                    eligible_expenses.with_context(
                        usl_expense_bank_match_migration=True,
                    )._usl_refresh_bank_match_candidates()
                    candidates = Candidate.search([
                        ("expense_id", "in", eligible_expenses.ids),
                    ])
                    second_signature = sorted(
                        (
                            candidate.id,
                            candidate.expense_id.id,
                            candidate.bank_statement_line_id.id,
                            candidate.state,
                            candidate.rank,
                            candidate.fingerprint,
                        )
                        for candidate in candidates
                    )
            except Exception as exc:  # noqa: BLE001
                refresh_errors.append({
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                })

        source_bank_ids = sorted({
            row["source_bank_statement_line_id"]
            for row in cache_rows
            if row["source_bank_statement_line_id"]
        })
        bank_lines_by_source_id = {
            line.rebuild_source_id: line
            for line in self.env[
                "account.bank.statement.line"
            ].sudo().search([
                (
                    "rebuild_source_model",
                    "=",
                    "account.bank.statement.line",
                ),
                (
                    "rebuild_source_snapshot",
                    "=",
                    options["source_snapshot_id"],
                ),
                ("rebuild_source_id", "in", source_bank_ids or [0]),
            ])
        }
        target_pairs = {
            (
                candidate.expense_id.id,
                candidate.bank_statement_line_id.id,
            )
            for candidate in Candidate.search([
                (
                    "expense_id",
                    "in",
                    expense_ids or [0],
                ),
            ])
            if candidate.state == "available"
        }
        source_pair_counts = Counter(
            (
                row["source_expense_id"],
                row["source_bank_statement_line_id"],
            )
            for row in cache_rows
        )
        expenses_by_source_bank = defaultdict(set)
        for row in cache_rows:
            expenses_by_source_bank[
                row["source_bank_statement_line_id"]
            ].add(row["source_expense_id"])

        classifications = []
        classification_counts = Counter()
        flag_counts = Counter()
        for row in cache_rows:
            expense = expenses_by_source_id.get(row["source_expense_id"])
            bank_line = bank_lines_by_source_id.get(
                row["source_bank_statement_line_id"],
            )
            flags = []
            source_pair = (
                row["source_expense_id"],
                row["source_bank_statement_line_id"],
            )
            if source_pair_counts[source_pair] > 1:
                flags.append("shared_source_cache")
            if len(expenses_by_source_bank[
                row["source_bank_statement_line_id"]
            ]) > 1:
                flags.append("ambiguous_bank_line")
            if row["cache_kind"] == "selected":
                flags.append("selected_in_source")

            if not expense:
                classification = "missing_target_expense"
            elif not bank_line:
                classification = "missing_target_bank_line"
            elif (
                bank_line.is_reconciled
                or expense.state in ("posted", "in_payment", "paid")
            ):
                classification = "already_settled_native_truth"
            elif (expense.id, bank_line.id) in target_pairs:
                classification = "reproducible_current_suggestion"
            else:
                classification = "stale_source_cache"
            classification_counts[classification] += 1
            flag_counts.update(flags)
            classifications.append({
                "cache_kind": row["cache_kind"],
                "cache_id": row["cache_id"],
                "source_expense_id": row["source_expense_id"],
                "source_bank_statement_line_id": row[
                    "source_bank_statement_line_id"
                ],
                "classification": classification,
                "flags": flags,
            })

        counts_after = self._native_expense_bank_match_business_counts(
            self.env,
        )
        legacy_schema = self._native_expense_legacy_bank_match_schema()
        return {
            "source_cache_association_count": len(cache_rows),
            "classified_association_count": len(classifications),
            "classification_counts": dict(sorted(
                classification_counts.items(),
            )),
            "flag_counts": dict(sorted(flag_counts.items())),
            "eligible_expense_count": len(eligible_expenses),
            "current_candidate_count": Candidate.search_count([
                (
                    "expense_id",
                    "in",
                    expense_ids or [0],
                ),
            ]),
            "refresh_error_count": len(refresh_errors),
            "refresh_errors": refresh_errors,
            "refresh_idempotent": first_signature == second_signature,
            "business_counts_before": counts_before,
            "business_counts_after": counts_after,
            "accounting_unchanged": counts_before == counts_after,
            "legacy_target_schema": legacy_schema,
            "classification_examples": classifications[:50],
        }

    def run_source_faithful_expense_materialization_from_source(self, options):
        """Materialize source expenses without duplicating imported accounting.

        The product database already contains the exact posted source journal
        entries and reconciliations. This stage reconstructs normal
        ``hr.expense`` records, restores their workflow state and attachments,
        then links them to those existing entries and journal items. New
        expenses continue to use Odoo's standard approval and posting engine.
        """
        self.ensure_one()
        options = {
            "source_database": "odoo_online_source_saas_19_3",
            "source_snapshot_id": "source-unknown",
            "source_dump_sha256": "",
            "source_version": "Odoo Online Enterprise saas~19.3",
            "target_database": self.env.cr.dbname,
            "date_from": "2024-01-10",
            "date_to": fields.Date.context_today(self),
            "source_company_ids": [1],
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
        try:
            currencies = self._currency_map(conn)
            countries = self._country_map(conn)
            companies, _company_rows = self._company_map(
                conn,
                options,
                countries,
            )
            partners = self._partner_map(conn, options)
            accounts, account_ids_to_archive = self._account_map(
                conn,
                options,
                companies,
                currencies,
            )
            expense_account_stats = self._native_expense_extend_account_map(
                conn,
                options,
                companies,
                currencies,
                accounts,
            )
            account_ids_to_archive.extend(
                expense_account_stats["archive_after_post_source_ids"],
            )
            tax_tags = self._tax_tag_map(conn, options, countries)
            tax_groups = self._tax_group_map(
                conn,
                options,
                companies,
                accounts,
                countries,
            )
            taxes, _tax_repartition_lines, _tax_stats = self._tax_map(
                conn,
                options,
                companies,
                accounts,
                tax_groups,
                tax_tags,
                countries,
            )
            journals, journal_ids_to_archive = self._journal_map(
                conn,
                options,
                companies,
                accounts,
                currencies,
            )
            method_lines = self._payment_method_line_map(
                conn,
                journals,
                accounts,
            )
            analytic_plans = self._analytic_plan_map(conn, options)
            analytic_accounts = self._analytic_account_map(
                conn,
                options,
                companies,
                partners,
                analytic_plans,
            )
            expense_rows = self._native_expense_rows(conn, options)
            departments = self._native_expense_department_map(
                conn,
                options,
                companies,
            )
            users = self._native_expense_user_map(
                conn,
                options,
                companies,
                partners,
            )
            employees = self._native_expense_employee_map(
                conn,
                options,
                companies,
                partners,
                departments,
                users,
            )
            products, current_standard_prices = self._native_expense_product_map(
                conn,
                options,
                companies,
                accounts,
            )
            self._native_expense_configure_companies(
                conn,
                options,
                companies,
                journals,
                method_lines,
                accounts,
                self._native_expense_move_rows(conn, options),
            )

            Expense = self.env["hr.expense"].sudo().with_context(
                tracking_disable=True,
                mail_create_nolog=True,
                rebuild_source_materialization=True,
            )
            created_count = 0
            reused_count = 0
            blocked_cases = []
            expenses_by_source_id = {}
            for source_expense in expense_rows:
                company = companies.get(source_expense["company_id"])
                employee = employees.get(source_expense["employee_id"])
                product = products.get(source_expense["product_id"])
                currency = currencies.get(source_expense["currency_id"])
                account = accounts.get(source_expense["account_id"])
                vendor = partners.get(source_expense["vendor_id"])
                department = departments.get(
                    source_expense["department_id"],
                )
                manager = users.get(source_expense["manager_id"])
                payment_method_line = method_lines.get(
                    source_expense["payment_method_line_id"],
                )
                missing_tax_ids = [
                    source_tax_id
                    for source_tax_id in source_expense["tax_ids"]
                    if source_tax_id not in taxes
                ]
                blockers = [
                    label
                    for label, value in (
                        ("company", company),
                        ("employee", employee),
                        ("currency", currency),
                        ("account", account),
                    )
                    if not value
                ]
                if source_expense["product_id"] and not product:
                    blockers.append("product")
                if source_expense["vendor_id"] and not vendor:
                    blockers.append("vendor")
                if source_expense["department_id"] and not department:
                    blockers.append("department")
                if source_expense["manager_id"] and not manager:
                    blockers.append("manager")
                if missing_tax_ids:
                    blockers.append("taxes")
                if blockers:
                    blocked_cases.append({
                        "source_expense_id": source_expense["id"],
                        "source_name": source_expense["name"],
                        "classification": (
                            "source_faithful_expense_mapping_error"
                        ),
                        "blockers": blockers,
                        "missing_source_tax_ids": missing_tax_ids,
                    })
                    continue

                expense = Expense.search([
                    ("rebuild_source_model", "=", "hr.expense"),
                    ("rebuild_source_id", "=", source_expense["id"]),
                    (
                        "rebuild_source_snapshot",
                        "=",
                        options["source_snapshot_id"],
                    ),
                ], limit=1)
                if expense:
                    reused_count += 1
                    expenses_by_source_id[source_expense["id"]] = expense
                    continue
                try:
                    with self.env.cr.savepoint():
                        current_standard_price = (
                            current_standard_prices.get(
                                (
                                    source_expense["product_id"],
                                    source_expense["company_id"],
                                ),
                                0.0,
                            )
                            if product else 0.0
                        )
                        if current_standard_price:
                            product.with_company(company).standard_price = (
                                self._amount(source_expense["price_unit"])
                            )
                        values = {
                            "name": (
                                source_expense["name"]
                                or f"Source expense {source_expense['id']}"
                            ),
                            "date": source_expense["date"],
                            "employee_id": employee.id,
                            "company_id": company.id,
                            "product_id": product.id if product else False,
                            "product_uom_id": (
                                product.uom_id.id if product else False
                            ),
                            "currency_id": currency.id,
                            "payment_mode": source_expense["payment_mode"],
                            "vendor_id": vendor.id if vendor else False,
                            "account_id": account.id,
                            "tax_ids": [Command.set([
                                taxes[source_tax_id].id
                                for source_tax_id
                                in source_expense["tax_ids"]
                            ])],
                            "quantity": self._amount(
                                source_expense["quantity"],
                            ),
                            "total_amount_currency": self._amount(
                                source_expense["total_amount_currency"],
                            ),
                            "total_amount": self._amount(
                                source_expense["total_amount"],
                            ),
                            "analytic_distribution": (
                                self._native_replay_analytic_distribution(
                                    source_expense[
                                        "analytic_distribution"
                                    ],
                                    analytic_accounts,
                                )
                            ),
                            "description": source_expense["description"],
                            "rebuild_import_note": (
                                "Source-faithful expense restored as a normal "
                                "Odoo expense and linked to the exact imported "
                                "source accounting entry."
                            ),
                            **self._trace_values(
                                "hr.expense",
                                source_expense["id"],
                                options,
                            ),
                        }
                        if payment_method_line:
                            values["payment_method_line_id"] = (
                                payment_method_line.id
                            )
                        expense = Expense.with_company(company).create(values)
                        if source_expense["state"] == "refused":
                            expense._do_refuse(
                                "Restored source refusal",
                            )
                        elif source_expense["state"] != "draft":
                            expense.action_submit()
                            if expense.state == "submitted":
                                expense._do_approve()
                            if source_expense["approval_date"]:
                                expense.approval_date = source_expense[
                                    "approval_date"
                                ]
                        if current_standard_price and product:
                            product.with_company(company).standard_price = (
                                current_standard_price
                            )
                    created_count += 1
                    expenses_by_source_id[source_expense["id"]] = expense
                except Exception as exc:  # noqa: BLE001
                    blocked_cases.append({
                        "source_expense_id": source_expense["id"],
                        "source_name": source_expense["name"],
                        "classification": (
                            "source_faithful_expense_creation_error"
                        ),
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    })

            context_stats = self._native_expense_restore_context(
                expense_rows,
                expenses_by_source_id,
                departments,
                users,
                blocked_cases,
            )

            source_move_ids = sorted({
                row["account_move_id"]
                for row in expense_rows
                if row["account_move_id"]
            })
            target_moves = {
                move.rebuild_source_id: move
                for move in self.env["account.move"].sudo().search([
                    ("rebuild_source_model", "=", "account.move"),
                    (
                        "rebuild_source_snapshot",
                        "=",
                        options["source_snapshot_id"],
                    ),
                    ("rebuild_source_id", "in", source_move_ids or [0]),
                ])
            }
            linked_move_count = 0
            for source_expense in expense_rows:
                if not source_expense["account_move_id"]:
                    continue
                expense = expenses_by_source_id.get(source_expense["id"])
                move = target_moves.get(source_expense["account_move_id"])
                if not expense or not move:
                    blocked_cases.append({
                        "source_expense_id": source_expense["id"],
                        "source_move_id": source_expense["account_move_id"],
                        "classification": (
                            "missing_exact_expense_accounting_entry"
                        ),
                    })
                    continue
                expense.account_move_id = move
                linked_move_count += 1

            source_line_links = self._fetchall(
                conn,
                """
                SELECT line.id AS source_line_id,
                       line.expense_id AS source_expense_id
                  FROM account_move_line line
                 WHERE line.expense_id IS NOT NULL
                   AND line.expense_id = ANY(%(source_expense_ids)s)
                """,
                {
                    "source_expense_ids": (
                        [row["id"] for row in expense_rows] or [0]
                    ),
                },
            )
            target_lines = {
                line.rebuild_source_id: line
                for line in self.env["account.move.line"].sudo().search([
                    ("rebuild_source_model", "=", "account.move.line"),
                    (
                        "rebuild_source_snapshot",
                        "=",
                        options["source_snapshot_id"],
                    ),
                    (
                        "rebuild_source_id",
                        "in",
                        [row["source_line_id"] for row in source_line_links]
                        or [0],
                    ),
                ])
            }
            linked_line_count = 0
            for source_link in source_line_links:
                line = target_lines.get(source_link["source_line_id"])
                expense = expenses_by_source_id.get(
                    source_link["source_expense_id"],
                )
                if line and expense:
                    # The exact source analytic items have already been
                    # imported.  Linking a posted journal item to an expense
                    # normally synchronizes the business model and recreates
                    # analytic items from ``analytic_distribution``.  That is
                    # correct for daily operations, but would duplicate the
                    # imported source truth during reconstruction.
                    line.with_context(
                        skip_analytic_sync=True,
                        tracking_disable=True,
                    ).write({"expense_id": expense.id})
                    linked_line_count += 1

            attachment_stats = self._import_attachments(
                conn,
                {
                    **options,
                    "attachment_target_trace_models": {
                        "hr.expense": ["hr.expense"],
                    },
                },
                companies,
                rows=self._native_replay_expense_attachment_rows(
                    conn,
                    options,
                ),
            )
            bank_match_cache_stats = (
                self._native_expense_recompute_bank_matches(
                    self._native_expense_bank_match_cache_rows(
                        conn,
                        options,
                    ),
                    expenses_by_source_id,
                    options,
                )
            )
            for source_account_id in account_ids_to_archive:
                accounts[source_account_id].active = False
            for source_journal_id in journal_ids_to_archive:
                journals[source_journal_id].active = False

            passed_count = 0
            mismatch_cases = []
            compatibility_transformations = []
            state_counts = defaultdict(int)
            for source_expense in expense_rows:
                expense = expenses_by_source_id.get(source_expense["id"])
                if not expense:
                    continue
                expense.invalidate_recordset([
                    "state",
                    "approval_state",
                    "account_move_id",
                    "department_id",
                    "manager_id",
                    "former_sheet_id",
                    "last_notification_date",
                    "split_expense_origin_id",
                ])
                state_counts[expense.state] += 1
                expected_product = products.get(
                    source_expense["product_id"],
                )
                expected_vendor = partners.get(
                    source_expense["vendor_id"],
                )
                expected_payment_method_line = method_lines.get(
                    source_expense["payment_method_line_id"],
                )
                expected_analytic_distribution = (
                    self._native_replay_analytic_distribution(
                        source_expense["analytic_distribution"],
                        analytic_accounts,
                    )
                )
                expected_split_origin = expenses_by_source_id.get(
                    source_expense["split_expense_origin_id"],
                )
                expected_state, state_transformation = (
                    self._native_expense_expected_state(
                        source_expense,
                        expense,
                    )
                )
                expected_untaxed, untaxed_transformation = (
                    self._native_expense_expected_untaxed(source_expense)
                )
                expected_untaxed_currency, currency_untaxed_transformation = (
                    self._native_expense_expected_untaxed(
                        source_expense,
                        currency=True,
                    )
                )
                compatibility_transformations.extend(filter(None, (
                    state_transformation,
                    untaxed_transformation,
                    currency_untaxed_transformation,
                )))
                checks = {
                    "state": expense.state == expected_state,
                    "approval_state": (
                        (expense.approval_state or False)
                        == (source_expense["approval_state"] or False)
                    ),
                    "name": (
                        expense.name
                        == (
                            source_expense["name"]
                            or f"Source expense {source_expense['id']}"
                        )
                    ),
                    "date": expense.date == source_expense["date"],
                    "employee": (
                        expense.employee_id.id
                        == getattr(
                            employees.get(source_expense["employee_id"]),
                            "id",
                            False,
                        )
                    ),
                    "department": (
                        expense.department_id.id
                        == getattr(
                            departments.get(
                                source_expense["department_id"],
                            ),
                            "id",
                            False,
                        )
                    ),
                    "manager": (
                        expense.manager_id.id
                        == getattr(
                            users.get(source_expense["manager_id"]),
                            "id",
                            False,
                        )
                    ),
                    "company": (
                        expense.company_id.id
                        == getattr(
                            companies.get(source_expense["company_id"]),
                            "id",
                            False,
                        )
                    ),
                    "product": expense.product_id.id
                    == getattr(expected_product, "id", False),
                    "currency": (
                        expense.currency_id.id
                        == getattr(
                            currencies.get(source_expense["currency_id"]),
                            "id",
                            False,
                        )
                    ),
                    "payment_mode": (
                        expense.payment_mode
                        == source_expense["payment_mode"]
                    ),
                    "payment_method_line": (
                        expense.payment_method_line_id.id
                        == getattr(
                            expected_payment_method_line,
                            "id",
                            False,
                        )
                    ),
                    "vendor": expense.vendor_id.id
                    == getattr(expected_vendor, "id", False),
                    "account": (
                        expense.account_id.id
                        == getattr(
                            accounts.get(source_expense["account_id"]),
                            "id",
                            False,
                        )
                    ),
                    "taxes": (
                        set(expense.tax_ids.ids)
                        == {
                            taxes[source_tax_id].id
                            for source_tax_id
                            in source_expense["tax_ids"]
                        }
                    ),
                    "account_move": (
                        (
                            expense.account_move_id.rebuild_source_id
                            or None
                        )
                        == source_expense["account_move_id"]
                    ),
                    "quantity": round(expense.quantity, 6)
                    == round(
                        self._amount(source_expense["quantity"]),
                        6,
                    ),
                    "tax_amount": round(expense.tax_amount, 2)
                    == round(
                        self._amount(source_expense["tax_amount"]),
                        2,
                    ),
                    "tax_currency_amount": round(
                        expense.tax_amount_currency,
                        2,
                    )
                    == round(
                        self._amount(
                            source_expense["tax_amount_currency"],
                        ),
                        2,
                    ),
                    "amount": round(expense.total_amount, 2)
                    == round(
                        self._amount(source_expense["total_amount"]),
                        2,
                    ),
                    "currency_amount": round(
                        expense.total_amount_currency,
                        2,
                    )
                    == round(
                        self._amount(
                            source_expense["total_amount_currency"],
                        ),
                        2,
                    ),
                    "untaxed_amount": round(
                        expense.untaxed_amount,
                        2,
                    )
                    == round(expected_untaxed, 2),
                    "untaxed_currency_amount": round(
                        expense.untaxed_amount_currency,
                        2,
                    )
                    == round(expected_untaxed_currency, 2),
                    "price_unit": round(expense.price_unit, 6)
                    == round(
                        self._amount(source_expense["price_unit"]),
                        6,
                    ),
                    "analytic_distribution": (
                        (expense.analytic_distribution or {})
                        == (expected_analytic_distribution or {})
                    ),
                    "description": (
                        (expense.description or False)
                        == (source_expense["description"] or False)
                    ),
                    "approval_date": (
                        (expense.approval_date or False)
                        == (source_expense["approval_date"] or False)
                    ),
                    "former_sheet": (
                        (expense.former_sheet_id or 0)
                        == (source_expense["former_sheet_id"] or 0)
                    ),
                    "last_notification": (
                        (expense.last_notification_date or False)
                        == (
                            source_expense["last_notification_date"]
                            or False
                        )
                    ),
                    "split_origin": (
                        expense.split_expense_origin_id.id
                        == getattr(expected_split_origin, "id", False)
                    ),
                }
                if all(checks.values()):
                    passed_count += 1
                else:
                    mismatch_cases.append({
                        "source_expense_id": source_expense["id"],
                        "target_expense_id": expense.id,
                        "source_state": source_expense["state"],
                        "expected_target_state": expected_state,
                        "target_state": expense.state,
                        "checks": checks,
                    })

            attachment_issue_count = self._attachment_issue_count(
                attachment_stats,
            )
            bank_match_issue_count = sum((
                bank_match_cache_stats["refresh_error_count"],
                int(not bank_match_cache_stats["refresh_idempotent"]),
                int(not bank_match_cache_stats["accounting_unchanged"]),
                int(
                    not bank_match_cache_stats[
                        "legacy_target_schema"
                    ]["absent"],
                ),
                int(
                    bank_match_cache_stats[
                        "classified_association_count"
                    ]
                    != bank_match_cache_stats[
                        "source_cache_association_count"
                    ],
                ),
            ))
            status = (
                "passed"
                if (
                    not blocked_cases
                    and not mismatch_cases
                    and not attachment_issue_count
                    and not bank_match_issue_count
                )
                else "partial"
            )
            stats = {
                "classification": (
                    "SOURCE_FAITHFUL_NATIVE_EXPENSE_MATERIALIZATION"
                ),
                "date_from": options["date_from"],
                "date_to": options["date_to"],
                "source_expense_count": len(expense_rows),
                "created_expense_count": created_count,
                "reused_expense_count": reused_count,
                "linked_account_move_count": linked_move_count,
                "source_expense_line_link_count": len(source_line_links),
                "linked_expense_line_count": linked_line_count,
                "restored_context_count": context_stats[
                    "context_restored_count"
                ],
                "restored_split_link_count": context_stats[
                    "split_link_count"
                ],
                "passed_expense_count": passed_count,
                "mismatch_expense_count": len(mismatch_cases),
                "blocked_case_count": len(blocked_cases),
                "state_counts": dict(sorted(state_counts.items())),
                "compatibility_transformation_counts": dict(sorted(Counter(
                    item["classification"]
                    for item in compatibility_transformations
                ).items())),
                "compatibility_transformation_examples": (
                    compatibility_transformations[:20]
                ),
                "attachments": attachment_stats,
                "expense_bank_matches": bank_match_cache_stats,
                "mismatch_examples": mismatch_cases[:20],
                "blocked_examples": blocked_cases[:20],
            }
            self.write({
                "status": status,
                "finished_at": fields.Datetime.now(),
                "company_ids": [Command.set([
                    company.id for company in companies.values()
                ])],
                "imported_company_count": len(companies),
                "imported_account_count": len(accounts),
                "imported_journal_count": len(journals),
                "imported_partner_count": len(partners),
                "imported_move_count": linked_move_count,
                "warning_count": (
                    len(blocked_cases)
                    + len(mismatch_cases)
                    + attachment_issue_count
                    + bank_match_issue_count
                ),
                "statistics_json": stats,
                "notes": (
                    "Native expense documents and receipts are attached to "
                    "the exact imported source accounting entries. No "
                    "additional journal entry is generated."
                ),
            })
            return stats
        except Exception:
            self.write({
                "status": "failed",
                "finished_at": fields.Datetime.now(),
            })
            raise
        finally:
            conn.close()

    @staticmethod
    def _expense_transition_normalize(value):
        value = unicodedata.normalize("NFKD", value or "")
        return "".join(
            character
            for character in value
            if not unicodedata.combining(character)
        ).lower()

    def run_expense_batch_transition(self):
        """Move eligible Canada drafts to the product Batch workflow.

        This is deliberately a post-import reconstruction step. It never edits
        later-stage expenses or their journal entries and returns all ambiguous
        cases as external evidence instead of storing migration provenance on
        operational records.
        """
        self.ensure_one()
        Product = self.env["product.product"].sudo().with_context(active_test=False)
        Expense = self.env["hr.expense"].sudo().with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        Batch = self.env["usl.expense.batch"].sudo().with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        trip_codes = ("AUS26", "CA26", "LPASUM26", "BCN2602")
        trip_products = Product.search([("default_code", "in", trip_codes)])
        canada_products = trip_products.filtered(
            lambda product: product.default_code == "CA26",
        )
        canada_drafts = Expense.search([
            ("state", "=", "draft"),
            ("product_id", "in", canada_products.ids or [0]),
            ("expense_batch_id", "=", False),
        ])

        historical = Expense.search([
            ("product_id", "in", trip_products.ids or [0]),
            ("state", "!=", "draft"),
        ])
        historical_signature = [
            (
                expense.id,
                expense.product_id.id,
                expense.account_id.id,
                json.dumps(expense.analytic_distribution or {}, sort_keys=True),
                expense.account_move_id.id,
                expense.state,
            )
            for expense in historical.sorted("id")
        ]

        def reusable_product(code, name_fragments=()):
            candidates = Product.search([
                ("default_code", "=", code),
                ("can_be_expensed", "=", True),
                ("active", "=", True),
            ])
            if len(candidates) == 1:
                return candidates
            named = candidates.filtered(
                lambda product: any(
                    fragment in self._expense_transition_normalize(product.name)
                    for fragment in name_fragments
                ),
            )
            return named if len(named) == 1 else Product.browse()

        reusable_products = {
            "transport": reusable_product("TRANS"),
            "meal": reusable_product(
                "FOOD",
                ("foreign", "abroad", "etranger", "international"),
            ),
            "gift": reusable_product("GIFT_NOVAT"),
        }
        missing_products = [
            category for category, product in reusable_products.items() if not product
        ]
        if canada_drafts and missing_products:
            raise UserError(
                _(
                    "The Canada draft transition is missing unambiguous reusable "
                    "categories: %s.",
                    ", ".join(missing_products),
                ),
            )

        transitioned = Expense.browse()
        newly_batched = Expense.browse()
        ambiguous = []
        created_batches = Batch.browse()
        existing_batches = Batch.search([
            ("name", "=", "SBFH — Canada 2026"),
            ("state", "=", "draft"),
        ])
        batch_ids = set(existing_batches.ids)
        grouped_expense_ids = defaultdict(list)
        for expense in canada_drafts:
            grouped_expense_ids[expense.company_id.id, expense.employee_id.id].append(
                expense.id,
            )
        for (company_id, employee_id), expense_ids in grouped_expense_ids.items():
            company = self.env["res.company"].browse(company_id)
            employee = self.env["hr.employee"].browse(employee_id)
            grouped_expenses = Expense.browse(expense_ids)
            account = self.env["account.account"].sudo().search([
                ("code", "=", "625600"),
                ("company_ids", "in", company.id),
            ], limit=2)
            project = self.env["account.analytic.account"].sudo().search([
                ("name", "=", "SBFH prod"),
                ("plan_id.name", "=", "Projet"),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", company.id),
            ], limit=2)
            epic = self.env["account.analytic.account"].sudo().search([
                ("name", "=", "Canada 2026"),
                ("plan_id.name", "=", "Epic"),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", company.id),
            ], limit=2)
            if len(account) != 1 or len(project) != 1 or len(epic) != 1:
                raise UserError(
                    _(
                        "The Canada Batch requires one 625600 account, one "
                        "Projet / SBFH prod analytic account and one Epic / "
                        "Canada 2026 analytic account for %(company)s.",
                        company=company.display_name,
                    ),
                )
            distribution_key = f"{project.id},{epic.id}"
            batch = Batch.search([
                ("name", "=", "SBFH — Canada 2026"),
                ("employee_id", "=", employee.id),
                ("company_id", "=", company.id),
            ], limit=1)
            if not batch:
                dates = grouped_expenses.mapped("date")
                batch = Batch.create({
                    "name": "SBFH — Canada 2026",
                    "purpose": _("SBFH travel — Canada 2026"),
                    "context_type": "travel",
                    "context_date_from": min(dates) if dates else False,
                    "context_date_to": max(dates) if dates else False,
                    "employee_id": employee.id,
                    "company_id": company.id,
                    "account_override_id": account.id,
                    "analytic_distribution": {distribution_key: 100.0},
                })
                created_batches |= batch
            batch_ids.add(batch.id)

            expenses_to_link = Expense.browse()
            for expense in grouped_expenses.sorted("id"):
                normalized = self._expense_transition_normalize(
                    " ".join(filter(None, (expense.name, expense.description))),
                )
                if any(token in normalized for token in ("uber", "taxi")):
                    target = reusable_products["transport"]
                elif any(
                    token in normalized
                    for token in ("repas", "meal", "snack", "restaurant", "food")
                ):
                    target = reusable_products["meal"]
                elif any(token in normalized for token in ("gift", "cadeau")):
                    target = reusable_products["gift"]
                else:
                    target = Product.browse()
                if target and expense.product_id != target:
                    expense.write({
                        "product_id": target.id,
                        "product_uom_id": target.uom_id.id,
                    })
                    transitioned |= expense
                elif not target:
                    ambiguous.append({
                        "expense_id": expense.id,
                        "name": expense.name,
                        "reason": "description_not_confidently_mapped",
                    })
                if expense.expense_batch_id and expense.expense_batch_id != batch:
                    ambiguous.append({
                        "expense_id": expense.id,
                        "name": expense.name,
                        "reason": "already_in_another_batch",
                    })
                    continue
                if not expense.expense_batch_id:
                    expenses_to_link |= expense
            if expenses_to_link:
                expenses_to_link.with_context(
                    usl_batch_context_defer_audit=True,
                ).write({"expense_batch_id": batch.id})
                newly_batched |= expenses_to_link

        normalized = Expense.browse()
        normalized_incompatible_taxes = Expense.browse()
        cleaned_context_messages = self.env["mail.message"]
        migration_summary_count = 0
        batches = Batch.browse(batch_ids).exists()
        for batch in batches:
            existing_migration_summaries = batch.message_ids.filtered(
                lambda message: (
                    "Canada draft transition prepared" in (message.body or "")
                ),
            )
            for expense in batch.expense_ids.filtered(
                lambda item: item.state == "draft",
            ):
                fiscal_country = (
                    expense.company_id.account_fiscal_country_id
                    or expense.company_id.country_id
                )
                incompatible_taxes = expense.tax_ids.filtered(
                    lambda tax: (
                        tax.country_id
                        and fiscal_country
                        and tax.country_id != fiscal_country
                    ),
                )
                if incompatible_taxes:
                    expense.write({
                        "tax_ids": [
                            Command.set((expense.tax_ids - incompatible_taxes).ids),
                        ],
                    })
                    normalized_incompatible_taxes |= expense
                values = {}
                if (
                    batch.account_override_id
                    and expense.account_id == batch.account_override_id
                    and expense.account_context_source != "batch"
                ):
                    if not expense.batch_account_baseline_captured:
                        values.update({
                            "pre_batch_account_id": expense.account_id.id,
                            "pre_batch_account_context_source": (
                                expense.account_context_source
                            ),
                            "batch_account_baseline_captured": True,
                        })
                    values.update({
                        "account_context_source": "batch",
                        "batch_applied_account_id": batch.account_override_id.id,
                    })
                if (
                    batch.analytic_distribution
                    and expense._analytic_distributions_equal(
                        expense.analytic_distribution,
                        batch.analytic_distribution,
                    )
                    and expense.analytic_context_source != "batch"
                ):
                    if not expense.batch_analytic_baseline_captured:
                        values.update({
                            "pre_batch_analytic_distribution": (
                                expense.analytic_distribution or {}
                            ),
                            "pre_batch_analytic_context_source": (
                                expense.analytic_context_source
                            ),
                            "batch_analytic_baseline_captured": True,
                        })
                    values.update({
                        "analytic_context_source": "batch",
                        "batch_applied_analytic_distribution": (
                            batch.analytic_distribution
                        ),
                    })
                if values:
                    values["batch_context_revision"] = batch.context_revision
                    expense.with_context(usl_batch_context_internal=True).write(values)
                    normalized |= expense

            generated_context_messages = batch.message_ids.filtered(
                lambda message: (
                    "Shared context revision" in (message.body or "")
                    and "explicit exception(s) were preserved" in (message.body or "")
                ),
            )
            if generated_context_messages:
                cleaned_context_messages |= generated_context_messages
                generated_context_messages.unlink()

            batch_changed = bool(
                (newly_batched & batch.expense_ids)
                or (transitioned & batch.expense_ids)
                or (normalized & batch.expense_ids)
                or (normalized_incompatible_taxes & batch.expense_ids)
                or generated_context_messages,
            )
            if batch_changed and not existing_migration_summaries:
                inherited_count = len(
                    batch.expense_ids.filtered(
                        lambda expense: (
                            expense.account_context_source == "batch"
                            or expense.analytic_context_source == "batch"
                        ),
                    ),
                )
                batch.message_post(
                    body=_(
                        "Canada draft transition prepared %(count)s expense(s): "
                        "%(inherited)s use shared context, %(exceptions)s keep "
                        "line exceptions and %(incomplete)s need information. "
                        "%(normalized_taxes)s incompatible imported tax "
                        "selection(s) were removed. "
                        "Nothing was submitted or posted.",
                        count=batch.expense_count,
                        inherited=inherited_count,
                        exceptions=batch.exception_count,
                        incomplete=batch.incomplete_count,
                        normalized_taxes=len(
                            normalized_incompatible_taxes & batch.expense_ids,
                        ),
                    ),
                )
                migration_summary_count += 1

        archived_templates = trip_products.product_tmpl_id.filtered("active")
        if archived_templates:
            archived_templates.write({"active": False})
        historical.invalidate_recordset()
        historical_signature_after = [
            (
                expense.id,
                expense.product_id.id,
                expense.account_id.id,
                json.dumps(expense.analytic_distribution or {}, sort_keys=True),
                expense.account_move_id.id,
                expense.state,
            )
            for expense in historical.sorted("id")
        ]
        return {
            "classification": "EXPENSE_BATCH_CONTEXT_TRANSITION",
            "candidate_draft_count": len(canada_drafts),
            "reclassified_expense_count": len(transitioned),
            "created_batch_count": len(created_batches),
            "batch_ids": batches.ids,
            "batched_expense_count": sum(batches.mapped("expense_count")),
            "newly_batched_expense_count": len(newly_batched),
            "normalized_inherited_count": len(normalized),
            "normalized_incompatible_tax_count": len(
                normalized_incompatible_taxes,
            ),
            "incomplete_expense_count": sum(batches.mapped("incomplete_count")),
            "exception_expense_count": sum(batches.mapped("exception_count")),
            "cleaned_context_message_count": len(cleaned_context_messages),
            "migration_summary_message_count": migration_summary_count,
            "ambiguous_count": len(ambiguous),
            "ambiguous_examples": ambiguous[:50],
            "archived_trip_product_count": len(archived_templates),
            "archived_trip_product_codes": sorted(
                trip_products.mapped("default_code"),
            ),
            "historical_expense_count": len(historical),
            "historical_unchanged": (
                historical_signature == historical_signature_after
            ),
        }

    def run_native_expense_replay_from_source(self, options):
        """Rebuild source expenses through the standard Odoo expense engine.

        Company-paid expenses generate native payments. Employee-paid expenses
        generate native grouped purchase receipts. Final bank matching and
        employee reimbursement are intentionally left to the following Track B
        stage, while this stage proves the expense documents and their complete
        accounting effects.
        """
        self.ensure_one()
        options = {
            "source_database": "odoo_online_source_saas_19_3",
            "source_snapshot_id": "source-unknown",
            "source_dump_sha256": "",
            "source_version": "Odoo Online Enterprise saas~19.3",
            "target_database": self.env.cr.dbname,
            "date_from": "2025-10-01",
            "date_to": "2026-06-30",
            "source_company_ids": [1],
            **(options or {}),
        }
        options["source_company_ids"] = self._source_company_ids(options)
        self.write({
            "status": "running",
            "mode": "native_engine_replay",
            "source_database": options["source_database"],
            "source_dump_sha256": options.get("source_dump_sha256"),
            "source_snapshot_id": options["source_snapshot_id"],
            "source_version": options.get("source_version"),
            "target_database": options["target_database"],
        })
        conn = self._source_connection(options)
        try:
            currencies = self._currency_map(conn)
            countries = self._country_map(conn)
            companies, _company_rows = self._company_map(conn, options, countries)
            currency_rate_stats = self._import_currency_rates(conn, options, companies, currencies)
            partners = self._partner_map(conn, options)
            accounts, account_ids_to_archive_after_post = self._account_map(
                conn,
                options,
                companies,
                currencies,
            )
            expense_account_stats = self._native_expense_extend_account_map(
                conn,
                options,
                companies,
                currencies,
                accounts,
            )
            account_ids_to_archive_after_post.extend(
                expense_account_stats["archive_after_post_source_ids"],
            )
            tax_tags = self._tax_tag_map(conn, options, countries)
            tax_groups = self._tax_group_map(conn, options, companies, accounts, countries)
            taxes, _tax_repartition_lines, tax_stats = self._tax_map(
                conn,
                options,
                companies,
                accounts,
                tax_groups,
                tax_tags,
                countries,
            )
            cash_basis_companies = self._sync_company_cash_basis_flags(companies)
            tax_stats["company_cash_basis_setting_count"] = len(cash_basis_companies)
            fiscal_positions, fiscal_position_stats = self._fiscal_position_map(
                conn,
                options,
                companies,
                accounts,
                taxes,
                countries,
            )
            partner_property_stats = self._sync_partner_accounting_properties(
                conn,
                options,
                companies,
                partners,
                accounts,
                fiscal_positions,
            )
            journals, journal_ids_to_archive_after_post = self._journal_map(
                conn,
                options,
                companies,
                accounts,
                currencies,
            )
            method_lines = self._payment_method_line_map(conn, journals, accounts)
            analytic_plans = self._analytic_plan_map(conn, options)
            analytic_accounts = self._analytic_account_map(
                conn,
                options,
                companies,
                partners,
                analytic_plans,
            )
            _reconciliation_models, reconciliation_model_stats = (
                self._reconciliation_model_map(
                    conn,
                    options,
                    companies,
                    accounts,
                    journals,
                    partners,
                    taxes,
                    analytic_accounts,
                )
            )
            expense_rows = self._native_expense_rows(conn, options)
            move_rows = self._native_expense_move_rows(conn, options)
            source_totals_by_move = self._native_expense_source_account_totals(conn, options)
            employees = self._native_expense_employee_map(
                conn,
                options,
                companies,
                partners,
            )
            products, current_standard_prices = self._native_expense_product_map(
                conn,
                options,
                companies,
                accounts,
            )
            expense_configuration_stats = self._native_expense_configure_companies(
                conn,
                options,
                companies,
                journals,
                method_lines,
                accounts,
                move_rows,
            )

            move_rows_by_id = {row["id"]: row for row in move_rows}
            expense_rows_by_move = defaultdict(list)
            state_counts = defaultdict(int)
            payment_mode_counts = defaultdict(int)
            for row in expense_rows:
                state_counts[row["state"] or "draft"] += 1
                payment_mode_counts[row["payment_mode"]] += 1
                if row["account_move_id"]:
                    expense_rows_by_move[row["account_move_id"]].append(row)

            Expense = self.env["hr.expense"].sudo().with_context(
                tracking_disable=True,
                mail_create_nolog=True,
                rebuild_source_materialization=True,
            )
            Move = self.env["account.move"].sudo().with_context(
                tracking_disable=True,
                mail_create_nolog=True,
            )
            created_expense_count = 0
            reused_expense_count = 0
            blocked_cases = []

            for source_expense in expense_rows:
                company = companies.get(source_expense["company_id"])
                employee = employees.get(source_expense["employee_id"])
                product = products.get(source_expense["product_id"])
                currency = currencies.get(source_expense["currency_id"])
                account = accounts.get(source_expense["account_id"])
                vendor = partners.get(source_expense["vendor_id"])
                payment_method_line = method_lines.get(
                    source_expense["payment_method_line_id"],
                )
                missing_tax_ids = sorted(
                    source_tax_id
                    for source_tax_id in source_expense["tax_ids"]
                    if source_tax_id not in taxes
                )
                blockers = []
                if not company:
                    blockers.append("company")
                if not employee:
                    blockers.append("employee")
                if not product:
                    blockers.append("product")
                if not currency:
                    blockers.append("currency")
                if not account:
                    blockers.append("account")
                if source_expense["vendor_id"] and not vendor:
                    blockers.append("vendor")
                if (
                    source_expense["payment_mode"] == "company_account"
                    and not payment_method_line
                ):
                    blockers.append("payment_method_line")
                if missing_tax_ids:
                    blockers.append("taxes")
                if source_expense["state"] == "paid" and not source_expense["account_move_id"]:
                    blockers.append("account_move")
                if blockers:
                    blocked_cases.append({
                        "source_expense_id": source_expense["id"],
                        "source_name": source_expense["name"],
                        "classification": "insufficient_source_or_mapping_data",
                        "blockers": blockers,
                        "missing_source_tax_ids": missing_tax_ids,
                    })
                    continue

                expense = Expense.search([
                    ("rebuild_source_model", "=", "hr.expense"),
                    ("rebuild_source_id", "=", source_expense["id"]),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ], limit=1)
                if expense:
                    reused_expense_count += 1
                else:
                    try:
                        with self.env.cr.savepoint():
                            current_standard_price = current_standard_prices.get(
                                (source_expense["product_id"], source_expense["company_id"]),
                                0.0,
                            )
                            # Priced category products derive the historical unit
                            # price from standard cost. Zero-cost products retain
                            # the source expense's explicit entered total instead.
                            if current_standard_price:
                                product.with_company(company).standard_price = self._amount(
                                    source_expense["price_unit"],
                                )
                            vals = {
                                "name": source_expense["name"] or f"Source expense {source_expense['id']}",
                                "date": source_expense["date"],
                                "employee_id": employee.id,
                                "company_id": company.id,
                                "product_id": product.id,
                                "product_uom_id": product.uom_id.id,
                                "currency_id": currency.id,
                                "payment_mode": source_expense["payment_mode"],
                                "vendor_id": vendor.id if vendor else False,
                                "account_id": account.id,
                                "tax_ids": [Command.set([
                                    taxes[source_tax_id].id
                                    for source_tax_id in source_expense["tax_ids"]
                                ])],
                                "quantity": self._amount(source_expense["quantity"]),
                                # The SaaS field is stored/precomputed and its
                                # current product cost can otherwise replace
                                # the historical source unit price during
                                # create. Supplying the source business value
                                # suppresses that precompute for this replay.
                                "price_unit": self._amount(
                                    source_expense["price_unit"],
                                ),
                                "total_amount_currency": self._amount(
                                    source_expense["total_amount_currency"],
                                ),
                                "total_amount": self._amount(source_expense["total_amount"]),
                                "analytic_distribution": (
                                    self._native_replay_analytic_distribution(
                                        source_expense["analytic_distribution"],
                                        analytic_accounts,
                                    )
                                ),
                                "description": source_expense["description"],
                                "rebuild_import_note": (
                                    "Track B native expense replay: the expense document was "
                                    "reconstructed from source business fields and advanced through "
                                    "the standard Odoo approval and accounting workflow."
                                ),
                                **self._trace_values(
                                    "hr.expense",
                                    source_expense["id"],
                                    options,
                                ),
                            }
                            if payment_method_line:
                                vals["payment_method_line_id"] = payment_method_line.id
                            expense = Expense.with_company(company).with_context(
                                rebuild_source_expense_price_unit=self._amount(
                                    source_expense["price_unit"],
                                ),
                            ).create(vals)
                            expense.flush_recordset([
                                "price_unit",
                                "total_amount_currency",
                                "total_amount",
                                "tax_amount_currency",
                                "tax_amount",
                                "untaxed_amount_currency",
                                "untaxed_amount",
                            ])
                            if current_standard_price:
                                product.with_company(company).standard_price = current_standard_price
                        created_expense_count += 1
                    except Exception as exc:  # noqa: BLE001 - classify each source expense.
                        blocked_cases.append({
                            "source_expense_id": source_expense["id"],
                            "source_name": source_expense["name"],
                            "classification": "native_expense_creation_error",
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                        })

            expenses_by_source_id = {
                expense.rebuild_source_id: expense
                for expense in Expense.search([
                    ("rebuild_source_model", "=", "hr.expense"),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                    ("rebuild_source_id", "in", [row["id"] for row in expense_rows] or [0]),
                ])
            }
            expense_attachment_stats = self._import_attachments(
                conn,
                {
                    **options,
                    "attachment_target_trace_models": {
                        "hr.expense": ["hr.expense"],
                    },
                },
                companies,
                rows=self._native_replay_expense_attachment_rows(conn, options),
            )
            expense_attachment_issue_count = self._attachment_issue_count(
                expense_attachment_stats,
            )

            # The USL product policy requires the receipt to exist before an
            # expense can be submitted. Create every expense in draft first,
            # restore its source attachment above, and only then exercise the
            # standard refusal/submission/approval workflow. The replay-only
            # context preserves source-approved records whose receipt was not
            # retained without weakening the policy for normal UI submissions.
            for source_expense in expense_rows:
                expense = expenses_by_source_id.get(source_expense["id"])
                if not expense or expense.state != "draft":
                    continue
                try:
                    with self.env.cr.savepoint():
                        replay_expense = expense.with_context(
                            rebuild_source_expense_price_unit=self._amount(
                                source_expense["price_unit"],
                            ),
                        )
                        replay_expense._compute_price_unit()
                        replay_expense.flush_recordset([
                            "price_unit",
                            "total_amount_currency",
                            "total_amount",
                            "tax_amount_currency",
                            "tax_amount",
                            "untaxed_amount_currency",
                            "untaxed_amount",
                        ])
                        if source_expense["state"] == "refused":
                            replay_expense._do_refuse("Restored source refusal")
                        elif source_expense["state"] != "draft":
                            replay_expense.action_submit()
                            if expense.state == "submitted":
                                replay_expense._do_approve()
                            if source_expense["approval_date"]:
                                expense.approval_date = (
                                    source_expense["approval_date"]
                                )
                except Exception as exc:  # noqa: BLE001 - classify each source expense.
                    blocked_cases.append({
                        "source_expense_id": source_expense["id"],
                        "source_name": source_expense["name"],
                        "classification": "native_expense_workflow_error",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    })

            created_company_payment_move_count = 0
            reused_company_payment_move_count = 0
            for source_expense in expense_rows:
                if (
                    source_expense["state"] != "paid"
                    or source_expense["payment_mode"] != "company_account"
                ):
                    continue
                expense = expenses_by_source_id.get(source_expense["id"])
                source_move = move_rows_by_id.get(source_expense["account_move_id"])
                if not expense or not source_move:
                    continue
                if expense.account_move_id:
                    reused_company_payment_move_count += 1
                    continue
                try:
                    with self.env.cr.savepoint():
                        expense._create_company_paid_moves()
                        move = expense.account_move_id
                        payment = move.origin_payment_id
                        move.write({
                            "rebuild_source_move_type": source_move["move_type"],
                            "rebuild_import_note": (
                                "Track B native company-paid expense entry generated by "
                                "hr.expense._create_company_paid_moves()."
                            ),
                            **self._trace_values(
                                "account.move.native_expense_replay",
                                source_move["id"],
                                options,
                            ),
                        })
                        payment.write({
                            "rebuild_import_note": (
                                "Track B native company-paid expense payment; final bank "
                                "matching is replayed in the reconciliation stage."
                            ),
                            **self._trace_values(
                                "account.payment.native_expense_replay",
                                source_move["payment_id"],
                                options,
                            ),
                        })
                        payment.action_post()
                    created_company_payment_move_count += 1
                except Exception as exc:  # noqa: BLE001 - classify native payment defects.
                    expense.invalidate_recordset(["account_move_id", "state"])
                    blocked_cases.append({
                        "source_expense_id": source_expense["id"],
                        "source_move_id": source_move["id"],
                        "classification": "native_company_expense_posting_error",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    })

            created_employee_receipt_count = 0
            reused_employee_receipt_count = 0
            for source_move_id, source_expenses in sorted(expense_rows_by_move.items()):
                if not source_expenses or source_expenses[0]["payment_mode"] != "own_account":
                    continue
                source_move = move_rows_by_id.get(source_move_id)
                target_expenses = Expense.browse([
                    expenses_by_source_id[row["id"]].id
                    for row in source_expenses
                    if row["id"] in expenses_by_source_id
                ])
                if not source_move or len(target_expenses) != len(source_expenses):
                    continue
                if target_expenses.account_move_id:
                    reused_employee_receipt_count += 1
                    continue
                company = companies[source_move["company_id"]]
                journal = journals.get(source_move["journal_id"])
                source_currency = currencies.get(source_move["currency_id"])
                if not journal or not source_currency:
                    blocked_cases.append({
                        "source_move_id": source_move_id,
                        "classification": "native_employee_receipt_configuration_error",
                        "blockers": [
                            name
                            for name, value in (
                                ("journal", journal),
                                ("currency", source_currency),
                            )
                            if not value
                        ],
                    })
                    continue
                try:
                    with self.env.cr.savepoint():
                        receipt_vals_list = target_expenses.with_company(
                            company,
                        )._prepare_receipts_vals()
                        if len(receipt_vals_list) != 1:
                            blocked_cases.append({
                                "source_move_id": source_move_id,
                                "source_expense_ids": [
                                    row["id"] for row in source_expenses
                                ],
                                "classification": "native_employee_receipt_grouping_error",
                                "generated_receipt_count": len(receipt_vals_list),
                            })
                            continue
                        receipt_vals = {
                            **receipt_vals_list[0],
                            "journal_id": journal.id,
                            "currency_id": source_currency.id,
                            "date": source_move["date"],
                            "invoice_date": source_move["invoice_date"],
                            "invoice_date_due": source_move["invoice_date_due"],
                            "ref": source_move["ref"],
                            "rebuild_source_move_type": source_move["move_type"],
                            "rebuild_import_note": (
                                "Track B native employee-paid expense receipt generated by "
                                "hr.expense._prepare_receipts_vals() and normal action_post."
                            ),
                            **self._trace_values(
                                "account.move.native_expense_replay",
                                source_move_id,
                                options,
                            ),
                        }
                        move = Move.with_company(company).create(receipt_vals)
                        move.action_post()
                    created_employee_receipt_count += 1
                except Exception as exc:  # noqa: BLE001 - classify native receipt defects.
                    target_expenses.invalidate_recordset(["account_move_id", "state"])
                    blocked_cases.append({
                        "source_move_id": source_move_id,
                        "source_expense_ids": [row["id"] for row in source_expenses],
                        "classification": "native_employee_receipt_posting_error",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    })

            target_moves_by_source_id = {
                move.rebuild_source_id: move
                for move in Move.search([
                    ("rebuild_source_model", "=", "account.move.native_expense_replay"),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                    ("rebuild_source_id", "in", list(move_rows_by_id) or [0]),
                ])
            }

            passed_expenses = []
            mismatch_expenses = []
            deferred_employee_reimbursement_count = 0
            for source_expense in expense_rows:
                expense = expenses_by_source_id.get(source_expense["id"])
                if not expense:
                    continue
                company = companies[source_expense["company_id"]]
                expected_states = {source_expense["state"]}
                if (
                    source_expense["state"] == "paid"
                    and source_expense["payment_mode"] == "own_account"
                ):
                    expected_states = {"posted", "paid"}
                    deferred_employee_reimbursement_count += 1
                expected_distribution = self._native_replay_analytic_distribution(
                    source_expense["analytic_distribution"],
                    analytic_accounts,
                ) or {}
                checks = {
                    "name": expense.name == (source_expense["name"] or ""),
                    "date": expense.date == source_expense["date"],
                    "state": expense.state in expected_states,
                    "approval_state": (
                        (expense.approval_state or False)
                        == (source_expense["approval_state"] or False)
                    ),
                    "payment_mode": expense.payment_mode == source_expense["payment_mode"],
                    "company": expense.company_id == company,
                    "employee": expense.employee_id.rebuild_source_id == source_expense["employee_id"],
                    "product": expense.product_id.rebuild_source_id == source_expense["product_id"],
                    "currency": expense.currency_id == currencies[source_expense["currency_id"]],
                    "account": expense.account_id.rebuild_source_id == source_expense["account_id"],
                    "vendor": (
                        (expense.vendor_id.rebuild_source_id or None)
                        == source_expense["vendor_id"]
                    ),
                    "taxes": sorted(expense.tax_ids.mapped("rebuild_source_id"))
                    == list(source_expense["tax_ids"]),
                    "quantity": round(expense.quantity, 4)
                    == round(self._amount(source_expense["quantity"]), 4),
                    "price_unit": round(expense.price_unit, 4)
                    == round(self._amount(source_expense["price_unit"]), 4),
                    "total_amount_currency": round(expense.total_amount_currency, 2)
                    == round(self._amount(source_expense["total_amount_currency"]), 2),
                    "total_amount": round(expense.total_amount, 2)
                    == round(self._amount(source_expense["total_amount"]), 2),
                    "tax_amount_currency": round(expense.tax_amount_currency, 2)
                    == round(self._amount(source_expense["tax_amount_currency"]), 2),
                    "tax_amount": round(expense.tax_amount, 2)
                    == round(self._amount(source_expense["tax_amount"]), 2),
                    "untaxed_amount_currency": round(expense.untaxed_amount_currency, 2)
                    == round(self._amount(source_expense["untaxed_amount_currency"]), 2),
                    "untaxed_amount": round(expense.untaxed_amount, 2)
                    == round(self._amount(source_expense["untaxed_amount"]), 2),
                    "analytic_distribution": (expense.analytic_distribution or {})
                    == expected_distribution,
                    "description": (expense.description or "")
                    == (source_expense["description"] or ""),
                    "account_move": (
                        (expense.account_move_id.rebuild_source_id or None)
                        == source_expense["account_move_id"]
                    ),
                }
                result = {
                    "source_expense_id": source_expense["id"],
                    "target_expense_id": expense.id,
                    "name": expense.name,
                    "source_state": source_expense["state"],
                    "expected_stage_states": sorted(expected_states),
                    "target_state": expense.state,
                    "payment_mode": expense.payment_mode,
                    "checks": checks,
                }
                if all(checks.values()):
                    passed_expenses.append(result)
                else:
                    mismatch_expenses.append(result)

            passed_moves = []
            mismatch_moves = []
            deferred_bank_match_count = 0
            for source_move in move_rows:
                move = target_moves_by_source_id.get(source_move["id"])
                if not move:
                    blocked_cases.append({
                        "source_move_id": source_move["id"],
                        "classification": "missing_native_expense_move",
                    })
                    continue
                source_account_totals = source_totals_by_move[source_move["id"]]
                target_account_totals = self._native_replay_target_account_totals(move)
                checks = {
                    "state": move.state == "posted",
                    "move_type": move.move_type == source_move["move_type"],
                    "journal": move.journal_id == journals[source_move["journal_id"]],
                    "company": move.company_id == companies[source_move["company_id"]],
                    "partner": (
                        (move.partner_id.rebuild_source_id or None)
                        == source_move["partner_id"]
                    ),
                    "currency": move.currency_id == currencies[source_move["currency_id"]],
                    "date": move.date == source_move["date"],
                    "invoice_date": (move.invoice_date or None)
                    == source_move["invoice_date"],
                    "invoice_date_due": (
                        move.move_type == "entry"
                        or (move.invoice_date_due or None)
                        == source_move["invoice_date_due"]
                    ),
                    "ref": (move.ref or "") == (source_move["ref"] or ""),
                    "amount_untaxed": round(move.amount_untaxed, 2)
                    == round(self._amount(source_move["amount_untaxed"]), 2),
                    "amount_tax": round(move.amount_tax, 2)
                    == round(self._amount(source_move["amount_tax"]), 2),
                    "amount_total": round(move.amount_total, 2)
                    == round(self._amount(source_move["amount_total"]), 2),
                    "account_totals": source_account_totals == target_account_totals,
                }
                payment_result = None
                if source_move["payment_id"]:
                    deferred_bank_match_count += 1
                    payment = move.origin_payment_id
                    payment_checks = {
                        "present": bool(payment),
                        "source_trace": (
                            payment.rebuild_source_id == source_move["payment_id"]
                        ),
                        "state": payment.state in {"in_process", "paid"},
                        "date": payment.date == source_move["payment_date"],
                        "journal": payment.journal_id == journals[source_move["payment_journal_id"]],
                        "company": payment.company_id == companies[source_move["payment_company_id"]],
                        "currency": payment.currency_id == currencies[source_move["payment_currency_id"]],
                        "partner": (
                            (payment.partner_id.rebuild_source_id or None)
                            == source_move["payment_partner_id"]
                        ),
                        "method": payment.payment_method_line_id
                        == method_lines[source_move["payment_method_line_id"]],
                        "outstanding_account": (
                            payment.outstanding_account_id.rebuild_source_id
                            == source_move["outstanding_account_id"]
                        ),
                        "amount": round(payment.amount, 2)
                        == round(self._amount(source_move["payment_amount"]), 2),
                        "payment_type": payment.payment_type == source_move["payment_type"],
                        "partner_type": payment.partner_type == source_move["partner_type"],
                        "memo": (payment.memo or "") == (source_move["memo"] or ""),
                    }
                    checks["payment_business_fields"] = all(payment_checks.values())
                    payment_result = {
                        "source_payment_id": source_move["payment_id"],
                        "target_payment_id": payment.id,
                        "source_state": source_move["payment_state"],
                        "expected_stage_states": ["in_process", "paid"],
                        "target_state": payment.state,
                        "destination_account_classification": (
                            "Source holds a legacy payable-account hint, while the current "
                            "native company-expense workflow derives a neutral destination. "
                            "The posted move uses the separately validated outstanding account."
                        ),
                        "source_destination_account_id": source_move[
                            "destination_account_id"
                        ],
                        "target_destination_account_source_id": (
                            payment.destination_account_id.rebuild_source_id or None
                        ),
                        "checks": payment_checks,
                    }
                result = {
                    "source_move_id": source_move["id"],
                    "source_name": source_move["name"],
                    "target_move_id": move.id,
                    "target_name": move.name,
                    "expense_count": len(expense_rows_by_move[source_move["id"]]),
                    "move_type": move.move_type,
                    "checks": checks,
                    "payment": payment_result,
                }
                if all(checks.values()):
                    passed_moves.append(result)
                else:
                    mismatch_moves.append({
                        **result,
                        "source_account_totals": source_account_totals,
                        "target_account_totals": target_account_totals,
                    })

            for source_account_id in account_ids_to_archive_after_post:
                accounts[source_account_id].active = False
            for source_journal_id in journal_ids_to_archive_after_post:
                journals[source_journal_id].active = False

            status = (
                "passed"
                if (
                    not blocked_cases
                    and not mismatch_expenses
                    and not mismatch_moves
                    and not expense_attachment_issue_count
                )
                else "partial"
            )
            stats = {
                "classification": "NATIVE_VALIDATION_NATIVE_EXPENSE_REPLAY",
                "date_from": options["date_from"],
                "date_to": options["date_to"],
                "source_company_ids": options["source_company_ids"],
                "source_expense_count": len(expense_rows),
                "created_expense_count": created_expense_count,
                "reused_expense_count": reused_expense_count,
                "passed_expense_count": len(passed_expenses),
                "mismatch_expense_count": len(mismatch_expenses),
                "blocked_case_count": len(blocked_cases),
                "source_generated_move_count": len(move_rows),
                "created_company_payment_move_count": created_company_payment_move_count,
                "reused_company_payment_move_count": reused_company_payment_move_count,
                "created_employee_receipt_count": created_employee_receipt_count,
                "reused_employee_receipt_count": reused_employee_receipt_count,
                "passed_generated_move_count": len(passed_moves),
                "mismatch_generated_move_count": len(mismatch_moves),
                "native_company_payment_count": sum(
                    bool(row["payment_id"]) for row in move_rows
                ),
                "deferred_bank_match_count": deferred_bank_match_count,
                "deferred_employee_reimbursement_count": (
                    deferred_employee_reimbursement_count
                ),
                "state_counts": dict(sorted(state_counts.items())),
                "payment_mode_counts": dict(sorted(payment_mode_counts.items())),
                "passed_expense_examples": passed_expenses[:20],
                "mismatch_expense_examples": mismatch_expenses[:20],
                "passed_move_examples": passed_moves[:20],
                "mismatch_move_examples": mismatch_moves[:20],
                "blocked_examples": blocked_cases[:20],
                "currency_rates": currency_rate_stats,
                "tax_configuration": tax_stats,
                "fiscal_positions": fiscal_position_stats,
                "reconciliation_models": reconciliation_model_stats,
                "partner_accounting_properties": partner_property_stats,
                "expense_accounts": expense_account_stats,
                "expense_configuration": expense_configuration_stats,
                "attachments": expense_attachment_stats,
                "next_stage_classification": {
                    "company_payments": (
                        "Native payments and exact accounting effects exist; their source "
                        "reconciled state awaits bank transaction matching."
                    ),
                    "employee_expenses": (
                        "Native receipts and exact accounting effects exist; their source "
                        "paid state awaits employee reimbursement replay."
                    ),
                },
            }
            self.write({
                "status": status,
                "finished_at": fields.Datetime.now(),
                "company_ids": [Command.set([company.id for company in companies.values()])],
                "imported_company_count": len(companies),
                "imported_currency_rate_count": currency_rate_stats["imported_currency_rate_count"],
                "imported_account_count": len(accounts),
                "imported_journal_count": len(journals),
                "imported_partner_count": len(partners),
                "imported_move_count": len(passed_moves) + len(mismatch_moves),
                "imported_payment_count": sum(bool(row["payment_id"]) for row in move_rows),
                "warning_count": (
                    len(blocked_cases)
                    + len(mismatch_expenses)
                    + len(mismatch_moves)
                    + expense_attachment_issue_count
                ),
                "statistics_json": stats,
                "notes": (
                    "Dedicated Track B expense replay. Expense documents are reconstructed "
                    "through native approval, payment and receipt generation. Bank matching "
                    "and employee reimbursement remain explicit inputs to the next stage."
                ),
            })
            return stats
        except Exception:
            self.write({
                "status": "failed",
                "finished_at": fields.Datetime.now(),
            })
            raise
        finally:
            conn.close()

    def run_native_engine_replay_from_source(self, options):
        """Recompute Track B business documents through native invoice posting.

        This mode runs only in the dedicated replay database.  It imports
        configuration, reconstructs commercial invoice lines, and calls normal
        ``action_post`` so taxes, due lines, currencies and analytics are
        generated by Odoo rather than copied from the finalized source entry.
        """
        self.ensure_one()
        options = {
            "source_database": "odoo_online_source_saas_19_3",
            "source_snapshot_id": "source-unknown",
            "source_dump_sha256": "",
            "source_version": "Odoo Online Enterprise saas~19.3",
            "target_database": self.env.cr.dbname,
            "date_from": "2025-10-01",
            "date_to": "2026-06-30",
            "source_company_ids": [1],
            **(options or {}),
        }
        options["source_company_ids"] = self._source_company_ids(options)
        self.write({
            "status": "running",
            "mode": "native_engine_replay",
            "source_database": options["source_database"],
            "source_dump_sha256": options.get("source_dump_sha256"),
            "source_snapshot_id": options["source_snapshot_id"],
            "source_version": options.get("source_version"),
            "target_database": options["target_database"],
        })
        conn = self._source_connection(options)
        try:
            currencies = self._currency_map(conn)
            countries = self._country_map(conn)
            companies, _company_rows = self._company_map(conn, options, countries)
            currency_rate_stats = self._import_currency_rates(conn, options, companies, currencies)
            partners = self._partner_map(conn, options)
            accounts, account_ids_to_archive_after_post = self._account_map(
                conn,
                options,
                companies,
                currencies,
            )
            tax_tags = self._tax_tag_map(conn, options, countries)
            tax_groups = self._tax_group_map(conn, options, companies, accounts, countries)
            taxes, _tax_repartition_lines, tax_stats = self._tax_map(
                conn,
                options,
                companies,
                accounts,
                tax_groups,
                tax_tags,
                countries,
            )
            cash_basis_companies = self._sync_company_cash_basis_flags(companies)
            tax_stats["company_cash_basis_setting_count"] = len(cash_basis_companies)
            payment_terms, payment_term_stats = self._payment_term_map(conn, options, companies)
            fiscal_positions, fiscal_position_stats = self._fiscal_position_map(
                conn,
                options,
                companies,
                accounts,
                taxes,
                countries,
            )
            partner_property_stats = self._sync_partner_accounting_properties(
                conn,
                options,
                companies,
                partners,
                accounts,
                fiscal_positions,
            )
            journals, journal_ids_to_archive_after_post = self._journal_map(
                conn,
                options,
                companies,
                accounts,
                currencies,
            )
            method_lines = self._payment_method_line_map(
                conn,
                journals,
                accounts,
            )
            self._native_expense_configure_companies(
                conn,
                options,
                companies,
                journals,
                method_lines,
                accounts,
                self._native_expense_move_rows(conn, options),
            )
            analytic_plans = self._analytic_plan_map(conn, options)
            analytic_accounts = self._analytic_account_map(
                conn,
                options,
                companies,
                partners,
                analytic_plans,
            )
            _reconciliation_models, reconciliation_model_stats = (
                self._reconciliation_model_map(
                    conn,
                    options,
                    companies,
                    accounts,
                    journals,
                    partners,
                    taxes,
                    analytic_accounts,
                )
            )
            document_rows = self._native_replay_document_rows(conn, options)
            lines_by_move = self._native_replay_line_rows_by_move(conn, options)
            source_totals_by_move = self._native_replay_source_account_totals(conn, options)
            source_tax_totals_by_move = self._native_replay_source_tax_totals(conn, options)

            Move = self.env["account.move"]
            passed_cases = []
            mismatch_cases = []
            blocked_cases = []
            manual_tax_override_cases = []
            created_count = 0
            reused_count = 0
            type_counts = defaultdict(int)
            currency_counts = defaultdict(int)

            for source_move in document_rows:
                type_counts[source_move["move_type"]] += 1
                source_currency = currencies.get(source_move["currency_id"])
                currency_counts[source_currency.name if source_currency else "unmapped"] += 1
                company = companies.get(source_move["company_id"])
                journal = journals.get(source_move["journal_id"])
                partner = partners.get(source_move["partner_id"])
                source_lines = lines_by_move[source_move["id"]]
                missing_accounts = sorted({
                    line["account_id"]
                    for line in source_lines
                    if line["display_type"] == "product" and line["account_id"] not in accounts
                })
                missing_taxes = sorted({
                    source_tax_id
                    for line in source_lines
                    for source_tax_id in line["tax_ids"]
                    if source_tax_id not in taxes
                })
                blockers = []
                if not company:
                    blockers.append("company")
                if not journal:
                    blockers.append("journal")
                if not partner:
                    blockers.append("partner")
                if not source_currency:
                    blockers.append("currency")
                if not source_lines:
                    blockers.append("commercial_lines")
                if missing_accounts:
                    blockers.append("accounts")
                if missing_taxes:
                    blockers.append("taxes")
                if blockers:
                    blocked_cases.append({
                        "source_move_id": source_move["id"],
                        "source_name": source_move["name"],
                        "move_type": source_move["move_type"],
                        "classification": "insufficient_source_or_mapping_data",
                        "blockers": blockers,
                        "missing_source_account_ids": missing_accounts,
                        "missing_source_tax_ids": missing_taxes,
                    })
                    continue

                move = Move.search([
                    (
                        "rebuild_source_model",
                        "in",
                        [
                            "account.move.native_engine_replay",
                            "account.move.native_expense_replay",
                        ],
                    ),
                    ("rebuild_source_id", "=", source_move["id"]),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ], limit=1)
                if move:
                    reused_count += 1
                else:
                    line_commands = []
                    for source_line in source_lines:
                        line_vals = {
                            "sequence": source_line["sequence"] or 10,
                            "name": source_line["name"] or "/",
                            "display_type": source_line["display_type"],
                            **self._trace_values(
                                "account.move.line.native_engine_input",
                                source_line["id"],
                                options,
                            ),
                        }
                        if source_line["display_type"] == "product":
                            line_vals.update({
                                "account_id": accounts[source_line["account_id"]].id,
                                "quantity": self._amount(source_line["quantity"]),
                                "price_unit": self._amount(source_line["price_unit"]),
                                "discount": self._amount(source_line["discount"]),
                                "tax_ids": [Command.set([
                                    taxes[source_tax_id].id
                                    for source_tax_id in source_line["tax_ids"]
                                ])],
                                "analytic_distribution": self._native_replay_analytic_distribution(
                                    source_line["analytic_distribution"],
                                    analytic_accounts,
                                ),
                            })
                        line_commands.append(Command.create(line_vals))

                    move_vals = {
                        "move_type": source_move["move_type"],
                        "journal_id": journal.id,
                        "company_id": company.id,
                        "partner_id": partner.id,
                        "currency_id": source_currency.id,
                        "invoice_currency_rate": self._amount(source_move["invoice_currency_rate"]),
                        "date": source_move["date"],
                        "invoice_date": source_move["invoice_date"],
                        "ref": source_move["ref"] or source_move["name"],
                        "payment_reference": source_move["payment_reference"],
                        "fiscal_position_id": (
                            fiscal_positions[source_move["fiscal_position_id"]].id
                            if source_move["fiscal_position_id"] in fiscal_positions
                            else False
                        ),
                        "invoice_payment_term_id": (
                            payment_terms[source_move["invoice_payment_term_id"]].id
                            if source_move["invoice_payment_term_id"] in payment_terms
                            else False
                        ),
                        "invoice_line_ids": line_commands,
                        "rebuild_source_move_type": source_move["move_type"],
                        "rebuild_import_note": (
                            "Track B native-engine replay: commercial invoice lines were reconstructed "
                            "from source business fields and posted through normal Odoo action_post."
                        ),
                        **self._trace_values(
                            "account.move.native_engine_replay",
                            source_move["id"],
                            options,
                        ),
                    }
                    if not move_vals["invoice_payment_term_id"]:
                        move_vals["invoice_date_due"] = source_move["invoice_date_due"]
                    try:
                        with self.env.cr.savepoint():
                            move = Move.with_company(company).with_context(
                                allowed_company_ids=[company.id],
                                tracking_disable=True,
                                mail_create_nolog=True,
                            ).create(move_vals)
                            manual_tax_override = self._native_replay_apply_manual_tax_override(
                                move,
                                source_lines,
                                source_tax_totals_by_move[source_move["id"]],
                                taxes,
                            )
                            if manual_tax_override:
                                manual_tax_override_cases.append(manual_tax_override)
                            move.action_post()
                        created_count += 1
                    except Exception as exc:  # noqa: BLE001 - classify every native posting defect.
                        blocked_cases.append({
                            "source_move_id": source_move["id"],
                            "source_name": source_move["name"],
                            "move_type": source_move["move_type"],
                            "classification": "native_posting_error",
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                        })
                        continue

                source_account_totals = source_totals_by_move[source_move["id"]]
                target_account_totals = self._native_replay_target_account_totals(move)
                amount_checks = {
                    "amount_untaxed": {
                        "source": round(self._amount(source_move["amount_untaxed"]), 2),
                        "target": round(move.amount_untaxed, 2),
                    },
                    "amount_tax": {
                        "source": round(self._amount(source_move["amount_tax"]), 2),
                        "target": round(move.amount_tax, 2),
                    },
                    "amount_total": {
                        "source": round(self._amount(source_move["amount_total"]), 2),
                        "target": round(move.amount_total, 2),
                    },
                }
                amounts_match = all(
                    check["source"] == check["target"]
                    for check in amount_checks.values()
                )
                due_date_matches = move.invoice_date_due == source_move["invoice_date_due"]
                account_totals_match = source_account_totals == target_account_totals
                result = {
                    "source_move_id": source_move["id"],
                    "source_name": source_move["name"],
                    "target_move_id": move.id,
                    "target_name": move.name,
                    "move_type": move.move_type,
                    "currency": move.currency_id.name,
                    "invoice_date": str(move.invoice_date or ""),
                    "accounting_date": str(move.date or ""),
                    "due_date": str(move.invoice_date_due or ""),
                    "state": move.state,
                    "amount_checks": amount_checks,
                    "due_date_matches": due_date_matches,
                    "account_totals_match": account_totals_match,
                }
                if move.state == "posted" and amounts_match and due_date_matches and account_totals_match:
                    passed_cases.append(result)
                else:
                    mismatch_cases.append({
                        **result,
                        "source_account_totals": source_account_totals,
                        "target_account_totals": target_account_totals,
                    })

            for source_account_id in account_ids_to_archive_after_post:
                accounts[source_account_id].active = False
            for source_journal_id in journal_ids_to_archive_after_post:
                journals[source_journal_id].active = False

            document_attachment_stats = self._import_attachments(
                conn,
                {
                    **options,
                    "attachment_target_trace_models": {
                        "account.move": [
                            "account.move.native_engine_replay",
                            "account.move.native_expense_replay",
                        ],
                    },
                },
                companies,
                rows=self._native_replay_document_attachment_rows(conn, options),
            )
            document_attachment_issue_count = self._attachment_issue_count(
                document_attachment_stats,
            )
            status = (
                "passed"
                if (
                    not blocked_cases
                    and not mismatch_cases
                    and not document_attachment_issue_count
                )
                else "partial"
            )
            stats = {
                "classification": "NATIVE_VALIDATION_NATIVE_BUSINESS_DOCUMENT_REPLAY",
                "date_from": options["date_from"],
                "date_to": options["date_to"],
                "source_company_ids": options["source_company_ids"],
                "source_document_count": len(document_rows),
                "created_document_count": created_count,
                "reused_document_count": reused_count,
                "posted_document_count": len(passed_cases) + len(mismatch_cases),
                "passed_document_count": len(passed_cases),
                "mismatch_document_count": len(mismatch_cases),
                "blocked_document_count": len(blocked_cases),
                "manual_tax_override_count": len(manual_tax_override_cases),
                "move_type_counts": dict(sorted(type_counts.items())),
                "currency_counts": dict(sorted(currency_counts.items())),
                "passed_examples": passed_cases[:20],
                "mismatch_examples": mismatch_cases[:20],
                "blocked_examples": blocked_cases[:20],
                "manual_tax_override_examples": manual_tax_override_cases[:20],
                "currency_rates": currency_rate_stats,
                "tax_configuration": tax_stats,
                "payment_terms": payment_term_stats,
                "fiscal_positions": fiscal_position_stats,
                "reconciliation_models": reconciliation_model_stats,
                "partner_accounting_properties": partner_property_stats,
                "attachments": document_attachment_stats,
            }
            self.write({
                "status": status,
                "finished_at": fields.Datetime.now(),
                "company_ids": [Command.set([company.id for company in companies.values()])],
                "imported_company_count": len(companies),
                "imported_currency_rate_count": currency_rate_stats["imported_currency_rate_count"],
                "imported_account_count": len(accounts),
                "imported_journal_count": len(journals),
                "imported_partner_count": len(partners),
                "imported_move_count": len(passed_cases) + len(mismatch_cases),
                "warning_count": (
                    len(blocked_cases)
                    + len(mismatch_cases)
                    + document_attachment_issue_count
                ),
                "statistics_json": stats,
                "notes": (
                    "Dedicated Track B replay. Finalized source journal entries are not imported; "
                    "business-document lines are reconstructed as native drafts and posted through Odoo."
                ),
            })
            return stats
        except Exception:
            self.write({
                "status": "failed",
                "finished_at": fields.Datetime.now(),
            })
            raise
        finally:
            conn.close()

    def run_exact_ledger_replay_from_source(self, options):
        self.ensure_one()
        options = {
            "source_database": "odoo_online_source_saas_19_3",
            "source_snapshot_id": "source-unknown",
            "source_dump_sha256": "",
            "source_version": "Odoo Online Enterprise saas~19.3",
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
        exact_replay_started = time.perf_counter()
        conn = self._source_connection(options)
        stats = {}
        warnings = []
        performance = {
            "schema": "usl-accounting-import-performance-v1",
            "exact_replay_batch_size": self._EXACT_REPLAY_BATCH_SIZE,
            "relation_batch_size": self._RELATION_BATCH_SIZE,
            "stages": [],
        }

        def record_stage(name, started, **counts):
            performance["stages"].append({
                "name": name,
                "duration_seconds": round(time.perf_counter() - started, 3),
                **counts,
            })

        try:
            stage_started = time.perf_counter()
            currencies = self._currency_map(conn)
            countries = self._country_map(conn)
            companies, company_rows = self._company_map(conn, options, countries)
            currency_rate_stats = self._import_currency_rates(conn, options, companies, currencies)
            source_report_stats = self._import_source_reports(conn, options)
            source_report_structure_stats = self._import_source_report_structure(conn, options)
            source_report_stats["structure"] = source_report_structure_stats
            record_stage(
                "base configuration",
                stage_started,
                company_count=len(companies),
                currency_count=len(currencies),
            )
            stage_started = time.perf_counter()
            partners = self._partner_map(conn, options)
            record_stage(
                "partners",
                stage_started,
                partner_count=len(partners),
            )
            stage_started = time.perf_counter()
            accounts, account_ids_to_archive_after_post = self._account_map(conn, options, companies, currencies)
            tax_tags = self._tax_tag_map(conn, options, countries)
            tax_groups = self._tax_group_map(conn, options, companies, accounts, countries)
            taxes, tax_repartition_lines, tax_stats = self._tax_map(conn, options, companies, accounts, tax_groups, tax_tags, countries)
            cash_basis_companies = self._sync_company_cash_basis_flags(companies)
            tax_stats["company_cash_basis_setting_count"] = len(cash_basis_companies)
            payment_terms, payment_term_stats = self._payment_term_map(
                conn,
                options,
                companies,
            )
            fiscal_positions, fiscal_position_stats = self._fiscal_position_map(
                conn,
                options,
                companies,
                accounts,
                taxes,
                countries,
            )
            partner_property_stats = self._sync_partner_accounting_properties(
                conn,
                options,
                companies,
                partners,
                accounts,
                fiscal_positions,
            )
            journals, journal_ids_to_archive_after_post = self._journal_map(
                conn,
                options,
                companies,
                accounts,
                currencies,
            )
            method_lines = self._payment_method_line_map(
                conn,
                journals,
                accounts,
            )
            expense_configuration_stats = (
                self._native_expense_configure_companies(
                    conn,
                    options,
                    companies,
                    journals,
                    method_lines,
                    accounts,
                    self._native_expense_move_rows(conn, options),
                )
            )
            analytic_plans = self._analytic_plan_map(conn, options)
            analytic_accounts = self._analytic_account_map(conn, options, companies, partners, analytic_plans)
            products, _current_standard_prices = (
                self._native_expense_product_map(
                    conn,
                    options,
                    companies,
                    accounts,
                )
            )
            _reconciliation_models, reconciliation_model_stats = (
                self._reconciliation_model_map(
                    conn,
                    options,
                    companies,
                    accounts,
                    journals,
                    partners,
                    taxes,
                    analytic_accounts,
                )
            )
            record_stage(
                "accounting configuration",
                stage_started,
                account_count=len(accounts),
                analytic_account_count=len(analytic_accounts),
                journal_count=len(journals),
                product_count=len(products),
            )
            stage_started = time.perf_counter()
            move_rows = self._move_rows(conn, options)
            line_rows_by_move = self._line_rows_by_move(conn, options)
            record_stage(
                "source move queries",
                stage_started,
                move_count=len(move_rows),
                move_line_count=sum(
                    len(lines) for lines in line_rows_by_move.values()
                ),
            )
            Move = self.env["account.move"].with_context(
                check_move_validity=False,
                tracking_disable=True,
                mail_create_nolog=True,
                skip_account_move_synchronization=True,
                skip_invoice_sync=True,
            )
            imported_move_ids = []
            imported_line_count = 0
            imported_display_lines = [
                line
                for lines in line_rows_by_move.values()
                for line in lines
                if not line["account_id"]
            ]
            skipped_non_account_lines = []
            skipped_non_account_line_count = 0
            skipped_non_account_line_examples = [
                {
                    "source_move_id": line["move_id"],
                    "source_line_id": line["id"],
                    "display_type": line["display_type"],
                    "name": line["name"],
                }
                for line in imported_display_lines[:20]
            ]
            existing_move_map = self._source_trace_record_map(
                "account.move",
                [move_row["id"] for move_row in move_rows],
                options,
            )
            sequence_parameter_key = (
                "sequence.mixin.constraint_start_date"
            )
            sequence_parameters = self.env[
                "ir.config_parameter"
            ].sudo()
            previous_sequence_constraint = sequence_parameters.get_str(
                sequence_parameter_key,
                default=None,
            )
            sequence_parameters.set_str(
                sequence_parameter_key,
                options["date_to"],
            )
            reused_native_move_representations = []
            pending_moves = []
            move_materialization_started = time.perf_counter()
            move_create_seconds = 0.0
            move_post_seconds = 0.0
            move_cancel_seconds = 0.0

            def flush_pending_moves():
                nonlocal move_create_seconds
                nonlocal move_post_seconds
                nonlocal move_cancel_seconds
                nonlocal imported_line_count
                if not pending_moves:
                    return
                batch = list(pending_moves)
                pending_moves.clear()
                started = time.perf_counter()
                created = Move.create([
                    move_vals for _move_row, move_vals, _line_count in batch
                ])
                move_create_seconds += time.perf_counter() - started
                pairs = list(zip(batch, created, strict=True))
                posted = Move.browse([
                    move.id
                    for ((move_row, _vals, _count), move) in pairs
                    if move_row["state"] == "posted"
                ])
                cancelled = Move.browse([
                    move.id
                    for ((move_row, _vals, _count), move) in pairs
                    if move_row["state"] == "cancel"
                ])
                if posted:
                    started = time.perf_counter()
                    posted.action_post()
                    move_post_seconds += time.perf_counter() - started
                if cancelled:
                    started = time.perf_counter()
                    cancelled.button_cancel()
                    move_cancel_seconds += time.perf_counter() - started
                for (move_row, _move_vals, line_count), move in pairs:
                    if move.name != (move_row["name"] or "/"):
                        warnings.append(
                            "Move %s imported with name %s instead of %s."
                            % (move_row["id"], move.name, move_row["name"]),
                        )
                    imported_move_ids.append(move.id)
                    imported_line_count += line_count

            for move_row in move_rows:
                existing = existing_move_map.get(move_row["id"])
                if existing:
                    if existing.rebuild_source_model != "account.move":
                        alias_evidence = (
                            self._validate_exact_replay_move_alias(
                                existing,
                                move_row,
                                line_rows_by_move[move_row["id"]],
                                companies,
                                journals,
                                partners,
                                accounts,
                                currencies,
                                options,
                            )
                        )
                        alias_evidence.update(
                            self._normalize_exact_replay_move_alias_identity(
                                existing,
                                move_row,
                            ),
                        )
                        reused_native_move_representations.append(
                            alias_evidence,
                        )
                    imported_move_ids.append(existing.id)
                    imported_line_count += len(existing.line_ids)
                    continue
                line_commands = []
                for line in line_rows_by_move[move_row["id"]]:
                    line_vals = {
                        "sequence": line["sequence"] or 10,
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
                    if line["account_id"]:
                        line_vals["account_id"] = accounts[line["account_id"]].id
                    elif line["display_type"] not in {"line_section", "line_note"}:
                        line_vals["display_type"] = "line_note"
                    if options.get("preserve_business_documents"):
                        line_vals.update({
                            "quantity": self._amount(line["quantity"]),
                            "price_unit": self._amount(line["price_unit"]),
                            "discount": self._amount(line["discount"]),
                            "analytic_distribution": (
                                self._native_replay_analytic_distribution(
                                    line["analytic_distribution"],
                                    analytic_accounts,
                                )
                            ),
                        })
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
                    "move_type": (
                        move_row["move_type"]
                        if options.get("preserve_business_documents")
                        else "entry"
                    ),
                    "rebuild_source_move_type": move_row["move_type"],
                    "partner_id": partners[move_row["partner_id"]].id if move_row["partner_id"] in partners else False,
                    "payment_reference": move_row["payment_reference"],
                    "line_ids": line_commands,
                    **self._trace_values("account.move", move_row["id"], options),
                }
                if options.get("preserve_business_documents"):
                    move_vals.update({
                        "invoice_date": move_row["invoice_date"],
                        "invoice_date_due": move_row["invoice_date_due"],
                        "fiscal_position_id": (
                            fiscal_positions[move_row["fiscal_position_id"]].id
                            if move_row["fiscal_position_id"] in fiscal_positions
                            else False
                        ),
                        "invoice_payment_term_id": (
                            payment_terms[move_row["invoice_payment_term_id"]].id
                            if move_row["invoice_payment_term_id"] in payment_terms
                            else False
                        ),
                    })
                if move_row["currency_id"] in currencies:
                    move_vals["currency_id"] = currencies[move_row["currency_id"]].id
                pending_moves.append((move_row, move_vals, len(line_commands)))
                if len(pending_moves) >= self._EXACT_REPLAY_BATCH_SIZE:
                    flush_pending_moves()
            flush_pending_moves()
            imported_moves = Move.browse(imported_move_ids)
            move_materialization_seconds = (
                time.perf_counter() - move_materialization_started
            )
            performance["stages"].extend((
                {
                    "name": "moves",
                    "duration_seconds": round(move_create_seconds, 3),
                    "move_count": len(imported_move_ids),
                    "move_line_count": imported_line_count,
                },
                {
                    "name": "posting",
                    "duration_seconds": round(
                        move_post_seconds + move_cancel_seconds,
                        3,
                    ),
                    "posted_move_count": sum(
                        row["state"] == "posted" for row in move_rows
                    ),
                    "cancelled_move_count": sum(
                        row["state"] == "cancel" for row in move_rows
                    ),
                },
                {
                    "name": "move materialization total",
                    "duration_seconds": round(
                        move_materialization_seconds,
                        3,
                    ),
                    "cancel_seconds": round(move_cancel_seconds, 3),
                    "create_seconds": round(move_create_seconds, 3),
                    "post_seconds": round(move_post_seconds, 3),
                },
            ))

            stage_started = time.perf_counter()
            imported_move_map = self._source_trace_record_map(
                "account.move",
                [move_row["id"] for move_row in move_rows],
                options,
            )
            sequence_chronology_stats = self._sequence_chronology_stats(
                [row for row in move_rows if row["state"] == "posted"],
                imported_move_map,
            )
            record_stage(
                "move chronology validation",
                stage_started,
                move_count=len(imported_move_map),
            )
            if not sequence_chronology_stats["target_matches_source"]:
                raise ValueError(
                    "Imported move sequence and chronology identity differs "
                    "from the source: %s"
                    % json.dumps(
                        sequence_chronology_stats[
                            "identity_mismatch_examples"
                        ],
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                )
            sequence_discrepancy_name = (
                "Historical journal sequence and chronology exceptions "
                "require review"
            )
            source_sequence_profile = sequence_chronology_stats["source"]
            source_sequence_exception_count = (
                source_sequence_profile["missing_name_count"]
                + source_sequence_profile["duplicate_name_group_count"]
                + source_sequence_profile[
                    "duplicate_sequence_number_group_count"
                ]
                + source_sequence_profile["sequence_gap_count"]
                + source_sequence_profile[
                    "sequence_date_decrease_count"
                ]
            )
            if source_sequence_exception_count:
                self._upsert_discrepancy({
                    "name": sequence_discrepancy_name,
                    "severity": "P2",
                    "classification": "source_anomaly",
                    "status": "investigating",
                    "period_key": (
                        f"{options['date_from']}:{options['date_to']}"
                    ),
                    "source_model": "account.move",
                    "target_model": "account.move",
                    "source_value": (
                        f"{source_sequence_profile['sequence_gap_count']} "
                        "sequence gaps; "
                        f"{source_sequence_profile['sequence_date_decrease_count']} "
                        "date-order decreases"
                    ),
                    "target_value": (
                        "Historical names, dates, sequence prefixes and "
                        "numbers preserved exactly"
                    ),
                    "difference": (
                        "No target-only sequence or chronology exception"
                    ),
                    "accounting_impact": (
                        "The source contains visible historical numbering "
                        "exceptions. The target preserves them exactly and "
                        "does not silently resequence posted history."
                    ),
                    "legal_or_tax_impact": (
                        "An accountant should review the source gaps and "
                        "date-order exceptions before accepting the audit "
                        "trail; technical parity does not explain their "
                        "business cause."
                    ),
                    "evidence": json.dumps(
                        sequence_chronology_stats,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                    "likely_cause": (
                        "The exceptions already exist in the restored source "
                        "ledger and may reflect deleted drafts, import order "
                        "or bank-entry sequencing."
                    ),
                    "recommendation": (
                        "Review the listed source entries and document the "
                        "cause. Do not use Odoo resequencing on imported "
                        "posted history without accountant approval."
                    ),
                    "owner": "accountant",
                })
            else:
                self.env["rebuild.account.discrepancy"].search([
                    ("name", "=", sequence_discrepancy_name),
                    ("status", "in", ["open", "investigating"]),
                ]).write({
                    "status": "resolved",
                    "decision": (
                        "No source or target sequence/chronology exception "
                        "exists in the imported period."
                    ),
                })

            for source_account_id in account_ids_to_archive_after_post:
                accounts[source_account_id].active = False
            for source_journal_id in journal_ids_to_archive_after_post:
                journals[source_journal_id].active = False

            non_posted_moves = [
                row for row in move_rows if row["state"] != "posted"
            ]
            non_posted_lines = [
                line
                for row in non_posted_moves
                for line in line_rows_by_move[row["id"]]
            ]
            posted_display_lines = [
                line
                for row in move_rows
                if row["state"] == "posted"
                for line in line_rows_by_move[row["id"]]
                if not line["account_id"]
            ]
            native_document_stats = {
                "source_non_posted_move_count": len(non_posted_moves),
                "imported_non_posted_move_count": len(non_posted_moves),
                "native_non_posted_move_count": len(non_posted_moves),
            }
            native_context_line_stats = {
                "source_context_line_count": len(non_posted_lines)
                + len(posted_display_lines),
                "imported_context_line_count": len(non_posted_lines)
                + len(posted_display_lines),
                "source_posted_non_account_line_count": len(
                    posted_display_lines
                ),
                "source_non_posted_line_count": len(non_posted_lines),
                "source_non_posted_accounting_line_count": len(
                    [line for line in non_posted_lines if line["account_id"]]
                ),
                "missing_imported_move_count": 0,
                "native_source_line_count": len(non_posted_lines)
                + len(posted_display_lines),
            }
            stage_started = time.perf_counter()
            reconciliation_stats = self._import_reconciliations(conn, options, companies)
            record_stage(
                "reconciliations",
                stage_started,
                full_count=reconciliation_stats[
                    "imported_full_reconcile_count"
                ],
                partial_count=reconciliation_stats[
                    "imported_partial_reconcile_count"
                ],
            )
            stage_started = time.perf_counter()
            payment_stats = self._import_payments(conn, options, companies, partners, accounts, journals, currencies)
            record_stage(
                "payments",
                stage_started,
                payment_count=payment_stats["imported_payment_count"],
            )
            stage_started = time.perf_counter()
            deferred_schedule_stats = self._import_deferred_schedules(
                conn, options, companies, partners, journals, currencies
            )
            record_stage(
                "deferred schedules",
                stage_started,
                schedule_line_count=deferred_schedule_stats[
                    "imported_deferred_schedule_line_count"
                ],
            )
            stage_started = time.perf_counter()
            bank_statement_line_stats = self._import_bank_statement_lines(
                conn, options, companies, partners, journals, currencies
            )
            record_stage(
                "bank statement lines",
                stage_started,
                line_count=bank_statement_line_stats[
                    "imported_bank_statement_line_count"
                ],
            )
            stage_started = time.perf_counter()
            asset_stats = self._import_assets(conn, options, companies, accounts, journals, currencies)
            record_stage(
                "assets",
                stage_started,
                asset_count=asset_stats["imported_asset_count"],
            )
            stage_started = time.perf_counter()
            analytic_stats = self._import_analytic_lines(
                conn,
                options,
                companies,
                partners,
                accounts,
                analytic_plans,
                analytic_accounts,
                products,
            )
            record_stage(
                "analytics",
                stage_started,
                line_count=analytic_stats["imported_analytic_line_count"],
            )
            stage_started = time.perf_counter()
            attachment_stats = self._import_attachments(conn, options, companies)
            record_stage(
                "attachments",
                stage_started,
                attachment_count=attachment_stats[
                    "imported_attachment_count"
                ],
                bytes=attachment_stats["source_total_bytes"],
            )
            external_report_values = self._seed_benchmark_external_report_values(companies)

            stage_started = time.perf_counter()
            for row in company_rows:
                company = companies[row["id"]]
                lock_vals = {}
                for field_name in ("fiscalyear_lock_date", "tax_lock_date", "sale_lock_date", "purchase_lock_date", "hard_lock_date"):
                    if row[field_name]:
                        lock_vals[field_name] = row[field_name]
                if lock_vals:
                    company.write(lock_vals)

            company_configuration_parity = (
                self._company_configuration_parity(
                    conn,
                    options,
                    companies,
                    method_lines,
                )
            )
            if company_configuration_parity["mismatch_count"]:
                raise ValueError(
                    "Per-company operational Accounting configuration "
                    "differs from the source or is incomplete: %s"
                    % json.dumps(
                        company_configuration_parity,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                )

            for company in companies.values():
                if company.rebuild_declaration_profile_active:
                    declarations = self.env["rebuild.account.declaration"].sync_for_company(company)
                    if company.rebuild_source_id == 1:
                        declarations.action_refresh_preparation()
                    self.env["rebuild.account.closing.period"].sync_for_company(company)

            if native_document_stats["source_non_posted_move_count"] != native_document_stats["imported_non_posted_move_count"]:
                raise ValueError("Not every non-posted source move was materialized as a native account.move.")
            if native_context_line_stats["source_context_line_count"] != native_context_line_stats["imported_context_line_count"]:
                raise ValueError("Not every source document line was materialized as a native account.move.line.")
            if reconciliation_stats["scope_summary"].get("partials_cross_boundary") or reconciliation_stats["scope_summary"].get("fulls_cross_boundary"):
                raise ValueError("The native reconciliation graph still has source endpoints outside the imported company scope.")

            if deferred_schedule_stats["source_not_replayed_count"]:
                self._upsert_discrepancy({
                    "name": "Posted source deferred schedule entries are not fully represented",
                    "severity": "P1",
                    "classification": "import_defect",
                    "status": "open",
                    "period_key": f"{options['date_from']}:open",
                    "source_value": str(deferred_schedule_stats["source_deferred_schedule_line_count"]),
                    "target_value": str(deferred_schedule_stats["imported_deferred_schedule_line_count"]),
                    "evidence": json.dumps(deferred_schedule_stats, ensure_ascii=False, sort_keys=True),
                    "accounting_impact": (
                        "At least one posted source deferred schedule entry is not linked to an imported "
                        "target journal entry."
                    ),
                    "recommendation": "Expand the replay scope or repair the deferred schedule mapping before declaring deferred report parity.",
                })

            vat_deductible_line = self.env["rebuild.account.french.tax.package.line"].search([
                ("source_company_id", "=", 1),
                ("period_key", "=", "Fiscal year 2024-01-10 to 2025-09-30"),
                ("field_code", "=", "3517S_TVA_DEDUCTIBLE_BIENS_SERVICES_445660"),
            ], limit=1)
            vat_external_value = self.env["rebuild.account.external.report.value"].search([
                ("company_id", "=", vat_deductible_line.company_id.id if vat_deductible_line else False),
                ("period_key", "=", "Fiscal year 2024-01-10 to 2025-09-30"),
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

            cca_configuration_stats = (
                self.env["res.company"]._rebuild_apply_cca_projection_defaults()
            )
            record_stage(
                "final accounting validation",
                stage_started,
                company_count=len(companies),
                discrepancy_count=len(self.discrepancy_ids),
            )
            performance["duration_seconds"] = round(
                time.perf_counter() - exact_replay_started,
                3,
            )
            performance["status"] = "passed"
            stats = {
                "date_from": options["date_from"],
                "date_to": options["date_to"],
                "source_company_ids": options["source_company_ids"],
                "source_move_count": len(move_rows),
                "source_move_line_count": sum(len(lines) for lines in line_rows_by_move.values()),
                "imported_move_count": len(imported_moves),
                "imported_move_line_count": imported_line_count,
                "reused_native_move_representation_count": len(
                    reused_native_move_representations,
                ),
                "reused_native_move_representations": (
                    reused_native_move_representations
                ),
                "sequence_chronology": sequence_chronology_stats,
                "skipped_non_account_line_count": skipped_non_account_line_count,
                "skipped_non_account_line_examples": skipped_non_account_line_examples,
                "account_count": len(accounts),
                "journal_count": len(journals),
                "partner_count": len(partners),
                "company_count": len(companies),
                "company_configuration": company_configuration_parity,
                "expense_configuration": expense_configuration_stats,
                "currency_rates": currency_rate_stats,
                "tax_configuration": tax_stats,
                "payment_terms": payment_term_stats,
                "fiscal_positions": fiscal_position_stats,
                "reconciliation_models": reconciliation_model_stats,
                "partner_accounting_properties": partner_property_stats,
                "cca_configuration": cca_configuration_stats,
                "reconciliations": reconciliation_stats,
                "payments": payment_stats,
                "native_documents": native_document_stats,
                "native_context_lines": native_context_line_stats,
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
                "performance": performance,
            }
            if previous_sequence_constraint is None:
                sequence_parameters.search([
                    ("key", "=", sequence_parameter_key),
                ]).unlink()
            else:
                sequence_parameters.set_str(
                    sequence_parameter_key,
                    previous_sequence_constraint,
                )

            self.write({
                "status": "passed",
                "finished_at": fields.Datetime.now(),
                "company_ids": [Command.set([company.id for company in companies.values()])],
                "imported_company_count": len(companies),
                "imported_currency_rate_count": currency_rate_stats["imported_currency_rate_count"],
                "imported_account_count": len(accounts),
                "imported_journal_count": len(journals),
                "imported_partner_count": len(partners),
                "imported_move_count": len(imported_moves),
                "imported_move_line_count": imported_line_count,
                "imported_non_posted_move_count": native_document_stats["imported_non_posted_move_count"],
                "imported_context_line_count": native_context_line_stats["imported_context_line_count"],
                "imported_payment_count": payment_stats["imported_payment_count"],
                "imported_no_entry_payment_count": payment_stats["native_no_entry_payment_count"],
                "imported_bank_statement_line_count": bank_statement_line_stats["imported_bank_statement_line_count"],
                "imported_analytic_line_count": analytic_stats["imported_analytic_line_count"],
                "imported_attachment_count": attachment_stats["imported_attachment_count"],
                "imported_reconciliation_count": (
                    reconciliation_stats["imported_partial_reconcile_count"]
                    + reconciliation_stats["imported_full_reconcile_count"]
                ),
                "imported_source_report_count": source_report_stats["imported_source_report_count"],
                "imported_deferred_schedule_line_count": deferred_schedule_stats["imported_deferred_schedule_line_count"],
                "external_report_value_count": len(external_report_values),
                "warning_count": len(warnings),
                "discrepancy_count": len(self.discrepancy_ids),
                "statistics_json": stats,
                "notes": "Complete source accounting replay for the selected companies. Posted, draft and cancelled entries, every native payment, the complete reconciliation graph, bank statement lines, tax and report configuration, deferred schedules, analytics, assets and scoped accounting attachments are materialized as native product records. No migration review placeholder represents source accounting truth.",
            })
            return stats
        except Exception:
            performance["duration_seconds"] = round(
                time.perf_counter() - exact_replay_started,
                3,
            )
            performance["status"] = "failed"
            stats.setdefault("performance", performance)
            self.write({
                "status": "failed",
                "finished_at": fields.Datetime.now(),
                "statistics_json": stats,
            })
            raise
        finally:
            conn.close()

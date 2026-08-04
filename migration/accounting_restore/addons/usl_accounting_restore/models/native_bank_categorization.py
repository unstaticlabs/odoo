from odoo import fields, models


class RebuildAccountImportRun(models.Model):
    _inherit = "rebuild.account.import.run"

    def _native_bank_categorization_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            WITH current_statement_lines AS (
                SELECT statement_line.*
                FROM account_bank_statement_line statement_line
                JOIN account_move statement_move
                  ON statement_move.id = statement_line.move_id
                WHERE statement_line.company_id = ANY(%(source_company_ids)s)
                  AND statement_move.date BETWEEN %(date_from)s AND %(date_to)s
            ),
            external_partials AS (
                SELECT DISTINCT statement_line.id AS statement_line_id,
                       partial.id AS partial_id
                FROM current_statement_lines statement_line
                JOIN account_move_line statement_move_line
                  ON statement_move_line.move_id = statement_line.move_id
                JOIN account_partial_reconcile partial
                  ON partial.debit_move_id = statement_move_line.id
                  OR partial.credit_move_id = statement_move_line.id
                JOIN account_move_line other_line ON other_line.id = CASE
                    WHEN partial.debit_move_id = statement_move_line.id
                    THEN partial.credit_move_id
                    ELSE partial.debit_move_id
                END
                WHERE other_line.move_id != statement_line.move_id
            ),
            selected AS (
                SELECT statement_line.*
                FROM current_statement_lines statement_line
                LEFT JOIN external_partials external
                  ON external.statement_line_id = statement_line.id
                GROUP BY statement_line.id, statement_line.move_id,
                         statement_line.journal_id, statement_line.company_id,
                         statement_line.statement_id, statement_line.sequence,
                         statement_line.partner_id, statement_line.currency_id,
                         statement_line.foreign_currency_id,
                         statement_line.create_uid, statement_line.write_uid,
                         statement_line.account_number,
                         statement_line.partner_name,
                         statement_line.transaction_type,
                         statement_line.payment_ref,
                         statement_line.internal_index,
                         statement_line.transaction_details,
                         statement_line.amount,
                         statement_line.amount_currency,
                         statement_line.is_reconciled,
                         statement_line.create_date,
                         statement_line.write_date,
                         statement_line.amount_residual,
                         statement_line.message_main_attachment_id,
                         statement_line.cron_last_check,
                         statement_line.unique_import_id,
                         statement_line.online_account_id,
                         statement_line.online_link_id,
                         statement_line.online_transaction_identifier,
                         statement_line.transaction_uuid
                HAVING count(external.partial_id) = 0
            ),
            counterpart_lines AS (
                SELECT selected.id AS statement_line_id,
                       line.id, line.account_id, line.partner_id,
                       line.currency_id, line.name, line.balance,
                       line.amount_currency, line.analytic_distribution,
                       count(*) OVER (PARTITION BY selected.id)
                           AS counterpart_line_count
                FROM selected
                JOIN account_journal journal ON journal.id = selected.journal_id
                JOIN account_move_line line ON line.move_id = selected.move_id
                WHERE line.account_id IS DISTINCT FROM journal.default_account_id
            )
            SELECT selected.id, selected.move_id, selected.journal_id,
                   selected.company_id, selected.sequence, selected.partner_id,
                   selected.foreign_currency_id, selected.account_number,
                   selected.partner_name, selected.transaction_type,
                   selected.payment_ref, selected.transaction_details,
                   selected.amount, selected.amount_currency,
                   selected.is_reconciled, selected.unique_import_id,
                   statement_move.date, statement_move.name AS move_name,
                   liquidity.balance AS liquidity_balance,
                   liquidity.amount_currency AS liquidity_amount_currency,
                   liquidity.currency_id AS liquidity_currency_id,
                   counterpart.id AS counterpart_line_id,
                   counterpart.account_id AS counterpart_account_id,
                   counterpart.partner_id AS counterpart_partner_id,
                   counterpart.currency_id AS counterpart_currency_id,
                   counterpart.name AS counterpart_name,
                   counterpart.balance AS counterpart_balance,
                   counterpart.amount_currency AS counterpart_amount_currency,
                   counterpart.analytic_distribution,
                   counterpart.counterpart_line_count
            FROM selected
            JOIN account_move statement_move ON statement_move.id = selected.move_id
            JOIN account_journal statement_journal
              ON statement_journal.id = selected.journal_id
            JOIN LATERAL (
                SELECT line.balance, line.amount_currency, line.currency_id
                FROM account_move_line line
                WHERE line.move_id = selected.move_id
                  AND line.account_id = statement_journal.default_account_id
                ORDER BY line.id
                LIMIT 1
            ) liquidity ON true
            LEFT JOIN counterpart_lines counterpart
              ON counterpart.statement_line_id = selected.id
            ORDER BY statement_move.date, selected.id
            """,
            options,
        )

    @staticmethod
    def _native_bank_categorization_source_analytic_ids(distribution):
        source_ids = set()
        for source_key in (distribution or {}):
            for source_id_text in str(source_key).split(","):
                try:
                    source_ids.add(int(source_id_text))
                except (TypeError, ValueError):
                    continue
        return source_ids

    def _native_bank_categorization_maps(self, conn, options, rows):
        currencies = self._currency_map(conn)
        partners = self._partner_map(conn, options)
        journals = {
            journal.rebuild_source_id: journal
            for journal in self.env["account.journal"].sudo().search([
                ("rebuild_source_model", "=", "account.journal"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                (
                    "rebuild_source_id",
                    "in",
                    sorted({row["journal_id"] for row in rows}) or [0],
                ),
            ])
        }
        accounts = {
            account.rebuild_source_id: account
            for account in self.env["account.account"].sudo().search([
                ("rebuild_source_model", "=", "account.account"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                (
                    "rebuild_source_id",
                    "in",
                    sorted({
                        row["counterpart_account_id"]
                        for row in rows
                        if row["is_reconciled"]
                    })
                    or [0],
                ),
            ])
        }
        analytic_accounts = {
            account.rebuild_source_id: account
            for account in self.env["account.analytic.account"].sudo().search([
                ("rebuild_source_model", "=", "account.analytic.account"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ])
        }
        return journals, accounts, partners, currencies, analytic_accounts

    def _native_bank_categorization_apply(
        self,
        bank_line,
        row,
        account,
        partner,
        currency,
        analytic_distribution,
    ):
        bank_line.reconcile_data_info = bank_line._default_reconcile_data()
        suspense_rows = [
            line
            for line in bank_line.reconcile_data_info.get("data", [])
            if line["kind"] == "suspense"
        ]
        if len(suspense_rows) != 1:
            message = (
                "Expected one OCA suspense candidate for direct bank "
                f"categorization, got {len(suspense_rows)}"
            )
            raise ValueError(message)
        bank_line.manual_reference = suspense_rows[0]["reference"]
        bank_line._onchange_manual_reconcile_reference()
        bank_line.manual_account_id = account
        bank_line.manual_partner_id = partner
        bank_line.manual_name = row["counterpart_name"] or row["payment_ref"] or "/"
        bank_line.manual_amount = self._amount(row["counterpart_balance"])
        bank_line.analytic_distribution = analytic_distribution
        bank_line._onchange_manual_reconcile_vals()

        data = bank_line.reconcile_data_info.get("data", [])
        manual_rows = [
            line for line in data if line["reference"] == bank_line.manual_reference
        ]
        if len(manual_rows) != 1:
            message = (
                "Expected one OCA manual allocation candidate for source bank "
                f"line {row['id']}, got {len(manual_rows)}"
            )
            raise ValueError(message)
        manual_rows[0].update({
            "kind": "other",
            "line_currency_id": currency.id,
            "currency_amount": self._amount(row["counterpart_amount_currency"]),
        })
        bank_line.reconcile_data_info = bank_line._recompute_suspense_line(
            data,
            bank_line.reconcile_data_info["reconcile_auxiliary_id"],
            bank_line.manual_reference,
        )
        if not bank_line.reconcile_data_info.get("can_reconcile"):
            message = (
                "OCA direct bank categorization does not balance for source bank "
                f"line {row['id']}"
            )
            raise ValueError(message)
        bank_line.reconcile_bank_line()

    def _native_bank_categorization_counterpart(self, bank_line):
        return bank_line.line_ids.filtered(
            lambda line: line.account_id != bank_line.journal_id.default_account_id,
        )

    def _native_bank_categorization_validate(
        self,
        bank_line,
        row,
        account,
        partner,
        counterpart_currency,
        effective_foreign_currency,
        effective_amount_currency,
        analytic_distribution,
        options,
    ):
        liquidity_lines = bank_line.line_ids.filtered(
            lambda line: line.account_id == bank_line.journal_id.default_account_id,
        )
        counterpart_lines = self._native_bank_categorization_counterpart(bank_line)
        checks = {
            "source_trace": (
                bank_line.rebuild_source_model
                == "account.bank.statement.line.native_bank_categorization"
                and bank_line.rebuild_source_id == row["id"]
                and bank_line.rebuild_source_snapshot
                == options["source_snapshot_id"]
            ),
            "date": bank_line.date == row["date"],
            "journal": bank_line.journal_id.rebuild_source_id == row["journal_id"],
            # Source bank imports can retain sub-cent precision (for example
            # 1.795). The SaaS Monetary field normalizes the displayed amount
            # to the journal currency precision on create.
            "amount": bank_line.currency_id.compare_amounts(
                bank_line.amount,
                self._amount(row["amount"]),
            )
            == 0,
            "amount_currency": round(bank_line.amount_currency, 2)
            == round(effective_amount_currency, 2),
            "foreign_currency": bank_line.foreign_currency_id
            == (effective_foreign_currency or self.env["res.currency"]),
            "reconciled_state": bank_line.is_reconciled
            == bool(row["is_reconciled"]),
            "move_balanced": round(sum(bank_line.line_ids.mapped("balance")), 2)
            == 0.0,
            "liquidity_line_count": len(liquidity_lines) == 1,
            "liquidity_balance": len(liquidity_lines) == 1
            and round(liquidity_lines.balance, 2)
            == round(self._amount(row["liquidity_balance"]), 2),
            "liquidity_amount_currency": len(liquidity_lines) == 1
            and round(liquidity_lines.amount_currency, 2)
            == round(self._amount(row["liquidity_amount_currency"]), 2),
        }
        target_line = self.env["account.move.line"]
        if row["is_reconciled"]:
            target_line = counterpart_lines.filtered(
                lambda line: (
                    line.rebuild_source_model
                    == "account.move.line.native_bank_categorization"
                    and line.rebuild_source_id == row["counterpart_line_id"]
                ),
            )[:1]
            if not target_line and len(counterpart_lines) == 1:
                target_line = counterpart_lines
                target_line.write({
                    "rebuild_import_note": (
                        "Track B direct bank allocation created through OCA Bank "
                        "Matching from the source operator categorization."
                    ),
                    **self._trace_values(
                        "account.move.line.native_bank_categorization",
                        row["counterpart_line_id"],
                        options,
                    ),
                })
            checks.update({
                "counterpart_line_count": len(counterpart_lines) == 1,
                "counterpart_source_trace": bool(target_line),
                "counterpart_account": bool(target_line)
                and target_line.account_id == account,
                "counterpart_partner": bool(target_line)
                and (target_line.partner_id.rebuild_source_id or None)
                == (row["counterpart_partner_id"] or None),
                "counterpart_balance": bool(target_line)
                and round(target_line.balance, 2)
                == round(self._amount(row["counterpart_balance"]), 2),
                "counterpart_amount_currency": bool(target_line)
                and round(target_line.amount_currency, 2)
                == round(self._amount(row["counterpart_amount_currency"]), 2),
                "counterpart_currency": bool(target_line)
                and target_line.currency_id == counterpart_currency,
                "analytic_distribution": bool(target_line)
                and (target_line.analytic_distribution or {})
                == (analytic_distribution or {}),
            })
        return checks, target_line

    def run_native_bank_categorization_from_source(self, options):
        """Replay direct bank categorization and retain source-open transactions."""
        self.ensure_one()
        options = {
            "source_database": "odoo_online_source_saas_19_2",
            "source_snapshot_id": "source-unknown",
            "source_dump_sha256": "",
            "source_version": "Odoo Online Enterprise saas~19.2",
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
            rows = self._native_bank_categorization_rows(conn, options)
            (
                journals,
                accounts,
                partners,
                currencies,
                analytic_accounts,
            ) = self._native_bank_categorization_maps(conn, options, rows)
            for journal in journals.values():
                if (
                    "reconcile_mode" in journal._fields
                    and journal.reconcile_mode != "edit"
                ):
                    journal.reconcile_mode = "edit"

            StatementLine = self.env["account.bank.statement.line"].sudo().with_context(
                tracking_disable=True,
                mail_create_nolog=True,
                rebuild_skip_auto_reconcile=True,
            )
            created_bank_line_count = 0
            reused_bank_line_count = 0
            created_categorization_count = 0
            reused_categorization_count = 0
            auto_reconcile_undo_count = 0
            blocked = []
            mismatches = []
            historical_countervalue_count = 0

            for row in rows:
                journal = journals.get(row["journal_id"])
                account = accounts.get(row["counterpart_account_id"])
                partner = partners.get(row["partner_id"])
                counterpart_partner = partners.get(row["counterpart_partner_id"])
                foreign_currency = currencies.get(row["foreign_currency_id"])
                counterpart_currency = currencies.get(
                    row["counterpart_currency_id"],
                )
                source_analytic_ids = (
                    self._native_bank_categorization_source_analytic_ids(
                        row["analytic_distribution"],
                    )
                )
                missing_analytic_ids = sorted(
                    source_analytic_ids - analytic_accounts.keys(),
                )
                analytic_distribution = self._native_replay_analytic_distribution(
                    row["analytic_distribution"],
                    analytic_accounts,
                )
                if (
                    not journal
                    or row["counterpart_line_count"] != 1
                    or (
                        row["is_reconciled"]
                        and (not account or not counterpart_currency)
                    )
                    or missing_analytic_ids
                ):
                    blocked.append({
                        "source_bank_statement_line_id": row["id"],
                        "classification": "missing_direct_bank_categorization_mapping",
                        "missing_journal": not bool(journal),
                        "missing_account": row["is_reconciled"]
                        and not bool(account),
                        "missing_currency": row["is_reconciled"]
                        and not bool(counterpart_currency),
                        "missing_analytic_ids": missing_analytic_ids,
                        "counterpart_line_count": row["counterpart_line_count"],
                    })
                    continue

                historical_countervalue = bool(
                    not foreign_currency
                    and journal.currency_id
                    and journal.currency_id != journal.company_id.currency_id,
                )
                historical_countervalue_count += int(historical_countervalue)
                effective_foreign_currency = (
                    journal.company_id.currency_id
                    if historical_countervalue
                    else foreign_currency
                )
                effective_amount_currency = (
                    self._amount(row["liquidity_balance"])
                    if historical_countervalue
                    else self._amount(row["amount_currency"])
                )
                bank_line = StatementLine.search([
                    (
                        "rebuild_source_model",
                        "=",
                        "account.bank.statement.line.native_bank_categorization",
                    ),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                    ("rebuild_source_id", "=", row["id"]),
                ], limit=1)
                if bank_line:
                    reused_bank_line_count += 1
                created_bank_line = False
                created_categorization = False
                auto_reconcile_undo = 0
                try:
                    with self.env.cr.savepoint():
                        if not bank_line:
                            bank_line = StatementLine.with_company(
                                journal.company_id,
                            ).create({
                                "journal_id": journal.id,
                                "date": row["date"],
                                "sequence": row["sequence"],
                                "partner_id": partner.id if partner else False,
                                "foreign_currency_id": (
                                    effective_foreign_currency.id
                                    if effective_foreign_currency
                                    else False
                                ),
                                "account_number": row["account_number"],
                                "partner_name": row["partner_name"],
                                "transaction_type": row["transaction_type"],
                                "payment_ref": row["payment_ref"],
                                "transaction_details": row["transaction_details"],
                                "amount": self._amount(row["amount"]),
                                "amount_currency": effective_amount_currency,
                                "unique_import_id": row["unique_import_id"],
                                "rebuild_import_note": (
                                    "Track B native direct-categorization or source-open "
                                    "bank transaction."
                                ),
                                **self._trace_values(
                                    "account.bank.statement.line.native_bank_categorization",
                                    row["id"],
                                    options,
                                ),
                            })
                            created_bank_line = True
                            if bank_line.is_reconciled:
                                bank_line.unreconcile_bank_line()
                                auto_reconcile_undo += 1
                        if row["is_reconciled"] and not bank_line.is_reconciled:
                            self._native_bank_categorization_apply(
                                bank_line,
                                row,
                                account,
                                counterpart_partner,
                                counterpart_currency,
                                analytic_distribution,
                            )
                            created_categorization = True
                        elif row["is_reconciled"]:
                            reused_categorization_count += 1
                        elif bank_line.is_reconciled:
                            bank_line.unreconcile_bank_line()
                            auto_reconcile_undo += 1
                except Exception as exc:  # noqa: BLE001 - classify source line.
                    blocked.append({
                        "source_bank_statement_line_id": row["id"],
                        "classification": "native_direct_bank_categorization_error",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    })
                    continue
                created_bank_line_count += int(created_bank_line)
                created_categorization_count += int(created_categorization)
                auto_reconcile_undo_count += auto_reconcile_undo

                checks, target_line = self._native_bank_categorization_validate(
                    bank_line,
                    row,
                    account,
                    counterpart_partner,
                    counterpart_currency,
                    effective_foreign_currency,
                    effective_amount_currency,
                    analytic_distribution,
                    options,
                )
                if not all(checks.values()):
                    mismatches.append({
                        "source_bank_statement_line_id": row["id"],
                        "target_bank_statement_line_id": bank_line.id,
                        "target_counterpart_line_id": (
                            target_line.id if target_line else False
                        ),
                        "checks": checks,
                    })

            status = "passed" if not blocked and not mismatches else "partial"
            stats = {
                "classification": "NATIVE_VALIDATION_NATIVE_DIRECT_BANK_CATEGORIZATION",
                "date_from": options["date_from"],
                "date_to": options["date_to"],
                "source_company_ids": options["source_company_ids"],
                "source_zero_external_partial_bank_line_count": len(rows),
                "source_categorized_bank_line_count": sum(
                    bool(row["is_reconciled"]) for row in rows
                ),
                "source_open_bank_line_count": sum(
                    not row["is_reconciled"] for row in rows
                ),
                "source_amount_precision_normalization_count": sum(
                    round(self._amount(row["amount"]), 6)
                    != round(
                        (
                            journal.currency_id
                            or journal.company_id.currency_id
                        ).round(
                            self._amount(row["amount"]),
                        ),
                        6,
                    )
                    for row in rows
                    if (
                        journal := journals.get(row["journal_id"])
                    )
                ),
                "source_analytic_categorization_count": sum(
                    bool(row["analytic_distribution"]) for row in rows
                ),
                "created_bank_statement_line_count": created_bank_line_count,
                "reused_bank_statement_line_count": reused_bank_line_count,
                "created_categorization_count": created_categorization_count,
                "reused_categorization_count": reused_categorization_count,
                "auto_reconcile_undo_count": auto_reconcile_undo_count,
                "historical_journal_countervalue_count": (
                    historical_countervalue_count
                ),
                "mismatch_bank_statement_line_count": len(mismatches),
                "mismatch_examples": mismatches[:20],
                "blocked_examples": blocked[:20],
                "bounded_scope_classification": (
                    "Source bank transactions without external partial endpoints are "
                    "either categorized through OCA from their operator account, "
                    "partner, analytic and currency inputs or retained open exactly "
                    "when the source transaction is unreconciled."
                ),
            }
            self.write({
                "status": status,
                "finished_at": fields.Datetime.now(),
                "imported_move_count": (
                    created_bank_line_count + reused_bank_line_count
                ),
                "imported_move_line_count": sum(
                    bool(row["is_reconciled"]) for row in rows
                ),
                "warning_count": len(blocked) + len(mismatches),
                "statistics_json": stats,
                "notes": (
                    "Track B native OCA categorization for bank transactions without "
                    "external reconciliation endpoints."
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

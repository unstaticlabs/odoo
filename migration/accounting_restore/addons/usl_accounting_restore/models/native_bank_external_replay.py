from collections import defaultdict

from odoo import Command, fields, models


class RebuildAccountImportRun(models.Model):
    _inherit = "rebuild.account.import.run"

    def _native_bank_external_stage_journal_suspense(
        self,
        journals,
        bank_rows,
    ):
        source_journal_ids = {row["journal_id"] for row in bank_rows}
        Account = self.env["account.account"].sudo().with_context(
            active_test=False,
            tracking_disable=True,
            mail_create_nolog=True,
        )
        staged = {}
        for source_journal_id in source_journal_ids:
            journal = journals.get(source_journal_id)
            if not journal or journal.type not in ("bank", "cash", "credit"):
                continue
            company = journal.company_id
            source_suspense = journal.suspense_account_id
            staging = Account.with_company(company).search([
                ("code", "=", "TBSUSP"),
                ("company_ids", "in", company.id),
                ("rebuild_source_model", "=", False),
            ], limit=1)
            if not staging:
                staging = Account.with_company(company).create({
                    "name": "Track B Bank Matching staging suspense",
                    "code": "TBSUSP",
                    "account_type": "asset_current",
                    "reconcile": True,
                    "company_ids": [Command.set([company.id])],
                    "rebuild_import_note": (
                        "Temporary clean-target suspense used only while OCA "
                        "Bank Matching converts source suspense allocations into "
                        "native categorized lines."
                    ),
                })
            elif not staging.active:
                staging.active = True
            journal.suspense_account_id = staging
            staged[journal.id] = {
                "journal": journal,
                "source_suspense": source_suspense,
                "staging": staging,
            }
        return staged

    @staticmethod
    def _native_bank_external_restore_journal_suspense(staged):
        for values in staged.values():
            values["journal"].suspense_account_id = values["source_suspense"]

    def _native_bank_external_source_ids(self, conn, options):
        rows = self._fetchall(
            conn,
            """
            SELECT statement_line.id
            FROM account_bank_statement_line statement_line
            JOIN account_move move ON move.id = statement_line.move_id
            WHERE statement_line.company_id = ANY(%(source_company_ids)s)
              AND move.date BETWEEN %(date_from)s AND %(date_to)s
            ORDER BY statement_line.id
            """,
            options,
        )
        covered_ids = set(
            self.env["account.bank.statement.line"].sudo().search([
                (
                    "rebuild_source_model",
                    "in",
                    [
                        "account.bank.statement.line.native_expense_settlement",
                        "account.bank.statement.line.native_document_settlement",
                        "account.bank.statement.line.native_bank_categorization",
                    ],
                ),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ]).mapped("rebuild_source_id"),
        )
        return [row["id"] for row in rows if row["id"] not in covered_ids]

    def _native_bank_external_bank_rows(self, conn, options, source_bank_ids):
        if not source_bank_ids:
            return []
        return self._fetchall(
            conn,
            """
            SELECT statement_line.id, statement_line.move_id,
                   statement_line.journal_id, statement_line.company_id,
                   statement_line.sequence, statement_line.partner_id,
                   statement_line.foreign_currency_id,
                   statement_line.account_number, statement_line.partner_name,
                   statement_line.transaction_type, statement_line.payment_ref,
                   statement_line.transaction_details,
                   statement_line.amount, statement_line.amount_currency,
                   statement_line.is_reconciled,
                   statement_line.unique_import_id,
                   move.date, move.name AS move_name,
                   liquidity.balance AS liquidity_balance,
                   liquidity.amount_currency AS liquidity_amount_currency,
                   liquidity.currency_id AS liquidity_currency_id
            FROM account_bank_statement_line statement_line
            JOIN account_move move ON move.id = statement_line.move_id
            JOIN account_journal journal ON journal.id = statement_line.journal_id
            JOIN LATERAL (
                SELECT line.balance, line.amount_currency, line.currency_id
                FROM account_move_line line
                WHERE line.move_id = statement_line.move_id
                  AND line.account_id = journal.default_account_id
                ORDER BY line.id
                LIMIT 1
            ) liquidity ON true
            WHERE statement_line.id = ANY(%(source_bank_ids)s)
            ORDER BY move.date, statement_line.id
            """,
            {**options, "source_bank_ids": source_bank_ids},
        )

    def _native_bank_external_counterpart_rows(
        self,
        conn,
        options,
        source_bank_ids,
    ):
        if not source_bank_ids:
            return []
        return self._fetchall(
            conn,
            """
            SELECT statement_line.id AS source_bank_statement_line_id,
                   line.id, line.sequence, line.account_id, line.partner_id,
                   line.currency_id, line.name, line.balance,
                   line.amount_currency, line.analytic_distribution
            FROM account_bank_statement_line statement_line
            JOIN account_journal journal ON journal.id = statement_line.journal_id
            JOIN account_move_line line ON line.move_id = statement_line.move_id
            WHERE statement_line.id = ANY(%(source_bank_ids)s)
              AND line.account_id IS DISTINCT FROM journal.default_account_id
            ORDER BY statement_line.id, line.sequence, line.id
            """,
            {**options, "source_bank_ids": source_bank_ids},
        )

    def _native_bank_external_edge_rows(self, conn, options, source_bank_ids):
        if not source_bank_ids:
            return []
        return self._fetchall(
            conn,
            """
            SELECT statement_line.id AS source_bank_statement_line_id,
                   statement_move_line.id AS source_statement_line_id,
                   partial.id AS source_partial_reconcile_id,
                   partial.full_reconcile_id AS source_full_reconcile_id,
                   partial.amount AS partial_amount,
                   partial.debit_amount_currency,
                   partial.credit_amount_currency,
                   partial.max_date,
                   partial.debit_move_id AS debit_source_line_id,
                   partial.credit_move_id AS credit_source_line_id,
                   endpoint.id AS endpoint_source_line_id,
                   endpoint.move_id AS endpoint_source_move_id,
                   endpoint.account_id AS endpoint_account_id,
                   endpoint.partner_id AS endpoint_partner_id,
                   endpoint.currency_id AS endpoint_currency_id,
                   endpoint.balance AS endpoint_balance,
                   endpoint.amount_currency AS endpoint_amount_currency,
                   endpoint_move.date AS endpoint_date,
                   endpoint_move.state AS endpoint_state,
                   endpoint_move.move_type AS endpoint_move_type,
                   endpoint_journal.code AS endpoint_journal_code,
                   endpoint_bank.id AS endpoint_bank_statement_line_id,
                   balancing.id AS balancing_source_line_id,
                   balancing.account_id AS balancing_account_id,
                   balancing.partner_id AS balancing_partner_id,
                   balancing.currency_id AS balancing_currency_id,
                   balancing.account_type AS balancing_account_type,
                   balancing.balance AS balancing_balance,
                   balancing.amount_currency AS balancing_amount_currency
            FROM account_bank_statement_line statement_line
            JOIN account_move_line statement_move_line
              ON statement_move_line.move_id = statement_line.move_id
            JOIN account_partial_reconcile partial
              ON partial.debit_move_id = statement_move_line.id
              OR partial.credit_move_id = statement_move_line.id
            JOIN account_move_line endpoint ON endpoint.id = CASE
                WHEN partial.debit_move_id = statement_move_line.id
                THEN partial.credit_move_id
                ELSE partial.debit_move_id
            END
            JOIN account_move endpoint_move ON endpoint_move.id = endpoint.move_id
            JOIN account_journal endpoint_journal
              ON endpoint_journal.id = endpoint_move.journal_id
            LEFT JOIN account_bank_statement_line endpoint_bank
              ON endpoint_bank.move_id = endpoint.move_id
            LEFT JOIN LATERAL (
                SELECT candidate.*, account.account_type
                FROM account_move_line candidate
                JOIN account_account account ON account.id = candidate.account_id
                WHERE candidate.move_id = endpoint.move_id
                  AND candidate.id != endpoint.id
                  AND round((candidate.balance + endpoint.balance)::numeric, 2) = 0
                ORDER BY
                    CASE WHEN account.account_type IN ('income', 'expense')
                         THEN 0 ELSE 1 END,
                    candidate.sequence,
                    candidate.id
                LIMIT 1
            ) balancing ON endpoint_journal.code = 'EXCH'
            WHERE statement_line.id = ANY(%(source_bank_ids)s)
              AND endpoint.move_id != statement_line.move_id
            ORDER BY partial.max_date, partial.id, statement_line.id
            """,
            {**options, "source_bank_ids": source_bank_ids},
        )

    @staticmethod
    def _native_bank_external_manual_move_ids(edge_rows):
        return sorted({
            edge["endpoint_source_move_id"]
            for edge in edge_rows
            if (
                edge["endpoint_state"] == "posted"
                and edge["endpoint_move_type"] == "entry"
                and edge["endpoint_journal_code"] != "EXCH"
                and not edge["endpoint_bank_statement_line_id"]
            )
        })

    @staticmethod
    def _native_bank_external_boundary_kind(
        edge,
        source_bank_ids,
        mapped_endpoint_line_ids=None,
    ):
        if (
            edge["endpoint_state"] == "draft"
            and edge["endpoint_move_type"] != "entry"
        ):
            return "draft_document_prepayment"
        if edge["endpoint_date"] > fields.Date.to_date("2026-06-30"):
            return "future_document_prepayment"
        if edge["endpoint_state"] == "draft":
            return "draft_entry_boundary"
        endpoint_bank_id = edge["endpoint_bank_statement_line_id"]
        if (
            endpoint_bank_id
            and endpoint_bank_id not in source_bank_ids
            and edge["endpoint_source_line_id"]
            not in (mapped_endpoint_line_ids or set())
        ):
            return "preexisting_bounded_bank_aggregate"
        return False

    def _native_bank_external_maps(
        self,
        conn,
        options,
        bank_rows,
        counterpart_rows,
        manual_move_rows,
        manual_line_rows,
    ):
        currencies = self._currency_map(conn)
        configuration_options = {**options, "date_from": "2025-07-01"}
        countries = self._country_map(conn)
        companies, _company_rows = self._company_map(
            conn,
            configuration_options,
            countries,
        )
        accounts, archive_account_ids = self._account_map(
            conn,
            configuration_options,
            companies,
            currencies,
        )
        journals, archive_journal_ids = self._journal_map(
            conn,
            configuration_options,
            companies,
            accounts,
            currencies,
        )
        staged_suspense = self._native_bank_external_stage_journal_suspense(
            journals,
            bank_rows,
        )
        partners = self._partner_map(conn, configuration_options)
        analytic_accounts = {
            account.rebuild_source_id: account
            for account in self.env["account.analytic.account"].sudo().search([
                (
                    "rebuild_source_model",
                    "=",
                    "account.analytic.account",
                ),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ])
        }
        return (
            journals,
            accounts,
            partners,
            currencies,
            analytic_accounts,
            archive_account_ids,
            staged_suspense,
            set(archive_journal_ids),
        )

    def _native_bank_external_categorize(
        self,
        bank_line,
        source_lines,
        accounts,
        partners,
        currencies,
        analytic_accounts,
    ):
        bank_line.reconcile_data_info = bank_line._default_reconcile_data()
        for source_line in source_lines:
            account = accounts[source_line["account_id"]]
            partner = partners.get(source_line["partner_id"])
            currency = currencies[source_line["currency_id"]]
            analytic_distribution = self._native_replay_analytic_distribution(
                source_line["analytic_distribution"],
                analytic_accounts,
            )
            self._native_bank_replay_add_manual_allocation(
                bank_line,
                source_line,
                account,
                partner,
                currency,
                analytic_distribution,
            )
        if not bank_line.reconcile_data_info.get("can_reconcile"):
            message = (
                "OCA multi-line bank categorization does not balance for source "
                f"bank line {source_lines[0]['source_bank_statement_line_id']}"
            )
            raise ValueError(message)
        bank_line.reconcile_bank_line()

    def _native_bank_external_trace_lines(
        self,
        bank_line,
        source_lines,
        accounts,
        partners,
        currencies,
        analytic_accounts,
        options,
        trace_model="account.move.line.native_external_replay",
        trace_note=(
            "Track B external-endpoint bank allocation created through OCA "
            "Bank Matching from source operator inputs."
        ),
        strict_line_count=True,
    ):
        target_lines = bank_line.line_ids.filtered(
            lambda line: line.account_id != bank_line.journal_id.default_account_id,
        )
        available_ids = set(target_lines.ids)
        mapped = {}
        mismatches = []
        for source_line in source_lines:
            account = accounts[source_line["account_id"]]
            partner = partners.get(source_line["partner_id"])
            currency = currencies[source_line["currency_id"]]
            analytic_distribution = self._native_replay_analytic_distribution(
                source_line["analytic_distribution"],
                analytic_accounts,
            )
            existing = target_lines.filtered(
                lambda line: (
                    line.rebuild_source_model
                    == trace_model
                    and line.rebuild_source_id == source_line["id"]
                ),
            )[:1]
            candidates = target_lines.filtered(
                lambda line: (
                    line.id in available_ids
                    and line.account_id == account
                    and line.partner_id == (partner or self.env["res.partner"])
                    and line.currency_id == currency
                    and abs(
                        line.balance - self._amount(source_line["balance"]),
                    )
                    <= 0.011
                    and abs(
                        line.amount_currency
                        - self._amount(source_line["amount_currency"]),
                    )
                    <= 0.011
                    and (line.analytic_distribution or {})
                    == (analytic_distribution or {})
                ),
            ).sorted(key=lambda line: (line.sequence, line.id))
            target_line = existing or candidates[:1]
            if target_line:
                available_ids.discard(target_line.id)
                if not existing:
                    target_line.write({
                        "rebuild_import_note": trace_note,
                        **self._trace_values(
                            trace_model,
                            source_line["id"],
                            options,
                        ),
                    })
                mapped[source_line["id"]] = target_line
            else:
                mismatches.append({
                    "source_bank_statement_line_id": (
                        source_line["source_bank_statement_line_id"]
                    ),
                    "source_move_line_id": source_line["id"],
                    "classification": "missing_exact_external_bank_counterpart",
                })
        if strict_line_count and len(target_lines) != len(source_lines):
            mismatches.append({
                "source_bank_statement_line_id": bank_line.rebuild_source_id,
                "classification": "external_bank_counterpart_line_count",
                "source_count": len(source_lines),
                "target_count": len(target_lines),
            })
        return mapped, mismatches

    def _native_bank_external_replay_banks(
        self,
        options,
        bank_rows,
        counterpart_rows,
        journals,
        accounts,
        partners,
        currencies,
        analytic_accounts,
    ):
        lines_by_bank = defaultdict(list)
        for row in counterpart_rows:
            lines_by_bank[row["source_bank_statement_line_id"]].append(row)
        StatementLine = self.env["account.bank.statement.line"].sudo().with_context(
            tracking_disable=True,
            mail_create_nolog=True,
            rebuild_skip_auto_reconcile=True,
        )
        created_count = 0
        reused_count = 0
        categorized_count = 0
        reused_categorization_count = 0
        historical_countervalue_count = 0
        blocked = []
        mismatches = []
        bank_lines = {}
        target_lines = {}
        for row in bank_rows:
            source_bank_id = row["id"]
            source_lines = lines_by_bank[source_bank_id]
            journal = journals.get(row["journal_id"])
            partner = partners.get(row["partner_id"])
            foreign_currency = currencies.get(row["foreign_currency_id"])
            missing_accounts = sorted({
                line["account_id"]
                for line in source_lines
                if line["account_id"] not in accounts
            })
            missing_currencies = sorted({
                line["currency_id"]
                for line in source_lines
                if line["currency_id"] not in currencies
            })
            source_analytic_ids = set().union(*[
                self._native_bank_categorization_source_analytic_ids(
                    line["analytic_distribution"],
                )
                for line in source_lines
            ]) if source_lines else set()
            missing_analytic_ids = sorted(
                source_analytic_ids - analytic_accounts.keys(),
            )
            if (
                not journal
                or not source_lines
                or missing_accounts
                or missing_currencies
                or missing_analytic_ids
            ):
                blocked.append({
                    "source_bank_statement_line_id": source_bank_id,
                    "classification": "missing_external_bank_configuration",
                    "missing_journal": not bool(journal),
                    "missing_account_ids": missing_accounts,
                    "missing_currency_ids": missing_currencies,
                    "missing_analytic_ids": missing_analytic_ids,
                })
                continue
            if (
                "reconcile_mode" in journal._fields
                and journal.reconcile_mode != "edit"
            ):
                journal.reconcile_mode = "edit"
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
                    "account.bank.statement.line.native_external_replay",
                ),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "=", source_bank_id),
            ], limit=1)
            created = False
            categorized = False
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
                                "Track B external-endpoint bank transaction "
                                "reconstructed through native OCA categorization."
                            ),
                            **self._trace_values(
                                "account.bank.statement.line.native_external_replay",
                                source_bank_id,
                                options,
                            ),
                        })
                        created = True
                        if bank_line.is_reconciled:
                            bank_line.unreconcile_bank_line()
                    if not bank_line.is_reconciled:
                        self._native_bank_external_categorize(
                            bank_line,
                            source_lines,
                            accounts,
                            partners,
                            currencies,
                            analytic_accounts,
                        )
                        categorized = True
                mapped, line_mismatches = self._native_bank_external_trace_lines(
                    bank_line,
                    source_lines,
                    accounts,
                    partners,
                    currencies,
                    analytic_accounts,
                    options,
                )
            except Exception as exc:  # noqa: BLE001 - classify source line.
                blocked.append({
                    "source_bank_statement_line_id": source_bank_id,
                    "classification": "native_external_bank_categorization_error",
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                })
                continue
            created_count += int(created)
            reused_count += int(not created)
            categorized_count += int(categorized)
            reused_categorization_count += int(not categorized)
            bank_lines[source_bank_id] = bank_line
            target_lines.update(mapped)
            mismatches.extend(line_mismatches)
            checks = {
                "date": bank_line.date == row["date"],
                "journal": bank_line.journal_id.rebuild_source_id
                == row["journal_id"],
                "amount": round(bank_line.amount, 6)
                == round(self._amount(row["amount"]), 6),
                "amount_currency": round(bank_line.amount_currency, 2)
                == round(effective_amount_currency, 2),
                "foreign_currency": bank_line.foreign_currency_id
                == (effective_foreign_currency or self.env["res.currency"]),
                "reconciled": bank_line.is_reconciled,
                "balanced": round(sum(bank_line.line_ids.mapped("balance")), 2)
                == 0.0,
                "liquidity_balance": round(
                    bank_line.line_ids.filtered(
                        lambda line: line.account_id
                        == bank_line.journal_id.default_account_id,
                    ).balance,
                    2,
                )
                == round(self._amount(row["liquidity_balance"]), 2),
            }
            if not all(checks.values()):
                mismatches.append({
                    "source_bank_statement_line_id": source_bank_id,
                    "target_bank_statement_line_id": bank_line.id,
                    "checks": checks,
                })
        return {
            "bank_lines": bank_lines,
            "target_lines": target_lines,
            "created_count": created_count,
            "reused_count": reused_count,
            "categorized_count": categorized_count,
            "reused_categorization_count": reused_categorization_count,
            "historical_countervalue_count": historical_countervalue_count,
            "blocked": blocked,
            "mismatches": mismatches,
        }

    def _native_bank_external_trace_exchange(
        self,
        options,
        exchange_edges,
        target_lines,
    ):
        traced_count = 0
        reused_count = 0
        boundary_count = 0
        blocked = []
        mismatches = []
        used_ids = set()
        for edge in exchange_edges:
            source_partial_id = edge["source_partial_reconcile_id"]
            target_line = target_lines.get(edge["source_statement_line_id"])
            if not target_line:
                boundary_count += 1
                continue
            candidates = (
                target_line.matched_debit_ids | target_line.matched_credit_ids
            ).filtered(
                lambda partial: (
                    partial.id not in used_ids
                    and (
                        partial.debit_move_id.move_id.journal_id.code == "EXCH"
                        or partial.credit_move_id.move_id.journal_id.code == "EXCH"
                    )
                ),
            )
            partial = candidates.filtered(
                lambda item: (
                    item.rebuild_source_model
                    == "account.partial.reconcile.native_external_exchange"
                    and item.rebuild_source_id == source_partial_id
                ),
            )[:1]
            if partial:
                reused_count += 1
            else:
                source_amount = self._amount(edge["partial_amount"])
                partial = candidates.filtered(
                    lambda item: abs(item.amount - source_amount) <= 0.011,
                ).sorted(
                    key=lambda item: (
                        abs(item.amount - source_amount),
                        item.max_date,
                        item.id,
                    ),
                )[:1]
                if not partial:
                    blocked.append({
                        "source_partial_reconcile_id": source_partial_id,
                        "classification": "missing_native_external_exchange",
                        "source_amount": round(source_amount, 2),
                    })
                    continue
                partial.write({
                    "rebuild_import_note": (
                        "Track B exchange partial generated natively while "
                        "clearing an external-endpoint bank allocation."
                    ),
                    **self._trace_values(
                        "account.partial.reconcile.native_external_exchange",
                        source_partial_id,
                        options,
                    ),
                })
                traced_count += 1
            used_ids.add(partial.id)
            exchange_line = (
                partial.credit_move_id
                if partial.debit_move_id == target_line
                else partial.debit_move_id
            )
            exchange_move = exchange_line.move_id
            if not exchange_move.rebuild_source_model:
                exchange_move.write({
                    "rebuild_import_note": (
                        "Track B exchange-difference move generated natively "
                        "from external bank clearing."
                    ),
                    **self._trace_values(
                        "account.move.native_external_exchange",
                        edge["endpoint_source_move_id"],
                        options,
                    ),
                })
            if not exchange_line.rebuild_source_model:
                exchange_line.write({
                    **self._trace_values(
                        "account.move.line.native_external_exchange",
                        edge["endpoint_source_line_id"],
                        options,
                    ),
                })
            checks = {
                "amount": abs(
                    partial.amount - self._amount(edge["partial_amount"]),
                )
                <= 0.011,
                "journal": exchange_move.journal_id.code == "EXCH",
                "posted": exchange_move.state == "posted",
                "balanced": round(sum(exchange_move.line_ids.mapped("balance")), 2)
                == 0.0,
                "counterpart_account": exchange_line.account_id.rebuild_source_id
                == edge["endpoint_account_id"],
                "counterpart_balance": abs(
                    exchange_line.balance
                    - self._amount(edge["endpoint_balance"]),
                )
                <= 0.011,
            }
            if not all(checks.values()):
                mismatches.append({
                    "source_partial_reconcile_id": source_partial_id,
                    "target_partial_reconcile_id": partial.id,
                    "checks": checks,
                })
        return {
            "traced_count": traced_count,
            "reused_count": reused_count,
            "boundary_count": boundary_count,
            "blocked": blocked,
            "mismatches": mismatches,
        }

    def run_native_bank_external_replay_from_source(self, options):
        """Replay the final current-period external-endpoint bank perimeter."""
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
        staged_suspense = {}
        try:
            source_bank_ids = self._native_bank_external_source_ids(conn, options)
            bank_rows = self._native_bank_external_bank_rows(
                conn,
                options,
                source_bank_ids,
            )
            counterpart_rows = self._native_bank_external_counterpart_rows(
                conn,
                options,
                source_bank_ids,
            )
            edge_rows = self._native_bank_external_edge_rows(
                conn,
                options,
                source_bank_ids,
            )
            manual_move_ids = self._native_bank_external_manual_move_ids(
                edge_rows,
            )
            manual_move_rows = self._native_general_reconciliation_move_rows(
                conn,
                options,
                manual_move_ids,
            )
            manual_line_rows = self._native_general_reconciliation_line_rows(
                conn,
                options,
                manual_move_ids,
            )
            (
                journals,
                accounts,
                partners,
                currencies,
                analytic_accounts,
                archive_account_ids,
                staged_suspense,
                archive_journal_ids,
            ) = self._native_bank_external_maps(
                conn,
                options,
                bank_rows,
                counterpart_rows,
                manual_move_rows,
                manual_line_rows,
            )
            replay = self._native_bank_external_replay_banks(
                options,
                bank_rows,
                counterpart_rows,
                journals,
                accounts,
                partners,
                currencies,
                analytic_accounts,
            )
            (
                manual_moves,
                created_manual_count,
                reused_manual_count,
                manual_blocked,
            ) = self._native_general_reconciliation_manual_moves(
                options,
                manual_move_rows,
                manual_line_rows,
                journals,
                accounts,
                partners,
                currencies,
                analytic_accounts=analytic_accounts,
            )
            manual_target_lines, manual_mismatches = (
                self._native_general_reconciliation_validate_manual_moves(
                    options,
                    manual_move_rows,
                    manual_line_rows,
                    manual_moves,
                )
            )
            for source_line in manual_line_rows:
                target_line = manual_target_lines.get(source_line["id"])
                expected_analytic = self._native_replay_analytic_distribution(
                    source_line["analytic_distribution"],
                    analytic_accounts,
                )
                if (
                    not target_line
                    or (target_line.analytic_distribution or {})
                    != (expected_analytic or {})
                ):
                    manual_mismatches.append({
                        "source_move_line_id": source_line["id"],
                        "classification": "manual_entry_analytic_distribution",
                    })
            bounded_target_line_records = self.env["account.move.line"].sudo().search([
                (
                    "rebuild_source_model",
                    "=",
                    "account.move.line.native_bounded_bank_counterpart",
                ),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                (
                    "rebuild_source_id",
                    "in",
                    sorted({
                        edge["endpoint_source_line_id"] for edge in edge_rows
                    })
                    or [0],
                ),
            ])
            bounded_target_lines = {
                line.rebuild_source_id: line for line in bounded_target_line_records
            }
            target_lines = {
                **replay["target_lines"],
                **manual_target_lines,
                **bounded_target_lines,
            }
            boundary_counts = defaultdict(int)
            boundary_edges = []
            input_edges = []
            exchange_edges = []
            boundary_by_source_line = {
                edge["source_statement_line_id"]
                for edge in edge_rows
                if self._native_bank_external_boundary_kind(
                    edge,
                    source_bank_ids,
                    set(target_lines),
                )
            }
            for edge in edge_rows:
                boundary_kind = self._native_bank_external_boundary_kind(
                    edge,
                    source_bank_ids,
                    set(target_lines),
                )
                if (
                    not boundary_kind
                    and edge["endpoint_journal_code"] == "EXCH"
                    and edge["source_statement_line_id"]
                    in boundary_by_source_line
                ):
                    boundary_kind = "exchange_of_bounded_input"
                if boundary_kind:
                    boundary_counts[boundary_kind] += 1
                    boundary_edges.append({
                        "source_partial_reconcile_id": (
                            edge["source_partial_reconcile_id"]
                        ),
                        "source_bank_statement_line_id": (
                            edge["source_bank_statement_line_id"]
                        ),
                        "classification": boundary_kind,
                        "endpoint_source_move_id": (
                            edge["endpoint_source_move_id"]
                        ),
                    })
                elif edge["endpoint_journal_code"] == "EXCH":
                    exchange_edges.append(edge)
                else:
                    input_edges.append(edge)
            (
                created_partial_count,
                reused_partial_count,
                rounding_difference_count,
                partial_blocked,
                partial_mismatches,
            ) = self._native_general_reconciliation_apply_inputs(
                options,
                input_edges,
                target_lines,
            )
            exchange = self._native_bank_external_trace_exchange(
                options,
                exchange_edges,
                target_lines,
            )
            for source_account_id in archive_account_ids:
                accounts[source_account_id].active = False
            for source_journal_id in archive_journal_ids:
                journals[source_journal_id].active = False
            blocked = (
                replay["blocked"]
                + manual_blocked
                + partial_blocked
                + exchange["blocked"]
            )
            mismatches = (
                replay["mismatches"]
                + manual_mismatches
                + partial_mismatches
                + exchange["mismatches"]
            )
            staging_accounts = self.env["account.account"]
            for values in staged_suspense.values():
                staging_accounts |= values["staging"]
            staging_lines = self.env["account.move.line"].sudo().search([
                ("account_id", "in", staging_accounts.ids or [0]),
            ])
            if staging_lines:
                mismatches.append({
                    "classification": "staging_suspense_not_cleared",
                    "account_ids": staging_accounts.ids,
                    "line_count": len(staging_lines),
                    "balance": round(sum(staging_lines.mapped("balance")), 2),
                })
            status = "passed" if not blocked and not mismatches else "partial"
            stats = {
                "classification": "NATIVE_VALIDATION_NATIVE_EXTERNAL_BANK_REPLAY",
                "date_from": options["date_from"],
                "date_to": options["date_to"],
                "source_company_ids": options["source_company_ids"],
                "source_bank_statement_line_count": len(bank_rows),
                "source_bank_counterpart_line_count": len(counterpart_rows),
                "source_external_partial_count": len(edge_rows),
                "created_bank_statement_line_count": replay["created_count"],
                "reused_bank_statement_line_count": replay["reused_count"],
                "created_categorization_count": replay["categorized_count"],
                "reused_categorization_count": (
                    replay["reused_categorization_count"]
                ),
                "historical_journal_countervalue_count": (
                    replay["historical_countervalue_count"]
                ),
                "source_manual_move_count": len(manual_move_rows),
                "source_manual_move_line_count": len(manual_line_rows),
                "source_manual_analytic_line_count": sum(
                    bool(row["analytic_distribution"])
                    for row in manual_line_rows
                ),
                "archived_boundary_account_count": len(archive_account_ids),
                "archived_boundary_journal_count": len(archive_journal_ids),
                "staged_suspense_journal_count": len(staged_suspense),
                "staging_suspense_line_count": len(staging_lines),
                "staging_suspense_balance": round(
                    sum(staging_lines.mapped("balance")),
                    2,
                ),
                "created_manual_move_count": created_manual_count,
                "reused_manual_move_count": reused_manual_count,
                "native_input_partial_count": len(input_edges),
                "mapped_bounded_bank_counterpart_count": len(
                    bounded_target_lines,
                ),
                "created_input_partial_count": created_partial_count,
                "reused_input_partial_count": reused_partial_count,
                "native_rounding_difference_count": rounding_difference_count,
                "source_exchange_partial_count": len(exchange_edges),
                "traced_exchange_partial_count": exchange["traced_count"],
                "reused_exchange_partial_count": exchange["reused_count"],
                "exchange_boundary_count": exchange["boundary_count"],
                "boundary_partial_count": len(boundary_edges),
                "boundary_counts": dict(sorted(boundary_counts.items())),
                "boundary_examples": boundary_edges[:20],
                "blocked_count": len(blocked),
                "blocked_examples": blocked[:20],
                "mismatch_count": len(mismatches),
                "mismatch_examples": mismatches[:20],
                "bounded_scope_classification": (
                    "All remaining current-period bank transactions are created "
                    "through OCA with exact source counterpart lines. Posted "
                    "manual/payroll/clearing endpoints are reconstructed and "
                    "reconciled natively; exact counterparts preserved by earlier "
                    "settlement stages are reused for cross-bank reconciliation. "
                    "Only draft and post-cutoff documents remain open prepayments."
                ),
            }
            self.write({
                "status": status,
                "finished_at": fields.Datetime.now(),
                "imported_move_count": (
                    len(bank_rows) + len(manual_move_rows)
                ),
                "imported_move_line_count": (
                    len(counterpart_rows) + len(manual_line_rows)
                ),
                "warning_count": len(blocked) + len(mismatches),
                "statistics_json": stats,
                "notes": (
                    "Track B final current-period external-endpoint bank replay."
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
            self._native_bank_external_restore_journal_suspense(
                staged_suspense,
            )
            conn.close()

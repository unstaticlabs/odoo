from collections import defaultdict

from odoo import fields, models


class RebuildAccountImportRun(models.Model):
    _inherit = "rebuild.account.import.run"

    def _native_expense_settlement_source_line_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            WITH current_expense_moves AS (
                SELECT DISTINCT expense.account_move_id AS move_id
                FROM hr_expense expense
                WHERE expense.company_id = ANY(%(source_company_ids)s)
                  AND expense.state = 'paid'
                  AND expense.date BETWEEN %(date_from)s AND %(date_to)s
                  AND expense.account_move_id IS NOT NULL
            )
            SELECT line.id, line.move_id, line.sequence, line.account_id,
                   line.partner_id, currency.name AS currency_name,
                   line.display_type, line.balance, line.amount_currency
            FROM account_move_line line
            JOIN current_expense_moves expense_move ON expense_move.move_id = line.move_id
            LEFT JOIN res_currency currency ON currency.id = line.currency_id
            WHERE line.account_id IS NOT NULL
            ORDER BY line.move_id, line.sequence, line.id
            """,
            options,
        )

    def _native_expense_settlement_edge_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            WITH current_expense_moves AS (
                SELECT DISTINCT expense.payment_mode,
                       expense.account_move_id AS move_id
                FROM hr_expense expense
                WHERE expense.company_id = ANY(%(source_company_ids)s)
                  AND expense.state = 'paid'
                  AND expense.date BETWEEN %(date_from)s AND %(date_to)s
                  AND expense.account_move_id IS NOT NULL
            )
            SELECT expense_move.payment_mode,
                   source_line.move_id AS source_move_id,
                   source_line.id AS source_line_id,
                   partial.id AS source_partial_reconcile_id,
                   partial.full_reconcile_id AS source_full_reconcile_id,
                   partial.amount AS partial_amount,
                   CASE
                       WHEN partial.debit_move_id = source_line.id
                       THEN partial.debit_amount_currency
                       ELSE partial.credit_amount_currency
                   END AS partial_amount_currency,
                   statement_line.id AS source_bank_statement_line_id,
                   statement_move.date AS statement_date
            FROM current_expense_moves expense_move
            JOIN account_move_line source_line
              ON source_line.move_id = expense_move.move_id
            JOIN account_partial_reconcile partial
              ON partial.debit_move_id = source_line.id
              OR partial.credit_move_id = source_line.id
            JOIN account_move_line statement_move_line
              ON statement_move_line.id = CASE
                   WHEN partial.debit_move_id = source_line.id
                   THEN partial.credit_move_id
                   ELSE partial.debit_move_id
                 END
            JOIN account_bank_statement_line statement_line
              ON statement_line.move_id = statement_move_line.move_id
            JOIN account_move statement_move ON statement_move.id = statement_line.move_id
            ORDER BY statement_move.date, statement_line.id,
                     partial.id, source_line.id
            """,
            options,
        )

    def _native_expense_settlement_bank_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            WITH current_expense_moves AS (
                SELECT DISTINCT expense.payment_mode,
                       expense.account_move_id AS move_id
                FROM hr_expense expense
                WHERE expense.company_id = ANY(%(source_company_ids)s)
                  AND expense.state = 'paid'
                  AND expense.date BETWEEN %(date_from)s AND %(date_to)s
                  AND expense.account_move_id IS NOT NULL
            ),
            current_expense_lines AS (
                SELECT line.id
                FROM current_expense_moves expense_move
                JOIN account_move_line line ON line.move_id = expense_move.move_id
            ),
            selected_statement_lines AS (
                SELECT DISTINCT statement_line.id,
                       expense_move.payment_mode
                FROM current_expense_moves expense_move
                JOIN account_move_line source_line
                  ON source_line.move_id = expense_move.move_id
                JOIN account_partial_reconcile partial
                  ON partial.debit_move_id = source_line.id
                  OR partial.credit_move_id = source_line.id
                JOIN account_move_line statement_move_line
                  ON statement_move_line.id = CASE
                       WHEN partial.debit_move_id = source_line.id
                       THEN partial.credit_move_id
                       ELSE partial.debit_move_id
                     END
                JOIN account_bank_statement_line statement_line
                  ON statement_line.move_id = statement_move_line.move_id
            ),
            nonliquidity_lines AS (
                SELECT selected.id AS statement_line_id,
                       move_line.id AS move_line_id
                FROM selected_statement_lines selected
                JOIN account_bank_statement_line statement_line
                  ON statement_line.id = selected.id
                JOIN account_journal journal ON journal.id = statement_line.journal_id
                JOIN account_move_line move_line ON move_line.move_id = statement_line.move_id
                WHERE move_line.account_id IS DISTINCT FROM journal.default_account_id
            ),
            line_scope AS (
                SELECT nonliquidity.statement_line_id,
                       nonliquidity.move_line_id,
                       count(partial.id) FILTER (
                           WHERE current_line.id IS NOT NULL
                       ) AS current_partial_count,
                       count(partial.id) FILTER (
                           WHERE partial.id IS NOT NULL
                             AND current_line.id IS NULL
                       ) AS outside_partial_count
                FROM nonliquidity_lines nonliquidity
                LEFT JOIN account_partial_reconcile partial
                  ON partial.debit_move_id = nonliquidity.move_line_id
                  OR partial.credit_move_id = nonliquidity.move_line_id
                LEFT JOIN current_expense_lines current_line
                  ON current_line.id = CASE
                       WHEN partial.debit_move_id = nonliquidity.move_line_id
                       THEN partial.credit_move_id
                       ELSE partial.debit_move_id
                     END
                GROUP BY nonliquidity.statement_line_id,
                         nonliquidity.move_line_id
            ),
            statement_scope AS (
                SELECT statement_line_id,
                       bool_and(
                           current_partial_count > 0
                           AND outside_partial_count = 0
                       ) AS current_scope_complete
                FROM line_scope
                GROUP BY statement_line_id
            ),
            current_account_allocations AS (
                SELECT statement_line.id AS statement_line_id,
                       statement_move_line.account_id,
                       sum(CASE
                           WHEN statement_move_line.balance >= 0
                           THEN partial.amount
                           ELSE -partial.amount
                       END) AS current_balance
                FROM current_expense_moves expense_move
                JOIN account_move_line source_line
                  ON source_line.move_id = expense_move.move_id
                JOIN account_partial_reconcile partial
                  ON partial.debit_move_id = source_line.id
                  OR partial.credit_move_id = source_line.id
                JOIN account_move_line statement_move_line
                  ON statement_move_line.id = CASE
                       WHEN partial.debit_move_id = source_line.id
                       THEN partial.credit_move_id
                       ELSE partial.debit_move_id
                     END
                JOIN account_bank_statement_line statement_line
                  ON statement_line.move_id = statement_move_line.move_id
                JOIN selected_statement_lines selected ON selected.id = statement_line.id
                GROUP BY statement_line.id, statement_move_line.account_id
            ),
            source_account_totals AS (
                SELECT selected.id AS statement_line_id,
                       move_line.account_id,
                       sum(move_line.balance) AS account_balance
                FROM selected_statement_lines selected
                JOIN account_bank_statement_line statement_line
                  ON statement_line.id = selected.id
                JOIN account_journal journal ON journal.id = statement_line.journal_id
                JOIN account_move_line move_line ON move_line.move_id = statement_line.move_id
                WHERE move_line.account_id IS DISTINCT FROM journal.default_account_id
                GROUP BY selected.id, move_line.account_id
            ),
            outside_account_balances AS (
                SELECT totals.statement_line_id,
                       totals.account_id,
                       round((
                           totals.account_balance
                           - COALESCE(current.current_balance, 0)
                       )::numeric, 2) AS outside_balance
                FROM source_account_totals totals
                LEFT JOIN current_account_allocations current
                  ON current.statement_line_id = totals.statement_line_id
                 AND current.account_id = totals.account_id
            ),
            statement_outside_totals AS (
                SELECT statement_line_id,
                       COALESCE(
                           jsonb_object_agg(account_id::text, outside_balance)
                               FILTER (WHERE abs(outside_balance) > 0.004),
                           '{}'::jsonb
                       ) AS outside_account_totals
                FROM outside_account_balances
                GROUP BY statement_line_id
            )
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
                   statement_move.date, statement_move.name AS move_name,
                   selected.payment_mode,
                   COALESCE(scope.current_scope_complete, false) AS current_scope_complete,
                   COALESCE(outside.outside_account_totals, '{}'::jsonb)
                       AS outside_account_totals
            FROM selected_statement_lines selected
            JOIN account_bank_statement_line statement_line
              ON statement_line.id = selected.id
            JOIN account_move statement_move ON statement_move.id = statement_line.move_id
            LEFT JOIN statement_scope scope ON scope.statement_line_id = statement_line.id
            LEFT JOIN statement_outside_totals outside
              ON outside.statement_line_id = statement_line.id
            ORDER BY statement_move.date, statement_line.id
            """,
            options,
        )

    def _native_expense_settlement_outside_line_rows(self, conn, options):
        """Return exact bank counterparts wholly outside the expense perimeter."""
        return self._fetchall(
            conn,
            """
            WITH current_expense_moves AS (
                SELECT DISTINCT expense.account_move_id AS move_id
                FROM hr_expense expense
                WHERE expense.company_id = ANY(%(source_company_ids)s)
                  AND expense.state = 'paid'
                  AND expense.date BETWEEN %(date_from)s AND %(date_to)s
                  AND expense.account_move_id IS NOT NULL
            ),
            current_expense_lines AS (
                SELECT line.id
                FROM current_expense_moves expense_move
                JOIN account_move_line line ON line.move_id = expense_move.move_id
            ),
            selected_statement_lines AS (
                SELECT DISTINCT statement_line.id, statement_line.move_id,
                       statement_line.journal_id
                FROM current_expense_lines expense_line
                JOIN account_partial_reconcile partial
                  ON partial.debit_move_id = expense_line.id
                  OR partial.credit_move_id = expense_line.id
                JOIN account_move_line statement_move_line
                  ON statement_move_line.id = CASE
                       WHEN partial.debit_move_id = expense_line.id
                       THEN partial.credit_move_id
                       ELSE partial.debit_move_id
                     END
                JOIN account_bank_statement_line statement_line
                  ON statement_line.move_id = statement_move_line.move_id
            ),
            scoped_lines AS (
                SELECT selected.id AS source_bank_statement_line_id,
                       line.id, line.sequence, line.account_id, line.partner_id,
                       line.currency_id, line.name, line.balance,
                       line.amount_currency, line.analytic_distribution,
                       count(partial.id) FILTER (
                           WHERE expense_line.id IS NOT NULL
                       ) AS current_partial_count
                FROM selected_statement_lines selected
                JOIN account_journal journal ON journal.id = selected.journal_id
                JOIN account_move_line line ON line.move_id = selected.move_id
                LEFT JOIN account_partial_reconcile partial
                  ON partial.debit_move_id = line.id
                  OR partial.credit_move_id = line.id
                LEFT JOIN current_expense_lines expense_line
                  ON expense_line.id = CASE
                       WHEN partial.debit_move_id = line.id
                       THEN partial.credit_move_id
                       ELSE partial.debit_move_id
                     END
                WHERE line.account_id IS DISTINCT FROM journal.default_account_id
                GROUP BY selected.id, line.id, line.sequence, line.account_id,
                         line.partner_id, line.currency_id, line.name,
                         line.balance, line.amount_currency,
                         line.analytic_distribution
            )
            SELECT source_bank_statement_line_id, id, sequence, account_id,
                   partner_id, currency_id, name, balance, amount_currency,
                   analytic_distribution
            FROM scoped_lines
            WHERE current_partial_count = 0
            ORDER BY source_bank_statement_line_id, sequence, id
            """,
            options,
        )

    @staticmethod
    def _native_expense_settlement_line_key(row):
        return (
            row["account_id"],
            row["partner_id"] or None,
            row["currency_name"] or None,
            row["display_type"] or "product",
            round(float(row["balance"] or 0.0), 2),
            round(float(row["amount_currency"] or 0.0), 2),
        )

    @staticmethod
    def _native_expense_settlement_target_line_key(line):
        return (
            line.account_id.rebuild_source_id or None,
            line.partner_id.rebuild_source_id or None,
            line.currency_id.name or None,
            line.display_type or "product",
            round(line.balance, 2),
            round(line.amount_currency, 2),
        )

    def _native_expense_settlement_target_line_map(
        self,
        source_line_rows,
        edge_rows,
        options,
        target_move_models=None,
        input_trace_model="account.move.line.native_expense_settlement_input",
        input_trace_note=(
            "Track B source accounting line selected as an OCA bank-matching "
            "candidate for native expense settlement."
        ),
        write_input_trace=True,
    ):
        Move = self.env["account.move"].sudo()
        target_move_models = target_move_models or [
            "account.move.native_expense_replay",
        ]
        source_move_ids = sorted({row["move_id"] for row in source_line_rows})
        target_moves = {
            move.rebuild_source_id: move
            for move in Move.search([
                (
                    "rebuild_source_model",
                    "in",
                    target_move_models,
                ),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", source_move_ids or [0]),
            ])
        }
        source_rows_by_move = defaultdict(list)
        for row in source_line_rows:
            source_rows_by_move[row["move_id"]].append(row)

        source_to_target = {}
        blocked = []
        for source_move_id, rows in sorted(source_rows_by_move.items()):
            target_move = target_moves.get(source_move_id)
            if not target_move:
                blocked.append({
                    "source_move_id": source_move_id,
                    "classification": "missing_native_target_move",
                })
                continue
            source_groups = defaultdict(list)
            target_groups = defaultdict(list)
            for row in rows:
                source_groups[self._native_expense_settlement_line_key(row)].append(row)
            for line in target_move.line_ids.filtered("account_id"):
                target_groups[
                    self._native_expense_settlement_target_line_key(line)
                ].append(line)
            if set(source_groups) != set(target_groups):
                blocked.append({
                    "source_move_id": source_move_id,
                    "classification": "native_target_line_signature_mismatch",
                    "source_signatures": [str(key) for key in sorted(source_groups, key=str)],
                    "target_signatures": [str(key) for key in sorted(target_groups, key=str)],
                })
                continue
            mismatch = False
            for key in source_groups:
                source_group = sorted(
                    source_groups[key],
                    key=lambda row: (row["sequence"], row["id"]),
                )
                target_group = sorted(
                    target_groups[key],
                    key=lambda line: (line.sequence, line.id),
                )
                if len(source_group) != len(target_group):
                    blocked.append({
                        "source_move_id": source_move_id,
                        "classification": "native_target_line_count_mismatch",
                        "signature": str(key),
                        "source_count": len(source_group),
                        "target_count": len(target_group),
                    })
                    mismatch = True
                    break
                for source_row, target_line in zip(source_group, target_group):
                    source_to_target[source_row["id"]] = target_line
            if mismatch:
                for row in rows:
                    source_to_target.pop(row["id"], None)

        edge_source_line_ids = {row["source_line_id"] for row in edge_rows}
        for source_line_id in sorted(edge_source_line_ids):
            target_line = source_to_target.get(source_line_id)
            if not target_line or not write_input_trace:
                continue
            target_line.write({
                "rebuild_import_note": input_trace_note,
                **self._trace_values(
                    input_trace_model,
                    source_line_id,
                    options,
                ),
            })
        return source_to_target, blocked

    @staticmethod
    def _native_expense_settlement_related_partial(target_line, bank_line):
        partials = target_line.matched_debit_ids | target_line.matched_credit_ids
        return partials.filtered(
            lambda partial: (
                partial.debit_move_id == target_line
                and partial.credit_move_id.move_id == bank_line.move_id
            ) or (
                partial.credit_move_id == target_line
                and partial.debit_move_id.move_id == bank_line.move_id
            ),
        )

    @staticmethod
    def _native_expense_settlement_auto_matched(auto_partials):
        fully_matched = all(len(partials) == 1 for partials in auto_partials)
        if any(auto_partials) and not fully_matched:
            message = "OCA auto-matched only part of the expected source edges"
            raise ValueError(message)
        return fully_matched

    @staticmethod
    def _native_expense_settlement_single_partial(related, source_partial_id):
        if len(related) != 1:
            message = (
                "Expected one new expense/bank partial for source "
                f"{source_partial_id}, got {len(related)}"
            )
            raise ValueError(message)
        return related[0]

    @staticmethod
    def _native_expense_settlement_outside_account_totals(
        bank_line,
        current_partials,
    ):
        liquidity_lines, suspense_lines, other_lines = bank_line._seek_for_lines()
        totals = defaultdict(float)
        for line in (suspense_lines | other_lines) - liquidity_lines:
            source_account_id = line.account_id.rebuild_source_id
            totals[str(source_account_id or 0)] += line.balance
        for partial in current_partials:
            bank_move_line = (
                partial.debit_move_id
                if partial.debit_move_id.move_id == bank_line.move_id
                else partial.credit_move_id
            )
            source_account_id = bank_move_line.account_id.rebuild_source_id
            direction = 1.0 if bank_move_line.balance >= 0 else -1.0
            totals[str(source_account_id or 0)] -= direction * partial.amount
        return {
            source_account_id: round(balance, 2)
            for source_account_id, balance in sorted(totals.items())
            if abs(balance) > 0.004
        }

    @staticmethod
    def _native_expense_settlement_remove_exchange_candidates(
        bank_line,
        target_line,
    ):
        """Remove OCA's derived FX row when replaying an operator-fixed rate.

        The source bank matching can preserve a document's custom transaction
        rate instead of accepting the date-rate exchange difference proposed by
        the widget. This bounded adapter discards only that generated candidate
        before setting both exact amounts; OCA still builds and reconciles the
        resulting bank journal items.
        """
        info = bank_line.reconcile_data_info
        data = [
            line
            for line in info.get("data", [])
            if line.get("original_exchange_line_id") != target_line.id
        ]
        bank_line.reconcile_data_info = bank_line._recompute_suspense_line(
            data,
            info["reconcile_auxiliary_id"],
            bank_line.manual_reference,
        )

    @staticmethod
    def _native_expense_settlement_preserve_operator_amounts(
        bank_line,
        target_line,
        amount,
        amount_currency,
    ):
        """Keep a custom-rate amount pair that OCA's widget otherwise derives."""
        info = bank_line.reconcile_data_info
        reference = f"account.move.line;{target_line.id}"
        matches = [
            line for line in info.get("data", []) if line["reference"] == reference
        ]
        if len(matches) != 1:
            message = (
                f"Expected one OCA candidate for move line {target_line.id}, "
                f"got {len(matches)}"
            )
            raise ValueError(message)
        matches[0].update({
            "amount": amount,
            "credit": -amount if amount < 0 else 0.0,
            "debit": amount if amount > 0 else 0.0,
            "currency_amount": amount_currency,
        })
        bank_line.reconcile_data_info = bank_line._recompute_suspense_line(
            info["data"],
            info["reconcile_auxiliary_id"],
            bank_line.manual_reference,
        )

    def _native_bank_replay_add_manual_allocation(
        self,
        bank_line,
        source_line,
        account,
        partner,
        currency,
        analytic_distribution,
    ):
        """Convert OCA's remaining suspense row into one exact source line."""
        suspense_rows = [
            line
            for line in bank_line.reconcile_data_info.get("data", [])
            if line["kind"] == "suspense"
        ]
        if len(suspense_rows) != 1:
            message = (
                "Expected one OCA suspense row while preserving bounded bank "
                f"line {source_line['id']}, got {len(suspense_rows)}"
            )
            raise ValueError(message)
        bank_line.manual_reference = suspense_rows[0]["reference"]
        bank_line._onchange_manual_reconcile_reference()
        bank_line.manual_account_id = account
        bank_line.manual_partner_id = partner
        bank_line.manual_name = source_line["name"] or "/"
        bank_line.manual_amount = self._amount(source_line["balance"])
        bank_line.analytic_distribution = analytic_distribution
        bank_line._onchange_manual_reconcile_vals()
        data = bank_line.reconcile_data_info.get("data", [])
        manual_rows = [
            line for line in data if line["reference"] == bank_line.manual_reference
        ]
        if len(manual_rows) != 1:
            message = (
                "Expected one OCA allocation row for bounded bank move line "
                f"{source_line['id']}, got {len(manual_rows)}"
            )
            raise ValueError(message)
        manual_rows[0].update({
            "kind": "other",
            "line_currency_id": currency.id,
            "currency_amount": self._amount(source_line["amount_currency"]),
        })
        bank_line.reconcile_data_info = bank_line._recompute_suspense_line(
            data,
            bank_line.reconcile_data_info["reconcile_auxiliary_id"],
            bank_line.manual_reference,
        )

    @staticmethod
    def _native_expense_settlement_add_edge(bank_line, target_line, source_edge):
        """Add one candidate and retain the operator's exact partial amount."""
        bank_line._add_account_move_line(target_line)
        RebuildAccountImportRun._native_expense_settlement_remove_exchange_candidates(
            bank_line,
            target_line,
        )
        bank_line.manual_reference = f"account.move.line;{target_line.id}"
        bank_line._onchange_manual_reconcile_reference()
        direction = -1.0 if target_line.balance > 0 else 1.0
        amount = direction * float(source_edge["partial_amount"])
        amount_currency = direction * float(source_edge["partial_amount_currency"])
        bank_line.manual_amount = amount
        if bank_line.manual_in_currency:
            bank_line.manual_amount_in_currency = amount_currency
            bank_line.previous_manual_amount_in_currency = (
                bank_line.manual_amount_in_currency
            )
        bank_line._onchange_manual_reconcile_vals()
        RebuildAccountImportRun._native_expense_settlement_preserve_operator_amounts(
            bank_line,
            target_line,
            amount,
            amount_currency,
        )

    def run_native_expense_settlement_from_source(self, options):
        """Replay expense bank matching through native statement/OCA APIs.

        Only source reconciliation edges whose counterpart is a current-period
        native expense move are replayed here. Exact bank counterpart lines wholly
        outside that perimeter are preserved for later settlement; only source
        lines split between perimeters retain a bounded aggregate residual.
        """
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
            currencies = self._currency_map(conn)
            partners = self._partner_map(conn, options)
            bank_rows = self._native_expense_settlement_bank_rows(conn, options)
            outside_line_rows = (
                self._native_expense_settlement_outside_line_rows(conn, options)
            )
            edge_rows = self._native_expense_settlement_edge_rows(conn, options)
            source_line_rows = self._native_expense_settlement_source_line_rows(
                conn,
                options,
            )
            target_line_map, blocked_cases = (
                self._native_expense_settlement_target_line_map(
                    source_line_rows,
                    edge_rows,
                    options,
                )
            )
            outside_accounts = {
                account.rebuild_source_id: account
                for account in self.env["account.account"].sudo().search([
                    ("rebuild_source_model", "=", "account.account"),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                    (
                        "rebuild_source_id",
                        "in",
                        sorted({row["account_id"] for row in outside_line_rows})
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
            journals = {
                journal.rebuild_source_id: journal
                for journal in self.env["account.journal"].sudo().search([
                    ("rebuild_source_model", "=", "account.journal"),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                    ("rebuild_source_id", "in", sorted({
                        row["journal_id"] for row in bank_rows
                    }) or [0]),
                ])
            }
            for journal in journals.values():
                if "reconcile_mode" in journal._fields and journal.reconcile_mode != "edit":
                    journal.reconcile_mode = "edit"

            edges_by_bank = defaultdict(list)
            for row in edge_rows:
                edges_by_bank[row["source_bank_statement_line_id"]].append(row)
            outside_lines_by_bank = defaultdict(list)
            for row in outside_line_rows:
                outside_lines_by_bank[
                    row["source_bank_statement_line_id"]
                ].append(row)

            StatementLine = self.env["account.bank.statement.line"].sudo().with_context(
                tracking_disable=True,
                mail_create_nolog=True,
            )
            Partial = self.env["account.partial.reconcile"].sudo()
            created_bank_line_count = 0
            reused_bank_line_count = 0
            created_reconciliation_count = 0
            reused_reconciliation_count = 0
            passed_bank_lines = []
            mismatch_bank_lines = []
            outside_line_mismatches = []

            for row in bank_rows:
                source_bank_line_id = row["id"]
                journal = journals.get(row["journal_id"])
                partner = partners.get(row["partner_id"])
                foreign_currency = currencies.get(row["foreign_currency_id"])
                source_edges = edges_by_bank[source_bank_line_id]
                outside_lines = outside_lines_by_bank[source_bank_line_id]
                missing_outside_accounts = sorted({
                    line["account_id"]
                    for line in outside_lines
                    if line["account_id"] not in outside_accounts
                })
                missing_outside_currencies = sorted({
                    line["currency_id"]
                    for line in outside_lines
                    if line["currency_id"] not in currencies
                })
                source_analytic_ids = set().union(*[
                    self._native_bank_categorization_source_analytic_ids(
                        line["analytic_distribution"],
                    )
                    for line in outside_lines
                ]) if outside_lines else set()
                missing_analytic_ids = sorted(
                    source_analytic_ids - analytic_accounts.keys(),
                )
                if (
                    not journal
                    or missing_outside_accounts
                    or missing_outside_currencies
                    or missing_analytic_ids
                    or any(
                        edge["source_line_id"] not in target_line_map
                        for edge in source_edges
                    )
                ):
                    blocked_cases.append({
                        "source_bank_statement_line_id": source_bank_line_id,
                        "classification": "missing_bank_or_expense_mapping",
                        "missing_journal": not bool(journal),
                        "missing_outside_account_ids": missing_outside_accounts,
                        "missing_outside_currency_ids": missing_outside_currencies,
                        "missing_analytic_ids": missing_analytic_ids,
                        "missing_source_line_ids": [
                            edge["source_line_id"]
                            for edge in source_edges
                            if edge["source_line_id"] not in target_line_map
                        ],
                    })
                    continue
                bank_line = StatementLine.search([
                    (
                        "rebuild_source_model",
                        "=",
                        "account.bank.statement.line.native_expense_settlement",
                    ),
                    ("rebuild_source_id", "=", source_bank_line_id),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ], limit=1)
                existing_partials = Partial.search([
                    (
                        "rebuild_source_model",
                        "=",
                        "account.partial.reconcile.native_expense_settlement",
                    ),
                    (
                        "rebuild_source_id",
                        "in",
                        [edge["source_partial_reconcile_id"] for edge in source_edges],
                    ),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ])
                if bank_line and len(existing_partials) == len(source_edges):
                    reused_bank_line_count += 1
                    reused_reconciliation_count += len(existing_partials)
                elif bank_line:
                    blocked_cases.append({
                        "source_bank_statement_line_id": source_bank_line_id,
                        "classification": "incomplete_existing_native_bank_replay",
                        "source_edge_count": len(source_edges),
                        "mapped_partial_count": len(existing_partials),
                    })
                    continue
                else:
                    reconcile_data_snapshot = None
                    try:
                        with self.env.cr.savepoint():
                            vals = {
                                "journal_id": journal.id,
                                "date": row["date"],
                                "sequence": row["sequence"],
                                "partner_id": partner.id if partner else False,
                                "foreign_currency_id": (
                                    foreign_currency.id if foreign_currency else False
                                ),
                                "account_number": row["account_number"],
                                "partner_name": row["partner_name"],
                                "transaction_type": row["transaction_type"],
                                "payment_ref": row["payment_ref"],
                                "transaction_details": row["transaction_details"],
                                "amount": self._amount(row["amount"]),
                                "amount_currency": self._amount(row["amount_currency"]),
                                "unique_import_id": row["unique_import_id"],
                                "rebuild_import_note": (
                                    "Track B native bank transaction. OCA bank matching replays "
                                    "only current-period expense settlement choices."
                                ),
                                **self._trace_values(
                                    "account.bank.statement.line.native_expense_settlement",
                                    source_bank_line_id,
                                    options,
                                ),
                            }
                            bank_line = StatementLine.with_company(
                                journal.company_id,
                            ).create(vals)
                            auto_partials = [
                                self._native_expense_settlement_related_partial(
                                    target_line_map[edge["source_line_id"]],
                                    bank_line,
                                )
                                for edge in source_edges
                            ]
                            if self._native_expense_settlement_auto_matched(
                                auto_partials,
                            ):
                                if outside_lines:
                                    bank_line.reconcile_data_info = (
                                        bank_line._default_reconcile_data()
                                    )
                            else:
                                for edge in source_edges:
                                    self._native_expense_settlement_add_edge(
                                        bank_line,
                                        target_line_map[edge["source_line_id"]],
                                        edge,
                                    )
                            for outside_line in outside_lines:
                                self._native_bank_replay_add_manual_allocation(
                                    bank_line,
                                    outside_line,
                                    outside_accounts[outside_line["account_id"]],
                                    partners.get(outside_line["partner_id"]),
                                    currencies[outside_line["currency_id"]],
                                    self._native_replay_analytic_distribution(
                                        outside_line["analytic_distribution"],
                                        analytic_accounts,
                                    ),
                                )
                            if not all(auto_partials) or outside_lines:
                                reconcile_data_snapshot = bank_line.reconcile_data_info
                                bank_line.reconcile_bank_line()
                            for edge in source_edges:
                                target_line = target_line_map[edge["source_line_id"]]
                                related = self._native_expense_settlement_related_partial(
                                    target_line,
                                    bank_line,
                                ).filtered(
                                    lambda partial: not partial.rebuild_source_model,
                                )
                                partial = self._native_expense_settlement_single_partial(
                                    related,
                                    edge["source_partial_reconcile_id"],
                                )
                                partial.write({
                                    "rebuild_import_note": (
                                        "Track B partial reconciliation generated by OCA bank "
                                        "matching from the source operator allocation."
                                    ),
                                    **self._trace_values(
                                        "account.partial.reconcile.native_expense_settlement",
                                        edge["source_partial_reconcile_id"],
                                        options,
                                    ),
                                })
                            created_bank_line_count += 1
                            created_reconciliation_count += len(source_edges)
                    except Exception as exc:  # noqa: BLE001 - classify each bank defect.
                        blocked_cases.append({
                            "source_bank_statement_line_id": source_bank_line_id,
                            "classification": "native_bank_matching_error",
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                            "reconcile_data": reconcile_data_snapshot,
                        })
                        continue

                if outside_lines:
                    _outside_target_lines, line_mismatches = (
                        self._native_bank_external_trace_lines(
                            bank_line,
                            outside_lines,
                            outside_accounts,
                            partners,
                            currencies,
                            analytic_accounts,
                            options,
                            trace_model=(
                                "account.move.line.native_bounded_bank_counterpart"
                            ),
                            trace_note=(
                                "Track B bounded bank counterpart preserved exactly "
                                "through OCA Bank Matching for later native settlement."
                            ),
                            strict_line_count=False,
                        )
                    )
                    outside_line_mismatches.extend(line_mismatches)

                mapped_partials = Partial.search([
                    (
                        "rebuild_source_model",
                        "=",
                        "account.partial.reconcile.native_expense_settlement",
                    ),
                    (
                        "rebuild_source_id",
                        "in",
                        [edge["source_partial_reconcile_id"] for edge in source_edges],
                    ),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ])
                source_amounts = {
                    edge["source_partial_reconcile_id"]: round(
                        self._amount(edge["partial_amount"]),
                        2,
                    )
                    for edge in source_edges
                }
                target_amounts = {
                    partial.rebuild_source_id: round(partial.amount, 2)
                    for partial in mapped_partials
                }
                partial_endpoints_match = all(
                    len(
                        self._native_expense_settlement_related_partial(
                            target_line_map[edge["source_line_id"]],
                            bank_line,
                        ).filtered(
                            lambda partial, source_id=edge[
                                "source_partial_reconcile_id"
                            ]: partial.rebuild_source_id == source_id,
                        ),
                    )
                    == 1
                    for edge in source_edges
                )
                source_outside_totals = {
                    str(source_account_id): round(self._amount(balance), 2)
                    for source_account_id, balance in (
                        row["outside_account_totals"] or {}
                    ).items()
                }
                target_outside_totals = (
                    self._native_expense_settlement_outside_account_totals(
                        bank_line,
                        mapped_partials,
                    )
                )
                expected_reconciled = bool(row["is_reconciled"])
                checks = {
                    "source_trace": bank_line.rebuild_source_id == source_bank_line_id,
                    "journal": bank_line.journal_id == journal,
                    "date": bank_line.date == row["date"],
                    "amount": round(bank_line.amount, 2)
                    == round(self._amount(row["amount"]), 2),
                    "amount_currency": round(bank_line.amount_currency, 2)
                    == round(self._amount(row["amount_currency"]), 2),
                    "edge_count": len(mapped_partials) == len(source_edges),
                    "partial_endpoints": partial_endpoints_match,
                    "partial_amounts": target_amounts == source_amounts,
                    "outside_account_totals": target_outside_totals
                    == source_outside_totals,
                    "scope_reconciled_state": bank_line.is_reconciled
                    == expected_reconciled,
                }
                result = {
                    "source_bank_statement_line_id": source_bank_line_id,
                    "target_bank_statement_line_id": bank_line.id,
                    "date": bank_line.date,
                    "payment_mode": row["payment_mode"],
                    "source_edge_count": len(source_edges),
                    "current_scope_complete": bool(row["current_scope_complete"]),
                    "expected_scope_reconciled": expected_reconciled,
                    "target_is_reconciled": bank_line.is_reconciled,
                    "checks": checks,
                }
                if all(checks.values()):
                    passed_bank_lines.append(result)
                else:
                    mismatch_bank_lines.append({
                        **result,
                        "source_partial_amounts": source_amounts,
                        "target_partial_amounts": target_amounts,
                        "source_outside_account_totals": source_outside_totals,
                        "target_outside_account_totals": target_outside_totals,
                    })

            edge_target_lines = self.env["account.move.line"].browse(sorted({
                target_line_map[row["source_line_id"]].id
                for row in edge_rows
                if row["source_line_id"] in target_line_map
            }))
            unreconciled_target_lines = edge_target_lines.filtered(
                lambda line: not line.reconciled,
            )
            company_payments = self.env["account.payment"].sudo().search([
                (
                    "rebuild_source_model",
                    "=",
                    "account.payment.native_expense_replay",
                ),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ])
            employee_expenses = self.env["hr.expense"].sudo().search([
                ("rebuild_source_model", "=", "hr.expense"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("payment_mode", "=", "own_account"),
                ("account_move_id", "!=", False),
            ])
            state_mismatches = []
            if unreconciled_target_lines:
                state_mismatches.append({
                    "classification": "expense_settlement_lines_remain_open",
                    "target_line_ids": unreconciled_target_lines.ids[:20],
                    "count": len(unreconciled_target_lines),
                })
            unpaid_payments = company_payments.filtered(lambda payment: payment.state != "paid")
            if unpaid_payments:
                state_mismatches.append({
                    "classification": "company_expense_payments_not_paid",
                    "target_payment_ids": unpaid_payments.ids[:20],
                    "count": len(unpaid_payments),
                })
            unpaid_employee_expenses = employee_expenses.filtered(
                lambda expense: expense.state != "paid",
            )
            if unpaid_employee_expenses:
                state_mismatches.append({
                    "classification": "employee_expenses_not_paid",
                    "target_expense_ids": unpaid_employee_expenses.ids[:20],
                    "count": len(unpaid_employee_expenses),
                })

            status = (
                "passed"
                if not blocked_cases
                and not mismatch_bank_lines
                and not outside_line_mismatches
                and not state_mismatches
                else "partial"
            )
            payment_mode_counts = defaultdict(int)
            complete_scope_counts = defaultdict(int)
            for row in bank_rows:
                payment_mode_counts[row["payment_mode"]] += 1
                if row["current_scope_complete"]:
                    complete_scope_counts[row["payment_mode"]] += 1
            stats = {
                "classification": "TRACK_B_NATIVE_EXPENSE_SETTLEMENT",
                "date_from": options["date_from"],
                "date_to": options["date_to"],
                "source_company_ids": options["source_company_ids"],
                "source_bank_statement_line_count": len(bank_rows),
                "created_bank_statement_line_count": created_bank_line_count,
                "reused_bank_statement_line_count": reused_bank_line_count,
                "passed_bank_statement_line_count": len(passed_bank_lines),
                "mismatch_bank_statement_line_count": len(mismatch_bank_lines),
                "source_reconciliation_edge_count": len(edge_rows),
                "source_exact_outside_counterpart_count": len(outside_line_rows),
                "outside_counterpart_mismatch_count": len(outside_line_mismatches),
                "created_reconciliation_count": created_reconciliation_count,
                "reused_reconciliation_count": reused_reconciliation_count,
                "source_expense_settlement_line_count": len({
                    row["source_line_id"] for row in edge_rows
                }),
                "settled_target_line_count": len(edge_target_lines),
                "company_payment_count": len(company_payments),
                "paid_company_payment_count": len(
                    company_payments.filtered(lambda payment: payment.state == "paid"),
                ),
                "employee_expense_count": len(employee_expenses),
                "paid_employee_expense_count": len(
                    employee_expenses.filtered(lambda expense: expense.state == "paid"),
                ),
                "payment_mode_bank_line_counts": dict(sorted(payment_mode_counts.items())),
                "complete_scope_bank_line_counts": dict(
                    sorted(complete_scope_counts.items()),
                ),
                "bounded_scope_classification": {
                    "company_account": (
                        "All selected company-expense bank transactions contain only the "
                        "current payment allocation and are fully matched."
                    ),
                    "own_account": (
                        "Employee transfers are grouped. Current-period expense payable "
                        "edges are replayed chronologically; exact outside-only source "
                        "counterparts are preserved for later native reconciliation, and "
                        "only source lines split across perimeters retain a bounded residual."
                    ),
                },
                "passed_bank_line_examples": passed_bank_lines[:20],
                "mismatch_bank_line_examples": mismatch_bank_lines[:20],
                "outside_counterpart_mismatch_examples": (
                    outside_line_mismatches[:20]
                ),
                "blocked_examples": blocked_cases[:20],
                "state_mismatch_examples": state_mismatches[:20],
            }
            self.write({
                "status": status,
                "finished_at": fields.Datetime.now(),
                "imported_bank_statement_line_count": len(passed_bank_lines)
                + len(mismatch_bank_lines),
                "imported_payment_count": len(company_payments),
                "imported_reconciliation_count": created_reconciliation_count
                + reused_reconciliation_count,
                "warning_count": len(blocked_cases)
                + len(mismatch_bank_lines)
                + len(outside_line_mismatches)
                + len(state_mismatches),
                "statistics_json": stats,
                "notes": (
                    "Track B native expense settlement through account.bank.statement.line "
                    "creation and OCA reconcile_bank_line(). Outside-only source counterpart "
                    "lines are preserved exactly for subsequent native settlement."
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

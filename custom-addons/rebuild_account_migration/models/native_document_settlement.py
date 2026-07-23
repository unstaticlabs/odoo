from collections import defaultdict

from odoo import fields, models


class RebuildAccountImportRun(models.Model):
    _inherit = "rebuild.account.import.run"

    def _native_document_settlement_source_line_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            WITH current_documents AS (
                SELECT move.id
                FROM account_move move
                WHERE move.company_id = ANY(%(source_company_ids)s)
                  AND move.state = 'posted'
                  AND move.move_type IN (
                      'out_invoice', 'out_refund', 'in_invoice',
                      'in_refund', 'out_receipt', 'in_receipt'
                  )
                  AND move.date BETWEEN %(date_from)s AND %(date_to)s
            )
            SELECT line.id, line.move_id, line.sequence, line.account_id,
                   line.partner_id, currency.name AS currency_name,
                   line.display_type, line.balance, line.amount_currency
            FROM current_documents document
            JOIN account_move_line line ON line.move_id = document.id
            LEFT JOIN res_currency currency ON currency.id = line.currency_id
            WHERE line.account_id IS NOT NULL
            ORDER BY line.move_id, line.sequence, line.id
            """,
            options,
        )

    def _native_document_settlement_edge_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            WITH current_documents AS (
                SELECT move.id
                FROM account_move move
                WHERE move.company_id = ANY(%(source_company_ids)s)
                  AND move.state = 'posted'
                  AND move.move_type IN (
                      'out_invoice', 'out_refund', 'in_invoice',
                      'in_refund', 'out_receipt', 'in_receipt'
                  )
                  AND move.date BETWEEN %(date_from)s AND %(date_to)s
            ),
            document_lines AS (
                SELECT line.id, line.move_id, line.balance, line.amount_currency,
                       line.amount_residual_currency
                FROM current_documents document
                JOIN account_move_line line ON line.move_id = document.id
                JOIN account_account account ON account.id = line.account_id
                WHERE account.account_type IN (
                    'asset_receivable', 'liability_payable'
                )
            )
            SELECT document_line.move_id AS source_move_id,
                   document_line.id AS source_line_id,
                   document_line.balance AS source_line_balance,
                   document_line.amount_currency AS source_line_amount_currency,
                   document_line.amount_residual_currency
                       AS source_line_amount_residual_currency,
                   partial.id AS source_partial_reconcile_id,
                   partial.full_reconcile_id AS source_full_reconcile_id,
                   partial.amount AS partial_amount,
                   CASE
                       WHEN partial.debit_move_id = document_line.id
                       THEN partial.debit_amount_currency
                       ELSE partial.credit_amount_currency
                   END AS partial_amount_currency,
                   statement_line.id AS source_bank_statement_line_id,
                   statement_move_line.id AS source_bank_move_line_id,
                   statement_move.date AS statement_date
            FROM document_lines document_line
            JOIN account_partial_reconcile partial
              ON partial.debit_move_id = document_line.id
              OR partial.credit_move_id = document_line.id
            JOIN account_move_line statement_move_line
              ON statement_move_line.id = CASE
                   WHEN partial.debit_move_id = document_line.id
                   THEN partial.credit_move_id
                   ELSE partial.debit_move_id
                 END
            JOIN account_bank_statement_line statement_line
              ON statement_line.move_id = statement_move_line.move_id
            JOIN account_move statement_move ON statement_move.id = statement_line.move_id
            ORDER BY statement_move.date, statement_line.id,
                     partial.id, document_line.id
            """,
            options,
        )

    def _native_document_settlement_bank_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            WITH current_documents AS (
                SELECT move.id
                FROM account_move move
                WHERE move.company_id = ANY(%(source_company_ids)s)
                  AND move.state = 'posted'
                  AND move.move_type IN (
                      'out_invoice', 'out_refund', 'in_invoice',
                      'in_refund', 'out_receipt', 'in_receipt'
                  )
                  AND move.date BETWEEN %(date_from)s AND %(date_to)s
            ),
            document_lines AS (
                SELECT line.id
                FROM current_documents document
                JOIN account_move_line line ON line.move_id = document.id
                JOIN account_account account ON account.id = line.account_id
                WHERE account.account_type IN (
                    'asset_receivable', 'liability_payable'
                )
            ),
            selected_statement_lines AS (
                SELECT DISTINCT statement_line.id
                FROM document_lines document_line
                JOIN account_partial_reconcile partial
                  ON partial.debit_move_id = document_line.id
                  OR partial.credit_move_id = document_line.id
                JOIN account_move_line statement_move_line
                  ON statement_move_line.id = CASE
                       WHEN partial.debit_move_id = document_line.id
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
                           WHERE document_line.id IS NOT NULL
                       ) AS current_partial_count,
                       count(partial.id) FILTER (
                           WHERE partial.id IS NOT NULL
                             AND document_line.id IS NULL
                       ) AS outside_partial_count
                FROM nonliquidity_lines nonliquidity
                LEFT JOIN account_partial_reconcile partial
                  ON partial.debit_move_id = nonliquidity.move_line_id
                  OR partial.credit_move_id = nonliquidity.move_line_id
                LEFT JOIN document_lines document_line
                  ON document_line.id = CASE
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
                   liquidity.balance AS liquidity_balance,
                   liquidity.amount_currency AS liquidity_amount_currency,
                   liquidity.currency_id AS liquidity_currency_id,
                   statement_move.date, statement_move.name AS move_name,
                   COALESCE(scope.current_scope_complete, false) AS current_scope_complete
            FROM selected_statement_lines selected
            JOIN account_bank_statement_line statement_line
              ON statement_line.id = selected.id
            JOIN account_move statement_move ON statement_move.id = statement_line.move_id
            JOIN account_journal statement_journal
              ON statement_journal.id = statement_line.journal_id
            JOIN LATERAL (
                SELECT move_line.balance, move_line.amount_currency,
                       move_line.currency_id
                FROM account_move_line move_line
                WHERE move_line.move_id = statement_line.move_id
                  AND move_line.account_id = statement_journal.default_account_id
                ORDER BY move_line.id
                LIMIT 1
            ) liquidity ON true
            LEFT JOIN statement_scope scope ON scope.statement_line_id = statement_line.id
            ORDER BY statement_move.date, statement_line.id
            """,
            options,
        )

    def _native_document_settlement_outside_line_rows(self, conn, options):
        """Return exact bank counterparts wholly outside the document perimeter."""
        return self._fetchall(
            conn,
            """
            WITH current_documents AS (
                SELECT move.id
                FROM account_move move
                WHERE move.company_id = ANY(%(source_company_ids)s)
                  AND move.state = 'posted'
                  AND move.move_type IN (
                      'out_invoice', 'out_refund', 'in_invoice',
                      'in_refund', 'out_receipt', 'in_receipt'
                  )
                  AND move.date BETWEEN %(date_from)s AND %(date_to)s
            ),
            document_lines AS (
                SELECT line.id
                FROM current_documents document
                JOIN account_move_line line ON line.move_id = document.id
                JOIN account_account account ON account.id = line.account_id
                WHERE account.account_type IN (
                    'asset_receivable', 'liability_payable'
                )
            ),
            selected_statement_lines AS (
                SELECT DISTINCT statement_line.id, statement_line.move_id,
                       statement_line.journal_id
                FROM document_lines document_line
                JOIN account_partial_reconcile partial
                  ON partial.debit_move_id = document_line.id
                  OR partial.credit_move_id = document_line.id
                JOIN account_move_line statement_move_line
                  ON statement_move_line.id = CASE
                       WHEN partial.debit_move_id = document_line.id
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
                           WHERE document_line.id IS NOT NULL
                       ) AS current_partial_count
                FROM selected_statement_lines selected
                JOIN account_journal journal ON journal.id = selected.journal_id
                JOIN account_move_line line ON line.move_id = selected.move_id
                LEFT JOIN account_partial_reconcile partial
                  ON partial.debit_move_id = line.id
                  OR partial.credit_move_id = line.id
                LEFT JOIN document_lines document_line
                  ON document_line.id = CASE
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
    def _native_document_settlement_open_bank_lines(bank_line, target_line):
        _liquidity_lines, suspense_lines, other_lines = bank_line._seek_for_lines()
        return (suspense_lines | other_lines).filtered(
            lambda line: (
                not line.reconciled
                and line.account_id == target_line.account_id
                and line.balance * target_line.balance < 0
            ),
        )

    def _native_document_settlement_apply_existing_edge(
        self,
        bank_line,
        target_line,
        source_edge,
        options,
    ):
        related = self._native_expense_settlement_related_partial(
            target_line,
            bank_line,
        ).filtered(
            lambda partial: not partial.rebuild_source_model,
        )
        if not related:
            open_bank_lines = bank_line.line_ids.filtered(
                lambda line: (
                    not line.reconciled
                    and line.account_id == target_line.account_id
                    and line.rebuild_source_model
                    == "account.move.line.native_bounded_bank_counterpart"
                    and line.rebuild_source_id
                    == source_edge["source_bank_move_line_id"]
                ),
            )
            if not open_bank_lines:
                open_bank_lines = self._native_document_settlement_open_bank_lines(
                    bank_line,
                    target_line,
                )
            if len(open_bank_lines) != 1:
                message = (
                    "Expected one open bank allocation line for document source edge "
                    f"{source_edge['source_partial_reconcile_id']}, got "
                    f"{len(open_bank_lines)}"
                )
                raise ValueError(message)
            (open_bank_lines | target_line).reconcile()
            related = self._native_expense_settlement_related_partial(
                target_line,
                bank_line,
            ).filtered(
                lambda partial: not partial.rebuild_source_model,
            )
        partial = self._native_expense_settlement_single_partial(
            related,
            source_edge["source_partial_reconcile_id"],
        )
        partial.write({
            "rebuild_import_note": (
                "Track B document settlement applied through native General "
                "Reconciliation against a prior bounded bank allocation."
            ),
            **self._trace_values(
                "account.partial.reconcile.native_document_settlement",
                source_edge["source_partial_reconcile_id"],
                options,
            ),
        })
        return partial

    def run_native_document_settlement_from_source(self, options):
        """Replay current-document bank allocations through native OCA APIs."""
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
            bank_rows = self._native_document_settlement_bank_rows(conn, options)
            outside_line_rows = (
                self._native_document_settlement_outside_line_rows(conn, options)
            )
            edge_rows = self._native_document_settlement_edge_rows(conn, options)
            source_line_rows = self._native_document_settlement_source_line_rows(
                conn,
                options,
            )
            target_line_map, blocked_cases = (
                self._native_expense_settlement_target_line_map(
                    source_line_rows,
                    edge_rows,
                    options,
                    target_move_models=[
                        "account.move.native_engine_replay",
                        "account.move.native_expense_replay",
                    ],
                    input_trace_model=(
                        "account.move.line.native_document_settlement_input"
                    ),
                    input_trace_note=(
                        "Track B source document due line selected as an OCA "
                        "bank-matching candidate for native document settlement."
                    ),
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
            edges_by_source_line = defaultdict(list)
            for row in edge_rows:
                edges_by_bank[row["source_bank_statement_line_id"]].append(row)
                edges_by_source_line[row["source_line_id"]].append(row)
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
            existing_bank_general_reconcile_count = 0
            historical_journal_countervalue_count = sum(
                bool(
                    not row["foreign_currency_id"]
                    and journals.get(row["journal_id"])
                    and journals[row["journal_id"]].currency_id
                    != journals[row["journal_id"]].company_id.currency_id,
                )
                for row in bank_rows
            )
            passed_bank_lines = []
            mismatch_bank_lines = []
            outside_line_mismatches = []

            for row in bank_rows:
                source_bank_line_id = row["id"]
                journal = journals.get(row["journal_id"])
                partner = partners.get(row["partner_id"])
                foreign_currency = currencies.get(row["foreign_currency_id"])
                historical_journal_countervalue = bool(
                    not foreign_currency
                    and journal
                    and journal.currency_id
                    and journal.currency_id != journal.company_id.currency_id,
                )
                effective_foreign_currency = (
                    journal.company_id.currency_id
                    if historical_journal_countervalue
                    else foreign_currency
                )
                effective_amount_currency = (
                    self._amount(row["liquidity_balance"])
                    if historical_journal_countervalue
                    else self._amount(row["amount_currency"])
                )
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
                        "classification": "missing_document_bank_mapping",
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
                    ("rebuild_source_id", "=", source_bank_line_id),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                    (
                        "rebuild_source_model",
                        "in",
                        [
                            "account.bank.statement.line.native_expense_settlement",
                            "account.bank.statement.line.native_document_settlement",
                        ],
                    ),
                ], limit=1)
                source_partial_ids = [
                    edge["source_partial_reconcile_id"] for edge in source_edges
                ]
                existing_partials = Partial.search([
                    ("rebuild_source_id", "in", source_partial_ids),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                    (
                        "rebuild_source_model",
                        "in",
                        [
                            "account.partial.reconcile.native_expense_settlement",
                            "account.partial.reconcile.native_document_settlement",
                        ],
                    ),
                ])
                mapped_source_ids = set(existing_partials.mapped("rebuild_source_id"))
                missing_edges = [
                    edge
                    for edge in source_edges
                    if edge["source_partial_reconcile_id"] not in mapped_source_ids
                ]
                if bank_line:
                    reused_bank_line_count += 1
                    reused_reconciliation_count += len(existing_partials)
                    try:
                        applied_existing_count = 0
                        with self.env.cr.savepoint():
                            for edge in missing_edges:
                                self._native_document_settlement_apply_existing_edge(
                                    bank_line,
                                    target_line_map[edge["source_line_id"]],
                                    edge,
                                    options,
                                )
                                applied_existing_count += 1
                        created_reconciliation_count += applied_existing_count
                        existing_bank_general_reconcile_count += applied_existing_count
                    except Exception as exc:  # noqa: BLE001 - classify each bank defect.
                        blocked_cases.append({
                            "source_bank_statement_line_id": source_bank_line_id,
                            "classification": "existing_bank_document_settlement_error",
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                        })
                        continue
                else:
                    try:
                        with self.env.cr.savepoint():
                            vals = {
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
                                "payment_ref": False,
                                "transaction_details": row["transaction_details"],
                                "amount": self._amount(row["amount"]),
                                "amount_currency": effective_amount_currency,
                                "unique_import_id": row["unique_import_id"],
                                "rebuild_import_note": (
                                    "Track B native bank transaction for current-period "
                                    "commercial document settlement."
                                ),
                                **self._trace_values(
                                    "account.bank.statement.line.native_document_settlement",
                                    source_bank_line_id,
                                    options,
                                ),
                            }
                            bank_line = StatementLine.with_company(
                                journal.company_id,
                            ).create(vals)
                            bank_line.payment_ref = row["payment_ref"]
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
                                        "Track B partial reconciliation generated by OCA "
                                        "from a source commercial-document bank allocation."
                                    ),
                                    **self._trace_values(
                                        "account.partial.reconcile.native_document_settlement",
                                        edge["source_partial_reconcile_id"],
                                        options,
                                    ),
                                })
                        created_bank_line_count += 1
                        created_reconciliation_count += len(source_edges)
                    except Exception as exc:  # noqa: BLE001 - classify each bank defect.
                        blocked_cases.append({
                            "source_bank_statement_line_id": source_bank_line_id,
                            "classification": "native_document_bank_matching_error",
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
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
                    ("rebuild_source_id", "in", source_partial_ids),
                    ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                    (
                        "rebuild_source_model",
                        "in",
                        [
                            "account.partial.reconcile.native_expense_settlement",
                            "account.partial.reconcile.native_document_settlement",
                        ],
                    ),
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
                source_currency_amounts = {
                    edge["source_partial_reconcile_id"]: round(
                        self._amount(edge["partial_amount_currency"]),
                        2,
                    )
                    for edge in source_edges
                }
                target_currency_amounts = {}
                for edge in source_edges:
                    target_line = target_line_map[edge["source_line_id"]]
                    partial = mapped_partials.filtered(
                        lambda item, source_id=edge[
                            "source_partial_reconcile_id"
                        ]: item.rebuild_source_id == source_id,
                    )
                    if len(partial) == 1:
                        target_currency_amounts[edge["source_partial_reconcile_id"]] = (
                            round(
                                partial.debit_amount_currency
                                if partial.debit_move_id == target_line
                                else partial.credit_amount_currency,
                                2,
                            )
                        )
                endpoint_checks = all(
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
                checks = {
                    "source_trace": bank_line.rebuild_source_id == source_bank_line_id,
                    "journal": bank_line.journal_id == journal,
                    "date": bank_line.date == row["date"],
                    "amount": round(bank_line.amount, 2)
                    == round(self._amount(row["amount"]), 2),
                    "foreign_currency": bank_line.foreign_currency_id.id
                    == (
                        False
                        if bank_line.rebuild_source_model
                        == "account.bank.statement.line.native_expense_settlement"
                        else effective_foreign_currency.id
                        if effective_foreign_currency
                        else False
                    ),
                    "amount_currency": round(bank_line.amount_currency, 2)
                    == round(
                        self._amount(row["amount_currency"])
                        if bank_line.rebuild_source_model
                        == "account.bank.statement.line.native_expense_settlement"
                        else effective_amount_currency,
                        2,
                    ),
                    "edge_count": len(mapped_partials) == len(source_edges),
                    "partial_endpoints": endpoint_checks,
                    "partial_amounts": target_amounts == source_amounts,
                    "partial_currency_amounts": (
                        target_currency_amounts == source_currency_amounts
                    ),
                    "complete_scope_reconciled": (
                        not row["current_scope_complete"] or bank_line.is_reconciled
                    ),
                }
                result = {
                    "source_bank_statement_line_id": source_bank_line_id,
                    "target_bank_statement_line_id": bank_line.id,
                    "date": bank_line.date,
                    "source_edge_count": len(source_edges),
                    "current_scope_complete": bool(row["current_scope_complete"]),
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
                        "source_partial_currency_amounts": source_currency_amounts,
                        "target_partial_currency_amounts": target_currency_amounts,
                    })

            line_residual_mismatches = []
            downstream_settled_line_count = 0
            for source_line_id, source_edges in edges_by_source_line.items():
                target_line = target_line_map.get(source_line_id)
                if not target_line:
                    continue
                source_amount_currency = abs(
                    self._amount(source_edges[0]["source_line_amount_currency"]),
                )
                source_bank_amount_currency = sum(
                    self._amount(edge["partial_amount_currency"])
                    for edge in source_edges
                )
                expected_residual = round(
                    max(source_amount_currency - source_bank_amount_currency, 0.0),
                    2,
                )
                source_final_residual = round(abs(self._amount(
                    source_edges[0]["source_line_amount_residual_currency"],
                )), 2)
                target_residual = round(abs(target_line.amount_residual_currency), 2)
                downstream_settled_line_count += int(
                    target_residual == source_final_residual
                    and target_residual != expected_residual,
                )
                if target_residual not in {expected_residual, source_final_residual}:
                    line_residual_mismatches.append({
                        "source_line_id": source_line_id,
                        "target_line_id": target_line.id,
                        "source_amount_currency": round(source_amount_currency, 2),
                        "source_bank_amount_currency": round(
                            source_bank_amount_currency,
                            2,
                        ),
                        "expected_bank_stage_currency_residual": expected_residual,
                        "source_final_currency_residual": source_final_residual,
                        "target_currency_residual": target_residual,
                    })

            status = (
                "passed"
                if not blocked_cases
                and not mismatch_bank_lines
                and not outside_line_mismatches
                and not line_residual_mismatches
                else "partial"
            )
            complete_scope_count = sum(
                bool(row["current_scope_complete"]) for row in bank_rows
            )
            stats = {
                "classification": "TRACK_B_NATIVE_DOCUMENT_BANK_SETTLEMENT",
                "date_from": options["date_from"],
                "date_to": options["date_to"],
                "source_company_ids": options["source_company_ids"],
                "source_bank_statement_line_count": len(bank_rows),
                "complete_scope_bank_statement_line_count": complete_scope_count,
                "bounded_scope_bank_statement_line_count": (
                    len(bank_rows) - complete_scope_count
                ),
                "created_bank_statement_line_count": created_bank_line_count,
                "reused_bank_statement_line_count": reused_bank_line_count,
                "passed_bank_statement_line_count": len(passed_bank_lines),
                "mismatch_bank_statement_line_count": len(mismatch_bank_lines),
                "source_reconciliation_edge_count": len(edge_rows),
                "source_exact_outside_counterpart_count": len(outside_line_rows),
                "outside_counterpart_mismatch_count": len(outside_line_mismatches),
                "created_reconciliation_count": created_reconciliation_count,
                "reused_reconciliation_count": reused_reconciliation_count,
                "existing_bank_general_reconcile_count": (
                    existing_bank_general_reconcile_count
                ),
                "historical_journal_countervalue_count": (
                    historical_journal_countervalue_count
                ),
                "source_document_settlement_line_count": len(edges_by_source_line),
                "line_residual_mismatch_count": len(line_residual_mismatches),
                "downstream_settled_line_count": downstream_settled_line_count,
                "bounded_scope_classification": (
                    "All current-document bank edges are applied. Bank lines that also "
                    "contain outside-only allocations preserve their exact source account, "
                    "partner, currency and amount detail for later native reconciliation; "
                    "only source lines split across perimeters retain a bounded residual."
                ),
                "passed_bank_line_examples": passed_bank_lines[:20],
                "mismatch_bank_line_examples": mismatch_bank_lines[:20],
                "outside_counterpart_mismatch_examples": (
                    outside_line_mismatches[:20]
                ),
                "line_residual_mismatch_examples": line_residual_mismatches[:20],
                "blocked_examples": blocked_cases[:20],
            }
            self.write({
                "status": status,
                "finished_at": fields.Datetime.now(),
                "imported_bank_statement_line_count": len(passed_bank_lines)
                + len(mismatch_bank_lines),
                "imported_reconciliation_count": created_reconciliation_count
                + reused_reconciliation_count,
                "warning_count": len(blocked_cases)
                + len(mismatch_bank_lines)
                + len(outside_line_mismatches)
                + len(line_residual_mismatches),
                "statistics_json": stats,
                "notes": (
                    "Track B commercial-document bank settlement through native statement "
                    "creation, OCA bank matching and native General Reconciliation for one "
                    "edge extending a prior bounded employee-transfer allocation."
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

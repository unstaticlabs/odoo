from collections import defaultdict

from odoo import fields, models


class RebuildAccountImportRun(models.Model):
    _inherit = "rebuild.account.import.run"

    def _native_general_reconciliation_document_rows(self, conn, options):
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

    def _native_general_reconciliation_edge_rows(self, conn, options):
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
            selected AS (
                SELECT partial.*,
                       debit_document.id AS debit_document_line_id,
                       credit_document.id AS credit_document_line_id
                FROM account_partial_reconcile partial
                JOIN account_move_line debit_line
                  ON debit_line.id = partial.debit_move_id
                JOIN account_move_line credit_line
                  ON credit_line.id = partial.credit_move_id
                LEFT JOIN document_lines debit_document
                  ON debit_document.id = debit_line.id
                LEFT JOIN document_lines credit_document
                  ON credit_document.id = credit_line.id
                LEFT JOIN account_bank_statement_line debit_bank
                  ON debit_bank.move_id = debit_line.move_id
                LEFT JOIN account_bank_statement_line credit_bank
                  ON credit_bank.move_id = credit_line.move_id
                WHERE (debit_document.id IS NOT NULL
                       OR credit_document.id IS NOT NULL)
                  AND debit_bank.id IS NULL
                  AND credit_bank.id IS NULL
            )
            SELECT selected.id AS source_partial_reconcile_id,
                   selected.full_reconcile_id AS source_full_reconcile_id,
                   selected.amount AS partial_amount,
                   selected.debit_amount_currency,
                   selected.credit_amount_currency,
                   selected.max_date,
                   debit_line.id AS debit_source_line_id,
                   debit_line.move_id AS debit_source_move_id,
                   credit_line.id AS credit_source_line_id,
                   credit_line.move_id AS credit_source_move_id,
                   selected.debit_document_line_id IS NOT NULL
                       AS debit_is_document,
                   selected.credit_document_line_id IS NOT NULL
                       AS credit_is_document,
                   CASE
                       WHEN selected.debit_document_line_id IS NOT NULL
                       THEN debit_line.id
                       ELSE credit_line.id
                   END AS document_source_line_id,
                   CASE
                       WHEN selected.debit_document_line_id IS NOT NULL
                       THEN credit_line.id
                       ELSE debit_line.id
                   END AS other_source_line_id,
                   CASE
                       WHEN selected.debit_document_line_id IS NOT NULL
                       THEN credit_line.move_id
                       ELSE debit_line.move_id
                   END AS other_source_move_id,
                   CASE
                       WHEN selected.debit_document_line_id IS NOT NULL
                       THEN credit_journal.code
                       ELSE debit_journal.code
                   END AS other_journal_code,
                   CASE
                       WHEN selected.debit_document_line_id IS NOT NULL
                            AND selected.credit_document_line_id IS NOT NULL
                       THEN 'document_netting'
                       WHEN CASE
                           WHEN selected.debit_document_line_id IS NOT NULL
                           THEN credit_journal.code
                           ELSE debit_journal.code
                       END = 'EXCH'
                       THEN 'exchange_difference'
                       WHEN CASE
                           WHEN selected.debit_document_line_id IS NOT NULL
                           THEN credit_journal.code
                           ELSE debit_journal.code
                       END IN ('CCAVV', 'MISC')
                       THEN 'manual_general_entry'
                       ELSE 'unsupported_nonbank_counterpart'
                   END AS reconciliation_kind,
                   other_line.account_id AS other_account_id,
                   other_line.partner_id AS other_partner_id,
                   other_line.currency_id AS other_currency_id,
                   other_line.balance AS other_balance,
                   other_line.amount_currency AS other_amount_currency,
                   balancing.id AS balancing_source_line_id,
                   balancing.account_id AS balancing_account_id,
                   balancing.partner_id AS balancing_partner_id,
                   balancing.currency_id AS balancing_currency_id,
                   balancing.account_type AS balancing_account_type,
                   balancing.balance AS balancing_balance,
                   balancing.amount_currency AS balancing_amount_currency
            FROM selected
            JOIN account_move_line debit_line
              ON debit_line.id = selected.debit_move_id
            JOIN account_move debit_move ON debit_move.id = debit_line.move_id
            JOIN account_journal debit_journal
              ON debit_journal.id = debit_move.journal_id
            JOIN account_move_line credit_line
              ON credit_line.id = selected.credit_move_id
            JOIN account_move credit_move ON credit_move.id = credit_line.move_id
            JOIN account_journal credit_journal
              ON credit_journal.id = credit_move.journal_id
            JOIN account_move_line other_line ON other_line.id = CASE
                WHEN selected.debit_document_line_id IS NOT NULL
                THEN credit_line.id
                ELSE debit_line.id
            END
            LEFT JOIN LATERAL (
                SELECT candidate.*, candidate_account.account_type
                FROM account_move_line candidate
                JOIN account_account candidate_account
                  ON candidate_account.id = candidate.account_id
                WHERE candidate.move_id = other_line.move_id
                  AND candidate.id != other_line.id
                  AND round((candidate.balance + other_line.balance)::numeric, 2) = 0
                ORDER BY
                    CASE WHEN candidate_account.account_type IN ('income', 'expense')
                         THEN 0 ELSE 1 END,
                    candidate.sequence,
                    candidate.id
                LIMIT 1
            ) balancing ON true
            ORDER BY selected.max_date, selected.id
            """,
            options,
        )

    def _native_general_reconciliation_move_rows(
        self,
        conn,
        options,
        source_move_ids,
    ):
        if not source_move_ids:
            return []
        return self._fetchall(
            conn,
            """
            SELECT move.id, move.journal_id, move.company_id, move.date,
                   move.ref, move.partner_id
            FROM account_move move
            WHERE move.id = ANY(%(source_move_ids)s)
            ORDER BY move.date, move.id
            """,
            {**options, "source_move_ids": source_move_ids},
        )

    def _native_general_reconciliation_standalone_move_ids(
        self,
        conn,
        options,
        edge_move_ids,
        downstream_bank_move_ids,
    ):
        rows = self._fetchall(
            conn,
            """
            SELECT move.id, move.date, journal.code, move.ref
            FROM account_move move
            JOIN account_journal journal ON journal.id = move.journal_id
            LEFT JOIN account_bank_statement_line statement_line
              ON statement_line.move_id = move.id
            WHERE move.company_id = ANY(%(source_company_ids)s)
              AND move.state = 'posted'
              AND move.move_type = 'entry'
              AND move.date BETWEEN %(date_from)s AND %(date_to)s
              AND journal.type = 'general'
              AND journal.code NOT IN ('CABA', 'EXCH')
              AND statement_line.id IS NULL
            ORDER BY move.date, move.id
            """,
            options,
        )
        candidate_ids = [row["id"] for row in rows]
        represented = self.env["account.move"].with_context(
            active_test=False,
        ).search([
            ("rebuild_source_id", "in", candidate_ids or [0]),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("rebuild_source_model", "!=", False),
        ])
        represented_by_source_id = {}
        for move in represented:
            if move.rebuild_source_id in represented_by_source_id:
                raise ValueError(
                    "Source move %s has duplicate target representations."
                    % move.rebuild_source_id,
                )
            represented_by_source_id[move.rebuild_source_id] = move

        edge_move_ids = set(edge_move_ids)
        downstream_bank_move_ids = set(downstream_bank_move_ids)
        standalone_rows = []
        downstream_bank_rows = []
        for row in rows:
            if row["id"] in edge_move_ids:
                continue
            if row["id"] in downstream_bank_move_ids:
                downstream_bank_rows.append(row)
                continue
            existing = represented_by_source_id.get(row["id"])
            if (
                not existing
                or existing.rebuild_source_model
                == "account.move.native_general_replay"
            ):
                standalone_rows.append(row)
        return [row["id"] for row in standalone_rows], {
            "source_operator_general_entry_count": len(rows),
            "edge_general_entry_count": len(edge_move_ids),
            "downstream_bank_general_entry_count": len(downstream_bank_rows),
            "standalone_general_entry_count": len(standalone_rows),
            "standalone_general_entry_examples": standalone_rows[:20],
        }

    def _native_general_reconciliation_line_rows(
        self,
        conn,
        options,
        source_move_ids,
    ):
        if not source_move_ids:
            return []
        return self._fetchall(
            conn,
            """
            SELECT line.id, line.move_id, line.sequence, line.account_id,
                   line.partner_id, line.currency_id, line.name,
                   line.balance, line.amount_currency, line.date_maturity,
                   line.analytic_distribution
            FROM account_move_line line
            WHERE line.move_id = ANY(%(source_move_ids)s)
              AND line.account_id IS NOT NULL
            ORDER BY line.move_id, line.sequence, line.id
            """,
            {**options, "source_move_ids": source_move_ids},
        )

    def _native_general_reconciliation_payment_state_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT move.id, move.move_type, move.payment_state
            FROM account_move move
            WHERE move.company_id = ANY(%(source_company_ids)s)
              AND move.state = 'posted'
              AND move.move_type IN (
                  'out_invoice', 'out_refund', 'in_invoice',
                  'in_refund', 'out_receipt', 'in_receipt'
              )
              AND move.date BETWEEN %(date_from)s AND %(date_to)s
            ORDER BY move.id
            """,
            options,
        )

    @staticmethod
    def _native_general_reconciliation_related_partial(debit_line, credit_line):
        return (debit_line.matched_credit_ids | debit_line.matched_debit_ids).filtered(
            lambda partial: (
                partial.debit_move_id == debit_line
                and partial.credit_move_id == credit_line
            )
            or (
                partial.debit_move_id == credit_line
                and partial.credit_move_id == debit_line
            ),
        )

    @staticmethod
    def _native_general_reconciliation_single_created_partial(
        created,
        source_partial_id,
    ):
        if len(created) != 1:
            message = (
                "Expected one native General Reconciliation partial "
                f"for source {source_partial_id}, got {len(created)}"
            )
            raise ValueError(message)
        return created

    def _native_general_reconciliation_maps(
        self,
        conn,
        options,
        move_rows,
        line_rows,
    ):
        currencies = self._currency_map(conn)
        partners = self._partner_map(conn, options)
        journal_source_ids = sorted({row["journal_id"] for row in move_rows})
        account_source_ids = sorted({row["account_id"] for row in line_rows})
        journals = {
            journal.rebuild_source_id: journal
            for journal in self.env["account.journal"].sudo().search([
                ("rebuild_source_model", "=", "account.journal"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", journal_source_ids or [0]),
            ])
        }
        accounts = {
            account.rebuild_source_id: account
            for account in self.env["account.account"].sudo().search([
                ("rebuild_source_model", "=", "account.account"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", account_source_ids or [0]),
            ])
        }
        return journals, accounts, partners, currencies

    def _native_general_reconciliation_manual_moves(
        self,
        options,
        move_rows,
        line_rows,
        journals,
        accounts,
        partners,
        currencies,
        analytic_accounts=None,
    ):
        Move = self.env["account.move"].sudo().with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        lines_by_move = defaultdict(list)
        for row in line_rows:
            lines_by_move[row["move_id"]].append(row)
        created_count = 0
        reused_count = 0
        blocked = []
        moves = {}
        for row in move_rows:
            source_move_id = row["id"]
            journal = journals.get(row["journal_id"])
            source_lines = lines_by_move[source_move_id]
            missing_account_ids = sorted({
                line["account_id"]
                for line in source_lines
                if line["account_id"] not in accounts
            })
            if not journal or missing_account_ids:
                blocked.append({
                    "source_move_id": source_move_id,
                    "classification": "missing_general_entry_configuration",
                    "missing_journal": not bool(journal),
                    "missing_account_ids": missing_account_ids,
                })
                continue
            move = Move.search([
                ("rebuild_source_model", "=", "account.move.native_general_replay"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "=", source_move_id),
            ], limit=1)
            if move:
                reused_count += 1
                moves[source_move_id] = move
                continue
            commands = []
            for source_line in source_lines:
                balance = self._amount(source_line["balance"])
                partner = partners.get(source_line["partner_id"])
                currency = currencies.get(source_line["currency_id"])
                analytic_distribution = False
                if analytic_accounts is not None:
                    analytic_distribution = (
                        self._native_replay_analytic_distribution(
                            source_line["analytic_distribution"],
                            analytic_accounts,
                        )
                    )
                commands.append((0, 0, {
                    "name": source_line["name"] or "/",
                    "sequence": source_line["sequence"],
                    "account_id": accounts[source_line["account_id"]].id,
                    "partner_id": partner.id if partner else False,
                    "currency_id": currency.id if currency else False,
                    "amount_currency": self._amount(
                        source_line["amount_currency"],
                    ),
                    "debit": balance if balance > 0 else 0.0,
                    "credit": -balance if balance < 0 else 0.0,
                    "date_maturity": source_line["date_maturity"],
                    "analytic_distribution": analytic_distribution,
                    "rebuild_import_note": (
                        "Track B native manual general-entry input for document "
                        "settlement and General Reconciliation."
                    ),
                    **self._trace_values(
                        "account.move.line.native_general_replay",
                        source_line["id"],
                        options,
                    ),
                }))
            move = Move.with_company(journal.company_id).create({
                "move_type": "entry",
                "journal_id": journal.id,
                "date": row["date"],
                "ref": row["ref"],
                "partner_id": (
                    partners[row["partner_id"]].id
                    if row["partner_id"] in partners
                    else False
                ),
                "line_ids": commands,
                "rebuild_import_note": (
                    "Track B manual shareholder, compensation or clearing entry "
                    "posted through the native journal-entry workflow."
                ),
                **self._trace_values(
                    "account.move.native_general_replay",
                    source_move_id,
                    options,
                ),
            })
            move.action_post()
            created_count += 1
            moves[source_move_id] = move
        return moves, created_count, reused_count, blocked

    def _native_general_reconciliation_validate_manual_moves(
        self,
        options,
        move_rows,
        line_rows,
        moves,
    ):
        target_lines = {
            line.rebuild_source_id: line
            for line in self.env["account.move.line"].sudo().search([
                ("rebuild_source_model", "=", "account.move.line.native_general_replay"),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "in", [row["id"] for row in line_rows] or [0]),
            ])
        }
        source_lines_by_move = defaultdict(list)
        for row in line_rows:
            source_lines_by_move[row["move_id"]].append(row)
        mismatches = []
        for row in move_rows:
            move = moves.get(row["id"])
            if not move:
                continue
            checks = {
                "posted": move.state == "posted",
                "date": move.date == row["date"],
                "journal": move.journal_id.rebuild_source_id == row["journal_id"],
                "ref": (move.ref or "") == (row["ref"] or ""),
                "balanced": round(sum(move.line_ids.mapped("balance")), 2) == 0.0,
                "line_count": len(move.line_ids)
                == len(source_lines_by_move[row["id"]]),
            }
            line_checks = []
            for source_line in source_lines_by_move[row["id"]]:
                target_line = target_lines.get(source_line["id"])
                line_checks.append(bool(target_line) and all([
                    target_line.move_id == move,
                    target_line.account_id.rebuild_source_id
                    == source_line["account_id"],
                    (target_line.partner_id.rebuild_source_id or None)
                    == (source_line["partner_id"] or None),
                    round(target_line.balance, 2)
                    == round(self._amount(source_line["balance"]), 2),
                    round(target_line.amount_currency, 2)
                    == round(self._amount(source_line["amount_currency"]), 2),
                ]))
            checks["line_effects"] = all(line_checks)
            if not all(checks.values()):
                mismatches.append({
                    "source_move_id": row["id"],
                    "target_move_id": move.id,
                    "checks": checks,
                })
        return target_lines, mismatches

    def _native_general_reconciliation_apply_inputs(
        self,
        options,
        input_edges,
        target_lines,
    ):
        Partial = self.env["account.partial.reconcile"].sudo()
        created_count = 0
        reused_count = 0
        rounding_difference_count = 0
        blocked = []
        mismatches = []
        for edge in input_edges:
            source_partial_id = edge["source_partial_reconcile_id"]
            debit_line = target_lines.get(edge["debit_source_line_id"])
            credit_line = target_lines.get(edge["credit_source_line_id"])
            if not debit_line or not credit_line:
                blocked.append({
                    "source_partial_reconcile_id": source_partial_id,
                    "classification": "missing_general_reconciliation_endpoint",
                    "missing_debit": not bool(debit_line),
                    "missing_credit": not bool(credit_line),
                })
                continue
            partial = Partial.search([
                (
                    "rebuild_source_model",
                    "=",
                    "account.partial.reconcile.native_general_reconciliation",
                ),
                ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
                ("rebuild_source_id", "=", source_partial_id),
            ], limit=1)
            if partial:
                reused_count += 1
            else:
                try:
                    with self.env.cr.savepoint():
                        existing = self._native_general_reconciliation_related_partial(
                            debit_line,
                            credit_line,
                        )
                        (debit_line | credit_line).reconcile()
                        created = (
                            self._native_general_reconciliation_related_partial(
                                debit_line,
                                credit_line,
                            )
                            - existing
                        ).filtered(lambda item: not item.rebuild_source_model)
                        partial = (
                            self._native_general_reconciliation_single_created_partial(
                                created,
                                source_partial_id,
                            )
                        )
                        partial.write({
                            "rebuild_import_note": (
                                "Track B partial created through native General "
                                "Reconciliation for a manual entry or document netting."
                            ),
                            **self._trace_values(
                                "account.partial.reconcile.native_general_reconciliation",
                                source_partial_id,
                                options,
                            ),
                        })
                    created_count += 1
                except Exception as exc:  # noqa: BLE001 - classify source edge.
                    blocked.append({
                        "source_partial_reconcile_id": source_partial_id,
                        "classification": "general_reconciliation_error",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    })
                    continue
            amount_difference = round(
                partial.amount - self._amount(edge["partial_amount"]),
                2,
            )
            rounding_difference_count += int(bool(amount_difference))
            checks = {
                "amount_within_native_rounding": abs(amount_difference) <= 0.01,
                "debit_endpoint": partial.debit_move_id == debit_line,
                "credit_endpoint": partial.credit_move_id == credit_line,
                "debit_amount_currency": round(
                    partial.debit_amount_currency,
                    2,
                )
                == round(self._amount(edge["debit_amount_currency"]), 2),
                "credit_amount_currency": round(
                    partial.credit_amount_currency,
                    2,
                )
                == round(self._amount(edge["credit_amount_currency"]), 2),
            }
            if not all(checks.values()):
                mismatches.append({
                    "source_partial_reconcile_id": source_partial_id,
                    "target_partial_reconcile_id": partial.id,
                    "checks": checks,
                    "source_amount": round(self._amount(edge["partial_amount"]), 2),
                    "target_amount": round(partial.amount, 2),
                })
        return (
            created_count,
            reused_count,
            rounding_difference_count,
            blocked,
            mismatches,
        )

    def _native_general_reconciliation_trace_exchange(
        self,
        options,
        exchange_edges,
        target_lines,
    ):
        edges_by_document = defaultdict(list)
        for edge in exchange_edges:
            edges_by_document[edge["document_source_line_id"]].append(edge)
        traced_count = 0
        reused_count = 0
        rounding_difference_count = 0
        extra_rounding_segment_count = 0
        blocked = []
        mismatches = []
        for source_line_id, source_edges in sorted(edges_by_document.items()):
            document_line = target_lines.get(source_line_id)
            if not document_line:
                blocked.append({
                    "source_document_line_id": source_line_id,
                    "classification": "missing_exchange_document_endpoint",
                })
                continue
            candidates = (
                document_line.matched_debit_ids
                | document_line.matched_credit_ids
            ).filtered(
                lambda partial: (
                    (
                        partial.debit_move_id == document_line
                        and partial.credit_move_id.move_id.journal_id.code == "EXCH"
                    )
                    or (
                        partial.credit_move_id == document_line
                        and partial.debit_move_id.move_id.journal_id.code == "EXCH"
                    )
                ),
            )
            available = candidates.filtered(
                lambda partial: not partial.rebuild_source_model,
            )
            used_ids = set()
            for edge in sorted(
                source_edges,
                key=lambda item: item["source_partial_reconcile_id"],
            ):
                source_partial_id = edge["source_partial_reconcile_id"]
                partial = candidates.filtered(
                    lambda item: (
                        item.rebuild_source_model
                        == "account.partial.reconcile.native_general_exchange"
                        and item.rebuild_source_id == source_partial_id
                        and item.id not in used_ids
                    ),
                )
                if partial:
                    partial = partial[:1]
                    reused_count += 1
                else:
                    source_amount = self._amount(edge["partial_amount"])
                    eligible = available.filtered(
                        lambda item: (
                            item.id not in used_ids
                            and abs(item.amount - source_amount) <= 0.011
                        ),
                    ).sorted(
                        key=lambda item: (
                            round(abs(item.amount - source_amount), 6),
                            item.max_date,
                            item.id,
                        ),
                    )
                    if not eligible:
                        blocked.append({
                            "source_partial_reconcile_id": source_partial_id,
                            "source_document_line_id": source_line_id,
                            "classification": "missing_native_exchange_difference",
                            "source_amount": round(source_amount, 2),
                        })
                        continue
                    partial = eligible[:1]
                    partial.write({
                        "rebuild_import_note": (
                            "Track B native exchange partial generated by Odoo/OCA "
                            "while reconciling the source document amount pair."
                        ),
                        **self._trace_values(
                            "account.partial.reconcile.native_general_exchange",
                            source_partial_id,
                            options,
                        ),
                    })
                    traced_count += 1
                used_ids.add(partial.id)
                other_line = (
                    partial.credit_move_id
                    if partial.debit_move_id == document_line
                    else partial.debit_move_id
                )
                exchange_move = other_line.move_id
                balancing_lines = exchange_move.line_ids - other_line
                source_amount = self._amount(edge["partial_amount"])
                amount_difference = round(partial.amount - source_amount, 2)
                rounding_difference_count += int(bool(amount_difference))
                if not exchange_move.rebuild_source_model:
                    exchange_move.write({
                        "rebuild_import_note": (
                            "Track B exchange-difference move generated natively; "
                            "source month-end naming/date are retained as trace evidence."
                        ),
                        **self._trace_values(
                            "account.move.native_general_exchange",
                            edge["other_source_move_id"],
                            options,
                        ),
                    })
                if not other_line.rebuild_source_model:
                    other_line.write({
                        **self._trace_values(
                            "account.move.line.native_general_exchange",
                            edge["other_source_line_id"],
                            options,
                        ),
                    })
                balancing_line = balancing_lines.filtered(
                    lambda line: (
                        line.account_id.account_type
                        == edge["balancing_account_type"]
                        and abs(
                            line.balance - self._amount(edge["balancing_balance"]),
                        )
                        <= 0.011
                    ),
                )[:1]
                if balancing_line and not balancing_line.rebuild_source_model:
                    balancing_line.write({
                        **self._trace_values(
                            "account.move.line.native_general_exchange",
                            edge["balancing_source_line_id"],
                            options,
                        ),
                    })
                checks = {
                    "amount_within_native_rounding": abs(amount_difference) <= 0.01,
                    "document_endpoint": document_line in (
                        partial.debit_move_id | partial.credit_move_id
                    ),
                    "exchange_journal": exchange_move.journal_id.code == "EXCH",
                    "move_posted": exchange_move.state == "posted",
                    "move_source_trace": (
                        exchange_move.rebuild_source_model
                        == "account.move.native_general_exchange"
                        and exchange_move.rebuild_source_id
                        == edge["other_source_move_id"]
                    ),
                    "move_balanced": round(
                        sum(exchange_move.line_ids.mapped("balance")),
                        2,
                    )
                    == 0.0,
                    "counterpart_account": other_line.account_id.rebuild_source_id
                    == edge["other_account_id"],
                    "counterpart_balance": abs(
                        other_line.balance - self._amount(edge["other_balance"]),
                    )
                    <= 0.011,
                    "counterpart_source_trace": (
                        other_line.rebuild_source_model
                        == "account.move.line.native_general_exchange"
                        and other_line.rebuild_source_id
                        == edge["other_source_line_id"]
                    ),
                    "balancing_segment": bool(balancing_line),
                    "balancing_source_trace": bool(balancing_line)
                    and balancing_line.rebuild_source_model
                    == "account.move.line.native_general_exchange"
                    and balancing_line.rebuild_source_id
                    == edge["balancing_source_line_id"],
                }
                if not all(checks.values()):
                    mismatches.append({
                        "source_partial_reconcile_id": source_partial_id,
                        "target_partial_reconcile_id": partial.id,
                        "source_exchange_move_id": edge["other_source_move_id"],
                        "target_exchange_move_id": exchange_move.id,
                        "source_amount": round(source_amount, 2),
                        "target_amount": round(partial.amount, 2),
                        "checks": checks,
                    })
            extra_candidates = candidates.filtered(
                lambda partial: partial.id not in used_ids,
            )
            if extra_candidates:
                bounded_rounding = extra_candidates.filtered(
                    lambda partial: partial.amount <= 0.011,
                )
                if bounded_rounding == extra_candidates:
                    extra_rounding_segment_count += len(bounded_rounding)
                    bounded_rounding.write({
                        "rebuild_import_note": (
                            "Native one-cent exchange segment generated because "
                            "Odoo reconciliation rounding differs from the source."
                        ),
                    })
                else:
                    blocked.append({
                        "source_document_line_id": source_line_id,
                        "classification": "extra_native_exchange_difference",
                        "target_partial_ids": extra_candidates.ids,
                    })
        return (
            traced_count,
            reused_count,
            rounding_difference_count,
            extra_rounding_segment_count,
            blocked,
            mismatches,
        )

    def run_native_general_reconciliation_from_source(self, options):
        """Replay non-bank document settlement through native journal APIs."""
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
            edge_rows = self._native_general_reconciliation_edge_rows(conn, options)
            unexpected = [
                row
                for row in edge_rows
                if row["reconciliation_kind"]
                == "unsupported_nonbank_counterpart"
            ]
            manual_edges = [
                row
                for row in edge_rows
                if row["reconciliation_kind"] == "manual_general_entry"
            ]
            netting_edges = [
                row
                for row in edge_rows
                if row["reconciliation_kind"] == "document_netting"
            ]
            exchange_edges = [
                row
                for row in edge_rows
                if row["reconciliation_kind"] == "exchange_difference"
            ]
            edge_manual_move_ids = sorted({
                row["other_source_move_id"] for row in manual_edges
            })
            downstream_bank_source_ids = (
                self._native_bank_external_source_ids(conn, options)
            )
            downstream_bank_edges = self._native_bank_external_edge_rows(
                conn,
                options,
                downstream_bank_source_ids,
            )
            downstream_bank_move_ids = (
                self._native_bank_external_manual_move_ids(
                    downstream_bank_edges,
                )
            )
            standalone_move_ids, standalone_move_stats = (
                self._native_general_reconciliation_standalone_move_ids(
                    conn,
                    options,
                    edge_manual_move_ids,
                    downstream_bank_move_ids,
                )
            )
            manual_move_ids = sorted(
                set(edge_manual_move_ids) | set(standalone_move_ids),
            )
            move_rows = self._native_general_reconciliation_move_rows(
                conn,
                options,
                manual_move_ids,
            )
            line_rows = self._native_general_reconciliation_line_rows(
                conn,
                options,
                manual_move_ids,
            )
            journals, accounts, partners, currencies = (
                self._native_general_reconciliation_maps(
                    conn,
                    options,
                    move_rows,
                    line_rows,
                )
            )
            manual_moves, created_move_count, reused_move_count, blocked = (
                self._native_general_reconciliation_manual_moves(
                    options,
                    move_rows,
                    line_rows,
                    journals,
                    accounts,
                    partners,
                    currencies,
                )
            )
            blocked.extend({
                "source_partial_reconcile_id": row["source_partial_reconcile_id"],
                "classification": "unsupported_nonbank_counterpart",
                "other_journal_code": row["other_journal_code"],
            } for row in unexpected)
            manual_target_lines, manual_move_mismatches = (
                self._native_general_reconciliation_validate_manual_moves(
                    options,
                    move_rows,
                    line_rows,
                    manual_moves,
                )
            )

            document_rows = self._native_general_reconciliation_document_rows(
                conn,
                options,
            )
            document_edge_line_ids = sorted({
                source_line_id
                for edge in edge_rows
                for source_line_id in (
                    edge["debit_source_line_id"],
                    edge["credit_source_line_id"],
                )
                if (
                    source_line_id == edge["debit_source_line_id"]
                    and edge["debit_is_document"]
                )
                or (
                    source_line_id == edge["credit_source_line_id"]
                    and edge["credit_is_document"]
                )
            })
            document_target_lines, document_mapping_blocked = (
                self._native_expense_settlement_target_line_map(
                    document_rows,
                    [
                        {"source_line_id": source_line_id}
                        for source_line_id in document_edge_line_ids
                    ],
                    options,
                    target_move_models=[
                        "account.move.native_engine_replay",
                        "account.move.native_expense_replay",
                    ],
                    write_input_trace=False,
                )
            )
            blocked.extend(document_mapping_blocked)
            target_lines = {**document_target_lines, **manual_target_lines}
            input_edges = sorted(
                manual_edges + netting_edges,
                key=lambda row: (
                    row["max_date"],
                    row["source_partial_reconcile_id"],
                ),
            )
            (
                created_partial_count,
                reused_partial_count,
                input_rounding_difference_count,
                input_blocked,
                input_mismatches,
            ) = self._native_general_reconciliation_apply_inputs(
                options,
                input_edges,
                target_lines,
            )
            blocked.extend(input_blocked)
            (
                traced_exchange_count,
                reused_exchange_count,
                rounding_difference_count,
                extra_rounding_segment_count,
                exchange_blocked,
                exchange_mismatches,
            ) = self._native_general_reconciliation_trace_exchange(
                options,
                exchange_edges,
                target_lines,
            )
            blocked.extend(exchange_blocked)

            payment_state_mismatches = []
            source_payment_rows = (
                self._native_general_reconciliation_payment_state_rows(
                    conn,
                    options,
                )
            )
            target_moves = {
                move.rebuild_source_id: move
                for move in self.env["account.move"].sudo().search([
                    (
                        "rebuild_source_model",
                        "in",
                        [
                            "account.move.native_engine_replay",
                            "account.move.native_expense_replay",
                        ],
                    ),
                    (
                        "rebuild_source_snapshot",
                        "=",
                        options["source_snapshot_id"],
                    ),
                    (
                        "rebuild_source_id",
                        "in",
                        [row["id"] for row in source_payment_rows] or [0],
                    ),
                ])
            }
            for source_row in source_payment_rows:
                target_move = target_moves.get(source_row["id"])
                if (
                    not target_move
                    or target_move.payment_state != source_row["payment_state"]
                ):
                    payment_state_mismatches.append({
                        "source_move_id": source_row["id"],
                        "target_move_id": target_move.id if target_move else False,
                        "move_type": source_row["move_type"],
                        "source_payment_state": source_row["payment_state"],
                        "target_payment_state": (
                            target_move.payment_state if target_move else False
                        ),
                    })

            status = (
                "passed"
                if not blocked
                and not manual_move_mismatches
                and not input_mismatches
                and not exchange_mismatches
                and not payment_state_mismatches
                else "partial"
            )
            stats = {
                "classification": "NATIVE_VALIDATION_NATIVE_GENERAL_RECONCILIATION",
                "date_from": options["date_from"],
                "date_to": options["date_to"],
                "source_company_ids": options["source_company_ids"],
                "source_nonbank_partial_count": len(edge_rows),
                "source_nonbank_document_endpoint_count": sum(
                    int(row["debit_is_document"])
                    + int(row["credit_is_document"])
                    for row in edge_rows
                ),
                "source_manual_general_entry_count": len(move_rows),
                "source_manual_general_entry_line_count": len(line_rows),
                "standalone_general_entries": standalone_move_stats,
                "created_manual_general_entry_count": created_move_count,
                "reused_manual_general_entry_count": reused_move_count,
                "source_manual_general_partial_count": len(manual_edges),
                "source_document_netting_partial_count": len(netting_edges),
                "created_input_partial_count": created_partial_count,
                "reused_input_partial_count": reused_partial_count,
                "source_exchange_partial_count": len(exchange_edges),
                "traced_exchange_partial_count": traced_exchange_count,
                "reused_exchange_partial_count": reused_exchange_count,
                "native_exchange_rounding_difference_count": (
                    rounding_difference_count
                ),
                "native_exchange_extra_rounding_segment_count": (
                    extra_rounding_segment_count
                ),
                "native_input_rounding_difference_count": (
                    input_rounding_difference_count
                ),
                "manual_move_mismatch_count": len(manual_move_mismatches),
                "input_partial_mismatch_count": len(input_mismatches),
                "exchange_mismatch_count": len(exchange_mismatches),
                "payment_state_mismatch_count": len(payment_state_mismatches),
                "bounded_scope_classification": (
                    "Manual shareholder, compensation and clearing entries are "
                    "posted natively; document netting and their partials use General "
                    "Reconciliation. Odoo/OCA-generated exchange moves are retained "
                    "with source traces even where native timing or one-cent rounding "
                    "differs from the source month-end batch."
                ),
                "manual_move_mismatch_examples": manual_move_mismatches[:20],
                "input_partial_mismatch_examples": input_mismatches[:20],
                "exchange_mismatch_examples": exchange_mismatches[:20],
                "payment_state_mismatch_examples": payment_state_mismatches[:20],
                "blocked_examples": blocked[:20],
            }
            self.write({
                "status": status,
                "finished_at": fields.Datetime.now(),
                "imported_move_count": created_move_count + reused_move_count,
                "imported_move_line_count": len(line_rows),
                "imported_reconciliation_count": (
                    created_partial_count
                    + reused_partial_count
                    + traced_exchange_count
                    + reused_exchange_count
                ),
                "warning_count": (
                    len(blocked)
                    + len(manual_move_mismatches)
                    + len(input_mismatches)
                    + len(exchange_mismatches)
                    + len(payment_state_mismatches)
                ),
                "statistics_json": stats,
                "notes": (
                    "Track B non-bank settlement through native manual journal entry, "
                    "document netting, General Reconciliation and generated exchange "
                    "difference workflows."
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

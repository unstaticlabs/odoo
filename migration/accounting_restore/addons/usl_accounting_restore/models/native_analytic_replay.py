from collections import defaultdict

from odoo import Command, _, fields, models


class RebuildAccountAnalyticOverride(models.Model):
    _name = "rebuild.account.analytic.override"
    _description = "Native Analytic Post-Posting Correction"
    _inherit = ["usl.accounting.restore.source.mixin"]
    _order = "source_move_id, source_move_line_id"

    name = fields.Char(required=True, index=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
    )
    expense_id = fields.Many2one(
        "hr.expense",
        required=True,
        index=True,
        ondelete="restrict",
    )
    target_move_line_id = fields.Many2one(
        "account.move.line",
        required=True,
        index=True,
        ondelete="restrict",
    )
    source_expense_id = fields.Integer(index=True, copy=False)
    source_move_id = fields.Integer(index=True, copy=False)
    source_move_line_id = fields.Integer(index=True, copy=False)
    source_business_distribution = fields.Json(copy=False)
    source_final_distribution = fields.Json(copy=False)
    target_before_distribution = fields.Json(copy=False)
    target_after_distribution = fields.Json(copy=False)
    status = fields.Selection(
        [
            ("applied", "Applied"),
            ("represented", "Already Represented"),
            ("mismatch", "Mismatch"),
        ],
        required=True,
        default="applied",
        index=True,
        copy=False,
    )
    note = fields.Text()

    def action_open_expense(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Expense"),
            "res_model": "hr.expense",
            "view_mode": "form",
            "res_id": self.expense_id.id,
            "context": {"create": False, "delete": False},
        }

    def action_open_journal_item(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Analytic Journal Item"),
            "res_model": "account.move.line",
            "view_mode": "form",
            "res_id": self.target_move_line_id.id,
            "context": {"create": False, "delete": False},
        }


class RebuildAccountImportRun(models.Model):
    _inherit = "rebuild.account.import.run"

    def _native_analytic_source_lines(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT line.id,
                   line.move_id,
                   line.account_id,
                   line.balance,
                   line.amount_currency,
                   line.analytic_distribution,
                   move.name AS move_name,
                   move.date,
                   move.journal_id
              FROM account_move_line line
              JOIN account_move move ON move.id = line.move_id
             WHERE move.company_id = ANY(%(source_company_ids)s)
               AND move.state = 'posted'
               AND move.date BETWEEN %(date_from)s AND %(date_to)s
             ORDER BY line.id
            """,
            options,
        )

    def _native_analytic_override_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT expense.id AS source_expense_id,
                   expense.name AS expense_name,
                   expense.company_id,
                   expense.analytic_distribution
                       AS source_business_distribution,
                   line.id AS source_move_line_id,
                   line.move_id AS source_move_id,
                   line.account_id,
                   line.balance,
                   line.amount_currency,
                   line.analytic_distribution
                       AS source_final_distribution
              FROM hr_expense expense
              JOIN account_move_line line
                ON line.expense_id = expense.id
              JOIN account_move move
                ON move.id = line.move_id
             WHERE expense.company_id = ANY(%(source_company_ids)s)
               AND expense.date BETWEEN %(date_from)s AND %(date_to)s
               AND move.state = 'posted'
               AND line.analytic_distribution IS NOT NULL
               AND line.analytic_distribution IS DISTINCT FROM
                   expense.analytic_distribution
             ORDER BY expense.id, line.id
            """,
            options,
        )

    def _native_analytic_source_line_totals(self, conn, options):
        plan_columns = [
            row["column_name"]
            for row in self._fetchall(
                conn,
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'account_analytic_line'
                   AND (
                       column_name = 'account_id'
                       OR column_name ~ '^x_plan[0-9]+_id$'
                   )
                 ORDER BY ordinal_position
                """,
            )
        ]
        selects = [
            f"""
            SELECT analytic.{column} AS source_account_id,
                   analytic.amount
              FROM account_analytic_line analytic
              JOIN account_move_line move_line
                ON move_line.id = analytic.move_line_id
              JOIN account_move move
                ON move.id = move_line.move_id
             WHERE move.company_id = ANY(%(source_company_ids)s)
               AND move.state = 'posted'
               AND move.date BETWEEN %(date_from)s AND %(date_to)s
               AND analytic.{column} IS NOT NULL
            """
            for column in plan_columns
        ]
        if not selects:
            return {}, {}
        rows = self._fetchall(
            conn,
            f"""
            WITH expanded AS (
                {" UNION ALL ".join(selects)}
            )
            SELECT source_account_id,
                   count(*)::integer AS line_count,
                   round(sum(amount)::numeric, 2) AS amount
              FROM expanded
             GROUP BY source_account_id
             ORDER BY source_account_id
            """,
            options,
        )
        return (
            {
                row["source_account_id"]: round(
                    self._amount(row["amount"]),
                    2,
                )
                for row in rows
            },
            {
                row["source_account_id"]: row["line_count"]
                for row in rows
            },
        )

    @staticmethod
    def _native_analytic_source_distribution(distribution):
        return {
            str(key): round(float(value), 6)
            for key, value in (distribution or {}).items()
        }

    def _native_analytic_target_distribution(
        self,
        distribution,
        analytic_accounts_by_target_id,
    ):
        translated = {}
        for target_key, percentage in (distribution or {}).items():
            source_ids = []
            for target_id_text in str(target_key).split(","):
                try:
                    target_id = int(target_id_text)
                except (TypeError, ValueError):
                    source_ids.append(f"unmapped:{target_id_text}")
                    continue
                target_account = analytic_accounts_by_target_id.get(
                    target_id,
                )
                source_ids.append(
                    str(target_account.rebuild_source_id)
                    if target_account
                    and target_account.rebuild_source_id
                    else f"unmapped:{target_id}",
                )
            translated[",".join(source_ids)] = round(
                float(percentage),
                6,
            )
        return translated

    def _native_analytic_allocation_totals(
        self,
        rows,
        distribution_key,
        balance_key,
    ):
        totals = defaultdict(float)
        allocation_counts = defaultdict(int)
        for row in rows:
            balance = self._amount(row[balance_key])
            for combined_key, percentage in (
                row[distribution_key] or {}
            ).items():
                amount = -balance * self._amount(percentage) / 100.0
                for account_id_text in str(combined_key).split(","):
                    try:
                        account_id = int(account_id_text)
                    except (TypeError, ValueError):
                        continue
                    totals[account_id] += amount
                    allocation_counts[account_id] += 1
        return (
            {
                source_id: round(amount, 2)
                for source_id, amount in sorted(totals.items())
            },
            dict(sorted(allocation_counts.items())),
        )

    def _native_analytic_target_move_line_totals(
        self,
        target_lines,
        analytic_accounts_by_target_id,
    ):
        rows = []
        for line in target_lines:
            distribution = self._native_analytic_target_distribution(
                line.analytic_distribution,
                analytic_accounts_by_target_id,
            )
            rows.append({
                "balance": line.balance,
                "distribution": distribution,
            })
        return self._native_analytic_allocation_totals(
            rows,
            "distribution",
            "balance",
        )

    def _native_analytic_line_totals(
        self,
        analytic_lines,
    ):
        totals = defaultdict(float)
        line_counts = defaultdict(int)
        unmapped = []
        for analytic_line in analytic_lines:
            analytic_accounts = analytic_line._get_analytic_accounts()
            if not analytic_accounts:
                unmapped.append(analytic_line.id)
                continue
            for analytic_account in analytic_accounts:
                source_id = analytic_account.rebuild_source_id
                if not source_id:
                    unmapped.append(analytic_line.id)
                    continue
                totals[source_id] += analytic_line.amount
                line_counts[source_id] += 1
        return (
            {
                source_id: round(amount, 2)
                for source_id, amount in sorted(totals.items())
            },
            dict(sorted(line_counts.items())),
            sorted(set(unmapped)),
        )

    def run_native_analytic_replay_from_source(self, options):
        """Apply classified analytic corrections and prove multi-plan parity."""
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
            source_lines = self._native_analytic_source_lines(
                conn,
                options,
            )
            source_lines_by_id = {
                row["id"]: row
                for row in source_lines
            }
            override_rows = self._native_analytic_override_rows(
                conn,
                options,
            )
            (
                source_analytic_line_totals,
                source_analytic_line_counts,
            ) = self._native_analytic_source_line_totals(
                conn,
                options,
            )

            Company = self.env["res.company"].sudo()
            companies = {
                company.rebuild_source_id: company
                for company in Company.search([
                    ("rebuild_source_model", "=", "res.company"),
                    (
                        "rebuild_source_id",
                        "in",
                        options["source_company_ids"],
                    ),
                ])
            }
            AnalyticAccount = self.env[
                "account.analytic.account"
            ].sudo()
            analytic_accounts = AnalyticAccount.search([
                (
                    "rebuild_source_model",
                    "=",
                    "account.analytic.account",
                ),
                (
                    "rebuild_source_snapshot",
                    "=",
                    options["source_snapshot_id"],
                ),
            ])
            analytic_accounts_by_source_id = {
                account.rebuild_source_id: account
                for account in analytic_accounts
            }
            analytic_accounts_by_target_id = {
                account.id: account
                for account in analytic_accounts
            }
            Expense = self.env["hr.expense"].sudo()
            expenses = {
                expense.rebuild_source_id: expense
                for expense in Expense.search([
                    ("rebuild_source_model", "=", "hr.expense"),
                    (
                        "rebuild_source_snapshot",
                        "=",
                        options["source_snapshot_id"],
                    ),
                    (
                        "rebuild_source_id",
                        "in",
                        [
                            row["source_expense_id"]
                            for row in override_rows
                        ]
                        or [0],
                    ),
                ])
            }
            Evidence = self.env[
                "rebuild.account.analytic.override"
            ].sudo()
            created_evidence_count = 0
            applied_override_count = 0
            represented_override_count = 0
            blocked = []
            mismatches = []
            applied_examples = []

            for source_row in override_rows:
                expense = expenses.get(source_row["source_expense_id"])
                expected_distribution = (
                    self._native_replay_analytic_distribution(
                        source_row["source_final_distribution"],
                        analytic_accounts_by_source_id,
                    )
                    or {}
                )
                candidate_lines = (
                    expense.account_move_id.line_ids.filtered(
                        lambda line: (
                            line.expense_id == expense
                            and line.account_id.rebuild_source_id
                            == source_row["account_id"]
                            and round(line.balance, 2)
                            == round(
                                self._amount(source_row["balance"]),
                                2,
                            )
                            and round(line.amount_currency, 2)
                            == round(
                                self._amount(
                                    source_row["amount_currency"],
                                ),
                                2,
                            )
                        ),
                    )
                    if expense and expense.account_move_id
                    else self.env["account.move.line"]
                )
                if len(candidate_lines) != 1:
                    blocked.append({
                        "source_expense_id": source_row[
                            "source_expense_id"
                        ],
                        "source_move_line_id": source_row[
                            "source_move_line_id"
                        ],
                        "classification": (
                            "native_analytic_override_target_ambiguous"
                        ),
                        "expense_found": bool(expense),
                        "target_move_found": bool(
                            expense and expense.account_move_id,
                        ),
                        "candidate_line_ids": candidate_lines.ids,
                    })
                    continue
                target_line = candidate_lines
                target_before = target_line.analytic_distribution or {}
                applied = target_before != expected_distribution
                if applied:
                    target_line.write({
                        "analytic_distribution": expected_distribution,
                        "rebuild_import_note": (
                            "Classified Track B post-posting analytic "
                            "correction: the source finalized analytic "
                            "distribution differs from the source expense "
                            "business record. Journal amounts are unchanged."
                        ),
                        **self._trace_values(
                            "account.move.line.native_analytic_override",
                            source_row["source_move_line_id"],
                            options,
                        ),
                        "rebuild_import_status": "transformed",
                    })
                    applied_override_count += 1
                else:
                    represented_override_count += 1

                evidence = Evidence.search([
                    (
                        "rebuild_source_model",
                        "=",
                        "account.move.line.native_analytic_override",
                    ),
                    (
                        "rebuild_source_snapshot",
                        "=",
                        options["source_snapshot_id"],
                    ),
                    (
                        "rebuild_source_id",
                        "=",
                        source_row["source_move_line_id"],
                    ),
                ], limit=1)
                evidence_values = {
                    "name": _(
                        "Analytic correction — %(expense)s",
                        expense=source_row["expense_name"],
                    ),
                    "company_id": expense.company_id.id,
                    "expense_id": expense.id,
                    "target_move_line_id": target_line.id,
                    "source_expense_id": source_row[
                        "source_expense_id"
                    ],
                    "source_move_id": source_row["source_move_id"],
                    "source_move_line_id": source_row[
                        "source_move_line_id"
                    ],
                    "source_business_distribution": (
                        source_row["source_business_distribution"]
                        or {}
                    ),
                    "source_final_distribution": (
                        source_row["source_final_distribution"] or {}
                    ),
                    "target_after_distribution": (
                        target_line.analytic_distribution or {}
                    ),
                    "status": (
                        "applied"
                        if applied
                        or (
                            evidence
                            and (
                                evidence.status == "applied"
                                or evidence.target_before_distribution
                                != expected_distribution
                            )
                        )
                        else "represented"
                    ),
                    "note": (
                        "The native expense engine correctly reproduced the "
                        "source expense business fields. The source journal "
                        "item was edited afterwards, so this explicit "
                        "analytic-only correction reconstructs that final "
                        "multi-plan classification without changing debit, "
                        "credit, tax, currency or reconciliation."
                    ),
                    **self._trace_values(
                        "account.move.line.native_analytic_override",
                        source_row["source_move_line_id"],
                        options,
                    ),
                }
                if evidence:
                    if not evidence.target_before_distribution:
                        evidence_values["target_before_distribution"] = (
                            target_before
                        )
                    evidence.write(evidence_values)
                else:
                    evidence_values["target_before_distribution"] = (
                        target_before
                    )
                    Evidence.create(evidence_values)
                    created_evidence_count += 1
                actual_distribution = (
                    target_line.analytic_distribution or {}
                )
                if actual_distribution != expected_distribution:
                    mismatches.append({
                        "source_expense_id": source_row[
                            "source_expense_id"
                        ],
                        "source_move_line_id": source_row[
                            "source_move_line_id"
                        ],
                        "target_move_line_id": target_line.id,
                        "classification": (
                            "native_analytic_override_mismatch"
                        ),
                        "expected_distribution": expected_distribution,
                        "actual_distribution": actual_distribution,
                    })
                elif len(applied_examples) < 10:
                    applied_examples.append({
                        "source_expense_id": source_row[
                            "source_expense_id"
                        ],
                        "source_move_line_id": source_row[
                            "source_move_line_id"
                        ],
                        "target_move_line_id": target_line.id,
                        "source_business_distribution": (
                            source_row[
                                "source_business_distribution"
                            ]
                            or {}
                        ),
                        "source_final_distribution": (
                            source_row["source_final_distribution"] or {}
                        ),
                        "target_distribution": actual_distribution,
                    })

            date_from = fields.Date.to_date(options["date_from"])
            date_to = fields.Date.to_date(options["date_to"])
            target_lines = self.env["account.move.line"].sudo().search([
                ("company_id", "in", [company.id for company in companies.values()]),
                ("parent_state", "=", "posted"),
                ("date", ">=", date_from),
                ("date", "<=", date_to),
                ("analytic_distribution", "!=", False),
            ])
            direct_trace_count = 0
            direct_trace_mismatches = []
            for target_line in target_lines.filtered(
                lambda line: line.rebuild_source_id,
            ):
                source_line = source_lines_by_id.get(
                    target_line.rebuild_source_id,
                )
                if not source_line:
                    continue
                source_distribution = (
                    self._native_analytic_source_distribution(
                        source_line["analytic_distribution"],
                    )
                )
                target_distribution = (
                    self._native_analytic_target_distribution(
                        target_line.analytic_distribution,
                        analytic_accounts_by_target_id,
                    )
                )
                if not source_distribution and not target_distribution:
                    continue
                direct_trace_count += 1
                if source_distribution != target_distribution:
                    direct_trace_mismatches.append({
                        "source_move_line_id": source_line["id"],
                        "target_move_line_id": target_line.id,
                        "source_distribution": source_distribution,
                        "target_distribution": target_distribution,
                    })

            source_totals, source_allocation_counts = (
                self._native_analytic_allocation_totals(
                    source_lines,
                    "analytic_distribution",
                    "balance",
                )
            )
            (
                target_move_line_totals,
                target_allocation_counts,
            ) = self._native_analytic_target_move_line_totals(
                target_lines,
                analytic_accounts_by_target_id,
            )
            target_analytic_lines = self.env[
                "account.analytic.line"
            ].sudo().search([
                ("company_id", "in", [company.id for company in companies.values()]),
                ("date", ">=", date_from),
                ("date", "<=", date_to),
                ("move_line_id.parent_state", "=", "posted"),
            ])
            (
                target_analytic_line_totals,
                target_analytic_line_counts,
                unmapped_analytic_line_ids,
            ) = self._native_analytic_line_totals(
                target_analytic_lines,
            )
            source_target_differences = {
                source_id: round(
                    target_move_line_totals.get(source_id, 0.0)
                    - source_totals.get(source_id, 0.0),
                    2,
                )
                for source_id in sorted(
                    set(source_totals) | set(target_move_line_totals),
                )
                if round(
                    target_move_line_totals.get(source_id, 0.0)
                    - source_totals.get(source_id, 0.0),
                    2,
                )
            }
            move_analytic_differences = {
                source_id: round(
                    target_analytic_line_totals.get(source_id, 0.0)
                    - target_move_line_totals.get(source_id, 0.0),
                    2,
                )
                for source_id in sorted(
                    set(target_move_line_totals)
                    | set(target_analytic_line_totals),
                )
                if round(
                    target_analytic_line_totals.get(source_id, 0.0)
                    - target_move_line_totals.get(source_id, 0.0),
                    2,
                )
            }
            source_analytic_target_differences = {
                source_id: round(
                    target_analytic_line_totals.get(source_id, 0.0)
                    - source_analytic_line_totals.get(source_id, 0.0),
                    2,
                )
                for source_id in sorted(
                    set(source_analytic_line_totals)
                    | set(target_analytic_line_totals),
                )
                if round(
                    target_analytic_line_totals.get(source_id, 0.0)
                    - source_analytic_line_totals.get(source_id, 0.0),
                    2,
                )
            }
            move_analytic_rounding_within_currency = all(
                abs(difference) <= 0.01
                for difference in move_analytic_differences.values()
            )
            status = (
                "passed"
                if not blocked
                and not mismatches
                and not direct_trace_mismatches
                and not source_target_differences
                and not source_analytic_target_differences
                and move_analytic_rounding_within_currency
                and not unmapped_analytic_line_ids
                and (
                    applied_override_count
                    + represented_override_count
                    == len(override_rows)
                )
                else "failed"
            )
            stats = {
                "classification": (
                    "NATIVE_VALIDATION_NATIVE_MULTI_PLAN_ANALYTIC_REPLAY"
                ),
                "status": status,
                "date_from": options["date_from"],
                "date_to": options["date_to"],
                "source_company_ids": options["source_company_ids"],
                "source_posted_move_line_count": len(source_lines),
                "source_analytic_override_count": len(override_rows),
                "applied_analytic_override_count": (
                    applied_override_count
                ),
                "represented_analytic_override_count": (
                    represented_override_count
                ),
                "created_override_evidence_count": (
                    created_evidence_count
                ),
                "target_distributed_move_line_count": len(target_lines),
                "target_analytic_line_count": len(
                    target_analytic_lines,
                ),
                "direct_trace_analytic_line_count": direct_trace_count,
                "direct_trace_mismatch_count": len(
                    direct_trace_mismatches,
                ),
                "direct_trace_mismatch_examples": (
                    direct_trace_mismatches[:20]
                ),
                "source_allocation_totals": source_totals,
                "target_move_line_allocation_totals": (
                    target_move_line_totals
                ),
                "target_analytic_line_totals": (
                    target_analytic_line_totals
                ),
                "source_analytic_line_totals": (
                    source_analytic_line_totals
                ),
                "source_allocation_counts": source_allocation_counts,
                "target_move_line_allocation_counts": (
                    target_allocation_counts
                ),
                "target_analytic_line_counts": (
                    target_analytic_line_counts
                ),
                "source_analytic_line_counts": (
                    source_analytic_line_counts
                ),
                "source_target_difference_count": len(
                    source_target_differences,
                ),
                "source_target_differences": (
                    source_target_differences
                ),
                "move_analytic_difference_count": len(
                    move_analytic_differences,
                ),
                "move_analytic_differences": (
                    move_analytic_differences
                ),
                "move_analytic_rounding_within_currency": (
                    move_analytic_rounding_within_currency
                ),
                "source_analytic_target_difference_count": len(
                    source_analytic_target_differences,
                ),
                "source_analytic_target_differences": (
                    source_analytic_target_differences
                ),
                "unmapped_analytic_line_count": len(
                    unmapped_analytic_line_ids,
                ),
                "unmapped_analytic_line_ids": (
                    unmapped_analytic_line_ids[:50]
                ),
                "blocked_count": len(blocked),
                "blocked_examples": blocked[:20],
                "mismatch_count": len(mismatches),
                "mismatch_examples": mismatches[:20],
                "applied_examples": applied_examples,
            }
            self.write({
                "status": status,
                "finished_at": fields.Datetime.now(),
                "company_ids": [
                    Command.set([
                        company.id
                        for company in companies.values()
                    ]),
                ],
                "warning_count": (
                    len(blocked)
                    + len(mismatches)
                    + len(direct_trace_mismatches)
                    + len(source_target_differences)
                    + len(source_analytic_target_differences)
                    + int(not move_analytic_rounding_within_currency)
                    + len(unmapped_analytic_line_ids)
                ),
                "statistics_json": stats,
                "notes": (
                    "Track B multi-plan analytic validation. Native "
                    "business documents retain their source distributions; "
                    "classified post-posting source corrections are applied "
                    "through standard analytic writes. Source allocations, "
                    "target journal-item allocations and generated analytic "
                    "items reconcile by source analytic account."
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

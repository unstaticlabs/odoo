from collections import defaultdict
from datetime import timedelta

from odoo import Command, fields, models


class AccountAsset(models.Model):
    _name = "account.asset"
    _inherit = ["account.asset", "rebuild.source.trace.mixin"]

    rebuild_source_depreciation_model_id = fields.Integer(index=True, copy=False)
    rebuild_source_state = fields.Char(index=True, copy=False)
    rebuild_source_opening_depreciation = fields.Monetary(
        currency_field="currency_id",
        copy=False,
    )
    rebuild_source_book_value = fields.Monetary(
        currency_field="currency_id",
        copy=False,
    )


class AccountAssetProfile(models.Model):
    _name = "account.asset.profile"
    _inherit = ["account.asset.profile", "rebuild.source.trace.mixin"]

    rebuild_source_depreciation_model_id = fields.Integer(index=True, copy=False)
    rebuild_source_asset_account_id = fields.Integer(index=True, copy=False)


class AccountAssetLine(models.Model):
    _name = "account.asset.line"
    _inherit = ["account.asset.line", "rebuild.source.trace.mixin"]

    rebuild_source_asset_id = fields.Integer(index=True, copy=False)
    rebuild_source_state = fields.Char(index=True, copy=False)
    rebuild_source_move_name = fields.Char(copy=False)


class RebuildAccountImportRun(models.Model):
    _inherit = "rebuild.account.import.run"

    @staticmethod
    def _native_asset_method(source_method):
        return {
            "linear": "linear",
            "degressive": "degressive",
            "degressive_then_linear": "degr-linear",
        }.get(source_method, "linear")

    @staticmethod
    def _native_asset_period(source_period):
        return {
            "1": "month",
            "3": "quarter",
            "12": "year",
        }.get(str(source_period or "1"), "month")

    def _native_asset_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            SELECT asset.id,
                   asset.company_id,
                   COALESCE(
                       asset.name->>'fr_FR',
                       asset.name->>'en_US',
                       asset.name::text
                   ) AS name,
                   asset.state,
                   asset.active,
                   asset.acquisition_date,
                   asset.prorata_date,
                   asset.prorata_computation_type,
                   asset.original_value,
                   asset.salvage_value,
                   asset.already_depreciated_amount_import,
                   asset.book_value,
                   asset.account_asset_id,
                   asset.account_depreciation_id,
                   asset.account_depreciation_expense_id,
                   COALESCE(model.journal_id, asset.journal_id) AS journal_id,
                   asset.model_id,
                   asset.analytic_distribution,
                   model.method,
                   model.method_number,
                   model.method_period,
                   model.method_progress_factor
              FROM account_asset asset
              JOIN account_depreciation_model model
                ON model.id = asset.model_id
             WHERE asset.company_id = ANY(%(source_company_ids)s)
               AND EXISTS (
                   SELECT 1
                     FROM account_move move
                    WHERE move.asset_id = asset.id
                      AND move.date >= %(date_from)s
               )
             ORDER BY asset.acquisition_date, asset.id
            """,
            options,
        )

    def _native_asset_schedule_rows(self, conn, options, source_asset_ids):
        if not source_asset_ids:
            return []
        return self._fetchall(
            conn,
            """
            SELECT move.asset_id,
                   move.id AS source_move_id,
                   move.date,
                   move.name,
                   move.ref,
                   move.state,
                   move.journal_id,
                   count(line.id)::integer AS source_line_count,
                   round(
                       abs(sum(line.balance) FILTER (
                           WHERE line.account_id = asset.account_depreciation_expense_id
                       ))::numeric,
                       2
                   ) AS amount
              FROM account_move move
              JOIN account_asset asset ON asset.id = move.asset_id
              JOIN account_move_line line ON line.move_id = move.id
             WHERE move.asset_id = ANY(%(source_asset_ids)s)
               AND move.date >= %(date_from)s
             GROUP BY move.asset_id,
                      move.id,
                      move.date,
                      move.name,
                      move.ref,
                      move.state,
                      move.journal_id,
                      asset.account_depreciation_expense_id
             ORDER BY move.asset_id, move.date, move.id
            """,
            {**options, "source_asset_ids": source_asset_ids},
        )

    def _native_asset_schedule_line_rows(self, conn, source_move_ids):
        if not source_move_ids:
            return []
        return self._fetchall(
            conn,
            """
            SELECT line.id,
                   line.move_id,
                   line.account_id,
                   line.balance,
                   line.amount_currency,
                   line.analytic_distribution
              FROM account_move_line line
             WHERE line.move_id = ANY(%(source_move_ids)s)
               AND line.account_id IS NOT NULL
             ORDER BY line.move_id, line.sequence, line.id
            """,
            {"source_move_ids": source_move_ids},
        )

    def _native_asset_profile(
        self,
        options,
        row,
        company,
        accounts,
        journal,
        analytic_accounts,
    ):
        Profile = self.env["account.asset.profile"].sudo().with_company(company)
        profile = Profile.search([
            (
                "rebuild_source_model",
                "=",
                "account.depreciation.model.asset_profile",
            ),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("rebuild_source_id", "=", row["model_id"]),
            (
                "rebuild_source_asset_account_id",
                "=",
                row["account_asset_id"],
            ),
        ], limit=1)
        analytic_distribution = self._native_replay_analytic_distribution(
            row["analytic_distribution"],
            analytic_accounts,
        )
        period_label = {
            "month": self.env._("monthly"),
            "quarter": self.env._("quarterly"),
            "year": self.env._("annual"),
        }[self._native_asset_period(row["method_period"])]
        method_label = {
            "linear": self.env._("straight-line"),
            "degressive": self.env._("declining-balance"),
            "degressive_then_linear": self.env._(
                "declining-balance then straight-line",
            ),
        }.get(row["method"], self.env._("depreciation"))
        asset_account = accounts[row["account_asset_id"]]
        profile_name = self.env._(
            "%(account_code)s — %(method)s, %(periods)s %(period_label)s periods",
            account_code=asset_account.code,
            method=method_label,
            periods=row["method_number"],
            period_label=period_label,
        )
        values = {
            "name": profile_name,
            "account_asset_id": accounts[row["account_asset_id"]].id,
            "account_depreciation_id": accounts[
                row["account_depreciation_id"]
            ].id,
            "account_expense_depreciation_id": accounts[
                row["account_depreciation_expense_id"]
            ].id,
            "journal_id": journal.id,
            "company_id": company.id,
            "salvage_type": "fixed",
            "salvage_value": self._amount(row["salvage_value"]),
            "method": self._native_asset_method(row["method"]),
            "method_time": "number",
            "method_number": row["method_number"],
            "method_period": self._native_asset_period(row["method_period"]),
            "method_progress_factor": row["method_progress_factor"] or 0.3,
            "prorata": bool(row["prorata_date"]),
            "open_asset": False,
            "analytic_distribution": analytic_distribution or False,
            "rebuild_source_depreciation_model_id": row["model_id"],
            "rebuild_source_asset_account_id": row["account_asset_id"],
            **self._trace_values(
                "account.depreciation.model.asset_profile",
                row["model_id"],
                options,
            ),
        }
        if profile:
            profile.write(values)
            profile.rebuild_import_status = "reused"
            return profile, False
        return Profile.create(values), True

    def _native_asset_record(
        self,
        options,
        row,
        company,
        profile,
    ):
        Asset = self.env["account.asset"].sudo().with_company(company)
        asset = Asset.search([
            ("rebuild_source_model", "=", "account.asset"),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("rebuild_source_id", "=", row["id"]),
        ], limit=1)
        values = {
            "name": row["name"],
            "purchase_value": self._amount(row["original_value"]),
            "profile_id": profile.id,
            "date_start": row["acquisition_date"],
            "company_id": company.id,
            "active": row["active"],
            "rebuild_source_depreciation_model_id": row["model_id"],
            "rebuild_source_state": row["state"],
            "rebuild_source_opening_depreciation": self._amount(
                row["already_depreciated_amount_import"],
            ),
            "rebuild_source_book_value": self._amount(row["book_value"]),
            **self._trace_values("account.asset", row["id"], options),
        }
        if asset:
            asset.rebuild_import_status = "reused"
            return asset, False
        asset = Asset.create(values)
        create_line = asset.depreciation_line_ids.filtered(
            lambda line: line.type == "create",
        )[:1]
        if create_line:
            create_line.write({
                "rebuild_source_asset_id": row["id"],
                **self._trace_values(
                    "account.asset.opening_value",
                    row["id"],
                    options,
                ),
            })
        return asset, True

    def _native_asset_opening_line(self, options, row, asset):
        opening_amount = self._amount(row["already_depreciated_amount_import"])
        if not opening_amount:
            return self.env["account.asset.line"]
        opening_date = fields.Date.to_date(
            options.get("opening_depreciation_date"),
        ) or (
            fields.Date.to_date(options["date_from"])
            - timedelta(days=1)
        )
        Line = self.env["account.asset.line"].sudo()
        opening = Line.search([
            ("asset_id", "=", asset.id),
            (
                "rebuild_source_model",
                "=",
                "account.asset.imported_depreciation",
            ),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("rebuild_source_id", "=", row["id"]),
        ], limit=1)
        if opening:
            opening.with_context(allow_asset_line_update=True).write({
                "name": self.env._("Depreciation before migration"),
                "amount": opening_amount,
            })
            if opening.line_date != opening_date:
                # OCA correctly blocks backdating ordinary asset lines after
                # posted depreciation. This traced opening line is migration
                # metadata rather than an accounting entry, so update only
                # its presentation date without touching any journal item.
                self.env.cr.execute(
                    """
                    UPDATE account_asset_line
                       SET line_date = %s
                     WHERE id = %s
                    """,
                    [opening_date, opening.id],
                )
                opening.invalidate_recordset(["line_date"])
            return opening
        return Line.create({
            "name": self.env._("Depreciation before migration"),
            "asset_id": asset.id,
            "amount": opening_amount,
            "line_date": opening_date,
            "init_entry": True,
            "type": "depreciate",
            "rebuild_source_asset_id": row["id"],
            "rebuild_source_state": "historical_opening",
            **self._trace_values(
                "account.asset.imported_depreciation",
                row["id"],
                options,
            ),
        })

    def _native_asset_schedule_line(
        self,
        options,
        source_row,
        asset,
        previous_line,
    ):
        Line = self.env["account.asset.line"].sudo()
        line = Line.search([
            ("asset_id", "=", asset.id),
            (
                "rebuild_source_model",
                "=",
                "account.move.asset_depreciation_schedule",
            ),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("rebuild_source_id", "=", source_row["source_move_id"]),
        ], limit=1)
        if line:
            return line, False
        return Line.create({
            "name": source_row["name"]
            or f"Depreciation {source_row['date']}",
            "asset_id": asset.id,
            "previous_id": previous_line.id if previous_line else False,
            "amount": self._amount(source_row["amount"]),
            "line_date": source_row["date"],
            "init_entry": False,
            "type": "depreciate",
            "rebuild_source_asset_id": source_row["asset_id"],
            "rebuild_source_state": source_row["state"],
            "rebuild_source_move_name": source_row["name"],
            **self._trace_values(
                "account.move.asset_depreciation_schedule",
                source_row["source_move_id"],
                options,
            ),
        }), True

    def _native_asset_trace_move(
        self,
        options,
        source_row,
        source_line_rows,
        move,
    ):
        move.write({
            "rebuild_source_move_type": "entry",
            **self._trace_values(
                "account.move.asset_native_replay",
                source_row["source_move_id"],
                options,
            ),
        })
        source_by_account = defaultdict(list)
        for source_line in source_line_rows:
            source_by_account[source_line["account_id"]].append(source_line)
        for target_line in move.line_ids:
            source_account_id = target_line.account_id.rebuild_source_id
            candidates = source_by_account[source_account_id]
            if not candidates:
                continue
            source_line = candidates.pop(0)
            target_line.write(
                self._trace_values(
                    "account.move.line.asset_native_replay",
                    source_line["id"],
                    options,
                ),
            )

    def _native_asset_move_check(
        self,
        source_row,
        source_line_rows,
        asset_line,
    ):
        move = asset_line.move_id
        source_totals = defaultdict(float)
        for source_line in source_line_rows:
            source_totals[source_line["account_id"]] += self._amount(
                source_line["balance"],
            )
        target_totals = defaultdict(float)
        for target_line in move.line_ids:
            target_totals[target_line.account_id.rebuild_source_id] += (
                target_line.balance
            )
        source_totals = {
            account_id: round(amount, 2)
            for account_id, amount in source_totals.items()
        }
        target_totals = {
            account_id: round(amount, 2)
            for account_id, amount in target_totals.items()
        }
        return {
            "date_matches": move.date == source_row["date"],
            "state_matches": move.state == source_row["state"] == "posted",
            "amount_matches": round(asset_line.amount, 2)
            == round(self._amount(source_row["amount"]), 2),
            "account_totals_match": target_totals == source_totals,
            "source_account_totals": source_totals,
            "target_account_totals": target_totals,
        }

    def run_native_asset_replay_from_source(self, options):
        """Replay Enterprise depreciation schedules through OCA native assets."""
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
            "use_exact_imported_moves": False,
            **(options or {}),
        }
        options["source_company_ids"] = self._source_company_ids(options)
        use_exact_imported_moves = bool(
            options.get("use_exact_imported_moves"),
        )
        self.write({
            "status": "running",
            "mode": (
                "exact_ledger_replay"
                if use_exact_imported_moves
                else "native_engine_replay"
            ),
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
            accounts, _accounts_to_archive = self._account_map(
                conn,
                options,
                companies,
                currencies,
            )
            journals = self._journal_map(
                conn,
                options,
                companies,
                accounts,
                currencies,
            )
            analytic_plans = self._analytic_plan_map(conn, options)
            analytic_accounts = self._analytic_account_map(
                conn,
                options,
                companies,
                self._partner_map(conn, options),
                analytic_plans,
            )
            asset_rows = self._native_asset_rows(conn, options)
            schedule_rows = self._native_asset_schedule_rows(
                conn,
                options,
                [row["id"] for row in asset_rows],
            )
            source_move_ids = [
                row["source_move_id"]
                for row in schedule_rows
            ]
            source_lines_by_move = defaultdict(list)
            for source_line in self._native_asset_schedule_line_rows(
                conn,
                source_move_ids,
            ):
                source_lines_by_move[source_line["move_id"]].append(source_line)
            schedules_by_asset = defaultdict(list)
            for schedule_row in schedule_rows:
                schedules_by_asset[schedule_row["asset_id"]].append(
                    schedule_row,
                )
            exact_moves = {}
            if use_exact_imported_moves:
                exact_moves = {
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

            created_profile_count = 0
            created_asset_count = 0
            created_schedule_line_count = 0
            created_move_count = 0
            reused_move_count = 0
            linked_move_line_count = 0
            passed_move_count = 0
            blocked = []
            mismatches = []
            passed_examples = []
            target_assets = {}

            for row in asset_rows:
                company = companies.get(row["company_id"])
                journal = journals.get(row["journal_id"])
                required_account_ids = {
                    row["account_asset_id"],
                    row["account_depreciation_id"],
                    row["account_depreciation_expense_id"],
                }
                missing_account_ids = sorted(
                    source_id
                    for source_id in required_account_ids
                    if source_id not in accounts
                )
                if not company or not journal or missing_account_ids:
                    blocked.append({
                        "source_asset_id": row["id"],
                        "classification": "missing_asset_configuration",
                        "missing_company": not bool(company),
                        "missing_journal": not bool(journal),
                        "missing_account_ids": missing_account_ids,
                    })
                    continue
                profile, profile_created = self._native_asset_profile(
                    options,
                    row,
                    company,
                    accounts,
                    journal,
                    analytic_accounts,
                )
                created_profile_count += int(profile_created)
                asset, asset_created = self._native_asset_record(
                    options,
                    row,
                    company,
                    profile,
                )
                created_asset_count += int(asset_created)
                target_assets[row["id"]] = asset
                previous_line = self._native_asset_opening_line(
                    options,
                    row,
                    asset,
                )
                for source_row in schedules_by_asset[row["id"]]:
                    asset_line, line_created = self._native_asset_schedule_line(
                        options,
                        source_row,
                        asset,
                        previous_line,
                    )
                    created_schedule_line_count += int(line_created)
                    previous_line = asset_line
                    if source_row["state"] != "posted":
                        if asset_line.move_id:
                            mismatches.append({
                                "source_move_id": source_row["source_move_id"],
                                "classification": "future_source_draft_was_posted",
                                "target_move_id": asset_line.move_id.id,
                            })
                        continue
                    if source_row["date"] > fields.Date.to_date(
                        options["date_to"],
                    ):
                        continue
                    reused_exact_move = bool(
                        use_exact_imported_moves
                        and asset_line.move_id
                        and asset_line.move_id.rebuild_source_model
                        == "account.move"
                    )
                    if use_exact_imported_moves and not asset_line.move_id:
                        exact_move = exact_moves.get(
                            source_row["source_move_id"],
                        )
                        if not exact_move:
                            mismatches.append({
                                "source_asset_id": source_row["asset_id"],
                                "source_move_id": (
                                    source_row["source_move_id"]
                                ),
                                "classification": (
                                    "missing_exact_asset_depreciation_move"
                                ),
                            })
                            continue
                        asset_line.with_context(
                            allow_asset_line_update=True,
                        ).write({"move_id": exact_move.id})
                        reused_exact_move = True
                    if reused_exact_move:
                        asset_line.move_id.line_ids.with_context(
                            allow_asset=True,
                        ).write({"asset_id": asset.id})
                        linked_move_line_count += len(
                            asset_line.move_id.line_ids,
                        )
                    if asset_line.move_id:
                        reused_move_count += 1
                    else:
                        asset_line.create_move()
                        created_move_count += 1
                    if not reused_exact_move:
                        self._native_asset_trace_move(
                            options,
                            source_row,
                            source_lines_by_move[
                                source_row["source_move_id"]
                            ],
                            asset_line.move_id,
                        )
                    checks = self._native_asset_move_check(
                        source_row,
                        source_lines_by_move[source_row["source_move_id"]],
                        asset_line,
                    )
                    result = {
                        "source_asset_id": source_row["asset_id"],
                        "source_move_id": source_row["source_move_id"],
                        "target_asset_id": asset.id,
                        "target_move_id": asset_line.move_id.id,
                        "date": str(source_row["date"]),
                        "amount": round(asset_line.amount, 2),
                        "checks": checks,
                    }
                    passed = all(
                        checks[key]
                        for key in (
                            "date_matches",
                            "state_matches",
                            "amount_matches",
                            "account_totals_match",
                        )
                    )
                    if passed:
                        passed_move_count += 1
                        if len(passed_examples) < 10:
                            passed_examples.append(result)
                    else:
                        mismatches.append({
                            **result,
                            "classification": "asset_depreciation_move_mismatch",
                        })
                asset.validate()

            source_posted_rows = [
                row
                for row in schedule_rows
                if row["state"] == "posted"
                and row["date"] <= fields.Date.to_date(options["date_to"])
            ]
            source_future_rows = [
                row
                for row in schedule_rows
                if row["state"] != "posted"
                or row["date"] > fields.Date.to_date(options["date_to"])
            ]
            status = (
                "passed"
                if not blocked
                and not mismatches
                and len(target_assets) == len(asset_rows)
                and passed_move_count == len(source_posted_rows)
                else "failed"
            )
            stats = {
                "classification": (
                    "SOURCE_FAITHFUL_NATIVE_ASSET_MATERIALIZATION"
                    if use_exact_imported_moves
                    else (
                        "NATIVE_VALIDATION_NATIVE_ASSET_"
                        "DEPRECIATION_REPLAY"
                    )
                ),
                "status": status,
                "date_from": options["date_from"],
                "date_to": options["date_to"],
                "source_company_ids": options["source_company_ids"],
                "source_asset_count": len(asset_rows),
                "created_profile_count": created_profile_count,
                "created_asset_count": created_asset_count,
                "reused_asset_count": len(target_assets) - created_asset_count,
                "source_schedule_line_count": len(schedule_rows),
                "source_posted_depreciation_move_count": len(
                    source_posted_rows,
                ),
                "source_future_draft_schedule_line_count": len(
                    source_future_rows,
                ),
                "created_schedule_line_count": created_schedule_line_count,
                "created_depreciation_move_count": created_move_count,
                "reused_depreciation_move_count": reused_move_count,
                "linked_depreciation_move_line_count": (
                    linked_move_line_count
                ),
                "passed_depreciation_move_count": passed_move_count,
                "blocked_count": len(blocked),
                "blocked_examples": blocked[:20],
                "mismatch_count": len(mismatches),
                "mismatch_examples": mismatches[:20],
                "passed_examples": passed_examples,
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
                "warning_count": len(blocked) + len(mismatches),
                "statistics_json": stats,
                "notes": (
                    (
                        "Source assets and depreciation schedules are restored "
                        "as normal OCA assets and linked to the exact imported "
                        "source accounting entries. No additional journal entry "
                        "is generated."
                    )
                    if use_exact_imported_moves
                    else (
                        "Dedicated Track B OCA asset replay. Source asset values "
                        "and depreciation schedules are reconstructed as native "
                        "assets; OCA creates and posts the accounting entries."
                    )
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

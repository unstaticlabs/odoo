from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class RebuildAccountDeferral(models.Model):
    _name = "rebuild.account.deferral"
    _description = "Deferred Expense and Revenue Schedule"
    _inherit = ["rebuild.source.trace.mixin"]
    _order = "start_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, index=True)
    schedule_type = fields.Selection(
        [
            ("expense", "Deferred Expense"),
            ("revenue", "Deferred Revenue"),
        ],
        required=True,
        default="expense",
        index=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("running", "Running"),
            ("closed", "Closed"),
        ],
        required=True,
        default="draft",
        index=True,
        copy=False,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    original_move_id = fields.Many2one(
        "account.move",
        string="Original Bill or Invoice",
        required=True,
        check_company=True,
        index=True,
        ondelete="restrict",
    )
    journal_id = fields.Many2one(
        "account.journal",
        required=True,
        check_company=True,
        index=True,
        domain="[('type', '=', 'general'), ('company_id', '=', company_id)]",
    )
    deferral_account_id = fields.Many2one(
        "account.account",
        required=True,
        check_company=True,
        index=True,
        domain="[('company_ids', 'in', company_id)]",
    )
    start_date = fields.Date(required=True, index=True)
    end_date = fields.Date(required=True, index=True)
    line_ids = fields.One2many(
        "rebuild.account.deferral.line",
        "deferral_id",
        string="Schedule",
        copy=True,
    )
    scheduled_line_count = fields.Integer(
        compute="_compute_progress",
        store=True,
    )
    posted_line_count = fields.Integer(
        compute="_compute_progress",
        store=True,
    )
    remaining_line_count = fields.Integer(
        compute="_compute_progress",
        store=True,
    )
    scheduled_amount = fields.Monetary(
        compute="_compute_progress",
        currency_field="currency_id",
        store=True,
    )
    source_original_move_id = fields.Integer(index=True, copy=False)
    source_original_move_name = fields.Char(index=True, copy=False)
    note = fields.Text()

    @api.depends(
        "line_ids",
        "line_ids.state",
        "line_ids.recognition_balance",
    )
    def _compute_progress(self):
        for deferral in self:
            deferral.scheduled_line_count = len(deferral.line_ids)
            deferral.posted_line_count = len(
                deferral.line_ids.filtered(lambda line: line.state == "posted"),
            )
            deferral.remaining_line_count = len(
                deferral.line_ids.filtered(
                    lambda line: line.state == "scheduled",
                ),
            )
            deferral.scheduled_amount = sum(
                abs(line.recognition_balance)
                for line in deferral.line_ids
            )

    @api.constrains("start_date", "end_date")
    def _check_date_range(self):
        for deferral in self:
            if deferral.start_date > deferral.end_date:
                raise ValidationError(
                    _("The deferral end date must not precede its start date."),
                )

    def action_start(self):
        self.check_access("write")
        for deferral in self:
            if not deferral.line_ids:
                raise UserError(
                    _("Add at least one schedule line before starting a deferral."),
                )
            if deferral.state == "draft":
                deferral.state = "running"
        return True

    def _sync_state(self):
        for deferral in self:
            if (
                deferral.line_ids
                and all(
                    line.state == "posted"
                    for line in deferral.line_ids
                )
            ):
                deferral.state = "closed"
            elif deferral.state != "draft":
                deferral.state = "running"

    def action_post_due(self):
        self.check_access("write")
        cutoff = fields.Date.to_date(
            self.env.context.get("deferral_cutoff_date"),
        ) or fields.Date.context_today(self)
        for deferral in self:
            deferral.action_start()
            due_lines = deferral.line_ids.filtered(
                lambda line: (
                    line.state == "scheduled"
                    and line.date <= cutoff
                ),
            )
            due_lines.action_post()
        return True

    def action_open_original_move(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Original Bill or Invoice"),
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.original_move_id.id,
            "context": {"create": False, "delete": False},
        }

    def action_open_posted_entries(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Deferred Entries"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.line_ids.move_id.ids)],
            "context": {"create": False, "delete": False},
        }


class RebuildAccountDeferralLine(models.Model):
    _name = "rebuild.account.deferral.line"
    _description = "Deferred Expense and Revenue Schedule Line"
    _inherit = ["analytic.mixin", "rebuild.source.trace.mixin"]
    _order = "date, sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    deferral_id = fields.Many2one(
        "rebuild.account.deferral",
        required=True,
        index=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="deferral_id.company_id",
        store=True,
        index=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="deferral_id.currency_id",
        store=True,
        readonly=True,
    )
    date = fields.Date(required=True, index=True)
    phase = fields.Selection(
        [
            ("initial_deferral", "Initial Deferral"),
            ("recognition", "Recognition"),
        ],
        required=True,
        default="recognition",
        index=True,
    )
    recognition_account_id = fields.Many2one(
        "account.account",
        required=True,
        check_company=True,
        index=True,
        domain="[('company_ids', 'in', company_id)]",
    )
    partner_id = fields.Many2one(
        "res.partner",
        check_company=True,
        index=True,
    )
    recognition_balance = fields.Monetary(
        required=True,
        currency_field="currency_id",
    )
    recognition_amount_currency = fields.Monetary(
        required=True,
        currency_field="currency_id",
    )
    deferral_balance = fields.Monetary(
        required=True,
        currency_field="currency_id",
    )
    deferral_amount_currency = fields.Monetary(
        required=True,
        currency_field="currency_id",
    )
    state = fields.Selection(
        [
            ("scheduled", "Scheduled"),
            ("posted", "Posted"),
        ],
        required=True,
        default="scheduled",
        index=True,
        copy=False,
    )
    move_id = fields.Many2one(
        "account.move",
        string="Journal Entry",
        check_company=True,
        index=True,
        readonly=True,
        copy=False,
        ondelete="restrict",
    )
    source_move_name = fields.Char(index=True, copy=False)
    source_move_ref = fields.Char(copy=False)
    source_move_state = fields.Char(index=True, copy=False)
    source_recognition_line_id = fields.Integer(index=True, copy=False)
    source_deferral_line_id = fields.Integer(index=True, copy=False)

    @api.constrains(
        "recognition_balance",
        "deferral_balance",
        "recognition_amount_currency",
        "deferral_amount_currency",
    )
    def _check_balanced_amounts(self):
        for line in self:
            if not line.currency_id.is_zero(
                line.recognition_balance + line.deferral_balance,
            ):
                raise ValidationError(
                    _("The recognition and deferral balances must offset."),
                )
            if not line.currency_id.is_zero(
                line.recognition_amount_currency
                + line.deferral_amount_currency,
            ):
                raise ValidationError(
                    _("The recognition and deferral currency amounts must offset."),
                )

    def _move_values(self):
        self.ensure_one()
        deferral = self.deferral_id
        trace_base = {
            "rebuild_source_database": self.rebuild_source_database,
            "rebuild_source_snapshot": self.rebuild_source_snapshot,
            "rebuild_import_run_id": self.rebuild_import_run_id.id,
            "rebuild_import_status": "transformed",
        }
        return {
            "move_type": "entry",
            "date": self.date,
            "journal_id": deferral.journal_id.id,
            "company_id": deferral.company_id.id,
            "currency_id": self.currency_id.id,
            "partner_id": self.partner_id.id,
            "ref": self.source_move_ref or self.name,
            "auto_post": "no",
            "rebuild_source_model": "account.move",
            "rebuild_source_id": self.rebuild_source_id,
            **trace_base,
            "line_ids": [
                Command.create({
                    "name": self.name,
                    "account_id": self.recognition_account_id.id,
                    "partner_id": self.partner_id.id,
                    "currency_id": self.currency_id.id,
                    "balance": self.recognition_balance,
                    "amount_currency": self.recognition_amount_currency,
                    "analytic_distribution": (
                        self.analytic_distribution or False
                    ),
                    "rebuild_source_model": "account.move.line",
                    "rebuild_source_id": self.source_recognition_line_id,
                    **trace_base,
                }),
                Command.create({
                    "name": self.name,
                    "account_id": deferral.deferral_account_id.id,
                    "partner_id": self.partner_id.id,
                    "currency_id": self.currency_id.id,
                    "balance": self.deferral_balance,
                    "amount_currency": self.deferral_amount_currency,
                    "rebuild_source_model": "account.move.line",
                    "rebuild_source_id": self.source_deferral_line_id,
                    **trace_base,
                }),
            ],
        }

    def action_post(self):
        self.check_access("write")
        for line in self:
            if line.move_id:
                if line.move_id.state != "posted":
                    line.move_id.action_post()
                line.state = "posted"
                continue
            if line.deferral_id.state == "draft":
                line.deferral_id.action_start()
            move = (
                self.env["account.move"]
                .with_company(line.company_id)
                .create(line._move_values())
            )
            move.action_post()
            line.write({
                "move_id": move.id,
                "state": "posted",
            })
        self.deferral_id._sync_state()
        return True

    def action_open_move(self):
        self.ensure_one()
        if not self.move_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Deferred Journal Entry"),
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.move_id.id,
            "context": {"create": False, "delete": False},
        }


class RebuildAccountImportRun(models.Model):
    _inherit = "rebuild.account.import.run"

    def _native_deferral_schedule_rows(self, conn, options):
        return self._fetchall(
            conn,
            """
            WITH original_bounds AS (
                SELECT line.move_id,
                       min(line.deferred_start_date) AS start_date,
                       max(line.deferred_end_date) AS end_date
                  FROM account_move_line line
                 WHERE line.deferred_start_date IS NOT NULL
                    OR line.deferred_end_date IS NOT NULL
                 GROUP BY line.move_id
            )
            SELECT relation.original_move_id,
                   original.name AS original_name,
                   original.date AS original_date,
                   original.state AS original_state,
                   original.move_type AS original_move_type,
                   bounds.start_date,
                   bounds.end_date,
                   deferred.id AS source_move_id,
                   deferred.name AS source_move_name,
                   deferred.ref AS source_move_ref,
                   deferred.date,
                   deferred.state,
                   deferred.auto_post,
                   deferred.company_id,
                   deferred.journal_id,
                   deferred.partner_id,
                   deferred.currency_id,
                   company.deferred_expense_account_id,
                   company.deferred_revenue_account_id,
                   count(source_line.id)::integer AS source_line_count
              FROM account_move_deferred_rel relation
              JOIN account_move original
                ON original.id = relation.original_move_id
              JOIN account_move deferred
                ON deferred.id = relation.deferred_move_id
              JOIN res_company company
                ON company.id = deferred.company_id
              JOIN original_bounds bounds
                ON bounds.move_id = original.id
              JOIN account_move_line source_line
                ON source_line.move_id = deferred.id
             WHERE original.company_id = ANY(%(source_company_ids)s)
               AND original.state = 'posted'
               AND original.move_type IN (
                   'out_invoice', 'out_refund', 'in_invoice',
                   'in_refund', 'out_receipt', 'in_receipt'
               )
               AND original.date BETWEEN %(date_from)s AND %(date_to)s
             GROUP BY relation.original_move_id,
                      original.name,
                      original.date,
                      original.state,
                      original.move_type,
                      bounds.start_date,
                      bounds.end_date,
                      deferred.id,
                      deferred.name,
                      deferred.ref,
                      deferred.date,
                      deferred.state,
                      deferred.auto_post,
                      deferred.company_id,
                      deferred.journal_id,
                      deferred.partner_id,
                      deferred.currency_id,
                      company.deferred_expense_account_id,
                      company.deferred_revenue_account_id
             ORDER BY relation.original_move_id, deferred.date, deferred.id
            """,
            options,
        )

    def _native_deferral_source_line_rows(self, conn, source_move_ids):
        if not source_move_ids:
            return []
        return self._fetchall(
            conn,
            """
            SELECT line.id,
                   line.move_id,
                   line.sequence,
                   line.account_id,
                   line.name,
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

    def _native_deferral_opening_rows(self, conn, options):
        source_ids = options.get(
            "opening_boundary_source_move_ids",
            [8871],
        )
        if not source_ids:
            return []
        return self._fetchall(
            conn,
            """
            SELECT move.id AS source_move_id,
                   move.name AS source_move_name,
                   move.ref AS source_move_ref,
                   move.date,
                   move.state,
                   move.company_id,
                   move.journal_id,
                   move.partner_id,
                   move.currency_id,
                   count(line.id)::integer AS source_line_count
              FROM account_move move
              JOIN account_move_line line ON line.move_id = move.id
             WHERE move.id = ANY(%(source_move_ids)s)
               AND move.company_id = ANY(%(source_company_ids)s)
               AND move.state = 'posted'
               AND move.move_type = 'entry'
               AND move.date BETWEEN %(date_from)s AND %(date_to)s
             GROUP BY move.id,
                      move.name,
                      move.ref,
                      move.date,
                      move.state,
                      move.company_id,
                      move.journal_id,
                      move.partner_id,
                      move.currency_id
             ORDER BY move.date, move.id
            """,
            {**options, "source_move_ids": source_ids},
        )

    @staticmethod
    def _native_deferral_phase(schedule_type, deferral_balance):
        if schedule_type == "expense":
            return (
                "initial_deferral"
                if deferral_balance > 0
                else "recognition"
            )
        return (
            "initial_deferral"
            if deferral_balance < 0
            else "recognition"
        )

    def _native_deferral_move_check(
        self,
        source_row,
        source_lines,
        move,
        analytic_accounts,
    ):
        source_by_line = {line["id"]: line for line in source_lines}
        target_by_line = {
            line.rebuild_source_id: line
            for line in move.line_ids
            if line.rebuild_source_model == "account.move.line"
        }
        amount_matches = True
        analytic_matches = True
        for source_id, source_line in source_by_line.items():
            target_line = target_by_line.get(source_id)
            if not target_line:
                amount_matches = False
                analytic_matches = False
                continue
            amount_matches = amount_matches and (
                round(target_line.balance, 2)
                == round(self._amount(source_line["balance"]), 2)
                and round(target_line.amount_currency, 2)
                == round(
                    self._amount(source_line["amount_currency"]),
                    2,
                )
            )
            expected_distribution = (
                self._native_replay_analytic_distribution(
                    source_line["analytic_distribution"],
                    analytic_accounts,
                )
                or {}
            )
            analytic_matches = analytic_matches and (
                (target_line.analytic_distribution or {})
                == expected_distribution
            )
        source_account_totals = defaultdict(float)
        for source_line in source_lines:
            source_account_totals[source_line["account_id"]] += self._amount(
                source_line["balance"],
            )
        target_account_totals = defaultdict(float)
        for target_line in move.line_ids:
            target_account_totals[
                target_line.account_id.rebuild_source_id
            ] += target_line.balance
        return {
            "date_matches": move.date == source_row["date"],
            "state_matches": move.state == source_row["state"] == "posted",
            "line_count_matches": (
                len(move.line_ids)
                == len(source_lines)
                == source_row["source_line_count"]
            ),
            "amounts_match": amount_matches,
            "analytic_distributions_match": analytic_matches,
            "account_totals_match": {
                key: round(value, 2)
                for key, value in source_account_totals.items()
            } == {
                key: round(value, 2)
                for key, value in target_account_totals.items()
            },
        }

    def _native_deferral_opening_move(
        self,
        options,
        source_row,
        source_lines,
        companies,
        accounts,
        journals,
        partners,
        currencies,
        analytic_accounts,
    ):
        Move = self.env["account.move"].sudo().with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        existing = Move.search([
            ("rebuild_source_model", "=", "account.move"),
            ("rebuild_source_snapshot", "=", options["source_snapshot_id"]),
            ("rebuild_source_id", "=", source_row["source_move_id"]),
        ], limit=1)
        if existing:
            return existing, False
        company = companies[source_row["company_id"]]
        trace = {
            **self._trace_values(
                "account.move",
                source_row["source_move_id"],
                options,
            ),
            "rebuild_import_status": "transformed",
        }
        move = Move.with_company(company).create({
            "move_type": "entry",
            "date": source_row["date"],
            "journal_id": journals[source_row["journal_id"]].id,
            "company_id": company.id,
            "currency_id": currencies[source_row["currency_id"]].id,
            "partner_id": (
                partners[source_row["partner_id"]].id
                if source_row["partner_id"] in partners
                else False
            ),
            "ref": source_row["source_move_ref"],
            "auto_post": "no",
            **trace,
            "line_ids": [
                Command.create({
                    "name": source_line["name"]
                    or source_row["source_move_ref"],
                    "account_id": accounts[
                        source_line["account_id"]
                    ].id,
                    "partner_id": (
                        partners[source_row["partner_id"]].id
                        if source_row["partner_id"] in partners
                        else False
                    ),
                    "currency_id": currencies[
                        source_row["currency_id"]
                    ].id,
                    "balance": self._amount(source_line["balance"]),
                    "amount_currency": self._amount(
                        source_line["amount_currency"],
                    ),
                    "analytic_distribution": (
                        self._native_replay_analytic_distribution(
                            source_line["analytic_distribution"],
                            analytic_accounts,
                        )
                        or False
                    ),
                    **self._trace_values(
                        "account.move.line",
                        source_line["id"],
                        options,
                    ),
                    "rebuild_import_status": "transformed",
                })
                for source_line in source_lines
            ],
        })
        move.action_post()
        return move, True

    def run_native_deferral_replay_from_source(self, options):
        """Replay source deferral schedules through native journal entries."""
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
            "opening_boundary_source_move_ids": [8871],
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
            companies, _company_rows = self._company_map(
                conn,
                options,
                countries,
            )
            partners = self._partner_map(conn, options)
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
                partners,
                analytic_plans,
            )
            schedule_rows = self._native_deferral_schedule_rows(
                conn,
                options,
            )
            opening_rows = self._native_deferral_opening_rows(
                conn,
                options,
            )
            all_source_move_ids = [
                row["source_move_id"]
                for row in schedule_rows + opening_rows
            ]
            source_lines_by_move = defaultdict(list)
            for source_line in self._native_deferral_source_line_rows(
                conn,
                all_source_move_ids,
            ):
                source_lines_by_move[source_line["move_id"]].append(
                    source_line,
                )

            original_move_ids = {
                row["original_move_id"]
                for row in schedule_rows
            }
            target_original_moves = {
                move.rebuild_source_id: move
                for move in self.env["account.move"].sudo().search([
                    (
                        "rebuild_source_model",
                        "in",
                        [
                            "account.move",
                            "account.move.native_engine_replay",
                        ],
                    ),
                    (
                        "rebuild_source_snapshot",
                        "=",
                        options["source_snapshot_id"],
                    ),
                    ("rebuild_source_id", "in", list(original_move_ids)),
                ])
            }

            Deferral = self.env["rebuild.account.deferral"].sudo()
            DeferralLine = self.env[
                "rebuild.account.deferral.line"
            ].sudo()
            deferrals = {}
            created_deferral_count = 0
            created_schedule_line_count = 0
            created_move_count = 0
            reused_move_count = 0
            passed_move_count = 0
            created_opening_move_count = 0
            reused_opening_move_count = 0
            passed_opening_move_count = 0
            blocked = []
            mismatches = []
            passed_examples = []

            for source_row in schedule_rows:
                source_lines = source_lines_by_move[
                    source_row["source_move_id"]
                ]
                expense_account_id = source_row[
                    "deferred_expense_account_id"
                ]
                revenue_account_id = source_row[
                    "deferred_revenue_account_id"
                ]
                deferral_source_line = next(
                    (
                        line
                        for line in source_lines
                        if line["account_id"]
                        in {expense_account_id, revenue_account_id}
                    ),
                    None,
                )
                recognition_source_lines = [
                    line
                    for line in source_lines
                    if not deferral_source_line
                    or line["id"] != deferral_source_line["id"]
                ]
                target_original = target_original_moves.get(
                    source_row["original_move_id"],
                )
                required_account_ids = {
                    line["account_id"]
                    for line in source_lines
                }
                missing_account_ids = sorted(
                    source_id
                    for source_id in required_account_ids
                    if source_id not in accounts
                )
                if (
                    source_row["source_line_count"] != 2
                    or not deferral_source_line
                    or len(recognition_source_lines) != 1
                    or not target_original
                    or source_row["company_id"] not in companies
                    or source_row["journal_id"] not in journals
                    or source_row["currency_id"] not in currencies
                    or missing_account_ids
                ):
                    blocked.append({
                        "source_move_id": source_row["source_move_id"],
                        "source_original_move_id": source_row[
                            "original_move_id"
                        ],
                        "classification": (
                            "missing_or_ambiguous_deferral_configuration"
                        ),
                        "source_line_count": source_row[
                            "source_line_count"
                        ],
                        "deferral_line_found": bool(
                            deferral_source_line,
                        ),
                        "recognition_line_count": len(
                            recognition_source_lines,
                        ),
                        "target_original_found": bool(target_original),
                        "missing_account_ids": missing_account_ids,
                    })
                    continue
                recognition_source_line = recognition_source_lines[0]
                schedule_type = (
                    "expense"
                    if deferral_source_line["account_id"]
                    == expense_account_id
                    else "revenue"
                )
                deferral_key = (
                    source_row["original_move_id"],
                    schedule_type,
                    deferral_source_line["account_id"],
                )
                deferral = deferrals.get(deferral_key)
                if not deferral:
                    deferral = Deferral.search([
                        (
                            "rebuild_source_snapshot",
                            "=",
                            options["source_snapshot_id"],
                        ),
                        (
                            "source_original_move_id",
                            "=",
                            source_row["original_move_id"],
                        ),
                        ("schedule_type", "=", schedule_type),
                        (
                            "deferral_account_id",
                            "=",
                            accounts[
                                deferral_source_line["account_id"]
                            ].id,
                        ),
                    ], limit=1)
                    deferral_values = {
                        "name": _(
                            "%(kind)s — %(move)s",
                            kind=(
                                "Deferred expenses"
                                if schedule_type == "expense"
                                else "Deferred revenue"
                            ),
                            move=source_row["original_name"],
                        ),
                        "schedule_type": schedule_type,
                        "company_id": companies[
                            source_row["company_id"]
                        ].id,
                        "original_move_id": target_original.id,
                        "journal_id": journals[
                            source_row["journal_id"]
                        ].id,
                        "deferral_account_id": accounts[
                            deferral_source_line["account_id"]
                        ].id,
                        "start_date": source_row["start_date"],
                        "end_date": source_row["end_date"],
                        "source_original_move_id": source_row[
                            "original_move_id"
                        ],
                        "source_original_move_name": source_row[
                            "original_name"
                        ],
                        "note": (
                            "Native operational schedule reconstructed from "
                            "the source deferred-entry relation. Posted "
                            "periods are replayed through standard journal "
                            "entry posting; future periods remain scheduled."
                        ),
                        **self._trace_values(
                            "account.move.deferred.schedule",
                            source_row["original_move_id"],
                            options,
                        ),
                    }
                    if deferral:
                        deferral.write(deferral_values)
                        deferral.rebuild_import_status = "reused"
                    else:
                        deferral = Deferral.create(deferral_values)
                        created_deferral_count += 1
                    deferrals[deferral_key] = deferral

                analytic_distribution = (
                    self._native_replay_analytic_distribution(
                        recognition_source_line[
                            "analytic_distribution"
                        ],
                        analytic_accounts,
                    )
                    or False
                )
                line = DeferralLine.search([
                    (
                        "rebuild_source_model",
                        "=",
                        "account_move_deferred_rel",
                    ),
                    (
                        "rebuild_source_snapshot",
                        "=",
                        options["source_snapshot_id"],
                    ),
                    (
                        "rebuild_source_id",
                        "=",
                        source_row["source_move_id"],
                    ),
                ], limit=1)
                line_values = {
                    "name": (
                        recognition_source_line["name"]
                        or source_row["source_move_ref"]
                        or source_row["original_name"]
                    ),
                    "deferral_id": deferral.id,
                    "sequence": recognition_source_line["sequence"],
                    "date": source_row["date"],
                    "phase": self._native_deferral_phase(
                        schedule_type,
                        self._amount(deferral_source_line["balance"]),
                    ),
                    "recognition_account_id": accounts[
                        recognition_source_line["account_id"]
                    ].id,
                    "partner_id": (
                        partners[source_row["partner_id"]].id
                        if source_row["partner_id"] in partners
                        else False
                    ),
                    "recognition_balance": self._amount(
                        recognition_source_line["balance"],
                    ),
                    "recognition_amount_currency": self._amount(
                        recognition_source_line["amount_currency"],
                    ),
                    "deferral_balance": self._amount(
                        deferral_source_line["balance"],
                    ),
                    "deferral_amount_currency": self._amount(
                        deferral_source_line["amount_currency"],
                    ),
                    "analytic_distribution": analytic_distribution,
                    "source_move_name": source_row["source_move_name"],
                    "source_move_ref": source_row["source_move_ref"],
                    "source_move_state": source_row["state"],
                    "source_recognition_line_id": (
                        recognition_source_line["id"]
                    ),
                    "source_deferral_line_id": deferral_source_line["id"],
                    **self._trace_values(
                        "account_move_deferred_rel",
                        source_row["source_move_id"],
                        options,
                    ),
                }
                if line:
                    line.write(line_values)
                    line.rebuild_import_status = "reused"
                else:
                    line = DeferralLine.create(line_values)
                    created_schedule_line_count += 1
                if deferral.state == "draft":
                    deferral.action_start()

                should_be_posted = (
                    source_row["state"] == "posted"
                    and source_row["date"]
                    <= fields.Date.to_date(options["date_to"])
                )
                if should_be_posted:
                    if line.move_id:
                        reused_move_count += 1
                    else:
                        line.action_post()
                        created_move_count += 1
                    checks = self._native_deferral_move_check(
                        source_row,
                        source_lines,
                        line.move_id,
                        analytic_accounts,
                    )
                    result = {
                        "source_original_move_id": source_row[
                            "original_move_id"
                        ],
                        "source_move_id": source_row["source_move_id"],
                        "target_deferral_id": deferral.id,
                        "target_move_id": line.move_id.id,
                        "date": str(source_row["date"]),
                        "checks": checks,
                    }
                    if all(checks.values()):
                        passed_move_count += 1
                        if len(passed_examples) < 10:
                            passed_examples.append(result)
                    else:
                        mismatches.append({
                            **result,
                            "classification": (
                                "deferral_schedule_move_mismatch"
                            ),
                        })
                elif line.move_id:
                    mismatches.append({
                        "source_move_id": source_row["source_move_id"],
                        "target_move_id": line.move_id.id,
                        "classification": (
                            "future_source_schedule_was_posted"
                        ),
                    })

            target_deferrals = Deferral.browse([
                deferral.id
                for deferral in deferrals.values()
            ])
            target_deferrals._sync_state()
            for deferral in target_deferrals:
                expected_state = (
                    "running"
                    if deferral.remaining_line_count
                    else "closed"
                )
                if deferral.state != expected_state:
                    mismatches.append({
                        "source_original_move_id": (
                            deferral.source_original_move_id
                        ),
                        "target_deferral_id": deferral.id,
                        "classification": (
                            "deferral_schedule_state_mismatch"
                        ),
                        "expected_state": expected_state,
                        "target_state": deferral.state,
                        "remaining_line_count": (
                            deferral.remaining_line_count
                        ),
                    })

            for source_row in opening_rows:
                source_lines = source_lines_by_move[
                    source_row["source_move_id"]
                ]
                required_account_ids = {
                    line["account_id"]
                    for line in source_lines
                }
                missing_account_ids = sorted(
                    source_id
                    for source_id in required_account_ids
                    if source_id not in accounts
                )
                if (
                    source_row["source_line_count"] != len(source_lines)
                    or source_row["company_id"] not in companies
                    or source_row["journal_id"] not in journals
                    or source_row["currency_id"] not in currencies
                    or missing_account_ids
                ):
                    blocked.append({
                        "source_move_id": source_row["source_move_id"],
                        "classification": (
                            "missing_opening_boundary_configuration"
                        ),
                        "missing_account_ids": missing_account_ids,
                    })
                    continue
                move, created = self._native_deferral_opening_move(
                    options,
                    source_row,
                    source_lines,
                    companies,
                    accounts,
                    journals,
                    partners,
                    currencies,
                    analytic_accounts,
                )
                created_opening_move_count += int(created)
                reused_opening_move_count += int(not created)
                checks = self._native_deferral_move_check(
                    source_row,
                    source_lines,
                    move,
                    analytic_accounts,
                )
                if all(checks.values()):
                    passed_opening_move_count += 1
                else:
                    mismatches.append({
                        "source_move_id": source_row["source_move_id"],
                        "target_move_id": move.id,
                        "classification": (
                            "opening_boundary_move_mismatch"
                        ),
                        "checks": checks,
                    })

            source_posted_rows = [
                row
                for row in schedule_rows
                if (
                    row["state"] == "posted"
                    and row["date"]
                    <= fields.Date.to_date(options["date_to"])
                )
            ]
            source_future_rows = [
                row
                for row in schedule_rows
                if row not in source_posted_rows
            ]
            expected_deferral_count = len({
                (
                    row["original_move_id"],
                    (
                        "expense"
                        if any(
                            line["account_id"]
                            == row["deferred_expense_account_id"]
                            for line in source_lines_by_move[
                                row["source_move_id"]
                            ]
                        )
                        else "revenue"
                    ),
                )
                for row in schedule_rows
            })
            status = (
                "passed"
                if not blocked
                and not mismatches
                and len(deferrals) == expected_deferral_count
                and passed_move_count == len(source_posted_rows)
                and passed_opening_move_count == len(opening_rows)
                else "failed"
            )
            stats = {
                "classification": (
                    "NATIVE_VALIDATION_NATIVE_DEFERRAL_AND_OPENING_REPLAY"
                ),
                "status": status,
                "date_from": options["date_from"],
                "date_to": options["date_to"],
                "source_company_ids": options["source_company_ids"],
                "source_deferral_count": len(deferrals),
                "created_deferral_count": created_deferral_count,
                "reused_deferral_count": (
                    len(deferrals) - created_deferral_count
                ),
                "running_deferral_count": len(
                    target_deferrals.filtered(
                        lambda deferral: deferral.state == "running",
                    ),
                ),
                "closed_deferral_count": len(
                    target_deferrals.filtered(
                        lambda deferral: deferral.state == "closed",
                    ),
                ),
                "source_schedule_line_count": len(schedule_rows),
                "created_schedule_line_count": (
                    created_schedule_line_count
                ),
                "reused_schedule_line_count": (
                    len(schedule_rows) - created_schedule_line_count
                ),
                "source_posted_deferral_move_count": len(
                    source_posted_rows,
                ),
                "source_future_schedule_line_count": len(
                    source_future_rows,
                ),
                "created_deferral_move_count": created_move_count,
                "reused_deferral_move_count": reused_move_count,
                "passed_deferral_move_count": passed_move_count,
                "source_opening_boundary_move_count": len(opening_rows),
                "created_opening_boundary_move_count": (
                    created_opening_move_count
                ),
                "reused_opening_boundary_move_count": (
                    reused_opening_move_count
                ),
                "passed_opening_boundary_move_count": (
                    passed_opening_move_count
                ),
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
                    "Dedicated Track B native deferral replay. Source "
                    "schedule rows become operational deferral records; "
                    "standard journal entries post due periods, future "
                    "periods remain scheduled, and the opening reversal is "
                    "replayed as a separately traced boundary entry."
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

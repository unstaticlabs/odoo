from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.safe_eval import safe_eval


class AccountAccountReconcile(models.Model):
    _inherit = "account.account.reconcile"
    _description = "General Reconciliation"

    selected_move_line_ids = fields.Many2many(
        "account.move.line",
        compute="_compute_rebuild_selection_summary",
        string="Selected items",
    )
    selected_count = fields.Integer(
        compute="_compute_rebuild_selection_summary",
    )
    selected_debit = fields.Monetary(
        compute="_compute_rebuild_selection_summary",
        currency_field="company_currency_id",
    )
    selected_credit = fields.Monetary(
        compute="_compute_rebuild_selection_summary",
        currency_field="company_currency_id",
    )
    selection_difference = fields.Monetary(
        compute="_compute_rebuild_selection_summary",
        currency_field="company_currency_id",
    )
    selection_reference_date = fields.Date(
        compute="_compute_rebuild_selection_summary",
    )
    selection_outcome = fields.Selection(
        selection=[
            ("choose", "Choose a counterpart"),
            ("full", "Full match"),
            ("partial", "Partial match"),
        ],
        compute="_compute_rebuild_selection_summary",
    )
    can_reconcile = fields.Boolean(
        compute="_compute_rebuild_selection_summary",
    )

    def _compute_reconcile_data_info(self):
        # Launch defaults must not replace a saved (even empty) selection on
        # the next RPC. This also repairs sessions opened before this upgrade.
        for record in self:
            saved = self.env["account.account.reconcile.data"].search([
                ("user_id", "=", self.env.uid),
                ("reconcile_id", "=", record.id),
            ], limit=1)
            if saved:
                record.reconcile_data_info = saved.data
            else:
                super(AccountAccountReconcile, record)._compute_reconcile_data_info()

    @api.depends("reconcile_data_info")
    def _compute_rebuild_selection_summary(self):
        for record in self:
            counterpart_ids = (
                record.reconcile_data_info or {}
            ).get("counterparts", [])
            lines = self.env["account.move.line"].browse(
                counterpart_ids,
            ).exists()
            positive = sum(
                lines.filtered(
                    lambda line: line.amount_residual > 0,
                ).mapped("amount_residual"),
            )
            negative = -sum(
                lines.filtered(
                    lambda line: line.amount_residual < 0,
                ).mapped("amount_residual"),
            )
            record.selected_move_line_ids = lines
            record.selected_count = len(lines)
            record.selected_debit = positive
            record.selected_credit = negative
            record.selection_difference = positive - negative
            record.selection_reference_date = (
                max(lines.mapped("date")) if lines else False
            )
            has_both_sides = bool(positive and negative)
            record.can_reconcile = len(lines) >= 2 and has_both_sides
            if not record.can_reconcile:
                record.selection_outcome = "choose"
            elif record.company_currency_id.is_zero(
                record.selection_difference,
            ):
                record.selection_outcome = "full"
            else:
                record.selection_outcome = "partial"

    def reconcile(self):
        self.ensure_one()
        lines = self.env["account.move.line"].browse(
            self.reconcile_data_info["counterparts"],
        ).exists()
        if len(lines) < 2:
            raise UserError(
                _("Select at least two journal items before matching."),
            )
        if len(lines.account_id) != 1:
            raise UserError(
                _("All selected items must use the same account."),
            )
        if not (
            any(lines.mapped("amount_residual"))
            and any(line.amount_residual > 0 for line in lines)
            and any(line.amount_residual < 0 for line in lines)
        ):
            raise UserError(
                _(
                    "Choose at least one debit and one credit. "
                    "Items on the same side cannot be matched.",
                ),
            )
        line_ids = lines.ids
        super().reconcile()
        lines.invalidate_recordset([
            "amount_residual",
            "amount_residual_currency",
            "reconciled",
            "matching_number",
            "matched_debit_ids",
            "matched_credit_ids",
        ])
        fully_matched = bool(lines) and all(lines.mapped("reconciled"))
        partially_matched = any(
            line.matched_debit_ids or line.matched_credit_ids
            for line in lines
        )
        if fully_matched:
            outcome = _("Fully matched")
        elif partially_matched:
            outcome = _("Partially matched — a residual remains")
        else:
            outcome = _("No matching was created")
        view = self.env.ref(
            "rebuild_account_migration."
            "view_rebuild_account_move_line_reconciliation_result",
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Reconciliation result — %s", outcome),
            "res_model": "account.move.line",
            "view_mode": "list,form",
            "views": [(view.id, "list"), (False, "form")],
            "domain": [("id", "in", line_ids)],
            "context": {
                "create": False,
                "delete": False,
                "reconciliation_result": True,
            },
        }


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    def action_reconcile_manually(self):
        action = super().action_reconcile_manually()
        if not action:
            return action
        # Seed once at launch, scoped to each company/account/partner/currency
        # workspace. Subsequent requests use OCA's per-user saved selection.
        context = dict(action["context"])
        selected = self.browse(context.pop("default_account_move_lines", []))
        # The workspace is a SQL view over these models, not a stored table.
        self.env["account.move.line"].flush_model()
        self.env["account.move"].flush_model()
        self.env["account.account"].flush_model()
        workspaces = self.env["account.account.reconcile"].with_context(
            active_test=False,
        ).search(action["domain"] + [("company_id", "in", self.company_id.ids)])
        for workspace in workspaces:
            lines = selected.filtered(lambda line: (
                line.company_id == workspace.company_id
                and line.account_id == workspace.account_id
                and line.currency_id == workspace.currency_id
                and (
                    workspace.account_id.account_type not in (
                        "asset_receivable", "liability_payable",
                    )
                    or line.partner_id == workspace.partner_id
                )
            ))
            workspace.reconcile_data_info = workspace._recompute_data({
                "data": [], "counterparts": lines.ids,
            })
        action["context"] = context
        return action

    rebuild_reconciliation_state = fields.Selection(
        selection=[
            ("open", "Unreconciled"),
            ("partial", "Partially matched"),
            ("full", "Fully matched"),
        ],
        compute="_compute_rebuild_reconciliation_display",
        string="Matching status",
    )
    rebuild_matching_color = fields.Integer(
        compute="_compute_rebuild_reconciliation_display",
    )

    @api.depends(
        "reconciled",
        "matching_number",
        "matched_debit_ids",
        "matched_credit_ids",
    )
    def _compute_rebuild_reconciliation_display(self):
        for line in self:
            if line.reconciled:
                line.rebuild_reconciliation_state = "full"
            elif line.matched_debit_ids or line.matched_credit_ids:
                line.rebuild_reconciliation_state = "partial"
            else:
                line.rebuild_reconciliation_state = "open"
            matching_reference = line.matching_number or ""
            color = 0
            for character in matching_reference:
                color = (
                    (color * 31) + ord(character)
                ) & 0xFFFFFFFF
            line.rebuild_matching_color = color % 10

    def action_rebuild_open_source(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.move_id.display_name,
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
        }

    @api.model
    def action_rebuild_open_matching_number(
        self,
        matching_number,
        company_id,
    ):
        matching_number = (matching_number or "").strip()
        company = self.env["res.company"].browse(company_id).exists()
        if not matching_number:
            raise UserError(
                _("This journal item has no matching reference."),
            )
        if not company or company not in self.env.companies:
            raise AccessError(
                _("You cannot inspect matching items for this company."),
            )
        action = self.env["ir.actions.actions"]._for_xml_id(
            "account.action_account_moves_all",
        )
        action.update({
            "name": _("Matching %(reference)s", reference=matching_number),
            "domain": [
                ("company_id", "=", company.id),
                ("matching_number", "=", matching_number),
                (
                    "display_type",
                    "not in",
                    ("line_section", "line_subsection", "line_note"),
                ),
            ],
            "context": {
                "allowed_company_ids": [company.id],
                "create": False,
                "delete": False,
            },
        })
        return action

    def action_rebuild_open_matching_items(self):
        self.ensure_one()
        return self.action_rebuild_open_matching_number(
            self.matching_number,
            self.company_id.id,
        )

    def action_rebuild_unreconcile(self):
        matched_lines = self.filtered(
            lambda line: (
                line.reconciled
                or line.matched_debit_ids
                or line.matched_credit_ids
            ),
        )
        matching_references = sorted(
            set(matched_lines.mapped("matching_number")) - {False},
        )
        line_ids = matched_lines.ids
        matched_lines.remove_move_reconcile()
        reference_text = (
            ", ".join(matching_references)
            if matching_references
            else _("selected matching")
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Matching undone"),
                "message": _(
                    "%s is open again and can be matched differently.",
                    reference_text,
                ),
                "type": "success",
                "sticky": False,
                "next": {
                    "type": "ir.actions.act_window",
                    "name": _("Matching undone — items are open again"),
                    "res_model": "account.move.line",
                    "view_mode": "list,form",
                    "views": [
                        (
                            self.env.ref(
                                "rebuild_account_migration."
                                "view_rebuild_account_move_line_reconciliation_result",
                            ).id,
                            "list",
                        ),
                        (False, "form"),
                    ],
                    "domain": [("id", "in", line_ids)],
                    "context": {
                        "create": False,
                        "delete": False,
                        "reconciliation_result": True,
                    },
                },
            },
        }

    @api.model
    def _reconcile_closest_amount_key(self, statement_line, move_line):
        target_currency = (
            statement_line.foreign_currency_id or statement_line.currency_id
        )
        target_amount = (
            statement_line.amount_currency
            if statement_line.foreign_currency_id
            else statement_line.amount
        )
        if move_line.currency_id == target_currency:
            candidate_amount = move_line.amount_residual_currency
        else:
            candidate_amount = move_line.amount_residual
            if target_currency != statement_line.company_id.currency_id:
                target_amount = target_currency._convert(
                    target_amount,
                    statement_line.company_id.currency_id,
                    statement_line.company_id,
                    statement_line.date,
                )
        return abs(candidate_amount - target_amount)

    @api.model
    def _reconcile_closest_date_key(self, statement_line, move_line):
        return abs((move_line.date - statement_line.date).days)

    @api.model
    @api.readonly
    def web_search_read(
        self,
        domain,
        specification,
        offset=0,
        limit=None,
        order=None,
        count_limit=None,
    ):
        general_amount = self.env.context.get(
            "general_reconcile_target_amount",
        )
        general_date = self.env.context.get(
            "general_reconcile_reference_date",
        )
        general_closest_amount = self.env.context.get(
            "general_reconcile_closest_amount",
        )
        general_closest_date = self.env.context.get(
            "general_reconcile_closest_date",
        )
        if (
            (general_closest_amount or general_closest_date)
            and general_amount
        ):
            candidates = self.search(domain, order=order)

            def general_ranking_key(line):
                key = []
                if general_closest_amount:
                    key.append(
                        abs(
                            abs(line.amount_residual)
                            - abs(general_amount),
                        ),
                    )
                if general_closest_date and general_date:
                    key.append(
                        abs(
                            (
                                line.date
                                - fields.Date.to_date(general_date)
                            ).days,
                        ),
                    )
                key.extend([-line.date.toordinal(), line.id])
                return tuple(key)

            ranked_ids = [
                line.id
                for line in sorted(candidates, key=general_ranking_key)
            ]
            page_ids = ranked_ids[
                offset : offset + limit if limit else None
            ]
            records = self.browse(page_ids).web_read(specification)
            length = len(ranked_ids)
            if count_limit:
                length = min(length, count_limit)
            return {"length": length, "records": records}

        statement_line_id = self.env.context.get("reconcile_statement_line_id")
        closest_amount = self.env.context.get("reconcile_closest_amount")
        closest_date = self.env.context.get("reconcile_closest_date")
        if not (closest_amount or closest_date) or not statement_line_id:
            return super().web_search_read(
                domain,
                specification,
                offset=offset,
                limit=limit,
                order=order,
                count_limit=count_limit,
            )

        statement_line = self.env["account.bank.statement.line"].browse(
            statement_line_id,
        ).exists()
        if not statement_line:
            return super().web_search_read(
                domain,
                specification,
                offset=offset,
                limit=limit,
                order=order,
                count_limit=count_limit,
            )

        candidates = self.search(domain, order=order)

        def ranking_key(line):
            key = []
            if closest_amount:
                key.append(
                    self._reconcile_closest_amount_key(statement_line, line),
                )
            if closest_date:
                key.append(
                    self._reconcile_closest_date_key(statement_line, line),
                )
            return tuple(key)

        ranked_ids = [
            line.id
            for line in sorted(
                candidates,
                key=ranking_key,
            )
        ]
        page_ids = ranked_ids[offset : offset + limit if limit else None]
        records = self.browse(page_ids).web_read(specification)
        length = len(ranked_ids)
        if count_limit:
            length = min(length, count_limit)
        return {
            "length": length,
            "records": records,
        }


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    rebuild_review_will_reconcile = fields.Boolean(
        compute="_compute_rebuild_review_will_reconcile",
    )

    @api.depends("can_reconcile", "journal_id.reconcile_mode")
    def _compute_rebuild_review_will_reconcile(self):
        for statement_line in self:
            statement_line.rebuild_review_will_reconcile = (
                statement_line.can_reconcile
                and statement_line.journal_id.reconcile_mode == "edit"
            )

    def _auto_reconcile(self):
        if self.env.context.get("rebuild_skip_auto_reconcile"):
            for statement_line in self:
                statement_line.reconcile_data_info = (
                    statement_line._default_reconcile_data()
                )
            return None
        return super()._auto_reconcile()

    rebuild_transaction_status = fields.Selection(
        selection=[
            ("open", "To match"),
            ("partial", "Partially matched"),
            ("matched", "Matched"),
        ],
        compute="_compute_rebuild_transaction_display",
        string="Matching status",
    )
    rebuild_review_state = fields.Selection(
        related="move_id.review_state",
        string="Review status",
    )
    rebuild_remaining_amount = fields.Monetary(
        compute="_compute_rebuild_transaction_display",
        currency_field="currency_id",
        string="Remaining",
    )
    rebuild_matching_reference = fields.Char(
        compute="_compute_rebuild_transaction_display",
        string="Match ref.",
    )
    rebuild_matching_color = fields.Integer(
        compute="_compute_rebuild_transaction_display",
    )
    rebuild_linked_move_id = fields.Many2one(
        "account.move",
        compute="_compute_rebuild_transaction_display",
        string="Linked document or entry",
    )
    # Compatibility for web clients that cached the transaction list before
    # rebuild_linked_move_id replaced the former display-only field. Keep this
    # out of current views; it can be removed after the next major upgrade.
    rebuild_linked_document = fields.Char(
        compute="_compute_rebuild_transaction_display",
        string="Legacy linked document",
    )

    @api.depends(
        "amount",
        "is_reconciled",
        "journal_id.default_account_id",
        "move_id.review_state",
        "move_id.line_ids.amount_residual",
        "move_id.line_ids.matching_number",
        "move_id.line_ids.matched_debit_ids",
        "move_id.line_ids.matched_credit_ids",
    )
    def _compute_rebuild_transaction_display(self):
        for statement_line in self:
            move_lines = statement_line.move_id.line_ids
            liquidity_account = statement_line.journal_id.default_account_id
            counterpart_lines = move_lines.filtered(
                lambda line: line.account_id != liquidity_account,
            )
            statement_line.rebuild_remaining_amount = abs(sum(
                counterpart_lines.mapped("amount_residual"),
            ))
            matching_references = sorted(
                set(counterpart_lines.mapped("matching_number")) - {False},
            )
            statement_line.rebuild_matching_reference = ", ".join(
                matching_references,
            )
            color = 0
            for character in statement_line.rebuild_matching_reference:
                color = ((color * 31) + ord(character)) & 0xFFFFFFFF
            statement_line.rebuild_matching_color = color % 10

            partials = (
                counterpart_lines.matched_debit_ids
                | counterpart_lines.matched_credit_ids
            )
            if (
                statement_line.is_reconciled
                or (
                    partials
                    and statement_line.currency_id.is_zero(
                        statement_line.rebuild_remaining_amount,
                    )
                )
            ):
                statement_line.rebuild_transaction_status = "matched"
            elif partials:
                statement_line.rebuild_transaction_status = "partial"
            else:
                statement_line.rebuild_transaction_status = "open"

            linked_lines = (
                partials.debit_move_id | partials.credit_move_id
            ) - move_lines
            linked_moves = linked_lines.move_id.filtered(
                lambda move: move != statement_line.move_id,
            )
            business_moves = linked_moves.filtered(
                lambda move: move.move_type != "entry",
            )
            statement_line.rebuild_linked_move_id = (
                business_moves[:1] or linked_moves[:1]
            )
            statement_line.rebuild_linked_document = (
                statement_line.rebuild_linked_move_id.display_name
            )

    def action_rebuild_open_matching_items(self):
        self.ensure_one()
        matching_references = [
            reference.strip()
            for reference in (self.rebuild_matching_reference or "").split(",")
            if reference.strip()
        ]
        if not matching_references:
            raise UserError(
                _("This bank transaction has no matching reference."),
            )

        action = self.env["account.move.line"].action_rebuild_open_matching_number(
            matching_references[0],
            self.company_id.id,
        )
        if len(matching_references) > 1:
            action.update({
                "name": _("Matched items"),
                "domain": [
                    ("company_id", "=", self.company_id.id),
                    ("matching_number", "in", matching_references),
                    (
                        "display_type",
                        "not in",
                        ("line_section", "line_subsection", "line_note"),
                    ),
                ],
            })
        return action

    def action_rebuild_open_bank_matching(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "rebuild_account_migration."
            "action_rebuild_account_reconcile_bank_transactions",
        )
        action["domain"] = [("id", "=", self.id)]
        action["context"] = {
            "create": False,
            "view_ref": (
                "account_reconcile_oca."
                "bank_statement_line_form_reconcile_view"
            ),
        }
        return action


class AccountJournal(models.Model):
    _inherit = "account.journal"

    def open_action(self):
        self.ensure_one()
        if self.type in {"bank", "cash"}:
            return self.action_rebuild_open_transactions()
        return super().open_action()

    def _rebuild_statement_line_action(self, *, matching=False):
        self.ensure_one()
        xmlid = (
            "rebuild_account_migration."
            "action_rebuild_account_reconcile_bank_transactions"
            if matching
            else "account_statement_base.account_bank_statement_line_action"
        )
        action = self.env["ir.actions.actions"]._for_xml_id(xmlid)
        action["name"] = _(
            "%(action)s — %(journal)s",
            action="Bank Matching" if matching else "Transactions",
            journal=self.display_name,
        )
        action["domain"] = [
            ("journal_id", "=", self.id),
        ]
        context = action.get("context") or {}
        if isinstance(context, str):
            context = safe_eval(context)
        action["context"] = {
            **context,
            "active_id": self.id,
            "active_ids": self.ids,
            "active_model": self._name,
            "default_journal_id": self.id,
            "create": False,
            **({"search_default_not_reconciled": 1} if matching else {}),
        }
        return action

    def action_rebuild_open_transactions(self):
        return self._rebuild_statement_line_action()

    def action_rebuild_open_bank_matching(self):
        return self._rebuild_statement_line_action(matching=True)

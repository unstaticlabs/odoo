from odoo import _, api, fields, models


class AccountAccountReconcile(models.Model):
    _inherit = "account.account.reconcile"
    _description = "General Reconciliation"

    def reconcile(self):
        self.ensure_one()
        lines = self.env["account.move.line"].browse(
            self.reconcile_data_info["counterparts"],
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
                    "type": "ir.actions.client",
                    "tag": "soft_reload",
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

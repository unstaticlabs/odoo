from odoo import api, models


class AccountAccountReconcile(models.Model):
    _inherit = "account.account.reconcile"
    _description = "General Reconciliation"


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

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

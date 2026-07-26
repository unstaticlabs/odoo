from odoo import fields, models


class RebuildAccountDeferredScheduleLine(models.Model):
    _name = "rebuild.account.deferred.schedule.line"
    _description = "USL Source Deferred Expense and Revenue Schedule"
    _inherit = ["rebuild.source.trace.mixin"]
    _order = "company_id, schedule_type, deferred_date, source_original_move_id, source_deferred_move_id"

    name = fields.Char(required=True, index=True)
    schedule_type = fields.Selection(
        [
            ("expense", "Deferred Expense"),
            ("revenue", "Deferred Revenue"),
            ("unknown", "Unknown Deferred Type"),
        ],
        required=True,
        default="unknown",
        index=True,
    )
    schedule_phase = fields.Selection(
        [
            ("initial_deferral", "Initial Deferral"),
            ("recognition", "Recognition"),
            ("unknown", "Unknown"),
        ],
        required=True,
        default="unknown",
        index=True,
    )
    representation_status = fields.Selection(
        [
            ("imported_posted_entry", "Imported Posted Entry"),
            ("source_draft_forecast", "Source Draft Forecast"),
            ("source_not_replayed", "Source Not Replayed"),
        ],
        required=True,
        default="source_not_replayed",
        index=True,
    )
    review_status = fields.Selection(
        [
            ("represented", "Represented"),
            ("review_required", "Review Required"),
        ],
        required=True,
        default="represented",
        index=True,
    )

    company_id = fields.Many2one("res.company", required=True, index=True)
    source_company_id = fields.Integer(index=True, copy=False)
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Company Currency",
        readonly=True,
    )
    currency_id = fields.Many2one("res.currency", required=True)
    journal_id = fields.Many2one("account.journal", index=True)
    partner_id = fields.Many2one("res.partner", index=True)

    original_move_id = fields.Many2one("account.move", index=True, ondelete="set null")
    deferred_move_id = fields.Many2one("account.move", index=True, ondelete="set null")
    original_move_imported = fields.Boolean(copy=False)
    deferred_move_imported = fields.Boolean(copy=False)

    source_original_move_id = fields.Integer(index=True, copy=False)
    source_deferred_move_id = fields.Integer(index=True, copy=False)
    source_original_name = fields.Char(copy=False)
    source_deferred_name = fields.Char(copy=False)
    source_original_state = fields.Char(index=True, copy=False)
    source_deferred_state = fields.Char(index=True, copy=False)
    source_original_move_type = fields.Char(index=True, copy=False)
    source_deferred_move_type = fields.Char(index=True, copy=False)
    original_date = fields.Date(index=True)
    deferred_date = fields.Date(index=True)
    deferred_start_date = fields.Date(index=True)
    deferred_end_date = fields.Date(index=True)

    deferred_account_code = fields.Char(index=True)
    deferred_account_name = fields.Char()
    counterpart_account_codes = fields.Char()
    counterpart_account_names = fields.Char()
    source_line_count = fields.Integer(copy=False)
    source_original_deferred_line_count = fields.Integer(copy=False)
    amount = fields.Monetary(currency_field="company_currency_id")
    deferred_account_balance = fields.Monetary(currency_field="company_currency_id")
    counterpart_balance = fields.Monetary(currency_field="company_currency_id")
    note = fields.Text()

    def action_open_original_move(self):
        self.ensure_one()
        if not self.original_move_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": "Original Deferred Source Move",
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.original_move_id.id,
            "context": {"create": False, "delete": False},
        }

    def action_open_deferred_move(self):
        self.ensure_one()
        if self.deferred_move_id:
            return {
                "type": "ir.actions.act_window",
                "name": "Deferred Journal Entry",
                "res_model": "account.move",
                "view_mode": "form",
                "res_id": self.deferred_move_id.id,
                "context": {"create": False, "delete": False},
            }
        return False

    def action_open_journal_items(self):
        self.ensure_one()
        source_move_ids = [self.source_original_move_id, self.source_deferred_move_id]
        return {
            "type": "ir.actions.act_window",
            "name": "Deferred Schedule Journal Items",
            "res_model": "account.move.line",
            "view_mode": "list,form,pivot",
            "domain": [
                ("rebuild_source_model", "=", "account.move.line"),
                ("rebuild_source_snapshot", "=", self.rebuild_source_snapshot),
                ("move_id.rebuild_source_id", "in", [value for value in source_move_ids if value] or [0]),
            ],
            "context": {"create": False, "delete": False},
        }

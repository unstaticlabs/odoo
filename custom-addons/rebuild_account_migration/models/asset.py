from odoo import api, fields, models
from odoo.exceptions import UserError


class RebuildAccountAsset(models.Model):
    _name = "rebuild.account.asset"
    _inherit = "rebuild.source.trace.mixin"
    _description = "USL Rebuild Asset Register"
    _order = "acquisition_date, id"

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, index=True)
    currency_id = fields.Many2one("res.currency", required=True)
    state = fields.Char(index=True)
    active = fields.Boolean(default=True)
    asset_group_name = fields.Char()
    source_asset_group_id = fields.Integer(index=True)
    prorata_computation_type = fields.Char()
    prorata_date = fields.Date()
    acquisition_date = fields.Date(index=True)
    disposal_date = fields.Date(index=True)
    original_value = fields.Monetary(currency_field="currency_id")
    book_value = fields.Monetary(currency_field="currency_id")
    salvage_value = fields.Monetary(currency_field="currency_id")
    non_deductible_tax_value = fields.Monetary(currency_field="currency_id")
    already_depreciated_amount_import = fields.Monetary(currency_field="currency_id")
    imported_period_net_value = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_imported_period_net_value",
        store=True,
    )
    net_gain_on_sale = fields.Monetary(currency_field="currency_id")
    asset_paused_days = fields.Float()
    asset_account_id = fields.Many2one("account.account")
    depreciation_account_id = fields.Many2one("account.account")
    depreciation_expense_account_id = fields.Many2one("account.account")
    journal_id = fields.Many2one("account.journal")
    source_model_id = fields.Integer(index=True)
    source_parent_id = fields.Integer(index=True)
    source_depreciation_move_count = fields.Integer()
    imported_depreciation_move_count = fields.Integer()
    depreciation_move_ids = fields.Many2many("account.move", string="Imported Depreciation Entries")
    depreciation_schedule_line_ids = fields.One2many(
        "rebuild.account.asset.depreciation.schedule.line",
        "asset_id",
        string="Source Depreciation Schedule",
    )
    notes = fields.Text()

    @api.depends("original_value", "already_depreciated_amount_import")
    def _compute_imported_period_net_value(self):
        for asset in self:
            asset.imported_period_net_value = asset.original_value - asset.already_depreciated_amount_import


class RebuildAccountAssetDepreciationScheduleLine(models.Model):
    _name = "rebuild.account.asset.depreciation.schedule.line"
    _inherit = "rebuild.source.trace.mixin"
    _description = "USL Rebuild Asset Depreciation Schedule Line"
    _order = "asset_id, depreciation_date, source_move_id"

    asset_id = fields.Many2one(
        "rebuild.account.asset",
        required=True,
        index=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one("res.company", required=True, index=True)
    currency_id = fields.Many2one("res.currency", required=True)
    imported_move_id = fields.Many2one("account.move", readonly=True, index=True)
    source_asset_id = fields.Integer(index=True)
    source_move_id = fields.Integer(index=True)
    source_move_name = fields.Char(index=True)
    source_move_state = fields.Char(index=True)
    depreciation_date = fields.Date(index=True)
    move_ref = fields.Char()
    expense_amount = fields.Monetary(currency_field="currency_id")
    depreciation_amount = fields.Monetary(currency_field="currency_id")
    accumulated_depreciation_amount = fields.Monetary(currency_field="currency_id")
    net_book_value_after_line = fields.Monetary(currency_field="currency_id")
    source_line_count = fields.Integer()
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

    def action_open_imported_move(self):
        self.ensure_one()
        if not self.imported_move_id:
            raise UserError("This source depreciation schedule line has no imported target journal entry.")
        return {
            "type": "ir.actions.act_window",
            "name": "Imported Depreciation Entry",
            "res_model": "account.move",
            "res_id": self.imported_move_id.id,
            "view_mode": "form",
            "target": "current",
        }

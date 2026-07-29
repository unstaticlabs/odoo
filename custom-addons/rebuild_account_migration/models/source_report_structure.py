from odoo import fields, models
from odoo.exceptions import UserError


class RebuildAccountSourceReportLine(models.Model):
    _name = "rebuild.account.source.report.line"
    _description = "USL Source Accounting Report Line"
    _inherit = ["rebuild.source.trace.mixin"]
    _order = "source_report_id, sequence, source_line_id"

    name = fields.Char(required=True, index=True)
    source_line_id = fields.Integer(index=True, copy=False)
    source_report_id = fields.Integer(index=True, copy=False)
    source_parent_line_id = fields.Integer(index=True, copy=False)
    source_action_id = fields.Integer(copy=False)
    report_id = fields.Many2one("rebuild.account.source.report", index=True, ondelete="cascade")
    parent_line_id = fields.Many2one("rebuild.account.source.report.line", index=True, ondelete="set null")
    hierarchy_level = fields.Integer(copy=False)
    sequence = fields.Integer(copy=False)
    code = fields.Char(index=True, copy=False)
    localized_name = fields.Char(copy=False)
    groupby = fields.Char(copy=False)
    user_groupby = fields.Char(copy=False)
    horizontal_split_side = fields.Char(copy=False)
    foldable = fields.Boolean(copy=False)
    print_on_new_page = fields.Boolean(copy=False)
    hide_if_zero = fields.Boolean(copy=False)
    expression_count = fields.Integer(copy=False)

    def action_open_source_report(self):
        self.ensure_one()
        if not self.report_id:
            raise UserError("This source report line is not linked to an imported source report.")
        return {
            "type": "ir.actions.act_window",
            "name": "Source Accounting Report",
            "res_model": "rebuild.account.source.report",
            "view_mode": "form",
            "res_id": self.report_id.id,
            "context": {"create": False, "delete": False},
        }

    def action_open_expressions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Source Report Expressions",
            "res_model": "rebuild.account.source.report.expression",
            "view_mode": "list,form,pivot",
            "domain": [("line_id", "=", self.id)],
            "context": {"create": False, "delete": False},
        }


class RebuildAccountSourceReportExpression(models.Model):
    _name = "rebuild.account.source.report.expression"
    _description = "USL Source Accounting Report Expression"
    _inherit = ["rebuild.source.trace.mixin"]
    _order = "source_report_id, source_report_line_id, source_expression_id"

    name = fields.Char(required=True, index=True)
    source_expression_id = fields.Integer(index=True, copy=False)
    source_report_id = fields.Integer(index=True, copy=False)
    source_report_line_id = fields.Integer(index=True, copy=False)
    report_id = fields.Many2one("rebuild.account.source.report", index=True, ondelete="cascade")
    line_id = fields.Many2one("rebuild.account.source.report.line", index=True, ondelete="cascade")
    line_code = fields.Char(index=True, copy=False)
    line_name = fields.Char(copy=False)
    label = fields.Char(index=True, copy=False)
    engine = fields.Char(index=True, copy=False)
    formula = fields.Text(copy=False)
    subformula = fields.Text(copy=False)
    date_scope = fields.Char(copy=False)
    figure_type = fields.Char(copy=False)
    carryover_target = fields.Char(copy=False)
    green_on_positive = fields.Boolean(copy=False)
    blank_if_zero = fields.Boolean(copy=False)
    auditable = fields.Boolean(copy=False)

    def action_open_source_line(self):
        self.ensure_one()
        if not self.line_id:
            raise UserError("This source report expression is not linked to an imported source report line.")
        return {
            "type": "ir.actions.act_window",
            "name": "Source Report Line",
            "res_model": "rebuild.account.source.report.line",
            "view_mode": "form",
            "res_id": self.line_id.id,
            "context": {"create": False, "delete": False},
        }


class RebuildAccountSourceReportColumn(models.Model):
    _name = "rebuild.account.source.report.column"
    _description = "USL Source Accounting Report Column"
    _inherit = ["rebuild.source.trace.mixin"]
    _order = "source_report_id, sequence, source_column_id"

    name = fields.Char(required=True, index=True)
    source_column_id = fields.Integer(index=True, copy=False)
    source_report_id = fields.Integer(index=True, copy=False)
    report_id = fields.Many2one("rebuild.account.source.report", index=True, ondelete="cascade")
    sequence = fields.Integer(copy=False)
    expression_label = fields.Char(index=True, copy=False)
    figure_type = fields.Char(copy=False)
    sortable = fields.Boolean(copy=False)
    blank_if_zero = fields.Boolean(copy=False)

    def action_open_source_report(self):
        self.ensure_one()
        if not self.report_id:
            raise UserError("This source report column is not linked to an imported source report.")
        return {
            "type": "ir.actions.act_window",
            "name": "Source Accounting Report",
            "res_model": "rebuild.account.source.report",
            "view_mode": "form",
            "res_id": self.report_id.id,
            "context": {"create": False, "delete": False},
        }

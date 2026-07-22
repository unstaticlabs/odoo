from odoo import fields, models


class RebuildSourceTraceMixin(models.AbstractModel):
    _name = "rebuild.source.trace.mixin"
    _description = "USL Rebuild Source Trace"

    rebuild_source_database = fields.Char(index=True, copy=False)
    rebuild_source_model = fields.Char(index=True, copy=False)
    rebuild_source_id = fields.Integer(index=True, copy=False)
    rebuild_source_xmlid = fields.Char(index=True, copy=False)
    rebuild_source_snapshot = fields.Char(index=True, copy=False)
    rebuild_import_run_id = fields.Many2one(
        "rebuild.account.import.run",
        index=True,
        copy=False,
        ondelete="set null",
    )
    rebuild_import_status = fields.Selection(
        [
            ("imported", "Imported"),
            ("reused", "Reused Existing Record"),
            ("transformed", "Transformed"),
            ("skipped", "Skipped"),
            ("failed", "Failed"),
        ],
        index=True,
        copy=False,
    )
    rebuild_import_note = fields.Text(copy=False)


class ResCompany(models.Model):
    _name = "res.company"
    _inherit = ["res.company", "rebuild.source.trace.mixin"]


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "rebuild.source.trace.mixin"]


class AccountAccount(models.Model):
    _name = "account.account"
    _inherit = ["account.account", "rebuild.source.trace.mixin"]


class AccountJournal(models.Model):
    _name = "account.journal"
    _inherit = ["account.journal", "rebuild.source.trace.mixin"]


class AccountTaxGroup(models.Model):
    _name = "account.tax.group"
    _inherit = ["account.tax.group", "rebuild.source.trace.mixin"]


class AccountTax(models.Model):
    _name = "account.tax"
    _inherit = ["account.tax", "rebuild.source.trace.mixin"]


class AccountTaxRepartitionLine(models.Model):
    _name = "account.tax.repartition.line"
    _inherit = ["account.tax.repartition.line", "rebuild.source.trace.mixin"]


class AccountAccountTag(models.Model):
    _name = "account.account.tag"
    _inherit = ["account.account.tag", "rebuild.source.trace.mixin"]


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "rebuild.source.trace.mixin"]

    rebuild_source_move_type = fields.Char(index=True, copy=False)


class AccountMoveLine(models.Model):
    _name = "account.move.line"
    _inherit = ["account.move.line", "rebuild.source.trace.mixin"]


class IrAttachment(models.Model):
    _name = "ir.attachment"
    _inherit = ["ir.attachment", "rebuild.source.trace.mixin"]


class AccountPayment(models.Model):
    _name = "account.payment"
    _inherit = ["account.payment", "rebuild.source.trace.mixin"]


class AccountBankStatementLine(models.Model):
    _name = "account.bank.statement.line"
    _inherit = ["account.bank.statement.line", "rebuild.source.trace.mixin"]


class AccountAnalyticPlan(models.Model):
    _name = "account.analytic.plan"
    _inherit = ["account.analytic.plan", "rebuild.source.trace.mixin"]


class AccountAnalyticAccount(models.Model):
    _name = "account.analytic.account"
    _inherit = ["account.analytic.account", "rebuild.source.trace.mixin"]


class AccountAnalyticLine(models.Model):
    _name = "account.analytic.line"
    _inherit = ["account.analytic.line", "rebuild.source.trace.mixin"]

    rebuild_analytic_account_id = fields.Many2one(
        "account.analytic.account",
        index=True,
        copy=False,
        ondelete="set null",
    )
    rebuild_source_analytic_account_id = fields.Integer(index=True, copy=False)
    rebuild_source_move_line_id = fields.Integer(index=True, copy=False)
    rebuild_source_general_account_id = fields.Integer(index=True, copy=False)
    rebuild_source_journal_id = fields.Integer(index=True, copy=False)


class AccountFullReconcile(models.Model):
    _name = "account.full.reconcile"
    _inherit = ["account.full.reconcile", "rebuild.source.trace.mixin"]


class AccountPartialReconcile(models.Model):
    _name = "account.partial.reconcile"
    _inherit = ["account.partial.reconcile", "rebuild.source.trace.mixin"]

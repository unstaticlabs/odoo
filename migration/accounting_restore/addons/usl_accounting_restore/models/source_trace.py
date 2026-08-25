from odoo import fields, models


class SourceTraceMixin(models.AbstractModel):
    _name = "usl.accounting.restore.source.mixin"
    _description = "Temporary Accounting Source Binding"

    # Exact replay resolves almost every target record through this identity.
    # The individual field indexes remain useful for diagnostics, while this
    # migration-only index avoids thousands of bitmap/index intersections and
    # also makes an accidental duplicate source representation impossible.
    # Finalization drops the indexed columns, so PostgreSQL removes this index
    # with the rest of the temporary migration schema.
    _rebuild_source_identity_uniq = models.UniqueIndex(
        "(rebuild_source_snapshot, rebuild_source_model, rebuild_source_id) "
        "WHERE rebuild_source_model IS NOT NULL",
    )

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


def _source_bound_model(class_name, model_name):
    return type(
        class_name,
        (models.Model,),
        {
            "__module__": __name__,
            "_name": model_name,
            "_inherit": [model_name, "usl.accounting.restore.source.mixin"],
        },
    )


for _class_name, _model_name in (
    ("ResCompany", "res.company"),
    ("ResPartner", "res.partner"),
    ("ResCurrencyRate", "res.currency.rate"),
    ("AccountFiscalPosition", "account.fiscal.position"),
    ("AccountFiscalPositionAccount", "account.fiscal.position.account"),
    ("AccountPaymentTerm", "account.payment.term"),
    ("AccountPaymentTermLine", "account.payment.term.line"),
    ("HrEmployee", "hr.employee"),
    ("HrExpense", "hr.expense"),
    ("ProductProduct", "product.product"),
    ("AccountAccount", "account.account"),
    ("AccountGroup", "account.group"),
    ("AccountJournal", "account.journal"),
    ("AccountReconcileModelLine", "account.reconcile.model.line"),
    ("AccountTaxGroup", "account.tax.group"),
    ("AccountTax", "account.tax"),
    ("AccountTaxRepartitionLine", "account.tax.repartition.line"),
    ("AccountAccountTag", "account.account.tag"),
    ("AccountMoveLine", "account.move.line"),
    ("AccountPayment", "account.payment"),
    ("AccountBankStatementLine", "account.bank.statement.line"),
    ("AccountAnalyticPlan", "account.analytic.plan"),
    ("AccountAnalyticAccount", "account.analytic.account"),
    ("AccountFullReconcile", "account.full.reconcile"),
    ("AccountPartialReconcile", "account.partial.reconcile"),
):
    globals()[_class_name] = _source_bound_model(_class_name, _model_name)


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "usl.accounting.restore.source.mixin"]

    rebuild_source_move_type = fields.Char(index=True, copy=False)


class AccountReconcileModel(models.Model):
    _name = "account.reconcile.model"
    _inherit = [
        "account.reconcile.model",
        "usl.accounting.restore.source.mixin",
    ]

    rebuild_source_use_count = fields.Integer(copy=False)
    rebuild_source_created_automatically = fields.Boolean(copy=False)
    rebuild_source_asked_for_autopost = fields.Boolean(copy=False)


class IrAttachment(models.Model):
    _name = "ir.attachment"
    _inherit = ["ir.attachment", "usl.accounting.restore.source.mixin"]

    rebuild_source_attachment_res_model = fields.Char(index=True, copy=False)
    rebuild_source_attachment_res_id = fields.Integer(index=True, copy=False)
    rebuild_source_message_id = fields.Integer(index=True, copy=False)
    rebuild_source_message_date = fields.Datetime(copy=False)
    rebuild_source_message_subject = fields.Char(copy=False)
    rebuild_source_is_main = fields.Boolean(copy=False)


class AccountAnalyticLine(models.Model):
    _name = "account.analytic.line"
    _inherit = ["account.analytic.line", "usl.accounting.restore.source.mixin"]

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


class HrExpenseRestore(models.Model):
    _inherit = "hr.expense"

    def _compute_price_unit(self):
        super()._compute_price_unit()
        source_price_unit = self.env.context.get(
            "rebuild_source_expense_price_unit",
        )
        if source_price_unit is not None:
            for expense in self.filtered(lambda record: record.state == "draft"):
                expense.price_unit = source_price_unit

    def _check_rebuild_required_receipt(self):
        if self.env.context.get("rebuild_source_materialization"):
            return
        return super()._check_rebuild_required_receipt()

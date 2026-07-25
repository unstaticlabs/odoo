from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


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


class ResCurrencyRate(models.Model):
    _name = "res.currency.rate"
    _inherit = ["res.currency.rate", "rebuild.source.trace.mixin"]

    rebuild_rate_provider = fields.Char(index=True, readonly=True, copy=False)
    rebuild_rate_retrieved_at = fields.Datetime(readonly=True, copy=False)


class AccountFiscalPosition(models.Model):
    _name = "account.fiscal.position"
    _inherit = ["account.fiscal.position", "rebuild.source.trace.mixin"]


class AccountFiscalPositionAccount(models.Model):
    _name = "account.fiscal.position.account"
    _inherit = ["account.fiscal.position.account", "rebuild.source.trace.mixin"]


class AccountPaymentTerm(models.Model):
    _name = "account.payment.term"
    _inherit = ["account.payment.term", "rebuild.source.trace.mixin"]


class AccountPaymentTermLine(models.Model):
    _name = "account.payment.term.line"
    _inherit = ["account.payment.term.line", "rebuild.source.trace.mixin"]


class HrEmployee(models.Model):
    _name = "hr.employee"
    _inherit = ["hr.employee", "rebuild.source.trace.mixin"]

    # Keep migration provenance on the private employee model. Odoo exposes
    # non-HR users to hr.employee.public, so trace fields must follow the same
    # boundary as other private employee-only fields.
    rebuild_source_database = fields.Char(
        index=True,
        copy=False,
        groups="hr.group_hr_user",
    )
    rebuild_source_model = fields.Char(
        index=True,
        copy=False,
        groups="hr.group_hr_user",
    )
    rebuild_source_id = fields.Integer(
        index=True,
        copy=False,
        groups="hr.group_hr_user",
    )
    rebuild_source_xmlid = fields.Char(
        index=True,
        copy=False,
        groups="hr.group_hr_user",
    )
    rebuild_source_snapshot = fields.Char(
        index=True,
        copy=False,
        groups="hr.group_hr_user",
    )
    rebuild_import_run_id = fields.Many2one(
        "rebuild.account.import.run",
        index=True,
        copy=False,
        ondelete="set null",
        groups="hr.group_hr_user",
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
        groups="hr.group_hr_user",
    )
    rebuild_import_note = fields.Text(
        copy=False,
        groups="hr.group_hr_user",
    )


class HrExpense(models.Model):
    _name = "hr.expense"
    _inherit = ["hr.expense", "rebuild.source.trace.mixin"]

    rebuild_receipt_state = fields.Selection(
        selection=[
            ("received", "Receipt attached"),
            ("missing", "Receipt missing"),
            ("not_required", "Receipt not required"),
        ],
        compute="_compute_rebuild_expense_guidance",
        search="_search_rebuild_receipt_state",
        string="Receipt",
    )
    rebuild_next_step = fields.Selection(
        selection=[
            ("category", "Choose category"),
            ("receipt", "Attach receipt"),
            ("submit", "Submit"),
            ("approve", "Approve"),
            ("post", "Post"),
            ("payment", "Record reimbursement"),
            ("processing", "Payment processing"),
            ("done", "Complete"),
            ("refused", "Refused"),
        ],
        compute="_compute_rebuild_expense_guidance",
        string="Next step",
    )

    @api.depends(
        "state",
        "product_id",
        "product_id.rebuild_receipt_required",
        "message_main_attachment_id",
        "payment_mode",
    )
    def _compute_rebuild_expense_guidance(self):
        for expense in self:
            has_receipt = bool(expense.message_main_attachment_id)
            receipt_required = (
                not expense.product_id
                or expense.product_id.rebuild_receipt_required
            )
            if has_receipt:
                expense.rebuild_receipt_state = "received"
            elif receipt_required:
                expense.rebuild_receipt_state = "missing"
            else:
                expense.rebuild_receipt_state = "not_required"
            if expense.state == "draft":
                if not expense.product_id:
                    expense.rebuild_next_step = "category"
                elif receipt_required and not has_receipt:
                    expense.rebuild_next_step = "receipt"
                else:
                    expense.rebuild_next_step = "submit"
            elif expense.state == "submitted":
                expense.rebuild_next_step = (
                    "receipt"
                    if receipt_required and not has_receipt
                    else "approve"
                )
            elif expense.state == "approved":
                expense.rebuild_next_step = (
                    "receipt"
                    if receipt_required and not has_receipt
                    else "post"
                )
            elif (
                expense.state == "posted"
                and expense.payment_mode == "own_account"
            ):
                expense.rebuild_next_step = "payment"
            elif expense.state == "in_payment":
                expense.rebuild_next_step = "processing"
            elif expense.state == "refused":
                expense.rebuild_next_step = "refused"
            else:
                expense.rebuild_next_step = "done"

    @api.model
    def _search_rebuild_receipt_state(self, operator, value):
        if operator not in ("=", "!=") or value not in (
            "received",
            "missing",
            "not_required",
        ):
            raise NotImplementedError
        if value == "not_required":
            domain = [
                ("message_main_attachment_id", "=", False),
                ("product_id.rebuild_receipt_required", "=", False),
            ]
            if operator == "!=":
                return [
                    "|",
                    ("message_main_attachment_id", "!=", False),
                    ("product_id.rebuild_receipt_required", "=", True),
                ]
            return domain
        if value == "missing":
            domain = [
                ("message_main_attachment_id", "=", False),
                ("product_id.rebuild_receipt_required", "=", True),
            ]
            if operator == "!=":
                return [
                    "|",
                    ("message_main_attachment_id", "!=", False),
                    ("product_id.rebuild_receipt_required", "=", False),
                ]
            return domain
        has_receipt = value == "received"
        if operator == "!=":
            has_receipt = not has_receipt
        return [
            (
                "message_main_attachment_id",
                "!=" if has_receipt else "=",
                False,
            ),
        ]

    def _can_be_autovalidated(self):
        self.ensure_one()
        auto_validate = super()._can_be_autovalidated()
        if (
            auto_validate
            and self.manager_id == self.env.user
            and self.env.user.has_group(
                "hr_expense.group_hr_expense_manager",
            )
        ):
            return False
        return auto_validate

    def _check_rebuild_required_receipt(self):
        # The deterministic source materializer restores the native workflow
        # state before copying source attachments. Its own parity controls
        # validate the restored evidence immediately afterwards.
        if self.env.context.get("rebuild_source_materialization"):
            return
        missing = self.filtered(
            lambda expense: (
                expense.product_id.rebuild_receipt_required
                and not expense.message_main_attachment_id
            ),
        )
        if missing:
            raise UserError(
                _(
                    "Attach a receipt before continuing with: %s",
                    ", ".join(missing.mapped("name")),
                ),
            )

    def action_submit(self):
        self._check_rebuild_required_receipt()
        return super().action_submit()

    def action_approve(self):
        self._check_rebuild_required_receipt()
        return super().action_approve()

    def action_post(self):
        self._check_rebuild_required_receipt()
        return super().action_post()

    def _check_rebuild_reviewer_expense_mutation(self):
        if (
            not self.env.su
            and self.env.user.has_group(
                "rebuild_account_migration.group_rebuild_accountant_reviewer",
            )
        ):
            raise AccessError(
                _(
                    "The USL Accountant Review role is read-only for native "
                    "expenses.",
                ),
            )

    @api.model
    def get_view(self, view_id=None, view_type="form", **options):
        result = super().get_view(view_id, view_type, **options)
        if self.env.user.has_group(
            "rebuild_account_migration.group_rebuild_accountant_reviewer",
        ):
            arch = etree.fromstring(result["arch"])
            arch.set("create", "false")
            arch.set("edit", "false")
            arch.set("delete", "false")
            if view_type == "form":
                for control in arch.xpath("//header/button | //header/widget"):
                    control.getparent().remove(control)
            result["arch"] = etree.tostring(arch, encoding="unicode")
        return result

    @api.model_create_multi
    def create(self, vals_list):
        self._check_rebuild_reviewer_expense_mutation()
        return super().create(vals_list)

    def write(self, vals):
        self._check_rebuild_reviewer_expense_mutation()
        return super().write(vals)

    def unlink(self):
        self._check_rebuild_reviewer_expense_mutation()
        return super().unlink()


class ProductProduct(models.Model):
    _name = "product.product"
    _inherit = ["product.product", "rebuild.source.trace.mixin"]

    rebuild_receipt_required = fields.Boolean(
        string="Receipt required",
        default=True,
        help=(
            "Require supporting evidence before an expense using this "
            "category can be submitted, approved or posted."
        ),
    )


class AccountAccount(models.Model):
    _name = "account.account"
    _inherit = ["account.account", "rebuild.source.trace.mixin"]


class AccountGroup(models.Model):
    _name = "account.group"
    _inherit = ["account.group", "rebuild.source.trace.mixin"]


class AccountJournal(models.Model):
    _name = "account.journal"
    _inherit = ["account.journal", "rebuild.source.trace.mixin"]

    def _get_bank_statements_available_import_formats(self):
        formats = super()._get_bank_statements_available_import_formats()
        friendly_names = {
            "TXT/CSV/XSLX": self.env._("CSV or XLSX"),
            "qif": self.env._("QIF"),
            "camt.053.001.02": self.env._("CAMT.053"),
            "camt.054.001.02": self.env._("CAMT.054"),
        }
        return list(
            dict.fromkeys(friendly_names.get(item, item) for item in formats),
        )


class AccountStatementImport(models.TransientModel):
    _inherit = "account.statement.import"

    def _parse_file(self, data_file):
        parsed = super()._parse_file(data_file)
        if not self._check_qif(data_file):
            return parsed
        journal = self.env["account.journal"].browse(
            self.env.context.get("journal_id"),
        )
        fallback_currency = journal.currency_id or journal.company_id.currency_id

        def with_currency(statement):
            currency_code, account_number, statement_values = statement
            for statement_value in statement_values:
                # QIF contains movements, not an authoritative account
                # balance. Let Odoo derive the statement end from the
                # journal's current balance and those movements.
                statement_value.pop("balance_end_real", None)
            return (
                currency_code or fallback_currency.name,
                account_number,
                statement_values,
            )

        if isinstance(parsed, list):
            return [with_currency(statement) for statement in parsed]
        return with_currency(parsed)


class AccountReconcileModel(models.Model):
    _name = "account.reconcile.model"
    _inherit = ["account.reconcile.model", "rebuild.source.trace.mixin"]


class AccountReconcileModelLine(models.Model):
    _name = "account.reconcile.model.line"
    _inherit = [
        "account.reconcile.model.line",
        "rebuild.source.trace.mixin",
    ]


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

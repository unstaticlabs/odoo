from lxml import etree

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.addons.account_statement_import_qif.wizards.account_statement_import_qif import (
    AccountStatementImport as QifAccountStatementImport,
)


class ResCurrencyRate(models.Model):
    _inherit = "res.currency.rate"

    rebuild_rate_provider = fields.Char(index=True, readonly=True, copy=False)
    rebuild_rate_retrieved_at = fields.Datetime(readonly=True, copy=False)


class HrExpense(models.Model):
    _inherit = "hr.expense"

    rebuild_receipt_state = fields.Selection(
        selection=[
            ("received", "Attached"),
            ("missing", "Missing"),
            ("not_required", "Not required"),
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
                    "receipt" if receipt_required and not has_receipt else "approve"
                )
            elif expense.state == "approved":
                expense.rebuild_next_step = (
                    "receipt" if receipt_required and not has_receipt else "post"
                )
            elif expense.state == "posted" and expense.payment_mode == "own_account":
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
            and self.env.user.has_group("hr_expense.group_hr_expense_manager")
        ):
            return False
        return auto_validate

    def _check_rebuild_required_receipt(self):
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
                for control in arch.xpath("//header/button | //header/widget | //widget[@name='attach_document']"):
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
    _inherit = "product.product"

    rebuild_receipt_required = fields.Boolean(
        string="Receipt required",
        default=True,
        help=(
            "Require supporting evidence before an expense using this "
            "category can be submitted, approved or posted."
        ),
    )


class AccountJournal(models.Model):
    _inherit = "account.journal"

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

    def _import_file(self):
        """Use the saas-19.3 raw-binary API with the pinned OCA importer.

        The pinned OCA 19.0 implementation still base64-decodes a binary field
        and writes the removed ``ir.attachment.datas`` compatibility key.
        Odoo saas-19.3 exposes :class:`BinaryValue` raw content instead.  Keep
        OCA's workflow and duplicate detection, but pass the exact uploaded
        bytes and create a real attachment through ``raw``.
        """
        self.ensure_one()
        result = {"statement_ids": [], "notifications": []}
        self.import_single_file(self.statement_file.content, result)
        if not result["statement_ids"]:
            raise UserError(
                self.env._(
                    "You have already imported this file, or this file "
                    "only contains already imported transactions.",
                ),
            )
        attachment = self.env["ir.attachment"].create(
            self._prepare_create_attachment(result),
        )
        for statement in self.env["account.bank.statement"].browse(
            result["statement_ids"],
        ):
            statement.write({"attachment_ids": [Command.link(attachment.id)]})
        return result

    def _prepare_create_attachment(self, result):
        values = super()._prepare_create_attachment(result)
        values.pop("datas", None)
        values["raw"] = self.statement_file
        return values

    def _complete_stmts_vals(self, stmt_vals, journal_id, account_number):
        # Bypass only the pinned QIF add-on's legacy second base64 decode.  Its
        # superclass still performs all generic statement completion; the
        # partner-name behavior is then reproduced against raw 19.3 bytes.
        result = super(
            QifAccountStatementImport,
            self,
        )._complete_stmts_vals(stmt_vals, journal_id, account_number)
        if not self.statement_file or not self._check_qif(
            self.statement_file.content,
        ):
            return result
        Partner = self.env["res.partner"]
        for statement in result:
            for line_values in statement["transactions"]:
                if not line_values.get("partner_id") and line_values.get(
                    "payment_ref",
                ):
                    partner = Partner.search([
                        ("name", "ilike", line_values["payment_ref"]),
                    ], limit=1)
                    line_values["partner_id"] = partner.id
        return result

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
                statement_value.pop("balance_end_real", None)
            return (
                currency_code or fallback_currency.name,
                account_number,
                statement_values,
            )

        if isinstance(parsed, list):
            return [with_currency(statement) for statement in parsed]
        return with_currency(parsed)


class AccountPayment(models.Model):
    _inherit = "account.payment"

    # These fields are ongoing representations of historical business events,
    # not source bindings. They preserve legitimate payments whose originating
    # system stored workflow history without a second journal entry.
    usl_historical_no_ledger_effect = fields.Boolean(
        string="Historical payment without journal entry",
        copy=False,
        index=True,
        help=(
            "Preserves a payment workflow record when its accounting effect "
            "is already represented by linked documents and reconciliations."
        ),
    )
    usl_source_is_reconciled = fields.Boolean(copy=False)
    usl_source_is_matched = fields.Boolean(copy=False)
    usl_source_is_sent = fields.Boolean(copy=False)
    usl_source_is_reconciled_raw = fields.Char(copy=False)
    usl_source_is_matched_raw = fields.Char(copy=False)
    usl_source_is_sent_raw = fields.Char(copy=False)
    usl_source_outstanding_account_id = fields.Integer(copy=False)
    usl_source_destination_account_id = fields.Integer(copy=False)
    usl_source_amount_company_currency_signed = fields.Monetary(
        currency_field="company_currency_id",
        copy=False,
    )

    def _get_outstanding_account(self, payment_type):
        if (
            self.env.su
            and self.env.context.get("usl_historical_payment_maintenance")
        ):
            return self.env["account.account"]
        return super()._get_outstanding_account(payment_type)

    @api.depends(
        "move_id.line_ids.amount_residual",
        "move_id.line_ids.amount_residual_currency",
        "move_id.line_ids.account_id",
        "state",
        "usl_historical_no_ledger_effect",
        "usl_source_is_reconciled",
        "usl_source_is_matched",
    )
    def _compute_reconciliation_status(self):
        super()._compute_reconciliation_status()
        for payment in self.filtered("usl_historical_no_ledger_effect"):
            payment.is_reconciled = payment.usl_source_is_reconciled
            payment.is_matched = payment.usl_source_is_matched

    def write(self, vals):
        historical = self.filtered("usl_historical_no_ledger_effect")
        protected_fields = {
            "amount",
            "company_id",
            "currency_id",
            "date",
            "destination_account_id",
            "journal_id",
            "move_id",
            "outstanding_account_id",
            "partner_id",
            "partner_type",
            "payment_method_line_id",
            "payment_type",
            "state",
        }
        if (
            historical
            and protected_fields.intersection(vals)
            and not (
                self.env.su
                and self.env.context.get(
                    "usl_historical_payment_maintenance",
                )
            )
        ):
            raise ValidationError(
                _(
                    "This historical payment records a completed business "
                    "event whose accounting effect is already represented. "
                    "Open the linked document instead of editing the payment.",
                ),
            )
        return super().write(vals)

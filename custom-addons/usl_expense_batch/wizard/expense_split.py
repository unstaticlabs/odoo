import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class HrExpenseSplit(models.TransientModel):
    _inherit = 'hr.expense.split'

    description = fields.Text(string='Internal Notes', compute='_compute_split_description', store=True, readonly=False)
    account_id = fields.Many2one('account.account', string='Account', check_company=True,
                                compute='_compute_split_account', store=True, readonly=False,
                                domain="[('account_type', 'not in', ('asset_receivable', 'liability_payable', 'asset_cash', 'liability_credit_card'))]")

    @api.depends('product_id', 'company_id')
    def _compute_split_account(self):
        for line in self:
            line.account_id = line.product_id.with_company(line.company_id).product_tmpl_id._get_product_accounts()['expense']

    @api.depends('expense_id')
    def _compute_split_description(self):
        for line in self:
            line.description = line.expense_id.description

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        source = self.env['hr.expense'].browse(values.get('expense_id'))
        if source:
            if 'description' in names:
                values['description'] = source.description
            if 'account_id' in names:
                values['account_id'] = source.account_id.id
        return values

    def _get_values(self):
        self.ensure_one()
        values = super()._get_values()
        source = self.expense_id
        default_account = self.product_id.with_company(self.company_id).product_tmpl_id._get_product_accounts()['expense']
        # Preserve known source provenance; copying a claim must not require
        # unrelated permission to inspect analytic-distribution configuration.
        values.update({
            'description': self.description,
            'account_id': self.account_id.id,
            'account_context_source': source.account_context_source if self.account_id == source.account_id else (
                'product' if self.account_id == default_account else 'explicit'),
            'analytic_context_source': source.analytic_context_source if source._analytic_distributions_equal(
                self.analytic_distribution, source.analytic_distribution) else 'explicit',
        })
        return values


class HrExpenseSplitWizard(models.TransientModel):
    _inherit = 'hr.expense.split.wizard'

    split_completed = fields.Boolean(copy=False, readonly=True)

    def action_split_expense(self):
        self.ensure_one()
        with self.env.cr.savepoint():
            if self.split_completed:
                raise UserError(_("This split was already applied. Open Split Expenses to inspect the saved parts."))
            source = self.expense_id._lock_draft_split_source()
            original_name = source.name or str(source.id)
            original_total, original_currency = source.total_amount_currency, source.currency_id
            source_batch, source_attachment = source.expense_batch_id, source.message_main_attachment_id
            lines = self.expense_split_line_ids
            if not 2 <= len(lines) <= 50:
                raise ValidationError(_("Supply between 2 and 50 explicit split lines."))
            if not source.total_amount_currency or not math.isfinite(source.total_amount_currency):
                raise ValidationError(_("The source expense needs a nonzero finite gross total before splitting."))
            gross_total = sum(lines.mapped('total_amount_currency'))
            if not math.isfinite(gross_total) or source.currency_id.compare_amounts(gross_total, source.total_amount_currency):
                raise ValidationError(_("The split gross total must match the source expense total."))
            for line in lines:
                if line.expense_id != source or line.company_id != source.company_id or line.currency_id != source.currency_id:
                    raise ValidationError(_("Every native split line must belong to its source expense, company and currency."))
                if not math.isfinite(line.total_amount_currency):
                    raise ValidationError(_("Every split line needs a finite gross amount."))
                requested = line.total_amount_currency
                # Native stored preview may have been computed in another
                # selected company; recompute in this receipt's company.
                line.with_company(source.company_id)._compute_from_product_id()
                if line.product_has_cost and line.currency_id != source.company_currency_id:
                    raise ValidationError(_("This fixed-cost category uses the company currency. Choose a variable-price category to preserve the receipt currency."))
                if line.product_has_cost:
                    if line.currency_id.compare_amounts(requested, line.total_amount_currency):
                        raise ValidationError(_("This fixed-cost category uses its native split amount. Review its unit price or choose a variable-price category for an explicit gross amount."))
                patch = {'name': line.name, 'description': line.description or '', 'product_id': line.product_id.id,
                         'tax_ids': line.tax_ids.ids, 'analytic_distribution': line.analytic_distribution or {}}
                default_account = line.product_id.with_company(source.company_id).product_tmpl_id._get_product_accounts()['expense']
                if line.account_id not in (source.account_id, default_account):
                    patch['account_id'] = line.account_id.id
                source._split_financial_values(patch)
                employee = line.employee_id
                self.env['hr.employee.public'].browse(employee.ids).check_access('read')
                if employee.company_id != source.company_id:
                    raise ValidationError(_("Choose split employees belonging to the expense company."))
            planned = [(line.total_amount_currency, line.currency_id, line.company_id, line.employee_id) for line in lines]
            source_date, source_payment = source.date, source.payment_mode
            if 'usl.mail.pdf.retrieval' in self.env:
                self.env['usl.mail.pdf.retrieval']._supersede_for_expense(source)
            action, parts = self._apply_native_split_with_context()
            for part in parts:
                if source_batch and not part.expense_batch_id:
                    part.write({'expense_batch_id': source_batch.id})
                if source_attachment and not part.message_main_attachment_id:
                    match = part.attachment_ids.filtered(lambda attachment: attachment.checksum == source_attachment.checksum)[:1]
                    if match:
                        part.message_main_attachment_id = match
            if len(parts) != len(planned) or any(
                part.currency_id != currency or currency.compare_amounts(part.total_amount_currency, gross)
                or part.company_id != company or part.employee_id != employee
                or part.date != source_date or part.payment_mode != source_payment
                for part, (gross, currency, company, employee) in zip(parts, planned)
            ):
                raise ValidationError(_("The chosen category changed a split amount or its defaults. Review its native unit price and quantity, or choose a variable-price category. Nothing was saved."))
            source.message_post(body=_("Expense %(source)s split into %(count)s draft parts. Original gross total: %(amount)s %(currency)s.",
                                       source=original_name, count=len(parts), amount=original_total, currency=original_currency.name), subtype_xmlid='mail.mt_note')
            self.split_completed = True
            return action

    @api.private
    def _apply_native_split_with_context(self):
        """Native split algorithm with its authorized source context retained.

        Mirrors hr_expense_split_wizard.py at staging fb119fee0b5a. Upstream
        replaces context with a dictionary before copy; keyword merging retains
        selected companies and the fixed workflow's in-process authorization.
        Keep this delegate aligned when that upstream method changes.
        """
        expense_split = self.expense_split_line_ids[0]
        copied_expenses = self.env['hr.expense']
        if expense_split:
            self.expense_id.write(expense_split._get_values())
            self.expense_split_line_ids -= expense_split
            if self.expense_split_line_ids:
                for split in self.expense_split_line_ids:
                    copied_expenses |= self.expense_id.with_context(from_split_wizard=True).copy(split._get_values())
                attachments = self.env['ir.attachment'].search([
                    ('res_model', '=', 'hr.expense'), ('res_id', '=', self.expense_id.id)])
                for copied in copied_expenses:
                    for attachment in attachments:
                        attachment.copy({'res_model': 'hr.expense', 'res_id': copied.id})
        related = self.env['hr.expense']
        if self.expense_id.split_expense_origin_id:
            related = self.env['hr.expense'].search([
                ('split_expense_origin_id', '=', self.expense_id.split_expense_origin_id.id)])
        (self.expense_id | copied_expenses).split_expense_origin_id = self.expense_id.split_expense_origin_id or self.expense_id
        return ((copied_expenses | self.expense_id | related)._get_records_action(name=_("Split Expenses")),
                self.expense_id | copied_expenses)


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    def _get_split_values(self):
        values = super()._get_split_values()
        for line in values:
            line.update({'account_id': self.account_id.id, 'description': self.description})
        return values

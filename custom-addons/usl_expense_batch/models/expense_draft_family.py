import math
from collections.abc import Mapping

from odoo import Command, _, api, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .expense_draft_configuration import RESULT_FIELDS

MAX_PARTS = 50
SPLIT_FIELDS = {"name", "description", "product_id", "account_id", "tax_ids", "analytic_distribution"}
FAMILY_FIELDS = SPLIT_FIELDS | {"expense_batch_id", "vendor_id", "existing_bill_id", "has_existing_bill"}
FAMILY_RESULT_FIELDS = RESULT_FIELDS + ["split_expense_origin_id", "account_context_source", "analytic_context_source", "tax_amount_currency"]


class HrExpense(models.Model):
    _inherit = "hr.expense"

    @api.private
    def _lock_draft_split_source(self):
        self.ensure_one()
        self.check_access("write")
        self.flush_recordset()
        origin = self.split_expense_origin_id or self
        # All native resplits and family corrections take the origin lock first.
        self.env.cr.execute("SELECT id FROM hr_expense WHERE id = %s FOR UPDATE", (origin.id,))
        self.env.cr.execute("SELECT id FROM hr_expense WHERE id = %s FOR UPDATE", (self.id,))
        self.invalidate_recordset()
        self.check_access("write")
        if (self.split_expense_origin_id or self).id != origin.id:
            raise UserError(_("The split origin changed while this action was waiting. Refresh the expense before retrying."))
        if self.state != "draft" or self.account_move_id:
            raise UserError(_("Only draft expenses without a journal entry can be split."))
        return self

    @api.private
    def _split_financial_values(self, values):
        values = self._draft_configuration_values(values) if values else {}
        for field in ("product_id", "account_id"):
            if field in values:
                record = self._draft_configuration_record(field, values[field], self.company_id)
                if field == "product_id" and record and not record.can_be_expensed:
                    raise ValidationError(_("Choose an expense category."))
        if "tax_ids" in values:
            taxes = self.env['account.tax'].browse(values['tax_ids']).exists()
            if len(taxes) != len(values['tax_ids']):
                raise ValidationError(_("Every selected tax must exist."))
            if taxes:
                taxes.check_access('read')
            if len(taxes.filtered_domain(taxes._check_company_domain(self.company_id))) != len(taxes):
                raise ValidationError(_("Choose taxes belonging to the expense company."))
            if any(tax.type_tax_use not in ('purchase', 'none') for tax in taxes):
                raise ValidationError(_("Choose purchase taxes for expense split lines."))
        if "analytic_distribution" in values:
            ids = {int(part) for key in values['analytic_distribution'] for part in key.split(',')}
            analytics = self.env['account.analytic.account'].browse(ids).exists()
            if analytics:
                analytics.check_access('read')
            if len(analytics) != len(ids) or len(analytics.filtered_domain(analytics._check_company_domain(self.company_id))) != len(analytics):
                raise ValidationError(_("Choose analytics belonging to the expense company or shared analytics."))
        return values

    def split_draft_expense(self, lines):
        """Apply explicit gross parts through the native wizard in one transaction."""
        self.ensure_one()
        with self.env.cr.savepoint():
            self._lock_draft_split_source()
            if self.split_expense_origin_id:
                raise UserError(_("This expense is already part of a split. Open Split Expenses before planning another split."))
            if not isinstance(lines, list) or not 2 <= len(lines) <= MAX_PARTS:
                raise ValidationError(_("Supply between 2 and 50 explicit split lines."))
            before = self.total_amount_currency
            if not before or not math.isfinite(before):
                raise ValidationError(_("The source expense needs a nonzero finite gross total before splitting."))
            prepared = []
            for line in lines:
                if not isinstance(line, Mapping) or set(line) - (SPLIT_FIELDS | {"total_amount_currency"}):
                    raise ValidationError(_("Supply supported split line fields."))
                gross = line.get("total_amount_currency")
                if isinstance(gross, bool) or not isinstance(gross, (int, float)) or not math.isfinite(gross):
                    raise ValidationError(_("Every split line needs a finite gross amount."))
                patch = self._split_financial_values({key: value for key, value in line.items() if key != 'total_amount_currency'})
                product_id = patch.get('product_id', self.product_id.id)
                if not product_id:
                    raise ValidationError(_("Choose an expense category for every split line."))
                values = {
                    'name': self.name, 'description': self.description,
                    'product_id': product_id, 'account_id': self.account_id.id,
                    'tax_ids': [Command.set(self.tax_ids.ids)],
                    'analytic_distribution': self.analytic_distribution or {},
                    'total_amount_currency': gross, 'expense_id': self.id,
                    'company_id': self.company_id.id, 'employee_id': self.employee_id.id,
                    'currency_id': self.currency_id.id,
                    'approval_state': self.approval_state, 'approval_date': self.approval_date,
                    'manager_id': self.manager_id.id,
                }
                values.update({key: value for key, value in patch.items() if key != 'tax_ids'})
                if 'product_id' in patch and 'account_id' not in patch:
                    values.pop('account_id')  # A chosen category keeps its native account default.
                if 'product_id' in patch and 'tax_ids' not in patch:
                    category = self.env['product.product'].browse(product_id)
                    values['tax_ids'] = [Command.set(category.supplier_taxes_id.filtered_domain(
                        self.env['account.tax']._check_company_domain(self.company_id)).ids)]
                if 'tax_ids' in patch:
                    values['tax_ids'] = [Command.set(patch['tax_ids'])]
                prepared.append(values)
            planned_total = sum(line['total_amount_currency'] for line in prepared)
            if not math.isfinite(planned_total) or self.currency_id.compare_amounts(planned_total, before):
                raise ValidationError(_("The split gross total must match the source expense total."))
            records = self._usl_agent_collaboration('write') if hasattr(self, '_usl_agent_collaboration') else self
            wizard = records.env['hr.expense.split.wizard'].create({
                'expense_id': self.id, 'expense_split_line_ids': [Command.create(line) for line in prepared],
            })
            wizard.action_split_expense()
            family = records.search([('split_expense_origin_id', '=', self.id)], order='id')
            family.check_access('write')
            if len(family) != len(lines):
                raise UserError(_("The native split did not return the complete planned family. Nothing was saved."))
            after = sum(family.mapped('total_amount_currency'))
            if self.currency_id.compare_amounts(before, after):
                raise ValidationError(_("The resulting split total must match the source expense total. Nothing was saved."))
            return {'origin_id': self.id, 'before_total': before, 'after_total': after,
                    'currency_id': self.currency_id.id, 'expenses': family.read(FAMILY_RESULT_FIELDS)}

    def configure_draft_expense_family(self, expense_ids, company_id, employee_id, lines):
        """Correct a caller-named complete draft family with individual mapping choices."""
        self.ensure_one()
        self.check_access('write')
        if not isinstance(expense_ids, list) or not 2 <= len(expense_ids) <= MAX_PARTS or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in expense_ids
        ) or len(set(expense_ids)) != len(expense_ids):
            raise ValidationError(_("Name every split expense once, using between 2 and 50 record IDs."))
        pair = self._draft_configuration_values({'company_id': company_id, 'employee_id': employee_id})
        if not isinstance(lines, list) or len(lines) != len(expense_ids):
            raise ValidationError(_("Supply one explicit mapping choice for every named split expense."))
        choices = {}
        for line in lines:
            if not isinstance(line, Mapping) or set(line) != {'expense_id', 'values'} or isinstance(line['expense_id'], bool) or not isinstance(line['expense_id'], int) or line['expense_id'] not in expense_ids:
                raise ValidationError(_("Each mapping must identify one named expense and its supported draft fields."))
            if line['expense_id'] in choices or not isinstance(line['values'], Mapping) or set(line['values']) - FAMILY_FIELDS:
                raise ValidationError(_("Supply each expense mapping once without amount or ownership overrides."))
            choices[line['expense_id']] = dict(line['values'])
        with self.env.cr.savepoint():
            self._lock_draft_split_source()
            origin = self.split_expense_origin_id
            if not origin:
                raise UserError(_("This expense is not part of a linked split family."))
            self.env.cr.execute("SELECT id FROM hr_expense WHERE split_expense_origin_id = %s OR id = %s ORDER BY id FOR UPDATE", (origin.id, origin.id))
            current_ids = [row[0] for row in self.env.cr.fetchall()]
            if set(current_ids) != set(expense_ids):
                raise UserError(_("The named expenses no longer match the complete split family. Refresh Split Expenses and review the whole family before retrying."))
            family = self.browse(current_ids)
            family.invalidate_recordset()
            # Check every member before disclosing any result or changing any part.
            try:
                family.check_access('write')
                family.check_access('read')
            except AccessError:
                raise AccessError(_("You need read and write access to every split expense before correcting family ownership.")) from None
            if any(part.state != 'draft' or part.account_move_id for part in family):
                raise UserError(_("Every split part must be a draft without a journal entry before family ownership can be corrected."))
            before = {part.id: (part.currency_id.id, part.total_amount_currency) for part in family}
            before_totals = {}
            for currency, amount in before.values():
                before_totals[currency] = before_totals.get(currency, 0) + amount
            for part in family:
                part._configure_draft_expense(choices[part.id] | pair, family_members=family)
            if any((part.currency_id.id, part.total_amount_currency) != before[part.id] for part in family):
                raise ValidationError(_("A family correction must preserve every document currency and gross amount. Nothing was saved."))
            totals = [{'currency_id': currency, 'before_total': amount,
                       'after_total': sum(part.total_amount_currency for part in family if part.currency_id.id == currency)}
                      for currency, amount in sorted(before_totals.items())]
            return {'origin_id': origin.id, 'totals': totals, 'expenses': family.read(FAMILY_RESULT_FIELDS)}

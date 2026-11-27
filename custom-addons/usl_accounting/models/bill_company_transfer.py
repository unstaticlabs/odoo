"""Move a draft vendor bill to another company, as one previewable native action.

`change_bill_company` is the single entry point. The review dialog and the MCP
tool both call it: a preview reads and proposes, a confirmation applies the same
proposal with one native write. Nothing is written during a preview.
"""
import hashlib
import json

from odoo import Command, _, models
from odoo.exceptions import AccessError, UserError


def _compatible(records, company):
    """Use the related model's own branch, company and shared-record rules."""
    return records.with_company(company).filtered_domain(records._check_company_domain(company))


def _record_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise UserError(_("Record identifiers must be positive integers."))
    return value


def _record_ids(values, label):
    if isinstance(values, (str, bytes, dict)) or not isinstance(values, (list, tuple)):
        raise UserError(_("%(label)s must be a list of record identifiers.", label=label))
    return list(dict.fromkeys(_record_id(value) for value in values))


class AccountMove(models.Model):
    _inherit = "account.move"

    # ------------------------------------------------------------------ gates

    def _check_draft_company_transfer(self):
        self.ensure_one()
        self.check_access("write")
        if self.move_type not in ("in_invoice", "in_refund") or self.state != "draft":
            raise UserError(_("Open a draft vendor bill or vendor credit note to change its company."))

    def action_change_bill_company(self):
        self._check_draft_company_transfer()
        wizard = self.env["usl.bill.company.transfer"].create({"bill_id": self.id})
        return wizard._dialog()

    # ------------------------------------------------------------- references

    def _company_transfer_ref(self, record):
        if not record:
            return None
        try:
            record.check_access("read")
            name = record.display_name
        except AccessError:
            name = _("A record you cannot read")
        return {"model": record._name, "id": record.id, "display_name": name,
                "url": "%s/odoo/%s/%s" % (self.get_base_url(), record._name, record.id)}

    def _company_transfer_refs(self, records):
        return [self._company_transfer_ref(record) for record in records]

    # --------------------------------------------------------------- blockers

    def _company_transfer_blockers(self, target=None):
        """Every native reason this draft cannot change company, found in one pass.

        Each blocker names its record with a link and the next step a person can
        take. A refusal is enough: nothing is remapped for these.
        """
        self.ensure_one()
        found = []

        def add(code, message, records, next_step):
            found.append({"code": code, "message": message, "next_step": next_step,
                          "records": self._company_transfer_refs(records)})

        lines = self.line_ids
        partials = lines.matched_debit_ids | lines.matched_credit_ids
        counterparts = (partials.debit_move_id | partials.credit_move_id) - lines
        payments = self.matched_payment_ids | self.origin_payment_id | counterparts.move_id.origin_payment_id
        statement_lines = (self.statement_line_id | self.statement_line_ids
                           | counterparts.move_id.statement_line_id | counterparts.move_id.statement_line_ids)
        for payment in payments:
            add("payment_linked",
                _("Payment %(name)s is linked to this bill.", name=payment.display_name), payment,
                _("Open the payment and unreconcile it from this bill, then reopen Change company. "
                  "Register the payment again in the destination company if it belongs there."))
        for statement_line in statement_lines:
            add("statement_line_linked",
                _("Bank transaction %(name)s is matched to this bill.", name=statement_line.display_name),
                statement_line,
                _("Open the bank transaction, remove its match with this bill, then reopen Change company."))
        for other in counterparts.move_id - payments.move_id - statement_lines.move_id:
            add("reconciled_entry",
                _("Journal entry %(name)s is reconciled with this bill.", name=other.display_name), other,
                _("Open the entry and unreconcile it from this bill, then reopen Change company."))
        if (partials or lines.full_reconcile_id) and not found:
            add("reconciled", _("This bill is reconciled with another entry."), self.browse(),
                _("Unreconcile the bill in Accounting, then reopen Change company."))
        if "purchase_line_id" in self.env["account.move.line"]._fields:
            for order in self.invoice_line_ids.purchase_line_id.order_id:
                add("purchase_order_linked",
                    _("Purchase order %(name)s is invoiced by this bill.", name=order.display_name), order,
                    _("Bill the destination company from its own purchase order and cancel this draft. "
                      "A bill linked to an order cannot change company."))
        if "existing_bill_id" in self.env["hr.expense"]._fields:
            for expense in self.env["hr.expense"].sudo().search([("existing_bill_id", "=", self.id)]):
                add("expense_linked",
                    _("Expense %(name)s uses this bill as its existing bill.", name=expense.display_name),
                    expense.with_env(self.env),
                    _("Open the expense, clear its Existing bill field, then reopen Change company."))
        origin = self.auto_post_origin_id
        if origin and origin != self:
            add("recurring_copy",
                _("This bill was generated by the recurring bill %(name)s.", name=origin.display_name), origin,
                _("Stop the recurrence on the source bill or change its company first, then reopen Change company."))
        if self.posted_before:
            add("posted_before",
                _("This draft was posted before and keeps its accounting sequence number."), self.browse(),
                _("Duplicate the bill in the destination company and cancel this one."))
        rounding = self.invoice_cash_rounding_id
        if rounding and (rounding.company_id if target is None else rounding != _compatible(rounding, target)):
            add("cash_rounding",
                _("Cash rounding %(name)s belongs to the current company.", name=rounding.display_name), rounding,
                _("Clear the cash rounding in the bill's Other Info tab, transfer the bill, then choose the "
                  "destination company's rounding method."))
        return found

    # ---------------------------------------------------------------- scoping

    def _company_transfer_scope(self, target_company_id):
        """Return the bill scoped to its two companies, the source and the destination.

        The operator needs access to both companies, not both active in the selector.
        """
        self.ensure_one()
        if isinstance(target_company_id, bool) or not isinstance(target_company_id, int):
            raise UserError(_("Choose the destination company by its identifier."))
        source = self.company_id
        target = self.env["res.company"].sudo().browse(target_company_id).exists()
        user_companies = self.env.user.company_ids
        if not target or target not in user_companies or source not in user_companies:
            raise AccessError(_("You can only move a bill between companies you have access to."))
        bill = self.with_context(allowed_company_ids=[source.id, target.id]).with_company(target)
        bill._check_draft_company_transfer()
        if target == source:
            raise UserError(_("Choose a different destination company."))
        target = bill.env["res.company"].browse(target.id)
        target.check_access("read")
        return bill, source, target

    # ------------------------------------------------------------------ plan

    def _company_transfer_proposed_journal(self, target):
        candidates = self._get_suitable_journal_ids(self.move_type, target).filtered(
            lambda journal: journal.active and (not journal.currency_id or journal.currency_id == self.currency_id))
        exact = candidates.filtered(lambda journal: journal.currency_id == self.currency_id)
        return (exact if exact else candidates)[:1]

    def _company_transfer_proposed_taxes(self, line, target, position):
        """Taxes belong to one company: the product's, then the same tax over there."""
        Tax = self.env["account.tax"]
        if not line.tax_ids:
            return Tax, True
        if line.tax_ids == _compatible(line.tax_ids, target):
            return line.tax_ids, True
        product_taxes = line.product_id.sudo().supplier_taxes_id.filtered_domain(
            Tax._check_company_domain(target))._filter_taxes_by_company(target)
        if product_taxes:
            return (position.map_tax(product_taxes) if position else product_taxes), True
        chosen = Tax
        for tax in line.tax_ids:
            same = Tax.search([
                *Tax._check_company_domain(target), ("type_tax_use", "=", "purchase"), ("active", "=", True),
                ("amount", "=", tax.amount), ("amount_type", "=", tax.amount_type),
                ("price_include", "=", tax.price_include),
            ]).sorted(lambda item: (item.name != tax.name, item.sequence, item.id))[:1]
            if not same:
                return Tax, False
            chosen |= same
        return chosen, True

    def _company_transfer_plan(self, target, mapping=None):
        """Resolve destination values: the native proposal, then explicit choices on top."""
        self.ensure_one()
        mapping = mapping or {}
        unresolved = []
        partner = self.partner_id
        Position = self.env["account.fiscal.position"]
        if "journal_id" in mapping:
            journal = self.env["account.journal"].browse(_record_id(mapping["journal_id"]))
        else:
            journal = self._company_transfer_proposed_journal(target)
        if not journal:
            unresolved.append({"code": "journal", "message": _("The destination company has no active purchase journal for this currency."),
                               "next_step": _("Create one in the destination company, or pass journal_id.")})
        if "fiscal_position_id" in mapping:
            choice = mapping["fiscal_position_id"]
            position = Position.browse(_record_id(choice) if choice else [])
        else:
            position = Position.with_company(target)._get_fiscal_position(partner, self.partner_shipping_id)
            if self.fiscal_position_id and not position:
                unresolved.append({"code": "fiscal_position", "message": _("No destination fiscal position was found for this supplier."),
                                   "next_step": _("Pass fiscal_position_id, or false to use no fiscal position.")})
        if "payment_term_id" in mapping:
            term = self.env["account.payment.term"].browse(_record_id(mapping["payment_term_id"]))
        else:
            term = _compatible(self.invoice_payment_term_id, target)
            if self.invoice_payment_term_id and not term:
                term = partner.with_company(target).property_supplier_payment_term_id
        if self.invoice_payment_term_id and not term:
            unresolved.append({"code": "payment_term", "message": _("No destination payment terms match the current ones."),
                               "next_step": _("Pass payment_term_id so the installments can be recalculated.")})
        overrides = {}
        for item in mapping.get("lines") or []:
            if not isinstance(item, dict):
                raise UserError(_("Each line mapping must be an object."))
            overrides[_record_id(item.get("line_id"))] = item
        product_lines = self.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        if set(overrides) - set(product_lines.ids):
            raise UserError(_("The mapping names lines that are not on this bill. Preview the bill again."))
        lines = []
        for line in product_lines:
            override = overrides.get(line.id, {})
            problems = []
            if "account_id" in override:
                account = self.env["account.account"].browse(_record_id(override["account_id"]))
            else:
                account = _compatible(line.account_id, target)
                if not account and line.product_id:
                    account = line.product_id.product_tmpl_id.with_company(target).get_product_accounts(
                        fiscal_pos=position).get("expense")
                if not account and journal:
                    account = journal.default_account_id
            if not account:
                problems.append({"code": "account", "line_id": line.id,
                                 "message": _("No destination account was found for %(line)s.", line=line.name),
                                 "next_step": _("Pass account_id for this line.")})
            if "tax_ids" in override:
                taxes = self.env["account.tax"].browse(_record_ids(override["tax_ids"], "tax_ids"))
            else:
                taxes, found = self._company_transfer_proposed_taxes(line, target, position)
                if not found:
                    problems.append({"code": "tax", "line_id": line.id,
                                     "message": _("No destination purchase tax matches the taxes on %(line)s.", line=line.name),
                                     "next_step": _("Pass tax_ids for this line, or an empty list for no tax.")})
            distribution = override["analytic_distribution"] if "analytic_distribution" in override else line.analytic_distribution
            lines.append({"line": line, "account": account, "taxes": taxes,
                          "analytic_distribution": distribution or {}, "unresolved": problems})
            unresolved.extend(problems)
        payable = partner.with_company(target).property_account_payable_id
        return {"journal": journal, "fiscal_position": position, "term": term, "lines": lines,
                "unresolved": unresolved, "payable_account": position.map_account(payable) if position else payable}

    def _company_transfer_values(self, plan, target):
        """Validate the resolved plan and return the single write for the bill."""
        journal, position, term = plan["journal"], plan["fiscal_position"], plan["term"]
        if (not journal or journal.company_id != target or journal.type != "purchase" or not journal.active
                or (journal.currency_id and journal.currency_id != self.currency_id)):
            raise UserError(_("Choose an active purchase journal of the destination company with no currency or the bill's currency."))
        for record in (journal, position, term):
            record.check_access("read")
            if record != _compatible(record, target):
                raise UserError(_("Select a compatible destination journal, fiscal position and payment terms."))
        values = {"company_id": target.id, "journal_id": journal.id, "fiscal_position_id": position.id,
                  "invoice_payment_term_id": term.id, "currency_id": self.currency_id.id,
                  "invoice_line_ids": [self._company_transfer_line_command(item, target) for item in plan["lines"]]}
        if not term:
            values["invoice_date_due"] = self.invoice_date_due
        return values

    def _company_transfer_line_command(self, item, target):
        line, account, taxes = item["line"], item["account"], item["taxes"]
        line.check_access("write")
        if not account or not account.active or account != _compatible(account, target):
            raise UserError(_("Choose a compatible destination account for %(line)s.", line=line.name))
        if taxes != _compatible(taxes, target) or any(tax.type_tax_use != "purchase" or not tax.active for tax in taxes):
            raise UserError(_("Select the destination purchase taxes for %(line)s.", line=line.name))
        for records in (account, taxes):
            records.check_access("read")
        distribution = item["analytic_distribution"]
        self._validate_draft_vendor_bill_line_values({"analytic_distribution": distribution})
        try:
            analytic_ids = line._get_analytic_account_ids_from_distributions(distribution)
        except (ValueError, TypeError) as error:
            raise UserError(_("Select valid analytic accounts for %(line)s.", line=line.name)) from error
        analytics = self.env["account.analytic.account"].browse(list(analytic_ids))
        if analytics != _compatible(analytics.exists(), target):
            raise UserError(_("Review the analytic distribution for %(line)s in the destination company.", line=line.name))
        analytics.check_access("read")
        return Command.update(line.id, {"account_id": account.id, "tax_ids": [Command.set(taxes.ids)],
                                        "analytic_distribution": distribution, "quantity": line.quantity,
                                        "price_unit": line.price_unit, "discount": line.discount})

    # ------------------------------------------------------------ description

    def _company_transfer_stamp(self, target):
        payload = json.dumps([self._vendor_bill_merge_snapshot(), target.id, self.env.uid], sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _company_transfer_describe(self):
        """The bill as the operator sees it now: header, due dates and lines."""
        self.ensure_one()
        term_lines = self.line_ids.filtered(lambda line: line.display_type == "payment_term")
        return {
            "company": self._company_transfer_ref(self.company_id),
            "journal": self._company_transfer_ref(self.journal_id),
            "fiscal_position": self._company_transfer_ref(self.fiscal_position_id),
            "payment_term": self._company_transfer_ref(self.invoice_payment_term_id),
            "payable_account": self._company_transfer_ref(term_lines.account_id[:1]),
            "currency": self.currency_id.name,
            "amount_untaxed": self.amount_untaxed, "amount_tax": self.amount_tax, "amount_total": self.amount_total,
            "due_dates": [{"date": line.date_maturity.isoformat() if line.date_maturity else None,
                           "amount": abs(line.amount_currency)}
                          for line in term_lines.sorted(lambda item: (item.date_maturity or item.date, item.id))],
            "lines": [{"line_id": line.id, "label": line.name,
                       "account": self._company_transfer_ref(line.account_id),
                       "taxes": self._company_transfer_refs(line.tax_ids)}
                      for line in self.invoice_line_ids.filtered(lambda item: item.display_type == "product")],
        }

    def _company_transfer_destination(self, plan, target):
        """What the destination will compute for taxes and due dates, without writing.

        A virtual bill goes through the same native computations as the stored one.
        """
        values = {
            "move_type": self.move_type, "company_id": target.id, "partner_id": self.partner_id.id,
            "currency_id": self.currency_id.id, "invoice_date": self.invoice_date, "date": self.date,
            "journal_id": plan["journal"].id, "fiscal_position_id": plan["fiscal_position"].id,
            "invoice_payment_term_id": plan["term"].id,
            "invoice_line_ids": [Command.create({
                "display_type": "product", "name": item["line"].name, "account_id": item["account"].id,
                "quantity": item["line"].quantity, "price_unit": item["line"].price_unit,
                "discount": item["line"].discount, "tax_ids": [Command.set(item["taxes"].ids)],
                "product_id": item["line"].product_id.id, "product_uom_id": item["line"].product_uom_id.id,
            }) for item in plan["lines"]],
        }
        virtual = self.env["account.move"].with_company(target).new(values)
        totals = virtual.tax_totals or {}
        terms = virtual.needed_terms
        terms = terms.items() if isinstance(terms, dict) else terms
        return {
            "currency": self.currency_id.name,
            "amount_untaxed": totals.get("base_amount_currency"), "amount_tax": totals.get("tax_amount_currency"),
            "amount_total": totals.get("total_amount_currency"),
            "tax_groups": [{"name": group.get("group_name"), "amount": group.get("tax_amount_currency"),
                            "base": group.get("base_amount_currency")}
                           for subtotal in totals.get("subtotals", []) for group in subtotal.get("tax_groups", [])],
            "due_dates": sorted(({"date": str(key["date_maturity"]), "amount": abs(amounts["amount_currency"])}
                                 for key, amounts in terms if key.get("date_maturity")), key=lambda due: due["date"]),
        }

    # ------------------------------------------------------------ public entry

    def change_bill_company(self, target_company_id, mapping=None, confirm=False, preview_id=None):
        """Preview, then confirm, moving this draft vendor bill to another company.

        Without ``confirm`` nothing is written. The result lists the blockers,
        the proposed journal, fiscal position, payment terms and payable
        account, every line from -> to with its account and taxes, what is still
        unresolved, and the destination taxes and due dates, with a
        ``preview_id``. With ``confirm`` and that id, the same proposal
        (plus any ``mapping`` choices) is applied in one native write, and the
        result reports the bill before and after.

        ``mapping`` may carry ``journal_id``, ``fiscal_position_id`` (false for
        none), ``payment_term_id`` and ``lines``: a list of ``{"line_id",
        "account_id", "tax_ids" (empty for no tax), "analytic_distribution"}``.
        """
        self.ensure_one()
        if mapping is not None and not isinstance(mapping, dict):
            raise UserError(_("The mapping must be an object."))
        if confirm and self.company_id.id == target_company_id and self.state == "draft":
            self.check_access("write")
            if self.company_id in self.env.user.company_ids:
                # A repeated confirmation after a lost reply: report, never move twice.
                return self._company_transfer_result("already_done", None)
        bill, source, target = self._company_transfer_scope(target_company_id)
        before = bill._company_transfer_describe()
        blockers = bill._company_transfer_blockers(target)
        plan = bill._company_transfer_plan(target, mapping)
        unresolved = list(plan["unresolved"])
        values = None
        if not blockers and not unresolved:
            try:
                values = bill._company_transfer_values(plan, target)
            except UserError as error:
                unresolved.append({"code": "invalid_choice", "message": str(error),
                                   "next_step": _("Correct the mapping and preview again.")})
        token = bill._company_transfer_stamp(target)
        if not confirm:
            destination = bill._company_transfer_destination(plan, target) if values else None
            return bill._company_transfer_preview(source, target, blockers, unresolved, plan, destination, before, token)
        problems = blockers + unresolved
        if problems:
            raise UserError("\n".join("%s %s" % (item["message"], item.get("next_step", "")) for item in problems))
        if not preview_id or preview_id != token:
            raise UserError(_("The bill changed since this preview, or none was requested. Preview it again."))
        if not bill.try_lock_for_update() or len(bill.line_ids.try_lock_for_update()) != len(bill.line_ids):
            raise UserError(_("The bill is being edited. Try again after the other edit finishes."))
        bill._check_fiscal_lock_dates()
        with self.env.cr.savepoint():
            bill.write(values)
            bill.line_ids.filtered(lambda line: line.display_type == "payment_term")._compute_account_id()
            bill._check_fiscal_lock_dates()
            bill.line_ids.flush_recordset()
            bill.message_post(body=_("Draft transferred from %(source)s to %(target)s.",
                                     source=source.display_name, target=target.display_name),
                              subtype_xmlid="mail.mt_note")
        return bill._company_transfer_result("done", before)

    def _company_transfer_result(self, status, before):
        return {"status": status, "bill": self._company_transfer_ref(self), "before": before,
                "after": self._company_transfer_describe()}

    def _company_transfer_preview(self, source, target, blockers, unresolved, plan, destination, before, token):
        status = "blocked" if blockers else "needs_input" if unresolved else "ready"
        lines = [{
            "line_id": item["line"].id, "label": item["line"].name,
            "from": {"account": self._company_transfer_ref(item["line"].account_id),
                     "taxes": self._company_transfer_refs(item["line"].tax_ids)},
            "to": {"account": self._company_transfer_ref(item["account"]),
                   "taxes": self._company_transfer_refs(item["taxes"])},
            "unresolved": [problem["code"] for problem in item["unresolved"]],
        } for item in plan["lines"]]
        return {
            "status": status, "bill": self._company_transfer_ref(self),
            "source_company": self._company_transfer_ref(source), "target_company": self._company_transfer_ref(target),
            "blockers": blockers, "unresolved": unresolved, "current": before,
            "proposal": {"journal": self._company_transfer_ref(plan["journal"]),
                         "fiscal_position": self._company_transfer_ref(plan["fiscal_position"]),
                         "payment_term": self._company_transfer_ref(plan["term"]),
                         "payable_account": self._company_transfer_ref(plan["payable_account"]), "lines": lines},
            "destination": destination, "preview_id": token,
            "next_step": {"blocked": _("Resolve the blockers, then preview again."),
                          "needs_input": _("Pass a mapping for the unresolved items, then preview again."),
                          "ready": _("Confirm with the preview_id to apply this proposal.")}[status],
        }

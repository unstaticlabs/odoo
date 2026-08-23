from odoo import Command, _, models


class ResCompany(models.Model):
    _inherit = "res.company"

    def _usl_available_journal_code(self, base_code):
        self.ensure_one()
        Journal = self.env["account.journal"].sudo().with_context(
            active_test=False,
        )
        for suffix in range(100):
            suffix_text = str(suffix) if suffix else ""
            candidate = f"{base_code[:5 - len(suffix_text)]}{suffix_text}"
            if not Journal.search_count([
                ("company_id", "=", self.id),
                ("code", "=", candidate),
            ]):
                return candidate
        return False

    def _usl_available_account_code(self, base_code):
        self.ensure_one()
        Account = self.env["account.account"].sudo().with_company(
            self,
        ).with_context(active_test=False)
        for suffix in range(1000):
            suffix_text = str(suffix) if suffix else ""
            candidate = (
                f"{base_code[:len(base_code) - len(suffix_text)]}"
                f"{suffix_text}"
            )
            if not Account.search_count([
                ("company_ids", "in", self.id),
                ("code", "=", candidate),
            ]):
                return candidate
        return False

    def _usl_ensure_operational_accounting_journals(self):
        """Add only the missing native journals needed for daily operation.

        Ordinary companies receive these journals from their localization.
        This idempotent safety net covers imported legal entities whose source
        database contained balances or bank activity but no operational
        customer, vendor, general or expense journal.
        """
        Journal = self.env["account.journal"].sudo().with_context(
            active_test=False,
            tracking_disable=True,
            mail_create_nolog=True,
        )
        created = Journal.browse()
        for company in self.sudo():
            Account = self.env["account.account"].sudo().with_company(company)
            operational_accounts = {}
            for account_type, code, name in (
                ("asset_receivable", "411000", _("Clients")),
                ("liability_payable", "401000", _("Fournisseurs")),
            ):
                account = Account.search([
                    ("company_ids", "in", company.id),
                    ("active", "=", True),
                    ("code", "=like", f"{code[:3]}%"),
                    ("account_type", "=", account_type),
                ], order="code", limit=1)
                if not account and company.account_fiscal_country_id.code != "FR":
                    account = Account.search([
                        ("company_ids", "in", company.id),
                        ("active", "=", True),
                        ("account_type", "=", account_type),
                    ], order="code", limit=1)
                if not account:
                    if company.account_fiscal_country_id.code != "FR":
                        continue
                    available_code = company._usl_available_account_code(code)
                    if not available_code:
                        continue
                    account = Account.create({
                        "name": name,
                        "code": available_code,
                        "account_type": account_type,
                        "reconcile": True,
                        "company_ids": [Command.set(company.ids)],
                    })
                operational_accounts[account_type] = account
            defaults = self.env["ir.default"].sudo().with_company(
                company,
            )._get_model_defaults("res.partner")
            for field_name, account_type in (
                ("property_account_receivable_id", "asset_receivable"),
                ("property_account_payable_id", "liability_payable"),
            ):
                if (
                    not defaults.get(field_name)
                    and account_type in operational_accounts
                ):
                    self.env["ir.default"].sudo().set(
                        "res.partner",
                        field_name,
                        operational_accounts[account_type].id,
                        company_id=company.id,
                    )
            company_journals = Journal.with_company(company).search([
                ("company_id", "=", company.id),
                ("active", "=", True),
            ])

            def ensure_journal(
                journal_type,
                code,
                name,
                *,
                exclude=None,
                reuse=True,
            ):
                nonlocal created, company_journals
                existing = (
                    company_journals.filtered(
                        lambda journal: (
                            journal.type == journal_type
                            and (not exclude or not exclude(journal))
                        ),
                    )[:1]
                    if reuse
                    else Journal.browse()
                )
                if existing:
                    return existing
                available_code = company._usl_available_journal_code(code)
                if not available_code:
                    return Journal.browse()
                journal = Journal.with_company(company).create({
                    "name": name,
                    "code": available_code,
                    "type": journal_type,
                    "company_id": company.id,
                })
                created |= journal
                company_journals |= journal
                return journal

            ensure_journal("sale", "INV", _("Factures clients"))
            ensure_journal(
                "purchase",
                "BILL",
                _("Factures fournisseurs"),
                exclude=lambda journal: journal == company.expense_journal_id,
            )
            ensure_journal("general", "MISC", _("Opérations diverses"))
            if not company.expense_journal_id:
                expense_journal = company_journals.filtered(
                    lambda journal: journal.code == "NDF",
                )[:1]
                if not expense_journal:
                    expense_journal = ensure_journal(
                        "purchase",
                        "NDF",
                        _("Notes de frais"),
                        reuse=False,
                    )
                if expense_journal:
                    company.expense_journal_id = expense_journal
            if company.transfer_account_id:
                payment_lines = company_journals.filtered(
                    lambda journal: journal.type in {"bank", "cash"},
                ).mapped(
                    "inbound_payment_method_line_ids",
                ) | company_journals.filtered(
                    lambda journal: journal.type in {"bank", "cash"},
                ).mapped("outbound_payment_method_line_ids")
                payment_lines.filtered(
                    lambda line: not line.payment_account_id,
                ).write({
                    "payment_account_id": company.transfer_account_id.id,
                })
        return created

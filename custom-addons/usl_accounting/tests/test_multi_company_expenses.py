from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_accounting")
class TestMultiCompanyExpenses(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].create({
            "name": "Expense company B",
            "currency_id": cls.company_a.currency_id.id,
        })
        group = cls.env.ref("hr_expense.group_hr_expense_user")
        cls.user = cls.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Multi-company expense user",
            "login": "multi-company-expense-user@example.invalid",
            "email": "multi-company-expense-user@example.invalid",
            "company_id": cls.company_a.id,
            "company_ids": [Command.set((cls.company_a | cls.company_b).ids)],
            "group_ids": [Command.set(group.ids)],
        })

    def test_enable_links_existing_profile_and_creates_missing_company(self):
        existing = self.env["hr.employee"].sudo().create({
            "name": "Existing employee identity",
            "company_id": self.company_a.id,
            "work_contact_id": self.user.partner_id.id,
            "private_email": "private@example.invalid",
        })

        self.user.usl_expense_multi_company = True

        profiles = self.env["hr.employee"].sudo().search([
            ("user_id", "=", self.user.id),
        ])
        self.assertEqual(profiles.company_id, self.company_a | self.company_b)
        self.assertEqual(existing.user_id, self.user)
        created = profiles.filtered(lambda profile: profile.company_id == self.company_b)
        self.assertEqual(created.work_contact_id, self.user.partner_id)
        self.assertFalse(created.private_email)
        self.assertEqual(
            self.user.usl_expense_company_profile_status,
            "ready",
        )

    def test_native_expense_defaults_to_active_company_profile(self):
        self.user.usl_expense_multi_company = True
        company_b_employee = self.env["hr.employee"].sudo().search([
            ("user_id", "=", self.user.id),
            ("company_id", "=", self.company_b.id),
        ])

        expense = self.env["hr.expense"].with_user(self.user).with_company(
            self.company_b,
        ).new({})

        self.assertEqual(expense.company_id, self.company_b)
        self.assertEqual(expense.employee_id, company_b_employee)

    def test_company_removal_preserves_employee_history(self):
        self.user.usl_expense_multi_company = True
        company_b_employee = self.env["hr.employee"].sudo().search([
            ("user_id", "=", self.user.id),
            ("company_id", "=", self.company_b.id),
        ])

        self.user.company_ids = [Command.set(self.company_a.ids)]

        self.assertTrue(company_b_employee.exists())
        self.assertEqual(company_b_employee.user_id, self.user)

    def test_excluded_company_does_not_require_or_recreate_employee(self):
        excluded_employee = self.env["hr.employee"].sudo().create({
            "name": "Excluded employee identity",
            "company_id": self.company_b.id,
            "user_id": self.user.id,
            "work_contact_id": self.user.partner_id.id,
        })
        self.user.write({
            "usl_expense_excluded_company_ids": [Command.set(self.company_b.ids)],
            "usl_expense_multi_company": True,
        })
        excluded_employee.active = False

        self.user._usl_ensure_expense_company_profiles(strict=True)
        self.user._usl_ensure_expense_company_profiles(strict=True)

        company_b_profiles = self.env["hr.employee"].sudo().with_context(
            active_test=False,
        ).search([
            ("user_id", "=", self.user.id),
            ("company_id", "=", self.company_b.id),
        ])
        self.assertEqual(company_b_profiles, excluded_employee)
        self.assertFalse(excluded_employee.active)
        self.assertEqual(
            self.user.usl_expense_company_profile_status,
            "ready",
        )
        self.assertIn(
            "1 companies",
            self.user.usl_expense_company_profile_message,
        )

    def test_ambiguous_unlinked_profiles_are_not_guessed(self):
        self.env["hr.employee"].sudo().create([
            {
                "name": "Candidate one",
                "company_id": self.company_a.id,
                "work_contact_id": self.user.partner_id.id,
            },
            {
                "name": "Candidate two",
                "company_id": self.company_a.id,
                "work_contact_id": self.user.partner_id.id,
            },
        ])

        self.user.usl_expense_multi_company = True

        profile = self.env["hr.employee"].sudo().search([
            ("user_id", "=", self.user.id),
            ("company_id", "=", self.company_a.id),
        ])
        self.assertFalse(profile)
        self.assertEqual(
            self.user.usl_expense_company_profile_status,
            "attention",
        )

    def test_non_administrator_cannot_change_governance_field(self):
        with self.assertRaises(AccessError):
            self.user.with_user(self.user).write({
                "usl_expense_multi_company": True,
            })
        with self.assertRaises(AccessError):
            self.user.with_user(self.user).write({
                "usl_expense_excluded_company_ids": [Command.link(self.company_b.id)],
            })


@tagged("post_install", "-at_install", "usl_accounting")
class TestMultiCompanyOperationalSetup(TransactionCase):
    def test_french_account_groups_are_company_scoped_and_idempotent(self):
        company = self.env["res.company"].create({
            "name": "French hierarchy company",
            "currency_id": self.env.company.currency_id.id,
            "account_fiscal_country_id": self.env.ref("base.fr").id,
        })
        Group = self.env["account.group"].sudo()
        groups = Group.search([("company_id", "=", company.id)])

        self.assertEqual(len(groups), 171)
        capital = groups.filtered(
            lambda group: group.code_prefix_start == "101"
        )
        self.assertEqual(capital.parent_id.code_prefix_start, "10")
        self.assertEqual(
            capital._fields["name"]._get_stored_translations(capital)["fr_FR"],
            "Capital",
        )

        capital.name = "Reviewed capital label"
        Group._ensure_french_compatibility_groups(company)

        self.assertEqual(
            Group.search_count([("company_id", "=", company.id)]),
            171,
        )
        self.assertEqual(capital.name, "Reviewed capital label")

    def test_incomplete_accounting_company_gets_idempotent_native_journals(self):
        company = self.env["res.company"].create({
            "name": "Imported bank-only company",
            "currency_id": self.env.company.currency_id.id,
            "account_fiscal_country_id": self.env.ref("base.fr").id,
        })
        transfer_account = self.env["account.account"].with_company(
            company,
        ).create({
            "name": "Imported funds in transit",
            "code": "580100",
            "account_type": "asset_current",
            "reconcile": True,
            "company_ids": [Command.set(company.ids)],
        })
        company.transfer_account_id = transfer_account
        bank_journal = self.env["account.journal"].with_company(company).create({
            "name": "Imported bank",
            "code": "BNK1",
            "type": "bank",
            "company_id": company.id,
        })

        first = company._usl_ensure_operational_accounting_journals()
        journals = self.env["account.journal"].with_context(
            active_test=False,
        ).search([("company_id", "=", company.id)])

        self.assertEqual(set(journals.mapped("type")), {
            "bank",
            "general",
            "purchase",
            "sale",
        })
        self.assertEqual(len(first), 4)
        self.assertEqual(company.expense_journal_id.code, "NDF")
        self.assertEqual(
            (
                bank_journal.inbound_payment_method_line_ids
                | bank_journal.outbound_payment_method_line_ids
            ).payment_account_id,
            transfer_account,
        )
        partner = self.env["res.partner"].with_company(company).create({
            "name": "Imported company partner",
        })
        self.assertEqual(
            partner.property_account_receivable_id.code,
            "411000",
        )
        self.assertEqual(
            partner.property_account_payable_id.code,
            "401000",
        )
        self.assertEqual(
            len(journals.filtered(lambda journal: journal.type == "purchase")),
            2,
        )

        second = company._usl_ensure_operational_accounting_journals()
        self.assertFalse(second)
        self.assertEqual(
            self.env["account.journal"].search_count([
                ("company_id", "=", company.id),
            ]),
            len(journals),
        )

    def test_non_french_company_does_not_receive_french_accounts(self):
        company = self.env["res.company"].create({
            "name": "Non-French imported company",
            "currency_id": self.env.company.currency_id.id,
            "account_fiscal_country_id": self.env.ref("base.us").id,
        })

        company._usl_ensure_operational_accounting_journals()

        self.assertFalse(self.env["account.account"].with_company(company).search([
            ("company_ids", "in", company.id),
            ("code", "in", ["401000", "411000"]),
        ]))
        self.assertFalse(self.env["account.group"].search([
            ("company_id", "=", company.id),
        ]))

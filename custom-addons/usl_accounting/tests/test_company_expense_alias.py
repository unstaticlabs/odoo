from email.message import EmailMessage
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo.addons.hr_expense.tests.common import TestExpenseCommon
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import new_test_user, tagged


@tagged("post_install", "-at_install", "usl_company_expense_aliases")
class TestCompanyExpenseAliases(TestExpenseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sender = cls.expense_user_employee
        cls.company_a = cls.sender.company_id
        cls.company_b = (cls.sender.company_ids - cls.company_a)[:1]
        cls.employee_b = cls.env["hr.employee"].sudo().create({
            "name": "Synthetic Inbox Employee B",
            "company_id": cls.company_b.id,
            "user_id": cls.sender.id,
            "work_email": cls.sender.email,
        })
        admin_company = cls.company_b.with_user(cls.env.ref("base.user_admin"))
        admin_company.alias_domain_id = cls.env["mail.alias.domain"].sudo().create({"name": "synthetic-inbox.example.invalid"})
        admin_company.expense_alias_name = "synthetic-expenses-b"

    def _deliver(self, alias, sender=None, reference="synthetic-company-inbox", extra_alias=None, subject=None, receipt=False, gateway_sudo=False):
        native = EmailMessage()
        native["From"] = sender or self.sender.email
        native["To"] = ", ".join((alias | (extra_alias or self.env["mail.alias"])).mapped("alias_full_name"))
        native["Subject"] = subject or "Synthetic receipt 12.50"
        native["Message-ID"] = f"<{reference}@example.invalid>"
        native.set_content("Synthetic disposable receipt.")
        if receipt:
            native.add_attachment(
                b"Synthetic receipt fixture only.\n", maintype="text", subtype="plain",
                filename="synthetic-receipt.txt",
            )
        gateway = self.env["mail.thread"]
        if gateway_sudo:
            gateway = gateway.sudo()
        # Only transport is stubbed: routing, sender identity and creation are native.
        with patch.object(type(self.env["mail.mail"]), "send", autospec=True, return_value=True):
            routes = gateway.message_route(native, gateway.message_parse(native))
            if extra_alias:
                self.assertEqual({route[4].id for route in routes}, {alias.id, extra_alias.id})
            else:
                self.assertEqual(routes[0][4], alias)
            return gateway.message_process("hr.expense", native.as_bytes())

    def _verified_personal_sender(self):
        if "usl.mail.sender.alias" not in self.env:
            self.skipTest("Verified sender integration requires the canonical PocketID registry")
        personal = self.env["usl.mail.sender.alias"].sudo().with_context(
            usl_sender_alias_skip_automatic_verification=True,
        ).create({
            "partner_id": self.sender.partner_id.id,
            "email": "synthetic-personal-sender@example.invalid",
        })
        token, _link, _mail = personal._issue_verification(send=False)
        self.assertTrue(personal._verify_token(token))
        return personal

    def _assert_native_bounce_without_expense(self, email):
        native = EmailMessage()
        native["From"] = email
        native["To"] = self.company_b.expense_alias_id.alias_full_name
        native["Subject"] = "Synthetic receipt 12.50"
        native["Message-ID"] = "<synthetic-recovery@example.invalid>"
        native.set_content("Synthetic disposable receipt.")
        expenses_before = self.env["hr.expense"].search([])
        with patch.object(type(self.env["mail.mail"]), "send", autospec=True, return_value=True):
            result = self.env["mail.thread"].message_process("hr.expense", native.as_bytes())
        self.assertFalse(result)
        self.assertEqual(self.env["hr.expense"].search([]), expenses_before)

    def test_company_alias_selects_existing_employee_in_target_company(self):
        employee_ids = self.env["hr.employee"].sudo().search([]).ids
        expense = self._deliver(self.company_b.expense_alias_id)
        self.assertEqual(expense.company_id, self.company_b)
        self.assertEqual(expense.employee_id, self.employee_b)
        self.assertEqual(expense.total_amount_currency, 12.5)
        self.assertEqual(expense.currency_id, self.company_b.currency_id)
        self.assertEqual(expense.state, "draft")
        self.assertFalse(expense.account_move_id)
        self.assertTrue(expense.message_ids.filtered(
            lambda message: message.subtype_id == self.env.ref("mail.mt_note")
            and "default employee company" in str(message.body)
        ))
        self.assertEqual(self.env["hr.employee"].sudo().search([]).ids, employee_ids)

    def test_global_alias_keeps_native_company_even_with_company_defaults(self):
        global_alias = self.env.ref("hr_expense.mail_alias_expense").sudo()
        global_alias.write({
            "alias_name": "synthetic-global-expenses",
            "alias_domain_id": self.company_b.alias_domain_id.id,
            "alias_defaults": repr({"company_id": self.company_b.id}),
        })
        expense = self._deliver(global_alias)
        self.assertEqual(expense.company_id, self.company_a)
        self.assertEqual(expense.employee_id, self.expense_employee)

    def test_forged_route_values_and_context_do_not_redirect_global_creation(self):
        for marker in ("company", {"company_id": self.company_b.id, "alias_id": self.company_b.expense_alias_id.id}):
            expense = self.env["hr.expense"].with_context(
                _usl_company_expense_route=marker,
                default_company_id=self.company_b.id,
            ).message_new({
                "email_from": self.sender.email,
                "subject": "Synthetic receipt 12.50",
                "body": "Synthetic receipt", "attachments": [],
            }, {"company_id": self.company_b.id, "_usl_company_expense_route": marker})
            self.assertEqual(expense.company_id, self.company_a)
            self.assertEqual(expense.employee_id, self.expense_employee)

    def test_configuration_rename_clear_retry_domain_preserves_global(self):
        global_alias = self.env.ref("hr_expense.mail_alias_expense").sudo()
        before = global_alias.read()[0]
        company = self.company_b.with_user(self.env.ref("base.user_admin"))
        alias = company.expense_alias_id
        for name in ("synthetic-renamed", False, "synthetic-restored", "synthetic-restored"):
            company.expense_alias_name = name
            self.assertEqual(company.expense_alias_id, alias)
            self.assertEqual(alias.alias_name, name)
        company.alias_domain_id = self.env["mail.alias.domain"].sudo().create({
            "name": "synthetic-renamed.example.invalid",
        })
        self.assertEqual(alias.alias_domain_id, company.alias_domain_id)
        self.assertEqual(global_alias.read()[0], before)

    def test_ordinary_employee_cannot_configure_company_inbox(self):
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.company_b.with_user(self.sender).expense_alias_name = "unauthorized"

    def test_missing_company_employee_uses_native_alias_recovery(self):
        self.employee_b.sudo().active = False
        alias = self.company_b.expense_alias_id
        message = EmailMessage()
        message["From"] = self.sender.email
        parsed = {"email_from": self.sender.email, "author_id": self.sender.partner_id.id}
        error = self.company_b._alias_get_error(message, parsed, alias)
        self.assertEqual(error.code, "company_expense_employee")
        self.assertFalse(error.is_config_error)
        self.assertIn(self.company_b.name, error.message)
        self.assertIn("global expense address", error.message)
        self.assertTrue(alias.alias_name)
        self._assert_native_bounce_without_expense(self.sender.email)

    def test_employee_without_user_can_use_company_inbox(self):
        self.employee_b.sudo().write({"user_id": False, "work_email": self.sender.email})
        expense = self._deliver(self.company_b.expense_alias_id)
        self.assertEqual(expense.employee_id, self.employee_b)
        self.assertEqual(expense.company_id, self.company_b)

    def test_ambiguous_employee_keeps_alias_usable_with_hr_recovery(self):
        self.employee_b.sudo().user_id = False
        self.env["hr.employee"].sudo().create({
            "name": "Synthetic Duplicate Profile", "user_id": False,
            "company_id": self.company_b.id, "work_email": self.sender.email,
        })
        message = EmailMessage()
        message["From"] = self.sender.email
        error = self.company_b._alias_get_error(message, {
            "email_from": self.sender.email, "author_id": self.sender.partner_id.id,
        }, self.company_b.expense_alias_id)
        self.assertEqual(error.code, "company_expense_employee")
        self.assertIn("More than one employee", error.message)
        self.assertFalse(error.is_config_error)
        self._assert_native_bounce_without_expense(self.sender.email)

    def test_global_and_company_recipients_keep_independent_company_selection(self):
        global_alias = self.env.ref("hr_expense.mail_alias_expense").sudo()
        global_alias.write({
            "alias_name": "synthetic-global-expenses",
            "alias_domain_id": self.company_b.alias_domain_id.id,
        })
        before = self.env["hr.expense"].search([])
        self._deliver(global_alias, extra_alias=self.company_b.expense_alias_id)
        expenses = self.env["hr.expense"].search([]) - before
        self.assertEqual(len(expenses), 2)
        self.assertEqual(set(expenses.company_id.ids), {self.company_a.id, self.company_b.id})
        self.assertEqual(set(expenses.employee_id.ids), {self.expense_employee.id, self.employee_b.id})

    def test_verified_personal_sender_keeps_identity_and_selects_company_employee(self):
        personal = self._verified_personal_sender()
        identity_before = personal.read()[0]
        expense = self._deliver(self.company_b.expense_alias_id, sender=personal.email)
        self.assertEqual(expense.employee_id, self.employee_b)
        self.assertEqual(expense.company_id, self.company_b)
        self.assertEqual(expense.message_ids.filtered(
            lambda message: message.message_type == "email",
        ).author_id, self.sender.partner_id)
        self.assertEqual(personal.read()[0], identity_before)

    def test_verified_identity_wins_over_colliding_work_email(self):
        personal = self._verified_personal_sender()
        unrelated = self.env["hr.employee"].sudo().create({
            "name": "Synthetic Unrelated Sender", "company_id": self.company_b.id,
            "work_email": personal.email,
        })
        expense = self._deliver(self.company_b.expense_alias_id, sender=personal.email)
        self.assertEqual(expense.employee_id, self.employee_b)
        self.assertNotEqual(expense.employee_id, unrelated)
        self.employee_b.sudo().active = False
        self._assert_native_bounce_without_expense(personal.email)
        self.assertEqual(personal.state, "verified")

    def test_verified_personal_sender_global_inference_remains_unchanged(self):
        personal = self._verified_personal_sender()
        global_alias = self.env.ref("hr_expense.mail_alias_expense").sudo()
        global_alias.write({
            "alias_name": "synthetic-global-expenses",
            "alias_domain_id": self.company_b.alias_domain_id.id,
            "alias_defaults": repr({"company_id": self.company_b.id}),
        })
        expense = self._deliver(global_alias, sender=personal.email)
        self.assertEqual(expense.employee_id, self.expense_employee)
        self.assertEqual(expense.company_id, self.company_a)

    def test_global_and_company_inboxes_preserve_native_creation_context(self):
        global_alias = self.env.ref("hr_expense.mail_alias_expense").sudo()
        global_alias.write({
            "alias_name": "synthetic-global-expenses",
            "alias_domain_id": self.company_b.alias_domain_id.id,
        })
        self.expense_employee.sudo().user_id = False
        self.employee_b.sudo().user_id = False
        for alias in (global_alias, self.company_b.expense_alias_id):
            expense = self._deliver(alias, reference=f"synthetic-context-{alias.id}")
            self.assertEqual(expense.message_follower_ids.partner_id, self.sender.partner_id)
            self.assertFalse(expense.message_ids.filtered(
                lambda message: "Expense created" in str(message.body),
            ))

    def test_direct_native_message_new_preserves_caller_defaults(self):
        with patch.object(type(self.env["mail.mail"]), "send", autospec=True, return_value=True):
            expense = self.env["hr.expense"].with_context(
                default_payment_mode="company_account", mail_create_nolog=True,
            ).message_new({
                "email_from": self.sender.email, "subject": "Synthetic receipt 12.50",
                "body": "Synthetic disposable receipt", "attachments": [],
            })
        self.assertEqual(expense.payment_mode, "company_account")

    def test_company_alias_pointer_cannot_repurpose_global_or_other_company_alias(self):
        company = self.company_a.with_user(self.env.ref("base.user_admin"))
        global_alias = self.env.ref("hr_expense.mail_alias_expense")
        global_before = global_alias.read()[0]
        for alias in (global_alias, self.company_b.expense_alias_id):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                company.write({"expense_alias_id": alias.id})
        self.assertFalse(company.expense_alias_id)
        self.assertEqual(global_alias.read()[0], global_before)

    def test_company_alias_cannot_be_retargeted_through_native_alias_write(self):
        alias = self.company_b.expense_alias_id.with_user(self.env.ref("base.user_admin"))
        with self.assertRaises(ValidationError), self.cr.savepoint():
            alias.write({"alias_parent_thread_id": self.company_a.id})
        self.assertEqual(alias.alias_parent_thread_id, self.company_b.id)

    def test_company_write_permission_does_not_replace_native_alias_configuration_rights(self):
        manager = new_test_user(
            self.env, login="synthetic.company.manager@example.invalid",
            groups="base.group_user,base.group_erp_manager",
        )
        company = self.company_b.with_user(manager)
        company.write({"name": "Synthetic Company Manager Rename"})
        with self.assertRaises(AccessError), self.cr.savepoint():
            company.write({"expense_alias_id": self.company_b.expense_alias_id.id})
        with self.assertRaises(AccessError), self.cr.savepoint():
            company.write({"expense_alias_id": False})

    def test_multiple_native_sender_companies_do_not_break_unique_company_route(self):
        self.employee_b.sudo().active = False
        user_b = new_test_user(
            self.env, login="synthetic.other.sender@example.invalid",
            email="synthetic.other.sender@example.invalid", groups="base.group_user",
            company_id=self.company_b.id, company_ids=[(6, 0, [self.company_b.id])],
        )
        employee_b = self.env["hr.employee"].sudo().create({
            "name": "Synthetic Other Company Profile", "company_id": self.company_b.id,
            "user_id": user_b.id, "work_email": self.sender.email,
        })
        native_sender = self.env["hr.expense"]._get_employee_from_email(self.sender.email)
        self.assertEqual(len(native_sender.company_id), 2)
        expense = self._deliver(self.company_b.expense_alias_id)
        self.assertEqual(expense.employee_id, employee_b)
        self.assertEqual(expense.company_id, self.company_b)
        self.assertNotEqual(self.company_b.expense_alias_id.alias_status, "invalid")

    def test_native_product_account_tax_currency_and_receipt_follow_destination(self):
        account = self.company_data_2["default_account_expense"]
        tax = self.company_data_2["default_tax_purchase"]
        self.product_c.with_company(self.company_b).property_account_expense_id = account
        self.product_c.supplier_taxes_id |= tax
        expense = self._deliver(
            self.company_b.expense_alias_id,
            subject=f"{self.product_c.default_code} Synthetic receipt 12.50", receipt=True,
        )
        self.assertEqual(expense.product_id, self.product_c)
        self.assertEqual(expense.account_id, account)
        self.assertEqual(expense.tax_ids, tax)
        self.assertEqual(expense.currency_id, self.company_b.currency_id)
        self.assertEqual(bytes(expense.attachment_ids.filtered(
            lambda attachment: attachment.name == "synthetic-receipt.txt",
        ).raw), b"Synthetic receipt fixture only.\n")
        self.assertFalse(expense.account_move_id)

    def test_settings_save_is_company_scoped_and_pointer_clear_disables_owned_alias(self):
        global_alias = self.env.ref("hr_expense.mail_alias_expense").sudo()
        before = global_alias.read()[0]
        settings = self.env["res.config.settings"].with_user(self.env.ref("base.user_admin")).create({
            "company_id": self.company_b.id,
            "company_expense_alias_name": "synthetic-saved-settings",
        })
        settings.execute()
        self.assertEqual(self.company_b.expense_alias_name, "synthetic-saved-settings")
        self.assertFalse(self.company_a.expense_alias_id)
        alias = self.company_b.expense_alias_id
        self.company_b.with_user(self.env.ref("base.user_admin")).write({"expense_alias_id": False})
        self.assertFalse(self.company_b.expense_alias_id)
        self.assertTrue(alias.exists())
        self.assertFalse(alias.alias_name)
        self.assertEqual(global_alias.read()[0], before)

    def test_empty_company_deletion_cleans_owned_aliases_only(self):
        global_alias = self.env.ref("hr_expense.mail_alias_expense").sudo()
        other_alias = self.company_b.expense_alias_id
        global_before = global_alias.read()[0]
        other_before = other_alias.read()[0]
        company = self.env["res.company"].with_user(self.env.ref("base.user_admin")).create({
            "name": "Synthetic Disposable Inbox Company",
            "alias_domain_id": self.company_b.alias_domain_id.id,
        })
        self.assertFalse(company.expense_alias_id)
        company.expense_alias_name = "synthetic-disposable-first"
        detached_alias = company.expense_alias_id
        company.write({"expense_alias_id": False})
        company.expense_alias_name = "synthetic-disposable-second"
        current_alias = company.expense_alias_id
        self.assertFalse(detached_alias.alias_name)
        if "stock.warehouse" in self.env and self.env["stock.warehouse"].sudo().search_count([
            ("company_id", "=", company.id),
        ]):
            self.skipTest(
                "Native stock created warehouse references in this disposable company; "
                "positive empty-company cleanup is qualified in the accounting-only registry. "
                "Canonical native refusal/preservation is exercised separately.",
            )
        providers = self.env["payment.provider"].sudo().search([
            ("company_id", "=", company.id),
        ])
        if providers:
            # A full native registry copies disabled providers on company
            # creation. Prove native deletion still blocks before cleanup.
            self.assertEqual(set(providers.mapped("state")), {"disabled"})
            self.assertFalse(self.env["payment.transaction"].sudo().search_count([
                ("provider_id", "in", providers.ids),
            ]))
            self.assertFalse(self.env["payment.token"].sudo().search_count([
                ("provider_id", "in", providers.ids),
            ]))
            with self.assertRaises(IntegrityError), self.cr.savepoint():
                company.unlink()
            self.assertTrue((detached_alias | current_alias).exists())
            providers.with_user(self.env.ref("base.user_admin")).unlink()
        company.unlink()
        self.assertFalse((detached_alias | current_alias).exists())
        self.assertEqual(global_alias.read()[0], global_before)
        self.assertEqual(other_alias.read()[0], other_before)

    def test_native_company_delete_refusal_preserves_current_and_detached_aliases(self):
        company = self.company_b.with_user(self.env.ref("base.user_admin"))
        detached = company.expense_alias_id
        company.write({"expense_alias_id": False})
        company.expense_alias_name = "synthetic-native-refusal"
        current = company.expense_alias_id
        global_alias = self.env.ref("hr_expense.mail_alias_expense").sudo()
        aliases = detached | current | global_alias
        self.env.flush_all()
        self.cr.execute("SELECT to_jsonb(c) FROM res_company c WHERE id = %s", [company.id])
        company_before = self.cr.fetchone()[0]
        self.cr.execute("SELECT to_jsonb(a) FROM mail_alias a WHERE id = ANY(%s) ORDER BY id", [aliases.ids])
        alias_before = self.cr.fetchall()
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            company.unlink()
        self.assertTrue(company.exists())
        self.cr.execute("SELECT to_jsonb(c) FROM res_company c WHERE id = %s", [company.id])
        self.assertEqual(self.cr.fetchone()[0], company_before)
        self.cr.execute("SELECT to_jsonb(a) FROM mail_alias a WHERE id = ANY(%s) ORDER BY id", [aliases.ids])
        self.assertEqual(self.cr.fetchall(), alias_before)

    def test_archived_company_retains_explicit_native_alias_destination(self):
        self.company_b.with_user(self.env.ref("base.user_admin")).active = False
        expense = self._deliver(self.company_b.expense_alias_id, gateway_sudo=True)
        self.assertEqual(expense.company_id, self.company_b)
        self.assertEqual(expense.employee_id, self.employee_b)

    def test_archived_company_retains_ordinary_native_hr_recovery(self):
        self.company_b.with_user(self.env.ref("base.user_admin")).active = False
        self._assert_native_bounce_without_expense(self.sender.email)

    def test_archived_company_alias_still_validates_native_owner(self):
        self.company_b.with_user(self.env.ref("base.user_admin")).active = False
        alias = self.company_b.expense_alias_id.with_user(self.env.ref("base.user_admin"))
        with self.assertRaises(ValidationError), self.cr.savepoint():
            alias.write({"alias_parent_thread_id": self.company_a.id})
        self.assertEqual(alias.alias_parent_thread_id, self.company_b.id)

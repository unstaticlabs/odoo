import base64
from datetime import date, timedelta

from odoo import Command, SUPERUSER_ID, api


MODULE = "usl_bootstrap"
FRENCH_COMPANY_CHART = "fr_comp"


def post_init_hook(env):
    env = api.Environment(env.cr, SUPERUSER_ID, {})
    Bootstrap(env).run()


class Bootstrap:
    def __init__(self, env):
        self.env = env
        self.company = env.ref("base.main_company")
        self.eur = env.ref("base.EUR")
        self.today = date(2026, 7, 21)

    def run(self):
        self._configure_company()
        self._configure_admin_user()
        self._configure_expense_approver_user()
        self._create_contacts()
        self._create_employee()
        self._defer_accounting_seed()

    def _seed_after_chart(self):
        if self.company.chart_template != FRENCH_COMPANY_CHART:
            self._load_accounting()
        self._create_analytics()
        self._create_projects_and_tasks()
        self._create_journals()
        self._create_products()
        self._create_invoices_and_bills()
        self._create_expenses()
        self._create_bank_statement_lines()

    def _defer_accounting_seed(self):
        previous_loader = getattr(self.env.registry, "_auto_install_template", None)

        def load_chart_then_seed(env):
            if previous_loader:
                previous_loader(env)
            Bootstrap(env)._seed_after_chart()

        self.env.registry._auto_install_template = load_chart_then_seed

    def _xmlid(self, name, record):
        data = self.env["ir.model.data"].sudo()
        existing = data.search([("module", "=", MODULE), ("name", "=", name)], limit=1)
        vals = {"module": MODULE, "name": name, "model": record._name, "res_id": record.id, "noupdate": True}
        if existing:
            existing.write(vals)
        else:
            data.create(vals)
        return record

    def _ref(self, name):
        return self.env.ref(f"{MODULE}.{name}", raise_if_not_found=False)

    def _upsert(self, xmlid, model, values, domain=None):
        record = self._ref(xmlid)
        if not record and domain:
            record = self.env[model].search(domain, limit=1)
        if record:
            record.write(values)
        else:
            record = self.env[model].create(values)
        return self._xmlid(xmlid, record)

    def _configure_company(self):
        france = self.env.ref("base.fr")
        self.eur.active = True
        self.env["res.currency"].with_context(active_test=False).search([("name", "in", ["USD", "GBP"])]).write({"active": True})
        lang = self.env["res.lang"]._activate_lang("fr_FR") or self.env["res.lang"].search([("code", "=", "fr_FR")], limit=1)
        self.company.write({
            "name": "Unstatic Labs",
            "currency_id": self.eur.id,
            "country_id": france.id,
            "street": "14 rue du Demo",
            "street2": "Etage 2",
            "zip": "75010",
            "city": "Paris",
            "email": "admin@unstatic-labs.test",
            "phone": "+33 1 23 45 67 89",
            "vat": "FR88100000009",
            "company_registry": "10000000900009",
        })
        self.company.partner_id.write({
            "name": "Unstatic Labs",
            "category_id": [Command.link(self.env.ref(f"{MODULE}.partner_category_usl_dev").id)],
        })
        admin = self.env.ref("base.user_admin")
        admin.write({
            "name": "Valentin",
            "login": "admin",
            "email": "valentin@unstatic-labs.test",
            "tz": "Europe/Paris",
            "lang": lang.code if lang else "fr_FR",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "notification_type": "inbox",
        })
        self._upsert("bank_account_shine_dev", "res.partner.bank", {
            "account_number": "FR7630006000011234567890189",
            "partner_id": self.company.partner_id.id,
            "company_id": self.company.id,
            "allow_out_payment": True,
        }, [("account_number", "=", "FR7630006000011234567890189")])

    def _load_accounting(self):
        if self.company.chart_template != FRENCH_COMPANY_CHART:
            self.env["account.chart.template"].try_loading(
                FRENCH_COMPANY_CHART,
                company=self.company,
                install_demo=False,
            )

    def _configure_admin_user(self):
        admin = self.env.ref("base.user_admin")
        groups = [
            "base.group_user",
            "account.group_account_manager",
            "hr.group_hr_manager",
            "hr_expense.group_hr_expense_manager",
            "project.group_project_manager",
            "sales_team.group_sale_manager",
        ]
        for group_xmlid in groups:
            group = self.env.ref(group_xmlid, raise_if_not_found=False)
            if group:
                admin.group_ids = [Command.link(group.id)]

    def _configure_expense_approver_user(self):
        groups = [
            self.env.ref("base.group_user"),
            self.env.ref("hr_expense.group_hr_expense_team_approver", raise_if_not_found=False),
        ]
        self._upsert("user_expense_approver", "res.users", {
            "name": "USL Expense Approver",
            "login": "approver@unstatic-labs.test",
            "email": "approver@unstatic-labs.test",
            "tz": "Europe/Paris",
            "lang": "fr_FR",
            "company_id": self.company.id,
            "company_ids": [Command.set([self.company.id])],
            "group_ids": [Command.set([group.id for group in groups if group])],
            "notification_type": "inbox",
        }, [("login", "=", "approver@unstatic-labs.test")])

    def _create_contacts(self):
        category_id = self.env.ref(f"{MODULE}.partner_category_usl_dev").id
        contacts = {
            "partner_customer_arcade": ("Arcade Atelier SAS", "customer", "client@arcade-atelier.test", "Paris", "FR15100000017"),
            "partner_supplier_print": ("Studio Papier Lyon", "supplier", "factures@studio-papier.test", "Lyon", "FR39100000025"),
            "partner_supplier_eu": ("Nordlicht Media GmbH", "supplier", "billing@nordlicht-media.test", "Berlin", False),
            "partner_software": ("SaaS Sandbox Ltd", "supplier", "invoices@saas-sandbox.test", "Dublin", "IE1234567T"),
            "partner_creator": ("Creator Collab Demo", "contact", "hello@creator-collab.test", "Marseille", False),
            "partner_adviser": ("Cabinet Conseil Demo", "supplier", "contact@cabinet-conseil.test", "Paris", "FR63100000033"),
        }
        for xmlid, (name, role, email, city, vat) in contacts.items():
            values = {
                "name": name,
                "is_company": role != "contact",
                "email": email,
                "phone": "+33 9 00 00 00 00",
                "street": "1 avenue Fiction",
                "zip": "75001" if city == "Paris" else "69001",
                "city": city,
                "country_id": self.env.ref("base.de").id if "GmbH" in name else self.env.ref("base.fr").id,
                "vat": vat,
                "category_id": [Command.link(category_id)],
                "company_id": False,
                "customer_rank": 1 if role == "customer" else 0,
                "supplier_rank": 1 if role == "supplier" else 0,
            }
            self._upsert(xmlid, "res.partner", values, [("name", "=", name)])

    def _create_analytics(self):
        plan = self._upsert("analytic_plan_usl", "account.analytic.plan", {
            "name": "USL Activities",
            "default_applicability": "optional",
        }, [("name", "=", "USL Activities")])
        for code, name in [
            ("ADMIN", "USL Admin"),
            ("SBFH", "SBFH"),
            ("GBC", "GBC"),
            ("YOSHI", "Yoshi"),
            ("SMASH", "Smash"),
            ("KINK", "KinkVerse"),
            ("ODOO", "Odoo Rebuild"),
            ("AI", "AI Pipelines"),
        ]:
            self._upsert(f"analytic_{code.lower()}", "account.analytic.account", {
                "name": name,
                "code": code,
                "plan_id": plan.id,
                "company_id": self.company.id,
            }, [("code", "=", code), ("plan_id", "=", plan.id)])

    def _create_journals(self):
        for name, code, currency in [
            ("Banque Shine", "SHINE", False),
            ("Revolut USD", "REVUSD", self.env.ref("base.USD")),
            ("Revolut GBP", "REVGBP", self.env.ref("base.GBP")),
            ("Wise USD", "WISUSD", self.env.ref("base.USD")),
        ]:
            journal = self._upsert(f"journal_{code.lower()}", "account.journal", {
                "name": name,
                "code": code,
                "type": "bank",
                "company_id": self.company.id,
                "currency_id": currency.id if currency else False,
            }, [("name", "=", name), ("company_id", "=", self.company.id)])
            if not journal.default_account_id:
                account = self.env["account.account"].search([
                    ("company_ids", "in", self.company.id),
                    ("account_type", "=", "asset_cash"),
                ], limit=1)
                journal.default_account_id = account
        for name, code, jtype in [
            ("Sales Journal", "INV", "sale"),
            ("Purchase Journal", "BILL", "purchase"),
            ("Miscellaneous Operations", "MISC", "general"),
            ("Expense Journal", "EXP", "purchase"),
        ]:
            self._upsert(f"journal_{code.lower()}", "account.journal", {
                "name": name,
                "code": code,
                "type": jtype,
                "company_id": self.company.id,
            }, [("code", "=", code), ("company_id", "=", self.company.id)])

    def _create_products(self):
        expense_account = self.company.expense_account_id
        tax = self._purchase_tax()
        for xmlid, name in [
            ("expense_meals", "Meals and invitations"),
            ("expense_travel", "Travel"),
            ("expense_accommodation", "Accommodation"),
            ("expense_production", "Production expenses"),
            ("expense_software", "Software and subscriptions"),
            ("expense_remote", "Remote-work allowance"),
            ("expense_equipment", "Equipment"),
        ]:
            self._upsert(xmlid, "product.product", {
                "name": name,
                "type": "service",
                "standard_price": 0,
                "can_be_expensed": True,
                "purchase_ok": True,
                "sale_ok": False,
                "supplier_taxes_id": [Command.set(tax.ids)],
                "property_account_expense_id": expense_account.id if expense_account else False,
            }, [("name", "=", name)])
        income = self.company.income_account_id
        sale_tax = self._sale_tax()
        self._upsert("product_service_build", "product.product", {
            "name": "Development service package",
            "type": "service",
            "lst_price": 2400,
            "sale_ok": True,
            "purchase_ok": False,
            "taxes_id": [Command.set(sale_tax.ids)],
            "property_account_income_id": income.id if income else False,
        }, [("name", "=", "Development service package")])

    def _create_employee(self):
        user = self.env.ref("base.user_admin")
        partner = user.partner_id
        bank = self._upsert("bank_account_valentin_dev", "res.partner.bank", {
            "account_number": "FR7630004000039876543210176",
            "partner_id": partner.id,
            "company_id": self.company.id,
            "allow_out_payment": True,
        }, [("account_number", "=", "FR7630004000039876543210176")])
        employee = self.env["hr.employee"].search([("user_id", "=", user.id)], limit=1)
        values = {
            "name": "Valentin",
            "user_id": user.id,
            "work_email": "valentin@unstatic-labs.test",
            "company_id": self.company.id,
            "work_contact_id": partner.id,
            "expense_manager_id": self._ref("user_expense_approver").id,
            "bank_account_ids": [Command.link(bank.id)],
        }
        if employee:
            employee.write(values)
        else:
            employee = self.env["hr.employee"].create(values)
        self._xmlid("employee_valentin", employee)
        user.employee_id = employee.id

    def _create_projects_and_tasks(self):
        stages = []
        for seq, name, fold in [
            (10, "Backlog", False), (20, "Ready", False), (30, "In Progress", False),
            (40, "Waiting", False), (50, "Review", False), (60, "Done", True),
        ]:
            stages.append(self._upsert(f"stage_{name.lower().replace(' ', '_')}", "project.task.type", {
                "name": name,
                "sequence": seq,
                "fold": fold,
            }, [("name", "=", name), ("user_id", "=", False)]))
        projects = {}
        for name, analytic_xmlid in [
            ("USL Admin", "analytic_admin"), ("SBFH Production", "analytic_sbfh"), ("SBFH Vault", "analytic_sbfh"),
            ("Yoshi", "analytic_yoshi"), ("KinkVerse Development", "analytic_kink"), ("KinkVerse Discovery", "analytic_kink"),
            ("Smash", "analytic_smash"), ("AI Pipelines", "analytic_ai"), ("Odoo Rebuild", "analytic_odoo"),
        ]:
            project = self._upsert(f"project_{name.lower().replace(' ', '_')}", "project.project", {
                "name": name,
                "company_id": self.company.id,
                "allow_milestones": True,
                "account_id": self._ref(analytic_xmlid).id,
                "type_ids": [Command.set([s.id for s in stages])],
                "favorite_user_ids": [Command.link(self.env.ref("base.user_admin").id)],
            }, [("name", "=", name), ("company_id", "=", self.company.id)])
            projects[name] = project
        waiting_tag = self.env.ref(f"{MODULE}.project_tag_waiting_external")
        github_tag = self.env.ref(f"{MODULE}.project_tag_github_source")
        dev_tag = self.env.ref(f"{MODULE}.project_tag_dev_data")
        user = self.env.ref("base.user_admin")
        task_defs = [
            ("task_admin_closing", "Prepare July admin close checklist", "USL Admin", "In Progress", 7, [dev_tag]),
            ("task_sbfh_brief", "Draft SBFH production brief", "SBFH Production", "Ready", 14, [dev_tag]),
            ("task_vault_archive", "Review vault taxonomy sample", "SBFH Vault", "Waiting", 10, [waiting_tag]),
            ("task_yoshi_specs", "Define Yoshi MVP accounting boundaries", "Yoshi", "Backlog", 21, [github_tag]),
            ("task_kinkverse_discovery", "Summarize KinkVerse discovery notes", "KinkVerse Discovery", "Review", 5, [dev_tag]),
            ("task_smash_roadmap", "Create Smash experiment backlog", "Smash", "Done", -2, [dev_tag]),
            ("task_ai_pipeline", "Map receipt capture pipeline gap", "AI Pipelines", "Waiting", 12, [waiting_tag]),
            ("task_odoo_rebuild", "Bootstrap Odoo rebuild demo database", "Odoo Rebuild", "In Progress", 3, [github_tag]),
        ]
        for xmlid, name, project_name, stage_name, days, tags in task_defs:
            task = self._upsert(xmlid, "project.task", {
                "name": name,
                "project_id": projects[project_name].id,
                "stage_id": next(s for s in stages if s.name == stage_name).id,
                "user_ids": [Command.set([user.id])],
                "date_deadline": self.today + timedelta(days=days),
                "tag_ids": [Command.set([t.id for t in tags])],
                "description": "Development data for the USL local bootstrap baseline.",
            }, [("name", "=", name), ("project_id", "=", projects[project_name].id)])
            task.message_post(body="USL development baseline chatter note.")
        self._ref("task_vault_archive").activity_schedule("mail.mail_activity_data_todo", date_deadline=self.today + timedelta(days=3), user_id=user.id, summary="Follow up on external response")
        self._attach(self._ref("task_sbfh_brief"), "usl_project_brief.txt", "Fictional USL project brief for local development.")
        self._attach(self._ref("task_admin_closing"), "usl_admin_document.txt", "Fictional administrative document for local development.")

    def _create_invoices_and_bills(self):
        product = self._ref("product_service_build")
        customer = self._ref("partner_customer_arcade")
        supplier = self._ref("partner_supplier_print")
        software = self._ref("partner_software")
        sale_line = {"product_id": product.id, "quantity": 1, "price_unit": 2400, "name": product.name}
        self._upsert("invoice_customer_draft", "account.move", {
            "move_type": "out_invoice",
            "partner_id": customer.id,
            "invoice_date": self.today,
            "company_id": self.company.id,
            "invoice_line_ids": [Command.clear(), Command.create(sale_line)],
        }, [("ref", "=", "USL-DEV-CINV-001")]).ref = "USL-DEV-CINV-001"
        for xmlid, partner, ref, amount in [
            ("bill_supplier_draft", supplier, "USL-DEV-VBILL-001", 380),
            ("bill_software_draft", software, "USL-DEV-VBILL-002", 59),
        ]:
            bill = self._upsert(xmlid, "account.move", {
                "move_type": "in_invoice",
                "partner_id": partner.id,
                "invoice_date": self.today,
                "company_id": self.company.id,
                "ref": ref,
                "invoice_line_ids": [Command.clear(), Command.create({
                    "name": "Development sample vendor bill",
                    "quantity": 1,
                    "price_unit": amount,
                    "account_id": self.company.expense_account_id.id,
                    "tax_ids": [Command.set(self._purchase_tax().ids)],
                })],
            }, [("ref", "=", ref), ("company_id", "=", self.company.id)])
            self._attach(bill, f"{ref.lower()}.txt", "Fictional supplier invoice for USL local development.")
        order = self._upsert("sale_order_customer_draft", "sale.order", {
            "partner_id": customer.id,
            "company_id": self.company.id,
            "order_line": [Command.clear(), Command.create({
                "product_id": product.id,
                "product_uom_qty": 1,
                "price_unit": 2400,
            })],
        }, [("client_order_ref", "=", "USL-DEV-SO-001")])
        order.client_order_ref = "USL-DEV-SO-001"

    def _create_expenses(self):
        employee = self._ref("employee_valentin")
        states = [
            ("expense_draft_meal", "Draft meal with VAT", "expense_meals", 42.50, "EUR", "draft", "analytic_admin"),
            ("expense_submitted_usd", "Submitted software subscription USD", "expense_software", 29.00, "USD", "submitted", "analytic_yoshi"),
            ("expense_approved_travel", "Approved travel sample", "expense_travel", 188.40, "EUR", "approved", "analytic_sbfh"),
            ("expense_posted_equipment", "Awaiting reimbursement equipment", "expense_equipment", 612.00, "GBP", "posted", "analytic_odoo"),
        ]
        for xmlid, name, product_xmlid, amount, currency_name, state, analytic_xmlid in states:
            currency = self.env["res.currency"].with_context(active_test=False).search([("name", "=", currency_name)], limit=1)
            expense = self._upsert(xmlid, "hr.expense", {
                "name": name,
                "employee_id": employee.id,
                "company_id": self.company.id,
                "date": self.today,
                "product_id": self._ref(product_xmlid).id,
                "total_amount_currency": amount,
                "currency_id": currency.id,
                "payment_mode": "own_account",
                "analytic_distribution": {str(self._ref(analytic_xmlid).id): 100},
                "tax_ids": [Command.set(self._purchase_tax().ids if currency == self.eur else [])],
            }, [("name", "=", name), ("company_id", "=", self.company.id)])
            self._attach(expense, f"{xmlid}.txt", "Fictional receipt for USL local development.")
            if state in {"submitted", "approved", "posted"} and expense.state == "draft":
                expense.action_submit()
            if state in {"approved", "posted"} and expense.state == "submitted":
                expense.action_approve()
            if state == "posted" and expense.state == "approved":
                action = expense.action_post()
                if action:
                    wizard = self.env["hr.expense.post.wizard"].with_context(action["context"]).browse(action["res_id"])
                    wizard.accounting_date = self.today
                    wizard.action_post_entry()

    def _create_bank_statement_lines(self):
        journal = self._ref("journal_shine")
        if not journal:
            return
        for xmlid, ref, amount, partner_xmlid in [
            ("bank_line_incoming", "USL-DEV bank incoming customer payment", 1200.00, "partner_customer_arcade"),
            ("bank_line_outgoing", "USL-DEV bank outgoing software payment", -59.00, "partner_software"),
        ]:
            self._upsert(xmlid, "account.bank.statement.line", {
                "date": self.today,
                "journal_id": journal.id,
                "company_id": self.company.id,
                "payment_ref": ref,
                "amount": amount,
                "partner_id": self._ref(partner_xmlid).id,
            }, [("payment_ref", "=", ref), ("journal_id", "=", journal.id)])

    def _sale_tax(self):
        return self.env["account.tax"].search([
            ("company_id", "=", self.company.id),
            ("type_tax_use", "=", "sale"),
            ("amount", "=", 20),
        ], limit=1)

    def _purchase_tax(self):
        return self.env["account.tax"].search([
            ("company_id", "=", self.company.id),
            ("type_tax_use", "=", "purchase"),
            ("amount", "=", 20),
        ], limit=1)

    def _attach(self, record, name, content):
        attachment = self.env["ir.attachment"].search([
            ("res_model", "=", record._name),
            ("res_id", "=", record.id),
            ("name", "=", name),
        ], limit=1)
        values = {
            "name": name,
            "res_model": record._name,
            "res_id": record.id,
            "type": "binary",
            "datas": base64.b64encode(content.encode()).decode(),
            "mimetype": "text/plain",
        }
        if attachment:
            attachment.write(values)
        else:
            attachment = self.env["ir.attachment"].create(values)
        if hasattr(record, "message_main_attachment_id") and not record.message_main_attachment_id:
            record.message_main_attachment_id = attachment.id
        return attachment

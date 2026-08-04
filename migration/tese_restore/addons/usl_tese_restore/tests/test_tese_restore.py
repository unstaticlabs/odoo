from datetime import date

from odoo import Command
from odoo.tests import TransactionCase, tagged

from odoo.addons.usl_tese_payroll.models.constants import TESE_COMPONENTS


@tagged("post_install", "-at_install", "usl_tese_restore")
class TestTeseRestore(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.sudo().write({
            "rebuild_source_database": "source",
            "rebuild_source_model": "res.company",
            "rebuild_source_id": 1,
        })
        cls.employee_partner = cls.env["res.partner"].sudo().create({
            "name": "Imported Employee",
            "email": "employee@example.test",
            "company_id": cls.company.id,
            "rebuild_source_database": "source",
            "rebuild_source_model": "res.partner",
            "rebuild_source_id": 3,
        })
        cls.collector = cls.env["res.partner"].sudo().create({
            "name": "URSSAF TESE",
            "company_id": cls.company.id,
            "rebuild_source_database": "source",
            "rebuild_source_model": "res.partner",
            "rebuild_source_id": 54,
        })
        cls.journal = cls.env["account.journal"].sudo().create({
            "name": "Imported TESE",
            "code": "ITSE",
            "type": "general",
            "company_id": cls.company.id,
        })
        amounts = {
            "641100": 3000.0,
            "645100": 600.0,
            "645200": 50.0,
            "645300": 150.0,
            "633300": 30.0,
            "633500": 20.0,
            "421000": 2300.0,
            "431000": 1100.0,
            "437020": 100.0,
            "437030": 250.0,
            "442100": 100.0,
        }
        cls.amounts = amounts
        cls.accounts = {}
        for source_id, component in enumerate(TESE_COMPONENTS, start=100):
            liability = component["side"] == "credit"
            account = cls.env["account.account"].sudo().create({
                "name": f"Imported {component['name']}",
                "code": component["code"],
                "account_type": (
                    "liability_current" if liability else "expense"
                ),
                "reconcile": liability,
                "company_ids": [Command.set(cls.company.ids)],
                "rebuild_source_database": "source",
                "rebuild_source_model": "account.account",
                "rebuild_source_id": source_id,
            })
            cls.accounts[component["code"]] = (source_id, account)
        cls.move = cls.env["account.move"].sudo().create({
            "move_type": "entry",
            "company_id": cls.company.id,
            "journal_id": cls.journal.id,
            "date": date(2026, 1, 31),
            "ref": "Imported payroll",
            "rebuild_source_database": "source",
            "rebuild_source_model": "account.move.native_engine_replay",
            "rebuild_source_id": 9000,
            "line_ids": [
                Command.create({
                    "name": component["name"],
                    "account_id": cls.accounts[component["code"]][1].id,
                    "partner_id": (
                        cls.employee_partner.id
                        if component["role"] in {"gross", "salary"}
                        else cls.collector.id
                    ),
                    "debit": (
                        amounts[component["code"]]
                        if component["side"] == "debit"
                        else 0
                    ),
                    "credit": (
                        amounts[component["code"]]
                        if component["side"] == "credit"
                        else 0
                    ),
                })
                for component in TESE_COMPONENTS
            ],
        })
        cls.move.action_post()

    def _payload(self):
        account_values = {}
        for code, (source_id, _account) in self.accounts.items():
            account_values[f"x_account_{code}_id"] = source_id
            account_values[f"x_amount_{code}"] = self.amounts[code]
        profile = {
            "id": 10,
            "x_name": "Imported TESE profile",
            "x_active": True,
            "x_company_id": 1,
            "x_employee_id": 1,
            "x_hr_version_id": 1,
            "x_tese_collector_partner_id": 54,
            "x_valid_from": date(2026, 1, 1),
            "x_valid_to": False,
            "x_default_hours": 151.67,
            "x_gross_salary": 3000.0,
            "x_employee_contrib_total": 600.0,
            "x_employer_contrib_total": 850.0,
            "x_net_social": 2400.0,
            "x_net_before_tax": 2400.0,
            "x_income_tax_base": 2400.0,
            "x_income_tax_rate": 4.1667,
            "x_income_tax_amount": 100.0,
            "x_net_paid": 2300.0,
            "x_review_status": "ok",
            "x_review_message": "Reviewed",
            "x_last_used_date": date(2026, 1, 31),
            **account_values,
        }
        archived_profile = {
            **profile,
            "id": 11,
            "x_name": "Archived TESE profile",
            "x_active": False,
            "x_valid_from": date(2025, 1, 1),
            "x_valid_to": date(2025, 12, 31),
            "x_review_status": "archived",
            "x_last_used_date": date(2025, 12, 31),
        }
        payslip = {
            "id": 20,
            "x_name": "Imported January payroll",
            "x_company_id": 1,
            "x_employee_id": 1,
            "x_hr_version_id": 1,
            "x_profile_id": 10,
            "x_tese_collector_partner_id": 54,
            "x_pay_month": 1,
            "x_pay_year": 2026,
            "x_period_start": date(2026, 1, 1),
            "x_period_end": date(2026, 1, 31),
            "x_payment_date": date(2026, 2, 1),
            "x_payslip_date": date(2026, 1, 31),
            "x_tese_payment_date": date(2026, 2, 15),
            "x_tese_reference": "TESE-IMPORTED-01",
            "x_hours": 151.67,
            "source_attachment_id": 700,
            "x_gross_salary": 3000.0,
            "x_employee_contrib_total": 600.0,
            "x_employer_contrib_total": 850.0,
            "x_net_social": 2400.0,
            "x_net_before_tax": 2400.0,
            "x_income_tax_base": 2400.0,
            "x_income_tax_rate": 4.1667,
            "x_income_tax_amount": 100.0,
            "x_net_paid": 2300.0,
            "x_move_id": 9000,
            "x_move_ref": "Imported payroll",
            "x_total_debit": 3850.0,
            "x_total_credit": 3850.0,
            "x_balance_diff": 0.0,
            "x_check_ok": True,
            "x_check_message": "Prepared",
            "x_control_checklist": "Balanced",
            "x_tese_contrib_total": 1450.0,
            "x_tese_income_tax_total": 100.0,
            "x_tese_detailed_total": 1550.0,
            "x_tese_bank_amount": 1550.0,
            "x_tese_bank_diff": 0.0,
            "x_profile_snapshot_label": "Imported TESE profile",
            "x_profile_snapshot_text": "Immutable source snapshot",
            "x_employee_snapshot_name": "Imported Employee",
            "x_hr_wage_snapshot": 3000.0,
            "x_hr_hours_snapshot": 151.67,
            "x_profile_valid_from_snapshot": date(2026, 1, 1),
            "x_profile_valid_to_snapshot": False,
            **account_values,
        }
        return {
            "employees": [{
                "id": 1,
                "company_id": 1,
                "current_version_id": 1,
                "user_id": False,
                "work_contact_id": 3,
                "name": "Imported Employee",
                "work_email": "employee@example.test",
                "active": True,
            }],
            "versions": [{
                "id": 1,
                "company_id": 1,
                "employee_id": 1,
                "date_version": date(2026, 1, 1),
                "contract_date_start": date(2026, 1, 1),
                "wage": 3000.0,
                "hours_per_week": 35.0,
                "employee_type": "employee",
                "active": True,
            }],
            "profiles": [archived_profile, profile],
            "payslips": [payslip],
            "partners": [
                {
                    "id": 3,
                    "name": "Imported Employee",
                    "email": "employee@example.test",
                },
                {"id": 54, "name": "URSSAF TESE", "email": False},
            ],
            "users": [],
            "countries": [],
            "employee_documents": [{
                "id": 700,
                "source_document_id": 70,
                "source_employee_id": 1,
                "name": "payroll.pdf",
                "type": "binary",
                "mimetype": "application/pdf",
                "db_datas": b"%PDF-1.4 imported payroll",
                "store_fname": False,
                "checksum": "fixture",
                "res_model": "account.move",
                "res_id": 9000,
            }],
            "employee_images": [],
            "messages": [{
                "id": 800,
                "model": "hr.employee",
                "res_id": 1,
                "parent_id": False,
                "author_id": 3,
                "subtype_id": False,
                "subject": "Imported history",
                "body": "Historical employee message",
                "message_type": "comment",
                "date": "2026-01-02 10:00:00",
            }],
            "tracking": [{
                "id": 810,
                "field_id": 1,
                "mail_message_id": 800,
                "source_field_model": "hr.employee",
                "source_field_name": "name",
                "old_value_char": "Old name",
                "new_value_char": "Imported Employee",
                "field_info": {
                    "name": "name",
                    "desc": "Name",
                    "type": "char",
                },
            }],
            "followers": [{
                "id": 820,
                "res_model": "hr.employee",
                "res_id": 1,
                "partner_id": 3,
            }],
            "follower_subtypes": [],
            "subtype_xmlids": [],
        }

    def _run(self):
        run = self.env["usl.tese.restore.run"].sudo().create({
            "source_database": "source",
            "source_snapshot": "fixture",
            "target_database": self.env.cr.dbname,
        })
        statistics = run.action_restore(self._payload())
        self.assertEqual(run.status, "passed")
        self.assertFalse(run.issue_ids.filtered(
            lambda issue: issue.severity == "error",
        ))
        return run, statistics

    def test_restore_is_idempotent_and_derives_open_status(self):
        _first_run, first = self._run()
        counts = {
            model: self.env[model].sudo().search_count([])
            for model in (
                "hr.employee",
                "hr.version",
                "usl.tese.profile",
                "usl.tese.payslip",
                "mail.message",
                "mail.tracking.value",
                "mail.followers",
            )
        }
        _second_run, second = self._run()

        self.assertEqual(first, second)
        self.assertEqual(first["employees"], 1)
        self.assertEqual(first["versions"], 1)
        self.assertEqual(first["profiles"], 2)
        self.assertEqual(first["payslips"], 1)
        self.assertEqual(first["payroll_moves"], 1)
        self.assertEqual(first["payroll_pdfs"], 1)
        self.assertEqual(first["messages"], 1)
        self.assertEqual(first["tracking_values"], 1)
        self.assertEqual(first["followers"], 1)
        self.assertEqual(first["paid"], 0)
        self.assertEqual(first["to_reconcile"], 1)
        self.assertEqual(
            counts,
            {
                model: self.env[model].sudo().search_count([])
                for model in counts
            },
        )
        payslip = self.env["usl.tese.payslip"].sudo().search([
            ("tese_reference", "=", "TESE-IMPORTED-01"),
        ])
        self.assertEqual(payslip.move_id, self.move)
        self.assertEqual(payslip.pay_period, date(2026, 1, 1))
        self.assertEqual(
            payslip.employee_id.current_version_id,
            payslip.hr_version_id,
        )
        self.assertEqual(payslip.profile_id.hr_version_id, payslip.hr_version_id)
        self.assertEqual(payslip.state, "to_reconcile")
        self.assertEqual(len(payslip.component_line_ids), 11)
        self.assertEqual(payslip.attachment_id.mimetype, "application/pdf")
        self.assertEqual(self.move.tese_payslip_id, payslip)
        profiles = self.env["usl.tese.profile"].sudo().with_context(
            active_test=False,
        ).search([
            ("name", "in", ["Imported TESE profile", "Archived TESE profile"]),
        ])
        self.assertEqual(len(profiles), 2)
        self.assertEqual(len(profiles.filtered("active")), 1)
        self.assertEqual(
            len(profiles.filtered(lambda profile: not profile.active)),
            1,
        )

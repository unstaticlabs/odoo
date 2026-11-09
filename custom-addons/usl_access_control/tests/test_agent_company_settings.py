"""Native company settings remain usable without granting lock authority."""

from psycopg2.errors import UniqueViolation

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from ..controllers.json2 import UslAgentJson2Controller


JSON_MARKERS = (None, False, True, 0, 1, 2.5, "", "True", {}, {"authorized": True}, [])


LOCK_FIELDS = (
    "fiscalyear_lock_date", "hard_lock_date", "purchase_lock_date",
    "sale_lock_date", "tax_lock_date",
)


@tagged("post_install", "-at_install", "agent_company_settings")
class TestAgentCompanySettings(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "[QA] Settings Company B"})
        cls.foreign_company = cls.env["res.company"].create({"name": "[QA] Settings Outside Scope"})
        cls.settings = cls.env.ref("base.group_system")
        cls.accounting = cls.env.ref("account.group_account_manager")
        cls.owner = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "[QA] Company Settings Owner", "login": "qa.company.settings.owner",
            "email": "qa.company.settings.owner@example.invalid",
            "company_id": cls.company.id,
            "company_ids": [Command.set((cls.company | cls.other_company).ids)],
            "group_ids": [Command.set((cls.settings | cls.accounting).ids)],
        })
        cls.basic = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "[QA] Ordinary Company Reader", "login": "qa.company.settings.basic",
            "company_id": cls.company.id, "company_ids": [Command.set(cls.company.ids)],
            "group_ids": [Command.set(cls.env.ref("base.group_user").ids)],
        })
        cls.agent = cls.env["usl.agent"].with_user(cls.owner).create({
            "name": "[QA] Company Settings Agent", "purpose": "Native Settings parity QA.",
            "company_id": cls.company.id,
            "company_ids": [Command.set((cls.company | cls.other_company).ids)],
            "delegated_group_ids": [Command.set((cls.settings | cls.accounting).ids)],
            "read_only_group_ids": [Command.clear()], "access_mode": "read_write",
        })
        accounts = cls.env["account.account"].create([
            {"name": "[QA] Settings Expense", "code": "QAS625", "account_type": "expense", "company_ids": [Command.set(cls.company.ids)]},
            {"name": "[QA] Settings Offset", "code": "QAS467", "account_type": "liability_current", "company_ids": [Command.set(cls.company.ids)]},
        ])
        journal = cls.env["account.journal"].create({"name": "[QA] Settings Preservation", "code": "QASET", "type": "general", "company_id": cls.company.id})
        cls.move = cls.env["account.move"].create({
            "journal_id": journal.id, "date": "2026-01-15", "ref": "[QA] Invented preservation entry",
            "line_ids": [Command.create({"name": "[QA] Expense", "account_id": accounts[0].id, "debit": 17}), Command.create({"name": "[QA] Offset", "account_id": accounts[1].id, "credit": 17})],
        })
        cls.move.action_post()

    def _fingerprint(self):
        self.env.flush_all()
        result = {}
        for table in ("account_move", "account_move_line", "account_partial_reconcile", "account_full_reconcile", "account_tax", "account_analytic_line", "ir_attachment"):
            self.env.cr.execute(f'SELECT to_jsonb(t) - \'write_uid\' - \'write_date\' FROM "{table}" t ORDER BY id')
            result[table] = self.env.cr.fetchall()
        result["locks"] = self.company.read(list(LOCK_FIELDS))[0]
        return result

    def _agent_write(self, vals, *, ids=None, context=None):
        # Use the existing public identity validation/reconciliation before the
        # dispatch seam; actual bearer authentication is exercised over HTTP.
        self.env["usl.agent"].with_user(self.agent.user_id).current_identity()
        kwargs = {"vals": vals}
        access = UslAgentJson2Controller._check_agent_call(
            agent=self.agent, model_name="res.company", method_name="write", kwargs=kwargs,
        )
        call_context = UslAgentJson2Controller._agent_call_context(
            context={"allowed_company_ids": self.agent.company_ids.ids, **(context or {})},
            agent=self.agent, model_name="res.company", method_name="write", access=access,
        )
        return self.env["res.company"].with_user(self.agent.user_id).with_context(call_context).browse(self.company.ids if ids is None else ids).write(vals)

    def test_native_human_and_agent_update_ordinary_settings(self):
        before = self._fingerprint()
        self.company.with_user(self.owner).write({"name": "[QA] Human Updated Company"})
        self.assertTrue(self._agent_write({"name": "[QA] Agent Updated Company", "phone": "+1 202 555 0142"}))
        self.assertEqual(self.company.name, "[QA] Agent Updated Company")
        self.assertEqual(self.company.phone, "+1 202 555 0142")
        self.assertEqual(self.company.write_uid, self.agent.user_id)
        self.assertEqual(self._fingerprint(), before)
        events = self.env["usl.audit.event"].search([("agent_id", "=", self.agent.id), ("model_name", "=", "res.company"), ("operation", "=", "write")])
        self.assertTrue(events)
        with self.assertRaises(UserError):
            events.write({"operation": "read"})

    def test_forged_lock_marker_never_replaces_authority(self):
        before = self._fingerprint()
        for marker in JSON_MARKERS:
            for field in LOCK_FIELDS:
                with self.subTest(marker=marker, field=field), self.assertRaises(AccessError):
                    self.company.with_user(self.owner).with_context(usl_accounting_lock_guarded=marker).write({field: False})
        self.assertEqual(self._fingerprint(), before)

    def test_agent_scope_is_not_accounting_lock_authority(self):
        before = self._fingerprint()
        for marker in JSON_MARKERS:
            for field in LOCK_FIELDS:
                for value in (False, "2025-12-31"):
                    with self.subTest(marker=marker, field=field, value=value), self.assertRaises(AccessError):
                        self._agent_write({"name": "[QA] Must Roll Back", field: value}, context={"usl_accounting_lock_guarded": marker})
        self.assertNotEqual(self.company.name, "[QA] Must Roll Back")
        self.assertEqual(self._fingerprint(), before)

    def test_native_empty_record_and_empty_values_noops(self):
        before = self._fingerprint()
        self.assertTrue(self._agent_write({}))
        self.assertTrue(self._agent_write({"fiscalyear_lock_date": False}, ids=[]))
        self.assertEqual(self._fingerprint(), before)

    def test_native_user_without_company_write_remains_denied(self):
        with self.assertRaises(AccessError):
            self.company.with_user(self.basic).write({"name": "[QA] Must Not Change"})

    def test_readonly_and_missing_settings_delegation_remain_denied(self):
        before = self._fingerprint()
        for groups, readonly in ((self.settings | self.accounting, self.settings | self.accounting), (self.env.ref("base.group_user"), self.env["res.groups"])):
            self.agent.with_user(self.owner).write({"delegated_group_ids": [Command.set(groups.ids)], "read_only_group_ids": [Command.set(readonly.ids)]})
            with self.assertRaises(AccessError):
                self._agent_write({"name": "[QA] Must Not Change"})
        self.assertEqual(self._fingerprint(), before)

    def test_agent_foreign_company_context_cannot_expand_scope(self):
        before = self._fingerprint()
        with self.assertRaises(AccessError):
            self._agent_write({"name": "[QA] Must Not Change"}, ids=self.foreign_company.ids, context={"allowed_company_ids": self.foreign_company.ids})
        self.assertEqual(self._fingerprint(), before)

    def test_native_validation_rolls_back_company_metadata(self):
        before = self._fingerprint()
        original = self.company.name
        with self.assertRaises((ValidationError, UserError)) as error, self.env.cr.savepoint():
            self._agent_write({"name": "[QA] Invalid Parent", "parent_id": self.company.id})
        self.assertNotIsInstance(error.exception, AccessError)
        self.assertEqual(self.company.name, original)
        currency = self.env.ref("base.EUR") if self.company.currency_id != self.env.ref("base.EUR") else self.env.ref("base.USD")
        with self.assertRaises((ValidationError, UserError)) as error, self.env.cr.savepoint():
            self._agent_write({"name": "[QA] Invalid Currency", "currency_id": currency.id})
        self.assertNotIsInstance(error.exception, AccessError)
        self.assertEqual(self.company.name, original)
        self.assertEqual(self._fingerprint(), before)

    def test_other_allowed_company_and_repeated_settings_write(self):
        before = self._fingerprint()
        for _repeat in range(2):
            self.assertTrue(self._agent_write({"phone": "+1 202 555 0182"}, ids=self.other_company.ids))
        self.assertEqual(self.other_company.phone, "+1 202 555 0182")
        self.assertEqual(self._fingerprint(), before)

    def test_owner_loss_and_suspension_do_not_expand_native_authority(self):
        before = self._fingerprint()
        self.agent.with_user(self.owner).action_suspend()
        with self.assertRaises(AccessError):
            self._agent_write({"name": "[QA] Suspended Must Not Write"})
        self.agent.with_user(self.owner).action_reactivate()
        self.owner.write({"group_ids": [Command.unlink(self.settings.id)]})
        self.assertFalse(self.owner.has_group("base.group_system"))
        with self.assertRaises(AccessError):
            self._agent_write({"name": "[QA] Reduced Owner Must Not Write"})
        self.assertEqual(self._fingerprint(), before)

    def test_sudo_agent_and_ordinary_human_cannot_forge_lock_authority(self):
        before = self._fingerprint()
        for actor in (self.owner, self.agent.user_id):
            for field in LOCK_FIELDS:
                record = self.company.with_user(actor)
                if actor == self.agent.user_id:
                    record = record.sudo()
                with self.subTest(actor=actor.id, field=field), self.assertRaises(AccessError):
                    record.with_context(usl_accounting_lock_guarded=True).write({"phone": "+1 202 555 0199", field: False})
        self.assertEqual(self._fingerprint(), before)

    def test_company_creation_and_localization_are_still_protected(self):
        before = self._fingerprint()
        for user in (self.owner, self.agent.user_id):
            Company = self.env["res.company"].with_user(user)
            with self.subTest(user=user.id), self.assertRaises(AccessError):
                Company.create({"name": "[QA] Protected Creation Must Not Happen"})
            with self.subTest(user=user.id), self.assertRaises(AccessError):
                Company.browse(self.company.ids).install_l10n_modules()
        self.assertEqual(self._fingerprint(), before)

    def test_authorized_native_closing_preserves_review_and_audit(self):
        company = self.other_company
        closing = self.env["rebuild.account.closing.period"].with_company(company).create({
            "name": "[QA] Native Settings Closing", "company_id": company.id,
            "period_type": "month", "date_from": "2026-01-01", "date_to": "2026-01-31",
            "fiscalyear_start": "2026-01-01", "fiscalyear_end": "2026-12-31",
            "package_reference": "[QA] Invented closing package",
        })
        closing.action_refresh_controls()
        wizard = self.env["rebuild.account.report.export.wizard"].with_company(company).create({
            "company_id": company.id, "closing_period_id": closing.id,
            "report_type": "closing_package", "date_from": closing.date_from,
            "date_to": closing.date_to, "target_move": "posted", "export_format": "xlsx",
        })
        wizard.action_generate_export()
        decision = self.env["rebuild.account.assurance.decision"].create({
            "gate": "closing_review", "conclusion": "accepted", "required_authority": "accountant",
            "company_id": company.id, "closing_period_id": closing.id,
            "decision_summary": "[QA] The generated native package was reviewed.",
            "evidence_summary": "[QA] Disposable empty-company closing workbook.",
        })
        decision.action_record()
        self.assertTrue(closing.snapshot_ids)
        self.assertTrue(closing.control_line_ids)
        self.assertIn(closing.readiness_status, ("ready", "warning"))
        payloads = [(snapshot.id, snapshot.sha256, bytes(snapshot.payload)) for snapshot in closing.snapshot_ids]
        ordinary = closing.with_user(self.owner).with_context(allowed_company_ids=(self.company | company).ids)
        with self.assertRaises(AccessError):
            ordinary.action_close_and_apply_lock_dates()
        # The native payroll control requires the existing combined HR and
        # Accounting role. Grant it only to the authorized closing persona.
        self.owner.write({"group_ids": [
            Command.link(self.env.ref("usl_access_control.group_irreversible_actions").id),
            Command.link(self.env.ref("hr.group_hr_manager").id),
        ]})
        before_close = self._fingerprint()
        events_before = self.env["usl.audit.event"].search_count([("actor_id", "=", self.owner.id), ("action_key", "=", "guard:accounting.lock.change")])
        ordinary.action_close_and_apply_lock_dates()
        self.assertEqual(self._fingerprint(), before_close)
        self.assertEqual(closing.state, "closed")
        for field in LOCK_FIELDS:
            self.assertEqual(str(company[field]) if company[field] else False, False if field == "hard_lock_date" else "2026-01-31")
        self.assertEqual(closing.closed_by_id, self.owner)
        self.assertEqual(payloads, [(snapshot.id, snapshot.sha256, bytes(snapshot.payload)) for snapshot in closing.snapshot_ids])
        events = self.env["usl.audit.event"].search([("actor_id", "=", self.owner.id), ("action_key", "=", "guard:accounting.lock.change")])
        self.assertEqual(len(events), events_before + 1)
        self.assertTrue(events[-1].policy_digest)
        with self.assertRaises(UserError):
            events.write({"operation": "read"})

    def test_global_company_configuration_matches_native_human_authority(self):
        before = self._fingerprint()
        context = {"allowed_company_ids": (self.company | self.other_company).ids}
        self.foreign_company.with_user(self.owner).with_context(**context).write({"phone": "+1 202 555 0131"})
        self.assertTrue(self._agent_write({"phone": "+1 202 555 0132"}, ids=self.foreign_company.ids, context=context))
        self.assertEqual(self.foreign_company.phone, "+1 202 555 0132")
        names = (self.company.name, self.foreign_company.name)
        with self.assertRaises(UniqueViolation) as error, self.env.cr.savepoint(), mute_logger("odoo.sql_db"):
            self._agent_write({"name": "[QA] Duplicate Native Company Name"}, ids=(self.company | self.foreign_company).ids, context=context)
        self.assertEqual(error.exception.diag.constraint_name, "res_company_name_uniq")
        self.assertEqual((self.company.name, self.foreign_company.name), names)
        self.assertEqual(self._fingerprint(), before)

    def test_company_owned_business_records_keep_native_company_ceiling(self):
        journal = self.env["account.journal"].create({
            "name": "[QA] Outside Scope Journal", "code": "QAFOR",
            "type": "general", "company_id": self.foreign_company.id,
        })
        move = self.env["account.move"].create({"journal_id": journal.id, "date": "2026-01-15"})
        before = self._fingerprint()
        for user in (self.owner, self.agent.user_id):
            with self.subTest(user=user.id), self.assertRaises(AccessError):
                move.with_user(user).with_context(allowed_company_ids=(self.company | self.other_company).ids).read(["state"])
        self.assertEqual(self._fingerprint(), before)

"""Native grouping RPCs retain delegated scope and cannot persist a hidden write."""

import hashlib
import json
from unittest.mock import patch

from psycopg2 import errors as pgerrors

from odoo import SUPERUSER_ID, Command, api, fields
from odoo.tests import HttpCase, tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tools import SQL

from ..models.agent_credential import UslAgentKeyWizard


@tagged("post_install", "-at_install", "usl_native_grouping_reads")
class TestNativeGroupingReads(AccountTestInvoicingCommon, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "[QA grouping] Other company"})
        cls.owner = cls.env["res.users"].with_context(
            no_reset_password=True, usl_governed_identity_provisioning=True,
        ).create({
            "name": "[QA grouping] Owner", "login": "qa.grouping.owner",
            "email": "qa.grouping.owner@example.test",
            "company_id": cls.company.id,
            "company_ids": [Command.set((cls.company | cls.other_company).ids)],
            "group_ids": [Command.set([
                cls.env.ref("project.group_project_user").id,
                cls.env.ref("account.group_account_manager").id,
            ])],
            "usl_identity_classification": "active",
        })
        cls.agent = cls.env["usl.agent"].with_user(cls.owner).create({
            "name": "[QA grouping] Agent", "purpose": "Read native grouping sets",
            "company_id": cls.company.id,
            "company_ids": [Command.set(cls.company.ids)],
        })
        wizard = cls.env["usl.agent.key.wizard"].with_user(cls.owner).create({
            "agent_id": cls.agent.id, "name": "[QA grouping] HTTP",
        })
        cls.key = UslAgentKeyWizard.action_generate.__wrapped__(wizard)["context"]["default_key"]
        cls.project = cls.env["project.project"].sudo().create({
            "name": "[QA grouping] Visible", "company_id": cls.company.id,
            "privacy_visibility": "followers",
        })
        cls.project.message_subscribe(partner_ids=cls.owner.partner_id.ids)
        hidden = cls.env["project.project"].sudo().create({
            "name": "[QA grouping] Private", "company_id": cls.company.id,
            "privacy_visibility": "followers",
        })
        foreign = cls.env["project.project"].sudo().create({
            "name": "[QA grouping] Foreign", "company_id": cls.other_company.id,
            "privacy_visibility": "employees",
        })
        cls.tasks = cls.env["project.task"].sudo().create([
            {"name": f"[QA grouping] Task {index}", "project_id": project.id,
             "allocated_hours": hours}
            for index, (project, hours) in enumerate([
                (cls.project, 4), (cls.project, 7), (hidden, 100), (foreign, 200),
            ])
        ])
        cls.move = cls.env["account.move"].create({
            "move_type": "entry", "journal_id": cls.company_data["default_journal_misc"].id,
            "line_ids": [
                Command.create({"name": "[QA grouping] Debit", "account_id": cls.company_data["default_account_expense"].id, "debit": 11}),
                Command.create({"name": "[QA grouping] Credit", "account_id": cls.company_data["default_account_revenue"].id, "credit": 11}),
            ],
        })
        cls.move.action_post()
        cls.attachment = cls.env["ir.attachment"].create({
            "name": "[QA grouping] evidence.txt", "raw": b"Synthetic grouping evidence",
            "res_model": "account.move", "res_id": cls.move.id,
        })

    def _call(self, model="project.task", method="formatted_read_grouping_sets", **kwargs):
        return self.url_open(
            f"/json/2/{model}/{method}",
            data=json.dumps(kwargs),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json", "X-Odoo-Database": self.env.cr.dbname},
        )

    def _fingerprint(self):
        self.env.flush_all()
        result = {}
        for table in (
            "account_move", "account_move_line", "account_partial_reconcile",
            "account_full_reconcile", "ir_attachment", "mail_message", "project_task",
        ):
            self.env.cr.execute(SQL(
                "SELECT md5(COALESCE(string_agg(to_jsonb(t)::text, '' ORDER BY id), '')) FROM %s t",
                SQL.identifier(table),
            ))
            result[table] = self.env.cr.fetchone()[0]
        return hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()

    def test_authenticated_native_grouping_scope_repeat_and_nonmutation(self):
        before = self._fingerprint()
        kwargs = {"domain": [("id", "in", self.tasks.ids)], "grouping_sets": [[], ["project_id"]], "aggregates": ["__count", "allocated_hours:sum"]}
        first = self._call(**kwargs)
        self.assertEqual(first.status_code, 200, first.text)
        groups = first.json()
        self.assertEqual(groups[0][0]["__count"], 2)
        self.assertEqual(groups[0][0]["allocated_hours:sum"], 11)
        self.assertEqual(len(groups[1]), 1)
        self.assertEqual(groups[1][0]["project_id"][0], self.project.id)
        repeated = self._call(**kwargs)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json(), groups)
        empty = self._call(domain=[("id", "=", 0)], grouping_sets=[[], ["project_id"]], aggregates=["__count"])
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()[0][0]["__count"], 0)
        self.assertEqual(empty.json()[1], [])
        ledger = self._call(model="account.move.line", domain=[("move_id", "=", self.move.id)], grouping_sets=[[]], aggregates=["__count", "balance:sum"])
        self.assertEqual(ledger.status_code, 200, ledger.text)
        self.assertEqual(ledger.json()[0][0]["__count"], 2)
        self.assertEqual(ledger.json()[0][0]["balance:sum"], 0)
        self.assertEqual(before, self._fingerprint())

    def test_crafted_company_and_inherited_mutation_remain_denied(self):
        before = self._fingerprint()
        foreign = self._call(domain=[("id", "in", self.tasks.ids)], grouping_sets=[[]], aggregates=["__count"], context={"allowed_company_ids": self.other_company.ids})
        self.assertEqual(foreign.status_code, 403, foreign.text)
        task_id = self.tasks[0].id

        def mutating_grouping(records, *_args, **_kwargs):
            records.browse(task_id).write({"name": "Forbidden grouping write"})
            return [[]]

        with patch.object(type(self.env["project.task"]), "_read_grouping_sets", mutating_grouping):
            denied = self._call(domain=[("id", "=", task_id)], grouping_sets=[[]], aggregates=["__count"])
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(before, self._fingerprint())

    def test_existing_secret_denial_applies_to_native_grouping_specs(self):
        self.owner.with_user(SUPERUSER_ID).write({
            "group_ids": [Command.link(self.env.ref("base.group_system").id)],
        })
        self.agent.with_user(self.owner).action_grant_all_read()
        secret = "qa-grouping-synthetic-secret"
        server = self.env["ir.mail_server"].sudo().create({
            "name": "[QA grouping] Secret", "smtp_host": "smtp.example.invalid",
            "smtp_user": "qa@example.invalid", "smtp_pass": secret,
        })
        direct = self._call(model="ir.mail_server", method="read", ids=server.ids, fields=["smtp_pass"])
        self.assertEqual(direct.status_code, 403, direct.text)
        for method, spec in (
            ("formatted_read_grouping_sets", {"grouping_sets": [[]], "aggregates": ["smtp_pass:array_agg"]}),
            ("formatted_read_grouping_sets", {"grouping_sets": [["smtp_pass"]], "aggregates": ["__count"]}),
            ("formatted_read_group", {"groupby": [], "aggregates": ["smtp_pass:array_agg"]}),
        ):
            with self.subTest(method=method, spec=spec):
                response = self._call(model="ir.mail_server", method=method, domain=[("id", "=", server.id)], **spec)
                self.assertEqual(response.status_code, 403, response.text)
                self.assertNotIn(secret, response.text)
        public = self._call(model="ir.mail_server", domain=[("id", "=", server.id)], grouping_sets=[["smtp_host"]], aggregates=["__count"])
        self.assertEqual(public.status_code, 200, public.text)
        self.assertEqual(public.json()[0][0]["smtp_host"], "smtp.example.invalid")

    def test_many2many_grouping_preserves_sums_without_duplicate_rows(self):
        tags = self.env["project.tags"].sudo().create([
            {"name": "[QA grouping] Alpha"}, {"name": "[QA grouping] Beta"},
        ])
        self.tasks[0].write({"tag_ids": [Command.set(tags.ids)]})
        self.tasks[1].write({"tag_ids": [Command.set(tags[:1].ids)]})
        before = self._fingerprint()
        response = self._call(domain=[("id", "in", self.tasks.ids)], grouping_sets=[[], ["tag_ids"]], aggregates=["__count", "allocated_hours:sum"])
        self.assertEqual(response.status_code, 200, response.text)
        groups = response.json()
        self.assertEqual(groups[0][0]["allocated_hours:sum"], 11)
        self.assertEqual(groups[0][0]["__count"], 2)
        actual = {row["tag_ids"][0]: (row["__count"], row["allocated_hours:sum"]) for row in groups[1]}
        self.assertEqual(actual, {tags[0].id: (2, 11), tags[1].id: (1, 4)})
        self.assertEqual(before, self._fingerprint())

    def test_native_hr_skills_context_override_retains_hierarchy_behavior(self):
        self.owner.with_user(SUPERUSER_ID).write({
            "group_ids": [Command.link(self.env.ref("hr.group_hr_user").id)],
        })
        self.agent.with_user(self.owner).action_grant_all_read()
        parent = self.env["hr.department"].sudo().create({"name": "[QA grouping] Parent"})
        child = self.env["hr.department"].sudo().create({"name": "[QA grouping] Child", "parent_id": parent.id})
        employee = self.env["hr.employee"].sudo().create({
            "name": "[QA grouping] Employee", "user_id": self.owner.id,
            "company_id": self.company.id, "department_id": child.id,
        })
        skill_type = self.env["hr.skill.type"].sudo().create({"name": "[QA grouping] Skill type"})
        skill = self.env["hr.skill"].sudo().create({"name": "[QA grouping] Skill", "skill_type_id": skill_type.id})
        level = self.env["hr.skill.level"].sudo().create({"name": "[QA grouping] Level", "skill_type_id": skill_type.id, "level_progress": 50})
        self.env["hr.employee.skill"].sudo().create({"employee_id": employee.id, "skill_type_id": skill_type.id, "skill_id": skill.id, "skill_level_id": level.id})
        self.env.flush_all()
        self.assertEqual(self.env["hr.employee.skill.report"].sudo().search_count([("employee_id", "=", employee.id)]), 1)
        self.assertEqual(self.env["hr.employee.skill.report"].with_user(self.owner).search_count([("employee_id", "=", employee.id)]), 1)
        response = self._call(model="hr.employee.skill.report", domain=[("employee_id", "=", employee.id)], grouping_sets=[["department_id"]], aggregates=["__count"])
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()[0][0]
        self.assertEqual(row["__count"], 1)
        self.assertEqual(row["department_id"], [child.id, child.name])

    def test_current_owner_private_scope_is_rechecked_for_grouping(self):
        before = self._call(domain=[("id", "in", self.tasks.ids)], grouping_sets=[[]], aggregates=["__count"])
        self.assertEqual(before.status_code, 200, before.text)
        self.assertEqual(before.json()[0][0]["__count"], 2)
        self.project.message_unsubscribe(partner_ids=self.owner.partner_id.ids)
        after = self._call(domain=[("id", "in", self.tasks.ids)], grouping_sets=[[]], aggregates=["__count"])
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(after.json()[0][0]["__count"], 0)

    def test_readonly_native_auth_defers_usage_telemetry_until_commit(self):
        agent = self.agent
        credential = agent.credential_ids
        now = fields.Datetime.now()
        callbacks = []
        with self.env.registry.cursor(readonly=True) as cursor:
            readonly_credential = api.Environment(cursor, SUPERUSER_ID, {})[
                "usl.agent.credential"
            ].browse(credential.id)
            with patch.object(type(cursor.postcommit), "add", lambda _callbacks, callback: callbacks.append(callback)):
                self.assertFalse(readonly_credential._touch_last_used_at(now))
        credential.invalidate_recordset(["last_used_at"])
        self.assertFalse(credential.last_used_at)
        self.assertEqual(len(callbacks), 1)
        updates = []
        touch = type(credential)._touch_last_used_at

        def observed_touch(records, used_at):
            result = touch(records, used_at)
            updates.append((records.env.cr.readonly, records.last_used_at))
            return result

        with patch.object(type(credential), "_touch_last_used_at", observed_touch):
            callbacks[0]()
        self.assertEqual(updates, [(False, now)])
        with patch.object(type(credential), "_touch_last_used_at", side_effect=pgerrors.OperationalError):
            with self.assertLogs("odoo.addons.usl_access_control.models.agent_credential", level="ERROR"):
                callbacks[0]()

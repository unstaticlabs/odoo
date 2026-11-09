"""Native owner rules keep their identity through SQL and record access paths."""

import itertools
import json
import logging
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.fields import Domain
from odoo.tests import HttpCase, tagged
from odoo.tools import SQL

from ..controllers.json2 import UslAgentJson2Controller
from ..models.agent_credential import UslAgentKeyWizard


_logger = logging.getLogger(__name__)


@tagged("post_install", "-at_install", "usl_delegated_owner_domains")
class TestDelegatedOwnerDomains(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "[QA owner domains] Other"})
        cls.owner = cls.env["res.users"].with_context(
            no_reset_password=True, usl_governed_identity_provisioning=True,
        ).create({
            "name": "[QA owner domains] Owner", "login": "qa.owner.domains",
            "email": "qa.owner.domains@example.test", "company_id": cls.company.id,
            "company_ids": [Command.set((cls.company | cls.other_company).ids)],
            "group_ids": [Command.set(cls.env.ref("project.group_project_user").ids)],
            "usl_identity_classification": "active",
        })
        cls.agent = cls.env["usl.agent"].with_user(cls.owner).create({
            "name": "[QA owner domains] Reader", "purpose": "Read on native owner authority",
            "company_id": cls.company.id, "company_ids": [Command.set(cls.company.ids)],
        })
        cls.agent_user = cls.agent.user_id
        cls.department = cls.env["hr.department"].create({"name": "[QA owner domains] Parent"})
        cls.child = cls.env["hr.department"].create({
            "name": "[QA owner domains] Child", "parent_id": cls.department.id,
        })
        cls.outside_department = cls.env["hr.department"].create({"name": "[QA owner domains] Outside"})
        cls.employees = cls.env["hr.employee"].create([
            {"name": "[QA owner domains] Personal", "user_id": cls.owner.id,
             "company_id": cls.company.id, "department_id": cls.department.id},
            {"name": "[QA owner domains] Subordinate", "company_id": cls.company.id,
             "department_id": cls.child.id},
            {"name": "[QA owner domains] Outside", "company_id": cls.company.id,
             "department_id": cls.outside_department.id},
        ])
        cls.skill_type = cls.env["hr.skill.type"].create({"name": "[QA owner domains] Type"})
        cls.skill = cls.env["hr.skill"].create({"name": "[QA owner domains] Skill", "skill_type_id": cls.skill_type.id})
        cls.level = cls.env["hr.skill.level"].create({
            "name": "[QA owner domains] Level", "skill_type_id": cls.skill_type.id, "level_progress": 50,
        })
        cls._add_skills(cls.employees)
        cls.project = cls.env["project.project"].create({
            "name": "[QA owner domains] Visible", "privacy_visibility": "followers",
            "company_id": cls.company.id,
        })
        cls.hidden_project = cls.env["project.project"].create({
            "name": "[QA owner domains] Private", "privacy_visibility": "followers",
            "company_id": cls.company.id,
        })
        cls.project.message_subscribe(partner_ids=cls.owner.partner_id.ids)
        cls.tasks = cls.env["project.task"].create([
            {"name": f"[QA owner domains] Task {i}", "project_id": cls.project.id}
            for i in range(12)
        ])
        cls.hidden_task = cls.env["project.task"].create({
            "name": "[QA owner domains] Private task", "project_id": cls.hidden_project.id,
        })
        cls.env.flush_all()

    @classmethod
    def _add_skills(cls, employees):
        return cls.env["hr.employee.skill"].create([
            {"employee_id": employee.id, "skill_type_id": cls.skill_type.id,
             "skill_id": cls.skill.id, "skill_level_id": cls.level.id}
            for employee in employees
        ])

    def _report(self, user=None, companies=None):
        return self.env["hr.employee.skill.report"].with_user(user or self.agent_user).with_context(
            allowed_company_ids=(companies or self.company).ids,
        )

    def _counts(self, model, employees=None):
        domain = [("employee_id", "in", (employees or self.employees).ids)]
        groups = model.formatted_read_grouping_sets(domain, [[]], ["__count"])
        return model.search_count(domain), groups[0][0]["__count"]

    def _writer(self):
        agent = self.env["usl.agent"].with_user(self.owner).create({
            "name": "[QA owner domains] Writer", "purpose": "Native operation tests",
            "company_id": self.company.id, "company_ids": [Command.set(self.company.ids)],
            "access_mode": "read_write",
            "delegated_group_ids": [Command.set(self.env.ref("project.group_project_user").ids)],
        })
        return agent, agent.user_id

    def _scope(self, agent, operation):
        access = UslAgentJson2Controller._check_agent_call(
            agent=agent, model_name="project.task", method_name=operation, kwargs={},
        )
        return UslAgentJson2Controller._agent_call_context(
            context={}, agent=agent, model_name="project.task", method_name=operation, access=access,
        )

    def _key(self):
        wizard = self.env["usl.agent.key.wizard"].with_user(self.owner).create({
            "agent_id": self.agent.id, "name": "[QA owner domains] Fresh HTTP",
        })
        return UslAgentKeyWizard.action_generate.__wrapped__(wizard)["context"]["default_key"]

    def _call(self, key, method, **kwargs):
        return self.url_open(
            f"/json/2/hr.employee.skill.report/{method}", data=json.dumps(kwargs),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "X-Odoo-Database": self.env.cr.dbname},
        )

    def test_personal_native_search_group_and_record_access(self):
        self.assertFalse(self.owner.has_group("hr.group_hr_user"))
        self.assertEqual(self._counts(self._report(self.owner)), (1, 1))
        self.assertEqual(self._counts(self._report()), (1, 1))
        personal = self._report(self.owner).search([("employee_id", "=", self.employees[0].id)])
        self.assertTrue(personal.with_user(self.agent_user).has_access("read"))
        self.assertEqual(personal.with_user(self.agent_user).read(["employee_id"])[0]["employee_id"][0], self.employees[0].id)
        self.assertEqual(self._counts(self._report()), (1, 1))
        self.assertEqual(self._report().search_count([("id", "=", 0)]), 0)

    def test_native_manager_hierarchy_and_current_reduction(self):
        self.department.manager_id = self.employees[0]
        self.env.flush_all()
        self.assertEqual(self._counts(self._report(self.owner)), (2, 2))
        self.assertEqual(self._counts(self._report()), (2, 2))
        self.department.manager_id = False
        self.env.flush_all()
        self.assertEqual(self._counts(self._report()), (1, 1))

    def test_nested_computed_rule_and_explicit_caller_identity(self):
        self.employees[1].parent_id = self.employees[0]
        self.env.flush_all()

        def nested_rule(records, operator, value):
            return Domain("employee_id.user_id", "=", records.env.uid) | Domain(
                "employee_id", "any", Domain("is_subordinate", "=", True),
            )

        # Test-only outer adaptation around the real native subordinate search.
        with patch.object(type(self._report()), "_search_has_department_manager_access", nested_rule):
            self.assertEqual(self._counts(self._report(self.owner)), (2, 2))
            self.assertEqual(self._counts(self._report()), (2, 2))
        # Explicit caller predicates retain their own native identity contract.
        self.department.manager_id = self.employees[0]
        self.env.flush_all()
        explicit = [("employee_id.is_subordinate", "=", True)]
        self.assertEqual(self._report(self.owner).search_count(explicit), 1)
        self.assertEqual(self._report().search_count(explicit), 0)

    def test_separate_native_hr_user_read_all(self):
        self.owner.group_ids = [Command.link(self.env.ref("hr.group_hr_user").id)]
        self.agent.with_user(self.owner).action_grant_all_read()
        self.assertEqual(self._counts(self._report(self.owner)), (3, 3))
        self.assertEqual(self._counts(self._report()), (3, 3))

    def test_company_subsets_and_current_owner_reduction(self):
        foreign = self.env["hr.employee"].create({
            "name": "[QA owner domains] Foreign personal", "user_id": self.owner.id,
            "company_id": self.other_company.id,
        })
        self._add_skills(foreign)
        employees = self.employees | foreign
        self.agent.with_user(self.owner).write({"company_ids": [Command.set((self.company | self.other_company).ids)]})
        self.env.flush_all()
        # Generate all nonempty selected-company subsets: native human result is
        # an independent oracle; singleton expected counts prevent vacuity.
        companies = self.company | self.other_company
        for size in (1, 2):
            for selected in itertools.combinations(companies.ids, size):
                with self.subTest(selected=selected):
                    scope = companies.browse(selected)
                    expected = len(selected)
                    self.assertEqual(self._counts(self._report(self.owner, scope), employees), (expected, expected))
                    self.assertEqual(self._counts(self._report(companies=scope), employees), (expected, expected))
        self.owner.company_ids = [Command.set(self.company.ids)]
        self.assertEqual(self._counts(self._report(companies=companies), employees), (1, 1))
        with self.assertRaises(AccessError):
            self._report(companies=self.other_company).search_count([])

    def test_generated_small_hierarchy_owner_ceiling(self):
        # Generate eight small valid forests. Visibility follows ancestry from
        # the managed root; this oracle does not call the rule being tested.
        for edges in itertools.product((False, True), repeat=3):
            parent = self.department
            descendants = []
            reachable = True
            for index, linked in enumerate(edges):
                if not linked:
                    parent = self.outside_department
                    reachable = False
                parent = self.env["hr.department"].create({
                    "name": f"[QA owner domains] Generated {index}", "parent_id": parent.id,
                })
                employee = self.env["hr.employee"].create({
                    "name": f"[QA owner domains] Generated {index}",
                    "company_id": self.company.id, "department_id": parent.id,
                })
                self._add_skills(employee)
                descendants.append((employee, reachable))
            employees = self.employees | self.env["hr.employee"].concat(*(e for e, _ in descendants))
            for manager in (False, True):
                self.department.manager_id = self.employees[0] if manager else False
                self.env.flush_all()
                expected = 1 + (1 + sum(reachable for _, reachable in descendants) if manager else 0)
                with self.subTest(edges=edges, manager=manager):
                    self.assertEqual(self._counts(self._report(self.owner), employees), (expected, expected))
                    self.assertEqual(self._counts(self._report(), employees), (expected, expected))

    def test_private_scope_field_authority_and_readonly_denial(self):
        model = self.env["project.task"].with_user(self.agent_user)
        domain = [("id", "in", (self.tasks | self.hidden_task).ids)]
        self.assertEqual(model.search(domain), self.tasks)
        with self.assertRaises(AccessError):
            self.hidden_task.with_user(self.agent_user).read(["name"])
        with self.assertRaises(AccessError):
            self.tasks.with_user(self.agent_user).write({"name": "Forbidden"})
        field = self.employees._fields["private_email"]
        self.assertFalse(self.employees.with_user(self.owner)._has_field_access(field, "read"))
        self.assertFalse(self.employees.with_user(self.agent_user)._has_field_access(field, "read"))
        with self.assertRaises(AccessError):
            self.employees[:1].with_user(self.agent_user).read(["private_email"])
        self.project.message_unsubscribe(partner_ids=self.owner.partner_id.ids)
        self.assertFalse(model.search(domain))

    def test_native_create_write_unlink_relations_and_owner_acl_loss(self):
        writer, user = self._writer()
        Task = self.env["project.task"].with_user(user)
        created = Task.with_context(self._scope(writer, "create")).create({
            "name": "[QA owner domains] Created", "project_id": self.project.id,
        })
        context = self._scope(writer, "write")
        self.tasks.with_user(user).with_context(context).write({"description": "[QA owner domains] Updated"})
        self.assertTrue(all("Updated" in task.description for task in self.tasks))
        created.with_context(context).write({"parent_id": self.tasks[0].id})
        self.assertEqual(created.sudo().parent_id, self.tasks[0])
        with self.assertRaises(AccessError):
            self.hidden_task.with_user(user).with_context(context).write({"description": "Forbidden"})
        # Task deletion already has an independent protected-action boundary.
        self.assertTrue(created.with_user(self.owner).has_access("unlink"))
        with self.assertRaises(AccessError):
            self._scope(writer, "unlink")
        with self.assertRaises(AccessError):
            created.unlink()
        self.assertTrue(created.sudo().exists())
        created.with_env(self.env).unlink()
        self.assertFalse(created.sudo().exists())
        self.owner.group_ids = [Command.set(self.env.ref("base.group_user").ids)]
        self.assertFalse(self.tasks.with_user(self.owner).has_access("write"))
        self.assertFalse(self.tasks.with_user(user).with_context(context).has_access("write"))

    def test_empty_record_native_access_operator_and_true_false(self):
        Rules = type(self.env["ir.rule"])
        original = Rules._compute_domain

        def rule(records, model_name, mode="read"):
            if model_name == "hr.employee.skill.report" and records.env.uid == self.owner.id:
                return Domain("employee_id", "access", "write")
            return original(records, model_name, mode)

        with patch.object(Rules, "_compute_domain", rule):
            self.env.invalidate_all()
            self.assertFalse(self._report(self.owner).has_access("read"))
            self.assertFalse(self._report().has_access("read"))
        self.assertFalse(self.env["ir.config_parameter"].with_user(self.agent_user).has_access("read"))
        self.assertTrue(self.env["project.task"].with_user(self.agent_user).has_access("read"))

    def test_bounded_native_mail_context(self):
        from odoo.addons.mail.models import mail_activity, mail_scheduled_message

        self.env["mail.activity"].with_context(mail_activity_quick_update=True).create([
            {"res_model_id": self.env["ir.model"]._get("project.task").id,
             "res_id": self.tasks[0].id, "user_id": self.owner.id,
             "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
             "summary": "[QA owner domains] Activity"}
            for _ in range(2)
        ])
        domain = [("res_model", "=", "project.task"), ("res_id", "=", self.tasks[0].id)]
        with patch.object(mail_activity, "MAX_SEARCH_LIMIT", 2):
            self.assertEqual(self.env["mail.activity"].with_user(self.owner).search_count(domain), 2)
            self.assertEqual(self.env["mail.activity"].with_user(self.agent_user).search_count(domain), 2)
        message = self.tasks[0].message_post(body="[QA owner domains] Message")
        self.assertEqual(self.env["mail.message"].with_user(self.agent_user).search([("id", "=", message.id)]), message)
        scheduled = self.env["mail.scheduled.message"].create([
            {"model": "project.task", "res_id": task.id,
             "body": "[QA owner domains] Scheduled", "author_id": self.owner.partner_id.id,
             "scheduled_date": "2099-01-01 12:00:00"}
            for task in (self.tasks[0], self.tasks[0], self.tasks[1])
        ])
        domain = [("model", "=", "project.task"), ("res_id", "=", self.tasks[0].id)]
        with patch.object(mail_scheduled_message, "MAX_SEARCH_LIMIT", 3):
            self.assertEqual(self.env["mail.scheduled.message"].with_user(self.owner).search_count(domain), 2)
            self.assertEqual(self.env["mail.scheduled.message"].with_user(self.agent_user).search_count(domain), 2)
        self.assertEqual(len(scheduled), 3)

    def test_record_access_batching(self):
        writer, user = self._writer()
        contexts = ((self.agent_user, "read", {}), (user, "write", self._scope(writer, "write")))
        for actor, operation, context in contexts:
            records = self.tasks.with_user(actor).with_context(context)
            self.env.invalidate_all()
            records[:1].has_access(operation)
            self.env._access_cache.clear()
            start = self.cr.sql_log_count
            self.assertTrue(records[:2].has_access(operation))
            small = self.cr.sql_log_count - start
            self.env._access_cache.clear()
            start = self.cr.sql_log_count
            self.assertTrue(records.has_access(operation))
            large = self.cr.sql_log_count - start
            _logger.info("Native owner %s batching: 2 records=%s queries, 12 records=%s queries", operation, small, large)
            with self.subTest(operation=operation):
                self.assertLessEqual(large, small + 3, (small, large))

    def _business_fingerprints(self):
        self.env.flush_all()
        result = {}
        for table in ("account_move", "account_move_line", "account_partial_reconcile",
                      "account_full_reconcile", "ir_attachment", "mail_message",
                      "project_task", "hr_employee", "hr_employee_skill", "hr_department"):
            self.cr.execute(SQL(
                "SELECT md5(COALESCE(string_agg(to_jsonb(t)::text, '' ORDER BY id), '')) FROM %s t",
                SQL.identifier(table),
            ))
            result[table] = self.cr.fetchone()[0]
        return result

    def test_fresh_key_native_http_personal_search_and_group(self):
        key = self._key()
        domain = [("employee_id", "in", self.employees.ids)]
        before = self._business_fingerprints()
        response = self._call(key, "search_count", domain=domain)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), 1)
        groups = self._call(key, "formatted_read_grouping_sets", domain=domain, grouping_sets=[[]], aggregates=["__count"])
        self.assertEqual(groups.status_code, 200, groups.text)
        self.assertEqual(groups.json()[0][0]["__count"], 1)
        forged = self._call(key, "search_count", domain=domain, context={"allowed_company_ids": self.other_company.ids})
        self.assertEqual(forged.status_code, 403, forged.text)
        denied = self._call(key, "write", ids=[], vals={"name": "Forbidden"})
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(self._business_fingerprints(), before)

    def test_native_notification_render_keeps_authorship_and_owner_ceiling(self):
        writer, user = self._writer()
        recipient = self.env["res.partner"].create({
            "name": "[QA owner domains] Recipient", "email": "recipient.owner.domains@example.test",
        })
        task = self.tasks[0]
        notification = task.with_user(user).message_notify(
            body="[QA owner domains] Agent notification", partner_ids=recipient.ids, force_send=False,
        )
        stored = notification.with_env(self.env)
        self.assertEqual(stored.author_id, user.partner_id)
        self.assertEqual(stored.create_uid, user)
        self.assertEqual(stored.partner_ids, recipient)
        self.assertEqual(stored.message_type, "user_notification")
        self.assertEqual((stored.model, stored.res_id), (task._name, task.id))
        mail = self.env["mail.mail"].search([("mail_message_id", "=", stored.id)])
        self.assertTrue(mail)
        self.assertIn("Agent notification", mail.body_html)
        # Native author/creator access includes the actor's own notification;
        # document access alone does not expose another actor's notification.
        self.env._access_cache.clear()
        ordinary = self.env["mail.message"].with_user(user).browse(stored.id)
        self.assertTrue(ordinary.has_access("read"))
        self.assertIn("Agent notification", ordinary.read(["body"])[0]["body"])
        private = self.hidden_task.message_post(body="[QA owner domains] Unrelated private")
        with self.assertRaises(AccessError):
            self.env["mail.message"].with_user(user).browse(private.id).read(["body"])
        human = task.with_user(self.owner).message_notify(
            body="[QA owner domains] Human notification", partner_ids=recipient.ids, force_send=False,
        )
        self.assertEqual(human.author_id, self.owner.partner_id)
        self.assertEqual(human.create_uid, self.owner)
        self.assertIn("Human notification", human.body)
        self.assertEqual(human.partner_ids, recipient)
        with self.assertRaises(AccessError):
            human.with_user(user).read(["body"])
        self.assertIn("Human notification", self.env["mail.mail"].search([
            ("mail_message_id", "=", human.id),
        ]).body_html)

    def test_notification_render_boundary_context_and_values(self):
        from odoo.addons.mail.models.mail_thread import MailThread as NativeMailThread
        from ..models.agent_policy_tokens import AGENT_COLLABORATION_CONTEXT_KEY

        writer, user = self._writer()
        records = self.tasks[0].with_user(user)
        scoped = records._usl_agent_collaboration(operation="write")
        message = self.tasks[0].message_post(body="[QA owner domains] Render").with_user(user)
        supplied = {"message": message, "record": records, "lang": "fr_FR"}
        snapshots = []

        def native_render(record, msg, recipients_group, msg_vals=False, render_values=None):
            snapshots.append((record, msg, recipients_group, msg_vals, render_values))
            return "native rendered layout"

        recipients = {"notification_group_name": "customer"}
        msg_vals = {"email_layout_xmlid": "mail.mail_notification_light"}
        with patch.object(NativeMailThread, "_notify_by_email_render_layout", native_render):
            self.assertEqual(scoped._notify_by_email_render_layout(
                message, recipients, msg_vals=msg_vals, render_values=supplied,
            ), "native rendered layout")
            record, rendered, actual_recipients, actual_vals, values = snapshots[-1]
            self.assertFalse(record.env.su)
            self.assertTrue(rendered.env.su)
            self.assertEqual(rendered.env.uid, user.id)
            self.assertIs(values["message"], rendered)
            self.assertIs(values["record"], records)
            self.assertEqual(values["lang"], "fr_FR")
            self.assertIs(actual_recipients, recipients)
            self.assertIs(actual_vals, msg_vals)
            self.assertIs(supplied["message"], message)
            self.assertFalse(message.env.su)
            scoped._notify_by_email_render_layout(message, recipients)
            self.assertTrue(snapshots[-1][-1]["message"].env.su)
            # A serializable marker cannot enable the private technical scope.
            forged = records.with_context(**{AGENT_COLLABORATION_CONTEXT_KEY: True})
            forged._notify_by_email_render_layout(message, recipients, render_values=supplied)
            self.assertIs(snapshots[-1][1], message)
            self.assertIs(snapshots[-1][-1], supplied)
            records.with_user(self.owner)._notify_by_email_render_layout(
                message, recipients, render_values=supplied,
            )
            self.assertIs(snapshots[-1][1], message)
            self.assertIs(snapshots[-1][-1], supplied)

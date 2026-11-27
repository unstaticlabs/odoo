"""Exact native structured reads keep the ordinary delegated data boundaries."""

import json
from unittest.mock import patch

from odoo import Command
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tools import SQL

from ..models.agent_credential import UslAgentKeyWizard


@tagged("post_install", "-at_install", "usl_native_structured_reads")
class TestNativeStructuredReads(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a = cls.env["res.company"].create({"name": "Atelier Ambre"})
        cls.b = cls.env["res.company"].create({"name": "Atelier Corail"})
        cls.c = cls.env["res.company"].create({"name": "Atelier Nacre"})
        cls.groups = cls.env.ref("project.group_project_user") | cls.env.ref("base.group_partner_manager")
        cls.owner = new_test_user(cls.env, login="qa.native.structured.owner", groups="project.group_project_user,base.group_partner_manager",
            company_id=cls.a.id, company_ids=[Command.set((cls.a | cls.b | cls.c).ids)], usl_identity_classification="active")
        cls.reader = cls._agent("Lecteur", cls.groups, "read_only")
        cls.writer = cls._agent("Collaborateur", cls.groups, "read_write")
        cls.contact_reader = cls._agent("Contacts", cls.env.ref("base.group_partner_manager"), "read_only")
        cls.keys = {}
        for agent in (cls.reader, cls.writer, cls.contact_reader):
            wizard = cls.env["usl.agent.key.wizard"].with_user(cls.owner).create({"agent_id": agent.id, "name": "Synthetic HTTP"})
            cls.keys[agent.id] = UslAgentKeyWizard.action_generate.__wrapped__(wizard)["context"]["default_key"]
        cls.projects = cls.env["project.project"].create([
            {"name": "Carnet ambre", "company_id": cls.a.id, "privacy_visibility": "followers"},
            {"name": "Carnet corail", "company_id": cls.b.id, "privacy_visibility": "followers"},
            {"name": "Carnet privé", "company_id": cls.a.id, "privacy_visibility": "followers"},
            {"name": "Carnet nacre", "company_id": cls.c.id, "privacy_visibility": "employees"},
        ])
        for project in cls.projects[:2]:
            project.message_subscribe(partner_ids=cls.owner.partner_id.ids)
        cls.tasks = cls.env["project.task"].create([
            {"name": project.name, "project_id": project.id} for project in cls.projects
        ])
        cls.contacts = cls.env["res.partner"].create([
            {"name": company.name, "company_id": company.id} for company in (cls.a, cls.b, cls.c)
        ])

    @classmethod
    def _agent(cls, name, groups, mode):
        return cls.env["usl.agent"].with_user(cls.owner).create({
            "name": name, "purpose": "Synthetic structured read qualification",
            "company_id": cls.a.id, "company_ids": [Command.set((cls.a | cls.b).ids)],
            "access_mode": mode, "delegated_group_ids": [Command.set(groups.ids)],
        })

    def _call(self, model, *, agent=None, method="web_search_read", **kwargs):
        return self.url_open(f"/json/2/{model}/{method}", data=json.dumps(kwargs), headers={
            "Authorization": "Bearer " + self.keys[(agent or self.reader).id],
            "Content-Type": "application/json", "X-Odoo-Database": self.env.cr.dbname,
        })

    def _read(self, records, company, *, agent=None, specification=None, **kwargs):
        return self._call(records._name, agent=agent, domain=[("id", "in", records.ids)],
            specification=specification or {"id": {}, "name": {}, "company_id": {"fields": {"id": {}, "name": {}}}},
            order="id", context={"allowed_company_ids": company.ids}, **kwargs)

    def _snapshot(self):
        self.env.flush_all()
        result = {}
        for table in ("project_project", "project_task", "res_partner", "mail_message", "ir_attachment", "res_company"):
            self.env.cr.execute(SQL("SELECT count(*), md5(coalesce(string_agg(to_jsonb(t)::text, '' ORDER BY id), '')) FROM %s t", SQL.identifier(table)))
            result[table] = self.env.cr.fetchone()
        return result

    def test_ro_rw_native_reads_have_exact_company_record_scope_and_no_writes(self):
        before = self._snapshot()
        for agent in (self.reader, self.writer):
            for records, first, second in ((self.tasks, self.tasks[0], self.tasks[1]),
                    (self.projects, self.projects[0], self.projects[1]),
                    (self.contacts, self.contacts[0], self.contacts[1])):
                for company, expected in ((self.a, first), (self.b, second), (self.a | self.b, first | second)):
                    with self.subTest(agent=agent.name, model=records._name, companies=company.ids):
                        response = self._read(records, company, agent=agent)
                        self.assertEqual(response.status_code, 200, response.text)
                        body = response.json()
                        self.assertEqual([row["id"] for row in body["records"]], expected.sorted("id").ids)
                        self.assertEqual(body["length"], len(expected))
        self.assertEqual(self._snapshot(), before)

    def test_existing_ordinary_reads_keep_their_authorized_population(self):
        for records, expected in ((self.tasks, self.tasks[:2]), (self.projects, self.projects[:2]), (self.contacts, self.contacts[:2])):
            response = self._call(records._name, method="search_read", domain=[("id", "in", records.ids)],
                fields=["id", "name"], order="id", context={"allowed_company_ids": (self.a | self.b).ids})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([row["id"] for row in response.json()], expected.ids)

    def test_native_pages_and_empty_result_keep_their_contract(self):
        for records in (self.tasks[:2], self.projects[:2], self.contacts[:2]):
            first = self._read(records, self.a | self.b, limit=1)
            self.assertEqual(first.status_code, 200, first.text)
            second = self._read(records, self.a | self.b, limit=1, offset=1)
            self.assertEqual(second.status_code, 200, second.text)
            self.assertEqual([x["id"] for x in first.json()["records"] + second.json()["records"]], records.ids)
            empty = self._read(records.browse(), self.a)
            self.assertEqual(empty.status_code, 200, empty.text)
            self.assertEqual(empty.json(), {"records": [], "length": 0})

    def test_unselected_company_and_missing_application_stay_denied(self):
        before = self._snapshot()
        for records in (self.tasks, self.projects, self.contacts):
            response = self._read(records, self.c)
            self.assertEqual(response.status_code, 403, response.text)
        denied = self._read(self.tasks, self.a, agent=self.contact_reader)
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(self._snapshot(), before)

    def test_native_hidden_field_stays_unreadable(self):
        with patch.object(self.env["res.partner"]._fields["name"], "groups", "base.group_system"):
            self.assertFalse(self.contacts.with_user(self.reader.user_id).has_field_access(self.contacts._fields["name"], "read"))
            response = self._read(self.contacts[:1], self.a)
            self.assertEqual(response.status_code, 403, response.text)
            self.assertNotIn("Atelier Ambre", response.text)

    def test_legitimate_read_cannot_persist_a_mutating_delegate(self):
        before = self._snapshot()
        task_id = self.tasks[0].id
        invoked = []
        def mutating_web_read(records, *_args, **_kwargs):
            invoked.append(True)
            records.browse(task_id).write({"name": "Forbidden synthetic change"})
            return []
        with patch.object(type(self.env["project.task"]), "web_read", mutating_web_read):
            response = self._read(self.tasks[:1], self.a)
        self.assertEqual(response.status_code, 403, response.text)
        self.assertTrue(invoked, "The permitted read must reach its native delegate before the existing mutation guard refuses it")
        self.assertEqual(self._snapshot(), before)

    def test_business_mutations_remain_denied_without_partial_effects(self):
        before = self._snapshot()
        for records in (self.tasks[:1], self.projects[:1], self.contacts[:1]):
            response = self._call(records._name, method="write", ids=records.ids,
                vals={"name": "Forbidden synthetic change"}, context={"allowed_company_ids": self.a.ids})
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self._snapshot(), before)

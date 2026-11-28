"""One-hop search evidence uses current native task and source authority."""

from unittest.mock import patch

from markupsafe import Markup

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.exceptions import AccessError, UserError


@tagged("post_install", "-at_install", "usl_search_relations")
class TestTaskRelationSearch(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "[QA] Indigo workshop"})
        cls.actor = new_test_user(cls.env, login="qa-search-relations", groups="project.group_project_user",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.env = cls.env(context={**cls.env.context, "allowed_company_ids": cls.company.ids})
        cls.project = cls.env["project.project"].create({"name": "[QA] Parcel workshop", "company_id": cls.company.id})
        cls.Task = cls.env["project.task"].with_user(cls.actor)
        cls.service = cls.env["usl.home.service"].with_user(cls.actor)

    def _task(self, name="[QA] Prepare parcel", **values):
        return self.env["project.task"].create({"name": name, "project_id": self.project.id,
            "user_ids": [Command.link(self.actor.id)], **values})

    def _index(self):
        self.env["project.task"]._cron_usl_index_task_content()
        self.env["project.project"]._cron_usl_index_content()

    def _ids(self, query):
        return self.Task.search([("usl_search_text", "ilike", query)]).ids

    def test_project_description_retrieves_task_and_explains_the_source(self):
        self.project.description = "<p>Fit the indigo heliograph.</p>"
        child = self._task()
        self._index()
        self.assertIn(child.id, self._ids("heliograph indigo"))
        rows = self.service.search_records("heliograph indigo", categories=["tasks"])["groups"][0]["records"]
        row = next(row for row in rows if row["id"] == child.id)
        self.assertEqual(row["source"]["kind"], "relation")
        self.assertEqual((row["source"]["model"], row["source"]["id"]), ("project.project", self.project.id))
        self.assertIn("indigo heliograph", row["source"]["excerpt"])

    def test_parent_description_retrieves_only_its_immediate_child(self):
        parent = self._task("[QA] First plan", description="<p>Repair the copper sextant.</p>")
        child = self._task(parent_id=parent.id)
        grandchild = self._task("[QA] Wrap parcel", parent_id=child.id)
        self._index()
        ids = self._ids("sextant copper")
        self.assertIn(child.id, ids)
        self.assertNotIn(grandchild.id, ids)
        sources = child.with_user(self.actor).with_context(search_source_query="sextant copper").read(["usl_search_sources"])[0]["usl_search_sources"]
        self.assertEqual(sources[0]["id"], parent.id)

    def test_source_edit_and_removed_relation_withdraw_the_old_match(self):
        parent = self._task(description="<p>Silver astrolabe.</p>")
        child = self._task(parent_id=parent.id)
        self._index()
        self.assertIn(child.id, self._ids("silver astrolabe"))
        parent.description = "<p>Violet compass.</p>"
        self.assertNotIn(child.id, self._ids("silver astrolabe"))
        self._index()
        self.assertIn(child.id, self._ids("compass violet"))
        child.parent_id = False
        self.assertNotIn(child.id, self._ids("compass violet"))

    def test_hidden_source_field_removes_match_and_raw_source_metadata(self):
        self.project.description = "<p>Amber chronometer.</p>"
        child = self._task()
        self._index()
        self.assertIn(child.id, self._ids("chronometer amber"))
        with patch.object(self.env["project.project"]._fields["description"], "groups", "base.group_system"):
            # Native read permission is memoized for one Environment/request.
            denied = self.Task.with_context(qa_field_access_phase="hidden-project-description")
            self.assertNotIn(child.id, denied.search([("usl_search_text", "ilike", "chronometer amber")]).ids)
            sources = denied.browse(child.id).with_context(search_source_query="chronometer amber").read(["usl_search_sources"])[0]["usl_search_sources"]
            self.assertFalse(sources)
        self.assertIn(child.id, self._ids("chronometer amber"))

    def test_negative_domain_is_the_complement_and_two_paths_do_not_duplicate(self):
        self.project.description = "<p>Teal pendulum.</p>"
        parent = self._task(description="<p>Teal pendulum.</p>")
        child = self._task(parent_id=parent.id)
        self._index()
        self.assertIn(child.id, self._ids("pendulum teal"))
        self.assertNotIn(child.id, self.Task.search([("usl_search_text", "not ilike", "pendulum teal")]).ids)
        rows = self.service.search_records("pendulum teal", categories=["tasks"])["groups"][0]["records"]
        self.assertEqual([row["id"] for row in rows].count(child.id), 1)

    def test_native_source_action_rechecks_edge_query_and_selected_source(self):
        parent = self._task(description="<p>Coral tellurium.</p>")
        child = self._task(parent_id=parent.id)
        other = self._task("[QA] Another plan", description="<p>Coral tellurium.</p>")
        self._index()
        kwargs = {"source_relation": "parent_id", "source_record_id": parent.id, "source_query": "tellurium coral"}
        action = self.service.get_search_result_action("project.task", child.id, **kwargs)
        self.assertEqual((action["res_model"], action["res_id"]), ("project.task", parent.id))
        for forged in ({"source_record_id": other.id}, {"source_relation": "user_ids"},
                       {"source_query": "unrelated"}, {"source_record_id": True}):
            with self.assertRaises(UserError):
                self.service.get_search_result_action("project.task", child.id, **(kwargs | forged))
        child.parent_id = other
        with self.assertRaises(UserError):
            self.service.get_search_result_action("project.task", child.id, **kwargs)
        parent.description = "<p>Replacement source.</p>"
        with self.assertRaises(UserError):
            self.service.get_search_result_action("project.task", child.id, **kwargs)

    def test_withdrawn_project_removes_evidence_but_keeps_independent_own_matches(self):
        self.project.description = "<p>Ochre escapement.</p>"
        linked = self._task()
        independent = self._task(description="<p>Ochre escapement.</p>")
        self._index()
        kwargs = {"source_relation": "project_id", "source_record_id": self.project.id, "source_query": "escapement ochre"}
        self.assertIn(linked.id, self._ids("escapement ochre"))
        rule = self.env["ir.rule"].create({"name": "[QA] Withdraw project source",
            "model_id": self.env["ir.model"]._get_id("project.project"),
            "domain_force": repr([("id", "!=", self.project.id)]), "global": True})
        self.env.invalidate_all()
        self.assertNotIn(linked.id, self._ids("escapement ochre"))
        self.assertIn(independent.id, self._ids("escapement ochre"))
        values = self.Task.browse(linked.id).with_context(search_source_query="escapement ochre").read(["usl_search_sources"])[0]
        self.assertFalse(values["usl_search_sources"])
        with self.assertRaises(UserError):
            self.service.get_search_result_action("project.task", linked.id, **kwargs)
        rule.unlink()
        self.env.invalidate_all()
        self.assertIn(linked.id, self._ids("escapement ochre"))
        self.assertTrue(self.Task.browse(linked.id).with_context(search_source_query="escapement ochre").usl_search_sources)

    def test_two_company_scopes_and_source_opening(self):
        other = self.env["res.company"].create({"name": "[QA] Copper workshop"})
        self.actor.company_ids = [Command.link(other.id)]
        project = self.env["project.project"].with_company(other).create({
            "name": "[QA] Other parcel", "description": "<p>Ivory gnomon.</p>", "company_id": other.id})
        child = self._task(project_id=project.id)
        self._index()
        domain = [("usl_search_text", "ilike", "gnomon ivory")]
        self.assertNotIn(child.id, self.Task.with_context(allowed_company_ids=self.company.ids).search(domain).ids)
        self.assertIn(child.id, self.Task.with_context(allowed_company_ids=other.ids).search(domain).ids)
        action = self.service.with_context(allowed_company_ids=other.ids).get_search_result_action("project.task", child.id,
            source_relation="project_id", source_record_id=project.id, source_query="gnomon ivory")
        self.assertEqual(action["res_id"], project.id)
        with self.assertRaises(UserError):
            self.service.with_context(allowed_company_ids=self.company.ids).get_search_result_action("project.task", child.id,
                source_relation="project_id", source_record_id=project.id, source_query="gnomon ivory")

    def test_exact_own_title_and_existing_message_source_keep_their_priority(self):
        exact = self._task("Indigo heliograph")
        child = self._task()
        self.project.description = "<p>Indigo heliograph.</p>"
        message = child.message_post(body=Markup("<p>Indigo heliograph shared instructions.</p>"),
            message_type="comment", subtype_xmlid="mail.mt_comment")
        self._index()
        self.env["mail.message"]._cron_usl_index_task_messages()
        rows = self.service.search_records("Indigo heliograph", categories=["tasks"])["groups"][0]["records"]
        self.assertEqual(rows[0]["id"], exact.id)
        row = next(row for row in rows if row["id"] == child.id)
        self.assertEqual((row["source"]["kind"], row["source"]["id"]), ("message", message.id))
        action = self.service.get_search_result_action("project.task", child.id, message.id, "body")
        self.assertEqual((action["res_model"], action["res_id"]), ("project.task", child.id))
        self.assertEqual(action["usl_highlight_message_id"], message.id)

    def test_query_count_pagination_and_raw_export_agree_without_writes(self):
        self.project.description = "<p>Violet armillary.</p>"
        children = self.env["project.task"].create([{"name": "[QA] Parcel %s" % i,
            "project_id": self.project.id, "user_ids": [Command.link(self.actor.id)]} for i in range(7)])
        self._index()
        exporter = new_test_user(self.env, login="qa-relations-export", groups="project.group_project_user,base.group_allow_export",
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        self.env.flush_all()
        domain = [("usl_search_text", "ilike", "armillary violet")]
        expected = list(reversed(children.ids))
        with patch.object(type(self.env["project.task"]), "_write", side_effect=AssertionError("Search wrote a task")), \
                patch.object(type(self.env["project.project"]), "_write", side_effect=AssertionError("Search wrote a project")):
            self.assertEqual(self.Task.search_count(domain), len(children))
            for size in (1, 3, 5):
                pages = [row["id"] for offset in range(0, len(children), size)
                    for row in self.service.search_records("armillary violet", categories=["tasks"], limit=size, offset=offset)["groups"][0]["records"]]
                self.assertEqual(pages, expected)
            rows = self.Task.with_context(search_source_query="armillary violet").search_read(domain, ["usl_search_sources"], order="id desc")
            self.assertEqual([row["id"] for row in rows], expected)
            self.assertTrue(all(row["usl_search_sources"][0]["id"] == self.project.id for row in rows))
            with self.assertRaises(UserError):
                self.Task.browse(children.ids).export_data(["usl_search_sources"])
            exported = children.with_user(exporter).with_context(search_source_query="armillary violet").export_data(["usl_search_sources"])
            self.assertEqual(len(exported["datas"]), len(children))
            action = self.service.get_search_result_action("project.task", children[0].id,
                source_relation="project_id", source_record_id=self.project.id, source_query="armillary violet")
            self.assertEqual(action["res_id"], self.project.id)

    def test_new_task_and_empty_query_have_no_source(self):
        self.assertFalse(self.Task.new({"name": "[QA] Unsaved parcel"}).usl_search_sources)
        child = self._task()
        self.assertFalse(self.Task.browse(child.id).with_context(search_source_query="").usl_search_sources)

    def test_managed_readonly_agent_uses_the_same_native_relation_path(self):
        if "usl.agent" not in self.env:
            self.skipTest("Native Agent parity runs in the Distribution registry")
        group = self.env.ref("project.group_project_user")
        agent = self.env["usl.agent"].with_user(self.actor).create({
            "name": "[QA] Relation reader", "purpose": "Find readable task context",
            "company_id": self.company.id, "company_ids": [Command.set(self.company.ids)],
            "delegated_group_ids": [Command.set(group.ids)], "read_only_group_ids": [Command.set(group.ids)],
            "access_mode": "read_only",
        })
        self.project.description = "<p>Jade orrery.</p>"
        child = self._task()
        self._index()
        native = self.env["project.task"].with_user(agent.user_id).with_context(allowed_company_ids=self.company.ids)
        domain = [("usl_search_text", "ilike", "orrery jade")]
        self.assertEqual(native.search(domain).ids, self.Task.search(domain).ids)
        values = native.browse(child.id).with_context(search_source_query="orrery jade").read(["usl_search_sources"])[0]
        self.assertEqual(values["usl_search_sources"][0]["id"], self.project.id)
        with self.assertRaises(AccessError):
            native.browse(child.id).write({"name": "Changed"})
        self.assertEqual(child.name, "[QA] Prepare parcel")

    def test_parent_rule_and_relation_field_withdraw_raw_evidence(self):
        parent = self._task(description="<p>Bronze tellurion.</p>")
        child = self._task(parent_id=parent.id)
        self._index()
        self.assertIn(child.id, self._ids("tellurion bronze"))
        rule = self.env["ir.rule"].create({"name": "[QA] Withdraw parent source",
            "model_id": self.env["ir.model"]._get_id("project.task"),
            "domain_force": repr([("id", "!=", parent.id)]), "global": True})
        self.env.invalidate_all()
        self.assertNotIn(child.id, self._ids("tellurion bronze"))
        self.assertFalse(self.Task.browse(child.id).with_context(search_source_query="tellurion bronze").usl_search_sources)
        rule.unlink()
        self.env.invalidate_all()
        with patch.object(self.Task._fields["parent_id"], "groups", "base.group_system"):
            denied = self.Task.with_context(qa_field_access_phase="hidden-parent-relation")
            # The existing generic Content traversal checks child_ids' inverse
            # parent_id too. Preserve that native refusal; the new source
            # projection must still return no hidden parent metadata.
            self.assertEqual(denied.search([("id", "=", child.id)]).ids, child.ids)
            with self.assertRaises(AccessError):
                denied.search([("usl_search_text", "ilike", "tellurion bronze")])
            with self.assertRaises(AccessError):
                denied.browse(child.id).with_context(search_source_query="tellurion bronze").usl_search_sources
        self.assertIn(child.id, self._ids("tellurion bronze"))

    def test_hidden_source_name_is_absent_from_source_metadata(self):
        self.project.description = "<p>Rose equatorium.</p>"
        child = self._task()
        self._index()
        with patch.object(self.env["project.project"]._fields["name"], "groups", "base.group_system"):
            reader = self.Task.with_context(qa_field_access_phase="hidden-project-name", search_source_query="equatorium rose")
            source = reader.browse(child.id).read(["usl_search_sources"])[0]["usl_search_sources"][0]
            self.assertNotIn(self.project.name, source["label"])
            self.assertIn("Rose equatorium", source["excerpt"])
            row = self.service.with_context(qa_field_access_phase="hidden-project-name-home").search_records(
                "equatorium rose", categories=["tasks"])["groups"][0]["records"][0]
            self.assertNotIn(self.project.name, row["source"]["label"])
            self.assertNotIn(self.project.name, row["context_label"])

    def test_native_domain_keeps_long_legacy_queries_for_related_descriptions(self):
        query = "q" * 2050
        self.project.description = "<p>" + query + "</p>"
        child = self._task()
        self._index()
        self.assertIn(child.id, self._ids(query))
        self.assertNotIn(child.id, self.Task.search([("usl_search_text", "not ilike", query)]).ids)

    def test_archived_source_found_on_home_still_opens_natively(self):
        self.project.description = "<p>Rose heliometer.</p>"
        child = self._task()
        self._index()
        self.project.active = False
        rows = self.service.search_records("heliometer rose", categories=["tasks"])["groups"][0]["records"]
        row = next(row for row in rows if row["id"] == child.id)
        self.assertEqual(row["source"]["id"], self.project.id)
        action = self.service.get_search_result_action("project.task", child.id,
            source_relation="project_id", source_record_id=self.project.id, source_query="heliometer rose")
        self.assertEqual((action["res_model"], action["res_id"]), ("project.project", self.project.id))

    def test_existing_activity_match_keeps_its_native_preview(self):
        self.project.description = "<p>Golden tachymeter.</p>"
        child = self._task()
        child.activity_schedule("mail.mail_activity_data_todo", user_id=self.actor.id,
            summary="Inspect golden tachymeter", note="Existing native task activity.")
        self._index()
        rows = self.service.search_records("golden tachymeter", categories=["tasks"])["groups"][0]["records"]
        row = next(row for row in rows if row["id"] == child.id)
        self.assertFalse(row["source"])
        self.assertIn("golden tachymeter", row["excerpt"])
        action = self.service.get_search_result_action("project.task", child.id)
        self.assertEqual((action["res_model"], action["res_id"]), ("project.task", child.id))

from markupsafe import Markup

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_task_chatter_search")
class TestTaskChatterSearch(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.actor = new_test_user(
            cls.env, login="qa-task-chatter-search",
            groups="project.group_project_user",
        )
        operator_groups = ["base.group_system", "project.group_project_manager"]
        for xmlid in ("usl_access_control.group_distribution_administrator", "usl_access_control.group_irreversible_actions"):
            if cls.env.ref(xmlid, raise_if_not_found=False):
                operator_groups.append(xmlid)
        cls.operator = new_test_user(
            cls.env, login="qa-task-chatter-operator", groups=",".join(operator_groups),
        )
        cls.project = cls.env["project.project"].create({
            "name": "[QA] Chatter search workspace", "company_id": cls.env.company.id,
        })

    def _fixture(self):
        task = self.env["project.task"].with_context(tracking_disable=True).create({
            "name": "[QA] Ordinary source task", "project_id": self.project.id,
            "user_ids": [Command.link(self.actor.id)],
        })
        message = task.with_user(self.actor).message_post(
            body=Markup("<p>Amber planning for the heliograph launch.</p>"),
            subject="[QA] Planning note", subtype_xmlid="mail.mt_comment",
        )
        self.env["project.task"]._cron_usl_index_task_content()
        worker = getattr(self.env["mail.message"], "_cron_usl_index_task_messages", None)
        if worker:
            worker()
        return task, message

    def test_task_matches_readable_chatter_and_exposes_exact_source(self):
        task, message = self._fixture()
        Task = self.env["project.task"].with_user(self.actor).with_context(
            search_source_query="heliograph Amber",
        )
        self.assertEqual(Task.search([("usl_search_text", "ilike", "heliograph Amber")]), task)
        sources = Task.browse(task.id).usl_search_sources
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["id"], message.id)
        self.assertEqual(sources[0]["model"], "mail.message")
        self.assertEqual(sources[0]["field"], "body")
        self.assertIn("heliograph", sources[0]["excerpt"])
        self.assertIn("Amber", sources[0]["excerpt"])
        self.assertEqual(sources[0]["url"], "/mail/message/%s" % message.id)
        message.with_user(self.actor).check_access("read")

    def test_home_identifies_chatter_source_and_plain_text(self):
        task, message = self._fixture()
        result = self.env["usl.home.service"].with_user(self.actor).search_records(
            "heliograph Amber", categories=["tasks"],
        )
        rows = result["groups"][0]["records"]
        self.assertEqual([row["id"] for row in rows], task.ids)
        self.assertEqual(rows[0]["source"]["id"], message.id)
        self.assertEqual(rows[0]["source"]["kind"], "message")
        self.assertIn("Amber", rows[0]["excerpt"])
        self.assertNotIn("<p>", rows[0]["excerpt"])

    def _search(self, text="heliograph Amber", **context):
        return self.env["project.task"].with_user(self.actor).with_context(**context).search([
            ("usl_search_text", "ilike", text),
        ])

    def test_message_rule_revocation_and_restore_without_reindex(self):
        task, message = self._fixture()
        rule = self.env["ir.rule"].create({
            "name": "[QA] Withdraw readable chatter source",
            "model_id": self.env["ir.model"]._get_id("mail.message"),
            "domain_force": repr([("id", "!=", message.id)]), "global": True,
        })
        self.assertFalse(self._search())
        self.assertEqual(task.with_user(self.actor)._usl_matching_message_sources("heliograph Amber"), {})
        self.assertFalse(task.with_user(self.actor).with_context(search_source_query="heliograph Amber").usl_search_sources)
        rule.unlink()
        self.assertEqual(self._search(), task)

    def test_parent_rule_revokes_source_result_and_evidence(self):
        task, _message = self._fixture()
        rule = self.env["ir.rule"].create({
            "name": "[QA] Withdraw task source parent",
            "model_id": self.env["ir.model"]._get_id("project.task"),
            "domain_force": repr([("id", "!=", task.id)]), "global": True,
        })
        self.assertFalse(self._search())
        self.assertEqual(task.with_user(self.actor)._usl_matching_message_sources("heliograph Amber"), {})
        self.assertFalse(task.with_user(self.actor).with_context(search_source_query="heliograph Amber").usl_search_sources)
        rule.unlink()
        self.assertEqual(self._search(), task)

    def test_source_field_permission_rechecked(self):
        from unittest.mock import patch
        task, _message = self._fixture()
        with patch.object(self.env["mail.message"]._fields["body"], "groups", "base.group_system"):
            self.assertFalse(self._search())
            self.assertEqual(task.with_user(self.actor)._usl_matching_message_sources("heliograph Amber"), {})
        self.assertEqual(self._search(), task)
        with patch.object(self.env["project.task"]._fields["message_ids"], "groups", "base.group_system"):
            self.assertFalse(self._search(qa_field_access_phase="restrict-thread-field"))
            fresh = task.with_user(self.actor).with_context(qa_field_access_phase="restrict-thread-field")
            self.assertEqual(fresh._usl_matching_message_sources("heliograph Amber"), {})

    def test_subject_is_independent_readable_source(self):
        from unittest.mock import patch
        task, message = self._fixture()
        message.write({"subject": "Copper planning for the lantern launch", "body": "<p>Unrelated prose.</p>"})
        self.env["mail.message"]._cron_usl_index_task_messages()
        with patch.object(self.env["mail.message"]._fields["body"], "groups", "base.group_system"):
            self.assertEqual(self._search("lantern Copper"), task)
            source = task.with_user(self.actor)._usl_matching_message_sources("lantern Copper")[task.id]
            self.assertEqual(source["field"], "subject")
        with patch.object(self.env["mail.message"]._fields["subject"], "groups", "base.group_system"):
            self.assertFalse(self._search("lantern Copper", qa_field_access_phase="restrict-subject"))

    def test_selected_company_excludes_message_and_excerpt(self):
        company = self.env["res.company"].create({"name": "[QA] Chatter other company"})
        self.actor.company_ids = [Command.link(company.id)]
        task, _message = self._fixture()
        task.write({"project_id": False, "company_id": company.id})
        self.assertFalse(self._search(allowed_company_ids=self.env.company.ids))
        self.assertEqual(self._search(allowed_company_ids=company.ids), task)
        hidden = task.with_user(self.actor).with_context(allowed_company_ids=self.env.company.ids)
        self.assertEqual(hidden._usl_matching_message_sources("heliograph Amber"), {})

    def test_edit_excludes_old_fragment_and_republishes_new_words(self):
        task, message = self._fixture()
        message.with_user(self.actor).write({"body": "<p>Violet planning for the semaphore launch.</p>"})
        self.assertFalse(self._search())
        self.assertEqual(self._search("Violet planning"), task)
        self.env["mail.message"]._cron_usl_index_task_messages()
        self.assertEqual(self._search("semaphore Violet"), task)
        self.assertFalse(self._search())

    def test_removed_source_disappears_without_refresh(self):
        task, message = self._fixture()
        message.with_user(self.operator).unlink()
        self.assertFalse(self._search())
        self.assertEqual(task.with_user(self.actor)._usl_matching_message_sources("heliograph Amber"), {})

    def test_relinked_message_never_matches_former_parent(self):
        task, message = self._fixture()
        other = self.env["project.task"].with_context(tracking_disable=True).create({
            "name": "[QA] New source parent", "project_id": self.project.id,
            "user_ids": [Command.link(self.actor.id)],
        })
        message.with_env(self.env).write({"res_id": other.id})
        self.assertEqual(self._search("Amber planning"), other)
        self.env["mail.message"]._cron_usl_index_task_messages()
        self.assertEqual(self._search(), other)
        self.assertEqual(task.with_user(self.actor)._usl_matching_message_sources("heliograph Amber"), {})

    def test_model_scoped_native_query_avoids_general_mail_fallback(self):
        from unittest.mock import patch
        task, _message = self._fixture()
        self.env.flush_all()
        def reject_general(*args, **kwargs):
            raise AssertionError("General mail access scan used instead of native model-scoped SQL")
        with patch.object(type(self.env["mail.message"]), "_filter_accessible_from_query", reject_general):
            self.assertEqual(self._search(), task)

    def test_private_helpers_cannot_be_called_as_rpc(self):
        from odoo.exceptions import AccessError
        from odoo.orm.models import get_public_method
        for model, helpers in [
            ("mail.message", ("init", "_usl_queue_task_messages", "_cron_usl_index_task_messages", "_usl_readable_task_message_fields", "_usl_task_conversation_domain", "_usl_matching_task_message_query")),
            ("project.task", ("_usl_matching_message_sources", "_compute_usl_search_sources")),
        ]:
            for helper in helpers:
                with self.subTest(model=model, helper=helper), self.assertRaises(AccessError):
                    get_public_method(self.env[model].with_user(self.actor), helper)

    def test_source_jump_rechecks_parent_message_and_field(self):
        from odoo.exceptions import UserError
        from unittest.mock import patch
        task, message = self._fixture()
        Home = self.env["usl.home.service"].with_user(self.actor)
        action = Home.get_search_result_action("project.task", task.id, message.id, "body")
        self.assertEqual(action, {"type": "ir.actions.act_url", "url": "/mail/message/%s" % message.id, "target": "self"})
        self.assertEqual(Home.get_search_result_action("project.task", task.id)["res_id"], task.id)
        with patch.object(self.env["mail.message"]._fields["body"], "groups", "base.group_system"), self.assertRaises(UserError):
            Home.with_context(qa_field_access_phase="restrict-jump-field").get_search_result_action("project.task", task.id, message.id, "body")
        rule = self.env["ir.rule"].create({
            "name": "[QA] Withdraw jump source", "model_id": self.env["ir.model"]._get_id("mail.message"),
            "domain_force": repr([("id", "!=", message.id)]), "global": True,
        })
        with self.assertRaises(UserError):
            Home.get_search_result_action("project.task", task.id, message.id, "body")
        rule.unlink()
        message.with_user(self.operator).unlink()
        with self.assertRaises(UserError):
            Home.get_search_result_action("project.task", task.id, message.id, "body")

    def test_evidence_cache_is_invalidated_by_source_edit(self):
        task, message = self._fixture()
        viewed = task.with_user(self.actor).with_context(search_source_query="heliograph Amber")
        self.assertEqual(viewed.usl_search_sources[0]["id"], message.id)
        message.with_user(self.actor).write({"body": "<p>Violet semaphore replacement.</p>"})
        self.assertFalse(viewed.usl_search_sources)

    def test_legacy_scalar_search_values_remain_supported(self):
        task, _message = self._fixture()
        Task = self.env["project.task"].with_user(self.actor)
        with self.subTest(value=False):
            self.assertEqual(Task.search([("id", "=", task.id), ("usl_search_text", "ilike", False)]), task)
            self.assertFalse(Task.search([("id", "=", task.id), ("usl_search_text", "not ilike", False)]))
        for value in (375815, "x" * 2050):
            with self.subTest(value_type=type(value).__name__):
                task.write({"name": str(value)})
                self.env["project.task"]._cron_usl_index_task_content()
                self.assertEqual(Task.search([("id", "=", task.id), ("usl_search_text", "ilike", value)]), task)

    def test_evidence_cache_tracks_selected_company_set(self):
        company = self.env["res.company"].create({"name": "[QA] Evidence other company"})
        self.actor.company_ids = [Command.link(company.id)]
        task, message = self._fixture()
        task.write({"project_id": False, "company_id": company.id})
        both = task.with_user(self.actor).with_context(
            allowed_company_ids=[self.env.company.id, company.id], search_source_query="heliograph Amber",
        )
        self.assertEqual(both.usl_search_sources[0]["id"], message.id)
        self.assertFalse(both.with_context(allowed_company_ids=self.env.company.ids).usl_search_sources)

    def test_clean_source_excerpt_and_negative_domain(self):
        task, message = self._fixture()
        message.write({"body": '<p class="markup-only-3758">Amber heliograph visible</p><a href="https://source-3758.invalid">Reference</a>'})
        self.env["mail.message"]._cron_usl_index_task_messages()
        self.assertFalse(self._search("markup-only-3758"))
        self.assertEqual(self._search("visible Amber"), task)
        self.assertEqual(self._search("source-3758.invalid"), task)
        negative = self.env["project.task"].with_user(self.actor).search([
            ("id", "=", task.id), ("usl_search_text", "not ilike", "visible Amber"),
        ])
        self.assertFalse(negative)

    def test_cached_evidence_withdraws_on_parent_archive_and_company_change(self):
        task, message = self._fixture()
        viewed = task.with_user(self.actor).with_context(search_source_query="heliograph Amber", allowed_company_ids=self.env.company.ids)
        self.assertEqual(viewed.usl_search_sources[0]["id"], message.id)
        task.active = False
        self.assertFalse(self._search())
        self.assertEqual(self._search(active_test=False), task)
        # Archive selection is not a read ACL: a directly read archived task
        # retains its readable source, like native mail and Home destinations.
        self.assertEqual(viewed.usl_search_sources[0]["id"], message.id)
        self.assertEqual(viewed.with_context(active_test=False).usl_search_sources[0]["id"], message.id)
        task.active = True
        company = self.env["res.company"].create({"name": "[QA] Moved evidence company"})
        self.actor.company_ids = [Command.link(company.id)]
        self.assertEqual(viewed.usl_search_sources[0]["id"], message.id)
        task.write({"project_id": False, "company_id": company.id})
        self.assertFalse(viewed.usl_search_sources)

    def test_rollback_restores_source_queue_and_publication_together(self):
        task, message = self._fixture()
        original = message.body
        with self.assertRaisesRegex(ValueError, "rollback fixture"):
            with self.env.cr.savepoint():
                message.write({"body": "<p>Violet semaphore replacement.</p>"})
                self.env["mail.message"]._cron_usl_index_task_messages()
                self.assertEqual(self._search("replacement Violet"), task)
                self.assertFalse(self._search())
                raise ValueError("rollback fixture")
        self.assertEqual(message.body, original)
        self.assertEqual(self._search(), task)
        self.assertFalse(self._search("replacement Violet"))

    def test_native_notification_is_not_a_chatter_source(self):
        task, message = self._fixture()
        message.with_env(self.env).write({"message_type": "user_notification"})
        self.env["mail.message"]._cron_usl_index_task_messages()
        self.assertFalse(self._search())
        self.assertEqual(task.with_user(self.actor)._usl_matching_message_sources("heliograph Amber"), {})

    def test_source_action_rejects_other_parent_and_stale_company(self):
        from odoo.exceptions import UserError
        task, message = self._fixture()
        other = self.env["project.task"].with_context(tracking_disable=True).create({
            "name": "[QA] Unrelated source parent", "project_id": self.project.id,
            "user_ids": [Command.link(self.actor.id)],
        })
        Home = self.env["usl.home.service"].with_user(self.actor)
        for source_id, source_field in ((message.id, "body"), (False, "body"), (message.id, "password"), (message.id, None)):
            with self.subTest(source_id=source_id, source_field=source_field), self.assertRaises(UserError):
                Home.get_search_result_action("project.task", other.id, source_id, source_field)
        company = self.env["res.company"].create({"name": "[QA] Stale selected company"})
        self.actor.company_ids = [Command.link(company.id)]
        task.write({"project_id": False, "company_id": company.id})
        with self.assertRaises(UserError):
            Home.with_context(allowed_company_ids=self.env.company.ids).get_search_result_action("project.task", task.id, message.id, "body")

    def test_search_and_evidence_have_no_database_mutation(self):
        import re
        from unittest.mock import patch
        from odoo.tools import SQL
        task, _message = self._fixture()
        self.env.flush_all()
        execute = self.env.cr.execute
        def readonly(statement, *args, **kwargs):
            text = statement._sql_tuple[0] if isinstance(statement, SQL) else statement
            if re.search(r"\b(?:INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE)\b", text, re.I):
                raise AssertionError("Database mutation inside source search")
            return execute(statement, *args, **kwargs)
        with patch.object(self.env.cr, "execute", readonly):
            self.assertEqual(self._search(), task)
            self.assertEqual(task.with_user(self.actor)._usl_matching_message_sources("heliograph Amber")[task.id]["kind"], "message")
            for statement in ("UPDATE mail_message SET body=body WHERE FALSE", SQL("UPDATE mail_message SET body=body WHERE FALSE")):
                with self.assertRaisesRegex(AssertionError, "Database mutation"):
                    self.env.cr.execute(statement)

    def test_projection_publication_and_requeue_invalidate_evidence_cache(self):
        task, message = self._fixture()
        message.write({"body": "<p>Violet semaphore replacement.</p>"})
        viewed = task.with_user(self.actor).with_context(search_source_query="replacement Violet")
        self.assertFalse(viewed.usl_search_sources)
        self.env["mail.message"]._cron_usl_index_task_messages()
        self.assertTrue(viewed.usl_search_sources)
        self.assertEqual(viewed.usl_search_sources[0]["id"], message.id)
        message._usl_queue_task_messages()
        self.assertFalse(viewed.usl_search_sources)

    def test_message_model_change_cannot_reuse_numeric_task_identity(self):
        task, message = self._fixture()
        # Native Many2oneReference has no cross-model foreign key; the author
        # can still read this message even when its new document is absent.
        message.with_env(self.env).write({"model": "res.partner", "res_id": task.id})
        self.assertTrue(message.with_user(self.actor).has_access("read"))
        self.assertFalse(self._search("Amber planning"))
        self.assertEqual(task.with_user(self.actor)._usl_matching_message_sources("Amber planning"), {})
        self.env["mail.message"]._cron_usl_index_task_messages()
        self.assertFalse(self._search())
        self.assertEqual(message.model, "res.partner")

    def test_failed_refresh_keeps_committed_source_and_pending_batch(self):
        from unittest.mock import patch
        task, message = self._fixture()
        message.write({"body": "<p>Violet semaphore replacement.</p>"})
        self.env.flush_all()
        execute = self.env.cr.execute
        def interrupt(statement, *args, **kwargs):
            if isinstance(statement, str) and "INSERT INTO usl_task_message_search_fragment" in statement:
                raise ValueError("interrupted refresh fixture")
            return execute(statement, *args, **kwargs)
        with self.assertRaisesRegex(ValueError, "interrupted refresh fixture"):
            with self.env.cr.savepoint(), patch.object(self.env.cr, "execute", interrupt):
                self.env["mail.message"]._cron_usl_index_task_messages()
        self.assertIn("Violet", message.body)
        self.assertFalse(self._search())
        self.assertEqual(self._search("Violet semaphore"), task)
        self.env.cr.execute("SELECT message_id FROM usl_task_message_search_queue WHERE message_id=%s", (message.id,))
        self.assertEqual(self.env.cr.fetchone(), (message.id,))
        self.env["mail.message"]._cron_usl_index_task_messages()
        self.assertEqual(self._search("replacement Violet"), task)

    def test_auto_task_event_is_excluded_but_deliberate_comment_is_searchable(self):
        from odoo.exceptions import UserError
        project = self.env["project.project"].create({
            "name": "[QA] Vermilion heliograph", "privacy_visibility": "followers",
        })
        task = self.env["project.task"].create({
            "name": "[QA] Ordinary conversation task", "project_id": project.id,
            "user_ids": [Command.link(self.actor.id)],
        })
        created = self.env["mail.message"].search([
            ("model", "=", "project.task"), ("res_id", "=", task.id),
            ("subtype_id", "=", self.env.ref("project.mt_task_new").id),
        ])
        self.assertEqual(len(created), 1)
        self.assertEqual(created.message_type, "notification")
        self.assertTrue(created.with_user(self.actor).has_access("read"))
        self.assertFalse(project.with_user(self.actor).has_access("read"))
        self.env["mail.message"]._cron_usl_index_task_messages()
        self.assertFalse(self._search("Vermilion heliograph"))
        comment = task.with_user(self.actor).message_post(
            body=Markup("<p>We agreed to discuss the Vermilion heliograph proposal.</p>"),
            message_type="comment", subtype_xmlid="mail.mt_comment",
        )
        self.assertEqual(comment.message_type, "comment")
        self.assertEqual(comment.subtype_id, self.env.ref("mail.mt_comment"))
        self.env["mail.message"]._cron_usl_index_task_messages()
        self.assertEqual(self._search("heliograph Vermilion"), task)
        source = task.with_user(self.actor)._usl_matching_message_sources("heliograph Vermilion")[task.id]
        self.assertEqual(source["id"], comment.id)
        with self.assertRaises(UserError):
            self.env["usl.home.service"].with_user(self.actor).get_search_result_action("project.project", project.id)

    def test_shared_native_note_keeps_notification_type_and_is_readable(self):
        task, _message = self._fixture()
        task.write({"project_id": False, "company_id": False})
        note = task.with_user(self.actor).message_post(
            body=Markup("<p>Ivory planning for the semaphore prototype.</p>"),
            subtype_xmlid="mail.mt_note",
        )
        self.assertEqual(note.message_type, "notification")
        self.assertEqual(note.subtype_id, self.env.ref("mail.mt_note"))
        self.env["mail.message"]._cron_usl_index_task_messages()
        self.assertEqual(self._search("prototype Ivory"), task)
        self.assertEqual(task.with_user(self.actor)._usl_matching_message_sources("prototype Ivory")[task.id]["id"], note.id)

    def test_user_correspondence_subject_without_subtype_is_preserved(self):
        task, _message = self._fixture()
        for kind in ("email", "email_outgoing", "comment", "notification"):
            with self.subTest(message_type=kind):
                message = self.env["mail.message"].with_user(self.actor).create({
                    "model": "project.task", "res_id": task.id,
                    "subject": "Iris planning for the meridian prototype", "body": "<p>Meeting details.</p>",
                    "message_type": kind, "subtype_id": False,
                    "author_id": self.actor.partner_id.id,
                })
                self.assertEqual(message.message_type, kind)
                self.assertFalse(message.subtype_id)
                self.env["mail.message"]._cron_usl_index_task_messages()
                self.assertEqual(self._search("prototype Iris"), task)
                source = task.with_user(self.actor)._usl_matching_message_sources("prototype Iris")[task.id]
                self.assertEqual(source["id"], message.id)
                self.assertEqual(source["field"], "subject")
                message.with_user(self.operator).unlink()

    def test_native_model_read_permission_controls_source_contribution(self):
        task, _message = self._fixture()
        access = self.env["ir.model.access"].search([
            ("model_id.model", "=", "mail.message"), ("perm_read", "=", True), ("active", "=", True),
        ])
        self.assertTrue(access)
        access.write({"active": False})
        self.assertFalse(self.env["mail.message"].with_user(self.actor).has_access("read"))
        self.assertFalse(self._search())
        self.assertFalse(task.with_user(self.actor).with_context(search_source_query="heliograph Amber").usl_search_sources)
        self.assertEqual(self._search("Ordinary source task"), task)
        access.write({"active": True})
        self.assertEqual(self._search(), task)

    def test_worker_is_bounded_and_pending_source_stays_live(self):
        task, _message = self._fixture()
        messages = self.env["mail.message"].with_user(self.actor).create([
            {"model": "project.task", "res_id": task.id, "body": "<p>Copper batch example %s.</p>" % index,
             "message_type": "comment", "subtype_id": self.env.ref("mail.mt_comment").id,
             "author_id": self.actor.partner_id.id}
            for index in range(502)
        ])
        self.env["mail.message"]._cron_usl_index_task_messages()
        self.env.cr.execute("SELECT message_id FROM usl_task_message_search_queue WHERE message_id=ANY(%s) ORDER BY message_id", (messages.ids,))
        pending = self.env["mail.message"].browse([row[0] for row in self.env.cr.fetchall()])
        self.assertEqual(len(pending), 2)
        pending[0].write({"body": "<p>Jade lantern freshly edited.</p>"})
        self.assertEqual(self._search("Jade lantern"), task)
        source = task.with_user(self.actor)._usl_matching_message_sources("Jade lantern")[task.id]
        self.assertEqual(source["id"], pending[0].id)
        self.env["mail.message"]._cron_usl_index_task_messages()
        self.assertEqual(self._search("edited Jade"), task)

    def test_repeated_initialization_keeps_ready_sources_available(self):
        task, message = self._fixture()
        original = message.body
        self.env["mail.message"].init()
        self.assertEqual(self._search(), task)
        self.assertEqual(message.body, original)
        self.env.cr.execute("SELECT message_id FROM usl_task_message_search_queue WHERE message_id=%s", (message.id,))
        self.assertIsNone(self.env.cr.fetchone())

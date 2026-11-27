from odoo import Command
from odoo.tools import SQL
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_task_lexical_index")
class TestTaskLexicalIndex(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.actor = new_test_user(cls.env, login="qa-lexical-search", groups="project.group_project_user")
        cls.project = cls.env["project.project"].create({"name": "[QA] Lexical search", "company_id": cls.env.company.id})

    def _task(self, **values):
        return self.env["project.task"].create({
            "name": "[QA] Ordinary task", "project_id": self.project.id,
            "user_ids": [Command.link(self.actor.id)], **values,
        })

    def _index(self):
        # Execute the fixed native scheduled action, when installed. This also
        # lets the same observable regression run on the pre-index predecessor.
        cron = self.env.ref("usl_project.ir_cron_usl_index_task_content", raise_if_not_found=False)
        if cron:
            cron.ir_actions_server_id.run()

    def _search(self, text, **context):
        return self.env["project.task"].with_user(self.actor).with_context(**context).search([
            ("usl_search_text", "ilike", text),
        ])

    def test_native_search_finds_words_in_different_order(self):
        task = self._task(description="<p>Amber planning for the heliograph launch</p>")
        self._index()
        self.assertEqual(self._search("heliograph Amber"), task)

    def test_clean_html_does_not_match_markup(self):
        task = self._task(description='<p class="zircon-markup-only">Amber heliograph</p>')
        self._index()
        self.assertFalse(self._search("zircon-markup-only"))
        self.assertEqual(self._search("Amber heliograph"), task)

    def test_pending_content_remains_live_and_old_fragment_is_excluded(self):
        task = self._task(description="Amber heliograph earlier")
        self._index()
        task.description = "Violet semaphore replacement"
        self.assertFalse(self._search("Amber heliograph earlier"))
        self.assertEqual(self._search("Violet semaphore replacement"), task)
        self._index()
        self.assertEqual(self._search("replacement Violet"), task)
        self.assertFalse(self._search("Amber heliograph earlier"))

    def test_rollback_restores_content_and_queue_together(self):
        task = self._task(description="Amber heliograph earlier")
        self._index()
        with self.assertRaisesRegex(ValueError, "rollback fixture"):
            with self.env.cr.savepoint():
                task.description = "Violet semaphore replacement"
                self.assertEqual(self._search("Violet semaphore replacement"), task)
                raise ValueError("rollback fixture")
        self.assertEqual(self._search("heliograph Amber"), task)
        self.assertFalse(self._search("Violet semaphore replacement"))

    def test_current_field_permission_controls_already_indexed_content(self):
        from unittest.mock import patch
        task = self._task(description="Coral heliograph evidence")
        self._index()
        with patch.object(self.env["project.task"]._fields["description"], "groups", "base.group_system"):
            self.assertFalse(self._search("heliograph Coral"))
        self.assertEqual(self._search("heliograph Coral"), task)

    def test_current_task_rule_controls_already_indexed_content(self):
        task = self._task(description="Coral heliograph evidence")
        self._index()
        self.env["ir.rule"].create({
            "name": "[QA] Revoke indexed task", "model_id": self.env["ir.model"]._get_id("project.task"),
            "domain_force": "[(\"id\", \"!=\", %s)]" % task.id, "global": True,
        })
        self.assertFalse(self._search("heliograph Coral"))

    def test_selected_company_controls_already_indexed_content(self):
        company = self.env["res.company"].create({"name": "[QA] Other search company"})
        self.actor.company_ids = [Command.link(company.id)]
        project = self.env["project.project"].with_company(company).create({
            "name": "[QA] Other project", "company_id": company.id,
        })
        task = self._task(project_id=project.id, description="Coral heliograph evidence")
        self._index()
        self.assertFalse(self._search("heliograph Coral", allowed_company_ids=self.env.company.ids))
        self.assertEqual(self._search("heliograph Coral", allowed_company_ids=company.ids), task)

    def test_accents_substrings_and_archived_records(self):
        task = self._task(description="<p>Préparer le sémaphore écarlate</p>", active=False)
        self._index()
        self.assertEqual(self._search("ecarlate semaphore", active_test=False), task)
        self.assertEqual(self._search("maphore", active_test=False), task)
        self.assertFalse(self._search("ecarlate semaphore"))

    def test_count_grouping_and_negation_use_same_native_domain(self):
        task = self._task(description="Amber planning for heliograph launch")
        self._index()
        Task = self.env["project.task"].with_user(self.actor)
        domain = [("usl_search_text", "ilike", "heliograph Amber")]
        self.assertEqual(Task.search_count(domain), 1)
        self.assertEqual(Task._read_group(domain, aggregates=["__count"]), [(1,)])
        self.assertFalse(Task.search([("id", "=", task.id), ("usl_search_text", "not ilike", "heliograph Amber")]))

    def test_queue_coalesces_edits_and_indexing_can_repeat(self):
        task = self._task(description="Amber heliograph initial")
        task.write({"description": "Amber heliograph second"})
        task.write({"description": "Amber heliograph final"})
        self.env.cr.execute("SELECT count(*) FROM usl_task_search_queue WHERE task_id = %s", (task.id,))
        self.assertEqual(self.env.cr.fetchone()[0], 1)
        self._index()
        self._index()
        self.assertEqual(self._search("final heliograph"), task)
        self.assertFalse(self._search("initial heliograph"))

    def test_deletion_removes_derived_rows_without_a_rebuild(self):
        task = self._task(description="Amber heliograph deletion")
        self._index()
        task_id = task.id
        task.unlink()
        for table in ("usl_task_search_queue", "usl_task_search_fragment"):
            from odoo.tools import SQL
            self.env.cr.execute(SQL("SELECT count(*) FROM %s WHERE task_id = %s", SQL.identifier(table), task_id))
            self.assertEqual(self.env.cr.fetchone()[0], 0)
        self.assertFalse(self._search("heliograph deletion"))

    def test_search_does_not_write_the_projection_or_queue(self):
        from unittest.mock import patch
        task = self._task(description="Amber heliograph evidence")
        self._index()
        execute = self.env.cr.execute
        self.env.flush_all()
        def readonly(query, *args, **kwargs):
            code = query._sql_tuple[0] if isinstance(query, SQL) else query
            self.assertNotRegex(str(code).lstrip().upper(), r"^(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b")
            return execute(query, *args, **kwargs)
        self.env.flush_all()  # Complete fixture writes before the read-only phase.
        with patch.object(self.env.cr, "execute", readonly):
            for statement in ("UPDATE usl_task_search_queue SET queued_at = queued_at WHERE FALSE", SQL("UPDATE usl_task_search_queue SET queued_at = queued_at WHERE FALSE")):
                with self.assertRaises(AssertionError):
                    self.env.cr.execute(statement)
            self.assertEqual(self._search("heliograph Amber"), task)

    def test_postgresql_lexical_index_is_usable(self):
        self._task(description="Amber heliograph evidence")
        self._index()
        self.env.cr.execute("SET LOCAL enable_seqscan = off")
        self.env.cr.execute("""
            EXPLAIN SELECT task_id FROM usl_task_search_fragment
            WHERE to_tsvector('simple', content) @@ plainto_tsquery('simple', 'heliograph Amber')
        """)
        plan = "\n".join(row[0] for row in self.env.cr.fetchall())
        self.assertIn("usl_task_search_fragment_lexical_idx", plan)

    def test_projection_helpers_are_not_rpc_entry_points(self):
        from odoo.exceptions import AccessError
        from odoo.orm.models import get_public_method
        Task = self.env["project.task"].with_user(self.actor)
        for method in ("init", "_usl_queue_task_content", "_cron_usl_index_task_content", "_usl_indexed_task_content_domain"):
            with self.subTest(method=method), self.assertRaises(AccessError):
                get_public_method(Task, method)

    def test_repeated_module_initialization_keeps_ready_results_available(self):
        task = self._task(description="Amber planning for the heliograph launch")
        self._index()
        self.env["project.task"].init()
        self.assertEqual(self._search("heliograph Amber"), task)

    def test_reinstallation_requeues_fragments_from_before_the_feature_was_disabled(self):
        from unittest.mock import patch
        from ..hooks import post_init_hook
        task = self._task(description="Amber heliograph earlier")
        self._index()
        # Native task writes can occur while this add-on is uninstalled. Retain
        # the projection but omit its write hook for this one fixture update.
        with patch.object(type(task), "_usl_queue_task_content", lambda records: None):
            task.description = "Violet semaphore replacement"
        post_init_hook(self.env)
        self.assertFalse(self._search("heliograph Amber"))
        self._index()
        self.assertEqual(self._search("replacement Violet"), task)

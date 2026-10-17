from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'usl_task_content_search')
class TestTaskContentSearch(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.actor = new_test_user(
            cls.env, login='qa-content-search-user', groups='project.group_project_user',
        )
        cls.project = cls.env['project.project'].create({
            'name': '[QA Search] Launch plan',
            'company_id': cls.env.company.id,
            'task_properties_definition': [
                {'name': 'qa_sources', 'string': 'Sources', 'type': 'char'},
            ],
        })

    def _task(self, **values):
        return self.env['project.task'].create({
            'name': '[QA Search] Ordinary task',
            'project_id': self.project.id,
            'user_ids': [Command.link(self.actor.id)],
            **values,
        })

    def _matches(self, query):
        return self.env['project.task'].with_user(self.actor).search([
            ('usl_search_text', 'ilike', query),
        ])

    def test_description_content_is_searchable_without_name_match(self):
        task = self._task(description='<p>Amber heliograph specification</p>')
        self.assertEqual(self._matches('Amber heliograph'), task)

    def test_related_activity_note_is_searchable(self):
        task = self._task()
        self.env['mail.activity'].create({
            'res_model_id': self.env['ir.model']._get_id('project.task'),
            'res_id': task.id,
            'user_id': self.actor.id,
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'summary': 'Review the plan',
            'note': '<p>Indigo heliograph instructions</p>',
        })
        self.assertEqual(self._matches('Indigo heliograph'), task)

    def test_native_property_value_is_searchable(self):
        task = self._task(task_properties=[
            {'name': 'qa_sources', 'value': 'Cobalt heliograph notes'},
        ])
        self.assertEqual(self._matches('Cobalt heliograph'), task)

    def test_readable_related_project_name_is_searchable(self):
        project = self.env["project.project"].create({"name": "[QA Search] Jade heliograph"})
        task = self._task(project_id=project.id)
        self.assertEqual(self._matches("Jade heliograph"), task)

    def test_unreadable_project_name_cannot_influence_readable_task_match(self):
        project = self.env["project.project"].create({
            "name": "[QA Search] Vermilion heliograph", "privacy_visibility": "followers",
        })
        task = self._task(project_id=project.id)
        self.assertTrue(task.with_user(self.actor).has_access("read"))
        self.assertFalse(project.with_user(self.actor).has_access("read"))
        self.assertFalse(self._matches("Vermilion heliograph"))

    def test_readable_task_properties_do_not_require_project_business_access(self):
        project = self.env["project.project"].create({
            "name": "[QA Search] Private plan", "privacy_visibility": "followers",
            "task_properties_definition": [
                {"name": "qa_private_sources", "string": "Sources", "type": "char"},
            ],
        })
        task = self._task(project_id=project.id, task_properties=[
            {"name": "qa_private_sources", "value": "Copper heliograph"},
        ])
        self.assertFalse(project.with_user(self.actor).has_access("read"))
        values = task.with_user(self.actor).read(["task_properties"])[0]["task_properties"]
        self.assertIn("Copper heliograph", [item.get("value") for item in values])
        self.assertEqual(self._matches("Copper heliograph"), task)

    def test_assignee_visible_activity_does_not_expose_unreadable_task(self):
        project = self.env["project.project"].create({
            "name": "[QA Search] Private plan", "privacy_visibility": "followers",
        })
        task = self._task(project_id=project.id, user_ids=[Command.clear()])
        activity = self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("project.task"),
            "res_id": task.id, "user_id": self.actor.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "note": "<p>Teal heliograph instructions</p>",
        })
        task.message_unsubscribe(partner_ids=self.actor.partner_id.ids)
        self.assertTrue(activity.with_user(self.actor).has_access("read"))
        self.assertFalse(task.with_user(self.actor).has_access("read"))
        self.assertFalse(self._matches("Teal heliograph"))

    def test_selected_company_limits_description_and_activity_matches(self):
        company = self.env["res.company"].create({"name": "[QA Search test] Company B"})
        self.actor.company_ids = [Command.link(company.id)]
        project = self.env["project.project"].with_company(company).create({
            "name": "[QA Search] Other company plan", "company_id": company.id,
        })
        task = self._task(project_id=project.id, description="<p>Silver heliograph</p>")
        self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("project.task"),
            "res_id": task.id, "user_id": self.actor.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "note": "<p>Silver heliograph</p>",
        })
        Task = self.env["project.task"].with_user(self.actor)
        self.assertFalse(Task.with_context(allowed_company_ids=[self.env.company.id]).search([
            ("usl_search_text", "ilike", "Silver heliograph"),
        ]))
        self.assertEqual(Task.with_context(allowed_company_ids=[company.id]).search([
            ("usl_search_text", "ilike", "Silver heliograph"),
        ]), task)

    def test_credentials_and_signature_properties_are_not_search_content(self):
        self.project.task_properties_definition = [
            {"name": "qa_opaque", "string": "API Token", "type": "char"},
        ]
        self._task(access_token="Quartz heliograph", task_properties=[
            {"name": "qa_opaque", "value": "Quartz heliograph"},
        ])
        self.assertFalse(self._matches("Quartz heliograph"))

    def test_native_field_group_denial_prevents_content_match(self):
        task = self._task(description="<p>Coral heliograph</p>")
        restricted = new_test_user(
            self.env, login="qa-content-field-user", groups="project.group_project_user",
        )
        with patch.object(self.env["project.task"]._fields["description"], "groups", "base.group_system"):
            with self.assertRaises(AccessError):
                task.with_user(restricted).read(["description"])
            self.assertFalse(self.env["project.task"].with_user(restricted).search([
                ("usl_search_text", "ilike", "Coral heliograph"),
            ]))
        self.assertEqual(self._matches("Coral heliograph"), task)

    def test_selection_label_and_task_id_are_searchable(self):
        task = self._task(priority="3")
        self.assertIn(task, self._matches("Urgent"))
        self.assertIn(task, self._matches(str(task.id)))

    def test_ordinary_description_can_discuss_credentials(self):
        task = self._task(description="<p>API token rollout instructions for synthetic QA</p>")
        self.assertEqual(self._matches("API token rollout"), task)

    def test_related_activity_type_label_is_searchable(self):
        task = self._task()
        kind = self.env["mail.activity.type"].create({"name": "[QA Search] Ochre heliograph review"})
        self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("project.task"),
            "res_id": task.id, "user_id": self.actor.id,
            "activity_type_id": kind.id,
        })
        self.assertEqual(self._matches("Ochre heliograph"), task)

    def test_property_schema_cannot_borrow_another_projects_plain_label(self):
        public = self._task(task_properties=[{"name": "qa_sources", "value": "Ordinary"}])
        secret_project = self.env["project.project"].create({
            "name": "[QA Search] Credential schema", "company_id": self.env.company.id,
            "task_properties_definition": [{"name": "qa_sources", "string": "API Token", "type": "char"}],
        })
        secret = self._task(project_id=secret_project.id, task_properties=[{"name": "qa_sources", "value": "Quartz heliograph"}])
        self.assertNotEqual(public.project_id, secret.project_id)
        self.assertFalse(self._matches("Quartz heliograph"))

    def test_date_boundary_is_valid_search_input(self):
        self.assertFalse(self._matches("9999-12-31"))

    def test_native_property_tag_label_is_searchable(self):
        self.project.task_properties_definition = [{"name": "qa_tags", "string": "Topics", "type": "tags",
            "tags": [["qa_tag", "Lavender heliograph", 1]]}]
        task = self._task(task_properties=[{"name": "qa_tags", "value": ["qa_tag"]}])
        self.assertEqual(self._matches("Lavender heliograph"), task)

    def test_private_property_metadata_is_prefetched_as_a_cohort(self):
        projects = self.env["project.project"].create([
            {"name": f"[QA Search] Private schema {index}", "privacy_visibility": "followers",
             "task_properties_definition": [{"name": f"qa_schema_{index}", "string": "Sources", "type": "char"}]}
            for index in range(8)
        ])
        tasks = self.env["project.task"].create([
            {"name": "[QA Search] Cohort work", "project_id": project.id, "user_ids": [Command.link(self.actor.id)],
             "task_properties": [{"name": f"qa_schema_{index}", "value": "Copper heliograph"}]}
            for index, project in enumerate(projects)
        ])
        self.env.flush_all()
        self.env.invalidate_all()
        model = self.env["project.task"].with_user(self.actor)
        # Warm ordinary ACL/rule metadata, not task/property rows.
        model.fields_get()
        model.has_access("read")
        from ..models.content_search import _property_domains, _typed_values
        from odoo.tools.profiler import Profiler, SQLCollector
        sql = SQLCollector()
        with Profiler(collectors=[sql], db=False):
            clauses = _property_domains(model, "task_properties", "Copper heliograph", _typed_values(model, "Copper heliograph"), {})
        schema_queries = [entry for entry in sql.entries if '"project_project"."task_properties_definition"' in entry["query"]]
        self.assertEqual(len(schema_queries), 1)
        from odoo.fields import Domain
        self.assertEqual(model.search(Domain.OR(clauses)), tasks)
        self.assertTrue(all(not project.with_user(self.actor).has_access("read") for project in projects))

    def test_native_portal_name_sql_does_not_bypass_related_user_record_rules(self):
        company = self.env["res.company"].create({"name": "[QA Search] Assignee company B"})
        other = new_test_user(self.env, login="qa-related-assignee", name="Vermilion heliograph reviewer", groups="base.group_user",
            company_id=company.id, company_ids=[Command.set(company.ids)])
        other.partner_id.company_id = company
        self.env["ir.rule"].create({"name": "[QA Search] User record separation", "model_id": self.env["ir.model"]._get_id("res.users"),
            "domain_force": f"[('id', '!=', {other.id})]", "global": True})
        task = self._task(user_ids=[Command.link(self.actor.id), Command.link(other.id)])
        scoped = self.env["project.task"].with_user(self.actor).with_context(allowed_company_ids=self.env.company.ids)
        self.assertFalse(other.with_user(self.actor).with_context(allowed_company_ids=self.env.company.ids).has_access("read"))
        self.assertTrue(task.with_user(self.actor).has_access("read"))
        self.assertFalse(scoped.search([("usl_search_text", "ilike", "Vermilion heliograph")]))

    def test_public_assignee_identity_remains_searchable_across_default_companies(self):
        company = self.env["res.company"].create({"name": "[QA Search] Public identity company B"})
        other = new_test_user(self.env, login="qa-public-assignee", name="Vermilion heliograph reviewer", groups="base.group_user",
            company_id=company.id, company_ids=[Command.set(company.ids)])
        other.partner_id.company_id = company
        task = self._task(user_ids=[Command.link(self.actor.id), Command.link(other.id)])
        scoped = self.env["project.task"].with_user(self.actor).with_context(allowed_company_ids=self.env.company.ids)
        self.assertTrue(other.with_user(self.actor).has_access("read"))
        self.assertEqual(other.with_user(self.actor).read(["name"])[0]["name"], "Vermilion heliograph reviewer")
        self.assertEqual(scoped.search([("usl_search_text", "ilike", "Vermilion heliograph")]), task)

    def test_readable_description_matches_even_when_same_assignee_is_denied(self):
        other = new_test_user(self.env, login="qa-denied-assignee-text", name="Vermilion heliograph reviewer", groups="base.group_user")
        self.env["ir.rule"].create({"name": "[QA Search] Other user separation", "model_id": self.env["ir.model"]._get_id("res.users"),
            "domain_force": f"[('id', '!=', {other.id})]", "global": True})
        task = self._task(user_ids=[Command.link(self.actor.id), Command.link(other.id)], description="<p>Vermilion heliograph instructions</p>")
        self.assertFalse(other.with_user(self.actor).has_access("read"))
        self.assertEqual(self._matches("Vermilion heliograph"), task)

    def test_bounded_task_page_does_not_fetch_every_matching_activity(self):
        task = self._task()
        self.env['mail.activity'].create([{
            'res_model_id': self.env['ir.model']._get_id('project.task'),
            'res_id': task.id, 'user_id': self.actor.id,
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'summary': 'Bounded amber activity evidence',
        } for _index in range(120)])
        self.env.flush_all()
        # Warm model metadata; observe real database fetches, without replacing
        # the query or result. A one-record page must not first fetch 120 matches.
        self._matches('metadata warmup with no matches')
        batches = []
        fetchall = self.env.cr.fetchall

        def observe():
            rows = fetchall()
            batches.append(len(rows))
            return rows

        with patch.object(self.env.cr, 'fetchall', observe):
            actual = self.env['project.task'].with_user(self.actor).search([
                ('usl_search_text', 'ilike', 'Bounded amber activity evidence'),
            ], limit=1)
        self.assertEqual(actual, task)
        self.assertLess(max(batches, default=0), 120,
                        'A bounded task page fetched the complete activity population')

    def test_shared_property_schema_does_not_repeat_the_database_predicate(self):
        from ..models.content_search import readable_content_domain
        Task = self.env['project.task'].with_user(self.actor)
        definition = [{'name': f'qa_shared_{i}', 'string': f'QA shared {i}', 'type': 'char'}
                      for i in range(8)]
        self.project.task_properties_definition = definition
        first = self._task(task_properties=[{'name': 'qa_shared_0', 'value': 'Bounded violet evidence'}])
        self.env.flush_all()

        def search_statement():
            # This is the actual native SELECT passed to PostgreSQL, including
            # current access rules, not a comparison of domain implementation.
            query = Task._search(readable_content_domain(Task, 'Bounded violet evidence'), limit=5)
            sql = query.select()
            self.env.execute_query(sql)
            return len(sql._sql_tuple[0])

        initial = search_statement()
        projects = self.env['project.project'].create([{
            'name': f'[QA Search] Shared schema {i}',
            'company_id': self.env.company.id,
            'task_properties_definition': definition,
        } for i in range(30)])
        self.env['project.task'].create([{
            'name': '[QA Search] Shared schema task', 'project_id': project.id,
            'user_ids': [Command.link(self.actor.id)],
            'task_properties': [{'name': 'qa_shared_0', 'value': 'Bounded violet evidence'}],
        } for project in projects])
        self.env.flush_all()
        self.assertLess(search_statement() - initial, 4096,
                        'Identical property schemas expanded the SQL once per project')
        self.assertIn(first, self._matches('Bounded violet evidence'))

    def test_shared_property_keys_keep_each_owners_selection_meaning(self):
        self.project.task_properties_definition = [{
            'name': 'qa_shared_choice', 'string': 'Topic', 'type': 'selection',
            'selection': [['same_key', 'Amber permission evidence']],
        }]
        public = self._task(task_properties=[{'name': 'qa_shared_choice', 'value': 'same_key'}])
        other = self.env['project.project'].create({
            'name': '[QA Search] Different schema meaning', 'company_id': self.env.company.id,
            'task_properties_definition': [{
                'name': 'qa_shared_choice', 'string': 'Topic', 'type': 'selection',
                'selection': [['same_key', 'Violet alternative']],
            }],
        })
        self._task(project_id=other.id, task_properties=[{'name': 'qa_shared_choice', 'value': 'same_key'}])
        self.assertEqual(self._matches('Amber permission evidence'), public)

    def test_content_execution_disables_jit_and_restores_native_setting(self):
        """Observe the setting at real Task SQL, including count and grouping."""
        from odoo.fields import Domain

        task = self._task(description='Amber execution boundary')
        self.env.flush_all()
        self.env.cr.execute("SELECT set_config('jit', 'on', true)")
        execute = self.env.cr.execute
        settings = []
        expected = ["off"]

        def observe(query, *args, **kwargs):
            text = query._sql_tuple[0] if hasattr(query, '_sql_tuple') else query
            if isinstance(text, str) and text.lstrip().startswith('SELECT') and 'project_task' in text:
                execute('SHOW jit')
                settings.append(self.env.cr.fetchone()[0])
                self.assertEqual(settings[-1], expected[0], 'Task SQL must execute with the scoped planner setting')
            return execute(query, *args, **kwargs)

        Task = self.env['project.task'].with_user(self.actor)
        content = Domain('usl_search_text', 'ilike', 'Amber execution boundary')
        domains = [content, ~~content, content & Domain('id', '=', task.id),
                   ~Domain('usl_search_text', 'not ilike', 'Amber execution boundary')]
        with patch.object(self.env.cr, 'execute', observe):
            for domain in domains:
                self.assertEqual(Task.search_fetch(domain, ['name'], limit=1), task)
                self.assertEqual(Task.search_count(domain), 1)
                self.assertEqual(Task._read_group(domain, aggregates=['__count']), [(1,)])
                execute('SHOW jit')
                self.assertEqual(self.env.cr.fetchone()[0], 'on')
        self.assertTrue(settings)
        self.assertEqual(set(settings), {'off'})
        settings.clear()
        expected[0] = "on"
        with patch.object(self.env.cr, 'execute', observe):
            Task.search_fetch([('id', '=', task.id)], ['name'])
        self.assertEqual(set(settings), {'on'})
        self.env.cr.execute("SELECT set_config('jit', 'off', true)")
        self.assertEqual(Task.search_count(content), 1)
        self.env.cr.execute('SHOW jit')
        self.assertEqual(self.env.cr.fetchone()[0], 'off')

    def test_native_pivot_content_execution_and_nested_schema_scope(self):
        task = self._task(description='Amber pivot boundary')
        self.env.flush_all()
        cr = self.env.cr
        cr.execute("SELECT set_config('jit', 'on', true)")
        execute = cr.execute
        grouping_settings = []
        schema_settings = []

        def observe(query, *args, **kwargs):
            text = query._sql_tuple[0] if hasattr(query, '_sql_tuple') else query
            if isinstance(text, str) and text.lstrip().startswith('SELECT') and 'project_task' in text:
                execute('SHOW jit')
                setting = cr.fetchone()[0]
                # Stop before executing unsafe SQL on a causal reversion.
                if 'GROUPING SETS' in text:
                    self.assertEqual(setting, 'off')
                    grouping_settings.append(setting)
                elif 'GROUP BY' in text:
                    schema_settings.append(setting)
            return execute(query, *args, **kwargs)

        Task = self.env['project.task'].with_user(self.actor)
        domain = [('usl_search_text', 'ilike', 'Amber pivot boundary')]
        with patch.object(cr, 'execute', observe):
            groups = Task.formatted_read_grouping_sets(domain, [[], ['project_id']], ['__count'])
        self.assertEqual(groups[0][0]['__count'], 1)
        self.assertEqual(len(groups[1]), 1)
        self.assertEqual(groups[1][0]['__count'], 1)
        self.assertEqual(groups[1][0]['project_id'][0], task.project_id.id)
        self.assertTrue(grouping_settings, 'Exercise the actual native Pivot GROUPING SETS query')
        self.assertTrue(schema_settings, 'Schema aggregation must stay inside the outer off scope')
        self.assertEqual(set(schema_settings), {'off'})
        cr.execute('SHOW jit')
        self.assertEqual(cr.fetchone()[0], 'on')

    def test_content_execution_transaction_and_exception_boundaries(self):
        import psycopg2
        from odoo.addons.usl_project.models.content_search import task_content_execution

        content = [('usl_search_text', 'ilike', 'Amber')]
        cr = self.env.cr
        Task = self.env['project.task']
        cr.execute("SELECT set_config('jit', 'on', true)")
        with self.assertRaisesRegex(ValueError, 'healthy failure'):
            with task_content_execution(Task, content):
                with task_content_execution(Task, content):
                    cr.execute('SHOW jit')
                    self.assertEqual(cr.fetchone()[0], 'off')
                raise ValueError('healthy failure')
        cr.execute('SHOW jit')
        self.assertEqual(cr.fetchone()[0], 'on')
        # The caller owns rollback: the original SQL exception survives, and
        # rollback to its savepoint restores settings along with database state.
        with self.assertRaises(psycopg2.errors.DivisionByZero):
            with cr.savepoint():
                with task_content_execution(Task, content):
                    cr.execute('SELECT 1 / 0')
        cr.execute('SHOW jit')
        self.assertEqual(cr.fetchone()[0], 'on')
        for domain in ([], [('name', 'ilike', 'Amber')]):
            with task_content_execution(Task, domain):
                cr.execute('SHOW jit')
                self.assertEqual(cr.fetchone()[0], 'on')

    def test_content_execution_preserves_pending_orm_write(self):
        task = self._task(description='Earlier pending value')
        self.env.flush_all()
        task.description = 'Amber pending flush'
        self.assertEqual(self._matches('Amber pending flush'), task)
        self.env.cr.execute('SELECT description FROM project_task WHERE id = %s', (task.id,))
        self.assertIn('Amber pending flush', self.env.cr.fetchone()[0])

    def test_content_execution_readonly_and_ended_transaction(self):
        from odoo import api, SUPERUSER_ID
        from odoo.addons.usl_project.models.content_search import task_content_execution

        domain = [('usl_search_text', 'ilike', '')]
        # An independent cursor avoids committing or closing the test's own
        # transaction. Local planner settings are legal on a readonly cursor.
        for readonly in (False, True):
            with self.registry.cursor(readonly=readonly) as cr:
                model = api.Environment(cr, SUPERUSER_ID, {})['project.task']
                cr.execute("SELECT set_config('jit', 'on', true)")
                with task_content_execution(model, domain):
                    cr.execute('SHOW jit')
                    self.assertEqual(cr.fetchone()[0], 'off')
                cr.execute('SHOW jit')
                self.assertEqual(cr.fetchone()[0], 'on')
                with task_content_execution(model, domain):
                    cr.rollback()
                cr.execute('SHOW jit')
                self.assertEqual(cr.fetchone()[0], 'on')
                with task_content_execution(model, domain):
                    cr.close()

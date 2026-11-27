"""Explicit task suggestions through the native reader and the shared index."""
from unittest.mock import patch

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'usl_task_semantics')
class TestTaskSemantics(TransactionCase):
    def test_unconfigured_search_keeps_existing_task_results(self):
        service = self.env['usl.home.service']
        task = self.env['project.task'].create({'name': 'Amber workshop fixture'})
        with patch.dict('os.environ', {'USL_TASK_SEMANTIC_SEARCH': '0'}):
            meaning = service.env['project.task'].search_by_meaning('Amber workshop fixture')
            self.assertEqual(meaning['status'], 'disabled')
            self.assertFalse(meaning['group']['records'])
            lexical = service.search_records('Amber workshop fixture', categories=['tasks'])
            self.assertIn(task.id, [row['id'] for row in lexical['groups'][0]['records']])

    def test_home_configuration_says_once_whether_meaning_search_is_offered(self):
        from odoo.addons.usl_home.models import task_semantics as semantics
        service = self.env['usl.home.service']
        with patch.object(semantics, '_configured_model', return_value=None):
            self.assertIs(service.get_configuration()['task_meaning_search'], False)
        with patch.object(semantics, '_configured_model', return_value=('http://fixture.invalid', 'fixture', 'a' * 64)):
            self.assertIs(service.get_configuration()['task_meaning_search'], True)

    def _rank_fixture(self, extra_companies=()):
        from odoo.addons.usl_home.models import task_semantics as semantics
        Task = self.env['project.task']
        if not Task._usl_has_vector_index():
            self.skipTest('pgvector is optional; run this case on the vector-enabled fixture')
        company = self.env['res.company'].create({'name': 'Blue Lantern Lab'})
        user = new_test_user(self.env, login='qa-task-meaning', groups='project.group_project_user',
                             company_id=company.id, company_ids=[Command.set([company.id, *extra_companies])],
                             context={"no_reset_password": True})
        project = self.env['project.project'].create({'name': 'Lantern Workshop', 'company_id': company.id})
        tasks = Task.with_context(tracking_disable=True).create([
            {'name': title, 'description': text, 'project_id': project.id, 'user_ids': [Command.link(user.id)]}
            for title, text in [('Parcel trail', 'The delivery company lost our parcel.'),
                                ('Workshop audio', 'Le microphone du studio est silencieux.')]])
        Task._cron_usl_index_task_content()
        # A fixture index can contain unrelated documents. Limit the background actor to this company.
        worker = Task.with_user(user).with_context(allowed_company_ids=company.ids)
        service = self.env['usl.home.service'].with_user(user).with_context(allowed_company_ids=company.ids)
        def embed(model, texts, **options):
            return [[1.0, 0.0, *([0.0] * 1022)] if any(term in text for term in ('parcel', 'courier'))
                    else [0.8, 0.6, *([0.0] * 1022)] for text in texts]
        self.enterContext(patch.object(semantics, '_configured_model', return_value=('http://fixture.invalid', 'fixture', 'a' * 64)))
        self.enterContext(patch.object(semantics, '_embeddings', side_effect=embed))
        worker._cron_usl_embed_task_descriptions()
        return tasks, service

    def test_title_only_task_is_found_by_meaning_and_text_has_both_parts(self):
        from odoo.addons.usl_home.models import task_semantics as semantics
        tasks, service = self._rank_fixture()
        project = tasks[0].project_id
        actor = service.env.user
        title_only = self.env['project.task'].with_context(tracking_disable=True).create({
            'name': 'Courier trace request', 'project_id': project.id, 'user_ids': [Command.link(actor.id)],
        })
        self.env['project.task']._cron_usl_index_task_content()
        seen = []
        def embed(model, texts, **options):
            seen.extend(texts)
            return [[1.0, 0.0, *([0.0] * 1022)] if any(term in text for term in ('parcel', 'courier', 'Courier'))
                    else [0.8, 0.6, *([0.0] * 1022)] for text in texts]
        with patch.object(semantics, '_embeddings', side_effect=embed):
            service.env['project.task']._cron_usl_embed_task_descriptions()
            rows = service.env['project.task'].search_by_meaning('who can trace the courier?', limit=5)['group']['records']
        self.assertIn(title_only.id, [row['id'] for row in rows])
        self.assertIn('Courier trace request', seen)

    def test_embedding_text_joins_title_and_description(self):
        text = self.env['project.task']._usl_embedding_text('  Parcel   trail ', '<p>The delivery company lost our parcel.</p>')
        self.assertEqual(text, 'Parcel trail\n\nThe delivery company lost our parcel.')
        self.assertEqual(self.env['project.task']._usl_embedding_text('Only a title', False), 'Only a title')

    def test_paraphrase_and_revocation_before_limit(self):
        from odoo.addons.usl_home.models import task_semantics as semantics
        tasks, service = self._rank_fixture()
        def first():
            return service.env['project.task'].search_by_meaning('Who can trace the courier?', limit=1)['group']['records']
        self.assertEqual([row['id'] for row in first()], [tasks[0].id])
        self.assertIn('parcel', first()[0]['excerpt'])
        self.assertEqual(service.get_search_result_action('project.task', tasks[0].id)['res_id'], tasks[0].id)
        self.env['ir.rule'].create({'name': 'Only second task remains available', 'model_id': self.env['ir.model']._get_id('project.task'),
                                    'domain_force': repr([('id', '=', tasks[1].id)]), 'global': True})
        self.assertEqual([row['id'] for row in first()], [tasks[1].id])
        with patch.object(self.env['project.task']._fields['description'], 'groups', 'base.group_system'), patch.object(semantics, '_embeddings') as call:
            field = self.env['project.task']._fields['description']
            service.with_context(active_test=False).env._field_access_memo.discard(field)
            self.assertFalse(first())
            call.assert_not_called()

    def test_pending_edit_removes_old_match_and_pinned_model(self):
        from odoo.addons.usl_home.models import task_semantics as semantics
        tasks, service = self._rank_fixture()
        def rows():
            return service.env['project.task'].search_by_meaning('Who can trace the courier?')['group']['records']
        tasks[0].description = '<p>Replace the lamps.</p>'
        self.assertNotIn(tasks[0].id, [row['id'] for row in rows()])
        self.env['project.task']._cron_usl_index_task_content()
        self.assertNotIn(tasks[0].id, [row['id'] for row in rows()])
        with patch.object(semantics, '_configured_model', return_value=('http://fixture.invalid', 'new', 'b' * 64)):
            self.assertFalse(rows())
        with patch.object(semantics, '_embeddings', return_value=None):
            self.assertEqual(service.env['project.task'].search_by_meaning('Who can trace the courier?')['status'], 'unavailable')

    def test_attestation_is_remembered_so_a_second_query_makes_one_call(self):
        from odoo.addons.usl_home.models import task_semantics as semantics
        semantics._ATTESTED.clear()
        self.addCleanup(semantics._ATTESTED.clear)
        target = ('http://fixture.invalid', 'fixture', 'a' * 64)
        vector = [1.0] + [0.0] * 1023
        with patch('requests.Session') as session:
            client = session.return_value.__enter__.return_value
            client.get.return_value.status_code = 200
            client.get.return_value.json.return_value = {'models': [{'name': 'fixture', 'digest': 'a' * 64}]}
            client.post.return_value.status_code = 200
            client.post.return_value.json.return_value = {'model': 'fixture', 'embeddings': [vector]}
            self.assertEqual(semantics._embeddings(target, ['first'], interactive=True), [vector])
            self.assertEqual((client.get.call_count, client.post.call_count), (1, 1))
            self.assertEqual(semantics._embeddings(target, ['second'], interactive=True), [vector])
            self.assertEqual((client.get.call_count, client.post.call_count), (1, 2))
            # Interactive calls are bounded by the half-second connect and 1.5 s total budget.
            for call in client.post.call_args_list:
                connect, read = call.kwargs['timeout']
                self.assertLessEqual(connect, 1)
                self.assertLessEqual(read, semantics.INTERACTIVE_BUDGET)
            # An answer from another model withdraws the attestation.
            client.post.return_value.json.return_value = {'model': 'other', 'embeddings': [vector]}
            self.assertIsNone(semantics._embeddings(target, ['third'], interactive=True))
            self.assertEqual(semantics._embeddings(target, ['fourth'], interactive=True), None)
            self.assertEqual(client.get.call_count, 2)

    def test_gateway_rejects_drift_before_sending_any_text(self):
        from odoo.addons.usl_home.models import task_semantics as semantics
        from odoo.addons.usl_home.models.task_semantics import _embeddings
        semantics._ATTESTED.clear()
        self.addCleanup(semantics._ATTESTED.clear)
        with patch('requests.Session') as session:
            client = session.return_value.__enter__.return_value
            response = client.get.return_value
            response.status_code = 200
            response.json.return_value = {'models': [{'name': 'fixture', 'digest': 'b' * 64}]}
            target = ('http://fixture.invalid', 'fixture', 'a' * 64)
            self.assertIsNone(_embeddings(target, ['Invented question']))
            client.post.assert_not_called()
            response.json.return_value['models'][0]['digest'] = 'a' * 64
            client.post.return_value.status_code = 200
            for invalid in [[], [0] * 1024, [float('nan')] * 1024, [1] * 1023, [True] * 1024]:
                client.post.return_value.json.return_value = {'model': 'fixture', 'embeddings': [invalid]}
                self.assertIsNone(_embeddings(target, ['Invented question']))
            vector = [1.0] + [0.0] * 1023
            client.post.return_value.json.return_value = {'model': 'fixture', 'embeddings': [vector]}
            self.assertEqual(_embeddings(target, ['Invented question']), [vector])

    def test_extension_is_optional_and_never_calls_model_while_unavailable(self):
        from odoo.addons.usl_home.models import task_semantics as semantics
        Task = self.env['project.task']
        service = self.env['usl.home.service']
        # Also cover the native absent-extension path on the plain PostgreSQL fixture.
        with patch.object(semantics, '_configured_model', return_value=('http://fixture.invalid', 'fixture', 'a' * 64)), patch.object(semantics, '_embeddings') as call:
            if Task._usl_has_vector_index():
                with patch.object(type(Task), '_usl_has_vector_index', return_value=False):
                    self.assertEqual(service.env['project.task'].search_by_meaning('studio')['status'], 'disabled')
            else:
                self.assertEqual(service.env['project.task'].search_by_meaning('studio')['status'], 'disabled')
                Task._cron_usl_embed_task_descriptions()
            call.assert_not_called()

    def test_other_selected_company_keeps_its_own_semantic_results(self):
        company = self.env['res.company'].create({'name': 'Amber Workshop Lab'})
        tasks, service = self._rank_fixture(extra_companies=company.ids)
        actor = service.env.user
        project = self.env['project.project'].create({'name': 'Amber Workshop', 'company_id': company.id})
        other = self.env['project.task'].with_context(tracking_disable=True).create({
            'name': 'Amber parcel', 'description': 'Another parcel belongs to this workshop.', 'project_id': project.id,
            'user_ids': [Command.link(actor.id)],
        })
        self.env['project.task']._cron_usl_index_task_content()
        self.env['project.task'].with_user(actor).with_context(allowed_company_ids=company.ids)._cron_usl_embed_task_descriptions()
        first = service.env['project.task'].search_by_meaning('courier')['group']['records']
        second = service.env['project.task'].with_context(allowed_company_ids=company.ids).search_by_meaning('courier')['group']['records']
        self.assertIn(tasks[0].id, [r['id'] for r in first])
        self.assertNotIn(other.id, [r['id'] for r in first])
        self.assertIn(other.id, [r['id'] for r in second])
        self.assertNotIn(tasks[0].id, [r['id'] for r in second])

    def test_one_clear_result_and_no_unrelated_filler(self):
        from odoo.addons.usl_home.models import task_semantics as semantics
        tasks, service = self._rank_fixture()
        group = service.env['project.task'].search_by_meaning('trace the courier', limit=5)['group']
        self.assertEqual([row['id'] for row in group['records']], [tasks[0].id])
        orthogonal = [0.0, 0.0, 1.0, *([0.0] * 1021)]
        with patch.object(semantics, '_embeddings', return_value=[orthogonal]):
            result = service.env['project.task'].search_by_meaning('Museum pottery')
            self.assertEqual(result['status'], 'empty')
            self.assertFalse(result['group']['records'])

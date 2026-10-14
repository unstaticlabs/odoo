from odoo import Command, fields
from odoo.exceptions import AccessError, RedirectWarning, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestTaskSwimlane(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.actor = new_test_user(cls.env, login='qa_swimlane_actor', groups='project.group_project_user', context={'no_reset_password': True, 'usl_governed_identity_provisioning': True})
        cls.users = [new_test_user(cls.env, login=f'qa_swimlane_member_{n}', groups='project.group_project_user', context={'no_reset_password': True, 'usl_governed_identity_provisioning': True}) for n in range(3)]
        cls.project = cls.env['project.project'].create({'name': '[QA] Swimlane Project', 'privacy_visibility': 'employees'})
        cls.stages = cls.env['project.task.type'].create([{'name': '[QA] Ready', 'project_ids': [Command.link(cls.project.id)]}, {'name': '[QA] Doing', 'project_ids': [Command.link(cls.project.id)]}])

    def task(self, **extra):
        return self.env['project.task'].create({'name': '[QA] Swimlane Task', 'project_id': self.project.id, 'stage_id': self.stages[0].id, 'priority': '0', **extra}).with_user(self.actor)

    def move(self, task, **extra):
        return task.action_move_kanban_cell([{'field': 'stage_id', 'source': self.stages[0].id, 'target': self.stages[1].id}, {'field': 'priority', 'source': '0', 'target': '1'}], **extra)

    def test_two_dimensions_reach_destination(self):
        task = self.task()
        self.move(task)
        self.assertEqual((task.stage_id, task.priority), (self.stages[1], '1'))

    def test_invalid_second_destination_writes_neither_dimension(self):
        task = self.task()
        with self.assertRaises(ValidationError):
            task.action_move_kanban_cell([{'field': 'stage_id', 'source': self.stages[0].id, 'target': self.stages[1].id}, {'field': 'priority', 'source': '0', 'target': 'invalid'}])
        self.assertEqual((task.stage_id, task.priority), (self.stages[0], '0'))

    def test_membership_transfer_preserves_unrelated_assignee(self):
        a, b, c = self.users
        task = self.task(user_ids=[Command.set((a | b).ids)])
        task.action_move_kanban_cell([{'field': 'user_ids', 'source': a.id, 'target': c.id}])
        self.assertEqual(task.user_ids, b | c)

    def test_unassigned_cannot_clear_other_membership(self):
        a, b, _c = self.users
        task = self.task(user_ids=[Command.set((a | b).ids)])
        with self.assertRaises(RedirectWarning) as redirect:
            task.action_move_kanban_cell([{'field': 'user_ids', 'source': a.id, 'target': False}])
        self.assertEqual(task.user_ids, a | b)
        self.assertEqual(redirect.exception.args[1], {'type': 'ir.actions.act_window', 'res_model': 'project.task', 'res_id': task.id, 'views': [(False, 'form')], 'target': 'current'})

    def test_unassigned_and_singleton_transfer(self):
        a = self.users[0]
        task = self.task(user_ids=[Command.clear()])
        task.action_move_kanban_cell([{'field': 'user_ids', 'source': False, 'target': a.id}])
        self.assertEqual(task.user_ids, a)
        task.action_move_kanban_cell([{'field': 'user_ids', 'source': a.id, 'target': False}])
        self.assertFalse(task.user_ids)

    def test_stale_membership_cannot_overwrite_new_stage(self):
        task = self.task(stage_id=self.stages[1].id)
        with self.assertRaises(UserError):
            self.move(task)
        self.assertEqual(task.stage_id, self.stages[1])

    def test_destination_already_member_preserves_it_and_other_members(self):
        a, b, c = self.users
        task = self.task(user_ids=[Command.set((a | b | c).ids)])
        task.action_move_kanban_cell([{'field': 'user_ids', 'source': a.id, 'target': c.id}])
        self.assertEqual(task.user_ids, b | c)

    def test_two_many_to_many_axes_transfer_only_source_memberships(self):
        a, b, c = self.users
        tags = self.env['project.tags'].create([{'name': '[QA] Tag A'}, {'name': '[QA] Tag B'}, {'name': '[QA] Tag C'}])
        task = self.task(user_ids=[Command.set((a | b).ids)], tag_ids=[Command.set(tags[:2].ids)])
        task.action_move_kanban_cell([{'field': 'user_ids', 'source': a.id, 'target': c.id}, {'field': 'tag_ids', 'source': tags[0].id, 'target': tags[2].id}])
        self.assertEqual(task.user_ids, b | c)
        self.assertEqual(task.tag_ids, tags[1:])

    def test_unchanged_date_lane_allows_stage_move_without_inventing_day(self):
        task = self.task(date_deadline='2026-10-23 10:30:00')
        task.action_move_kanban_cell([{'field': 'stage_id', 'source': self.stages[0].id, 'target': self.stages[1].id}, {'field': 'date_deadline:month', 'source': '2026-10-01 00:00:00', 'target': '2026-10-01 00:00:00'}])
        self.assertEqual(task.stage_id, self.stages[1])
        self.assertEqual(fields.Datetime.to_string(task.date_deadline), '2026-10-23 10:30:00')

    def test_changed_date_lane_writes_neither_axis(self):
        task = self.task(date_deadline='2026-10-23 10:30:00')
        with self.assertRaises(UserError):
            task.action_move_kanban_cell([{'field': 'stage_id', 'source': self.stages[0].id, 'target': self.stages[1].id}, {'field': 'date_deadline:month', 'source': '2026-10-01 00:00:00', 'target': '2026-11-01 00:00:00'}])
        self.assertEqual(task.stage_id, self.stages[0])
        self.assertEqual(fields.Datetime.to_string(task.date_deadline), '2026-10-23 10:30:00')

    def test_readonly_unchanged_lane_allows_native_stage_move(self):
        task = self.task()
        task.action_move_kanban_cell([{'field': 'stage_id', 'source': self.stages[0].id, 'target': self.stages[1].id}, {'field': 'create_date:month', 'source': fields.Datetime.to_string(task.create_date.replace(day=1, hour=0, minute=0, second=0)), 'target': fields.Datetime.to_string(task.create_date.replace(day=1, hour=0, minute=0, second=0))}])
        self.assertEqual(task.stage_id, self.stages[1])

    def test_missing_field_and_forged_source_are_rejected_before_write(self):
        task = self.task()
        for item in [{'field': 'not_a_field', 'source': False, 'target': 'x'}, {'field': 'priority', 'source': '1', 'target': '0'}]:
            with self.assertRaises((RedirectWarning, UserError, ValidationError)):
                task.action_move_kanban_cell([item])
        self.assertEqual((task.stage_id, task.priority), (self.stages[0], '0'))

    def test_observed_write_date_is_checked_before_mutation(self):
        task = self.task()
        with self.assertRaises(UserError):
            self.move(task, observed_write_date='2000-01-01 00:00:00')
        self.assertEqual(task.stage_id, self.stages[0])

    def test_native_recursive_parent_constraint_rolls_back_both_dimensions(self):
        task = self.task()
        with self.assertRaises(UserError), self.cr.savepoint():
            task.action_move_kanban_cell([{'field': 'stage_id', 'source': self.stages[0].id, 'target': self.stages[1].id}, {'field': 'parent_id', 'source': False, 'target': task.id}])
        task.invalidate_recordset()
        self.assertEqual(task.stage_id, self.stages[0])
        self.assertFalse(task.parent_id)

    def test_native_write_rule_denies_move_without_mutation(self):
        task = self.task()
        self.env['ir.rule'].create({'name': '[QA] Read-only tasks', 'model_id': self.env['ir.model']._get_id('project.task'), 'domain_force': "[('id', '=', False)]", 'perm_read': False, 'perm_write': True, 'perm_create': False, 'perm_unlink': False})
        self.assertTrue(task.has_access('read'))
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.move(task)
        self.assertEqual((task.stage_id, task.priority), (self.stages[0], '0'))

    def test_expanded_empty_stage_cannot_validate_a_forged_source_membership(self):
        task = self.task().with_context(default_project_id=self.project.id, project_kanban=True)
        with self.assertRaises(UserError):
            task.action_move_kanban_cell([{'field': 'stage_id', 'source': self.stages[1].id, 'target': self.stages[0].id}, {'field': 'priority', 'source': '0', 'target': '1'}])
        self.assertEqual(task.priority, '0')

    def test_company_scope_denies_hidden_task_and_related_destination(self):
        from odoo.exceptions import AccessError
        company = self.env['res.company'].create({'name': '[QA] Other company'})
        self.actor.company_ids = [Command.link(company.id)]
        other_project = self.env['project.project'].with_company(company).create({'name': '[QA] Other board', 'company_id': company.id, 'privacy_visibility': 'employees'})
        other_task = self.env['project.task'].with_company(company).create({'name': '[QA] Other task', 'project_id': other_project.id})
        with self.assertRaises(AccessError):
            other_task.with_user(self.actor).with_context(allowed_company_ids=[self.env.company.id]).action_move_kanban_cell([{'field': 'priority', 'source': '0', 'target': '1'}])
        task = self.task().with_context(allowed_company_ids=[self.env.company.id])
        with self.assertRaises(UserError):
            task.action_move_kanban_cell([{'field': 'project_id', 'source': self.project.id, 'target': other_project.id}, {'field': 'priority', 'source': '0', 'target': '1'}])
        self.assertEqual((task.project_id, task.priority), (self.project, '0'))

    def test_personal_stage_moves_use_native_alias_without_a_second_stage_system(self):
        personal = self.env['project.task.type'].create([{'name': '[QA] Personal ready', 'user_id': self.actor.id}, {'name': '[QA] Personal next', 'user_id': self.actor.id}])
        task = self.env['project.task'].with_user(self.actor).create({'name': '[QA] Personal task', 'project_id': self.project.id, 'user_ids': [Command.link(self.actor.id)], 'personal_stage_type_id': personal[0].id})
        self.assertEqual(task.personal_stage_type_id, personal[0])
        task.action_move_kanban_cell([{'field': 'personal_stage_type_id', 'source': personal[0].id, 'target': personal[1].id}, {'field': 'priority', 'source': '0', 'target': '1'}])
        self.assertEqual(task.personal_stage_type_id, personal[1])
        self.assertEqual(task.priority, '1')

    def test_native_paged_groups_honor_explicit_leaf_opening_beyond_auto_budget(self):
        tasks = self.env['project.task'].create([{'name': f'[QA] Page task {n:02}', 'project_id': self.project.id, 'stage_id': self.stages[0].id} for n in range(85)])
        model = self.env['project.task'].with_user(self.actor).with_context(default_project_id=self.project.id, project_kanban=True)
        domain = [('id', 'in', tasks.ids)]
        kwargs = {'limit': 80, 'auto_unfold': True, 'unfold_read_default_limit': 80, 'unfold_read_specification': {'name': {}}}
        first = model.web_read_group(domain, ['stage_id', 'name'], ['__count'], **kwargs)
        column = next(g for g in first['groups'] if g['stage_id'][0] == self.stages[0].id)
        self.assertEqual(column['__groups']['length'], 85)
        self.assertEqual(len(column['__groups']['groups']), 80)
        opening = [{'value': self.stages[0].id, 'folded': False, 'limit': 160, 'offset': 0, 'groups': [{'value': task.name, 'folded': n not in [*range(10), *range(80, 85)], 'limit': 40, 'offset': 0} for n, task in enumerate(tasks)]}]
        final = model.web_read_group(domain, ['stage_id', 'name'], ['__count'], **{**kwargs, 'unfold_read_default_limit': 40, 'opening_info': opening})
        column = next(g for g in final['groups'] if g['stage_id'][0] == self.stages[0].id)
        self.assertEqual(len(column['__groups']['groups']), 85)
        visible = {record['id'] for group in column['__groups']['groups'] for record in group.get('__records', [])}
        self.assertEqual(visible, set(tasks[:10].ids + tasks[80:].ids))

    def test_readable_property_lane_keeps_unrelated_values_when_stage_changes(self):
        self.project.task_properties_definition = [{'name': 'qa_lane', 'string': 'Lane', 'type': 'selection', 'selection': [('a', 'A'), ('b', 'B')]}, {'name': 'qa_notes', 'string': 'Notes', 'type': 'char'}]
        task = self.task(task_properties=[{'name': 'qa_lane', 'value': 'a'}, {'name': 'qa_notes', 'value': 'Preserved synthetic note'}])
        task.action_move_kanban_cell([{'field': 'stage_id', 'source': self.stages[0].id, 'target': self.stages[1].id}, {'field': 'task_properties.qa_lane', 'source': 'a', 'target': 'a'}])
        self.assertEqual(task.stage_id, self.stages[1])
        self.assertEqual({item['name']: item.get('value') for item in task.read(['task_properties'])[0]['task_properties']}, {'qa_lane': 'a', 'qa_notes': 'Preserved synthetic note'})
        with self.assertRaises(UserError):
            task.action_move_kanban_cell([{'field': 'task_properties.qa_lane', 'source': 'a', 'target': 'b'}])
        self.assertEqual({item['name']: item.get('value') for item in task.read(['task_properties'])[0]['task_properties']}, {'qa_lane': 'a', 'qa_notes': 'Preserved synthetic note'})

    def test_inaccessible_property_relation_recovers_label_without_hiding_readable_task(self):
        self.project.task_properties_definition = [{'name': 'qa_reference', 'string': 'Reference', 'type': 'many2one', 'comodel': 'project.project'}]
        hidden = self.env['project.project'].create({'name': '[QA] Restricted reference', 'privacy_visibility': 'followers'})
        task = self.task(user_ids=[Command.link(self.actor.id)], task_properties=[{'name': 'qa_reference', 'value': hidden.id}])
        self.assertTrue(task.has_access('read'))
        self.assertFalse(hidden.with_user(self.actor).has_access('read'))
        for cold in (False, True):
            if cold:
                hidden.invalidate_recordset(['name', 'display_name'])
            else:
                self.assertEqual(hidden.display_name, '[QA] Restricted reference')
            result = task.with_context(usl_task_swimlanes=True).web_read_group([('id', '=', task.id)], ['stage_id', 'task_properties.qa_reference'], ['__count'], auto_unfold=True, unfold_read_default_limit=80, unfold_read_specification={'name': {}})
            column = next(group for group in result['groups'] if group['stage_id'][0] == self.stages[0].id)
            self.assertEqual(column['__groups']['groups'][0]['task_properties.qa_reference'], (hidden.id, 'Unavailable'))
            self.assertEqual(column['__groups']['groups'][0]['__count'], 1)
            self.assertEqual(column['__count'], 1)

    def test_readable_property_relation_retains_native_label_and_membership(self):
        for property_type in ('many2one', 'many2many'):
            self.project.task_properties_definition = [{'name': 'qa_reference', 'string': 'Reference', 'type': property_type, 'comodel': 'project.project'}]
            task = self.task(task_properties=[{'name': 'qa_reference', 'value': self.project.id if property_type == 'many2one' else [self.project.id]}])
            result = task.with_context(usl_task_swimlanes=True).web_read_group([('id', '=', task.id)], ['stage_id', 'task_properties.qa_reference'], ['__count'], auto_unfold=True, unfold_read_default_limit=80, unfold_read_specification={'name': {}})
            column = next(group for group in result['groups'] if group['stage_id'][0] == self.stages[0].id)
            leaf = column['__groups']['groups'][0]
            self.assertEqual(leaf['task_properties.qa_reference'], (self.project.id, self.project.name))
            self.assertEqual(leaf['__count'], 1)


    def test_native_task_variants_keep_their_existing_default_groupings(self):
        for xmlid, default in [('project.view_task_kanban_inherit_my_task', 'personal_stage_type_id'), ('project.view_task_kanban_inherit_all_task', 'project_id'), ('project.view_task_kanban', 'stage_id')]:
            arch = self.env.ref(xmlid)._get_combined_arch()
            self.assertEqual(arch.get('js_class'), 'usl_task_swimlanes')
            self.assertEqual(arch.get('default_group_by'), default)

    def test_new_unrelated_membership_prevents_stale_unassigned_move(self):
        a, b, _c = self.users
        task = self.task(user_ids=[Command.link(a.id)])
        # Another native edit adds B after the displayed card was loaded.
        task.write({'user_ids': [Command.link(b.id)]})
        with self.assertRaises(RedirectWarning) as redirect:
            task.action_move_kanban_cell([{'field': 'user_ids', 'source': a.id, 'target': False}])
        self.assertEqual(task.user_ids, a | b)
        self.assertEqual(redirect.exception.args[1], {'type': 'ir.actions.act_window', 'res_model': 'project.task', 'res_id': task.id, 'views': [(False, 'form')], 'target': 'current'})

    def test_repeated_stale_two_axis_move_cannot_write_again(self):
        task = self.task()
        self.move(task)
        with self.assertRaises(UserError):
            self.move(task)
        self.assertEqual((task.stage_id, task.priority), (self.stages[1], '1'))

    def test_readable_archived_property_reference_keeps_its_native_label(self):
        reference = self.env['project.project'].create({'name': '[QA] Archived reference', 'privacy_visibility': 'employees', 'active': False})
        self.assertTrue(reference.with_user(self.actor).has_access('read'))
        self.project.task_properties_definition = [{'name': 'qa_reference', 'string': 'Reference', 'type': 'many2one', 'comodel': 'project.project'}]
        task = self.task(task_properties=[{'name': 'qa_reference', 'value': reference.id}])
        result = task.with_context(usl_task_swimlanes=True).web_read_group([('id', '=', task.id)], ['stage_id', 'task_properties.qa_reference'], ['__count'], auto_unfold=True, unfold_read_default_limit=80, unfold_read_specification={'name': {}})
        column = next(group for group in result['groups'] if group['stage_id'][0] == self.stages[0].id)
        self.assertEqual(column['__groups']['groups'][0]['task_properties.qa_reference'], (reference.id, reference.name))
        self.assertEqual(column['__groups']['groups'][0]['__count'], 1)


    def test_governed_agents_keep_native_write_and_readonly_boundaries(self):
        if 'usl.agent' not in self.env:
            self.skipTest('Governed Agent policy is qualified in the full distribution registry.')
        group = self.env.ref('project.group_project_user')
        agents = {}
        for mode in ('read_write', 'read_only'):
            agents[mode] = self.env['usl.agent'].with_user(self.actor).create({
                'name': f'[QA] Swimlane {mode}',
                'purpose': 'Qualify task swimlane permissions.',
                'owner_id': self.actor.id,
                'company_id': self.actor.company_id.id,
                'company_ids': [Command.set(self.actor.company_ids.ids)],
                'delegated_group_ids': [Command.set(group.ids)],
                'access_mode': mode,
            })
        from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller
        access = UslAgentJson2Controller._check_agent_call(
            agent=agents['read_write'], model_name='project.task',
            method_name='action_move_kanban_cell', kwargs={},
        )
        context = UslAgentJson2Controller._agent_call_context(
            context={}, agent=agents['read_write'], model_name='project.task',
            method_name='action_move_kanban_cell', access=access,
        )
        task = self.task().with_user(agents['read_write'].user_id).with_context(context)
        self.move(task)
        self.assertEqual((task.stage_id, task.priority), (self.stages[1], '1'))
        self.assertEqual(task.write_uid, agents['read_write'].user_id)
        events = self.env['usl.audit.event'].sudo().search([
            ('actor_id', '=', agents['read_write'].user_id.id),
            ('model_name', '=', 'project.task'),
            ('operation', '=', 'write'),
        ])
        self.assertTrue(events)
        self.assertTrue(all(events.mapped('actor_is_agent')))
        self.assertTrue(all(events.mapped('correlation_id')))
        readonly_task = self.task().with_user(agents['read_only'].user_id)
        self.assertEqual(readonly_task.read(['name'])[0]['name'], '[QA] Swimlane Task')
        with self.assertRaises(AccessError):
            UslAgentJson2Controller._check_agent_call(
                agent=agents['read_only'], model_name='project.task',
                method_name='action_move_kanban_cell', kwargs={},
            )
        with self.assertRaises(AccessError):
            self.move(readonly_task)
        self.assertEqual((readonly_task.sudo().stage_id, readonly_task.sudo().priority), (self.stages[0], '0'))

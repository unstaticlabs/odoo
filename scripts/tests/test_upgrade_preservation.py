from __future__ import annotations
import copy
import json
import unittest
from operations import upgrade_preservation as p

class UpgradePreservationTests(unittest.TestCase):
    def setUp(self):
        self.scope = {t: {'maximum': 7, 'columns': sorted({'id', k})} for t,k in p.TABLES.items()}
        self.fingerprints = {t: {'count': 3, 'sha256': 'a'*64} for t in p.TABLES}
        self.baseline = {'schema':p.SCHEMA, 'scope':self.scope, 'fingerprints':self.fingerprints}

    def execute(self, scope=None, fingerprints=None):
        def call(query):
            return json.dumps((scope or self.scope) if query == p.scope_sql() else (fingerprints or self.fingerprints))
        return call

    def test_new_records_and_columns_do_not_hide_existing_record_changes(self):
        current=copy.deepcopy(self.scope)
        for v in current.values():
            v['maximum']=9
            v['columns']=sorted([*v['columns'],'new_column'])
        self.assertEqual(p.verify(self.baseline,self.execute(scope=current))['status'],'preserved')
        changed=copy.deepcopy(self.fingerprints);changed['mail_message']['sha256']='b'*64
        with self.assertRaisesRegex(ValueError,'mail_message'):
            p.verify(self.baseline,self.execute(scope=current,fingerprints=changed))

    def test_deleted_records_are_rejected_even_when_new_records_replace_the_count(self):
        changed=copy.deepcopy(self.fingerprints);changed['project_project']['count']=2
        with self.assertRaisesRegex(ValueError,'project_project'):
            p.verify(self.baseline,self.execute(fingerprints=changed))

    def test_existing_group_membership_changes_are_rejected(self):
        changed=copy.deepcopy(self.fingerprints);changed['res_groups_users_rel']['sha256']='c'*64
        with self.assertRaisesRegex(ValueError,'res_groups_users_rel'):
            p.verify(self.baseline,self.execute(fingerprints=changed))

    def test_removed_business_columns_are_rejected(self):
        current=copy.deepcopy(self.scope);current['res_groups_users_rel']['columns']=['gid']
        with self.assertRaisesRegex(ValueError,'removed captured columns'):
            p.verify(self.baseline,self.execute(scope=current))

    def test_scope_rejects_injected_columns_boolean_boundaries_and_unknown_tables(self):
        for mutate in (
            lambda s:s['ir_attachment'].update(maximum=True),
            lambda s:s['ir_attachment'].update(columns=['id); DROP TABLE x;']),
            lambda s:s.update(foreign=s['ir_attachment']),
        ):
            bad=copy.deepcopy(self.scope);mutate(bad)
            with self.assertRaises(ValueError):p.scoped_controls_sql('SELECT 1',bad)

    def test_sql_scopes_only_additive_business_tables_and_group_boundaries(self):
        sql=p.scoped_controls_sql('SELECT 1',self.scope)
        self.assertIn('public.ir_attachment WHERE id <= 7',sql)
        self.assertIn('public.res_groups_users_rel WHERE gid <= 7',sql)
        self.assertNotIn('account_move',sql)
        self.assertNotIn('res_users AS',sql)
        fingerprints=p.fingerprint_sql(self.scope)
        self.assertIn('ORDER BY r.uid,r.gid',fingerprints)
        self.assertIn('sha256',fingerprints)

    def test_malformed_fingerprints_are_rejected(self):
        for invalid in (None, [], {}, {'count': True, 'sha256': 'a'*64},
                        {'count': -1, 'sha256': 'a'*64}, {'count': 1, 'sha256': 'invalid'}):
            baseline = copy.deepcopy(self.baseline)
            baseline['fingerprints']['ir_attachment'] = invalid
            with self.assertRaises(ValueError):
                p.verify(baseline, self.execute())

    def test_unhashable_scope_column_is_rejected_cleanly(self):
        scope = copy.deepcopy(self.scope)
        scope['ir_attachment']['columns'] = [['id']]
        with self.assertRaises(ValueError):
            p.validate_scope(scope)

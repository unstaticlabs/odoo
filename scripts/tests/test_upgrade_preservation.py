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

    def test_menu_icons_are_excluded_so_a_release_can_change_one(self):
        """An app icon is a binary regenerated from module source, not evidence.

        It lives in ir_attachment as ir.ui.menu.web_icon_data, so freezing it
        made every icon change unreleasable: the upgrade rewrote the row, the
        gate refused, the rollback restored the old icon and the next attempt
        repeated it.  That happened for real on 2026-09-08.
        """
        sql = p.fingerprint_sql(self.scope)
        predicate = sql.split('FROM public.ir_attachment r WHERE ')[1]

        self.assertIn("coalesce(r.res_model, '') = 'ir.ui.menu'", predicate)
        self.assertIn("coalesce(r.res_field, '') = 'web_icon_data'", predicate)
        # coalesce, not a bare comparison: a NULL res_field must still be
        # fingerprinted rather than silently dropped by three-valued logic.
        self.assertNotIn("r.res_field = 'web_icon_data'", predicate)

    def test_only_attachments_are_narrowed(self):
        """The exclusion must not widen to the other frozen tables."""
        sql = p.fingerprint_sql(self.scope)
        self.assertEqual(sql.count('AND NOT ('), 1)
        self.assertEqual(set(p.EXCLUDED_ROWS), {'ir_attachment'})
        for table in p.TABLES:
            if table == 'ir_attachment':
                continue
            predicate = sql.split(f'FROM public.{table} r WHERE ')[1].split(')')[0]
            self.assertNotIn('NOT', predicate)

    def test_controls_keep_every_row_inside_the_boundary(self):
        """The controls count rows; the fingerprint asks if rows changed.

        Excluding menu icons from the control CTEs made the post-upgrade
        attachment count 23 lower than the pre-upgrade one, so the restore
        comparison rejected the release. The exclusion belongs to the
        fingerprint alone.
        """
        controls = p.scoped_controls_sql('SELECT 1', self.scope)
        self.assertIn('public.ir_attachment WHERE id <= 7', controls)
        self.assertNotIn('NOT (', controls)
        self.assertNotIn('res_field', controls)

    def test_a_user_document_is_still_frozen(self):
        """A real attachment carries no res_field, so it stays in scope."""
        predicate = p.fingerprint_sql(self.scope).split(
            'FROM public.ir_attachment r WHERE ',
        )[1]
        # An uploaded document has res_field NULL; coalesce makes it '' which
        # never equals web_icon_data, so NOT(...) keeps the row.
        self.assertIn("AND NOT (coalesce(r.res_model, '')", predicate)

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


class TestIconAttachmentPredicateIsShared(unittest.TestCase):
    """One definition of "an application icon", used by two different gates.

    The preservation fingerprint and the restore controls read ir_attachment for
    different reasons. On 2026-09-08 they disagreed about icons and seven
    consecutive releases were refused with
    `{"odoo.stored_attachments": {"after": 1547, "before": 1546}}`.
    """

    def test_the_alias_only_changes_the_column_prefix(self):
        # "r." also occurs inside 'ir.ui.menu', so compare the column
        # references themselves rather than the bare substring.
        bare = p.icon_attachment_predicate()
        aliased = p.icon_attachment_predicate("r")
        for column in ("res_model", "res_field"):
            self.assertIn(f"coalesce({column}, '')", bare)
            self.assertIn(f"coalesce(r.{column}, '')", aliased)
        self.assertEqual(bare, aliased.replace("r.res_", "res_"))

    def test_a_null_res_field_still_evaluates(self):
        # A user-uploaded document carries no res_field; coalesce keeps the
        # comparison false rather than NULL, so NOT(...) retains the row.
        self.assertIn("coalesce", p.icon_attachment_predicate())

    def test_the_preservation_exclusion_uses_it(self):
        self.assertEqual(
            p.EXCLUDED_ROWS["ir_attachment"], p.icon_attachment_predicate("r"),
        )

    def test_the_restore_control_uses_the_same_definition(self):
        from operations.control_manifest import ODOO_CONTROL_SQL

        stored = [
            line for line in ODOO_CONTROL_SQL.splitlines()
            if "'stored_attachments'" in line
        ]
        self.assertEqual(len(stored), 1)
        self.assertIn(p.icon_attachment_predicate(), stored[0])

    def test_the_control_template_is_fully_rendered(self):
        from operations.control_manifest import ODOO_CONTROL_SQL

        self.assertNotIn("__ICON_ATTACHMENT__", ODOO_CONTROL_SQL)

    def test_the_plain_attachment_count_is_deliberately_not_excluded(self):
        """Only the stored-file count is icon-sensitive.

        `attachments` is count(*): rewriting an existing icon does not change it,
        and a new icon row falls outside the scoped boundary the candidate side
        applies. Excluding icons there would hide a genuinely lost row for no gain.
        """
        from operations.control_manifest import ODOO_CONTROL_SQL

        plain = [
            line for line in ODOO_CONTROL_SQL.splitlines()
            if "'attachments'" in line and "stored" not in line
        ]
        self.assertEqual(len(plain), 1)
        self.assertNotIn("web_icon_data", plain[0])

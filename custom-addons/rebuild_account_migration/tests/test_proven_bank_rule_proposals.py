from unittest.mock import patch

from odoo import Command, SUPERUSER_ID, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install', 'rebuild_proven_bank_rules')
class TestProvenBankRuleProposals(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bank = cls.company_data['default_journal_bank']
        cls.expense = cls.company_data['default_account_expense']
        cls.other_expense = cls.expense.copy({'name': '[QA] Alternative rule expense'})
        cls.manager = new_test_user(
            cls.env, login='qa.proven.bank.rules',
            groups='account.group_account_manager',
            company_id=cls.env.company.id,
            company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.rules = cls.env['account.reconcile.model'].with_user(cls.manager)
        plan = cls.env['account.analytic.plan'].create({'name': '[QA] Rule evidence plan'})
        cls.analytic = cls.env['account.analytic.account'].create({
            'name': '[QA] Rule evidence project', 'plan_id': plan.id,
            'company_id': cls.env.company.id,
        })

    def _evidence(self, label='[QA] Recurring service', *, amount=-36, analytics=False):
        statement = self.env['account.bank.statement.line'].create({
            'journal_id': self.bank.id, 'date': fields.Date.today(),
            'payment_ref': label, 'amount': amount,
        })
        _liquidity, suspense, _other = statement._seek_for_lines()
        self.assertEqual(len(suspense), 1)
        suspense.write({
            'account_id': self.expense.id,
            'analytic_distribution': {str(self.analytic.id): 100} if analytics else False,
        })
        self.assertTrue(statement.is_reconciled)
        return statement

    def _find(self):
        before = self.rules.search([('rebuild_is_proposal', '=', True)])
        self.rules.action_rebuild_analyze_rule_opportunities()
        return self.rules.search([('rebuild_is_proposal', '=', True)]) - before

    def test_consistent_simple_effect_still_creates_inert_proposal(self):
        for _index in range(3):
            self._evidence()
        proposal = self._find()
        self.assertEqual(len(proposal), 1)
        self.assertEqual(proposal.line_ids.account_id, self.expense)
        self.assertEqual(proposal.trigger, 'manual')
        self.assertTrue(proposal.rebuild_is_proposal)

    def test_supported_varying_references_produce_one_evidenced_pattern(self):
        evidence = self.env['account.bank.statement.line']
        for reference in ('7012', '8345', '9267'):
            evidence |= self._evidence('[QA] Subscription ref ' + reference)
        proposal = self._find()
        self.assertEqual(len(proposal), 1)
        self.assertEqual(proposal.match_label, 'match_regex')
        self.assertEqual(set(proposal.rebuild_evidence_statement_line_ids.ids), set(evidence.ids))

    def test_native_replay_preserves_proven_analytic_effect(self):
        for _index in range(3):
            self._evidence(analytics=True)
        proposal = self._find()
        self.assertEqual(len(proposal), 1)
        self.assertEqual(proposal.line_ids.analytic_distribution, {str(self.analytic.id): 100})

    def test_broader_existing_rule_refuses_generated_overlap(self):
        self.rules.create({
            'name': '[QA] Existing broader service rule',
            'company_id': self.env.company.id, 'trigger': 'manual',
            'match_label': 'contains', 'match_label_param': '[QA] Recurring',
            'match_journal_ids': [Command.set(self.bank.ids)],
            'line_ids': [Command.create({
                'account_id': self.other_expense.id,
                'amount_type': 'percentage', 'amount_string': '100',
            })],
        })
        for _index in range(3):
            self._evidence()
        self.assertFalse(self._find(), 'A proven overlapping generated rule must not be offered')

    def test_find_help_displays_source_labels_as_literal_text(self):
        label = '[QA] <b>Beta</b> service'
        self.rules.create(self._manual_values(label))
        for _index in range(4):
            self._evidence('[QA] Alpha supported service')
        for _index in range(3):
            self._evidence(label)
        action = self.rules.action_rebuild_analyze_rule_opportunities()
        listing = action.get('params', {}).get('next', action)
        self.assertEqual(listing['type'], 'ir.actions.act_window')
        self.assertIn('&lt;b&gt;Beta&lt;/b&gt;', str(listing['help']))
        self.assertNotIn('<b>Beta</b>', str(listing['help']))

    def test_find_mixed_results_warns_and_opens_nonempty_native_list(self):
        refused_label = '[QA] Beta overlapping service'
        self.rules.create(self._manual_values(refused_label))
        for _index in range(4):
            self._evidence('[QA] Alpha supported service')
        for _index in range(3):
            self._evidence(refused_label)
        action = self.rules.action_rebuild_analyze_rule_opportunities()
        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'warning')
        self.assertTrue(action['params']['sticky'])
        listing = action['params']['next']
        self.assertEqual(listing['type'], 'ir.actions.act_window')
        suggestions = self.rules.search(listing['domain'])
        self.assertEqual(len(suggestions), 1)
        self.assertTrue(suggestions.rebuild_is_proposal)
        self.assertEqual(len(action['params']['links']), 1)
        skipped = self.rules.with_context(active_test=False).search([
            ('rebuild_generated_proof', '=', True), ('active', '=', False),
        ])
        self.assertEqual(len(skipped), 1)
        self.assertTrue(action['params']['links'][0]['url'].endswith('/%s' % skipped.id))

    def test_changed_surviving_effect_prevents_approval(self):
        statements = self.env['account.bank.statement.line']
        for _index in range(3):
            statements |= self._evidence()
        proposal = self._find()
        self.assertEqual(len(proposal), 1)
        statements[:1].move_id.line_ids.filtered(lambda line: line.account_id == self.expense).write({
            'account_id': self.other_expense.id,
        })
        with self.assertRaises(UserError):
            proposal.action_rebuild_approve_proposal()
        self.assertTrue(proposal.rebuild_is_proposal)

    def _approved(self):
        for _index in range(3):
            self._evidence()
        proposal = self._find()
        proposal.action_rebuild_approve_proposal()
        self.assertFalse(proposal.rebuild_is_proposal)
        return proposal

    def _manual_values(self, label="[QA] Unrelated manual pattern"):
        return {
            "name": label, "company_id": self.env.company.id,
            "trigger": "manual", "match_label": "contains", "match_label_param": label,
            "match_journal_ids": [Command.set(self.bank.ids)],
            "line_ids": [Command.create({"account_id": self.other_expense.id,
                "amount_type": "percentage", "amount_string": "100"})],
        }

    def test_billing_create_only_still_creates_disjoint_rule_beside_active_generated(self):
        proposal = self._approved()
        billing = new_test_user(self.env, login="qa.proven.billing", groups="account.group_account_invoice")
        model = self.env["account.reconcile.model"].with_user(billing)
        rule = model.create(self._manual_values())
        self.assertTrue(rule.line_ids)
        self.assertFalse(rule.rebuild_generated_proof)
        with self.assertRaises(AccessError):
            rule.write({"name": "[QA] Billing cannot edit"})
        self.assertFalse(proposal.rebuild_is_proposal)

    def test_basic_manual_rule_and_line_crud_beside_active_generated(self):
        self._approved()
        accountant = new_test_user(self.env, login="qa.proven.basic", groups="account.group_account_basic")
        rule = self.env["account.reconcile.model"].with_user(accountant).create(self._manual_values())
        rule.write({"name": "[QA] Native manual edit"})
        line = self.env["account.reconcile.model.line"].with_user(accountant).create({
            "model_id": rule.id, "account_id": self.other_expense.id,
            "amount_type": "percentage", "amount_string": "20", "sequence": 20,
        })
        line.write({"amount_string": "30"})
        line.unlink()
        with self.assertRaises(AccessError):
            rule.unlink()
        rule.write({"active": False})

    def test_proven_manual_overlap_with_active_generated_refuses(self):
        proposal = self._approved()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.rules.create(self._manual_values("[QA] Recurring"))
        self.assertFalse(proposal.rebuild_is_proposal)

    def test_generated_evidence_and_activation_are_not_public_write_credentials(self):
        for _index in range(3):
            self._evidence()
        proposal = self._find()
        with self.assertRaises(AccessError):
            proposal.write({"rebuild_is_proposal": False})
        with self.assertRaises(AccessError):
            proposal.with_context(_rebuild_proof=True).write({"rebuild_proof_digest": "forged"})
        with self.assertRaises(AccessError):
            self.rules.with_context(default_rebuild_generated_proof=True).create(self._manual_values())
        with self.assertRaises(AccessError):
            self.env["rebuild.reconcile.rule.coordination"].with_user(self.manager).search([])

    def test_nonzero_tax_reporting_base_is_not_silently_flattened(self):
        for _index in range(3):
            statement = self._evidence()
            statement.move_id.line_ids.filtered(lambda line: line.account_id == self.expense).write({"tax_base_amount": 12})
        self.assertFalse(self._find())

    def test_tax_base_change_after_preview_prevents_approval(self):
        statements = self.env["account.bank.statement.line"]
        for _index in range(3):
            statements |= self._evidence()
        proposal = self._find()
        statements[:1].move_id.line_ids.filtered(lambda line: line.account_id == self.expense).write({"tax_base_amount": 12})
        with self.assertRaises(UserError):
            proposal.action_rebuild_approve_proposal()
        self.assertTrue(proposal.rebuild_is_proposal)

    def _native_tax_evidence(self, label="[QA] Taxed recurring service", analytics=True):
        tax = self.company_data["default_tax_purchase"].copy({"name": "[QA] Native example VAT", "amount": 20})
        reference = self.env["account.reconcile.model"].create({
            "name": "[QA] Historical native classification", "company_id": self.env.company.id,
            "match_label": "contains", "match_label_param": "[QA] Never match automatically",
            "line_ids": [Command.create({"account_id": self.expense.id,
                "amount_type": "percentage", "amount_string": "83.33333333333333",
                "tax_ids": [Command.set(tax.ids)],
                "analytic_distribution": {str(self.analytic.id): 100} if analytics else False})],
        })
        statement = self.env["account.bank.statement.line"].create({
            "journal_id": self.bank.id, "date": fields.Date.today(), "payment_ref": label, "amount": -36,
        })
        native = statement._default_reconcile_data()
        data, _next = statement._reconcile_data_by_model(native["data"], reference, native["reconcile_auxiliary_id"])
        statement._reconcile_bank_line_edit(data)
        self.assertTrue(statement.is_reconciled)
        reference.active = False
        return statement, tax

    def _native_tax_cohort(self):
        first, tax = self._native_tax_evidence()
        statements = first
        reference = self.env["account.reconcile.model"].with_context(active_test=False).search([
            ("name", "=", "[QA] Historical native classification"),
        ], limit=1)
        for _index in range(2):
            statement = self.env["account.bank.statement.line"].create({
                "journal_id": self.bank.id, "date": fields.Date.today(),
                "payment_ref": "[QA] Taxed recurring service", "amount": -36,
            })
            native = statement._default_reconcile_data()
            data, _next = statement._reconcile_data_by_model(native["data"], reference, native["reconcile_auxiliary_id"])
            statement._reconcile_bank_line_edit(data)
            self.assertTrue(statement.is_reconciled)
            statements |= statement
        return statements, tax

    def test_native_tax_and_analytic_result_can_be_inferred_without_ledger_writes(self):
        statements, tax = self._native_tax_cohort()
        fields_to_compare = [
            "balance", "amount_currency", "currency_id", "account_id", "tax_ids", "tax_tag_ids",
            "tax_repartition_line_id", "tax_base_amount", "analytic_distribution", "write_date",
        ]
        snapshot = statements.move_id.line_ids.sorted("id").read(fields_to_compare)
        proposal = self._find()
        self.assertEqual(len(proposal), 1)
        self.assertEqual(proposal.line_ids.tax_ids, tax)
        self.assertEqual(proposal.line_ids.analytic_distribution, {str(self.analytic.id): 100})
        self.assertAlmostEqual(proposal.line_ids.amount, 100 / 1.2)
        proposal.action_rebuild_approve_proposal()
        self.assertEqual(snapshot, statements.move_id.line_ids.sorted("id").read(fields_to_compare))

    def test_observed_tax_reporting_base_is_part_of_effect_not_just_tax_ids(self):
        statements, _tax = self._native_tax_cohort()
        tax_lines = statements.move_id.line_ids.filtered("tax_repartition_line_id")
        self.assertEqual(len(tax_lines), 3)
        # The pinned native execution values omit tax_base_amount (default zero).
        # Identical tax identities/balances do not prove a nonzero reporting base.
        self.assertFalse(any(tax_lines.mapped("tax_base_amount")))
        tax_lines.write({"tax_base_amount": 30})
        self.assertFalse(self._find())

    def test_analysis_and_approval_do_not_call_financial_mutators(self):
        for _index in range(3):
            self._evidence()

        def refuse(*args, **kwargs):
            raise AssertionError("Analysis must not mutate financial records")

        from contextlib import ExitStack
        with ExitStack() as stack:
            for model in ("account.bank.statement.line", "account.move", "account.move.line"):
                for operation in ("create", "write", "unlink"):
                    stack.enter_context(patch.object(type(self.env[model]), operation, refuse))
            proposal = self._find()
            self.assertEqual(len(proposal), 1)
            proposal.action_rebuild_approve_proposal()

    def test_approved_result_is_usable_by_native_future_bank_matching(self):
        proposal = self._approved()
        statement = self.env["account.bank.statement.line"].create({
            "journal_id": self.bank.id, "date": fields.Date.today(),
            "payment_ref": "[QA] Recurring service", "amount": -42,
        })
        matches = self.rules._get_rules(statement, trigger="manual")
        self.assertIn(proposal.id, matches[statement.id])
        native = statement._default_reconcile_data()
        data, _next = statement._reconcile_data_by_model(native["data"], proposal, native["reconcile_auxiliary_id"])
        statement._reconcile_bank_line_edit(data)
        self.assertTrue(statement.is_reconciled)
        expense = statement.move_id.line_ids.filtered(lambda line: line.account_id == self.expense)
        self.assertEqual(expense.balance, 42)
        self.assertEqual(expense.reconcile_model_id, proposal)

    def test_archived_generated_rule_can_recheck_and_reactivate_without_source_change(self):
        rule = self._approved()
        rule.write({"active": False})
        rule.action_rebuild_recheck_evidence()
        self.assertFalse(rule.active)
        rule.write({"active": True})
        self.assertTrue(rule.active)
        self.assertFalse(rule.rebuild_is_proposal)

    def test_changed_proposal_requires_explicit_recheck_and_replays_edited_result(self):
        for _index in range(3):
            self._evidence()
        proposal = self._find()
        proposal.line_ids.write({"label": "[QA] Reviewed native result label"})
        with self.assertRaises(UserError):
            proposal.action_rebuild_approve_proposal()
        proposal.action_rebuild_recheck_evidence()
        proposal.action_rebuild_approve_proposal()
        self.assertFalse(proposal.rebuild_is_proposal)

    def test_copy_of_generated_suggestion_cannot_skip_evidence_approval(self):
        for _index in range(3):
            self._evidence()
        source = self._find()
        copied = source.copy()
        self.assertTrue(copied.rebuild_generated_proof)
        self.assertTrue(copied.rebuild_is_proposal)
        self.assertFalse(copied.rebuild_proof_digest)
        with self.assertRaises(UserError):
            copied.action_rebuild_approve_proposal()
        with self.assertRaises(AccessError):
            source.copy({"rebuild_is_proposal": False})
        copied.action_rebuild_dismiss_proposal()
        source.action_rebuild_approve_proposal()
        self.assertFalse(source.rebuild_is_proposal)

    def test_managed_agent_uses_native_find_and_approval_under_owner_ceiling(self):
        if "usl.agent" not in self.env:
            self.skipTest("Managed Agent addon is optional in the standalone accounting closure")
        for _index in range(3):
            self._evidence()
        agent = self.env["usl.agent"].with_user(self.manager).create({
            "name": "[QA] Native rule assistant", "purpose": "Prepare demonstrated bank matching rules",
            "owner_id": self.manager.id, "company_id": self.env.company.id,
            "company_ids": [Command.set(self.env.company.ids)],
            "delegated_group_ids": [Command.set(self.env.ref("account.group_account_manager").ids)],
            "access_mode": "read_write",
        })
        model = self.env["account.reconcile.model"].with_user(agent.user_id)
        before = model.search([("rebuild_is_proposal", "=", True)])
        model.action_rebuild_analyze_rule_opportunities()
        proposal = model.search([("rebuild_is_proposal", "=", True)]) - before
        self.assertEqual(len(proposal), 1)
        proposal.action_rebuild_approve_proposal()
        self.assertFalse(proposal.rebuild_is_proposal)
        with self.assertRaises(AccessError):
            self.env["rebuild.reconcile.rule.coordination"].with_user(agent.user_id).search([])

    def test_readonly_agent_cannot_find_or_seed_authoring_coordination(self):
        if "usl.agent" not in self.env:
            self.skipTest("Managed Agent addon is optional in the standalone accounting closure")
        agent = self.env["usl.agent"].with_user(self.manager).create({
            "name": "[QA] Readonly rule assistant", "purpose": "Inspect bank matching rules",
            "owner_id": self.manager.id, "company_id": self.env.company.id,
            "company_ids": [Command.set(self.env.company.ids)],
            "delegated_group_ids": [Command.set(self.env.ref("account.group_account_manager").ids)],
            "access_mode": "read_only",
        })
        self.env.cr.execute("SELECT count(*) FROM rebuild_reconcile_rule_coordination")
        before = self.env.cr.fetchone()[0]
        with self.assertRaises(AccessError):
            self.env["account.reconcile.model"].with_user(agent.user_id).with_context(
                _rebuild_rule_coordination=["forged", [self.env.company.id]], _rebuild_proof=True,
            ).action_rebuild_analyze_rule_opportunities()
        self.env.cr.execute("SELECT count(*) FROM rebuild_reconcile_rule_coordination")
        self.assertEqual(self.env.cr.fetchone()[0], before)

    def test_unknown_existing_regex_warns_without_refusing_supported_proposal(self):
        self.rules.create(self._manual_values("^Unrelated[A-Z]+$") | {
            "match_label": "match_regex", "match_label_param": "^Unrelated[A-Z]+$",
        })
        for _index in range(3):
            self._evidence()
        proposal = self._find()
        self.assertEqual(len(proposal), 1)
        self.assertIn("could overlap", proposal.rebuild_overlap_warning)
        self.assertTrue(proposal.rebuild_overlap_rule_ids)
        proposal.action_rebuild_approve_proposal()
        self.assertFalse(proposal.rebuild_is_proposal)

    def test_disjoint_native_signed_amount_scope_preserves_proposal(self):
        self.rules.create(self._manual_values("[QA] Recurring") | {
            "match_amount": "greater", "match_amount_min": 1,
        })
        for _index in range(3):
            self._evidence()
        proposal = self._find()
        self.assertEqual(len(proposal), 1)
        self.assertNotIn("[QA] Recurring", proposal.rebuild_overlap_warning or "")

    def test_dismissed_supported_pattern_is_not_reoffered_or_reactivated(self):
        for _index in range(3):
            self._evidence()
        proposal = self._find()
        proposal.action_rebuild_dismiss_proposal()
        self.assertFalse(self._find())
        self.assertFalse(proposal.active)
        self.assertTrue(proposal.rebuild_is_proposal)

    def test_all_native_or_label_fields_participate_in_proven_overlap(self):
        for field in ("transaction_details", "narration"):
            with self.subTest(field=field), self.env.cr.savepoint():
                rule = self.rules.create(self._manual_values("[QA] Existing hidden-in-metadata marker"))
                for _index in range(3):
                    statement = self._evidence("[QA] Native OR example " + field)
                    if field == "transaction_details":
                        statement.transaction_details = {"native_note": "[QA] Existing hidden-in-metadata marker"}
                    else:
                        statement.move_id.narration = "[QA] Existing hidden-in-metadata marker"
                self.assertFalse(self._find())
                rule.active = False

    def test_native_pattern_escaping_and_observed_reference_boundaries(self):
        examples = ("[QA] Plan (A+B) ref 1200", "[QA] Plan (A+B) ref 4500", "[QA] Plan (A+B) ref 7800")
        for label in examples:
            self._evidence(label)
        proposal = self._find()
        self.assertEqual(len(proposal), 1)
        scope = (self.bank, self.env["res.partner"], -10)
        for label in (*examples, "[QA] Plan (A+B) ref 3456"):
            self.assertIn(proposal.id, proposal._rebuild_native_witness_matches(label, scope, proposal))
        for label in ("[QA] Plan AAB ref 3456", "[QA] Plan (A+B) ref 34", "[QA] Another plan ref 3456"):
            self.assertNotIn(proposal.id, proposal._rebuild_native_witness_matches(label, scope, proposal))

    def test_bounded_shape_witnesses_are_verified_by_postgresql(self):
        from ..models.reconciliation_rule_patterns import infer_pattern, shape_witness
        for punctuation in ("[QA] ref ", "[QA] (A+B)%_ ref ", "[QA] fee.* ref "):
            labels = [punctuation + suffix for suffix in ("11", "222", "333")]
            pattern, shape = infer_pattern(labels)
            self.assertEqual((pattern, shape), infer_pattern(list(reversed(labels))))
            other_pattern, other_shape = infer_pattern([punctuation + suffix for suffix in ("55", "66", "77")])
            witness, decided = shape_witness(shape, other_shape)
            self.assertTrue(decided)
            self.assertTrue(witness)
            self.env.cr.execute("SELECT %s ~* %s AND %s ~* %s", [witness, pattern, witness, other_pattern])
            self.assertTrue(self.env.cr.fetchone()[0])
            for label in labels:
                self.env.cr.execute("SELECT %s ~* %s", [label, pattern])
                self.assertTrue(self.env.cr.fetchone()[0])

    def test_native_contains_wildcards_are_not_python_literal_matching(self):
        self.rules.create(self._manual_values("[QA] Recurring%service"))
        for _index in range(3):
            self._evidence()
        self.assertFalse(self._find())

    def test_native_manual_company_defaults_and_line_authoring_remain_selected_company_scoped(self):
        self._approved()
        other = self.setup_other_company(name="[QA] Other rule company")
        company = other["company"]
        accountant = new_test_user(self.env, login="qa.proven.multicompany", groups="account.group_account_basic",
            company_id=self.env.company.id, company_ids=[Command.set((self.env.company | company).ids)])
        model = self.env["account.reconcile.model"].with_user(accountant).with_context(
            allowed_company_ids=(self.env.company | company).ids, default_company_id=company.id,
        )
        values = self._manual_values("[QA] Other-company native manual purpose")
        values.pop("company_id")
        values["match_journal_ids"] = [Command.set(other["default_journal_bank"].ids)]
        values["line_ids"] = [Command.create({"account_id": other["default_account_expense"].id})]
        rule = model.create(values)
        self.assertEqual(rule.company_id, company)
        rule.write({"name": "[QA] Edited from another selected current company"})
        rule.line_ids.write({"amount_string": "75"})
        self.env.cr.execute("SELECT company_id FROM rebuild_reconcile_rule_coordination WHERE company_id = %s", [company.id])
        self.assertEqual(self.env.cr.fetchone()[0], company.id)

    def test_generated_review_requires_its_current_company_not_merely_selected_companies(self):
        for _index in range(3):
            self._evidence()
        proposal = self._find()
        company = self.setup_other_company(name="[QA] Other review company")["company"]
        self.manager.with_user(SUPERUSER_ID).write({"company_ids": [Command.set((self.env.company | company).ids)]})
        with self.assertRaises(AccessError):
            proposal.with_context(allowed_company_ids=(company | self.env.company).ids).action_rebuild_approve_proposal()
        self.assertTrue(proposal.rebuild_is_proposal)

    def test_denied_company_create_does_not_seed_private_metadata(self):
        company = self.setup_other_company(name="[QA] Denied rule company")["company"]
        self.env.cr.execute("SELECT count(*) FROM rebuild_reconcile_rule_coordination WHERE company_id = %s", [company.id])
        before = self.env.cr.fetchone()[0]
        with self.assertRaises(AccessError):
            self.rules.create(self._manual_values() | {"company_id": company.id, "line_ids": [], "match_journal_ids": []})
        self.env.cr.execute("SELECT count(*) FROM rebuild_reconcile_rule_coordination WHERE company_id = %s", [company.id])
        self.assertEqual(self.env.cr.fetchone()[0], before)

    def test_journal_write_permission_is_not_a_find_prerequisite(self):
        self.env["ir.rule"].with_user(SUPERUSER_ID).create({
            "name": "[QA] Journal write deliberately denied", "model_id": self.env["ir.model"]._get("account.journal").id,
            "domain_force": repr([("id", "=", 0)]), "perm_read": False, "perm_write": True,
            "perm_create": False, "perm_unlink": False,
        })
        with self.assertRaises(AccessError):
            self.bank.with_user(self.manager).check_access("write")
        for _index in range(3):
            self._evidence()
        self.assertEqual(len(self._find()), 1)

    def test_hidden_evidence_is_not_replaced_by_an_approval_success(self):
        statements = self.env["account.bank.statement.line"]
        for _index in range(3):
            statements |= self._evidence()
        proposal = self._find()
        self.env["ir.rule"].with_user(SUPERUSER_ID).create({
            "name": "[QA] Evidence access revoked", "model_id": self.env["ir.model"]._get("account.bank.statement.line").id,
            "domain_force": repr([("id", "!=", statements[:1].id)]),
            "perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False,
        })
        with self.assertRaises(AccessError):
            proposal.action_rebuild_approve_proposal()
        self.assertTrue(proposal.rebuild_is_proposal)

    def test_hidden_fourth_source_cannot_be_silently_removed_by_recheck(self):
        statements = self.env["account.bank.statement.line"]
        for _index in range(4):
            statements |= self._evidence()
        proposal = self._find()
        digest = proposal.rebuild_proof_digest
        self.env["ir.rule"].with_user(SUPERUSER_ID).create({
            "name": "[QA] Fourth source access revoked", "model_id": self.env["ir.model"]._get("account.bank.statement.line").id,
            "domain_force": repr([("id", "!=", statements[:1].id)]),
            "perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False,
        })
        with self.assertRaisesRegex(AccessError, "every supporting transaction"):
            proposal.action_rebuild_recheck_evidence()
        self.assertEqual(proposal.rebuild_proof_digest, digest)
        self.assertTrue(proposal.rebuild_is_proposal)

"""Native declaration history stays distinct from recalculable data checks."""

from pathlib import Path

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged
from odoo.modules.module import get_module_path, load_script


@tagged("post_install", "-at_install", "declaration_presentation")
class TestDeclarationPresentation(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Synthetic declaration presentation"})
        cls.reader = cls.env["res.users"].with_context(
            no_reset_password=True, usl_governed_identity_provisioning=True,
        ).create({
            "name": "Synthetic declaration reader", "login": "declaration.presentation.reader",
            "company_id": cls.company.id, "company_ids": [Command.set(cls.company.ids)],
            "group_ids": [Command.set((cls.env.ref("base.group_user")
                                       | cls.env.ref("account.group_account_readonly")).ids)],
            "lang": "en_US",
        })
        cls.declarations = {}
        rule = cls.env.ref("rebuild_account_migration.declaration_rule_2572_2026")
        for index, state in enumerate(("filed", "paid", "archived", "data_missing")):
            cls.declarations[state] = cls.env["rebuild.account.declaration"].create({
                "name": "Synthetic declaration " + state,
                "company_id": cls.company.id, "rule_id": rule.id,
                "period_start": f"{2030 + index}-01-01", "period_end": f"{2030 + index}-12-31",
                "fiscalyear_start": f"{2030 + index}-01-01", "fiscalyear_end": f"{2030 + index}-12-31",
                "deadline_date": "2036-04-15", "deadline_basis": "Synthetic deadline only.",
                "applicability": "applicable", "applicability_reason": "Synthetic obligation only.",
                "status": state, "preparation_status": "missing_data", "validation_status": "blocked",
                "validation_summary": "Synthetic unresolved current information.",
                "filing_status": "filed" if state != "data_missing" else "not_open",
                "payment_status": "paid" if state == "paid" else "not_assessed",
                "external_filing_reference": "SYNTHETIC-FILING" if state != "data_missing" else False,
            })

    def test_completed_record_checks_are_detail_not_current_work(self):
        for size, state in (("1280x900", "filed"), ("390x844", "paid"), ("1280x900", "archived")):
            declaration = self.declarations[state]
            self.browser_size = size
            self.browser_js(
                f"/odoo/rebuild.account.declaration/{declaration.id}",
                """
                (async () => {
                    const visible = (node) => node && node.getClientRects().length > 0;
                    const primaryChecks = [...document.querySelectorAll(
                        '.o_form_sheet [name="preparation_status"], .o_form_sheet [name="validation_status"]'
                    )].filter(node => visible(node) && !node.closest('.o_notebook'));
                    if (primaryChecks.length) {
                        throw new Error('Completed obligation still shows current preparation checks as primary status.');
                    }
                    const filing = document.querySelector('.o_form_sheet [name="filing_status"]');
                    if (!visible(filing)) throw new Error('Recorded filing outcome is not visible.');
                    const tab = [...document.querySelectorAll('.o_notebook .nav-link')]
                        .find(node => node.textContent.trim() === 'Data Checks');
                    if (!tab) throw new Error('Current data checks are not discoverable.');
                    tab.click();
                    await new Promise(resolve => setTimeout(resolve, 100));
                    const checks = document.querySelector('.o_notebook [name="preparation_status"]');
                    if (!visible(checks) || !checks.textContent.includes('Missing Data')) {
                        throw new Error('Actual missing-input evidence was lost from details.');
                    }
                    if (!document.querySelector('.o_notebook').textContent.includes('do not reverse')) {
                        throw new Error('Current checks are not distinguished from recorded filing.');
                    }
                    console.log('test successful');
                })();
                """,
                ready="Boolean(document.querySelector('.o_form_sheet [name=filing_status]'))",
                login=self.reader.login,
            )
            self.assertEqual(declaration.preparation_status, "missing_data")
            self.assertEqual(declaration.validation_status, "blocked")
            self.assertEqual(declaration.status, state)

    def test_open_record_keeps_preparation_warning(self):
        declaration = self.declarations["data_missing"]
        self.browser_js(
            f"/odoo/rebuild.account.declaration/{declaration.id}",
            """
            const check = [...document.querySelectorAll('.o_form_sheet [name="preparation_status"]')]
                .find(node => !node.closest('.o_notebook') && node.getClientRects().length);
            if (!check || !check.textContent.includes('Missing Data')) {
                throw new Error('Open declaration lost its actionable preparation warning.');
            }
            console.log('test successful');
            """,
            ready="Boolean(document.querySelector('.o_form_sheet [name=filing_status]'))",
            login=self.reader.login,
        )

    def test_filing_rejection_remains_visible_on_completed_record(self):
        declaration = self.declarations["filed"]
        declaration.write({"filing_status": "rejected", "acceptance_status": "rejected"})
        self.browser_js(
            f"/odoo/rebuild.account.declaration/{declaration.id}",
            """
            const filing = document.querySelector('.o_form_sheet [name="filing_status"]');
            if (!filing?.getClientRects().length || !filing.textContent.includes('Rejected Externally')) {
                throw new Error('External rejection was concealed by the completed lifecycle.');
            }
            console.log('test successful');
            """,
            ready="Boolean(document.querySelector('.o_form_sheet [name=filing_status]'))",
            login=self.reader.login,
        )

    def test_native_reader_and_company_boundaries_remain_in_force(self):
        declaration = self.declarations["filed"]
        self.assertEqual(declaration.with_user(self.reader).read(["status"])[0]["status"], "filed")
        with self.assertRaises(AccessError):
            declaration.with_user(self.reader).action_mark_filed()
        foreign_company = self.env["res.company"].create({"name": "Synthetic other declaration company"})
        foreign = declaration.copy({"company_id": foreign_company.id})
        with self.assertRaises(AccessError):
            foreign.with_user(self.reader).read(["status"])

    def test_schedule_rows_distinguish_open_checks_and_rejected_filing(self):
        self.declarations["filed"].write({"filing_status": "rejected"})
        action = self.env.ref("rebuild_account_migration.action_rebuild_account_declaration").copy({
            "context": "{'create': False, 'delete': False}",
            "domain": repr([("company_id", "=", self.company.id)]),
        })
        self.browser_js(
            f"/odoo/action-{action.id}",
            """
            const rows = [...document.querySelectorAll('.o_data_row')];
            const rowFor = state => rows.find(row => row.querySelector('[name="name"]')
                ?.textContent.trim() === 'Synthetic declaration ' + state);
            for (const state of ['paid', 'archived']) {
                if (rowFor(state)?.classList.contains('text-danger')) {
                    throw new Error('Completed history is colored as blocked preparation: ' + state);
                }
            }
            for (const state of ['data_missing', 'filed']) {
                if (!rowFor(state)?.classList.contains('text-danger')) {
                    throw new Error('Current preparation or real rejection warning is missing: ' + state);
                }
            }
            console.log('test successful');
            """,
            ready="document.querySelectorAll('.o_data_row').length === 4",
            login=self.reader.login,
        )

    def test_french_filed_lifecycle_and_filing_labels_match(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        self.env["ir.module.module"]._load_module_terms(["rebuild_account_migration"], ["fr_FR"])
        selections = self.env["rebuild.account.declaration"].with_context(lang="fr_FR").fields_get(
            ["status", "filing_status"],
        )
        for field_name in ("status", "filing_status"):
            self.assertEqual(dict(selections[field_name]["selection"])["filed"], "Déposée en externe", field_name)

    def test_french_filing_label_upgrade_preserves_custom_labels(self):
        path = (Path(get_module_path("rebuild_account_migration")) / "migrations"
                / "saas~19.3.1.13.37" / "post-correct-french-filing-label.py")
        migration = load_script(str(path), "declaration_french_label_upgrade_test")
        self.env["res.lang"]._activate_lang("fr_FR")
        lifecycle = self.env.ref("rebuild_account_migration.selection__rebuild_account_declaration__status__filed")
        filing = self.env.ref("rebuild_account_migration.selection__rebuild_account_declaration__filing_status__filed")
        overview = self.env.ref("rebuild_account_migration.selection__rebuild_account_overview__next_declaration_status__filed")
        english = (lifecycle | filing | overview).with_context(lang="en_US").mapped("name")
        lifecycle.with_context(lang="fr_FR").write({"name": "Classé en externe"})
        overview.with_context(lang="fr_FR").write({"name": "Classé en externe"})
        filing.with_context(lang="fr_FR").write({"name": "Dépôt personnalisé"})
        for _ in range(2):
            migration.migrate(self.env.cr, "saas~19.3.1.13.36")
            self.assertEqual(lifecycle.with_context(lang="fr_FR").name, "Déposée en externe")
            self.assertEqual(overview.with_context(lang="fr_FR").name, "Déposée en externe")
            self.assertEqual(filing.with_context(lang="fr_FR").name, "Dépôt personnalisé")
            self.assertEqual((lifecycle | filing | overview).with_context(lang="en_US").mapped("name"), english)

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "rebuild_account_migration_unit")
class TestAccountingArchitectureCompatibility(TransactionCase):
    """Protect stable product ownership and the migration boundary."""

    _STABLE_XML_IDS = (
        "group_rebuild_accountant_reviewer",
        "action_rebuild_accounting_home",
        "action_rebuild_account_hygiene",
        "action_rebuild_account_closing_period",
        "action_rebuild_account_declaration",
        "action_rebuild_interactive_trial_balance",
        "action_rebuild_interactive_profit_loss",
        "menu_rebuild_accounting_overview",
        "menu_rebuild_analytic_reporting",
        "menu_rebuild_einvoice_readiness",
    )

    _STABLE_MODEL_TABLES = {
        "rebuild.account.overview": "rebuild_account_overview",
        "rebuild.account.hygiene.issue": "rebuild_account_hygiene_issue",
        "rebuild.account.hygiene.dismissal": "rebuild_account_hygiene_dismissal",
        "rebuild.account.closing.period": "rebuild_account_closing_period",
        "rebuild.account.declaration": "rebuild_account_declaration",
        "rebuild.account.report.definition": "rebuild_account_report_definition",
        "rebuild.einvoice.reception": "rebuild_einvoice_reception",
    }

    def test_stable_xml_ids_remain_owned_by_compatibility_module(self):
        model_data = self.env["ir.model.data"]
        for name in self._STABLE_XML_IDS:
            record = model_data.search([
                ("module", "=", "rebuild_account_migration"),
                ("name", "=", name),
            ])
            self.assertEqual(
                len(record),
                1,
                f"rebuild_account_migration.{name} must remain a unique XML ID",
            )
            self.assertTrue(record.exists())

    def test_stable_models_keep_their_database_tables(self):
        for model_name, table_name in self._STABLE_MODEL_TABLES.items():
            self.assertIn(model_name, self.env.registry)
            self.assertEqual(self.env[model_name]._table, table_name)

    def test_one_shot_restore_models_are_not_in_product_registry(self):
        for model_name in (
            "rebuild.account.import.run",
            "rebuild.account.discrepancy",
            "rebuild.account.source.report",
            "rebuild.account.deferred.schedule.line",
            "rebuild.account.analytic.override",
        ):
            self.assertNotIn(model_name, self.env.registry)

    def test_specialized_company_views_do_not_replace_native_settings(self):
        """Action-specific company screens must not win default view lookup."""
        native_form = self.env.ref("base.view_company_form")
        native_list = self.env.ref("base.view_company_tree")
        specialized_views = (
            (
                "rebuild_account_migration."
                "view_company_rebuild_einvoice_readiness_list",
                native_list,
            ),
            (
                "rebuild_account_migration."
                "view_company_rebuild_einvoice_readiness_form",
                native_form,
            ),
        )
        for xmlid, native_view in specialized_views:
            specialized_view = self.env.ref(xmlid)
            self.assertGreater(
                specialized_view.priority,
                native_view.priority,
                f"{xmlid} must remain action-specific",
            )

        einvoice_action = self.env.ref(
            "rebuild_account_migration.action_rebuild_einvoice_readiness",
        )
        self.assertEqual(
            einvoice_action.view_ids.mapped("view_id"),
            self.env[
                "ir.ui.view"
            ].browse([
                self.env.ref(
                    "rebuild_account_migration."
                    "view_company_rebuild_einvoice_readiness_list",
                ).id,
                self.env.ref(
                    "rebuild_account_migration."
                    "view_company_rebuild_einvoice_readiness_form",
                ).id,
            ]),
        )

    def test_company_scoped_custom_models_have_record_rules(self):
        """A public product model must never rely on UI company domains."""
        Model = self.env["ir.model"].sudo()
        Access = self.env["ir.model.access"].sudo()
        Rule = self.env["ir.rule"].sudo().with_context(active_test=False)
        product_modules = {
            module.name
            for module in self.env["ir.module.module"].sudo().search([
                ("name", "=like", "usl_%"),
                ("state", "=", "installed"),
            ])
        } | {"rebuild_account_migration"}
        missing = []
        for model_name, model_class in sorted(self.env.registry.models.items()):
            company_field = model_class._fields.get("company_id")
            if (
                model_class._transient
                or not company_field
                or not company_field.store
            ):
                continue
            model = Model._get(model_name)
            if model_class._original_module not in product_modules:
                continue
            if not Access.search_count([
                ("model_id", "=", model.id),
                ("active", "=", True),
                ("perm_read", "=", True),
            ]):
                continue
            rules = Rule.search([
                ("model_id", "=", model.id),
                ("active", "=", True),
            ])
            if not any(
                getattr(rule, "global")
                and "company_ids" in (rule.domain_force or "")
                for rule in rules
            ):
                missing.append(model_name)
        self.assertFalse(
            missing,
            "Company-scoped custom models with read access require a global "
            f"allowed-company record rule: {', '.join(missing)}",
        )

    def test_sql_reports_and_assurance_decisions_are_company_isolated(self):
        first_company = self.env.company
        second_company = self.env["res.company"].create({
            "name": "Accounting Isolation Company",
            "currency_id": first_company.currency_id.id,
        })
        accounts = self.env["account.account"]
        for code, name, account_type in (
            ("601991", "Isolation debit", "expense"),
            ("701991", "Isolation credit", "income"),
        ):
            accounts |= self.env["account.account"].with_company(
                second_company,
            ).create({
                "code": code,
                "name": name,
                "account_type": account_type,
                "company_ids": [Command.set([second_company.id])],
            })
        journal = self.env["account.journal"].with_company(
            second_company,
        ).create({
            "name": "Isolation journal",
            "code": "ISO",
            "type": "general",
            "company_id": second_company.id,
        })
        move = self.env["account.move"].with_company(second_company).create({
            "date": "2026-01-31",
            "journal_id": journal.id,
            "line_ids": [
                Command.create({
                    "name": "Isolation debit",
                    "account_id": accounts[0].id,
                    "debit": 100,
                }),
                Command.create({
                    "name": "Isolation credit",
                    "account_id": accounts[1].id,
                    "credit": 100,
                }),
            ],
        })
        move.action_post()
        decision = self.env[
            "rebuild.account.assurance.decision"
        ].with_company(second_company).create({
            "name": "Isolation decision",
            "company_id": second_company.id,
            "decision_summary": "Only the second company may review this.",
        })

        reviewer = self.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "Single-company accounting reviewer",
            "login": "single-company-reviewer@example.invalid",
            "company_id": first_company.id,
            "company_ids": [Command.set([first_company.id])],
            "group_ids": [Command.set([
                self.env.ref(
                    "rebuild_account_migration."
                    "group_rebuild_accountant_reviewer",
                ).id,
            ])],
        })
        reviewer_context = {
            "allowed_company_ids": [first_company.id],
        }
        for model_name in (
            "rebuild.account.trial.balance.line",
            "rebuild.account.general.ledger.line",
            "rebuild.account.journal.report.line",
            "rebuild.account.financial.statement.line",
            "rebuild.account.management.summary.line",
            "rebuild.account.french.statement.line",
        ):
            leaked = self.env[model_name].with_user(reviewer).with_context(
                **reviewer_context,
            ).search([("company_id", "=", second_company.id)])
            self.assertFalse(
                leaked,
                f"{model_name} exposed another company's accounting data",
            )
        self.assertNotIn(
            decision,
            self.env["rebuild.account.assurance.decision"].with_user(
                reviewer,
            ).with_context(**reviewer_context).search([]),
        )

    def test_governed_definitions_do_not_duplicate_business_keys(self):
        for model_name in (
            "rebuild.account.closing.control.definition",
            "rebuild.account.report.definition",
            "rebuild.account.declaration.rule",
        ):
            model = self.env[model_name]
            keys = {}
            for record in model.with_context(active_test=False).search([]):
                key = (
                    record.company_id.id if "company_id" in model._fields else False,
                    record.code,
                    record.definition_version,
                )
                keys.setdefault(key, record)
                self.assertEqual(
                    keys[key],
                    record,
                    f"{model_name} contains duplicate governed definition {key}",
                )

    def test_distribution_owns_french_navigation_terms(self):
        expected_names = {
            "account.menu_finance": "Comptabilité",
            "account.menu_board_journal_1": "Journaux",
            "account.menu_finance_payables": "Fournisseurs",
            "account.menu_finance_reports": "Rapports",
            "account.account_audit_menu": "Révision",
            "account.account_reports_legal_statements_menu": "États financiers",
            "account.account_reports_partners_reports_menu": "Tiers et échéances",
            "account.account_reports_taxes_and_fiscal_menu": "Fiscalité",
            "account.account_reports_management_menu": "Pilotage",
            "account_reconcile_oca.menu_account_reconcile_model": (
                "Règles de rapprochement bancaire"
            ),
            "account.action_account_reconcile_model": (
                "Règles de rapprochement bancaire"
            ),
            "usl_platform_billing.selection__account_move__platform_billing_payment_state__in_payment": (
                "En cours de paiement"
            ),
            "usl_platform_billing.selection__account_move__platform_billing_payment_state__invoicing_legacy": (
                "Hérité de l’application Facturation"
            ),
            "usl_platform_billing.selection__account_move__platform_billing_payment_state__not_paid": (
                "Non payé"
            ),
        }
        for xmlid, expected_name in expected_names.items():
            record = self.env.ref(xmlid)
            self.assertEqual(
                record.with_context(lang="fr_FR").name,
                expected_name,
                f"{xmlid} must not retain an obsolete upstream French name",
            )

        platform_view = self.env.ref(
            "usl_platform_billing.view_platform_billing_platform_form",
        ).with_context(lang="fr_FR")
        self.assertIn(
            "Répartition analytique par défaut facultative",
            platform_view.arch_db,
        )
        self.assertNotIn(
            "Optional default analytic distribution",
            platform_view.arch_db,
        )

        accounting_home = self.env.ref(
            "rebuild_account_migration.view_rebuild_accounting_home_form",
        ).with_context(lang="fr_FR")
        for expected in (
            "Factures fournisseurs en brouillon",
            "à rapprocher",
            "Montant estimé dû par USL à",
            "Montant estimé dû par",
            "à USL",
            "Trésorerie",
            "Qualité comptable",
            "Espace de clôture en cours",
            "Cette estimation de gestion",
        ):
            self.assertIn(expected, accounting_home.arch_db)
        for rejected in (
            "Projets de loi",
            ">correspondre<",
            "Montant estimé qu'USL doit",
            "Hygiène comptable",
            "devis prévisionnel",
        ):
            self.assertNotIn(rejected, accounting_home.arch_db)

        closing_view = self.env.ref(
            "rebuild_account_migration.view_rebuild_account_closing_form",
        ).with_context(lang="fr_FR")
        for expected in (
            "Dossier de clôture",
            "Valider comme prêt à clôturer",
            "Actualiser l’état de préparation",
        ):
            self.assertIn(expected, closing_view.arch_db)

        statement_lines = self.env["account.bank.statement.line"].with_context(
            lang="fr_FR",
        )
        status_labels = dict(
            statement_lines._fields[
                "rebuild_transaction_status"
            ]._description_selection(statement_lines.env),
        )
        self.assertEqual(status_labels["open"], "À rapprocher")

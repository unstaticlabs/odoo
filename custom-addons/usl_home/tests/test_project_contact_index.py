from odoo import Command
from odoo.tools import SQL
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_search_project_contact")
class TestProjectContactIndex(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.actor = new_test_user(
            cls.env, login="qa-index-project-contact",
            groups="project.group_project_user,base.group_partner_manager",
        )
        cls.service = cls.env["usl.home.service"].with_user(cls.actor)

    def _refresh(self, model):
        worker = getattr(self.env[model], "_cron_usl_index_content", None)
        if worker:
            worker()

    def _results(self, category, query, **context):
        result = self.service.with_context(**context).search_records(query, categories=[category])
        return [row["id"] for row in result["groups"][0]["records"]]

    def test_project_description_words_in_different_order(self):
        project = self.env["project.project"].create({
            "name": "[QA Index] Ordinary project",
            "description": "<p>Amber planning for the heliograph launch</p>",
            "company_id": self.env.company.id,
        })
        self._refresh("project.project")
        self.assertEqual(self._results("projects", "heliograph Amber"), project.ids)

    def test_contact_notes_words_in_different_order(self):
        contact = self.env["res.partner"].create({
            "name": "[QA Index] Ordinary contact",
            "comment": "<p>Ivory planning for the semaphore launch</p>",
            "company_id": self.env.company.id,
        })
        self._refresh("res.partner")
        self.assertEqual(self._results("contacts", "semaphore Ivory"), contact.ids)

    def test_long_project_and_contact_notes_keep_distant_search_words(self):
        vocabulary = " ".join(f"article{i:06d}" for i in range(75000))
        for model in ("project.project", "res.partner"):
            with self.subTest(model=model):
                record = self._record(model, f"Reliure {vocabulary} colophon")
                self._refresh(model)
                self.assertEqual(self._search(model, "colophon reliure"), record)
                self.assertEqual(self._search(model, "article074999 article000000"), record)

    def test_project_and_contact_ligatures_survive_indexing(self):
        if not self.env.registry.has_unaccent:
            self.skipTest("Native SQL unaccent dictionary is not installed")
        for model in ("project.project", "res.partner"):
            with self.subTest(model=model):
                record = self._record(model, "Manuscrit de l’œuvre reliée")
                self.assertEqual(self._search(model, "oeuvre reliee"), record)
                self._refresh(model)
                self.assertEqual(self._search(model, "oeuvre reliee"), record)

    def _record(self, model, text="Coral planning for the heliograph launch", **values):
        field = "description" if model == "project.project" else "comment"
        return self.env[model].create({
            "name": "[QA Index] Ordinary source", field: text,
            "company_id": self.env.company.id, **values,
        })

    def _search(self, model, query, **context):
        return self.env[model].with_user(self.actor).with_context(**context).search([
            ("usl_search_text", "ilike", query),
        ])

    def test_project_title_language_and_native_fallback(self):
        project = self._record("project.project", "", name={
            "en_US": "[QA Index] Copper heliograph launch",
            "fr_FR": "[QA Index] Lancement sémaphore ivoire",
        })
        fallback = self._record("project.project", "", name={"en_US": "[QA Index] Cobalt lantern plan"})
        self._refresh("project.project")
        self.assertEqual(self._search("project.project", "heliograph Copper", lang="en_US"), project)
        self.assertFalse(self._search("project.project", "semaphore ivoire", lang="en_US"))
        self.assertEqual(self._search("project.project", "ivoire semaphore", lang="fr_FR"), project)
        self.assertFalse(self._search("project.project", "heliograph Copper", lang="fr_FR"))
        self.assertEqual(self._search("project.project", "lantern Cobalt", lang="fr_FR"), fallback)
        self.assertEqual(project.with_context(lang="en_US").name, "[QA Index] Copper heliograph launch")
        self.assertEqual(project.with_context(lang="fr_FR").name, "[QA Index] Lancement sémaphore ivoire")

    def test_empty_project_translation_does_not_match_other_language(self):
        project = self._record("project.project", "", name={
            "en_US": "[QA Index] Copper heliograph launch", "fr_FR": "",
        })
        self.assertEqual(project.with_context(lang="fr_FR").name, "")
        self._refresh("project.project")
        self.assertFalse(self._search("project.project", "heliograph Copper", lang="fr_FR"))
        self.assertEqual(self._search("project.project", "heliograph Copper", lang="en_US"), project)

    def test_translation_edit_excludes_old_fragment_until_refresh(self):
        project = self._record("project.project", "", name={
            "en_US": "[QA Index] Copper heliograph launch",
            "fr_FR": "[QA Index] Lancement sémaphore ivoire",
        })
        self._refresh("project.project")
        project.with_context(lang="fr_FR").write({"name": "[QA Index] Violet lantern replacement"})
        self.assertFalse(self._search("project.project", "ivoire semaphore", lang="fr_FR"))
        self.assertEqual(self._search("project.project", "Violet lantern replacement", lang="fr_FR"), project)
        self._refresh("project.project")
        self.assertEqual(self._search("project.project", "replacement Violet", lang="fr_FR"), project)
        self.assertEqual(self._search("project.project", "heliograph Copper", lang="en_US"), project)

    def test_clean_html_and_legacy_fields(self):
        for model in ("project.project", "res.partner"):
            with self.subTest(model=model):
                record = self._record(model, '<p class="markup-lantern-only">Amber heliograph visible</p><a href="https://source-lantern.invalid">Reference</a>')
                self._refresh(model)
                self.assertFalse(self._search(model, "markup-lantern-only"))
                self.assertEqual(self._search(model, "visible Amber"), record)
                # Native html2plaintext retains link references as source text.
                self.assertEqual(self._search(model, "source-lantern.invalid"), record)
        contact = self._record("res.partner", "", email="qa-index-semaphore@invalid.example")
        self._refresh("res.partner")
        self.assertEqual(self._search("res.partner", "qa-index-semaphore@invalid.example"), contact)

    def test_current_source_field_permission(self):
        from unittest.mock import patch
        for model, field in (("project.project", "description"), ("res.partner", "comment")):
            with self.subTest(model=model):
                record = self._record(model)
                self._refresh(model)
                with patch.object(self.env[model]._fields[field], "groups", "base.group_system"):
                    self.assertFalse(self._search(model, "heliograph Coral"))
                self.assertEqual(self._search(model, "heliograph Coral"), record)

    def test_current_rule_revocation_and_recovery_without_reindex(self):
        for model in ("project.project", "res.partner"):
            with self.subTest(model=model):
                record = self._record(model)
                self._refresh(model)
                rule = self.env["ir.rule"].create({
                    "name": "[QA Index] Revoke current source",
                    "model_id": self.env["ir.model"]._get_id(model),
                    "domain_force": repr([("id", "!=", record.id)]), "global": True,
                })
                self.assertFalse(self._search(model, "heliograph Coral"))
                rule.unlink()
                self.assertEqual(self._search(model, "heliograph Coral"), record)

    def test_selected_company_and_shared_contact(self):
        company = self.env["res.company"].create({"name": "[QA Index] Other company"})
        self.actor.company_ids = [Command.link(company.id)]
        for model in ("project.project", "res.partner"):
            with self.subTest(model=model):
                record = self._record(model, company_id=company.id)
                self._refresh(model)
                self.assertFalse(self._search(model, "heliograph Coral", allowed_company_ids=self.env.company.ids))
                self.assertEqual(self._search(model, "heliograph Coral", allowed_company_ids=company.ids), record)
        shared = self._record("res.partner", "Ivory semaphore shared", company_id=False)
        self._refresh("res.partner")
        self.assertEqual(self._search("res.partner", "shared Ivory", allowed_company_ids=self.env.company.ids), shared)
        self.assertEqual(self._search("res.partner", "shared Ivory", allowed_company_ids=company.ids), shared)

    def test_pending_change_and_rollback(self):
        for model, field in (("project.project", "description"), ("res.partner", "comment")):
            with self.subTest(model=model):
                record = self._record(model)
                self._refresh(model)
                with self.assertRaisesRegex(ValueError, "rollback fixture"):
                    with self.env.cr.savepoint():
                        record.write({field: "Violet semaphore replacement"})
                        self.assertFalse(self._search(model, "heliograph Coral"))
                        self.assertEqual(self._search(model, "Violet semaphore replacement"), record)
                        raise ValueError("rollback fixture")
                self.assertEqual(self._search(model, "heliograph Coral"), record)
                self.assertFalse(self._search(model, "Violet semaphore replacement"))

    def test_counts_groups_negation_and_archival(self):
        for model in ("project.project", "res.partner"):
            with self.subTest(model=model):
                record = self._record(model)
                other = self._record(model, "Ivory semaphore alternative")
                self._refresh(model)
                Model = self.env[model].with_user(self.actor)
                domain = [("usl_search_text", "ilike", "heliograph Coral")]
                self.assertEqual(Model.search_count(domain), 1)
                self.assertEqual(Model._read_group(domain, [], ["__count"]), [(1,)])
                self.assertEqual(Model.search([("id", "in", (record | other).ids), ("usl_search_text", "not ilike", "heliograph Coral")]), other)
                record.active = False
                self.assertFalse(self._search(model, "heliograph Coral"))
                self.assertEqual(self._search(model, "heliograph Coral", active_test=False), record)
                record.active = True
                self.assertEqual(self._search(model, "heliograph Coral"), record)

    def test_search_does_not_write(self):
        from unittest.mock import patch
        for model in ("project.project", "res.partner"):
            record = self._record(model)
            self._refresh(model)
            execute = self.env.cr.execute
            def readonly(query, *args, **kwargs):
                code = query._sql_tuple[0] if isinstance(query, SQL) else query
                self.assertNotRegex(str(code).lstrip().upper(), r"^(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b")
                return execute(query, *args, **kwargs)
            self.env.flush_all()  # Complete fixture writes before the read-only phase.
            with self.subTest(model=model), patch.object(self.env.cr, "execute", readonly):
                for statement in ("UPDATE usl_task_search_queue SET queued_at = queued_at WHERE FALSE", SQL("UPDATE usl_task_search_queue SET queued_at = queued_at WHERE FALSE")):
                    with self.assertRaises(AssertionError):
                        self.env.cr.execute(statement)
                self.assertEqual(self._search(model, "heliograph Coral"), record)

    def test_private_helpers_and_native_filters(self):
        from lxml import etree
        from odoo.exceptions import AccessError
        from odoo.orm.models import get_public_method
        for model, view in (("project.project", "project.view_project_project_filter"), ("res.partner", "base.view_res_partner_filter")):
            with self.subTest(model=model):
                Model = self.env[model].with_user(self.actor)
                for method in ("init", "_usl_queue_content", "_cron_usl_index_content"):
                    with self.assertRaises(AccessError):
                        get_public_method(Model, method)
                arch = etree.fromstring(Model.get_view(self.env.ref(view).id, "search")["arch"])
                self.assertEqual(len(arch.xpath("//field[@name='usl_search_text']")), 1)
                self.assertEqual(len(arch.xpath("//field[@name='name']")), 1)
                if model == "res.partner":
                    self.assertTrue(arch.xpath("//field[@name='email']"))

    def test_repeat_initialization_keeps_ready_results(self):
        for model in ("project.project", "res.partner"):
            with self.subTest(model=model):
                record = self._record(model)
                self._refresh(model)
                self.env[model].init()
                self.assertEqual(self._search(model, "heliograph Coral"), record)

    def test_bounded_worker_preserves_pending_records(self):
        self._refresh("res.partner")
        contacts = self.env["res.partner"].create([
            {"name": "[QA Index] Bounded contact %s" % index, "comment": "Cobalt planning for the lantern launch"}
            for index in range(501)
        ])
        Model = self.env["res.partner"].with_user(self.actor)
        self._refresh("res.partner")
        domain = [("id", "in", contacts.ids), ("usl_search_text", "ilike", "lantern Cobalt")]
        self.assertEqual(Model.search_count(domain), 500)
        self.assertEqual(Model.search_count([("id", "in", contacts.ids), ("usl_search_text", "ilike", "Cobalt planning")]), 501)
        self._refresh("res.partner")
        self.assertEqual(Model.search_count(domain), 501)

    def test_deletion_removes_fragments_and_queue(self):
        for model, field in (("project.project", "description"), ("res.partner", "comment")):
            with self.subTest(model=model):
                record = self._record(model)
                self._refresh(model)
                record.write({field: "Ivory semaphore pending"})
                record.unlink()
                self._refresh(model)
                self.assertFalse(self._search(model, "heliograph Coral"))
                self.assertFalse(self._search(model, "Ivory semaphore pending"))

    def test_reinstall_requeues_sources_changed_while_hooks_were_absent(self):
        from unittest.mock import patch
        from ..hooks import post_init_hook
        records = []
        for model, field in (("project.project", "description"), ("res.partner", "comment")):
            record = self._record(model)
            self._refresh(model)
            with patch.object(type(record), "_usl_queue_content", lambda source, **kwargs: None):
                record.write({field: "Violet semaphore replacement"})
            records.append(record)
        post_init_hook(self.env)
        for record in records:
            self.assertFalse(self._search(record._name, "heliograph Coral"))
            self._refresh(record._name)
            self.assertEqual(self._search(record._name, "replacement Violet"), record)

    def test_delegated_user_search_cannot_probe_unreadable_contact_notes(self):
        identity = new_test_user(
            self.env, login="qa-index-public-identity", name="[QA Index] Public identity",
            groups="base.group_user", active=True,
            **({"usl_identity_classification": False} if "usl_identity_classification" in self.env["res.users"]._fields else {}),
        )
        identity.partner_id.comment = "Quartz private heliograph evidence"
        self._refresh("res.partner")
        self.env.flush_all()  # Make the native public-user view see committed fixture flags.
        Users = self.env["res.users"].with_user(self.actor).with_context(active_test=False)
        self.assertEqual(Users.search([("id", "=", identity.id)]).read(["name"])[0]["name"], "[QA Index] Public identity")
        self.assertEqual(Users.search_count([("id", "=", identity.id), ("usl_search_text", "ilike", "heliograph Quartz")]), 1)
        self.env["ir.rule"].create({
            "name": "[QA Index] Private identity contact",
            "model_id": self.env["ir.model"]._get_id("res.partner"),
            "domain_force": repr([("id", "!=", identity.partner_id.id)]), "global": True,
        })
        self.assertFalse(identity.partner_id.with_user(self.actor).has_access("read"))
        self.assertEqual(Users.search_count([("id", "=", identity.id)]), 0)
        self.assertFalse(Users.search_count([
            ("id", "=", identity.id), ("usl_search_text", "ilike", "heliograph Quartz"),
        ]))

    def test_private_mixin_adds_no_registry_model_table_or_public_helper(self):
        from odoo.exceptions import AccessError
        from odoo.orm.models import get_public_method
        self.assertNotIn("usl.content.index.mixin", self.env.registry)
        self.env.cr.execute("SELECT to_regclass('usl_content_index_mixin')")
        self.assertIsNone(self.env.cr.fetchone()[0])
        helpers = (
            "_usl_validate_projection", "_usl_initialize_projection", "_usl_queue_projection",
            "_usl_delete_fragments", "_usl_remove_queue", "_usl_insert_fragment",
            "_usl_refresh_projection", "_usl_indexed_projection_domain",
        )
        for model in ("project.task", "project.project", "res.partner"):
            Model = self.env[model].with_user(self.actor)
            self.assertFalse(Model._abstract)
            for method in helpers:
                with self.subTest(model=model, method=method), self.assertRaises(AccessError):
                    get_public_method(Model, method)

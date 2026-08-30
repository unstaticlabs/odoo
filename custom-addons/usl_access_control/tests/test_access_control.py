import json
from io import BytesIO
from unittest.mock import patch

from lxml import etree

from odoo import SUPERUSER_ID, Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.service.model import call_kw
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "usl_access_control")
class TestDistributionAccessControl(AccountTestInvoicingCommon):
    @classmethod
    def get_default_groups(cls):
        # The native accounting harness creates an isolated company as its
        # fixture user. Give that bootstrap identity the same explicit product
        # administration capability production requires for company creation.
        return super().get_default_groups() | cls.env.ref(
            "usl_access_control.group_distribution_administrator",
        )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company_data_2 = cls.setup_other_company(name="Access Isolation")
        cls.other_company = cls.company_data_2["company"]
        cls.groups = {
            name: cls.env.ref(f"usl_access_control.group_{name}")
            for name in (
                "accounting_reviewer",
                "ai_agent",
                "distribution_administrator",
                "irreversible_actions",
                "technical_administrator",
            )
        }
        cls.valentin = cls._create_user(
            "access.valentin",
            cls.groups["distribution_administrator"],
            companies=cls.company | cls.other_company,
        )
        cls.roger = cls._create_user(
            "access.roger",
            cls.groups["technical_administrator"],
            companies=cls.company | cls.other_company,
        )
        cls.prosper = cls._create_user(
            "access.prosper",
            cls.groups["accounting_reviewer"],
        )
        cls.agent = cls._create_user(
            "access.agent",
            cls.groups["ai_agent"],
            cls.env.ref("account.group_account_user"),
            cls.env.ref("project.group_project_manager"),
        )

    @classmethod
    def _create_user(cls, login, *groups, companies=None):
        companies = companies or cls.company
        return cls.env["res.users"].with_context(no_reset_password=True).create(
            {
                "name": login,
                "login": login,
                "email": f"{login}@example.test",
                "company_id": companies[0].id,
                "company_ids": [Command.set(companies.ids)],
                "group_ids": [Command.set([group.id for group in groups])],
            },
        )

    def _draft_invoice(self, user, *, company=None):
        company = company or self.company
        template = self.init_invoice(
            "out_invoice",
            company=company,
            products=[self.product_a],
        )
        if user == self.env.user:
            return template
        return template.with_user(user).with_company(company).copy(
            {"ref": f"Created by {user.login}"},
        )

    def test_role_matrix_is_explicit_and_attributable(self):
        self.assertTrue(self.valentin.has_group("base.group_system"))
        self.assertTrue(self.valentin.has_group("api_doc.group_allow_doc"))
        self.assertTrue(
            self.valentin.has_group(
                "usl_access_control.group_irreversible_actions",
            ),
        )
        self.assertFalse(self.roger.has_group("base.group_system"))
        self.assertTrue(self.roger.has_group("base.group_erp_manager"))
        self.assertTrue(self.roger.has_group("api_doc.group_allow_doc"))
        self.assertTrue(self.roger.has_group("account.group_account_readonly"))
        self.assertFalse(self.roger.has_group("account.group_account_user"))
        self.assertTrue(self.roger.has_group("usl_b2c.group_b2c_operator"))
        self.assertTrue(self.roger.has_group("project.group_project_manager"))
        self.assertTrue(
            self.roger.has_group("usl_documents.group_documents_manager"),
        )
        self.assertTrue(self.prosper.has_group("account.group_account_user"))
        self.assertFalse(self.prosper.has_group("base.group_erp_manager"))
        self.assertFalse(
            self.prosper.has_group(
                "usl_access_control.group_irreversible_actions",
            ),
        )
        self.assertTrue(self.agent.has_group("usl_access_control.group_ai_agent"))
        self.assertTrue(self.agent.has_group("api_doc.group_allow_doc"))
        self.assertFalse(self.prosper.has_group("api_doc.group_allow_doc"))

    def test_runtime_policy_resolves_semantic_and_model_operation_guards(self):
        policy = self.env["base"]._usl_qualified_action_policy()
        self.assertRegex(policy.qualified_policy_digest, r"^[0-9a-f]{64}$")
        self.assertEqual(
            policy.protected_guard("accounting.lock.change").action_key,
            "guard:accounting.lock.change",
        )
        self.assertEqual(
            policy.model_operation_guard("project.task", "unlink").action_key,
            "rpc:project.task.unlink",
        )
        self.assertEqual(
            policy.server_action_classification(
                "server_action:project.action_server_share_project",
            ),
            "operational",
        )
        with self.assertRaises(TypeError):
            policy.model_operation_guards["project.task", "unlink"] = None

    def test_document_detail_keeps_model_rpc_contract(self):
        with self.assertRaisesRegex(ValidationError, "no longer exists"):
            call_kw(
                self.env["usl.document"],
                "document_detail",
                [0],
                {},
            )

    def test_agent_and_irreversible_capability_are_backend_incompatible(self):
        with self.assertRaisesRegex(ValidationError, "incompatible"):
            self.agent.write(
                {
                    "group_ids": [
                        Command.link(self.groups["irreversible_actions"].id),
                    ],
                },
            )
        self.assertFalse(
            self.agent.has_group(
                "usl_access_control.group_irreversible_actions",
            ),
        )

        composite = self.env["res.groups"].create(
            {
                "name": "Unsafe composition probe",
                "implied_ids": [
                    Command.set(
                        [
                            self.groups["ai_agent"].id,
                            self.groups["irreversible_actions"].id,
                        ],
                    ),
                ],
            },
        )
        with self.assertRaisesRegex(ValidationError, "incompatible"):
            self.agent.write({"group_ids": [Command.link(composite.id)]})

    def test_named_profiles_resolve_to_one_distribution_role(self):
        definitions = self.env["res.users"]._usl_pocketid_profile_definitions()
        self.assertEqual(
            definitions["administrator"]["groups"],
            (
                "usl_access_control.group_distribution_administrator",
                "base.group_system",
            ),
        )
        self.assertEqual(
            definitions["break_glass"]["groups"],
            (
                "usl_access_control.group_distribution_administrator",
                "base.group_system",
            ),
        )
        self.assertEqual(
            definitions["technical_operator"]["groups"],
            ("usl_access_control.group_technical_administrator",),
        )
        self.assertEqual(
            definitions["accountant_reviewer"]["groups"],
            ("usl_access_control.group_accounting_reviewer",),
        )

    def test_agent_can_mutate_operational_records_and_leaves_audit_evidence(self):
        project = self.env["project.project"].create(
            {"name": "Delegated work", "company_id": self.company.id},
        )
        task = self.env["project.task"].with_user(self.agent).create(
            {
                "name": "Agent-created task",
                "project_id": project.id,
            },
        )
        task.write({"name": "Agent-updated task"})
        events = self.env["usl.audit.event"].sudo().search(
            [
                ("actor_id", "=", self.agent.id),
                ("model_name", "=", "project.task"),
            ],
        )
        self.assertEqual(set(events.mapped("operation")), {"create", "write"})
        self.assertTrue(all(events.mapped("actor_is_agent")))
        self.assertTrue(all(events.mapped("correlation_id")))
        self.assertIn("Agent-created task", "\n".join(events.mapped("changes_json")))

    def test_permanent_deletion_is_denied_to_agent_and_restricted_humans(self):
        project = self.env["project.project"].create(
            {"name": "Deletion boundary", "company_id": self.company.id},
        )
        task = self.env["project.task"].create(
            {"name": "Keep history", "project_id": project.id},
        )
        for user in (self.agent, self.roger, self.prosper):
            with self.subTest(user=user.login), self.assertRaisesRegex(
                AccessError,
                "Irreversible Actions|AI Agents",
            ):
                task.with_user(user).unlink()
        task.with_user(self.valentin).unlink()
        self.assertFalse(task.exists())
        event = self.env["usl.audit.event"].sudo().search(
            [
                ("actor_id", "=", self.valentin.id),
                ("action_name", "=", "permanently delete project.task"),
            ],
            limit=1,
        )
        self.assertTrue(event)
        self.assertEqual(event.action_key, "rpc:project.task.unlink")
        self.assertEqual(
            event.policy_digest,
            self.env["base"]._usl_qualified_action_policy().qualified_policy_digest,
        )

    def test_denied_protected_action_log_carries_policy_identity(self):
        project = self.env["project.project"].create(
            {"name": "Denial evidence", "company_id": self.company.id},
        )
        task = self.env["project.task"].create(
            {"name": "Keep denial evidence", "project_id": project.id},
        )
        with (
            self.assertLogs(
                "odoo.addons.usl_access_control.models.base",
                level="WARNING",
            ) as captured,
            self.assertRaisesRegex(AccessError, "Irreversible Actions"),
        ):
            task.with_user(self.roger).unlink()
        line = next(
            message
            for message in captured.output
            if "USL_PROTECTED_ACTION_DENIED" in message
        )
        payload = json.loads(line.split("USL_PROTECTED_ACTION_DENIED ", 1)[1])
        self.assertEqual(payload["action_key"], "rpc:project.task.unlink")
        self.assertEqual(
            payload["policy_digest"],
            self.env["base"]._usl_qualified_action_policy().qualified_policy_digest,
        )

    def test_accounting_remains_reversible_for_reviewer_and_agent(self):
        for user in (self.prosper, self.agent):
            with self.subTest(user=user.login):
                invoice = self._draft_invoice(user)
                invoice.action_post()
                self.assertEqual(invoice.state, "posted")
                invoice.button_draft()
                self.assertEqual(invoice.state, "draft")
                with self.assertRaisesRegex(
                    AccessError,
                    "Irreversible Actions|AI Agents",
                ):
                    invoice.unlink()

    def test_roger_accounting_is_read_only_in_backend(self):
        invoice = self._draft_invoice(self.env.user)
        self.assertEqual(invoice.with_user(self.roger).read(["name"])[0]["name"], invoice.name)
        with self.assertRaises(AccessError):
            invoice.with_user(self.roger).write({"ref": "must not change"})
        with self.assertRaises(AccessError):
            self.env["account.move"].with_user(self.roger).create(
                {
                    "move_type": "entry",
                    "company_id": self.company.id,
                    "journal_id": self.company_data["default_journal_misc"].id,
                },
            )

    def test_roger_accounting_views_expose_read_only_controls(self):
        list_arch = self.env["account.move"].with_user(self.roger).get_view(
            view_type="list",
        )["arch"]
        self.assertIn('create="false"', list_arch)
        self.assertIn('edit="false"', list_arch)
        self.assertIn('delete="false"', list_arch)

        form_arch = self.env["account.move"].with_user(self.roger).get_view(
            view_type="form",
        )["arch"]
        self.assertIn('create="false"', form_arch)
        self.assertIn('edit="false"', form_arch)
        self.assertIn('delete="false"', form_arch)

    def test_lock_dates_and_security_changes_require_irreversible_actions(self):
        for user in (self.roger, self.prosper, self.agent):
            with self.subTest(user=user.login), self.assertRaisesRegex(
                AccessError,
                "Irreversible Actions|AI Agents",
            ):
                self.company.with_user(user).write(
                    {"fiscalyear_lock_date": "2025-12-31"},
                )
        self.company.with_user(self.valentin).write(
            {"fiscalyear_lock_date": "2025-12-31"},
        )
        self.assertEqual(str(self.company.fiscalyear_lock_date), "2025-12-31")
        event = self.env["usl.audit.event"].sudo().search(
            [
                ("actor_id", "=", self.valentin.id),
                ("action_key", "=", "guard:accounting.lock.change"),
            ],
            limit=1,
        )
        self.assertTrue(event)
        self.assertTrue(event.policy_digest)

        with self.assertRaisesRegex(AccessError, "Irreversible Actions"):
            self.agent.with_user(self.roger).write(
                {"group_ids": [Command.link(self.groups["irreversible_actions"].id)]},
            )

    def test_sudo_does_not_bypass_actor_or_technical_guards(self):
        with self.assertRaisesRegex(AccessError, "AI Agents"):
            self.env["res.company"].with_user(self.agent).sudo().create(
                {"name": "Forbidden Agent Company"},
            )
        with self.assertRaisesRegex(AccessError, "Irreversible Actions"):
            self.env["res.company"].with_user(self.roger).sudo().create(
                {"name": "Forbidden Technical Company"},
            )
        with self.assertRaisesRegex(AccessError, "Irreversible Actions"):
            self.env["ir.config_parameter"].with_user(self.roger).sudo().set_str(
                "usl.access.probe",
                "forbidden",
            )
        server_action = self.env["ir.actions.server"].with_user(self.valentin).create(
            {
                "name": "Unreviewed execution probe",
                "model_id": self.env["ir.model"]._get_id("project.task"),
                "state": "code",
                "code": "action = False",
            },
        )
        with self.assertRaisesRegex(AccessError, "AI Agents"):
            server_action.with_user(self.agent).sudo().run()

    def test_reviewed_operational_server_action_does_not_require_capability(self):
        project = self.env["project.project"].create(
            {"name": "Reviewed server action", "company_id": self.company.id},
        )
        action = self.env.ref("project.action_server_share_project").with_user(
            self.agent,
        ).with_context(
            active_id=project.id,
            active_ids=project.ids,
            active_model="project.project",
        )

        result = action.run()

        self.assertEqual(result["res_model"], "project.share.wizard")

    def test_reviewed_destructive_server_action_stays_protected(self):
        action = self.env.ref(
            "privacy_lookup.ir_actions_server_unlink_all",
        ).with_user(self.roger).sudo()

        with self.assertRaisesRegex(AccessError, "Irreversible Actions"):
            action.run()

    def test_framework_and_user_cleanup_are_operational(self):
        attachment = self.env["ir.attachment"].with_user(self.agent).create(
            {
                "name": "Disposable draft.txt",
                "raw": b"disposable",
                "mimetype": "text/plain",
            },
        )
        attachment.with_user(self.agent).unlink()
        self.assertFalse(attachment.exists())

        saved_filter = self.env["ir.filters"].with_user(self.agent).create(
            {
                "name": "Disposable filter",
                "model_id": "project.task",
                "domain": "[]",
                "context": "{}",
            },
        )
        saved_filter.with_user(self.agent).unlink()
        self.assertFalse(saved_filter.exists())

    def test_business_record_attachment_evidence_stays_protected(self):
        project = self.env["project.project"].create(
            {"name": "Attachment evidence", "company_id": self.company.id},
        )
        attachment = self.env["ir.attachment"].create(
            {
                "name": "governed-evidence.txt",
                "raw": b"governed evidence",
                "mimetype": "text/plain",
                "res_model": project._name,
                "res_id": project.id,
            },
        )

        with self.assertRaisesRegex(AccessError, "AI Agents"):
            attachment.with_user(self.agent).sudo().unlink()
        self.assertTrue(attachment.exists())
        attachment.with_user(self.valentin).unlink()
        self.assertFalse(attachment.exists())

    def test_validated_pocketid_login_does_not_require_irreversible_permission(self):
        provider = self.env.ref("usl_pocketid.provider_pocketid")
        provider._usl_pocketid_environment_write(
            {
                "enabled": True,
                "usl_oidc_issuer": "https://identity.example.test",
            },
        )
        user = self._create_user("access.signer", self.env.ref("base.group_user"))
        user.write(
            {
                "usl_pocketid_access": True,
                "usl_identity_classification": "active",
            },
        )
        identity = self.env["usl.oidc.identity"].create(
            {
                "issuer": provider.usl_oidc_issuer,
                "subject": "access-signing-subject",
                "provider_id": provider.id,
                "user_id": user.id,
            },
        )
        with self.assertRaisesRegex(AccessError, "Irreversible Actions"):
            identity.with_user(self.roger).sudo().write(
                {"last_display_name": "Untrusted direct edit"},
            )

        _database, _login, _token, resolved_identity = (
            self.env["res.users"].with_user(self.roger)._usl_pocketid_login(
                provider,
                {
                    "iss": provider.usl_oidc_issuer,
                    "sub": identity.subject,
                    "email": user.email,
                    "name": "Validated signer",
                },
                "validated-access-token",
            )
        )

        self.assertEqual(resolved_identity, identity)
        self.assertEqual(identity.last_display_name, "Validated signer")
        self.assertTrue(identity.last_login_at)

    def test_module_lifecycle_and_code_import_are_guarded_at_direct_entry(self):
        module = self.env["ir.module.module"].search([("name", "=", "base")], limit=1)
        self.assertTrue(module)
        with self.assertRaisesRegex(AccessError, "AI Agents"):
            module.with_user(self.agent).sudo().button_install()
        with self.assertRaisesRegex(AccessError, "AI Agents"):
            module.with_user(self.agent).sudo()._import_zipfile(BytesIO(b"not a module"))

    def test_internal_superuser_can_recover_when_policy_loading_fails(self):
        with patch(
            "odoo.addons.usl_access_control.models.base.load_action_policy",
            side_effect=RuntimeError("broken policy fixture"),
        ) as loader:
            partner = self.env["res.partner"].with_user(SUPERUSER_ID).create(
                {"name": "Policy recovery probe"},
            )
            self.env["base"].with_user(SUPERUSER_ID)._usl_require_irreversible_action(
                "module.install",
            )
            partner.unlink()
        loader.assert_not_called()

    def test_external_registration_actions_are_guarded_before_provider_calls(self):
        settings = self.env["res.config.settings"].with_user(self.agent).sudo().create(
            {"company_id": self.company.id},
        )
        with self.assertRaisesRegex(AccessError, "AI Agents"):
            settings.button_peppol_deregister()
        pdp = self.env["pdp.registration"].with_user(self.roger).sudo().create(
            {"company_id": self.company.id},
        )
        with self.assertRaisesRegex(AccessError, "Irreversible Actions"):
            pdp.button_register_pdp_participant()
        service_registration = self.env.ref(
            "account_peppol_response.ir_cron_peppol_auto_register_services_ir_actions_server",
        ).with_user(self.roger).sudo()
        proxy_user_model = type(self.env["account_edi_proxy_client.user"])
        with patch.object(
            proxy_user_model,
            "_cron_peppol_auto_register_services",
            autospec=True,
        ) as provider_call, self.assertRaisesRegex(
            AccessError,
            "Irreversible Actions",
        ):
            service_registration.run()
        provider_call.assert_not_called()

    def test_official_letter_finalization_is_guarded_before_rendering(self):
        recipient = self.env["res.partner"].create(
            {
                "name": "Official recipient",
                "street": "20 avenue Victor Hugo",
                "zip": "69002",
                "city": "Lyon",
                "country_id": self.env.ref("base.fr").id,
                "lang": "fr_FR",
            }
        )
        letter = self.env["usl.document.letter"].create(
            {
                "recipient_id": recipient.id,
                "subject": "Governed correspondence",
                "signatory_id": self.env.user.id,
                "signatory_title": "Présidence",
                "body": "<p>Official content.</p>",
            }
        )
        renderer = self.env["usl.document.renderer"]
        render_result = {
            "pdf": b"%PDF-1.7\n%%EOF\n",
            "template_revision": "test-revision",
            "payload_sha256": "a" * 64,
            "renderer_version": "1.0.0",
        }
        company_payload = {
            "name": self.company.name,
            "legal_identity_lines": ["Test identity"],
            "primary_color": "714B67",
            "footer_label": self.company.name,
        }
        with (
            patch.object(
                type(self.company),
                "_usl_document_renderer_company_payload",
                return_value=(company_payload, []),
            ),
            patch.object(type(renderer), "render", return_value=render_result) as render,
        ):
            with self.assertRaisesRegex(AccessError, "AI Agents"):
                letter.with_user(self.agent).sudo().action_finalize()
            render.assert_not_called()
            letter.with_user(self.valentin).sudo().action_finalize()

        self.assertEqual(letter.state, "finalized")
        self.assertTrue(
            self.valentin.has_group(
                "usl_document_templates.group_document_letter_manager"
            )
        )
        event = self.env["usl.audit.event"].sudo().search(
            [
                ("actor_id", "=", self.valentin.id),
                ("action_key", "=", "guard:documents.letter.finalize"),
            ],
            limit=1,
        )
        self.assertTrue(event)
        self.assertEqual(event.outcome, "succeeded")

    def test_official_letter_view_hides_irreversible_lifecycle_actions(self):
        letter_manager = self._create_user(
            "access.letter.manager",
            self.env.ref("usl_document_templates.group_document_letter_manager"),
        )
        view = self.env.ref("usl_document_templates.view_document_letter_form")
        ordinary_arch = etree.fromstring(
            self.env["usl.document.letter"]
            .with_user(letter_manager)
            .get_view(view_id=view.id, view_type="form")["arch"],
        )
        self.assertFalse(ordinary_arch.xpath("//button[@name='action_finalize']"))
        self.assertFalse(ordinary_arch.xpath("//button[@name='action_mark_sent']"))
        self.assertFalse(ordinary_arch.xpath("//button[@string='Cancel Issued']"))
        self.assertEqual(len(ordinary_arch.xpath("//button[@name='action_cancel']")), 1)

        authorized_arch = etree.fromstring(
            self.env["usl.document.letter"]
            .with_user(self.valentin)
            .get_view(view_id=view.id, view_type="form")["arch"],
        )
        self.assertTrue(authorized_arch.xpath("//button[@name='action_finalize']"))
        self.assertTrue(authorized_arch.xpath("//button[@name='action_mark_sent']"))
        self.assertTrue(authorized_arch.xpath("//button[@string='Cancel Issued']"))

    def test_company_rules_remain_separate_from_roles(self):
        other_invoice = self._draft_invoice(self.env.user, company=self.other_company)
        with self.assertRaises(AccessError):
            other_invoice.with_user(self.prosper).read(["name"])
        self.assertEqual(
            other_invoice.with_user(self.roger).with_company(self.other_company).read(["name"])[0]["name"],
            other_invoice.name,
        )

    def test_audit_history_is_immutable(self):
        project = self.env["project.project"].create(
            {"name": "Audit integrity", "company_id": self.company.id},
        )
        self.env["project.task"].with_user(self.agent).create(
            {"name": "Audited", "project_id": project.id},
        )
        event = self.env["usl.audit.event"].sudo().search(
            [("actor_id", "=", self.agent.id)],
            limit=1,
        )
        with self.assertRaisesRegex(UserError, "immutable"):
            event.with_user(self.agent).write({"origin": "forged"})
        with self.assertRaisesRegex(UserError, "cannot be deleted"):
            event.with_user(self.roger).unlink()
        with self.assertRaisesRegex(UserError, "immutable"):
            event.with_user(self.valentin).write({"origin": "administrator-forgery"})

    def test_new_protected_audit_rows_require_policy_identity(self):
        with self.assertRaisesRegex(ValidationError, "action key and policy digest"):
            self.env["usl.audit.event"]._record_event(
                {
                    "actor_id": self.env.uid,
                    "actor_is_agent": False,
                    "event_type": "protected_action",
                    "model_name": "res.company",
                    "record_count": 0,
                    "operation": "action",
                    "action_name": "unqualified protected action",
                    "origin": "test",
                },
            )

    def test_pre_policy_audit_rows_keep_nullable_policy_identity(self):
        self.env.cr.execute(
            """
            INSERT INTO usl_audit_event (
                occurred_at, actor_id, actor_is_agent, event_type, outcome,
                model_name, record_count, operation, action_name, origin,
                create_uid, create_date, write_uid, write_date
            ) VALUES (
                NOW(), %s, FALSE, 'protected_action', 'succeeded',
                'res.company', 0, 'action', 'legacy protected action', 'migration',
                %s, NOW(), %s, NOW()
            ) RETURNING id
            """,
            [self.env.uid, self.env.uid, self.env.uid],
        )
        event = self.env["usl.audit.event"].browse(self.env.cr.fetchone()[0])
        self.assertFalse(event.action_key)
        self.assertFalse(event.policy_digest)

    def test_agent_mutation_policy_identity_remains_optional(self):
        event = self.env["usl.audit.event"]._record_event(
            {
                "actor_id": self.env.uid,
                "actor_is_agent": True,
                "event_type": "mutation",
                "model_name": "project.task",
                "record_count": 0,
                "operation": "write",
                "action_name": "project.task.write",
                "origin": "agent",
            },
        )
        self.assertFalse(event.action_key)
        self.assertFalse(event.policy_digest)

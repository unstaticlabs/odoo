"""Native template resets are writes under the caller's delegated authority."""

from types import MethodType, SimpleNamespace
from unittest.mock import patch

from odoo import SUPERUSER_ID, Command
from odoo.exceptions import AccessError
from odoo.http.requestlib import Request, _request_stack
from odoo.tests import TransactionCase, tagged
from odoo.tools import convert_file

from ..controllers.json2 import UslAgentJson2Controller
from ..exceptions import AgentPolicyAccessError


@tagged("post_install", "-at_install", "usl_access_control")
class TestNativeTemplateReset(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "Template QA Company"})
        cls.settings = cls.env.ref("base.group_system")
        cls.editor = cls.env.ref("mail.group_mail_template_editor")
        cls.owner = cls._user(
            "template.reset.owner", cls.settings | cls.editor,
            companies=cls.company | cls.other_company,
        )
        cls.reader = cls._user("template.reset.reader", cls.env.ref("base.group_user"))
        cls.portal = cls._user("template.reset.portal", cls.env.ref("base.group_portal"))
        for module in ("mail", "sms"):
            convert_file(
                cls.env, module, f"tests/test_{module}_template.xml", {},
                mode="init", noupdate=False,
            )
        cls.env["res.lang"]._activate_lang("fr_FR")
        cls.mail_template = cls.env.ref("mail.mail_template_test")
        cls.sms_template = cls.env.ref("sms.sms_template_test")

    @classmethod
    def _user(cls, login, groups, companies=None):
        companies = companies or cls.company
        return cls.env["res.users"].with_user(SUPERUSER_ID).with_context(
            no_reset_password=True, usl_governed_identity_provisioning=True,
        ).create({
            "name": login,
            "login": login,
            "email": f"{login}@example.test",
            "share": bool(groups & cls.env.ref("base.group_portal")),
            "company_id": companies[0].id,
            "company_ids": [Command.set(companies.ids)],
            "group_ids": [Command.set(groups.ids)],
        })

    def _agent(self):
        return self.env["usl.agent"].with_user(self.owner).create({
            "name": "Template reset QA Agent",
            "purpose": "Exercise native template reset with delegated access.",
            "owner_id": self.owner.id,
            "company_id": self.company.id,
            "company_ids": [Command.set((self.company | self.other_company).ids)],
            "delegated_group_ids": [Command.set((self.settings | self.editor).ids)],
            "read_only_group_ids": [Command.clear()],
            "access_mode": "read_write",
        })

    def _templates(self):
        return ((self.mail_template, "body_html"), (self.sms_template, "body"))

    def _edit(self, template, field):
        template.with_context(lang="en_US").write({field: "<div>Edited EN</div>"})
        template.with_context(lang="fr_FR").write({field: "<div>Edited FR</div>"})

    def _bodies(self, template, field):
        return tuple(str(template.with_context(lang=lang)[field]).strip()
                     for lang in ("en_US", "fr_FR"))

    def _assert_reset(self, template, field):
        self.assertEqual(self._bodies(template, field),
                         ("<div>Hello Odoo</div>", "<div>Hello Odoo</div>"))

    def _wizard(self, template, user):
        return self.env[f"{template._name}.reset"].with_user(user).create({
            "template_ids": [Command.set(template.ids)],
        })

    def _dispatch(self, agent, model, method, ids):
        state = SimpleNamespace(
            env=self.env(user=agent.user_id, context={}),
            db=self.env.cr.dbname,
            registry=self.env.registry,
            httprequest=SimpleNamespace(
                headers={}, remote_addr="127.0.0.1",
                user_agent=SimpleNamespace(string="template-reset-test"),
            ),
        )
        state.update_env = MethodType(Request.update_env, state)
        previous_env = self.env.transaction.default_env
        controller = UslAgentJson2Controller()
        _request_stack.push(state)
        try:
            # The audit's separate transaction cannot see uncommitted test
            # identities. Keep the real dispatch and inspect its audit boundary.
            with patch.object(controller, "_record_agent_api_call") as audit:
                result = controller.web_json_2_rpc.__wrapped__(
                    controller, model, method, ids=ids,
                )
                self.assertEqual(audit.call_args.kwargs["outcome"], "succeeded")
                return result
        finally:
            _request_stack.pop()
            self.env.transaction.default_env = previous_env

    def test_authorized_human_resets_source_values_and_translations(self):
        for template, field in self._templates():
            for wizard in (False, True):
                with self.subTest(model=template._name, wizard=wizard):
                    self._edit(template, field)
                    target = (self._wizard(template, self.owner) if wizard
                              else template.with_user(self.owner))
                    target.reset_template()
                    self._assert_reset(template, field)

    def test_authorized_writer_agent_resets_directly_and_through_wizards(self):
        agent = self._agent()
        for template, field in self._templates():
            for wizard in (False, True):
                with self.subTest(model=template._name, wizard=wizard):
                    self._edit(template, field)
                    target = self._wizard(template, agent.user_id) if wizard else template
                    self.assertEqual(
                        UslAgentJson2Controller._check_agent_call(
                            agent=agent, model_name=target._name,
                            method_name="reset_template", kwargs={},
                        ),
                        "write",
                    )
                    self._dispatch(agent, target._name, "reset_template", target.ids)
                    self._assert_reset(template, field)

    def test_readonly_profile_refuses_all_reset_entry_points(self):
        agent = self._agent()
        targets = [template for template, _field in self._templates()]
        targets.extend(self._wizard(template, agent.user_id) for template in list(targets))
        agent.with_user(self.owner).action_grant_all_read()
        self.assertEqual(agent.access_mode, "read_only")
        for template, field in self._templates():
            self._edit(template, field)
        before = [self._bodies(template, field) for template, field in self._templates()]
        # The abstract mixin is also advertised by the generated API surface;
        # it must never be presented as a read-only reset, even without records.
        models = [target._name for target in targets] + ["template.reset.mixin"]
        for model in models:
            with self.subTest(model=model):
                with self.assertRaises(AgentPolicyAccessError) as denied:
                    UslAgentJson2Controller._check_agent_call(
                        agent=agent, model_name=model,
                        method_name="reset_template", kwargs={},
                    )
                self.assertEqual(denied.exception.context["usl_code"],
                                 "agent_read_only_action_denied")
        for target in targets:
            with self.subTest(dispatch=target._name):
                with self.assertRaises(AgentPolicyAccessError):
                    self._dispatch(agent, target._name, "reset_template", target.ids)
        self.assertEqual(
            [self._bodies(template, field) for template, field in self._templates()], before,
        )

    def test_native_reader_and_portal_denials_preserve_templates(self):
        self._edit(self.sms_template, "body")
        before = self._bodies(self.sms_template, "body")
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.sms_template.with_user(self.reader).reset_template()
        self.assertEqual(self._bodies(self.sms_template, "body"), before)
        for template, field in self._templates():
            self._edit(template, field)
            before = self._bodies(template, field)
            with self.assertRaises(AccessError), self.env.cr.savepoint():
                template.with_user(self.portal).reset_template()
            with self.assertRaises(AccessError), self.env.cr.savepoint():
                self._wizard(template, self.portal)
            self.assertEqual(self._bodies(template, field), before)

    def test_native_non_editor_cannot_reset_another_users_mail_template(self):
        self.env["ir.config_parameter"].set_bool("mail.restrict.template.rendering", True)
        self.assertFalse(self.reader.has_group("mail.group_mail_template_editor"))
        self._edit(self.mail_template, "body_html")
        before = self._bodies(self.mail_template, "body_html")
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.mail_template.with_user(self.reader).reset_template()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self._wizard(self.mail_template, self.reader)
        self.assertEqual(self._bodies(self.mail_template, "body_html"), before)

    def test_global_templates_keep_native_cross_company_editor_access(self):
        # Mail/SMS templates are global, not company-owned business records.
        # The metadata correction must not invent a company-specific gate.
        for template, field in self._templates():
            self._edit(template, field)
            template.with_user(self.owner).with_company(self.other_company).reset_template()
            self._assert_reset(template, field)

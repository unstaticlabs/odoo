import hashlib

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller

ORIGINAL = b"Synthetic native chatter original.\n"
REVISED = b"Synthetic native chatter revised.\n"


@tagged("post_install", "-at_install", "usl_native_agent_chatter")
class TestNativeAgentChatter(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "[QA] Chatter Other"})
        cls.project_group = cls.env.ref("project.group_project_manager")
        cls.account_group = cls.env.ref("account.group_account_manager")
        cls.groups = cls.project_group | cls.account_group
        cls.owner = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "[QA] Native Chatter Owner", "login": "qa.native.chatters.unit",
            "company_id": cls.company.id,
            "company_ids": [Command.set((cls.company | cls.other_company).ids)],
            "group_ids": [Command.set(cls.groups.ids)],
        })
        cls.agents = {}
        for name, readonly in (
            ("rw", cls.env["res.groups"]), ("ro", cls.groups),
            ("project_ro", cls.project_group), ("account_ro", cls.account_group),
        ):
            cls.agents[name] = cls.env["usl.agent"].with_user(cls.owner).create({
                "name": "[QA] Chatter " + name,
                "purpose": "Native chatter capability regression.",
                "company_id": cls.company.id,
                "company_ids": [Command.set((cls.company | cls.other_company).ids)],
                "delegated_group_ids": [Command.set(cls.groups.ids)],
                "read_only_group_ids": [Command.set(readonly.ids)],
                "access_mode": "read_write",
            })
        cls.tasks = cls.env["project.task"]
        for company in (cls.company, cls.other_company):
            project = cls.env["project.project"].create({
                "name": "[QA] Chatter Project", "company_id": company.id,
                "privacy_visibility": "employees",
            })
            cls.tasks |= cls.env["project.task"].create({
                "name": "[QA] Chatter Task", "project_id": project.id,
            })
        account = cls.env["account.account"].create({
            "name": "[QA] Chatter Expense", "code": "QAT625",
            "account_type": "expense", "company_ids": [Command.set(cls.company.ids)],
        })
        payable = cls.env["account.account"].create({
            "name": "[QA] Chatter Payable", "code": "QAT401",
            "account_type": "liability_payable", "reconcile": True,
            "company_ids": [Command.set(cls.company.ids)],
        })
        vendor = cls.env["res.partner"].create({"name": "[QA] Invented Chatter Vendor"})
        vendor.with_company(cls.company).property_account_payable_id = payable
        journal = cls.env["account.journal"].create({
            "name": "[QA] Chatter Purchases", "code": "QAATT", "type": "purchase",
            "company_id": cls.company.id,
        })
        cls.bill = cls.env["account.move"].with_company(cls.company).create({
            "move_type": "in_invoice", "partner_id": vendor.id, "journal_id": journal.id,
            "invoice_date": "2026-01-15",
            "invoice_line_ids": [Command.create({
                "name": "[QA] Invented expense", "account_id": account.id,
                "quantity": 1, "price_unit": 18, "tax_ids": [Command.clear()],
            })],
        })

    def _call(self, method, *, actor="rw", ids=None, context=None, **kwargs):
        agent = self.agents[actor]
        self.env["usl.agent"].with_user(agent.user_id).current_identity()
        access = UslAgentJson2Controller._check_agent_call(
            agent=agent, model_name="mail.message", method_name=method, kwargs=kwargs,
        )
        call_context = UslAgentJson2Controller._agent_call_context(
            context={"allowed_company_ids": agent.company_ids.ids, **(context or {})},
            agent=agent, model_name="mail.message", method_name=method, access=access,
        )
        records = self.env["mail.message"].with_user(agent.user_id).with_context(call_context)
        if ids is not None:
            records = records.browse(ids)
        return getattr(records, method)(**kwargs)

    def _values(self, parent=None, **values):
        parent = parent if parent is not None else self.tasks[0]
        return {
            "model": parent._name if parent else False,
            "res_id": parent.id if parent else False,
            "message_type": "comment", "body": "<p>[QA] Native note</p>",
            **values,
        }

    def test_native_human_and_agent_create_read_write_task_and_bill(self):
        for parent in (self.tasks[0], self.bill):
            with self.subTest(model=parent._name):
                human = self.env["mail.message"].with_user(self.owner).create(self._values(parent))
                self.assertTrue(human.write({"subject": "[QA] Native human subject"}))
                message = self._call("create", vals_list=[self._values(parent)])
                self.assertEqual(message.create_uid, self.agents["rw"].user_id)
                self.assertTrue(self._call("write", ids=message.ids, vals={"body": "<p>[QA] Revised</p>", "subject": "[QA] Native subject"}))
                self.assertEqual(message.body, "<p>[QA] Revised</p>")
                self.assertEqual(self._call("read", ids=message.ids, fields=["subject"])[0]["subject"], "[QA] Native subject")

    def test_native_author_and_recipient_paths_without_parent_write(self):
        agent = self.agents["project_ro"]
        self.assertFalse(self.tasks[0].with_user(agent.user_id).has_access("write"))
        for values in (
            {"author_id": agent.user_id.partner_id.id},
            {"partner_ids": [Command.link(agent.user_id.partner_id.id)]},
            {"notification_ids": [Command.create({
                "res_partner_id": agent.user_id.partner_id.id,
                "notification_type": "inbox",
            })]},
        ):
            with self.subTest(values=values):
                message = self.env["mail.message"].create(self._values(**values))
                self.assertTrue(self._call("write", actor="project_ro", ids=message.ids, vals={"body": "<p>[QA] Native relationship edit</p>"}))
        unrelated = self.env["mail.message"].create(self._values())
        with self.assertRaises(AccessError):
            self._call("write", actor="project_ro", ids=unrelated.ids, vals={"body": "[QA] Denied unrelated"})
        self.assertEqual(unrelated.body, "<p>[QA] Native note</p>")
        original = message.body
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self._call("write", actor="project_ro", ids=(message | unrelated).ids,
                       vals={"body": "[QA] Whole batch denied"})
        self.assertEqual(message.body, original)
        self.assertEqual(unrelated.body, "<p>[QA] Native note</p>")

    def test_native_read_post_access_and_mixed_readonly(self):
        for actor, parent in (("project_ro", self.tasks[0]), ("account_ro", self.bill)):
            with self.subTest(actor=actor):
                self.assertEqual(parent._mail_post_access, "read")
                self.assertTrue(self._call("create", actor=actor, vals_list=[self._values(parent)]))
        for operation, kwargs in (("create", {"vals_list": [self._values()]}), ("write", {"ids": [], "vals": {}})):
            with self.assertRaises(AccessError):
                self._call(operation, actor="ro", **kwargs)

    def test_generic_native_write_has_no_optional_repair_window_or_size_gate(self):
        from datetime import timedelta
        from odoo import fields
        message = self._call("create", vals_list=[self._values()])
        self.env.cr.execute("UPDATE mail_message SET create_date=%s WHERE id=%s", [fields.Datetime.now()-timedelta(days=2), message.id])
        message.invalidate_recordset(["create_date"])
        body = "<p>" + "[QA] Longer native note " * 2500 + "</p>"
        self.assertTrue(self._call("write", ids=message.ids, vals={"body": body}))
        self.assertEqual(message.body, body)
        with self.assertRaises(UserError):
            self.tasks[0].with_user(self.agents["rw"].user_id).mcp_revise_own_message(message.id, "[QA] Optional repair remains bounded")

    def test_native_standalone_recipient_and_reassignment_rules(self):
        agent = self.agents["rw"]
        message = self._call("create", vals_list=[self._values(self.env["project.task"], partner_ids=[Command.link(self.owner.partner_id.id)])])
        self.assertTrue(self._call("write", ids=message.ids, vals={"body": "<p>[QA] Native standalone edit</p>"}))
        with self.assertRaises(AccessError):
            self._call("write", ids=message.ids, vals={"model": "project.task", "res_id": self.tasks[0].id})
        self.assertFalse(message.model)

    def test_native_batch_rollback_and_company_context(self):
        agent = self.agents["rw"]
        agent.with_user(self.owner).write({"company_ids": [Command.set(self.company.ids)]})
        subject = "[QA] Mixed denied batch"
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self._call("create", vals_list=[self._values(subject=subject), self._values(self.tasks[1], subject=subject)])
        self.assertFalse(self.env["mail.message"].search_count([("subject", "=", subject)]))
        with self.assertRaises(AccessError):
            self._call("create", vals_list=[self._values()], context={"allowed_company_ids": self.other_company.ids})

    def test_native_attachments_and_protected_unlink(self):
        file = self.env["ir.attachment"].with_user(self.owner).create({"name": "[QA] Chatter receipt.txt", "raw": ORIGINAL, "res_model": "project.task", "res_id": self.tasks[0].id})
        message = self._call("create", vals_list=[self._values(attachment_ids=[Command.link(file.id)])])
        self.assertEqual(message.attachment_ids, file)
        self.assertEqual(bytes(file.raw), ORIGINAL)
        with self.assertRaises(AccessError):
            self._call("unlink", ids=message.ids)
        self.assertTrue(message.exists())

    def test_forged_context_does_not_make_readonly_writable(self):
        for marker in (None, False, True, "forged", {"access": "write"}):
            with self.subTest(marker=marker), self.assertRaises(AccessError):
                self._call("create", actor="ro", context={"usl_agent_collaboration_token": marker}, vals_list=[self._values()])

    def test_native_metadata_writes_keep_author_choices_and_attribution(self):
        message = self._call("create", vals_list=[self._values()])
        values = {"author_id": self.owner.partner_id.id, "email_from": "qa-native@example.invalid"}
        human = self.env["mail.message"].with_user(self.owner).create(self._values())
        self.assertTrue(human.write(values))
        self.assertTrue(self._call("write", ids=message.ids, vals=values))
        self.assertEqual(message.author_id, human.author_id)
        self.assertEqual(message.email_from, human.email_from)
        self.assertEqual(message.create_uid, self.agents["rw"].user_id)

    def test_native_restricted_accounting_audit_message_cannot_be_changed(self):
        self.bill.action_post()
        self.company.restrictive_audit_trail = True
        message = self.env["mail.message"].create(self._values(
            self.bill, message_type="tracking", body="<p>[QA] Restricted audit</p>",
        ))
        self.assertTrue(message.account_audit_log_restricted)
        for actor in ("human", "rw"):
            with self.subTest(actor=actor), self.assertRaises(UserError):
                if actor == "human":
                    message.with_user(self.owner).write({"body": "[QA] Denied"})
                else:
                    self._call("write", ids=message.ids, vals={"body": "[QA] Denied"})
        self.assertEqual(message.body, "<p>[QA] Restricted audit</p>")
        self.assertEqual(self.bill.state, "posted")

    def test_native_inline_html_attachment_extraction_keeps_image_bytes(self):
        import base64
        image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg=="
        message = self._call("create", vals_list=[self._values(
            body='<p>[QA] Inline image</p><img src="data:image/png;base64,' + image + '">',
        )])
        self.assertEqual(len(message.attachment_ids), 1)
        self.assertEqual(bytes(message.attachment_ids.raw), base64.b64decode(image))
        self.assertNotIn("data:image", message.body)
        self.assertIn("/web/image/", message.body)

    def test_native_attachment_failure_rolls_back_whole_message(self):
        agent = self.agents["rw"]
        agent.with_user(self.owner).write({"company_ids": [Command.set(self.company.ids)]})
        foreign = self.env["ir.attachment"].create({
            "name": "[QA] Foreign attachment.txt", "raw": ORIGINAL,
            "res_model": "project.task", "res_id": self.tasks[1].id,
            "company_id": self.other_company.id,
        })
        subject = "[QA] Attachment failure must roll back"
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self._call("create", vals_list=[self._values(
                subject=subject, attachment_ids=[Command.link(foreign.id)],
            )])
        self.assertFalse(self.env["mail.message"].search_count([("subject", "=", subject)]))
        self.assertEqual(bytes(foreign.raw), ORIGINAL)

    def test_native_received_parent_reply_can_use_native_fallback(self):
        agent = self.agents["rw"]
        agent.with_user(self.owner).write({"company_ids": [Command.set(self.company.ids)]})
        parent = self.env["mail.message"].create({
            "message_type": "comment", "body": "<p>[QA] Received private parent</p>",
            "partner_ids": [Command.set((agent.user_id.partner_id | self.owner.partner_id).ids)],
        })
        values = self._values(self.tasks[1], parent_id=parent.id)
        human = self.env["mail.message"].with_user(self.owner).create(values)
        reply = self._call("create", vals_list=[values])
        self.assertEqual((reply.model, reply.res_id), (human.model, human.res_id))
        self.assertEqual(reply.parent_id, parent)

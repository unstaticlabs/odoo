import hashlib

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller

ORIGINAL = b"Synthetic native attachment original.\n"
REVISED = b"Synthetic native attachment revised.\n"


@tagged("post_install", "-at_install", "usl_native_agent_attachments")
class TestNativeAgentAttachments(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "[QA] Attachment Other"})
        cls.project_group = cls.env.ref("project.group_project_manager")
        cls.account_group = cls.env.ref("account.group_account_manager")
        cls.groups = cls.project_group | cls.account_group
        cls.owner = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "[QA] Native Attachment Owner", "login": "qa.native.attachments.unit",
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
                "name": "[QA] Attachment " + name,
                "purpose": "Native attachment capability regression.",
                "company_id": cls.company.id,
                "company_ids": [Command.set((cls.company | cls.other_company).ids)],
                "delegated_group_ids": [Command.set(cls.groups.ids)],
                "read_only_group_ids": [Command.set(readonly.ids)],
                "access_mode": "read_write",
            })
        cls.tasks = cls.env["project.task"]
        for company in (cls.company, cls.other_company):
            project = cls.env["project.project"].create({
                "name": "[QA] Attachment Project", "company_id": company.id,
                "privacy_visibility": "employees",
            })
            cls.tasks |= cls.env["project.task"].create({
                "name": "[QA] Attachment Task", "project_id": project.id,
            })
        account = cls.env["account.account"].create({
            "name": "[QA] Attachment Expense", "code": "QAT625",
            "account_type": "expense", "company_ids": [Command.set(cls.company.ids)],
        })
        payable = cls.env["account.account"].create({
            "name": "[QA] Attachment Payable", "code": "QAT401",
            "account_type": "liability_payable", "reconcile": True,
            "company_ids": [Command.set(cls.company.ids)],
        })
        vendor = cls.env["res.partner"].create({"name": "[QA] Invented Attachment Vendor"})
        vendor.with_company(cls.company).property_account_payable_id = payable
        journal = cls.env["account.journal"].create({
            "name": "[QA] Attachment Purchases", "code": "QAATT", "type": "purchase",
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
            agent=agent, model_name="ir.attachment", method_name=method, kwargs=kwargs,
        )
        call_context = UslAgentJson2Controller._agent_call_context(
            context={"allowed_company_ids": agent.company_ids.ids, **(context or {})},
            agent=agent, model_name="ir.attachment", method_name=method, access=access,
        )
        records = self.env["ir.attachment"].with_user(agent.user_id).with_context(call_context)
        if ids is not None:
            records = records.browse(ids)
        return getattr(records, method)(**kwargs)

    def _values(self, parent=None, **values):
        parent = parent if parent is not None else self.tasks[0]
        return {
            "name": "[QA] Native attachment.txt", "raw": ORIGINAL, "mimetype": "text/plain",
            "res_model": parent._name if parent else False,
            "res_id": parent.id if parent else False, **values,
        }

    def _fingerprint(self):
        self.env.flush_all()
        result = {}
        for table in ("account_move", "account_move_line", "account_partial_reconcile", "account_full_reconcile"):
            self.env.cr.execute(f'SELECT to_jsonb(t) FROM "{table}" t ORDER BY id')
            result[table] = self.env.cr.fetchall()
        return result

    def test_native_task_and_bill_creation_keep_exact_content_and_attribution(self):
        before = self._fingerprint()
        for parent in (self.tasks[0], self.bill):
            with self.subTest(parent=parent._name):
                human = self.env["ir.attachment"].with_user(self.owner).create(self._values(parent))
                agent_file = self._call("create", vals_list=[self._values(parent)])
                self.assertEqual(bytes(human.raw), ORIGINAL)
                self.assertEqual(bytes(agent_file.raw), ORIGINAL)
                self.assertEqual(agent_file.checksum, hashlib.sha1(ORIGINAL).hexdigest())
                self.assertEqual(agent_file.create_uid, self.agents["rw"].user_id)
                self.assertEqual((agent_file.res_model, agent_file.res_id), (parent._name, parent.id))
                events = self.env["usl.audit.event"].search([
                    ("agent_id", "=", self.agents["rw"].id),
                    ("model_name", "=", "ir.attachment"), ("operation", "=", "create"),
                    ("record_ids", "=", str(agent_file.ids)),
                ])
                self.assertEqual(len(events), 1)
        self.assertEqual(self._fingerprint(), before)

    def test_native_rename_content_copy_and_relink_preserve_original(self):
        file = self._call("create", vals_list=[self._values()])
        self.assertTrue(self._call("write", ids=file.ids, vals={"name": "[QA] Renamed.txt"}))
        self.assertTrue(self._call("write", ids=file.ids, vals={"raw": REVISED}))
        self.assertEqual(bytes(file.raw), REVISED)
        copy = self._call("copy", ids=file.ids, default={"res_model": "account.move", "res_id": self.bill.id})
        self.assertEqual(bytes(copy.raw), REVISED)
        self.assertEqual(bytes(file.raw), REVISED)
        self.assertTrue(self._call("write", ids=copy.ids, vals={"res_model": "project.task", "res_id": self.tasks[1].id, "company_id": self.other_company.id}))
        self.assertEqual(copy.res_id, self.tasks[1].id)
        self.assertEqual(file.res_id, self.tasks[0].id)

    def test_mixed_profiles_use_each_native_parent_permission(self):
        for actor, allowed, denied in (
            ("project_ro", self.bill, self.tasks[0]),
            ("account_ro", self.tasks[0], self.bill),
        ):
            with self.subTest(actor=actor):
                self.assertTrue(self._call("create", actor=actor, vals_list=[self._values(allowed)]))
                with self.assertRaises(AccessError):
                    self._call("create", actor=actor, vals_list=[self._values(denied)])
                file = self.env["ir.attachment"].create(self._values(denied))
                with self.assertRaises(AccessError):
                    self._call("write", actor=actor, ids=file.ids, vals={"name": "[QA] Must not persist"})
                self.assertEqual(file.name, "[QA] Native attachment.txt")

    def test_batch_failure_has_no_partial_file_or_reassignment(self):
        self.agents["rw"].with_user(self.owner).write({"company_ids": [Command.set(self.company.ids)]})
        name = "[QA] Batch denied attachment"
        with self.assertRaises(AccessError):
            self._call("create", vals_list=[self._values(name=name), self._values(self.tasks[1], name=name)])
        self.assertFalse(self.env["ir.attachment"].search_count([("name", "=", name)]))
        file = self._call("create", vals_list=[self._values()])
        with self.assertRaises(AccessError):
            self._call("write", ids=file.ids, vals={"name": name, "res_model": "project.task", "res_id": self.tasks[1].id})
        self.assertEqual(file.res_id, self.tasks[0].id)
        self.assertEqual(file.name, "[QA] Native attachment.txt")
        self.assertEqual(bytes(file.raw), ORIGINAL)

    def test_global_readonly_and_forged_context_do_not_gain_mutation_access(self):
        file = self.env["ir.attachment"].create(self._values())
        for marker in (None, True, "forged", {"access": "write"}):
            with self.subTest(marker=marker), self.assertRaises(AccessError):
                self._call("create", actor="ro", context={"usl_agent_collaboration_token": marker}, vals_list=[self._values()])
        with self.assertRaises(AccessError):
            file.with_user(self.agents["ro"].user_id).sudo().write({"name": "[QA] Must not persist"})
        self.assertEqual(file.name, "[QA] Native attachment.txt")

    def test_standalone_native_creator_ownership_is_preserved(self):
        standalone = self.env["ir.attachment"]
        human = standalone.with_user(self.owner).create(self._values(standalone))
        self.assertTrue(human.with_user(self.owner).write({"name": "[QA] Human standalone"}))
        agent_file = self._call("create", vals_list=[self._values(standalone)])
        self.assertTrue(self._call("write", ids=agent_file.ids, vals={"name": "[QA] Agent standalone"}))
        with self.assertRaises(AccessError):
            self._call("write", ids=human.ids, vals={"name": "[QA] Other creator denied"})
        self.assertEqual(human.name, "[QA] Human standalone")
        self.assertEqual(bytes(agent_file.raw), ORIGINAL)

    def test_protected_unlink_and_chatter_mapping_remain_unchanged(self):
        file = self._call("create", vals_list=[self._values()])
        with self.assertRaises(AccessError):
            self._call("unlink", ids=file.ids)
        self.assertTrue(file.exists())
        for method in ("create", "write"):
            with self.assertRaises(AccessError):
                UslAgentJson2Controller._check_agent_call(
                    agent=self.agents["rw"], model_name="mail.message", method_name=method, kwargs={},
                )

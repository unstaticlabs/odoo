from uuid import uuid4

from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestQueueJobAdminAccess(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        functions = cls.env["queue.job.function"].sudo()
        model = cls.env["ir.model"]._get("res.partner")
        cls.function = functions.search(
            [("model_id", "=", model.id), ("method", "=", "exists")], limit=1,
        ) or functions.create({"model_id": model.id, "method": "exists"})
        assert cls.function.channel_id

    def _admin(self):
        admin = new_test_user(
            self.env, login=f"queue-admin-{self._testMethodName}",
            groups="base.group_system",
        )
        self.assertNotIn(
            self.env.ref("queue_job.group_queue_job_manager"), admin.all_group_ids,
        )
        return admin

    def _failed_job(self, company=None):
        return self.env["queue.job"].sudo().create(
            {
                "uuid": str(uuid4()),
                "name": "Synthetic failed queue job",
                "model_name": "res.partner",
                "method_name": "exists",
                "job_function_id": self.function.id,
                "channel": self.function.channel,
                "company_id": (company or self.env.company).id,
                "user_id": self.env.user.id,
                "priority": 10,
                "state": "failed",
            }
        )

    def test_settings_admin_can_open_a_failed_job(self):
        admin = self._admin()
        job = self._failed_job().with_user(admin)
        self.assertEqual(job.read(["state"])[0]["state"], "failed")
        # Derive the native notification form's fields, including real relations.
        view = job.get_view(
            view_id=self.env.ref("queue_job.view_queue_job_form").id,
            view_type="form",
        )
        names = set(etree.fromstring(view["arch"]).xpath("//field/@name"))
        spec = {
            name: ({"fields": {"display_name": {}}}
                   if job._fields[name].type == "many2one" else {})
            for name in names
        }
        result = job.web_read(spec)[0]
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["job_function_id"]["id"], self.function.id)
        self.assertTrue(result["job_function_id"]["display_name"])
        self.assertEqual(result["channel"], self.function.channel)
        # Native many2one display labels do not grant configuration access.
        with self.assertRaises(AccessError):
            self.function.with_user(admin).read(["method"])
        with self.assertRaises(AccessError):
            self.function.channel_id.with_user(admin).read(["complete_name"])

    def test_settings_admin_can_edit_priority(self):
        job = self._failed_job().with_user(self._admin())
        job.write({"priority": 7})
        self.assertEqual(job.priority, 7)

    def test_settings_admin_cannot_create_jobs(self):
        jobs = self.env["queue.job"].with_user(self._admin())
        with self.assertRaises(AccessError):
            jobs.create({"uuid": str(uuid4()), "state": "failed"})

    def test_settings_admin_cannot_delete_jobs(self):
        job = self._failed_job().with_user(self._admin())
        with self.assertRaises(AccessError):
            job.unlink()

    def test_internal_user_still_cannot_open_jobs(self):
        user = new_test_user(self.env, login="queue-user", groups="base.group_user")
        with self.assertRaises(AccessError):
            self._failed_job().with_user(user).read(["state"])

    def test_settings_admin_keeps_native_company_rule(self):
        admin = self._admin().with_context(allowed_company_ids=self.env.company.ids)
        other = self.env["res.company"].create({"name": "Synthetic queue company"})
        foreign = self._failed_job(company=other).with_user(admin)
        with self.assertRaises(AccessError):
            foreign.read(["state"])
        with self.assertRaises(AccessError):
            foreign.write({"priority": 7})

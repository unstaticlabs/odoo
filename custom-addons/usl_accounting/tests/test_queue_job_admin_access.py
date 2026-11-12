from uuid import uuid4

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestQueueJobAdminAccess(TransactionCase):
    def _failed_job(self):
        return self.env["queue.job"].sudo().create(
            {
                "uuid": str(uuid4()),
                "name": "Failed job",
                "model_name": "res.partner",
                "method_name": "exists",
                "state": "failed",
            }
        )

    def test_settings_admin_can_open_a_failed_job(self):
        admin = new_test_user(self.env, login="queue-admin", groups="base.group_system")
        manager = self.env.ref("queue_job.group_queue_job_manager")
        self.assertNotIn(manager, admin.all_group_ids)
        job = self._failed_job().with_user(admin)
        self.assertEqual(job.read(["state"])[0]["state"], "failed")
        # What the form view asks for when the notification link is followed.
        spec = {
            "name": {},
            "state": {},
            "job_function_id": {"fields": {"display_name": {}}},
            "channel": {},
            "exc_info": {},
        }
        self.assertEqual(job.web_read(spec)[0]["state"], "failed")

    def test_settings_admin_cannot_delete_jobs(self):
        admin = new_test_user(self.env, login="queue-admin2", groups="base.group_system")
        job = self._failed_job().with_user(admin)
        with self.assertRaises(AccessError):
            job.unlink()

    def test_internal_user_still_cannot_open_jobs(self):
        user = new_test_user(self.env, login="queue-user", groups="base.group_user")
        job = self._failed_job()
        with self.assertRaises(AccessError):
            job.with_user(user).read(["state"])

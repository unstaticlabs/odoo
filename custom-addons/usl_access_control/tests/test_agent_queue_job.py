"""The job runner can start and run a job that an Agent enqueued."""

from contextlib import contextmanager
from email.message import EmailMessage
from unittest.mock import patch

from odoo import SUPERUSER_ID, Command
from odoo.tests import tagged

from ..exceptions import AgentPolicyAccessError
from ..models.agent_policy_tokens import (
    AGENT_OPERATION_SCOPE_CONTEXT_KEY,
    create_agent_operation_scope,
)
from odoo.addons.hr_expense.tests.common import TestExpenseCommon
from odoo.addons.queue_job.controllers.main import RunJobController
from odoo.addons.queue_job.job import Job


def _job_runner():
    """Compose the runner controller the way Odoo composes installed controllers."""
    classes = [RunJobController, *RunJobController.__subclasses__()]
    if len(classes) == 1:
        return RunJobController
    return type("JobRunner", tuple(reversed(classes)), {})


@tagged("post_install", "-at_install", "usl_access_control")
class TestAgentQueueJob(TestExpenseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = (
            cls.env.ref("account.group_account_manager")
            | cls.env.ref("hr_expense.group_hr_expense_manager")
        )
        cls.env.user.group_ids |= groups
        cls.agent = cls.env["usl.agent"].create({
            "name": "[QA queue job] Receipt Agent",
            "purpose": "Select receipt links for employees.",
            "owner_id": cls.env.user.id,
            "company_id": cls.env.company.id,
            "company_ids": [Command.set(cls.env.company.ids)],
            "delegated_group_ids": [Command.set(groups.ids)],
            "read_only_group_ids": [Command.clear()],
            "access_mode": "read_write",
        })

    def _email(self, token):
        message = EmailMessage()
        message["From"] = self.expense_user_employee.email
        message["To"] = "expenses@example.invalid"
        message["Subject"] = f"{self.product_c.default_code} Taxi EUR 12.00"
        message["Message-ID"] = f"<{token}@example.invalid>"
        message.set_content("Download receipt")
        message.add_alternative(
            f"""<html><body><p>Your ride is complete.</p>
            <a href="https://receipts.example.com/trips/0a1b2c3d4e5f6789/download?token={token}&amp;locale=en">
            Download PDF receipt</a></body></html>""",
            subtype="html",
        )
        return message.as_bytes()

    def _agent_owned_job(self, token):
        """Enqueue a receipt fetch the way a governed Agent call does."""
        with patch.dict("os.environ", {"USL_LINKED_PDF_DOWNLOAD_ENABLED": "1"}):
            expense = self.env["mail.thread"].message_process("hr.expense", self._email(token))
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search([("expense_id", "=", expense.id)])
        self.assertEqual(len(retrieval), 1)
        candidate = retrieval._extract_candidates(retrieval.source_message_id)[0]
        retrieval._select_candidate(candidate["fingerprint"], teach=True)
        scope = create_agent_operation_scope(
            agent_user_id=self.agent.user_id.id,
            root_model="usl.mail.pdf.retrieval",
            root_method="action_select_candidate",
            access="write",
        )
        as_agent = retrieval.with_user(self.agent.user_id).with_context(
            **{AGENT_OPERATION_SCOPE_CONTEXT_KEY: scope},
        )
        with patch.object(type(retrieval), "_feature_enabled", return_value=True):
            as_agent._enqueue()
        job = self.env["queue.job"].sudo().search([
            ("identity_key", "=", f"receipt-fetch:{retrieval.id}:{retrieval.generation}"),
        ])
        self.assertEqual(len(job), 1)
        self.assertEqual(job.user_id, self.agent.user_id)
        self.assertEqual(job.state, "pending")
        # The runner marks a job enqueued before it asks a worker to run it.
        job.write({"state": "enqueued"})
        job.flush_recordset()
        return retrieval, job

    def _jobs_for(self, retrieval):
        return self.env["queue.job"].sudo().search_count([
            ("identity_key", "like", f"receipt-fetch:{retrieval.id}:%"),
        ])

    @staticmethod
    def _acquire(runner_env, job_uuid):
        """The persistence steps of ``RunJobController._acquire_job``.

        The controller also locks the row and commits; a test cursor refuses
        the commit, and the Agent policy ran on exactly these two calls.
        """
        job = Job.load(runner_env, job_uuid)
        job.set_started()
        job.store()
        return job

    @staticmethod
    def _job_on_test_cursor():
        # Receipt jobs opt into queue_job's commit-capable temporary cursor,
        # which cannot see the test transaction. Focused tests run the job on
        # the test cursor instead, exactly as the existing receipt tests do.
        @contextmanager
        def on_test_cursor(_job):
            yield

        return patch.object(Job, "in_temporary_env", on_test_cursor)

    def test_runner_starts_an_agent_owned_job(self):
        _retrieval, job = self._agent_owned_job("qa-agent-job-start")
        runner_env = self.env(user=SUPERUSER_ID)
        acquired = self._acquire(runner_env, job.uuid)
        self.assertEqual(acquired.user_id, self.agent.user_id.id)
        job.invalidate_recordset()
        self.assertEqual(job.state, "started")
        self.assertEqual(job.user_id, self.agent.user_id)

    def test_runner_runs_an_agent_owned_job_with_its_authority(self):
        retrieval, job = self._agent_owned_job("qa-agent-job-run")
        # A paused pattern makes the job body take a branch that only writes
        # the retrieval and never calls the fetcher.
        retrieval.pattern_id.action_pause()
        runner_env = self.env(user=SUPERUSER_ID)
        acquired = self._acquire(runner_env, job.uuid)
        with patch.object(type(retrieval), "_feature_enabled", return_value=True), \
                patch.object(type(retrieval), "_fetcher_request") as fetch, \
                self._job_on_test_cursor():
            _job_runner()._runjob(runner_env, acquired)
        fetch.assert_not_called()
        job.invalidate_recordset()
        retrieval.invalidate_recordset()
        self.assertEqual(job.state, "done")
        self.assertEqual(self._jobs_for(retrieval), 1)
        self.assertEqual(retrieval.state, "needs_attention")
        self.assertEqual(retrieval.failure_code, "pattern_unavailable")

    def test_runner_fails_closed_for_a_suspended_agent(self):
        retrieval, job = self._agent_owned_job("qa-agent-job-suspended")
        retrieval.pattern_id.action_pause()
        self.agent.action_suspend()
        runner_env = self.env(user=SUPERUSER_ID)
        acquired = self._acquire(runner_env, job.uuid)
        # Odoo's ``assertRaises`` wraps its body in a savepoint and rolls it
        # back, which would also discard the stored failure under test.
        raised = None
        with patch.object(type(retrieval), "_feature_enabled", return_value=True), \
                patch.object(type(retrieval), "_fetcher_request") as fetch, \
                self._job_on_test_cursor(), \
                self.assertLogs("odoo.addons.queue_job.controllers.main", level="ERROR"):
            try:
                # The runner stores the failure, then re-raises so the request
                # that asked for the job reports it.
                _job_runner()._runjob(runner_env, acquired)
            except AgentPolicyAccessError as error:
                raised = error
        self.assertIsInstance(raised, AgentPolicyAccessError)
        fetch.assert_not_called()
        job.invalidate_recordset()
        retrieval.invalidate_recordset()
        self.assertEqual(job.state, "failed")
        self.assertEqual(self._jobs_for(retrieval), 1)
        self.assertEqual(retrieval.state, "queued")

from odoo import SUPERUSER_ID, api, models
from odoo.fields import Domain

from .agent_policy_tokens import create_agent_operation_scope


class QueueJob(models.Model):
    _inherit = "queue.job"

    def _usl_agent_ephemeral_effect_allowed(self, operation):
        # The job runner loads a job as the user who enqueued it and persists
        # the job state through ``sudo()`` on that environment. ``sudo()``
        # keeps the uid, so for an Agent-owned job the actor is still the
        # Agent, and without this the runner's own ``set_started``/``store``
        # was refused, the transaction rolled back, and the runner re-queued
        # the job every minute. OCA's in-process edit sentinel proves the write
        # comes from the worker: RPC cannot serialize it or substitute a truthy
        # context value.
        if (
            operation == "write"
            and self.env.context.get("_job_edit_sentinel") is self.EDIT_SENTINEL
        ):
            return True
        return super()._usl_agent_ephemeral_effect_allowed(operation)

    @api.model
    def _access_domain(self, operation):
        # The worker reloads a job by uuid through ``sudo()`` on the job's own
        # environment before every state change. For an Agent whose delegated
        # groups no longer read ``queue.job`` (a suspended Agent, for one) that
        # search found nothing, ``Job.store()`` created a second job record,
        # and the real one stayed ``started``. An Agent may always see the jobs
        # it owns; its other reads keep the ordinary policy.
        agent = self._usl_managed_agent()
        if agent and operation == "read" and self.env.su:
            return Domain("user_id", "=", self.env.uid)
        return super()._access_domain(operation)

    def _message_post_on_failure(self):
        # When a job fails, OCA subscribes the queue managers and posts the
        # failure in the job's chatter before it stores the failed state. The
        # runner does this as the job's user; an Agent has no approved access
        # to ``queue.job``, so the failure could never be stored and the runner
        # re-queued the job forever. The bookkeeping is the runner's, not the
        # Agent's: record it as the system.
        if self._usl_actor_is_agent():
            system = self.with_user(SUPERUSER_ID)
            return super(QueueJob, system)._message_post_on_failure()
        return super()._message_post_on_failure()

    @api.model
    def _usl_agent_job_scope(self, job):
        """Return the Agent operation scope a fixed job runs under, or None.

        A job enqueued by an Agent carries the Agent's uid but none of the
        request-bound authority of the JSON-2 call that enqueued it. The job
        body is a fixed, reviewed job function, so it runs under a scope for
        its own model and method while the Agent is active. An Agent can only
        own a job that a server-approved scoped call created, because the
        policy refuses an unscoped ``queue.job`` create. A job for a method
        without a job function record, or for a suspended Agent, runs unscoped
        and fails closed.
        """
        agent = (
            self.env["usl.agent"]
            .sudo()
            .with_context(active_test=False)
            .search([("user_id", "=", job.env.uid)], limit=1)
        )
        if not agent or agent.state != "active":
            return None
        function_name = self.env["queue.job.function"].job_function_name(
            job.model_name,
            job.method_name,
        )
        functions = self.env["queue.job.function"].sudo()
        if not functions.search_count([("name", "=", function_name)], limit=1):
            return None
        return create_agent_operation_scope(
            agent_user_id=agent.user_id.id,
            root_model=job.model_name,
            root_method=job.method_name,
            access="write",
        )

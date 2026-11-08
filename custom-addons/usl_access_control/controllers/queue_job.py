from ..models.agent_policy_tokens import AGENT_OPERATION_SCOPE_CONTEXT_KEY
from odoo.addons.queue_job.controllers.main import RunJobController


class UslRunJobController(RunJobController):
    @classmethod
    def _runjob(cls, env, job):
        # The job was enqueued inside a governed Agent call, which gave it an
        # operation scope. The runner request has none, so the Agent policy
        # would refuse the job body's own writes. Rebuild the scope here, in
        # process, from the stored job and the Agent's current authority.
        scope = env["queue.job"]._usl_agent_job_scope(job)
        if scope:
            job._env = job.env(
                context={**job.env.context, AGENT_OPERATION_SCOPE_CONTEXT_KEY: scope},
            )
        super()._runjob(env, job)

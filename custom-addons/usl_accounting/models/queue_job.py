from odoo import models


class QueueJob(models.Model):
    _inherit = "queue.job"

    def write(self, vals):
        # Queue managers may operate existing jobs, but changing their execution
        # identity or company crosses the authority boundary. OCA's in-process
        # identity sentinel preserves normal worker persistence; RPC cannot
        # serialize it or substitute a truthy context value.
        if (
            {"user_id", "company_id"}.intersection(vals)
            and self.env.context.get("_job_edit_sentinel") is not self.EDIT_SENTINEL
        ):
            self._usl_require_irreversible_action(
                "queue_job_authority",
                self.env._("Change a queued job's execution identity or company"),
            )
        return super().write(vals)

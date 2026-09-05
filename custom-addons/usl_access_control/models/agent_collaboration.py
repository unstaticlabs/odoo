from odoo import models
from odoo.exceptions import AccessError

from ..exceptions import AgentPolicyAccessError
from .agent_policy_tokens import (
    AGENT_COLLABORATION_CONTEXT_KEY,
    AGENT_COLLABORATION_TOKEN,
    get_agent_operation_scope,
    has_agent_collaboration_token,
)


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _usl_agent_feedback_scope(self, agent):
        operation_scope = get_agent_operation_scope(
            self.env.context,
            agent_user_id=agent.user_id.id,
        )
        return bool(
            self.env.su
            and operation_scope
            and operation_scope.root_model == "usl.agent"
            and operation_scope.root_method == "submit_mcp_feedback",
        )

    def _usl_agent_collaboration(self, operation="read"):
        agent = self._usl_managed_agent()
        if not agent:
            return self
        if self._usl_agent_feedback_scope(agent):
            return self.with_context(
                **{AGENT_COLLABORATION_CONTEXT_KEY: AGENT_COLLABORATION_TOKEN},
            )
        if not agent._allows_model_operation(self._name, operation):
            raise AgentPolicyAccessError(
                self.env._(
                    "This Agent has no approved application access for "
                    "%(model)s.%(operation)s.",
                    model=self._name,
                    operation=operation,
                ),
                "agent_read_only_action_denied",
            )
        self.check_access(operation)
        return self.with_context(
            **{AGENT_COLLABORATION_CONTEXT_KEY: AGENT_COLLABORATION_TOKEN},
        )

    def message_post(self, *args, **kwargs):
        records = self._usl_agent_collaboration()
        agent = self._usl_managed_agent()
        if (
            agent
            and not self._usl_agent_feedback_scope(agent)
            and agent._model_is_read_only(self._name)
        ):
            message_type = kwargs.get("message_type", "notification")
            subtype_xmlid = kwargs.get("subtype_xmlid")
            subtype_id = kwargs.get("subtype_id")
            allowed_subtypes = self.env["mail.message.subtype"]
            for xmlid in ("mail.mt_comment", "mail.mt_note"):
                allowed_subtypes |= self.env.ref(xmlid, raise_if_not_found=False)
            if (
                message_type not in {"comment", "notification"}
                or (subtype_xmlid and subtype_xmlid not in {"mail.mt_comment", "mail.mt_note"})
                or (subtype_id and subtype_id not in allowed_subtypes.ids)
            ):
                raise AgentPolicyAccessError(
                    self.env._("A read-only Agent may post only Chatter comments and notes."),
                    "agent_read_only_action_denied",
                )
        return super(MailThread, records).message_post(*args, **kwargs)

    def message_notify(self, *args, **kwargs):
        records = self._usl_agent_collaboration(operation="write")
        return super(MailThread, records).message_notify(*args, **kwargs)

    def _message_log_batch(self, *args, **kwargs):
        """Allow Odoo's private Chatter log created by an authorized business write."""
        agent = self._usl_managed_agent()
        records = self
        if agent and (
            has_agent_collaboration_token(self.env.context)
            or agent._allows_model_operation(self._name, "write")
        ):
            records = self.with_context(
                **{AGENT_COLLABORATION_CONTEXT_KEY: AGENT_COLLABORATION_TOKEN},
            )
        return super(MailThread, records)._message_log_batch(*args, **kwargs)

    def message_subscribe(self, partner_ids=None, subtype_ids=None):
        agent = self._usl_managed_agent()
        writable = bool(
            agent and agent._allows_model_operation(self._name, "write"),
        )
        if agent and not writable:
            requested = set(partner_ids or ())
            if requested - {agent.user_id.partner_id.id}:
                raise AccessError(self.env._("A read-only Agent may follow only itself."))
        records = self._usl_agent_collaboration(
            operation="write" if writable else "read",
        )
        return super(MailThread, records).message_subscribe(
            partner_ids=partner_ids,
            subtype_ids=subtype_ids,
        )

    def message_unsubscribe(self, partner_ids=None):
        agent = self._usl_managed_agent()
        writable = bool(
            agent and agent._allows_model_operation(self._name, "write"),
        )
        if agent and not writable:
            requested = set(partner_ids or ())
            if requested - {agent.user_id.partner_id.id}:
                raise AccessError(self.env._("A read-only Agent may unfollow only itself."))
        records = self._usl_agent_collaboration(
            operation="write" if writable else "read",
        )
        return super(MailThread, records).message_unsubscribe(partner_ids=partner_ids)


class MailActivityMixin(models.AbstractModel):
    _inherit = "mail.activity.mixin"

    def activity_schedule(self, *args, **kwargs):
        agent = self._usl_managed_agent()
        records = self
        if agent:
            if not agent._allows_model_operation(self._name, "read"):
                raise AgentPolicyAccessError(
                    self.env._(
                        "This Agent has no approved application access for %(model)s.read.",
                        model=self._name,
                    ),
                    "agent_read_only_action_denied",
                )
            self.check_access("read")
            records = self.with_context(
                **{AGENT_COLLABORATION_CONTEXT_KEY: AGENT_COLLABORATION_TOKEN},
            )
        return super(MailActivityMixin, records).activity_schedule(*args, **kwargs)


class MailActivity(models.Model):
    _inherit = "mail.activity"

    def _usl_agent_complete_activity(self):
        """Authorize completion against the activity's business record."""
        agent = self._usl_managed_agent()
        if not agent:
            return self
        for model_name, activities in self.grouped("res_model").items():
            if not model_name or model_name not in self.env:
                raise AgentPolicyAccessError(
                    self.env._("This activity has no accessible business record."),
                    "agent_read_only_action_denied",
                )
            if not agent._allows_model_operation(model_name, "write"):
                raise AgentPolicyAccessError(
                    self.env._(
                        "This Agent has no approved application access for %(model)s.write.",
                        model=model_name,
                    ),
                    "agent_read_only_action_denied",
                )
            records = self.env[model_name].browse(activities.mapped("res_id")).exists()
            if len(records) != len(set(activities.mapped("res_id"))):
                raise AccessError(self.env._("An activity business record is unavailable."))
            records.check_access("write")
        return self.with_context(
            **{AGENT_COLLABORATION_CONTEXT_KEY: AGENT_COLLABORATION_TOKEN},
        )

    def action_feedback(self, *args, **kwargs):
        activities = self._usl_agent_complete_activity()
        return super(MailActivity, activities).action_feedback(*args, **kwargs)

    def action_feedback_schedule_next(self, *args, **kwargs):
        activities = self._usl_agent_complete_activity()
        return super(MailActivity, activities).action_feedback_schedule_next(*args, **kwargs)

    def action_done_schedule_next(self, *args, **kwargs):
        activities = self._usl_agent_complete_activity()
        return super(MailActivity, activities).action_done_schedule_next(*args, **kwargs)


class UslDocument(models.Model):
    _inherit = "usl.document"

    def _usl_agent_download_context(self):
        agent = self._usl_managed_agent()
        if not agent:
            return self
        if not agent._allows_model_operation(self._name, "read"):
            raise AgentPolicyAccessError(
                self.env._(
                    "This Agent has no approved application access for %(model)s.read.",
                    model=self._name,
                ),
                "agent_read_only_action_denied",
            )
        self.check_access("read")
        return self.with_context(
            **{AGENT_COLLABORATION_CONTEXT_KEY: AGENT_COLLABORATION_TOKEN},
        )

    def mcp_create_download_grant(self, *args, **kwargs):
        records = self._usl_agent_download_context()
        return super(UslDocument, records).mcp_create_download_grant(*args, **kwargs)

    def mcp_revoke_download_grant(self, *args, **kwargs):
        agent = self._usl_managed_agent()
        if agent and agent._model_is_read_only(self._name):
            grant_id = args[0] if args else kwargs.get("grant_id")
            grant = self.env["usl.document.download.grant"].sudo().search(
                [("public_id", "=", str(grant_id or ""))],
                limit=1,
            )
            if not grant or grant.issued_by_id != agent.user_id:
                raise AccessError(
                    self.env._("A read-only Agent may revoke only its own download grants."),
                )
        records = self._usl_agent_download_context()
        return super(UslDocument, records).mcp_revoke_download_grant(*args, **kwargs)

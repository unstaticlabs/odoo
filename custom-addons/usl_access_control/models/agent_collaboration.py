from odoo import models
from odoo.exceptions import AccessError

from ..exceptions import AgentPolicyAccessError
from .agent_policy_tokens import (
    AGENT_COLLABORATION_CONTEXT_KEY,
    AGENT_COLLABORATION_TOKEN,
)


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _usl_readonly_agent_collaboration(self):
        agent = self._usl_managed_agent()
        if not agent or not agent._model_is_read_only(self._name):
            return self
        self.check_access("read")
        return self.with_context(
            **{AGENT_COLLABORATION_CONTEXT_KEY: AGENT_COLLABORATION_TOKEN},
        )

    def message_post(self, *args, **kwargs):
        records = self._usl_readonly_agent_collaboration()
        agent = self._usl_managed_agent()
        if agent and agent._model_is_read_only(self._name):
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

    def message_subscribe(self, partner_ids=None, subtype_ids=None):
        records = self._usl_readonly_agent_collaboration()
        agent = self._usl_managed_agent()
        if agent and agent._model_is_read_only(self._name):
            requested = set(partner_ids or ())
            if requested - {agent.user_id.partner_id.id}:
                raise AccessError(self.env._("A read-only Agent may follow only itself."))
        return super(MailThread, records).message_subscribe(
            partner_ids=partner_ids,
            subtype_ids=subtype_ids,
        )

    def message_unsubscribe(self, partner_ids=None):
        records = self._usl_readonly_agent_collaboration()
        agent = self._usl_managed_agent()
        if agent and agent._model_is_read_only(self._name):
            requested = set(partner_ids or ())
            if requested - {agent.user_id.partner_id.id}:
                raise AccessError(self.env._("A read-only Agent may unfollow only itself."))
        return super(MailThread, records).message_unsubscribe(partner_ids=partner_ids)


class MailActivityMixin(models.AbstractModel):
    _inherit = "mail.activity.mixin"

    def activity_schedule(self, *args, **kwargs):
        agent = self._usl_managed_agent()
        records = self
        if agent and agent._model_is_read_only(self._name):
            self.check_access("read")
            records = self.with_context(
                **{AGENT_COLLABORATION_CONTEXT_KEY: AGENT_COLLABORATION_TOKEN},
            )
        return super(MailActivityMixin, records).activity_schedule(*args, **kwargs)


class UslDocument(models.Model):
    _inherit = "usl.document"

    def _usl_readonly_agent_download_context(self):
        agent = self._usl_managed_agent()
        if not agent or not agent._model_is_read_only(self._name):
            return self
        return self.with_context(
            **{AGENT_COLLABORATION_CONTEXT_KEY: AGENT_COLLABORATION_TOKEN},
        )

    def mcp_create_download_grant(self, *args, **kwargs):
        records = self._usl_readonly_agent_download_context()
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
        records = self._usl_readonly_agent_download_context()
        return super(UslDocument, records).mcp_revoke_download_grant(*args, **kwargs)

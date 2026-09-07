from datetime import timedelta

from markupsafe import Markup, escape

from odoo import fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..exceptions import AgentPolicyAccessError
from .agent_policy_tokens import (
    AGENT_COLLABORATION_CONTEXT_KEY,
    AGENT_COLLABORATION_TOKEN,
    get_agent_operation_scope,
    has_agent_collaboration_token,
)

# A Chatter body is stored as HTML, so a caller that sends markup without
# saying so stores escaped tags that readers see as characters.  The author
# cannot repair that afterwards through the ordinary write path, because
# mail.message is a governed side-effect model.  `mcp_revise_own_message`
# below is that repair, and it is deliberately narrow: an identity may correct
# what it wrote itself, on a record it can still read, while the note is new.
# A day covers a note noticed the next working morning and still stops an
# identity rewording something people have long since read and acted on.
_MESSAGE_REVISION_WINDOW = timedelta(hours=24)
_REVISABLE_SUBTYPE_XMLIDS = ("mail.mt_comment", "mail.mt_note")
_MAX_MESSAGE_BODY = 50_000


def _plaintext_to_html(value):
    """Escape plain text and keep its line breaks visible, as a post does."""
    return Markup("<p>%s</p>") % Markup("<br>").join(
        escape(line) for line in value.split("\n")
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

    def mcp_revise_own_message(self, message_id, body, body_is_html=False):
        """Correct the body of one Chatter message this identity posted itself.

        Editing a message is normally avoided, because notifications have
        already gone out.  This exists for one case the ordinary path cannot
        serve: the author stored a body that reads as garbage and no one else
        can be asked to fix it.  Odoo marks the result edited, keeps the
        original in the audit trail, and refuses everything that is not the
        caller's own recent note.
        """
        self.ensure_one()
        if not isinstance(body, str) or not body.strip():
            raise ValidationError(self.env._("A revised message body cannot be empty."))
        if len(body) > _MAX_MESSAGE_BODY:
            raise ValidationError(self.env._("A revised message body is too long."))

        # Reading the record is the same bar as posting to it, and the
        # returned recordset carries the collaboration token that lets a
        # governed Agent write mail.message at all.
        records = self._usl_agent_collaboration()
        message = records.env["mail.message"].sudo().browse(
            int(message_id or 0),
        ).exists()
        if not message or message.model != self._name or message.res_id != self.id:
            raise UserError(
                self.env._("That message does not belong to this record."),
            )
        if message.author_guest_id or message.author_id != self.env.user.partner_id:
            raise AccessError(
                self.env._("You may revise only a message you posted yourself."),
            )
        revisable = self._usl_revisable_subtypes()
        if message.message_type == "tracking" or message.subtype_id not in revisable:
            raise UserError(
                self.env._("Only a Chatter comment or note can be revised."),
            )
        posted_at = message.create_date or fields.Datetime.now()
        if fields.Datetime.now() - posted_at > _MESSAGE_REVISION_WINDOW:
            raise UserError(
                self.env._(
                    "This message is older than %(hours)s hours and can no longer be "
                    "revised. Post a follow-up message instead.",
                    hours=int(_MESSAGE_REVISION_WINDOW.total_seconds() // 3600),
                ),
            )

        revised = Markup(body) if body_is_html else _plaintext_to_html(body)
        records._message_update_content(message, body=revised, strict=False)
        return {
            "message_id": message.id,
            "model": self._name,
            "res_id": self.id,
            "revised_at": fields.Datetime.to_string(fields.Datetime.now()),
        }

    def _usl_revisable_subtypes(self):
        """The Chatter subtypes a posting identity may also correct."""
        subtypes = self.env["mail.message.subtype"].sudo().browse()
        for xmlid in _REVISABLE_SUBTYPE_XMLIDS:
            subtype = self.env.ref(xmlid, raise_if_not_found=False)
            if subtype:
                subtypes |= subtype.sudo()
        return subtypes

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

    def activity_reschedule(self, *args, **kwargs):
        agent = self._usl_managed_agent()
        records = self
        if agent:
            if not agent._allows_model_operation(self._name, "write"):
                raise AgentPolicyAccessError(
                    self.env._(
                        "This Agent has no approved application access for %(model)s.write.",
                        model=self._name,
                    ),
                    "agent_read_only_action_denied",
                )
            self.check_access("write")
            records = self.with_context(
                **{AGENT_COLLABORATION_CONTEXT_KEY: AGENT_COLLABORATION_TOKEN},
            )
        return super(MailActivityMixin, records).activity_reschedule(*args, **kwargs)


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

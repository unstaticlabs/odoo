import os
import re

from markupsafe import Markup, escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..exceptions import AgentPolicyAccessError

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_CATEGORIES = {"bug", "feature_request", "documentation_gap", "usability"}
_IMPACTS = {"blocking", "major", "minor", "suggestion"}
_TAG_NAMES = {
    "bug": ("MCP", "Agent Feedback", "Bug"),
    "feature_request": ("MCP", "Agent Feedback", "Feature"),
    "documentation_gap": ("MCP", "Agent Feedback", "Docs"),
    "usability": ("MCP", "Agent Feedback"),
}
_FEEDBACK_FIELDS = frozenset(
    {
        "category",
        "impact",
        "title",
        "summary",
        "affected_tool",
        "expected_behavior",
        "actual_behavior",
        "reproduction_steps",
        "workaround",
        "correlation_id",
    },
)
_RELEASE_FIELDS = frozenset({"mcp_server_version", "mcp_commit", "gitops_commit"})


def _bounded_text(payload, name, *, maximum, required=False):
    value = payload.get(name)
    if value is None:
        if required:
            raise ValidationError(_("Feedback field %(field)s is required.", field=name))
        return ""
    if not isinstance(value, str):
        raise ValidationError(_("Feedback field %(field)s must be text.", field=name))
    value = value.strip()
    if required and not value:
        raise ValidationError(_("Feedback field %(field)s is required.", field=name))
    if len(value) > maximum:
        raise ValidationError(
            _("Feedback field %(field)s is too long.", field=name),
        )
    return value


def _release_commit(value):
    candidate = str(value or "").strip().lower()
    return candidate if _COMMIT_RE.fullmatch(candidate) else "unknown"


class UslAgentFeedback(models.Model):
    _inherit = "usl.agent"

    @api.model
    def submit_mcp_feedback(self, feedback, release=None):
        """Create one low-trust, structured development task atomically."""
        agent = self._usl_managed_agent()
        if not agent or agent.state != "active" or not agent.user_id.active:
            raise AgentPolicyAccessError(
                _("Only an active governed Agent may submit MCP feedback."),
                "agent_read_only_action_denied",
            )
        if not isinstance(feedback, dict) or not isinstance(release or {}, dict):
            raise ValidationError(_("Feedback and release metadata must be objects."))
        if set(feedback) - _FEEDBACK_FIELDS or set(release or {}) - _RELEASE_FIELDS:
            raise ValidationError(_("Feedback or release metadata contains unknown fields."))

        category = feedback.get("category")
        impact = feedback.get("impact")
        if category not in _CATEGORIES or impact not in _IMPACTS:
            raise ValidationError(_("Feedback category or impact is invalid."))
        title = _bounded_text(feedback, "title", maximum=300, required=True)
        summary = _bounded_text(feedback, "summary", maximum=10_000, required=True)
        affected_tool = _bounded_text(feedback, "affected_tool", maximum=128)
        expected = _bounded_text(feedback, "expected_behavior", maximum=10_000)
        actual = _bounded_text(feedback, "actual_behavior", maximum=10_000)
        workaround = _bounded_text(feedback, "workaround", maximum=10_000)
        raw_steps = feedback.get("reproduction_steps") or []
        if not isinstance(raw_steps, list) or len(raw_steps) > 20:
            raise ValidationError(_("Reproduction steps must be a list of at most 20 items."))
        steps = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, str) or not raw_step.strip() or len(raw_step.strip()) > 2_000:
                raise ValidationError(_("Each reproduction step must be 1-2000 characters."))
            steps.append(raw_step.strip())
        if category == "bug" and (not expected or not actual or not steps):
            raise ValidationError(
                _("Bug feedback requires expected behavior, actual behavior, and reproduction steps."),
            )

        project_id = self._feedback_destination_id("USL_MCP_FEEDBACK_PROJECT_ID")
        stage_id = self._feedback_destination_id("USL_MCP_FEEDBACK_STAGE_ID")
        project = self.env["project.project"].sudo().browse(project_id).exists()
        stage = self.env["project.task.type"].sudo().browse(stage_id).exists()
        if not project or not project.active or project.name != "[DEV] Odoo MCP":
            raise UserError(_("The configured MCP feedback project is unavailable."))
        if not stage or stage.name.strip().casefold() != "inbox":
            raise UserError(_("The configured MCP feedback Inbox stage is unavailable."))
        if stage.project_ids and project not in stage.project_ids:
            raise UserError(_("The configured MCP feedback stage does not belong to the project."))

        correlation_id = _bounded_text(feedback, "correlation_id", maximum=128) or "unknown"
        submitted_at = fields.Datetime.now()
        release = release or {}
        release_values = {
            "MCP server version": _bounded_text(release, "mcp_server_version", maximum=64) or "unknown",
            "MCP commit": _release_commit(release.get("mcp_commit")),
            "Odoo commit": _release_commit(os.getenv("USL_RELEASE_COMMIT")),
            "GitOps commit": _release_commit(release.get("gitops_commit")),
        }
        sections = [
            ("Trust", "[agent-feedback] Low-trust report; verify before acting."),
            ("Category", category),
            ("Impact", impact),
            ("Summary", summary),
            ("Affected tool", affected_tool or "unknown"),
            ("Expected behavior", expected or "not provided"),
            ("Actual behavior", actual or "not provided"),
            ("Workaround", workaround or "not provided"),
            ("Submitting Agent", f"{agent.name} (usl.agent,{agent.id})"),
            ("Submitted at (UTC)", fields.Datetime.to_string(submitted_at)),
            ("Correlation ID", correlation_id),
            *release_values.items(),
        ]
        description = Markup("<h2>Agent feedback</h2>")
        for label, value in sections:
            description += Markup("<p><strong>%s</strong><br>%s</p>") % (
                escape(label),
                escape(value),
            )
        description += Markup("<h3>Reproduction steps</h3>")
        if steps:
            description += Markup("<ol>%s</ol>") % Markup().join(
                Markup("<li>%s</li>") % escape(step) for step in steps
            )
        else:
            description += Markup("<p>not provided</p>")

        tags = self.env["project.tags"].sudo().search(
            [("name", "in", list(_TAG_NAMES[category]))],
        )
        task = self.env["project.task"].with_user(agent.user_id).sudo().create(
            {
                "name": f"[Agent feedback] {title}",
                "project_id": project.id,
                "stage_id": stage.id,
                "description": description,
                "tag_ids": [Command.set(tags.ids)],
            },
        )
        task.message_post(
            body=Markup("<p><strong>[agent-feedback]</strong> Submitted by %s.</p>")
            % escape(agent.name),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        return {
            "task_id": task.id,
            "display_name": task.display_name,
            "project_id": project.id,
            "stage_id": stage.id,
            "submitted_at": fields.Datetime.to_string(submitted_at),
        }

    @api.model
    def _feedback_destination_id(self, name):
        value = os.getenv(name, "").strip()
        if not value.isdigit() or int(value) <= 0:
            raise UserError(_("The MCP feedback destination is not configured."))
        return int(value)

"""Native report-session effects do not confer business-write authority."""

from dataclasses import dataclass, field

from odoo import api, models

from .agent_policy_tokens import AGENT_OPERATION_SCOPE_CONTEXT_KEY

_REPORT_SESSION_CONTEXT_KEY = "usl_agent_accounting_report_session"
_REPORT_SESSION_MARKER = object()


@dataclass(slots=True)
class _ReportSession:
    agent_user_id: int
    wizard_ids: set[int] = field(default_factory=set)
    exporting: bool = False
    creating_export_attachment: int = 0
    marker: object = field(default=_REPORT_SESSION_MARKER, repr=False)


def _report_session(records):
    session = records.env.context.get(_REPORT_SESSION_CONTEXT_KEY)
    if (
        isinstance(session, _ReportSession)
        and session.marker is _REPORT_SESSION_MARKER
        and session.agent_user_id == records.env.uid
    ):
        return session
    return None


class Base(models.AbstractModel):
    _inherit = "base"

    @api.model
    def _usl_agent_ephemeral_effect_allowed(self, operation):
        # Native field/cleanup work visits empty attachment/metadata recordsets.
        # No guessed IDs are shrunk with exists(); this admits no stored row.
        if operation in {"write", "unlink"} and not self.ids and _report_session(self):
            return True
        return super()._usl_agent_ephemeral_effect_allowed(operation)


class ReportExportWizard(models.TransientModel):
    _inherit = "rebuild.account.report.export.wizard"

    @api.model
    def _usl_readonly_report_session(self, wizard_id=None):
        agent = self._usl_managed_agent()
        if not agent or agent._allows_model_operation(self._name, "write"):
            return self
        # An HTTP caller reaches this method through its reviewed native
        # workflow. Ordinary ACLs and the owner's company ceiling still apply.
        self.check_access("read")
        wizard = self.browse(wizard_id).exists() if wizard_id else self.browse()
        wizard.check_access("read")
        session = _report_session(self) or _ReportSession(self.env.uid)
        session.wizard_ids.update(wizard.ids)
        return self.with_context(**{
            _REPORT_SESSION_CONTEXT_KEY: session,
            # A generic write/collaboration scope would authorize arbitrary
            # nested sudo. This read journey carries only its technical effects.
            AGENT_OPERATION_SCOPE_CONTEXT_KEY: False,
        })

    @api.model
    def report_client_load(self, report_type, filters=None, wizard_id=None):
        records = self._usl_readonly_report_session(wizard_id)
        return super(ReportExportWizard, records).report_client_load(
            report_type, filters=filters, wizard_id=wizard_id,
        )

    @api.model
    def report_client_toggle_group(self, wizard_id, line_id):
        records = self._usl_readonly_report_session(wizard_id)
        return super(ReportExportWizard, records).report_client_toggle_group(
            wizard_id, line_id,
        )

    @api.model
    def report_client_export(self, wizard_id, export_format, filters=None):
        records = self._usl_readonly_report_session(wizard_id)
        session = _report_session(records)
        if session:
            session.exporting = True
        return super(ReportExportWizard, records).report_client_export(
            wizard_id, export_format, filters=filters,
        )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        session = _report_session(self)
        if session:
            session.wizard_ids.update(records.ids)
        return records

    @api.model
    def _usl_agent_ephemeral_effect_allowed(self, operation):
        session = _report_session(self)
        if session:
            if operation == "create":
                return True
            if operation in {"write", "unlink"}:
                return set(self.ids) <= session.wizard_ids
        return super()._usl_agent_ephemeral_effect_allowed(operation)


class ReportPreviewLine(models.TransientModel):
    _inherit = "rebuild.account.report.preview.line"

    @api.model_create_multi
    def create(self, vals_list):
        session = _report_session(self)
        if session and any(
            values.get("wizard_id") not in session.wizard_ids
            for values in vals_list
        ):
            ordinary = self.with_context(**{_REPORT_SESSION_CONTEXT_KEY: False})
            return super(ReportPreviewLine, ordinary).create(vals_list)
        return super().create(vals_list)

    @api.model
    def _usl_agent_ephemeral_effect_allowed(self, operation):
        session = _report_session(self)
        if session and operation in {"create", "write", "unlink"}:
            return not self or set(self.wizard_id.ids) <= session.wizard_ids
        return super()._usl_agent_ephemeral_effect_allowed(operation)


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    @api.model
    def _usl_report_export_target(self, values, session):
        if (
            not session.exporting
            or values.get("res_model") != "rebuild.account.report.export.wizard"
            or values.get("res_field") != "export_file"
            or values.get("res_id") not in session.wizard_ids
        ):
            return False
        wizard = self.env[values["res_model"]].with_user(
            session.agent_user_id,
        ).browse(values["res_id"])
        wizard.check_access("write")
        return bool(wizard.exists())

    @api.model_create_multi
    def create(self, vals_list):
        session = _report_session(self)
        if not session:
            return super().create(vals_list)
        if not all(self._usl_report_export_target(values, session) for values in vals_list):
            ordinary = self.with_context(**{_REPORT_SESSION_CONTEXT_KEY: False})
            return super(IrAttachment, ordinary).create(vals_list)
        session.creating_export_attachment += 1
        try:
            return super().create(vals_list)
        finally:
            session.creating_export_attachment -= 1

    def write(self, vals):
        session = _report_session(self)
        if session:
            for attachment in self:
                target = {
                    "res_model": vals.get("res_model", attachment.res_model),
                    "res_id": vals.get("res_id", attachment.res_id),
                    "res_field": vals.get("res_field", attachment.res_field),
                }
                if not self._usl_report_export_target(target, session):
                    ordinary = self.with_context(**{_REPORT_SESSION_CONTEXT_KEY: False})
                    return super(IrAttachment, ordinary).write(vals)
        return super().write(vals)

    @api.model
    def _usl_agent_ephemeral_effect_allowed(self, operation):
        session = _report_session(self)
        if session:
            if operation == "create":
                return bool(session.creating_export_attachment)
            if operation in {"write", "unlink"} and self:
                return all(
                    self._usl_report_export_target({
                        "res_model": attachment.res_model,
                        "res_id": attachment.res_id,
                        "res_field": attachment.res_field,
                    }, session)
                    for attachment in self
                )
        return super()._usl_agent_ephemeral_effect_allowed(operation)

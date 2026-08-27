import io
from collections import OrderedDict

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    usl_output_policy = fields.Selection(
        selection=[
            ("latex", "LaTeX-rendered"),
            ("source_passthrough", "Source passthrough"),
            ("machine", "Machine output"),
            ("non_official", "Non-official"),
            ("pending_migration", "Pending migration"),
        ],
        string="Document output policy",
        required=True,
        default="pending_migration",
        index=True,
        copy=False,
    )
    usl_document_template_id = fields.Many2one(
        "usl.document.template",
        string="Governed document template",
        ondelete="restrict",
        copy=False,
    )

    @api.constrains("usl_output_policy", "usl_document_template_id", "report_type")
    def _check_usl_document_policy(self):
        for report in self:
            if bool(report.usl_document_template_id) != (report.usl_output_policy == "latex"):
                raise ValidationError(
                    _("A LaTeX output policy and governed template binding must be set together.")
                )
            if report.usl_document_template_id and report.report_type != "qweb-pdf":
                raise ValidationError(_("Governed document templates require a qweb-pdf report action."))

    def _usl_document_company(self, record):
        company = record.company_id if "company_id" in record._fields else self.env.company
        if company not in self.env.companies:
            raise AccessError(_("You cannot render documents for this company."))
        return company

    def _usl_document_locale(self, record, company):
        partner = record.partner_id if "partner_id" in record._fields else company.partner_id
        language = partner.lang or self.env.lang or "en_US"
        return "fr_FR" if language.startswith("fr") else "en_US"

    def _usl_render_one(self, report, record, data):
        record.check_access("read")
        company = self._usl_document_company(record)
        if not company.usl_document_renderer_enabled:
            company._usl_document_raise_configuration_error(
                _("The governed document renderer is disabled for this company.")
            )
        adapter = getattr(record, "_usl_document_render_payload", None)
        if adapter is None:
            raise UserError(
                _(
                    "Report %(report)s is bound to %(template)s but %(model)s has no document adapter.",
                    report=report.display_name,
                    template=report.usl_document_template_id.key,
                    model=report.model,
                )
            )
        locale = self._usl_document_locale(record, company)
        company_payload, assets = company._usl_document_renderer_company_payload(locale)
        document_payload = adapter(report, report.usl_document_template_id, data or {}, locale)
        if isinstance(document_payload, tuple):
            document_payload, adapter_assets = document_payload
            assets.extend(adapter_assets)
        try:
            rendered = self.env["usl.document.renderer"].render(
                report.usl_document_template_id,
                company_payload,
                document_payload,
                locale,
                assets=assets,
            )
        except UserError as error:
            company._usl_document_raise_configuration_error(str(error))
        provenance = {
            "usl_document_template_id": report.usl_document_template_id.id,
            "usl_document_template_revision": rendered["template_revision"],
            "usl_document_payload_sha256": rendered["payload_sha256"],
            "usl_document_renderer_version": rendered["renderer_version"],
            "usl_document_company_id": company.id,
            "usl_document_rendered_at": fields.Datetime.now(),
        }
        return io.BytesIO(rendered["pdf"]), provenance

    def _usl_render_governed_streams(self, report, data, res_ids):
        if not res_ids:
            raise UserError(_("A governed document report requires at least one business record."))
        records = self.env[report.model].browse(res_ids)
        records.check_access("read")
        has_duplicates = len(res_ids) != len(set(res_ids))
        if has_duplicates:
            streams = []
            for res_id in res_ids:
                stream, _provenance = self._usl_render_one(
                    report, self.env[report.model].browse(res_id), data
                )
                streams.append(stream)
            return {
                False: {
                    "stream": self._merge_pdfs(streams),
                    "attachment": None,
                }
            }

        collected = OrderedDict()
        for record in records:
            attachment = None
            stream = None
            if (
                report.attachment
                and not self.env.context.get("report_pdf_no_attachment")
            ):
                attachment = report.retrieve_attachment(record)
                if attachment and report.attachment_use:
                    stream = io.BytesIO(attachment.raw)
            if stream is not None:
                collected[record.id] = {"stream": stream, "attachment": attachment}
                continue
            stream, provenance = self._usl_render_one(report, record, data)
            collected[record.id] = {
                "stream": stream,
                "attachment": attachment,
                "usl_provenance": provenance,
            }
        return collected

    def _render_qweb_pdf_prepare_streams(self, report_ref, data, res_ids=None):
        report = self._get_report(report_ref)
        if report.usl_document_template_id:
            return self._usl_render_governed_streams(report, data, res_ids)
        return super()._render_qweb_pdf_prepare_streams(report_ref, data, res_ids=res_ids)

    def _pre_render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        report = self._get_report(report_ref)
        if report.usl_document_template_id:
            if isinstance(res_ids, int):
                res_ids = [res_ids]
            data = dict(data or {}, report_type="pdf")
            return self._render_qweb_pdf_prepare_streams(
                report_ref,
                data,
                res_ids=res_ids,
            ), "pdf"
        return super()._pre_render_qweb_pdf(report_ref, res_ids=res_ids, data=data)

    def _prepare_pdf_report_attachment_vals_list(self, report, streams):
        values = super()._prepare_pdf_report_attachment_vals_list(report, streams)
        if not report.usl_document_template_id:
            return values
        provenance_by_id = {
            res_id: stream_data.get("usl_provenance")
            for res_id, stream_data in streams.items()
            if res_id and stream_data.get("usl_provenance")
        }
        for attachment_values in values:
            provenance = provenance_by_id.get(attachment_values["res_id"])
            if provenance:
                attachment_values.update(provenance)
        return values

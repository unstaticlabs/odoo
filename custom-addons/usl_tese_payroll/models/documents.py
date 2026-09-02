import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class UslTesePayslip(models.Model):
    _inherit = "usl.tese.payslip"

    @api.model_create_multi
    def create(self, values_list):
        payslips = super().create(values_list)
        payslips._reconcile_documents_after_attachment_change()
        return payslips

    def write(self, values):
        result = super().write(values)
        if "attachment_id" in values:
            self._reconcile_documents_after_attachment_change()
        return result

    def _reconcile_documents_after_attachment_change(self):
        if self.env.context.get("usl_tese_skip_immediate_document_reconciliation"):
            return {"reconciled": 0, "queued": 0}
        return self.filtered("attachment_id")._reconcile_archived_payslip_document()

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never":
            return policy
        return {
            **policy,
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "tese_payroll_evidence",
            "confidentiality": "hr",
            "accounting_evidence": True,
        }

    def _document_related_records(self, attachment=None):
        self.ensure_one()
        records = super()._document_related_records(attachment)
        if self.employee_id:
            records.append(
                {
                    "model": "hr.employee",
                    "id": self.employee_id.id,
                    "document_role": "library",
                },
            )
        if self.move_id:
            records.append({"model": "account.move", "id": self.move_id.id})
        return records

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values.update(
            {
                "confidentiality": "hr",
                "accounting_evidence": True,
                "tags": ["HR", "Payroll"],
                "document_type": "Payroll record",
                "replace_document_type": True,
                "correspondent_partner_id": self.employee_partner_id.id or False,
                "document_date": fields.Date.to_string(
                    self.payslip_date or self.period_end,
                ),
            },
        )
        return values

    def _document_access_trigger_fields(self):
        return super()._document_access_trigger_fields() | {
            "employee_id",
            "state",
        }

    def _reconcile_archived_payslip_document(self):
        """Reuse the attachment's archive root and add payroll semantics."""
        operation_model = self.env["usl.document.operation"].sudo()
        touched_documents = self.env["usl.document"]
        classified = 0
        queued = 0
        for payslip in self.sudo().filtered("attachment_id"):
            attachment_checksum = hashlib.sha256(
                bytes(payslip.attachment_id.raw or b""),
            ).hexdigest()
            linked_documents = self.env["usl.document.link"].sudo().search(
                [
                    ("res_model", "=", payslip._name),
                    ("res_id", "=", payslip.id),
                    ("active", "=", True),
                ],
            ).mapped("document_id")
            document = linked_documents.filtered(
                lambda candidate: (
                    candidate.checksum == attachment_checksum
                    or attachment_checksum in candidate.version_ids.mapped("checksum")
                ),
            )[:1]
            operation = operation_model.search(
                [
                    ("source_attachment_id", "=", payslip.attachment_id.id),
                    ("state", "in", ("archived", "duplicate")),
                    "|",
                    ("document_id", "!=", False),
                    ("target_document_id", "!=", False),
                ],
                order="id desc",
                limit=1,
            )
            document = document or operation.document_id or operation.target_document_id
            if not document:
                payslip.attachment_id._queue_usl_documents_archive()
                queued += 1
                continue
            context = self.env["usl.document"]._prepare_archive_context(
                payslip,
                payslip.attachment_id,
            )
            document.with_context(
                usl_documents_trusted_backfill_access=True,
            )._apply_archive_context(
                context,
                submitted_by=self.env.ref("base.user_root"),
                access_user=self.env.ref("base.user_root"),
            )
            touched_documents |= document
            classified += 1
        if touched_documents:
            self.env["usl.document"].reconcile_linked_classification(limit=1000)
        return {"reconciled": classified, "queued": queued}

    def action_choose_archived_pdf(self):
        self.ensure_one()
        self._check_workflow_access()
        if self.state in {"to_reconcile", "paid"} or (
            self.move_id and self.move_id.state == "posted"
        ):
            raise UserError(_(
                "The provider document cannot be changed after the payroll "
                "journal entry has been posted.",
            ))
        return {
            "type": "ir.actions.act_window",
            "name": _("Choose the official TESE PDF"),
            "res_model": "usl.tese.document.link.wizard",
            "view_mode": "form",
            "views": [
                (
                    self.env.ref(
                        "usl_tese_payroll.view_tese_document_link_wizard_form",
                    ).id,
                    "form",
                ),
            ],
            "target": "new",
            "context": {
                "default_payslip_id": self.id,
                "allowed_company_ids": self.company_id.ids,
            },
        }

    @api.model
    def cron_reconcile_archived_documents(self):
        payslips = self.sudo().search(
            [("attachment_id", "!=", False)],
            order="id",
        )
        links = self.env["usl.document.link"].sudo().search(
            [
                ("res_model", "=", self._name),
                ("res_id", "in", payslips.ids),
                ("active", "=", True),
            ],
        )
        by_payslip = {}
        for link in links:
            by_payslip.setdefault(
                link.res_id,
                self.env["usl.document"],
            )
            by_payslip[link.res_id] |= link.document_id
        pending = payslips.filtered(
            lambda payslip: not any(
                document.document_type_id.name == "Payroll record"
                and document.confidentiality == "hr"
                and {"HR", "Payroll"}.issubset(
                    set(document.tag_ids.mapped("name")),
                )
                and document.link_ids.filtered(
                    lambda document_link: (
                        document_link.active
                        and document_link.res_model == "hr.employee"
                        and document_link.res_id == payslip.employee_id.id
                    ),
                )
                for document in by_payslip.get(
                    payslip.id,
                    self.env["usl.document"],
                )
            ),
        )
        return pending._reconcile_archived_payslip_document()

    @api.depends(
        "attachment_id",
        "attachment_id.mimetype",
        "state",
        "archived_document_count",
        "document_archive_pending_count",
        "document_archive_failure_count",
    )
    def _compute_document_status(self):
        super()._compute_document_status()
        for payslip in self.filtered("attachment_id"):
            if payslip.document_archive_failure_count:
                payslip.document_status = "warning"
                payslip.document_message = _(
                    "The payroll PDF could not be archived. Next: open Documents "
                    "issues and retry it.",
                )
            elif not payslip.archived_document_count:
                payslip.document_status = "linked"
                payslip.document_message = _(
                    "The payroll PDF is still being linked to Documents. The "
                    "original remains attached to this payroll record.",
                )
            elif payslip.document_archive_pending_count:
                payslip.document_status = "linked"
                payslip.document_message = _(
                    "The payroll PDF is archived and its latest update is still "
                    "being processed in Documents.",
                )
            else:
                payslip.document_status = "ok"
                payslip.document_message = _(
                    "The official payroll PDF is archived in Documents and linked "
                    "to this payroll record and the employee library.",
                )


class UslDocumentLink(models.Model):
    _inherit = "usl.document.link"

    def _allowed_models(self):
        return super()._allowed_models() | {"usl.tese.payslip"}


class UslDocumentOperation(models.Model):
    _inherit = "usl.document.operation"

    def write(self, values):
        result = super().write(values)
        if values.get("state") != "archived":
            return result
        completed = self.filtered(
            lambda operation: (
                operation.source_attachment_id
                and (operation.document_id or operation.target_document_id)
            ),
        )
        if not completed:
            return result
        payslips = self.env["usl.tese.payslip"].sudo().search(
            [
                (
                    "attachment_id",
                    "in",
                    completed.mapped("source_attachment_id").ids,
                ),
            ],
        )
        if payslips:
            payslips.with_context(
                usl_tese_skip_immediate_document_reconciliation=True,
            )._reconcile_archived_payslip_document()
        return result


class UslTeseDocumentLinkWizard(models.TransientModel):
    _name = "usl.tese.document.link.wizard"
    _description = "Choose an Archived TESE Payroll PDF"

    payslip_id = fields.Many2one(
        "usl.tese.payslip",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        "res.company",
        related="payslip_id.company_id",
        readonly=True,
    )
    document_id = fields.Many2one(
        "usl.document",
        string="Official TESE PDF",
        required=True,
        domain=(
            "[('company_id', '=', company_id), "
            "('availability_state', '=', 'available'), "
            "('permission_sync_state', '=', 'synchronized'), "
            "('mime_type', '=', 'application/pdf')]"
        ),
    )

    def _validated_records(self):
        self.ensure_one()
        payslip = self.payslip_id.exists()
        document = self.document_id.exists()
        if not payslip or not document:
            raise UserError(_(
                "The payroll record or archived document no longer exists.",
            ))
        payslip.check_access("read")
        payslip.check_access("write")
        payslip._check_workflow_access()
        document.check_access("read")
        document.check_access("write")
        if payslip.state in {"to_reconcile", "paid"} or (
            payslip.move_id and payslip.move_id.state == "posted"
        ):
            raise UserError(_(
                "The provider document cannot be changed after the payroll "
                "journal entry has been posted.",
            ))
        if payslip.attachment_id:
            raise UserError(_(
                "An official TESE PDF is already linked. Remove it before "
                "choosing another archived document.",
            ))
        if document.company_id != payslip.company_id:
            raise ValidationError(_(
                "The archived document and payroll record must belong to the "
                "same company.",
            ))
        if (
            document.availability_state != "available"
            or document.permission_sync_state != "synchronized"
        ):
            raise UserError(_(
                "The archived document is not ready for secure use. Wait for "
                "Documents access synchronization, then try again.",
            ))
        if document.mime_type != "application/pdf":
            raise ValidationError(_("Choose a PDF document."))
        version = document.version_ids.filtered("is_current")[:1]
        if not version or not version.checksum:
            raise UserError(_(
                "Documents has not synchronized the current file version yet. "
                "Try again after the next Documents refresh.",
            ))
        document._check_archive_binary_access()
        return payslip, document, version

    def action_link_document(self):
        payslip, document, version = self._validated_records()
        content, headers = document._paperless().download(
            document.paperless_id,
            version_id=version.paperless_version_id,
            original=True,
        )
        checksum = hashlib.sha256(content).hexdigest()
        if checksum != version.checksum:
            raise ValidationError(_(
                "The archived PDF no longer matches its verified Documents "
                "checksum. Nothing was linked.",
            ))
        content_type = (
            headers.get("Content-Type")
            or headers.get("content-type")
            or version.mime_type
            or document.mime_type
            or ""
        ).split(";", 1)[0].strip().casefold()
        if content_type != "application/pdf" or b"%PDF-" not in content[:1024]:
            raise ValidationError(_("The archived file is not a valid PDF."))

        filename = (
            version.original_filename
            or document.original_filename
            or document.name
            or _("TESE payroll.pdf")
        )
        attachment = self.env["ir.attachment"].with_context(
            usl_documents_skip_attachment_queue=True,
        ).create(
            {
                "name": filename,
                "type": "binary",
                "raw": content,
                "mimetype": "application/pdf",
                "res_model": payslip._name,
                "res_id": payslip.id,
                "company_id": payslip.company_id.id,
            },
        )
        payslip.with_context(
            usl_tese_skip_immediate_document_reconciliation=True,
        ).write({"attachment_id": attachment.id})
        raw_context = payslip._document_archive_context(attachment)
        raw_context["attachment_origin"] = "documents_workspace"
        archive_context = self.env["usl.document"]._prepare_archive_context(
            payslip,
            attachment,
            context=raw_context,
        )
        document._apply_archive_context(
            archive_context,
            submitted_by=self.env.user,
            access_user=self.env.user,
        )
        return payslip._notify(_(
            "The archived PDF is now the official TESE document. The existing "
            "Paperless original was reused without creating a duplicate.",
        ))

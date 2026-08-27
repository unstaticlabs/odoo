from odoo import _, api, fields, models


class UslTesePayslip(models.Model):
    _inherit = "usl.tese.payslip"

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
            document = operation.document_id or operation.target_document_id
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

    @api.model
    def cron_reconcile_archived_documents(self):
        payslips = self.sudo().search(
            [("attachment_id", "!=", False)],
            order="id",
            limit=500,
        )
        links = self.env["usl.document.link"].sudo().search(
            [
                ("res_model", "=", self._name),
                ("res_id", "in", payslips.ids),
                ("active", "=", True),
            ],
        )
        by_payslip = {link.res_id: link.document_id for link in links}
        pending = payslips.filtered(
            lambda payslip: (
                not by_payslip.get(payslip.id)
                or by_payslip[payslip.id].document_type_id.name != "Payroll record"
                or by_payslip[payslip.id].confidentiality != "hr"
                or not {"HR", "Payroll"}.issubset(
                    set(by_payslip[payslip.id].tag_ids.mapped("name")),
                )
                or not by_payslip[payslip.id].link_ids.filtered(
                    lambda link: (
                        link.active
                        and link.res_model == "hr.employee"
                        and link.res_id == payslip.employee_id.id
                    ),
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

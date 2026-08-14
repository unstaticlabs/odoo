from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class DocumentLinkMixin(models.AbstractModel):
    _name = "usl.document.link.mixin"
    _description = "Archived Document Link Mixin"

    archived_document_count = fields.Integer(
        string="Archived Documents", compute="_compute_archived_document_count",
    )
    document_archive_pending_count = fields.Integer(
        string="Documents processing", compute="_compute_document_archive_status",
    )
    document_archive_failure_count = fields.Integer(
        string="Document archive issues", compute="_compute_document_archive_status",
    )

    def _compute_archived_document_count(self):
        Document = self.env["usl.document"]
        for record in self:
            link_domain = [
                ("link_ids.res_model", "=", record._name),
                ("link_ids.res_id", "=", record.id),
                ("link_ids.active", "=", True),
            ]
            if record._name == "res.partner":
                record.archived_document_count = Document.search_count(
                    [
                        "|",
                        ("correspondent_id.partner_id", "=", record.id),
                        "&",
                        "&",
                        ("link_ids.res_model", "=", record._name),
                        ("link_ids.res_id", "=", record.id),
                        ("link_ids.active", "=", True),
                    ],
                )
            else:
                record.archived_document_count = Document.search_count(link_domain)

    def _compute_document_archive_status(self):
        # A status count is safe to expose on an already-authorized business
        # record. The technical operation itself retains its stricter owner /
        # administrator rules.
        Operation = self.env["usl.document.operation"].sudo()
        for record in self:
            domain = [
                ("res_model", "=", record._name),
                ("res_id", "=", record.id),
                ("acknowledged", "=", False),
            ]
            record.document_archive_pending_count = Operation.search_count(
                domain + [("state", "in", ("pending", "uploading", "processing"))],
            )
            record.document_archive_failure_count = Operation.search_count(
                domain + [("state", "in", ("failed", "duplicate"))],
            )

    def action_open_documents_workspace(self):
        self.ensure_one()
        self.check_access("read")
        action = self.env.ref("usl_documents.action_documents_workspace").read()[0]
        linked = bool(self.archived_document_count)
        action["params"] = {
            "res_model": self._name,
            "res_id": self.id,
            "record_name": self.display_name,
            "linked_filter": bool(linked),
            "mapped_partner_id": self.id if self._name == "res.partner" else False,
        }
        return action

    def _document_company(self):
        self.ensure_one()
        if self._name == "res.company":
            company = self
        else:
            company = getattr(self, "company_id", False) or self.env.company
        if not self.env.su and company not in self.env.user.company_ids:
            raise AccessError(_("You cannot access documents for this company."))
        return company

    def _document_archive_policy(self, attachment):
        """Return the stable archive policy for one native attachment."""
        self.ensure_one()
        return {
            "archive": True,
            "confidentiality": "internal",
            "accounting_evidence": False,
            "access_scope": "linked_record",
        }

    def _document_related_records(self, attachment=None):
        self.ensure_one()
        return [{"model": self._name, "id": self.id}]

    def _document_archive_context(self, attachment=None):
        """Describe business meaning without calling Paperless."""
        self.ensure_one()
        policy = self._document_archive_policy(attachment)
        company = self._document_company()
        return {
            **policy,
            "company_id": company.id,
            "tags": [],
            "entity_tags": [],
            "document_type": False,
            "correspondent_partner_id": False,
            "document_date": False,
            "related_records": self._document_related_records(attachment),
        }

    def _document_access_trigger_fields(self):
        return {"active", "company_id"}

    def _document_refresh_linked_access(self):
        links = self.env["usl.document.link"].sudo().search(
            [
                ("res_model", "=", self._name),
                ("res_id", "in", self.ids),
                ("active", "=", True),
            ],
        )
        documents = links.mapped("document_id")
        if documents:
            documents._recompute_linked_record_access(sync_permissions=True)
        return True

    def write(self, values):
        access_changed = bool(
            set(values).intersection(self._document_access_trigger_fields()),
        )
        result = super().write(values)
        if access_changed:
            self._document_refresh_linked_access()
        return result

    def action_archive_attachment(self, attachment_id, source="odoo_attachment"):
        self.ensure_one()
        self.check_access("read")
        attachment = self.env["ir.attachment"].browse(int(attachment_id)).exists()
        if not attachment:
            raise UserError(_("The attachment no longer exists."))
        attachment.check_access("read")
        if attachment.res_model != self._name or attachment.res_id != self.id:
            raise AccessError(_("This attachment does not belong to the current record."))
        if attachment.type != "binary" or not attachment.datas:
            raise UserError(_("Only stored binary attachments can be archived."))
        # Keep Odoo's operational copy available immediately. The archive worker
        # performs every Paperless call after the user's transaction has committed.
        operation = self.env["usl.document.operation"].queue_attachment(
            attachment,
            source=source,
        )
        if not operation:
            raise UserError(_("This attachment is not eligible for archival."))
        return {
            "state": operation.state,
            "operation_id": operation.id,
            "document_id": operation.document_id.id or False,
        }


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "usl.document.link.mixin"]

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        move_types = {
            "in_invoice": ("Vendor bills", "Supplier invoice"),
            "in_refund": ("Vendor bills", "Supplier credit note"),
            "in_receipt": ("Vendor bills", "Supplier invoice"),
            "out_invoice": ("Customer invoices", "Customer invoice"),
            "out_refund": ("Customer invoices", "Customer credit note"),
            "out_receipt": ("Customer invoices", "Customer invoice"),
            "entry": ("Journal entries", "Journal entry evidence"),
        }
        tag, document_type = move_types.get(
            self.move_type, ("Journal entries", "Journal entry evidence"),
        )
        values.update(
            {
                "confidentiality": "accounting",
                "accounting_evidence": True,
                "tags": ["Accounting", tag],
                "document_type": document_type,
                "correspondent_partner_id": self.partner_id.id or False,
                "document_date": fields.Date.to_string(
                    self.invoice_date or self.date,
                ),
            },
        )
        return values


class HrExpense(models.Model):
    _name = "hr.expense"
    _inherit = ["hr.expense", "usl.document.link.mixin"]

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values.update(
            {
                "confidentiality": "accounting",
                "accounting_evidence": True,
                "tags": ["Accounting", "Expenses"],
                "document_type": "Expense receipt",
                "correspondent_partner_id": self.vendor_id.id or False,
                "document_date": fields.Date.to_string(self.date),
            },
        )
        return values

    def _document_access_trigger_fields(self):
        return super()._document_access_trigger_fields() | {
            "employee_id",
            "state",
        }


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "usl.document.link.mixin"]

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values["correspondent_partner_id"] = self.id
        return values


class ResCompany(models.Model):
    _name = "res.company"
    _inherit = ["res.company", "usl.document.link.mixin"]

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values["tags"] = ["Company records"]
        return values


class ProjectProject(models.Model):
    _name = "project.project"
    _inherit = ["project.project", "usl.document.link.mixin"]

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values.update(
            {
                "tags": ["Projects"],
                "entity_tags": [
                    {
                        "namespace": "project",
                        "model": self._name,
                        "id": self.id,
                        "name": self.name,
                        "parent": "Projects",
                    },
                ],
                "document_type": "Project document",
            },
        )
        return values

    def _document_access_trigger_fields(self):
        return super()._document_access_trigger_fields() | {
            "privacy_visibility",
        }


class ProjectTask(models.Model):
    _name = "project.task"
    _inherit = ["project.task", "usl.document.link.mixin"]

    def _document_related_records(self, attachment=None):
        self.ensure_one()
        records = super()._document_related_records(attachment)
        if self.project_id:
            records.append({"model": "project.project", "id": self.project_id.id})
        return records

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values.update(
            {
                "tags": ["Projects"],
                "document_type": "Project document",
            },
        )
        if self.project_id:
            values["entity_tags"] = [
                {
                    "namespace": "project",
                    "model": "project.project",
                    "id": self.project_id.id,
                    "name": self.project_id.name,
                    "parent": "Projects",
                },
            ]
        return values

    def _document_access_trigger_fields(self):
        return super()._document_access_trigger_fields() | {
            "project_id",
            "user_ids",
        }


class HrEmployee(models.Model):
    _name = "hr.employee"
    _inherit = ["hr.employee", "usl.document.link.mixin"]

    def _document_archive_policy(self, attachment):
        return {
            **super()._document_archive_policy(attachment),
            "confidentiality": "hr",
        }

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values.update({"tags": ["HR"], "document_type": "Employee document"})
        return values


class AccountPayment(models.Model):
    _name = "account.payment"
    _inherit = ["account.payment", "usl.document.link.mixin"]

    def _document_related_records(self, attachment=None):
        self.ensure_one()
        records = super()._document_related_records(attachment)
        if self.move_id:
            records.append({"model": "account.move", "id": self.move_id.id})
        return records

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values.update(
            {
                "confidentiality": "accounting",
                "accounting_evidence": True,
                "tags": ["Accounting", "Payments"],
                "document_type": "Payment evidence",
                "correspondent_partner_id": self.partner_id.id or False,
                "document_date": fields.Date.to_string(self.date),
            },
        )
        return values


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def action_archive_in_paperless(self):
        operations = self.env["usl.document.operation"]
        allowed_models = self.env["usl.document.link"]._allowed_models()
        for attachment in self:
            attachment.check_access("read")
            if attachment.res_model not in allowed_models or not attachment.res_id:
                raise UserError(
                    _("Select an attachment belonging to a supported business record."),
                )
            record = self.env[attachment.res_model].browse(
                attachment.res_id,
            ).exists()
            if not record:
                raise UserError(_("The attachment's business record no longer exists."))
            operations |= attachment._queue_usl_documents_archive()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Archive retry queued"),
                "message": _(
                    "%(count)s attachment(s) will be checked in the background. "
                    "Odoo files remain available while Paperless processes them.",
                ) % {"count": len(operations)},
                "type": "success",
                "sticky": False,
            },
        }

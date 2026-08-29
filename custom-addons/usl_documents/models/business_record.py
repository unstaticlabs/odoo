from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class DocumentLinkMixin(models.AbstractModel):
    _name = "usl.document.link.mixin"
    _description = "Archived Document Link Mixin"

    archived_document_count = fields.Integer(
        string="Archived Documents",
        compute="_compute_archived_document_count",
    )
    document_archive_pending_count = fields.Integer(
        string="Documents processing", compute="_compute_document_archive_status",
    )
    document_archive_failure_count = fields.Integer(
        string="Document archive issues", compute="_compute_document_archive_status",
    )

    def _compute_archived_document_count(self):
        """Count readable documents in a bounded number of queries.

        Links and correspondent mappings are technical relationships, so they
        are collected with sudo.  The candidate documents are then searched in
        the caller's environment: record rules remain the final authority and
        no inaccessible document contributes to a badge.
        """
        for record in self:
            record.archived_document_count = 0

        counts = {record_id: set() for record_id in self.ids}
        if not counts:
            return

        Document = self.env["usl.document"]
        candidate_document_ids = set()
        links = self.env["usl.document.link"].sudo().search(
            [
                ("res_model", "=", self._name),
                ("res_id", "in", self.ids),
                ("active", "=", True),
            ],
        )
        for link in links:
            counts[link.res_id].add(link.document_id.id)
            candidate_document_ids.add(link.document_id.id)

        if self._name == "res.partner":
            correspondents = (
                self.env["usl.paperless.correspondent"]
                .sudo()
                .search([("partner_id", "in", self.ids)])
            )
            partner_by_correspondent = {
                correspondent.id: correspondent.partner_id.id
                for correspondent in correspondents
            }
            mapped_documents = Document.sudo().search(
                [("correspondent_id", "in", correspondents.ids)],
            )
            for document in mapped_documents:
                partner_id = partner_by_correspondent.get(document.correspondent_id.id)
                if partner_id:
                    counts[partner_id].add(document.id)
                    candidate_document_ids.add(document.id)

        visible_document_ids = set(
            Document.search([("id", "in", list(candidate_document_ids))]).ids,
        ) if candidate_document_ids else set()
        for record in self:
            record.archived_document_count = len(
                counts[record.id] & visible_document_ids,
            )

    def _compute_document_archive_status(self):
        # A status count is safe to expose on an already-authorized business
        # record. The technical operation itself retains its stricter owner /
        # administrator rules.
        for record in self:
            record.document_archive_pending_count = 0
            record.document_archive_failure_count = 0

        Operation = self.env["usl.document.operation"].sudo()
        pending_states = {"pending", "uploading", "processing"}
        failure_states = {"failed", "duplicate"}
        grouped = Operation._read_group(
            [
                ("res_model", "=", self._name),
                ("res_id", "in", self.ids),
                ("acknowledged", "=", False),
                ("state", "in", list(pending_states | failure_states)),
            ],
            ["res_id", "state"],
            ["__count"],
        ) if self.ids else []
        pending_by_record = {record_id: 0 for record_id in self.ids}
        failures_by_record = {record_id: 0 for record_id in self.ids}
        for record_id, state, count in grouped:
            target = (
                pending_by_record if state in pending_states else failures_by_record
            )
            target[record_id] = target.get(record_id, 0) + count
        for record in self:
            record.document_archive_pending_count = pending_by_record.get(record.id, 0)
            record.document_archive_failure_count = failures_by_record.get(
                record.id,
                0,
            )

    def action_open_documents_workspace(self):
        self.ensure_one()
        # A record-scoped client action must keep the record's legal company
        # active for every later RPC.  The web client can otherwise retain a
        # different company from the switcher and fail while reading related
        # records (notably hr.employee on TESE payrolls).  Resolve only the
        # company under sudo, then re-run the normal record access check with
        # that company added to the caller's active, authorized companies.
        sudo_record = self.sudo()
        company = (
            sudo_record
            if self._name == "res.company"
            else getattr(sudo_record, "company_id", False)
            or self.env.company
        )
        if not self.env.su and company not in self.env.user.company_ids:
            raise AccessError(_("You cannot access documents for this company."))
        authorized_company_ids = set(self.env.user.company_ids.ids)
        active_company_ids = [
            company_id
            for company_id in (
                self.env.context.get("allowed_company_ids")
                or self.env.companies.ids
            )
            if self.env.su or company_id in authorized_company_ids
        ]
        if company.id not in active_company_ids:
            active_company_ids.append(company.id)
        record = self.with_context(allowed_company_ids=active_company_ids)
        record.check_access("read")
        action = self.env.ref("usl_documents.action_documents_workspace").read()[0]
        action["context"] = {"allowed_company_ids": active_company_ids}
        action["params"] = {
            "res_model": record._name,
            "res_id": record.id,
            "record_name": record.display_name,
            "linked_filter": True,
            "mapped_partner_id": (
                record.id if record._name == "res.partner" else False
            ),
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
        origin = (
            attachment.usl_documents_origin
            if attachment and "usl_documents_origin" in attachment._fields
            else self.env.context.get(
                "usl_documents_policy_origin",
                "documents_workspace",
            )
        )
        if origin == "generated_transient":
            return {
                "archive_mode": "never",
                "document_role": "background",
                "policy_reason": "transient_generated_output",
                "confidentiality": "internal",
                "accounting_evidence": False,
                "access_scope": "linked_record",
            }
        if origin == "generated_final":
            return {
                "archive_mode": "automatic",
                "document_role": "evidence",
                "policy_reason": "final_generated_output",
                "confidentiality": "internal",
                "accounting_evidence": True,
                "access_scope": "linked_record",
            }
        return {
            "archive_mode": "automatic",
            "document_role": "library",
            "policy_reason": "business_record_default",
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
            "attachment_origin": (
                attachment.usl_documents_origin
                if attachment and "usl_documents_origin" in attachment._fields
                else self.env.context.get(
                    "usl_documents_policy_origin",
                    "documents_workspace",
                )
            ),
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
        if attachment.type != "binary" or not attachment.file_size:
            raise UserError(_("Only stored binary attachments can be archived."))
        # Keep Odoo's operational copy available immediately. The archive worker
        # performs every Paperless call after the user's transaction has committed.
        operation = self.env["usl.document.operation"]._queue_attachment(
            attachment,
            source=source,
            force_on_request=True,
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

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never":
            return policy
        return {
            **policy,
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "accounting_move_evidence",
            "confidentiality": "accounting",
            "accounting_evidence": True,
        }

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

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never":
            return policy
        return {
            **policy,
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "expense_evidence",
            "confidentiality": "accounting",
            "accounting_evidence": True,
        }

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

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if (
            policy["archive_mode"] != "never"
            and policy.get("document_role") != "evidence"
            and attachment
            and attachment.usl_documents_origin == "chatter"
        ):
            policy.update(
                {
                    "archive_mode": "on_request",
                    "document_role": "library",
                    "policy_reason": "contact_chatter_on_request",
                },
            )
        return policy

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values["correspondent_partner_id"] = self.id
        return values


class ResCompany(models.Model):
    _name = "res.company"
    _inherit = ["res.company", "usl.document.link.mixin"]

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if (
            policy["archive_mode"] != "never"
            and policy.get("document_role") != "evidence"
            and attachment
            and attachment.usl_documents_origin == "chatter"
        ):
            policy.update(
                {
                    "archive_mode": "on_request",
                    "document_role": "library",
                    "policy_reason": "company_chatter_on_request",
                },
            )
        return policy

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values["tags"] = ["Company records"]
        return values


class ProjectProject(models.Model):
    _name = "project.project"
    _inherit = ["project.project", "usl.document.link.mixin"]

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never" or policy["document_role"] == "evidence":
            return policy
        origin = (
            attachment.usl_documents_origin
            if attachment
            else self.env.context.get(
                "usl_documents_policy_origin",
                "documents_workspace",
            )
        )
        if origin == "chatter":
            return {
                **policy,
                "archive_mode": "on_request",
                "document_role": "library",
                "policy_reason": "project_chatter_on_request",
            }
        if origin == "documents_workspace":
            return {
                **policy,
                "archive_mode": "automatic",
                "document_role": "library",
                "policy_reason": "project_documents_upload",
            }
        return {
            **policy,
            "archive_mode": "automatic",
            "document_role": "background",
            "policy_reason": "project_direct_attachment",
        }

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

    def _document_archive_policy(self, attachment):
        return (
            self.project_id._document_archive_policy(attachment)
            if self.project_id
            else super()._document_archive_policy(attachment)
        )

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
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never":
            return policy
        return {
            **policy,
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "employee_evidence",
            "confidentiality": "hr",
            "accounting_evidence": True,
        }

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values.update({"tags": ["HR"], "document_type": "Employee document"})
        return values


class AccountPayment(models.Model):
    _name = "account.payment"
    _inherit = ["account.payment", "usl.document.link.mixin"]

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never":
            return policy
        return {
            **policy,
            "archive_mode": "mandatory",
            "document_role": "evidence",
            "policy_reason": "payment_evidence",
            "confidentiality": "accounting",
            "accounting_evidence": True,
        }

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
            record = (
                self.env[attachment.res_model]
                .browse(
                    attachment.res_id,
                )
                .exists()
            )
            if not record:
                raise UserError(_("The attachment's business record no longer exists."))
            operations |= attachment._queue_usl_documents_archive(
                force_on_request=True,
            )
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

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class DocumentLinkMixin(models.AbstractModel):
    _name = "usl.document.link.mixin"
    _description = "Archived Document Link Mixin"

    archived_document_count = fields.Integer(
        string="Archived Documents", compute="_compute_archived_document_count",
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
        if company not in self.env.user.company_ids:
            raise AccessError(_("You cannot access documents for this company."))
        return company

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
        # Existing attachments are intentionally retained. The checksum associates
        # both copies while Paperless processing completes.
        return self.env["usl.document"].upload_from_odoo(
            attachment.name,
            attachment.datas.decode()
            if isinstance(attachment.datas, bytes)
            else attachment.datas,
            attachment.mimetype,
            res_model=self._name,
            res_id=self.id,
            company_id=self._document_company().id,
            source=source,
        )


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "usl.document.link.mixin"]


class HrExpense(models.Model):
    _name = "hr.expense"
    _inherit = ["hr.expense", "usl.document.link.mixin"]


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "usl.document.link.mixin"]


class ResCompany(models.Model):
    _name = "res.company"
    _inherit = ["res.company", "usl.document.link.mixin"]


class ProjectProject(models.Model):
    _name = "project.project"
    _inherit = ["project.project", "usl.document.link.mixin"]


class ProjectTask(models.Model):
    _name = "project.task"
    _inherit = ["project.task", "usl.document.link.mixin"]


class HrEmployee(models.Model):
    _name = "hr.employee"
    _inherit = ["hr.employee", "usl.document.link.mixin"]


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def action_archive_in_paperless(self):
        results = []
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
            source = "odoo_attachment"
            if (
                attachment.res_model == "account.move"
                and record.state == "posted"
                and attachment.mimetype == "application/pdf"
            ):
                source = "odoo_generated"
            results.append(
                record.action_archive_attachment(attachment.id, source=source)
                | {"archive_source": source},
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Archive request created"),
                "message": _(
                    "%(count)s attachment(s) sent to Paperless. Odoo copies were retained.",
                ) % {"count": len(results)},
                "type": "success",
                "sticky": False,
            },
        }

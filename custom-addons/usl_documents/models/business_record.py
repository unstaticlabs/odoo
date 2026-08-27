import base64

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class DocumentLinkMixin(models.AbstractModel):
    _name = "usl.document.link.mixin"
    _description = "Archived Document Link Mixin"

    archived_document_count = fields.Integer(
        string="Archived Documents",
        compute="_compute_archived_document_count",
    )

    def _compute_archived_document_count(self):
        """Count readable documents in a bounded number of queries.

        Links and correspondent mappings are technical relationships, so they
        are collected with sudo.  The candidate documents are then searched in
        the caller's environment: record rules remain the final authority and
        no inaccessible document contributes to a badge.
        """
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
            raise AccessError(
                _("This attachment does not belong to the current record."),
            )
        content = bytes(attachment.raw or b"")
        if attachment.type != "binary" or not content:
            raise UserError(_("Only stored binary attachments can be archived."))
        # Existing attachments are intentionally retained. The checksum associates
        # both copies while Paperless processing completes.
        return self.env["usl.document"].upload_from_odoo(
            attachment.name,
            base64.b64encode(content).decode(),
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
            record = (
                self.env[attachment.res_model]
                .browse(
                    attachment.res_id,
                )
                .exists()
            )
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
                )
                % {"count": len(results)},
                "type": "success",
                "sticky": False,
            },
        }

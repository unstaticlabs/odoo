"""Authorized links between documents and business records."""

from odoo import (
    _,
    api,
    fields,
    models,
)
from odoo.exceptions import AccessError, UserError, ValidationError

from .document import ARCHIVE_MODES, ATTACHMENT_ORIGINS, DOCUMENT_ROLES


class UslDocumentLink(models.Model):
    _name = "usl.document.link"
    _description = "Archived Document Business Relationship"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    document_id = fields.Many2one(
        "usl.document", required=True, index=True, ondelete="restrict", tracking=True,
    )
    res_model = fields.Char(required=True, index=True, readonly=True)
    res_id = fields.Integer(required=True, index=True, readonly=True)
    record_name = fields.Char(required=True, readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="restrict", readonly=True,
    )
    linked_by_id = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user,
    )
    linked_at = fields.Datetime(
        required=True, readonly=True, default=fields.Datetime.now,
    )
    version_id = fields.Char(
        help="Paperless version that supports this business record, when legally relevant.",
        readonly=True,
    )
    archive_mode = fields.Selection(
        ARCHIVE_MODES,
        required=True,
        default="automatic",
        readonly=True,
        index=True,
    )
    policy_role = fields.Selection(
        DOCUMENT_ROLES,
        required=True,
        default="library",
        readonly=True,
        index=True,
        help="Document role resolved by the business archive policy.",
    )
    document_role = fields.Selection(
        DOCUMENT_ROLES,
        required=True,
        default="library",
        tracking=True,
        index=True,
        help=(
            "Current Odoo presentation role. Promotion or demotion changes only "
            "this relationship and never uploads another Paperless file."
        ),
    )
    attachment_origin = fields.Selection(
        ATTACHMENT_ORIGINS,
        required=True,
        default="migration",
        readonly=True,
        index=True,
    )
    policy_reason = fields.Char(
        required=True,
        default="legacy_relationship_backfill_pending",
        readonly=True,
        index=True,
    )
    active = fields.Boolean(default=True, tracking=True)

    _record_link_unique = models.Constraint(
        "UNIQUE(document_id, res_model, res_id)",
        "This archived document is already linked to that Odoo record.",
    )
    _active_record_lookup_idx = models.Index(
        "(res_model, res_id, document_id) WHERE active IS TRUE",
    )

    @api.model_create_multi
    def create(self, vals_list):
        protected = {
            "archive_mode",
            "policy_role",
            "document_role",
            "attachment_origin",
            "policy_reason",
        }
        if any(protected.intersection(values) for values in vals_list) and not (
            self.env.su
            and self.env.context.get("usl_documents_link_policy_write")
        ):
            raise AccessError(
                _("Document relationship policy can only change through Documents."),
            )
        return super().create(vals_list)

    @api.model
    def _allowed_models(self):
        return {
            "account.move",
            "account.payment",
            "hr.expense",
            "res.partner",
            "res.company",
            "project.project",
            "project.task",
            "hr.employee",
        }

    @api.model
    def create_for_record(
        self,
        document,
        res_model,
        res_id,
        version_id=None,
        *,
        archive_mode="automatic",
        policy_role="library",
        attachment_origin="documents_workspace",
        policy_reason="manual_documents_link",
    ):
        document.ensure_one()
        document.check_access("write")
        allow_trashed_link = (
            self.env.su
            and self.env.context.get("usl_documents_allow_trashed_link")
            and document.availability_state == "trashed"
        )
        # A permission_error root is live in Paperless; only its last access
        # push failed. Linking it is what lets the next access recomputation
        # repair that push, so refusing here would latch the failure instead.
        if (
            document.availability_state not in ("available", "permission_error")
            and not allow_trashed_link
        ):
            raise UserError(
                _("Only an available archived document can receive a new Odoo link."),
            )
        if res_model not in self._allowed_models():
            raise ValidationError(_("This Odoo model cannot receive archived documents."))
        if version_id and not document.version_ids.filtered(
            lambda version: version.paperless_version_id == str(version_id),
        ):
            raise ValidationError(
                _("The selected file version does not belong to this document."),
            )
        record = self.env[res_model].browse(res_id).exists()
        if not record:
            raise ValidationError(_("The target Odoo record no longer exists."))
        record.check_access("read")
        company = (
            getattr(record, "company_id", False)
            or document.company_id
            or self.env.company
        )
        if res_model == "res.company":
            company = record
        if company not in self.env.user.company_ids:
            raise AccessError(_("You cannot link records from this company."))
        if document.company_id and document.company_id != company:
            raise ValidationError(
                _("The document and Odoo record must belong to the same legal company."),
            )
        existing = self.sudo().search([
            ("document_id", "=", document.id),
            ("res_model", "=", res_model),
            ("res_id", "=", res_id),
        ], limit=1)
        if existing:
            diagnostics = {}
            if not existing.active:
                diagnostics["active"] = True
            if version_id and not existing.version_id:
                diagnostics["version_id"] = str(version_id)
            if existing.policy_reason == "legacy_relationship_backfill_pending":
                diagnostics.update(
                    {
                        "archive_mode": archive_mode,
                        "policy_role": policy_role,
                        "document_role": policy_role,
                        "attachment_origin": attachment_origin,
                        "policy_reason": policy_reason,
                    },
                )
            if diagnostics:
                existing.sudo().with_context(
                    usl_documents_link_policy_write=True,
                ).write(diagnostics)
            if not self.env.context.get("usl_documents_defer_access_sync"):
                document._recompute_linked_record_access(sync_permissions=True)
                document.reconcile_linked_classification(limit=1000)
            return existing
        if not document.company_id:
            document.sudo().with_context(usl_documents_policy_write=True).write(
                {
                    "company_id": company.id,
                    "review_state": "classified",
                },
            )
        record_name = (record.display_name or "").strip()
        if not record_name:
            record_name = (
                document.original_filename or document.name or ""
            ).strip()
        if not record_name:
            model = self.env["ir.model"]._get(res_model)
            record_name = _(
                "%(model)s #%(record_id)s",
                model=model.name or record._description,
                record_id=record.id,
            )
        link = self.sudo().with_context(
            usl_documents_link_policy_write=True,
        ).create(
            {
                "document_id": document.id,
                "res_model": res_model,
                "res_id": res_id,
                "record_name": record_name,
                "company_id": company.id,
                "linked_by_id": (
                    int(self.env.context.get("usl_documents_linked_by_id"))
                    if self.env.su
                    and self.env.context.get("usl_documents_linked_by_id")
                    else self.env.user.id
                ),
                "version_id": (
                    str(version_id)
                    if version_id
                    else document.version_ids.filtered("is_current")[
                        :1
                    ].paperless_version_id
                    or False
                ),
                "archive_mode": archive_mode,
                "policy_role": policy_role,
                "document_role": policy_role,
                "attachment_origin": attachment_origin,
                "policy_reason": policy_reason,
            },
        )
        if not self.env.context.get("usl_documents_defer_access_sync"):
            document._recompute_linked_record_access(sync_permissions=True)
            document.reconcile_linked_classification(limit=1000)
        return link

    def action_open_record(self):
        self.ensure_one()
        return self._record_action()

    def _record_action(self, user_env=None):
        self.ensure_one()
        user_env = user_env or self.env
        record = user_env[self.res_model].browse(self.res_id).exists()
        if not record:
            raise UserError(_("The linked Odoo record no longer exists."))
        record.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": record.display_name,
            "res_model": self.res_model,
            "res_id": self.res_id,
            "view_mode": "form",
            "views": [(False, "form")],
        }

    def unlink(self):
        if self.env.su:
            return super().unlink()
        # Relationships can be removed; the archived original is deliberately untouched.
        for link in self:
            record = (
                self.env[link.res_model].browse(link.res_id).exists()
                if link.res_model in self.env
                else False
            )
            if record:
                record.check_access("read")
        documents = self.mapped("document_id")
        result = super().unlink()
        documents._recompute_linked_record_access(sync_permissions=True)
        return result

    def write(self, vals):
        protected = {
            "archive_mode",
            "policy_role",
            "document_role",
            "attachment_origin",
            "policy_reason",
        }
        if protected.intersection(vals) and not (
            self.env.su
            and self.env.context.get("usl_documents_link_policy_write")
        ):
            raise AccessError(
                _("Document relationship policy can only change through Documents."),
            )
        documents = self.mapped("document_id") if "active" in vals else self.env[
            "usl.document"
        ]
        result = super().write(vals)
        if documents:
            documents._recompute_linked_record_access(sync_permissions=True)
        return result

from odoo import api, fields, models
from odoo.exceptions import ValidationError


SESSION_MODEL = "b2c.accounting.session"


class B2cOrder(models.Model):
    _name = "b2c.order"
    _inherit = ["b2c.order", "usl.document.link.mixin"]


class B2cPaymentEvent(models.Model):
    _name = "b2c.payment.event"
    _inherit = ["b2c.payment.event", "usl.document.link.mixin"]


class B2cFulfilmentEvent(models.Model):
    _name = "b2c.fulfilment.event"
    _inherit = ["b2c.fulfilment.event", "usl.document.link.mixin"]


class B2cAccountingSession(models.Model):
    _name = "b2c.accounting.session"
    _inherit = ["b2c.accounting.session", "usl.document.link.mixin"]

    def _has_company_bound_evidence(self):
        if super()._has_company_bound_evidence():
            return True
        # Inactive relationships still preserve the original archive ownership.
        return bool(self.env["usl.document.link"].sudo().with_context(active_test=False).search_count(
            [("res_model", "=", SESSION_MODEL), ("res_id", "in", self.ids)], limit=1,
        ))


class B2cProviderEvidence(models.Model):
    _inherit = "b2c.provider.evidence"

    archived_document_id = fields.Many2one(
        "usl.document",
        string="Archived Source Document",
        check_company=True,
        ondelete="restrict",
        copy=False,
        readonly=True,
        groups="usl_b2c.group_b2c_sensitive_evidence",
        help=(
            "Restricted archived file supporting this immutable provider row. "
            "The relationship does not expose the provider payload."
        ),
    )


class UslDocumentLink(models.Model):
    _inherit = "usl.document.link"

    def _record_b2c_archive_relationship(self):
        links = self.filtered(lambda link: link.res_model == SESSION_MODEL)
        if not links:
            return
        sessions = self.env[SESSION_MODEL].browse(set(links.mapped("res_id"))).exists()
        sessions.check_access("read")
        companies = {session.id: session.company_id for session in sessions}
        for link in links:
            company = companies.get(link.res_id)
            document_company = link.document_id.company_id
            if not company or link.company_id != company or (document_company and document_company != company):
                raise ValidationError(self.env._("The document and Odoo record must belong to the same legal company."))
        # Documents may link evidence with session read access, including after locking.
        # Update only native audit columns, using the same row as a company change.
        # PostgreSQL makes a competing writer retry without requiring session write access.
        audit_fields = ["write_uid", "write_date"]
        sessions.flush_recordset(audit_fields)
        sessions._write({})
        sessions.invalidate_recordset(audit_fields, flush=False)

    @api.model_create_multi
    def create(self, vals_list):
        if all("res_model" in vals and vals["res_model"] != SESSION_MODEL for vals in vals_list):
            return super().create(vals_list)
        with self.env.cr.savepoint():
            links = super().create(vals_list)
            links._record_b2c_archive_relationship()
            return links

    def write(self, vals):
        if not {"res_model", "res_id", "company_id", "document_id"}.intersection(vals):
            return super().write(vals)
        with self.env.cr.savepoint():
            result = super().write(vals)
            self._record_b2c_archive_relationship()
            return result

    def _allowed_models(self):
        return super()._allowed_models() | {
            "b2c.order",
            "b2c.payment.event",
            "b2c.fulfilment.event",
            "b2c.accounting.session",
        }

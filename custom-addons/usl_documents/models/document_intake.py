"""Emailed document intake.

One incoming message to the Documents alias becomes one intake record. The
attachments it carries are ordinary ``ir.attachment`` rows on that record, so
the existing capture policy, archive queue and Paperless bridge apply unchanged.
This deliberately adds a front door, not a second ingestion path.
"""

from odoo import _, api, fields, models
from odoo.tools import email_normalize


class UslDocumentIntake(models.Model):
    _name = "usl.document.intake"
    _description = "Emailed Document"
    _inherit = ["mail.thread", "usl.document.link.mixin"]
    _order = "received_at desc, id desc"

    name = fields.Char(string="Subject", required=True, readonly=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        readonly=True,
        index=True,
        default=lambda self: self.env.company,
    )
    email_from = fields.Char(
        string="Sent by",
        readonly=True,
        help="From header of the message this record was created from.",
    )
    sender_user_id = fields.Many2one(
        "res.users",
        string="Sender",
        readonly=True,
        index=True,
        help="Internal user matched to the sending address, when it matched one.",
    )
    received_at = fields.Datetime(
        string="Received",
        required=True,
        readonly=True,
        index=True,
        default=fields.Datetime.now,
        help="When Odoo accepted the message, not the date its sender wrote.",
    )

    @api.model
    def _intake_sender(self, email_from):
        """Resolve the internal sender and the company that owns the intake.

        The alias already refuses senders without an employee record, so an
        unresolved address here means the two lookups disagree — an employee
        matched by a wildcard the exact comparison below rejects. Falling back
        to the environment company keeps the document rather than losing it.
        """
        address = email_normalize(email_from, strict=False)
        users = self.env["res.users"].sudo()
        employees = self.env["hr.employee"].sudo()
        if not address:
            return users, self.env.company
        user = users.search(
            [
                ("share", "=", False),
                ("active", "=", True),
                ("email_normalized", "=", address),
            ],
            limit=1,
        )
        employee = employees
        if user:
            # A user can hold one employee record per company. The company that
            # owns the intake must be the one the sender works in by default,
            # not whichever record the search happened to return first.
            for_user = employees.search([("user_id", "=", user.id)])
            employee = for_user.filtered(
                lambda record: record.company_id == user.company_id,
            )[:1] or for_user[:1]
        if not employee:
            employee = employees.search([("work_email", "=ilike", address)], limit=1)
            user = user or employee.user_id
        company = employee.company_id or user.company_id or self.env.company
        return user, company

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        values = dict(custom_values or {})
        email_from = msg_dict.get("email_from") or ""
        user, company = self._intake_sender(email_from)
        subject = str(msg_dict.get("subject") or "").strip()
        values.setdefault("name", subject or _("Emailed document"))
        values.setdefault("email_from", email_from)
        values.setdefault("received_at", fields.Datetime.now())
        values.setdefault("company_id", company.id)
        if user:
            values.setdefault("sender_user_id", user.id)
        return super().message_new(msg_dict, values)

    def _document_archive_policy(self, attachment):
        policy = super()._document_archive_policy(attachment)
        if policy["archive_mode"] == "never":
            return policy
        return {
            **policy,
            "archive_mode": "automatic",
            "document_role": "library",
            "policy_reason": "emailed_document_intake",
            "confidentiality": "internal",
            "accounting_evidence": False,
        }

    def _document_archive_context(self, attachment=None):
        self.ensure_one()
        values = super()._document_archive_context(attachment)
        values["tags"] = ["Email intake"]
        return values

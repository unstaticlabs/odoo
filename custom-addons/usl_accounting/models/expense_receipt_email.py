"""Expense receipt copies retain the source email's native visibility."""

import json
import logging
from urllib.parse import urlencode

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.binary import BinaryBytes

from ..tools.expense_receipt_email import MAX_SOURCE_BYTES, incidental_terms, receipt_email

_logger = logging.getLogger(__name__)
REPORT = "usl_accounting.expense_receipt_email"


class HrExpense(models.Model):
    _inherit = "hr.expense"

    receipt_email_url = fields.Char(compute="_compute_receipt_email_url", string="Receipt email", readonly=True)

    def _receipt_email_info(self):
        self.ensure_one()
        if not self.id or not isinstance(self.id, int) or not self.has_access("read"):
            return None
        if self.company_id not in self.env.companies:
            return None
        source = self.split_expense_origin_id or self
        if source != self and (
            not source.has_access("read") or source.company_id != self.company_id
            or source.split_expense_origin_id != source
        ):
            return None
        messages = self.env["mail.message"].search([
            ("model", "=", self._name), ("res_id", "=", source.id), ("message_type", "=", "email"),
        ], order="id desc")
        found = [(message, info) for message in messages if message.has_access("read") and (info := receipt_email(message.body))]
        if not found or len({info["reference"] for _message, info in found}) != 1:
            return None
        message, info = found[0]
        return dict(info, message=message, source_attachments=messages.browse([item.id for item, _info in found]).attachment_ids)

    def _receipt_email_report_sources(self):
        expenses = self.exists()
        if len(expenses) != len(set(self.ids)):
            raise AccessError(self.env._("The receipt email is unavailable."))
        expenses.check_access("read")
        sources = []
        for expense in expenses:
            info = expense._receipt_email_info()
            if not info:
                raise UserError(self.env._("No single readable receipt email was identified. Open the original conversation or choose the matching receipt manually."))
            info["message"].check_access("read")
            sources.append(info)
        return sources


    def _receipt_email_identity(self):
        info = self._receipt_email_info()
        if not info:
            return None
        main = self.message_main_attachment_id
        if main and not self._incidental_receipt_attachment(main) and main not in info["source_attachments"]:
            # A manual receipt correction remains authoritative.
            return None
        return info["provider"], info["reference"]

    def _incidental_receipt_attachment(self, attachment):
        if not attachment.has_access("read") or not 0 < attachment.file_size <= MAX_SOURCE_BYTES:
            return False
        try:
            return incidental_terms(attachment.raw.content)
        except AccessError:
            return False

    @api.depends(
        "company_id", "employee_id", "message_ids.body", "message_ids.message_type", "message_ids.attachment_ids",
        "split_expense_origin_id", "split_expense_origin_id.company_id", "split_expense_origin_id.message_ids.body",
    )
    @api.depends_context("uid", "allowed_company_ids", "lang", "tz")
    def _compute_receipt_email_url(self):
        for expense in self:
            info = expense._receipt_email_info()
            context = {"allowed_company_ids": expense.env.companies.ids, "lang": expense.env.lang}
            if expense.env.context.get("tz"):
                context["tz"] = expense.env.context["tz"]
            expense.receipt_email_url = (
                f"/report/html/{REPORT}/{expense.id}?" + urlencode({"context": json.dumps(context)})
                if info else False
            )

    @api.depends("company_id", "employee_id", "attachment_ids.checksum", "message_main_attachment_id", "message_ids.body", "message_ids.attachment_ids", "split_expense_origin_id")
    @api.depends_context("uid", "allowed_company_ids")
    def _compute_same_receipt_expense_ids(self):
        self.same_receipt_expense_ids = [Command.clear()]
        expenses = self.filtered(lambda expense: isinstance(expense.id, int) and expense.id and not expense.split_expense_origin_id)
        if not expenses:
            return
        Expense = self.env["hr.expense"]
        visible = Expense._search([])
        attachments = {
            expense.id: (expense.attachment_ids | expense.message_main_attachment_id).filtered(
                lambda attachment: attachment.has_access("read") and attachment.checksum
                and not expense._incidental_receipt_attachment(attachment)
            ) for expense in expenses
        }
        checksums = {attachment.checksum for group in attachments.values() for attachment in group}
        grouped = dict(self.env["ir.attachment"]._read_group([
            ("res_model", "=", self._name), ("res_id", "in", visible), ("checksum", "in", list(checksums)),
        ], ["checksum"], ["res_id:array_agg"])) if checksums else {}
        # A source-message attachment can be the native main of an expense,
        # including a shared original. Keep byte-identity warnings symmetric.
        for peer in Expense.search([("message_main_attachment_id.checksum", "in", list(checksums))]) if checksums else Expense:
            main = peer.message_main_attachment_id
            if main.has_access("read"):
                grouped.setdefault(main.checksum, []).append(peer.id)
        identities = {}
        for expense in expenses:
            peers = {peer for attachment in attachments[expense.id] for peer in grouped.get(attachment.checksum, [])}
            identity = identities.setdefault(expense.id, expense._receipt_email_identity())
            if identity:
                messages = self.env["mail.message"].search([
                    ("model", "=", self._name), ("res_id", "in", visible),
                    ("message_type", "=", "email"), ("body", "ilike", identity[1]),
                ])
                for peer in Expense.search([("id", "in", messages.mapped("res_id"))]):
                    if peer.id not in identities:
                        identities[peer.id] = peer._receipt_email_identity()
                    if identities[peer.id] == identity:
                        peers.add(peer.id)
            expense.same_receipt_expense_ids = [Command.set(sorted(peers - {expense.id}))]

    def _get_mail_thread_data_attachments(self):
        attachments = super()._get_mail_thread_data_attachments()
        # Mail can pass a sudo thread after checking the expense. Extra source
        # evidence must still be checked as the actual requesting user.
        actor = self.sudo(False)
        if not actor.has_access("read"):
            return attachments
        main = actor.message_main_attachment_id
        if (
            not main or not main.has_access("read")
            or main.res_model != "mail.message" or main.mimetype != "application/pdf"
        ):
            return attachments
        origin = actor.split_expense_origin_id or actor
        if origin != actor and (
            not origin.has_access("read") or origin.company_id != actor.company_id
            or origin.split_expense_origin_id != origin
        ):
            return attachments
        source = actor.env["mail.message"].browse(main.res_id).exists()
        if source and source.has_access("read") and source.model == actor._name and source.res_id == origin.id:
            attachments |= main
        return attachments

    def _message_set_main_attachment_id(self, attachments, force=False, filter_xml=True):
        if not force:
            attachments = attachments.filtered(lambda attachment: not self._incidental_receipt_attachment(attachment))
        return super()._message_set_main_attachment_id(attachments, force=force, filter_xml=filter_xml)

    def _auto_receipt_email_copy(self, message):
        self.ensure_one()
        if (
            self.state not in {"draft", "submitted"} or self.message_main_attachment_id or self.split_expense_origin_id
            or message.message_type != "email" or message.model != self._name or message.res_id != self.id
            or not self.has_access("write") or not message.has_access("read") or not message.has_access("write")
        ):
            return
        info = self._receipt_email_info()
        if not info or info["message"] != message:
            return
        if self.env["usl.mail.pdf.retrieval"]._message_has_receipt(message):
            return
        # Do not replace a real receipt left without a main selection.
        if (self.attachment_ids - message.attachment_ids).filtered(lambda attachment: attachment.mimetype == "application/pdf" or (attachment.mimetype or "").startswith("image/")):
            return
        try:
            with self.env.cr.savepoint():
                content, output_format = self.env["ir.actions.report"]._render_qweb_pdf(REPORT, res_ids=self.ids)
                if output_format != "pdf" or not isinstance(content, bytes) or not content.startswith(b"%PDF-"):
                    _logger.info("Receipt email renderer returned no PDF; original email and files retained")
                    return
        except AccessError:
            raise
        except (UserError, OSError):
            _logger.info("Receipt email PDF rendering unavailable; original email and files retained")
            return
        attachment = self.env["ir.attachment"].create({
            "name": self.env._("Receipt email.pdf"),
            "raw": BinaryBytes(content), "mimetype": "application/pdf", "type": "binary",
            "res_model": "mail.message", "res_id": message.id, "company_id": False,
            "public": False, "access_token": False,
            "description": self.env._("Rendered copy of the original receipt email, retained in the expense chatter."),
        })
        message.write({"attachment_ids": [Command.link(attachment.id)]})
        self._message_set_main_attachment_id(attachment)
        self.env["usl.mail.pdf.retrieval"]._supersede_for_expense(self)

    def _message_post_after_hook(self, message, msg_values):
        if len(self) == 1:
            self._auto_receipt_email_copy(message)
        return super()._message_post_after_hook(message, msg_values)

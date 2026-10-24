import hashlib
import json

from markupsafe import Markup

from odoo import Command, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL


class PurchaseMailGroupingReview(models.TransientModel):
    _name = "usl.purchase.mail.grouping.review"
    _description = "Review Purchase Mail Attachments"

    move_id = fields.Many2one("account.move", string="Bill", required=True, readonly=True)
    fingerprint = fields.Char(string="Attachment fingerprint", required=True, readonly=True)
    # Retain the original generic API fields; the form uses one choice per file.
    attachment_ids = fields.Many2many("ir.attachment", string="Current attachments", readonly=True)
    main_attachment_id = fields.Many2one("ir.attachment", string="Main document")
    legacy_main_explicit = fields.Boolean(readonly=True, default=False)
    line_ids = fields.One2many("usl.purchase.mail.grouping.review.line", "review_id", string="Documents")
    has_separate_files = fields.Boolean(compute="_compute_has_separate_files")
    result_move_id = fields.Many2one("account.move", string="Created draft bill", readonly=True)
    summary = fields.Text(related="move_id.usl_mail_grouping_summary", readonly=True, compute_sudo=False)

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for values in vals_list:
            values = dict(values)
            if "main_attachment_id" in values and "line_ids" not in values:
                values["legacy_main_explicit"] = True
            prepared.append(values)
        return super().create(prepared)

    def write(self, vals):
        if "main_attachment_id" in vals:
            vals = dict(vals, legacy_main_explicit=True)
        return super().write(vals)

    @api.depends("line_ids.use")
    def _compute_has_separate_files(self):
        for review in self:
            review.has_separate_files = any(line.use == "separate" for line in review.line_ids)

    def _locked_source(self):
        self.ensure_one()
        move = self.move_id
        move.check_access("write")
        self.env.cr.execute(SQL("SELECT id FROM account_move WHERE id = %s FOR UPDATE", move.id))
        move.invalidate_recordset()
        return move

    def _current_choices(self, move):
        current, attachments = move._mail_grouping_fingerprint()
        if current != self.fingerprint:
            raise UserError(self.env._("The attachments changed. Close this review and open it again to review the current files."))
        chosen = self.line_ids.mapped("attachment_id")
        if chosen - attachments or len(chosen) != len(self.line_ids):
            raise UserError(self.env._("Choose readable attachments belonging to this bill, once each."))
        main = self.line_ids.filtered(lambda line: line.use == "main").mapped("attachment_id")
        if len(main) > 1:
            raise UserError(self.env._("Choose only one main document."))
        # Old API callers may still change main_attachment_id directly.
        if self.legacy_main_explicit:
            main = self.main_attachment_id
        if main:
            main.check_access("read")
            if main not in attachments:
                raise UserError(self.env._("Choose a readable attachment belonging to this bill."))
        return current, attachments, main

    def action_keep_together(self):
        move = self._locked_source()
        current, _attachments, main = self._current_choices(move)
        if self.has_separate_files:
            raise UserError(self.env._("Use Create draft bill for the files marked Another invoice."))
        self._confirm_source(move, main, current, dict(move.usl_mail_grouping or {}))
        return {"type": "ir.actions.act_window_close"}

    def _confirm_source(self, move, main, fingerprint, metadata):
        if main and main != move.message_main_attachment_id:
            move._message_set_main_attachment_id(main, force=True, filter_xml=False)
        elif self.line_ids and not main and move.message_main_attachment_id:
            move.message_main_attachment_id = False
        metadata["confirmed_fingerprint"] = fingerprint
        move.with_context(skip_is_manually_modified=True).write({
            "usl_mail_grouping": metadata, "usl_mail_grouping_needs_review": False,
            "usl_mail_grouping_reviewed_by": self.env.user.id,
            "usl_mail_grouping_reviewed_at": fields.Datetime.now(),
        })

    def _draft_action(self, draft):
        if not draft.exists() or not draft.has_access("read"):
            raise UserError(self.env._("The draft created from these files is no longer available. Ask a colleague to check its history before creating another bill."))
        return {"name": self.env._("Bill from selected files"), "type": "ir.actions.act_window",
                "res_model": "account.move", "res_id": draft.id, "view_mode": "form",
                "target": "current"}

    def action_create_draft_bill(self):
        move = self._locked_source()
        if self.result_move_id:
            return self._draft_action(self.result_move_id)
        current, _attachments, main = self._current_choices(move)
        if move.move_type not in {"in_invoice", "in_refund"}:
            raise UserError(self.env._("Create a draft bill from a vendor bill or credit note."))
        selected = self.line_ids.filtered(lambda line: line.use == "separate").mapped("attachment_id")
        if not selected:
            raise UserError(self.env._("Choose the files that belong to another invoice."))
        selected.check_access("read")
        signature = [(attachment.id, hashlib.sha256(attachment.raw.content).hexdigest()) for attachment in selected.sorted("id")]
        key = hashlib.sha256(json.dumps(signature, separators=(",", ":")).encode()).hexdigest()
        metadata = dict(move.usl_mail_grouping or {})
        receipts = list(metadata.get("manual_separations", []))
        for receipt in receipts:
            if receipt["key"] == key:
                draft = self.env["account.move"].browse(receipt["move_id"])
                action = self._draft_action(draft)
                self.result_move_id = draft
                return action
        # The native upload entry point may auto-post, and its decoder commits.
        # This review deliberately creates an atomic, empty native draft instead.
        clean_context = {key: value for key, value in self.env.context.items() if not key.startswith("default_")}
        draft = self.env["account.move"].with_context(clean_context).with_company(move.company_id).create({
            "move_type": move.move_type, "company_id": move.company_id.id,
            "journal_id": move.journal_id.id, "auto_post": "no",
        })
        copies = self.env["ir.attachment"]
        origins = {}
        for attachment in selected:
            copied = attachment.copy({"res_model": "account.move", "res_id": draft.id,
                                      "res_field": False, "company_id": move.company_id.id})
            copies |= copied
            origins[copied.id] = attachment.id
        main_copy = copies.filtered(lambda attachment: attachment.mimetype == "application/pdf")[:1] or copies[:1]
        draft._message_set_main_attachment_id(main_copy, force=True, filter_xml=False)
        original_files = {file["id"]: file for file in metadata.get("files", [])}
        draft_metadata = {
            "version": metadata.get("version", 1), "source_move_id": move.id,
            "source_message_id": metadata.get("source_message_id"),
            "files": [dict(original_files.get(origins[copy.id], {}), id=copy.id,
                           origin_id=origins[copy.id], name=copy.name,
                           sha256=hashlib.sha256(copy.raw.content).hexdigest(),
                           role=original_files.get(origins[copy.id], {}).get("role", "unknown"),
                           reason="manual_separation") for copy in copies],
        }
        draft.with_context(skip_is_manually_modified=True).write({
            "usl_mail_grouping": draft_metadata,
            "usl_mail_grouping_needs_review": True,
            "usl_mail_grouping_move_ids": [Command.set((move | move.usl_mail_grouping_move_ids).ids)],
        })
        draft.with_context(disable_attachment_import=True).message_post(
            body=Markup("%s %s<br/>%s") % (
                self.env._("Files copied from"), move._get_html_link(),
                self.env._("The originals remain on the source bill. Complete and review this draft's invoice details before posting.")),
            attachment_ids=copies.ids, message_type="notification", subtype_xmlid="mail.mt_note",
        )
        receipts.append({"key": key, "move_id": draft.id, "source_attachment_ids": selected.ids,
                         "source_fingerprint": current, "created_by": self.env.user.id,
                         "created_at": fields.Datetime.to_string(fields.Datetime.now())})
        metadata["manual_separations"] = receipts
        move.with_context(skip_is_manually_modified=True).write({"usl_mail_grouping_move_ids": [Command.link(draft.id)]})
        self._confirm_source(move, main, current, metadata)
        self.result_move_id = draft
        return self._draft_action(draft)


class PurchaseMailGroupingReviewLine(models.TransientModel):
    _name = "usl.purchase.mail.grouping.review.line"
    _description = "Purchase Mail Document Choice"
    _order = "id"

    review_id = fields.Many2one("usl.purchase.mail.grouping.review", required=True, ondelete="cascade")
    attachment_id = fields.Many2one("ir.attachment", string="Source attachment", required=True, readonly=True)
    name = fields.Char(related="attachment_id.name", readonly=True, compute_sudo=False)
    raw = fields.Binary(related="attachment_id.raw", string="Document", readonly=True, compute_sudo=False)
    use = fields.Selection([("supporting", "Supporting file"), ("main", "Main document"),
                            ("separate", "Another invoice")], string="Use as", required=True, default="supporting")

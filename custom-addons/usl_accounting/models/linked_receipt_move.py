"""Vendor bills reuse the existing linked-PDF discovery, worker and recovery."""
from odoo import _, fields, models
from odoo.exceptions import UserError

from .linked_receipt_expense import _compute_receipt_status


class AccountMove(models.Model):
    _inherit = "account.move"

    linked_receipt_state = fields.Selection(
        selection=[
            ("selection_required", "Selection required"),
            ("queued", "Queued"),
            ("running", "Running"),
            ("retrying", "Retrying"),
            ("succeeded", "Succeeded"),
            ("needs_attention", "Needs attention"),
            ("superseded", "Superseded"),
            ("dismissed", "Dismissed"),
        ],
        compute="_compute_linked_receipt_status",
        compute_sudo=True,
    )
    linked_receipt_message = fields.Char(compute="_compute_linked_receipt_status", compute_sudo=True)
    linked_receipt_queue_stale = fields.Boolean(compute="_compute_linked_receipt_status", compute_sudo=True)
    linked_receipt_can_manage = fields.Boolean(compute="_compute_linked_receipt_can_manage")
    linked_receipt_can_open_website = fields.Boolean(
        compute="_compute_linked_receipt_can_manage",
    )
    linked_receipt_authentication_required = fields.Boolean(
        compute="_compute_linked_receipt_status",
        compute_sudo=True,
    )
    linked_receipt_suggested_label = fields.Char(
        compute="_compute_linked_receipt_status",
        compute_sudo=True,
    )
    linked_receipt_has_alternatives = fields.Boolean(
        compute="_compute_linked_receipt_status",
        compute_sudo=True,
    )

    def _compute_linked_receipt_status(self):
        _compute_receipt_status(self, "move_id")
        for move in self:
            if move.linked_receipt_state == "superseded" and not move.message_main_attachment_id:
                move.linked_receipt_message = _("The bill or source email changed after this download was requested. Check the latest supplier email and attach its original PDF.")

    def _compute_linked_receipt_can_manage(self):
        for move in self:
            permitted = bool(
                move.move_type in ("in_invoice", "in_refund", "in_receipt")
                and move.company_id in move.env.companies
                and move.has_access("write")
            )
            move.linked_receipt_can_manage = permitted
            move.linked_receipt_can_open_website = permitted

    def _latest_linked_receipt(self):
        self.ensure_one()
        self.check_access("read")
        retrieval = self.env["usl.mail.pdf.retrieval"].search(
            [("move_id", "=", self.id)], order="id desc", limit=1,
        )
        if not retrieval:
            raise UserError(_("This bill has no linked receipt to manage."))
        return retrieval

    def action_scan_existing_receipt_emails(self):
        self.check_access("read")
        self.check_access("write")
        Retrieval = self.env["usl.mail.pdf.retrieval"]
        if not Retrieval._feature_enabled():
            raise UserError(_("Linked receipt retrieval is disabled in this environment."))
        found = 0
        for move in self.sorted("id"):
            if move.move_type not in ("in_invoice", "in_refund", "in_receipt") or move.state == "cancel":
                continue
            if move.company_id not in move.env.companies:
                raise UserError(_("Select this bill’s company before managing its linked receipt."))
            self.env.cr.execute("SELECT id FROM account_move WHERE id = %s FOR UPDATE", [move.id])
            move.invalidate_recordset()
            if move.message_main_attachment_id or Retrieval.sudo().search_count([("move_id", "=", move.id)]):
                continue
            if self.env["ir.attachment"].sudo().search_count([
                ("res_model", "=", "account.move"), ("res_id", "=", move.id),
                ("res_field", "=", False), "|", ("mimetype", "=", "application/pdf"),
                ("mimetype", "=like", "image/%"),
            ], limit=1):
                continue
            if any(Retrieval._message_has_receipt(message) for message in move.message_ids):
                continue
            for message in move.message_ids.filtered(lambda item: item.message_type == "email").sorted("id", reverse=True):
                if Retrieval.sudo()._discover_for_move(move, message):
                    found += 1
                    break
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"title": _("Receipt email scan complete"),
                       "message": _("Found receipt links for %(count)s vendor bills. Bills with files or existing requests were skipped.", count=found),
                       "type": "success" if found else "info", "sticky": False,
                       "next": {"type": "ir.actions.client", "tag": "reload"}},
        }

    def action_review_linked_receipt(self):
        self.ensure_one()
        retrieval = self._latest_linked_receipt()
        retrieval.with_user(self.env.user)._check_can_manage()
        Wizard = self.env["usl.mail.pdf.candidate.wizard"]
        wizard = Wizard.create(
            {
                "retrieval_id": retrieval.id,
                "candidate_ids": Wizard._candidate_commands(retrieval),
            },
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Choose the PDF receipt link"),
            "res_model": "usl.mail.pdf.candidate.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_accept_linked_receipt(self):
        self.ensure_one()
        self._latest_linked_receipt().with_user(self.env.user).action_accept_suggestion()
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_retry_linked_receipt(self):
        self.ensure_one()
        self._latest_linked_receipt().with_user(self.env.user).action_retry()
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_open_linked_receipt_website(self):
        self.ensure_one()
        retrieval = self._latest_linked_receipt().with_user(self.env.user)
        retrieval._check_can_open_handoff()
        return {
            "type": "ir.actions.client",
            "tag": "usl_linked_receipt_handoff",
            "params": {
                "retrieval_id": retrieval.id,
                "generation": retrieval.generation,
            },
        }

    def action_dismiss_linked_receipt(self):
        self.ensure_one()
        self._latest_linked_receipt().with_user(self.env.user).action_dismiss()
        return {"type": "ir.actions.client", "tag": "reload"}

    def _supersede_linked_receipts(self):
        for move in self:
            retrievals = self.env["usl.mail.pdf.retrieval"].sudo().search([
                ("move_id", "=", move.id),
                ("state", "in", ("selection_required", "queued", "running", "retrying", "needs_attention")),
            ])
            for retrieval in retrievals:
                retrieval.write({"state": "superseded", "generation": retrieval.generation + 1})

    def action_attach_linked_receipt(self, attachment_ids):
        """Finish the native upload widget on this exact bill, preserving files."""
        self.ensure_one()
        self.check_access("write")
        if self.move_type not in ("in_invoice", "in_refund", "in_receipt"):
            raise UserError(_("Choose a vendor bill for this downloaded document."))
        attachments = self.env["ir.attachment"].browse(attachment_ids).exists()
        attachments.check_access("read")
        if not attachments or any(
            item.res_model != self._name or item.res_id != self.id or item.res_field
            for item in attachments
        ):
            raise UserError(_("Upload the downloaded document on this bill before attaching it."))
        self.env.cr.execute("SELECT id FROM account_move WHERE id = %s FOR UPDATE", [self.id])
        self.invalidate_recordset(["message_main_attachment_id"])
        # This explicit upload can select the original while a separate mail
        # grouping review is pending. Keep any previously selected main file.
        self._message_set_main_attachment_id(
            attachments, force=not bool(self.message_main_attachment_id),
        )
        self._supersede_linked_receipts()
        return True

    def _message_post_after_hook(self, message, msg_values):
        result = super()._message_post_after_hook(message, msg_values)
        if len(self) != 1 or self.move_type not in ("in_invoice", "in_refund", "in_receipt"):
            return result
        Retrieval = self.env["usl.mail.pdf.retrieval"]
        if message.message_type == "email":
            Retrieval.sudo()._discover_for_move(self, message)
        elif not self.env.context.get("linked_receipt_attachment") and Retrieval._message_has_receipt(message):
            self._supersede_linked_receipts()
        return result

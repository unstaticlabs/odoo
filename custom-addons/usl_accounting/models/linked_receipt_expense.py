"""Expense-side linked receipt status, actions and historical email scans."""

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class HrExpense(models.Model):
    _inherit = "hr.expense"

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
        Retrieval = self.env["usl.mail.pdf.retrieval"].sudo()
        latest_by_expense = {
            expense.id: Retrieval.browse(latest_id)
            for expense, latest_id in Retrieval._read_group(
                [("expense_id", "in", self.ids)], ["expense_id"], ["id:max"],
            )
        }
        Wizard = self.env["usl.mail.pdf.candidate.wizard"]
        for expense in self:
            retrieval = latest_by_expense.get(expense.id, Retrieval)
            expense.linked_receipt_state = retrieval.state or False
            expense.linked_receipt_authentication_required = bool(
                retrieval.state == "needs_attention"
                and retrieval.failure_code == "authentication_required",
            )
            # Odoo already ranked the links when it discovered them; showing
            # the winner and one button spares the employee a picker that
            # usually holds a single row.
            features = retrieval.candidate_features or []
            suggestion = features[0] if isinstance(features, list) and features else None
            expense.linked_receipt_suggested_label = (
                Wizard._display_label(suggestion["label"]) if suggestion else False
            )
            expense.linked_receipt_has_alternatives = bool(
                isinstance(features, list) and len(features) > 1,
            )
            if not retrieval:
                expense.linked_receipt_message = False
            elif retrieval.state == "selection_required":
                if not suggestion:
                    expense.linked_receipt_message = _(
                        "Choose the receipt link so Odoo can learn this email format.",
                    )
                elif expense.linked_receipt_has_alternatives:
                    expense.linked_receipt_message = _(
                        "Odoo picked %(label)s on %(host)s. Download it, or choose"
                        " another link from this email.",
                        label=expense.linked_receipt_suggested_label,
                        host=suggestion["hostname"],
                    )
                else:
                    expense.linked_receipt_message = _(
                        "Odoo picked %(label)s on %(host)s, the only receipt link"
                        " in this email.",
                        label=expense.linked_receipt_suggested_label,
                        host=suggestion["hostname"],
                    )
            elif retrieval.state in ("queued", "running"):
                expense.linked_receipt_message = _("Odoo is downloading the linked PDF receipt.")
            elif retrieval.state == "retrying":
                expense.linked_receipt_message = _("The receipt download will retry automatically.")
            elif retrieval.state == "needs_attention":
                if retrieval.failure_code == "authentication_required":
                    expense.linked_receipt_message = _(
                        "Sign in on the receipt website, download the PDF, then attach it here. Your credentials stay with the provider.",
                    )
                else:
                    expense.linked_receipt_message = retrieval.failure_message or _("The linked receipt needs attention.")
            else:
                expense.linked_receipt_message = False

    def _compute_linked_receipt_can_manage(self):
        is_manager = self.env.user.has_group("account.group_account_manager")
        for expense in self:
            expense.linked_receipt_can_manage = bool(
                expense.employee_id.user_id == self.env.user or is_manager,
            )
            expense.linked_receipt_can_open_website = bool(
                expense.employee_id.user_id == self.env.user,
            )

    def _latest_linked_receipt(self):
        self.ensure_one()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", self.id)], order="id desc", limit=1,
        )
        if not retrieval:
            raise UserError(_("This expense has no linked receipt to manage."))
        return retrieval

    def action_scan_existing_receipt_emails(self):
        """Discover historical links without reprocessing existing retrievals."""
        self.check_access("read")
        Retrieval = self.env["usl.mail.pdf.retrieval"]
        eligible = self.filtered(Retrieval._expense_is_eligible)
        eligible.check_access("write")
        manager = self.env.user.has_group("account.group_account_manager")
        if any(expense.employee_id.user_id != self.env.user for expense in self) and not manager:
            raise AccessError(_("Only the expense owner or an Accounting Manager can manage its linked receipt."))
        if not Retrieval._feature_enabled():
            raise UserError(_("Linked receipt retrieval is disabled in this environment."))
        found = 0
        for expense in eligible.sorted("id"):
            # Serialize repeat clicks before discovering or enqueueing any work.
            self.env.cr.execute(
                "SELECT id FROM hr_expense WHERE id = %s FOR UPDATE", [expense.id],
            )
            expense.invalidate_recordset()
            if not Retrieval._expense_is_eligible(expense):
                continue
            scoped = Retrieval.sudo().with_company(expense.company_id)
            if scoped.search_count([("expense_id", "=", expense.id)]):
                continue
            attachments = self.env["ir.attachment"].sudo().search_count([
                ("res_model", "=", "hr.expense"), ("res_id", "=", expense.id),
                "|", ("mimetype", "=", "application/pdf"), ("mimetype", "=like", "image/%"),
            ])
            messages = expense.message_ids
            if expense.message_main_attachment_id or attachments or any(
                scoped._message_has_receipt(message) for message in messages
            ):
                continue
            emails = messages.filtered(lambda item: item.message_type == "email")
            for message in emails.sorted("id", reverse=True):
                if scoped._discover_for_expense(expense, message):
                    found += 1
                    break
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Receipt email scan complete"),
                "message": _(
                    "Found receipt links for %(count)s expenses. Existing requests and expenses with receipts were skipped.",
                    count=found,
                ),
                "type": "success" if found else "info",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
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
            "type": "ir.actions.act_url",
            "url": f"/usl/expenses/linked-receipt/{retrieval.id}/open",
            "target": "new",
        }

    def action_dismiss_linked_receipt(self):
        self.ensure_one()
        self._latest_linked_receipt().with_user(self.env.user).action_dismiss()
        return {"type": "ir.actions.client", "tag": "reload"}

    def attach_document(self, **kwargs):
        result = super().attach_document(**kwargs)
        self.env["usl.mail.pdf.retrieval"]._supersede_for_expense(self)
        return result

    def _message_post_after_hook(self, message, msg_values):
        result = super()._message_post_after_hook(message, msg_values)
        if len(self) != 1:
            return result
        Retrieval = self.env["usl.mail.pdf.retrieval"]
        if message.message_type == "email":
            Retrieval.sudo()._discover_for_expense(self, message)
        elif (
            not self.env.context.get("linked_receipt_attachment")
            and Retrieval._message_has_receipt(message)
        ):
            Retrieval._supersede_for_expense(self)
        return result

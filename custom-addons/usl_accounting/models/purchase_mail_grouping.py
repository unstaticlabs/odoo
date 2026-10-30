"""Content evidence inside the native purchase-alias attachment lifecycle."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date

from odoo import _, Command, api, fields, models
from odoo.exceptions import UserError

from ..tools import purchase_mail_evidence as evidence

_CREATED = object()


@dataclass
class _GroupingPlan:
    source_id: int
    message_id: int
    extra_files: list
    groups: list = field(default_factory=list)
    prepared_ids: set = field(default_factory=set)


class AccountMove(models.Model):
    _inherit = "account.move"

    usl_mail_grouping = fields.Json(string="Mail attachment grouping", copy=False, readonly=True)
    usl_mail_grouping_needs_review = fields.Boolean(string="Attachment grouping to review", copy=False, readonly=True, index=True)
    usl_mail_grouping_summary = fields.Text(string="Original attachment evidence", compute="_compute_mail_grouping_summary")
    usl_mail_grouping_reviewed_by = fields.Many2one("res.users", string="Grouping reviewed by", copy=False, readonly=True)
    usl_mail_grouping_reviewed_at = fields.Datetime(string="Grouping reviewed on", copy=False, readonly=True)

    usl_mail_grouping_move_ids = fields.Many2many(
        "account.move", "usl_mail_grouping_move_rel", "move_id", "related_move_id",
        string="Bills from this email", copy=False, readonly=True,
    )

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        move = super().message_new(msg_dict, custom_values)
        if move.journal_id.type == "purchase" and move.move_type in {"in_invoice", "in_refund"}:
            return move.with_context(usl_purchase_mail_created=_CREATED)
        return move

    def _message_post_after_hook(self, new_message, message_values):
        if (
            len(self) == 1 and self.env.context.get("usl_purchase_mail_created") is _CREATED
            and new_message.message_type == "email" and new_message.attachment_ids
            and not self.env.context.get("disable_attachment_import")
        ):
            inline_ids = {int(value) for value in re.findall(r"/(?:web/image|web/content)/(\d+)", str(new_message.body or ""))}
            extras = [file for file in self._to_files_data(new_message.attachment_ids)
                      if file["attachment"].id not in inline_ids and file["mimetype"] != "message/rfc822"]
            plan = _GroupingPlan(self.id, new_message.id, extras)
            scoped = self.with_context(usl_purchase_mail_plan=plan)
            return super(AccountMove, scoped)._message_post_after_hook(new_message, message_values)
        return super()._message_post_after_hook(new_message, message_values)

    def _mail_structured_evidence(self, file_data):
        """Use native pure schema helpers; never call an importer/decoder here."""
        raw = file_data["raw"]
        tree = file_data["xml_tree"]
        model = file_data["import_file_type"]
        if tree is None or not model or not model.startswith("account.edi.xml.") or model not in self.env:
            return evidence.unknown(raw, "unsupported_content")
        try:
            parser = self.env[model]
            kind, _sign = parser._get_import_document_amount_sign(tree)
            cii = model == "account.edi.xml.cii"
            partner = parser._import_retrieve_partner_vals(tree, "SellerTradeParty" if cii else "AccountingSupplier")
            address = partner.get("postal_address") or {}
            reference_nodes = tree.findall(".//{*}ExchangedDocument/{*}ID" if cii else "{*}ID")
            reference = reference_nodes[0].text if len(reference_nodes) == 1 else None
            currency = tree.findtext(".//{*}InvoiceCurrencyCode" if cii else "{*}DocumentCurrencyCode")
            amount = evidence.decimal_amount(tree.findtext(".//{*}SpecifiedTradeSettlementHeaderMonetarySummation/{*}GrandTotalAmount" if cii else ".//{*}LegalMonetaryTotal/{*}TaxInclusiveAmount"))
            issued_nodes = tree.findall(".//{*}ExchangedDocument/{*}IssueDateTime/{*}DateTimeString" if cii else "{*}IssueDate")
            issued = issued_nodes[0].text if len(issued_nodes) == 1 else None
            if cii and issued_nodes and issued_nodes[0].get("format") != "102":
                return evidence.unknown(raw, "unsupported_structured_date")
            if cii and issued and len(issued) == 8:
                issued = f"{issued[:4]}-{issued[4:6]}-{issued[6:]}"
            date.fromisoformat(issued or "")
            issuer = evidence.normalized(partner.get("name"))
            location = evidence.normalized(", ".join(filter(None, (address.get("street"), " ".join(filter(None, (address.get("zip"), address.get("city"))))))))
            legal_id = evidence.normalized(partner.get("vat"))
            if kind not in {"invoice", "refund"} or not reference or not issuer or not (legal_id or (address.get("street") and address.get("city"))) or not currency or amount is None or amount == 0:
                return evidence.unknown(raw, "incomplete_structured_identity")
            return {
                "role": "invoice", "reason": "native_structured_identity",
                "sha256": hashlib.sha256(raw).hexdigest(), "kind": kind,
                "reference": evidence.normalized(reference), "issuer": issuer,
                "address": location, "legal_id": legal_id, "currency": currency.upper(),
                "amount": format(abs(amount).normalize(), "f"), "date": issued,
            }
        except (AttributeError, KeyError, TypeError, ValueError):
            return evidence.unknown(raw, "structured_identity_uncertain")

    def _group_files_data_into_groups_of_mixed_types(self, files_data):
        plan = self.env.context.get("usl_purchase_mail_plan")
        if not isinstance(plan, _GroupingPlan) or plan.groups or self.id != plan.source_id:
            return super()._group_files_data_into_groups_of_mixed_types(files_data)
        present = {file["attachment"].id for file in files_data if file["attachment"]}
        files_data = list(files_data) + [file for file in plan.extra_files if file["attachment"].id not in present]
        bounded = len(files_data) <= evidence.MAX_FILES and sum(len(file["raw"]) for file in files_data) <= evidence.MAX_MESSAGE_BYTES
        by_origin = {}
        for file in files_data:
            by_origin.setdefault(file["origin_attachment"].id, []).append(file)
        families = []
        for origin_files in by_origin.values():
            observations = []
            for file in origin_files:
                raw = file["raw"]
                if not bounded or len(raw) > evidence.MAX_FILE_BYTES:
                    observed = evidence.unknown(raw, "extraction_budget")
                elif raw.startswith(b"%PDF-"):
                    observed = evidence.pdf_evidence(raw)
                else:
                    observed = self._mail_structured_evidence(file)
                observations.append((file, observed))
            invoices = [item for item in observations if item[1]["role"] == "invoice"]
            anchor = invoices[0][1] if invoices else observations[0][1]
            conflicts = any(not evidence.same_invoice(anchor, item[1]) or (anchor["amount"], anchor["currency"], anchor["date"]) != (item[1]["amount"], item[1]["currency"], item[1]["date"]) for item in invoices[1:])
            if invoices:
                conflicts |= any(observed["role"] == "receipt" and not evidence.receipt_matches(observed, anchor) for _file, observed in observations)
            if conflicts:
                anchor = evidence.unknown(origin_files[0]["raw"], "conflicting_embedded_invoices")
            family = {"files": origin_files, "evidence": anchor, "observations": observations}
            if anchor["role"] == "unknown":
                family["uncertain"] = anchor["reason"]
            families.append(family)
        plan.groups = evidence.group_families(families)
        return [[file for family in group for file in family["files"]] for group in plan.groups]

    def _fix_attachments_on_record_from_files_data(self, valid_files_data, extra_files_data):
        plan = self.env.context.get("usl_purchase_mail_plan")
        if not isinstance(plan, _GroupingPlan) or self.id in plan.prepared_ids:
            return super()._fix_attachments_on_record_from_files_data(valid_files_data, extra_files_data)
        group = next((group for group in plan.groups if any(file is candidate for family in group for file in family["files"] for candidate in valid_files_data)), [])
        if not group:
            return super()._fix_attachments_on_record_from_files_data(valid_files_data, extra_files_data)
        all_selected = {file["attachment"].id for candidate in plan.groups for family in candidate for file in family["files"] if file["attachment"]}
        result = super()._fix_attachments_on_record_from_files_data(valid_files_data, [file for file in extra_files_data if file["attachment"].id not in all_selected])
        plan.prepared_ids.add(self.id)
        needs_review = any(family.get("uncertain") for family in group)
        metadata = {
            "version": evidence.VERSION, "source_message_id": plan.message_id,
            "files": [{"id": file["attachment"].id if file["attachment"] else False,
                       "origin_id": file["origin_attachment"].id, "name": file["name"],
                       "sha256": observed["sha256"], "role": observed["role"],
                       "reason": family.get("uncertain") or observed["reason"]}
                      for family in group for file, observed in family["observations"]],
        }
        self.write({"usl_mail_grouping": metadata, "usl_mail_grouping_needs_review": needs_review})
        invoice_files = [file for family in group if family["evidence"]["role"] == "invoice" and not family.get("uncertain") for file, observed in family["observations"] if observed["role"] == "invoice" and file["raw"].startswith(b"%PDF-") and file["attachment"]]
        if invoice_files:
            self._message_set_main_attachment_id(self._from_files_data(invoice_files[:1]), force=True, filter_xml=False)
        elif needs_review or any(family["evidence"]["role"] == "invoice" for family in group):
            # A receipt/logo must not suppress the native XML invoice PDF.
            # The native decoder may subsequently generate its own main PDF.
            self.message_main_attachment_id = False
        if len(plan.prepared_ids) == len(plan.groups):
            self.browse(sorted(plan.prepared_ids)).write({
                "usl_mail_grouping_move_ids": [Command.set(sorted(plan.prepared_ids))],
            })
        return result

    def _message_set_main_attachment_id(self, attachments, force=False, filter_xml=True):
        if len(self) == 1 and self.usl_mail_grouping_needs_review and not force and not self.message_main_attachment_id:
            return
        return super()._message_set_main_attachment_id(attachments, force=force, filter_xml=filter_xml)

    @api.depends("usl_mail_grouping", "usl_mail_grouping_needs_review")
    def _compute_mail_grouping_summary(self):
        reasons = {
            "conflicting_invoice_values": self.env._("Conflicting invoice details"),
            "conflicting_embedded_invoices": self.env._("Conflicting embedded invoice identities"),
            "receipt_match_uncertain": self.env._("Payment document could not be matched to one invoice"),
            "extraction_budget": self.env._("Extraction limit reached"),
            "file_byte_limit": self.env._("Extraction limit reached"),
            "text_limit": self.env._("Text extraction limit reached"),
            "encrypted_or_page_limit": self.env._("Encrypted document or page limit reached"),
        }
        for move in self:
            files = (move.usl_mail_grouping or {}).get("files", [])
            lines = []
            for file in files:
                status = reasons.get(file["reason"])
                if not status:
                    status = (self.env._("Invoice evidence") if file["role"] == "invoice" else
                              self.env._("Payment evidence") if file["role"] == "receipt" else
                              self.env._("Document role could not be established"))
                lines.append(self.env._("%(filename)s — %(status)s", filename=file["name"], status=status))
            move.usl_mail_grouping_summary = "\n".join(lines)

    def _mail_grouping_fingerprint(self):
        self.ensure_one()
        self.check_access("read")
        attachments = self.env["ir.attachment"].search([
            ("res_model", "=", self._name), ("res_id", "=", self.id),
            "|", ("res_field", "=", False), ("res_field", "=", "ubl_cii_xml_file"),
        ])
        payload = [(attachment.id, hashlib.sha256(attachment.raw.content).hexdigest()) for attachment in attachments]
        return hashlib.sha256(json.dumps(sorted(payload)).encode()).hexdigest(), attachments

    def action_review_mail_grouping(self):
        self.ensure_one()
        self.check_access("write")
        fingerprint, attachments = self._mail_grouping_fingerprint()
        wizard = self.env["usl.purchase.mail.grouping.review"].create({
            "move_id": self.id, "fingerprint": fingerprint,
            "attachment_ids": [Command.set(attachments.ids)],
            "main_attachment_id": self.message_main_attachment_id.id,
            "line_ids": [Command.create({"attachment_id": attachment.id,
                                         "use": "main" if attachment == self.message_main_attachment_id else "supporting"})
                         for attachment in attachments],
        })
        return {"name": _("Review attachments"), "type": "ir.actions.act_window", "res_model": wizard._name,
                "res_id": wizard.id, "view_mode": "form", "target": "new"}

    @api.readonly
    def get_purchase_attachment_context(self, offset=0, limit=50):
        """Relevant bill evidence and native review actions, without file contents.

        Metadata pagination uses current record rules; opening review obtains and
        later rechecks the exact byte fingerprint separately. Generic clients use
        the same transient choices and native methods as the form.
        """
        self.ensure_one()
        self.check_access("read")
        if self.move_type not in {"in_invoice", "in_refund"} or not self.usl_mail_grouping:
            return {}
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise UserError(self.env._("Use an offset of zero or more and a limit between 1 and 100."))
        domain = [("res_model", "=", self._name), ("res_id", "=", self.id),
                  "|", ("res_field", "=", False), ("res_field", "=", "ubl_cii_xml_file")]
        attachments = self.env["ir.attachment"]
        count = attachments.search_count(domain)
        page = attachments.search(domain, order="id", offset=offset, limit=limit)
        metadata = self.usl_mail_grouping
        evidence_by_id = {file["id"]: file for file in metadata.get("files", [])}
        related_domain = [("id", "in", self.usl_mail_grouping_move_ids.ids)]
        related_model = self.env["account.move"]
        related_count = related_model.search_count(related_domain)
        related = related_model.search(related_domain, order="id", limit=100)
        readable_ids = set(related.ids)
        documents = []
        for attachment in page:
            evidence = evidence_by_id.get(attachment.id, {})
            name = attachment.name or ""
            reason = evidence.get("reason", "not_classified")
            reason = reason if isinstance(reason, str) else "not_classified"
            mimetype = attachment.mimetype or ""
            role = evidence.get("role", "unknown")
            document = {
                "id": attachment.id, "name": name[:512], "mimetype": mimetype[:128],
                "role": role if role in {"invoice", "receipt", "unknown"} else "unknown", "reason": reason[:1024],
                "is_main": attachment == self.message_main_attachment_id,
                "separated_bill_ids": [receipt["move_id"] for receipt in metadata.get("manual_separations", [])
                                       if attachment.id in receipt["source_attachment_ids"] and receipt["move_id"] in readable_ids],
            }
            for field, value, bound in (("name", name, 512), ("reason", reason, 1024), ("mimetype", mimetype, 128)):
                if len(value) > bound:
                    document[f"{field}_status"] = "truncated"
            documents.append(document)
        actions = {}
        effective_access = self._api_doc_access()
        if self.has_access("write") and effective_access.get("write"):
            actions["review"] = {"model": self._name, "method": "action_review_mail_grouping", "ids": [self.id], "kwargs": {},
                                 "description": self.env._("Open the current files and choose how each is used.")}
            actions["confirm"] = {"model": "usl.purchase.mail.grouping.review", "method": "action_keep_together",
                                  "description": self.env._("Open review, set each line's use to supporting or main, then call this method with ids containing the opened review's res_id. This does not post the bill or record a payment.")}
            if effective_access.get("create") and self.env["account.move"].has_access("create"):
                actions["separate"] = {"model": "usl.purchase.mail.grouping.review", "method": "action_create_draft_bill",
                                       "description": self.env._("Open review and mark selected lines as separate. Call this method with ids containing the opened review's res_id to create one draft from copies. Complete its invoice details. Originals remain here; retrying opens the previously created bill.")}
        next_offset = offset + len(page) if offset + len(page) < count else False
        return {
            "version": 1, "bill_id": self.id, "company_id": self.company_id.id, "state": self.state,
            "needs_review": self.usl_mail_grouping_needs_review,
            "main_attachment_id": self.message_main_attachment_id.id,
            "documents": documents, "related_bill_ids": related.ids,
            "related_bill_count": related_count, "related_bills_truncated": related_count > len(related),
            "reviewed_by": self.usl_mail_grouping_reviewed_by.id,
            "reviewed_at": fields.Datetime.to_string(self.usl_mail_grouping_reviewed_at) or False,
            "offset": offset, "limit": limit, "count": count,
            "complete": next_offset is False, "truncated": next_offset is not False, "next_offset": next_offset,
            "review_lines": {"model": "usl.purchase.mail.grouping.review.line", "relation": "line_ids",
                             "attachment_field": "attachment_id", "choice_field": "use",
                             "choices": ["supporting", "main", "separate"]},
            "actions": actions,
        }

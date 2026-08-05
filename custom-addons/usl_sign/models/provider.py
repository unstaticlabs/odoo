import hashlib
import re
from base64 import b64decode, b64encode
from datetime import timezone
from io import BytesIO

from odoo import fields, models
from odoo.exceptions import ValidationError
from odoo.tools.pdf import PdfReader

from ..services import ProviderError, get_provider
from .request import ACTIVE_REQUEST_STATES, TERMINAL_REQUEST_STATES


ASSURANCE_TO_YOUSIGN = {
    "standard": "electronic_signature",
    "verified": "advanced_electronic_signature",
    "qualified": "qualified_electronic_signature",
}

PROVIDER_REQUEST_STATES = {
    "draft": "ready",
    "approval": "sent",
    "ongoing": "sent",
    "paused": "action_required",
    "done": "completed",
    "declined": "declined",
    "expired": "expired",
    "canceled": "cancelled",
}

PROVIDER_SIGNER_STATES = {
    "initiated": "draft",
    "notified": "notified",
    "signed": "signed",
    "declined": "declined",
    "error": "error",
}


class SignRequestProvider(models.Model):
    _inherit = "sign.oca.request"

    def _provider_client(self):
        self.ensure_one()
        configuration = self.company_id._sign_provider_configuration()
        return get_provider(self.provider_code, configuration)

    def _provider_send(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id FROM sign_oca_request WHERE id = %s FOR UPDATE", [self.id]
        )
        client = self._provider_client()
        try:
            snapshot = self._recover_or_create_provider_request(client)
            self._recover_or_upload_document(client, snapshot)
            snapshot = client.get_request(self.provider_transaction_id)
            self._recover_or_create_signers(client, snapshot)
            self._recover_or_create_fields(client)
            activation = client.activate(self.provider_transaction_id)
            self._apply_provider_snapshot(activation)
            self.signer_ids.filtered(
                lambda signer: signer.state == "notified"
            )._send_signer_invitation()
            self.with_context(usl_sign_transition=True).write(
                {
                    "state": "sent",
                    "sent_at": fields.Datetime.now(),
                    "last_error": False,
                    "recovery_action": False,
                    "provider_status": activation.get("status", "ongoing"),
                }
            )
            self._post_business_event(
                self.env._("Signature request sent: %(name)s", name=self.name)
            )
        except ProviderError as error:
            recovery = (
                self.env._("Reconcile provider status before sending again.")
                if error.uncertain
                else self.env._("Correct the configuration, then send again.")
            )
            self._set_action_required(str(error), recovery)

    def _recover_or_create_provider_request(self, client):
        self.ensure_one()
        snapshot = None
        if self.provider_transaction_id:
            snapshot = client.get_request(self.provider_transaction_id)
        else:
            snapshot = client.recover_request(self.idempotency_key)
            if not snapshot:
                expiration = self.expires_at
                if expiration and not expiration.tzinfo:
                    expiration = expiration.replace(tzinfo=timezone.utc)
                snapshot = client.create_request(
                    {
                        "name": self.name,
                        "external_id": self.idempotency_key,
                        "ordered_signers": self.signing_order,
                        "expiration_date": expiration.isoformat()
                        if expiration
                        else None,
                        # Odoo owns branded invitations and reminder
                        # idempotency; provider delivery remains disabled.
                        "reminder_settings": None,
                    }
                )
            self.with_context(usl_sign_transition=True).write(
                {
                    "provider_transaction_id": snapshot["id"],
                    "provider_environment": self.company_id.sign_yousign_environment,
                    "provider_status": snapshot.get("status", "draft"),
                }
            )
        return snapshot

    def _initials_configuration(self):
        self.ensure_one()
        initials = [
            item
            for item in (self.frozen_layout or {}).values()
            if self.env["sign.oca.field"].browse(item.get("field_id")).usl_kind
            == "initials"
        ]
        if not initials:
            return None
        first = initials[0]
        horizontal = (
            "left"
            if first.get("position_x", 0) < 33
            else "right"
            if first.get("position_x", 0) > 66
            else "center"
        )
        vertical = "top" if first.get("position_y", 0) < 50 else "bottom"
        page_height = self._pdf_page_metrics()[0][1]
        y_percentage = (
            first.get("position_y", 0)
            if vertical == "top"
            else 100 - first.get("position_y", 0)
        )
        y_position = min(
            32767,
            max(
                0,
                round(page_height * y_percentage / 100),
            ),
        )
        return {"alignment": f"{vertical}-{horizontal}", "y": y_position}

    def _recover_or_upload_document(self, client, snapshot):
        self.ensure_one()
        documents = snapshot.get("documents") or []
        if self.provider_document_id:
            return
        if len(documents) > 1:
            raise ProviderError(
                "The provider transaction contains unexpected additional documents."
            )
        document = documents[0] if documents else client.upload_document(
            self.provider_transaction_id,
            self.original_filename or f"{self.name}.pdf",
            b64decode(self.original_data),
            initials=self._initials_configuration(),
        )
        self.with_context(usl_sign_transition=True).write(
            {"provider_document_id": document["id"]}
        )

    def _partner_info(self, partner):
        name_parts = (partner.name or "Signer").strip().rsplit(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) == 2 else "-"
        info = {
            "first_name": first_name[:64],
            "last_name": last_name[:64],
            "email": partner.email,
            "locale": (partner.lang or "fr_FR").split("_")[0],
        }
        if self.authentication_method in {
            "otp_sms",
            "identity_verification",
            "qualified_identity",
        }:
            phone = re.sub(r"[^\d+]", "", partner.mobile or partner.phone or "")
            if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
                raise ValidationError(
                    self.env._(
                        "Signer %(signer)s needs a mobile number in international format, for example +33612345678.",
                        signer=partner.display_name,
                    )
                )
            info["phone_number"] = phone
        return info

    def _signer_payload(self, signer, insert_after_id=False):
        payload = {
            "info": self._partner_info(signer.partner_id),
            "signature_level": ASSURANCE_TO_YOUSIGN[self.requested_assurance],
            "delivery_mode": "none",
            "redirect_urls": {
                "success": f"{self.get_base_url()}/sign/result/success",
                "error": f"{self.get_base_url()}/sign/result/error",
                "declined": f"{self.get_base_url()}/sign/result/declined",
            },
        }
        if self.authentication_method in {"no_otp", "otp_email", "otp_sms"}:
            payload["signature_authentication_mode"] = self.authentication_method
        elif self.authentication_method == "identity_verification":
            payload.update(
                {
                    "signature_authentication_mode": "otp_sms",
                    "pre_identity_verification_required": True,
                }
            )
        if insert_after_id:
            payload["insert_after_id"] = insert_after_id
        return payload

    def _recover_or_create_signers(self, client, snapshot):
        self.ensure_one()
        provider_signers = snapshot.get("signers") or []
        by_email = {
            (row.get("info") or {}).get("email", "").casefold(): row
            for row in provider_signers
        }
        previous_id = False
        for signer in self.signer_ids.sorted(lambda row: (row.sequence, row.id)):
            row = None
            if signer.provider_signer_id:
                row = next(
                    (
                        item
                        for item in provider_signers
                        if item.get("id") == signer.provider_signer_id
                    ),
                    None,
                )
            if not row:
                row = by_email.get((signer.partner_id.email or "").casefold())
            if not row:
                row = client.add_signer(
                    self.provider_transaction_id,
                    self._signer_payload(
                        signer, previous_id if self.signing_order else False
                    ),
                )
            signer.write(
                {
                    "provider_signer_id": row["id"],
                    "state": PROVIDER_SIGNER_STATES.get(
                        row.get("status"), signer.state
                    ),
                    "authentication_method": self.authentication_method,
                }
            )
            signer._portal_ensure_token()
            previous_id = row["id"]

    def _pdf_page_metrics(self):
        reader = PdfReader(BytesIO(b64decode(self.original_data)))
        metrics = []
        for page in reader.pages:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            rotation = int(page.get("/Rotate", 0) or 0) % 360
            # OCA positions fields against PDF.js' displayed page. Yousign's
            # origin is also the visual top-left, so quarter-turn pages use
            # their displayed (swapped) dimensions.
            metrics.append((height, width) if rotation in {90, 270} else (width, height))
        return metrics

    def _field_payload(self, item, signer, page_metrics):
        page_number = int(item.get("page") or 1)
        if page_number < 1 or page_number > len(page_metrics):
            raise ValidationError(
                self.env._("A field is placed outside the source PDF page range.")
            )
        page_width, page_height = page_metrics[page_number - 1]
        x = round(page_width * float(item.get("position_x") or 0) / 100)
        y = round(page_height * float(item.get("position_y") or 0) / 100)
        width = round(page_width * float(item.get("width") or 0) / 100)
        height = round(page_height * float(item.get("height") or 0) / 100)
        kind = self.env["sign.oca.field"].browse(item["field_id"]).usl_kind
        common = {"page": page_number, "x": max(0, x), "y": max(0, y)}
        if kind not in {"company", "role"}:
            common["signer_id"] = signer.provider_signer_id
        if kind == "signature":
            width, height = max(85, width), max(37, height)
            common.update(
                {
                    "type": "signature",
                    "width": width,
                    "height": height,
                    "display": {"layout": "minimal"},
                }
            )
        elif kind == "text":
            common.update(
                {
                    "type": "text",
                    "width": max(24, width),
                    "height": max(15, height),
                    "max_length": max(1, min(32767, width // 6 or 120)),
                    "question": item.get("placeholder") or "",
                    "optional": not item.get("required", False),
                }
            )
        elif kind == "signer_name":
            common.update({"type": "signer_name", "name_format": "full_name"})
        elif kind == "date":
            common.update(
                {"type": "signature_date", "date_format": "dd/MM/yyyy"}
            )
        elif kind == "checkbox":
            common.update(
                {
                    "type": "checkbox",
                    "size": max(8, min(240, width or height or 24)),
                    "optional": not item.get("required", False),
                }
            )
        elif kind in {"company", "role"}:
            common.pop("signer_id", None)
            text = (
                signer.partner_id.commercial_company_name
                if kind == "company"
                else signer.role_id.name
            )
            common.update(
                {
                    "type": "read_only_text",
                    "text": text or "-",
                    "width": max(24, width),
                    "height": max(15, height),
                }
            )
        elif kind == "initials":
            return None
        else:
            raise ValidationError(
                self.env._("This document contains an unsupported signature field.")
            )
        if "width" in common:
            common["x"] = max(0, min(common["x"], round(page_width) - common["width"]))
        if "height" in common:
            common["y"] = max(0, min(common["y"], round(page_height) - common["height"]))
        return common

    @staticmethod
    def _field_identity(payload):
        return "|".join(
            str(payload.get(key, ""))
            for key in ("signer_id", "type", "page", "x", "y")
        )

    def _recover_or_create_fields(self, client):
        self.ensure_one()
        field_map = dict(self.provider_field_map or {})
        existing_fields = client.list_fields(
            self.provider_transaction_id, self.provider_document_id
        )
        existing_by_identity = {
            self._field_identity(row): row for row in (existing_fields or [])
        }
        page_metrics = self._pdf_page_metrics()
        signer_by_role = {row.role_id.id: row for row in self.signer_ids}
        for item_id, item in sorted(
            (self.frozen_layout or {}).items(), key=lambda row: int(row[0])
        ):
            signer = signer_by_role.get(int(item.get("role_id") or 0))
            if not signer:
                raise ValidationError(
                    self.env._("Every positioned field must belong to an assigned signer role.")
                )
            payload = self._field_payload(item, signer, page_metrics)
            if not payload or str(item_id) in field_map:
                continue
            existing = existing_by_identity.get(self._field_identity(payload))
            created = existing or client.add_field(
                self.provider_transaction_id, self.provider_document_id, payload
            )
            field_map[str(item_id)] = created["id"]
            self.with_context(usl_sign_transition=True).write(
                {"provider_field_map": field_map}
            )

    def _apply_provider_snapshot(self, snapshot):
        self.ensure_one()
        provider_status = snapshot.get("status") or self.provider_status
        target_state = PROVIDER_REQUEST_STATES.get(provider_status)
        vals = {
            "provider_status": provider_status,
            "last_reconciled_at": fields.Datetime.now(),
        }
        if target_state and self.state not in TERMINAL_REQUEST_STATES:
            vals["state"] = (
                "action_required" if target_state == "completed" else target_state
            )
        if target_state == "completed":
            vals.update(
                {
                    "evidence_status": "pending",
                    "last_error": self.env._(
                        "All signatures are complete; final evidence is being retrieved."
                    ),
                    "recovery_action": self.env._(
                        "Refresh provider status to retry evidence retrieval."
                    ),
                }
            )
        self.with_context(usl_sign_transition=True).write(vals)
        for row in snapshot.get("signers") or []:
            signer = self.signer_ids.filtered(
                lambda item: item.provider_signer_id == row.get("id")
            )[:1]
            if not signer:
                continue
            signer_vals = {
                "state": PROVIDER_SIGNER_STATES.get(
                    row.get("status"), signer.state
                ),
                "provider_signature_link": row.get("signature_link")
                or signer.provider_signature_link,
                "signature_link_expires_at": row.get(
                    "signature_link_expiration_date"
                )
                or signer.signature_link_expires_at,
            }
            if row.get("status") == "signed":
                signer_vals.update(
                    {
                        "signed_on": row.get("signed_at")
                        or signer.signed_on
                        or fields.Datetime.now(),
                        "achieved_assurance": self.requested_assurance,
                        "access_revoked": True,
                    }
                )
            signer.write(signer_vals)
        if target_state != "completed" and self.state not in TERMINAL_REQUEST_STATES:
            signed_count = len(
                self.signer_ids.filtered(lambda signer: signer.state == "signed")
            )
            viewed_count = len(
                self.signer_ids.filtered(lambda signer: signer.state == "viewed")
            )
            progress_state = (
                "partial" if signed_count else "viewed" if viewed_count else False
            )
            if progress_state:
                self.with_context(usl_sign_transition=True).write(
                    {"state": progress_state}
                )

    def _provider_reconcile(self, manual=False):
        for request in self:
            if not request.provider_transaction_id:
                if manual:
                    recovered = request._provider_client().recover_request(
                        request.idempotency_key
                    )
                    if not recovered:
                        raise ValidationError(
                            request.env._("No provider transaction exists for this request.")
                        )
                    request.with_context(usl_sign_transition=True).write(
                        {
                            "provider_transaction_id": recovered["id"],
                            "provider_environment": request.company_id.sign_yousign_environment,
                        }
                    )
                else:
                    continue
            try:
                snapshot = request._provider_client().get_request(
                    request.provider_transaction_id
                )
                request._apply_provider_snapshot(snapshot)
                if snapshot.get("status") == "done":
                    request._retrieve_provider_evidence()
                request.with_context(usl_sign_transition=True).write(
                    {"last_error": False, "recovery_action": False}
                )
            except ProviderError as error:
                request._set_action_required(
                    str(error),
                    request.env._("Retry reconciliation after checking provider availability."),
                )
        return True

    def _retrieve_provider_evidence(self):
        self.ensure_one()
        client = self._provider_client()
        evidence_model = self.env["usl.sign.evidence"]
        if not evidence_model.search_count(
            [
                ("request_id", "=", self.id),
                ("kind", "=", "signed"),
                ("provider_reference", "=", self.provider_document_id),
            ],
            limit=1,
        ):
            content = client.download_document(
                self.provider_transaction_id, self.provider_document_id
            )
            try:
                PdfReader(BytesIO(content))
                validation = "valid"
            except Exception:
                validation = "invalid"
            encoded = b64encode(content)
            evidence_model.create(
                {
                    "request_id": self.id,
                    "kind": "signed",
                    "name": f"{self.name} - signed.pdf",
                    "data": encoded,
                    "mimetype": "application/pdf",
                    "provider_reference": self.provider_document_id,
                    "retrieved_at": fields.Datetime.now(),
                    "validation_status": validation,
                }
            )
            self.with_context(usl_sign_transition=True, usl_sign_freeze=True).write(
                {
                    "final_data": encoded,
                    "final_filename": f"{self.name} - signed.pdf",
                    "final_sha256": hashlib.sha256(content).hexdigest(),
                    "validation_status": validation,
                }
            )
        audit_missing = False
        for signer in self.signer_ids.filtered("provider_signer_id"):
            reference = f"audit:{signer.provider_signer_id}"
            if evidence_model.search_count(
                [
                    ("request_id", "=", self.id),
                    ("kind", "=", "audit_trail"),
                    ("provider_reference", "=", reference),
                ],
                limit=1,
            ):
                continue
            try:
                audit = client.download_audit_trail(
                    self.provider_transaction_id, signer.provider_signer_id
                )
            except ProviderError:
                audit_missing = True
                continue
            evidence_model.create(
                {
                    "request_id": self.id,
                    "signer_id": signer.id,
                    "kind": "audit_trail",
                    "name": f"{self.name} - audit - {signer.partner_id.name}.json",
                    "data": b64encode(audit),
                    "mimetype": "application/json",
                    "provider_reference": reference,
                    "retrieved_at": fields.Datetime.now(),
                    "validation_status": "valid",
                }
            )
        all_signed = bool(self.signer_ids) and all(
            signer.state == "signed" for signer in self.signer_ids
        )
        evidence_complete = not audit_missing and self.validation_status == "valid"
        vals = {
            "evidence_status": "available" if evidence_complete else "missing",
            "achieved_assurance": self.requested_assurance if all_signed else False,
        }
        if evidence_complete and all_signed:
            vals.update(
                {
                    "state": "completed",
                    "completed_at": self.completed_at or fields.Datetime.now(),
                    "last_error": False,
                    "recovery_action": False,
                }
            )
        else:
            vals.update(
                {
                    "state": "action_required",
                    "last_error": self.env._(
                        "The provider completed signing, but the full evidence package is not yet available."
                    ),
                    "recovery_action": self.env._(
                        "Refresh provider status to retry evidence retrieval."
                    ),
                }
            )
        self.with_context(usl_sign_transition=True).write(vals)
        if evidence_complete and all_signed:
            self._post_business_event(
                self.env._("Signed document available: %(name)s", name=self.name)
            )
            self._notify_responsible(
                self.env._("Signed document available"),
                self.env._(
                    "The signature request %(name)s is complete and its evidence is available.",
                    name=self.name,
                ),
            )
            self._deliver_completed_document()

    def _deliver_completed_document(self):
        self.ensure_one()
        if not self.company_id.sign_deliver_completed_to_signers or not self.final_data:
            return
        attachment = self.env["ir.attachment"].sudo().search(
            [
                ("res_model", "=", "sign.oca.request"),
                ("res_id", "=", self.id),
                ("res_field", "=", "final_data"),
            ],
            limit=1,
        )
        self.env["mail.thread"].message_notify(
            subject=self.env._("Your signed document is available"),
            body=self.env._(
                "All required signatures and the evidence package have been received."
            ),
            partner_ids=self.signer_ids.partner_id.ids,
            attachment_ids=attachment.ids,
            email_layout_xmlid="mail.mail_notification_light",
        )

    def _provider_cancel(self):
        for request in self.filtered("provider_transaction_id"):
            try:
                request._provider_client().cancel(request.provider_transaction_id)
            except ProviderError as error:
                request._set_action_required(
                    str(error), request.env._("Reconcile provider status before cancelling.")
                )
                raise ValidationError(str(error)) from error
        return True

    def _apply_provider_event(self, payload):
        self.ensure_one()
        event_model = self.env["usl.sign.provider.event"].sudo()
        event_id = payload.get("event_id")
        existing = event_model.search(
            [("provider_code", "=", "yousign"), ("event_id", "=", event_id)],
            limit=1,
        )
        if event_id and existing:
            return existing
        event_time = event_model._event_datetime(payload)
        if self.provider_last_event_at and event_time < self.provider_last_event_at:
            return event_model.record_event(
                self, payload, "ignored", "Older than the latest processed event"
            )[0]
        event_name = payload.get("event_name", "")
        data = payload.get("data") or {}
        signer_data = data.get("signer") or {}
        signer = self.signer_ids.filtered(
            lambda row: row.provider_signer_id == signer_data.get("id")
        )[:1]
        if signer and signer.provider_last_event_at and event_time < signer.provider_last_event_at:
            return event_model.record_event(
                self, payload, "ignored", "Older than the latest signer event"
            )[0]
        request_state = {
            "signature_request.activated": "sent",
            "signature_request.done": "action_required",
            "signature_request.declined": "declined",
            "signature_request.expired": "expired",
            "signature_request.canceled": "cancelled",
        }.get(event_name)
        if request_state and self.state not in TERMINAL_REQUEST_STATES:
            vals = {"state": request_state, "provider_last_event_at": event_time}
            if event_name == "signature_request.done":
                vals.update(
                    {
                        "evidence_status": "pending",
                        "last_error": self.env._(
                            "All signatures are complete; final evidence is being retrieved."
                        ),
                        "recovery_action": self.env._(
                            "Refresh provider status to retry evidence retrieval."
                        ),
                    }
                )
            self.with_context(usl_sign_transition=True).write(vals)
        if signer:
            signer_state = {
                "signer.notified": "notified",
                "signer.link_opened": "viewed",
                "signer.done": "signed",
                "signer.declined": "declined",
                "signer.error": "error",
                "signer.identification_failed": "error",
                "signer.identification_blocked": "error",
            }.get(event_name)
            if signer_state and (
                not signer.provider_last_event_at
                or event_time >= signer.provider_last_event_at
            ):
                vals = {
                    "state": signer_state,
                    "provider_last_event_at": event_time,
                }
                if signer_state == "viewed":
                    vals["viewed_at"] = fields.Datetime.now()
                elif signer_state == "signed":
                    vals.update(
                        {
                            "signed_on": fields.Datetime.now(),
                            "achieved_assurance": self.requested_assurance,
                            "access_revoked": True,
                        }
                    )
                elif signer_state == "declined":
                    decline_reason = signer_data.get("decline_reason") or data.get(
                        "decline_reason"
                    )
                    vals.update(
                        {
                            "declined_at": fields.Datetime.now(),
                            "access_revoked": True,
                            "decline_reason": decline_reason or False,
                        }
                    )
                signer.write(vals)
                if signer_state == "notified":
                    signer._send_signer_invitation()
                elif signer_state == "viewed" and self.state == "sent":
                    self.with_context(usl_sign_transition=True).write({"state": "viewed"})
                elif signer_state == "signed" and self.state not in TERMINAL_REQUEST_STATES:
                    self.with_context(usl_sign_transition=True).write({"state": "partial"})
                elif signer_state == "declined":
                    explanation = decline_reason or self.env._(
                        "The signer declined the request."
                    )
                    self.with_context(usl_sign_transition=True).write(
                        {"state": "declined"}
                    )
                    self.signer_ids.filtered(lambda row: row != signer).write(
                        {"access_revoked": True}
                    )
                    self._store_terminal_evidence(
                        "decline",
                        explanation,
                        signer=signer,
                        reference=f"decline:{payload.get('event_id')}",
                    )
                    self._notify_responsible(
                        self.env._("Signature request declined"),
                        self.env._(
                            "%(signer)s declined the signature request %(name)s.",
                            signer=signer.partner_id.display_name,
                            name=self.name,
                        ),
                    )
                    self._post_business_event(
                        self.env._("Signature request declined: %(name)s", name=self.name)
                    )
                elif signer_state == "error" and self.state not in TERMINAL_REQUEST_STATES:
                    self._set_action_required(
                        self.env._("Signer authentication requires attention."),
                        self.env._("Reconcile provider status before asking the signer to retry."),
                    )
        if request_state == "expired":
            self.signer_ids.filtered(lambda row: row.state != "signed").write(
                {"state": "expired", "access_revoked": True}
            )
            self._store_terminal_evidence(
                "expiration",
                self.env._("The provider reported that the request expired."),
                reference=f"expiration:{payload.get('event_id')}",
            )
            self._notify_responsible(
                self.env._("Signature request expired"),
                self.env._("The signature request %(name)s expired.", name=self.name),
            )
        elif request_state == "declined":
            self.signer_ids.filtered(lambda row: row.state != "signed").write(
                {"access_revoked": True}
            )
            self._store_terminal_evidence(
                "decline",
                self.env._("The provider reported that the request was declined."),
                reference=f"decline:{payload.get('event_id')}",
            )
            self._notify_responsible(
                self.env._("Signature request declined"),
                self.env._("The signature request %(name)s was declined.", name=self.name),
            )
        elif request_state == "cancelled":
            self.signer_ids.filtered(lambda row: row.state != "signed").write(
                {"state": "cancelled", "access_revoked": True}
            )
            self._store_terminal_evidence(
                "cancellation",
                self.env._("The provider reported that the request was cancelled."),
                reference=f"cancellation:{payload.get('event_id')}",
            )
        if not self.provider_last_event_at or event_time >= self.provider_last_event_at:
            self.with_context(usl_sign_transition=True).write(
                {"provider_last_event_at": event_time}
            )
        event, _created = event_model.record_event(self, payload, "processed")
        return event

    @classmethod
    def _provider_request_id_from_event(cls, payload):
        data = payload.get("data") or {}
        request_data = data.get("signature_request") or {}
        signer_data = data.get("signer") or {}
        return (
            request_data.get("id")
            or signer_data.get("signature_request_id")
            or data.get("signature_request_id")
        )

    def _cron_reconcile_provider(self):
        requests = self.search(
            [
                ("provider_transaction_id", "!=", False),
                "|",
                ("state", "in", list(ACTIVE_REQUEST_STATES)),
                "&",
                ("state", "=", "completed"),
                ("evidence_status", "!=", "available"),
            ],
            limit=50,
        )
        requests._provider_reconcile()

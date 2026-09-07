"""Guarded PDP and Peppol transport extensions for electronic-invoice reception."""

import hashlib
import os
from datetime import timedelta

from lxml import etree

from odoo import (
    _,
    api,
    fields,
    models,
)
from odoo.exceptions import UserError

from .einvoice_readiness import EINVOICE_RECEPTION_STATUSES, TRUTHY_ENVIRONMENT_VALUES


class PdpRegistration(models.TransientModel):
    _inherit = "pdp.registration"

    def _rebuild_check_live_action(self):
        self.ensure_one()
        if (
            self.company_id._get_peppol_edi_mode() == "demo"
            and self.env.context.get("rebuild_einvoice_safe_demo")
        ):
            self.company_id._check_rebuild_einvoice_manager_access()
        else:
            self.company_id._check_rebuild_einvoice_activation_ready()

    def button_trigger_authentication(self):
        self._rebuild_check_live_action()
        return super(PdpRegistration, self.sudo()).button_trigger_authentication()

    def button_refresh_authentication(self):
        self._rebuild_check_live_action()
        return super(PdpRegistration, self.sudo()).button_refresh_authentication()

    def button_open_authentication_link(self):
        self._rebuild_check_live_action()
        return super(
            PdpRegistration,
            self.sudo(),
        ).button_open_authentication_link()

    def button_register_pdp_participant(self):
        self._rebuild_check_live_action()
        result = super(
            PdpRegistration,
            self.sudo(),
        ).button_register_pdp_participant()
        return result

    def button_deregister_pdp_participant(self):
        self._rebuild_check_live_action()
        result = super(
            PdpRegistration,
            self.sudo(),
        ).button_deregister_pdp_participant()
        self.company_id.sudo().rebuild_einvoice_exchange_enabled = False
        return result


class PeppolRegistration(models.TransientModel):
    _inherit = "peppol.registration"

    def button_register_peppol_participant(self, selected_auth=None):
        self.ensure_one()
        if self.company_id.account_fiscal_country_id.code == "FR":
            self.company_id._check_rebuild_einvoice_activation_ready()
        return super().button_register_peppol_participant(
            selected_auth=selected_auth,
        )


class AccountEdiCii(models.AbstractModel):
    _inherit = "account.edi.cii"

    def _cii_get_billing_specified_period_node(self, vals):
        """Keep CII export compatible with the USL deferral data model.

        Upstream uses optional line-level deferred dates.  USL keeps deferral
        schedules in its own model, so those fields are not necessarily
        installed on account.move.line.  In that case, retain the invoice-level
        billing dates without inventing line-level deferral data.
        """
        invoice = vals["invoice"]
        line_fields = invoice.invoice_line_ids._fields
        if {
            "deferred_start_date",
            "deferred_end_date",
        } <= line_fields.keys():
            return super()._cii_get_billing_specified_period_node(vals)
        return {
            "ram:StartDateTime": self._cii_get_date_time_string_node(
                vals,
                invoice.invoice_date,
            ) if invoice.invoice_date else None,
            "ram:EndDateTime": self._cii_get_date_time_string_node(
                vals,
                invoice.invoice_date_due,
            ) if invoice.invoice_date_due else None,
        }


class AccountEdiProxyClientUser(models.Model):
    _inherit = "account_edi_proxy_client.user"

    @api.model
    def _rebuild_ereporting_live_enabled(self):
        return (
            os.getenv("USL_EREPORTING_LIVE_ENABLED", "").strip().lower()
            in TRUTHY_ENVIRONMENT_VALUES
        )

    def _call_peppol_proxy(self, endpoint, params=None):
        self.ensure_one()
        company = self.company_id
        if self.edi_mode == "demo":
            return super()._call_peppol_proxy(endpoint, params=params)
        if not company._rebuild_einvoice_live_call_allowed():
            raise UserError(
                _(
                    "Production activation required. This database is not "
                    "authorized to contact an electronic-invoicing platform.",
                ),
            )
        try:
            result = super()._call_peppol_proxy(endpoint, params=params)
        except Exception as error:  # noqa: BLE001
            error_text = str(error)
            status = (
                "authentication"
                if any(
                    token in error_text.lower()
                    for token in ("auth", "token", "signature", "credential")
                )
                else "temporary_failure"
            )
            message = (
                _(
                    "Platform authentication needs attention. Suspend reception "
                    "until credentials or the database connection are restored.",
                )
                if status == "authentication"
                else _(
                    "The platform could not be reached. No document was lost; "
                    "scheduled reception will retry after service recovery.",
                )
            )
            company._rebuild_record_einvoice_poll_result(status, message)
            raise
        if endpoint.rstrip("/").endswith("get_all_documents"):
            company._rebuild_record_einvoice_poll_result(
                "passed",
                _("The latest platform reception check completed successfully."),
            )
        return result

    @api.model
    def _cron_peppol_get_new_documents(self):
        edi_users = self.search([
            ("company_id.account_peppol_proxy_state", "=", "receiver"),
            ("company_id.rebuild_einvoice_exchange_enabled", "=", True),
            ("proxy_type", "in", self._get_peppol_proxy_types()),
        ])
        edi_users._peppol_get_new_documents(skip_no_journal=True)

    @api.model
    def _cron_peppol_get_message_status(self):
        edi_users = self.search([
            (
                "company_id.account_peppol_proxy_state",
                "in",
                self._get_can_send_domain(),
            ),
            ("company_id.rebuild_einvoice_exchange_enabled", "=", True),
            ("proxy_type", "in", self._get_peppol_proxy_types()),
        ])
        edi_users._peppol_get_message_status()

    @api.model
    def _cron_peppol_get_participant_status(self):
        edi_users = self.search([
            ("company_id.rebuild_einvoice_activation_approved", "=", True),
            ("proxy_type", "in", self._get_peppol_proxy_types()),
        ])
        edi_users._peppol_get_participant_status()

        # This override narrows upstream polling to USL-approved providers, but
        # must retain its one-hour retry while SMP registration is pending.
        if self.search_count([
            ("company_id.rebuild_einvoice_activation_approved", "=", True),
            ("company_id.account_peppol_proxy_state", "=", "smp_registration"),
            ("proxy_type", "in", self._get_peppol_proxy_types()),
        ], limit=1):
            self.env.ref(
                "account_peppol.ir_cron_peppol_get_participant_status",
            )._trigger(at=fields.Datetime.now() + timedelta(hours=1))

    @api.model
    def _cron_peppol_webhook_keepalive(self):
        edi_users = self.search([
            ("company_id.account_peppol_proxy_state", "in", ["sender", "receiver"]),
            ("company_id.rebuild_einvoice_exchange_enabled", "=", True),
        ])
        edi_users._peppol_reset_webhook()

    def _pdp_get_regulatory_documents(self, batch_size=None):
        return super()._pdp_get_regulatory_documents(batch_size=batch_size)

    @api.model
    def _cron_pdp_get_regulatory_documents(self):
        edi_users = self.search([
            ("company_id.account_peppol_proxy_state", "=", "receiver"),
            ("company_id.rebuild_einvoice_exchange_enabled", "=", True),
            ("proxy_type", "=", "pdp"),
        ])
        edi_users._pdp_get_regulatory_documents()

    def _pdp_send_lifecycles(self, batch_size=None):
        if not self._rebuild_ereporting_live_enabled():
            return None
        enabled_users = self.filtered(
            "company_id.rebuild_einvoice_exchange_enabled",
        )
        return super(
            AccountEdiProxyClientUser,
            enabled_users,
        )._pdp_send_lifecycles(batch_size=batch_size)

    def _peppol_import_invoice(self, attachment, peppol_state, uuid, journal=None):
        self.ensure_one()
        company = self.company_id
        evidence_model = self.env["rebuild.einvoice.reception"].sudo()
        retry_evidence = evidence_model.browse(
            self.env.context.get("rebuild_einvoice_retry_reception_id"),
        ).exists()
        if retry_evidence:
            if (
                retry_evidence.company_id != company
                or retry_evidence.provider_message_uuid != uuid
            ):
                raise UserError(_("The retry evidence does not match this document."))
            evidence = retry_evidence
        else:
            idempotent_evidence = evidence_model.search([
                ("company_id", "=", company.id),
                ("provider_message_uuid", "=", uuid),
            ], limit=1)
            if idempotent_evidence:
                if attachment != idempotent_evidence.attachment_id:
                    attachment.sudo().write({
                        "res_model": idempotent_evidence._name,
                        "res_id": idempotent_evidence.id,
                    })
                return {
                    "uuid": uuid,
                    **(
                        {"move": idempotent_evidence.move_id}
                        if idempotent_evidence.move_id
                        else {}
                    ),
                }

            document_hash = hashlib.sha256(
                bytes(attachment.raw or b""),
            ).hexdigest()
            document_format, document_kind = self._rebuild_einvoice_format(
                attachment,
            )
            original = evidence_model.search([
                ("company_id", "=", company.id),
                ("status", "!=", "duplicate"),
                ("document_hash", "=", document_hash),
            ], limit=1)
            evidence = evidence_model.create({
                "company_id": company.id,
                "provider_message_uuid": uuid,
                "provider_state": peppol_state,
                "filename": attachment.name,
                "document_hash": document_hash,
                "document_format": document_format,
                "document_kind": document_kind,
                "attachment_id": attachment.id,
                "is_test": bool(
                    self.env.context.get("rebuild_einvoice_is_test")
                    or self.edi_mode in {"demo", "test"},
                ),
            })
            if original:
                evidence.write({
                    "status": "duplicate",
                    "failure_kind": "accounting",
                    "duplicate_of_id": original.id,
                    "move_id": original.move_id.id,
                    "processing_summary": _(
                        "No second vendor bill was created. The structured "
                        "document matches an invoice already received.",
                    ),
                })
                attachment.sudo().write({
                    "res_model": evidence._name,
                    "res_id": evidence.id,
                })
                return {"uuid": uuid}

        evidence.write({
            "status": "received",
            "failure_kind": False,
            "failure_code": False,
            "processing_summary": _("The structured document is being processed."),
            "technical_details": False,
            "attempt_count": evidence.attempt_count + 1,
            "last_attempt_at": fields.Datetime.now(),
        })
        processing_attachment = attachment
        if evidence.document_format == "facturx":
            files_data = self.env["account.move"]._to_files_data(
                attachment.sudo(),
            )
            embedded_files = self.env["account.move"]._unwrap_attachments(
                files_data,
            )
            embedded_invoice = next(
                (
                    file_data
                    for file_data in embedded_files
                    if file_data.get("xml_tree") is not None
                    and self._rebuild_einvoice_xml_format(
                        file_data["xml_tree"],
                    )[0] in {"ubl", "cii"}
                ),
                None,
            )
            if embedded_invoice:
                processing_attachment = self.env["ir.attachment"].sudo().create({
                    "name": embedded_invoice["name"],
                    "raw": embedded_invoice["raw"],
                    "mimetype": embedded_invoice["mimetype"],
                })

        try:
            # Upstream logs a malformed type-code parse but continues into its
            # importer, which can create an empty draft move.  Parse first so
            # the existing reception-evidence path remains non-polluting.
            self._get_type_code(
                self.env["account.move"]._to_files_data(processing_attachment),
            )
            result = super()._peppol_import_invoice(
                processing_attachment,
                peppol_state,
                uuid,
                journal=journal,
            )
        except Exception as error:  # noqa: BLE001
            evidence.write({
                "status": "technical_error",
                "failure_kind": "technical",
                "failure_code": (
                    "invalid_document"
                    if evidence.document_format == "unknown"
                    else self._rebuild_einvoice_failure_code(error)
                ),
                "processing_summary": _(
                    "The original document is safe, but a draft bill could not "
                    "be created. Correct the reported condition and select Retry Processing.",
                ),
                "technical_details": str(error),
            })
            attachment.sudo().write({
                "res_model": evidence._name,
                "res_id": evidence.id,
            })
            return {"uuid": uuid}

        move = result.get("move", self.env["account.move"])
        values = {
            "move_id": move.id,
            "attachment_id": attachment.id,
        }
        if peppol_state == "error":
            values.update({
                "status": "rejected",
                "failure_kind": "technical",
                "failure_code": "temporary_provider",
                "processing_summary": _(
                    "The platform marked this delivery as rejected. The draft "
                    "and original document are retained for review; do not create "
                    "a replacement bill without platform confirmation.",
                ),
            })
        elif (
            move
            and move.partner_id
            and move.invoice_line_ids
            and move.move_type in {"in_invoice", "in_refund"}
        ):
            values.update({
                "status": "bill_created",
                "failure_kind": False,
                "failure_code": False,
                "processing_summary": _(
                    "A native draft vendor document was created and the "
                    "original structured invoice was preserved.",
                ),
                "technical_details": False,
            })
        else:
            if move:
                move.peppol_move_state = "error"
            values.update({
                "status": "technical_error",
                "failure_kind": "technical",
                "failure_code": (
                    "invalid_document"
                    if evidence.document_format == "unknown"
                    else "mapping"
                ),
                "processing_summary": _(
                    "The original document is safe, but it did not produce a "
                    "complete vendor bill. Review the document and Retry Processing "
                    "after correcting tax, supplier or journal configuration.",
                ),
            })
        evidence.write(values)
        if evidence.document_format == "facturx":
            attachment.sudo().write({
                "res_model": evidence._name,
                "res_id": evidence.id,
            })
        return result

    @api.model
    def _rebuild_einvoice_format(self, attachment):
        if (
            attachment.mimetype == "application/pdf"
            or (attachment.name or "").lower().endswith(".pdf")
        ):
            files_data = self.env["account.move"]._to_files_data(attachment)
            embedded = self.env["account.move"]._unwrap_attachments(files_data)
            for file_data in embedded:
                if file_data.get("xml_tree") is not None:
                    document_format, kind = self._rebuild_einvoice_xml_format(
                        file_data["xml_tree"],
                    )
                    if document_format in {"ubl", "cii"}:
                        return "facturx", kind
            return "facturx", "unknown"
        try:
            tree = etree.fromstring(
                bytes(attachment.raw or b""),
                parser=etree.XMLParser(resolve_entities=False),
            )
        except etree.XMLSyntaxError:
            return "unknown", "unknown"
        return self._rebuild_einvoice_xml_format(tree)

    @api.model
    def _rebuild_einvoice_xml_format(self, tree):
        localname = etree.QName(tree).localname
        if localname == "CrossIndustryInvoice":
            type_code = tree.findtext(
                ".//{*}ExchangedDocument/{*}TypeCode",
            )
            return (
                "cii",
                "credit_note" if type_code == "381" else "invoice",
            )
        if localname == "CreditNote":
            return "ubl", "credit_note"
        if localname == "Invoice":
            return "ubl", "invoice"
        return "unknown", "unknown"

    @api.model
    def _rebuild_einvoice_failure_code(self, error):
        text = str(error).lower()
        if isinstance(error, etree.XMLSyntaxError) or any(
            marker in text
            for marker in ("xml", "malformed", "parse", "decode")
        ):
            return "invalid_document"
        if any(
            marker in text
            for marker in ("auth", "token", "signature", "credential")
        ):
            return "authentication"
        if any(
            marker in text
            for marker in ("timeout", "temporar", "connection", "unavailable")
        ):
            return "temporary_provider"
        return "unexpected"


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model
    def _pdp_annuaire_lookup_participant(self, edi_identification):
        company = self.env.company
        if (
            company.account_fiscal_country_id.code == "FR"
            and company._get_peppol_edi_mode() != "demo"
            and not company._rebuild_einvoice_live_call_allowed()
        ):
            return None
        return super()._pdp_annuaire_lookup_participant(edi_identification)

    @api.model
    def _peppol_lookup_participant(self, edi_identification):
        company = self.env.company
        if (
            company.account_fiscal_country_id.code == "FR"
            and company._get_peppol_edi_mode() != "demo"
            and not company._rebuild_einvoice_live_call_allowed()
        ):
            return None
        return super()._peppol_lookup_participant(edi_identification)


class PdpReportsFlow(models.Model):
    _inherit = "l10n.fr.pdp.reports.flow"

    @api.model
    def _rebuild_ereporting_live_enabled(self):
        return (
            os.getenv("USL_EREPORTING_LIVE_ENABLED", "").strip().lower()
            in TRUTHY_ENVIRONMENT_VALUES
        )

    @api.model
    def _cron_update_and_send_flows(self):
        if not self._rebuild_ereporting_live_enabled():
            return None
        return super()._cron_update_and_send_flows()

    def action_send(self, check_totp=True):
        if not self._rebuild_ereporting_live_enabled():
            raise UserError(
                _(
                    "E-reporting is inactive. A separate production rollout "
                    "and deployment-level authorization are required before "
                    "any regulatory flow can be submitted.",
                ),
            )
        return super().action_send(check_totp=check_totp)


class AccountMove(models.Model):
    _inherit = "account.move"

    rebuild_einvoice_reception_ids = fields.One2many(
        "rebuild.einvoice.reception",
        "move_id",
        string="Electronic Invoice Reception Evidence",
    )
    rebuild_einvoice_reception_status = fields.Selection(
        selection=EINVOICE_RECEPTION_STATUSES,
        string="Reception Status",
        compute="_compute_rebuild_einvoice_reception_status",
    )
    rebuild_einvoice_reception_count = fields.Integer(
        string="Electronic Invoices",
        compute="_compute_rebuild_einvoice_reception_status",
    )

    @api.depends(
        "rebuild_einvoice_reception_ids",
        "rebuild_einvoice_reception_ids.status",
        "rebuild_einvoice_reception_ids.received_at",
    )
    def _compute_rebuild_einvoice_reception_status(self):
        for move in self:
            move.rebuild_einvoice_reception_count = len(
                move.rebuild_einvoice_reception_ids,
            )
            latest = move.rebuild_einvoice_reception_ids.sorted(
                lambda reception: (
                    reception.received_at or fields.Datetime.from_string(
                        "1970-01-01 00:00:00",
                    ),
                    reception.id,
                ),
                reverse=True,
            )[:1]
            move.rebuild_einvoice_reception_status = latest.status

    def action_open_rebuild_einvoice_reception(self):
        self.ensure_one()
        receptions = self.rebuild_einvoice_reception_ids
        if len(receptions) == 1:
            return {
                "type": "ir.actions.act_window",
                "name": _("Electronic Invoice"),
                "res_model": "rebuild.einvoice.reception",
                "res_id": receptions.id,
                "view_mode": "form",
            }
        action = self.env["ir.actions.actions"]._for_xml_id(
            "rebuild_account_migration.action_rebuild_einvoice_reception",
        )
        action["domain"] = [("move_id", "=", self.id)]
        action["context"] = {"create": False, "delete": False}
        return action


class AccountJournal(models.Model):
    _inherit = "account.journal"

    def button_fetch_in_einvoices(self):
        inactive_companies = self.company_id.filtered(
            lambda company: (
                company.account_fiscal_country_id.code == "FR"
                and not company.rebuild_einvoice_exchange_enabled
            ),
        )
        if inactive_companies:
            raise UserError(
                _(
                    "Incoming electronic invoices are paused for: %s",
                    ", ".join(inactive_companies.mapped("display_name")),
                ),
            )
        return super().button_fetch_in_einvoices()

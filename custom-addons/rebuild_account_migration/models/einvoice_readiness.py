import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import UserError

EINVOICE_CRON_XMLIDS = (
    "account_peppol.ir_cron_peppol_get_new_documents",
    "account_peppol.ir_cron_peppol_get_message_status",
    "account_peppol.ir_cron_peppol_get_participant_status",
    "account_peppol.ir_cron_peppol_webhook_keepalive",
    "l10n_fr_pdp.ir_cron_pdp_get_regulatory_documents",
    "l10n_fr_pdp.ir_cron_pdp_send_lifecycles",
    "l10n_fr_pdp.ir_cron_l10n_fr_pdp_generate_flows",
)
EINVOICE_RECEPTION_STATUSES = [
    ("received", "Received"),
    ("bill_created", "Draft Bill Created"),
    ("rejected", "Rejected by Provider"),
    ("duplicate", "Duplicate"),
    ("technical_error", "Technical Failure"),
]


class RebuildEinvoiceReception(models.Model):
    _name = "rebuild.einvoice.reception"
    _description = "Electronic Invoice Reception Evidence"
    _order = "received_at desc, id desc"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    received_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    provider_message_uuid = fields.Char(
        string="Provider Message ID",
        required=True,
        index=True,
    )
    provider_state = fields.Char(readonly=True)
    filename = fields.Char(readonly=True)
    document_hash = fields.Char(
        string="Document SHA-256",
        readonly=True,
        index=True,
    )
    status = fields.Selection(
        EINVOICE_RECEPTION_STATUSES,
        required=True,
        default="received",
        index=True,
        readonly=True,
    )
    failure_kind = fields.Selection(
        [
            ("accounting", "Accounting Review"),
            ("technical", "Technical Processing"),
        ],
        readonly=True,
    )
    processing_summary = fields.Text(readonly=True)
    move_id = fields.Many2one(
        "account.move",
        string="Vendor Bill",
        readonly=True,
        index=True,
    )
    attachment_id = fields.Many2one(
        "ir.attachment",
        string="Original Structured Invoice",
        readonly=True,
        ondelete="restrict",
    )
    duplicate_of_id = fields.Many2one(
        "rebuild.einvoice.reception",
        string="Original Reception",
        readonly=True,
    )

    def action_open_bill(self):
        self.ensure_one()
        if not self.move_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": self.move_id.display_name,
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
        }


class ResCompany(models.Model):
    _inherit = "res.company"

    rebuild_einvoice_provider = fields.Selection(
        [
            ("odoo_pdp", "Odoo Approved Platform (native integration)"),
            ("other", "Another Approved Platform (adapter required)"),
        ],
        string="Approved Platform",
        tracking=True,
    )
    rebuild_einvoice_environment = fields.Selection(
        [
            ("development", "Development"),
            ("production", "Production"),
        ],
        string="Accounting Deployment",
        required=True,
        default="development",
        tracking=True,
    )
    rebuild_einvoice_activation_approved = fields.Boolean(
        string="Production Activation Approved",
        readonly=True,
        tracking=True,
    )
    rebuild_einvoice_approved_by_id = fields.Many2one(
        "res.users",
        string="Approved By",
        readonly=True,
    )
    rebuild_einvoice_approved_at = fields.Datetime(
        string="Approved At",
        readonly=True,
    )
    rebuild_einvoice_capability_status = fields.Selection(
        [
            ("implemented_validated", "Implemented and Validated"),
            ("module_missing", "Required Module Missing"),
        ],
        compute="_compute_rebuild_einvoice_readiness",
        string="Reception Capability",
    )
    rebuild_einvoice_readiness_status = fields.Selection(
        [
            ("configuration_required", "Configuration Required"),
            ("ready", "Ready for Production Activation"),
            ("connected", "Connected"),
        ],
        compute="_compute_rebuild_einvoice_readiness",
        string="Production Readiness",
    )
    rebuild_einvoice_connection_status = fields.Selection(
        [
            ("not_connected", "Not Connected"),
            ("registration_pending", "Registration Pending"),
            ("connected", "Connected"),
            ("rejected", "Connection Rejected"),
        ],
        compute="_compute_rebuild_einvoice_readiness",
        string="Live Connection",
    )
    rebuild_einvoice_exchange_enabled = fields.Boolean(
        string="Scheduled Exchange Enabled",
        compute="_compute_rebuild_einvoice_exchange_enabled",
    )
    rebuild_einvoice_blockers = fields.Text(
        string="Remaining Decisions and Configuration",
        compute="_compute_rebuild_einvoice_readiness",
    )
    rebuild_einvoice_reception_ids = fields.One2many(
        "rebuild.einvoice.reception",
        "company_id",
        string="Reception Evidence",
    )

    def _rebuild_einvoice_modules_ready(self):
        modules = self.env["ir.module.module"].sudo()
        return not modules.search_count([
            ("name", "in", [
                "account_edi_ubl_cii",
                "account_peppol",
                "account_peppol_response",
                "l10n_fr_pdp",
            ]),
            ("state", "!=", "installed"),
        ])

    def _rebuild_einvoice_configuration_blockers(self):
        self.ensure_one()
        blockers = []
        if self.account_fiscal_country_id.code != "FR":
            blockers.append(_("Set the accounting fiscal country to France."))
        if not self.vat:
            blockers.append(_("Record the French VAT number."))
        if not self.company_registry:
            blockers.append(_("Record the SIREN or SIRET company identifier."))
        if self.peppol_eas != "0225" or not self.peppol_endpoint:
            blockers.append(_("Configure the French electronic-invoicing identifier (scheme 0225)."))
        if not self.account_peppol_contact_email:
            blockers.append(_("Record the approved-platform contact email."))
        if not self.account_peppol_phone_number:
            blockers.append(_("Record the approved-platform mobile number."))
        if not self.peppol_purchase_journal_id:
            blockers.append(_("Select the purchase journal for received invoices."))
        if not self.rebuild_einvoice_provider:
            blockers.append(_("Select an approved platform."))
        elif self.rebuild_einvoice_provider != "odoo_pdp":
            blockers.append(_("Install and validate the selected platform adapter."))
        return blockers

    @api.depends(
        "account_fiscal_country_id",
        "vat",
        "company_registry",
        "peppol_eas",
        "peppol_endpoint",
        "account_peppol_contact_email",
        "account_peppol_phone_number",
        "peppol_purchase_journal_id",
        "rebuild_einvoice_provider",
        "rebuild_einvoice_environment",
        "rebuild_einvoice_activation_approved",
        "account_peppol_proxy_state",
    )
    def _compute_rebuild_einvoice_readiness(self):
        modules_ready = self._rebuild_einvoice_modules_ready()
        for company in self:
            blockers = company._rebuild_einvoice_configuration_blockers()
            company.rebuild_einvoice_capability_status = (
                "implemented_validated" if modules_ready else "module_missing"
            )
            state = company.account_peppol_proxy_state
            company.rebuild_einvoice_connection_status = {
                "sender": "registration_pending",
                "smp_registration": "registration_pending",
                "receiver": "connected",
                "rejected": "rejected",
            }.get(state, "not_connected")
            if state == "receiver":
                company.rebuild_einvoice_readiness_status = "connected"
            elif (
                modules_ready
                and not blockers
                and company.rebuild_einvoice_environment == "production"
                and company.rebuild_einvoice_activation_approved
            ):
                company.rebuild_einvoice_readiness_status = "ready"
            else:
                company.rebuild_einvoice_readiness_status = (
                    "configuration_required"
                )
            if company.rebuild_einvoice_environment != "production":
                blockers.append(_("Mark the deployed Accounting system as Production."))
            if not company.rebuild_einvoice_activation_approved:
                blockers.append(_("Record Accounting Manager approval for live activation."))
            company.rebuild_einvoice_blockers = "\n".join(
                f"• {blocker}" for blocker in blockers
            ) or _("No activation blocker remains.")

    def _compute_rebuild_einvoice_exchange_enabled(self):
        crons = [
            cron
            for xmlid in EINVOICE_CRON_XMLIDS
            if (cron := self.env.ref(xmlid, raise_if_not_found=False))
        ]
        for company in self:
            company.rebuild_einvoice_exchange_enabled = any(
                cron.active for cron in crons
            )

    def _check_rebuild_einvoice_activation_ready(self):
        self.ensure_one()
        blockers = self._rebuild_einvoice_configuration_blockers()
        if self.rebuild_einvoice_environment != "production":
            blockers.append(_("The Accounting deployment is not marked Production."))
        if not self.rebuild_einvoice_activation_approved:
            blockers.append(_("Production activation has not been approved."))
        if blockers:
            raise UserError(
                _("Electronic invoicing cannot be activated:\n%s")
                % "\n".join(f"• {blocker}" for blocker in blockers),
            )

    def action_rebuild_approve_einvoice_activation(self):
        self.ensure_one()
        if self.rebuild_einvoice_environment != "production":
            raise UserError(
                _("Approval is available only on the deployed production Accounting system."),
            )
        blockers = self._rebuild_einvoice_configuration_blockers()
        if blockers:
            raise UserError(
                _("Complete the configuration before approval:\n%s")
                % "\n".join(f"• {blocker}" for blocker in blockers),
            )
        self.write({
            "rebuild_einvoice_activation_approved": True,
            "rebuild_einvoice_approved_by_id": self.env.user.id,
            "rebuild_einvoice_approved_at": fields.Datetime.now(),
        })

    def action_rebuild_revoke_einvoice_activation(self):
        self._rebuild_set_einvoice_crons(False)
        self.write({
            "rebuild_einvoice_activation_approved": False,
            "rebuild_einvoice_approved_by_id": False,
            "rebuild_einvoice_approved_at": False,
        })

    def _rebuild_set_einvoice_crons(self, active):
        for xmlid in EINVOICE_CRON_XMLIDS:
            if cron := self.env.ref(xmlid, raise_if_not_found=False):
                cron.active = active

    def action_rebuild_enable_einvoice_exchange(self):
        self.ensure_one()
        self._check_rebuild_einvoice_activation_ready()
        if self.account_peppol_proxy_state != "receiver":
            raise UserError(
                _("Complete approved-platform registration before enabling scheduled exchange."),
            )
        connected_companies = self.search([
            ("account_peppol_proxy_state", "=", "receiver"),
        ])
        unauthorized = connected_companies.filtered(
            lambda company: (
                company.rebuild_einvoice_environment != "production"
                or not company.rebuild_einvoice_activation_approved
                or company.rebuild_einvoice_provider != "odoo_pdp"
            ),
        )
        if unauthorized:
            raise UserError(_(
                "Scheduled exchange is database-wide. Review and approve every "
                "connected company first: %s",
                ", ".join(unauthorized.mapped("display_name")),
            ))
        self._rebuild_set_einvoice_crons(True)

    def action_rebuild_suspend_einvoice_exchange(self):
        self._rebuild_set_einvoice_crons(False)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    rebuild_einvoice_provider = fields.Selection(
        related="company_id.rebuild_einvoice_provider",
        readonly=False,
    )
    rebuild_einvoice_environment = fields.Selection(
        related="company_id.rebuild_einvoice_environment",
        readonly=False,
    )
    rebuild_einvoice_activation_approved = fields.Boolean(
        related="company_id.rebuild_einvoice_activation_approved",
    )
    rebuild_einvoice_capability_status = fields.Selection(
        related="company_id.rebuild_einvoice_capability_status",
    )
    rebuild_einvoice_readiness_status = fields.Selection(
        related="company_id.rebuild_einvoice_readiness_status",
    )
    rebuild_einvoice_connection_status = fields.Selection(
        related="company_id.rebuild_einvoice_connection_status",
    )
    rebuild_einvoice_blockers = fields.Text(
        related="company_id.rebuild_einvoice_blockers",
    )

    def action_open_pdp_form(self):
        self.ensure_one()
        self.company_id._check_rebuild_einvoice_activation_ready()
        return super().action_open_pdp_form()

    def action_open_peppol_form(self):
        self.ensure_one()
        if self.company_id.account_fiscal_country_id.code == "FR":
            self.company_id._check_rebuild_einvoice_activation_ready()
        return super().action_open_peppol_form()

    def button_peppol_deregister(self):
        result = super().button_peppol_deregister()
        self.company_id._rebuild_set_einvoice_crons(False)
        return result


class PdpRegistration(models.TransientModel):
    _inherit = "pdp.registration"

    def button_register_pdp_participant(self):
        self.ensure_one()
        self.company_id._check_rebuild_einvoice_activation_ready()
        return super().button_register_pdp_participant()


class AccountEdiProxyClientUser(models.Model):
    _inherit = "account_edi_proxy_client.user"

    def _peppol_import_invoice(self, attachment, peppol_state, uuid, journal=None):
        self.ensure_one()
        company = self.company_id
        document_hash = hashlib.sha256(attachment.raw or b"").hexdigest()
        evidence_model = self.env["rebuild.einvoice.reception"].sudo()
        original = evidence_model.search([
            ("company_id", "=", company.id),
            ("status", "!=", "duplicate"),
            "|",
            ("provider_message_uuid", "=", uuid),
            ("document_hash", "=", document_hash),
        ], limit=1)
        evidence = evidence_model.create({
            "company_id": company.id,
            "provider_message_uuid": uuid,
            "provider_state": peppol_state,
            "filename": attachment.name,
            "document_hash": document_hash,
            "attachment_id": attachment.id,
        })
        if original:
            evidence.write({
                "status": "duplicate",
                "failure_kind": "accounting",
                "duplicate_of_id": original.id,
                "move_id": original.move_id.id,
                "processing_summary": _(
                    "No second vendor bill was created. The provider message ID "
                    "or structured document matches reception %(reception)s.",
                    reception=original.display_name,
                ),
            })
            attachment.write({
                "res_model": evidence._name,
                "res_id": evidence.id,
            })
            return {"uuid": uuid}

        try:
            with self.env.cr.savepoint():
                result = super()._peppol_import_invoice(
                    attachment,
                    peppol_state,
                    uuid,
                    journal=journal,
                )
        except Exception as error:  # noqa: BLE001
            evidence.write({
                "status": "technical_error",
                "failure_kind": "technical",
                "processing_summary": _(
                    "The structured document could not be decoded: %s",
                    str(error),
                ),
            })
            attachment.write({
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
                "processing_summary": _(
                    "The provider delivered the document with an error status. "
                    "The draft and original payload are retained for diagnosis.",
                ),
            })
        elif move and move.partner_id and move.invoice_line_ids:
            values.update({
                "status": "bill_created",
                "processing_summary": _(
                    "A native draft vendor bill was created from the structured invoice.",
                ),
            })
        else:
            if move:
                move.peppol_move_state = "error"
            values.update({
                "status": "technical_error",
                "failure_kind": "technical",
                "processing_summary": _(
                    "The payload was retained, but it did not produce a complete "
                    "vendor bill. Review the bill chatter and structured document.",
                ),
            })
        evidence.write(values)
        return result


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

    @api.depends(
        "rebuild_einvoice_reception_ids",
        "rebuild_einvoice_reception_ids.status",
        "rebuild_einvoice_reception_ids.received_at",
    )
    def _compute_rebuild_einvoice_reception_status(self):
        for move in self:
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

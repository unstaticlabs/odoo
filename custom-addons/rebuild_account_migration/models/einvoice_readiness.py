import hashlib
import os
import uuid as uuid_lib

from lxml import etree
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import file_open

EINVOICE_RECEPTION_CRON_XMLIDS = (
    "account_peppol.ir_cron_peppol_get_new_documents",
    "account_peppol.ir_cron_peppol_get_message_status",
    "account_peppol.ir_cron_peppol_get_participant_status",
    "account_peppol.ir_cron_peppol_webhook_keepalive",
)
EINVOICE_RESTRICTED_CRON_XMLIDS = (
    "account_peppol_response.ir_cron_peppol_auto_register_services",
    "l10n_fr_pdp.ir_cron_pdp_get_regulatory_documents",
    "l10n_fr_pdp.ir_cron_pdp_send_lifecycles",
    "l10n_fr_pdp.ir_cron_l10n_fr_pdp_generate_flows",
)
EINVOICE_ALL_CRON_XMLIDS = (
    *EINVOICE_RECEPTION_CRON_XMLIDS,
    *EINVOICE_RESTRICTED_CRON_XMLIDS,
)
EINVOICE_RECEPTION_STATUSES = [
    ("received", "Processing"),
    ("bill_created", "Draft Bill Created"),
    ("rejected", "Rejected by Platform"),
    ("duplicate", "Duplicate Controlled"),
    ("technical_error", "Action Required"),
]
EINVOICE_TEST_TEMPLATE = (
    "rebuild_account_migration/static/src/einvoice/"
    "representative_ubl_invoice.xml"
)
TRUTHY_ENVIRONMENT_VALUES = {"1", "true", "yes", "on"}


class RebuildEinvoiceReception(models.Model):
    _name = "rebuild.einvoice.reception"
    _description = "Electronic Invoice Reception Evidence"
    _order = "received_at desc, id desc"
    _rec_name = "document_reference"

    _provider_message_company_uniq = models.Constraint(
        "UNIQUE(company_id, provider_message_uuid)",
        "This platform message has already been received for the company.",
    )

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
        string="Platform Message Reference",
        required=True,
        index=True,
    )
    provider_state = fields.Char(readonly=True)
    filename = fields.Char(
        string="Original Filename",
        readonly=True,
    )
    document_hash = fields.Char(
        string="Document Fingerprint",
        readonly=True,
        index=True,
    )
    document_format = fields.Selection(
        [
            ("ubl", "UBL"),
            ("cii", "CII"),
            ("facturx", "Factur-X"),
            ("unknown", "Unrecognized"),
        ],
        string="Structured Format",
        readonly=True,
    )
    document_kind = fields.Selection(
        [
            ("invoice", "Supplier Invoice"),
            ("credit_note", "Supplier Credit Note"),
            ("unknown", "Unrecognized Document"),
        ],
        string="Document Type",
        readonly=True,
    )
    is_test = fields.Boolean(
        string="Safe Test Document",
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
            ("technical", "Processing Failure"),
        ],
        readonly=True,
    )
    failure_code = fields.Selection(
        [
            ("invalid_document", "Malformed or Unsupported Document"),
            ("mapping", "Invoice Data Needs Review"),
            ("authentication", "Platform Authentication Required"),
            ("temporary_provider", "Platform Temporarily Unavailable"),
            ("unexpected", "Unexpected Processing Failure"),
        ],
        string="Recovery Category",
        readonly=True,
    )
    processing_summary = fields.Text(
        string="What Happened",
        readonly=True,
    )
    technical_details = fields.Text(readonly=True)
    attempt_count = fields.Integer(
        string="Processing Attempts",
        readonly=True,
        default=0,
    )
    last_attempt_at = fields.Datetime(readonly=True)
    can_retry = fields.Boolean(compute="_compute_can_retry")
    move_id = fields.Many2one(
        "account.move",
        string="Vendor Bill",
        readonly=True,
        index=True,
    )
    partner_id = fields.Many2one(
        related="move_id.partner_id",
        string="Supplier",
    )
    document_reference = fields.Char(
        related="move_id.ref",
        string="Supplier Reference",
    )
    invoice_date = fields.Date(
        related="move_id.invoice_date",
        string="Invoice Date",
    )
    currency_id = fields.Many2one(
        related="move_id.currency_id",
    )
    amount_total = fields.Monetary(
        related="move_id.amount_total",
        currency_field="currency_id",
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

    @api.depends("status", "attachment_id", "attempt_count")
    def _compute_can_retry(self):
        is_manager = self.env.user.has_group("account.group_account_manager")
        for reception in self:
            reception.can_retry = bool(
                is_manager
                and reception.status == "technical_error"
                and reception.attachment_id
                and reception.attempt_count < 5,
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

    def action_retry_processing(self):
        self.ensure_one()
        self.company_id._check_rebuild_einvoice_manager_access()
        if not self.can_retry:
            raise UserError(
                _("This document is not available for another processing attempt."),
            )
        reception = self.sudo()
        old_move = reception.move_id
        if (
            old_move
            and old_move.state == "draft"
            and not old_move.invoice_line_ids
        ):
            reception.move_id = False
            old_move.with_context(force_delete=True).unlink()

        proxy_user = (
            reception.company_id.account_edi_proxy_client_ids.filtered(
                lambda user: user.proxy_type == "pdp",
            )[:1]
            or self.env["account_edi_proxy_client.user"].sudo().new({
                "company_id": reception.company_id.id,
                "proxy_type": "pdp",
                "edi_mode": "demo",
            })
        )
        result = proxy_user.with_context(
            rebuild_einvoice_retry_reception_id=reception.id,
            rebuild_einvoice_is_test=reception.is_test,
        )._peppol_import_invoice(
            reception.attachment_id.sudo(),
            reception.provider_state or "done",
            reception.provider_message_uuid,
            journal=reception.company_id.peppol_purchase_journal_id,
        )
        if reception.status == "technical_error":
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Processing still needs attention"),
                    "message": reception.processing_summary,
                    "type": "warning",
                    "sticky": True,
                },
            }
        return (
            self.action_open_bill()
            if result.get("move")
            else self._get_records_action()
        )


class ResCompany(models.Model):
    _inherit = "res.company"

    rebuild_einvoice_provider = fields.Selection(
        [
            ("odoo_pdp", "Odoo Approved Platform"),
            ("other", "Different Approved Platform (not implemented)"),
        ],
        string="Approved Platform",
        help="The default French adapter uses Odoo's hosted Approved Platform service. No connection is made until production activation.",
        tracking=True,
    )
    rebuild_einvoice_provider_contract_status = fields.Selection(
        [
            ("not_verified", "Production onboarding required"),
            ("verified", "Identity verified"),
        ],
        compute="_compute_rebuild_einvoice_readiness",
        string="Production Onboarding",
        help="Derived from Odoo's production identity verification. Demo and test identities never count as production onboarding.",
    )
    rebuild_einvoice_environment = fields.Selection(
        [
            ("development", "Development or Test"),
            ("production", "Production"),
        ],
        string="Accounting Deployment",
        help="Live activation is allowed only on the deployed production system.",
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
    rebuild_einvoice_test_status = fields.Selection(
        [
            ("not_run", "Not yet verified"),
            ("passed", "Test passed"),
            ("failed", "Test needs attention"),
        ],
        string="Offline Reception Test",
        required=True,
        default="not_run",
        readonly=True,
        tracking=True,
    )
    rebuild_einvoice_tested_at = fields.Datetime(
        string="Last Tested At",
        readonly=True,
    )
    rebuild_einvoice_test_reception_id = fields.Many2one(
        "rebuild.einvoice.reception",
        string="Last Test Evidence",
        readonly=True,
    )
    rebuild_einvoice_capability_status = fields.Selection(
        [
            ("test_passed", "Test passed"),
            ("not_verified", "Not yet verified"),
            ("module_missing", "Configuration incomplete"),
        ],
        compute="_compute_rebuild_einvoice_readiness",
        string="Reception Validation",
    )
    rebuild_einvoice_readiness_status = fields.Selection(
        [
            ("configuration_incomplete", "Configuration incomplete"),
            ("not_verified", "Not yet verified"),
            ("ready_inactive", "Ready but inactive"),
            ("activation_required", "Production activation required"),
            ("active", "Active"),
        ],
        compute="_compute_rebuild_einvoice_readiness",
        string="Reception Readiness",
    )
    rebuild_einvoice_connection_status = fields.Selection(
        [
            ("inactive", "Inactive"),
            ("test", "Safe test ready"),
            ("registration_pending", "Registration pending"),
            ("connected_suspended", "Connected; retrieval suspended"),
            ("active", "Connected and receiving"),
            ("rejected", "Registration needs attention"),
        ],
        compute="_compute_rebuild_einvoice_readiness",
        string="Live Reception",
    )
    rebuild_einvoice_exchange_enabled = fields.Boolean(
        string="Scheduled Reception Enabled",
        compute="_compute_rebuild_einvoice_exchange_enabled",
    )
    rebuild_einvoice_blockers = fields.Text(
        string="Full Activation Checklist",
        compute="_compute_rebuild_einvoice_readiness",
    )
    rebuild_einvoice_next_action = fields.Char(
        string="Next Action",
        compute="_compute_rebuild_einvoice_readiness",
    )
    rebuild_einvoice_next_steps = fields.Html(
        string="Next Steps",
        compute="_compute_rebuild_einvoice_readiness",
        sanitize=True,
    )
    rebuild_einvoice_last_poll_at = fields.Datetime(
        string="Last Reception Check",
        readonly=True,
    )
    rebuild_einvoice_last_poll_status = fields.Selection(
        [
            ("not_run", "Not yet verified"),
            ("passed", "Last check passed"),
            ("authentication", "Authentication required"),
            ("temporary_failure", "Platform temporarily unavailable"),
        ],
        string="Platform Check",
        required=True,
        default="not_run",
        readonly=True,
    )
    rebuild_einvoice_last_poll_message = fields.Text(
        string="Platform Check Guidance",
        readonly=True,
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

    @api.model
    def _rebuild_apply_default_einvoice_provider(self):
        """Apply safe reception defaults without contacting any external service."""
        companies = self.sudo().search([
            ("account_fiscal_country_id.code", "=", "FR"),
        ])
        for company in companies:
            values = {"l10n_fr_pdp_send_to_ppf": False}
            if not company.rebuild_einvoice_provider:
                values["rebuild_einvoice_provider"] = "odoo_pdp"
            if not company.peppol_purchase_journal_id:
                purchase_journal = self.env["account.journal"].sudo().search([
                    ("company_id", "=", company.id),
                    ("type", "=", "purchase"),
                ], limit=1)
                if purchase_journal:
                    values["peppol_purchase_journal_id"] = purchase_journal.id
            if not company.account_peppol_contact_email and company.email:
                values["account_peppol_contact_email"] = company.email
            if not company.account_peppol_phone_number and company.phone:
                values["account_peppol_phone_number"] = company.phone
            suggested_identifier = (
                company.partner_id._get_suggested_pdp_identifier()
            )
            if (
                suggested_identifier
                and (
                    company.peppol_eas != "0225"
                    or not company.peppol_endpoint
                )
            ):
                values.update({
                    "peppol_eas": "0225",
                    "peppol_endpoint": suggested_identifier,
                })
            company.write(values)

        if not self._rebuild_einvoice_runtime_guard_enabled():
            self.env["ir.config_parameter"].sudo().set_str(
                "account_peppol.edi.mode",
                "demo",
            )

    @api.onchange("account_fiscal_country_id")
    def _onchange_rebuild_einvoice_provider(self):
        for company in self:
            if (
                company.account_fiscal_country_id.code == "FR"
                and not company.rebuild_einvoice_provider
            ):
                company.rebuild_einvoice_provider = "odoo_pdp"

    @api.model
    def _rebuild_einvoice_runtime_guard_enabled(self):
        return (
            os.getenv("USL_EINVOICE_LIVE_ENABLED", "").strip().lower()
            in TRUTHY_ENVIRONMENT_VALUES
        )

    def _rebuild_einvoice_configuration_blockers(
        self,
        *,
        include_provider=True,
    ):
        self.ensure_one()
        blockers = []
        if self.account_fiscal_country_id.code != "FR":
            blockers.append(_("Set the accounting fiscal country to France."))
        if not self.vat:
            blockers.append(_("Record the French VAT number."))
        if not self.company_registry:
            blockers.append(_("Record the SIREN or SIRET company identifier."))
        if self.peppol_eas != "0225" or not self.peppol_endpoint:
            blockers.append(
                _("Configure the French electronic-invoicing identifier (scheme 0225)."),
            )
        if not self.peppol_purchase_journal_id:
            blockers.append(_("Select the purchase journal for received invoices."))
        if include_provider:
            if not self.account_peppol_contact_email:
                blockers.append(_("Record the approved-platform contact email."))
            if not self.rebuild_einvoice_provider:
                blockers.append(_("Select the Odoo Approved Platform adapter."))
            elif self.rebuild_einvoice_provider != "odoo_pdp":
                blockers.append(
                    _(
                        "The selected platform is not supported by this release; "
                        "select Odoo Approved Platform.",
                    ),
                )
        return blockers

    def _rebuild_einvoice_production_blockers(self, *, include_onboarding=True):
        self.ensure_one()
        blockers = self._rebuild_einvoice_configuration_blockers()
        if not self._rebuild_einvoice_modules_ready():
            blockers.append(_("Install the required electronic-invoicing modules."))
        if self.rebuild_einvoice_test_status != "passed":
            blockers.append(_("Run the offline reception test and resolve any failure."))
        if (
            include_onboarding
            and self.rebuild_einvoice_provider_contract_status != "verified"
        ):
            blockers.append(
                _(
                    "Complete legal-representative identity verification and "
                    "accept the platform terms during production activation.",
                ),
            )
        if self.rebuild_einvoice_environment != "production":
            blockers.append(_("Perform activation only on the deployed production system."))
        if not self._rebuild_einvoice_runtime_guard_enabled():
            blockers.append(
                _(
                    "The production deployment has not authorized live reception.",
                ),
            )
        return blockers

    def _rebuild_einvoice_user_guidance(self, modules_ready):
        self.ensure_one()
        if not modules_ready:
            return (
                _("Install e-invoicing support"),
                [_("Install or upgrade the required Odoo e-invoicing modules.")],
            )

        setup_steps = self._rebuild_einvoice_configuration_blockers(
            include_provider=False,
        )
        if setup_steps:
            return _("Complete reception setup"), setup_steps

        if self.rebuild_einvoice_test_status != "passed":
            return (
                _("Test invoice reception"),
                [_("Run the offline reception test and inspect the draft bill.")],
            )

        platform_steps = self._rebuild_einvoice_configuration_blockers(
            include_provider=True,
        )
        if platform_steps:
            return _("Complete platform setup"), platform_steps

        if self.rebuild_einvoice_environment != "production":
            return (
                _("Continue during production deployment"),
                [
                    _(
                        "Deploy this release to production, rerun the offline "
                        "test there, and follow the activation runbook.",
                    ),
                ],
            )

        if not self._rebuild_einvoice_runtime_guard_enabled():
            return (
                _("Authorize live reception on the production host"),
                [
                    _(
                        "Enable the deployment-level reception guard after the "
                        "approved change window begins.",
                    ),
                ],
            )

        if not self.rebuild_einvoice_activation_approved:
            return (
                _("Approve production activation"),
                [
                    _(
                        "Ask an Accounting Manager to approve activation on "
                        "this production database.",
                    ),
                ],
            )

        if self.rebuild_einvoice_provider_contract_status != "verified":
            return (
                _("Complete production onboarding"),
                [
                    _(
                        "Authenticate the legal representative, accept the "
                        "platform terms and return to Odoo.",
                    ),
                ],
            )

        connection_status = self.rebuild_einvoice_connection_status
        if connection_status == "rejected":
            return (
                _("Resolve the platform registration"),
                [_("Review the platform rejection before trying registration again.")],
            )
        if connection_status == "registration_pending":
            return (
                _("Complete platform registration"),
                [_("Finish registration and verify the French directory effective date.")],
            )
        if connection_status == "inactive":
            return (
                _("Register the production receiver"),
                [_("Complete the approved-platform receiver registration in Settings.")],
            )
        if connection_status == "connected_suspended":
            return (
                _("Start scheduled reception"),
                [
                    _(
                        "Verify the first-invoice plan, then enable scheduled "
                        "reception.",
                    ),
                ],
            )
        return (
            _("No action required"),
            [_("Production reception is active and monitored.")],
        )

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
        "rebuild_einvoice_test_status",
        "account_peppol_proxy_state",
        "pdp_kyc_status",
        "account_edi_proxy_client_ids.active",
        "account_edi_proxy_client_ids.edi_mode",
        "account_edi_proxy_client_ids.proxy_type",
    )
    def _compute_rebuild_einvoice_readiness(self):
        modules_ready = self._rebuild_einvoice_modules_ready()
        for company in self:
            pdp_users = company.sudo().account_edi_proxy_client_ids.filtered(
                lambda user: user.proxy_type == "pdp",
            )
            production_user = pdp_users.filtered(
                lambda user: user.edi_mode == "prod",
            )[:1]
            test_user = pdp_users.filtered(
                lambda user: user.edi_mode in {"demo", "test"},
            )[:1]
            company.rebuild_einvoice_provider_contract_status = (
                "verified"
                if company.pdp_kyc_status == "success" and production_user
                else "not_verified"
            )
            configuration_blockers = (
                company._rebuild_einvoice_configuration_blockers(
                    include_provider=False,
                )
            )
            production_blockers = company._rebuild_einvoice_production_blockers()
            exchange_enabled = company.rebuild_einvoice_exchange_enabled
            raw_state = company.account_peppol_proxy_state

            if not modules_ready:
                company.rebuild_einvoice_capability_status = "module_missing"
            elif company.rebuild_einvoice_test_status == "passed":
                company.rebuild_einvoice_capability_status = "test_passed"
            else:
                company.rebuild_einvoice_capability_status = "not_verified"

            if test_user and raw_state == "receiver":
                company.rebuild_einvoice_connection_status = "test"
            elif raw_state == "receiver":
                company.rebuild_einvoice_connection_status = (
                    "active" if exchange_enabled else "connected_suspended"
                )
            elif raw_state in {"sender", "smp_registration"}:
                company.rebuild_einvoice_connection_status = "registration_pending"
            elif raw_state == "rejected":
                company.rebuild_einvoice_connection_status = "rejected"
            else:
                company.rebuild_einvoice_connection_status = "inactive"

            if exchange_enabled and raw_state == "receiver" and production_user:
                company.rebuild_einvoice_readiness_status = "active"
            elif configuration_blockers:
                company.rebuild_einvoice_readiness_status = (
                    "configuration_incomplete"
                )
            elif (
                not modules_ready
                or company.rebuild_einvoice_test_status != "passed"
            ):
                company.rebuild_einvoice_readiness_status = "not_verified"
            elif company.rebuild_einvoice_environment == "production":
                company.rebuild_einvoice_readiness_status = "activation_required"
            else:
                company.rebuild_einvoice_readiness_status = "ready_inactive"

            if not company.rebuild_einvoice_activation_approved:
                production_blockers.append(
                    _("Accounting Manager production approval is still required."),
                )
            company.rebuild_einvoice_blockers = "\n".join(
                f"• {blocker}" for blocker in dict.fromkeys(production_blockers)
            ) or _("No prerequisite remains; reception can be activated deliberately.")
            next_action, next_steps = company._rebuild_einvoice_user_guidance(
                modules_ready,
            )
            company.rebuild_einvoice_next_action = next_action
            company.rebuild_einvoice_next_steps = Markup(
                '<ul class="mb-0 ps-3">%s</ul>',
            ) % Markup().join(
                Markup("<li>%s</li>") % step
                for step in dict.fromkeys(next_steps)
            )

    @api.depends_context("uid")
    def _compute_rebuild_einvoice_exchange_enabled(self):
        crons = [
            cron.sudo()
            for xmlid in EINVOICE_RECEPTION_CRON_XMLIDS
            if (cron := self.env.ref(xmlid, raise_if_not_found=False))
        ]
        enabled = any(cron.active for cron in crons)
        for company in self:
            company.rebuild_einvoice_exchange_enabled = enabled

    def _check_rebuild_einvoice_manager_access(self):
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(
                _("Only an Accounting Manager can govern electronic-invoice activation."),
            )

    def _check_rebuild_einvoice_activation_ready(self):
        self.ensure_one()
        blockers = self._rebuild_einvoice_production_blockers(
            include_onboarding=False,
        )
        if not self.rebuild_einvoice_activation_approved:
            blockers.append(_("Production activation has not been approved."))
        if blockers:
            raise UserError(
                _("Electronic invoicing cannot contact a live platform:\n%s")
                % "\n".join(f"• {blocker}" for blocker in dict.fromkeys(blockers)),
            )

    def _rebuild_einvoice_live_call_allowed(self):
        self.ensure_one()
        return not (
            self._rebuild_einvoice_production_blockers(
                include_onboarding=False,
            )
            or not self.rebuild_einvoice_activation_approved
        )

    def action_rebuild_run_einvoice_acceptance_test(self):
        self.ensure_one()
        self._check_rebuild_einvoice_manager_access()
        blockers = self._rebuild_einvoice_configuration_blockers(
            include_provider=False,
        )
        if blockers:
            raise UserError(
                _("Complete the reception test prerequisites:\n%s")
                % "\n".join(f"• {blocker}" for blocker in blockers),
            )

        with file_open(EINVOICE_TEST_TEMPLATE, "rb") as fixture:
            tree = etree.fromstring(fixture.read())
        issue_date = fields.Date.context_today(self)
        due_date = fields.Date.add(issue_date, days=30)
        test_id = f"USL-SAFE-TEST-{issue_date}-{uuid_lib.uuid4().hex[:8]}"
        tree.find("{*}ID").text = test_id
        tree.find("{*}IssueDate").text = fields.Date.to_string(issue_date)
        tree.find("{*}DueDate").text = fields.Date.to_string(due_date)
        tree.find(".//{*}PaymentID").text = test_id
        customer = tree.find("{*}AccountingCustomerParty/{*}Party")
        customer.find("{*}EndpointID").text = self.peppol_endpoint
        customer.find("{*}PartyIdentification/{*}ID").text = (
            self.company_registry
        )
        customer.find("{*}PartyName/{*}Name").text = self.name
        customer.find("{*}PartyTaxScheme/{*}CompanyID").text = self.vat
        legal_entity = customer.find("{*}PartyLegalEntity")
        legal_entity.find("{*}RegistrationName").text = self.name
        legal_entity.find("{*}CompanyID").text = (
            (self.company_registry or "")[:9]
        )
        address = customer.find("{*}PostalAddress")
        address.find("{*}StreetName").text = self.street or "Safe test address"
        address.find("{*}CityName").text = self.city or "Paris"
        address.find("{*}PostalZone").text = self.zip or "75001"
        raw = etree.tostring(
            tree,
            xml_declaration=True,
            encoding="UTF-8",
        )

        attachment = self.env["ir.attachment"].create({
            "name": f"{test_id}.xml",
            "raw": raw,
            "mimetype": "application/xml",
        })
        message_reference = f"offline-test-{uuid_lib.uuid4()}"
        proxy_user = self.env["account_edi_proxy_client.user"].sudo().new({
            "company_id": self.id,
            "proxy_type": "pdp",
            "edi_mode": "demo",
        })
        result = proxy_user.with_context(
            rebuild_einvoice_is_test=True,
        )._peppol_import_invoice(
            attachment,
            "done",
            message_reference,
            journal=self.peppol_purchase_journal_id,
        )
        reception = self.env["rebuild.einvoice.reception"].search([
            ("company_id", "=", self.id),
            ("provider_message_uuid", "=", message_reference),
        ], limit=1)
        passed = bool(
            reception.status == "bill_created"
            and result.get("move")
            and result["move"].state == "draft",
        )
        self.sudo().write({
            "rebuild_einvoice_test_status": "passed" if passed else "failed",
            "rebuild_einvoice_tested_at": fields.Datetime.now(),
            "rebuild_einvoice_test_reception_id": reception.id,
        })
        if not passed:
            raise UserError(
                _(
                    "The offline test did not create a complete draft vendor bill. "
                    "Open the retained reception evidence and resolve the failure.",
                ),
            )
        return reception._get_records_action(
            name=_("Offline Reception Test Passed"),
            views=[(
                self.env.ref(
                    "rebuild_account_migration."
                    "view_rebuild_einvoice_reception_form",
                ).id,
                "form",
            )],
        )

    def action_rebuild_approve_einvoice_activation(self):
        self.ensure_one()
        self._check_rebuild_einvoice_manager_access()
        blockers = self._rebuild_einvoice_production_blockers(
            include_onboarding=False,
        )
        if blockers:
            raise UserError(
                _("Complete the production prerequisites before approval:\n%s")
                % "\n".join(f"• {blocker}" for blocker in blockers),
            )
        demo_users = self.sudo().account_edi_proxy_client_ids.filtered(
            lambda user: user.proxy_type == "pdp" and user.edi_mode == "demo",
        )
        if demo_users:
            demo_users.active = False
            self.sudo().write({
                "account_peppol_proxy_state": "not_registered",
                "l10n_fr_pdp_annuaire_start_date": False,
                "pdp_kyc_status": False,
                "pdp_authentication_uuid": False,
            })
        self.env["ir.config_parameter"].sudo().set_str(
            "account_peppol.edi.mode",
            "prod",
        )
        self.sudo().write({
            "rebuild_einvoice_activation_approved": True,
            "rebuild_einvoice_approved_by_id": self.env.user.id,
            "rebuild_einvoice_approved_at": fields.Datetime.now(),
        })

    def action_rebuild_revoke_einvoice_activation(self):
        self._check_rebuild_einvoice_manager_access()
        self._rebuild_set_einvoice_crons(False)
        self.sudo().write({
            "rebuild_einvoice_activation_approved": False,
            "rebuild_einvoice_approved_by_id": False,
            "rebuild_einvoice_approved_at": False,
        })

    def _rebuild_set_einvoice_crons(self, active):
        xmlids = (
            EINVOICE_RECEPTION_CRON_XMLIDS
            if active
            else EINVOICE_ALL_CRON_XMLIDS
        )
        for xmlid in xmlids:
            if cron := self.env.ref(xmlid, raise_if_not_found=False):
                cron.sudo().write({"active": active})

    def action_rebuild_enable_einvoice_exchange(self):
        self.ensure_one()
        self._check_rebuild_einvoice_manager_access()
        self._check_rebuild_einvoice_activation_ready()
        if self.rebuild_einvoice_provider_contract_status != "verified":
            raise UserError(
                _(
                    "Complete production identity verification before starting "
                    "scheduled reception.",
                ),
            )
        if self.account_peppol_proxy_state != "receiver":
            raise UserError(
                _("Complete approved-platform registration before enabling reception."),
            )
        production_user = self.sudo().account_edi_proxy_client_ids.filtered(
            lambda user: user.proxy_type == "pdp" and user.edi_mode == "prod",
        )
        if not production_user:
            raise UserError(
                _("Complete the production approved-platform connection first."),
            )
        connected_companies = self.search([
            ("account_peppol_proxy_state", "=", "receiver"),
        ])
        unauthorized = connected_companies.filtered(
            lambda company: not company._rebuild_einvoice_live_call_allowed(),
        )
        if unauthorized:
            raise UserError(_(
                "Scheduled reception is database-wide. Review and approve every "
                "connected company first: %s",
                ", ".join(unauthorized.mapped("display_name")),
            ))
        self._rebuild_set_einvoice_crons(True)

    def action_rebuild_suspend_einvoice_exchange(self):
        self._check_rebuild_einvoice_manager_access()
        self._rebuild_set_einvoice_crons(False)

    def _rebuild_record_einvoice_poll_result(
        self,
        status,
        message=False,
    ):
        self.sudo().write({
            "rebuild_einvoice_last_poll_at": fields.Datetime.now(),
            "rebuild_einvoice_last_poll_status": status,
            "rebuild_einvoice_last_poll_message": message,
        })

    def _refresh_pdp_authentication_status(self, send_bus=True):
        self.ensure_one()
        self._check_rebuild_einvoice_activation_ready()
        return super()._refresh_pdp_authentication_status(send_bus=send_bus)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    rebuild_einvoice_provider = fields.Selection(
        related="company_id.rebuild_einvoice_provider",
        readonly=False,
    )
    rebuild_einvoice_provider_contract_status = fields.Selection(
        related="company_id.rebuild_einvoice_provider_contract_status",
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
        if (
            self.company_id._get_peppol_edi_mode() == "demo"
            and self.env.context.get("rebuild_einvoice_safe_demo")
        ):
            self.company_id._check_rebuild_einvoice_manager_access()
        else:
            self.company_id._check_rebuild_einvoice_activation_ready()
        return super().action_open_pdp_form()

    def action_open_peppol_form(self):
        self.ensure_one()
        if self.company_id.account_fiscal_country_id.code == "FR":
            self.company_id._check_rebuild_einvoice_activation_ready()
        return super().action_open_peppol_form()

    def button_peppol_deregister(self):
        self.ensure_one()
        self.company_id._check_rebuild_einvoice_activation_ready()
        result = super().button_peppol_deregister()
        self.company_id._rebuild_set_einvoice_crons(False)
        return result


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
        return super(
            PdpRegistration,
            self.sudo(),
        ).button_register_pdp_participant()

    def button_deregister_pdp_participant(self):
        self._rebuild_check_live_action()
        result = super(
            PdpRegistration,
            self.sudo(),
        ).button_deregister_pdp_participant()
        self.company_id._rebuild_set_einvoice_crons(False)
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
        except Exception as error:  # ruff: ignore[blind-except]
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

    def _pdp_get_regulatory_documents(self, batch_size=None):
        if not self._rebuild_ereporting_live_enabled():
            return None
        return super()._pdp_get_regulatory_documents(batch_size=batch_size)

    def _pdp_send_lifecycles(self, batch_size=None):
        if not self._rebuild_ereporting_live_enabled():
            return None
        return super()._pdp_send_lifecycles(batch_size=batch_size)

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

            document_hash = hashlib.sha256(attachment.raw or b"").hexdigest()
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
            result = super()._peppol_import_invoice(
                processing_attachment,
                peppol_state,
                uuid,
                journal=journal,
            )
        except Exception as error:  # ruff: ignore[blind-except]
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
                attachment.raw or b"",
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

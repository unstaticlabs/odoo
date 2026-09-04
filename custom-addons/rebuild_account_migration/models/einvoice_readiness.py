import hashlib
import logging
import os
import uuid as uuid_lib
from datetime import timedelta

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
    "l10n_fr_pdp.ir_cron_pdp_get_regulatory_documents",
)
EINVOICE_RECEPTION_STATUSES = [
    ("received", "Processing"),
    ("bill_created", "Ready for Review"),
    ("rejected", "Rejected"),
    ("duplicate", "Duplicate Ignored"),
    ("technical_error", "Needs Attention"),
]
EINVOICE_TEST_TEMPLATE = (
    "rebuild_account_migration/static/src/einvoice/"
    "representative_ubl_invoice.xml"
)
TRUTHY_ENVIRONMENT_VALUES = {"1", "true", "yes", "on"}
EINVOICE_SELF_CHECK_VERSION = "2026-07-31.1"
_logger = logging.getLogger(__name__)


class _EinvoiceSelfCheckRollback(Exception):
    """Rollback a successful self-check without retaining business records."""


def _rollback_einvoice_self_check():
    raise _EinvoiceSelfCheckRollback


def _ensure_einvoice_self_check_result(passed):
    if not passed:
        raise UserError(
            _(
                "The self-check did not create a complete draft vendor bill.",
            ),
        )


def _ensure_einvoice_decoder_result(decoder_info, failure_reason=False):
    if not decoder_info or decoder_info.get("priority", 0) <= 0:
        raise UserError(
            _("No supported decoder recognized the sample invoice."),
        )
    if failure_reason:
        raise UserError(failure_reason)


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
    rebuild_einvoice_production_prepared_by_id = fields.Many2one(
        "res.users",
        string="Production Prepared By",
        readonly=True,
        copy=False,
    )
    rebuild_einvoice_production_prepared_at = fields.Datetime(
        string="Production Prepared At",
        readonly=True,
        copy=False,
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
    rebuild_einvoice_test_fingerprint = fields.Char(
        string="Tested Configuration",
        readonly=True,
        copy=False,
    )
    rebuild_einvoice_test_summary = fields.Char(
        string="Self-Check Result",
        readonly=True,
        copy=False,
    )
    rebuild_einvoice_test_reception_id = fields.Many2one(
        "rebuild.einvoice.reception",
        string="Last Test Evidence",
        readonly=True,
    )
    rebuild_einvoice_test_current = fields.Boolean(
        compute="_compute_rebuild_einvoice_readiness",
        string="Self-Check Current",
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
            ("configuration_incomplete", "Needs setup"),
            ("not_verified", "Ready to test"),
            ("ready_inactive", "Ready for production"),
            ("activation_required", "Activation required"),
            ("registration_in_progress", "Registration in progress"),
            ("active", "Receiving"),
            ("needs_attention", "Needs attention"),
        ],
        compute="_compute_rebuild_einvoice_readiness",
        string="Status",
    )
    rebuild_einvoice_connection_status = fields.Selection(
        [
            ("inactive", "Inactive"),
            ("test", "Safe test ready"),
            ("registration_pending", "Registration in progress"),
            ("connected_suspended", "Connected; retrieval suspended"),
            ("active", "Connected and receiving"),
            ("rejected", "Registration needs attention"),
        ],
        compute="_compute_rebuild_einvoice_readiness",
        string="Live Reception",
    )
    rebuild_einvoice_exchange_enabled = fields.Boolean(
        string="Incoming Invoices Enabled",
        readonly=True,
        tracking=True,
        default=False,
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
    rebuild_einvoice_reception_count = fields.Integer(
        compute="_compute_rebuild_einvoice_counts",
        string="Incoming E-Invoices",
    )
    rebuild_einvoice_attention_count = fields.Integer(
        compute="_compute_rebuild_einvoice_counts",
        string="Needs Attention",
    )

    def _compute_rebuild_einvoice_counts(self):
        grouped = self.env["rebuild.einvoice.reception"].sudo()._read_group(
            [("company_id", "in", self.ids)],
            ["company_id", "status"],
            ["__count"],
        )
        totals = {}
        attention = {}
        for company, status, count in grouped:
            totals[company.id] = totals.get(company.id, 0) + count
            if status in {"rejected", "technical_error"}:
                attention[company.id] = attention.get(company.id, 0) + count
        for company in self:
            company.rebuild_einvoice_reception_count = totals.get(company.id, 0)
            company.rebuild_einvoice_attention_count = attention.get(
                company.id,
                0,
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
        """Seed reception defaults without rewriting governed onboarding state.

        Runtime guards prevent external traffic during upgrades and restores. They
        must not turn that temporary process constraint into a persistent company
        deactivation; explicit product actions govern that state.
        """
        companies = self.sudo().search([
            ("account_fiscal_country_id.code", "=", "FR"),
        ])
        for company in companies:
            values = {}
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
                and not company.peppol_eas
                and not company.peppol_endpoint
            ):
                values.update({
                    "peppol_eas": "0225",
                    "peppol_endpoint": suggested_identifier,
                })
            if not company._rebuild_einvoice_runtime_guard_enabled():
                values.update({
                    "l10n_fr_pdp_send_to_ppf": False,
                    "l10n_fr_pdp_pilot_phase": False,
                })
            if values:
                company.write(values)
        companies._rebuild_cleanup_legacy_einvoice_self_checks()

    def _rebuild_cleanup_legacy_einvoice_self_checks(self):
        """Remove only untouched draft bills retained by the former self-check."""
        for company in self.sudo():
            legacy_evidence = self.env[
                "rebuild.einvoice.reception"
            ].sudo().search([
                ("company_id", "=", company.id),
                ("is_test", "=", True),
                ("provider_message_uuid", "=like", "offline-test-%"),
            ])
            candidate_bills = legacy_evidence.move_id
            linked_bill_ids = [
                evidence.attachment_id.res_id
                for evidence in legacy_evidence
                if evidence.attachment_id.res_model == "account.move"
                and evidence.attachment_id.res_id
            ]
            candidate_bills |= self.env["account.move"].sudo().browse(
                linked_bill_ids,
            ).exists()
            candidate_bills |= self.env["account.move"].sudo().search([
                ("company_id", "=", company.id),
                ("move_type", "=", "in_invoice"),
                ("state", "=", "draft"),
                ("ref", "=like", "USL-SAFE-TEST-%"),
            ])
            if not legacy_evidence and not candidate_bills:
                continue
            untouched_bills = candidate_bills.filtered(
                lambda bill: (
                    bill.state == "draft"
                    and (bill.ref or "").startswith("USL-SAFE-TEST-")
                    and bill.move_type == "in_invoice"
                    and len(bill.invoice_line_ids) == 2
                    and bill.ubl_cii_xml_id
                    and (
                        bill.ubl_cii_xml_id.name or ""
                    ).startswith("USL-SAFE-TEST-")
                    and bill.currency_id.compare_amounts(
                        bill.amount_total,
                        175.0,
                    ) == 0
                ),
            )
            modified_bills = candidate_bills - untouched_bills
            company.write({
                "rebuild_einvoice_test_status": "not_run",
                "rebuild_einvoice_tested_at": False,
                "rebuild_einvoice_test_fingerprint": False,
                "rebuild_einvoice_test_reception_id": False,
                "rebuild_einvoice_test_summary": (
                    _(
                        "A prior retained test bill was modified and remains "
                        "available for manual review.",
                    )
                    if modified_bills
                    else _(
                        "Previous retained self-check data was removed. Run "
                        "the non-polluting self-check again.",
                    )
                ),
            })
            removable_evidence = legacy_evidence.filtered(
                lambda evidence: (
                    not evidence.move_id
                    or evidence.move_id in untouched_bills
                    or (
                        evidence.attachment_id.res_model == "account.move"
                        and evidence.attachment_id.res_id in untouched_bills.ids
                    )
                ),
            )
            removable_evidence.unlink()
            untouched_bills.with_context(force_delete=True).unlink()

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

    def _rebuild_einvoice_configuration_fingerprint(self):
        self.ensure_one()
        values = (
            EINVOICE_SELF_CHECK_VERSION,
            self.id,
            self.account_fiscal_country_id.id,
            self.vat or "",
            self.company_registry or "",
            self.peppol_eas or "",
            self.peppol_endpoint or "",
            self.peppol_purchase_journal_id.id,
            (self.account_peppol_contact_email or "").strip().lower(),
            self.rebuild_einvoice_provider or "",
        )
        return hashlib.sha256(
            "\x1f".join(map(str, values)).encode(),
        ).hexdigest()

    def _rebuild_einvoice_self_check_is_current(self):
        self.ensure_one()
        return bool(
            self.rebuild_einvoice_test_status == "passed"
            and self.rebuild_einvoice_test_fingerprint
            == self._rebuild_einvoice_configuration_fingerprint(),
        )

    def _rebuild_einvoice_production_mode_is_configured(self):
        """Return whether native PDP onboarding is configured for production.

        Odoo treats a missing value as production. A stored demo/test value is
        deployment configuration and must never be changed by a company-level
        onboarding action.
        """
        self.ensure_one()
        configured_mode = self.env["ir.config_parameter"].sudo().get_str(
            "account_peppol.edi.mode",
        )
        return not configured_mode or configured_mode == "prod"

    def _rebuild_einvoice_production_blockers(self, *, include_onboarding=True):
        self.ensure_one()
        blockers = self._rebuild_einvoice_configuration_blockers()
        if not self._rebuild_einvoice_modules_ready():
            blockers.append(_("Install the required electronic-invoicing modules."))
        if not self._rebuild_einvoice_self_check_is_current():
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
        if not self._rebuild_einvoice_production_mode_is_configured():
            blockers.append(
                _(
                    "The deployment is configured for safe demo onboarding. "
                    "Configure the native PDP mode for production before activation.",
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

        if not self._rebuild_einvoice_self_check_is_current():
            return (
                _("Run the reception self-check"),
                [
                    _(
                        "Verify that a representative electronic invoice can "
                        "become a complete draft bill without contacting a provider.",
                    ),
                ],
            )

        platform_steps = self._rebuild_einvoice_configuration_blockers(
            include_provider=True,
        )
        if platform_steps:
            return _("Complete platform setup"), platform_steps

        if self.rebuild_einvoice_environment != "production":
            return (
                _("Prepare production activation"),
                [
                    _(
                        "Mark this company as eligible for production onboarding. "
                        "This does not contact or register with the platform, start "
                        "invoice reception, enable e-reporting, or send regulatory "
                        "data.",
                    ),
                ],
            )

        if not self._rebuild_einvoice_runtime_guard_enabled():
            return (
                _("Authorize the production connection"),
                [
                    _(
                        "Enable the deployment-level reception guard after the "
                        "approved change window begins.",
                    ),
                ],
            )

        if not self.rebuild_einvoice_activation_approved:
            return (
                _("Activate reception"),
                [
                    _(
                        "Ask an Accounting Manager to approve activation on "
                        "this production database.",
                    ),
                ],
            )

        if self.rebuild_einvoice_provider_contract_status != "verified":
            return (
                _("Complete platform activation"),
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
                _("Registration in progress"),
                [
                    _(
                        "Odoo's Approved Platform is registering this company in "
                        "the French directory. Reception can be enabled after the "
                        "native status becomes Receiver.",
                    ),
                ],
            )
        if connection_status == "inactive":
            return (
                _("Register the production receiver"),
                [_("Complete the approved-platform receiver registration in Settings.")],
            )
        if connection_status == "connected_suspended":
            return (
                _("Start receiving invoices"),
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
        "rebuild_einvoice_test_fingerprint",
        "rebuild_einvoice_exchange_enabled",
        "rebuild_einvoice_last_poll_status",
        "rebuild_einvoice_reception_ids.status",
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
                    include_provider=True,
                )
            )
            production_blockers = company._rebuild_einvoice_production_blockers()
            exchange_enabled = company.rebuild_einvoice_exchange_enabled
            raw_state = company.account_peppol_proxy_state
            test_current = company._rebuild_einvoice_self_check_is_current()
            company.rebuild_einvoice_test_current = test_current

            if not modules_ready:
                company.rebuild_einvoice_capability_status = "module_missing"
            elif test_current:
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

            connection_needs_attention = (
                raw_state == "rejected"
                or company.rebuild_einvoice_last_poll_status
                in {"authentication", "temporary_failure"}
                or any(
                    reception.status in {"rejected", "technical_error"}
                    for reception in company.rebuild_einvoice_reception_ids
                )
            )
            if connection_needs_attention:
                company.rebuild_einvoice_readiness_status = "needs_attention"
            elif exchange_enabled and raw_state == "receiver" and production_user:
                company.rebuild_einvoice_readiness_status = "active"
            elif configuration_blockers:
                company.rebuild_einvoice_readiness_status = (
                    "configuration_incomplete"
                )
            elif (
                not modules_ready
                or not test_current
            ):
                company.rebuild_einvoice_readiness_status = "not_verified"
            elif raw_state == "smp_registration":
                company.rebuild_einvoice_readiness_status = (
                    "registration_in_progress"
                )
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

    def _check_rebuild_einvoice_manager_access(self):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(
                _("Only an Accounting Manager can govern electronic-invoice activation."),
            )
        if self.id not in self.env.user.company_ids.ids:
            raise AccessError(
                _(
                    "You cannot govern electronic-invoice activation for a company "
                    "outside your allowed companies.",
                ),
            )
        self.check_access("read")

    def _rebuild_einvoice_preparation_blockers(self):
        self.ensure_one()
        blockers = self._rebuild_einvoice_configuration_blockers(
            include_provider=True,
        )
        if not self._rebuild_einvoice_modules_ready():
            blockers.append(_("Install the required electronic-invoicing modules."))
        if not self._rebuild_einvoice_self_check_is_current():
            blockers.append(_("Run the offline reception test and resolve any failure."))
        if not self._rebuild_einvoice_runtime_guard_enabled():
            blockers.append(
                _("The production deployment has not authorized live reception."),
            )
        if not self._rebuild_einvoice_production_mode_is_configured():
            blockers.append(
                _(
                    "The deployment is configured for safe demo onboarding. "
                    "Configure the native PDP mode for production before activation.",
                ),
            )
        return blockers

    def action_rebuild_prepare_einvoice_activation(self):
        self.ensure_one()
        self._check_rebuild_einvoice_manager_access()
        if self.rebuild_einvoice_environment == "production":
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Production activation already prepared"),
                    "message": _(
                        "No setting changed. Continue with the separate reception "
                        "activation step when ready.",
                    ),
                    "type": "info",
                    "next": {"type": "ir.actions.client", "tag": "soft_reload"},
                },
            }

        blockers = self._rebuild_einvoice_preparation_blockers()
        if blockers:
            raise UserError(
                _("Production activation cannot be prepared:\n%s")
                % "\n".join(f"• {blocker}" for blocker in dict.fromkeys(blockers)),
            )

        self.sudo().write({
            "rebuild_einvoice_environment": "production",
            "rebuild_einvoice_production_prepared_by_id": self.env.user.id,
            "rebuild_einvoice_production_prepared_at": fields.Datetime.now(),
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Production activation prepared"),
                "message": _(
                    "The company is eligible for production onboarding. No platform "
                    "was contacted and invoice reception remains inactive.",
                ),
                "type": "success",
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

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
            include_provider=True,
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

        passed = False
        failure_summary = False
        try:
            with self.env.cr.savepoint():
                attachment = self.env["ir.attachment"].sudo().create({
                    "name": f"{test_id}.xml",
                    "raw": raw,
                    "mimetype": "application/xml",
                })
                bill = self.env["account.move"].sudo().with_company(self).create({
                    "journal_id": self.peppol_purchase_journal_id.id,
                    "move_type": "in_invoice",
                })
                file_data = bill._to_files_data(attachment)[0]
                decoder_info = bill._get_edi_decoder(file_data, new=True)
                _ensure_einvoice_decoder_result(decoder_info)
                reason_cannot_decode = decoder_info["decoder"](
                    bill,
                    file_data,
                    True,
                )
                _ensure_einvoice_decoder_result(
                    decoder_info,
                    reason_cannot_decode,
                )
                attachment.write({
                    "res_model": bill._name,
                    "res_id": bill.id,
                })
                passed = bool(
                    bill.state == "draft"
                    and bill.move_type == "in_invoice"
                    and bill.partner_id
                    and len(bill.invoice_line_ids) == 2
                    and bill.currency_id == self.currency_id
                    and bill.invoice_line_ids.tax_ids
                    and bill.ubl_cii_xml_id == attachment
                    and attachment.res_model == bill._name
                    and attachment.res_id == bill.id
                    and self.currency_id.compare_amounts(
                        bill.amount_total,
                        175.0,
                    ) == 0,
                )
                _ensure_einvoice_self_check_result(passed)
                _rollback_einvoice_self_check()
        except _EinvoiceSelfCheckRollback:
            passed = True
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Electronic-invoice self-check failed for company %s",
                self.id,
            )
            failure_summary = _(
                "The representative invoice could not be validated. Review "
                "the company identity, journal and accounting setup, then "
                "try again.",
            )

        self.sudo().write({
            "rebuild_einvoice_test_status": "passed" if passed else "failed",
            "rebuild_einvoice_tested_at": fields.Datetime.now(),
            "rebuild_einvoice_test_fingerprint": (
                self._rebuild_einvoice_configuration_fingerprint()
                if passed
                else False
            ),
            "rebuild_einvoice_test_summary": (
                _("Reception self-check passed; no test bill was retained.")
                if passed
                else failure_summary or _("Reception self-check needs attention.")
            ),
            "rebuild_einvoice_test_reception_id": False,
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": (
                    _("Reception is ready")
                    if passed
                    else _("Reception self-check needs attention")
                ),
                "message": self.rebuild_einvoice_test_summary,
                "type": "success" if passed else "warning",
                "sticky": not passed,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_rebuild_begin_einvoice_activation(self):
        self.ensure_one()
        self._check_rebuild_einvoice_manager_access()
        if self.rebuild_einvoice_environment != "production":
            raise UserError(
                _(
                    "Activate reception only from the deployed production "
                    "system. This development database remains safely inactive.",
                ),
            )
        if not self._rebuild_einvoice_runtime_guard_enabled():
            raise UserError(
                _(
                    "The production deployment has not authorized the "
                    "connection yet.",
                ),
            )
        if not self.rebuild_einvoice_activation_approved:
            self.action_rebuild_approve_einvoice_activation()
        settings = self.env["res.config.settings"].create({
            "company_id": self.id,
        })
        return settings.action_open_peppol_form()

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
        self.sudo().write({
            "rebuild_einvoice_activation_approved": True,
            "rebuild_einvoice_approved_by_id": self.env.user.id,
            "rebuild_einvoice_approved_at": fields.Datetime.now(),
        })

    def action_rebuild_revoke_einvoice_activation(self):
        self._check_rebuild_einvoice_manager_access()
        self.sudo().write({
            "rebuild_einvoice_environment": "development",
            "rebuild_einvoice_production_prepared_by_id": False,
            "rebuild_einvoice_production_prepared_at": False,
            "rebuild_einvoice_activation_approved": False,
            "rebuild_einvoice_approved_by_id": False,
            "rebuild_einvoice_approved_at": False,
            "rebuild_einvoice_exchange_enabled": False,
        })

    def _rebuild_check_einvoice_reception_crons(self):
        missing = []
        inactive = []
        for xmlid in EINVOICE_RECEPTION_CRON_XMLIDS:
            cron = self.env.ref(xmlid, raise_if_not_found=False)
            if not cron:
                missing.append(xmlid)
                continue
            cron = cron.sudo()
            if not cron.active:
                inactive.append(cron.display_name)
        if missing or inactive:
            details = [
                *(
                    self.env._("Missing scheduled action: %s", xmlid)
                    for xmlid in missing
                ),
                *(
                    self.env._("Inactive scheduled action: %s", name)
                    for name in inactive
                ),
            ]
            raise UserError(
                _(
                    "Production reception scheduling is not ready. Ask a system "
                    "administrator to apply the production cron policy:\n%s",
                )
                % "\n".join(f"• {detail}" for detail in details),
            )

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
        self._rebuild_check_einvoice_reception_crons()
        self.sudo().rebuild_einvoice_exchange_enabled = True

    def action_rebuild_suspend_einvoice_exchange(self):
        self._check_rebuild_einvoice_manager_access()
        self.sudo().rebuild_einvoice_exchange_enabled = False

    def action_rebuild_check_einvoice_now(self):
        self.ensure_one()
        self._check_rebuild_einvoice_manager_access()
        if (
            not self.rebuild_einvoice_exchange_enabled
            or self.account_peppol_proxy_state != "receiver"
        ):
            raise UserError(_("Incoming invoices are not active for this company."))
        self.peppol_purchase_journal_id.button_fetch_in_einvoices()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Reception checked"),
                "message": _(
                    "New documents and status updates have been requested from "
                    "the Approved Platform.",
                ),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_rebuild_review_einvoice_issues(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "rebuild_account_migration.action_rebuild_einvoice_reception",
        )
        action.update({
            "domain": [
                ("company_id", "=", self.id),
                ("status", "in", ["rejected", "technical_error"]),
            ],
            "context": {
                "search_default_attention": 1,
                "create": False,
                "delete": False,
            },
        })
        return action

    def action_rebuild_open_einvoice_history(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "rebuild_account_migration.action_rebuild_einvoice_reception",
        )
        action.update({
            "domain": [("company_id", "=", self.id)],
            "context": {
                "search_default_attention": 0,
                "create": False,
                "delete": False,
            },
        })
        return action

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
        self.company_id.sudo().rebuild_einvoice_exchange_enabled = False
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

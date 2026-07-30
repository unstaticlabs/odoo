import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class UslPlatformBillingPlatform(models.Model):
    _name = "usl.platform.billing.platform"
    _description = "Content Platform Billing Configuration"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name, id"
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True, index=True)
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Main Partner",
        required=True,
        check_company=True,
        ondelete="restrict",
        tracking=True,
    )
    customer_partner_id = fields.Many2one(
        "res.partner",
        check_company=True,
        ondelete="restrict",
        tracking=True,
        help="Leave empty to use the main partner on customer invoices.",
    )
    supplier_partner_id = fields.Many2one(
        "res.partner",
        check_company=True,
        ondelete="restrict",
        tracking=True,
        help="Leave empty to use the main partner on commission bills.",
    )
    commission_rate = fields.Float(
        string="Commission %",
        required=True,
        default=20.0,
        tracking=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Platform Currency",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    revenue_product_id = fields.Many2one(
        "product.product",
        required=True,
        check_company=True,
        ondelete="restrict",
        tracking=True,
    )
    commission_product_id = fields.Many2one(
        "product.product",
        required=True,
        check_company=True,
        ondelete="restrict",
        tracking=True,
    )
    sale_journal_id = fields.Many2one(
        "account.journal",
        required=True,
        check_company=True,
        domain="[('type', '=', 'sale'), ('company_id', '=', company_id)]",
        ondelete="restrict",
        tracking=True,
    )
    purchase_journal_id = fields.Many2one(
        "account.journal",
        required=True,
        check_company=True,
        domain="[('type', '=', 'purchase'), ('company_id', '=', company_id)]",
        ondelete="restrict",
        tracking=True,
    )
    compensation_journal_id = fields.Many2one(
        "account.journal",
        check_company=True,
        domain="[('type', '=', 'general'), ('company_id', '=', company_id)]",
        ondelete="restrict",
        tracking=True,
    )
    bank_journal_id = fields.Many2one(
        "account.journal",
        check_company=True,
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]",
        ondelete="restrict",
        tracking=True,
    )
    bank_label_pattern = fields.Char(
        tracking=True,
        help=(
            "Expected bank label pattern. Use {ref} where the platform "
            "reference occurs, for example: OF inv {ref}."
        ),
    )
    bank_label_keywords = fields.Text(
        tracking=True,
        help="Comma-separated fallback keywords used only after pattern and partner matching.",
    )
    bank_match_days_tolerance = fields.Integer(
        string="Date Tolerance (days)",
        default=15,
        tracking=True,
    )
    bank_match_amount_tolerance = fields.Monetary(
        string="Bank Amount Tolerance",
        currency_field="company_currency_id",
        default=1.0,
        tracking=True,
    )
    company_currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
    )
    analytic_distribution = fields.Json(
        string="Analytic Distribution",
        tracking=True,
    )
    vendor_bill_grouping_mode = fields.Selection(
        [
            ("monthly", "One bill per session"),
            ("per_payout", "One bill per payout"),
        ],
        required=True,
        default="monthly",
        tracking=True,
    )
    auto_post_invoices = fields.Boolean(
        string="Post invoices and bills during generation",
        tracking=True,
    )
    auto_create_compensation = fields.Boolean(
        string="Create compensation entry",
        default=True,
        tracking=True,
    )

    _name_company_unique = models.Constraint(
        "UNIQUE(company_id, name)",
        "A platform name must be unique within a company.",
    )

    @api.constrains("commission_rate")
    def _check_commission_rate(self):
        for platform in self:
            if not 0.0 < platform.commission_rate < 100.0:
                raise ValidationError(
                    _("The commission rate must be strictly between 0% and 100%."),
                )

    @api.constrains("bank_match_days_tolerance", "bank_match_amount_tolerance")
    def _check_matching_tolerances(self):
        for platform in self:
            if (
                platform.bank_match_days_tolerance < 0
                or platform.bank_match_amount_tolerance < 0
            ):
                raise ValidationError(_("Bank matching tolerances cannot be negative."))

    @api.constrains(
        "company_id",
        "partner_id",
        "customer_partner_id",
        "supplier_partner_id",
        "revenue_product_id",
        "commission_product_id",
        "sale_journal_id",
        "purchase_journal_id",
        "compensation_journal_id",
        "bank_journal_id",
    )
    def _check_company_configuration(self):
        for platform in self:
            records = (
                platform.partner_id,
                platform.customer_partner_id,
                platform.supplier_partner_id,
                platform.revenue_product_id,
                platform.commission_product_id,
                platform.sale_journal_id,
                platform.purchase_journal_id,
                platform.compensation_journal_id,
                platform.bank_journal_id,
            )
            if any(
                record
                and record.company_id
                and record.company_id != platform.company_id
                for record in records
            ):
                raise ValidationError(
                    _("Every platform setting must belong to the platform company."),
                )
            journal_types = (
                (platform.sale_journal_id, "sale"),
                (platform.purchase_journal_id, "purchase"),
                (platform.compensation_journal_id, "general"),
                (platform.bank_journal_id, "bank"),
            )
            if any(
                journal and journal.type != expected_type
                for journal, expected_type in journal_types
            ):
                raise ValidationError(
                    _("Each configured journal must have the required accounting type."),
                )

    @api.constrains("bank_label_pattern")
    def _check_bank_label_pattern(self):
        for platform in self.filtered("bank_label_pattern"):
            if platform.bank_label_pattern.count("{ref}") > 1:
                raise ValidationError(
                    _("The bank label pattern can contain {ref} only once."),
                )
            try:
                platform._bank_label_regex()
            except re.error as error:
                raise ValidationError(
                    _("The bank label pattern is invalid: %s", error),
                ) from error

    @property
    def customer_partner(self):
        self.ensure_one()
        return self.customer_partner_id or self.partner_id

    @property
    def supplier_partner(self):
        self.ensure_one()
        return self.supplier_partner_id or self.partner_id

    def _bank_label_regex(self):
        self.ensure_one()
        if not self.bank_label_pattern:
            return False
        escaped = re.escape(self.bank_label_pattern)
        marker = re.escape("{ref}")
        if marker in escaped:
            escaped = escaped.replace(marker, r"(?P<ref>[A-Za-z0-9._/-]+)")
        return re.compile(escaped, re.IGNORECASE)

    def _bank_keywords(self):
        self.ensure_one()
        return [
            keyword.strip().casefold()
            for keyword in (self.bank_label_keywords or "").split(",")
            if len(keyword.strip()) >= 3
        ]

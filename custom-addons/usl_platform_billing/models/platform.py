import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class UslPlatformBillingPlatform(models.Model):
    _name = "usl.platform.billing.platform"
    _description = "Content Platform Billing Configuration"
    _inherit = ["mail.thread", "mail.activity.mixin", "analytic.mixin"]
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
        domain="[('sale_ok', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        ondelete="restrict",
        tracking=True,
    )
    commission_product_id = fields.Many2one(
        "product.product",
        required=True,
        check_company=True,
        domain="[('purchase_ok', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id)]",
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
    revenue_account_id = fields.Many2one(
        "account.account",
        string="Revenue Account",
        compute="_compute_effective_accounts",
        help="Effective default account from the revenue product. A fiscal position may remap it on the invoice.",
    )
    commission_account_id = fields.Many2one(
        "account.account",
        string="Commission Account",
        compute="_compute_effective_accounts",
        help="Effective default account from the commission product. A fiscal position may remap it on the bill.",
    )
    customer_receivable_account_id = fields.Many2one(
        "account.account",
        string="Customer Receivable Account",
        compute="_compute_effective_accounts",
    )
    supplier_payable_account_id = fields.Many2one(
        "account.account",
        string="Supplier Payable Account",
        compute="_compute_effective_accounts",
    )
    bank_account_id = fields.Many2one(
        "account.account",
        string="Bank Account",
        compute="_compute_effective_accounts",
    )

    _name_company_unique = models.Constraint(
        "UNIQUE(company_id, name)",
        "A platform name must be unique within a company.",
    )

    @api.depends(
        "company_id",
        "revenue_product_id",
        "commission_product_id",
        "partner_id",
        "customer_partner_id",
        "supplier_partner_id",
        "bank_journal_id",
    )
    def _compute_effective_accounts(self):
        for platform in self:
            company = platform.company_id or self.env.company
            revenue_product = platform.revenue_product_id.with_company(company)
            commission_product = platform.commission_product_id.with_company(company)
            customer = platform.customer_partner.with_company(company)
            supplier = platform.supplier_partner.with_company(company)
            platform.revenue_account_id = (
                revenue_product.product_tmpl_id.get_product_accounts().get("income")
                if revenue_product
                else False
            )
            platform.commission_account_id = (
                commission_product.product_tmpl_id.get_product_accounts().get("expense")
                if commission_product
                else False
            )
            platform.customer_receivable_account_id = (
                customer.property_account_receivable_id if customer else False
            )
            platform.supplier_payable_account_id = (
                supplier.property_account_payable_id if supplier else False
            )
            platform.bank_account_id = platform.bank_journal_id.default_account_id

    def _account_configuration_errors(self):
        self.ensure_one()
        errors = []
        checks = (
            (
                self.revenue_account_id,
                {"income", "income_other"},
                _("Revenue product account"),
            ),
            (
                self.commission_account_id,
                {
                    "expense",
                    "expense_other",
                    "expense_depreciation",
                    "expense_direct_cost",
                },
                _("Commission product account"),
            ),
            (
                self.customer_receivable_account_id,
                {"asset_receivable"},
                _("Customer receivable account"),
            ),
            (
                self.supplier_payable_account_id,
                {"liability_payable"},
                _("Supplier payable account"),
            ),
        )
        for account, allowed_types, label in checks:
            if not account:
                errors.append(_("%(label)s is missing.", label=label))
            elif account.account_type not in allowed_types:
                errors.append(
                    _(
                        "%(label)s (%(account)s) has an incompatible account type.",
                        label=label,
                        account=account.display_name,
                    ),
                )
        if self.bank_journal_id and (
            not self.bank_account_id
            or self.bank_account_id.account_type
            not in {"asset_cash", "liability_credit_card"}
        ):
            errors.append(_("The bank journal needs a valid liquidity account."))
        return errors

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

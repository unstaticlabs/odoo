import hashlib

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .constants import MAPPING_STATES, SOURCE_PROVIDERS


class B2cProductAlias(models.Model):
    _name = "b2c.product.alias"
    _description = "External B2C Product and SKU Alias"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "mapping_state, source_provider, original_sku, id"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="cascade",
    )
    channel_id = fields.Many2one(
        "b2c.channel",
        check_company=True,
        index=True,
        ondelete="restrict",
    )
    source_provider = fields.Selection(SOURCE_PROVIDERS, required=True, index=True)
    original_sku = fields.Char(index=True, copy=False)
    source_sku_is_unique = fields.Boolean(
        string="Source SKU uniquely identifies a variant",
        default=True,
        required=True,
        copy=False,
        help=(
            "Clear this only when provider evidence proves that the source reused "
            "one generic SKU for several distinct variants. The original SKU is "
            "preserved, while product matching also uses the exact source name and "
            "variation."
        ),
    )
    original_name = fields.Char(copy=False)
    original_variation = fields.Text(copy=False)
    external_listing_id = fields.Char(index=True, copy=False)
    alias_key = fields.Char(
        compute="_compute_alias_key",
        store=True,
        index=True,
        readonly=True,
    )
    mapping_state = fields.Selection(
        MAPPING_STATES,
        required=True,
        default="pending",
        index=True,
        tracking=True,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Verified Product",
        check_company=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    suggested_product_id = fields.Many2one(
        "product.product",
        string="Suggested Product",
        check_company=True,
        ondelete="restrict",
        help=(
            "Suggestions are advisory. A product is assigned only through a "
            "governed, evidence-backed verification."
        ),
    )
    evidence_note = fields.Text()
    evidence_id = fields.Many2one(
        "b2c.provider.evidence",
        check_company=True,
        ondelete="restrict",
        copy=False,
    )
    reviewed_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    reviewed_at = fields.Datetime(readonly=True, copy=False)
    line_ids = fields.One2many("b2c.order.line", "alias_id", string="Order Lines")

    _company_alias_key_unique = models.Constraint(
        "UNIQUE(company_id, alias_key)",
        "An external product/SKU alias must be unique per company.",
    )

    @api.depends(
        "source_provider",
        "channel_id",
        "original_sku",
        "source_sku_is_unique",
        "external_listing_id",
        "original_name",
        "original_variation",
    )
    def _compute_alias_key(self):
        for alias in self:
            sku = (alias.original_sku or "").strip()
            listing = (alias.external_listing_id or "").strip()
            fallback = not sku or not alias.source_sku_is_unique
            payload = "\x1f".join(
                [
                    alias.source_provider or "",
                    str(alias.channel_id.id or 0),
                    sku,
                    listing,
                    (alias.original_name or "").strip() if fallback else "",
                    (alias.original_variation or "").strip() if fallback else "",
                ],
            )
            alias.alias_key = hashlib.sha256(payload.encode(), usedforsecurity=False).hexdigest()

    @api.constrains(
        "original_sku",
        "external_listing_id",
        "original_name",
        "original_variation",
    )
    def _check_business_identifier(self):
        for alias in self:
            has_provider_id = bool(alias.original_sku or alias.external_listing_id)
            has_exact_fallback = bool(
                alias.original_name and alias.original_variation,
            )
            if not (has_provider_id or has_exact_fallback):
                raise ValidationError(
                    self.env._(
                        "A product alias requires a source SKU, a listing "
                        "identifier, or an exact source product name and variation.",
                    ),
                )

    @api.constrains("mapping_state", "product_id")
    def _check_mapping_state(self):
        for alias in self:
            if alias.mapping_state == "verified" and not alias.product_id:
                raise ValidationError(
                    self.env._("A verified SKU alias requires a product."),
                )
            if alias.mapping_state == "rejected" and alias.product_id:
                raise ValidationError(
                    self.env._("A rejected SKU alias cannot retain a verified product."),
                )

    def action_verify(self):
        for alias in self:
            product = alias.product_id or alias.suggested_product_id
            if not product:
                raise ValidationError(
                    self.env._("Select the verified product before verifying this alias."),
                )
            alias.write(
                {
                    "product_id": product.id,
                    "mapping_state": "verified",
                    "reviewed_by_id": self.env.user.id,
                    "reviewed_at": fields.Datetime.now(),
                },
            )
            alias.line_ids.write(
                {"product_id": product.id, "mapping_state": "verified"},
            )

    def action_reject(self):
        self.write(
            {
                "product_id": False,
                "mapping_state": "rejected",
                "reviewed_by_id": self.env.user.id,
                "reviewed_at": fields.Datetime.now(),
            },
        )

    def action_reset_pending(self):
        self.write(
            {
                "product_id": False,
                "mapping_state": "pending",
                "reviewed_by_id": False,
                "reviewed_at": False,
            },
        )

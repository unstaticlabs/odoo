from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .constants import (
    HISTORICAL_B2C_COMMUNICATION_PARAMETER,
    HISTORICAL_B2C_MATERIALIZATION_CONTEXT,
    SOURCE_PROVIDERS,
)


COMMUNICATION_PARAMETER = HISTORICAL_B2C_COMMUNICATION_PARAMETER
MATERIALIZATION_CONTEXT = HISTORICAL_B2C_MATERIALIZATION_CONTEXT


def _guard_provenance_create(recordset, values_list, protected_fields):
    if not recordset.env.su and any(
        protected_fields.intersection(values) for values in values_list
    ):
        raise AccessError(
            recordset.env._(
                "Historical B2C provenance is maintained by the audited importer."
            ),
        )


def _guard_provenance_write(recordset, values, protected_fields):
    if protected_fields.intersection(values) and not recordset.env.su:
        raise AccessError(recordset.env._("Historical B2C provenance is immutable."))


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    allow_historical_b2c_customer_communication = fields.Boolean(
        string="Allow communication with historical B2C customers",
        config_parameter=COMMUNICATION_PARAMETER,
        groups="usl_b2c.group_b2c_manager",
        help=(
            "Allow customer-facing messages from reconstructed historical Sales "
            "orders. Invoice creation remains blocked because their Accounting "
            "history was restored separately."
        ),
    )


class B2cPartnerIdentity(models.Model):
    _name = "b2c.partner.identity"
    _description = "B2C Provider Contact Identity"
    _order = "source_provider, external_customer_id, id"
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        ondelete="cascade",
        default=lambda self: self.env.company,
    )
    source_provider = fields.Selection(
        selection=SOURCE_PROVIDERS,
        required=True,
        index=True,
        readonly=True,
    )
    external_customer_id = fields.Char(index=True, readonly=True, copy=False)
    identity_role = fields.Selection(
        [("customer", "Customer"), ("delivery", "Delivery recipient")],
        required=True,
        readonly=True,
    )
    identity_digest = fields.Char(required=True, index=True, readonly=True, copy=False)
    partner_id = fields.Many2one(
        "res.partner",
        required=True,
        check_company=True,
        index=True,
        ondelete="restrict",
        readonly=True,
    )
    evidence_id = fields.Many2one(
        "b2c.provider.evidence",
        check_company=True,
        ondelete="restrict",
        readonly=True,
    )

    _company_provider_identity_unique = models.Constraint(
        "UNIQUE(company_id, source_provider, identity_digest)",
        "A provider contact identity must be unique per company.",
    )

    @api.model_create_multi
    def create(self, values_list):
        if not self.env.su:
            raise AccessError(self.env._("Provider identities are maintained by the audited B2C importer."))
        return super().create(values_list)

    def write(self, values):
        if not self.env.su:
            raise AccessError(self.env._("Provider identities are immutable."))
        return super().write(values)

    def unlink(self):
        raise AccessError(self.env._("Provider identities are immutable."))


class ResPartner(models.Model):
    _inherit = "res.partner"

    _usl_materialization_fields = frozenset({"usl_historical_b2c_contact"})

    usl_historical_b2c_contact = fields.Boolean(
        string="Historical B2C contact",
        index=True,
        copy=False,
        readonly=True,
        help="Created from an immutable provider identity during B2C reconstruction.",
    )
    b2c_identity_ids = fields.One2many(
        "b2c.partner.identity",
        "partner_id",
        string="Provider identities",
        readonly=True,
    )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        return super().write(values)


class B2cOrderProvenance(models.Model):
    _inherit = "b2c.order"

    _usl_materialization_fields = frozenset(
        {"sale_order_id", "partner_identity_id", "partner_id", "shipping_partner_id"},
    )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        return super().write(values)


class B2cOrderLineProvenance(models.Model):
    _inherit = "b2c.order.line"

    _usl_materialization_fields = frozenset(
        {"sale_order_line_id", "production_ids", "stock_move_ids"},
    )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        return super().write(values)


class B2cFulfilmentEventProvenance(models.Model):
    _inherit = "b2c.fulfilment.event"

    _usl_materialization_fields = frozenset(
        {
            "stock_picking_id",
            "stock_picking_ids",
            "stock_move_id",
            "stock_move_ids",
            "sale_order_line_ids",
            "purchase_order_id",
            "production_order_id",
        },
    )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        return super().write(values)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    _usl_materialization_fields = frozenset(
        {
            "usl_b2c_order_id",
            "usl_historical_b2c",
            "usl_historical_b2c_completed",
            "usl_historical_source_warning",
            "usl_source_payment_state",
            "usl_source_fulfilment_state",
            "usl_source_total",
        },
    )

    usl_b2c_order_id = fields.Many2one(
        "b2c.order",
        string="B2C source order",
        check_company=True,
        ondelete="restrict",
        copy=False,
        index=True,
        readonly=True,
    )
    usl_historical_b2c = fields.Boolean(
        string="Historical B2C order",
        index=True,
        copy=False,
        readonly=True,
    )
    usl_historical_b2c_completed = fields.Boolean(
        string="Historical fulfilment complete",
        index=True,
        copy=False,
        readonly=True,
    )
    usl_historical_source_warning = fields.Char(copy=False, readonly=True)
    usl_source_payment_state = fields.Char(copy=False, readonly=True)
    usl_source_fulfilment_state = fields.Char(copy=False, readonly=True)
    usl_source_total = fields.Monetary(
        string="Provider total",
        currency_field="currency_id",
        readonly=True,
        copy=False,
    )

    _b2c_source_order_unique = models.Constraint(
        "UNIQUE(usl_b2c_order_id)",
        "A canonical B2C order can be promoted only once.",
    )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def _historical_communication_allowed(self):
        return self.env["ir.config_parameter"].sudo().get_bool(COMMUNICATION_PARAMETER)

    def _assert_historical_communication_allowed(self):
        if self.filtered("usl_historical_b2c") and not self._historical_communication_allowed():
            raise UserError(
                self.env._(
                    "Communication from historical B2C orders is disabled in Settings."
                ),
            )

    def action_quotation_send(self):
        self._assert_historical_communication_allowed()
        return super().action_quotation_send()

    def action_confirm(self):
        self._assert_historical_communication_allowed()
        return super().action_confirm()

    def _send_order_notification_mail(self, mail_template, allow_deferred_sending=True):
        if self.usl_historical_b2c and not self._historical_communication_allowed():
            return None
        return super()._send_order_notification_mail(
            mail_template,
            allow_deferred_sending=allow_deferred_sending,
        )

    def _send_payment_succeeded_for_order_mail(self):
        allowed = self.filtered(
            lambda order: not order.usl_historical_b2c
            or order._historical_communication_allowed()
        )
        return super(SaleOrder, allowed)._send_payment_succeeded_for_order_mail()

    def _send_order_confirmation_mail(self):
        allowed = self.filtered(
            lambda order: not order.usl_historical_b2c
            or order._historical_communication_allowed()
        )
        return super(SaleOrder, allowed)._send_order_confirmation_mail()

    def _create_invoices(self, grouped=False, final=False, date=None):
        if self.filtered("usl_historical_b2c"):
            raise UserError(
                self.env._(
                    "Historical B2C orders cannot create invoices because their "
                    "Accounting was reconstructed separately."
                ),
            )
        return super()._create_invoices(grouped=grouped, final=final, date=date)

    def _compute_invoice_status(self):
        super()._compute_invoice_status()
        self.filtered("usl_historical_b2c").invoice_status = "no"

    def message_post(self, **kwargs):
        if self.filtered("usl_historical_b2c") and not self.env.context.get(
            MATERIALIZATION_CONTEXT,
        ):
            subtype_xmlid = kwargs.get("subtype_xmlid")
            is_internal_note = subtype_xmlid in {"mail.mt_note", "mail.mt_comment_internal"}
            has_external_recipients = bool(kwargs.get("partner_ids"))
            force_email = bool(kwargs.get("email_layout_xmlid") or self.env.context.get("force_email"))
            if (has_external_recipients or force_email) and not is_internal_note:
                self._assert_historical_communication_allowed()
        return super().message_post(**kwargs)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        protected = {
            "partner_id",
            "partner_invoice_id",
            "partner_shipping_id",
            "date_order",
            "currency_id",
            "order_line",
            "state",
        }
        locked = self.filtered(
            lambda order: order.usl_historical_b2c_completed
            and not order.env.context.get(MATERIALIZATION_CONTEXT)
        )
        if locked and protected.intersection(values):
            raise UserError(
                self.env._(
                    "Completed historical B2C orders are locked. Use their source "
                    "evidence for audit corrections."
                ),
            )
        return super().write(values)

    def unlink(self):
        if self.filtered("usl_historical_b2c"):
            raise UserError(self.env._("Historical B2C orders cannot be deleted."))
        return super().unlink()


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    _usl_materialization_fields = frozenset(
        {"usl_b2c_order_line_id", "usl_provider_line_total", "usl_provider_adjustment"},
    )

    usl_b2c_order_line_id = fields.Many2one(
        "b2c.order.line",
        string="B2C source line",
        check_company=True,
        ondelete="restrict",
        copy=False,
        index=True,
        readonly=True,
    )
    usl_provider_line_total = fields.Monetary(
        string="Provider line total",
        currency_field="currency_id",
        readonly=True,
        copy=False,
    )
    usl_provider_adjustment = fields.Boolean(readonly=True, copy=False)

    _b2c_source_line_unique = models.Constraint(
        "UNIQUE(usl_b2c_order_line_id)",
        "A canonical B2C line can be promoted only once.",
    )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        protected = {
            "product_id",
            "name",
            "product_uom_qty",
            "product_uom_id",
            "price_unit",
            "discount",
            "tax_ids",
        }
        locked = self.filtered(
            lambda line: line.order_id.usl_historical_b2c_completed
            and not line.env.context.get(MATERIALIZATION_CONTEXT)
        )
        if locked and protected.intersection(values):
            raise UserError(self.env._("Completed historical B2C order lines are locked."))
        return super().write(values)

    def unlink(self):
        if self.filtered("order_id.usl_historical_b2c"):
            raise UserError(self.env._("Historical B2C order lines cannot be deleted."))
        return super().unlink()


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    _usl_materialization_fields = frozenset(
        {"usl_historical_b2c", "usl_b2c_source_key", "usl_b2c_evidence_id"},
    )

    usl_historical_b2c = fields.Boolean(readonly=True, copy=False, index=True)
    usl_b2c_source_key = fields.Char(readonly=True, copy=False, index=True)
    usl_b2c_evidence_id = fields.Many2one(
        "b2c.provider.evidence",
        check_company=True,
        ondelete="restrict",
        readonly=True,
    )

    _b2c_purchase_source_unique = models.Constraint(
        "UNIQUE(company_id, usl_b2c_source_key)",
        "A documented B2C acquisition can be promoted only once.",
    )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        return super().write(values)


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    _usl_materialization_fields = frozenset({"usl_source_bill_line_ids"})

    usl_source_bill_line_ids = fields.Many2many(
        "account.move.line",
        "usl_b2c_purchase_bill_evidence_rel",
        "purchase_line_id",
        "bill_line_id",
        string="Source vendor-bill lines",
        readonly=True,
        copy=False,
        help=(
            "Immutable evidence relation used when a source bill aggregated several "
            "physical products and Odoo's one-to-one purchase-line link cannot "
            "represent that fact."
        ),
    )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        return super().write(values)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    _usl_materialization_fields = frozenset(
        {"usl_historical_b2c", "usl_b2c_source_key", "usl_b2c_order_id"},
    )

    usl_historical_b2c = fields.Boolean(readonly=True, copy=False, index=True)
    usl_b2c_source_key = fields.Char(readonly=True, copy=False, index=True)
    usl_b2c_order_id = fields.Many2one(
        "b2c.order",
        check_company=True,
        ondelete="restrict",
        readonly=True,
        copy=False,
    )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        return super().write(values)


class StockMove(models.Model):
    _inherit = "stock.move"

    _usl_materialization_fields = frozenset(
        {"usl_b2c_order_line_id", "usl_b2c_source_key"},
    )

    usl_b2c_order_line_id = fields.Many2one(
        "b2c.order.line",
        check_company=True,
        ondelete="restrict",
        readonly=True,
        copy=False,
        index=True,
    )
    usl_b2c_source_key = fields.Char(readonly=True, copy=False, index=True)

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        return super().write(values)


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    _usl_materialization_fields = frozenset(
        {"usl_b2c_order_line_id", "usl_b2c_source_key"},
    )

    usl_b2c_order_line_id = fields.Many2one(
        "b2c.order.line",
        check_company=True,
        ondelete="restrict",
        readonly=True,
        copy=False,
        index=True,
    )
    usl_b2c_source_key = fields.Char(readonly=True, copy=False, index=True)

    _b2c_production_source_unique = models.Constraint(
        "UNIQUE(company_id, usl_b2c_source_key)",
        "A historical B2C production demand can be promoted only once.",
    )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        return super().write(values)


class MrpUnbuild(models.Model):
    _inherit = "mrp.unbuild"

    _usl_materialization_fields = frozenset(
        {"usl_historical_b2c", "usl_b2c_source_key"},
    )

    usl_historical_b2c = fields.Boolean(readonly=True, copy=False, index=True)
    usl_b2c_source_key = fields.Char(readonly=True, copy=False, index=True)

    _b2c_unbuild_source_unique = models.Constraint(
        "UNIQUE(company_id, usl_b2c_source_key)",
        "A historical supplier-pack conversion can be promoted only once.",
    )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        return super().write(values)


class StockLandedCost(models.Model):
    _inherit = "stock.landed.cost"

    _usl_materialization_fields = frozenset(
        {"usl_historical_b2c", "usl_b2c_source_key"},
    )

    usl_historical_b2c = fields.Boolean(readonly=True, copy=False, index=True)
    usl_b2c_source_key = fields.Char(readonly=True, copy=False, index=True)

    _b2c_landed_cost_source_unique = models.Constraint(
        "UNIQUE(company_id, usl_b2c_source_key)",
        "A documented historical landed cost can be promoted only once.",
    )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        return super().write(values)

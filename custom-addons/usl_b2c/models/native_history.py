from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .constants import (
    HISTORICAL_B2C_COMMUNICATION_PARAMETER,
    HISTORICAL_B2C_MATERIALIZATION_CONTEXT,
    SOURCE_PROVIDERS,
)


COMMUNICATION_PARAMETER = HISTORICAL_B2C_COMMUNICATION_PARAMETER
MATERIALIZATION_CONTEXT = HISTORICAL_B2C_MATERIALIZATION_CONTEXT
MATERIALIZATION_TOKEN = object()


def _is_materialization(recordset):
    """Return whether trusted importer code owns this operation."""
    return (
        recordset.env.su
        and recordset.env.context.get(MATERIALIZATION_CONTEXT)
        is MATERIALIZATION_TOKEN
    )


def _guard_provenance_create(recordset, values_list, protected_fields):
    if not _is_materialization(recordset) and any(
        protected_fields.intersection(values) for values in values_list
    ):
        raise AccessError(
            recordset.env._(
                "Historical B2C provenance is maintained by the audited importer."
            ),
        )


def _guard_provenance_write(recordset, values, protected_fields):
    if protected_fields.intersection(values) and not _is_materialization(recordset):
        raise AccessError(recordset.env._("Historical B2C provenance is immutable."))


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    allow_historical_b2c_customer_communication = fields.Boolean(
        string="Allow communication with historical B2C customers",
        config_parameter=COMMUNICATION_PARAMETER,
        groups="base.group_system",
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

    @api.private
    @api.model_create_multi
    def create(self, values_list):
        if not _is_materialization(self):
            raise AccessError(self.env._("Provider identities are maintained by the audited B2C importer."))
        return super().create(values_list)

    @api.private
    def write(self, values):
        if not _is_materialization(self):
            raise AccessError(self.env._("Provider identities are immutable."))
        return super().write(values)

    @api.private
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

    def _get_default_payment_link_values(self):
        if self.usl_historical_b2c:
            raise UserError(
                self.env._(
                    "Historical B2C orders cannot create payment links because "
                    "their payments were reconstructed separately."
                ),
            )
        return super()._get_default_payment_link_values()

    def _compute_invoice_status(self):
        super()._compute_invoice_status()
        self.filtered("usl_historical_b2c").invoice_status = "no"

    def message_post(self, **kwargs):
        if self.filtered("usl_historical_b2c") and not _is_materialization(self):
            subtype_xmlid = kwargs.get("subtype_xmlid")
            is_internal_note = subtype_xmlid in {"mail.mt_note", "mail.mt_comment_internal"}
            has_external_recipients = bool(
                kwargs.get("partner_ids") or kwargs.get("outgoing_email_to")
            )
            force_email = bool(kwargs.get("email_layout_xmlid") or self.env.context.get("force_email"))
            if (has_external_recipients or force_email) and not is_internal_note:
                self._assert_historical_communication_allowed()
        return super().message_post(**kwargs)

    def _notify_get_recipients(self, message, msg_vals=False, **kwargs):
        recipients = super()._notify_get_recipients(
            message,
            msg_vals=msg_vals,
            **kwargs,
        )
        if self.usl_historical_b2c and not self._historical_communication_allowed():
            # Filtering at the final recipient boundary covers pre-existing
            # followers, template-driven recipients and direct email addresses.
            # Internal Odoo users still receive normal inbox notifications.
            return [
                recipient
                for recipient in recipients
                if recipient.get("uid") and not recipient.get("ushare")
            ]
        return recipients

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        historical = self.filtered("usl_historical_b2c")
        if historical and "transaction_ids" in values:
            raise UserError(
                self.env._(
                    "Historical B2C orders cannot be linked to payment transactions."
                ),
            )
        protected = {
            "company_id",
            "name",
            "origin",
            "client_order_ref",
            "partner_id",
            "partner_invoice_id",
            "partner_shipping_id",
            "date_order",
            "commitment_date",
            "validity_date",
            "currency_id",
            "pricelist_id",
            "fiscal_position_id",
            "payment_term_id",
            "warehouse_id",
            "user_id",
            "team_id",
            "note",
            "order_line",
            "state",
        }
        locked = self.filtered(
            lambda order: order.usl_historical_b2c_completed
            and not _is_materialization(order)
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
        if not _is_materialization(self):
            order_ids = {
                values["order_id"]
                for values in values_list
                if values.get("order_id")
            }
            if self.env["sale.order"].browse(order_ids).filtered(
                "usl_historical_b2c_completed"
            ):
                raise UserError(
                    self.env._("Completed historical B2C order lines are locked."),
                )
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        protected = {
            "order_id",
            "sequence",
            "display_type",
            "product_id",
            "name",
            "product_uom_qty",
            "product_uom_id",
            "price_unit",
            "discount",
            "tax_ids",
            "customer_lead",
            "route_id",
            "product_packaging_id",
            "product_packaging_qty",
            "analytic_distribution",
        }
        locked = self.filtered(
            lambda line: line.order_id.usl_historical_b2c_completed
            and not _is_materialization(line)
        )
        if locked and protected.intersection(values):
            raise UserError(self.env._("Completed historical B2C order lines are locked."))
        return super().write(values)

    def unlink(self):
        if self.filtered("order_id.usl_historical_b2c"):
            raise UserError(self.env._("Historical B2C order lines cannot be deleted."))
        return super().unlink()


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    def _usl_historical_sales_from_commands(self, commands):
        order_ids = self._fields["sale_order_ids"].convert_to_cache(commands, self)
        return self.env["sale.order"].browse(order_ids).exists().filtered(
            "usl_historical_b2c"
        )

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            commands = values.get("sale_order_ids")
            if commands and self._usl_historical_sales_from_commands(commands):
                raise UserError(
                    self.env._(
                        "Historical B2C orders cannot create payment transactions."
                    ),
                )
        return super().create(values_list)

    def write(self, values):
        commands = values.get("sale_order_ids")
        if commands:
            for transaction in self:
                if transaction._usl_historical_sales_from_commands(commands):
                    raise UserError(
                        self.env._(
                            "Historical B2C orders cannot be linked to payment "
                            "transactions."
                        ),
                    )
        return super().write(values)


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
        protected = {
            "company_id",
            "partner_id",
            "currency_id",
            "date_order",
            "date_planned",
            "origin",
            "partner_ref",
            "user_id",
            "picking_type_id",
            "fiscal_position_id",
            "payment_term_id",
            "order_line",
            "state",
        }
        historical = self.filtered(
            lambda order: order.usl_historical_b2c
            and not _is_materialization(order)
        )
        if historical and protected.intersection(values):
            raise UserError(
                self.env._(
                    "Historical B2C purchase orders are locked. Use their source "
                    "vendor evidence for audit corrections."
                ),
            )
        return super().write(values)

    def action_rfq_send(self):
        if self.filtered("usl_historical_b2c"):
            raise UserError(
                self.env._(
                    "Historical B2C purchase orders cannot send supplier emails."
                ),
            )
        return super().action_rfq_send()

    def message_post(self, **kwargs):
        historical = self.filtered("usl_historical_b2c")
        if historical and not _is_materialization(self):
            subtype_xmlid = kwargs.get("subtype_xmlid")
            is_internal_note = subtype_xmlid in {
                "mail.mt_note",
                "mail.mt_comment_internal",
            }
            if (
                kwargs.get("partner_ids")
                or kwargs.get("outgoing_email_to")
                or kwargs.get("email_layout_xmlid")
                or self.env.context.get("force_email")
            ) and not is_internal_note:
                raise UserError(
                    self.env._(
                        "Historical B2C purchase orders cannot contact suppliers."
                    ),
                )
        return super().message_post(**kwargs)

    def _notify_get_recipients(self, message, msg_vals=False, **kwargs):
        recipients = super()._notify_get_recipients(
            message,
            msg_vals=msg_vals,
            **kwargs,
        )
        if self.usl_historical_b2c:
            return [
                recipient
                for recipient in recipients
                if recipient.get("uid") and not recipient.get("ushare")
            ]
        return recipients

    def unlink(self):
        if self.filtered("usl_historical_b2c"):
            raise UserError(
                self.env._("Historical B2C purchase orders cannot be deleted."),
            )
        return super().unlink()


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
        if not _is_materialization(self):
            order_ids = {
                values["order_id"]
                for values in values_list
                if values.get("order_id")
            }
            if self.env["purchase.order"].browse(order_ids).filtered(
                "usl_historical_b2c"
            ):
                raise UserError(
                    self.env._(
                        "Historical B2C purchase order lines are locked."
                    ),
                )
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        protected = {
            "order_id",
            "sequence",
            "display_type",
            "product_id",
            "name",
            "product_qty",
            "uom_id",
            "price_unit",
            "tax_ids",
            "date_planned",
            "analytic_distribution",
        }
        historical = self.filtered(
            lambda line: line.order_id.usl_historical_b2c
            and not _is_materialization(line)
        )
        if historical and protected.intersection(values):
            raise UserError(
                self.env._("Historical B2C purchase order lines are locked."),
            )
        return super().write(values)

    def unlink(self):
        if self.filtered("order_id.usl_historical_b2c"):
            raise UserError(
                self.env._(
                    "Historical B2C purchase order lines cannot be deleted."
                ),
            )
        return super().unlink()


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

    _b2c_picking_source_unique = models.Constraint(
        "UNIQUE(company_id, usl_b2c_source_key)",
        "A historical B2C stock operation can be promoted only once.",
    )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        protected = {
            "company_id",
            "partner_id",
            "picking_type_id",
            "location_id",
            "location_dest_id",
            "scheduled_date",
            "date_done",
            "origin",
            "move_ids",
            "move_line_ids",
            "state",
        }
        completed = self.filtered(
            lambda picking: picking.usl_historical_b2c
            and picking.state == "done"
            and not _is_materialization(picking)
        )
        if completed and protected.intersection(values):
            raise UserError(
                self.env._("Completed historical B2C stock operations are locked."),
            )
        return super().write(values)

    def unlink(self):
        if self.filtered("usl_historical_b2c"):
            raise UserError(
                self.env._("Historical B2C stock operations cannot be deleted."),
            )
        return super().unlink()


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

    def _usl_is_historical_b2c_move(self):
        self.ensure_one()
        return bool(
            self.usl_b2c_source_key
            or self.picking_id.usl_historical_b2c
            or self.raw_material_production_id.usl_b2c_source_key
            or self.production_id.usl_b2c_source_key
            or self.unbuild_id.usl_historical_b2c
            or self.consume_unbuild_id.usl_historical_b2c
        )

    @api.model_create_multi
    def create(self, values_list):
        _guard_provenance_create(self, values_list, self._usl_materialization_fields)
        if not _is_materialization(self):
            picking_ids = {
                values["picking_id"]
                for values in values_list
                if values.get("picking_id")
            }
            production_ids = {
                values.get(field_name)
                for values in values_list
                for field_name in (
                    "raw_material_production_id",
                    "production_id",
                )
            }
            production_ids.discard(None)
            production_ids.discard(False)
            unbuild_ids = {
                values.get(field_name)
                for values in values_list
                for field_name in ("unbuild_id", "consume_unbuild_id")
            }
            unbuild_ids.discard(None)
            unbuild_ids.discard(False)
            completed_picking = self.env["stock.picking"].browse(picking_ids).filtered(
                lambda picking: picking.usl_historical_b2c
                and picking.state == "done"
            )
            completed_production = self.env["mrp.production"].browse(
                production_ids,
            ).filtered(
                lambda production: production.usl_b2c_source_key
                and production.state == "done"
            )
            completed_unbuild = self.env["mrp.unbuild"].browse(unbuild_ids).filtered(
                lambda unbuild: unbuild.usl_historical_b2c
                and unbuild.state == "done"
            )
            if completed_picking or completed_production or completed_unbuild:
                raise UserError(
                    self.env._("Completed historical B2C stock moves are locked."),
                )
        return super().create(values_list)

    def write(self, values):
        _guard_provenance_write(self, values, self._usl_materialization_fields)
        protected = {
            "company_id",
            "picking_id",
            "product_id",
            "product_uom_qty",
            "quantity",
            "uom_id",
            "location_id",
            "location_dest_id",
            "date",
            "state",
            "purchase_line_id",
            "sale_line_id",
            "move_line_ids",
        }
        completed = self.filtered(
            lambda move: move._usl_is_historical_b2c_move()
            and move.state == "done"
            and not _is_materialization(move)
        )
        if completed and protected.intersection(values):
            raise UserError(
                self.env._("Completed historical B2C stock moves are locked."),
            )
        return super().write(values)

    def unlink(self):
        if self.filtered(lambda move: move._usl_is_historical_b2c_move()):
            raise UserError(
                self.env._("Historical B2C stock moves cannot be deleted."),
            )
        return super().unlink()


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    @api.model_create_multi
    def create(self, values_list):
        if not _is_materialization(self):
            move_ids = {
                values["move_id"]
                for values in values_list
                if values.get("move_id")
            }
            completed = self.env["stock.move"].browse(move_ids).filtered(
                lambda move: move.state == "done"
                and move._usl_is_historical_b2c_move()
            )
            if completed:
                raise UserError(
                    self.env._("Completed historical B2C stock move lines are locked."),
                )
        return super().create(values_list)

    def write(self, values):
        protected = {
            "move_id",
            "product_id",
            "quantity",
            "uom_id",
            "location_id",
            "location_dest_id",
            "lot_id",
            "lot_name",
            "package_id",
            "result_package_id",
            "owner_id",
            "date",
            "state",
        }
        completed = self.filtered(
            lambda line: line.move_id.state == "done"
            and line.move_id._usl_is_historical_b2c_move()
            and not _is_materialization(line)
        )
        if completed and protected.intersection(values):
            raise UserError(
                self.env._("Completed historical B2C stock move lines are locked."),
            )
        return super().write(values)

    def unlink(self):
        completed = self.filtered(
            lambda line: line.move_id.state == "done"
            and line.move_id._usl_is_historical_b2c_move()
        )
        if completed:
            raise UserError(
                self.env._("Completed historical B2C stock move lines cannot be deleted."),
            )
        return super().unlink()


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
        protected = {
            "company_id",
            "product_id",
            "product_qty",
            "uom_id",
            "bom_id",
            "date_start",
            "date_finished",
            "location_src_id",
            "location_dest_id",
            "move_raw_ids",
            "move_finished_ids",
            "state",
        }
        completed = self.filtered(
            lambda production: production.usl_b2c_source_key
            and production.state == "done"
            and not _is_materialization(production)
        )
        if completed and protected.intersection(values):
            raise UserError(
                self.env._("Completed historical B2C production orders are locked."),
            )
        return super().write(values)

    def unlink(self):
        if self.filtered("usl_b2c_source_key"):
            raise UserError(
                self.env._("Historical B2C production orders cannot be deleted."),
            )
        return super().unlink()


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
        protected = {
            "company_id",
            "product_id",
            "product_qty",
            "uom_id",
            "bom_id",
            "location_id",
            "location_dest_id",
            "consume_line_ids",
            "produce_line_ids",
            "state",
        }
        completed = self.filtered(
            lambda unbuild: unbuild.usl_historical_b2c
            and unbuild.state == "done"
            and not _is_materialization(unbuild)
        )
        if completed and protected.intersection(values):
            raise UserError(
                self.env._("Completed historical supplier-pack conversions are locked."),
            )
        return super().write(values)

    def unlink(self):
        if self.filtered("usl_historical_b2c"):
            raise UserError(
                self.env._("Historical supplier-pack conversions cannot be deleted."),
            )
        return super().unlink()


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
        protected = {
            "company_id",
            "date",
            "picking_ids",
            "cost_lines",
            "valuation_adjustment_lines",
            "account_journal_id",
            "state",
        }
        completed = self.filtered(
            lambda cost: cost.usl_historical_b2c
            and cost.state == "done"
            and not _is_materialization(cost)
        )
        if completed and protected.intersection(values):
            raise UserError(
                self.env._("Validated historical B2C landed costs are locked."),
            )
        return super().write(values)

    def unlink(self):
        if self.filtered("usl_historical_b2c"):
            raise UserError(
                self.env._("Historical B2C landed costs cannot be deleted."),
            )
        return super().unlink()


class StockLandedCostLine(models.Model):
    _inherit = "stock.landed.cost.lines"

    def _usl_locked_historical_cost_lines(self):
        return self.filtered(
            lambda line: line.cost_id.usl_historical_b2c
            and line.cost_id.state == "done"
        )

    @api.model_create_multi
    def create(self, values_list):
        if not _is_materialization(self):
            cost_ids = {
                values["cost_id"]
                for values in values_list
                if values.get("cost_id")
            }
            if self.env["stock.landed.cost"].browse(cost_ids).filtered(
                lambda cost: cost.usl_historical_b2c and cost.state == "done"
            ):
                raise UserError(
                    self.env._("Validated historical B2C landed cost lines are locked."),
                )
        return super().create(values_list)

    def write(self, values):
        if (
            self._usl_locked_historical_cost_lines()
            and not _is_materialization(self)
        ):
            raise UserError(
                self.env._("Validated historical B2C landed cost lines are locked."),
            )
        return super().write(values)

    def unlink(self):
        if self._usl_locked_historical_cost_lines():
            raise UserError(
                self.env._(
                    "Validated historical B2C landed cost lines cannot be deleted."
                ),
            )
        return super().unlink()


class StockValuationAdjustmentLine(models.Model):
    _inherit = "stock.valuation.adjustment.lines"

    def _usl_locked_historical_adjustment_lines(self):
        return self.filtered(
            lambda line: line.cost_id.usl_historical_b2c
            and line.cost_id.state == "done"
        )

    @api.model_create_multi
    def create(self, values_list):
        if not _is_materialization(self):
            cost_ids = {
                values["cost_id"]
                for values in values_list
                if values.get("cost_id")
            }
            if self.env["stock.landed.cost"].browse(cost_ids).filtered(
                lambda cost: cost.usl_historical_b2c and cost.state == "done"
            ):
                raise UserError(
                    self.env._(
                        "Validated historical B2C valuation adjustments are locked."
                    ),
                )
        return super().create(values_list)

    def write(self, values):
        if (
            self._usl_locked_historical_adjustment_lines()
            and not _is_materialization(self)
        ):
            raise UserError(
                self.env._(
                    "Validated historical B2C valuation adjustments are locked."
                ),
            )
        return super().write(values)

    def unlink(self):
        if self._usl_locked_historical_adjustment_lines():
            raise UserError(
                self.env._(
                    "Validated historical B2C valuation adjustments cannot be deleted."
                ),
            )
        return super().unlink()

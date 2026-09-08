"""What has to be true before a drop can become accounting.

Every check here failed at least once against a real chart of accounts, and
each one would have produced a plausible-looking wrong number rather than an
error.  A check states what it expects, names what it found, and where the
correction is a matter of fact rather than judgement it can make it.
"""

from odoo import Command, models
from odoo.exceptions import UserError

#: What each finding would spoil, so a drop is stopped by what it is about to
#: do rather than by everything that is not yet perfect.
BLOCKS_APPLY = ("shadowed_destination", "product_tax_unclear")
BLOCKS_INVOICING = (
    *BLOCKS_APPLY,
    "retired_rate",
    "carriage_unaccounted",
    "destination_vat_misplaced",
    "revenue_unaccounted",
)
BLOCKS_SETTLEMENT = ("operator_untaxed",)
READINESS_KINDS = (*BLOCKS_INVOICING, *BLOCKS_SETTLEMENT, "tax_added_to_price")


class B2cImportBatchReadiness(models.Model):
    _inherit = "b2c.import.batch"

    def action_check_readiness(self):
        """Report what would make this drop post the wrong numbers."""
        for batch in self:
            batch.issue_ids.filtered(lambda issue: issue.kind in READINESS_KINDS).unlink()
            batch._check_destination_positions()
            batch._check_product_taxes()
            batch._check_carriage()
            batch._check_revenue_accounts()
            batch._check_position_rates()
            batch._check_destination_vat()
            batch._check_channel_operators()
            batch.issue_ids.filtered(
                lambda issue: issue.kind in READINESS_KINDS
                and issue.kind not in BLOCKS_APPLY,
            ).severity = "advisory"
            batch.write({"report": batch._build_report()})
        return True

    def _assert_settled(self, kinds, doing):
        """Refuse an action whose result these findings would falsify."""
        self.ensure_one()
        found = self.issue_ids.filtered(lambda issue: issue.kind in kinds)
        if found:
            raise UserError(
                self.env._(
                    "Settle this before %(doing)s:\n%(findings)s",
                    doing=doing,
                    findings="\n".join(f"- {issue.name}" for issue in found),
                ),
            )

    def _sold_products(self):
        self.ensure_one()
        return self.row_ids.filtered(
            lambda row: row.grain == "line" and row.resolution != "supplier",
        ).product_id

    def _destinations(self):
        """Return the countries this drop ships to."""
        self.ensure_one()
        found = self.env["res.country"]
        for row in self.row_ids.filtered(lambda item: item.grain == "order"):
            found |= self._country(row.values or {})
        return found

    # -- checks ------------------------------------------------------------

    def _check_destination_positions(self):
        """Report a position that answers for a country built to answer itself.

        A generic position with no country and a low sequence silently takes
        every destination that has one of its own, which is how a German sale
        comes to be invoiced without German VAT.
        """
        self.ensure_one()
        Position = self.env["account.fiscal.position"]
        positions = Position.search(
            [("company_id", "=", self.company_id.id), ("auto_apply", "=", True)],
        )
        for country in self._destinations():
            own = positions.filtered(lambda item, c=country: item.country_id == c)
            if not own:
                continue
            partner = self.env["res.partner"].new(
                {"name": "readiness", "country_id": country.id},
            )
            answering = Position._get_fiscal_position(partner)
            if answering in own:
                continue
            self._raise_issue(
                "shadowed_destination",
                self.env._(
                    "%(country)s is answered by %(answering)s, not by %(own)s",
                    country=country.display_name,
                    answering=answering.display_name or self.env._("no position"),
                    own=own[:1].display_name,
                ),
                note=self.env._(
                    "A sale to %(country)s would be taxed as %(answering)s says. "
                    "Give %(own)s priority, or retire the position that answers "
                    "for every country it was not built for.",
                    country=country.display_name,
                    answering=answering.display_name or self.env._("no position"),
                    own=own[:1].display_name,
                ),
                proposal={"shadowing_id": answering.id},
            )

    def _positions_in_use(self):
        """Return the fiscal positions this drop's sales would be stated under."""
        self.ensure_one()
        Position = self.env["account.fiscal.position"]
        found = self.company_id.usl_b2c_export_position_id
        for country in self._destinations():
            partner = self.env["res.partner"].new(
                {"name": "readiness", "country_id": country.id},
            )
            found |= Position._get_fiscal_position(partner)
        return found

    def _check_position_rates(self):
        """Report a position that would state a sale at a retired rate.

        A rate keeps answering for a position long after it stops being usable,
        and the invoice only says so when it is posted. A position that maps a
        sale to an archived rate is the same defect as a product pointing at an
        archived account, and just as quiet.
        """
        self.ensure_one()
        for position in self._positions_in_use():
            retired = position.with_context(active_test=False).tax_ids.filtered(
                lambda tax: not tax.active,
            )
            if not retired:
                continue
            self._raise_issue(
                "retired_rate",
                self.env._(
                    "%(position)s states %(count)s retired rate(s)",
                    position=position.display_name,
                    count=len(retired),
                ),
                note=self.env._(
                    "%(taxes)s no longer exist to post to, so a sale this "
                    "position answers for would refuse to post. Removing one "
                    "needs the archived records to be visible: write the "
                    "mapping with active_test disabled, or the ORM keeps it.",
                    taxes=", ".join(retired.mapped("name")),
                ),
                proposal={"position_id": position.id},
            )

    def _check_product_taxes(self):
        """Report a product whose tax is not one tax."""
        self.ensure_one()
        for product in self._sold_products():
            taxes = product.taxes_id.filtered(
                lambda tax: tax.company_id == self.company_id
                and tax.type_tax_use == "sale",
            )
            if len(taxes) == 1:
                continue
            self._raise_issue(
                "product_tax_unclear",
                self.env._(
                    "%(product)s carries %(count)s sales taxes",
                    product=product.display_name,
                    count=len(taxes),
                ),
                note=self.env._(
                    "A product states one rate. This one states %(taxes)s, so an "
                    "invoice for it would charge %(total)s per cent.",
                    taxes=", ".join(taxes.mapped("name")) or self.env._("none"),
                    total=sum(taxes.mapped("amount")),
                ),
                proposal={"product_id": product.id},
            )

    def _check_carriage(self):
        """Report carriage that would land in revenue, or nowhere."""
        self.ensure_one()
        for channel in self._channels().values():
            product = channel.shipping_product_id
            if not product:
                self._raise_issue(
                    "carriage_unaccounted",
                    self.env._(
                        "%(channel)s names no product for the carriage it charges",
                        channel=channel.display_name,
                    ),
                    note=self.env._(
                        "Without one, what customers pay to ship would be sold as "
                        "goods. Name a product whose income account is the carriage "
                        "account.",
                    ),
                    proposal={"channel_id": channel.id},
                )
                continue
            account = self._income_account(product)
            if not (account and account.active):
                self._raise_issue(
                    "carriage_unaccounted",
                    self.env._(
                        "%(product)s has no usable income account",
                        product=product.display_name,
                    ),
                    note=self.env._(
                        "Carriage charged to customers belongs in its own account, "
                        "not in the revenue of the goods.",
                    ),
                    proposal={"product_id": product.id},
                )

    def _check_revenue_accounts(self):
        """Report goods whose revenue has no usable account of its own."""
        self.ensure_one()
        for product in self._sold_products():
            account = self._income_account(product)
            if account and account.active:
                continue
            self._raise_issue(
                "revenue_unaccounted",
                self.env._(
                    "%(product)s has %(state)s income account",
                    product=product.display_name,
                    state=self.env._("an archived") if account else self.env._("no"),
                ),
                note=self.env._(
                    "Neither the product nor its category names where its revenue "
                    "goes, so it would fall to whatever the company defaults to. "
                    "Goods made in-house and goods bought to resell do not share "
                    "an account.",
                ),
                proposal={"product_id": product.id},
            )

    def _check_channel_operators(self):
        """Report a channel operator whose commission would not reverse-charge.

        A commission billed from another Member State is the buyer's VAT to
        account for, and Odoo only does that when a position requiring a tax
        number answers for the supplier.  Where none does, the position built
        for *selling* to that country answers instead and no reverse charge is
        made at all, which understates both the VAT due and the VAT deductible.
        """
        self.ensure_one()
        union = self.env.ref("base.europe").country_ids
        home = self.company_id.account_fiscal_country_id
        Position = self.env["account.fiscal.position"]
        for channel in self._channels().values():
            partner = channel.operator_partner_id
            if not partner or partner.country_id not in union - home:
                continue
            answering = Position._get_fiscal_position(partner)
            if answering.vat_required:
                continue
            self._raise_issue(
                "operator_untaxed",
                self.env._(
                    "%(partner)s is answered by %(position)s, so its commission "
                    "would not be reverse-charged",
                    partner=partner.display_name,
                    position=answering.display_name or self.env._("no position"),
                ),
                note=self.env._(
                    "%(channel)s is billed from %(country)s, so the VAT on its "
                    "commission is the company's to account for. %(cause)s",
                    channel=channel.display_name,
                    country=partner.country_id.display_name,
                    cause=" ".join((self._untaxed_cause(partner), self._untaxed_remedy())),
                ),
                proposal={"partner_id": partner.id},
            )

    def _untaxed_cause(self, partner):
        """Say why no position requiring a tax number answers for a supplier."""
        self.ensure_one()
        if not partner.vat:
            return self.env._("It has no tax number on file.")
        if not partner._get_vat_required_valid(company=self.company_id):
            return self.env._(
                "Its tax number %(vat)s has not been checked against VIES, and "
                "until it is Odoo treats it as absent.",
                vat=partner.vat,
            )
        return self.env._(
            "Its tax number is on file and checked, so the position that should "
            "answer is missing or outranked.",
        )

    def _untaxed_remedy(self):
        return self.env._(
            "Either check the number against VIES, or set the intra-Community "
            "position on the supplier itself, which always answers first.",
        )

    def _income_account(self, product):
        return product.with_company(self.company_id)._get_product_accounts().get("income")

    # -- corrections -------------------------------------------------------

    def action_correct_chart(self):
        """Correct the chart itself, not just what this drop happens to touch.

        The narrower corrections settle a finding.  These settle the chart the
        findings come from, whose defects are wrong whether or not anything is
        being imported.  Each one is stated separately because each is undone
        separately, and each says in the log what it changed.
        """
        self.ensure_one()
        self.action_retire_generic_positions()
        self.action_include_tax_in_price()
        self.action_settle_catalog_taxes()
        self.action_account_for_revenue()
        self.action_reverse_charge_operators()
        self.action_check_readiness()
        return True

    def action_retire_generic_positions(self):
        """Retire a position answering for a country that has one of its own."""
        self.ensure_one()
        self.company_id._usl_b2c_retire_shadowing_positions()
        return True

    def action_include_tax_in_price(self):
        """State the rates a channel sale is charged at as inside the price."""
        self.ensure_one()
        self.company_id._usl_b2c_state_prices_tax_included()
        return True

    def action_settle_catalog_taxes(self):
        """Give each catalog product the one rate its kind of thing is charged at."""
        self.ensure_one()
        self.company_id._usl_b2c_settle_product_taxes()
        return True

    def action_account_for_revenue(self):
        """Say where the revenue of each kind of B2C good goes."""
        self.ensure_one()
        self.company_id._usl_b2c_account_for_revenue()
        return True

    def action_reverse_charge_operators(self):
        """Let a channel operator's commission be reverse-charged."""
        self.ensure_one()
        self.company_id._usl_b2c_reverse_charge_operators()
        return True

    def action_retire_shadowing_positions(self):
        """Retire the generic positions that answer for countries not their own.

        Only a position that names no country is ever retired: one built for a
        country is what should answer for it, and this is what stops answering
        for every other.
        """
        self.ensure_one()
        findings = self.issue_ids.filtered(
            lambda issue: issue.kind == "shadowed_destination",
        )
        retired = self.env["account.fiscal.position"]
        for issue in findings:
            position = self.env["account.fiscal.position"].browse(
                (issue.proposal or {}).get("shadowing_id") or 0,
            )
            if position.exists() and not position.country_id and not position.country_group_id:
                retired |= position
        if not retired:
            raise UserError(
                self.env._(
                    "Every position answering for another country's sales names a "
                    "country of its own, so none can be retired without a decision.",
                ),
            )
        retired.active = False
        self.action_check_readiness()
        return True

    def action_settle_product_taxes(self):
        """Give each product the one sales tax its kind of thing is charged at.

        A good is charged the goods rate and a service the services rate; the
        chart already holds both, and a product carrying both is a product that
        would be charged twice.
        """
        self.ensure_one()
        findings = self.issue_ids.filtered(
            lambda issue: issue.kind == "product_tax_unclear",
        )
        for issue in findings:
            product = self.env["product.product"].browse(
                (issue.proposal or {}).get("product_id") or 0,
            )
            if not product.exists():
                continue
            wanted = self._standard_tax(product.type == "service")
            if not wanted:
                raise UserError(
                    self.env._("The chart holds no single standard sales tax to use."),
                )
            product.product_tmpl_id.taxes_id = [Command.set(wanted.ids)]
        self.action_check_readiness()
        return True

    def _standard_tax(self, is_service):
        """Return the company's standard sales rate for goods or for services."""
        self.ensure_one()
        wanted = self.company_id._usl_b2c_standard_sale_tax(is_service)
        if not wanted:
            raise UserError(
                self.env._(
                    "The chart does not state one sales rate for %(kind)s, so which "
                    "one a product is charged at is a decision to be made.",
                    kind=self.env._("services") if is_service else self.env._("goods"),
                ),
            )
        return wanted

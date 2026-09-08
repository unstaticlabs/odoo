"""The VAT each Member State is owed, quarter by quarter.

The One Stop Shop is declared per quarter, per country of destination, on the
taxable amount and the rate that country charges.  Odoo Community holds no such
report, so it is built from the invoices themselves: what a country is owed is
the sum of the bases taxed under the position built for that country, which is
the same evidence the ledger already carries.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError

QUARTERS = [
    ("1", "January to March"),
    ("2", "April to June"),
    ("3", "July to September"),
    ("4", "October to December"),
]

RETURN_STATES = [
    ("draft", "Draft"),
    ("ready", "Ready to file"),
    ("filed", "Filed"),
]


class B2cOssReturn(models.Model):
    _name = "b2c.oss.return"
    _description = "Destination VAT Return"
    _order = "year desc, quarter desc"
    _inherit = ["mail.thread"]

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="cascade",
    )
    year = fields.Integer(required=True, default=lambda self: fields.Date.today().year)
    quarter = fields.Selection(QUARTERS, required=True)
    state = fields.Selection(RETURN_STATES, required=True, default="draft", tracking=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    line_ids = fields.One2many("b2c.oss.return.line", "return_id", string="Countries")
    base_amount = fields.Monetary(compute="_compute_totals", store=True)
    vat_amount = fields.Monetary(compute="_compute_totals", store=True)
    registered = fields.Boolean(related="company_id.usl_b2c_oss_registered")
    computed_at = fields.Datetime(readonly=True, copy=False)

    _company_period_unique = models.Constraint(
        "UNIQUE(company_id, year, quarter)",
        "A destination VAT return covers one quarter once.",
    )

    @api.depends("year", "quarter")
    def _compute_name(self):
        for record in self:
            record.name = f"OSS {record.year} Q{record.quarter or '?'}"

    @api.depends("line_ids.base_amount", "line_ids.vat_amount")
    def _compute_totals(self):
        for record in self:
            record.base_amount = sum(record.line_ids.mapped("base_amount"))
            record.vat_amount = sum(record.line_ids.mapped("vat_amount"))

    def _period(self):
        self.ensure_one()
        if not self.quarter:
            raise UserError(self.env._("Choose the quarter this return covers."))
        first = (int(self.quarter) - 1) * 3 + 1
        start = fields.Date.to_date(f"{self.year}-{first:02d}-01")
        end = fields.Date.to_date(f"{self.year + (first + 3) // 13}-{(first + 3 - 1) % 12 + 1:02d}-01")
        return start, end

    def action_compute(self):
        """Read the quarter's invoices for what each destination is owed."""
        for record in self:
            start, end = record._period()
            record.line_ids.unlink()
            record.env["b2c.oss.return.line"].create(record._lines(start, end))
            record.write(
                {"computed_at": fields.Datetime.now(), "state": "draft"},
            )
        return True

    def _destination_countries(self):
        """Return the country each destination rate is charged on behalf of."""
        self.ensure_one()
        home = self.company_id.account_fiscal_country_id
        union = self.env.ref("base.europe").country_ids
        positions = self.env["account.fiscal.position"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("country_id", "in", (union - home).ids),
            ],
        )
        return {
            tax: position.country_id
            for position in positions
            for tax in position.tax_ids
        }

    def _lines(self, start, end):
        """Return one line per country, from the tax the invoices actually bore.

        A country's VAT is what its own rate collected, and its taxable amount
        is what bore that rate: the lines carrying the tax, not every line of
        the invoices that happened to include one.
        """
        self.ensure_one()
        countries = self._destination_countries()
        if not countries:
            return []
        period = [
            ("company_id", "=", self.company_id.id),
            ("parent_state", "=", "posted"),
            ("date", ">=", start),
            ("date", "<", end),
            ("move_id.move_type", "in", ("out_invoice", "out_refund")),
        ]
        taxes = self.env["account.tax"].browse([tax.id for tax in countries])
        found = {}
        for tax, balance in self.env["account.move.line"]._read_group(
            [*period, ("tax_line_id", "in", taxes.ids)],
            groupby=["tax_line_id"],
            aggregates=["balance:sum"],
        ):
            entry = found.setdefault(
                countries[tax], {"base": 0.0, "vat": 0.0, "rate": tax.amount},
            )
            entry["vat"] -= balance
            entry["rate"] = tax.amount
        for tax, balance in self.env["account.move.line"]._read_group(
            [*period, ("tax_ids", "in", taxes.ids)],
            groupby=["tax_ids"],
            aggregates=["balance:sum"],
        ):
            if tax not in countries:
                continue
            entry = found.setdefault(
                countries[tax], {"base": 0.0, "vat": 0.0, "rate": tax.amount},
            )
            entry["base"] -= balance
        return [
            {
                "return_id": self.id,
                "country_id": country.id,
                "rate": values["rate"],
                "base_amount": values["base"],
                "vat_amount": values["vat"],
            }
            for country, values in sorted(found.items(), key=lambda item: item[0].code)
            if values["base"] or values["vat"]
        ]

    def action_mark_ready(self):
        for record in self:
            if not record.line_ids:
                raise UserError(
                    record.env._("Compute the return before saying it is ready."),
                )
            record.state = "ready"
        return True

    def action_mark_filed(self):
        self.write({"state": "filed"})
        return True


class B2cOssReturnLine(models.Model):
    _name = "b2c.oss.return.line"
    _description = "Destination VAT Return Line"
    _order = "return_id, country_id"

    return_id = fields.Many2one(
        "b2c.oss.return",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(related="return_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="return_id.currency_id")
    country_id = fields.Many2one("res.country", required=True, ondelete="restrict")
    rate = fields.Float(digits=(5, 2), string="Rate (%)")
    base_amount = fields.Monetary()
    vat_amount = fields.Monetary()

from babel.dates import format_date
from dateutil.relativedelta import relativedelta

from odoo import _, fields, models
from odoo.tools.misc import format_amount


class AccountJournal(models.Model):
    _inherit = "account.journal"

    _dashboard_month_count = 6

    def _sale_purchase_dashboard_months(self):
        """Return the rolling calendar-month window shown on journal cards."""
        today = fields.Date.context_today(self)
        current_month = today.replace(day=1)
        return [
            current_month - relativedelta(months=offset)
            for offset in range(self._dashboard_month_count - 1, -1, -1)
        ]

    def _get_sale_purchase_graph_data(self):
        """Show posted document activity instead of an open-balance forecast.

        ``amount_total_signed`` is stable company-currency evidence. Customer
        refunds are negative; vendor documents use the opposite accounting
        sign, so the journal sign below keeps both charts readable while
        preserving refunds as reductions.
        """
        if not self:
            return {}

        self.check_access("read")
        months = self._sale_purchase_dashboard_months()
        today = fields.Date.context_today(self)
        date_from = months[0]
        date_to = months[-1] + relativedelta(months=1)
        move_model = self.env["account.move"]
        totals = {
            (journal.id, document_month): total
            for journal, document_month, total in move_model._read_group(
                domain=[
                    *move_model._check_company_domain(self.env.companies),
                    ("journal_id", "in", self.ids),
                    ("state", "=", "posted"),
                    (
                        "move_type",
                        "in",
                        move_model.get_invoice_types(include_receipts=True),
                    ),
                    ("invoice_date", ">=", date_from),
                    ("invoice_date", "<", date_to),
                    ("invoice_date", "<=", today),
                ],
                groupby=["journal_id", "invoice_date:month"],
                aggregates=["amount_total_signed:sum"],
            )
        }
        locale = self.env.lang or "en_US"
        result = {}
        for journal in self:
            currency = journal.company_id.currency_id
            sign = 1 if journal.type == "sale" else -1
            values = []
            for month in months:
                value = currency.round(
                    sign * totals.get((journal.id, month), 0),
                )
                values.append(
                    {
                        "label": format_date(month, "MMM y", locale=locale),
                        "type": "past",
                        "value": value,
                        "formatted_value": format_amount(
                            self.env,
                            value,
                            currency,
                        ),
                    },
                )
            result[journal.id] = [
                {
                    "values": values,
                    "title": "",
                    "key": _(
                        "Net posted amount — refunds deducted",
                    ),
                    "is_sample_data": False,
                },
            ]
        return result

    def _fill_sale_purchase_dashboard_data(self, dashboard_data):
        super()._fill_sale_purchase_dashboard_data(dashboard_data)
        months = self._sale_purchase_dashboard_months()
        locale = self.env.lang or "en_US"
        period = _(
            "%(date_from)s–%(date_to)s",
            date_from=format_date(months[0], "MMM y", locale=locale),
            date_to=format_date(months[-1], "MMM y", locale=locale),
        )
        for journal in self.filtered(
            lambda item: item.type in ("sale", "purchase"),
        ):
            currency = journal.company_id.currency_id
            dashboard_data[journal.id].update(
                {
                    "monthly_activity_caption": _(
                        "Posted documents · %(period)s · %(currency)s · "
                        "%(current_month)s MTD",
                        period=period,
                        currency=currency.name,
                        current_month=format_date(
                            months[-1],
                            "MMM",
                            locale=locale,
                        ),
                    ),
                    "monthly_activity_help": _(
                        "Monthly totals use posted invoice or bill dates and "
                        "company currency. Refunds reduce the month. The last "
                        "bar is the current month to date.",
                    ),
                },
            )

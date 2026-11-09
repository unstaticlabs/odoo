"""Exact fiscal-year buckets for the accounting report views.

Every report view groups posted accounting by the fiscal year each date
belongs to. The bucket is derived from the owning company: its recurring
fiscal-year end (``fiscalyear_last_month`` / ``fiscalyear_last_day``) and the
optional exceptional first fiscal year
(``rebuild_first_fiscalyear_start`` / ``rebuild_first_fiscalyear_end``).

A bucket is always a closed range. The view carries it as three columns,
``period_key`` (a display label), ``period_date_from`` and ``period_date_to``;
drill-down domains, filters and exports read the two date columns and never
parse the label.

The SQL expressions and :func:`fiscal_period_bounds` implement the same rule as
``res.company.compute_fiscalyear_dates``, including the Odoo convention that a
February 28 or 29 year end means the last day of February.
"""

import re
from collections import namedtuple
from datetime import date, timedelta

from odoo.tools.date_utils import get_fiscal_year

FISCAL_YEAR_LABEL_PREFIX = "Fiscal year"
EARLIER_POSTED_LABEL = "Earlier posted accounting"
EARLIER_ANALYTIC_LABEL = "Earlier analytic accounting"

FiscalPeriodSql = namedtuple(
    "FiscalPeriodSql",
    ["key", "date_from", "date_to", "select", "group_by"],
)


FirstFiscalYearSql = namedtuple(
    "FirstFiscalYearSql",
    ["is_set", "key", "date_from", "date_to", "select"],
)


def fiscal_period_label(date_from, date_to):
    """Return the display label of the closed range ``date_from``..``date_to``."""
    return (
        f"{FISCAL_YEAR_LABEL_PREFIX} "
        f"{date_from.isoformat()} to {date_to.isoformat()}"
    )


_FISCAL_YEAR_LABEL = re.compile(
    rf"^{FISCAL_YEAR_LABEL_PREFIX} (\d{{4}}-\d{{2}}-\d{{2}}) to (\d{{4}}-\d{{2}}-\d{{2}})$",
)
_CALENDAR_YEAR_LABEL = re.compile(r"^Calendar year (\d{4})$")
_LEGACY_OPEN_LABEL = re.compile(
    rf"^{FISCAL_YEAR_LABEL_PREFIX} from (\d{{4}}-\d{{2}}-\d{{2}})$",
)


def stored_label_bounds(label):
    """Read the dates of a label typed into a stored record.

    Reports never use this: their buckets carry real date columns. It exists
    for free-text period labels kept on reviewed external values, where a
    person typed ``Fiscal year A to B`` or ``Calendar year YYYY``. Returns
    ``(date_from, date_to)`` or ``None`` when the label is neither form.
    """
    label = (label or "").strip()
    match = _FISCAL_YEAR_LABEL.match(label)
    if match:
        try:
            return (
                date.fromisoformat(match.group(1)),
                date.fromisoformat(match.group(2)),
            )
        except ValueError:
            return None
    match = _CALENDAR_YEAR_LABEL.match(label)
    if match:
        year = int(match.group(1))
        return date(year, 1, 1), date(year, 12, 31)
    return None


def legacy_open_label_start(label):
    """Start date of an open-ended ``Fiscal year from YYYY-MM-DD`` label."""
    match = _LEGACY_OPEN_LABEL.match((label or "").strip())
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def fiscal_period_bounds(
    on_date,
    last_day,
    last_month,
    first_start=None,
    first_end=None,
):
    """Python twin of :func:`fiscal_period_sql` for one date.

    Returns ``(date_from, date_to)``. ``date_from`` is ``None`` for the open
    "earlier" bucket that precedes the exceptional first fiscal year.
    """
    if first_start and first_end:
        if on_date < first_start:
            return None, first_start - timedelta(days=1)
        if on_date <= first_end:
            return first_start, first_end
    return get_fiscal_year(on_date, day=int(last_day), month=int(last_month))


def fiscal_period_key(
    on_date,
    last_day,
    last_month,
    first_start=None,
    first_end=None,
    earlier_label=EARLIER_POSTED_LABEL,
):
    """Return the display label of the bucket that contains ``on_date``."""
    date_from, date_to = fiscal_period_bounds(
        on_date, last_day, last_month, first_start, first_end,
    )
    if date_from is None:
        return earlier_label
    return fiscal_period_label(date_from, date_to)


def _year_end_sql(year, month, day):
    """SQL for the last day of the fiscal year that ends in calendar ``year``."""
    return (
        f"(CASE WHEN {month} = 2 AND {day} IN (28, 29) "
        f"THEN make_date({year}, 3, 1) - 1 "
        f"ELSE make_date({year}, {month}, LEAST({day}, "
        f"EXTRACT(DAY FROM (make_date({year}, {month}, 1) "
        f"+ INTERVAL '1 month' - INTERVAL '1 day'))::integer)) END)"
    )


def first_fiscal_year_sql(company_alias):
    """SQL expressions of the company's exceptional first fiscal year.

    The expressions are NULL for a company without that exception, so a
    ``WHERE`` on ``is_set`` keeps the first-year-only sources out of the
    other companies.
    """
    first_start = f"{company_alias}.rebuild_first_fiscalyear_start"
    first_end = f"{company_alias}.rebuild_first_fiscalyear_end"
    key = (
        f"('{FISCAL_YEAR_LABEL_PREFIX} ' "
        f"|| to_char({first_start}, 'YYYY-MM-DD') "
        f"|| ' to ' || to_char({first_end}, 'YYYY-MM-DD'))"
    )
    return FirstFiscalYearSql(
        is_set=f"({first_start} IS NOT NULL AND {first_end} IS NOT NULL)",
        key=key,
        date_from=first_start,
        date_to=first_end,
        select=(
            f"{key} AS period_key,\n"
            f"{first_start} AS period_date_from,\n"
            f"{first_end} AS period_date_to"
        ),
    )


def fiscal_period_sql(
    date_expr,
    company_alias,
    earlier_label=EARLIER_POSTED_LABEL,
):
    """Return the SQL expressions of the fiscal bucket of ``date_expr``.

    ``company_alias`` names a joined ``res_company`` row. The result carries
    ``key``, ``date_from`` and ``date_to`` expressions, a ``select`` fragment
    (``<key> AS period_key, <from> AS period_date_from, <to> AS
    period_date_to``) and a ``group_by`` fragment with the same three
    expressions.
    """
    month = f"{company_alias}.fiscalyear_last_month::integer"
    day = f"{company_alias}.fiscalyear_last_day"
    first_start = f"{company_alias}.rebuild_first_fiscalyear_start"
    first_end = f"{company_alias}.rebuild_first_fiscalyear_end"
    first_set = f"({first_start} IS NOT NULL AND {first_end} IS NOT NULL)"
    year = f"EXTRACT(YEAR FROM {date_expr})::integer"

    def year_end(offset):
        shifted = year if not offset else f"({year} {offset:+d})"
        return _year_end_sql(shifted, month, day)

    standard_to = (
        f"CASE WHEN {date_expr} <= {year_end(0)} "
        f"THEN {year_end(0)} ELSE {year_end(1)} END"
    )
    standard_from = (
        f"CASE WHEN {date_expr} <= {year_end(0)} "
        f"THEN {year_end(-1)} + 1 ELSE {year_end(0)} + 1 END"
    )
    is_earlier = f"({first_set} AND {date_expr} < {first_start})"
    in_first_year = f"({first_set} AND {date_expr} <= {first_end})"
    date_from = (
        f"(CASE WHEN {is_earlier} THEN NULL::date "
        f"WHEN {in_first_year} THEN {first_start} "
        f"ELSE {standard_from} END)"
    )
    date_to = (
        f"(CASE WHEN {is_earlier} THEN {first_start} - 1 "
        f"WHEN {in_first_year} THEN {first_end} "
        f"ELSE {standard_to} END)"
    )
    key = (
        f"(CASE WHEN {is_earlier} THEN '{earlier_label}' "
        f"ELSE '{FISCAL_YEAR_LABEL_PREFIX} ' "
        f"|| to_char({date_from}, 'YYYY-MM-DD') "
        f"|| ' to ' || to_char({date_to}, 'YYYY-MM-DD') END)"
    )
    return FiscalPeriodSql(
        key=key,
        date_from=date_from,
        date_to=date_to,
        select=(
            f"{key} AS period_key,\n"
            f"{date_from} AS period_date_from,\n"
            f"{date_to} AS period_date_to"
        ),
        group_by=f"{key},\n{date_from},\n{date_to}",
    )

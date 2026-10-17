"""Close the open-ended ``Fiscal year from <date>`` period label.

Until this version the report views labelled every posted date from the
current fiscal year's start onward as ``Fiscal year from <start>``, with no end.
The views now bucket each date into the closed ``Fiscal year <start> to <end>``
of its company. Reviewed external values and assurance decisions keep a copy of
that label in ``period_key``, so they are relabelled with the same rule.

A row is moved to the explicit label of the fiscal year of its company that
starts on the date in the legacy label. When the company has no fiscal year
starting on that date (for instance an exceptional first year), the row is left
as it is and only the number of such rows is logged. The script writes only
``period_key`` on those two tables. It never touches a posted entry, a lock
date or a ledger row, and it never merges rows: it stops, changing nothing, only
when a relabel would duplicate an existing external value on the unique
company, period, form, field, kind and source.
"""

import logging

from odoo.addons.rebuild_account_migration.models.fiscal_period import (
    fiscal_period_bounds,
    fiscal_period_label,
    legacy_open_label_start,
)

_logger = logging.getLogger(__name__)

LEGACY_PREFIX = "Fiscal year from "
TABLES = (
    "rebuild_account_external_report_value",
    "rebuild_account_assurance_decision",
)


class FiscalPeriodRelabelError(Exception):
    """Relabelling would merge two external values."""


def relabel_target(
    legacy_label,
    last_day,
    last_month,
    first_start=None,
    first_end=None,
):
    """Return the closed label that replaces ``legacy_label``, or ``None``.

    ``None`` means the company has no fiscal year starting on the date the
    legacy label names, so the row must be left as it is.
    """
    start = legacy_open_label_start(legacy_label)
    if start is None:
        return None
    date_from, date_to = fiscal_period_bounds(
        start, last_day, last_month, first_start, first_end,
    )
    if date_from != start:
        return None
    return fiscal_period_label(date_from, date_to)


def _legacy_rows(cr, table):
    cr.execute(
        f"""
        SELECT value.id,
               value.period_key,
               company.fiscalyear_last_day,
               company.fiscalyear_last_month,
               company.rebuild_first_fiscalyear_start,
               company.rebuild_first_fiscalyear_end
          FROM {table} value
          JOIN res_company company ON company.id = value.company_id
         WHERE value.period_key LIKE %s
         ORDER BY value.id
        """,
        [LEGACY_PREFIX + "%"],
    )
    return cr.fetchall()


def _unique_collisions(cr, plan):
    """Rows whose new label already exists for the same unique source."""
    collisions = []
    seen = {}
    for row_id, new_key in plan:
        cr.execute(
            """
            SELECT value.id, other.id
              FROM rebuild_account_external_report_value value
              JOIN rebuild_account_external_report_value other
                ON other.company_id = value.company_id
               AND other.form_code = value.form_code
               AND other.field_code = value.field_code
               AND other.value_kind = value.value_kind
               AND other.source_key = value.source_key
               AND other.id != value.id
               AND other.period_key = %s
             WHERE value.id = %s
            """,
            [new_key, row_id],
        )
        collisions.extend(cr.fetchall())
        cr.execute(
            """
            SELECT company_id, form_code, field_code, value_kind, source_key
              FROM rebuild_account_external_report_value
             WHERE id = %s
            """,
            [row_id],
        )
        identity = (new_key, *cr.fetchone())
        if identity in seen:
            collisions.append((seen[identity], row_id))
        seen[identity] = row_id
    return collisions


def migrate(cr, version):
    del version
    plans = {}
    for table in TABLES:
        plan = []
        unmatched = 0
        for (
            row_id,
            period_key,
            last_day,
            last_month,
            first_start,
            first_end,
        ) in _legacy_rows(cr, table):
            new_key = relabel_target(
                period_key, last_day, last_month, first_start, first_end,
            )
            if new_key is None:
                unmatched += 1
                continue
            plan.append((row_id, new_key))
        plans[table] = plan
        if unmatched:
            _logger.warning(
                "%s legacy period label(s) in %s match no fiscal year of "
                "their company and were left unchanged.",
                unmatched,
                table,
            )

    collisions = _unique_collisions(cr, plans["rebuild_account_external_report_value"])
    if collisions:
        message = (
            "The open-ended fiscal period labels were not changed because "
            "relabelling would duplicate an existing external value on the "
            "unique company, period, form, field, kind and source. Move or "
            "remove the duplicate, then upgrade again:\n- "
            + "\n- ".join(
                f"row #{first_id} would duplicate row #{second_id}"
                for first_id, second_id in collisions
            )
        )
        raise FiscalPeriodRelabelError(message)

    for table, plan in plans.items():
        for row_id, new_key in plan:
            cr.execute(
                f"UPDATE {table} SET period_key = %s WHERE id = %s",
                [new_key, row_id],
            )
        if plan:
            _logger.info(
                "Relabelled %s open-ended fiscal period label(s) in %s.",
                len(plan),
                table,
            )

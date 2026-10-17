"""Close the open-ended ``Fiscal year from <date>`` period label.

Until this version the report views labelled every posted date from the
current fiscal year's start onward as ``Fiscal year from <start>``, with no end.
The views now bucket each date into the closed ``Fiscal year <start> to <end>``
of its company. Reviewed external values and assurance decisions keep a copy of
that label in ``period_key``, so they are relabelled with the same rule.

The script writes only ``period_key`` on those two tables. It never touches a
posted entry, a lock date or a ledger row, and it refuses to guess: it stops
without changing anything when a relabel would collide with the unique
constraint, when a label cannot be tied to one company fiscal year, or when a
row was created after the fiscal year its label would now name had ended.
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
    """A stored period label cannot be moved to a closed fiscal year safely."""


def relabel_target(
    legacy_label,
    created_at,
    last_day,
    last_month,
    first_start=None,
    first_end=None,
):
    """Return the closed label that replaces ``legacy_label`` for one row.

    Raises :class:`FiscalPeriodRelabelError` when the legacy label cannot be
    tied to exactly one fiscal year of the company, or when the row was created
    after that fiscal year ended (the open-ended label may then have been used
    for a later year).
    """
    start = legacy_open_label_start(legacy_label)
    if start is None:
        message = f"cannot read a start date from the label {legacy_label!r}"
        raise FiscalPeriodRelabelError(message)
    date_from, date_to = fiscal_period_bounds(
        start, last_day, last_month, first_start, first_end,
    )
    if date_from != start:
        message = (
            f"the label {legacy_label!r} starts inside a fiscal year of the "
            f"company ({date_from} to {date_to}), not on its first day"
        )
        raise FiscalPeriodRelabelError(message)
    if created_at is not None and created_at.date() > date_to:
        message = (
            f"a row labelled {legacy_label!r} was created on "
            f"{created_at.date()}, after the fiscal year it would now name "
            f"({date_from} to {date_to}) ended"
        )
        raise FiscalPeriodRelabelError(message)
    return fiscal_period_label(date_from, date_to)


def _legacy_rows(cr, table):
    cr.execute(
        f"""
        SELECT value.id,
               value.company_id,
               value.period_key,
               value.create_date,
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
    problems = []
    for table in TABLES:
        plan = []
        for (
            row_id,
            company_id,
            period_key,
            created_at,
            last_day,
            last_month,
            first_start,
            first_end,
        ) in _legacy_rows(cr, table):
            try:
                new_key = relabel_target(
                    period_key,
                    created_at,
                    last_day,
                    last_month,
                    first_start,
                    first_end,
                )
            except FiscalPeriodRelabelError as error:
                problems.append(f"{table} #{row_id} (company {company_id}): {error}")
                continue
            plan.append((row_id, new_key, period_key))
        plans[table] = plan

    external_plan = [
        (row_id, new_key)
        for row_id, new_key, _old in plans["rebuild_account_external_report_value"]
    ]
    for first_id, second_id in _unique_collisions(cr, external_plan):
        problems.append(
            "rebuild_account_external_report_value: relabelling row "
            f"#{first_id} would duplicate row #{second_id} on the unique "
            "company, period, form, field, kind and source",
        )

    if problems:
        message = (
            "The open-ended fiscal period labels were not changed. "
            "Resolve each item below (move the row to the explicit "
            "'Fiscal year <start> to <end>' label of the year it belongs to, "
            "or remove the duplicate), then upgrade again:\n- "
            + "\n- ".join(problems)
        )
        raise FiscalPeriodRelabelError(message)

    for table, plan in plans.items():
        for row_id, new_key, _old_key in plan:
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

"""Adopting the months the historical reconstruction already settled.

The ledger holds monthly entries for what each channel kept and what the
supplier drew, posted in 2026 by the reconstruction that rebuilt the B2C
history.  They are bank-reconciled and their VAT has been declared.

The ingestor decides what a month still owes by subtracting the settlements it
has recorded from what a statement says the month cost.  It recorded none of
these — the reconstruction wrote its own reference vocabulary and knew nothing
of this one — so without adopting them the first run over a settled month bills
its commission and its supply a second time.

Adopting them is what makes re-importing an old export genuinely a no-op: not
because a date forbids it, but because the tool can see the month is done.  A
month whose posted amount and whose statement disagree still shows the
difference as owed, which is the right answer rather than a refusal.

It runs on installing the module and on upgrading it, because either can be the
first time the tool meets a database the reconstruction wrote: production is
upgraded, a clone is installed into.  It is idempotent, so running it both ways
adopts each month once.

This knows the reconstruction's reference conventions.  That knowledge belongs
in this one file, which exists only to close the gap between what those scripts
posted and what this module can see, and never in the product's own reasoning.
"""

import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

#: How the reconstruction named a month of what a channel kept, per provider.
#: A trailing currency is optional: it wrote one for Revolut and not for Stripe.
CHANNEL_FAMILIES = {
    "etsy": (
        "etsy:wallet:",
        re.compile(r"^etsy:wallet:(?P<period>\d{4}-\d{2})(?::(?P<currency>[A-Z]{3}))?$"),
    ),
    "stripe": (
        "stripe:fees:",
        re.compile(r"^stripe:fees:(?P<period>\d{4}-\d{2})(?::(?P<currency>[A-Z]{3}))?$"),
    ),
    "revolut": (
        "revolut:fees:",
        re.compile(r"^revolut:fees:(?P<period>\d{4}-\d{2})(?::(?P<currency>[A-Z]{3}))?$"),
    ),
}

#: How it named a month of what the supplier drew on the wallet.
SUPPLY_FAMILY = (
    "printful:wallet:consumption:",
    re.compile(r"^printful:wallet:consumption:(?P<period>\d{4}-\d{2})$"),
)

#: The account kinds a cost reaches. What a document put anywhere else — a
#: receipt, a tax, a sale — is not something that was bought.
EXPENSE_TYPES = ("expense", "expense_depreciation", "expense_direct_cost")


def post_init_hook(env):
    """Adopt on install: a clone is installed into, not upgraded."""
    adopt_reconstruction(env)


def migrate(cr, version):
    """Adopt on upgrade: production already holds the module."""
    del version
    adopt_reconstruction(api.Environment(cr, SUPERUSER_ID, {}))


def adopt_reconstruction(env):
    """Record a settlement for every month the reconstruction already settled."""
    _adopt_channel_months(env)
    _adopt_supply_months(env)


def _adopt_channel_months(env):
    """Record a settlement for each month of commission already posted."""
    Settlement = env["b2c.channel.settlement"].sudo()
    channels = env["b2c.channel"].sudo().search([])
    by_provider = {}
    for channel in channels:
        by_provider.setdefault(channel.code, channel)
        if channel.processor_provider:
            by_provider.setdefault(channel.processor_provider, channel)
    values = []
    for provider, (prefix, pattern) in CHANNEL_FAMILIES.items():
        channel = by_provider.get(provider)
        if not channel:
            continue
        for move in _moves_matching(env, prefix, pattern):
            period, currency = _period_and_currency(env, move, pattern)
            if Settlement.search_count(
                [
                    ("move_id", "=", move.id),
                    ("channel_id", "=", channel.id),
                    ("period", "=", period),
                    ("currency_id", "=", currency.id),
                ],
            ):
                continue
            values.append(
                {
                    "company_id": move.company_id.id,
                    "channel_id": channel.id,
                    "move_id": move.id,
                    "provider": provider,
                    "period": period,
                    "currency_id": currency.id,
                    "fee_amount": _expense_booked(move),
                },
            )
    if values:
        Settlement.create(values)
    _logger.info("Adopted %s month(s) of commission the reconstruction settled.", len(values))


def _adopt_supply_months(env):
    """Mark the fulfilments of each already-settled month as settled.

    A settlement answers to a fulfilment rather than to a month, so the month's
    entry is spread across the fulfilments it covered — each at what it cost.
    Where the two do not add up to the same figure, the difference stays owed
    and is billed as a correction, which is the honest outcome.
    """
    Settlement = env["b2c.supply.settlement"].sudo()
    Event = env["b2c.fulfilment.event"].sudo()
    adopted = 0
    prefix, pattern = SUPPLY_FAMILY
    for move in _moves_matching(env, prefix, pattern):
        period = pattern.match(move.ref).group("period")
        start = f"{period}-01"
        events = Event.search(
            [
                ("company_id", "=", move.company_id.id),
                ("event_date", ">=", start),
                ("event_date", "<", _month_after(start)),
            ],
        )
        values = [
            {
                "company_id": move.company_id.id,
                "event_id": event.id,
                "move_id": move.id,
                "currency_id": event.currency_id.id,
                "cogs_amount": event.cogs_amount,
                "company_cogs_amount": event.cogs_amount,
            }
            for event in events
            if not Settlement.search_count(
                [("event_id", "=", event.id), ("move_id", "=", move.id)],
            )
        ]
        if values:
            Settlement.create(values)
            adopted += len(values)
        booked = _expense_booked(move)
        held = sum(events.mapped("cogs_amount"))
        if abs(booked - held) >= 0.01:
            # Nothing is posted for it: the month is settled and its VAT is
            # declared. But the two accounts of it differ, and somebody should
            # know which fulfilment the reconstruction saw that Odoo does not.
            _logger.warning(
                "%s booked %.2f of supply where Odoo holds %.2f of fulfilment.",
                move.ref, booked, held,
            )
    _logger.info("Adopted %s fulfilment(s) the reconstruction already settled.", adopted)


def _expense_booked(move):
    """Return what an entry charged to expense, which is what was kept.

    A reconstruction entry states a whole month of a channel in one document:
    the sales, the tax the marketplace collected, the receipts and the fee, all
    together.  Only the part that reached an expense account is what the
    channel kept, so only that part is a commission already settled.  Reading
    the document's total instead would claim a month's whole turnover had been
    bought as commission.
    """
    return sum(
        line.balance
        for line in move.line_ids
        if line.account_id.account_type in EXPENSE_TYPES
    )


def _moves_matching(env, prefix, pattern):
    """Return every posted entry whose reference this family names.

    The database narrows by the prefix, because the alternative is reading
    every document the ledger has ever posted in order to discard almost all
    of them.  The pattern then decides, since a prefix is not an identity.
    """
    candidates = env["account.move"].sudo().search(
        [("ref", "=like", f"{prefix}%"), ("state", "=", "posted")],
    )
    return candidates.filtered(lambda move: pattern.match(move.ref or ""))


def _period_and_currency(env, move, pattern):
    found = pattern.match(move.ref)
    period = f"{found.group('period')}-01"
    named = found.groupdict().get("currency")
    currency = (
        env["res.currency"].sudo().with_context(active_test=False).search(
            [("name", "=", named)], limit=1,
        )
        if named
        else env["res.currency"]
    )
    return period, currency or move.company_id.currency_id


def _month_after(start):
    """Return the first day of the month after the one a period starts."""
    year, month, _day = (int(part) for part in start.split("-"))
    if month == 12:
        return f"{year + 1}-01-01"
    return f"{year}-{month + 1:02d}-01"

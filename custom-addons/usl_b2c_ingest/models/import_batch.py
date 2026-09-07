"""One drop of channel exports, from files to a report an operator can act on.

Reading a batch never changes anything outside the batch itself, so an operator
can drop a file purely to find out what it contains.
"""

from collections import Counter, defaultdict
from decimal import Decimal

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.usl_b2c_ingest import parsers

#: The values two exports of the same sale must agree on. Everything else is
#: vocabulary: one channel calls an order "completed" where another calls the
#: same order "pending", and neither is stating a different fact.
RECONCILED_VALUES = frozenset(
    {
        "currency",
        "discount_amount",
        "external_listing_id",
        "fee_amount",
        "net_amount",
        "original_sku",
        "quantity",
        "shipping_amount",
        "subtotal_amount",
        "tax_amount",
        "total_amount",
        "unit_price",
    },
)

BATCH_STATES = [
    ("draft", "Draft"),
    ("parsed", "Read"),
    ("resolved", "Resolved"),
    ("simulated", "Simulated"),
    ("applied", "Applied"),
]


class B2cImportBatch(models.Model):
    _name = "b2c.import.batch"
    _description = "B2C Import Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, default=lambda self: self._default_name(), tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    state = fields.Selection(
        BATCH_STATES,
        required=True,
        default="draft",
        readonly=True,
        tracking=True,
    )
    file_ids = fields.One2many("b2c.import.file", "batch_id", string="Files")
    row_ids = fields.One2many("b2c.import.row", "batch_id", string="Rows")
    issue_ids = fields.One2many("b2c.import.issue", "batch_id", string="Findings")

    file_count = fields.Integer(compute="_compute_counts", store=True)
    row_count = fields.Integer(compute="_compute_counts", store=True)
    order_count = fields.Integer(compute="_compute_counts", store=True)
    line_count = fields.Integer(compute="_compute_counts", store=True)
    new_order_count = fields.Integer(compute="_compute_counts", store=True)
    known_order_count = fields.Integer(compute="_compute_counts", store=True)
    conflicting_count = fields.Integer(compute="_compute_counts", store=True)
    blocking_issue_count = fields.Integer(compute="_compute_counts", store=True)
    advisory_issue_count = fields.Integer(compute="_compute_counts", store=True)

    period_start = fields.Date(readonly=True)
    period_end = fields.Date(readonly=True)
    report = fields.Text(readonly=True)

    @api.model
    def _default_name(self):
        return fields.Date.context_today(self).strftime("Import of %d %B %Y")

    @api.depends(
        "file_ids",
        "row_ids.grain",
        "row_ids.resolution",
        "issue_ids.severity",
    )
    def _compute_counts(self):
        for batch in self:
            rows = batch.row_ids.filtered(lambda row: row.resolution != "supplier")
            orders = rows.filtered(lambda row: row.grain == "order")
            batch.file_count = len(batch.file_ids)
            batch.row_count = len(rows)
            batch.order_count = len({(row.provider, row.external_order_id) for row in orders})
            batch.line_count = len(rows) - len(orders)
            batch.new_order_count = len(
                {
                    (row.provider, row.external_order_id)
                    for row in orders
                    if row.resolution == "new"
                },
            )
            batch.known_order_count = len(
                {
                    (row.provider, row.external_order_id)
                    for row in orders
                    if row.resolution == "known"
                },
            )
            batch.conflicting_count = len(
                rows.filtered(lambda row: row.resolution == "conflicting"),
            )
            batch.blocking_issue_count = len(
                batch.issue_ids.filtered(lambda issue: issue.severity == "blocking"),
            )
            batch.advisory_issue_count = len(batch.issue_ids) - batch.blocking_issue_count

    # -- reading -----------------------------------------------------------

    def action_parse(self):
        """Read every file, resolve what it describes, and report. Idempotent."""
        for batch in self:
            if not batch.file_ids:
                raise UserError(self.env._("Add at least one export file to read."))
            batch.row_ids.unlink()
            batch.issue_ids.unlink()
            parsed = batch.file_ids._recognise()
            batch._report_unreadable_files()
            rows = batch._store_rows(parsed)
            batch._resolve(rows)
            batch._check_conflicts(rows)
            batch._check_order_money(rows)
            batch.write(batch._period(rows))
            batch.write({"state": "parsed", "report": batch._build_report()})
        return True

    def action_reset(self):
        """Return the batch to draft, keeping the files that were dropped."""
        self.row_ids.unlink()
        self.issue_ids.unlink()
        self.write({"state": "draft", "report": False, "period_start": False, "period_end": False})
        return True

    # -- internals ---------------------------------------------------------

    def _report_unreadable_files(self):
        self.ensure_one()
        for source in self.file_ids.filtered(lambda item: item.state == "unreadable"):
            self._raise_issue(
                "unreadable_file",
                self.env._("%(name)s was not recognised", name=source.name),
                note=source.note,
            )

    def _store_rows(self, parsed):
        """Create the batch's rows, keeping the first format that states a fact.

        Channels export the same sale in several layouts.  When two layouts
        agree, the higher-precedence one is kept and the other is dropped
        silently; when they disagree on a shared field, the row is marked
        conflicting so nobody has to guess which export was right.
        """
        self.ensure_one()
        best = {}
        conflicts = defaultdict(list)
        for source in self.file_ids:
            entry = parsed.get(source.id)
            if entry is None:
                continue
            fmt, rows = entry
            for row in rows:
                key = (row.provider, row.external_order_id, row.grain, row.external_line_id)
                values = parsers.jsonable(row.values)
                candidate = (fmt.precedence, source, row, values)
                held = best.get(key)
                if held is None:
                    best[key] = candidate
                    continue
                disagreement = _disagreement(held[3], values)
                if disagreement:
                    conflicts[key].append((held[2].format_id, row.format_id, disagreement))
                if candidate[0] < held[0]:
                    best[key] = candidate
        created = self.env["b2c.import.row"].create(
            [
                {
                    "batch_id": self.id,
                    "file_id": source.id,
                    "format_id": row.format_id,
                    "provider": row.provider,
                    "grain": row.grain,
                    "external_order_id": row.external_order_id,
                    "external_line_id": row.external_line_id or False,
                    "source_row_number": row.row_number,
                    "occurred_at": row.occurred_at,
                    "payload_digest": row.payload_digest,
                    "values": values,
                    "payload": parsers.jsonable(row.payload),
                }
                for _precedence, source, row, values in best.values()
            ],
        )
        self._report_payload_conflicts(created, conflicts)
        return created

    def _report_payload_conflicts(self, rows, conflicts):
        self.ensure_one()
        by_key = {
            (row.provider, row.external_order_id, row.grain, row.external_line_id or ""): row
            for row in rows
        }
        for key, disagreements in conflicts.items():
            row = by_key.get((key[0], key[1], key[2], key[3] or ""))
            for held_format, other_format, fields_differing in disagreements:
                self._raise_issue(
                    "payload_conflict",
                    self.env._(
                        "%(held)s and %(other)s disagree about order %(order)s",
                        held=held_format,
                        other=other_format,
                        order=key[1],
                    ),
                    external_order_id=key[1],
                    row=row,
                    note="\n".join(
                        f"{name}: {held!r} vs {other!r}"
                        for name, held, other in fields_differing
                    ),
                )

    def _resolve(self, rows):
        """Mark every row against the commerce Odoo already holds."""
        self.ensure_one()
        channels = self._channels()
        matches = self._known_orders({row.external_order_id for row in rows})
        headers = {
            (row.provider, row.external_order_id)
            for row in rows
            if row.grain == "order"
        }
        for row in rows:
            found = matches.get(row.external_order_id)
            if not found:
                if row.grain == "line" and (row.provider, row.external_order_id) not in headers:
                    row.resolution = "orphan_line"
                    continue
                row.resolution = "new"
                continue
            if len(found) > 1:
                row.write({"resolution": "conflicting", "order_id": found[0].id})
                continue
            channel = channels.get(row.provider)
            if channel and found.channel_id and found.channel_id != channel:
                row.write({"resolution": "conflicting", "order_id": found.id})
                continue
            row.write({"resolution": "known", "order_id": found.id})

    def _channels(self):
        """Return the configured channel each export's provider sells through.

        A channel outlives the systems that served it: a Medusa order imported
        from the legacy shop and one exported by Medusa today are the same
        commerce, so identity is compared on the channel rather than on which
        provider happened to state the fact.
        """
        self.ensure_one()
        channels = self.env["b2c.channel"].search([("company_id", "=", self.company_id.id)])
        by_code = {channel.code: channel for channel in channels}
        return {
            provider: by_code[provider]
            for provider, _label in self.env["b2c.order"]._fields["source_provider"].selection
            if provider in by_code
        }

    def _known_orders(self, external_ids):
        """Return every existing order each external reference resolves to.

        Medusa exports its human display number while Odoo holds the internal
        identifier, so both are matched.  A reference that resolves to more
        than one order is kept whole rather than collapsed, because that is a
        duplicate an operator has to see.
        """
        self.ensure_one()
        references = [reference for reference in external_ids if reference]
        if not references:
            return {}
        orders = self.env["b2c.order"].search(
            [
                ("company_id", "=", self.company_id.id),
                "|",
                ("external_order_id", "in", references),
                ("external_display_id", "in", references),
            ],
        )
        found = defaultdict(lambda: self.env["b2c.order"])
        for order in orders:
            if order.external_order_id in references:
                found[order.external_order_id] |= order
            elif order.external_display_id in references:
                found[order.external_display_id] |= order
        return found

    def _check_conflicts(self, rows):
        """Report each conflicting identity once, whichever rows carried it."""
        self.ensure_one()
        conflicting = rows.filtered(lambda item: item.resolution == "conflicting")
        reported = set()
        for row in conflicting:
            if row.external_order_id in reported:
                continue
            reported.add(row.external_order_id)
            held = rows.filtered(
                lambda item, reference=row.external_order_id:
                item.external_order_id == reference,
            ).order_id
            duplicates = self._known_orders({row.external_order_id}).get(row.external_order_id)
            if duplicates and len(duplicates) > 1:
                self._raise_issue(
                    "duplicate_order",
                    self.env._(
                        "Odoo holds order %(order)s more than once",
                        order=row.external_order_id,
                    ),
                    external_order_id=row.external_order_id,
                    row=row,
                    note="\n".join(
                        self.env._(
                            "%(name)s — %(provider)s, %(lines)s line(s), %(total)s",
                            name=order.name,
                            provider=order.source_provider,
                            lines=len(order.line_ids),
                            total=order.total_amount,
                        )
                        for order in duplicates
                    ),
                )
                continue
            self._raise_issue(
                "identity_conflict",
                self.env._(
                    "Order %(order)s belongs to %(held)s, not to %(read)s",
                    order=row.external_order_id,
                    held=held[:1].channel_id.display_name or held[:1].source_provider,
                    read=row.provider,
                ),
                external_order_id=row.external_order_id,
                row=row,
            )

    def _check_order_money(self, rows):
        """Report the money a channel's export does not carry, once per channel."""
        self.ensure_one()
        incomplete = defaultdict(list)
        for row in rows.filtered(lambda item: item.grain == "order"):
            values = row.values or {}
            residual = values.get("net_identity_residual")
            if residual is not None and Decimal(residual) != 0:
                self._raise_issue(
                    "net_identity",
                    self.env._(
                        "Order %(order)s no longer adds up to the net the channel paid",
                        order=row.external_order_id,
                    ),
                    external_order_id=row.external_order_id,
                    row=row,
                    note=self.env._("Difference: %(residual)s", residual=residual),
                )
            if row.resolution == "new" and "total_amount" not in values:
                incomplete[row.provider].append(row)
        for provider, provider_rows in incomplete.items():
            self._raise_issue(
                "order_money_missing",
                self.env._(
                    "%(count)s new %(provider)s order(s) have no shipping, discount or tax",
                    count=len(provider_rows),
                    provider=provider,
                ),
                row=provider_rows[0],
                severity="advisory",
                note=self.env._(
                    "The order total has to come from the payment processor for: %(orders)s",
                    orders=", ".join(sorted(row.external_order_id for row in provider_rows)),
                ),
            )

    def _raise_issue(self, kind, name, *, note=None, external_order_id=None, row=None,
                     severity="blocking", proposal=None):
        self.ensure_one()
        return self.env["b2c.import.issue"].create(
            {
                "batch_id": self.id,
                "row_id": row.id if row else False,
                "kind": kind,
                "severity": severity,
                "name": name,
                "external_order_id": external_order_id or False,
                "note": note or False,
                "proposal": proposal or False,
            },
        )

    def _country(self, values):
        name = (values.get("original_country") or "").strip()
        if not name:
            return self.env["res.country"]
        Country = self.env["res.country"]
        if len(name) == 2:
            found = Country.search([("code", "=", name.upper())], limit=1)
            if found:
                return found
        return Country.search([("name", "=ilike", name)], limit=1)

    def _period(self, rows):
        dates = [row.occurred_at for row in rows if row.occurred_at]
        if not dates:
            return {"period_start": False, "period_end": False}
        return {"period_start": min(dates).date(), "period_end": max(dates).date()}

    def _build_report(self):
        """Return a plain-language account of what the batch found."""
        self.ensure_one()
        self.invalidate_recordset(["row_ids", "issue_ids"])
        self._compute_counts()
        self._compute_mapping_counts()
        self._compute_fulfilment_count()
        lines = [
            self.env._(
                "%(files)s file(s) read, covering %(start)s to %(end)s.",
                files=self.file_count,
                start=self.period_start or self.env._("no date"),
                end=self.period_end or self.env._("no date"),
            ),
            self.env._(
                "%(orders)s order(s) and %(items)s line(s): %(new)s new, %(known)s already in Odoo.",
                orders=self.order_count,
                items=self.line_count,
                new=self.new_order_count,
                known=self.known_order_count,
            ),
        ]
        by_provider = defaultdict(Counter)
        for row in self.row_ids.filtered(
            lambda item: item.grain == "order" and item.resolution != "supplier",
        ):
            by_provider[row.provider][row.resolution] += 1
        for provider, counts in sorted(by_provider.items()):
            line = self.env._(
                "  %(provider)s: %(new)s new, %(known)s known.",
                provider=provider,
                new=counts["new"],
                known=counts["known"],
            )
            if counts["conflicting"]:
                line += self.env._(
                    " %(count)s to resolve.",
                    count=counts["conflicting"],
                )
            lines.append(line)
        if self.state in ("resolved", "applied"):
            lines.append(
                self.env._(
                    "%(mapped)s line(s) map to a product, %(unmapped)s still need one.",
                    mapped=self.mapped_line_count,
                    unmapped=self.unmapped_line_count,
                ),
            )
        if self.fulfilment_count:
            linked = len(self._fulfilment_rows().filtered("fulfilment_of_row_id"))
            lines.append(
                self.env._(
                    "%(count)s supplier fulfilment(s) read, %(linked)s tied to a sale.",
                    count=self.fulfilment_count,
                    linked=linked,
                ),
            )
        if self.blocking_issue_count or self.advisory_issue_count:
            lines.append(
                self.env._(
                    "%(blocking)s finding(s) to resolve and %(advisory)s to note.",
                    blocking=self.blocking_issue_count,
                    advisory=self.advisory_issue_count,
                ),
            )
        else:
            lines.append(self.env._("Nothing needs your attention."))
        return "\n".join(lines)


def _disagreement(held, other):
    """Return the facts two exports of the same sale state differently."""
    return [
        (name, held[name], other[name])
        for name in sorted(held.keys() & other.keys() & RECONCILED_VALUES)
        if held[name] != other[name]
    ]

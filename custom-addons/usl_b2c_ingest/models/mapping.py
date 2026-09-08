"""Deciding which product a channel sold.

A channel names a product the way its own shop does.  Odoo already holds that
translation as a verified ``b2c.product.alias`` for everything sold so far, so
mapping is a lookup, not a guess.  What is genuinely new gets proposed and left
for a person: nothing here invents a product, a variant or an attribute value.
"""

from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.usl_b2c_ingest.parsers import variation

#: Findings that describe the mapping, and are therefore replaced by each run.
MAPPING_ISSUE_KINDS = (
    "alias_derived",
    "destination_unknown",
    "ambiguous_product",
    "missing_variant",
    "unknown_product",
)


class B2cImportBatchMapping(models.Model):
    _inherit = "b2c.import.batch"

    mapped_line_count = fields.Integer(compute="_compute_mapping_counts", store=True)
    unmapped_line_count = fields.Integer(compute="_compute_mapping_counts", store=True)

    @api.depends("row_ids.grain", "row_ids.mapping", "row_ids.resolution")
    def _compute_mapping_counts(self):
        for batch in self:
            lines = batch.row_ids.filtered(
                lambda row: row.grain == "line" and row.resolution != "supplier",
            )
            batch.mapped_line_count = len(
                lines.filtered(lambda row: row.mapping in ("mapped", "derived")),
            )
            batch.unmapped_line_count = len(
                lines.filtered(lambda row: row.mapping == "unmapped"),
            )

    # -- resolving ---------------------------------------------------------

    def action_resolve(self):
        """Map every line to the product it sold. Changes nothing outside the batch."""
        for batch in self:
            if batch.state == "draft":
                raise UserError(batch.env._("Read the files before resolving them."))
            batch.issue_ids.filtered(
                lambda issue: issue.kind in MAPPING_ISSUE_KINDS,
            ).unlink()
            lines = batch.row_ids.filtered(
                lambda row: row.grain == "line" and row.resolution != "supplier",
            )
            (batch.row_ids - lines).mapping = "not_applicable"
            batch._map_lines(lines)
            batch._check_destinations()
            batch.write({"state": "resolved", "report": batch._build_report()})
        return True

    def _check_destinations(self):
        """Refuse to price a sale whose destination no export states.

        The rate a sale is taxed at is the rate of the country the goods went
        to, so an export that omits the country omits the tax.  Medusa's item
        exports do; its orders export does not.
        """
        self.ensure_one()
        stranded = defaultdict(list)
        for row in self.row_ids.filtered(
            lambda item: item.grain == "order" and item.resolution == "new",
        ):
            if not self._country(row.values or {}):
                stranded[row.provider].append(row)
        for provider, rows in stranded.items():
            self._raise_issue(
                "destination_unknown",
                self.env._(
                    "%(count)s new %(provider)s order(s) do not say where the goods went",
                    count=len(rows),
                    provider=provider,
                ),
                row=rows[0],
                note=self.env._(
                    "Without the destination there is no rate to charge. Add the "
                    "channel's orders export, which carries the shipping country "
                    "alongside the carriage and discount its item export omits.\n"
                    "Orders: %(orders)s",
                    orders=", ".join(sorted(row.external_order_id for row in rows)),
                ),
            )

    def _map_lines(self, lines):
        """Resolve each line, reporting anything a person has to decide."""
        self.ensure_one()
        aliases = self._verified_aliases()
        templates = self._templates_by_source_name(aliases)
        channels = self._channels()
        unresolved = defaultdict(list)
        for line in lines:
            values = line.values or {}
            alias = self._alias_for(channels, aliases, line, values)
            if alias is not None:
                line.write(
                    {
                        "alias_id": alias.id,
                        "product_id": alias.product_id.id,
                        "mapping": "mapped",
                    },
                )
                continue
            product, reason, missing = self._propose_product(
                channels, templates, line, values,
            )
            line.write(
                {
                    "product_id": product.id if product else False,
                    "mapping": "derived" if product else "unmapped",
                },
            )
            unresolved[reason, self._identity(line, values)].append((line, missing))
        self._report_unresolved(unresolved)

    @staticmethod
    def _identity(line, values):
        """Return what a channel called the thing it sold."""
        return (
            line.provider,
            (values.get("original_sku") or "").strip(),
            (values.get("original_name") or "").strip(),
            (values.get("original_variation") or "").strip(),
        )

    def _verified_aliases(self):
        """Return the verified translations, indexed by every way they are stated."""
        self.ensure_one()
        aliases = self.env["b2c.product.alias"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("mapping_state", "=", "verified"),
            ],
        )
        by_identity = {}
        by_sku = defaultdict(lambda: self.env["b2c.product.alias"])
        for alias in aliases:
            by_identity[

                    alias.channel_id.id,
                    (alias.original_sku or "").strip(),
                    (alias.original_name or "").strip(),
                    (alias.original_variation or "").strip(),

            ] = alias
            if alias.original_sku:
                by_sku[alias.channel_id.id, alias.original_sku.strip()] |= alias
        return {"records": aliases, "by_identity": by_identity, "by_sku": by_sku}

    def _alias_for(self, channels, aliases, line, values):
        """Return the verified alias that already translates this line, if any."""
        channel = channels.get(line.provider)
        if not channel:
            return None
        _provider, sku, name, variant = self._identity(line, values)
        exact = aliases["by_identity"].get((channel.id, sku, name, variant))
        if exact is not None:
            return exact
        if not sku:
            return None
        # A channel SKU that names exactly one product needs no other evidence.
        found = aliases["by_sku"].get((channel.id, sku))
        if found is not None and len(found.product_id) == 1:
            return found[0]
        return None

    def _templates_by_source_name(self, aliases):
        """Return the product template each channel product name resolves to.

        The same garment is listed under a different name on every channel, so
        the template is found through the names the channel itself has already
        used, never through Odoo's own product name.
        """
        self.ensure_one()
        found = defaultdict(lambda: self.env["product.template"])
        for alias in aliases["records"]:
            key = (alias.channel_id.id, variation.normalise(alias.original_name))
            found[key] |= alias.product_id.product_tmpl_id
        return found

    def _propose_product(self, channels, templates, line, values):
        """Return the variant this line sold, or say precisely what is missing."""
        channel = channels.get(line.provider)
        if not channel:
            return self.env["product.product"], "unknown_product", None
        key = (channel.id, variation.normalise(values.get("original_name")))
        candidates = templates.get(key)
        if not candidates:
            return self.env["product.product"], "unknown_product", None
        if len(candidates) > 1:
            return self.env["product.product"], "ambiguous_product", None
        variant, missing = self._variant_for(
            candidates,
            variation.tokens(values.get("original_variation")),
        )
        if variant:
            return variant, "alias_derived", None
        return self.env["product.product"], "missing_variant", missing

    def _variant_for(self, template, tokens):
        """Return the variant a channel's values name, and what it lacks.

        Only attributes that actually distinguish variants are considered: a
        template that offers one colour has no colour dimension, so a channel
        naming it states nothing that has to be matched.
        """
        distinguishing = [
            line
            for line in template.attribute_line_ids
            if len(line.value_ids) > 1
        ]
        wanted = []
        used = set()
        for attribute_line in distinguishing:
            names = attribute_line.value_ids.mapped("name")
            for index, token in enumerate(tokens):
                if index in used:
                    continue
                found = variation.match(names, token)
                if found is not None:
                    used.add(index)
                    wanted.append(found)
                    break
            else:
                return self.env["product.product"], (attribute_line, self._unused(tokens, used))
        expected = {variation.normalise(name) for name in wanted}
        for variant in template.product_variant_ids:
            actual = {
                variation.normalise(value.name)
                for value in variant.product_template_attribute_value_ids
                if len(value.attribute_line_id.value_ids) > 1
            }
            if actual == expected:
                return variant, None
        return self.env["product.product"], None

    @staticmethod
    def _unused(tokens, used):
        """Return the value a channel stated that no attribute could account for."""
        remaining = [token for index, token in enumerate(tokens) if index not in used]
        return remaining[0] if remaining else ""

    def _report_unresolved(self, unresolved):
        """Say once per unknown product exactly what a person has to decide."""
        self.ensure_one()
        for (reason, identity), found in sorted(unresolved.items()):
            provider, sku, name, variant = identity
            lines = [line for line, _missing in found]
            missing = next((item for _line, item in found if item), None)
            issue = self._raise_issue(
                reason,
                self._finding(reason, name or sku),
                row=lines[0],
                severity="advisory" if reason == "alias_derived" else "blocking",
                note=self.env._(
                    "Channel: %(provider)s\nSKU: %(sku)s\nVariation: %(variation)s\n"
                    "Orders: %(orders)s\n%(hint)s",
                    provider=provider,
                    sku=sku or self.env._("none"),
                    variation=variant or self.env._("none"),
                    orders=", ".join(sorted({line.external_order_id for line in lines})),
                    hint=self._hint(reason, lines[0], missing),
                ),
            )
            if missing:
                attribute_line, token = missing
                issue.write(
                    {
                        "proposal": {
                            "template_id": attribute_line.product_tmpl_id.id,
                            "attribute_line_id": attribute_line.id,
                        },
                        "proposed_value": token,
                    },
                )

    def _finding(self, reason, name):
        if reason == "unknown_product":
            return self.env._("Odoo does not know what %(name)s is", name=name)
        if reason == "ambiguous_product":
            return self.env._("%(name)s points at more than one product", name=name)
        if reason == "missing_variant":
            return self.env._("%(name)s is not held in this variant", name=name)
        return self.env._("%(name)s matches a product Odoo holds", name=name)

    def _hint(self, reason, line, missing=None):
        if reason == "alias_derived":
            return self.env._(
                "Proposed: %(product)s. Confirm it to map every future sale of it.",
                product=line.product_id.display_name,
            )
        if reason == "missing_variant" and missing:
            attribute_line, token = missing
            return self.env._(
                "%(attribute)s has no value for %(token)s. It offers %(values)s. "
                "Add the value to create the variant.",
                attribute=attribute_line.attribute_id.display_name,
                token=token or self.env._("what the channel stated"),
                values=", ".join(attribute_line.value_ids.mapped("name")),
            )
        if reason == "missing_variant":
            return self.env._("The catalogue holds no variant with these values.")
        return self.env._("Create the product, then resolve again.")

    # -- operator actions --------------------------------------------------

    def action_confirm_mappings(self):
        """Write the derived mappings as verified aliases.

        A derived mapping is a lookup, not a judgement: the channel's own
        product name already resolves to one template through aliases a person
        confirmed, and every distinguishing attribute the channel stated
        matched one existing variant.  Writing it as a verified alias is what
        makes the next sale of the same thing map without anyone being asked.
        """
        self.ensure_one()
        created = self._write_derived_aliases()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Mappings written"),
            "res_model": "b2c.product.alias",
            "view_mode": "list,form",
            "domain": [("id", "in", created.ids)],
        }

    def _write_derived_aliases(self):
        """Return the aliases written for this batch's derived mappings."""
        self.ensure_one()
        Alias = self.env["b2c.product.alias"]
        channels = self._channels()
        created = Alias
        seen = set()
        for line in self.row_ids.filtered(lambda row: row.mapping == "derived"):
            values = line.values or {}
            provider, sku, name, variant = self._identity(line, values)
            channel = channels.get(provider)
            if not channel or not line.product_id:
                continue
            key = (channel.id, sku, name, variant)
            if key in seen:
                continue
            seen.add(key)
            domain = [
                ("company_id", "=", self.company_id.id),
                ("channel_id", "=", channel.id),
                ("source_provider", "=", provider),
                ("original_sku", "=", sku or False),
                ("original_name", "=", name),
                ("original_variation", "=", variant or False),
            ]
            existing = Alias.search(domain, limit=1)
            if existing:
                continue
            created |= Alias.create(
                {
                    "company_id": self.company_id.id,
                    "channel_id": channel.id,
                    "source_provider": provider,
                    "original_sku": sku or False,
                    "original_name": name,
                    "original_variation": variant or False,
                    "external_listing_id": (values.get("external_listing_id") or "") or False,
                    "product_id": line.product_id.id,
                    "suggested_product_id": line.product_id.id,
                    "mapping_state": "verified",
                    "reviewed_by_id": self.env.user.id,
                    "reviewed_at": fields.Datetime.now(),
                    "evidence_note": self.env._(
                        "Derived by import %(batch)s from %(orders)s: every attribute the "
                        "channel stated matched this variant exactly.",
                        batch=self.name,
                        orders=line.external_order_id,
                    ),
                },
            )
        if created:
            self.row_ids.filtered(
                lambda row: row.mapping == "derived",
            ).mapping = "mapped"
        return created

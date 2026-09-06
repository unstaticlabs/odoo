"""Materialize the reviewed catalog and map every source line to a variant.

The reconstruction needs one Odoo product per garment, carrying the source
variation as real attributes, before any historical sale can become a native
Sales line. This builds exactly what the pinned specification describes: the
products, their attribute values, the channel aliases, and — for goods made
from own stock — the bill of materials that a sale consumes.

Existing products keep their identity. A padlock the catalog already holds is
matched by its internal reference, never duplicated.
"""

from __future__ import annotations

import re
from decimal import Decimal

from odoo import Command, models
from odoo.exceptions import UserError

from odoo.addons.usl_b2c_restore import private_evidence
from odoo.addons.usl_b2c_restore.native_plan import source_line_components

from .native_history.comparison import materialization_context

# Own-stock products the catalog already holds, matched by internal reference.
EXISTING_PRODUCT_CODES = {
    ("padlock-40mm", "Black"): "PADLOCK_BLACK",
    ("padlock-40mm", "Blue"): "PADLOCK_BLUE",
    ("padlock-40mm", "Brown"): "PADLOCK_BROWN",
    ("padlock-40mm", "Gold"): "PADLOCK_GOLD",
    ("padlock-40mm", "Green"): "PADLOCK_GREEN",
    ("padlock-40mm", "Orange"): "PADLOCK_ORANGE",
    ("padlock-40mm", "Purple"): "PADLOCK_PURPLE",
    ("padlock-40mm", "Red"): "PADLOCK_RED",
    ("padlock-master-20mm", "Black"): "PADLOCK_MASTER_9120EUR_BLACK",
    ("padlock-master-20mm", "Blue"): "PADLOCK_MASTER_9120EUR_BLUE",
    ("padlock-master-20mm", "Green"): "PADLOCK_MASTER_9120EUR_GREEN",
    ("padlock-master-20mm", "Pink"): "PADLOCK_MASTER_9120EUR_PINK",
    ("padlock-master-20mm", "Purple"): "PADLOCK_MASTER_9120EUR_PURPLE",
}

# The four colours a supplier pack unbuilds into must exist even where the
# channel evidence never sold one of them.
REQUIRED_COLOURS = {
    "padlock-master-20mm": ("Black", "Blue", "Green", "Pink", "Purple"),
}

# A colour that must exist is created exactly like this reviewed sibling.
SIBLING_PRODUCTS = {
    "padlock-master-20mm": "PADLOCK_MASTER_9120EUR_BLACK",
    "padlock-40mm": "PADLOCK_BLACK",
}

ATTRIBUTE_SEQUENCE = (
    "Size", "Colour", "Secondary colour", "Chain diameter", "Configuration",
    "Pattern", "Pack",
)


def _attribute_key(name):
    """Return the identity of an attribute name across spelling and case."""
    key = re.sub(r"\s+", " ", (name or "").strip()).casefold()
    return key.replace("colour", "color")


class UslB2cCatalogMaterializer(models.AbstractModel):
    _name = "usl.b2c.catalog.materializer"
    _description = "USL B2C Reviewed Catalog Materializer"

    def _ctx(self):
        return materialization_context()

    def _company(self):
        companies = self.env["res.company"].sudo().search(
            [("partner_id.vat", "=", "FR48983982950")], limit=2,
        )
        if len(companies) != 1:
            raise UserError("USL company identity is not unique.")
        return companies

    def _attribute(self, name, values):
        """Return one product attribute holding at least these values.

        The catalog already carries placeholder attributes from the source, in
        another spelling and case. Reuse them: a second "Colour" beside "color"
        makes every later lookup ambiguous.
        """
        Attribute = self.env["product.attribute"].sudo().with_context(**self._ctx())
        wanted = _attribute_key(name)
        candidates = Attribute.with_context(active_test=False).search([]).filtered(
            lambda item: _attribute_key(item.name) == wanted,
        )
        if len(candidates) > 1:
            raise UserError(
                f"Product attribute {name!r} is ambiguous: "
                f"{sorted(candidates.mapped('name'))!r}.",
            )
        attribute = candidates[:1]
        if attribute:
            if attribute.name != name:
                attribute.write({"name": name})
        else:
            attribute = Attribute.create(
                {"name": name, "create_variant": "always", "display_type": "radio"},
            )
        existing = {value.name: value for value in attribute.value_ids}
        missing = [value for value in values if value not in existing]
        if missing:
            self.env["product.attribute.value"].sudo().with_context(
                **self._ctx(),
            ).create([
                {"name": value, "attribute_id": attribute.id} for value in missing
            ])
            attribute.invalidate_recordset()
        return attribute

    def _template(self, company, product, attribute_values):
        """Return the template for one reviewed product, created once."""
        Template = self.env["product.template"].sudo().with_context(**self._ctx())
        template = Template.with_context(active_test=False).search(
            [("default_code", "=", f"B2C-{product['key'].upper()}")], limit=1,
        )
        own_stock = product["fulfilment_mode"] == "own_stock"
        values = {
            "name": re.sub(r"\s+", " ", product["name"].replace("\xa0", " ")).strip(),
            "default_code": f"B2C-{product['key'].upper()}",
            "company_id": False,
            "type": "consu",
            "is_storable": own_stock,
            "sale_ok": True,
            "purchase_ok": own_stock,
            "b2c_catalog_classification": "operational",
            "b2c_fulfilment_mode": product["fulfilment_mode"],
            "b2c_inventory_role": "saleable_unit" if own_stock else "ordinary",
            "b2c_opening_stock_state": (
                "theoretical_reconstructed" if own_stock else "not_applicable"
            ),
        }
        if template:
            template.write({k: v for k, v in values.items() if k != "default_code"})
        else:
            template = Template.create(values)
        lines = []
        for name in ATTRIBUTE_SEQUENCE:
            if name not in attribute_values:
                continue
            attribute = self._attribute(name, sorted(attribute_values[name]))
            wanted = {
                value.id
                for value in attribute.value_ids
                if value.name in attribute_values[name]
            }
            line = template.attribute_line_ids.filtered(
                lambda item, attribute=attribute: item.attribute_id == attribute,
            )
            if line:
                if set(line.value_ids.ids) != wanted:
                    line.write({"value_ids": [Command.set(sorted(wanted))]})
            else:
                lines.append(
                    Command.create(
                        {"attribute_id": attribute.id, "value_ids": [Command.set(sorted(wanted))]},
                    ),
                )
        if lines:
            template.write({"attribute_line_ids": lines})
        return template

    def _variant(self, template, attributes):
        """Return the variant carrying these attribute values.

        An attribute with a single value does not create a dimension in Odoo's
        matrix, so match on the values that actually distinguish one variant
        from another rather than on every value the source recorded.
        """
        wanted = frozenset(attributes.values())
        by_values = {
            frozenset(
                value.name
                for value in variant.product_template_variant_value_ids.product_attribute_value_id
            ): variant
            for variant in template.product_variant_ids
        }
        if wanted in by_values:
            return by_values[wanted]
        distinguishing = frozenset().union(*by_values) if by_values else frozenset()
        narrowed = wanted & distinguishing
        if narrowed in by_values:
            return by_values[narrowed]
        if len(template.product_variant_ids) == 1:
            return template.product_variant_ids
        raise UserError(
            f"{template.display_name} has no variant for {attributes!r}; "
            f"it offers {sorted(sorted(values) for values in by_values)!r}.",
        )

    def _own_stock_product(self, code, product_key, colour):
        """Return an own-stock product, created from its reviewed sibling.

        A colour the supplier pack unbuilds into is a real physical unit even
        where the catalog never held it: it is created exactly like the colour
        that does exist, so cost, unit and classification stay identical.
        """
        Product = self.env["product.product"].sudo().with_context(active_test=False)
        product = Product.search([("default_code", "=", code)], limit=2)
        if len(product) == 1:
            return product
        if len(product) > 1:
            raise UserError(f"Internal reference {code!r} identifies {len(product)} products.")
        sibling_code = SIBLING_PRODUCTS.get(product_key)
        sibling = Product.search([("default_code", "=", sibling_code)], limit=2)
        if len(sibling) != 1:
            raise UserError(
                f"Cannot create {code!r}: its reviewed sibling {sibling_code!r} "
                "must identify exactly one product.",
            )
        template = sibling.product_tmpl_id
        created = self.env["product.template"].sudo().with_context(**self._ctx()).create(
            {
                "name": re.sub(
                    r"—\s*\S+\s*—", f"— {colour} —", template.name, count=1,
                ) if "—" in template.name else f"{template.name} — {colour}",
                "default_code": code,
                "company_id": template.company_id.id or False,
                "type": template.type,
                "is_storable": template.is_storable,
                "categ_id": template.categ_id.id,
                "uom_id": template.uom_id.id,
                "sale_ok": template.sale_ok,
                "purchase_ok": template.purchase_ok,
                "standard_price": template.standard_price,
                "b2c_catalog_classification": template.b2c_catalog_classification,
                "b2c_fulfilment_mode": template.b2c_fulfilment_mode,
                "b2c_inventory_role": template.b2c_inventory_role,
                "b2c_opening_stock_state": template.b2c_opening_stock_state,
            },
        )
        return created.product_variant_id

    def _alias(self, company, alias, variant):
        """Point one channel identity at its reviewed product."""
        Alias = self.env["b2c.product.alias"].sudo().with_context(**self._ctx())
        # A legacy identity belongs to the channel it was sold through.
        code = "medusa" if alias["provider"] == "medusa_legacy" else alias["provider"]
        channel = self.env["b2c.channel"].sudo().search(
            [("company_id", "=", company.id), ("code", "=", code)], limit=1,
        )
        domain = [
            ("company_id", "=", company.id),
            ("source_provider", "=", alias["provider"]),
            ("original_sku", "=", alias["sku"] or False),
            ("original_name", "=", alias["name"]),
            ("original_variation", "=", alias["variation"] or False),
        ]
        record = Alias.search(domain, limit=1)
        values = {
            "company_id": company.id,
            "channel_id": channel.id or False,
            "source_provider": alias["provider"],
            "original_sku": alias["sku"] or False,
            "original_name": alias["name"],
            "original_variation": alias["variation"] or False,
            "source_sku_is_unique": False,
            "product_id": variant.id,
            "suggested_product_id": variant.id,
            "mapping_state": "verified",
            "evidence_note": (
                "The reviewed catalog names this exact source identity as this "
                "product variant."
            ),
        }
        if record:
            record.write(values)
        else:
            record = Alias.create(values)
        return record

    def _bill_of_materials(self, company, variant, alias):
        """Give an own-made product the exact demand one unit consumes."""
        components = source_line_components(alias["name"], alias["variation"], 1)
        if not components:
            return self.env["mrp.bom"]
        Bom = self.env["mrp.bom"].sudo().with_context(**self._ctx())
        existing = Bom.with_context(active_test=False).search(
            [("product_id", "=", variant.id), ("type", "=", "normal")], limit=1,
        )
        lines = []
        for code, quantity in sorted(components.items()):
            component = self.env["product.product"].sudo().search(
                [("default_code", "=", code)], limit=2,
            )
            if len(component) != 1:
                raise UserError(f"Component {code!r} must identify exactly one product.")
            lines.append(
                Command.create(
                    {
                        "product_id": component.id,
                        "product_qty": float(quantity),
                        "uom_id": component.uom_id.id,
                    },
                ),
            )
        values = {
            "product_tmpl_id": variant.product_tmpl_id.id,
            "product_id": variant.id,
            "product_qty": 1,
            "uom_id": variant.uom_id.id,
            "type": "normal",
            "company_id": company.id,
            "bom_line_ids": [Command.clear(), *lines],
        }
        if existing:
            existing.write(values)
            return existing
        return Bom.create(values)

    def materialize(self, apply=True):
        """Create the reviewed catalog and map every source line to a variant."""
        company = self._company()
        products = private_evidence.catalog_specification()
        report = {
            "products": 0, "variants": 0, "aliases": 0, "boms": 0,
            "reused_products": 0, "lines_mapped": 0,
        }
        alias_by_identity = {}
        for product in products:
            attribute_values = {}
            for variant in product["variants"]:
                for name, value in variant["attributes"].items():
                    attribute_values.setdefault(name, set()).add(value)
            for name, extra in REQUIRED_COLOURS.items():
                if product["key"] == name:
                    attribute_values.setdefault("Colour", set()).update(extra)
            codes = {
                value: EXISTING_PRODUCT_CODES.get((product["key"], value))
                for value in attribute_values.get("Colour", ())
            }
            reuses = product["key"] in {"padlock-40mm", "padlock-master-20mm"}
            template = None
            if not reuses and apply:
                template = self._template(company, product, attribute_values)
                report["products"] += 1
            for variant_spec in product["variants"]:
                attributes = variant_spec["attributes"]
                if reuses:
                    code = codes.get(attributes.get("Colour"))
                    if not code:
                        raise UserError(
                            f"{product['key']} has no reviewed product for "
                            f"{attributes!r}.",
                        )
                    variant = self._own_stock_product(
                        code, product["key"], attributes.get("Colour"),
                    )
                    report["reused_products"] += 1
                elif apply:
                    variant = self._variant(template, attributes)
                else:
                    continue
                report["variants"] += 1
                for alias in variant_spec["aliases"]:
                    record = self._alias(company, alias, variant)
                    alias_by_identity[
                        (alias["provider"], alias["sku"], alias["name"], alias["variation"])
                    ] = (record, variant)
                    report["aliases"] += 1
                if product["fulfilment_mode"] == "own_stock" and not reuses:
                    if self._bill_of_materials(company, variant, variant_spec["aliases"][0]):
                        report["boms"] += 1
            if apply and product["key"] in REQUIRED_COLOURS:
                # A colour the supplier pack unbuilds into must exist even
                # where no channel ever sold one.
                for colour in REQUIRED_COLOURS[product["key"]]:
                    code = EXISTING_PRODUCT_CODES.get((product["key"], colour))
                    if code:
                        self._own_stock_product(code, product["key"], colour)
        if apply:
            report["lines_mapped"] = self._map_lines(company, alias_by_identity)
        return report

    def _map_lines(self, company, alias_by_identity):
        """Point every canonical order line at its reviewed variant."""
        lines = self.env["b2c.order.line"].sudo().search(
            [("company_id", "=", company.id)],
        )
        mapped = 0
        unmatched = []
        for line in lines:
            key = (
                line.source_provider,
                line.original_sku or "",
                line.original_name or "",
                line.original_variation or "",
            )
            found = alias_by_identity.get(key)
            if not found:
                unmatched.append(key)
                continue
            record, variant = found
            values = {}
            if line.product_id != variant:
                values["product_id"] = variant.id
            if line.alias_id != record:
                values["alias_id"] = record.id
            if line.mapping_state != "verified":
                values["mapping_state"] = "verified"
            if values:
                line.with_context(**self._ctx()).write(values)
            mapped += 1
        if unmatched:
            sample = ", ".join(f"{key[2]}/{key[3]}" for key in unmatched[:5])
            raise UserError(
                f"{len(unmatched)} source lines are outside the reviewed catalog: {sample}",
            )
        return mapped


def run_catalog(env, apply=True):
    return env["usl.b2c.catalog.materializer"].materialize(apply=apply)

"""One-off, fail-closed normalization of the restored USL product master.

Run through Odoo shell with ``USL_B2C_CATALOG_MODE=dry-run`` first.  Set the
mode to ``apply`` only on an isolated clone or during an approved, checkpointed
upgrade window.  The script uses native business records and reviewed provider
identity; it never modifies raw provider evidence or manufactures stock.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict

from odoo import Command, fields


CATALOG_MODE = os.environ.get("USL_B2C_CATALOG_MODE", "dry-run")
EXPECTED_PROVIDERS = {"etsy", "medusa"}
BLOCKED_FAMILIES = {
    ("etsy", "1838821663"): (
        "The same Etsy variation is evidenced with conflicting source SKUs; "
        "the family requires reviewed provider evidence before normalization."
    ),
}

ETSY_FAMILIES = {
    "1827574902",
    "1835869928",
    "1835871476",
    "1838821663",
    "1850659208",
    "1890044896",
    "4298199085",
    "4299360338",
    "4299415409",
    "4301607154",
    "4302177338",
}
MEDUSA_POD_FAMILIES = {
    "2026 calendar",
    "2026 calendar sbfh",
    "beach towel - good boys obey - 2025.11",
    "certified good boy - cap - locktober 2025",
    "club hoodie - locktober 2025",
    "club tee - good boys club",
    "denim hat - good boys obey - 2025.11",
    "hoodie - good boys obey - 2025.11",
    "jersey - good boys obey - 2025.11",
    "locked boxer briefs - locktober 2025 edit",
    "locked swim trunks – chastity pride flag",
    "locktober champion - jersey - locktober 2025",
    "pet bowl – good boys obey - good boys club",
    "socks - good boys obey",
}
MEDUSA_STOCK_FAMILIES = {
    "ankle chains",
    "heavy chain 6mm",
    "master padlock 20mm",
    "slave padlock 40mm - locktober reward",
    "sub padlock 40mm",
    "the everyday collar",
    "wrist chains",
}
MEDUSA_FAMILIES = MEDUSA_POD_FAMILIES | MEDUSA_STOCK_FAMILIES

PADLOCK_CODES = {
    "PADLOCK_RED": "Red",
    "PADLOCK_BLUE": "Blue",
    "PADLOCK_ORANGE": "Orange",
    "PADLOCK_PURPLE": "Purple",
    "PADLOCK_GREEN": "Green",
    "PADLOCK_BROWN": "Brown",
    "PADLOCK_BLACK": "Black",
    "PADLOCK_GOLD": "Gold",
}
CHAIN_CODES = {
    "CHAIN_CM_3MM_AISI404_CNCHO10CHO": "3 mm",
    "CHAIN_CM_4MM_AISI404_CNCHO10CHO": "4 mm",
}
MASTER_PACK_CODES = {
    "GBC-ML-9120-QCOLNOP": "Assorted family pack — ASIN B001OXDCOI",
    "GBC-ML-9120-TBLK": "Black 2-pack — ASIN B001MTEROS",
    "GBC-ML-9120-QBLKNOP": "Black family pack — ASIN B001MTEROI",
}
MASTER_UNIT_CODES = {
    "PADLOCK_MASTER_9120EUR_BLACK": "Black",
    "PADLOCK_MASTER_9120EUR_BLUE": "Blue",
    "PADLOCK_MASTER_9120EUR_PINK": "Pink",
}

ATTRIBUTE_ALIASES = {
    "colour": {"color", "colour", "couleur"},
    "secondary colour": {"secondary color", "secondary colour"},
    "size": {"size", "taille"},
    "pattern": {"pattern", "motif"},
    "material": {"material", "matériau", "matière"},
    "diameter": {"diameter", "diamètre"},
    "configuration": {"configuration"},
    "package": {"package", "pack", "conditionnement"},
}
ETSY_ATTRIBUTE_KEYS = {
    "color": "colour",
    "secondary color": "secondary colour",
    "men's chest size": "size",
    "unisex shirt size": "size",
    "unisex pants size": "size",
    "custom property": "size",
    "pattern": "pattern",
}

PROTECTED_PRODUCT_REFERENCES = {
    "account.move.line": ("product_id",),
    "sale.order.line": ("product_id",),
    "purchase.order.line": ("product_id",),
    "stock.move": ("product_id",),
    "stock.move.line": ("product_id",),
    "stock.quant": ("product_id",),
    "stock.lot": ("product_id",),
    "mrp.bom": ("product_id", "product_tmpl_id"),
    "mrp.bom.line": ("product_id",),
    "mrp.production": ("product_id",),
    "product.pricelist.item": ("product_id", "product_tmpl_id"),
    "product.supplierinfo": ("product_id", "product_tmpl_id"),
    "product.packaging": ("product_id",),
}

COMMON_TEMPLATE_FIELDS = (
    "company_id",
    "categ_id",
    "uom_id",
    "uom_po_id",
    "type",
    "is_storable",
    "sale_ok",
    "purchase_ok",
    "tracking",
    "invoice_policy",
    "expense_policy",
    "taxes_id",
    "supplier_taxes_id",
    "route_ids",
    "list_price",
    "volume",
    "weight",
)


class CatalogNormalizationError(RuntimeError):
    pass


def normalized_text(value):
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def canonical_attribute_name(value):
    normalized = normalized_text(value)
    for key, aliases in ATTRIBUTE_ALIASES.items():
        if normalized in aliases or normalized == key:
            return key
    return normalized


def family_key(line):
    if line.source_provider == "etsy":
        return (line.external_listing_id or "").strip()
    return normalized_text(line.original_name)


def parse_etsy_variation(raw):
    result = []
    for item in (raw or "").split(","):
        key, separator, value = item.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise CatalogNormalizationError(
                f"Malformed Etsy variation {raw!r}",
            )
        attribute = ETSY_ATTRIBUTE_KEYS.get(normalized_text(key))
        if not attribute:
            raise CatalogNormalizationError(
                f"Unsupported Etsy variation key {key.strip()!r}",
            )
        result.append((attribute, value.strip()))
    return tuple(result) or (("configuration", "Default"),)


def parse_medusa_variation(key, raw):
    value = (raw or "").strip()
    if not value or normalized_text(value) == "default variant":
        return (("configuration", "Default"),)
    if key in {"slave padlock 40mm - locktober reward", "sub padlock 40mm"}:
        return (("colour", value),)
    if key == "master padlock 20mm":
        colour, separator, package = value.rpartition(" - ")
        if not separator:
            raise CatalogNormalizationError(
                f"Malformed Master Lock variation {value!r}",
            )
        return (("colour", colour.strip()), ("package", package.strip()))
    parts = tuple(part.strip() for part in value.split(" / ") if part.strip())
    if key in {"ankle chains", "the everyday collar", "wrist chains"}:
        if len(parts) != 3:
            raise CatalogNormalizationError(
                f"Malformed stocked-chain variation {value!r}",
            )
        return (
            ("size", parts[0]),
            ("diameter", parts[1]),
            ("configuration", parts[2]),
        )
    if key == "heavy chain 6mm":
        if len(parts) != 1:
            raise CatalogNormalizationError(
                f"Malformed heavy-chain variation {value!r}",
            )
        return (("size", parts[0]),)
    if len(parts) == 2:
        return (("size", parts[0]), ("colour", parts[1]))
    if len(parts) == 1:
        return (("configuration", parts[0]),)
    raise CatalogNormalizationError(f"Malformed Medusa variation {value!r}")


def parse_variation(provider, key, raw):
    if provider == "etsy":
        return parse_etsy_variation(raw)
    if provider == "medusa":
        return parse_medusa_variation(key, raw)
    raise CatalogNormalizationError(f"Unsupported provider {provider!r}")


def record_value(record, field_name):
    value = record[field_name]
    if hasattr(value, "ids"):
        return tuple(sorted(value.ids))
    return value


def field_matches(record, field_name, expected):
    current = record[field_name]
    if hasattr(current, "ids"):
        current = current.id if len(current) <= 1 else tuple(sorted(current.ids))
    return current == expected


class CatalogNormalizer:
    def __init__(self, env, mode):
        if mode not in {"dry-run", "apply"}:
            raise CatalogNormalizationError(
                "USL_B2C_CATALOG_MODE must be dry-run or apply.",
            )
        self.env = env
        self.mode = mode
        self.changes = defaultdict(int)
        self.blocked = {}
        self.created_templates = []
        self._attributes = {}
        self._source_product_value_ids = set()
        self.product_mapping = {}
        self.family_report = {}

    def _lock(self):
        self.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
            ["usl_b2c_inventory_foundations"],
        )
        if not self.env.cr.fetchone()[0]:
            raise CatalogNormalizationError(
                "Another inventory-foundations operation owns the migration lock.",
            )

    def _company_and_lines(self):
        lines = (
            self.env["b2c.order.line"]
            .sudo()
            .search([("source_provider", "in", sorted(EXPECTED_PROVIDERS))])
        )
        companies = lines.mapped("company_id")
        if len(companies) != 1:
            raise CatalogNormalizationError(
                "B2C catalog evidence does not resolve to exactly one company.",
            )
        observed = defaultdict(lambda: self.env["b2c.order.line"])
        for line in lines:
            observed[(line.source_provider, family_key(line))] |= line
        expected = {
            *(("etsy", key) for key in ETSY_FAMILIES),
            *(("medusa", key) for key in MEDUSA_FAMILIES),
        }
        unknown = sorted(set(observed) - expected)
        missing = sorted(expected - set(observed))
        if unknown or missing:
            raise CatalogNormalizationError(
                f"Catalog family perimeter changed: unknown={unknown}, missing={missing}",
            )
        return companies, observed

    def _accounting_fingerprint(self):
        result = {}
        for model_name, amount_fields in (
            ("account.move", ()),
            ("account.move.line", ("debit", "credit", "balance")),
            ("account.partial.reconcile", ("amount",)),
            ("account.full.reconcile", ()),
        ):
            records = self.env[model_name].sudo().search([])
            result[model_name] = {
                "count": len(records),
                **{
                    field_name: sum(records.mapped(field_name))
                    for field_name in amount_fields
                },
            }
        return result

    def _inventory_fingerprint(self):
        return {
            model_name: self.env[model_name].sudo().search_count([])
            for model_name in (
                "stock.move",
                "stock.move.line",
                "stock.picking",
                "stock.lot",
                "stock.landed.cost",
            )
        } | {
            "nonzero_quants": self.env["stock.quant"].sudo().search_count(
                [("quantity", "!=", 0)],
            ),
        }

    def _category(self, name, fallback_xmlid=None):
        category = self.env["product.category"].sudo().search(
            [("name", "=", name)],
            limit=2,
        )
        if len(category) == 1:
            return category
        if category:
            raise CatalogNormalizationError(f"Product category {name!r} is ambiguous.")
        if self.mode == "dry-run":
            return self.env["product.category"]
        parent = (
            self.env.ref(fallback_xmlid, raise_if_not_found=False)
            if fallback_xmlid
            else False
        )
        category = self.env["product.category"].sudo().create(
            {"name": name, "parent_id": parent.id if parent else False},
        )
        self.changes["categories_created"] += 1
        return category

    def _attribute(self, key):
        if key in self._attributes:
            return self._attributes[key]
        aliases = ATTRIBUTE_ALIASES[key]
        candidates = self.env["product.attribute"].sudo().search([]).filtered(
            lambda item: normalized_text(item.name) in aliases,
        )
        if len(candidates) > 1:
            raise CatalogNormalizationError(f"Product attribute {key!r} is ambiguous.")
        if candidates:
            attribute = candidates
        elif self.mode == "apply":
            attribute = self.env["product.attribute"].sudo().create(
                {
                    "name": key.title(),
                    "create_variant": "always",
                    "display_type": "radio",
                },
            )
            self.changes["attributes_created"] += 1
        else:
            attribute = self.env["product.attribute"]
        self._attributes[key] = attribute
        return attribute

    def _attribute_value(self, attribute, name):
        values = self.env["product.attribute.value"].sudo().search(
            [("attribute_id", "=", attribute.id)],
        ).filtered(lambda item: normalized_text(item.name) == normalized_text(name))
        if len(values) > 1:
            raise CatalogNormalizationError(
                f"Attribute value {attribute.name!r}/{name!r} is ambiguous.",
            )
        if values:
            return values
        value = self.env["product.attribute.value"].sudo().create(
            {"attribute_id": attribute.id, "name": name},
        )
        self.changes["attribute_values_created"] += 1
        return value

    def _ensure_variant_matrix(self, template, combinations):
        by_attribute = defaultdict(set)
        for combination in combinations:
            for attribute_name, value_name in combination:
                by_attribute[attribute_name].add(value_name)
        desired_lines = {}
        lines = []
        for attribute_name in sorted(by_attribute):
            attribute = self._attribute(attribute_name)
            values = [
                self._attribute_value(attribute, value_name)
                for value_name in sorted(by_attribute[attribute_name])
            ]
            desired_lines[canonical_attribute_name(attribute.name)] = {
                normalized_text(value.name) for value in values
            }
            lines.append(
                Command.create(
                    {
                        "attribute_id": attribute.id,
                        "value_ids": [Command.set([value.id for value in values])],
                    },
                ),
            )
        current_lines = {}
        for line in template.attribute_line_ids:
            key = canonical_attribute_name(line.attribute_id.name)
            if key in current_lines:
                raise CatalogNormalizationError(
                    f"Template {template.display_name!r} repeats attribute {key!r}.",
                )
            current_lines[key] = {
                normalized_text(value.name) for value in line.value_ids
            }
        if current_lines and current_lines != desired_lines:
            raise CatalogNormalizationError(
                f"Template {template.display_name!r} has a different existing "
                "variant matrix; the one-off normalizer will not rewrite it.",
            )
        if not current_lines:
            template.write({"attribute_line_ids": lines})
        template._create_variant_ids()
        expected = {
            tuple(sorted((canonical_attribute_name(a), normalized_text(v)) for a, v in combo))
            for combo in combinations
        }
        varying_attributes = {
            canonical_attribute_name(attribute_name)
            for attribute_name, value_names in by_attribute.items()
            if len(value_names) > 1
        }
        expected_by_variant_key = {}
        for combination in expected:
            variant_key = tuple(
                item for item in combination if item[0] in varying_attributes
            )
            if variant_key in expected_by_variant_key:
                raise CatalogNormalizationError(
                    f"Template {template.display_name!r} contains two source "
                    f"combinations for native variant key {variant_key!r}.",
                )
            expected_by_variant_key[variant_key] = combination
        variants = {}
        for variant in template.with_context(active_test=False).product_variant_ids:
            key = tuple(
                sorted(
                    (
                        canonical_attribute_name(value.attribute_id.name),
                        normalized_text(value.name),
                    )
                    for value in variant.product_template_variant_value_ids
                ),
            )
            expected_combination = expected_by_variant_key.get(key)
            if expected_combination:
                if expected_combination in variants:
                    raise CatalogNormalizationError(
                        f"Template {template.display_name!r} has duplicate variant {key!r}.",
                    )
                variants[expected_combination] = variant
                if not variant.active:
                    variant.active = True
            elif variant.active:
                variant.active = False
                self.changes["unsupported_combinations_archived"] += 1
        if set(variants) != expected:
            raise CatalogNormalizationError(
                f"Template {template.display_name!r} did not create the exact "
                f"variant set: expected={sorted(expected)!r}, "
                f"actual={sorted(variants)!r}.",
            )
        return variants

    def _protected_references(self, products):
        references = {}
        product_ids = products.ids
        template_ids = products.product_tmpl_id.ids
        for model_name, field_names in PROTECTED_PRODUCT_REFERENCES.items():
            if model_name not in self.env:
                continue
            for field_name in field_names:
                field = self.env[model_name]._fields.get(field_name)
                if not field or not field.store:
                    continue
                ids = template_ids if field.comodel_name == "product.template" else product_ids
                count = self.env[model_name].sudo().search_count([(field_name, "in", ids)])
                if count:
                    references[f"{model_name}.{field_name}"] = count
        return references

    def _compatible_templates(self, products):
        templates = products.product_tmpl_id
        representative = templates[0]
        differences = {}
        for field_name in COMMON_TEMPLATE_FIELDS:
            if field_name not in templates._fields:
                continue
            values = {record_value(template, field_name) for template in templates}
            if len(values) > 1:
                differences[field_name] = sorted(map(str, values))
        return representative, differences

    def _active_products_by_codes(self, codes):
        result = {}
        for code in codes:
            products = (
                self.env["product.product"]
                .sudo()
                .with_context(active_test=True)
                .search([("default_code", "=", code)], limit=2)
            )
            if len(products) != 1:
                raise CatalogNormalizationError(
                    f"Internal reference {code!r} does not identify one active product.",
                )
            result[code] = products
        return result

    def _move_product_history(self, source, target):
        existing_target = self.product_mapping.get(source.id)
        if existing_target and existing_target != target.id:
            raise CatalogNormalizationError(
                f"Product {source.id} maps to two replacement variants.",
            )
        self.product_mapping[source.id] = target.id
        values = self.env["product.value"].sudo().search(
            [("product_id", "=", source.id)],
        )
        if values:
            values.write({"product_id": target.id})
            self.changes["cost_history_rows_repointed"] += len(values)
        aliases = self.env["b2c.product.alias"].sudo().search(
            ["|", ("product_id", "=", source.id), ("suggested_product_id", "=", source.id)],
        )
        for alias in aliases:
            changes = {}
            if alias.product_id == source:
                changes["product_id"] = target.id
            if alias.suggested_product_id == source:
                changes["suggested_product_id"] = target.id
            alias.write(changes)
        lines = self.env["b2c.order.line"].sudo().search(
            [("product_id", "=", source.id)],
        )
        if lines:
            lines.write({"product_id": target.id})
            self.changes["b2c_lines_repointed"] += len(lines)
        target.with_context(disable_auto_revaluation=True).write(
            {
                "standard_price": source.standard_price,
                "default_code": source.default_code,
                "barcode": source.barcode,
                "image_variant_1920": source.image_variant_1920,
            },
        )

    def _consolidate_existing(self, canonical_name, code_values, attribute_name):
        products_by_code = self._active_products_by_codes(code_values)
        products = sum(products_by_code.values(), self.env["product.product"])
        if len(products.product_tmpl_id) == 1:
            template = products.product_tmpl_id
            expected_by_code = {
                code: ((canonical_attribute_name(attribute_name), normalized_text(value)),)
                for code, value in code_values.items()
            }
            actual_by_code = {
                code: tuple(
                    sorted(
                        (
                            canonical_attribute_name(value.attribute_id.name),
                            normalized_text(value.name),
                        )
                        for value in product.product_template_variant_value_ids
                    ),
                )
                for code, product in products_by_code.items()
            }
            if actual_by_code != expected_by_code:
                raise CatalogNormalizationError(
                    f"Existing canonical family {canonical_name!r} has an "
                    "unexpected variant matrix.",
                )
            return template, products_by_code
        references = self._protected_references(products)
        representative, differences = self._compatible_templates(products)
        if references or differences:
            raise CatalogNormalizationError(
                f"Cannot consolidate {canonical_name!r}: references={references}, "
                f"incompatible_fields={differences}",
            )
        if self.mode == "dry-run":
            return self.env["product.template"], products_by_code
        template = representative.copy(
            default={
                "name": canonical_name,
                "active": True,
                "default_code": False,
                "barcode": False,
            },
        )
        combinations = [((attribute_name, value),) for value in code_values.values()]
        variants = self._ensure_variant_matrix(template, combinations)
        for code, value in code_values.items():
            source = products_by_code[code]
            target = variants[((canonical_attribute_name(attribute_name), normalized_text(value)),)]
            self._move_product_history(source, target)
            products_by_code[code] = target
        old_templates = products.product_tmpl_id
        old_templates.write({"active": False})
        if hasattr(template, "message_post"):
            template.message_post(
                body=(
                    "Created as the canonical variant family from exact restored "
                    "product identities. Historical source templates remain archived."
                ),
            )
            for old in old_templates:
                old.message_post(
                    body=f"Archived after exact consolidation into {template.display_name}.",
                )
        self.changes["templates_consolidated"] += len(old_templates)
        self.created_templates.append(template.id)
        return template, products_by_code

    def _new_template(self, name, company, category, fulfilment_mode):
        if self.mode == "dry-run":
            return self.env["product.template"]
        template = self.env["product.template"].sudo().create(
            {
                "name": name,
                "company_id": company.id,
                "categ_id": category.id,
                "type": "consu",
                "is_storable": fulfilment_mode == "own_stock",
                "tracking": "none",
                "sale_ok": True,
                "purchase_ok": False,
                "list_price": 0,
                "b2c_catalog_classification": "operational",
                "b2c_fulfilment_mode": fulfilment_mode,
                "b2c_opening_stock_state": (
                    "not_evidenced" if fulfilment_mode == "own_stock" else "not_applicable"
                ),
            },
        )
        self.changes["templates_created"] += 1
        self.created_templates.append(template.id)
        return template

    def _latest_name(self, lines):
        return lines.sorted(lambda line: (line.order_date, line.id), reverse=True)[:1].original_name

    def _family_variants(self, provider, key, lines):
        raw_variants = {}
        for variation in sorted(set(lines.mapped("original_variation")), key=lambda item: item or ""):
            matching = lines.filtered(lambda line: line.original_variation == variation)
            skus = sorted({sku for sku in matching.mapped("original_sku") if sku})
            if len(skus) > 1:
                raise CatalogNormalizationError(
                    f"{provider}/{key}/{variation} has conflicting SKUs {skus}",
                )
            combination = parse_variation(provider, key, variation)
            if combination in raw_variants:
                raise CatalogNormalizationError(
                    f"{provider}/{key} has two source variations for {combination!r}",
                )
            raw_variants[combination] = {
                "variation": variation,
                "sku": skus[0] if skus else False,
                "lines": matching,
            }
        attributes = sorted(
            {attribute for combination in raw_variants for attribute, _value in combination},
        )
        variants = {}
        for combination, values in raw_variants.items():
            by_attribute = dict(combination)
            complete = tuple(
                (attribute, by_attribute.get(attribute, "Not applicable"))
                for attribute in attributes
            )
            if complete in variants:
                raise CatalogNormalizationError(
                    f"{provider}/{key} has two source variations for {complete!r}",
                )
            variants[complete] = values
        return variants

    def _existing_family_template(self, lines):
        templates = lines.mapped("alias_id.product_id.product_tmpl_id")
        if templates and all(line.mapping_state == "verified" for line in lines):
            if len(templates) != 1:
                raise CatalogNormalizationError(
                    "Verified family mappings point to multiple product templates.",
                )
            return templates
        return self.env["product.template"]

    def _verify_alias_and_lines(self, product, lines):
        grouped = defaultdict(lambda: self.env["b2c.order.line"])
        for line in lines:
            grouped[
                (
                    line.company_id.id,
                    line.channel_id.id,
                    line.source_provider,
                    line.original_sku or False,
                    line.external_listing_id or False,
                    line.original_name,
                    line.original_variation,
                )
            ] |= line
        for key, exact_lines in grouped.items():
            company_id, channel_id, provider, sku, listing, name, variation = key
            domain = [
                ("company_id", "=", company_id),
                ("channel_id", "=", channel_id),
                ("source_provider", "=", provider),
                ("original_sku", "=", sku),
                ("external_listing_id", "=", listing),
            ]
            if not sku:
                domain.extend(
                    [("original_name", "=", name), ("original_variation", "=", variation)],
                )
            alias = self.env["b2c.product.alias"].sudo().search(domain, limit=2)
            if len(alias) > 1:
                raise CatalogNormalizationError(f"Ambiguous B2C alias {key!r}")
            identity_values = {
                "company_id": company_id,
                "channel_id": channel_id,
                "source_provider": provider,
                "original_sku": sku,
                "external_listing_id": listing,
                "original_name": name,
                "original_variation": variation,
            }
            mapping_values = {
                "mapping_state": "verified",
                "product_id": product.id,
                "suggested_product_id": product.id,
                "evidence_id": exact_lines.sorted("id", reverse=True)[:1].evidence_id.id,
                "evidence_note": (
                    "Verified through the reviewed post-migration product-family "
                    "normalization; raw provider identity remains unchanged."
                ),
            }
            if alias:
                changed = {
                    field_name: value
                    for field_name, value in mapping_values.items()
                    if not field_matches(alias, field_name, value)
                }
                if changed:
                    changed.update(
                        {
                            "reviewed_by_id": self.env.user.id,
                            "reviewed_at": fields.Datetime.now(),
                        },
                    )
                    alias.write(changed)
            else:
                alias = self.env["b2c.product.alias"].sudo().create(
                    {
                        **identity_values,
                        **mapping_values,
                        "reviewed_by_id": self.env.user.id,
                        "reviewed_at": fields.Datetime.now(),
                    },
                )
                self.changes["aliases_created"] += 1
            line_changes = exact_lines.filtered(
                lambda line: line.alias_id != alias
                or line.product_id != product
                or line.mapping_state != "verified",
            )
            if line_changes:
                line_changes.write(
                    {
                        "alias_id": alias.id,
                        "product_id": product.id,
                        "mapping_state": "verified",
                    },
                )
                self.changes["lines_verified"] += len(line_changes)

    def _materialize_family(self, provider, key, lines, company, categories):
        variants = self._family_variants(provider, key, lines)
        existing = self._existing_family_template(lines)
        if existing:
            template = existing
        else:
            fulfilment_mode = (
                "printful"
                if provider == "etsy" or key in MEDUSA_POD_FAMILIES
                else "own_stock"
            )
            template = self._new_template(
                self._latest_name(lines),
                company,
                categories[fulfilment_mode],
                fulfilment_mode,
            )
        if self.mode == "dry-run":
            return
        matrix = self._ensure_variant_matrix(template, list(variants))
        for combination, item in variants.items():
            normalized = tuple(
                sorted((canonical_attribute_name(a), normalized_text(v)) for a, v in combination)
            )
            product = matrix[normalized]
            if item["sku"]:
                product.default_code = item["sku"]
            self._verify_alias_and_lines(product, item["lines"])

    def _map_padlock_family(self, provider, key, lines, products_by_code):
        variants = self._family_variants(provider, key, lines)
        if self.mode == "dry-run":
            return
        by_colour = {normalized_text(value): product for product, value in (
            (products_by_code[code], colour) for code, colour in PADLOCK_CODES.items()
        )}
        for combination, item in variants.items():
            if len(combination) != 1 or combination[0][0] != "colour":
                raise CatalogNormalizationError(
                    f"Unexpected 40 mm padlock variation {combination!r}",
                )
            product = by_colour.get(normalized_text(combination[0][1]))
            if not product or item["sku"] != product.default_code:
                raise CatalogNormalizationError(
                    f"40 mm padlock identity mismatch for {combination!r}",
                )
            self._verify_alias_and_lines(product, item["lines"])

    def _master_unit_template(self, company, category, lines):
        found = {}
        missing = []
        for code in MASTER_UNIT_CODES:
            products = (
                self.env["product.product"]
                .sudo()
                .with_context(active_test=True)
                .search([("default_code", "=", code)], limit=2)
            )
            if len(products) > 1:
                raise CatalogNormalizationError(
                    f"Internal reference {code!r} is ambiguous.",
                )
            if products:
                found[code] = products
            else:
                missing.append(code)
        if not missing:
            templates = sum(found.values(), self.env["product.product"]).product_tmpl_id
            if len(templates) != 1:
                raise CatalogNormalizationError(
                    "Master Lock physical-unit variants span multiple active templates.",
                )
            if self.mode == "apply":
                variants = self._family_variants("medusa", "master padlock 20mm", lines)
                by_colour = {
                    normalized_text(colour): found[code]
                    for code, colour in MASTER_UNIT_CODES.items()
                }
                for combination, item in variants.items():
                    product = by_colour.get(normalized_text(combination[0][1]))
                    if not product or item["sku"] != product.default_code:
                        raise CatalogNormalizationError(
                            f"Master Lock unit identity mismatch for {combination!r}",
                        )
                    self._verify_alias_and_lines(product, item["lines"])
            return templates, found
        if set(found) != {"PADLOCK_MASTER_9120EUR_BLACK"}:
            raise CatalogNormalizationError(
                f"Master Lock physical-unit family is partially materialized: "
                f"found={sorted(found)}, missing={sorted(missing)}",
            )
        source = found["PADLOCK_MASTER_9120EUR_BLACK"]
        references = self._protected_references(source)
        if references:
            raise CatalogNormalizationError(
                f"Master Lock physical unit has protected references: {references}",
            )
        if self.mode == "dry-run":
            return self.env["product.template"], {}
        template = source.product_tmpl_id.copy(
            default={
                "name": "Master Lock 9120 physical unit",
                "active": True,
                "default_code": False,
                "barcode": False,
                "categ_id": category.id,
            },
        )
        combinations = [(('colour', colour),) for colour in MASTER_UNIT_CODES.values()]
        matrix = self._ensure_variant_matrix(template, combinations)
        result = {}
        for code, colour in MASTER_UNIT_CODES.items():
            product = matrix[(("colour", normalized_text(colour)),)]
            product.default_code = code
            result[code] = product
        self._move_product_history(source, result["PADLOCK_MASTER_9120EUR_BLACK"])
        source.product_tmpl_id.active = False
        self.created_templates.append(template.id)
        self.changes["templates_consolidated"] += 1
        variants = self._family_variants("medusa", "master padlock 20mm", lines)
        by_colour = {normalized_text(colour): result[code] for code, colour in MASTER_UNIT_CODES.items()}
        for combination, item in variants.items():
            colour = normalized_text(combination[0][1])
            product = by_colour.get(colour)
            if not product or item["sku"] != product.default_code:
                raise CatalogNormalizationError(
                    f"Master Lock unit identity mismatch for {combination!r}",
                )
            self._verify_alias_and_lines(product, item["lines"])
        return template, result

    def _refresh_order_mapping_states(self, company):
        for order in self.env["b2c.order"].sudo().search([("company_id", "=", company.id)]):
            states = set(order.line_ids.mapped("mapping_state"))
            expected = (
                "not_applicable"
                if not states or states == {"not_applicable"}
                else "verified"
                if states == {"verified"}
                else "partial"
            )
            if order.mapping_state != expected:
                order.mapping_state = expected

    def _product_value_snapshot(self):
        rows = []
        for value in self.env["product.value"].sudo().search([], order="id"):
            rows.append(
                {
                    "id": value.id,
                    "product_id": value.product_id.id or False,
                    "lot_id": value.lot_id.id or False,
                    "move_id": value.move_id.id or False,
                    "value": value.value,
                    "company_id": value.company_id.id,
                    "date": fields.Datetime.to_string(value.date),
                    "user_id": value.user_id.id,
                    "description": value.description or False,
                },
            )
        return rows

    @staticmethod
    def _digest(rows):
        payload = json.dumps(rows, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def _validate_product_value_history(self, before_rows):
        expected_rows = []
        for row in before_rows:
            expected = dict(row)
            expected["product_id"] = self.product_mapping.get(
                row["product_id"],
                row["product_id"],
            )
            expected_rows.append(expected)
        after_rows = self._product_value_snapshot()
        if after_rows != expected_rows:
            raise CatalogNormalizationError(
                "Product cost-history identity or meaning changed outside the "
                "explicit old-to-new product mapping.",
            )
        return {
            "source_digest": self._digest(before_rows),
            "mapped_digest": self._digest(expected_rows),
            "row_count": len(after_rows),
        }

    def _evidence_snapshot(self):
        evidence = self.env["b2c.provider.evidence"].sudo().search([], order="id")
        rows = [
            {
                "id": item.id,
                "evidence_key": item.evidence_key,
                "source_checksum": item.source_checksum,
                "schema_digest": item.schema_digest,
                "payload_digest": item.payload_digest,
                "attachment_id": item.attachment_id.id or False,
            }
            for item in evidence
        ]
        return rows

    def _source_line_snapshot(self):
        lines = self.env["b2c.order.line"].sudo().search([], order="id")
        return [
            {
                "id": line.id,
                "source_provider": line.source_provider,
                "external_listing_id": line.external_listing_id or False,
                "original_sku": line.original_sku or False,
                "original_name": line.original_name or False,
                "original_variation": line.original_variation or False,
            }
            for line in lines
        ]

    def _alias_identity_snapshot(self, alias_ids=None):
        domain = [("id", "in", sorted(alias_ids))] if alias_ids is not None else []
        aliases = self.env["b2c.product.alias"].sudo().search(domain, order="id")
        return [
            {
                "id": alias.id,
                "company_id": alias.company_id.id,
                "channel_id": alias.channel_id.id,
                "source_provider": alias.source_provider,
                "original_sku": alias.original_sku or False,
                "external_listing_id": alias.external_listing_id or False,
                "original_name": alias.original_name or False,
                "original_variation": alias.original_variation or False,
            }
            for alias in aliases
        ]

    def _prepare_family_report(self, observed):
        self.family_report = {
            "native/40-mm-quandun-padlock": {
                "expected_variants": len(PADLOCK_CODES),
                "source_products": len(PADLOCK_CODES),
                "status": "planned" if self.mode == "dry-run" else "normalized",
            },
            "native/aisi-304-chain": {
                "expected_variants": len(CHAIN_CODES),
                "source_products": len(CHAIN_CODES),
                "status": "planned" if self.mode == "dry-run" else "normalized",
            },
            "native/master-lock-commercial-packs": {
                "expected_variants": len(MASTER_PACK_CODES),
                "source_products": len(MASTER_PACK_CODES),
                "status": "planned" if self.mode == "dry-run" else "normalized",
            },
        }
        for (provider, key), lines in sorted(observed.items()):
            report_key = f"{provider}/{key}"
            fulfilment = (
                "print_on_demand"
                if provider == "etsy" or key in MEDUSA_POD_FAMILIES
                else "stock"
            )
            self.family_report[report_key] = {
                "fulfilment": fulfilment,
                "source_lines": len(lines),
                "source_variations": len(set(lines.mapped("original_variation"))),
                "status": (
                    "blocked"
                    if (provider, key) in BLOCKED_FAMILIES
                    else "planned"
                    if self.mode == "dry-run"
                    else "normalized"
                ),
            }

    def _remove_normalization_cost_rows(self):
        """Remove only history rows generated by creating replacement variants."""
        generated = self.env["product.value"].sudo().search(
            [("id", "not in", sorted(self._source_product_value_ids))],
        )
        unsafe = generated.filtered(lambda value: value.move_id or value.lot_id)
        if unsafe:
            raise CatalogNormalizationError(
                "Normalization unexpectedly created move- or lot-backed cost history.",
            )
        if generated:
            generated.unlink()
            self.changes["generated_cost_rows_removed"] += len(generated)

    def run(self):
        self._lock()
        before_product_values = self._product_value_snapshot()
        self._source_product_value_ids = {row["id"] for row in before_product_values}
        before_evidence = self._evidence_snapshot()
        before_source_lines = self._source_line_snapshot()
        before_alias_identity = self._alias_identity_snapshot()
        source_alias_ids = {row["id"] for row in before_alias_identity}
        company, observed = self._company_and_lines()
        self._prepare_family_report(observed)
        before_accounting = self._accounting_fingerprint()
        before_inventory = self._inventory_fingerprint()
        before = {
            "templates": self.env["product.template"].sudo().with_context(active_test=False).search_count([]),
            "variants": self.env["product.product"].sudo().with_context(active_test=False).search_count([]),
            "aliases": self.env["b2c.product.alias"].sudo().search_count([]),
            "evidence": self.env["b2c.provider.evidence"].sudo().search_count([]),
            "product_values": self.env["product.value"].sudo().search_count([]),
        }

        pod_category = self._category("GBC Print-on-Demand", "product.product_category_goods")
        finished_category = self._category("GBC Finished Products")
        resale_category = self._category("GBC Resale Goods")
        categories = {"printful": pod_category, "own_stock": finished_category}

        _padlock_template, padlock_products = self._consolidate_existing(
            "40 mm Quandun padlock",
            PADLOCK_CODES,
            "colour",
        )
        self._consolidate_existing(
            "AISI 304 stainless chain",
            CHAIN_CODES,
            "diameter",
        )
        self._consolidate_existing(
            "Master Lock 9120 commercial pack",
            MASTER_PACK_CODES,
            "package",
        )
        for family, reason in BLOCKED_FAMILIES.items():
            self.blocked[f"{family[0]}/{family[1]}"] = reason

        for (provider, key), lines in sorted(observed.items()):
            if (provider, key) in BLOCKED_FAMILIES:
                continue
            if provider == "medusa" and key in {
                "slave padlock 40mm - locktober reward",
                "sub padlock 40mm",
            }:
                self._map_padlock_family(provider, key, lines, padlock_products)
                continue
            if provider == "medusa" and key == "master padlock 20mm":
                self._master_unit_template(company, resale_category, lines)
                continue
            changes_before = dict(self.changes)
            templates_before = list(self.created_templates)
            try:
                with self.env.cr.savepoint():
                    self._materialize_family(
                        provider,
                        key,
                        lines,
                        company,
                        categories,
                    )
            except CatalogNormalizationError as error:
                self.changes = defaultdict(int, changes_before)
                self.created_templates = templates_before
                self._attributes = {}
                self.blocked[f"{provider}/{key}"] = str(error)
                self.family_report[f"{provider}/{key}"]["status"] = "blocked"

        if self.mode == "apply":
            self._refresh_order_mapping_states(company)
            self._remove_normalization_cost_rows()

        after_accounting = self._accounting_fingerprint()
        after_inventory = self._inventory_fingerprint()
        if after_accounting != before_accounting:
            raise CatalogNormalizationError("Accounting fingerprint changed.")
        if after_inventory != before_inventory:
            raise CatalogNormalizationError("Historical stock fingerprint changed.")
        after = {
            "templates": self.env["product.template"].sudo().with_context(active_test=False).search_count([]),
            "variants": self.env["product.product"].sudo().with_context(active_test=False).search_count([]),
            "aliases": self.env["b2c.product.alias"].sudo().search_count([]),
            "evidence": self.env["b2c.provider.evidence"].sudo().search_count([]),
            "product_values": self.env["product.value"].sudo().search_count([]),
        }
        if after["evidence"] != before["evidence"]:
            raise CatalogNormalizationError("Raw provider evidence count changed.")
        if after["product_values"] != before["product_values"]:
            raise CatalogNormalizationError("Product cost-history row count changed.")
        if self._evidence_snapshot() != before_evidence:
            raise CatalogNormalizationError("Raw provider evidence identity changed.")
        if self._source_line_snapshot() != before_source_lines:
            raise CatalogNormalizationError("Raw B2C order-line identity changed.")
        if self._alias_identity_snapshot(source_alias_ids) != before_alias_identity:
            raise CatalogNormalizationError("A restored B2C alias identity changed.")
        product_value_evidence = self._validate_product_value_history(
            before_product_values,
        )
        evidence_digest = self._digest(before_evidence)
        source_line_digest = self._digest(before_source_lines)
        source_document_ids = sorted(
            {row["attachment_id"] for row in before_evidence if row["attachment_id"]},
        )
        report = {
            "schema": "usl-inventory-foundations-normalization-v1",
            "mode": self.mode,
            "company_id": company.id,
            "before": before,
            "after": after,
            "changes": dict(sorted(self.changes.items())),
            "blocked_families": dict(sorted(self.blocked.items())),
            "family_perimeter": dict(sorted(self.family_report.items())),
            "created_template_ids": sorted(self.created_templates),
            "product_mapping": {
                str(source_id): target_id
                for source_id, target_id in sorted(self.product_mapping.items())
            },
            "product_value_evidence": product_value_evidence,
            "raw_evidence_digest": evidence_digest,
            "source_line_digest": source_line_digest,
            "source_document_count": len(source_document_ids),
            "accounting_fingerprint": after_accounting,
            "inventory_fingerprint": after_inventory,
        }
        print("USL_INVENTORY_FOUNDATIONS_REPORT=" + json.dumps(report, sort_keys=True))
        return report


if "env" in globals():
    CatalogNormalizer(env, CATALOG_MODE).run()

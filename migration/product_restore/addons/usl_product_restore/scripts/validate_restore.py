# ruff: noqa: F821, T201

import hashlib
import json
from decimal import Decimal

from odoo.addons.usl_product_restore.models.restore import (
    ProductSourceReader,
    source_options,
)


def normalized(value):
    if value is False or value is None:
        return None
    if isinstance(value, (Decimal, float)):
        decimal = Decimal(str(value)).normalize()
        return "0" if not decimal else format(decimal, "f")
    return value


def normalized_field(field_name, value):
    if field_name in {"color", "volume", "weight"} and not value:
        return "0"
    return normalized(value)


def digest(rows):
    return hashlib.sha256(
        json.dumps(
            rows,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode(),
    ).hexdigest()


def source_localized(value):
    english = run._text(value)
    french = value.get("fr_FR") if isinstance(value, dict) else None
    return {"en_US": english, "fr_FR": french or english}


def target_localized(record, field_name):
    return {
        language: record.with_context(lang=language)[field_name] or ""
        for language in ("en_US", "fr_FR")
    }


source = ProductSourceReader(source_options()).read()
run = env["usl.product.restore.run"].sudo().search([], order="id desc", limit=1)
assert run and run.status == "passed"
assert run.statistics_json["source"] == source["counts"]
assert run.statistics_json["target"] == source["counts"]


def traced(model, rows):
    records = (
        env[model]
        .sudo()
        .with_context(active_test=False)
        .search(
            [
                ("rebuild_source_model", "=", model),
                ("rebuild_source_id", "in", [row["id"] for row in rows] or [0]),
                ("rebuild_source_snapshot", "=", run.source_snapshot),
            ],
        )
    )
    result = {record.rebuild_source_id: record for record in records}
    assert len(result) == len(rows)
    assert len(records) == len(result)
    return result


categories = traced("product.category", source["categories"])
attributes = traced("product.attribute", source["attributes"])
templates = traced("product.template", source["templates"])
products = traced("product.product", source["products"])
pricelists = traced("product.pricelist", source["pricelists"])
companies = {
    record.rebuild_source_id: record
    for record in env["res.company"].sudo().with_context(active_test=False).search(
        [("rebuild_source_model", "=", "res.company")],
    )
}
xmlids = {}
for row in source["xmlids"]:
    xmlids.setdefault((row["model"], row["res_id"]), []).append(row["xmlid"])


def expected_reference(model, source_id, target):
    for xmlid in xmlids.get((model, source_id), []):
        if env.ref(xmlid, raise_if_not_found=False) == target:
            return xmlid
    return None


customer_taxes = {}
for row in source["customer_taxes"]:
    customer_taxes.setdefault(row["template_id"], []).append(row["tax_id"])
supplier_taxes = {}
for row in source["supplier_taxes"]:
    supplier_taxes.setdefault(row["template_id"], []).append(row["tax_id"])
source_product_by_template = {
    row["product_tmpl_id"]: row for row in source["products"]
}

source_rows = []
target_rows = []
template_fields = (
    "sequence", "color", "type", "service_tracking", "list_price", "sale_ok",
    "purchase_ok", "active", "can_be_expensed", "is_storable",
    "volume", "weight",
)
product_fields = ("default_code", "barcode", "volume", "weight", "active")
for row in source["templates"]:
    template = templates[row["id"]]
    source_product = source_product_by_template[row["id"]]
    product = products[source_product["id"]]
    source_rows.append(
        {
            "id": row["id"],
            "name": source_localized(row["name"]),
            "description": source_localized(row["description"]),
            "description_purchase": source_localized(row["description_purchase"]),
            "description_sale": source_localized(row["description_sale"]),
            **{field: normalized_field(field, row[field]) for field in template_fields},
            "category": row["categ_id"],
            "company": row["company_id"],
            "uom": next(iter(xmlids.get(("uom.uom", row["uom_id"]), [])), None),
            "customer_taxes": sorted(customer_taxes.get(row["id"], [])),
            "supplier_taxes": sorted(supplier_taxes.get(row["id"], [])),
            "product": {
                "id": source_product["id"],
                **{
                    field: normalized_field(field, source_product[field])
                    for field in product_fields
                },
                "standard_price": {
                    key: normalized(value)
                    for key, value in sorted(
                        (source_product["standard_price"] or {}).items(),
                    )
                },
            },
        },
    )
    target_rows.append(
        {
            "id": row["id"],
            "name": target_localized(template, "name"),
            "description": target_localized(template, "description"),
            "description_purchase": target_localized(
                template,
                "description_purchase",
            ),
            "description_sale": target_localized(template, "description_sale"),
            **{
                field: normalized_field(field, template[field])
                for field in template_fields
            },
            "category": template.categ_id.rebuild_source_id
            if row["categ_id"]
            else None,
            "company": template.company_id.rebuild_source_id or None,
            "uom": expected_reference("uom.uom", row["uom_id"], template.uom_id),
            "customer_taxes": sorted(template.taxes_id.mapped("rebuild_source_id")),
            "supplier_taxes": sorted(
                template.supplier_taxes_id.mapped("rebuild_source_id"),
            ),
            "product": {
                "id": product.rebuild_source_id,
                **{
                    field: normalized_field(field, product[field])
                    for field in product_fields
                },
                "standard_price": {
                    key: normalized(
                        product.with_company(companies[int(key)]).standard_price,
                    )
                    for key in sorted(source_product["standard_price"] or {})
                },
            },
        },
    )

category_source = [
    (row["id"], source_localized(row["name"]), row["parent_id"])
    for row in source["categories"]
]
category_target = [
    (
        source_id,
        target_localized(record, "name"),
        record.parent_id.rebuild_source_id or None,
    )
    for source_id, record in sorted(categories.items())
]
attribute_source = [
    (
        row["id"], source_localized(row["name"]), row["sequence"],
        row["create_variant"], row["display_type"], row["active"],
    )
    for row in source["attributes"]
]
attribute_target = [
    (
        source_id, target_localized(record, "name"), record.sequence, record.create_variant,
        record.display_type, record.active,
    )
    for source_id, record in sorted(attributes.items())
]
pricelist_source = [
    (
        row["id"], source_localized(row["name"]), row["sequence"], row["active"],
        row["company_id"],
        next(iter(xmlids.get(("res.currency", row["currency_id"]), [])), None),
    )
    for row in source["pricelists"]
]
pricelist_target = [
    (
        row["id"], target_localized(pricelists[row["id"]], "name"), pricelists[row["id"]].sequence,
        pricelists[row["id"]].active,
        pricelists[row["id"]].company_id.rebuild_source_id or None,
        expected_reference(
            "res.currency",
            row["currency_id"],
            pricelists[row["id"]].currency_id,
        ),
    )
    for row in source["pricelists"]
]
parity = {
    "categories": (digest(category_source), digest(category_target)),
    "attributes": (digest(attribute_source), digest(attribute_target)),
    "products": (digest(source_rows), digest(target_rows)),
    "pricelists": (digest(pricelist_source), digest(pricelist_target)),
}
examples = []
for source_row, target_row in zip(source_rows, target_rows, strict=True):
    fields = sorted(
        key
        for key in set(source_row) | set(target_row)
        if source_row.get(key) != target_row.get(key)
    )
    if fields:
        for localized_field in (
            "name",
            "description",
            "description_purchase",
            "description_sale",
        ):
            if source_row.get(localized_field) != target_row.get(localized_field):
                fields.extend(
                    localized_field + "." + language
                    for language in ("en_US", "fr_FR")
                    if source_row[localized_field].get(language)
                    != target_row[localized_field].get(language)
                )
        if source_row.get("product") != target_row.get("product"):
            fields.extend(
                "product." + key
                for key in set(source_row["product"]) | set(target_row["product"])
                if source_row["product"].get(key)
                != target_row["product"].get(key)
            )
        examples.append({"source_id": source_row["id"], "fields": fields})
assert all(left == right for left, right in parity.values()), {
    "parity": parity,
    "examples": examples[:12],
}
print(
    json.dumps(
        {
            "counts": source["counts"],
            "delegated_sales_fields_sha256": digest(
                [
                    (
                        row["id"],
                        row["service_type"],
                        row["expense_policy"],
                        row["invoice_policy"],
                    )
                    for row in source["templates"]
                ],
            ),
            "defaulted_category_source_ids": [
                row["id"] for row in source["templates"] if not row["categ_id"]
            ],
            "parity_sha256": {key: value[0] for key, value in parity.items()},
            "source_snapshot": run.source_snapshot,
        },
        indent=2,
        sort_keys=True,
    ),
)

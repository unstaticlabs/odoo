# ruff: noqa: F821, T201

import hashlib
import json
from decimal import Decimal
from itertools import zip_longest

from odoo.addons.usl_product_restore.models.restore import (
    EXPECTED_NATIVE_BASELINE,
    ProductSourceReader,
    source_binary,
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
assert source["native_baseline"] == EXPECTED_NATIVE_BASELINE
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
product_values = traced("product.value", source["product_values"])
warehouses = traced("stock.warehouse", source["warehouses"])
locations = traced("stock.location", source["locations"])
routes = traced("stock.route", source["routes"])
rules = traced("stock.rule", source["rules"])
picking_types = traced("stock.picking.type", source["picking_types"])
companies = {
    record.rebuild_source_id: record
    for record in env["res.company"].sudo().with_context(active_test=False).search(
        [("rebuild_source_model", "=", "res.company")],
    )
}
source_user_by_partner = {
    row["partner_id"]: row["id"] for row in source["users"]
}
xmlids = {}
for row in source["xmlids"]:
    xmlids.setdefault((row["model"], row["res_id"]), []).append(row["xmlid"])


def expected_reference(model, source_id, target):
    for xmlid in xmlids.get((model, source_id), []):
        if env.ref(xmlid, raise_if_not_found=False) == target:
            return xmlid
    return None


def source_company_values(raw):
    return {
        str(key): normalized(value)
        for key, value in sorted((raw or {}).items(), key=lambda item: int(item[0]))
    }


def target_company_values(
    record,
    field_name,
    raw,
    *,
    relation=False,
    user_relation=False,
):
    result = {}
    for key in sorted((raw or {}), key=int):
        value = record.with_company(companies[int(key)])[field_name]
        if user_relation:
            value = target_source_user(value)
        elif relation:
            value = value.rebuild_source_id if value else None
        result[str(key)] = normalized(value)
    return result


def target_source_user(user):
    if not user:
        return None
    return source_user_by_partner.get(user.partner_id.rebuild_source_id)


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
    "volume", "weight", "service_type", "reinvoice_policy", "invoice_policy",
    "tracking", "lot_valuated", "hs_code", "purchase_method",
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
            "description_picking": source_localized(row["description_picking"]),
            "description_pickingout": source_localized(row["description_pickingout"]),
            "description_pickingin": source_localized(row["description_pickingin"]),
            **{field: normalized_field(field, row[field]) for field in template_fields},
            "category": row["categ_id"],
            "company": row["company_id"],
            "uom": next(iter(xmlids.get(("uom.uom", row["uom_id"]), [])), None),
            "lot_sequence": next(
                iter(xmlids.get(("ir.sequence", row["lot_sequence_id"]), [])),
                None,
            ),
            "country_of_origin": next(
                iter(xmlids.get(("res.country", row["country_of_origin"]), [])),
                None,
            ),
            "catalog_classification": (
                "capability_test"
                if not row["active"]
                and run._text(row["name"])
                == "__MCP_CAPABILITY_TEST_GBC_NATIVE_PRODUCT_TEMPLATE__"
                else "operational"
            ),
            "fulfilment_mode": (
                "not_applicable"
                if not row["active"]
                and run._text(row["name"])
                == "__MCP_CAPABILITY_TEST_GBC_NATIVE_PRODUCT_TEMPLATE__"
                else "own_stock"
                if row["is_storable"]
                else "unknown"
            ),
            "opening_stock_state": (
                "not_evidenced" if row["is_storable"] else "not_applicable"
            ),
            "sale_delay": source_company_values(row["sale_delay"]),
            "responsible_id": source_company_values(row["responsible_id"]),
            "property_account_income_id": source_company_values(
                row["property_account_income_id"],
            ),
            "property_account_expense_id": source_company_values(
                row["property_account_expense_id"],
            ),
            "property_stock_production": source_company_values(
                row["property_stock_production"],
            ),
            "property_stock_inventory": source_company_values(
                row["property_stock_inventory"],
            ),
            "property_price_difference_account_id": source_company_values(
                row["property_price_difference_account_id"],
            ),
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
            "description_picking": target_localized(template, "description_picking"),
            "description_pickingout": target_localized(
                template,
                "description_pickingout",
            ),
            "description_pickingin": target_localized(
                template,
                "description_pickingin",
            ),
            **{
                field: normalized_field(field, template[field])
                for field in template_fields
            },
            "category": template.categ_id.rebuild_source_id
            if row["categ_id"]
            else None,
            "company": template.company_id.rebuild_source_id or None,
            "uom": expected_reference("uom.uom", row["uom_id"], template.uom_id),
            "lot_sequence": expected_reference(
                "ir.sequence",
                row["lot_sequence_id"],
                template.lot_sequence_id,
            ),
            "country_of_origin": expected_reference(
                "res.country",
                row["country_of_origin"],
                template.country_of_origin,
            ),
            "catalog_classification": template.b2c_catalog_classification,
            "fulfilment_mode": template.b2c_fulfilment_mode,
            "opening_stock_state": template.b2c_opening_stock_state,
            "sale_delay": target_company_values(
                template,
                "sale_delay",
                row["sale_delay"],
            ),
            "responsible_id": target_company_values(
                template,
                "responsible_id",
                row["responsible_id"],
                user_relation=True,
            ),
            "property_account_income_id": target_company_values(
                template,
                "property_account_income_id",
                row["property_account_income_id"],
                relation=True,
            ),
            "property_account_expense_id": target_company_values(
                template,
                "property_account_expense_id",
                row["property_account_expense_id"],
                relation=True,
            ),
            "property_stock_production": target_company_values(
                template,
                "property_stock_production",
                row["property_stock_production"],
                relation=True,
            ),
            "property_stock_inventory": target_company_values(
                template,
                "property_stock_inventory",
                row["property_stock_inventory"],
                relation=True,
            ),
            "property_price_difference_account_id": target_company_values(
                template,
                "property_price_difference_account_id",
                row["property_price_difference_account_id"],
                relation=True,
            ),
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

category_property_fields = (
    "property_valuation",
    "property_cost_method",
    "property_account_income_categ_id",
    "property_account_expense_categ_id",
    "property_stock_journal",
    "property_stock_valuation_account_id",
    "property_price_difference_account_id",
    "property_stock_account_production_cost_id",
)
category_relation_fields = {
    "property_account_income_categ_id",
    "property_account_expense_categ_id",
    "property_stock_journal",
    "property_stock_valuation_account_id",
    "property_price_difference_account_id",
    "property_stock_account_production_cost_id",
}
category_source = [
    {
        "id": row["id"],
        "name": source_localized(row["name"]),
        "parent_id": row["parent_id"],
        # Community normalizes an unset properties definition to an empty
        # JSON list; the source stores the same business state as SQL NULL.
        "product_properties_definition": row["product_properties_definition"] or [],
        **{
            field_name: source_company_values(row[field_name])
            for field_name in category_property_fields
        },
    }
    for row in source["categories"]
]
category_target = [
    {
        "id": row["id"],
        "name": target_localized(categories[row["id"]], "name"),
        "parent_id": categories[row["id"]].parent_id.rebuild_source_id or None,
        "product_properties_definition": categories[
            row["id"]
        ].product_properties_definition,
        **{
            field_name: target_company_values(
                categories[row["id"]],
                field_name,
                row[field_name],
                relation=field_name in category_relation_fields,
            )
            for field_name in category_property_fields
            if field_name in categories[row["id"]]._fields
        },
    }
    for row in source["categories"]
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

product_value_source = [
    (
        row["id"],
        row["product_id"],
        row["company_id"],
        row["user_id"],
        row["description"],
        normalized(row["value"]),
        row["date"],
    )
    for row in source["product_values"]
]
product_value_target = [
    (
        row["id"],
        product_values[row["id"]].product_id.rebuild_source_id,
        product_values[row["id"]].company_id.rebuild_source_id,
        target_source_user(product_values[row["id"]].user_id),
        product_values[row["id"]].description,
        normalized(product_values[row["id"]].value),
        product_values[row["id"]].date,
    )
    for row in source["product_values"]
]

warehouse_source = [
    (
        row["id"], row["name"], row["code"], row["active"], row["sequence"],
        row["company_id"], row["reception_steps"], row["delivery_steps"],
        row["manufacture_steps"],
    )
    for row in source["warehouses"]
]
warehouse_target = [
    (
        row["id"], warehouses[row["id"]].name, warehouses[row["id"]].code,
        warehouses[row["id"]].active, warehouses[row["id"]].sequence,
        warehouses[row["id"]].company_id.rebuild_source_id,
        warehouses[row["id"]].reception_steps,
        warehouses[row["id"]].delivery_steps,
        warehouses[row["id"]].manufacture_steps,
    )
    for row in source["warehouses"]
]
location_source = [
    (
        row["id"], row["name"], row["usage"], row["active"], row["location_id"],
        row["company_id"], row["warehouse_id"], bool(row["replenish_location"]),
    )
    for row in source["locations"]
]
location_target = [
    (
        row["id"], locations[row["id"]].name, locations[row["id"]].usage,
        locations[row["id"]].active,
        locations[row["id"]].location_id.rebuild_source_id or None,
        locations[row["id"]].company_id.rebuild_source_id or None,
        locations[row["id"]].warehouse_id.rebuild_source_id or None,
        locations[row["id"]].replenish_location,
    )
    for row in source["locations"]
]
picking_type_source = [
    (
        row["id"], source_localized(row["name"]), row["code"], row["active"],
        row["warehouse_id"], row["company_id"], row["default_location_src_id"],
        row["default_location_dest_id"], row["return_picking_type_id"],
    )
    for row in source["picking_types"]
]
picking_type_target = [
    (
        row["id"], target_localized(picking_types[row["id"]], "name"),
        picking_types[row["id"]].code, picking_types[row["id"]].active,
        picking_types[row["id"]].warehouse_id.rebuild_source_id or None,
        picking_types[row["id"]].company_id.rebuild_source_id or None,
        picking_types[row["id"]].default_location_src_id.rebuild_source_id or None,
        picking_types[row["id"]].default_location_dest_id.rebuild_source_id or None,
        picking_types[row["id"]].return_picking_type_id.rebuild_source_id or None,
    )
    for row in source["picking_types"]
]
route_source = [
    (
        row["id"], source_localized(row["name"]), row["active"], row["sequence"],
        row["company_id"], bool(row["product_selectable"]),
        bool(row["product_categ_selectable"]), bool(row["warehouse_selectable"]),
        bool(row["package_type_selectable"]),
    )
    for row in source["routes"]
]
route_target = [
    (
        row["id"], target_localized(routes[row["id"]], "name"),
        routes[row["id"]].active, routes[row["id"]].sequence,
        routes[row["id"]].company_id.rebuild_source_id or None,
        routes[row["id"]].product_selectable,
        routes[row["id"]].product_categ_selectable,
        routes[row["id"]].warehouse_selectable,
        routes[row["id"]].package_type_selectable,
    )
    for row in source["routes"]
]
rule_source = [
    (
        row["id"], source_localized(row["name"]), row["active"], row["action"],
        row["procure_method"], row["route_id"], row["picking_type_id"],
        row["location_src_id"], row["location_dest_id"], row["warehouse_id"],
    )
    for row in source["rules"]
]
rule_target = [
    (
        row["id"], target_localized(rules[row["id"]], "name"),
        rules[row["id"]].active, rules[row["id"]].action,
        rules[row["id"]].procure_method,
        rules[row["id"]].route_id.rebuild_source_id,
        rules[row["id"]].picking_type_id.rebuild_source_id,
        rules[row["id"]].location_src_id.rebuild_source_id or None,
        rules[row["id"]].location_dest_id.rebuild_source_id,
        rules[row["id"]].warehouse_id.rebuild_source_id or None,
    )
    for row in source["rules"]
]
route_warehouse_source = [
    (row["route_id"], row["warehouse_id"])
    for row in source["route_warehouses"]
]
route_warehouse_target = sorted(
    (
        source_route_id,
        warehouse.rebuild_source_id,
    )
    for source_route_id, route in routes.items()
    for warehouse in route.warehouse_ids
    if warehouse.rebuild_source_id
)
parity = {
    "categories": (digest(category_source), digest(category_target)),
    "attributes": (digest(attribute_source), digest(attribute_target)),
    "products": (digest(source_rows), digest(target_rows)),
    "pricelists": (digest(pricelist_source), digest(pricelist_target)),
    "product_values": (digest(product_value_source), digest(product_value_target)),
    "warehouses": (digest(warehouse_source), digest(warehouse_target)),
    "locations": (digest(location_source), digest(location_target)),
    "picking_types": (digest(picking_type_source), digest(picking_type_target)),
    "routes": (digest(route_source), digest(route_target)),
    "rules": (digest(rule_source), digest(rule_target)),
    "route_warehouses": (
        digest(route_warehouse_source),
        digest(route_warehouse_target),
    ),
    "images": (
        digest(
            [
                (row["id"], row["res_id"], row["checksum"], row["file_size"])
                for row in source["images"]
            ],
        ),
        digest(
            [
                (
                    row["id"],
                    row["res_id"],
                    hashlib.sha1(
                        bytes(templates[row["res_id"]].image_1920),
                        usedforsecurity=False,
                    ).hexdigest(),
                    len(bytes(templates[row["res_id"]].image_1920)),
                )
                for row in source["images"]
            ],
        ),
    ),
}
parity_rows = {
    "categories": (category_source, category_target),
    "attributes": (attribute_source, attribute_target),
    "products": (source_rows, target_rows),
    "pricelists": (pricelist_source, pricelist_target),
    "product_values": (product_value_source, product_value_target),
    "warehouses": (warehouse_source, warehouse_target),
    "locations": (location_source, location_target),
    "picking_types": (picking_type_source, picking_type_target),
    "routes": (route_source, route_target),
    "rules": (rule_source, rule_target),
    "route_warehouses": (route_warehouse_source, route_warehouse_target),
}
parity_examples = {}
for key, (source_digest, target_digest) in parity.items():
    if source_digest == target_digest or key not in parity_rows:
        continue
    source_values, target_values = parity_rows[key]
    parity_examples[key] = [
        {"index": index, "source": source_value, "target": target_value}
        for index, (source_value, target_value) in enumerate(
            zip_longest(source_values, target_values),
        )
        if source_value != target_value
    ][:12]
for row in source["images"]:
    assert bytes(templates[row["res_id"]].image_1920) == source_binary(row)
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
    "parity_examples": parity_examples,
}
print(
    json.dumps(
        {
            "counts": source["counts"],
            "native_baseline": source["native_baseline"],
            "restored_sales_fields_sha256": digest(
                [
                    (
                        row["id"],
                        row["service_type"],
                        row["reinvoice_policy"],
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

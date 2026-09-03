import hashlib
import os
from pathlib import Path

import psycopg2
import psycopg2.extras

from odoo import Command, fields, models
from odoo.tools import BinaryBytes

RESTORE_REVISION = 2
EXPECTED_NATIVE_BASELINE = {
    "account_full_reconcile": 1467,
    "account_move": 5434,
    "account_move_line": 13024,
    "account_partial_reconcile": 2861,
    "active_product_templates": 45,
    "archived_product_templates": 1,
    "mrp_bom": 0,
    "mrp_production": 0,
    "payment_transaction": 0,
    "product_product": 46,
    "product_supplierinfo": 0,
    "product_template": 46,
    "product_value": 45,
    "products_with_internal_reference": 43,
    "purchase_order": 0,
    "purchase_order_line": 0,
    "quality_check": 0,
    "quality_point": 0,
    "sale_order": 0,
    "sale_order_line": 0,
    "stock_location": 23,
    "stock_move": 0,
    "stock_move_line": 0,
    "stock_picking": 0,
    "stock_picking_type": 11,
    "stock_quant": 0,
    "stock_route": 6,
    "stock_rule": 7,
    "stock_valuation_layer": 0,
    "stock_warehouse": 1,
    "storable_product_templates": 17,
    "templates_without_exactly_one_variant": 0,
}
SOURCE_FILESTORE = Path(
    os.getenv("PRODUCT_SOURCE_FILESTORE", "/mnt/accounting-source/filestore"),
).resolve()


def source_binary(row):
    path = (SOURCE_FILESTORE / row["store_fname"]).resolve()
    if SOURCE_FILESTORE not in path.parents or not path.is_file():
        raise RuntimeError(f"Product source attachment {row['id']} is missing or unsafe")
    content = path.read_bytes()
    if len(content) != row["file_size"]:
        raise RuntimeError(f"Product source attachment {row['id']} size changed")
    checksum = hashlib.sha1(content, usedforsecurity=False).hexdigest()
    if checksum != row["checksum"]:
        raise RuntimeError(f"Product source attachment {row['id']} checksum changed")
    return content


class ProductSourceReader:
    def __init__(self, options):
        self.options = options

    def _connect(self):
        connection = psycopg2.connect(
            host=self.options["host"],
            port=self.options["port"],
            user=self.options["user"],
            password=self.options["password"],
            dbname=self.options["database"],
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        connection.set_session(readonly=True, autocommit=False)
        return connection

    @staticmethod
    def _rows(cursor, query):
        cursor.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _scalar(cursor, query):
        cursor.execute(query)
        return next(iter(cursor.fetchone().values()))

    def _native_baseline(self, cursor):
        tables = {
            "account_full_reconcile": "account_full_reconcile",
            "account_move": "account_move",
            "account_move_line": "account_move_line",
            "account_partial_reconcile": "account_partial_reconcile",
            "mrp_bom": "mrp_bom",
            "mrp_production": "mrp_production",
            "payment_transaction": "payment_transaction",
            "product_product": "product_product",
            "product_supplierinfo": "product_supplierinfo",
            "product_template": "product_template",
            "product_value": "product_value",
            "purchase_order": "purchase_order",
            "purchase_order_line": "purchase_order_line",
            "quality_check": "quality_check",
            "quality_point": "quality_point",
            "sale_order": "sale_order",
            "sale_order_line": "sale_order_line",
            "stock_location": "stock_location",
            "stock_move": "stock_move",
            "stock_move_line": "stock_move_line",
            "stock_picking": "stock_picking",
            "stock_picking_type": "stock_picking_type",
            "stock_quant": "stock_quant",
            "stock_route": "stock_route",
            "stock_rule": "stock_rule",
            "stock_valuation_layer": "stock_valuation_layer",
            "stock_warehouse": "stock_warehouse",
        }
        result = {}
        for label, table in tables.items():
            cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
            exists = next(iter(cursor.fetchone().values()))
            result[label] = (
                int(self._scalar(cursor, f"SELECT count(*) FROM {table}"))
                if exists
                else 0
            )
        result.update(
            {
                "active_product_templates": int(
                    self._scalar(cursor, "SELECT count(*) FROM product_template WHERE active"),
                ),
                "archived_product_templates": int(
                    self._scalar(cursor, "SELECT count(*) FROM product_template WHERE NOT active"),
                ),
                "products_with_internal_reference": int(
                    self._scalar(
                        cursor,
                        "SELECT count(*) FROM product_product "
                        "WHERE NULLIF(BTRIM(default_code), '') IS NOT NULL",
                    ),
                ),
                "storable_product_templates": int(
                    self._scalar(
                        cursor,
                        "SELECT count(*) FROM product_template WHERE is_storable",
                    ),
                ),
                "templates_without_exactly_one_variant": int(
                    self._scalar(
                        cursor,
                        "SELECT count(*) FROM ("
                        "SELECT template.id FROM product_template template "
                        "LEFT JOIN product_product product "
                        "ON product.product_tmpl_id = template.id "
                        "GROUP BY template.id HAVING count(product.id) != 1"
                        ") variants",
                    ),
                ),
            },
        )
        return result

    def read(self):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE accounting_source_ro")
            cursor.execute("SHOW transaction_read_only")
            if cursor.fetchone()["transaction_read_only"] != "on":
                message = "Product source connection is not read-only"
                raise RuntimeError(message)
            cursor.execute("SELECT current_user")
            if cursor.fetchone()["current_user"] != "accounting_source_ro":
                message = "Product source role is not accounting_source_ro"
                raise RuntimeError(message)
            result = {
                "native_baseline": self._native_baseline(cursor),
                "categories": self._rows(
                    cursor,
                    "SELECT id, parent_id, name, product_properties_definition, "
                    "property_account_income_categ_id, property_account_expense_categ_id, "
                    "property_valuation, property_cost_method, property_stock_journal, "
                    "property_stock_valuation_account_id, property_price_difference_account_id, "
                    "property_stock_account_production_cost_id, create_date, write_date "
                    "FROM product_category ORDER BY id",
                ),
                "attributes": self._rows(
                    cursor,
                    "SELECT id, sequence, create_variant, display_type, name, "
                    "active, create_date, write_date FROM product_attribute ORDER BY id",
                ),
                "templates": self._rows(
                    cursor,
                    """
                    SELECT id, sequence, categ_id, uom_id, company_id, color,
                           type, service_tracking, default_code, name,
                           description, description_purchase, description_sale,
                           list_price, volume, weight, sale_ok, purchase_ok,
                           active, can_be_expensed, service_type, reinvoice_policy,
                           invoice_policy, is_storable, lot_sequence_id, tracking,
                           responsible_id, sale_delay, description_picking,
                           description_pickingout, description_pickingin,
                           lot_valuated, country_of_origin, hs_code, purchase_method,
                           property_account_income_id, property_account_expense_id,
                           property_stock_production, property_stock_inventory,
                           property_price_difference_account_id,
                           create_date, write_date
                      FROM product_template
                     ORDER BY id
                    """,
                ),
                "products": self._rows(
                    cursor,
                    """
                    SELECT id, product_tmpl_id, default_code, barcode,
                           standard_price, volume, weight, active,
                           create_date, write_date
                      FROM product_product
                     ORDER BY id
                    """,
                ),
                "users": self._rows(
                    cursor,
                    "SELECT id, partner_id FROM res_users ORDER BY id",
                ),
                "images": self._rows(
                    cursor,
                    """
                    SELECT id, res_id, store_fname, checksum, file_size, mimetype
                      FROM ir_attachment
                     WHERE res_model = 'product.template'
                       AND res_field = 'image_1920'
                     ORDER BY id
                    """,
                ),
                "customer_taxes": self._rows(
                    cursor,
                    "SELECT prod_id AS template_id, tax_id "
                    "FROM product_taxes_rel ORDER BY prod_id, tax_id",
                ),
                "supplier_taxes": self._rows(
                    cursor,
                    "SELECT prod_id AS template_id, tax_id "
                    "FROM product_supplier_taxes_rel ORDER BY prod_id, tax_id",
                ),
                "pricelists": self._rows(
                    cursor,
                    "SELECT id, sequence, currency_id, company_id, name, active, "
                    "create_date, write_date FROM product_pricelist ORDER BY id",
                ),
                "product_values": self._rows(
                    cursor,
                    "SELECT id, product_id, lot_id, move_id, company_id, user_id, "
                    "description, value, date, create_date, write_date "
                    "FROM product_value ORDER BY id",
                ),
                "warehouses": self._rows(
                    cursor,
                    "SELECT id, company_id, partner_id, view_location_id, lot_stock_id, "
                    "wh_input_stock_loc_id, wh_qc_stock_loc_id, wh_output_stock_loc_id, "
                    "wh_pack_stock_loc_id, mto_pull_id, pick_type_id, pack_type_id, "
                    "out_type_id, in_type_id, int_type_id, qc_type_id, store_type_id, "
                    "xdock_type_id, reception_route_id, delivery_route_id, sequence, "
                    "name, code, reception_steps, delivery_steps, active, buy_pull_id, "
                    "manufacture_pull_id, manufacture_mto_pull_id, pbm_mto_pull_id, "
                    "sam_rule_id, manu_type_id, pbm_type_id, sam_type_id, pbm_route_id, "
                    "pbm_loc_id, sam_loc_id, manufacture_steps, create_date, write_date "
                    "FROM stock_warehouse ORDER BY id",
                ),
                "locations": self._rows(
                    cursor,
                    "SELECT id, location_id, company_id, removal_strategy_id, "
                    "cyclic_inventory_frequency, warehouse_id, storage_category_id, "
                    "name, complete_name, usage, barcode, last_inventory_date, "
                    "next_inventory_date, active, replenish_location, valuation_account_id, "
                    "create_date, write_date FROM stock_location ORDER BY id",
                ),
                "routes": self._rows(
                    cursor,
                    "SELECT id, sequence, supplied_wh_id, supplier_wh_id, company_id, "
                    "name, active, product_selectable, product_categ_selectable, "
                    "warehouse_selectable, package_type_selectable, sale_selectable, "
                    "shipping_selectable, create_date, write_date "
                    "FROM stock_route ORDER BY id",
                ),
                "rules": self._rows(
                    cursor,
                    "SELECT id, sequence, company_id, location_dest_id, location_src_id, "
                    "route_id, picking_type_id, delay, partner_address_id, warehouse_id, "
                    "action, procure_method, auto, push_domain, name, active, "
                    "location_dest_from_rule, propagate_cancel, propagate_carrier, "
                    "create_date, write_date FROM stock_rule ORDER BY id",
                ),
                "picking_types": self._rows(
                    cursor,
                    "SELECT id, color, sequence, default_location_src_id, "
                    "default_location_dest_id, return_picking_type_id, warehouse_id, "
                    "company_id, code, reservation_method, name, show_entire_packs, "
                    "active, use_create_lots, use_existing_lots, show_operations, "
                    "create_backorder, move_type, create_date, write_date "
                    "FROM stock_picking_type ORDER BY id",
                ),
                "route_warehouses": self._rows(
                    cursor,
                    "SELECT route_id, warehouse_id FROM stock_route_warehouse "
                    "ORDER BY route_id, warehouse_id",
                ),
                "xmlids": self._rows(
                    cursor,
                    "SELECT model, res_id, module || '.' || name AS xmlid "
                    "FROM ir_model_data WHERE model IN ("
                    "'uom.uom', 'res.currency', 'res.country', 'ir.sequence', "
                    "'stock.warehouse', 'stock.location', 'stock.route', "
                    "'stock.rule', 'stock.picking.type') "
                    "ORDER BY model, res_id, module, name",
                ),
            }
        result["counts"] = {
            key: len(value)
            for key, value in result.items()
            if key not in {"counts", "native_baseline", "users", "xmlids"}
        }
        return result


class UslProductRestoreRun(models.Model):
    _name = "usl.product.restore.run"
    _description = "USL Product Master Restoration Run"
    _order = "started_at desc, id desc"

    status = fields.Selection(
        [("running", "Running"), ("passed", "Passed"), ("failed", "Failed")],
        required=True,
        default="running",
    )
    source_database = fields.Char(required=True)
    source_snapshot = fields.Char(required=True)
    started_at = fields.Datetime(required=True, default=fields.Datetime.now)
    finished_at = fields.Datetime()
    statistics_json = fields.Json(readonly=True)

    @staticmethod
    def _text(value):
        if isinstance(value, dict):
            return value.get("en_US") or value.get("fr_FR") or next(iter(value.values()), "")
        return value or ""

    def _trace_values(self, model, source_id):
        return {
            "rebuild_source_database": self.source_database,
            "rebuild_source_model": model,
            "rebuild_source_id": source_id,
            "rebuild_source_snapshot": self.source_snapshot,
            "rebuild_import_status": "imported",
            "rebuild_import_note": (
                f"Restored by Product run {self.id}, revision {RESTORE_REVISION} "
                f"from {self.source_database}."
            ),
        }

    def _cleanup_generated_zero_product_values(self):
        generated = (
            self.env["product.value"]
            .sudo()
            .search(
                [
                    ("lot_id", "=", False),
                    ("move_id", "=", False),
                    ("value", "=", 0),
                    ("date", "=", "0001-01-01 00:00:00"),
                    ("user_id", "=", self.env.ref("base.user_root").id),
                    ("description", "=", "Price update from None to 0.0 by OdooBot"),
                    ("rebuild_source_model", "=", False),
                ],
            )
        )
        count = len(generated)
        generated.unlink()
        return count

    def _upsert_product_value(self, row, values):
        """Adopt one exact native history row and remove only exact rerun copies."""
        domain = [
            ("product_id", "=", values["product_id"]),
            ("company_id", "=", values["company_id"]),
            ("user_id", "=", values["user_id"]),
            ("description", "=", values["description"]),
            ("value", "=", values["value"]),
            ("date", "=", values["date"]),
            ("lot_id", "=", False),
            ("move_id", "=", False),
        ]
        candidates = self.env["product.value"].sudo().search(domain, order="id")
        record = self._traced("product.value", row["id"])
        if record and record.id not in candidates.ids:
            raise RuntimeError(
                f"Traced product value {row['id']} differs from locked source truth",
            )
        if not record and candidates:
            record = candidates[:1]
        duplicates = candidates.filtered(lambda candidate: candidate.id != record.id)
        removed = len(duplicates)
        duplicates.unlink()
        trace_values = self._trace_values("product.value", row["id"])
        if record:
            # The exact source-backed business values were already established
            # by ``domain``.  Rewriting product_id/company_id can retrigger
            # Product Value's stored company computation for global products,
            # causing a row adopted on the first run to reject itself on the
            # second.  Adoption therefore adds trace metadata only.
            record.write(trace_values)
        else:
            record = self.env["product.value"].sudo().create(
                {
                    **values,
                    **trace_values,
                },
            )
        return record, removed

    def _traced(self, model, source_id):
        return (
            self.env[model]
            .sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("rebuild_source_model", "=", model),
                    ("rebuild_source_id", "=", source_id),
                ],
                limit=1,
            )
        )

    def _upsert(self, model, row, values, natural_domain=None):
        record = self._traced(model, row["id"])
        if not record and natural_domain:
            candidates = (
                self.env[model]
                .sudo()
                .with_context(active_test=False)
                .search(natural_domain, limit=2)
            )
            if len(candidates) == 1:
                record = candidates
        values = {**values, **self._trace_values(model, row["id"])}
        target = self.env[model].sudo().with_context(
            active_test=False,
            lang="en_US",
            tracking_disable=True,
            mail_create_nolog=True,
        )
        if record:
            record.with_context(lang="en_US").write(values)
        else:
            record = target.create(values)
        return record

    def _reference(self, xmlids, model, source_id):
        for xmlid in xmlids.get((model, source_id), []):
            record = self.env.ref(xmlid, raise_if_not_found=False)
            if record and record._name == model:
                return record
        return self.env[model]

    def _stamp_dates(self, model, mapping, rows):
        table = self.env[model]._table
        for row in rows:
            self.env.cr.execute(
                f"UPDATE {table} SET create_date=COALESCE(%s, create_date), "
                "write_date=COALESCE(%s, write_date) WHERE id=%s",
                (row["create_date"], row["write_date"], mapping[row["id"]].id),
            )

    def _write_french(self, record, row, field_names):
        values = {}
        for field_name in field_names:
            source_value = row.get(field_name)
            if isinstance(source_value, dict) and source_value.get("fr_FR"):
                values[field_name] = source_value["fr_FR"]
        if values:
            record.with_context(lang="fr_FR", tracking_disable=True).write(values)

    @staticmethod
    def _company_value(raw_value, source_company_id):
        if not isinstance(raw_value, dict):
            return None, False
        key = str(source_company_id)
        return raw_value.get(key), key in raw_value

    def _adopt_source_record(self, model, source_id, record):
        if not source_id or not record:
            return record
        traced = self._traced(model, source_id)
        if traced and traced != record:
            raise RuntimeError(
                f"Source {model} {source_id} is already mapped to {traced.id}, "
                f"not native record {record.id}",
            )
        record.sudo().with_context(active_test=False, tracking_disable=True).write(
            self._trace_values(model, source_id),
        )
        return record

    @staticmethod
    def _mapped_company_relation(raw_value, source_company_id, mapping, label):
        value, present = UslProductRestoreRun._company_value(
            raw_value,
            source_company_id,
        )
        if not present:
            return None, False
        if not value:
            return False, True
        target = mapping.get(int(value))
        if not target:
            raise RuntimeError(
                f"Missing target {label} for source identifier {value}",
            )
        return target.id, True

    def _restore_stock_configuration(
        self,
        source,
        xmlids,
        companies,
        partners,
        accounts,
    ):
        warehouses = {}
        for row in source["warehouses"]:
            company = companies.get(row["company_id"])
            if not company:
                raise RuntimeError(
                    f"Missing target company for warehouse {row['id']}",
                )
            partner = partners.get(row["partner_id"])
            values = {
                "name": row["name"],
                "code": row["code"],
                "company_id": company.id,
                "partner_id": partner.id if partner else company.partner_id.id,
                "sequence": row["sequence"],
                "active": row["active"],
                "reception_steps": row["reception_steps"],
                "delivery_steps": row["delivery_steps"],
            }
            if "manufacture_steps" in self.env["stock.warehouse"]._fields:
                values["manufacture_steps"] = row["manufacture_steps"]
            warehouses[row["id"]] = self._upsert(
                "stock.warehouse",
                row,
                values,
                [("company_id", "=", company.id), ("code", "=", row["code"])],
            )

        locations = {}
        picking_types = {}
        routes = {}
        rules = {}
        warehouse_location_fields = (
            "view_location_id",
            "lot_stock_id",
            "wh_input_stock_loc_id",
            "wh_qc_stock_loc_id",
            "wh_output_stock_loc_id",
            "wh_pack_stock_loc_id",
            "pbm_loc_id",
            "sam_loc_id",
        )
        warehouse_picking_fields = (
            "pick_type_id",
            "pack_type_id",
            "out_type_id",
            "in_type_id",
            "int_type_id",
            "qc_type_id",
            "store_type_id",
            "xdock_type_id",
            "manu_type_id",
            "pbm_type_id",
            "sam_type_id",
        )
        warehouse_route_fields = (
            "reception_route_id",
            "delivery_route_id",
            "pbm_route_id",
        )
        warehouse_rule_fields = (
            "mto_pull_id",
            "buy_pull_id",
            "manufacture_pull_id",
            "manufacture_mto_pull_id",
            "pbm_mto_pull_id",
            "sam_rule_id",
        )
        rows_by_warehouse = {row["id"]: row for row in source["warehouses"]}
        for source_warehouse_id, warehouse in warehouses.items():
            row = rows_by_warehouse[source_warehouse_id]
            for field_name in warehouse_location_fields:
                if field_name in warehouse._fields and row.get(field_name):
                    locations[row[field_name]] = self._adopt_source_record(
                        "stock.location",
                        row[field_name],
                        warehouse[field_name],
                    )
            for field_name in warehouse_picking_fields:
                if field_name in warehouse._fields and row.get(field_name):
                    picking_types[row[field_name]] = self._adopt_source_record(
                        "stock.picking.type",
                        row[field_name],
                        warehouse[field_name],
                    )
            for field_name in warehouse_route_fields:
                if field_name in warehouse._fields and row.get(field_name):
                    routes[row[field_name]] = self._adopt_source_record(
                        "stock.route",
                        row[field_name],
                        warehouse[field_name],
                    )
            for field_name in warehouse_rule_fields:
                if field_name in warehouse._fields and row.get(field_name):
                    rules[row[field_name]] = self._adopt_source_record(
                        "stock.rule",
                        row[field_name],
                        warehouse[field_name],
                    )

        for row in source["locations"]:
            if row["removal_strategy_id"] or row["storage_category_id"]:
                raise RuntimeError(
                    f"Stock location {row['id']} uses an unmapped removal/storage policy",
                )
            company = companies.get(row["company_id"])
            parent = locations.get(row["location_id"])
            warehouse = warehouses.get(row["warehouse_id"])
            valuation_account = accounts.get(row["valuation_account_id"])
            values = {
                "name": row["name"],
                "location_id": parent.id if parent else False,
                "company_id": company.id if company else False,
                "usage": row["usage"],
                "barcode": row["barcode"],
                "active": row["active"],
                "cyclic_inventory_frequency": row["cyclic_inventory_frequency"],
                "last_inventory_date": row["last_inventory_date"],
                "next_inventory_date": row["next_inventory_date"],
                "replenish_location": row["replenish_location"],
                "valuation_account_id": (
                    valuation_account.id if valuation_account else False
                ),
            }
            location = locations.get(row["id"])
            if not location:
                native = self._reference(xmlids, "stock.location", row["id"])
                if native and (
                    not company or not native.company_id or native.company_id == company
                ):
                    location = self._adopt_source_record(
                        "stock.location",
                        row["id"],
                        native,
                    )
            if location:
                location.sudo().with_context(active_test=False).write(
                    {**values, **self._trace_values("stock.location", row["id"])},
                )
            else:
                domain = [
                    ("name", "=", row["name"]),
                    ("usage", "=", row["usage"]),
                    ("company_id", "=", company.id if company else False),
                    ("location_id", "=", parent.id if parent else False),
                ]
                location = self._upsert("stock.location", row, values, domain)
            locations[row["id"]] = location

        for row in source["picking_types"]:
            company = companies.get(row["company_id"])
            warehouse = warehouses.get(row["warehouse_id"])
            values = {
                "name": self._text(row["name"]),
                "color": row["color"],
                "sequence": row["sequence"],
                "default_location_src_id": (
                    locations[row["default_location_src_id"]].id
                    if row["default_location_src_id"]
                    else False
                ),
                "default_location_dest_id": (
                    locations[row["default_location_dest_id"]].id
                    if row["default_location_dest_id"]
                    else False
                ),
                "warehouse_id": warehouse.id if warehouse else False,
                "company_id": company.id if company else False,
                "code": row["code"],
                "reservation_method": row["reservation_method"],
                "show_entire_packs": row["show_entire_packs"],
                "active": row["active"],
                "use_create_lots": row["use_create_lots"],
                "use_existing_lots": row["use_existing_lots"],
                "show_operations": row["show_operations"],
                "create_backorder": row["create_backorder"],
                "move_type": row["move_type"],
            }
            picking_type = picking_types.get(row["id"])
            if not picking_type:
                native = self._reference(xmlids, "stock.picking.type", row["id"])
                if native and (
                    not company or not native.company_id or native.company_id == company
                ):
                    picking_type = self._adopt_source_record(
                        "stock.picking.type",
                        row["id"],
                        native,
                    )
            if picking_type:
                picking_type.sudo().with_context(active_test=False, lang="en_US").write(
                    {
                        **values,
                        **self._trace_values("stock.picking.type", row["id"]),
                    },
                )
            else:
                picking_type = self._upsert(
                    "stock.picking.type",
                    row,
                    values,
                    [
                        ("warehouse_id", "=", warehouse.id if warehouse else False),
                        ("code", "=", row["code"]),
                        ("name", "=", self._text(row["name"])),
                    ],
                )
            picking_types[row["id"]] = picking_type
            self._write_french(picking_type, row, ("name",))
        for row in source["picking_types"]:
            picking_types[row["id"]].write(
                {
                    "return_picking_type_id": (
                        picking_types[row["return_picking_type_id"]].id
                        if row["return_picking_type_id"]
                        else False
                    ),
                },
            )

        for row in source["routes"]:
            company = companies.get(row["company_id"])
            values = {
                "name": self._text(row["name"]),
                "sequence": row["sequence"],
                "company_id": company.id if company else False,
                "active": row["active"],
                "product_selectable": row["product_selectable"],
                "product_categ_selectable": row["product_categ_selectable"],
                "warehouse_selectable": row["warehouse_selectable"],
                "package_type_selectable": row["package_type_selectable"],
                "supplied_wh_id": (
                    warehouses[row["supplied_wh_id"]].id if row["supplied_wh_id"] else False
                ),
                "supplier_wh_id": (
                    warehouses[row["supplier_wh_id"]].id if row["supplier_wh_id"] else False
                ),
            }
            for optional_field in ("sale_selectable", "shipping_selectable"):
                if optional_field in self.env["stock.route"]._fields:
                    values[optional_field] = row[optional_field]
            route = routes.get(row["id"])
            if not route:
                native = self._reference(xmlids, "stock.route", row["id"])
                if native and (
                    not company or not native.company_id or native.company_id == company
                ):
                    route = self._adopt_source_record(
                        "stock.route",
                        row["id"],
                        native,
                    )
            if route:
                route.sudo().with_context(active_test=False, lang="en_US").write(
                    {**values, **self._trace_values("stock.route", row["id"])},
                )
            else:
                route = self._upsert(
                    "stock.route",
                    row,
                    values,
                    [
                        ("name", "=", self._text(row["name"])),
                        ("company_id", "=", company.id if company else False),
                    ],
                )
            routes[row["id"]] = route
            self._write_french(route, row, ("name",))
        route_warehouses = {}
        for relation in source["route_warehouses"]:
            route_warehouses.setdefault(relation["route_id"], []).append(
                warehouses[relation["warehouse_id"]].id,
            )
        for source_route_id, route in routes.items():
            route.write(
                {
                    "warehouse_ids": [
                        Command.set(route_warehouses.get(source_route_id, [])),
                    ],
                },
            )

        for row in source["rules"]:
            company = companies.get(row["company_id"])
            warehouse = warehouses.get(row["warehouse_id"])
            partner = partners.get(row["partner_address_id"])
            values = {
                "name": self._text(row["name"]),
                "sequence": row["sequence"],
                "company_id": company.id if company else False,
                "location_dest_id": locations[row["location_dest_id"]].id,
                "location_src_id": (
                    locations[row["location_src_id"]].id if row["location_src_id"] else False
                ),
                "route_id": routes[row["route_id"]].id,
                "picking_type_id": picking_types[row["picking_type_id"]].id,
                "delay": row["delay"],
                "partner_address_id": partner.id if partner else False,
                "warehouse_id": warehouse.id if warehouse else False,
                "action": row["action"],
                "procure_method": row["procure_method"],
                "auto": row["auto"],
                "push_domain": row["push_domain"],
                "active": row["active"],
                "location_dest_from_rule": row["location_dest_from_rule"],
                "propagate_cancel": row["propagate_cancel"],
                "propagate_carrier": row["propagate_carrier"],
            }
            rule = rules.get(row["id"])
            if rule:
                rule.sudo().with_context(active_test=False, lang="en_US").write(
                    {**values, **self._trace_values("stock.rule", row["id"])},
                )
            else:
                rule = self._upsert(
                    "stock.rule",
                    row,
                    values,
                    [
                        ("route_id", "=", routes[row["route_id"]].id),
                        ("name", "=", self._text(row["name"])),
                        ("warehouse_id", "=", warehouse.id if warehouse else False),
                    ],
                )
            rules[row["id"]] = rule
            self._write_french(rule, row, ("name",))

        self._stamp_dates("stock.warehouse", warehouses, source["warehouses"])
        self._stamp_dates("stock.location", locations, source["locations"])
        self._stamp_dates("stock.picking.type", picking_types, source["picking_types"])
        self._stamp_dates("stock.route", routes, source["routes"])
        self._stamp_dates("stock.rule", rules, source["rules"])
        return {
            "warehouses": warehouses,
            "locations": locations,
            "picking_types": picking_types,
            "routes": routes,
            "rules": rules,
            "route_warehouses": source["route_warehouses"],
        }

    def restore(self, source):
        self.ensure_one()
        if source["native_baseline"] != EXPECTED_NATIVE_BASELINE:
            raise RuntimeError(
                "Product source native baseline changed: "
                f"{source['native_baseline']} != {EXPECTED_NATIVE_BASELINE}",
            )
        xmlids = {}
        for row in source["xmlids"]:
            xmlids.setdefault((row["model"], row["res_id"]), []).append(row["xmlid"])
        companies = {
            record.rebuild_source_id: record
            for record in self.env["res.company"].sudo().with_context(active_test=False).search(
                [("rebuild_source_model", "=", "res.company")],
            )
        }
        taxes = {
            record.rebuild_source_id: record
            for record in self.env["account.tax"].sudo().with_context(active_test=False).search(
                [("rebuild_source_model", "=", "account.tax")],
            )
        }
        accounts = {
            record.rebuild_source_id: record
            for record in self.env["account.account"].sudo().with_context(active_test=False).search(
                [("rebuild_source_model", "=", "account.account")],
            )
        }
        journals = {
            record.rebuild_source_id: record
            for record in self.env["account.journal"].sudo().with_context(active_test=False).search(
                [("rebuild_source_model", "=", "account.journal")],
            )
        }
        partners = {
            record.rebuild_source_id: record
            for record in self.env["res.partner"].sudo().with_context(active_test=False).search(
                [("rebuild_source_model", "=", "res.partner")],
            )
        }
        users = {}
        for row in source["users"]:
            partner = partners.get(row["partner_id"])
            if not partner:
                continue
            target_users = (
                self.env["res.users"]
                .sudo()
                .with_context(active_test=False)
                .search([("partner_id", "=", partner.id)], limit=2)
            )
            if len(target_users) == 1:
                users[row["id"]] = target_users

        categories = {}
        for row in source["categories"]:
            name = self._text(row["name"])
            categories[row["id"]] = self._upsert(
                "product.category",
                row,
                {
                    "name": name,
                    "product_properties_definition": row[
                        "product_properties_definition"
                    ],
                },
                [("name", "=", name)],
            )
            self._write_french(categories[row["id"]], row, ("name",))
        for row in source["categories"]:
            categories[row["id"]].write(
                {"parent_id": categories.get(row["parent_id"]).id if row["parent_id"] else False},
            )

        category_selection_properties = (
            "property_valuation",
            "property_cost_method",
        )
        category_relation_properties = {
            "property_account_income_categ_id": (accounts, "income account"),
            "property_account_expense_categ_id": (accounts, "expense account"),
            "property_stock_journal": (journals, "stock journal"),
            "property_stock_valuation_account_id": (accounts, "stock valuation account"),
            "property_price_difference_account_id": (accounts, "price difference account"),
            "property_stock_account_production_cost_id": (
                accounts,
                "production cost account",
            ),
        }
        for row in source["categories"]:
            category = categories[row["id"]]
            for source_company_id, company in companies.items():
                values = {}
                for field_name in category_selection_properties:
                    value, present = self._company_value(
                        row[field_name],
                        source_company_id,
                    )
                    if present:
                        values[field_name] = value or False
                for field_name, (mapping, label) in category_relation_properties.items():
                    if field_name not in category._fields:
                        continue
                    value, present = self._mapped_company_relation(
                        row[field_name],
                        source_company_id,
                        mapping,
                        label,
                    )
                    if present:
                        values[field_name] = value
                if values:
                    category.with_company(company).with_context(
                        disable_auto_revaluation=True,
                        tracking_disable=True,
                    ).write(values)

        stock = self._restore_stock_configuration(
            source,
            xmlids,
            companies,
            partners,
            accounts,
        )

        attributes = {}
        for row in source["attributes"]:
            name = self._text(row["name"])
            attributes[row["id"]] = self._upsert(
                "product.attribute",
                row,
                {
                    "name": name,
                    "sequence": row["sequence"],
                    "create_variant": row["create_variant"],
                    "display_type": row["display_type"],
                    "active": row["active"],
                },
                [("name", "=", name)],
            )
            self._write_french(attributes[row["id"]], row, ("name",))

        source_products_by_template = {
            row["product_tmpl_id"]: row for row in source["products"]
        }
        stock_locations = stock["locations"]
        templates = {}
        products = {}
        for row in source["templates"]:
            source_product = source_products_by_template[row["id"]]
            product = self._traced("product.product", source_product["id"])
            template = product.product_tmpl_id if product else self._traced(
                "product.template",
                row["id"],
            )
            uom = self._reference(xmlids, "uom.uom", row["uom_id"])
            lot_sequence = self._reference(
                xmlids,
                "ir.sequence",
                row["lot_sequence_id"],
            )
            country = self._reference(
                xmlids,
                "res.country",
                row["country_of_origin"],
            )
            is_capability_test = (
                not row["active"]
                and self._text(row["name"])
                == "__MCP_CAPABILITY_TEST_GBC_NATIVE_PRODUCT_TEMPLATE__"
            )
            values = {
                "name": self._text(row["name"]),
                "sequence": row["sequence"],
                "categ_id": (
                    categories[row["categ_id"]].id
                    if row["categ_id"]
                    else self.env.ref("product.product_category_services").id
                ),
                "uom_id": uom.id,
                "company_id": companies.get(row["company_id"]).id if row["company_id"] else False,
                "color": row["color"],
                "type": row["type"],
                "service_tracking": row["service_tracking"],
                "description": self._text(row["description"]),
                "description_purchase": self._text(row["description_purchase"]),
                "description_sale": self._text(row["description_sale"]),
                "list_price": row["list_price"],
                "volume": row["volume"],
                "weight": row["weight"],
                "sale_ok": row["sale_ok"],
                "purchase_ok": row["purchase_ok"],
                "active": row["active"],
                "can_be_expensed": row["can_be_expensed"],
                "service_type": row["service_type"],
                "reinvoice_policy": row["reinvoice_policy"],
                "invoice_policy": row["invoice_policy"],
                "is_storable": row["is_storable"],
                "lot_sequence_id": lot_sequence.id if lot_sequence else False,
                "tracking": row["tracking"],
                "description_picking": self._text(row["description_picking"]),
                "description_pickingout": self._text(row["description_pickingout"]),
                "description_pickingin": self._text(row["description_pickingin"]),
                "lot_valuated": row["lot_valuated"],
                "country_of_origin": country.id if country else False,
                "hs_code": row["hs_code"],
                "purchase_method": row["purchase_method"],
                "b2c_catalog_classification": (
                    "capability_test" if is_capability_test else "operational"
                ),
                "b2c_fulfilment_mode": (
                    "not_applicable"
                    if is_capability_test
                    else "own_stock"
                    if row["is_storable"]
                    else "unknown"
                ),
                "b2c_opening_stock_state": (
                    "not_evidenced" if row["is_storable"] else "not_applicable"
                ),
                **self._trace_values("product.template", row["id"]),
            }
            if template:
                template.sudo().with_context(
                    lang="en_US",
                    tracking_disable=True,
                ).write(values)
            else:
                template = self.env["product.template"].sudo().with_context(
                    lang="en_US",
                    tracking_disable=True,
                ).create(values)
            templates[row["id"]] = template
            self._write_french(
                template,
                row,
                (
                    "name",
                    "description",
                    "description_purchase",
                    "description_sale",
                    "description_picking",
                    "description_pickingout",
                    "description_pickingin",
                ),
            )
            product = product or template.with_context(
                active_test=False,
            ).product_variant_id
            if not product:
                raise RuntimeError(
                    f"Product template {row['id']} has no native variant",
                )
            product.write(
                {
                    "default_code": source_product["default_code"],
                    "barcode": source_product["barcode"],
                    "volume": source_product["volume"],
                    "weight": source_product["weight"],
                    "active": source_product["active"],
                    **self._trace_values("product.product", source_product["id"]),
                },
            )
            for source_company_id, standard_price in (
                source_product["standard_price"] or {}
            ).items():
                company = companies.get(int(source_company_id))
                if company:
                    product.with_company(company).with_context(
                        disable_auto_revaluation=True,
                    ).standard_price = standard_price
            products[source_product["id"]] = product

            for source_company_id, company in companies.items():
                company_values = {}
                sale_delay, sale_delay_present = self._company_value(
                    row["sale_delay"],
                    source_company_id,
                )
                if sale_delay_present:
                    company_values["sale_delay"] = sale_delay or 0
                responsible_id, responsible_present = self._mapped_company_relation(
                    row["responsible_id"],
                    source_company_id,
                    users,
                    "responsible user",
                )
                if responsible_present:
                    company_values["responsible_id"] = responsible_id
                for field_name in (
                    "property_account_income_id",
                    "property_account_expense_id",
                    "property_price_difference_account_id",
                ):
                    account_id, present = self._mapped_company_relation(
                        row[field_name],
                        source_company_id,
                        accounts,
                        field_name,
                    )
                    if present:
                        company_values[field_name] = account_id
                for field_name in (
                    "property_stock_production",
                    "property_stock_inventory",
                ):
                    location_id, present = self._mapped_company_relation(
                        row[field_name],
                        source_company_id,
                        stock_locations,
                        field_name,
                    )
                    if present:
                        company_values[field_name] = location_id
                if company_values:
                    template.with_company(company).with_context(
                        tracking_disable=True,
                    ).write(company_values)

        customer_taxes = {}
        for row in source["customer_taxes"]:
            customer_taxes.setdefault(row["template_id"], []).append(taxes[row["tax_id"]].id)
        supplier_taxes = {}
        for row in source["supplier_taxes"]:
            supplier_taxes.setdefault(row["template_id"], []).append(taxes[row["tax_id"]].id)
        for source_id, template in templates.items():
            template.write(
                {
                    "taxes_id": [Command.set(customer_taxes.get(source_id, []))],
                    "supplier_taxes_id": [Command.set(supplier_taxes.get(source_id, []))],
                },
            )

        for row in source["images"]:
            template = templates.get(row["res_id"])
            if not template:
                raise RuntimeError(
                    f"Product image {row['id']} references missing template {row['res_id']}",
                )
            template.write({"image_1920": BinaryBytes(source_binary(row))})

        pricelists = {}
        for row in source["pricelists"]:
            name = self._text(row["name"])
            currency = self._reference(xmlids, "res.currency", row["currency_id"])
            pricelists[row["id"]] = self._upsert(
                "product.pricelist",
                row,
                {
                    "name": name,
                    "sequence": row["sequence"],
                    "currency_id": currency.id,
                    "company_id": companies.get(row["company_id"]).id if row["company_id"] else False,
                    "active": row["active"],
                },
                [
                    ("name", "=", name),
                    ("currency_id", "=", currency.id),
                    (
                        "company_id",
                        "=",
                        companies.get(row["company_id"]).id
                        if row["company_id"]
                        else False,
                    ),
                ],
            )
            self._write_french(pricelists[row["id"]], row, ("name",))

        generated_zero_value_count = self._cleanup_generated_zero_product_values()

        product_values = {}
        duplicate_product_value_count = 0
        for row in source["product_values"]:
            if row["lot_id"] or row["move_id"]:
                raise RuntimeError(
                    f"Product value {row['id']} unexpectedly references stock history",
                )
            product = products.get(row["product_id"])
            company = companies.get(row["company_id"])
            user = users.get(row["user_id"])
            if not product or not company or not user:
                raise RuntimeError(
                    f"Product value {row['id']} has an unmapped product, company or user",
                )
            product_values[row["id"]], removed = self._upsert_product_value(
                row,
                {
                    "product_id": product.id,
                    "company_id": company.id,
                    "user_id": user.id,
                    "description": row["description"],
                    "value": row["value"],
                    "date": row["date"],
                },
            )
            duplicate_product_value_count += removed

        self._stamp_dates("product.category", categories, source["categories"])
        self._stamp_dates("product.attribute", attributes, source["attributes"])
        self._stamp_dates("product.template", templates, source["templates"])
        self._stamp_dates("product.product", products, source["products"])
        self._stamp_dates("product.pricelist", pricelists, source["pricelists"])
        self._stamp_dates("product.value", product_values, source["product_values"])
        counts = {
            "categories": len(categories),
            "attributes": len(attributes),
            "templates": len(templates),
            "products": len(products),
            "customer_taxes": sum(len(value) for value in customer_taxes.values()),
            "supplier_taxes": sum(len(value) for value in supplier_taxes.values()),
            "pricelists": len(pricelists),
            "images": len(source["images"]),
            "product_values": len(product_values),
            "warehouses": len(stock["warehouses"]),
            "locations": len(stock["locations"]),
            "routes": len(stock["routes"]),
            "rules": len(stock["rules"]),
            "picking_types": len(stock["picking_types"]),
            "route_warehouses": len(stock["route_warehouses"]),
        }
        if counts != source["counts"]:
            raise RuntimeError(f"Product source/target counts differ: {source['counts']} != {counts}")
        self.write(
            {
                "status": "passed",
                "finished_at": fields.Datetime.now(),
                "statistics_json": {
                    "cleanup": {
                        "duplicate_source_values_removed": duplicate_product_value_count,
                        "generated_zero_values_removed": generated_zero_value_count,
                    },
                    "source": source["counts"],
                    "target": counts,
                },
            },
        )
        return counts


def source_options():
    return {
        "host": os.getenv("PRODUCT_SOURCE_DB_HOST", "accounting-source-db"),
        "port": int(os.getenv("PRODUCT_SOURCE_DB_PORT", "5432")),
        "user": os.getenv("PRODUCT_SOURCE_DB_USER", "odoo"),
        "password": os.getenv("PRODUCT_SOURCE_DB_PASSWORD", "odoo"),
        "database": os.getenv("PRODUCT_SOURCE_DATABASE", "odoo_online_source_saas_19_3"),
    }

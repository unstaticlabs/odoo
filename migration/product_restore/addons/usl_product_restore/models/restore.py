import base64
import hashlib
import os
from pathlib import Path

import psycopg2
import psycopg2.extras

from odoo import Command, fields, models


RESTORE_REVISION = 1
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

    def read(self):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            if cursor.fetchone()["transaction_read_only"] != "on":
                raise RuntimeError("Product source connection is not read-only")
            result = {
                "categories": self._rows(
                    cursor,
                    "SELECT id, parent_id, name, create_date, write_date "
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
                           active, can_be_expensed, service_type, expense_policy,
                           invoice_policy, is_storable, create_date, write_date
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
                "xmlids": self._rows(
                    cursor,
                    "SELECT model, res_id, module || '.' || name AS xmlid "
                    "FROM ir_model_data WHERE model IN ('uom.uom', 'res.currency') "
                    "ORDER BY model, res_id, module, name",
                ),
            }
        result["counts"] = {
            key: len(value)
            for key, value in result.items()
            if key not in {"counts", "xmlids"}
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

    def restore(self, source):
        self.ensure_one()
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

        categories = {}
        for row in source["categories"]:
            name = self._text(row["name"])
            categories[row["id"]] = self._upsert(
                "product.category",
                row,
                {"name": name},
                [("name", "=", name)],
            )
            self._write_french(categories[row["id"]], row, ("name",))
        for row in source["categories"]:
            categories[row["id"]].write(
                {"parent_id": categories.get(row["parent_id"]).id if row["parent_id"] else False},
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
                "sale_ok": row["sale_ok"],
                "purchase_ok": row["purchase_ok"],
                "active": row["active"],
                "can_be_expensed": row["can_be_expensed"],
                "is_storable": row["is_storable"],
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
                ),
            )
            product = product or template.product_variant_id
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
                    product.with_company(company).standard_price = standard_price
            products[source_product["id"]] = product

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
            template.write({"image_1920": base64.b64encode(source_binary(row))})

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

        self._stamp_dates("product.category", categories, source["categories"])
        self._stamp_dates("product.attribute", attributes, source["attributes"])
        self._stamp_dates("product.template", templates, source["templates"])
        self._stamp_dates("product.product", products, source["products"])
        self._stamp_dates("product.pricelist", pricelists, source["pricelists"])
        counts = {
            "categories": len(categories),
            "attributes": len(attributes),
            "templates": len(templates),
            "products": len(products),
            "customer_taxes": sum(len(value) for value in customer_taxes.values()),
            "supplier_taxes": sum(len(value) for value in supplier_taxes.values()),
            "pricelists": len(pricelists),
            "images": len(source["images"]),
        }
        if counts != source["counts"]:
            raise RuntimeError(f"Product source/target counts differ: {source['counts']} != {counts}")
        self.write(
            {
                "status": "passed",
                "finished_at": fields.Datetime.now(),
                "statistics_json": {"source": source["counts"], "target": counts},
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

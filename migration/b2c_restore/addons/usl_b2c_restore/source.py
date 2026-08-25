import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import psycopg2
import psycopg2.extras

from .parsers import (
    ETSY_ITEMS_HEADER,
    ETSY_STATEMENT_HEADER,
    LEGACY_MEDUSA_HEADER,
    MEDUSA_HEADER,
    MEDUSA_ITEMS_HEADER,
    REVOLUT_HEADER,
    STRIPE_PAYMENT_HEADER,
    STRIPE_PAYOUT_HEADER,
    load_csv,
)

EXPECTED_DUMP_SHA256 = "0b9916db4807206f63b654bd2933ac89b0aab30ba7e0a1004edc4c060490238f"
SOURCE_FILESTORE = Path(
    os.getenv("B2C_SOURCE_FILESTORE", "/mnt/accounting-source/filestore"),
).resolve()
SUPPLEMENTAL_EVIDENCE_DIR = Path(
    os.getenv("B2C_SUPPLEMENTAL_EVIDENCE_DIR", "/mnt/b2c-evidence/source"),
).resolve()


@dataclass(frozen=True)
class SourceFile:
    attachment_id: int | None
    name: str
    store_fname: str
    checksum: str
    file_size: int
    mimetype: str
    kind: str


_ETSY_STATEMENTS = (
    (3058, "etsy_statement_2026_8.csv", "be/beffd8e492c541a37ca289566e6c68835058907d", "beffd8e492c541a37ca289566e6c68835058907d", 152),
    (3059, "etsy_statement_2026_7.csv", "7a/7abb77ccf5010985dc44e5f8bd512acefd0488c2", "7abb77ccf5010985dc44e5f8bd512acefd0488c2", 1317),
    (3060, "etsy_statement_2026_6.csv", "21/21235265709f09224783501cc679fc241ad7cbad", "21235265709f09224783501cc679fc241ad7cbad", 831),
    (3061, "etsy_statement_2026_5.csv", "78/780bbeec53b22182a1e6978293fd7ce6896f1604", "780bbeec53b22182a1e6978293fd7ce6896f1604", 938),
    (3062, "etsy_statement_2026_4.csv", "36/36db02c0ba60f840b5fc07efe6075b641b1770d9", "36db02c0ba60f840b5fc07efe6075b641b1770d9", 1648),
    (3063, "etsy_statement_2026_3.csv", "4e/4e0e29d2d6149c42fc1b2190eb91425b2ebe0915", "4e0e29d2d6149c42fc1b2190eb91425b2ebe0915", 2528),
    (3064, "etsy_statement_2026_2.csv", "49/49916159fc7fb991019dbe80d63beb88c1c3ae8b", "49916159fc7fb991019dbe80d63beb88c1c3ae8b", 5619),
    (3065, "etsy_statement_2026_1.csv", "65/6548cb64def54b1b8f7ee3d48707b0bfcd562315", "6548cb64def54b1b8f7ee3d48707b0bfcd562315", 1902),
    (3066, "etsy_statement_2025_12.csv", "46/463aedecd9b0be6a679944c489f3284847ef1f8d", "463aedecd9b0be6a679944c489f3284847ef1f8d", 10434),
    (3067, "etsy_statement_2025_11.csv", "a3/a38ce55cb08c5886acf24f23f43dea9c27b4cb77", "a38ce55cb08c5886acf24f23f43dea9c27b4cb77", 4148),
    (3068, "etsy_statement_2025_10.csv", "e4/e415714bb48af39eda8a3782557cb8d715d30c8c", "e415714bb48af39eda8a3782557cb8d715d30c8c", 5577),
    (3069, "etsy_statement_2025_9.csv", "19/193c0643b9f4a8ae33b0aca5ea35face476b1a76", "193c0643b9f4a8ae33b0aca5ea35face476b1a76", 10428),
    (3070, "etsy_statement_2025_8.csv", "d5/d514e38c83c75ab637f30c3ad25693a84bef93db", "d514e38c83c75ab637f30c3ad25693a84bef93db", 3653),
    (3071, "etsy_statement_2025_7.csv", "41/4167fd1f7b271aa8fa6cf321923c846eaae6226a", "4167fd1f7b271aa8fa6cf321923c846eaae6226a", 8351),
    (3072, "etsy_statement_2025_6.csv", "09/09315919afb35287b87658db944a550ca130a743", "09315919afb35287b87658db944a550ca130a743", 6343),
    (3073, "etsy_statement_2025_5.csv", "43/4393001c823ac17b95c294354a17caace3433955", "4393001c823ac17b95c294354a17caace3433955", 12053),
    (3074, "etsy_statement_2025_4.csv", "53/53a57f18f7c10397a9782656f835bba9055e4e86", "53a57f18f7c10397a9782656f835bba9055e4e86", 14189),
    (3075, "etsy_statement_2025_3.csv", "03/039db931b5f32e3a8769271a86c656da6a283089", "039db931b5f32e3a8769271a86c656da6a283089", 13881),
    (3076, "etsy_statement_2025_2.csv", "3a/3a27b15668c38d3d295606b66b1517c743289e3e", "3a27b15668c38d3d295606b66b1517c743289e3e", 17089),
    (3077, "etsy_statement_2025_1.csv", "91/917203ddebd23dd7c8c58d42ac92e8e44983613d", "917203ddebd23dd7c8c58d42ac92e8e44983613d", 6023),
    (3078, "etsy_statement_2024_12.csv", "b1/b1db46cf19ca2984696395b95820df636ecd0b1a", "b1db46cf19ca2984696395b95820df636ecd0b1a", 9185),
)

SOURCE_FILES = tuple(
    SourceFile(*values, "text/csv", "etsy_statement")
    for values in _ETSY_STATEMENTS
) + (
    SourceFile(3079, "2025.08 - 2026.07 - revolut payments statement.csv", "96/96b1e0ec498ed0eaf1789ec3557012a13403effc", "96b1e0ec498ed0eaf1789ec3557012a13403effc", 95311, "text/csv", "revolut"),
    SourceFile(3080, "sales-report 2025-1.pdf", "34/34526b4e95653e2b8fa52a0e646ad6955843e4be", "34526b4e95653e2b8fa52a0e646ad6955843e4be", 40318, "application/pdf", "supporting_pdf"),
    SourceFile(3081, "EtsySoldOrderItems2024.csv", "ed/eda8f9dea84d11f6af9fb1bfc1aafac9986ccc4b", "eda8f9dea84d11f6af9fb1bfc1aafac9986ccc4b", 6377, "text/csv", "etsy_items"),
    SourceFile(3082, "EtsySoldOrderItems2025.csv", "8e/8eee6f0eb01f0f0750d14b29bb440e084e7711fb", "8eee6f0eb01f0f0750d14b29bb440e084e7711fb", 68780, "text/csv", "etsy_items"),
    SourceFile(3083, "EtsySoldOrderItems2026.csv", "d8/d8ead587548d17cd5ff5ef6920a3778820c0a875", "d8ead587548d17cd5ff5ef6920a3778820c0a875", 7652, "text/csv", "etsy_items"),
    SourceFile(3084, "orders_export_a191650c-55f5-4147-a354-df5b07b8bda5.csv", "39/3948236d7b1a19984f38a53c563893db4ddd6d0c", "3948236d7b1a19984f38a53c563893db4ddd6d0c", 31270, "text/csv", "medusa_legacy"),
    SourceFile(3085, "1785945169566-order-exports-01KZ9A17MY10N4NMCTNK4XD11M.csv", "ae/aef608551d088b7efa797f6683058f72157af4fd", "aef608551d088b7efa797f6683058f72157af4fd", 24269, "text/csv", "medusa"),
    SourceFile(3086, "stripe-payouts.csv", "b1/b1dcb91080f57d70a4d21f096ebaf523f238a929", "b1dcb91080f57d70a4d21f096ebaf523f238a929", 2307, "text/csv", "stripe_payout"),
    SourceFile(3087, "stripe-unified_payments.csv", "41/41dba2bc11cfc0176c90804b2270198e2bddb760", "41dba2bc11cfc0176c90804b2270198e2bddb760", 55895, "text/csv", "stripe_payment"),
    SourceFile(3164, "Printful_Full_Orders_and_Bills_Dec2024_Jul2026.pdf", "f2/f28c0ba9967c590b3505cdb98a88cfd311038e66", "f28c0ba9967c590b3505cdb98a88cfd311038e66", 58391, "application/pdf", "printful"),
    SourceFile(3182, "Stripe Tax Invoice LWA02EXD-2026-07.pdf", "0c/0c4afb366970b8f4967ad39a0fc0282564a5f2c8", "0c4afb366970b8f4967ad39a0fc0282564a5f2c8", 30911, "application/pdf", "supporting_pdf"),
    SourceFile(3183, "Stripe Tax Invoice LWA02EXD-2026-02.pdf", "dd/ddc4d0aa20cbab35be8c7176264a67d582ca508f", "ddc4d0aa20cbab35be8c7176264a67d582ca508f", 31310, "application/pdf", "supporting_pdf"),
    SourceFile(3184, "Stripe Tax Invoice LWA02EXD-2026-01.pdf", "e6/e6e9689ce128f7750d84f938e37496e566db0d53", "e6e9689ce128f7750d84f938e37496e566db0d53", 31534, "application/pdf", "supporting_pdf"),
    SourceFile(3185, "Stripe Tax Invoice LWA02EXD-2025-12.pdf", "4f/4f6cd4c93f3cf57a6b55dd1cea554827ce452730", "4f6cd4c93f3cf57a6b55dd1cea554827ce452730", 31653, "application/pdf", "supporting_pdf"),
    SourceFile(3186, "Stripe Tax Invoice LWA02EXD-2026-03.pdf", "d7/d766fa49a4b4f74d48ce7413c846752b5d603d23", "d766fa49a4b4f74d48ce7413c846752b5d603d23", 31658, "application/pdf", "supporting_pdf"),
    SourceFile(3187, "Stripe Tax Invoice LWA02EXD-2026-04.pdf", "41/41eadccfd13eac9ec4753c88ca638f6340c067df", "41eadccfd13eac9ec4753c88ca638f6340c067df", 31544, "application/pdf", "supporting_pdf"),
    SourceFile(3188, "Stripe Tax Invoice LWA02EXD-2026-05.pdf", "03/036361ab7b3df4b8c5454834693850eb2c6bb0e6", "036361ab7b3df4b8c5454834693850eb2c6bb0e6", 31631, "application/pdf", "supporting_pdf"),
    SourceFile(3189, "Stripe Tax Invoice LWA02EXD-2026-06.pdf", "6b/6b28c52436d4c9bf8d214910b58ce810731633c7", "6b28c52436d4c9bf8d214910b58ce810731633c7", 31412, "application/pdf", "supporting_pdf"),
)

SUPPLEMENTAL_SOURCE_FILES = (
    SourceFile(
        None,
        "medusa-sold-items-2026-08-05.csv",
        "medusa-sold-items-2026-08-05.csv",
        "c40c79abf63639456c230d330e76aa30824151c8",
        24822,
        "text/csv",
        "medusa_items",
    ),
)
SUPPLEMENTAL_SHA256 = {
    "medusa-sold-items-2026-08-05.csv": (
        "e8308c402a63d4c4fd7ee066c8a59daeba7b00cd66f421221191cec50418550a"
    ),
}

CSV_HEADERS = {
    "etsy_statement": ETSY_STATEMENT_HEADER,
    "etsy_items": ETSY_ITEMS_HEADER,
    "medusa_legacy": LEGACY_MEDUSA_HEADER,
    "medusa": MEDUSA_HEADER,
    "medusa_items": MEDUSA_ITEMS_HEADER,
    "revolut": REVOLUT_HEADER,
    "stripe_payout": STRIPE_PAYOUT_HEADER,
    "stripe_payment": STRIPE_PAYMENT_HEADER,
}


def source_options():
    return {
        "host": os.getenv("B2C_SOURCE_DB_HOST", "accounting-source-db"),
        "port": int(os.getenv("B2C_SOURCE_DB_PORT", "5432")),
        "user": os.getenv("B2C_SOURCE_DB_USER", "odoo"),
        "password": os.getenv("B2C_SOURCE_DB_PASSWORD", "odoo"),
        "database": os.getenv(
            "B2C_SOURCE_DATABASE",
            "odoo_online_source_saas_19_3",
        ),
    }


class B2cSourceReader:
    def __init__(self, options=None):
        self.options = options or source_options()

    def _content(self, source_file):
        source_root = (
            SOURCE_FILESTORE
            if source_file.attachment_id is not None
            else SUPPLEMENTAL_EVIDENCE_DIR
        )
        path = (source_root / source_file.store_fname).resolve()
        if source_root not in path.parents or not path.is_file():
            source_label = (
                f"attachment {source_file.attachment_id}"
                if source_file.attachment_id is not None
                else f"supplemental file {source_file.name}"
            )
            raise RuntimeError(
                f"B2C source {source_label} is missing or unsafe",
            )
        content = path.read_bytes()
        if len(content) != source_file.file_size:
            raise RuntimeError(
                f"B2C source file {source_file.name} size changed",
            )
        checksum = hashlib.sha1(content, usedforsecurity=False).hexdigest()
        if checksum != source_file.checksum:
            raise RuntimeError(
                f"B2C source file {source_file.name} SHA-1 changed",
            )
        expected_sha256 = SUPPLEMENTAL_SHA256.get(source_file.name)
        if expected_sha256 and hashlib.sha256(content).hexdigest() != expected_sha256:
            raise RuntimeError(
                f"B2C supplemental source file {source_file.name} SHA-256 changed",
            )
        return content

    def read(self):
        connection = psycopg2.connect(
            **self.options,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        connection.set_session(readonly=True, autocommit=False)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE accounting_source_ro")
                cursor.execute("SHOW transaction_read_only")
                if cursor.fetchone()["transaction_read_only"] != "on":
                    message = "B2C source connection is not read-only"
                    raise RuntimeError(message)
                cursor.execute("SELECT current_user")
                if cursor.fetchone()["current_user"] != "accounting_source_ro":
                    message = "B2C source role is not accounting_source_ro"
                    raise RuntimeError(message)
                cursor.execute(
                    "SELECT id, name, store_fname, checksum, file_size, mimetype, "
                    "company_id FROM ir_attachment WHERE id = ANY(%s) ORDER BY id",
                    ([item.attachment_id for item in SOURCE_FILES],),
                )
                actual = [dict(row) for row in cursor.fetchall()]
                cursor.execute(
                    "SELECT NULLIF(BTRIM(default_code), '') AS sku "
                    "FROM product_product "
                    "WHERE NULLIF(BTRIM(default_code), '') IS NOT NULL ORDER BY sku",
                )
                catalog_skus = tuple(row["sku"] for row in cursor.fetchall())
                cursor.execute(
                    "SELECT company.id, company.name, partner.vat, "
                    "partner.company_registry "
                    "FROM res_company AS company "
                    "JOIN res_partner AS partner ON partner.id = company.partner_id "
                    "WHERE company.id = 1",
                )
                source_company = dict(cursor.fetchone() or {})
        finally:
            connection.rollback()
            connection.close()

        expected = [
            {
                "id": item.attachment_id,
                "name": item.name,
                "store_fname": item.store_fname,
                "checksum": item.checksum,
                "file_size": item.file_size,
                "mimetype": item.mimetype,
                "company_id": 1,
            }
            for item in SOURCE_FILES
        ]
        if actual != expected:
            message = "The B2C source attachment manifest changed"
            raise RuntimeError(message)

        files = []
        documents = defaultdict(list)
        for source_file in (*SOURCE_FILES, *SUPPLEMENTAL_SOURCE_FILES):
            content = self._content(source_file)
            sha256 = hashlib.sha256(content).hexdigest()
            descriptor = {
                "source": source_file,
                "content": content,
                "sha256": sha256,
            }
            files.append(descriptor)
            if source_file.kind in CSV_HEADERS:
                documents[source_file.kind].append(
                    load_csv(
                        source_file.name,
                        sha256,
                        content,
                        CSV_HEADERS[source_file.kind],
                        delimiter=";" if source_file.kind == "medusa_items" else ",",
                    ),
                )
        return {
            "catalog_skus": catalog_skus,
            "documents": dict(documents),
            "files": tuple(files),
            "source_company": source_company,
            "source_company_id": 1,
        }

"""Build an idempotent, synthetic Documents product-review environment.

This script is only for the isolated local QA stack. It uses the real
Paperless service, but no production data, identities, credentials, or live
electronic-invoice/e-reporting services.
"""

import base64
import hashlib
import json
import os
import time

from odoo import Command, fields


def wait_for_operation(operation, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        operation.poll()
        env.cr.commit()
        operation.invalidate_recordset()
        if operation.state in ("archived", "duplicate", "failed"):
            return operation
        time.sleep(2)
    raise RuntimeError(f"Paperless operation {operation.id} timed out")


def ensure_user(login, name, groups, company):
    user = env["res.users"].with_context(no_reset_password=True).search(
        [("login", "=", login)], limit=1
    )
    values = {
        "name": name,
        "login": login,
        "password": "admin",
        "company_id": company.id,
        "company_ids": [Command.set(company.ids)],
        "group_ids": [
            Command.set(
                [
                    env.ref("base.group_user").id,
                    *[env.ref(group).id for group in groups],
                ]
            )
        ],
    }
    if user:
        user.sudo().with_context(
            usl_documents_user_access_no_sync=True,
        ).write(values)
    else:
        user = env["res.users"].with_context(no_reset_password=True).create(values)
    return user


def ensure_metadata(model_name, name, **values):
    model = env[model_name].sudo()
    record = model.search([("name", "=ilike", name)], limit=1)
    desired = {"name": name, **values}
    if record:
        changed = {
            key: value
            for key, value in desired.items()
            if record[key] != value
        }
        if changed:
            record.write(changed)
    else:
        record = model.create(desired)
    return record


def link_once(document, record):
    existing = env["usl.document.link"].search(
        [
            ("document_id", "=", document.id),
            ("res_model", "=", record._name),
            ("res_id", "=", record.id),
            ("active", "=", True),
        ],
        limit=1,
    )
    link = existing or document.link_to_record(record._name, record.id)
    if not link.version_id:
        current = document.version_ids.filtered("is_current")[:1]
        if current:
            link.write({"version_id": current.paperless_version_id})
    return link


def upload_document(spec):
    content = spec["content"].encode()
    checksum = hashlib.sha256(content).hexdigest()
    document = env["usl.document"].with_user(admin).search(
        [
            ("availability_state", "in", ["available", "trashed"]),
            "|",
            ("checksum", "=", checksum),
            ("version_ids.checksum", "=", checksum),
        ],
        limit=1,
    )
    if document and document.availability_state == "trashed":
        # Trash has a separate Paperless API namespace; an idempotent QA rerun
        # must retain the existing root instead of trying to edit it as active.
        return document
    if not document:
        target = spec.get("records", [False])[0]
        result = documents.upload_from_odoo(
            spec["filename"],
            base64.b64encode(content).decode(),
            "text/plain",
            res_model=target._name if target else None,
            res_id=target.id if target else None,
            company_id=spec.get("company", main_company).id,
        )
        if result["state"] == "processing":
            operation = wait_for_operation(
                env["usl.document.operation"].browse(result["operation_id"])
            )
            if operation.state != "archived":
                raise RuntimeError(
                    f"QA document {spec['filename']} failed: "
                    f"{operation.error_message}"
                )
            document = operation.document_id
        else:
            document = documents.browse(result["document_id"])
    document.update_archive_metadata(
        {
            "name": spec["title"],
            "document_date": spec["date"],
            "correspondent_id": spec["correspondent"].id,
            "document_type_id": spec["document_type"].id,
            "tag_ids": [tag.id for tag in spec["tags"]],
        }
    )
    document.with_context(usl_documents_policy_write=True).write(
        {
            "company_id": spec.get("company", main_company).id,
            "confidentiality": spec.get("confidentiality", "internal"),
            "accounting_evidence": spec.get("accounting_evidence", False),
            "review_state": spec.get("review_state", "reviewed"),
        }
    )
    # Fixture text can legitimately evolve between branches. The checksum then
    # identifies the new canonical root, while an older root with the same
    # reserved QA filename would otherwise remain as a misleading duplicate.
    # Keep it recoverable in Trash, but remove its synthetic business links.
    legacy_roots = documents.search(
        [
            ("id", "!=", document.id),
            ("original_filename", "=", spec["filename"]),
            ("availability_state", "!=", "permanently_deleted"),
        ]
    )
    for legacy in legacy_roots:
        legacy.sudo().link_ids.unlink()
        if legacy.availability_state == "available":
            legacy.with_user(admin).move_to_trash()
    for record in spec.get("records", []):
        link_once(document, record)
    return document


params = env["ir.config_parameter"].sudo()
token = os.environ.get("PAPERLESS_QA_TOKEN") or params.get_str(
    "usl_documents.paperless_token"
)
if not token:
    raise RuntimeError("PAPERLESS_QA_TOKEN is required")

params.set_str(
    "usl_documents.paperless_url",
    os.environ.get("PAPERLESS_QA_INTERNAL_URL", "http://paperless-webserver:8000"),
)
params.set_str(
    "usl_documents.paperless_public_url",
    os.environ.get("PAPERLESS_QA_PUBLIC_URL", "http://127.0.0.1:8010"),
)
params.set_str("usl_documents.paperless_token", token)
params.set_int("usl_documents.paperless_timeout", 20)
params.set_int("usl_documents.paperless_trash_retention_days", 30)
params.set_int(
    "usl_documents.paperless_service_user_id",
    int(os.environ.get("PAPERLESS_QA_SERVICE_USER_ID", "3")),
)

admin = env.ref("base.user_admin")
admin.write({"password": "admin"})
documents = env["usl.document"].with_user(admin)
client = documents._paperless()
policy = client.ensure_fail_closed_ingestion_policy()
initial_sync = documents.sync_from_paperless(full=True)

# Keep the product-review archive deterministic. Earlier acceptance revisions
# used random markers and could leave several copies of the same synthetic
# automation fixture. Remove only those recognizable QA artifacts, retain one
# canonical real-service example of each kind, and never touch ordinary archive
# content.
canonical_acceptance_names = {
    "acceptance-qa-real-service.txt",
    "External ingestion qa-real-service",
    "Legal contract qa-real-service",
    "final-accounting-output-qa-real-service.pdf",
}
kept_acceptance_names = set()
legacy_acceptance = documents.browse()
for document in documents.search([], order="paperless_id"):
    if document.availability_state == "permanently_deleted":
        continue
    name = document.name or ""
    is_acceptance_artifact = (
        (name.startswith("acceptance-") and name.endswith(".txt"))
        or name.startswith("External ingestion ")
        or name.startswith("Legal contract ")
        or name.startswith("final-accounting-output-")
        or name.startswith("Browser supplier invoice ")
    )
    if not is_acceptance_artifact:
        continue
    if name in canonical_acceptance_names and name not in kept_acceptance_names:
        kept_acceptance_names.add(name)
        continue
    legacy_acceptance |= document
if legacy_acceptance:
    for document in legacy_acceptance.filtered(
        lambda item: item.availability_state == "available",
    ):
        client.trash_document(document.paperless_id)
    remote_ids = legacy_acceptance.filtered(
        lambda item: item.availability_state in ("available", "trashed"),
    ).mapped("paperless_id")
    if remote_ids:
        client.permanently_delete_trashed_documents(remote_ids)
    operations = env["usl.document.operation"].search(
        [
            "|",
            ("document_id", "in", legacy_acceptance.ids),
            ("target_document_id", "in", legacy_acceptance.ids),
        ],
    )
    operations.unlink()
    legacy_acceptance.link_ids.unlink()
    legacy_acceptance.with_context(usl_documents_cache_write=True).write(
        {
            "availability_state": "permanently_deleted",
            "permanently_deleted_at": fields.Datetime.now(),
            "last_error": (
                "Obsolete synthetic acceptance artifact removed by the "
                "idempotent QA bootstrap."
            ),
        },
    )

main_company = env.company
restricted_company = env["res.company"].search(
    [("name", "=", "Synthetic Restricted Company")], limit=1
) or env["res.company"].create({"name": "Synthetic Restricted Company"})

qa_users = {
    "documents-user": ensure_user(
        "documents-user",
        "Documents General User",
        ["usl_documents.group_documents_user"],
        main_company,
    ),
    "documents-accountant": ensure_user(
        "documents-accountant",
        "Documents Accountant",
        ["usl_documents.group_documents_accountant"],
        main_company,
    ),
    "documents-hr": ensure_user(
        "documents-hr",
        "Documents HR Reviewer",
        [
            "usl_documents.group_documents_user",
            "usl_documents.group_documents_hr",
        ],
        main_company,
    ),
    "documents-restricted": ensure_user(
        "documents-restricted",
        "Documents Restricted User",
        ["usl_documents.group_documents_user"],
        restricted_company,
    ),
}

paperless_users = json.loads(os.environ.get("PAPERLESS_QA_IDENTITIES", "{}"))
extra_pocket_users = {
    item["username"]: item
    for item in json.loads(os.environ.get("POCKET_ID_EXTRA_USERS_JSON", "[]"))
}
sso_specification = extra_pocket_users.get("documents-sso-user")
sso_user = False
if sso_specification:
    sso_user = ensure_user(
        "documents-sso-user",
        "Documents Pocket QA User",
        ["usl_documents.group_documents_user"],
        main_company,
    )
    sso_user.sudo().with_context(
        usl_documents_user_access_no_sync=True,
    ).write(
        {
            "usl_identity_classification": "active",
            "usl_pocketid_access": True,
        },
    )
    provider = env.ref("usl_pocketid.provider_pocketid")
    oidc_identity = env["usl.oidc.identity"].sudo().search(
        [
            ("issuer", "=", provider.usl_oidc_issuer),
            ("subject", "=", sso_specification["id"]),
        ],
        limit=1,
    )
    if not oidc_identity:
        env["usl.oidc.identity"].sudo().create(
            {
                "issuer": provider.usl_oidc_issuer,
                "subject": sso_specification["id"],
                "provider_id": provider.id,
                "user_id": sso_user.id,
            },
        )
identity_pairs = [
    (admin, "archive-admin"),
    *((user, username) for username, user in qa_users.items()),
]
if sso_user and paperless_users.get("documents-sso-user"):
    identity_pairs.append((sso_user, "documents-sso-user"))
for user, username in identity_pairs:
    paperless_user_id = paperless_users.get(username)
    if not paperless_user_id:
        raise RuntimeError(f"Paperless QA identity {username} is missing")
    mapping = env["usl.paperless.user.mapping"].search(
        [("user_id", "=", user.id)], limit=1
    )
    values = {
        "paperless_user_id": int(paperless_user_id),
        "paperless_username": username,
        "sync_state": "synchronized",
        "last_verified_at": fields.Datetime.now(),
        "active": True,
        "qa_local_identity": True,
    }
    if mapping:
        mapping.sudo().with_context(
            usl_documents_mapping_no_sync=True,
        ).write(values)
    else:
        env["usl.paperless.user.mapping"].sudo().with_context(
            usl_documents_mapping_no_sync=True,
        ).create({"user_id": user.id, **values})

contacts = {}
for reference, name in {
    "USL-DOCS-QA-SUPPLIER": "Alpine Office Supplies",
    "USL-DOCS-QA-CUSTOMER": "Northstar Retail",
    "USL-DOCS-QA-BANK": "Banque Démonstration",
    "USL-DOCS-QA-TAX": "French Tax Administration — Synthetic",
}.items():
    contacts[reference] = env["res.partner"].search(
        [("ref", "=", reference)], limit=1
    ) or env["res.partner"].create(
        {
            "name": name,
            "ref": reference,
            "company_id": main_company.id,
        }
    )

people_contact = env["res.partner"].search(
    [("ref", "=", "USL-DOCS-QA-PEOPLE")], limit=1
) or env["res.partner"].create(
    {
        "name": "USL People Operations",
        "ref": "USL-DOCS-QA-PEOPLE",
        "company_id": main_company.id,
    }
)

purchase_journal = env["account.journal"].search(
    [("company_id", "=", main_company.id), ("type", "=", "purchase")], limit=1
) or env["account.journal"].create(
    {
        "name": "Synthetic Purchases",
        "code": "DQA",
        "type": "purchase",
        "company_id": main_company.id,
    }
)
sales_journal = env["account.journal"].search(
    [("company_id", "=", main_company.id), ("type", "=", "sale")], limit=1
) or env["account.journal"].create(
    {
        "name": "Synthetic Sales",
        "code": "DQS",
        "type": "sale",
        "company_id": main_company.id,
    }
)
general_journal = env["account.journal"].search(
    [("company_id", "=", main_company.id), ("type", "=", "general")], limit=1
) or env["account.journal"].create(
    {
        "name": "Synthetic Miscellaneous",
        "code": "DQG",
        "type": "general",
        "company_id": main_company.id,
    }
)

bill = env["account.move"].search(
    [("ref", "=", "USL-DOCS-CEO-QA-BILL"), ("move_type", "=", "in_invoice")],
    limit=1,
) or env["account.move"].create(
    {
        "move_type": "in_invoice",
        "journal_id": purchase_journal.id,
        "partner_id": contacts["USL-DOCS-QA-SUPPLIER"].id,
        "invoice_date": "2026-07-15",
        "ref": "USL-DOCS-CEO-QA-BILL",
        "company_id": main_company.id,
    }
)
customer_invoice = env["account.move"].search(
    [("ref", "=", "USL-DOCS-CEO-QA-CUSTOMER-INVOICE")], limit=1
) or env["account.move"].create(
    {
        "move_type": "out_invoice",
        "journal_id": sales_journal.id,
        "partner_id": contacts["USL-DOCS-QA-CUSTOMER"].id,
        "invoice_date": "2026-07-18",
        "ref": "USL-DOCS-CEO-QA-CUSTOMER-INVOICE",
        "company_id": main_company.id,
    }
)
journal_entry = env["account.move"].search(
    [("ref", "=", "USL-DOCS-QA-BANK-ENTRY")], limit=1
) or env["account.move"].create(
    {
        "move_type": "entry",
        "journal_id": general_journal.id,
        "date": "2026-07-31",
        "ref": "USL-DOCS-QA-BANK-ENTRY",
        "company_id": main_company.id,
    }
)

project = env["project.project"].search(
    [("name", "=", "Atlas Website Rollout")], limit=1
) or env["project.project"].create(
    {"name": "Atlas Website Rollout", "company_id": main_company.id}
)
task = env["project.task"].search(
    [("name", "=", "Approve launch evidence"), ("project_id", "=", project.id)],
    limit=1,
) or env["project.task"].create(
    {"name": "Approve launch evidence", "project_id": project.id}
)
employee = env["hr.employee"].search(
    [("name", "=", "Camille Martin — Synthetic")], limit=1
) or env["hr.employee"].create(
    {"name": "Camille Martin — Synthetic", "company_id": main_company.id}
)
expense = env["hr.expense"].search(
    [
        ("name", "=", "Atlas client workshop travel — Synthetic"),
        ("employee_id", "=", employee.id),
    ],
    limit=1,
) or env["hr.expense"].create(
    {
        "name": "Atlas client workshop travel — Synthetic",
        "employee_id": employee.id,
        "company_id": main_company.id,
        "currency_id": main_company.currency_id.id,
        "date": "2026-07-11",
        "total_amount_currency": 84.20,
    }
)

correspondents = {
    key: ensure_metadata(
        "usl.paperless.correspondent",
        partner.name,
        matching_algorithm="3",
        match=partner.name,
        is_insensitive=True,
    )
    for key, partner in contacts.items()
}
for key, correspondent in correspondents.items():
    correspondent.write({"partner_id": contacts[key].id})
internal_correspondent = ensure_metadata(
    "usl.paperless.correspondent",
    "USL People Operations",
    matching_algorithm="3",
    match="USL PEOPLE OPERATIONS",
    is_insensitive=True,
)
internal_correspondent.with_context(usl_documents_cache_write=True).write(
    {"partner_id": False, "rejected_partner_id": False}
)
ensure_metadata(
    "usl.paperless.correspondent",
    "Mobile Capture Gateway",
    matching_algorithm="0",
)

document_types = {
    name: ensure_metadata(
        "usl.paperless.document.type",
        name,
        matching_algorithm="0",
        is_insensitive=True,
    )
    for name in (
        "Supplier invoice",
        "Customer evidence",
        "Expense receipt",
        "Signed contract",
        "Bank statement",
        "Tax filing",
        "Payroll evidence",
        "Project evidence",
        "General correspondence",
    )
}
tag_specs = {
    "Accounting": ("#355f9f", "SYNTHETIC ACCOUNTING EVIDENCE"),
    "Contracts & legal": ("#71558f", "SYNTHETIC SIGNED AGREEMENT"),
    "Banking": ("#176b87", "SYNTHETIC BANK STATEMENT"),
    "Tax & reporting": ("#2d7a48", "VAT RETURN SYNTHETIC"),
    "HR": ("#9b3a5a", "SYNTHETIC PAYROLL PRIVATE"),
    "Projects": ("#8a5a24", "ATLAS PROJECT EVIDENCE"),
    "Signed": ("#5c6078", "ELECTRONICALLY SIGNED SYNTHETIC"),
    "Needs follow-up": ("#9a6b16", "NEEDS FOLLOW UP SYNTHETIC"),
}
tags = {
    name: ensure_metadata(
        "usl.paperless.tag",
        name,
        color=color,
        matching_algorithm="3",
        match=match,
        is_insensitive=True,
    )
    for name, (color, match) in tag_specs.items()
}

specs = [
    {
        "filename": "qa-supplier-invoice-si-2026-0715.txt",
        "title": "Alpine Office Supplies — Invoice SI-2026-0715",
        "date": "2026-07-15",
        "content": (
            "SYNTHETIC ACCOUNTING EVIDENCE\nSupplier invoice SI-2026-0715\n"
            "OCR-only search phrase: heliotrope cobalt compliance evidence.\n"
            "Subtotal 1,250.00 EUR — VAT 250.00 EUR — Total 1,500.00 EUR"
        ),
        "correspondent": correspondents["USL-DOCS-QA-SUPPLIER"],
        "document_type": document_types["Supplier invoice"],
        "tags": [tags["Accounting"]],
        "records": [bill, contacts["USL-DOCS-QA-SUPPLIER"]],
        "confidentiality": "accounting",
        "accounting_evidence": True,
    },
    {
        "filename": "qa-customer-delivery-evidence.txt",
        "title": "Northstar Retail — Delivery acceptance",
        "date": "2026-07-18",
        "content": (
            "ATLAS PROJECT EVIDENCE\nCustomer acceptance for delivery DEL-7741.\n"
            "Accepted against customer invoice USL-DOCS-CEO-QA-CUSTOMER-INVOICE."
        ),
        "correspondent": correspondents["USL-DOCS-QA-CUSTOMER"],
        "document_type": document_types["Customer evidence"],
        "tags": [tags["Projects"]],
        "records": [customer_invoice, task],
    },
    {
        "filename": "qa-expense-receipt.txt",
        "title": "Synthetic travel receipt — client workshop",
        "date": "2026-07-11",
        "content": (
            "SYNTHETIC ACCOUNTING EVIDENCE\nTravel receipt for Atlas workshop.\n"
            "Amount 84.20 EUR. Employee: Camille Martin."
        ),
        "correspondent": correspondents["USL-DOCS-QA-SUPPLIER"],
        "document_type": document_types["Expense receipt"],
        "tags": [tags["Accounting"], tags["Projects"]],
        "records": [expense, project],
        "confidentiality": "accounting",
        "accounting_evidence": True,
    },
    {
        "filename": "qa-signed-contract-v1.txt",
        "title": "Northstar Retail — Signed services agreement",
        "date": "2026-06-30",
        "content": (
            "SYNTHETIC SIGNED AGREEMENT\nELECTRONICALLY SIGNED SYNTHETIC\n"
            "Services agreement between USL and Northstar Retail. Initial term."
        ),
        "correspondent": correspondents["USL-DOCS-QA-CUSTOMER"],
        "document_type": document_types["Signed contract"],
        "tags": [tags["Contracts & legal"], tags["Signed"]],
        "records": [contacts["USL-DOCS-QA-CUSTOMER"], project],
    },
    {
        "filename": "qa-bank-statement-july.txt",
        "title": "Banque Démonstration — July 2026 statement",
        "date": "2026-07-31",
        "content": (
            "SYNTHETIC BANK STATEMENT\nStatement period 1–31 July 2026.\n"
            "Closing balance 42,775.31 EUR."
        ),
        "correspondent": correspondents["USL-DOCS-QA-BANK"],
        "document_type": document_types["Bank statement"],
        "tags": [tags["Banking"], tags["Accounting"]],
        "records": [journal_entry],
        "confidentiality": "accounting",
        "accounting_evidence": True,
    },
    {
        "filename": "qa-vat-return-july.txt",
        "title": "Synthetic VAT return — July 2026",
        "date": "2026-07-31",
        "content": (
            "VAT RETURN SYNTHETIC\nSYNTHETIC ACCOUNTING EVIDENCE\n"
            "French VAT package for July 2026. Net VAT payable 7,842.00 EUR."
        ),
        "correspondent": correspondents["USL-DOCS-QA-TAX"],
        "document_type": document_types["Tax filing"],
        "tags": [tags["Tax & reporting"], tags["Accounting"]],
        "records": [main_company],
        "confidentiality": "accounting",
        "accounting_evidence": True,
    },
    {
        "filename": "qa-payroll-evidence.txt",
        "title": "Camille Martin — July payroll evidence",
        "date": "2026-07-31",
        "content": (
            "SYNTHETIC PAYROLL PRIVATE\nPayroll evidence for Camille Martin.\n"
            "This fixture contains no real salary or personal information."
        ),
        "correspondent": internal_correspondent,
        "document_type": document_types["Payroll evidence"],
        "tags": [tags["HR"]],
        "records": [employee],
        "confidentiality": "hr",
    },
    {
        "filename": "qa-project-launch-evidence.txt",
        "title": "Atlas rollout — Launch approval evidence",
        "date": "2026-07-25",
        "content": (
            "ATLAS PROJECT EVIDENCE\nLaunch checklist reviewed and accepted.\n"
            "Performance, accessibility and security review complete."
        ),
        "correspondent": correspondents["USL-DOCS-QA-CUSTOMER"],
        "document_type": document_types["Project evidence"],
        "tags": [tags["Projects"]],
        "records": [project, task],
    },
]
seeded = {spec["filename"]: upload_document(spec) for spec in specs}

# Paperless custom fields are searchable archive metadata, not Odoo-only
# columns. Keep the fixture idempotent and assign representative invoice data
# through the supported document API.
remote_custom_fields = client.list_custom_fields()
invoice_reference_field = next(
    (
        item
        for item in remote_custom_fields
        if item["name"] == "Invoice reference"
    ),
    None,
) or client.create_custom_field(
    {"name": "Invoice reference", "data_type": "string"},
)
invoice_amount_field = next(
    (
        item
        for item in remote_custom_fields
        if item["name"] == "Gross amount"
    ),
    None,
) or client.create_custom_field(
    {"name": "Gross amount", "data_type": "monetary"},
)
supplier_document = seeded["qa-supplier-invoice-si-2026-0715.txt"]
client.update_document_metadata(
    supplier_document.paperless_id,
    {
        "custom_fields": [
            {"field": invoice_reference_field["id"], "value": "INV-QA-2026-0042"},
            {"field": invoice_amount_field["id"], "value": "1240.00"},
        ],
    },
)

contract = seeded["qa-signed-contract-v1.txt"]
replacement = (
    "SYNTHETIC SIGNED AGREEMENT\nELECTRONICALLY SIGNED SYNTHETIC\n"
    "Services agreement amendment. Current commercial term; original retained."
).encode()
replacement_checksum = hashlib.sha256(replacement).hexdigest()
if replacement_checksum not in contract.version_ids.mapped("checksum"):
    result = contract.upload_new_version(
        "qa-signed-contract-amendment.txt",
        base64.b64encode(replacement).decode(),
        "text/plain",
        "Signed amendment — current",
    )
    if result["state"] == "processing":
        wait_for_operation(
            env["usl.document.operation"].browse(result["operation_id"])
        )

# Older QA bootstrap revisions could create another root after the contract's
# current checksum changed. Preserve those test artifacts, but make the
# duplicate condition explicit and remove them from the clean Contracts view.
contract_original_checksum = hashlib.sha256(
    specs[3]["content"].encode()
).hexdigest()
legacy_contract_roots = documents.search(
    [
        ("id", "!=", contract.id),
        ("availability_state", "=", "available"),
        ("version_ids.checksum", "=", contract_original_checksum),
    ],
    order="paperless_id",
)
for index, duplicate in enumerate(legacy_contract_roots, start=1):
    duplicate.update_archive_metadata(
        {
            "name": (
                "Duplicate candidate — Northstar signed agreement"
                if index == 1
                else f"Version recovery exercise {index} — Northstar agreement"
            ),
            "document_type_id": document_types["Signed contract"].id,
            "correspondent_id": correspondents["USL-DOCS-QA-CUSTOMER"].id,
            "tag_ids": [tags["Needs follow-up"].id],
        }
    )
    duplicate.with_context(usl_documents_policy_write=True).write(
        {"review_state": "needs_attention"}
    )
    duplicate.link_ids.filtered(
        lambda link: (
            link.res_model == contacts["USL-DOCS-QA-CUSTOMER"]._name
            and link.res_id == contacts["USL-DOCS-QA-CUSTOMER"].id
        )
        or (
            link.res_model == project._name
            and link.res_id == project.id
        )
    ).unlink()

# Upload one item directly through Paperless. It deliberately remains in Needs
# review with no business link, proving that external intake is discoverable.
external_content = (
    "NEEDS FOLLOW UP SYNTHETIC\nExternal mailroom intake.\n"
    "Please decide company, correspondent and business relationship."
).encode()
external_checksum = hashlib.sha256(external_content).hexdigest()
external = documents.search([("checksum", "=", external_checksum)], limit=1)
if not external:
    task_id = client.upload_multipart(
        external_content,
        "qa-external-mailroom-intake.txt",
        "text/plain",
        title="External mailroom intake — needs review",
    )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        remote_task = client.task(task_id)
        if remote_task.get("status") in ("SUCCESS", "success"):
            break
        if remote_task.get("status") in ("FAILURE", "failed"):
            raise RuntimeError(f"External QA ingestion failed: {remote_task}")
        time.sleep(2)
    documents.sync_from_paperless(full=True)
    external = documents.search([("checksum", "=", external_checksum)], limit=1)
if not external:
    raise RuntimeError("External QA ingestion was not synchronized")

# Re-submit an identical supplier file to prove reuse rather than another root.
supplier_spec = specs[0]
duplicate_result = documents.upload_from_odoo(
    "qa-supplier-invoice-duplicate.txt",
    base64.b64encode(supplier_spec["content"].encode()).decode(),
    "text/plain",
    res_model=bill._name,
    res_id=bill.id,
)
if duplicate_result["state"] != "duplicate":
    raise RuntimeError("Synthetic duplicate fixture was not detected")

failed_operation = env["usl.document.operation"].search(
    [("name", "=", "qa-corrupted-upload.pdf")], limit=1
)
if not failed_operation:
    failed_operation = env["usl.document.operation"].sudo().create(
        {
            "name": "qa-corrupted-upload.pdf",
            "state": "failed",
            "checksum": hashlib.sha256(b"synthetic-corrupt-pdf").hexdigest(),
            "mime_type": "application/pdf",
            "company_id": main_company.id,
            "source": "odoo_upload",
            "error_message": (
                "Synthetic QA failure: the file is corrupted. Upload a valid PDF."
            ),
        }
    )

trash_spec = {
    "filename": "qa-retention-trash-sample.txt",
    "title": "Retention review sample — in Trash",
    "date": "2026-05-01",
    "content": (
        "SYNTHETIC RETENTION SAMPLE\nDocument intentionally moved to Trash for QA."
    ),
    "correspondent": internal_correspondent,
    "document_type": document_types["General correspondence"],
    "tags": [tags["Needs follow-up"]],
    "records": [main_company],
}
trashed = upload_document(trash_spec)
if trashed.availability_state == "available":
    client.trash_document(trashed.paperless_id)
    documents.sync_from_paperless(full=True)

documents.search(
    [("availability_state", "in", ("available", "permission_error"))]
).action_sync_permissions()
env.cr.commit()
print(
    "DOCUMENTS_QA_READY",
    {
        "initial_sync": initial_sync,
        "legacy_acceptance_removed": len(legacy_acceptance),
        "documents": documents.search_count([]),
        "links": env["usl.document.link"].search_count([("active", "=", True)]),
        "versions": env["usl.document.version"].search_count([]),
        "trashed": documents.search_count([("availability_state", "=", "trashed")]),
        "needs_review": documents.search_count(
            [("review_state", "=", "needs_attention")]
        ),
        "failed_operation": failed_operation.id,
        "bill_id": bill.id,
        "customer_invoice_id": customer_invoice.id,
        "project_id": project.id,
        "task_id": task.id,
        "employee_id": employee.id,
        "expense_id": expense.id,
        "policy": policy["workflow_id"],
    },
)

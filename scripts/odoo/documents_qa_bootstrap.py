"""Configure an isolated Documents QA database from environment variables.

This script is safe only for synthetic local/QA archives. It never creates a
Paperless credential and never contains one in source control.
"""

import os

from odoo import fields


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
params.set_int(
    "usl_documents.paperless_service_user_id",
    int(os.environ.get("PAPERLESS_QA_SERVICE_USER_ID", "3")),
)
policy = env["usl.document"]._paperless().ensure_fail_closed_ingestion_policy()

admin = env.ref("base.user_admin")
mapping = env["usl.paperless.user.mapping"].search(
    [("user_id", "=", admin.id)], limit=1
)
mapping_values = {
    "paperless_user_id": int(os.environ.get("PAPERLESS_QA_USER_ID", "2")),
    "paperless_username": os.environ.get(
        "PAPERLESS_QA_USERNAME", "archive-admin"
    ),
    "sync_state": "synchronized",
    "last_verified_at": fields.Datetime.now(),
}
if mapping:
    mapping.write(mapping_values)
else:
    mapping = env["usl.paperless.user.mapping"].create({
        "user_id": admin.id,
        **mapping_values,
    })

result = env["usl.document"].with_user(admin).sync_from_paperless(full=True)
documents = env["usl.document"].with_user(admin).search([
    ("company_id", "=", False),
])
documents.write({
    "company_id": env.company.id,
    "review_state": "classified",
})
all_documents = env["usl.document"].with_user(admin).search([])
all_documents.action_sync_permissions()

# Stable synthetic records used by the browser journeys. No chart, posting, or
# production data is required: a draft bill is enough to validate record-bound
# evidence upload and linking.
partner = env["res.partner"].search(
    [("ref", "=", "USL-DOCS-QA-SUPPLIER")], limit=1
)
if not partner:
    partner = env["res.partner"].create({
        "name": "Synthetic Documents Supplier",
        "ref": "USL-DOCS-QA-SUPPLIER",
        "company_id": env.company.id,
    })
bill = env["account.move"].search(
    [("ref", "=", "USL-DOCS-QA-BILL"), ("move_type", "=", "in_invoice")],
    limit=1,
)
if not bill:
    purchase_journal = env["account.journal"].search([
        ("company_id", "=", env.company.id),
        ("type", "=", "purchase"),
    ], limit=1)
    if not purchase_journal:
        purchase_journal = env["account.journal"].create({
            "name": "Synthetic Purchases",
            "code": "DQA",
            "type": "purchase",
            "company_id": env.company.id,
        })
    bill = env["account.move"].create({
        "move_type": "in_invoice",
        "journal_id": purchase_journal.id,
        "partner_id": partner.id,
        "invoice_date": fields.Date.today(),
        "ref": "USL-DOCS-QA-BILL",
        "company_id": env.company.id,
    })

project = env["project.project"].search(
    [("name", "=", "Synthetic Documents Project")], limit=1
)
if not project:
    project = env["project.project"].create({
        "name": "Synthetic Documents Project",
        "company_id": env.company.id,
    })
task = env["project.task"].search(
    [("name", "=", "Synthetic Documents Task"), ("project_id", "=", project.id)],
    limit=1,
)
if not task:
    task = env["project.task"].create({
        "name": "Synthetic Documents Task",
        "project_id": project.id,
    })

restricted_company = env["res.company"].search(
    [("name", "=", "Synthetic Restricted Company")], limit=1
)
if not restricted_company:
    restricted_company = env["res.company"].create({
        "name": "Synthetic Restricted Company",
    })
restricted_user = env["res.users"].with_context(no_reset_password=True).search(
    [("login", "=", "documents-restricted")], limit=1
)
if not restricted_user:
    restricted_user = env["res.users"].with_context(no_reset_password=True).create({
        "name": "Documents Restricted User",
        "login": "documents-restricted",
        "password": os.environ.get(
            "ODOO_DOCUMENTS_QA_RESTRICTED_PASSWORD", "documents-local-only"
        ),
        "company_id": restricted_company.id,
        "company_ids": [(6, 0, [restricted_company.id])],
        "group_ids": [(6, 0, [
            env.ref("base.group_user").id,
            env.ref("usl_documents.group_documents_user").id,
        ])],
    })
env.cr.commit()
print(
    "Documents QA ready:",
    result,
    "mapped_documents=",
    len(all_documents),
    "identity=",
    mapping.paperless_username,
    "ingestion_policy=",
    policy["workflow_id"],
    "bill_id=",
    bill.id,
    "project_id=",
    project.id,
    "task_id=",
    task.id,
    "restricted_user_id=",
    restricted_user.id,
)

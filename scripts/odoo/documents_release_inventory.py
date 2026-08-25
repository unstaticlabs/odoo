"""Emit non-secret Odoo release-cohort counters through ``odoo shell``."""

# ruff: noqa: EM101, F821, T201 - Odoo shell injects ``env``.

import json
import os
from decimal import Decimal


def grouped_count(table, field, *, where="TRUE"):
    env.cr.execute(
        f'SELECT COALESCE("{field}"::text, \'<unset>\'), count(*) '
        f'FROM "{table}" WHERE {where} GROUP BY 1 ORDER BY 1',  # noqa: S608
    )
    return {str(name): int(count) for name, count in env.cr.fetchall()}


def scalar(query, parameters=()):
    env.cr.execute(query, parameters)
    return env.cr.fetchone()[0]


ledger = grouped_count(
    "ir_attachment",
    "usl_documents_ledger_state",
    where="type = 'binary' AND res_id > 0",
)
operations = grouped_count("usl_document_operation", "state")
root_roles = grouped_count("usl_document", "intake_role")
link_roles = grouped_count(
    "usl_document_link",
    "document_role",
    where="active",
)
module_residue = int(
    scalar(
        "SELECT count(*) FROM ir_module_module "
        "WHERE state = 'installed' AND name LIKE %s ESCAPE '\\'",
        (r"usl\_%\_restore",),
    ),
)
permission_failures = int(
    scalar(
        "SELECT count(*) FROM usl_document "
        "WHERE permission_sync_state = 'failed'",
    ),
)
active_links = int(scalar("SELECT count(*) FROM usl_document_link WHERE active"))
roots = int(scalar("SELECT count(*) FROM usl_document"))
versions = int(scalar("SELECT count(*) FROM usl_document_version"))
posted_moves = int(
    scalar("SELECT count(*) FROM account_move WHERE state = 'posted'"),
)
move_lines = int(scalar("SELECT count(*) FROM account_move_line"))
env.cr.execute(
    "SELECT COALESCE(round(sum(line.debit)::numeric, 2), 0), "
    "COALESCE(round(sum(line.credit)::numeric, 2), 0) "
    "FROM account_move_line line "
    "JOIN account_move move ON move.id = line.move_id "
    "WHERE move.state = 'posted'",
)
posted_debit, posted_credit = env.cr.fetchone()

blocking_states = {
    "eligible_attachment_pending": ledger.get("pending", 0),
    "eligible_attachment_unresolved": ledger.get("unresolved", 0),
    "odoo_operations_failed": operations.get("failed", 0),
    "odoo_operations_pending": operations.get("pending", 0),
    "odoo_operations_processing": (
        operations.get("processing", 0) + operations.get("uploading", 0)
    ),
    "permission_failures": permission_failures,
    "migration_module_residue": module_residue,
}
blockers = [
    f"{name}={value}"
    for name, value in sorted(blocking_states.items())
    if value
]
status = "passed" if not blockers else "partial"
result = {
    "schema": "usl-documents-archive-policy-ledger-v1",
    "status": status,
    "database": env.cr.dbname,
    "ledger_state_counts": ledger,
    "operation_state_counts": operations,
    "root_role_counts": root_roles,
    "link_role_counts": link_roles,
    "root_count": roots,
    "active_link_count": active_links,
    "version_count": versions,
    "permission_failures": permission_failures,
    "migration_module_residue": module_residue,
    "runtime_paperless_token_configured": bool(
        env["ir.config_parameter"].sudo().get_str(
            "usl_documents.paperless_token",
        ),
    ),
    "accounting": {
        "posted_moves": posted_moves,
        "move_lines": move_lines,
        "posted_debit": str(Decimal(posted_debit)),
        "posted_credit": str(Decimal(posted_credit)),
        "balanced": Decimal(posted_debit) == Decimal(posted_credit),
    },
    "qualification_counters": blocking_states,
    "blockers": blockers,
}
print("USL_DOCUMENTS_RELEASE_INVENTORY=" + json.dumps(result, sort_keys=True))
if os.environ.get("USL_RELEASE_REQUIRE_COMPLETE") == "1" and blockers:
    raise RuntimeError("Documents release inventory is incomplete: " + ", ".join(blockers))

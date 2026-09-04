"""Clear the Odoo mirror before an explicit disposable Paperless reset."""

import os


if os.environ.get("DOCUMENTS_CANONICAL_RESET_CONFIRMED") != "1":
    raise RuntimeError("Documents target-cache reset was not explicitly confirmed")
if env.cr.dbname != "odoo_dev":  # noqa: F821
    raise RuntimeError("Documents target-cache reset is reserved for odoo_dev")


def _quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def _clear_external_references(referenced_table, internal_tables):
    """Detach rebuildable product references before deleting the mirror.

    Documents consumers deliberately use restrictive foreign keys so ordinary
    product actions cannot orphan archive references.  A confirmed disposable
    reconstruction is different: downstream stages recreate those links from
    the governed source.  Discovering the nullable consumers from PostgreSQL
    keeps this migration-only reset complete as integrations evolve, while a
    non-nullable consumer fails closed and requires an explicit migration.
    """
    env.cr.execute(  # noqa: F821
        """
        SELECT child_ns.nspname, child.relname, child_column.attname,
               child_column.attnotnull
          FROM pg_constraint AS foreign_key
          JOIN pg_class AS parent
            ON parent.oid = foreign_key.confrelid
          JOIN pg_namespace AS parent_ns
            ON parent_ns.oid = parent.relnamespace
          JOIN pg_class AS child
            ON child.oid = foreign_key.conrelid
          JOIN pg_namespace AS child_ns
            ON child_ns.oid = child.relnamespace
          JOIN pg_attribute AS child_column
            ON child_column.attrelid = child.oid
           AND child_column.attnum = foreign_key.conkey[1]
         WHERE foreign_key.contype = 'f'
           AND parent_ns.nspname = 'public'
           AND parent.relname = %s
           AND array_length(foreign_key.conkey, 1) = 1
         ORDER BY child_ns.nspname, child.relname, child_column.attname
        """,
        [referenced_table],
    )
    cleared = {}
    for schema_name, table_name, column_name, is_required in env.cr.fetchall():  # noqa: F821
        if table_name in internal_tables:
            continue
        if is_required:
            raise RuntimeError(
                "Documents reset cannot detach required reference "
                f"{schema_name}.{table_name}.{column_name}"
            )
        qualified_table = ".".join(
            (_quote_identifier(schema_name), _quote_identifier(table_name)),
        )
        quoted_column = _quote_identifier(column_name)
        env.cr.execute(  # noqa: F821
            f"UPDATE {qualified_table} SET {quoted_column} = NULL "
            f"WHERE {quoted_column} IS NOT NULL"
        )
        if env.cr.rowcount:
            cleared[f"{schema_name}.{table_name}.{column_name}"] = env.cr.rowcount
    return cleared

Operation = env["usl.document.operation"].sudo()  # noqa: F821
Link = env["usl.document.link"].sudo()  # noqa: F821
Document = env["usl.document"].sudo()  # noqa: F821
ContextTag = env["usl.document.context.tag"].sudo()  # noqa: F821
SmartView = env["usl.document.smart.view"].sudo()  # noqa: F821
UserMapping = env["usl.paperless.user.mapping"].sudo()  # noqa: F821
Parameter = env["ir.config_parameter"].sudo()  # noqa: F821
Evidence = env.get("b2c.provider.evidence")  # noqa: F821
metadata_models = {
    "paperless_correspondents": env["usl.paperless.correspondent"].sudo(),  # noqa: F821
    "paperless_document_types": env["usl.paperless.document.type"].sudo(),  # noqa: F821
    "paperless_tags": env["usl.paperless.tag"].sudo(),  # noqa: F821
}

evidence_links = (
    Evidence.sudo().search([("archived_document_id", "!=", False)])
    if Evidence is not None and "archived_document_id" in Evidence._fields
    else Evidence
)

cleared_references = {}
cleared_references.update(
    _clear_external_references(
        "usl_document_operation",
        {"usl_document_operation"},
    ),
)
cleared_references.update(
    _clear_external_references(
        "usl_document",
        {
            "usl_document_link",
            "usl_document_operation",
            "usl_document_permitted_user_rel",
            "usl_document_tag_rel",
            "usl_document_user_state",
            "usl_document_version",
        },
    ),
)

counts = {
    "operations": Operation.search_count([]),
    "links": Link.search_count([]),
    "documents": Document.with_context(active_test=False).search_count([]),
    "b2c_evidence_links": len(evidence_links) if evidence_links else 0,
    "context_tags": ContextTag.with_context(active_test=False).search_count([]),
    "paperless_saved_view_identities": SmartView.with_context(
        active_test=False,
    ).search_count([("paperless_id", "!=", False)]),
    "paperless_user_mappings": UserMapping.with_context(
        active_test=False,
    ).search_count([]),
}
counts.update(
    {
        name: model.with_context(active_test=False).search_count([])
        for name, model in metadata_models.items()
    },
)
Operation.search([]).unlink()
Link.search([]).unlink()
if evidence_links:
    # The delivered relationship remains restrictive. Only this explicitly
    # confirmed one-shot reset may detach it before rebuilding the same locked
    # archive; the final B2C pass restores every checksum-backed link.
    evidence_links.with_context(b2c_evidence_import=True).write(
        {"archived_document_id": False},
    )
Document.with_context(active_test=False).search([]).unlink()

# Paperless allocates every catalog, saved-view, and user identity inside its
# own PostgreSQL volume.  The volume is removed immediately after this script,
# so retaining any of those numeric identities in Odoo could bind an unrelated
# object after the fresh archive reuses the same integer.  Rebuild the cache
# boundary completely; all business data and source-backed relationships are
# reconstructed later in the same governed run.
UserMapping.with_context(active_test=False).search([]).unlink()
ContextTag.with_context(active_test=False).search([]).unlink()
SmartView.with_context(active_test=False).search([]).with_context(
    usl_documents_archive_view_sync=True,
).write(
    {
        "tag_ids": [(5, 0, 0)],
        "document_type_ids": [(5, 0, 0)],
        "correspondent_ids": [(5, 0, 0)],
        "paperless_filter_json": False,
        "paperless_sync_state": "pending",
        "paperless_sync_error": False,
        "paperless_synced_at": False,
    },
)
# Odoo's Integer conversion writes ``False`` as zero, which would violate the
# unique saved-view identity constraint for the second row.  SQL NULL is the
# actual unbound representation used when these records are first created.
env.cr.execute(  # noqa: F821
    "UPDATE usl_document_smart_view "
    "SET paperless_id = NULL WHERE paperless_id IS NOT NULL"
)
SmartView.invalidate_model(["paperless_id"])
paperless_tags = metadata_models["paperless_tags"].with_context(
    active_test=False,
).search([])
paperless_tags.with_context(usl_documents_cache_write=True).write(
    {"parent_id": False},
)
for model in metadata_models.values():
    model.with_context(active_test=False).search([]).with_context(
        usl_documents_cache_write=True,
    ).unlink()

Parameter.search(
    [
        (
            "key",
            "in",
            [
                "usl_documents.last_sync",
                "usl_documents.sync_checkpoint",
                "usl_documents.sync_cursor_page",
                "usl_documents.sync_error",
                "usl_documents.sync_mode",
                "usl_documents.sync_modified_after",
                "usl_documents.sync_status",
                "usl_documents.paperless_custom_fields",
            ],
        ),
    ],
).unlink()
env.cr.commit()  # noqa: F821
print(
    "DOCUMENTS_TARGET_CACHE_RESET="
    + ",".join(f"{key}:{value}" for key, value in sorted(counts.items())),
    flush=True,
)
print(
    "DOCUMENTS_EXTERNAL_REFERENCES_CLEARED="
    + (
        ",".join(
            f"{key}:{value}" for key, value in sorted(cleared_references.items())
        )
        or "none"
    ),
    flush=True,
)

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    document_model = env["usl.document"].sudo()
    operations = env["usl.document.operation"].sudo().search(
        [("context_json", "!=", False)],
        order="id",
    )
    fingerprints = {}
    for operation in operations:
        metadata_hash = document_model._archive_metadata_hash(operation.context_json)
        operation.write({"metadata_hash": metadata_hash})
        document = operation.document_id
        if not document:
            continue
        fingerprints.setdefault(
            (document.id, operation.checksum),
            set(),
        ).add(metadata_hash)

    for (document_id, checksum), metadata_hashes in fingerprints.items():
        # Content-only reuse in an older release may already have merged two
        # classifications. Keep that ambiguous pair unmatched instead of
        # choosing one fingerprint and recreating the collision during upgrade.
        if len(metadata_hashes) != 1:
            continue
        metadata_hash = next(iter(metadata_hashes))
        document = document_model.browse(document_id)
        versions = document.version_ids.filtered(
            lambda item: item.checksum == checksum,
        )
        if versions:
            versions.write({"metadata_hash": metadata_hash})
        if document.checksum == checksum:
            document.with_context(usl_documents_cache_write=True).write(
                {"metadata_hash": metadata_hash},
            )

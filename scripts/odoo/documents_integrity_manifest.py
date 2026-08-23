"""Print the Paperless/Odoo cross-system integrity manifest as JSON.

Run through ``odoo shell`` after both databases and Paperless media have been
backed up. The caller redirects stdout into the encrypted backup set.
"""

import json
import os


manager_group = env.ref("usl_documents.group_documents_manager")
manager = env["res.users"].sudo().search(
    [("active", "=", True), ("all_group_ids", "in", manager_group.id)],
    order="id",
    limit=1,
)
if not manager:
    raise RuntimeError("No active Documents administrator can create the manifest.")
documents = env["usl.document"].with_user(manager)
manifest = documents.integrity_manifest(os.environ.get("USL_BACKUP_ID"))
if os.environ.get("USL_BACKUP_COMPLETION_STATUS"):
    env["ir.config_parameter"].sudo().set_str(
        "usl_documents.backup_completion_status",
        os.environ["USL_BACKUP_COMPLETION_STATUS"],
    )
    env.cr.commit()
    manifest = documents.integrity_manifest(os.environ.get("USL_BACKUP_ID"))
print(json.dumps(manifest, indent=2, sort_keys=True))

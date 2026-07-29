"""Print the Paperless/Odoo cross-system integrity manifest as JSON.

Run through ``odoo shell`` after both databases and Paperless media have been
backed up. The caller redirects stdout into the encrypted backup set.
"""

import json
import os


manifest = env["usl.document"].with_user(env.ref("base.user_admin")).integrity_manifest(
    os.environ.get("USL_BACKUP_ID")
)
print(json.dumps(manifest, indent=2, sort_keys=True))


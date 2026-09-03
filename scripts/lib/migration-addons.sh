#!/usr/bin/env bash

# Reconstruction deliberately keeps temporary source identities available
# until every fallible restore stage has passed. Every migration-only runner
# must therefore load the complete temporary registry, including terminal
# stages already installed during the current reconstruction. The delivered Odoo service
# never uses this path.
USL_MIGRATION_ADDONS_PATH="/opt/odoo/addons,/opt/odoo/odoo/addons,/mnt/custom-addons,/mnt/oca-addons,/mnt/accounting-migration-addons,/mnt/identity-migration-addons,/mnt/product-migration-addons,/mnt/b2c-migration-addons,/mnt/hr-migration-addons,/mnt/project-migration-addons,/mnt/tese-migration-addons,/mnt/platform-billing-migration-addons,/mnt/collaboration-migration-addons"
readonly USL_MIGRATION_ADDONS_PATH

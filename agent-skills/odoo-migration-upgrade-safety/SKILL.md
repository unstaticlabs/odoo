---
name: odoo-migration-upgrade-safety
description: Design and qualify Odoo schema changes, module upgrades, data migrations, backups, recovery, and database or filestore integrity. Use whenever a change alters stored data, manifests, upgrade behavior, migration scripts, attachments, configuration, or release sequencing.
---

# Odoo Migration and Upgrade Safety

After cutover, the Community production database is canonical. The old Odoo Online dump is not a valid future rollback strategy.

1. Identify every affected model, field, constraint, index, relation, external identifier, module, configuration key, attachment, and filestore path.
2. Separate normal Odoo module upgrade behavior from explicit data migration steps. State the required `-u` module set and dependency order.
3. Make forward operations idempotent or document and guard their single-run semantics. Test both a representative upgrade and a clean install when risk warrants it.
4. Preserve relational integrity, company boundaries, access metadata, attachments, chatter, business dates, and stable XML/data ownership.
5. Define preflight, database and filestore backup consistency, forward procedure, verification queries/journeys, failure detection, and recovery. A rollback may be restore-and-redeploy; it need not be a reverse migration, but it must be credible and timed.
6. State data-loss risk and genuinely irreversible effects explicitly. Do not label an operation reversible merely because code can be rolled back.
7. Never exercise destructive migration logic on production or the protected read-only Online source. Use named disposable validation databases and repository runbooks.
8. Keep one-shot Online-to-Community reconstruction machinery under `migration/` and out of the delivered registry. Run `make product-migration-boundary` when that boundary is affected.

Put precise forward/recovery steps and module-upgrade evidence in the handoff. The Lead Developer independently validates them; CI eventually owns production backup, upgrade, verify, and recovery orchestration.

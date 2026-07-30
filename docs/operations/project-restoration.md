# Projects restoration from Odoo Online

This runbook restores the useful Projects perimeter from the private Odoo
Online dump into a disposable or approved Community target. It does not alter
the source database and it never replays outgoing mail.

For a plain-English product-review tour of a prepared local copy, see
[Projects restoration: local QA guide](project-restoration-qa-guide.md).

## Design decision

The finalized product uses standard Community Projects plus the focused
`usl_project` runtime add-on. The importer is a temporary
`usl_project_restore` add-on under `migration/project_restore/addons/`; it is
available only to the dedicated `project-migration` service and is uninstalled
before product acceptance.

Three approaches were evaluated:

1. Standard Odoo import was the lightest option, but cannot preserve source
   identities, chatter chronology, tracking values, inactive activities,
   filestore attachments, dependency links, or safe repeat execution.
2. OCA modules were considered for planning and migration. No maintained
   combination supplied source-aware, idempotent restoration of this complete
   perimeter. Adding a broad project suite would also introduce behaviour not
   used in the source.
3. A temporary Odoo migration add-on uses the ORM and source bindings while a
   focused product add-on retains only the missing planned-start field and
   dependency-date warning. Finalization removes the migration module,
   provenance fields, models and XML IDs. This is the selected approach.

No Odoo core code is changed. The product add-on depends only on native
`project`. Migration-only dependencies do not enter the product dependency
graph.

## Source reconciliation

The inspected snapshot contains:

| Perimeter | Source records |
| --- | ---: |
| Projects | 17 |
| Tasks and one task template | 1,793 |
| Project stages / task stages | 4 / 99 |
| Tags / milestones / recurrences / updates | 122 / 3 / 14 / 16 |
| Task assignees / tags / parents / dependencies | 387 / 3,075 / 1,221 / 227 |
| Project stage / tag / favourite links | 103 / 16 / 13 |
| Task milestone / recurrence links | 66 / 9 |
| Chatter messages / tracking values | 18,458 / 7,506 |
| Message parents / recipients / attachment links | 14,432 / 48 / 13 |
| Followers / activities | 2,051 / 658 |
| Binary attachments | 38 (15,433,661 bytes) |
| Project aliases / named local parts | 17 / 11 |
| Analytic projects / linked expenses | 2 / 116 |

During migration, temporary source bindings carry the source database, model,
identifier, snapshot, status and note. A newer snapshot or importer
reconciliation revision updates that bound record. Repeating the same snapshot
and reconciliation revision does not overwrite valid work continued in the
target after cutover. Finalization removes those bindings while leaving native
business records intact. Followers use their native target uniqueness rule.
Relationships are applied after record creation so parents, subtasks, blockers,
message parents, attachments and recipients can be resolved safely.

Existing target companies, partners, and users are matched conservatively by
stable business identity before a record is created. Existing traced project
records are updated from the selected source snapshot; unrelated target
projects are not touched.

Projects is a downstream migration stage. Run it only after the target's
companies, users, partners, analytic accounts and imported business records
are present. The Projects importer reconnects those records; it does not
silently duplicate another perimeter's source data. In particular, this
snapshot expects 116 already-imported expenses to resolve through the two
project analytic accounts. Validation blocks finalization if that prerequisite
is incomplete.

The importer preserves source active flags, workflow state, stage, privacy,
company, customer, manager and assignees, dates, milestone, recurrence,
properties, updates, analytic account, chatter audit dates, followers,
activities, and attachment bytes/checksums. A source activity without a user is
assigned to the first task assignee, then the project manager, then the restore
operator. Each such activity and the run issue log disclose the fallback.
Readonly native task HTML revision history is reconciled after ORM creation so
historical description revisions remain available. Project email-alias local
parts and contact policies are retained while the target environment keeps its
own mail domain. The source `odoo@unstaticlabs.com` ownership identity resolves
to Valentin's existing target user through the shared partner identity; Roger
retains his existing target account.

Technical results are printed by the migration harness and retained as private
external evidence. They are not exposed through a product menu or kept as
permanent Odoo models.

## Deliberate exclusions

- The 17 Enterprise Documents folder shells are empty. They are counted and
  reported but not recreated. Project/task attachments are restored normally.
- The proprietary Enterprise Gantt client is not copied. `planned_date_begin`
  is retained beside the native deadline in task form and list views, so the
  planning interval remains usable.
- Historical notification and outgoing-mail queues are not replayed.
  Recipient relationships remain on chatter messages.
- The source has no project/task sales links, no project-linked Documents
  records, and no external collaborators. Nothing synthetic is created for
  those empty perimeters.
- Enterprise-only AI property-definition keys are reported by migration but
  removed from the native Community property definition, where they are
  invalid. Supported property definitions and values remain available.

## Safe execution

Keep the source dump outside the repository. The default path is
`/Users/valentin/Code/odoo/usl-online-dump`; override it with
`USL_ONLINE_DUMP_DIR`.

The source PostgreSQL connection is read-only. The script also forces both
electronic-invoice live guards to zero. It refuses the preserved source,
canonical development, and accounting proof databases as targets.

```bash
PROJECT_TARGET_DATABASE=odoo_projects_candidate_01 \
  scripts/project-restore all
```

The operations can be separated:

```bash
PROJECT_TARGET_DATABASE=odoo_projects_candidate_01 \
  scripts/project-restore install

PROJECT_TARGET_DATABASE=odoo_projects_candidate_01 \
  scripts/project-restore import

PROJECT_TARGET_DATABASE=odoo_projects_candidate_01 \
  scripts/project-restore validate

PROJECT_TARGET_DATABASE=odoo_projects_candidate_01 \
  scripts/project-restore finalize

PROJECT_TARGET_DATABASE=odoo_projects_candidate_01 \
  scripts/project-restore product-validate
```

`finalize` is terminal for that rehearsal: it requires a passed validation,
uninstalls the temporary migration module, checks that business counts did not
change and validates the database through the normal product add-ons path. Run
repeated-import checks before finalization.

If validation reports missing connected business records, repair or complete
the upstream migration stage and repeat the Projects import. Do not weaken the
parity gate or import a second copy of another app's records from this stage.

Only set `PROJECT_RESTORE_ALLOW_PROTECTED_TARGET=1` as part of an explicitly
approved promotion. `PROJECT_RESTORE_DEFAULT_PASSWORD` is optional and is used
only for a newly created referenced user.

## Acceptance gates

For the canonical production-shaped `odoo_dev` target, prefer the complete
repository workflow:

```bash
make target-reconstruct
```

It runs Accounting reconstruction and parity first, then this Projects
workflow, removes the temporary importer, validates the product boundary and
applies target-only Pocket ID configuration last. Use the Project-specific
commands below only when iterating on this migration stage in isolation.

Before product review:

1. The migration validator exits successfully; retain its counts, exclusions
   and issue list in private migration evidence.
2. Source and target counts match for every material perimeter in the table
   above.
3. A second import has the same counts and hashes, creates no duplicate traced
   records, messages, tracking values, activities, attachments, followers, or
   links, and leaves post-cutover target edits intact.
4. Two clean targets produce equivalent source-keyed project, task,
   relationship, mail, activity, and attachment checksums.
5. Verify active and archived project/task actions, private-project rules,
   company access, stage and state distinctions, planned dates, milestones,
   subtasks, blockers, recurring configuration, chatter, attachment download,
   updates, and analytic links with representative records.
6. Run the focused migration and product module tests:

   ```bash
   docker compose --profile project-migration run --rm \
     -e ODOO_INIT_DB=odoo_project_restore_unit \
     project-migration odoo --config=/etc/odoo/odoo.conf \
     --database=odoo_project_restore_unit \
     --init=usl_project_restore --without-demo=true \
     --test-enable --test-tags=/usl_project_restore --stop-after-init

   docker compose --profile init run --rm \
     -e ODOO_INIT_DB=odoo_project_product_unit \
     init-db odoo --config=/etc/odoo/odoo.conf \
     --database=odoo_project_product_unit \
     --init=usl_project --without-demo=true \
     --test-enable --test-tags=/usl_project --stop-after-init

   make product-migration-boundary
   ```

7. Finalize the database and require `product-validate` to report zero
   migration models, fields and XML IDs with `usl_project_restore`
   uninstalled.

Do not finalize or promote a run with an unresolved error. Warnings must have
a documented reconciliation decision. Informational activity-assignment
fallbacks are expected for this snapshot and do not make a run partial.

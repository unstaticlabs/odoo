# TESE restoration from Odoo Online

This runbook restores Paie TESE and its native HR history after the
source-faithful Accounting stage. It never opens the preserved Odoo Online
database with target Odoo code and never writes to that source database.

## Architecture and ordering

`usl_tese_payroll` is the delivered product application under
`custom-addons/`. `usl_tese_restore` is temporary migration machinery under
`migration/tese_restore/addons/`. Only the `tese-migration` Compose profile
mounts that second path. Both electronic-invoice live guards are forced to
zero and cron workers are disabled.

The selected ORM importer is preferred to two rejected alternatives:

1. direct SQL or CSV would bypass HR, mail, attachment and accounting
   invariants;
2. permanent source fields in the product would make reconstruction
   terminology and private provenance part of every future deployment.

Temporary mapping records make the import repeatable. Finalization uninstalls
the migration module and removes those mappings, run records, issues and XML
IDs while retaining native business records.

Run the stages in this order:

1. restore and validate the source database through the Accounting harness;
2. complete the source-faithful Accounting import;
3. install and run the TESE restoration;
4. validate and repeat the TESE import;
5. finalize and validate through the normal product add-ons path.

TESE is downstream of Accounting because profiles must reuse the eleven
source-mapped accounts and every payroll record must link its already-posted
source-mapped journal entry. A missing or ambiguous prerequisite blocks the
record; the importer does not create an approximate account or duplicate an
entry.

## Exact source perimeter

The provided snapshot contains:

| Perimeter | Expected |
| --- | ---: |
| Employees | 2 |
| Employee versions | 3 |
| TESE profiles | 4 |
| Monthly payroll records | 9 |
| Linked posted payroll entries | 9 |
| Employee-folder PDFs | 14 |
| PDFs used by payroll | 9 |
| HR chatter messages | 30 |
| Tracking values | 57 |
| Followers | 3 |
| Residual-derived Settled / To reconcile | 5 / 4 |

The 14 employee-folder PDFs include five earlier payroll documents and the
nine PDFs linked to the migrated payroll records. The nine accounting-linked
attachments stay native to their posted moves and are referenced by the
payroll records; the earlier documents become native employee attachments.
Employee images are restored from the source `image_1920`, letting Odoo
regenerate its standard image sizes.

The source contains one employee-less `hr.version`. It is preserved as the
native contract-template-shaped employee version that it is, rather than
being assigned speculatively to an employee. The two employees retain their
actual current versions.

Profiles and payroll snapshots preserve provider figures, validity dates,
account mappings, HR references and the eleven accounting components.
Migrated Studio state flags are not authoritative. After linking each posted
move, the product recomputes **Settled** or **To reconcile** from native
residuals.

## Safe commands

Keep the private dump outside Git. The default path is
`/Users/valentin/Code/odoo/usl-online-dump`; set `USL_ONLINE_DUMP_DIR` when it
differs.

For the normal disposable developer/QA database:

```bash
scripts/tese-restore all
```

Or run the stages separately:

```bash
scripts/tese-restore install
scripts/tese-restore import
scripts/tese-restore validate
scripts/tese-restore idempotence
scripts/tese-restore finalize
scripts/tese-restore product-validate
```

The default target is `odoo_dev`. Set `TESE_TARGET_DATABASE` only for an
explicitly named, disposable proof. The harness refuses the preserved source
database and canonical Accounting proof databases unless the protected-target
override is set for an intentional downstream rehearsal.

When several worktrees share Docker, always give the proof its own Compose
project:

```bash
COMPOSE_PROJECT_NAME=usl-tese-proof \
TESE_TARGET_DATABASE=odoo_tese_proof \
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
scripts/tese-restore all
```

Do not point the temporary service at another worktree's target database or
volumes. The harness only stops and restarts the normal Odoo service inside
the selected Compose project.

## Acceptance gates

Validation must prove all of the following:

1. the exact perimeter table matches;
2. all nine payroll entries are unique, linked and still posted;
3. every payroll has one provider PDF and eleven snapshot components;
4. debit and credit remain equal in company currency;
5. five records are paid and four remain open based on current residuals;
6. both employees point to their exact current version, all three HR versions
   exist (including the employee-less template), and every profile/payroll
   points to its exact employee and HR version;
7. all four profiles are found with `active_test=False`, with exactly one
   active and three archived, exact validity, figures and eleven components;
8. all 30 messages, 57 tracking values and three followers map to native HR
   records;
9. all 14 employee-folder PDFs have their source bytes/checksums;
10. a second import changes no counts and creates no duplicate;
11. product and migration add-on tests pass;
12. `make product-migration-boundary` passes;
13. finalization preserves business counts while removing
    `usl_tese_restore`, its three models and all of its XML IDs.

Retain the command summaries as private reconstruction evidence. Do not
commit the dump, filestore, extracted personal data or private logs.

Do not finalize a partial or failed run. Correct the missing upstream identity
or source file and rerun. Never weaken an exact account, move, document or
residual gate merely to make the count pass.

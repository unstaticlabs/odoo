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

The full `migration/manage` reconstruction runs the stages in this order:

1. restore and validate the source database through the Accounting harness;
2. complete and validate the source-faithful Accounting import, leaving its
   temporary source bindings installed;
3. install the delivered Documents models and security, then restore identity,
   Product Master and HR facts;
4. restore Projects and their Accounting/analytic links;
5. install, run, validate and repeat the TESE restoration;
6. rebuild the Paperless archive and link its payroll evidence to the restored
   TESE records;
7. finalize every temporary importer, then validate through the normal
   product add-ons path.

TESE is downstream of Accounting because profiles must reuse the eleven
source-mapped accounts and every payroll record must link its already-posted
source-mapped journal entry. A missing or ambiguous prerequisite blocks the
record; the importer does not create an approximate account or duplicate an
entry.

## Exact source perimeter

The validator derives its expected perimeter from the restored, read-only
source on every run. It does not carry a hardcoded business count from an
older dump. The snapshot verified on 18 August 2026 contains:

| Perimeter | Expected |
| --- | ---: |
| Employees | 3 |
| Employee versions | 4 |
| TESE profiles | 4 |
| Monthly payroll records | 10 |
| Linked payroll entries | 10: all posted |
| Employee-folder PDFs | 15 |
| PDFs used by payroll | 10 |
| HR chatter messages | 33 |
| Tracking values | 57 |
| Followers | 5 |
| Settled / To reconcile / To post | 5 / 5 / 0 |

The 15 employee-folder PDFs include five earlier payroll documents and the
ten PDFs linked to the migrated payroll records. The ten accounting-linked
attachments stay native to their posted moves and are referenced by the
payroll records; the earlier documents become native employee attachments.
Paperless also archives and links the official payroll evidence to its TESE
record, while the native operational attachment remains the posting evidence.
Employee images are restored from the source `image_1920`, letting Odoo
regenerate its standard image sizes.

The source now contains the July 2026 provider PDF and all ten payroll entries
are posted. The importer still derives **Settled** or **To reconcile** from the
native residual rather than trusting a Studio status. Missing evidence on a
posted or otherwise completed payroll remains blocking.

The source contains one employee-less `hr.version`. It is preserved as the
native contract-template-shaped employee version that it is, rather than
being assigned speculatively to an employee. The three employees retain their
actual current versions.

Profiles and payroll snapshots preserve provider figures, validity dates,
account mappings, HR references and the eleven accounting components.
Migrated Studio state flags are not authoritative. After linking each posted
move, the product recomputes **Settled** or **To reconcile** from native
residuals.

## Safe commands

The ignored checkout-local `usl-online-dump/` is the development default. Set
`USL_ONLINE_DUMP_DIR` to the approved absolute external package path for
rehearsal or production use; never commit the private dump.

For migration QA, use the single public lifecycle:

```bash
migration/manage qa refresh \
  --runtime <runtime-id> --fresh --confirm REFRESH:<runtime-id>
```

The following stage commands are for focused migration development only. They
must run after Accounting import and before Accounting finalization; they are
not an alternative way to patch an already finalized product database:

```bash
migration/internal/tese-restore install
migration/internal/tese-restore import
migration/internal/tese-restore validate
migration/internal/tese-restore idempotence
migration/internal/tese-restore finalize
migration/internal/tese-restore product-validate
```

The default target is `odoo_dev`. Set `TESE_TARGET_DATABASE` only for an
explicitly named, disposable proof. The harness refuses the preserved source
database and canonical Accounting proof databases unless the protected-target
override is set for an intentional downstream rehearsal.

Do not point a focused proof at another runtime's database or volumes. Use a
disposable target and exact ownership checks.

## Acceptance gates

Validation must prove all of the following:

1. source-derived counts and field-level mappings match the current snapshot;
2. every payroll entry is unique, linked and preserves its source draft or
   posted state;
3. every posted payroll retains its provider PDF, every source draft retains
   its actual evidence state, and every payroll has eleven snapshot
   components;
4. debit and credit remain equal in company currency;
5. workflow states match source/native facts: settled and open posted records
   come from residuals, while source drafts remain **To post**;
6. all three employees point to their exact current version, all four HR versions
   exist (including the employee-less template), and every profile/payroll
   points to its exact employee and HR version;
7. every profile is found with `active_test=False` and retains its source
   active state, validity, figures and eleven components;
8. all source messages, tracking values and followers map to native HR
   records;
9. every source employee/payroll PDF has its source bytes and checksum;
10. a second import changes no counts and creates no duplicate;
11. product and migration add-on tests pass;
12. `make product-migration-boundary` passes;
13. finalization preserves business counts while removing
    `usl_tese_restore`, its three models and all of its XML IDs.

Retain the command summaries as private reconstruction evidence. Do not
commit the dump, filestore, extracted personal data or private logs.

Do not finalize a partial or failed run. Correct an unexplained missing
identity, move or source file and rerun. A genuinely unfinished source payroll
may remain draft without a PDF only when both its source move and document
state prove that condition. Never weaken an exact account, move, document or
residual gate merely to make a count pass.

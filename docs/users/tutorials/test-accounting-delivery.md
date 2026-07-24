# Run and Test the Accounting Delivery

Audience: Valentin.

Goal: open the accounting product delivered so far, check its most important
features and run the safe automated validation without rebuilding source data.

Time:

- 2 minutes to start and sign in;
- 20–30 minutes for the browser checklist;
- about 2 minutes for the normal automated checks;
- longer only if you deliberately run the full add-on suite or clean rebuild.

## The short version

From a normal macOS Terminal:

```bash
cd /Users/valentin/Code/odoo
docker compose ps
scripts/odoo-dev start
```

Then open:

```text
http://localhost:8069/web/login?db=odoo_rebuild_accounting_test
```

For the current local development database, sign in with:

```text
Login: admin
Password: admin
```

Open the Accounting app and follow the browser checklist below.

You do **not** need to restore the source, extract data, reset the target or
rerun the full import merely to inspect what is already delivered.

## 1. Know which database you are opening

Use this database first:

```text
odoo_rebuild_accounting_test
```

It is the exact imported audit target. Use it to verify historical truth,
reports, FEC, closing controls, permissions and review decisions.

Do not use these by accident:

| Database | Purpose |
| --- | --- |
| `odoo19` | Synthetic development/demo data, not parity evidence. |
| `odoo_online_source_saas_19_2` | Restored source evidence; never update or test product behavior here. |
| `odoo_rebuild_accounting_track_b` | Technical native-workflow proof. |
| `odoo_rebuild_accounting_replacement` | Hybrid production candidate; professionally unaccepted and not promoted. |

The database is selected by the `db=` part of the browser URL. If numbers or
menus look wrong, check that first.

## 2. Check that Odoo is running

Open a normal macOS Terminal, not a terminal inside the Dev Container:

```bash
cd /Users/valentin/Code/odoo
docker compose ps
```

Expected:

- `db` is `Up` and healthy;
- `odoo` is `Up` and healthy;
- port `8069` is published for `odoo`.

If `db` or `odoo` is stopped:

```bash
scripts/odoo-dev start
docker compose ps
```

If the browser still does not respond:

```bash
docker compose logs --tail=100 odoo
```

Look at the final lines. A healthy server normally shows successful
`/web/health` requests.

Do not start a second Odoo server in the Dev Container while the Compose
`odoo` service owns port `8069`.

## 3. Sign in and confirm the company

Open:

```text
http://localhost:8069/web/login?db=odoo_rebuild_accounting_test
```

Sign in as `admin / admin` for this local review. If Odoo first shows
**Choose a user**, select **Administrator**. If it reports **Session expired
(invalid CSRF token)**, reload the complete login URL once and sign in again.

Then:

1. open **Accounting**;
2. confirm the active company is **Unstatic Labs**;
3. confirm the page opens on **Accounting Home**.

If the active company is `USL MEDIA`, switch to Unstatic Labs before checking
the expected values below.

## 4. Browser acceptance checklist

### A. Accounting Home

Open:

```text
Accounting
```

Check that you can see:

- Cash and Bank;
- Daily Accounting Work;
- Open Balances;
- Closing and Declarations;
- Prepared Actions and Evidence.

Expected current state:

- the page opens without a server error;
- the latest closing is blocked rather than falsely shown as complete;
- open work and decisions are visible.

`Blocked` is currently expected. It means professional decisions and normal
accounting work remain; it does not mean the technical reconstruction failed.

### B. Accounting Hygiene

Open:

```text
Accounting > Review > Control > Accounting Hygiene
```

Open Unstatic Labs and inspect:

- Bank to Match;
- Stale Draft Documents;
- Vendor Documents Missing Evidence;
- Open P0 and P1;
- Prepared for Valentin;
- Prepared for Prosper.

Current evidence may show approximately:

- `355` attention items;
- `207` unmatched bank transactions;
- `37` stale draft vendor documents;
- `45` accountant actions;
- `2` Valentin actions.

These counts are a checkpoint aid. If someone records decisions or completes
accounting work, the live counts should change.

### C. Revenue versus spending

Open:

```text
Accounting > Reporting > Revenue versus Spending Trend
```

Use the October 2025–June 2026 preset.

Check all three views:

1. graph;
2. pivot;
3. list.

Expected:

- `9` months;
- `3` metrics per month;
- `27` rows in total;
- revenue: EUR `176,928.45`;
- spending: EUR `101,215.69`;
- net contribution: EUR `75,712.76`.

Open one row. The journal-item drill-down should show the posted entries behind
that month and metric.

### D. Trial Balance

Open the report launcher:

```text
Accounting > Reporting > Interactive Reports > Trial Balance
```

Set:

```text
Company: Unstatic Labs
Start: 2024-01-10
End: 2025-09-30
Entries: Posted only
Data scope: All Native Accounting
```

Check:

- the preview loads;
- there are `68` account rows;
- debit and credit both total EUR `1,064,045.02`;
- opening, movement and closing columns are visible;
- clicking a material row opens its journal items;
- XLSX and PDF downloads work.

### E. General Ledger and historical identity

Open:

```text
Accounting > Reporting > Interactive Reports > General Ledger
```

Use the same benchmark period.

Open at least one journal entry and confirm:

- entry reference is present;
- date, journal and account are understandable;
- source-trace information is visible where applicable;
- the posted item cannot be silently edited before the lock date.

The historical benchmark contains:

- `2,046` posted moves;
- `4,809` journal items;
- balanced debit and credit of EUR `1,064,045.02`.

### F. Reconciliation workbenches

Open:

```text
Accounting > Transactions > Bank Matching
Accounting > Closing > General Reconciliation
Accounting > Closing > Matched Items and Undo
```

Check that:

- Bank Matching shows bank transactions and candidates;
- General Reconciliation shows account/partner residual groups;
- Matched Items shows reconciled journal items;
- the source boundary review remains visible rather than silently applying
  draft/future endpoint reconciliations.

Do not bulk-reconcile the imported audit target during acceptance testing.

### G. FEC

Open:

```text
Accounting > Reporting > Taxes & Fiscal > FEC
```

Use:

```text
Company: Unstatic Labs
End date: 2025-09-30
Test mode: enabled
All journals included
```

Expected:

- filename `983982950FEC20250930.txt`;
- `4,781` data rows;
- debit and credit both EUR `1,064,045.02`;
- download completes without an access error.

This proves technical generation and validation. It is not Prosper's
professional FEC acceptance.

### H. Closing package and accepted snapshots

Open:

```text
Accounting > Closing
```

Open the relevant annual or monthly closing workspace.

Check:

- controls are grouped as passed, warning, blocking or not applicable;
- the closing XLSX/PDF package can be generated by the manager;
- a close with blockers cannot advance lock dates;
- an accepted decision requires a package.

Then open:

```text
Accounting > Closing > Accepted Closing Snapshots
```

Expected current exact-target count:

```text
0
```

That zero is correct: no named professional has accepted a real closing
package. Do not create a fake acceptance merely to make the count non-zero.

When a real acceptance is eventually recorded, the package bytes, checksum,
decision, evidence, reviewer and review time become immutable.

### I. Prepared decisions and blockers

Open:

```text
Accounting > Review > Advanced Audit > Review Decisions
Accounting > Review > Advanced Audit > Discrepancies
```

Expected:

- `45` draft decisions;
- `1` open P0;
- `1` open P1;
- `1` accountant-owned P2;
- `0` recorded professional decisions until Valentin or Prosper acts.

Open a few decisions and read their evidence and required authority.

Do not accept decisions on Prosper's behalf. `Requires Change` or
`Accepted With Difference` is valid when it accurately records the review.

## 5. Run the normal automated checks

Run these from a normal macOS Terminal:

```bash
cd /Users/valentin/Code/odoo
make accounting-target-validate
make accounting-reports
make accounting-readiness
make accounting-evidence
```

Expected:

| Command | Expected result |
| --- | --- |
| `make accounting-target-validate` | `status: passed`, classification `POSTED_LEDGER_SLICE_PARITY`. |
| `make accounting-reports` | `status: passed`, 56 capability rows and zero technical gaps. |
| `make accounting-readiness` | Command succeeds but report status remains `blocked`. |
| `make accounting-evidence` | Evidence index is regenerated successfully. |

The readiness result must currently say:

```text
TECHNICAL_REHEARSAL_PASSED_PROFESSIONAL_ACCEPTANCE_PENDING
```

It should also show:

- no technical failures;
- one P0, one P1 and one P2;
- 45 draft professional decisions.

A `blocked` readiness status is therefore the correct expected result. Do not
change the test to expect `passed` before the named decisions are recorded.

## 6. Run the add-on test suite

This is optional for normal inspection, but useful after code changes:

```bash
cd /Users/valentin/Code/odoo
make accounting-addon-tests
```

Expected:

- a new timestamped disposable test database is created;
- the `rebuild_account_migration` test suite passes;
- the command exits with status `0`.

The current suite has `85` tests.

Odoo may print two reStructuredText parser warnings while loading existing help
text. They are non-fatal if the command still exits successfully.

Do not use the imported audit database as the add-on test database.

## 7. Test as Prosper

For the accountant acceptance test, Prosper should use his own scoped user,
not `admin`.

Follow:

- [Prosper Accounting Acceptance Walkthrough](prosper-accounting-acceptance.md)
- [Use Accountant Access Safely](../how-to/use-accountant-access.md)

Expected:

- Prosper can read Unstatic Labs accounting and evidence;
- Prosper can preview/export reports and generate the permitted FEC test file;
- Prosper can record review decisions;
- Prosper cannot edit posted accounting, change settings, apply lock dates or
  inspect USL Media/private technical material.

If Prosper does not yet have a working local login, stop there and create/test
the named user deliberately. Do not infer acceptance from the automated test
account.

## 8. What not to do

For normal review, do not run:

```bash
make accounting-source-restore
make accounting-extract
make accounting-target-reset
make accounting-target-import
scripts/odoo-dev reset
```

Why:

- source restore and extraction are unnecessary when the snapshot is
  unchanged;
- target reset/import destroys and rebuilds the disposable exact target;
- `scripts/odoo-dev reset` removes Compose volumes and local databases.

Use the full rebuild only when you deliberately want to test clean
reproducibility and have time to recreate the environment.

The complete rebuild procedure is documented in:

- `docs/operations/run-imported-accounting-dev.md`;
- `docs/operations/accounting-development-workflow.md`.

## 9. How to report a problem

When something fails, send:

1. the database name from the browser URL;
2. the page/menu you opened;
3. the exact dates and filters;
4. what you expected;
5. what happened instead;
6. a screenshot;
7. the final Odoo log lines:

```bash
cd /Users/valentin/Code/odoo
docker compose logs --tail=150 odoo
```

For an automated command, also include:

- the full command;
- its exit code;
- the first error;
- the final 50 lines of output.

Do not summarize an accounting difference as simply “wrong.” Name the report,
line, expected amount, actual amount, period, company and evidence source.

## 10. Your acceptance result

After the browser checklist, record one short result:

```text
Database reviewed:
Date:
Reviewer:

Accounting Home: pass / issue
Revenue versus spending: pass / issue
Trial Balance and drill-down: pass / issue
General Ledger and locks: pass / issue
Reconciliation workbenches: pass / issue
FEC test export: pass / issue
Closing package: pass / issue
Prepared decisions understood: pass / issue

Blocking issues:
Non-blocking observations:
Decision requested:
```

This review confirms your product experience. It does not replace Prosper's
accounting and statutory acceptance.

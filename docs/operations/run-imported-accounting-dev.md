# Run Imported Accounting Data in Development

Audience: developer or operator who wants to open the production-derived accounting reconstruction in Odoo.

This guide uses two shells:

- Host shell: your normal macOS terminal in `/Users/valentin/Code/odoo`.
- Dev Container shell: the VS Code/Cursor terminal inside `/workspace/odoo`.

The accounting import harness currently requires Docker Compose. The Dev Container does not include the Docker CLI, so run `make accounting-*` from the host shell.

## What This Does

The import pipeline restores the Odoo Online backup and reconstructs a disposable Odoo target database named:

```text
odoo_rebuild_accounting_test
```

You then start Odoo against that database and inspect it in the browser.

Do not confuse it with:

```text
odoo19
```

`odoo19` is the normal local demo/dev database and may contain synthetic bootstrap data.

## Step 1 - Open a Host Shell

Open a normal terminal on your Mac, not the Dev Container terminal.

Run:

```bash
cd /Users/valentin/Code/odoo
```

Check that Docker is available:

```bash
docker compose ps
```

Expected: this command prints Compose services such as `db` or `odoo`. If it says `docker: command not found`, Docker Desktop is not available to this shell.

## Step 2 - Fetch the OCA Accounting Add-ons

Still in the host shell:

```bash
make oca-addons-sync
```

Why: Milestone 13 uses pinned OCA 19.0 add-ons for Community financial reports, MIS reports, bank statement support and reconciliation. The command creates local ignored checkouts under `oca-src/` and exposes selected modules through `oca-addons/`.

If the Dev Container was already open before this step, recreate it before starting Odoo so its environment includes `oca-addons/`.

If you use the normal Compose `odoo` service, make sure your local `.env` has the OCA path as well:

```text
ODOO_ADDONS_PATH=/opt/odoo/addons,/opt/odoo/odoo/addons,/mnt/custom-addons,/mnt/oca-addons
```

Why: `oca-addons/` contains symlinks into `oca-src/`. Compose mounts both directories into the Odoo and init containers. If `ODOO_ADDONS_PATH` omits `/mnt/oca-addons`, installed OCA menus can appear in the database while their Python code or browser assets are unavailable at runtime.

Also keep scheduled jobs disabled during imported-accounting development:

```text
ODOO_MAX_CRON_THREADS=0
```

Why: imported or freshly initialized databases may contain scheduled jobs for mail, SMS, VIES, PEPPOL, PDP or other external services. The import pipeline neutralizes target cron records after import, but the safest development default is to prevent the Odoo server from running scheduler threads at all. Only change this value when you are intentionally testing scheduled-job behavior.

## Step 3 - Stop the Normal Odoo Web Service

Still in the host shell:

```bash
docker compose stop odoo
```

Why: the normal Compose `odoo` service uses port `8069`. If you want to run Odoo from the Dev Container on the same port, the normal service must be stopped.

Keep PostgreSQL running.

## Step 4 - Build the Imported Accounting Database

Still in the host shell:

```bash
make accounting-source-restore
make accounting-extract
make accounting-target-reset
make accounting-target-import
make accounting-target-validate
make accounting-reports
```

Expected:

- no `FileNotFoundError: docker`;
- no failed `make` target;
- target database `odoo_rebuild_accounting_test` is recreated;
- report evidence is generated.

These commands are ordered. Run them in this order because each command produces state that the next command needs.

| Command | What it does | Depends on | Produces |
| --- | --- | --- | --- |
| `make accounting-source-restore` | Starts the isolated `accounting-source-db` PostgreSQL service and restores `usl-online-dump/dump.sql` into `odoo_online_source_saas_19_2`. It also creates the read-only source role used by extraction. | Docker Compose, `usl-online-dump/dump.sql`, `usl-online-dump/filestore/`. | A running source database containing the Odoo Online backup. |
| `make accounting-extract` | Reads accounting records from the restored source database and writes the private canonical snapshot/extract files. It does not read business data from the SQL file directly. | `accounting-source-db` must still be running and restored. | Snapshot files under `accounting_compat/private/` and `artifacts/accounting-compat/private/`. |
| `make accounting-target-reset` | Recreates the disposable target Odoo database `odoo_rebuild_accounting_test` from scratch and initializes the needed Community, OCA and USL target modules. | The normal `db` PostgreSQL service must be running, and `make oca-addons-sync` must have populated `oca-addons/`. | A clean target Odoo database ready for import. |
| `make accounting-target-import` | Imports the extracted accounting snapshot into the clean target database through the target Odoo ORM. | Source database still running, extracted snapshot present, clean target database present. | Imported companies, accounts, journals, posted entries, report evidence, assets, review records and source traces. |
| `make accounting-target-validate` | Runs target controls: balanced moves, duplicate source traces, counts, locks, relationships and imported evidence checks. | Successful target import. | Validation status artifacts and discrepancy updates. |
| `make accounting-reports` | Exercises Odoo-facing report views, previews, exports and drill-down evidence from the imported target. | Successful target validation and imported report data. | Report export/check artifacts and Odoo report evidence. |

Keep these services running until the sequence finishes:

```text
db
accounting-source-db
```

If you see:

```text
service "accounting-source-db" is not running
```

start it again from the host shell:

```bash
docker compose --profile accounting-compat up -d accounting-source-db
```

Then rerun from the earliest step that needs the source database. When in doubt, rerun the full Step 3 sequence.

If you want the full rehearsal, run this instead:

```bash
make accounting-compat
```

## Step 5 - Open the Dev Container

In VS Code or Cursor:

```text
Dev Containers: Reopen in Container
```

Open a terminal inside the Dev Container and check:

```bash
cd /workspace/odoo
pwd
command -v odoo
command -v docker || true
```

Expected:

- `pwd` prints `/workspace/odoo`;
- `command -v odoo` prints an Odoo executable path;
- `command -v docker` may print nothing. That is normal.

## Step 6 - Update the Accounting Addon in the Imported Database

Inside the Dev Container:

```bash
odoo --config=/etc/odoo/odoo.conf \
  --addons-path=/workspace/odoo/addons,/workspace/odoo/odoo/addons,/workspace/odoo/custom-addons,/workspace/odoo/oca-addons \
  --database=odoo_rebuild_accounting_test \
  --update=rebuild_account_migration \
  --stop-after-init
```

Expected: Odoo exits by itself without an error.

## Step 7 - Start the Dev Odoo Server

Inside the Dev Container:

```bash
odoo --config=/etc/odoo/odoo.conf \
  --addons-path=/workspace/odoo/addons,/workspace/odoo/odoo/addons,/workspace/odoo/custom-addons,/workspace/odoo/oca-addons \
  --database=odoo_rebuild_accounting_test \
  --max-cron-threads=0 \
  --dev=reload,xml,qweb
```

Keep this terminal open. It is the running Odoo server.

## Step 8 - Open Odoo in the Browser

Open:

```text
http://localhost:8069/web/login?db=odoo_rebuild_accounting_test
```

Login:

```text
admin / admin
```

## Step 9 - Open the Imported Accounting Features

In Odoo, open:

```text
Accounting > Review > Control > Issues
```

Then try:

```text
Accounting > Transactions > Bank Matching
Accounting > Closing > General Reconciliation
Accounting > Reporting > Interactive Reports > Trial Balance
Accounting > Reporting > Interactive Reports > General Ledger
Accounting > Reporting > Statement Reports > Balance Sheet
Accounting > Reporting > Statement Reports > Profit and Loss
Accounting > Reporting > Taxes & Fiscal > FEC
Accounting > Review > Advanced Audit > User Guide
Accounting > Review > Advanced Audit > Imported Report Export
```

The direct user-guide URL after login is:

```text
http://localhost:8069/usl/user-docs
```

## If Port 8069 Is Already Used

Either stop the normal Compose Odoo service from the host shell:

```bash
docker compose stop odoo
```

or start the Dev Container Odoo server on another port:

```bash
odoo --config=/etc/odoo/odoo.conf \
  --database=odoo_rebuild_accounting_test \
  --http-port=8070 \
  --dev=reload,xml,qweb
```

Then open:

```text
http://localhost:8070/web/login?db=odoo_rebuild_accounting_test
```

## If the User Guide Shows 404 After Login

The user probably lacks accounting read-only access.

Inside the Dev Container:

```bash
odoo shell --config=/etc/odoo/odoo.conf --database=odoo_rebuild_accounting_test <<'PY'
user = env.ref("base.user_admin")
group = env.ref("account.group_account_readonly")
user.write({"group_ids": [(4, group.id)]})
env.cr.commit()
print(user.login, user.has_group("account.group_account_readonly"))
PY
```

Restart the Dev Container Odoo server after this.

## Common Mistake

Do not run this inside the Dev Container:

```bash
make accounting-source-restore
```

It fails because the harness calls `docker compose`, and the Dev Container currently has no Docker CLI.

Run `make accounting-*` from the host shell instead.

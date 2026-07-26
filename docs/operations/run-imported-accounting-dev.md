# Run Imported Accounting Data in Development

Audience: developer or operator who wants to open the production-derived accounting reconstruction in Odoo.

This guide uses two shells:

- Host shell: your normal macOS terminal in `/Users/valentin/Code/odoo`.
- Dev Container shell: the VS Code/Cursor terminal inside `/workspace/odoo`.

The accounting import harness currently requires Docker Compose. The Dev Container does not include the Docker CLI, so run `make accounting-*` from the host shell.

## What This Does

The complete pipeline restores the Odoo Online backup and builds the canonical
disposable developer/QA database:

```text
odoo_dev
```

Two other target databases exist only to validate the two reconstruction
strategies independently:

```text
odoo_saas_19_2_validation_exact
odoo_saas_19_2_validation_native
```

There is no separate demo database. All three targets are disposable; only
`odoo_dev` is the disposable user-facing development and QA database.

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

Why: Milestone 13 uses pinned OCA 19.0 add-ons for selected Community financial-report foundations, bank statement support, reconciliation and asset schedules. The canonical end-user reports are provided by the unified interactive Accounting experience; the superseded MIS engine is no longer exposed or synchronized. The command creates local ignored checkouts under `oca-src/` and exposes only the selected modules through `oca-addons/`.

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

## Step 4 - Build the Development/QA Database

Still in the host shell:

```bash
make accounting-compat
```

Expected:

- no `FileNotFoundError: docker`;
- no failed `make` target;
- both validation databases are recreated;
- `odoo_dev` is recreated from the validated native state and exact history;
- report evidence is generated.

The full target runs the ordered exact-validation, native-validation and
development integration stages. Use the individual commands below only when
iterating on one pipeline layer.

| Command | What it does | Depends on | Produces |
| --- | --- | --- | --- |
| `make accounting-source-restore` | Starts the isolated `accounting-source-db` PostgreSQL service and restores `usl-online-dump/dump.sql` into `odoo_online_source_saas_19_2`. It also creates the read-only source role used by extraction. | Docker Compose, `usl-online-dump/dump.sql`, `usl-online-dump/filestore/`. | A running source database containing the Odoo Online backup. |
| `make accounting-dev-attachments` | Replays verified Accounting files through the ORM into the existing `odoo_dev` candidate and links source chatter evidence without rebuilding the ledger. | Restored read-only source database, mounted source filestore, current reconstructed records. | Idempotent source-traced attachments, native main previews and internal chatter links. |
| `make accounting-attachment-audit` | Verifies every source-referenced blob, classifies unreferenced files, compares the complete Accounting scope with `odoo_dev`, and reads every target binary through Odoo storage. | Restored source database and a reconstructed `odoo_dev`. | Private attachment reconstruction evidence and a blocking pass/partial result. |
| `make accounting-extract` | Reads accounting records from the restored source database and writes the private canonical snapshot/extract files. It does not read business data from the SQL file directly. | `accounting-source-db` must still be running and restored. | Snapshot files under `accounting_compat/private/` and `artifacts/accounting-compat/private/`. |
| `make accounting-validation-exact-reset` | Recreates the disposable target Odoo database `odoo_saas_19_2_validation_exact` from scratch and initializes the needed Community, OCA and USL target modules. | The normal `db` PostgreSQL service must be running, and `make oca-addons-sync` must have populated `oca-addons/`. | A clean target Odoo database ready for import. |
| `make accounting-validation-exact-import` | Imports the extracted accounting snapshot into the clean target database through the target Odoo ORM. | Source database still running, extracted snapshot present, clean target database present. | Imported companies, accounts, journals, posted entries, report evidence, assets, review records and source traces. |
| `make accounting-validation-exact-validate` | Runs target controls: balanced moves, duplicate source traces, counts, locks, relationships and imported evidence checks. | Successful target import. | Validation status artifacts and discrepancy updates. |
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

Stop a manually started Odoo server for this database before updating it. The
upgrade command is a separate process and cannot reload Python already held by
the running server.

```bash
odoo --config=/etc/odoo/odoo.conf \
  --addons-path=/workspace/odoo/addons,/workspace/odoo/odoo/addons,/workspace/odoo/custom-addons,/workspace/odoo/oca-addons \
  --database=odoo_dev \
  --update=rebuild_account_migration \
  --stop-after-init
```

Expected: Odoo exits by itself without an error.

The update applies only to the named database. Use
`odoo_dev` for normal development and QA. Update
`odoo_saas_19_2_validation_exact` or `odoo_saas_19_2_validation_native` only
while investigating that pipeline stage. Never
run an update against `odoo_online_source_saas_19_2`.

## Step 7 - Start the Dev Odoo Server

Inside the Dev Container:

```bash
odoo --config=/etc/odoo/odoo.conf \
  --addons-path=/workspace/odoo/addons,/workspace/odoo/odoo/addons,/workspace/odoo/custom-addons,/workspace/odoo/oca-addons \
  --database=odoo_dev \
  --max-cron-threads=0 \
  --dev=reload,xml,qweb
```

Keep this terminal open. It is the running Odoo server.

## Step 8 - Open Odoo in the Browser

Open:

```text
http://localhost:8069/web/login?db=odoo_dev
```

Login:

```text
valentin / admin
prosper / admin
```

`valentin` is the Accounting Manager and Expense Manager across the imported
companies. `prosper` has the company-scoped USL Accountant Review role: normal
accounting remains read-only, while marking a bank transaction for review is
allowed. Override the local development passwords before rebuilding with
`USL_DEV_ACCOUNTING_MANAGER_PASSWORD` and `USL_DEV_ACCOUNTANT_PASSWORD`.

### Refresh the UI after a change

Use the smallest refresh that matches the change:

| Change | Refresh |
| --- | --- |
| Python | Stop the server, run the module update when model/data state changed, restart, then reload |
| XML view, menu, action or ACL | Stop the server, update the module, restart, then reload or hard refresh |
| JavaScript, QWeb or manifest asset entry | Update the module, restart, enable `debug=assets` in the URL, then hard refresh |
| `docs/users/` Markdown | Reload `/usl/user-docs`; no module update is needed in the mounted development checkout |

The development flags `--dev=reload,xml,qweb` do not write backend XML
records, ACLs or manifest changes into the database. A browser hard refresh
also does not replace a required module update.

If a change is still absent, first confirm the `db=` query parameter and the
server using port `8069`. Do not rerun source restore, extraction or a target
reset just to refresh the UI. The fuller decision matrix is in
[Accounting development workflow](accounting-development-workflow.md#module-and-browser-refresh-contract).

## Step 9 - Open the Imported Accounting Features

In Odoo, open:

```text
Accounting > Review > Control > Accounting Hygiene
```

Then try:

```text
Accounting > Transactions > Bank Matching
Accounting > Closing > General Reconciliation
Accounting > Reporting > Interactive Reports > Balance générale
Accounting > Reporting > Interactive Reports > Grand livre
Accounting > Reporting > Statement Reports > Bilan
Accounting > Reporting > Statement Reports > Compte de résultat
Accounting > Reporting > Taxes & Fiscal > FEC
Accounting > Review > Advanced Audit > Accounting Reconstruction Review
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
  --database=odoo_dev \
  --http-port=8070 \
  --dev=reload,xml,qweb
```

Then open:

```text
http://localhost:8069/web/login?db=odoo_dev
```

## If the User Guide Shows 404 After Login

The user probably lacks accounting read-only access.

Inside the Dev Container:

```bash
odoo shell --config=/etc/odoo/odoo.conf --database=odoo_dev <<'PY'
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

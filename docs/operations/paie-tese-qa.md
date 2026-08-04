# Paie TESE feature QA data

Use this fixture when the production-shaped `odoo_dev` history no longer has
an open month or suitable bank transactions for an end-to-end demonstration.

The fixture is an explicit operations script, not Odoo product demo data. It
reuses the current company, French payroll accounts, TESE journal, collector
and bank journal from `odoo_dev`. Every employee, profile, payroll, PDF and
bank transaction it creates is synthetic and labeled `[QA TESE NN]` or
`QA-TESE-NN`. It is not migration or accounting-parity evidence.

This approach was chosen over two alternatives:

- product `demo/` XML would not load into the normal `--without-demo` target
  and could blur the product/migration boundary;
- a second permanent QA database would drift from the single `odoo_dev`
  development target and add shared-Docker risk.

## Seed a generation

Keep both regulatory live guards disabled. In the canonical worktree:

```bash
make tese-qa-bootstrap
```

In an isolated worktree, keep its Compose project and ports explicit:

```bash
ODOO_SAAS_COMPOSE_PROJECT=usl-tese-f73b \
ODOO_HTTP_PORT=19669 \
ODOO_GEVENT_PORT=19672 \
make tese-qa-bootstrap
```

The bootstrap is idempotent while its scenarios remain untouched. After a QA
journey changes posted accounting, create the next generation instead of
rewriting history:

```bash
make tese-qa-bootstrap TESE_QA_GENERATION=02
```

## Included journeys

| Label | Starting point | What to demonstrate |
| --- | --- | --- |
| `01 Monthly creation` | Active profile, no payroll | Click **New**, select this employee, confirm the proposed completed month, review the TESE/HR hours warning, and revise settings. |
| `02 Missing PDF` | Draft entry ready to post | Confirm that posting is blocked until the official provider PDF is attached and that Diagnostics links to the record. |
| `03 Exact matching` | Posted payroll with one salary and one URSSAF candidate | Refresh, inspect both candidates, then match salary and URSSAF to reach **Settled**. |
| `04 URSSAF rounding` | Posted payroll with exact salary and a `€0.55` URSSAF difference | Match both payments, confirm **Settled**, and verify that the neutral `431000` carry-over asks for no payroll action. |
| `05 Ambiguous bank` | Posted payroll with two salary and two URSSAF candidates | Confirm that automatic matching is unavailable and Bank Matching is the next step. |
| `06 Settled overview` | Fully reconciled payroll | Review the final payment badge, zero payment residuals and accounting links. |

In **Payroll Records**, open Favorites and select `QA TESE NN · Payroll
scenarios`. In **Payroll Profiles**, select `QA TESE NN · Profiles`.

## Safety and refresh

- The script refuses the read-only source database and any target other than
  `odoo_dev` or an explicitly named `*_qa` database.
- It refuses to run if electronic-invoicing or e-reporting live guards are on.
- It never deletes or resets posted entries. Use a new generation after a
  demo, or reconstruct the disposable `odoo_dev` target when a completely
  clean environment is required.

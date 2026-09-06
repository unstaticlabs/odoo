# Accounting development workflow

Use focused product fixtures and module tests for ordinary Accounting work.
Rehearse stored-data transformations on an isolated clone of the current
production database.

## Safety rules

- Keep the Online source database and filestore read-only.
- Never open the source database with target Odoo code.
- Do not run tests, demo loaders, bootstrap helpers, destructive retries or
  speculative SQL against a production or frozen runtime.
- Preserve posted ledger meaning, reconciliation, currencies, taxes,
  analytics, lock dates, attachments, and audit chronology.
- Keep e-invoice reception, e-reporting, mail, bank polling and external
  providers disabled in development runtimes.
- Reproduce defects on a disposable database or clone before changing stored
  data.

## Choose the smallest honest validation

For Python, XML, security, report, or UI changes that do not alter importer
semantics:

1. run the affected module tests;
2. update only the affected modules in the disposable product database;
3. run the relevant Accounting, access, and multi-company control;
4. use browser QA only for changed user journeys.

For stored-model or data-upgrade changes:

1. test the transformation on an isolated disposable clone;
2. run the affected transformation and integrity tests;
3. verify idempotence and failure behavior;
4. run `make product-migration-boundary`;
5. verify the upgrade against the current database shape and an identical
   repeated module upgrade.

## Accounting acceptance

The relevant perimeter includes:

- debit and credit balance and journal-entry balance;
- journals, moves, lines, accounts, taxes, currencies, historical invoice
  rates, payments, partial/full reconciliation, and lock dates;
- analytic accounts, plans, distributions, and analytic lines;
- assets, deferrals, expenses, bank statements, FEC, and French reports;
- company controls and active multi-company isolation;
- attachment and archive-link integrity;
- identical repeated module upgrade;
- clean delivered product registry.

Do not weaken a gate or adjust ledger data merely to make a report match.
Document every deliberate source exception and its financial effect.

## Persistent changes

Before applying a module or schema upgrade to the local production dataset:

1. pause writers and queue submissions;
2. take a coordinated Odoo/Paperless/Ollama checkpoint;
3. record the exact code and module identities;
4. apply only the required module upgrades;
5. restart and verify the affected Accounting, Documents, access, and queue
   controls;
6. record the observed data effect and recovery point in private evidence.

Non-trivial repairs must be versioned, idempotent, and rehearsed on a clone.
Protected CI/GitOps remains the default deployment path. An explicitly
authorized operator may deploy manually or bypass CI after verifying a current
restorable backup and the exact up-to-date GitOps desired state. The same
Accounting preservation and post-change evidence requirements still apply.

## Focused commands

```bash
make accounting-addon-tests
make accounting-multicompany-acceptance
make product-migration-boundary
```

Use module-specific test tags and the ordinary `scripts/odoo-dev` workflow for
narrow validation. Inspect production read-only unless an approved upgrade or
repair is being applied.

# How To Review Reconciliation Boundary Cases

Audience: accountant, Accounting Manager, finance operator preparing evidence.

Use this guide when imported reconciliations touch records outside the posted replay boundary.

## Open Boundary Reviews

Go to:

```text
Accounting > Review > Advanced Audit > Source Reconciliation Boundary Review
```

These rows represent source reconciliations that include at least one imported posted endpoint and at least one endpoint outside the selected posted replay scope.

## Read the Boundary

Open a row and check:

- reconciliation kind;
- review status;
- source partial or full reconcile id;
- imported line count;
- missing line count;
- amount;
- max date;
- imported source line ids;
- missing source move ids;
- missing source move states;
- generated missing endpoint coverage.

## Inspect Endpoints

Use:

- `Imported Journal Items` to open imported posted endpoints.
- `Generated Draft Endpoint Items` to open generated draft endpoints.
- `Preview Native Partial` to view the exact pair that would be used for native partial reconciliation.

## Record a Decision

If accountant/product review supports native application:

1. Click `Record Decision`.
2. Review the prefilled evidence.
3. Choose an accepted conclusion only if authorized.
4. Record the decision.

## Apply Native Partial Reconciliation

Only an Accounting Manager can apply native reconciliation.

Before applying:

- all missing endpoints must have generated draft coverage;
- an accepted review decision must be linked to the boundary row;
- the action must be appropriate for accounting presentation.

Click `Apply Native Partial` only after those conditions are met.

This action changes target reconciliation presentation. Do not use it as a routine cleanup tool.

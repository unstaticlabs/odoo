# Expense Batch transition

The Canada draft transition runs after native expense reconstruction inside
the isolated Accounting migration add-on. It is not installed in the final
product registry.

## Preconditions

- `625600 Missions` exists for the company;
- native analytic accounts `Projet: SBFH prod` and `Epic: Canada 2026` are
  unambiguous;
- active reusable Products resolve unambiguously for `TRANS`, the foreign
  `FOOD` variant and `GIFT_NOVAT`;
- live e-invoicing and e-reporting flags remain disabled.

## Deterministic result

Eligible draft `CA26` expenses are linked to `SBFH — Canada 2026`. Uber/taxi,
meal/snack and gift descriptions are mapped to the corresponding reusable
category. An unrecognized description stays on `CA26` and is reported as
ambiguous. The Batch applies the Missions account and combined SBFH/Epic
analytic distribution. Missing evidence remains missing and nothing is
submitted.

Non-draft signatures—Product, account, analytics, move and state—are captured
before and checked after the operation. Finally `AUS26`, `CA26`, `LPASUM26`
and `BCN2602` are archived. A second invocation must report no candidates,
reclassification, Batch creation or archival.

Run the focused migration tests, then the isolated
`accounting-dev-reset`, `accounting-dev-import` and
`accounting-dev-validate` sequence. Retain the emitted transition JSON with
the reconstruction evidence; do not store migration provenance on operational
models.

# Milestone 13 declaration and closing workflow

Status: implemented technical workflow; external filing, professional report acceptance and final milestone closure remain reviewer-controlled.

## Architecture decision

The selected design is a thin USL workflow layer over standard Odoo accounting controls and the pinned OCA reporting/reconciliation stack.

Alternatives considered:

1. Standard Odoo lock dates plus OCA reports, Bank Matching and General Reconciliation, with a small custom declaration/closing coordinator. Selected because the ledger, reconciliation and lock semantics remain standard while the missing French filing lifecycle is explicit and source-traced.
2. OCA fiscal-year or cutoff modules alone. Rejected for this milestone because the pinned stack does not supply the required versioned French 2571, 2572, 2065, 2033, 3517/CA12, 3514 and conditional 2069-RCI/2777 preparation lifecycle.
3. A custom tax engine or electronic filing integration. Rejected because it would duplicate official tax logic, create avoidable maintenance and compliance risk, and exceed the requirement for accurate preparation plus external filing tracking.

The custom models never replace Odoo journal entries, taxes, reconciliation or locks. They schedule obligations, expose traceable inputs, collect evidence-backed review decisions and coordinate standard operations.

## Confirmed company profile

The exact-target importer records the confirmed USL profile on source company `1`:

- French SASU;
- corporate income tax (`IS`);
- simplified BIC/IS package;
- simplified VAT / CA12-E workflow;
- fiscal year ending 30 September;
- first reconstructed fiscal year beginning 10 January 2024.

Obligations are not generated until this profile is active. Conditional forms are created only when accounting or explicit evidence provides a signal: 2069-RCI requires a tax-credit evidence record and 2777 requires a dividend/RCM ledger event. The UI therefore does not display every possible French form automatically.

## Versioned declaration rules

The add-on installs separate 2025 and 2026 versions for these families:

| Family | Applicability | Deadline basis |
| --- | --- | --- |
| 2571-SD | IS companies; amount/exemption remains a reviewed portal fact | four dates from the official closing-date band; a 30 September close uses 15 December, 15 March, 15 June and 15 September |
| 2572-SD | final IS balance | 15th day of the fourth month after a non-calendar close |
| 2065 / 2065-bis | IS result declaration | last day of the third month after close plus the published 15-day teleprocedure extension |
| 2033 A-G | simplified BIC/IS package | same filing cycle as 2065 |
| 2069-RCI | only when a qualifying reduction or credit is detected | accompanies the applicable result/IS filing cycle |
| 3517-S / CA12-E | simplified VAT | within three months after a non-calendar close |
| 3514 | simplified-VAT instalment task | company-specific portal day in the official 15-24 July/December window |
| 2777 | only when a dividend/RCM event is detected | event-specific official fiscal calendar |

Official sources retained on every rule:

- [DGFiP professional fiscal calendar](https://www.impots.gouv.fr/professionnel/calendrier-fiscal)
- [DGFiP VAT regimes](https://www.impots.gouv.fr/professionnel/les-regimes-dimposition-la-tva)
- [DGFiP CA12-E deadline guidance](https://www.impots.gouv.fr/professionnel/questions/je-suis-soumis-au-regime-simplifie-dimposition-la-tva-quelle-echeance-dois)
- [2571-SD notice, 2026](https://www.impots.gouv.fr/sites/default/files/formulaires/2571-sd/2026/2571-sd_5289.pdf)
- [2572-SD](https://www.impots.gouv.fr/formulaire/2572-sd/releve-de-solde)
- [2065-SD, 2026](https://www.impots.gouv.fr/sites/default/files/formulaires/2065-sd/2026/2065-sd_5381.pdf)
- [2033 A-G, 2026](https://www.impots.gouv.fr/sites/default/files/formulaires/2033-sd/2026/2033-sd_5395.pdf)
- [2069-RCI-SD](https://www.impots.gouv.fr/formulaire/2069-rci-sd/reductions-et-credits-dimpot)
- [3517-S-SD / CA12](https://www.impots.gouv.fr/formulaire/3517-s-sd/tva-et-taxes-assimilees-et-regime-simplifie)

Rules are routing and deadline metadata, not a substitute for the official portal. Each filing cycle must refresh the current rule version and exact portal deadline.

## Declaration workbench

`Accounting > Declarations` provides list, calendar and form views. Each obligation shows:

- company, fiscal period, form and rule version;
- official source and professional-portal links;
- applicability and computed deadline basis;
- ledger-derived or explicit evidence-derived fields;
- formula, account prefixes, source record and journal-item drill-down;
- missing-information and mismatch flags;
- internal, accountant, filing, acceptance and payment/refund status;
- filing reference and attachment evidence.

Ledger-derived tax-package lines are copied into the workbench with their existing mapping and review state. Information that cannot safely come from the ledger remains explicit: 2065-bis administrative facts and 2033 E/F/G payroll, ownership and subsidiary facts are unresolved until supported by external evidence and review.

Filing cannot be recorded without an accepted reviewer decision and either an external filing reference or evidence attachment. Payment/refund completion cannot be recorded before filing. Electronic submission is not implemented or claimed.

## Confirmed VAT facts and €942 correction

The CA12 workbench records the confirmed facts once:

- opening credit: €0;
- VAT instalments paid: €0;
- accepted and reimbursed refund: €2,500;
- later reimbursed credit: €942;
- remaining VAT credit: €0.

The source ledger held a €3,442 debit on account 445670, a €2,500 credit transfer and bank settlement, and the later €942 DGFiP receipt misclassified to 471000. The correction deliberately preserves that imported bank entry. It posts one balanced, source-traced journal entry dated with the bank receipt:

- debit 471000: €942;
- credit 445670: €942.

Native reconciliation clears the imported 471000 credit against the correction debit and reconciles the three 445670 lines (€3,442 debit, €2,500 credit and €942 credit). The result is one correction move, a reconciled DGFiP statement line, zero residual on 471000 for the receipt, and zero residual on 445670. Repeating the action reuses the same traced correction and creates no duplicate.

The exact-target import applies this confirmed transformation after source lock dates and declaration synchronization, before closing controls. CA12 displays the refund facts; 3514 instalment tasks display only opening credit and zero instalments and are marked no-payment-due.

## Closing workspaces

`Accounting > Closing` generates current fiscal-year month, quarter and annual workspaces plus the locked benchmark annual workspace. Each refresh evaluates:

- draft journal entries;
- draft business documents and missing main attachments;
- bank reconciliation;
- receivable/payable open items;
- due and annual declarations;
- payroll, explicitly external when no payroll module is installed;
- assets and deferrals;
- foreign-currency open items;
- analytic completeness;
- open discrepancies;
- report professional acceptance;
- period-specific FEC acceptance;
- standard Odoo lock dates.

Controls are passed, warning, blocking or not applicable, with count, amount, owner, explanation and drill-down. Warnings remain visible in the package; blockers prevent requesting closing review and prevent applying locks.

An Accounting Manager may close only after all blockers clear and an evidence-backed closing decision is recorded. Closing advances standard Odoo global, tax, sales and purchase lock dates through the period end and records before/after JSON evidence. It never applies the irreversible hard lock date.

The Closing Review Package is available in XLSX and PDF. It contains the overview, every control, declaration schedule, declaration fields and lock evidence. The PDF uses a restrained A4 accounting-package layout derived from the supplied USL plaquette and liasse: repeated legal identity and period headers, compact shaded tables, French status labels and page numbering. The XLSX contains separate `Metadata`, readable `Report` and raw `Audit Data` sheets, with typed amounts, filters, frozen headers and print settings. Company legal address, registry and VAT identity come from the source-traced company partner; identifiers stay text-formatted. Professional acceptance is never inferred from technical checks.

When an Accounting Manager generates a package from a closing workspace, the
file is attached to that workspace. Recording an accepted or
accepted-with-difference closing decision requires at least one such package
and immediately copies every accepted package into an immutable snapshot. Each
snapshot freezes the file bytes, SHA-256, size, package reference, conclusion,
summary, evidence, reviewer and review timestamp. Neither managers nor
reviewers can edit or delete it. The package reference and accepted attachments
also remain locked while that recorded decision is current; a superseding
decision starts a new review cycle. Standard lock dates cannot advance without
the accepted snapshot.

Two alternatives were considered. Keeping only the original attachment link
was rejected because the linked file could later change. Freezing the entire
closing workspace was rejected because normal follow-up cycles must remain
possible. The selected snapshot freezes exactly the accepted evidence and
decision while leaving a controlled superseding-review path.

## Roles and review gates

- Valentin / Accounting Manager can refresh, prepare, post the confirmed VAT correction, record external filing/payment state and apply standard locks.
- Prosper / USL Accountant Review inherits Odoo accounting read-only access. The role can inspect source entries, reports, declarations, closings and packages and can create immutable review decisions. It cannot edit posted accounting, declaration preparation records, closing controls or lock dates.
- Review decisions update declaration/closing state through the controlled decision application only. Accepted declaration decisions require an evidence summary; accepted closing decisions also require a generated package and create immutable snapshots.
- A closing acceptance does not override automated blockers. A close with blockers remains blocked.
- Future finance agents should receive company-scoped read access or purpose-specific manager authority; they must not receive unrestricted cross-company accounting access.

## Validation workflow

For a declaration/closing-only change, use the narrow workflow:

```bash
scripts/odoo-dev ruff \
  custom-addons/rebuild_account_migration/models/declaration.py \
  custom-addons/rebuild_account_migration/models/closing.py \
  custom-addons/rebuild_account_migration/tests/test_declaration_closing.py

docker compose exec -T devcontainer odoo \
  --config=/etc/odoo/odoo.conf \
  --addons-path=/workspace/odoo/addons,/workspace/odoo/odoo/addons,/workspace/odoo/custom-addons,/workspace/odoo/oca-addons \
  --database=odoo_rebuild_accounting_test \
  --update=rebuild_account_migration \
  --test-enable \
  --test-tags=/rebuild_account_migration:TestDeclarationAndClosing \
  --stop-after-init
```

Because the confirmed profile and VAT transformation run inside the exact-target importer, changes to that integration require the documented target reset/full import stages before final rehearsal. They do not require re-restoring or re-extracting an unchanged source snapshot.

## Remaining professional decisions

Technical completion does not resolve these authority gates:

- report-by-report professional parity acceptance;
- final CA12, 2065/2033 and other declaration values;
- period-specific FEC acceptance;
- closing package acceptance and final milestone closure;
- any accepted difference or deliberate draft/cutoff boundary.

These remain recorded review decisions for Valentin, Prosper or another named authorized professional. Agent-generated evidence must never be presented as their acceptance.

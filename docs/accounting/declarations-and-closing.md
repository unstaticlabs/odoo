# Declarations and closing

Status: implemented product workflow. External electronic filing and professional sign-off remain optional external activities, not engineering completion gates.

## Architecture decision

The selected design is a thin USL workflow layer over standard Odoo accounting controls and the pinned OCA reporting/reconciliation stack.

Alternatives considered:

1. Standard Odoo lock dates plus OCA reports, Bank Matching and General Reconciliation, with a small custom declaration/closing coordinator. Selected because the ledger, reconciliation and lock semantics remain standard while the missing French filing lifecycle is explicit and source-traced.
2. A generic executable tax-query DSL. Rejected because arbitrary trigger queries would be difficult to review, unsafe to extend and too close to a second tax engine.
3. OCA fiscal-year or cutoff modules alone. Rejected because the pinned stack does not supply period-aware French filing obligations.
4. A custom electronic filing integration. Rejected because it would duplicate official tax logic and exceed the accurate-preparation and external-tracking boundary.

The custom models never replace Odoo journal entries, taxes, reconciliation or locks. They schedule obligations, expose traceable inputs, collect evidence-backed review decisions and coordinate standard operations.

## Confirmed company profile

Company profiles are keyed by SIREN. The confirmed profiles for Unstatic Labs
and USL MEDIA are:

- French SASU;
- corporate income tax (`IS`);
- simplified BIC/IS package;
- simplified VAT / CA12-E workflow;
- fiscal year ending 30 September;
- each company's exceptional first fiscal year;
- simplified VAT through 30 September 2027 and quarterly CA3 from 1 October 2027;
- OSS disabled unless explicit registration evidence is later recorded.

USL MEDIA's first fiscal year is 1 June 2026–30 September 2027. It receives no 2571 instalment during that first year. Its simplified-VAT regularisation is split into 1 June–31 December 2026 and 1 January–30 September 2027. Obligations are not generated until a profile is active.

## Versioned declaration rules

The add-on installs versioned, whitelisted rules. Each rule declares its fiscal-year, calendar-year, quarter, month or event period; transaction/registration/threshold trigger; deadline basis; filing channel; and payment applicability.

| Family | Applicability | Deadline basis |
| --- | --- | --- |
| 2571-SD | IS companies; amount/exemption remains a reviewed portal fact | four dates from the official closing-date band; a 30 September close uses 15 December, 15 March, 15 June and 15 September |
| 2572-SD | final IS balance | 15th day of the fourth month after a non-calendar close |
| 2065 result dossier | 2065 / 2065-bis with 2033 A-G as supporting annexes, not a duplicate obligation | last day of the third month after close plus the published 15-day teleprocedure extension |
| 2069-RCI | only when a qualifying reduction or credit is detected | accompanies the applicable result/IS filing cycle |
| 3517-S / CA12-E | simplified VAT | within three months after a non-calendar close |
| 3514 | simplified-VAT instalment task | company-specific portal day in the official 15-24 July/December window |
| 2777 | one calendar month only when an RCM transaction is detected | 15th of the following month, reconciled to the official calendar |
| 2561 IFU | calendar year only when RCM occurred | 15 February following the year |
| DES | month containing a qualifying reverse-charge EU B2B service | tenth working day of the following month |
| OSS | quarter only after explicit registration; nil filings continue while registered | end of the following month |
| DAS2 | calendar-year review only when a beneficiary's account-622 evidence exceeds €2,400 | conservative 30 April campaign date; paid/category facts remain reviewable |
| 1330-CVAE / 1329-AC | reporting above €152,500 turnover; instalments only with reviewed prior liability above €1,500 | result cycle / 15 June and 15 September |
| 1447-C / CFE | creation event, annual balance, and prior-liability-only advance | 31 December / 15 December / conditional 15 June |
| CA3 | quarterly from the company-specific post-RSI transition | conservative end of the following month pending portal reconciliation |

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
- internal review, optional accountant review, filing and payment/refund status;
- filing reference and attachment evidence.

Ledger-derived tax-package lines are copied only to the matching evidenced fiscal period; values are never copied into a later fiscal year. Information that cannot safely come from the ledger remains explicit. A 3514 amount is never defaulted to zero or “not due”: the portal amount remains unresolved, with the 80% first-period guidance for a new company. USL-specific VAT refund facts never leak to USL MEDIA or to an unevidenced future period.

An Accounting Manager can approve a prepared declaration for filing without waiting for an external accountant. Filing requires an external reference or evidence attachment. Payment/refund completion cannot be recorded before filing. Electronic submission is not implemented or claimed.

## Confirmed VAT facts and €942 refund

The CA12 workbench records the confirmed facts once:

- opening credit: €0;
- VAT instalments paid: €0;
- accepted and reimbursed refund: €2,500;
- later reimbursed credit: €942;
- remaining VAT credit: €0.

The working ledger records the later €942 DGFiP receipt on account 445670 and
reconciles it natively with the VAT-credit balance. The
statement line has zero residual, the relevant 445670 lines share the same
full reconciliation, and no 471 suspense position or local correction is
required.

Release validation compares those entries, residuals and reconciliation links
directly without normalizing them. CA12
displays the refund facts; 3514 instalment tasks display only opening credit and
zero instalments and are marked no-payment-due.

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
- canonical report availability;
- native FEC availability;
- standard Odoo lock dates.

Controls are passed, warning, blocking or not applicable, with count, amount, owner, explanation and drill-down. Warnings remain visible in the package; blockers prevent requesting closing review and prevent applying locks.

An Accounting Manager may close only after all blockers clear and an evidence-backed internal closing decision is recorded. Optional accountant review can be retained alongside that decision. Closing advances standard Odoo global, tax, sales and purchase lock dates through the period end and records before/after JSON evidence. It never applies the irreversible hard lock date.

The Closing Review Package is available in XLSX and PDF. It contains the overview, every control, declaration schedule, declaration fields and lock evidence. The PDF uses a restrained A4 accounting-package layout derived from the supplied USL plaquette and liasse: repeated legal identity and period headers, compact shaded tables, French status labels and page numbering. The XLSX contains separate `Metadata`, readable `Report` and raw `Audit Data` sheets, with typed amounts, filters, frozen headers and print settings. Company legal address, registry and VAT identity come from the company partner; identifiers stay text-formatted. Technical checks are recorded separately from any optional professional opinion.

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

- Accounting Manager can refresh, prepare, post permitted corrections, record external filing/payment state and apply standard locks.
- Accountant Reviewer may perform reversible Accounting work in unlocked periods and may create and record an audited `declaration_review` decision for a declaration in an allowed company. Declaration and closing controls remain narrower: the reviewer cannot alter the declaration directly, file, pay, refresh ledger values, approve closings, change lock dates, supersede decisions or cross a company boundary. Prosper receives this role with Unstatic Labs and USL MEDIA as his only allowed companies.
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
  --database=odoo_validation_exact \
  --update=rebuild_account_migration \
  --test-enable \
  --test-tags=/rebuild_account_migration:TestDeclarationAndClosing \
  --stop-after-init
```

Changes to company profiles, VAT periods or generated obligations must be
rehearsed on a disposable clone of the current production database. Apply them
through a versioned, idempotent module upgrade after taking a coordinated
checkpoint. Never reset the working database from the Online export.

## Residual professional assumptions

Final declaration values, actual filing, an external professional opinion and the legal decision to lock a production period remain human responsibilities. The product makes their source, evidence and status explicit. Their absence does not block engineering completion or internal preparation, and generated evidence must never be presented as a person’s acceptance.

## Company schedule acceptance

- USL has 2571, 2572, one 2065/2033 result dossier, 3514 through July 2027, CA12-E through 30 September 2027, and quarterly CA3 from 1 October 2027.
- USL MEDIA has no 2571 through its first close; its first 2065/2033 and 2572 are due 15 January 2028; its long first VAT period is split; 1447-C is due 31 December 2026; its first annual CFE balance is represented in 2027.
- No fiscal-year 2777 or independent 2033 obligation remains active. Superseded generated rows are retained as explicitly not applicable for auditability.
- RCM, IFU, DES, OSS, DAS2 and CVAE instances exist only when their governed transaction, registration, threshold or reviewed-liability signal is present.
- Synchronization is idempotent, never copies an old tax-package value into a future period and does not overwrite filed, paid or archived evidence.
- Prosper can read both companies' declaration records and record declaration-review decisions, while direct mutations, closing approvals, filing/payment actions and unauthorized-company access fail closed.

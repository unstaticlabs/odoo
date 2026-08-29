---
name: odoo-accounting-integrity
description: Protect stable accounting behavior when changing Odoo ledgers, journals, taxes, reconciliation, lock dates, reports, multi-company accounting, audit evidence, or accounting migrations. Use for any feature whose failure could alter financial meaning or auditability.
---

# Odoo Accounting Integrity

Apply stable accounting invariants independently of the temporary migration milestone state. Read the relevant `docs/accounting/` specifications and `docs/operations/accounting-development-workflow.md` before changing accounting behavior.

1. Preserve balanced, immutable posted entries and the distinction between draft correction, reversal, cancellation, and deletion.
2. Preserve fiscal/tax lock dates, journal sequencing, reconciliation links, analytic dimensions, currency semantics, tax exigibility, evidence attachments, chatter, and company ownership.
3. Treat displayed totals, exports, reports, and drill-downs as accounting behavior; reconcile them to the ledger with representative cases.
4. Test permitted and forbidden paths using realistic roles and multiple companies. Never rely on UI hiding or `sudo()` as a substitute for accounting authorization.
5. For schema, data, module, or ownership changes, also use `odoo-migration-upgrade-safety`. Prove upgrades on a disposable representative database when warranted.
6. Keep source reconstruction bindings, parity evidence, and migration-only provenance out of the delivered product registry.
7. Record assumptions, exceptions, before/after evidence, lock-date behavior, upgrade/recovery, and any residual audit risk in the task or pull request.

Never weaken an accounting control to make a test or merge pass. Escalate unresolved legal, fiscal, or audit meaning to a qualified human decision-maker.

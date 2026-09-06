# Operate French Electronic-Invoice Reception

This runbook is for maintainers of the deployed production Accounting system.
The user-facing checklist is
[Activate, monitor or pause reception](../users/how-to/activate-electronic-invoice-reception.md).

## Safety boundary

Development, QA, reconstruction, copied and restored databases must use:

```text
USL_EINVOICE_LIVE_ENABLED=0
USL_EREPORTING_LIVE_ENABLED=0
```

With the first guard off, production registration, directory/provider lookup,
fetch, approval/refusal responses and manual checks fail before a network
request. With the second guard off, payment lifecycle and Flow 10 e-reporting
remain disabled. Do not enable both as one change.

Shared native reception schedulers may be active after an upgrade. They process
only companies whose stored reception flag is enabled and whose native
registration is valid. Do not use global scheduler activation as the company
readiness signal.

## Production prerequisites

Before the change window:

1. record the release commit and successful database upgrade;
2. confirm Electronic Invoicing shows **Ready for production**;
3. review VAT number, SIREN/SIRET, `0225` identity, contact and incoming
   purchase journal;
4. confirm the self-check is current and left no synthetic records;
5. record the legal representative, Accounting Manager, operator, provider
   support contact and rollback owner;
6. verify a database and filestore backup;
7. arrange one controlled supplier invoice;
8. confirm e-reporting and pilot behavior remain off.

Stop if any business value or provider eligibility is uncertain.

## Activation

1. Deploy with `USL_EINVOICE_LIVE_ENABLED=1` and
   `USL_EREPORTING_LIVE_ENABLED=0`.
2. Mark the accounting deployment as production through the governed
   deployment procedure.
3. The Accounting Manager selects **Activate reception**.
4. Complete legal-representative authentication and native Odoo Approved
   Platform receiver registration.
5. Treat **Registration in progress** as a waiting state. Do not repeat
   registration while the native state is `smp_registration`.
6. After the company becomes a receiver, deliberately enable its stored
   incoming flag and confirm the status is **Receiving**.
7. Confirm no other company was enabled and no e-reporting job or setting was
   activated.

Do not create a generic Peppol registration as a workaround. Do not copy
credentials or receiver state from another database.

## First-invoice proof

1. Confirm exactly one **Ready for Review** item in
   **Vendors > Incoming E-Invoices**.
2. Verify supplier, reference, date, currency, lines, VAT and total.
3. Verify the original structured document through the bill attachments.
4. Poll again and prove no duplicate bill is created.
5. Post, pay and reconcile the bill.
6. Verify the native approval response was accepted by the mocked boundary in
   pre-production and by the real platform in the production change record.

## Incidents and pause

- **Pause incoming invoices** changes only the selected company. Existing
  bills and evidence remain.
- For authentication or provider failures, pause, preserve the last message
  reference, repair the connection and use **Check now**.
- Retry malformed or mapping failures only after correcting the stated cause.
  Retried documents must not create duplicate liabilities.
- Provider deregistration changes real routing; perform it only with provider
  support and an understood directory consequence.
- Prefer forward repair. Restore a backup only when the incident procedure
  proves that accounting and reception evidence cannot be preserved.

After copying or restoring production, immediately set both guards to `0`,
reset approval and the company reception-enabled flag, and remove copied proxy
credentials through the neutralization procedure.

## Upgrade checks

An upgrade must preserve an already valid production company and its stored
reception flag. It must never activate a new company. After every upgrade:

1. inspect enabled companies and native registration state;
2. verify the reception schedulers were not silently disabled;
3. verify disabled companies remain ignored;
4. verify e-reporting remains off;
5. perform one provider health check in the approved production window.

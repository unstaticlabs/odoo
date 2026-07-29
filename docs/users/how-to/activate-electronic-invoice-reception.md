# Activate Electronic-Invoice Reception in Production

Use this checklist only on the deployed production Accounting database during
an approved change window. It activates reception through the Odoo Approved
Platform while keeping e-reporting disabled.

Do not use these steps on development, QA, staging, reconstruction, a copied
database or a restored backup. Authentication, production registration,
directory changes, polling and deregistration contact external services and
can change the company's real invoice routing.

## Required people and information

Have these available before starting:

- the Accounting Manager approving the change;
- the legal representative completing identity authentication and accepting
  the platform terms;
- the production operator able to change deployment environment variables;
- the rollback owner and a provider-support contact;
- the company VAT number, SIREN/SIRET, representative email and intended
  purchase journal;
- one supplier able to send a controlled first electronic invoice.

Odoo documents its French Approved Platform setup and legal-representative
authentication in the
[official French localization guide](https://www.odoo.com/documentation/19.0/fr/applications/finance/fiscal_localizations/france.html#facturation-electronique).
The French administration explains why reception must use a
[platform approved by the DGFiP](https://www.impots.gouv.fr/facturation-electronique-et-plateformes-agreees).

## Before the change window

1. Confirm the exact application release and database upgrade have passed.
2. Open **Accounting > Configuration > Invoicing > E-Invoicing** for every
   company that may receive invoices.
3. Confirm the accounting country is France and check the VAT number,
   SIREN/SIRET, scheme `0225` identifier, contact email and purchase journal.
4. Select **Test Reception**. Inspect the resulting synthetic €175 draft bill,
   its two VAT rates, original XML and **Test passed** status.
5. Keep **Odoo Approved Platform** selected.
6. Confirm all reception jobs are stopped and the state is **Ready but
   inactive**.
7. Take and verify a recoverable database and filestore backup.
8. Record the operator, Accounting Manager, legal representative, first
   supplier and rollback owner in the change record.

Stop if any value is uncertain. Do not continue from **Configuration
incomplete** or **Not yet verified**.

## Authorize production onboarding

Deploy production with:

```text
USL_EINVOICE_LIVE_ENABLED=1
USL_EREPORTING_LIVE_ENABLED=0
```

Then:

1. In **E-Invoicing**, set **Accounting Deployment** to **Production**.
2. Confirm no other company in the database has an unapproved connected
   receiver.
3. Select **Approve Activation**. This records internal approval and switches
   from Demo to production mode; it does not register or poll.
4. Open the native Odoo Approved Platform configuration.
5. The legal representative selects **Authenticate**, completes the identity
   process and accepts the displayed terms.
6. Return to Odoo and select **Refresh**.
7. Check the SIREN-derived identifier before selecting **Validate Registration
   (Production)**.
8. If activation occurs before 1 September 2026, use **Pilot Phase** only as
   part of the approved live rollout. It is an external connection, not an
   offline test.
9. Confirm **Production onboarding** reads **Identity verified**.
10. Confirm the connection reads **Connected; retrieval suspended** and record
    the directory effective date. Odoo states that directory changes normally
    take effect the following day.

Do not create a separate generic Peppol registration. Odoo states that the
French Approved Platform registration also registers the identifier on
Peppol.

## Start reception

1. Confirm `USL_EREPORTING_LIVE_ENABLED=0`.
2. Select **Start Reception**.
3. Confirm the state becomes **Active**.
4. Confirm only reception, connection-status, participant-status and
   webhook-health jobs are active.
5. Confirm auto-registration, regulatory-document, lifecycle and e-reporting
   jobs remain inactive.

## Verify the first real invoice

Arrange one supplier invoice with an agreed reference, currency, net amount,
VAT rates and total.

1. Open **Accounting > Review > Electronic Invoice Reception**.
2. Confirm exactly one new item reads **Draft Bill Created**.
3. Compare the supplier, reference, dates, currency, lines, VAT and total with
   the supplier control.
4. Open the original structured document. For Factur-X, also confirm the
   original PDF and its embedded structured invoice are retained.
5. Confirm the platform message reference and document fingerprint are
   present.
6. Poll once more and confirm no duplicate bill is created.
7. Review and post the native draft bill, pay it normally and reconcile the
   payment.
8. Record the bill, reception evidence, directory date and last successful
   connection check in the change record.

Reception is accepted only after this complete journey passes.

## Suspend or roll back

If routing, authentication, document creation or evidence is wrong:

1. Select **Pause Reception** immediately.
2. If a stronger stop is required, select **Revoke Approval and Suspend** and
   redeploy with `USL_EINVOICE_LIVE_ENABLED=0`.
3. Preserve every received bill, original file and evidence record.
4. Record the last successful message and all supplier invoices requiring
   follow-up.
5. Prefer forward repair. Restore the backup only when the incident procedure
   proves that accounting and reception evidence cannot be preserved.
6. Use **Remove from Approved Platform** only with provider support and an
   understood French-directory routing consequence.

Reactivation repeats this entire checklist. E-reporting remains a separate
rollout.

## Actions that are not harmless tests

The following actions contact or prepare access to real external services:

- **Authenticate**, **Refresh** and **Validate Registration (Production)**;
- **Pilot Phase**;
- **Start Reception** or any manual live poll;
- live directory lookup;
- **Remove from Approved Platform**.

For consequence-free validation, remain in Demo mode and use **Test
Reception**. Demo and the maintained synthetic fixture do not communicate with
the French electronic-invoicing network.

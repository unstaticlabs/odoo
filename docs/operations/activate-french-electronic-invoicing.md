# Activate, Verify and Suspend French Electronic-Invoice Reception

Use this runbook only for the deployed production Accounting database after
change approval. Never use it on development, staging, reconstruction, copied
or restored databases.

The browser-accessible operator checklist is
[Activate electronic-invoice reception in production](../users/how-to/activate-electronic-invoice-reception.md).
Keep that page open during the change window; this document retains the
maintainer-level activation and recovery contract.

## Production prerequisites

Before the change window:

1. verify that the exact release commit and database upgrade passed;
2. record the legal representative who will complete Odoo's identity
   verification and accept the displayed platform terms;
3. record the VAT number, SIREN/SIRET, scheme `0225` endpoint, platform contact
   email and incoming purchase journal;
4. run **Accounting > Configuration > Invoicing > E-Invoicing > Test
   Reception** and inspect the resulting two-line €175 draft bill,
   original XML and **Test passed** state;
5. keep **Odoo Approved Platform** selected; **Production onboarding
   required** is expected before activation;
6. keep `USL_EREPORTING_LIVE_ENABLED=0`;
7. take a recoverable database and filestore backup and record the operator,
   Accounting Manager, platform support contact and rollback owner.

If any item is uncertain, stop. The correct state is **Not yet verified** or
**Configuration incomplete**, not Ready.

## Deliberate activation

1. Deploy with `USL_EINVOICE_LIVE_ENABLED=1` and
   `USL_EREPORTING_LIVE_ENABLED=0`.
2. Set **Accounting Deployment** to **Production** on the readiness record.
3. Confirm no other company in the database has an unapproved connected
   receiver.
4. Select **Approve Production Activation**. This records who approved and
   when; it does not register or poll.
5. From Accounting settings, open the native approved-platform registration,
   complete legal-representative authentication, accept the displayed terms,
   and validate receiver registration.
6. Confirm **Production onboarding** reads **Identity verified**.
7. Confirm the connection reads **Connected — reception suspended** and the
   French directory effective date is correct.
8. Select **Enable Scheduled Reception**. This enables reception, status,
   participant-status and webhook-health jobs only.
9. Confirm auto-registration, regulatory-document, lifecycle and e-reporting
   jobs remain inactive.

Do not enable a generic Peppol registration as a workaround. Do not change the
e-reporting guard as part of this procedure.

## First-invoice verification

Arrange one controlled supplier invoice with an agreed supplier, reference,
currency, net amount, VAT rates and total.

1. Confirm exactly one new item under **Accounting > Review > Electronic
   Invoice Reception**.
2. Confirm it reads **Draft Bill Created**, not a raw provider state.
3. Compare supplier, reference, date, currency, lines, VAT rates and total with
   the supplier control.
4. Download/open the original structured file from the evidence. For Factur-X,
   confirm the original PDF is retained and the embedded structured invoice is
   attached to the native bill.
5. Confirm the platform message reference and fingerprint are present.
6. Re-poll once and confirm no second bill appears.
7. Review and post the native draft, pay it through the normal workflow, and
   reconcile the bank transaction.
8. Record the bill, reception evidence and last successful connection check in
   the production change record.

The rollout is complete only after these checks pass.

## Recovery

- **Action Required**: read the friendly recovery category, correct the
  supplier/tax/journal or temporary condition, then select **Retry Processing**.
  The retained original is reused and attempts are visible.
- **Duplicate Controlled**: open the linked original reception. Do not create
  another bill unless the supplier confirms a distinct legal document.
- **Rejected by Platform**: preserve the evidence, check the platform incident
  and supplier status, and do not replace the document blindly.
- **Authentication required**: suspend reception, repair credentials or the
  database/platform association, verify the connection, then resume.
- **Platform temporarily unavailable**: keep the evidence and retry after
  recovery. Do not fall back to treating an emailed PDF as the regulated
  invoice.

## Immediate suspension and rollback

1. Select **Suspend Scheduled Reception**. Confirm all reception jobs are
   inactive.
2. For a stronger stop, select **Revoke Approval and Suspend** and deploy with
   `USL_EINVOICE_LIVE_ENABLED=0`.
3. Preserve all received bills and evidence; never delete them during rollback.
4. Record the last successful message and all supplier documents requiring
   follow-up.
5. Use native deregistration only with provider support and an understood
   French directory/routing consequence.
6. Restore a backup only when the incident procedure proves that forward
   repair cannot preserve accounting and reception evidence.

Reactivation repeats the full prerequisites, deliberate activation and
first-invoice verification. There is no automatic re-registration.

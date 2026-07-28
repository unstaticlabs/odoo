# Prepare and Process Electronic-Invoice Reception

France requires every VAT-registered business to receive regulated electronic
invoices through an approved platform from 1 September 2026. This Accounting
release is **ready but inactive** until production activation.

## Check readiness safely

1. Open **Accounting > Configuration > Invoicing > E-Invoicing**.
2. Select the company. Read the state literally:
   - **Configuration incomplete** lists company or journal details to finish;
   - **Not yet verified** means a test or provider-access decision remains;
   - **Test passed** proves the safe software journey for this company;
   - **Ready but inactive** means no live reception is running;
   - **Production activation required** identifies a deliberate production step;
   - **Active** means scheduled production reception is enabled.
3. Follow **Next Action**. It shows only the current phase: company setup,
   offline test, platform verification or production activation.
4. Under **Reception setup**, complete the accounting country, VAT number,
   SIREN/SIRET, French scheme `0225` identifier and purchase journal.
5. Select **Test Reception**. This creates a synthetic two-line
   supplier invoice without contacting a supplier, directory or platform.
6. On the **Safe test** evidence, confirm **Draft Bill Created**, €175 total,
   two lines and the original XML.
7. Open the vendor bill and its **E-Invoice Evidence** tab. Do not post the test
   bill as a real supplier liability.

Provider eligibility and production activation remain separate. A passed test
does not mean USL is registered or connected.

## Process a received invoice

After the production runbook has been completed:

1. Open **Accounting > Review > Electronic Invoice Reception**.
2. Open the new **Draft Bill Created** item.
3. Compare supplier, reference, date, currency, lines, VAT and total with the
   original structured document.
4. Select **Open Vendor Bill**, complete the normal Accounting review, and post
   only when correct.
5. Pay and reconcile it through the ordinary vendor-bill and Bank Matching
   workflow. The original file and reception evidence remain linked.

## Understand exceptions

- **Duplicate Controlled**: no second bill was created. Open the original link
  and confirm whether the supplier intended a new legal document.
- **Action Required**: the original is safe. Correct the stated condition and
  ask an Accounting Manager to select **Retry Processing**.
- **Rejected by Platform**: preserve the item and follow the platform/supplier
  investigation; do not manually duplicate it.
- **Authentication required** or **Platform temporarily unavailable**: suspend
  reception if needed and follow the operator recovery procedure.

Read-only accountants can inspect evidence and draft bills but cannot run the
test, retry processing, post, configure or activate.

Production activation, first-invoice verification and suspension are in
`docs/operations/activate-french-electronic-invoicing.md`.

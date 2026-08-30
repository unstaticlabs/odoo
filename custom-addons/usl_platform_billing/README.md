# USL Platform Billing

`usl_platform_billing` turns content-platform payouts into auditable native
Odoo customer invoices, commission bills, compensation entries and bank
reconciliations.

The application is independent from the historical Accounting reconstruction
module. It depends on Odoo Accounting and the pinned OCA reconciliation API.
The temporary Odoo Online importer lives only under
`migration/platform_billing_restore`.

## Operator flow

1. Configure the platform's partners, products, journals, currency,
   commission rate and bank-recognition rules.
2. Create a monthly session. Its name follows the historical French format,
   such as `Août 2026`; enter payouts or import received bank transactions.
   Imported rows are drafts: complete their platform, original reference,
   currency and original payout amount on the session's Payouts tab.
   When a company-currency bank transaction creates a foreign-currency payout,
   the app derives and displays the effective bank rate.
3. Check the session, generate drafts and review native taxes, accounts,
   payment terms, fiscal positions and analytic distributions.
4. Confirm any warning about an active platform missing from the month, then
   post the documents and optional compensation entries.
5. Select incoming bank transactions and reconcile them. A delayed payout
   stays as an open customer receivable; one later pooled receipt can be
   allocated across several payouts and sessions, and several partial receipts
   can settle one payout.

The bank wizard shows **All open** incoming transactions by default. Configured
patterns, partners, keywords, dates and amounts rank suggestions but do not
hide valid manual choices. **Suggested only** is an optional shorter view.

Auto-posting is off by default. Posted entries cannot be reset or deleted from
the application. Mixed platform currencies are summarized separately, while
the session bank total remains in one declared bank currency.

Bank-created foreign payouts use the actual company-currency receipt to value
their draft invoice, commission bill and compensation. For example, USD 1,000
received as EUR 700 applies `1 USD = 0.70 EUR` to the generated documents and
reconciles without an immediate exchange difference. Payouts recorded before a
receipt exists keep Odoo's reference rate; a later payment can therefore create
the normal delayed-settlement exchange gain or loss.

Access is explicit. A user needs the Platform Billing Reader, Operator or
Administrator role. The standard Odoo Accountant role alone does not expose
this application. Administrators can set an optional native analytic
distribution on each platform; it is copied to generated revenue and
commission lines.

See:

- `docs/product/platform-billing.md`
- `docs/accounting/platform-billing.md`
- `docs/users/how-to/process-platform-payouts.md`

This application accounts for content-platform payouts. It does not connect to
French electronic-invoice platforms and does not make provider calls.

## Validation

```bash
scripts/odoo-dev test usl_platform_billing odoo_test_platform_billing
scripts/odoo-dev ruff custom-addons/usl_platform_billing
make product-migration-boundary
```

## Local QA demo

On an isolated `odoo_dev` project with both electronic-invoice live flags set
to `0`, prepare the repeatable demo data with:

```bash
scripts/odoo-dev bootstrap-platform-billing-qa
```

The command prints the four local-only logins. Search Billing Sessions for
`QA DEMO`: the retained records cover delayed customer debt, a EUR 160 pooled
receipt for two EUR 80 payouts, a EUR 30 partial receipt, a USD payout received
as EUR 72, and creation from an unlinked bank transaction. Rerun the bootstrap
after consuming a demo; it prepares the next clean pooled or import batch.

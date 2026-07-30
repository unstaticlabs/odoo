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
2. Create a monthly session and enter or import its payouts.
3. Check the session, generate drafts and review native taxes, accounts,
   payment terms, fiscal positions and analytic distributions.
4. Post the documents and optional compensation entries.
5. Select incoming bank transactions and reconcile them.

Auto-posting is off by default. Posted entries cannot be reset or deleted from
the application. Mixed platform currencies are summarized separately, while
the session bank total remains in one declared bank currency.

See:

- `docs/product/platform-billing.md`
- `docs/accounting/platform-billing.md`
- `docs/users/how-to/process-platform-payouts.md`
- `docs/operations/platform-billing-migration.md`

This application accounts for content-platform payouts. It does not connect to
French electronic-invoice platforms and does not make provider calls.

## Validation

```bash
scripts/odoo-dev test usl_platform_billing odoo_test_platform_billing
scripts/odoo-dev ruff custom-addons/usl_platform_billing
make product-migration-boundary
```

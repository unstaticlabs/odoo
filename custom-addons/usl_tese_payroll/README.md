# USL TESE Payroll

`usl_tese_payroll` records payroll calculated by an outside provider such as
TESE while retaining Odoo as the HR and accounting source of truth.

It provides versioned employee profiles, immutable monthly accounting
snapshots, provider-PDF posting gates, posted-entry traceability,
residual-derived payment status, conservative bank-candidate suggestions and
persistent diagnostics. Its Documents integration exposes the authorized
Paperless archive from each payroll while retaining the native PDF as the
operational posting evidence.

The module does not calculate a French legal payslip and does not contain
source-dump or restoration machinery.

Documentation:

- product contract: `docs/product/paie-tese.md`;
- French user guide: `docs/users/guides/paie-tese.md`.

Run the focused backend suite with:

```bash
scripts/odoo-dev test usl_tese_payroll odoo_test_usl_tese_payroll
```

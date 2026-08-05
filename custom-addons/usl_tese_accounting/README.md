# USL TESE Payroll — Accounting Closing

This focused runtime bridge connects `usl_tese_payroll` records to the
operational Accounting closing workspace. It replaces the generic external
payroll fallback with company- and period-scoped controls for official TESE
evidence, posted journal entries, and open salary or URSSAF liabilities.

The bridge keeps the payroll and Accounting products independently
installable. It contains no source bindings, reconstruction state, or
migration provenance.

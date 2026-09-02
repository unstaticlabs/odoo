# USL Expense Batches

Technical module name: `usl_expense_batch`

This cohesive production feature extends native Odoo Expenses with optional,
contextual claim batches. Products remain the expense categories; a Batch can
carry a shared purpose, intended date window, native analytic distribution and
manager-controlled account override. Per-expense provenance, explicit
exceptions, revision checks and baseline restoration make application
predictable and idempotent.

Batches stay open until manually archived. Expense progress is informational:
later draft, approved or posted expenses can join an open compatible Batch
without changing posted accounting.

The Expenses app title opens **My Expenses** directly. Its navbar keeps only
the operational **Expenses to Process** and **Expense Batches** entries before
the native **Reporting** and **Configuration** sections; it does not add a
redundant **My Expenses** submenu.

The create-or-select preview ranks compatible Batches and explains changed,
preserved and skipped lines before mutation. The focused Batch form keeps
interactive shared analytics, expense nature and mixed-payer work together;
specific line attention is available through a compact indicator. Access-
checked ORM services expose the same rules to future MCP consumers.

`usl_accounting` depends on this module to expose Batch and payer reporting
dimensions on journal and analytic lines. Reconstruction logic stays under
`migration/` and must not be added here.

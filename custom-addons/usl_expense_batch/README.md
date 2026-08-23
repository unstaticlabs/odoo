# USL Expense Batches

Technical module name: `usl_expense_batch`

This cohesive production feature extends native Odoo Expenses with lightweight
claim batches. It owns its batch model, wizard, views, security and focused
tests and depends only on `hr_expense`.

The Expenses app title opens **My Expenses** directly. Its navbar keeps only
the operational **Expenses to Process** and **Expense Batches** entries before
the native **Reporting** and **Configuration** sections; it does not add a
redundant **My Expenses** submenu.

The USL Accounting compatibility module depends on it so existing navigation
and product integration remain stable. Reconstruction logic must not be added
to this module.

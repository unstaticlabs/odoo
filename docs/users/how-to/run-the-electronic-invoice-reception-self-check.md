---
title: Run the electronic-invoice reception self-check
type: how-to
description: Prove that a company can receive electronic invoices, with a representative invoice that leaves no accounting behind.
lang: en
persona: administrator
app: Accounting
models:
  - res.company
screen: /odoo/action-rebuild_account_migration.action_rebuild_einvoice_readiness
journey: run-the-electronic-invoice-reception-self-check
tour: usl_einvoice_manager_reception
source:
  - custom-addons/rebuild_account_migration/tests/test_einvoice_journeys.py
screenshot_01-companies_png: c92e41a3aef6a277a2fc9bd1d4e3d78d9c1c92cf61e6acc493534609746271a2
screenshot_02-setup_png: c92e41a3aef6a277a2fc9bd1d4e3d78d9c1c92cf61e6acc493534609746271a2
screenshot_03-passed_png: 42537a6f5ff68e3a0155c706c1dd3f93fe371711adec309da9c58c7aba76504b
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Run the electronic-invoice reception self-check

Prove that a company can receive electronic invoices, with a representative invoice that leaves no accounting behind.

1. Open **Accounting > Configuration > Electronic Invoicing**. Each company you may act in is listed with its reception status. Choose the company.

   ![Open Accounting Configuration Electronic Invoicing](run-the-electronic-invoice-reception-self-check/01-companies.png)

2. The setup screen gathers what reception needs: the journals, the accounts, the approved platform and the last self-check.

   ![The setup screen gathers what reception needs: the journals, the accounts, the approved platform and the last self-check](run-the-electronic-invoice-reception-self-check/02-setup.png)

3. Choose **Run self-check**. Odoo receives a representative electronic invoice through the whole reception path, then discards every record it created.

4. **Reception is ready** confirms the check passed; the setup screen shows when it last passed and that nothing was kept. Run it again after any change to the journals, the accounts or the platform.

   ![Reception is ready confirms the check passed; the setup screen shows when it last passed and that nothing was kept](run-the-electronic-invoice-reception-self-check/03-passed.png)

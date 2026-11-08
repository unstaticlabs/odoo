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
screenshot_01-setup_png: c92e41a3aef6a277a2fc9bd1d4e3d78d9c1c92cf61e6acc493534609746271a2
screenshot_02-passed_png: fdc7e60740a4fbf1178570221b314b193c6499884f4b26bb25b4413f31d4e4db
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Run the electronic-invoice reception self-check

Prove that a company can receive electronic invoices, with a representative invoice that leaves no accounting behind.

1. Open **Accounting > Configuration > Electronic Invoicing**. Each company you may act in is listed with its reception status. Choose the company.

2. The setup screen gathers what reception needs: the journals, the accounts, the approved platform and the last self-check.

   ![The setup screen gathers what reception needs: the journals, the accounts, the approved platform and the last self-check](run-the-electronic-invoice-reception-self-check/01-setup.png)

3. Choose **Run self-check**. Odoo receives a representative electronic invoice through the whole reception path, then discards every record it created.

4. The self-check reads **Test passed**, with when it ran and the confirmation that no test bill was kept. Run it again after any change to the journals, the accounts or the platform.

   ![The self-check reads Test passed, with when it ran and the confirmation that no test bill was kept](run-the-electronic-invoice-reception-self-check/02-passed.png)

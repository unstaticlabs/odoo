---
title: Contrôler un lot de dépenses en lecture seule
type: how-to
description: Consultez les dépenses, leurs justificatifs et leur état de rapprochement avec les droits de lecture comptable.
lang: fr
persona: accountant
translation_of: how-to/audit-a-batch-read-only.md
app: Dépenses
models:
  - usl.expense.batch
screen: /odoo/action-usl_expense_batch.action_expense_batches
journey: controler-un-lot-en-lecture-seule
tour: usl_expense_batch_readonly_audit
source:
  - custom-addons/usl_expense_batch/tests/test_expense_batch_journeys.py
screenshot_01-evidence_png: 6a94b6959b9bf240ae73d5541a7d120464e76248edf8bb4577cca8259693dd51
screenshot_02-reconciliation_png: 8579eb298176eefa8699a56bfdb97242cdfcc988eeb5a8e286f5438686e17a5f
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Contrôler un lot de dépenses en lecture seule

Consultez les dépenses, leurs justificatifs et leur état de rapprochement avec les droits de lecture comptable.

1. Dans **Dépenses > Lots de dépenses**, ouvrez le lot à contrôler. Consultez son récapitulatif, ses lignes et les justificatifs associés.

   ![Dans Dépenses Lots de dépenses, ouvrez le lot à contrôler](controler-un-lot-en-lecture-seule/01-evidence.png)

2. Avec les droits de lecture comptable, vous pouvez examiner le lot à toutes les étapes. Les actions de soumission, d’approbation, de comptabilisation et de retrait restent réservées aux personnes habilitées.

3. Ouvrez l’onglet **Comptabilité et historique**.

4. Lisez l’état du rapprochement et l’historique. Si une correction est nécessaire, indiquez le lot et la ligne concernés à la personne chargée de la comptabilisation.

   ![Lisez l’état du rapprochement et l’historique](controler-un-lot-en-lecture-seule/02-reconciliation.png)

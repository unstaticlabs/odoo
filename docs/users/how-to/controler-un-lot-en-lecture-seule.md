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
screenshot_01-evidence_png: 9d5d833b7c90cbd3fe069d5d964b6ff1a5abbcbe933c3cb524ec97e8e363d3d0
screenshot_02-reconciliation_png: 3a5caa19efd376ae80c010e45edcace8249cda3c635da89cf42f43ee19b33ab9
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

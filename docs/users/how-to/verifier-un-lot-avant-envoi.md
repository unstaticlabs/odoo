---
title: Vérifier un lot de dépenses avant de le soumettre
type: how-to
description: Contrôlez les montants par payeur et repérez les lignes dont la répartition analytique diffère de celle du lot.
lang: fr
persona: employee
translation_of: how-to/review-a-batch-before-submitting.md
app: Dépenses
models:
  - usl.expense.batch
  - hr.expense
screen: /odoo/action-usl_expense_batch.action_expense_batches
journey: verifier-un-lot-avant-envoi
tour: usl_expense_batch_focused_review
source:
  - custom-addons/usl_expense_batch/tests/test_expense_batch_journeys.py
screenshot_01-batch_png: 21c93df064d3daf20ff59a9b3c53cd71f3fd9a22b39d2a8845c687250bbc6e5c
screenshot_02-exception_png: 1517f9d60792b8d0e0abdbf4daaad763be3a45411b30e4671aa993b81886bfcc
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Vérifier un lot de dépenses avant de le soumettre

Contrôlez les montants par payeur et repérez les lignes dont la répartition analytique diffère de celle du lot.

1. Dans **Dépenses > Lots de dépenses**, ouvrez votre lot. Le récapitulatif distingue les avances du salarié des frais de la société. L’onglet **Dépenses** en donne le détail.

   ![Dans Dépenses Lots de dépenses, ouvrez votre lot](verifier-un-lot-avant-envoi/01-batch.png)

2. Vérifiez la répartition analytique du lot. Une modification s’applique aux lignes qui reprennent cette répartition.

3. Avec **Appliquer le contexte**, consultez l’aperçu avant de confirmer. Une ligne avec une affectation particulière conserve votre choix, sauf si vous demandez son remplacement.

4. Un repère signale une ligne dont l’affectation diffère. Survolez-le pour lire la différence. Ouvrez la ligne pour la corriger si nécessaire.

   ![Un repère signale une ligne dont l’affectation diffère](verifier-un-lot-avant-envoi/02-exception.png)

5. Vous pouvez corriger la composition du lot en conservant les dépenses.

6. Cliquez sur **Retirer du lot** pour remettre la dépense dans votre liste. Elle pourra rejoindre un autre lot.

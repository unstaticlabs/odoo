---
title: Créer ou choisir un lot de dépenses
type: how-to
description: Réunissez les dépenses d’un même déplacement dans un lot, même si certaines ont été réglées par la société et d’autres par le salarié.
lang: fr
persona: employee
translation_of: how-to/group-expenses-into-a-batch.md
app: Dépenses
models:
  - hr.expense
  - usl.expense.batch
screen: /odoo/action-hr_expense.hr_expense_actions_my_all
journey: regrouper-des-depenses-en-lot
tour: usl_expense_batch_create_or_select
source:
  - custom-addons/usl_expense_batch/tests/test_expense_batch_journeys.py
screenshot_01-work_list_png: 6c7e5cbff37272e4c4d05065abeaad4d33f1f040695a4f7e14eb7685fc77885a
screenshot_02-preview_png: 08a4911db8b224a96765efe8b94269171546e44c2e9759561928e38c90b3d922
screenshot_03-grouped_png: 7ffbebc36936bd6f3f2e440a3ef74ad19fffc400b2eeea8a195418bae86938ac
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Créer ou choisir un lot de dépenses

Réunissez les dépenses d’un même déplacement dans un lot, même si certaines ont été réglées par la société et d’autres par le salarié.

1. Dans **Dépenses > Mes dépenses**, retrouvez les frais du déplacement. Dans cet exemple, il s’agit de l’hôtel et du taxi.

   ![Dans Dépenses Mes dépenses, retrouvez les frais du déplacement](regrouper-des-depenses-en-lot/01-work_list.png)

2. Cochez la première dépense.

3. Cochez les autres dépenses du déplacement.

4. Cliquez sur **Ajouter à un lot de dépenses**.

5. Vérifiez le lot proposé. Odoo tient compte du salarié, de la société, des dates et de la répartition analytique. Vous pouvez aussi créer un lot.

6. Contrôlez les deux montants de l’aperçu. L’un correspond aux frais avancés par le salarié, l’autre aux frais réglés par la société.

   ![Contrôlez les deux montants de l’aperçu](regrouper-des-depenses-en-lot/02-preview.png)

7. Confirmez l’ajout au lot.

8. Les dépenses quittent la liste des frais à regrouper. Ouvrez le lot pour les vérifier et les soumettre ensemble.

   ![Les dépenses quittent la liste des frais à regrouper](regrouper-des-depenses-en-lot/03-grouped.png)

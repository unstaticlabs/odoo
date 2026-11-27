---
title: De la première connexion au premier lot de dépenses comptabilisé
type: tutorial
description: Entraînez-vous sur un déplacement approuvé. Comptabilisez les frais payés par la société et l’avance du salarié, puis retrouvez les deux écritures.
lang: fr
persona: accountant
translation_of: tutorial/post-your-first-expense-batch.md
app: Expenses
models:
  - usl.expense.batch
  - hr.expense
  - account.move
screen: /odoo
journey: comptabiliser-votre-premier-lot-de-depenses
tour: usl_expense_first_posted_batch
source:
  - custom-addons/usl_expense_batch/tests/test_expense_batch_journeys.py
screenshot_01-home_png: e2679f3f34cbd477856c7f824d1b6e5cc011f8440bb7a339508983466f553709
screenshot_02-approved_png: f5c16d087322a8060241985da32aa3fbc22a4a7c28dab60eb9f7488d94a4744e
screenshot_03-post_png: 091018b9ebeb98f1e7c2981e84c482d4cb5a495ce686e0979e98f38bdb127f80
screenshot_04-entries_png: 9239f4f3d15d135ce15b8fd5c7d8080a569f25015a12f04759e38d38a98d5d04
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# De la première connexion au premier lot de dépenses comptabilisé

Entraînez-vous sur un déplacement approuvé. Comptabilisez les frais payés par la société et l’avance du salarié, puis retrouvez les deux écritures.

1. Utilisez une base de formation avec un lot approuvé et un compte autorisé à comptabiliser les dépenses. Connectez-vous avec votre compte habituel. Vous arrivez sur **Accueil**.

   ![Utilisez une base de formation avec un lot approuvé et un compte autorisé à comptabiliser les dépenses](comptabiliser-votre-premier-lot-de-depenses/01-home.png)

2. Ouvrez le menu des applications en haut à gauche.

3. Ouvrez **Dépenses**. Dans cet exemple, le salarié a avancé les frais d’hôtel et la société a réglé le repas.

4. Ouvrez **Lots de dépenses**. Repérez le déplacement approuvé qui vous sert d’exercice.

5. Ouvrez **Séminaire de démonstration**, ou le lot équivalent dans votre base de formation.

6. Lisez le récapitulatif avant de comptabiliser. Repérez qui a payé chaque dépense et gardez les deux montants en tête pour la suite.

   ![Lisez le récapitulatif avant de comptabiliser](comptabiliser-votre-premier-lot-de-depenses/02-approved.png)

7. Cliquez sur **Comptabiliser le lot**. Odoo comptabilise les frais payés par la société et ouvre une confirmation distincte pour la somme avancée par le salarié.

   ![Cliquez sur Comptabiliser le lot](comptabiliser-votre-premier-lot-de-depenses/03-post.png)

8. Vérifiez le journal et la date comptable, puis cliquez sur **Comptabiliser les dépenses** pour enregistrer la part du salarié.

9. Vous venez de comptabiliser votre premier lot. Comparez les montants des deux écritures avec le récapitulatif. Vous retrouvez séparément la dépense payée par la société et la somme à rembourser au salarié.

   ![Vous venez de comptabiliser votre premier lot](comptabiliser-votre-premier-lot-de-depenses/04-entries.png)

---
title: Comptabiliser les versements mensuels des plateformes
type: how-to
description: Saisissez les montants déclarés par les plateformes et comptabilisez les pièces du mois, y compris lorsque le versement bancaire reste à recevoir.
lang: fr
persona: finance_operator
translation_of: how-to/create-a-monthly-platform-billing-session.md
app: Facturation des plateformes
models:
  - usl.platform.billing.session
  - usl.platform.billing.payout
screen: /odoo/action-usl_platform_billing.action_platform_billing_sessions
journey: comptabiliser-les-versements-mensuels-des-plateformes
tour: usl_platform_billing_operator_creation_journey
source:
  - custom-addons/usl_platform_billing/tests/test_platform_billing_journeys.py
screenshot_01-sessions_png: 8961ffee1b1cb747db3efe74817daee8f1903d66e809aa0ba32be6b4a7bb7534
screenshot_02-period_png: 9171db2a86bae336bdf730edb6f605eb002f4825c004084b74488a316db9271e
screenshot_03-net_amount_png: 581d6e13f7dd4e96fab27c91609fd6a8b7e4dc59341f738a8768efd8dbbced05
screenshot_04-coverage_png: 1316712b7693b47c839852c3106b553596ddff097676cbaab85fd465d01eeb82
screenshot_05-posted_png: b89e3f85dffe98e8867a376e9633da010ed070cb5defc26298fbd8b9096a86de
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Comptabiliser les versements mensuels des plateformes

Saisissez les montants déclarés par les plateformes et comptabilisez les pièces du mois, y compris lorsque le versement bancaire reste à recevoir.

1. Ouvrez **Facturation des plateformes > Sessions de facturation**. Consultez les sessions existantes pour vérifier que le mois reste à traiter.

   ![Ouvrez Facturation des plateformes Sessions de facturation](comptabiliser-les-versements-mensuels-des-plateformes/01-sessions.png)

2. Cliquez sur **Nouveau**.

3. Choisissez le premier jour du mois à traiter. Odoo donne à la session le nom du mois. Vérifiez la date de facturation et choisissez le dernier jour du mois si la date précédente est encore affichée.

   ![Choisissez le premier jour du mois à traiter](comptabiliser-les-versements-mensuels-des-plateformes/02-period.png)

4. Ajoutez une ligne pour chaque versement déclaré par une plateforme au cours du mois.

5. Choisissez la plateforme et indiquez la date, la référence et le montant net figurant sur son relevé. Odoo calcule le montant brut et la commission à partir du taux de la plateforme.

   ![Choisissez la plateforme et indiquez la date, la référence et le montant net figurant sur son relevé](comptabiliser-les-versements-mensuels-des-plateformes/03-net_amount.png)

6. Cliquez sur **Contrôle**. Odoo enregistre la session et vérifie les plateformes, les journaux, les produits et les montants nécessaires à la facturation.

7. Cliquez sur **Générer les brouillons**. Chaque versement donne lieu à une facture de vente pour la recette et à une facture fournisseur pour la commission.

8. Vérifiez les pièces puis cliquez sur **Comptabiliser les pièces**.

9. Si une plateforme active n’a aucun versement ce mois-ci, Odoo indique son nom. Revenez à la saisie si une ligne manque.

   ![Si une plateforme active n’a aucun versement ce mois-ci, Odoo indique son nom](comptabiliser-les-versements-mensuels-des-plateformes/04-coverage.png)

10. Confirmez si l’absence est attendue, par exemple pour une plateforme qui paie chaque trimestre.

11. La session et les pièces sont comptabilisées. Un versement encore absent de la banque reste ouvert.

   ![La session et les pièces sont comptabilisées](comptabiliser-les-versements-mensuels-des-plateformes/05-posted.png)

12. Lorsque l’encaissement apparaît sur le relevé, revenez dans la session pour **Rapprocher les transactions bancaires**.

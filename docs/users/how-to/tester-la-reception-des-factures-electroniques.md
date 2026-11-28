---
title: Tester la réception des factures électroniques
type: how-to
description: Vérifiez la configuration d’une société avec une facture électronique d’essai. Le résultat du contrôle reste consultable, et la facture d’essai est supprimée.
lang: fr
persona: administrator
translation_of: how-to/run-the-electronic-invoice-reception-self-check.md
app: Comptabilité
models:
  - res.company
screen: /odoo/action-rebuild_account_migration.action_rebuild_einvoice_readiness
journey: tester-la-reception-des-factures-electroniques
tour: usl_einvoice_manager_reception
source:
  - custom-addons/rebuild_account_migration/tests/test_einvoice_journeys.py
screenshot_01-setup_png: f746ab95217d0c51a41c0542ba62a3b25a46dc7e5349f0972b77307f7da24e45
screenshot_02-passed_png: 9d70b58dae6a29eafecb756bc24c4c21bb99037611a4d20bb85f2523d28026d4
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Tester la réception des factures électroniques

Vérifiez la configuration d’une société avec une facture électronique d’essai. Le résultat du contrôle reste consultable, et la facture d’essai est supprimée.

1. Dans **Comptabilité > Configuration > Facturation électronique**, ouvrez la société dont vous voulez tester la réception.

2. Vérifiez les journaux, les comptes utilisés et la plateforme agréée. Le résultat du dernier essai figure sur la même fiche.

   ![Vérifiez les journaux, les comptes utilisés et la plateforme agréée](tester-la-reception-des-factures-electroniques/01-setup.png)

3. Cliquez sur **Exécuter une auto-vérification**. Odoo fait passer une facture d’essai par les étapes de réception, puis supprime les pièces créées pour ce contrôle.

4. Vérifiez la mention **Test réussi**, la date de l’essai et la confirmation qu’aucune facture d’essai n’a été conservée. Relancez le contrôle après une modification des journaux, des comptes ou de la plateforme.

   ![Vérifiez la mention Test réussi, la date de l’essai et la confirmation qu’aucune facture d’essai n’a été conservée](tester-la-reception-des-factures-electroniques/02-passed.png)

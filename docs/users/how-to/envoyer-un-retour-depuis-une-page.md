---
title: Envoyer un retour depuis une page Odoo
type: how-to
description: Signalez une difficulté, proposez une amélioration ou posez une question depuis la page concernée, puis retrouvez les réponses de l’équipe produit.
lang: fr
persona: everyone
translation_of: how-to/send-feedback-from-any-page.md
app: Retours
models:
  - project.task
screen: /odoo
journey: envoyer-un-retour-depuis-une-page
tour: usl_feedback_desktop_journey
source:
  - custom-addons/usl_feedback/tests/test_feedback_journeys.py
screenshot_01-open_drawer_png: 32891066d7506a8c4926fa6f0f5f1b65cfa9070d467977274c86853e2049f9f2
screenshot_02-conversation_png: cad926cc97c848735d413cab68dab678b63b8122c20a47032c9aef6771445458
screenshot_03-chatter_png: 1e2136514a30dae137a7da445696ea498618751799b39ef083d644c50440ddc1
screenshot_04-team_received_png: d181d2fe93e1ac43d30b668ad542c85875b6abc8e670e42c9d49472bd26319da
screenshot_05-recent_list_png: 5f2410eb4d8d893153d1d3cf98e1a88b6227620de1acb3a88398877c9f5eecc8
generated: true
---

<!-- Generated from the journey named in the front matter. Change the test, then run `make docs`. -->

# Envoyer un retour depuis une page Odoo

Signalez une difficulté, proposez une amélioration ou posez une question depuis la page concernée, puis retrouvez les réponses de l’équipe produit.

1. Ouvrez **Messages** dans la barre du haut. Le bouton de retour se trouve à côté de **Nouveau message**, dans chaque onglet.

   ![Ouvrez Messages dans la barre du haut](envoyer-un-retour-depuis-une-page/01-open_drawer.png)

2. Cliquez sur le bouton de retour.

3. Une conversation s’ouvre. Odoo prépare un aperçu de la page. Il reste sur votre appareil jusqu’à ce que vous choisissiez de l’envoyer.

   ![Une conversation s’ouvre](envoyer-un-retour-depuis-une-page/02-conversation.png)

4. Décrivez ce que vous vouliez faire, puis ce que vous avez observé.

5. Vérifiez **Détails de la page**. Cette option joint la page de travail, la fiche accessible, la société et la version. Décochez-la si la page n’aide pas à comprendre le retour.

6. Cliquez sur **Envoyer un retour** pour démarrer la conversation. L’assistant prépare un brouillon à relire.

7. Relisez le brouillon dans la conversation. Vous pouvez ajouter une précision ou modifier le texte avant de le transmettre à l’équipe produit.

   ![Relisez le brouillon dans la conversation](envoyer-un-retour-depuis-une-page/03-chatter.png)

8. Lorsque le brouillon exprime bien votre demande, cliquez sur **Envoyer un retour** sur la fiche du brouillon.

9. Le bandeau confirme la transmission à l’équipe produit. Retrouvez ses réponses dans cette conversation.

   ![Le bandeau confirme la transmission à l’équipe produit](envoyer-un-retour-depuis-une-page/04-team_received.png)

10. Cliquez sur **Nouveau** pour préparer un autre retour. Le premier reste accessible.

11. Dans **Mes retours**, retrouvez vos demandes et leur statut.

12. Choisissez une demande pour reprendre la conversation ou ajouter une précision.

13. Revenez dans **Mes retours** pour consulter l’avancement depuis n’importe quelle page.

   ![Revenez dans Mes retours pour consulter l’avancement depuis n’importe quelle page](envoyer-un-retour-depuis-une-page/05-recent_list.png)

---
title: Ce que partage le lien d’une fiche
type: explanation
description: Comprendre le lien direct copié depuis un formulaire et les droits nécessaires pour l’ouvrir.
lang: fr
persona: everyone
app: Home
models:
  - res.partner
  - project.project
  - project.task
source:
  - custom-addons/usl_home/static/src/record_link/copy_record_link.js
  - custom-addons/usl_home/static/src/record_link/copy_record_link.xml
generated: false
---

# Ce que partage le lien d’une fiche

L’icône **Copier le lien** d’un formulaire principal enregistré copie l’adresse
directe de cette fiche. Ce lien désigne seulement la fiche enregistrée. Il ne
transporte ni jeton d’accès, ni filtres, ni action, ni sélection de sociétés.

La personne qui reçoit le lien doit se connecter et disposer des droits natifs
sur la fiche. Copier le lien ne partage aucun droit. Une personne autorisée peut
ouvrir la fiche ; les restrictions de sociétés et de confidentialité restent
celles d’Odoo.

Copier ne sauvegarde ni n’annule les modifications en cours. Le destinataire
voit les valeurs enregistrées auxquelles il a accès. Une nouvelle fiche sans
identifiant enregistré et les formulaires ouverts dans une boîte de dialogue
n’offrent pas cette action.

Si le navigateur refuse l’accès au presse-papiers, Odoo affiche le lien dans une
boîte de dialogue dans un champ en lecture seule, déjà sélectionné et prêt à
être copié au clavier. Cette copie
manuelle conserve les mêmes droits et la même destination.

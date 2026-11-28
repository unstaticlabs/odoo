---
title: Comprendre le contexte et le suivi d’un lot
type: how-to
description: Comprendre le contexte partagé, les dépenses verrouillées, les payeurs et l’archivage d’un lot.
lang: fr
persona: employee
app: Dépenses
models:
  - usl.expense.batch
  - hr.expense
source:
  - custom-addons/usl_expense_batch/models/expense_batch.py
  - custom-addons/usl_expense_batch/static/tests/tours/expense_batch_tour.js
generated: false
---

# Comprendre le contexte et le suivi d’un lot

Pour suivre les étapes, ouvrez les guides
[Créer ou choisir un lot](regrouper-des-depenses-en-lot.md) et
[Vérifier un lot avant de le soumettre](verifier-un-lot-avant-envoi.md).
En anglais, consultez
[Group related expenses](group-expenses-into-a-batch.md) et
[Review a batch](review-a-batch-before-submitting.md).

Un **lot de dépenses** réunit des frais liés et permet de leur donner un contexte
commun. Utilisez-le pour un voyage, une production, un événement, un projet ou
une période cohérente. Une dépense isolée peut toujours suivre le circuit Odoo
normal sans lot.

La **catégorie** décrit ce qui a été acheté : repas, transport, cadeau,
fournitures, kilométrage ou autre nature stable. Le **lot** décrit pourquoi les
dépenses vont ensemble. Pour un voyage SBFH, le lot porte l’activité `SBFH
prod` et l’Epic du voyage ; ne créez plus de catégorie propre au voyage.

## Employé, société et clôture du lot

Un lot concerne toujours un employé et une société. Il peut mélanger les
dépenses payées par l’employé et celles payées par la société.

Le lot reste ouvert lorsque toutes ses dépenses sont comptabilisées ou payées.
Vous pouvez donc y ajouter plus tard une dépense brouillon, approuvée ou déjà
comptabilisée. Archivez manuellement le lot lorsqu’il ne doit plus recevoir de
dépenses ; vous pourrez le rouvrir si nécessaire.

## Appliquer le contexte commun

L’analytique et les informations du lot restent modifiables dans **Contexte
commun** tant que le lot est ouvert. Un responsable peut aussi définir un
compte de charge commun. Sélectionnez **Appliquer le contexte à … notes de frais** pour
prévisualiser les changements, puis confirmez dans la fenêtre. Seules les dépenses
brouillon peuvent recevoir ce contexte : les dépenses approuvées ou
comptabilisées restent inchangées.

L’ordre de priorité est le suivant :

1. choix explicite sur la dépense ;
2. contexte configuré sur le lot ;
3. valeur par défaut de la catégorie ;
4. suggestion non confirmée.

Une valeur identique au lot n’est pas une exception. Une vraie différence est
signalée par un petit triangle. Survolez-le ou placez-y le focus au clavier pour
voir le compte, l’analytique ou l’information qui diffère. Un cadenas indique
qu’une dépense approuvée ou comptabilisée reste volontairement inchangée.

Changer le contexte ne réécrit pas immédiatement les lignes : elles sont
signalées comme à actualiser. Réappliquer le même contexte est sans effet
supplémentaire. Retirer un brouillon du lot ne supprime jamais la dépense et ne
supprime pas une correction explicite.

## Corriger une dépense

**Retirer du lot** détache un brouillon sans le supprimer. **Renvoyer pour
correction** remet une dépense soumise ou approuvée en brouillon et la détache.
Une date hors de la période prévue déclenche un avertissement. Les détails de
rapprochement restent dans **Comptabilité et historique**.

## Soumettre et comptabiliser

Les actions du lot utilisent les étapes Odoo natives : **Soumettre** agit sur
les brouillons, **Approuver** sur les lignes soumises et **Comptabiliser** sur
les lignes approuvées. Une information requise manquante bloque la soumission
avant toute modification partielle.

L’indicateur **Progression des dépenses** résume ces états sans fermer le lot.

Dans un lot mixte, les dépenses personnelles suivent le remboursement et les
dépenses société suivent la comptabilisation puis le rapprochement bancaire.
La fin d’un côté ne masque pas le travail restant de l’autre.

Dans le journal **Notes de frais**, la référence reprend le nom du lot. Les
liens permettent de parcourir :

**écriture → lot de dépenses → dépense → justificatif**.

Les écritures et lignes analytiques restent analysables par lot, Epic,
catégorie, compte, employé, payeur et période. Les anciennes catégories propres
aux voyages restent visibles dans l’historique, mais sont archivées pour les
nouvelles dépenses.

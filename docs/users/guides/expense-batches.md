# Lots de dépenses

Un **lot de dépenses** regroupe des dépenses liées et leur applique un contexte
commun. Utilisez-le pour un voyage, une production, un événement, un projet ou
une période cohérente. Une dépense isolée peut toujours suivre le circuit Odoo
normal sans lot.

La **catégorie** décrit ce qui a été acheté : repas, transport, cadeau,
fournitures, kilométrage ou autre nature stable. Le **lot** décrit pourquoi les
dépenses vont ensemble. Pour un voyage SBFH, le lot porte l’activité `SBFH
prod` et l’Epic du voyage ; ne créez plus de catégorie propre au voyage.

## Créer ou choisir un lot

1. Ouvrez **Dépenses > Mes dépenses** et préparez les justificatifs.
2. Sélectionnez les dépenses liées, puis **Ajouter à un lot de dépenses**.
3. Choisissez un lot proposé ou créez-en un. Les propositions tiennent compte
   de l’employé, de la société, des dates et de l’analytique.
4. Vérifiez le motif, la période prévue, le total et les deux modes de paiement.
5. Ajoutez les dépenses au lot. La soumission reste une action séparée et
   explicite.

Un lot concerne toujours un employé et une société. Il peut mélanger les
dépenses payées par l’employé et celles payées par la société.

Le lot reste ouvert lorsque toutes ses dépenses sont comptabilisées ou payées.
Vous pouvez donc y ajouter plus tard une dépense brouillon, approuvée ou déjà
comptabilisée. Archivez manuellement le lot lorsqu’il ne doit plus recevoir de
dépenses ; vous pourrez le rouvrir si nécessaire.

## Appliquer le contexte commun

L’analytique et les informations du lot restent modifiables dans **Contexte
commun** tant que le lot est ouvert. Un responsable peut aussi définir un
compte de charge commun. Sélectionnez **Appliquer le contexte commun** pour
prévisualiser les changements avant de les confirmer. Seules les dépenses
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

## Contrôler et corriger

La fiche montre d’abord le motif, le total, le nombre de dépenses et de
justificatifs, les dates réelles, la répartition par payeur et la préparation.
La liste conserve la catégorie, le payeur, le statut, le montant et l’accès à
chaque dépense.

- **Retirer du lot** détache seulement un brouillon ;
- **Renvoyer pour correction** remet une dépense soumise ou approuvée en
  brouillon et la détache ;
- un justificatif manquant ou un doublon possible apparaît dans l’aide de la
  ligne ;
- une date en dehors de la période prévue déclenche un avertissement, sans
  empêcher l’ajout, la soumission, l’approbation ou la comptabilisation ;
- les détails de rapprochement et l’historique sont dans **Comptabilité et
  historique**.

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

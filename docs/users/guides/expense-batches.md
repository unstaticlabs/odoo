# Notes de frais

Une **note de frais** regroupe des dépenses liées et leur applique un contexte
commun. Utilisez-la pour un voyage, une production, un événement, un projet ou
une période cohérente. Une dépense isolée peut toujours suivre le circuit Odoo
normal sans note.

La **catégorie** décrit la nature de la dépense : repas, transport et
hébergement, cadeau, fournitures, kilométrage, stationnement, communication ou
forfait. La **note de frais** décrit son motif. Pour un voyage SBFH, la note est
liée à l’activité `SBFH prod` et à l’Epic du voyage ; il n’est plus nécessaire
de créer une catégorie propre au voyage.

## Ajouter des dépenses à une note

1. Ouvrez **Dépenses > Mes dépenses** et préparez chaque justificatif : date,
   description, montant, catégorie, mode de paiement et pièce jointe.
2. Sélectionnez les dépenses liées. Pour plusieurs lignes, **Ajouter à une
   note de frais** est l’action principale ; les actions Odoo normales restent
   disponibles.
3. Dans l’aperçu, choisissez une note proposée ou créez-en une. Les propositions
   tiennent compte de l’employé, de la société, des dates et de l’analytique.
   Un avertissement signale une note proche afin d’éviter les doublons.
4. Vérifiez le motif, les dates prévues, les totaux payés par l’employé et par
   la société, la préparation et l’impact du contexte partagé.
5. Choisissez **Ajouter à la note** pour regrouper sans soumettre, ou
   **Créer et soumettre** lorsque toutes les dépenses en brouillon sont prêtes.

Une note ne peut concerner qu’un employé et une société. Elle peut mélanger les
dépenses payées personnellement et celles payées par la société.

## Comprendre les valeurs partagées et les exceptions

La note peut fournir l’analytique, une justification commune et, lorsqu’un
responsable le configure, un compte de charge commun. À l’ajout d’une dépense,
ces valeurs remplacent seulement une valeur manquante ou issue de la catégorie.

Une valeur choisie volontairement sur la dépense reste prioritaire et apparaît
comme **Exception explicite**. Elle n’est jamais normalisée en silence. Si le
contexte de la note change, les lignes déjà héritées sont signalées comme
**Contexte à actualiser**. Utilisez **Aperçu de l’application du contexte** :
l’écran annonce les lignes modifiées, inchangées, conservées comme exceptions
et ignorées parce qu’elles sont déjà à un stade ultérieur.

Seul un responsable Dépenses ou Comptabilité peut remplacer volontairement
une exception ou définir le compte général commun. Réappliquer le même contexte
ne duplique pas l’analytique. Retirer une ligne restaure son ancienne valeur
seulement si elle n’a pas été modifiée depuis ; une correction explicite est
conservée.

## Contrôler la note

La fiche présente d’abord le motif, le total, la période réelle, la répartition
par payeur, la préparation, les exceptions et le travail restant. L’onglet de
contrôle ajoute :

- la répartition par catégorie de dépense ;
- l’activité et l’Epic, regroupées par plan analytique ;
- les justificatifs manquants et les doublons possibles ;
- les dépenses hors de la période prévue ;
- le rapprochement entre les dépenses et les écritures générées.

Un doublon possible est un avertissement, pas un blocage automatique. Ouvrez
les deux dépenses et leurs justificatifs pour décider. Une ligne incorrecte
peut être ouverte, corrigée ou retirée sans refuser toute la note.

## Soumettre, approuver et comptabiliser

Les actions de la note utilisent les étapes Odoo natives :

- **Soumettre** agit uniquement sur les brouillons prêts ;
- **Approuver** agit uniquement sur les lignes soumises ;
- **Comptabiliser** agit uniquement sur les lignes approuvées.

Une information ou un justificatif requis manquant bloque atomiquement les
brouillons : aucune partie n’est soumise à moitié. Les dépenses déjà approuvées
ou comptabilisées ne reviennent pas en arrière et leur comptabilité n’est pas
réécrite par le contexte de la note.

Pour une note mixte, Odoo conserve les traitements nécessaires : les dépenses
payées par l’employé suivent le remboursement et celles payées par la société
suivent la comptabilisation et le rapprochement bancaire. Les deux compteurs
restent visibles. La fin du traitement société ne masque donc pas un
remboursement encore attendu, notamment si l’assistant de comptabilisation de
l’employé a été annulé.

## Retrouver la comptabilité et analyser

Dans le journal **Notes de frais**, la référence reprend le nom de la note.
Les liens permettent de parcourir :

**écriture → note de frais → dépense → justificatif**.

Les écritures et lignes analytiques peuvent être filtrées ou regroupées par
note et par payeur, en plus du Produit, du compte, de l’employé, de la période,
de l’activité et de l’Epic. Cela permet de connaître à la fois le coût total
de `Canada 2026` et sa composition par repas, transport ou cadeaux.

Les anciennes catégories propres aux voyages restent visibles sur l’historique
mais sont archivées pour les nouvelles dépenses. Utilisez désormais une
catégorie réutilisable pour la nature et une note de frais pour le voyage.

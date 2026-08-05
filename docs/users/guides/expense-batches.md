# Notes de frais

Une **note de frais** regroupe des dépenses liées sans remplacer leurs
justificatifs ni leurs détails individuels. Utilisez-la pour un déplacement,
une mission, un projet ou une période cohérente.

## Comprendre la liste des dépenses

La liste **Dépenses > Mes dépenses** reste centrée sur chaque dépense. Elle
affiche le **Statut du justificatif** (**Joint**, **Manquant** ou **Non
requis**), la **Note de frais** associée, lorsqu'elle existe, et le statut
normal de la dépense. Elle n'affiche pas de colonne permanente **Préparation
de la note**.

Le filtre **Hors note de frais** est activé par défaut. La liste de travail
affiche ainsi uniquement les dépenses qui peuvent encore être regroupées.
Retirez ce filtre pour retrouver les dépenses déjà incluses dans une note.

Utilisez plutôt les filtres **Prête à soumettre**, **Informations requises**
et **Déjà dans une note de frais**. Le contrôle détaillé de préparation
apparaît ensuite dans l'aperçu de création, avant tout enregistrement ou
soumission.

## Préparer et soumettre une note

1. Ouvrez **Dépenses > Mes dépenses**.
2. Utilisez les filtres **Prête à soumettre**, **Informations requises** et
   **Déjà dans une note de frais** pour vérifier vos dépenses.
3. Sélectionnez les dépenses liées puis cliquez sur
   **Créer une note de frais**. Ce bouton apparaît lorsque vous avez
   sélectionné une ou plusieurs dépenses admissibles qui ne figurent encore
   dans aucune note : **Brouillon**, **Approuvée** ou **Comptabilisée**.
4. Vérifiez le nom, l’objet, la période, les totaux payés par l’employé et par
   la société, l’activité analytique et la préparation globale. Pour chaque
   ligne, vérifiez le statut du justificatif, le statut de la dépense et, si
   nécessaire, les **Informations manquantes**.
5. Retirez une ligne si elle n’appartient pas à cette demande.
6. Choisissez l’action adaptée :

   - **Créer la note** enregistre seulement le regroupement et ne change aucun
     statut de dépense ;
   - **Soumettre la note de frais** crée le regroupement puis soumet uniquement
     ses dépenses en brouillon au responsable.

Après l’une ou l’autre action, l’aperçu se ferme et la liste se met à jour.
Avec le filtre **Hors note de frais**, les dépenses qui viennent d’être
regroupées disparaissent immédiatement de la liste de travail.

**Prête à soumettre** signifie que la description, la catégorie, le montant et
le justificatif éventuellement requis sont présents. **Informations
requises** indique ce qu'il faut corriger sur la dépense avant de pouvoir
soumettre la note.

Une dépense déjà **Approuvée** ou **Comptabilisée** conserve son statut lorsque
vous soumettez la note. Cette action ne comptabilise aucune écriture et ne crée
aucun paiement. Une dépense **Soumise**, **En cours de paiement**, **Payée**,
**Refusée** ou déjà présente dans une autre note ne peut pas être ajoutée.

L’ancien raccourci **Soumettre les dépenses prêtes** n’existe plus. Vous devez
toujours choisir explicitement les dépenses puis utiliser **Créer une note de
frais** ; Odoo ne sélectionne pas automatiquement tous vos brouillons prêts.

Une note ne peut contenir que les dépenses d’un seul employé et d’une seule
société. Elle peut combiner des dépenses payées par l’employé et par la
société : Odoo conservera leurs traitements comptables distincts.

## Contrôler une note

Le responsable ouvre **Dépenses > Notes de frais** pour
contrôler l’objet commun, le total, la période, l’analytique et les
justificatifs. La préparation globale apparaît en haut de la note ; les lignes
affichent le **Statut de la dépense** et le **Statut du justificatif**, sans
colonne de préparation redondante.

**Approuver la note de frais** approuve uniquement les dépenses encore
**Soumises**. Les dépenses déjà approuvées ou comptabilisées ne reviennent
jamais à un statut antérieur.

Une ligne incorrecte peut être ouverte ou retirée avec **Retirer**. Elle
revient alors en brouillon pour correction, tandis que les autres lignes
restent dans la note et poursuivent le circuit.

## Retrouver la note en comptabilité

La comptabilisation utilise le nom de la note comme référence visible dans le
journal **Notes de frais**. Chaque dépense reste une ligne distincte. Les liens
permettent de parcourir :

**écriture comptable → note de frais → dépense → justificatif**.

Odoo regroupe les dépenses remboursables compatibles et conserve une écriture
distincte pour chaque dépense payée par la société.

## Rapprocher une dépense avec le compte courant d'associé

Pour une dépense payée personnellement par Valentin, Odoo utilise le
**Compte fournisseur** de sa **Coordonnée professionnelle**. Cette coordonnée
doit être le même contact que celui du compte utilisateur Valentin et des
écritures du compte `455100 — Associés - Comptes courants - Valentin`.

Après comptabilisation, les débits CCA non rapprochés peuvent alors apparaître
dans **Débits en circulation** sur l'écriture de la note de frais. Choisissez
un débit pour compenser tout ou partie de la somme due.

Si aucun débit CCA n'est proposé, demandez à un responsable comptable de
vérifier :

1. que l'écriture de la note utilise le compte `455100`;
2. que l'écriture et le débit CCA utilisent exactement le même contact
   Valentin ;
3. que le débit CCA est comptabilisé, non rapproché et positif.

Le paramètre se trouve sur le contact Valentin, onglet
**Comptabilité/Facturation**, et non sur le journal Notes de frais. Une
correction de configuration ne modifie pas rétroactivement une écriture déjà
comptabilisée.

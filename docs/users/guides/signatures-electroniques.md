# Préparer et suivre une signature électronique

L’application **Signature** conserve dans Odoo le document, les signataires,
les consentements, l’état, les contrôles et les preuves. Utilisez d’abord
l’approbation habituelle de la fiche métier lorsqu’une décision attribuable
suffit et qu’aucun PDF signé n’est nécessaire.

## Choisir le parcours

Odoo recommande un parcours et explique la raison et les conséquences avant
l’envoi :

- **Signature électronique standard avec preuve renforcée** : parcours normal
  pour les NDA, devis, bons de commande, accusés de réception et accords
  courants. Le lien individuel, le consentement, le dossier de preuves et le
  sceau de la plateforme renforcent la preuve, sans constituer une signature
  personnelle avancée ou qualifiée.
- **Signature personnelle forte — conçue pour les exigences de signature
  avancée** : pour un salarié, prestataire ou partenaire récurrent dont
  l’identité a été contrôlée et qui a enregistré une clé d’accès. Face ID,
  Touch ID, Windows Hello ou un mécanisme équivalent autorise une signature
  personnelle liée au document exact.
- **Signature qualifiée externe** : uniquement lorsqu’une QES est formellement
  exigée ou que le risque impose l’assurance maximale. Le signataire suit les
  instructions d’un prestataire examiné, puis Odoo contrôle le résultat
  importé.

Le niveau demandé et le niveau réellement atteint restent visibles
séparément. Un utilisateur autorisé peut déroger à la recommandation avec une
justification. Odoo ne réduit jamais le niveau demandé sans le signaler.

## Créer un modèle ou une demande ponctuelle

1. Ouvrez **Signature → Modèles**, ou créez une demande ponctuelle depuis une
   fiche métier.
2. Ajoutez le PDF principal et, si nécessaire, les annexes.
3. Placez les champs depuis la palette, choisissez leur rôle, leur caractère
   obligatoire et contrôlez chaque page avec les miniatures et le zoom.
4. Ajoutez les signataires dans l’ordre voulu. Activez l’ordre de signature si
   chacun doit signer la révision créée par le précédent.
5. Contrôlez la recommandation, l’échéance, les relances, le consentement et la
   prochaine action.
6. Passez à **Prête**, puis envoyez.

Un modèle publié ou déjà utilisé n’est plus modifiable. Créez une nouvelle
version pour changer le PDF, les rôles ou les champs. La demande envoyée
conserve toujours un instantané exact du modèle, des signataires, de la
politique et du consentement.

## Signer en Standard

Le signataire ouvre son lien individuel sur ordinateur ou mobile. Le lien est
échangé contre une session courte et ne peut pas servir pour une autre
personne ou un autre document. Selon la politique, le signataire peut aussi
s’authentifier avec son portail ou Pocket ID.

Après lecture, il renseigne les champs, accepte explicitement le consentement
et signe. Odoo enregistre la méthode d’authentification, la date, l’adresse IP,
le navigateur et les valeurs saisies. Le document final est scellé et contrôlé
indépendamment avant toute attestation de fin.

## S’inscrire et signer avec une clé d’accès

La signature personnelle forte n’est proposée qu’après inscription :

1. un contrôleur d’identité confirme le lien existant avec USL et sa politique
   de contrôle ;
2. le signataire ouvre l’invitation d’inscription sur une page isolée ;
3. il enregistre une clé d’accès avec vérification de l’utilisateur ;
4. il est invité à enregistrer une deuxième clé d’accès de récupération.

Lors de la signature, le téléphone ou l’ordinateur demande Face ID, Touch ID,
Windows Hello ou l’équivalent. Une clé privée à usage unique est créée dans le
navigateur pour ce document. Elle ne peut pas être exportée et n’est jamais
envoyée à Odoo. Une clé d’accès perdue doit être déclarée perdue ou révoquée ;
une nouvelle inscription n’altère pas les signatures déjà terminées.

## Suivre une signature qualifiée externe

Odoo prépare et gèle le PDF exact, puis affiche le prestataire conseillé, les
informations du signataire et des instructions adaptées au mobile. Le statut
reste **En attente de signature externe** tant que le résultat n’est pas
revenu.

Importez ensuite le PDF signé et toutes les preuves fournies. L’import ne
termine pas la demande : Odoo vérifie d’abord que la révision d’origine est
strictement celle exportée, que la chaîne de certificats est approuvée, que le
signataire correspond et que le niveau qualifié a réellement été atteint. Un
résultat modifié, non fiable, mal attribué ou insuffisant passe en **Échec de
validation**.

## Comprendre les états et agir

La demande distingue notamment **Brouillon**, **Prête**, **Envoyée**,
**Consultée**, **Partiellement signée**, **En attente d’inscription**, **En
attente de signature externe**, **Document signé à importer**, **Validation en
cours**, **Preuves incomplètes**, **Échec de validation**, **Terminée** et
**Action requise**. Les états **Refusée**, **Expirée** et **Annulée** décrivent
une fin sans signature réussie.

La fiche métier indique toujours la prochaine action. Une demande n’est
**Terminée** que si toutes les signatures attendues sont présentes, si les
contrôles sont réussis, si le dossier de preuves est complet et si Paperless a
confirmé l’archivage. Un courriel envoyé, un fichier exporté ou importé, ou la
déclaration d’un signataire ne suffit jamais.

## Contrôler les preuves et résoudre un échec

Le dossier Odoo contient les documents source et final, les empreintes
SHA-256, les rôles et champs gelés, les consentements, la chaîne d’événements,
les certificats, les horodatages disponibles, les rapports de validation et
l’attestation de fin. Paperless reçoit un dossier PDF/A-3 unique avec ces
éléments embarqués.

En cas d’échec Paperless, utilisez **Réessayer l’archivage** : l’opération est
idempotente et reconnaît un dossier déjà présent avec la même empreinte. En cas
de désaccord de validation, de service indisponible ou de preuve incomplète,
suivez la prochaine action et contactez un contrôleur des preuves. Ne remplacez
jamais manuellement un PDF ou une empreinte, et ne transmettez pas un lien de
signature à une autre personne.

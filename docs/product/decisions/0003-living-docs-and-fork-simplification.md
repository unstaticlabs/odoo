# 3. Une documentation vivante qui pilote la simplification du fork

Date : 2026-11-08
Statut : proposé (plan de livraison soumis à Valentin)

## Conclusion

Le chantier des PR #206 à #209 est sain dans son architecture mais mort dans sa
forme : les quatre branches ont deux mois de retard sur `19-usl-staging`
(1 809 commits), la première est en conflit, et aucune ne peut être rebasée
telle quelle sans réécrire le travail. La bonne livraison n'est pas un rebase :
c'est **une reprise sur staging d'aujourd'hui, en deux PR livrées dans un seul
cycle de staging**, qui reprend 90 % du code des quatre PR, corrige trois choix
de conception qui gênaient la modularité, et ajoute ce qui manquait (MCP,
surface agents, UX du guide). Trois vagues suivent, chacune livrable
indépendamment et en parallèle par des sessions distinctes.

Le principe directeur, inchangé depuis la décision 0002 du 8 septembre : **la
documentation est générée par les tests du produit partout où un test peut la
porter, et chaque page dit ce qui la prouve.** Ce qui change : la documentation
devient *propriété de chaque module* plutôt qu'un dossier central, ce qui est
la réponse à « des développements parallèles qui ne se marchent pas dessus ».

## Ce que les audits ont établi (faits, pas opinions)

**La pile de PR.** Un seul auteur (`elio-usl`), quatre brouillons empilés,
12 000 lignes ajoutées dont 6 500 de référence générée. Fusionnée d'un bloc sur
staging aujourd'hui, elle produit 10 conflits : `Makefile`,
`qualification.yml`, `product-image.yml`, deux manifestes de modules,
`action_surface.json` (rescellé 20 fois depuis), `docs/users/README.md`,
`requirements-docs.txt` (Dependabot l'a touché le 1er novembre), et la
numérotation des décisions (staging a pris le 0002 le 14 octobre pour les
aperçus de liens ; la nôtre devient 0003). Le harnais (`journey.py`,
`journey_runner.js`) repose sur `odoo.startTour`, `ChromeBrowser._handle_console`
et `Page.captureScreenshot`, tous encore présents dans l'Odoo d'aujourd'hui.

**Le produit.** 21 modules, aucun cycle de dépendances, 31 tours de navigateur
dans 10 modules (aucun n'a encore d'`id` de pas), 2 855 champs stockés dont 7,4 %
portent un `help=`. Les quatre registres `PRODUCT_MODULES` sont identiques entre
eux mais **déjà faux** : `usl_document_templates` n'y figure pas alors que trois
modules en dépendent, et `usl_mattermost` y figure sans exister dans l'arbre.
`usl_bootstrap` est un module de test que cinq fichiers excluent un par un.
Le cœur Odoo n'est modifié que sur 41 fichiers ; une partie (`api_doc`,
`l10n_fr_pdp`, `mrp_account`, la barre de navigation) n'est pas dans la liste
des divergences autorisées par `fork-overview.md`, et `odoo/http/session.py`
porte un bloc de trace qui se déclare lui-même temporaire.

**La documentation.** 200 pages Markdown (20 000 lignes) et 239 fichiers
binaires (24 Mo) ; 56 pages sont des comptes rendus datés, 23 pages ne sont
référencées nulle part, 57 images ne sont référencées par aucune page.
`docs/users` : 41 pages, aucune n'a de front matter, 4 en français, six dans un
`guides/` qui double `how-to/`. Trois index coexistent (`README.md`,
`mkdocs.yml` que rien ne construit, la liste du contrôleur). Les six pages de
référence manuscrites portent du *sens* (ce que veut dire « Partiellement payé »,
ce qu'un rôle peut faire) qu'un générateur de registre ne produira jamais : elles
restent. Dix how-to peuvent être générés depuis des tours existants avec pour
seul coût une entrée de registre et des `id` de pas.

**La chaîne de livraison.** De la fusion sur staging au déploiement en
production, tout est automatique. Le manifeste de release accepte déjà une
évidence nommée librement (`--evidence docs=<sha>`), il est injecté dans le
conteneur Odoo au déploiement (`USL_RELEASE_MANIFEST_STDIN`), et `usl_feedback`
lit déjà `usl.release.commit`. **Aucun changement gitops n'est nécessaire** pour
que le guide dise « Testé il y a 3 heures » en production. La qualification d'un
push staging dure 10 minutes ; `scripts/tests` tourne en 56 secondes.

## Décision (architecture cible)

### 1. Un module `usl_docs`, un guide central, des parcours dans chaque module

Valentin a tranché le 8 novembre : les pages restent **centralisées sous
`docs/users/`** (lisibilité d'un seul arbre, une seule copie dans l'image).
La modularité vient d'ailleurs :

- `usl_docs` possède le *moteur* : contrôleur, gabarits QWeb (plus de HTML
  construit en Python : le contrôleur de la PR #209 fait 932 lignes dont 300 de
  CSS et JS inline), un bundle d'assets propre, le harnais de parcours
  (`JourneyCase`, `journey_runner.js`), le modèle `usl.docs.guide` (recherche et
  lecture, appelables par les agents), les générateurs.
- Chaque module produit possède **ses** parcours (le tour et son entrée
  `usl_docs.journeys`, dans `static/tests/tours/`) et son test `JourneyCase`.
  Le générateur écrit la page dans `docs/users/how-to/<slug>.md`, ses captures
  et son sidecar dans `docs/users/how-to/<slug>/`. Deux développements
  parallèles sur deux modules ne touchent donc jamais le même fichier.
- La référence générée est découpée par module
  (`docs/users/reference/generated/<module>/…`) pour la même raison.
- Les index ne sont plus des fichiers : la navigation, `llms.txt`, `index.json`
  sont dérivés à la requête du front matter des pages (cache par worker).
  `docs/users/README.md` ne contient que le texte d'accueil. Un index engagé qui
  périme était la seule source de conflit restante entre deux PR de contenu :
  elle disparaît.

### 2. Quatre types, quatre sources

| Type Diátaxis | Source | Contrôle |
|---|---|---|
| Tutoriel, how-to | parcours = tour `web_tour` + entrée `usl_docs.journeys` | `scripts/docs generate --check` en CI : page et captures doivent correspondre au tour (comparaison par pixels, tolérance) |
| Référence (champs, états, rôles, menus, réglages) | registre Odoo, par module | `scripts/docs reference --check` en CI |
| Référence MCP (outils, profils, authentification) | catalogue exporté du commit `odoo-mcp` épinglé dans `deploy/odoo-mcp/release.json` | vérifié contre l'épingle dans le job `external` déjà existant |
| Explication, sens des états et des rôles | manuscrit, avec front matter et `source:` | `scripts/docs check` : front matter, type = répertoire, liens, sources existantes |

Un seul point d'entrée `scripts/docs` (sous-commandes `check`, `generate`,
`reference`, `mcp`, `evidence`) au lieu de cinq scripts, et `make docs` /
`make docs-check`.

### 3. La fraîcheur vient de la qualification, jamais de git

Inchangé depuis 0002 : la qualification rejoue tous les parcours, écrit
`docs-evidence.json`, le manifeste de release l'embarque, le déploiement le
pousse dans Odoo, le guide affiche « Testé il y a N heures » et la page de
dossier de test (commit, run, pas, assertions, captures, liens GitHub). Le mot
« preuve » n'apparaît pas à l'écran.

### 4. Le guide est natif, contextuel et lisible par les agents

- **Natif** : rendu QWeb, jetons de `DESIGN.md`, navigation par application
  puis par type, filtre par persona, bascule FR/EN, mode sombre, impression,
  mobile d'abord. Pas de bibliothèque JS ; moins de 200 lignes de script.
- **Contextuel** : chaque page déclare `models:` et `actions:` ; le menu Aide
  d'Odoo ouvre la page qui correspond à l'écran courant ; chaque how-to généré
  porte un bouton « Ouvrir cet écran » qui mène à l'action réelle (le parcours
  connaît son URL de départ).
- **Agents** : `?format=md` et `Accept: text/markdown`, `llms.txt`,
  `llms-full.txt`, `index.json`, `?format=json` par page (pas, captures,
  sources, date du dernier test) ; `usl.docs.guide.search(query, lang)` et
  `.read(path)` appelables via l'API JSON-2 donc via l'outil générique
  `odoo_call_method` du MCP dès la vague 1, puis un outil `docs_search` natif
  dans `odoo-mcp` en vague 3. `docs/README.md` cartographie, `CLAUDE.md`
  importe `AGENTS.md`, la compétence `usl-docs-authoring` vit dans
  `.agents/skills/` (convention du dépôt) et est nommée dans `AGENTS.md`.
- **MCP** : un how-to « Connecter un agent (MCP) » (Mes Agents, clé ou OAuth,
  en-têtes, profils) et une référence générée des outils et profils.

### 5. La documentation pilote la simplification du dépôt

Chaque suppression ci-dessous est justifiée par l'audit (rien ne la référence)
et par le principe « un seul rendu, une seule source » :

- MkDocs disparaît : `mkdocs.yml`, `requirements-docs.txt`, trois cibles
  `user-docs-*`, le commentaire Dependabot.
- `DECISIONS/` rejoint `docs/product/decisions/` ; `AGENTS.md` et
  `fork-overview.md` suivent.
- Les 56 comptes rendus datés et les dossiers de captures de PR (236 binaires,
  24 Mo) vont dans `docs/archive/` ; les 57 images orphelines sont supprimées ;
  les 8 revues action-risk restent en place parce que `action_policy.json` les
  cite. Les 23 pages orphelines sont archivées.
- Un seul registre des modules produit, dérivé de `custom-addons/*/__manifest__.py`
  (`operations/module_release.build_inventory` existe déjà) ; les deux copies
  côté shell Odoo reçoivent la liste par l'environnement ; un test unitaire
  interdit toute nouvelle copie. La dérive actuelle est corrigée au passage.
- `usl_bootstrap` sort de `custom-addons/` (cinq exclusions disparaissent).
- Le cœur Odoo : la modification de pure mise en forme de
  `sale_order_template_line_field.js` est annulée ; `fork-overview.md` liste
  honnêtement les divergences réelles ; le bloc de trace de `session.py` est
  retiré si Valentin confirme que la cause est nommée.
- `README.md` redevient court et renvoie à `docs/README.md` ; les listes de
  frontières dupliquées entre `README.md` et `AGENTS.md` n'existent plus qu'une
  fois.

### Ce qui n'est pas décidé ici

Renommer `rebuild_account_migration` (524 XML IDs, 3 400 occurrences dans la
surface scellée, lignes `ir_model_data` en production) : non, le coût dépasse le
gain, et le nom est documenté comme historique. Replier les micro-modules
(`usl_tese_accounting`, `usl_platform_billing_pocketid`, `usl_documents_b2c`,
`usl_documents_accounting`) : chacun demande une migration de désinstallation
sur une base existante ; proposé en vague 3 au cas par cas, pas maintenant.

## Plan de livraison

Chaque vague est un lot pour la file de fusion de staging (au plus cinq PR par
cycle, un cycle à la fois). La promotion en production est automatique.

### Vague 1 : le socle (un cycle de staging, deux PR, cette semaine)

**PR A `feat/living-docs` : le moteur.** Reprise des PR #206 à #209 sur
staging, réécrite selon l'architecture ci-dessus :

1. Module `usl_docs` : contrôleur + QWeb + assets, front matter, agrégation
   multi-racines, `llms.txt` / `index.json` / Markdown / JSON, modèle
   `usl.docs.guide`, aide contextuelle, harnais de parcours, page de dossier de
   test. `rebuild_account_migration` perd le contrôleur (shim de réexport pour
   le test de restauration).
2. `scripts/docs` (check, generate, reference, mcp, evidence) +
   `scripts/tests/test_docs.py` ; `make docs`, `make docs-check`.
3. CI : `docs check` dans le job `compatibility` ; parcours + `generate --check`
   + `reference --check` + `docs evidence` dans le job `database` ;
   `product-image.yml` embarque l'évidence dans le manifeste.
4. Front matter sur les 41 pages, `guides/` fusionné dans `how-to/`, décisions
   déplacées (0003 = cette décision), MkDocs supprimé, `docs/README.md`,
   `CLAUDE.md`, compétence `usl-docs-authoring`, `AGENTS.md`.
5. Référence générée par module (champs, états, rôles, menus, réglages,
   couverture `help=`) dans `<module>/docs/reference/`.
6. MCP : how-to de connexion + référence exportée du commit épinglé.
7. Parcours pilote : `usl_expense_batch_create_or_select` (déjà écrit).
8. Rescellement action-risk depuis une découverte runtime ; captures réelles
   (bureau et mobile) de l'accueil, d'un how-to généré, du dossier de test, de
   la référence, de l'aide contextuelle.

**PR B `feat/living-docs-journeys-wave-1` (empilée sur A) : le contenu.**
Les dix how-to générables depuis des tours existants, par module : retour
produit (bureau et mobile), lots de dépenses (six tours), encaissements
plateformes (quatre tours), règlement immédiat, réception e-facture (deux
tours), correction de période bancaire (nouveau how-to). Chaque tour reçoit ses
`id` ; les `expectUnloadPage` sont remplacés par une navigation dans le client
web. Les pages manuscrites remplacées sont supprimées ; les pages françaises
restent `lang: fr` jusqu'aux parcours français.

Critère de fin : staging déployé, `/usl/user-docs` ouvert depuis le menu Aide
montre « Testé il y a N minutes » sur les pages générées, la promotion nocturne
passe, puis surveillance à +5, +15, +60, +120 minutes.

### Vague 2 : la simplification du dépôt (en parallèle de la vague 1, sessions séparées, petites PR)

Indépendantes du moteur, chacune livrable seule : registre unique des modules
produit (+ correction de la dérive) ; `usl_bootstrap` hors de `custom-addons/` ;
archivage des comptes rendus et suppression des images orphelines ; `README.md`
court et `fork-overview.md` honnête ; annulation de la retouche de mise en forme
du cœur ; retrait du bloc de trace de `session.py` (si confirmé) ;
`scripts/odoo-dev` lit l'inventaire au lieu d'une liste en dur.

### Vague 3 : la profondeur (cycles suivants)

Export du catalogue d'outils dans `odoo-mcp` + outil `docs_search` + bump de
l'épingle ; parcours français (le harnais rejoue un parcours dans la langue de
l'utilisateur, captures incluses) ; tutoriel généré depuis `usl_home_core_journey`
et le parcours comptable ; reçus liés et e-facture en production ; page « Quoi
de neuf » servie depuis les notes de release déjà présentes dans le manifeste ;
remontée du `help=` module par module, le chiffre étant désormais une page du
guide ; replis de micro-modules au cas par cas.

## Conséquences

- Tout parcours tourne à chaque qualification ; un changement dans un module
  peut casser un parcours d'un autre module, et l'échec nomme le parcours et le
  pas. C'est le prix de « toujours testé ». Le job `database` passe d'environ
  6 à 10 minutes.
- Une page générée n'est jamais éditée à la main ; on change le tour ou le
  registre. Le contrôle CI le garantit.
- Une mise à niveau de Chromium dans l'image de base re-baseline beaucoup de
  captures d'un coup : une PR attendue, pas un défaut.
- Un nouveau module n'a plus que son manifeste à écrire pour exister dans tous
  les registres et dans le guide.

## Alternatives écartées

- **Rebaser les quatre PR une à une.** Dix conflits connus, deux mois de dérive
  dans `action_surface.json`, et trois choix de conception à défaire de toute
  façon (HTML en Python, pages centralisées, cinq scripts). Plus long et moins
  bon qu'une reprise.
- **Garder les pages dans `docs/users/`.** Simple à lire, mais deux
  développements parallèles touchant des modules différents se disputent le
  même dossier et le même index. La propriété par module est aussi celle de
  `help=`, des tours et des tests.
- **Un site de documentation externe (MkDocs, Docusaurus).** Déjà écarté en
  0002 : un deuxième rendu que rien ne construit et un troisième index.
- **Un serveur MCP de documentation séparé.** Contraire à l'invariant
  d'`odoo-mcp` (un seul registre de capacités). Le guide est lisible par l'outil
  générique dès la vague 1, puis par un outil natif.
- **Renommer `rebuild_account_migration` maintenant.** Voir ci-dessus.

# Libris

![Libris](../frontend/public/assets/libris-logo.png)

Atelier de traduction littéraire self-hosted : **EPUB → analyse → mémoire du livre → traduction contextuelle → relecture → EPUB**.

React / TypeScript strict, FastAPI, PostgreSQL, worker Python persistant, Docker Compose. Le modèle de traduction utilise une API OpenAI-compatible. **OpenViking** est le moteur externe facultatif de mémoire ; les modes disponibles sont `internal`, `openviking` et `hybrid`.

## Démarrage

Prérequis : Linux AMD64, Git, Docker Engine et Docker Compose v2. Python n’est pas obligatoire.

```bash
git clone https://github.com/HeartBtz/Libris.git
cd Libris
./scripts/install-docker.sh
```

Le script crée `.env` avec permissions `0600`, télécharge l’image Docker publiée et refuse d’écraser une configuration existante. Ouvrir <http://127.0.0.1:8088>. Compte initial : `BOOTSTRAP_USERNAME`, mot de passe : `BOOTSTRAP_PASSWORD` dans `.env`. Le [guide Docker](docker.md) explique chaque étape, les sauvegardes et les mises à jour.

Pour rendre l’application accessible sur le LAN, définir `BIND_ADDRESS` à l’IP du serveur et ajouter son origine complète à `ALLOWED_ORIGINS`. Derrière un reverse proxy HTTPS, ajouter son origine et activer `COOKIE_SECURE=true`.

Le compte initial est créé seulement lorsque la base ne contient aucun utilisateur. Changer `BOOTSTRAP_PASSWORD` ensuite ne réinitialise pas un compte existant.

### Stockage

- Volume `books` monté sur `/data` : `/data/books` (EPUB originaux), `/data/sources` (chapitres TXT et payloads JSON), `/data/staging` (fichiers d’imports non confirmés, temporaires), `/data/projects`, `/data/exports`.
- Volume `database` : données PostgreSQL, textes, traductions, versions, mémoire, paramètres chiffrés, jobs et traces.
- `.env` : clé de chiffrement de l’application et identifiants de déploiement.

Les traductions sont enregistrées immédiatement en SQL ; le volume `/data/projects` est prévu pour des artefacts supplémentaires, il ne remplace pas la sauvegarde SQL. **Sauvegarder PostgreSQL, `/data` et la clé `SECRET_KEY` ensemble.** Un export de projet est portable, mais ne contient ni comptes, ni mots de passe, ni clés de providers.

## Premier livre

Pour les commandes par lot, les deux progressions, les interruptions, les refus et le graphe : [guide d’exploitation](operations.md).

1. Dans **Paramètres → Providers LLM**, renseigner la base URL (incluant `/v1`), le modèle et éventuellement la clé. Le bouton de test interroge `/models`. Les capacités JSON et reasoning restent configurables.
2. Importer un EPUB dans la bibliothèque (bouton **Importer des EPUB** ou glisser-déposer des fichiers sur la page). La structure, les ressources et le texte sont analysés ; EPUBCheck est exécuté dans l’image Docker.
3. Dans l’onglet **Réglages** du livre, choisir le provider, les langues, le mode qualité et les instructions globales. Le choix du premier provider lance l’analyse puis la traduction. Utiliser des codes de langue BCP 47, par exemple `en`, `fr`, `ja`.
4. **Analyser le livre** (le bouton principal de l’en-tête du livre propose toujours l’étape suivante). Une confirmation affiche l’estimation de tokens et de coût du serveur lorsqu’elle est disponible. Chaque unité est analysée, puis les résultats sont consolidés par chapitre en Book Bible. L’historique d’analyse reste consultable.
5. Examiner et corriger la **Book Bible**, les personnages et les propositions de glossaire. Les entrées proposées ne sont pas acceptées automatiquement par défaut. Le glossaire s’exporte et s’importe en JSON, CSV ou TBX (format d’échange des outils de traduction). L’import reconnaît le format au contenu, accepte les CSV de tableur (séparateur `;` ou `,`, en-têtes français ou anglais comme « Terme source ; Traduction ») et ne remplace jamais un terme déjà présent. En TBX, un terme verrouillé est « preferred », un terme accepté « admitted » et une proposition non acceptée « deprecated ».
6. **Traduire**. Le suivi se reconnecte automatiquement. Pause, reprise et retry conservent les traductions enregistrées.
7. Comparer, corriger et valider dans l’éditeur (`Ctrl`/`⌘`+`S` enregistre, `Ctrl`/`⌘`+`Entrée` valide). Le texte source affiche la mise en forme du livre ; dans la traduction, les repères `⟦t0⟧…⟦/t0⟧` apparaissent en marques discrètes (‹ ouvre une mise en forme, › la ferme, un petit carré signale une image ou une note ; le survol les nomme) et doivent rester en place : leur suppression est signalée puis refusée par le serveur. Quitter un onglet ou la page avec une traduction non enregistrée demande confirmation.
8. Exporter depuis le menu **Exporter** du volume (voir [Exports](#exports)). L’archive de projet est une sauvegarde complète du travail (statuts, validations, historique, critiques, glossaire, personnages, travaux) ; les membres, le provider et le propriétaire ne sont pas restaurés — voir [Archive de projet](operations.md#archive-de-projet).

### Exports

Le menu **Exporter** propose les formats adaptés à la source du volume :

- **Volume EPUB** : EPUB traduit, Texte, Markdown, Book Bible JSON, Projet complet (.zip), et **EPUB partiel · originaux conservés**.
- **Volume TXT ou JSON** : **Chapitres (.zip, un fichier par chapitre)**, **Texte consolidé (.txt)**, Markdown, Book Bible JSON, Projet complet (.zip). L’export EPUB n’existe pas pour ces volumes.

Le ZIP de chapitres contient un fichier UTF-8 par chapitre (`chapters/001 - Titre.txt`, dans l’ordre de lecture, numéros complétés de zéros) et `manifest.json` (titre, série, numéro de volume, langues, et pour chaque chapitre son numéro, son titre, son empreinte SHA-256 et s’il est complet). Chaque fichier garde la mise en page de la source : paragraphes, lignes, lignes vides, indentation, séparateurs de scène ; aucun repère interne de Libris n’y figure. Le texte consolidé réunit tous les chapitres sous leur titre ; le Markdown met un titre `##` au-dessus de chaque chapitre. L’export texte d’un EPUB respecte aussi les chapitres et leurs titres traduits.

**Options d’export…** permet de choisir le format et deux options : **Compléter avec le texte original** (les passages non traduits gardent leur texte source ; le manifeste signale les chapitres incomplets) et, pour le ZIP, **Ajouter le texte consolidé au ZIP**. Sans la première option, un export texte ou EPUB d’une traduction incomplète est refusé ; l’export EPUB complet est aussi refusé si EPUBCheck signale une non-conformité.

L’API permet en plus d’exporter plusieurs volumes d’une série en un seul ZIP (`POST /api/exports/text`, un dossier par volume). L’aperçu d’un chapitre TXT ou JSON est une mise en page simple de son texte.

### Écrans du livre

- **Traduction** : sections repliables à gauche, passages source et traduction alignés, consignes propres à un passage via **Retraduire… → Consignes du passage…**.
- **Validations** : file paginée des passages à vérifier (source, traduction, doutes et propositions de l’IA) ; chaque proposition peut être acceptée, refusée ou chargée dans l’éditeur avec **Modifier**. **Tout accepter** et **Lancer la revue IA** demandent confirmation.
- **Book Bible** : résumé, fiches personnages modifiables dans un formulaire, maintenance de la mémoire (synchroniser, vérifier, réindexer dans OpenViking, reconstruire depuis la base).
- **Glossaire** : import JSON, CSV ou TBX et export dans ces trois formats.
- **Réglages** : informations du livre, langues, modèle, **Mémoire de traduction** lorsque le serveur la propose, partage (liste des membres, invitation, révocation) et suppression du projet.

Le thème (clair, sombre ou celui du système) et la langue se choisissent dans le menu du compte, en bas de la barre latérale.

La preview est volontairement simplifiée (CSS de lecture neutre) ; les CSS et ressources originales sont conservées dans l’EPUB exporté. Aucune mention IA n’est ajoutée automatiquement.

## Codex

Deux connexions natives par clé API existent aussi : **Anthropic · Claude** (base URL `https://api.anthropic.com`, API Messages ; température et Top P ne sont pas envoyés car les modèles Claude actuels les refusent) et **OpenAI · Chat Completions** (base URL `https://api.openai.com/v1`). La clé API y est obligatoire, et le bouton de test liste les modèles via `/v1/models`.

Pour connecter **Codex avec un compte ChatGPT** ou **un modèle Codex via clé API OpenAI**, voir [Connexion Codex](codex.md). Les providers OpenAI-compatible existants continuent d’utiliser Chat Completions.

## OpenViking

L’application fonctionne sans OpenViking. Pour utiliser une instance existante : **Paramètres → Mémoire · OpenViking**.

```text
URL : http://openviking:1933
Clé : clé utilisateur ou administrateur liée au compte OpenViking
Racine : viking://resources/epub-translator
Mode : API key
```

Les mêmes valeurs initiales peuvent être définies avec `OPENVIKING_URL`, `OPENVIKING_API_KEY`, `OPENVIKING_ROOT_URI`. Les paramètres enregistrés dans l’interface prennent le dessus. Les clés sont chiffrées côté backend et jamais renvoyées par l’API.

Arborescence gérée :

```text
viking://resources/epub-translator/
  <owner_uuid>/<project_uuid>/events/<event_uuid>.json
```

Utiliser un compte OpenViking dédié si les livres doivent être isolés d’autres consommateurs de la même instance. Le préfixe par projet est une limite de retrieval appliquée par l’application, **pas un remplacement des ACL OpenViking**.

### Comportement

- Écritures via outbox SQL, URI stable, `replace`, puis `create` sur 404 pour les versions qui l’exigent.
- `wait=false` : la réussite d’une écriture n’est pas présentée comme une preuve d’indexation.
- Recherche courante : `POST /api/v1/search/find` avec `target_uri` et niveau 2.
- Recherche approfondie : `/api/v1/search/search`, **mode list et même target_uri**. Le mode context non bornable par URI n’est pas utilisé.
- Lecture L2 seulement pour les résultats appartenant aux événements SQL autorisés, antérieurs au passage courant. Le contenu relu doit correspondre à l’événement canonique.
- Déduplication, classement, budget et provenance visibles dans le Context Inspector.
- En cas d’indisponibilité : repli interne affiché et événements conservés pour synchronisation.
- Reconstruction depuis SQL et réindexation disponibles dans Book Bible. Certaines clés USER ne peuvent pas réindexer `resources/` : l’écriture/recherche peuvent néanmoins fonctionner. Les droits de l’instance restent respectés.

OpenViking gère lui-même ses modèles de compréhension et d’embeddings. L’application n’exige pas son installation dans la stack et ne modifie pas sa configuration serveur. Documentation : [API](https://docs.openviking.ai/en/api/01-overview), [authentification](https://docs.openviking.ai/en/guides/04-authentication).

## Qualité et fonctionnement

### Contexte et temps narratif

Chaque requête combine les règles utilisateur, les choix humains pertinents, le glossaire applicable, les personnages sélectionnés, une synthèse éditoriale, les faits antérieurs et les passages voisins source/traduction. La Book Bible éditoriale peut connaître la fin ; les événements injectés comme mémoire narrative sont bornés par position. Les prompts interdisent de dévoiler une révélation future ou d’attribuer à un personnage la connaissance du traducteur.

Les choix humains validés remplacent leurs anciennes versions dans le retrieval local. Les souvenirs externes ne modifient jamais directement les traductions, fiches validées ou termes verrouillés.

### Séries

Donnez à chaque tome le même nom de série (la casse et les espaces ne comptent pas) et son numéro de volume. Un tome reçoit le glossaire accepté des tomes précédents (jamais des suivants) : un terme verrouillé dans un tome antérieur est imposé et contrôlé comme le glossaire verrouillé du livre, sauf si ce livre verrouille lui-même une autre traduction. Vos corrections validées d’une traduction automatique (« Tour Argentée » → « Tour d’Argent ») sont transmises aux tomes suivants lorsque le passage concerné y est évoqué.

### Mémoire de traduction

Réglage **Mémoire de traduction** (Stratégie du livre, activé par défaut ; `translation_memory` dans `PUT /api/projects/{id}`). Un passage dont la source est identique (formes Unicode et espaces près, mise en forme comprise) à un passage déjà traduit dans un de vos livres, avec la même paire de langues, reprend cette traduction sans appel au modèle ; une traduction validée par vous passe d’abord. Dans une série, seuls ce livre et les tomes antérieurs servent. La version apparaît avec l’origine `translation_memory` dans l’historique, puis la relecture et la revue finale s’appliquent normalement. Le nombre de passages repris s’affiche sous le réglage (`stats.translation_memory_reused`). Une retraduction forcée interroge toujours le modèle.

### Segmentation et contenus particuliers

- Les longs paragraphes japonais, chinois ou coréens sont coupés en fin de phrase (。！？…).
- Les lectures ruby (furigana) restent telles quelles au-dessus du texte traduit.
- Le texte préformaté (`pre`), les formules MathML et les titres de dessins SVG sont conservés en original ; ils sont listés dans **Rapport de validation à l’import** (« Conservés tels quels à l’import »). Le texte visible des dessins SVG et les `aria-label` sont traduits.
- La table des matières et le NCX sont traduits mais ne comptent pas dans le nombre de sections ; les pages hors lecture linéaire (`linear="no"`) viennent après le récit.
- La quatrième de couverture (`dc:description`) et les sujets courts (`dc:subject`) forment une section **Métadonnées du livre**, traduite et réécrite dans le fichier exporté.
- Vers l’arabe, l’hébreu, le persan ou l’ourdou, l’export pose `dir="rtl"` et le sens de lecture droite-gauche ; les citations dans une troisième langue gardent leur langue.

### Modes

| Mode          | Passes                                                        |
| ------------- | ------------------------------------------------------------- |
| Rapide        | Analyse, traduction, contrôles déterministes                  |
| Normal        | Rapide + critique LLM                                         |
| Haute qualité | Critique, révision si problèmes réels, contrôles de cohérence |
| Maximum       | Haute qualité + polishing littéraire                          |

Les contrôles globaux LLM échantillonnent les occurrences dans tout le livre, pour la terminologie et les personnages, avec un plafond de trois lots par sujet. Leur couverture est explicitement notée `sampled`. Les contrôles structurels et de présence du glossaire verrouillé s’appliquent à chaque unité.

### Reprise et concurrence

- File SQL avec verrouillage de prise en charge, bail de 60 secondes et heartbeat de 2 secondes (`WORKER_HEARTBEAT_SECONDS`).
- Après un arrêt brutal, reprise au plus tard après expiration du bail, sous réserve de disponibilité du worker/provider.
- Un ancien worker ne peut plus appliquer un résultat après pause, annulation ou reprise par un nouveau worker.
- Traductions et étapes intermédiaires enregistrées séparément ; cache des appels valides par contenu du prompt, contexte, modèle et paramètres.
- Une correction humaine arrivée pendant l’inférence gagne : le résultat IA devient une proposition dans l’historique.
- La concurrence est définie par provider : `max_concurrency=3` autorise trois livres utilisant ce provider, analyses et traductions confondues. Les capacités de Codex et des providers personnalisés sont indépendantes. Au sein d’un livre, plusieurs passages sont traduits et relus à la fois, dans la limite de la capacité du provider partagée entre les livres en cours (`WORKER_BOOK_PARALLELISM` plafonne ce nombre ; `1` traite un passage à la fois) ; l’analyse reste séquentielle.

### Tokens et observabilité

Le budget actuel utilise une estimation **conservatrice en octets UTF-8**, affichée comme estimation, pas un tokenizer exact (un caractère japonais compte trois). La réservation de sortie, le schéma de réponse et une marge sont contrôlés avant envoi. Une entrée qui ne tient pas est refusée avec une erreur chiffrée (fenêtre, sortie réservée, prompt, passage, règles et fenêtre suffisante), sans tronquer le passage ni les règles obligatoires. Avec une petite fenêtre (16k), une première traduction trop longue est faite par parties coupées en fin de phrase puis réassemblées . À 8k, le prompt système et les règles occupent presque toute la fenêtre avec cette estimation prudente : prévoyez au moins 12 000 tokens avec une sortie maximale de 2 048. Ajuster la fenêtre selon la capacité réelle du serveur d’inférence. Le contexte optionnel (voisinage, personnages, glossaire, mémoire) est plafonné par le réglage **Budget de contexte** des paramètres de mémoire, 12 000 par défaut quelle que soit la fenêtre du modèle : l’augmenter enrichit chaque requête, et augmente d’autant son coût.

La réponse complète est validée par schéma, identifiants, marqueurs et contrôles de texte. JSON Schema est utilisé si déclaré, avec repli JSON simple lorsqu’un endpoint rejette explicitement ce format. Une réponse tronquée ou polluée par du texte hors JSON est rejetée.

Les métriques distinguent le cache, les tentatives, les tokens rapportés par le provider, la durée et le débit moyen global. Les coûts par million sont facultatifs et valent zéro par défaut. Les tokens d’entrée des requêtes en erreur, refusées ou interrompues sont comptés à part (`wasted_input_tokens`, et leur part `wasted_share` dans `GET /api/projects/{id}/metrics` et, par modèle, dans `GET /api/statistics/models`) : c’est la dépense qui n’a produit aucun résultat appliqué.

#### Estimer avant de lancer

`GET /api/projects/{id}/estimate?operation=analyze|translate|review` (lecture seule, accessible à tout membre du livre) annonce ce qu’un travail coûterait, sans jamais appeler le modèle : `input_tokens`, `output_tokens`, `requests`, `passages` à traiter, `cost` et le détail par étape (`breakdown`).

- **Seuls les passages restant à traiter comptent** : l’analyse ignore les passages déjà analysés (et la synthèse si la Book Bible est validée) ; la traduction ignore les passages terminés ou corrigés à la main ; la relecture reprend tous les passages sauf ceux validés.
- **Base de calcul (`basis`, `basis_kind`)** : si le propriétaire du livre a déjà traité au moins 5 passages avec le même fournisseur (ce livre compris), chaque étape reprend les moyennes observées : appels par passage (nouvelles tentatives et erreurs comprises), tokens d’entrée et de sortie par appel, part des passages qui reçoivent une révision ou une revue finale (`history`, avec le nombre de livres). Sinon, estimation par défaut (`default`) : 12 500 tokens de consignes et de contexte par appel plus le passage (4 caractères par token), 15 % de nouvelles tentatives, révision sur 60 % et revue finale sur 50 % des passages, étapes selon la qualité du livre. Une étape jamais observée utilise la valeur par défaut (`mixed`). Les réponses servies par le cache ne comptent pas.
- **Coût** : prix actuels du fournisseur du livre (`currency_note` rappelle lesquels) ; 0 si aucun prix n’est saisi. Les remises de cache des fournisseurs et les forfaits d’abonnement (Codex/ChatGPT) ne sont pas pris en compte.

C’est un ordre de grandeur : les contrôles de cohérence (qualité haute et maximum) sont comptés à un appel par terme ou personnage connu, et l’option de contexte approfondi (`deep`) n’est pas incluse. Les traces complètes de prompts/réponses sont privées au projet ; les logs de service ne contiennent pas le livre.

## Tests et développement

```bash
python3 -m venv .venv
.venv/bin/pip install -e './backend[test]'
```

Depuis `backend/` :

```bash
../.venv/bin/pytest -q
../.venv/bin/ruff check app tests
```

Depuis `frontend/` :

```bash
npm ci
npm run build
```

Test complet Docker avec un provider **explicitement synthétique** :

```bash
docker compose -p libris-smoke -f docker-compose.yml -f docker-compose.test.yml --profile test up -d --build
.venv/bin/python scripts/smoke.py --compose-project libris-smoke --confirm-disposable \
  --compose-file docker-compose.yml --compose-file docker-compose.test.yml
npm --prefix frontend exec playwright install chromium
npm --prefix frontend run test:e2e
```

Le smoke test crée un EPUB de test, analyse et traduit réellement via HTTP, vérifie la pause/reprise, tue le worker par SIGKILL, vérifie la reprise et exporte un EPUB contrôlé par EPUBCheck. Il utilise le compte local créé par `setup.py` sans afficher ses secrets. Il suppose que l’installation de test n’a pas de travail utilisateur actif.

Les tests navigateur couvrent correction, historique, inspecteur, preview et mobile. Le smoke test ne redémarre un worker externe que si son projet Compose est nommé explicitement. Nettoyage des données SQL synthétiques : relancer la même commande avec `--cleanup`. La mémoire externe éventuelle reste disponible pour le diagnostic et ne fait pas l’objet d’une suppression globale automatique.

## État de cette première version

Le parcours import → analyse → traduction contextualisée → reprise → correction → export est implémenté et testé. OpenViking a également été testé contre une instance réelle, y compris le comportement `create`/`replace`, la recherche fast/deep et l’injection dans le contexte.

Points à approfondir avant de qualifier la fidélité d’un roman de plusieurs centaines de pages :

- évaluation humaine bilingue sur un corpus littéraire long, avec le modèle réel choisi ;
- tokenizer exact par provider et adaptation automatique plus fine des unités aux petites fenêtres ;
- graphe temporel détaillé des relations/croyances, au-delà des événements sourcés et bornés actuels ;
- meilleure édition visuelle des fiches et des marqueurs inline ;
- preview CSS fidèle et résolution des défauts EPUB préexistants ;
- migration portable des journaux complets de requêtes (l’archive projet conserve le travail, les travaux et les chiffres des requêtes, mais pas les prompts et réponses, les clés ni les membres).

**La conformité EPUB, les mocks et les scores automatiques ne constituent pas une preuve de qualité littéraire.** Voir [le protocole d’évaluation](quality-evaluation.md).

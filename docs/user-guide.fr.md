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

- Volume `books` monté sur `/data` : `/data/books` (originaux), `/data/projects`, `/data/exports`.
- Volume `database` : données PostgreSQL, textes, traductions, versions, mémoire, paramètres chiffrés, jobs et traces.
- `.env` : clé de chiffrement de l’application et identifiants de déploiement.

Les traductions sont enregistrées immédiatement en SQL ; le volume `/data/projects` est prévu pour des artefacts supplémentaires, il ne remplace pas la sauvegarde SQL. **Sauvegarder PostgreSQL, `/data` et la clé `SECRET_KEY` ensemble.** Un export de projet est portable, mais ne contient ni comptes, ni mots de passe, ni clés de providers.

## Premier livre

Pour les commandes par lot, les deux progressions, les interruptions, les refus et le graphe : [guide d’exploitation](operations.md).

1. Dans **Paramètres → Providers LLM**, renseigner la base URL (incluant `/v1`), le modèle et éventuellement la clé. Le bouton de test interroge `/models`. Les capacités JSON et reasoning restent configurables.
2. Importer un EPUB dans la bibliothèque. La structure, les ressources et le texte sont analysés ; EPUBCheck est exécuté dans l’image Docker.
3. Dans **Configuration**, choisir le provider, les langues, le mode qualité et les instructions globales. Utiliser des codes de langue BCP 47, par exemple `en`, `fr`, `ja`.
4. **Analyser le livre**. Chaque unité est analysée, puis les résultats sont consolidés par chapitre en Book Bible. L’historique d’analyse reste consultable.
5. Examiner et corriger la **Book Bible**, les personnages et les propositions de glossaire. Les entrées proposées ne sont pas acceptées automatiquement par défaut.
6. **Traduire**. Le suivi se reconnecte automatiquement. Pause, reprise et retry conservent les traductions enregistrées.
7. Comparer, corriger et valider dans le workspace. Les marqueurs `⟦t0⟧…⟦/t0⟧` protègent les éléments inline ; leur suppression est refusée.
8. Exporter en EPUB, TXT, Markdown, Book Bible (JSON) ou archive de projet. L’export EPUB complet est refusé si du texte manque ou si EPUBCheck signale une non-conformité.

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

Le budget actuel utilise une estimation **conservatrice en octets UTF-8**, affichée comme estimation, pas un tokenizer exact. La réservation de sortie et une marge sont contrôlées avant envoi. Une entrée qui ne tient pas est refusée avec une erreur explicite, sans tronquer le passage ni les règles obligatoires. Ajuster la fenêtre selon la capacité réelle du serveur d’inférence. Le contexte optionnel (voisinage, personnages, glossaire, mémoire) est plafonné par le réglage **Budget de contexte** des paramètres de mémoire, 12 000 par défaut quelle que soit la fenêtre du modèle : l’augmenter enrichit chaque requête, et augmente d’autant son coût.

La réponse complète est validée par schéma, identifiants, marqueurs et contrôles de texte. JSON Schema est utilisé si déclaré, avec repli JSON simple lorsqu’un endpoint rejette explicitement ce format. Une réponse tronquée ou polluée par du texte hors JSON est rejetée.

Les métriques distinguent le cache, les tentatives, les tokens rapportés par le provider, la durée et le débit moyen global. Les coûts par million sont facultatifs et valent zéro par défaut. Les traces complètes de prompts/réponses sont privées au projet ; les logs de service ne contiennent pas le livre.

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
- migration portable de tout l’audit d’exécution (l’archive projet conserve textes, versions et mémoire, mais pas les jobs actifs, clés ou journaux complets de requêtes).

**La conformité EPUB, les mocks et les scores automatiques ne constituent pas une preuve de qualité littéraire.** Voir [le protocole d’évaluation](quality-evaluation.md).

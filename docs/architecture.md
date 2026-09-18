# Architecture et invariants

## Modules

- `engines/epub` : préflight ZIP, lecture EbookLib, DOM lxml, unités avec codes inline, segmentation et réinjection dans une copie de l’archive.
- `providers/llm.py` : OpenAI-compatible, formats structurés, validation, retries, cache, budget et traces.
- `providers/openviking.py` : contrat HTTP OpenViking, authentification API key/trusted, URI, écriture idempotente et retrieval borné.
- `engines/context` : requête narrative, mémoire locale/externe/hybride, sélection, temporalité, budget et inspecteur.
- `engines/memory` : décisions humaines, personnages, glossaire et outbox.
- `engines/translation` : analyse hiérarchique, versions, orchestration et contrôle global.
- `engines/quality` : identifiants d’unités, codes DOM, sorties vides, longueur, répétition, texte inchangé et terminologie.
- `jobs` : prise en charge transactionnelle, bail, fencing, événements persistants, reprise, état par passage (`segment_state`) et exécution hors de la boucle asyncio (`concurrency`).
- `api` : authentification, autorisations, projets, édition, paramètres, exports et SSE.

## Modèle SQL

Entités normalisées : users, login_sessions, memberships, projects, chapters, segments, translation_versions, entities, glossary, memories, bible_revisions, memory_outbox, prompts, jobs, job_segment_state, events, llm_requests, quality_issues, app_settings.

Les documents XHTML/NCX sont des sections de travail ; les subdivisions sémantiques sont conservées dans les unités et `Segment.section`. Le `spine` original est stocké explicitement et ne dépend jamais d’un ordre de noms de fichiers. Les ancres sont déterministes : ressource + XPath + type de champ. Les paragraphes longs peuvent être fragmentés à des frontières linguistiques, puis réassemblés avant réinjection.

## Transactions importantes

1. **Enregistrer une traduction** : vérifier le bail du job, comparer la révision source, insérer une version, puis modifier la version active seulement si autorisé. Les événements mémoire associés entrent dans l’outbox dans la même transaction.
2. **Correction humaine** : contrôle d’accès, révision attendue obligatoire, validation des unités et codes, nouvelle version, mémoire prioritaire si validée, commit.
3. **Reprendre** : invalider l’ancien détenteur du bail. Les écritures d’un résultat tardif sont refusées même si le provider termine sa requête.
4. **Synchroniser** : SQL reste canonique. Un échec externe ne retire jamais un résultat local. Un accusé d’écriture perdu peut être rejoué sur l’URI stable.

Une réponse HTTP reçue juste avant un crash peut être recalculée si elle n’avait pas été commitée. L’application garantit la persistance des résultats commités, pas l’exécution exactly-once d’une inférence distante.

## Exécution des jobs

### Checkpoint et état par passage

`jobs.checkpoint` ne contient qu’un curseur et des compteurs : `step`, `current`, `total`, `segment_id`, `consecutive_failures`, les indicateurs de la récupération automatique, `review_targets` (nombre de passages visés par la revue finale), les compteurs de lots. Sa taille ne dépend pas de celle du livre (quelques centaines d’octets, toujours moins de 4 Ko) ; il est réécrit à chaque passage et renvoyé par `GET /api/projects/{id}/jobs`.

Ce qu’un job a réglé passage par passage vit dans `job_segment_state` (clé primaire `job_id, step, segment_id, key`, écriture idempotente) :

| `step` | Signification |
| --- | --- |
| `finished` | plus rien à faire pour ce passage dans ce job (y compris une correction humaine ou un original conservé pendant le job) |
| `started` | une retraduction forcée a déjà appliqué sa nouvelle version |
| `review_target` / `reviewed` | périmètre figé de la revue finale ; issue (`resolved`, `needs_human`, `protected`, `failed`) et `data.revised` |
| `recovery_target` | passages repris par la récupération automatique |
| `repair` | groupe de quatre unités déjà validé d’un passage en réparation (`key` = révision:opération:début) |
| `bible`, `consistency` | lots de synthèse de la Book Bible et échantillons de cohérence déjà traités (`segment_id` vide) |

La progression (`project.progress`, `stats`) lit ces lignes. Une fois un job fini depuis `RETENTION_JOB_STATE_DAYS`, seules ses lignes `reviewed` sont gardées (voir le guide d’exploitation). L’archive de projet emporte ces lignes avec les jobs, et une archive exportée avant la 0.5 est convertie à la restauration. La migration `b856c2e068f8` a converti les anciens checkpoints (listes `finished_ids`, `final_review_*`, `repair`…) : un job en pause au moment de la mise à jour reprend sans retraduire ; le retour arrière reconstruit les listes.

### Boucle du worker

Tous les jobs d’un worker partagent une boucle asyncio ; une requête SQL synchrone ou une boucle CPU longue y retarde les heartbeats des autres jobs, qui perdent leur bail. Les sessions SQL et les calculs proportionnels au livre (préparation du contexte, score de la mémoire, échantillonnage de cohérence, admission et journal des appels au modèle, écritures des résultats) s’exécutent donc dans des threads, une session par appel : huit threads pour ce travail, deux réservés au renouvellement des baux pour qu’un heartbeat n’attende jamais derrière. Le nombre de threads reste sous la taille du pool de connexions. Un verrou par job sérialise, dans le worker, la lecture-modification-écriture du checkpoint (PostgreSQL le fait déjà avec `FOR UPDATE` ; SQLite lit avant de prendre son verrou d’écriture).

### Plusieurs passages d’un même livre

La traduction, la revue finale et les contrôles de cohérence traitent plusieurs passages d’un livre à la fois, dans une fenêtre glissante : les passages démarrent dans l’ordre du livre, et au plus N sont en vol. N vaut la capacité (`max_concurrency`) du provider, partagée à parts égales (arrondi supérieur) entre les livres qui l’utilisent au même moment, et relue avant chaque démarrage ; `WORKER_BOOK_PARALLELISM` la plafonne (`1` rétablit le traitement strictement séquentiel). Dans chaque processus, les appels à un provider passent par une file d’attente dimensionnée à sa capacité, servie dans l’ordre d’arrivée, avant l’admission en base qui reste la limite commune à tous les processus.

Compromis de contexte : le contexte d’un passage ne dépend que de ce qui est déjà enregistré. Un voisin précédent encore en cours de traduction apparaît dans `PREVIOUS_CONTEXT` avec sa source seule (traduction vide) et son état narratif manque à `CHAPTER_STATE` ; avec N passages en vol, au plus les N − 1 précédents sont concernés. Les voisins suivants ne présentent toujours que leur source. Avec `WORKER_BOOK_PARALLELISM=1`, chaque passage voit la traduction de tous ceux qui le précèdent, comme auparavant.

L’analyse des passages reste séquentielle : chaque analyse lit le résumé du chapitre, les personnages et les relations laissés par les passages précédents et réécrit le résumé « jusqu’à la position » ; en parallèle, la mémoire chronologique serait construite dans le désordre. La synthèse de la Book Bible, qui enrichit la bible lot après lot, reste séquentielle pour la même raison.

Reprise et sûreté :

- Un passage n’est marqué `finished` qu’une fois toutes ses étapes enregistrées. Chaque écriture vérifie le bail (`fence`) et la révision du passage ; une version déjà appliquée n’est pas réappliquée.
- Une pause, une annulation, la perte du bail ou l’arrêt du worker annulent tous les appels en vol (leur requête passe à `interrupted`) ; la première erreur d’un passage (panne du provider, authentification) arrête aussi les autres. Les passages interrompus ne sont pas marqués : la reprise les recommence à partir de ce qu’ils avaient enregistré, sans refaire ceux qui étaient terminés ni émettre d’événement pour eux.
- Le compteur des dix passages consécutifs en échec suit l’ordre d’achèvement des passages.
- Ordre des verrous : le worker verrouille la ligne de son job (`fence`) avant toute ligne de passage. Les actions de l’API qui touchent un passage et les jobs actifs du livre (correction humaine, original conservé, mise en file d’une proposition IA) verrouillent d’abord ces jobs (`lock_live_jobs`, par identifiant croissant), puis le passage ; l’interblocage passage/job avec le worker est donc impossible sous PostgreSQL. L’action attend au plus la fin de la courte transaction d’écriture du worker.

## Sélection contextuelle

Priorité : instructions > décisions humaines validées > glossaire verrouillé > données structurées validées > retrieval externe > synthèses automatiques > voisinage > inférences.

Le voisinage est servi en premier afin qu’un passage ne devienne pas isolé, mais il ne reçoit au plus que 60 % du budget de contexte optionnel (dont deux tiers pour ce qui précède) dès que d’autres éléments — fiches de personnages, glossaire, état du chapitre, mémoire — sont candidats ; un voisin trop long est réduit à un extrait (fin du passage précédent, début du suivant) plutôt que retiré. Les instructions et le glossaire obligatoire ne sont jamais retirés pour masquer un dépassement de budget. Les éléments supprimés et la raison de leur exclusion sont enregistrés.

Pour OpenViking : `target_uri` borne la recherche ; une seconde barrière compare les URI retournées à la liste exacte des événements admis par SQL. Les résultats inattendus, les synthèses globales de répertoire et les événements futurs ne sont pas injectés. Le contenu L2 est comparé à l’événement canonique.

L’état narratif n’est pas assimilé à la connaissance éditoriale du roman. Les fiches et la Book Bible issues d’une lecture globale sont marquées éditoriales ; elles ne doivent pas conduire à dévoiler une ambiguïté dans le texte traduit.

## Sécurité

- Pas d’extraction ZIP sur des chemins choisis par le livre ; contrôle des chemins, doublons, symlinks, tailles et ratios.
- Pas d’entités XML externes ni de chargements réseau du parseur.
- Aucun HTML généré par le modèle n’est accepté comme structure DOM.
- Preview assainie + iframe sans permissions + CSP restrictive.
- Clés chiffrées en SQL, aucune clé dans les réponses API, traces ou exports.
- Connexions réseau configurées par l’administrateur ; absence de redirections et de proxy d’environnement implicites.
- Bibliothèques privées et contrôle d’accès par projet, y compris pour les logs, SSE, versions et exports.
- Cookies HttpOnly/SameSite, contrôle d’origine, limitation des tentatives de connexion.
- Paramètres Docker persistants, migrations séparées, processus non-root.

## Évolutions prévues

La frontière `ContextProvider` permet d’ajouter d’autres moteurs sans dépendance dans le Translation Engine. Les modèles de génération sont configurables ; aucune hypothèse sur un modèle Qwen précis ou une fenêtre de 128k n’est encodée dans les prompts.

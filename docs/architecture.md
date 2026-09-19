# Architecture et invariants

## Modules

- `engines/ingestion` : adaptateurs de sources (`SourceAdapter.inspect()` sans rien créer, `parse()` vers `ImportedVolume`/`ImportedChapter`) pour EPUB (parseur existant inchangé), TXT (décodage, mise en page, unités déterministes) et JSON ; inférence des séries, volumes et chapitres (`naming.py`) ; écriture en SQL et fichiers sous `DATA_DIR` (`store.py`), ajout, insertion et remplacement explicite de chapitres. Le reste du pipeline ignore le format source.
- `engines/series` : mémoire de série (identités canoniques, liens proposés/confirmés, relations, glossaire de série, Series Bible) recalculée depuis les volumes (`refresh_series`), et journal d’audit.
- `engines/epub` : préflight ZIP, lecture EbookLib, DOM lxml, unités avec codes inline, segmentation et réinjection dans une copie de l’archive.
- `providers/llm.py` : OpenAI-compatible, formats structurés, validation, retries, cache, budget et traces.
- `providers/openviking.py` : contrat HTTP OpenViking, authentification API key/trusted, URI, écriture idempotente et retrieval borné.
- `engines/context` : requête narrative, mémoire locale/externe/hybride, sélection, temporalité, budget et inspecteur ; `series.py` : conventions héritées des tomes antérieurs d’une série.
- `engines/memory` : décisions humaines, personnages, glossaire et outbox.
- `engines/translation` : analyse hiérarchique, versions, orchestration, contrôle global, traduction par parties (`repair.py`) et mémoire de traduction (`memory.py`).
- `engines/autopilot` : pilote automatique (voir [autopilot.md](autopilot.md)) — boucle de convergence (`loop.py`), échelle de récupération des passages en échec (`recovery.py`), arbitrage IA des points ouverts (`arbitration.py`), décisions sur la mémoire (`memory.py`), fournisseurs de secours et pannes bornées (`providers.py`), dégradation des étapes facultatives (`degrade.py`) et journal `autopilot_decisions` (`decisions.record`).
- `engines/quality` : identifiants d’unités, codes DOM, sorties vides, longueur, répétition, texte inchangé et terminologie.
- `jobs` : prise en charge transactionnelle, bail, fencing, événements persistants, reprise, état par passage (`segment_state`) et exécution hors de la boucle asyncio (`concurrency`).
- `api` : authentification, autorisations, projets, édition, paramètres, exports et SSE ; `api/v1.py` + `api/tokens.py` : API d’automatisation par jetons (voir [api.md](api.md)), dont les requêtes attendent en SQL que leur volume soit libre (`jobs/requests.py`, répartiteur du worker).

## Modèle SQL

Entités normalisées (détail : [modèle de données](data-model.md)) : users, login_sessions, memberships, series, projects (volumes, flux continus de webnovel), source_assets, import_sessions, chapters, segments, translation_versions, entities, glossary, memories, bible_revisions, memory_outbox, prompts, jobs, job_segment_state, events, llm_requests, quality_issues, app_settings, series_entities, series_entity_links, series_relations, series_glossary, audit_entries, api_tokens, translation_requests, autopilot_decisions (journal des décisions du pilote automatique ; `jobs.result` porte le rapport final).

Les documents XHTML/NCX sont des sections de travail ; les subdivisions sémantiques sont conservées dans les unités et `Segment.section`. Le `spine` original est stocké explicitement et ne dépend jamais d’un ordre de noms de fichiers. Les ancres sont déterministes : ressource + XPath + type de champ. Les paragraphes longs peuvent être fragmentés à des frontières linguistiques, puis réassemblés avant réinjection.

`Chapter.kind` distingue le récit (`narrative`), les documents hors lecture linéaire (`auxiliary`, `linear="no"`, placés après le récit), la navigation (`navigation` : document nav, NCX) et les métadonnées (`metadata` : `dc:description` et `dc:subject` court de l’OPF, traduits comme des passages). `stats.chapters` et `stats.synthesized_chapters` ne comptent que les deux premiers.

Unités : un bloc feuille (`p`, `li`, `td`…) forme une unité ; dans un parent qui mêle texte et blocs, chaque suite de texte et d’éléments en ligne entre deux blocs forme une unité `run` (ancre : parent + indice du bloc qui la précède). Les lectures ruby (`rt`, `rp`), le code, les formules et le texte préformaté sont des marqueurs immuables ; le texte SVG `<text>` et `aria-label` sont traduits ; `pre`, MathML et les titres SVG conservés sont listés dans `book_info.untranslated`. Les paragraphes CJK se coupent sur 。！？… (guillemets fermants compris), puis sur les propositions, puis à la limite.

À l’export, les documents traduits reçoivent la langue et la direction de la cible (`dir="rtl"` pour l’arabe, l’hébreu, le persan, l’ourdou…, `page-progression-direction` du spine en EPUB 3) ; les éléments déclarant la langue source passent à la langue cible, ceux dans une troisième langue gardent langue et direction.

La découpe est versionnée (`book_info.segmentation`, 2 depuis la v0.5). Les livres importés avant gardent leurs unités ; une archive de projet sans ce champ est réimportée avec la découpe 1 (`extract_units_v1`), faute de quoi ses passages ne correspondraient plus.

`Segment.source_key` (SHA-256 des unités normalisées NFKC, espaces réduits, marqueurs compris) indexe la mémoire de traduction.

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
| `analysis_skipped` | pilote automatique : analyse du passage abandonnée après un refus ou des réponses invalides |
| `autopilot_ladder`, `autopilot_arbitrated` | pilote automatique : passage passé par l’échelle de récupération, ou points ouverts arbitrés, pendant le tour `key` (`r1`, `r2`…) ; issue (`recovered`, `source_retained`, `applied`, `decided`, `failed`, `protected`) |

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
- Le compteur des dix passages consécutifs en échec suit l’ordre d’achèvement des passages ; il n’arrête jamais un job du pilote automatique, dont les passages en échec passent par l’échelle de récupération.
- Ordre des verrous : le worker verrouille la ligne de son job (`fence`) avant toute ligne de passage. Les actions de l’API qui touchent un passage et les jobs actifs du livre (correction humaine, original conservé, mise en file d’une proposition IA) verrouillent d’abord ces jobs (`lock_live_jobs`, par identifiant croissant), puis le passage ; l’interblocage passage/job avec le worker est donc impossible sous PostgreSQL. L’action attend au plus la fin de la courte transaction d’écriture du worker.

## Sélection contextuelle

Priorité : instructions > décisions humaines validées > glossaire verrouillé > termes verrouillés de la série > données structurées validées > retrieval externe > synthèses automatiques > voisinage > inférences.

Budget : fenêtre du fournisseur − sortie réservée − schéma de réponse de l’opération − marge contrôlée par `llm.complete`. Les tailles sont mesurées sur les sections sérialisées (échappements et balises compris) ; `input_estimate` est exactement la valeur que `llm.complete` compare à la fenêtre. Si le passage et ses règles obligatoires ne tiennent pas, l’erreur donne les chiffres (fenêtre, sortie, schéma, prompt système, passage, règles) et la fenêtre suffisante. Une première traduction est alors découpée aux frontières de phrase en parties dimensionnées pour la fenêtre (la moitié de ce qui reste après les règles ; l’autre moitié pour le voisinage), traduites avec leur contexte, puis réassemblées ; une révision ne découpe que par unités entières.

Séries (`series.py`) : seuls les volumes antérieurs de la même série (`series_id`, même propriétaire, numéro de volume inférieur) et de même paire de langues comparées sur la sous-étiquette primaire (`en-US` ≈ `en`). Un flux continu de webnovel ou un volume sans numéro n’a pas de volume antérieur : il s’appuie sur ses propres chapitres, lus dans l’ordre. `SERIES_CONVENTIONS` porte aussi `known_identities` : les personnages déjà rencontrés dans les volumes antérieurs, sous les seuls noms employés par ces volumes. Priorité terminologique : instruction explicite > décision humaine validée > terme verrouillé du volume (ou dérogation `series_override`, auditée) > terme verrouillé de la série (décision humaine de série ou volume antérieur) > terme accepté de la série > proposition automatique. Pour un même terme, un choix verrouillé l’emporte sur tout choix non verrouillé, puis le tome le plus récent. Les termes verrouillés de série sont contrôlés en sortie comme le glossaire verrouillé du livre, sauf si ce livre verrouille autrement le même terme. Une correction humaine validée d’une traduction automatique enregistre ses remplacements courts (avant/après) et les noms du passage ; un tome ultérieur qui mentionne ces noms reçoit ces choix dans `SERIES_CONVENTIONS.human_decisions`.

Mémoire de traduction (`memory.py`) : avant l’appel du modèle pour une première traduction, un passage terminé de même `source_key` chez le même propriétaire, même langue source (primaire) et même langue cible, est réutilisé ; une version validée par un humain passe d’abord. Dans une série, seuls le même livre et les tomes antérieurs sont admis. Les unités ne sont recopiées que si la structure des marqueurs est valide et que le glossaire verrouillé du livre (série comprise) est respecté. La version porte l’origine `translation_memory` ; relecture, révision et revue finale s’appliquent ensuite normalement. Un relancement forcé interroge toujours le modèle. Avec plusieurs passages d’un livre en vol, les passages de même `source_key` d’un job s’exécutent l’un après l’autre (verrou asyncio par job et clé) : le second attend que le premier soit terminé et le réutilise, au lieu de le devancer auprès du modèle avec une traduction différente. La consultation est une requête SQL exécutée hors de la boucle asyncio ; une réutilisation marque le passage commencé puis fini dans `job_segment_state` comme une traduction normale.

Prompts : le contenu des sections échappe `<` et `>` (échappements JSON), si bien qu’un texte du livre ne peut pas fermer une section. `load_prompt` ajoute à chaque prompt, surcharges en base comprises, une clause « données non fiables » et, pour les opérations qui écrivent ou relisent, une règle de registre (tu/vous…) et la typographie de la langue cible ; les langues sont nommées (« French (fr) »). Version : `file-v3` (connaissances de série depuis 0.6) ou `db-vN`, suffixée de `+rules-v1`. Le schéma JSON voyage une seule fois : dans `response_format` en mode structuré, sinon dans un message système.

Le voisinage est servi en premier afin qu’un passage ne devienne pas isolé, mais il ne reçoit au plus que 60 % du budget de contexte optionnel (dont deux tiers pour ce qui précède) dès que d’autres éléments — fiches de personnages, glossaire, état du chapitre, mémoire — sont candidats ; un voisin trop long est réduit à un extrait (fin du passage précédent, début du suivant) plutôt que retiré. Les instructions et le glossaire obligatoire ne sont jamais retirés pour masquer un dépassement de budget. Les éléments supprimés et la raison de leur exclusion sont enregistrés.

Pour OpenViking (voir [le guide](openviking.md)) : un volume d’une série vit dans `<racine>/<propriétaire>/series/<série>/volumes/<volume>`, un volume unique dans `<racine>/<propriétaire>/standalone/<volume>`. `target_uri` borne la recherche (répertoire de la série) ; une seconde barrière compare les URI retournées à la liste exacte des événements admis par SQL, calculée depuis la table `memories` : passages antérieurs du volume et volumes antérieurs de la série. Les résultats inattendus, les synthèses globales de répertoire, les catalogues, les chapitres et volumes futurs ne sont pas injectés. Le contenu L2 est comparé à l’événement canonique, recalculé depuis SQL. La mémoire interne lit les volumes antérieurs de la même façon.

L’état narratif n’est pas assimilé à la connaissance éditoriale du roman. Les fiches et la Book Bible issues d’une lecture globale sont marquées éditoriales ; elles ne doivent pas conduire à dévoiler une ambiguïté dans le texte traduit.

## Exports et archives multiformat

Un volume vient d’un EPUB, de fichiers TXT (un par chapitre) ou d’un payload JSON ; ses fichiers sources sont des lignes `SourceAsset` (`storage_path` relatif à `DATA_DIR` : `books/<projet>.epub`, `sources/<projet>/<asset>.<ext>`). `Project.original_path`/`original_hash` ne restent renseignés que pour les EPUB, et l’EPUB d’origine est lu par sa ligne `SourceAsset` puis, pour les livres antérieurs à 0.6, par ces anciens champs et `DATA_DIR/books/<id>.epub`.

`GET /api/projects/{pid}/export/{format}` :

| Format | Source | Contenu |
|---|---|---|
| `epub` | EPUB seulement (409 explicite pour TXT/JSON) | EPUB reconstruit depuis l’original, validé par EPUBCheck |
| `txt` | toutes | un fichier : titre du volume, puis chaque chapitre sous son titre, chapitres séparés par deux lignes vides |
| `txt-zip` | toutes | `chapters/NNN - Titre.txt` (UTF-8, ordre de lecture, numéros complétés de zéros, noms nettoyés et uniques) + `manifest.json` (SHA-256 de chaque fichier, chapitres incomplets) ; `consolidated=true` ajoute le fichier unique |
| `md` | toutes | `# Volume`, puis `## Chapitre` au-dessus de chaque chapitre |
| `bible`, `project` | toutes | Book Bible JSON ; archive de projet (ci-dessous) |

`allow_source=true` exporte une traduction inachevée (originaux conservés) en EPUB comme en texte ; sans lui, un export texte incomplet répond 409. `POST /api/exports/text {project_ids, allow_source, consolidated}` exporte plusieurs volumes (une série) : un dossier `NN - Titre/` par volume avec ses chapitres et son manifeste. Le rendu texte (`engines/exports/text.py`) suit `Chapter.import_meta["layout"]` pour TXT/JSON (lignes, lignes vides, indentation, séparateurs de scène) et donne un paragraphe par unité pour un EPUB, sans `<title>` ni attributs ; le titre d’un chapitre EPUB est la traduction de l’unité d’où il a été lu. Aucun marqueur `⟦…⟧` n’est écrit. L’aperçu d’un chapitre TXT/JSON est un HTML simple construit depuis le layout, texte échappé, même CSP que l’aperçu EPUB, sans lire d’EPUB.

### Archive de projet, version 3

`translation-project.zip` contient `project.json` et les fichiers sources sous des noms fixés par Libris : `sources/<n>.epub|txt|json` (toutes les `SourceAsset` du volume) et, pour les chapitres JSON, `texts/<n>.txt`. `project.json` (`schema_version: 3`) ajoute aux données de la version 2 :

- `series` : `{name, kind, authors}` ; le projet garde `series_name`, `volume_number`, `source_format`, `project_kind`, `external_id`, `import_meta` ;
- `sources` : `{file, format, original_name, media_type, sha256, meta}` ;
- par chapitre : `asset` (fichier source), `external_id`, `chapter_number`, `source_checksum`, `import_meta` (layout), `context_stale`, et pour TXT/JSON `text_source {file, title, first_line_title}` : le fichier à redécouper et les options d’import qui redonnent les mêmes unités (texte source reconstruit depuis les unités et le layout pour JSON, dont le payload n’est pas un texte de chapitre) ;
- glossaire avec `series_override`.

Restauration (`POST /api/projects/import`) : seuls `project.json`, `original.epub` (versions 1 et 2) et les noms `sources/…`, `texts/…` ci-dessus sont admis, aucun n’est utilisé comme chemin ; nombre d’entrées (`MAX_ENTRIES`), tailles déclarées (`MAX_UNPACKED_MB`), ratio de compression, lecture bornée par la taille déclarée, empreintes SHA-256 et cohérence sources/chapitres sont vérifiés avant toute écriture. Un EPUB est réimporté par `import_book` (découpe de l’archive) ; un volume TXT/JSON est recréé par `TxtAdapter`/`text_chapter` avec les `resource` enregistrées, donc les mêmes identifiants d’unités, puis chaque passage doit avoir le même texte source que dans l’archive, sinon refus. Tout est fait dans une transaction, fichiers retirés en cas d’échec. La personne qui restaure devient propriétaire, la série est retrouvée ou créée par nom normalisé parmi les siennes (`get_or_create_series`), un second conteneur de feuilleton ou un `external_id` de volume déjà pris dans la série sont refusés (409), aucun provider n’est restauré. `NOT_ARCHIVED` liste les colonnes volontairement absentes ; un test échoue si une nouvelle colonne n’est ni archivée ni listée.

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

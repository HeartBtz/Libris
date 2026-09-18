# Bibliothèque, reprise et couverture

## Plusieurs livres

La bibliothèque accepte plusieurs EPUB en un import (deux fichiers traités simultanément à l’import). Les cases de sélection permettent de configurer, analyser, traduire, mettre en pause, reprendre, annuler les analyses, annuler les traductions et supprimer les projets sélectionnés.

La suppression est confirmée une seule fois pour la sélection. Elle arrête les travaux concernés et supprime le projet local. Les documents déjà présents dans OpenViking restent distincts.

Le worker ordonnance les livres selon `max_concurrency` de leur provider. Cette limite réunit analyses, traductions, relectures et contrôles de cohérence : un provider réglé à 3 exécute au plus trois livres à la fois, quelle que soit la combinaison des opérations. Les capacités des providers sont indépendantes ; Codex à 3 et Qwen à 1 autorisent donc jusqu’à quatre livres actifs. Au sein d’un livre, la traduction, la revue finale et les contrôles de cohérence traitent plusieurs passages à la fois, jusqu’à la capacité du provider, partagée entre les livres qui l’utilisent au même moment ; l’analyse des passages et la synthèse de la Book Bible restent séquentielles. `WORKER_BOOK_PARALLELISM` plafonne ce nombre par livre (`0`, par défaut : la capacité du provider ; `1` : un passage à la fois, comme avant la 0.5). Le limiteur des requêtes LLM applique la capacité comme seconde protection, dans l’ordre d’arrivée des demandes. Le compromis sur le contexte des passages voisins est décrit dans l’[architecture](architecture.md#plusieurs-passages-dun-même-livre).

Le provider est figé pendant une exécution afin de préserver les limites et le fencing des résultats. Pour changer de modèle en cours de livre, mettre le job en pause, modifier le provider du projet puis reprendre : le job est alors réaffecté au nouveau provider à partir du prochain passage. Les jobs de reprise ciblée conservent leur provider explicitement choisi.

## Les deux progressions

- **Analyse & mémoire** : proportion des passages analysés et sections synthétisées dans la Book Bible parmi ces unités de travail. La synthèse compte dans la progression ; 100 % exige que les deux étapes soient terminées. Ce pourcentage représente la couverture, pas une estimation du temps restant.
- **Traduction** : passages disposant d’une traduction, hors passages explicitement conservés en langue source.

Les détails sont disponibles au survol. Chaque livre possède aussi un bouton **Actualiser**, qui recharge statistiques, jobs et panneaux sans remplacer un brouillon de traduction en cours.

Cliquer **Analyser** sur un livre entièrement analysé est une opération sans recalcul. Une réanalyse complète est une action distincte, confirmée ; les analyses humaines sont conservées.

## Arrêts et erreurs

| Situation                     | État / suite                                                                                                     |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Pause volontaire              | `paused`, reprise explicite uniquement                                                                           |
| Annulation                    | `cancelled`, résultats conservés ; reprise explicite possible                                                    |
| Arrêt propre du worker        | requête interrompue, travail remis en attente au checkpoint                                                      |
| Arrêt brutal                  | récupération après expiration du bail de 60 s                                                                    |
| Réseau, timeout, HTTP 429/5xx | `waiting`, nouvelle tentative planifiée, délai progressif de 60 s à 1 h (`PROVIDER_RECOVERY_BASE_SECONDS`, `PROVIDER_RECOVERY_MAX_SECONDS`) ; `Retry-After` respecté jusqu’à 24 h |
| Authentification invalide     | `blocked`, reconnexion et reprise nécessaires                                                                    |
| Refus pendant l’analyse       | `blocked` / `content_refusal`, intervention humaine nécessaire                                                   |
| Refus pendant la traduction   | deuxième essai, puis passage marqué `refused` et poursuite du livre                                              |
| JSON ou structure invalide    | retries bornés, puis erreur localisée                                                                            |

Un contrôle de bail toutes les deux secondes détecte les pauses et annulations ; il interrompt alors tous les appels en vol du livre. Les écritures sont protégées par la révision du passage et le détenteur du bail. Un résultat tardif ne remplace pas une correction humaine ou un état annulé. Les étapes initiale, critique, révision et synthèse ont leurs checkpoints ; l’état de chaque passage est enregistré dans `job_segment_state`, le checkpoint du job ne gardant qu’un curseur et des compteurs de taille fixe.

Le travail SQL et les calculs proportionnels à la taille du livre s’exécutent hors de la boucle asyncio du worker : un gros livre ne retarde plus les heartbeats des autres livres. Sur un livre synthétique de 1 500 passages traduit en même temps qu’un second de même taille (SQLite, provider sans latence), le retard maximal de la boucle est passé de 450 ms à environ 100 ms et l’intervalle entre deux renouvellements de bail n’a pas dépassé 2,2 s pour un heartbeat de 2 s.

## Refus et absence de trous silencieux

Les refus explicites du provider, les filtres de contenu et les réponses contenant un refus à la place d’un résultat sont distingués des pannes. Le texte original est toujours conservé dans le projet. Pour une traduction, Libris effectue exactement deux tentatives, marque ensuite le passage comme refusé et continue avec le passage suivant. Un refus n’est pas comptabilisé comme une traduction réussie.

L’onglet **Validations** regroupe les passages refusés. Une reprise ciblée permet de choisir un autre provider, notamment un modèle non censuré, et de retraduire uniquement ces passages. Le provider principal du livre n’est pas modifié. Chaque nouvelle reprise dispose à nouveau de deux tentatives par passage et laisse les autres traductions intactes.

La file de validation reste stable pendant le traitement en arrière-plan afin de préserver les brouillons. Chaque avis IA expose son doute et sa correction proposée. **Accepter cette proposition** remplace uniquement l’unité concernée et préserve les marqueurs EPUB. **Refuser cette proposition** conserve le texte courant. Les deux décisions créent une correction humaine protégée ; lorsque la dernière proposition est arbitrée et qu’aucun autre motif ne subsiste, le passage disparaît de la file. La validation éditoriale finale reste une action distincte.

Résolutions dans le workspace :

1. saisir une traduction humaine ;
2. ouvrir l’inspecteur, onglet **Analyse humaine**, et fournir un résumé utile à la continuité ;
3. choisir explicitement **Conserver l’original pour l’export**.

Conserver l’original ne remplace pas une analyse manquante. Pour un refus de synthèse globale, une Book Bible humaine avec un résumé non vide peut être validée. Le travail peut ensuite être repris.

L’export EPUB normal demande une traduction complète. **EPUB partiel · originaux conservés** inclut le texte source aux endroits non traduits et porte un nom de fichier distinct. Le **rapport de couverture** liste les passages sans analyse, sans traduction et ceux conservés en original. L’original retenu n’est pas présenté comme une traduction validée.

## Personnages et OpenViking

L’onglet **Personnages & liens** propose recherche, déplacement, zoom, fusion d’identités, alias et liens dirigés. Les liens IA ou tirés d’anciennes fiches sont en pointillés ; les validations humaines sont identifiées. Les fusions conservent leurs fiches historiques et leurs snapshots.

Les alias confirmés humainement restent prioritaires. Pronoms et descriptions relationnelles ne deviennent pas automatiquement des alias globaux. Les variantes incertaines sont stockées comme propositions.

En mode Hybrid/OpenViking, un catalogue nommé est publié et actualisé :

- `book.md` : titre, auteur, progression et liens ;
- `book-bible.json` : synthèse éditoriale ;
- `characters.json` et pages associées : identités et alias ;
- `relationships.json` et pages associées : liens, provenance et validation.

Dans **Book Bible → Mémoire OpenViking**, les liens ouvrent les fichiers réellement lus sur l’instance distante à travers le backend. **Synchroniser le livre et le graphe** fonctionne même pendant une analyse. **Vérifier dans OpenViking** distingue lecture et présence dans l’index.

Les documents globaux sont séparés du retrieval narratif, limité aux événements admissibles sous `/events`. Les fichiers de catalogue sont des projections contextuelles compactes ; SQL conserve les données complètes et exactes.

## Archive de projet

**Exporter → Archive de projet** produit `translation-project.zip` : l’EPUB original et `project.json` (format `schema_version: 2`). Elle sert de sauvegarde d’un livre ou à le déplacer vers une autre instance ; **Restaurer** (import d’archive) recrée un nouveau projet.

L’archive conserve tout ce qui fait le travail sur le livre : configuration (titre, série et tome, langues, qualité, source mémoire, instructions globales), consignes par chapitre et par passage, traductions avec leur statut (validé, à vérifier, refusé, original conservé), étape, critiques et incertitudes, historique complet des versions (sans doublon), glossaire, Book Bible et ses révisions, personnages, fusions et liens, mémoires, problèmes qualité, travaux avec leurs checkpoints (dont les résultats de la revue finale) et les chiffres des requêtes LLM (opération, modèle, tokens, durée, coût, statut).

Ne sont **pas** restaurés, par sécurité : le propriétaire (la personne qui restaure devient propriétaire), les membres et leurs droits (à repartager), le provider (à choisir parmi ceux du serveur ; les travaux qui en épinglaient un reprennent sur celui du livre), les prompts et réponses complets des requêtes, les événements de progression et la file d’envoi OpenViking. Un travail qui était en cours revient **en pause** : rien ne repart ni n’est facturé sans action. Un livre archivé revient actif.

L’archive est validée avant toute écriture : une archive incomplète ou altérée est refusée (422) en nommant les champs fautifs, et une archive dont le texte source ne correspond pas à son EPUB est refusée sans laisser de livre partiel. Les archives de l’ancien format (`schema_version: 1`) restent lisibles. L’export refuse une archive que l’import ne pourrait pas relire (`MAX_UPLOAD_MB`, `MAX_UNPACKED_MB`) ; le message indique le réglage à augmenter sur les deux serveurs.

## Limites de charge

| Variable | Défaut | Effet |
|---|---|---|
| `EVENT_STREAMS_PER_USER` | `4` | suivis en direct (un par onglet ouvert sur un livre) simultanés par compte ; au-delà, 429 et message invitant à fermer des onglets. |
| `EVENT_STREAMS_TOTAL` | `100` | suivis en direct simultanés pour tout le processus API. |
| `MAX_COMPRESSION_RATIO` | `100` | ratio de compression global maximal d’un EPUB de plus de 8 Mio décompressé ; la taille totale déclarée est contrôlée avant toute décompression. |
| `PREVIEW_CACHE_MB` | `64` | mémoire gardée pour les livres décompressés des derniers aperçus ; une modification de passage invalide l’entrée. |

La liste des projets, rafraîchie toutes les 5 secondes par l’interface, est calculée en un nombre constant de requêtes SQL quel que soit le nombre de livres, et n’inclut plus la Book Bible (la page du livre la charge).

## Langue des messages d’erreur

Les messages d’erreur de l’API sont en français par défaut. Quand l’interface est en anglais, elle envoie `Accept-Language: en` et reçoit les messages en anglais (`detail`). Le catalogue est `backend/app/i18n.py` ; un test échoue si un message levé dans le code n’y a pas de traduction.

## Rétention des données de diagnostic

Le worker borne lui-même la croissance de la base : une passe au démarrage, puis une par heure, par petits lots et hors de la boucle des jobs.

| Variable | Défaut | Effet |
|---|---|---|
| `RETENTION_REQUEST_BODIES_DAYS` | `30` | vide le prompt, la réponse brute et la trace de contexte des requêtes LLM terminées plus anciennes. La ligne reste : tokens, coût, durée, statut, erreur et réponse validée (utilisée par le cache) sont conservés. |
| `RETENTION_EVENTS_DAYS` | `7` | supprime les événements de progression plus anciens, en gardant toujours les 500 derniers de chaque livre. |
| `RETENTION_OUTBOX_SENT_DAYS` | `7` | supprime les envois OpenViking déjà transmis. |
| `RETENTION_BIBLE_REVISIONS` | `20` | garde les 20 dernières révisions automatiques de la Book Bible par livre ; les révisions humaines sont toutes conservées. |
| `RETENTION_JOB_STATE_DAYS` | `30` | pour les jobs terminés, échoués ou annulés depuis plus longtemps, supprime l'état par passage qui ne sert qu'à la reprise (`job_segment_state` : passages finis, cibles, groupes réparés, lots de synthèse et de cohérence). Les issues de la revue finale (`reviewed`) sont gardées : elles alimentent l'historique de relecture du livre. Les jobs en pause ou en attente ne sont jamais touchés ; si un job échoué ou annulé est repris après ce délai, ses passages déjà terminés ne sont pas retraduits, sauf retraduction forcée, qui les refait. |

Les jobs terminés avant la 0.5 comptent à partir de leur création. `0` désactive une règle. Pour mesurer avant d'appliquer : `docker compose exec api python -m app.maintenance.retention --dry-run`. Conséquence visible : l'inspecteur de requêtes n'affiche plus le prompt des requêtes de plus de 30 jours.

PostgreSQL réutilise l'espace libéré mais ne le rend au système qu'après `VACUUM (FULL, ANALYZE) llm_requests;`, qui verrouille la table : arrêtez le worker avant, et prévoyez autant d'espace disque libre que la taille utile de la table.

## Supervision (Prometheus)

`GET /metrics` expose l'état de l'instance au format texte Prometheus 0.0.4. L'adresse est désactivée par défaut (réponse 404) ; elle s'active en définissant `METRICS_TOKEN` (24 caractères au moins, par exemple `openssl rand -hex 32`) dans `.env`, puis `docker compose up -d`. Chaque collecte doit présenter ce jeton en `Authorization: Bearer …` ; une session de navigateur ne suffit pas (401). Le jeton est comparé en temps constant.

| Métrique | Type | Contenu |
|---|---|---|
| `libris_jobs{operation,status}` | gauge | travaux par opération et état, terminés compris |
| `libris_jobs_oldest_queued_age_seconds` | gauge | attente du plus ancien travail prêt à partir (`pending`, ou `waiting` dont le délai de reprise est échu) ; un travail repris compte depuis sa reprise |
| `libris_jobs_expired_leases` | gauge | travaux en cours dont le bail de 60 s a expiré (worker arrêté ou bloqué) |
| `libris_llm_requests_total{operation,status}` | counter | requêtes LLM terminées par opération et issue (`success`, `error`, `refused`, `interrupted`, `abandoned`) ; les réponses servies par le cache comptent en `success` |
| `libris_llm_input_tokens_total{operation}`, `libris_llm_output_tokens_total{operation}` | counter | tokens rapportés par les fournisseurs |
| `libris_llm_wasted_input_tokens_total{operation}` | counter | tokens d'entrée des requêtes en erreur, refusées ou interrompues |
| `libris_llm_cache_hits_total{operation}`, `libris_llm_cache_hit_ratio` | counter, gauge | réponses servies par le cache, et leur part de toutes les requêtes terminées |
| `libris_llm_requests_in_flight{provider}` | gauge | requêtes en cours par fournisseur (son nom, jamais son adresse) |
| `libris_segments{status}` | gauge | passages de tous les livres par état |
| `libris_memory_outbox_pending` | gauge | mises à jour OpenViking pas encore transmises |

Les compteurs sont lus dans la base, où chaque appel laisse une ligne : ils sont cumulés depuis l'installation, identiques pour tous les processus et insensibles aux redémarrages. La rétention ne supprime pas ces lignes (elle vide seulement les corps) ; supprimer un livre supprime ses requêtes, ce que Prometheus traite comme une remise à zéro du compteur. Utilisez `rate()`/`increase()` pour une fenêtre (« tokens par heure »). Aucune étiquette ne contient de titre, de texte, d'identifiant de livre ni d'URL. Le résultat est gardé 10 secondes : un intervalle de collecte de 30 s à 1 min suffit.

```yaml
scrape_configs:
  - job_name: libris
    scrape_interval: 60s
    metrics_path: /metrics
    scheme: https
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/libris-metrics-token
    static_configs:
      - targets: ["books.example.com"]
```

Exemples d'alertes : `libris_jobs_expired_leases > 0` pendant 5 min (worker arrêté), `libris_jobs_oldest_queued_age_seconds > 900` (aucun worker ne prend les travaux ou fournisseur saturé), `sum(rate(libris_llm_wasted_input_tokens_total[1h])) / sum(rate(libris_llm_input_tokens_total[1h])) > 0.2` (plus d'un token sur cinq dépensé pour rien).

Derrière un proxy inverse, exposez `/metrics` seulement au réseau de Prometheus si possible.

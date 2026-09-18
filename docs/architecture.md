# Architecture et invariants

## Modules

- `engines/epub` : préflight ZIP, lecture EbookLib, DOM lxml, unités avec codes inline, segmentation et réinjection dans une copie de l’archive.
- `providers/llm.py` : OpenAI-compatible, formats structurés, validation, retries, cache, budget et traces.
- `providers/openviking.py` : contrat HTTP OpenViking, authentification API key/trusted, URI, écriture idempotente et retrieval borné.
- `engines/context` : requête narrative, mémoire locale/externe/hybride, sélection, temporalité, budget et inspecteur.
- `engines/memory` : décisions humaines, personnages, glossaire et outbox.
- `engines/translation` : analyse hiérarchique, versions, orchestration et contrôle global.
- `engines/quality` : identifiants d’unités, codes DOM, sorties vides, longueur, répétition, texte inchangé et terminologie.
- `jobs` : prise en charge transactionnelle, bail, fencing, événements persistants et reprise.
- `api` : authentification, autorisations, projets, édition, paramètres, exports et SSE.

## Modèle SQL

Entités normalisées : users, login_sessions, memberships, projects, chapters, segments, translation_versions, entities, glossary, memories, bible_revisions, memory_outbox, prompts, jobs, events, llm_requests, quality_issues, app_settings.

Les documents XHTML/NCX sont des sections de travail ; les subdivisions sémantiques sont conservées dans les unités et `Segment.section`. Le `spine` original est stocké explicitement et ne dépend jamais d’un ordre de noms de fichiers. Les ancres sont déterministes : ressource + XPath + type de champ. Les paragraphes longs peuvent être fragmentés à des frontières linguistiques, puis réassemblés avant réinjection.

## Transactions importantes

1. **Enregistrer une traduction** : vérifier le bail du job, comparer la révision source, insérer une version, puis modifier la version active seulement si autorisé. Les événements mémoire associés entrent dans l’outbox dans la même transaction.
2. **Correction humaine** : contrôle d’accès, révision attendue obligatoire, validation des unités et codes, nouvelle version, mémoire prioritaire si validée, commit.
3. **Reprendre** : invalider l’ancien détenteur du bail. Les écritures d’un résultat tardif sont refusées même si le provider termine sa requête.
4. **Synchroniser** : SQL reste canonique. Un échec externe ne retire jamais un résultat local. Un accusé d’écriture perdu peut être rejoué sur l’URI stable.

Une réponse HTTP reçue juste avant un crash peut être recalculée si elle n’avait pas été commitée. L’application garantit la persistance des résultats commités, pas l’exécution exactly-once d’une inférence distante.

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

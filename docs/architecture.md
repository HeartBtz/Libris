# Architecture et invariants

## Modules

- `engines/epub` : préflight ZIP, lecture EbookLib, DOM lxml, unités avec codes inline, segmentation et réinjection dans une copie de l’archive.
- `providers/llm.py` : OpenAI-compatible, formats structurés, validation, retries, cache, budget et traces.
- `providers/openviking.py` : contrat HTTP OpenViking, authentification API key/trusted, URI, écriture idempotente et retrieval borné.
- `engines/context` : requête narrative, mémoire locale/externe/hybride, sélection, temporalité, budget et inspecteur ; `series.py` : conventions héritées des tomes antérieurs d’une série.
- `engines/memory` : décisions humaines, personnages, glossaire et outbox.
- `engines/translation` : analyse hiérarchique, versions, orchestration, contrôle global, traduction par parties (`repair.py`) et mémoire de traduction (`memory.py`).
- `engines/quality` : identifiants d’unités, codes DOM, sorties vides, longueur, répétition, texte inchangé et terminologie.
- `jobs` : prise en charge transactionnelle, bail, fencing, événements persistants et reprise.
- `api` : authentification, autorisations, projets, édition, paramètres, exports et SSE.

## Modèle SQL

Entités normalisées : users, login_sessions, memberships, projects, chapters, segments, translation_versions, entities, glossary, memories, bible_revisions, memory_outbox, prompts, jobs, events, llm_requests, quality_issues, app_settings.

Les documents XHTML/NCX sont des sections de travail ; les subdivisions sémantiques sont conservées dans les unités et `Segment.section`. Le `spine` original est stocké explicitement et ne dépend jamais d’un ordre de noms de fichiers. Les ancres sont déterministes : ressource + XPath + type de champ. Les paragraphes longs peuvent être fragmentés à des frontières linguistiques, puis réassemblés avant réinjection.

`Chapter.kind` distingue le récit (`narrative`), les documents hors lecture linéaire (`auxiliary`, `linear="no"`, placés après le récit), la navigation (`navigation` : document nav, NCX) et les métadonnées (`metadata` : `dc:description` et `dc:subject` court de l’OPF, traduits comme des passages). `stats.chapters` et `stats.synthesized_chapters` ne comptent que les deux premiers.

Unités : un bloc feuille (`p`, `li`, `td`…) forme une unité ; dans un parent qui mêle texte et blocs, chaque suite de texte et d’éléments en ligne entre deux blocs forme une unité `run` (ancre : parent + indice du bloc qui la précède). Les lectures ruby (`rt`, `rp`), le code, les formules et le texte préformaté sont des marqueurs immuables ; le texte SVG `<text>` et `aria-label` sont traduits ; `pre`, MathML et les titres SVG conservés sont listés dans `book_info.untranslated`. Les paragraphes CJK se coupent sur 。！？… (guillemets fermants compris), puis sur les propositions, puis à la limite.

À l’export, les documents traduits reçoivent la langue et la direction de la cible (`dir="rtl"` pour l’arabe, l’hébreu, le persan, l’ourdou…, `page-progression-direction` du spine en EPUB 3) ; les éléments déclarant la langue source passent à la langue cible, ceux dans une troisième langue gardent langue et direction.

`Segment.source_key` (SHA-256 des unités normalisées NFKC, espaces réduits, marqueurs compris) indexe la mémoire de traduction.

## Transactions importantes

1. **Enregistrer une traduction** : vérifier le bail du job, comparer la révision source, insérer une version, puis modifier la version active seulement si autorisé. Les événements mémoire associés entrent dans l’outbox dans la même transaction.
2. **Correction humaine** : contrôle d’accès, révision attendue obligatoire, validation des unités et codes, nouvelle version, mémoire prioritaire si validée, commit.
3. **Reprendre** : invalider l’ancien détenteur du bail. Les écritures d’un résultat tardif sont refusées même si le provider termine sa requête.
4. **Synchroniser** : SQL reste canonique. Un échec externe ne retire jamais un résultat local. Un accusé d’écriture perdu peut être rejoué sur l’URI stable.

Une réponse HTTP reçue juste avant un crash peut être recalculée si elle n’avait pas été commitée. L’application garantit la persistance des résultats commités, pas l’exécution exactly-once d’une inférence distante.

## Sélection contextuelle

Priorité : instructions > décisions humaines validées > glossaire verrouillé > termes verrouillés de la série > données structurées validées > retrieval externe > synthèses automatiques > voisinage > inférences.

Budget : fenêtre du fournisseur − sortie réservée − schéma de réponse de l’opération − marge contrôlée par `llm.complete`. Les tailles sont mesurées sur les sections sérialisées (échappements et balises compris) ; `input_estimate` est exactement la valeur que `llm.complete` compare à la fenêtre. Si le passage et ses règles obligatoires ne tiennent pas, l’erreur donne les chiffres (fenêtre, sortie, schéma, prompt système, passage, règles) et la fenêtre suffisante. Une première traduction est alors découpée aux frontières de phrase en parties dimensionnées pour la fenêtre (la moitié de ce qui reste après les règles ; l’autre moitié pour le voisinage), traduites avec leur contexte, puis réassemblées ; une révision ne découpe que par unités entières.

Séries (`series.py`) : seuls les tomes antérieurs du même propriétaire, nom de série normalisé (casse, espaces) et langues comparées sur la sous-étiquette primaire (`en-US` ≈ `en`). Pour un même terme, un choix verrouillé l’emporte sur tout choix non verrouillé, puis le tome le plus récent. Les termes verrouillés de série sont contrôlés en sortie comme le glossaire verrouillé du livre, sauf si ce livre verrouille autrement le même terme. Une correction humaine validée d’une traduction automatique enregistre ses remplacements courts (avant/après) et les noms du passage ; un tome ultérieur qui mentionne ces noms reçoit ces choix dans `SERIES_CONVENTIONS.human_decisions`.

Mémoire de traduction (`memory.py`) : avant l’appel du modèle pour une première traduction, un passage terminé de même `source_key` chez le même propriétaire, même langue source (primaire) et même langue cible, est réutilisé ; une version validée par un humain passe d’abord. Dans une série, seuls le même livre et les tomes antérieurs sont admis. Les unités ne sont recopiées que si la structure des marqueurs est valide et que le glossaire verrouillé du livre (série comprise) est respecté. La version porte l’origine `translation_memory` ; relecture, révision et revue finale s’appliquent ensuite normalement. Un relancement forcé interroge toujours le modèle.

Prompts : le contenu des sections échappe `<` et `>` (échappements JSON), si bien qu’un texte du livre ne peut pas fermer une section. `load_prompt` ajoute à chaque prompt, surcharges en base comprises, une clause « données non fiables » et, pour les opérations qui écrivent ou relisent, une règle de registre (tu/vous…) et la typographie de la langue cible ; les langues sont nommées (« French (fr) »). Version : `file-v2` ou `db-vN`, suffixée de `+rules-v1`. Le schéma JSON voyage une seule fois : dans `response_format` en mode structuré, sinon dans un message système.

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

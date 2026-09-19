# Pilote automatique

Le pilote automatique mène un livre importé jusqu’à une sortie sans aucune action humaine : chaque étape
qui attendait une personne est décidée par Libris (ou par le modèle), bornée et journalisée. Les
corrections humaines restent possibles à tout moment ; elles ne sont jamais nécessaires.

## Quand il s’applique

- Activé par défaut (`AUTOPILOT_ENABLED=true`) pour tout lancement portant sur le livre entier :
  import avec démarrage, choix du premier fournisseur, requête de l’API d’automatisation
  (`/api/v1`), boutons **Analyser** et **Traduire** (`POST /api/projects/{id}/jobs` avec
  `operation` = `analyze` ou `translate`, sans chapitre ni passage ciblé).
- Un livre peut s’en écarter (`PUT /api/projects/{id}` avec `"autopilot": false`) ; un lancement aussi
  (`"autopilot": false` dans le corps de `POST …/jobs`). Les travaux ciblés (un chapitre, un passage,
  une sélection de récupération, la file des propositions acceptées) restent des demandes humaines et
  gardent leur comportement.
- Le job porte `options.autopilot = true`.

## Déroulement

1. **Analyse.** Un passage dont l’analyse est refusée ou reste invalide est sauté (décision
   `analysis/chapter_analysis/skipped`) ; un lot de Book Bible en échec aussi. Ensuite, les termes
   proposés, les liens d’identité de série et la Book Bible sont décidés (voir plus bas).
2. **Traduction.** Inchangée. Sous le pilote, la relecture, la révision, le polissage et le plan de
   contexte en échec sont sautés en gardant la traduction existante ; une erreur de modèle sur la
   traduction elle-même marque le passage en échec sans arrêter le livre.
3. **Boucle de convergence**, au plus `AUTOPILOT_MAX_ROUNDS` tours (3 par défaut) :
   1. *échelle de récupération* pour chaque passage en échec ou non traduit ;
   2. premier tour, qualité haute ou maximale : *cohérence globale* ;
   3. *revue finale* (tout le livre au premier tour, puis les passages encore ouverts et ceux récupérés
      pendant le tour) ;
   4. *arbitrage IA* de tout ce qui reste ouvert.

   La boucle s’arrête dès qu’aucun passage n’est en échec ni ouvert (stable).
4. **Clôture.** Les points encore ouverts après le dernier tour sont clos sur la traduction actuelle
   (décision `settle/open_points/kept_translation` avec la liste des points) ; un passage encore en
   échec garde son texte original (`source_retained`) avec la raison. Aucun passage automatique ne
   termine en `check`, `error`, `refused` ou non traduit. Les passages corrigés par une personne ne
   sont jamais modifiés, y compris lorsqu’ils sont encore « à vérifier ».

Le tour et la phase sont enregistrés dans le checkpoint (`autopilot_round`, `autopilot_phase`) : un job
repris continue où il en était, sans refaire ce qui est réglé (`job_segment_state`,
`autopilot_ladder` et `autopilot_arbitrated` par tour).

## Échelle de récupération d’un passage

Du moins cher au plus cher, jusqu’au premier succès :

1. nouvel essai informé : le même fournisseur reçoit la raison de l’échec précédent ;
2. réparation par groupes de quatre paragraphes (passages de plusieurs paragraphes) ;
3. découpage en phrases, traduites une à une puis réassemblées ;
4. contexte réduit : le passage et ses règles obligatoires seulement ;
5. chaque fournisseur de secours : nouvel essai informé, puis découpage en phrases ;
6. dernier recours : texte original conservé (`source_retained`), avec la liste des tentatives.

Chaque réponse passe les validations habituelles (paragraphes, marqueurs, glossaire verrouillé).

## Fournisseurs de secours et pannes

La chaîne est : le fournisseur du job, celui du livre, `config.fallback_provider_ids` du livre (réglable
par `PUT /api/projects/{id}`), puis `AUTOPILOT_FALLBACK_PROVIDERS` (noms ou identifiants séparés par des
virgules). Une panne est attendue comme d’habitude (délais `PROVIDER_RECOVERY_*`) au plus
`AUTOPILOT_OUTAGE_MAX_RETRIES` fois et `AUTOPILOT_OUTAGE_MAX_WAIT_SECONDS` secondes ; ensuite le job
passe au fournisseur suivant (`provider/outage/fallback_provider`). Des identifiants refusés font passer
au suivant immédiatement. Quand plus aucun fournisseur ne répond, le job se termine `failed`
(`stop_reason = providers_exhausted`) avec la raison : il n’attend jamais indéfiniment. Le fournisseur du
livre n’est pas modifié.

## Arbitrage IA

Sont arbitrés : les critiques des relectures, les doutes (`uncertainties`), les remarques de cohérence
globale, les avertissements des contrôles automatiques et tout autre problème non résolu du passage.
Un seul appel par passage décide de tous ses points, et seuls les passages qui en ont sont envoyés
(opération `autopilot_arbitration`, prompt `prompts/autopilot_arbitration.txt`). Le modèle renvoie une
décision par point et uniquement les paragraphes modifiés. Une correction n’est appliquée que si le
texte obtenu passe les validations d’une traduction ; sinon tous les points de l’appel sont rejetés
avec cette raison. Un appel refusé laisse les points ouverts pour le tour suivant. Un avertissement
déjà tranché n’est pas soulevé une seconde fois.

## Décisions sur la mémoire

Sans appel au modèle, à partir des données du livre :

| Proposition | Décision | Réglage |
| --- | --- | --- |
| Terme de glossaire proposé | confiance selon les occurrences dans le texte source (0 : 0 ; 1 : 0,6 ; 2 : 0,8 ; 3 et plus : 1) ; accepté au-dessus du seuil, sinon retiré | `AUTOPILOT_GLOSSARY_MIN_CONFIDENCE` (0,75) |
| Lien d’identité de série ambigu | confiance = noms communs / noms réunis, ±genre ; le meilleur candidat est lié s’il atteint le seuil avec 0,1 d’avance, sinon tous sont rejetés et le personnage reste propre au volume (jamais de fusion) | `AUTOPILOT_IDENTITY_MIN_CONFIDENCE` (0,8) |
| Book Bible | validée quand la part des passages analysés atteint le seuil | `AUTOPILOT_BIBLE_MIN_COVERAGE` (0,8) |
| Chapitre au contexte périmé (`context_stale`) | l’indicateur est levé quand ce job a retraduit ou relu la part voulue de ses passages | `AUTOPILOT_STALE_MIN_COVERAGE` (0,5) |

Un lien rejeté par le pilote n’est pas reproposé par les rafraîchissements de la série.

## Journal et rapport

Chaque décision est une ligne de `autopilot_decisions` (`stage`, `kind`, `action`, `reason`,
fournisseur et modèle concernés, job et passage éventuels). À la fin, `jobs.result["autopilot"]` vaut :

```json
{"outcome": "completed" | "completed_with_residuals" | "failed",
 "rounds": 2,
 "residuals": [{"segment_id": "…", "chapter_id": "…", "reason": "Texte original conservé automatiquement : …"}],
 "reason": null}
```

`GET /api/projects/{id}/autopilot?limit=50&offset=0[&job_id=…][&segment_id=…][&stage=…]` renvoie
l’activation effective, les réglages, le dernier rapport (avec `job_id`, `status`, `finished_at`) et les
décisions paginées, les plus récentes d’abord.

## Coût

L’arbitrage n’appelle le modèle que pour les passages qui ont des points ouverts, un appel par passage.
L’échelle de récupération ne concerne que les passages en échec. Les tours suivants ne relisent que les
passages encore ouverts.

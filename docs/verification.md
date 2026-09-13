# Vérifications de la première livraison — 12 septembre 2026

## Exécuté

| Vérification | Résultat |
|---|---|
| Suite pytest | 42 tests réussis |
| Ruff | Aucun diagnostic bloquant |
| TypeScript strict + build Vite | Réussi |
| Construction de l’image Docker | Réussie |
| Migrations PostgreSQL | Appliquées |
| Alembic / modèle de données | Pas de migration manquante au contrôle |
| Smoke test HTTP sur PostgreSQL | Réussi |
| Pause / reprise | Réussie |
| Arrêt SIGKILL du worker pendant un livre | Reprise réussie, traductions enregistrées conservées |
| Export EPUB du smoke test | Accepté par EPUBCheck 5.3.0 |
| Playwright desktop / mobile / paramètres provider | 3 scénarios réussis |
| OpenViking réel — écriture / lecture | Contenu L2 identique au contenu canonique |
| OpenViking réel — find / search | Résultats récupérés et filtrés par position narrative |
| OpenViking réel — prompt hybride | Source OPENVIKING_FIND effectivement injectée après déduplication |

Le corpus du smoke test est **synthétique**, et son endpoint de traduction est un mock OpenAI-compatible explicitement nommé `synthetic-literary-test`. Ce test vérifie le produit, le protocole et la reprise, pas la qualité d’un modèle littéraire réel.

## Corrections issues des essais

- Permissions de lecture de l’image pour son utilisateur non-root.
- Compatibilité des filtres JSON avec PostgreSQL.
- Version d’un brouillon React conservée pendant les rafraîchissements SSE.
- Contrôle de l’enregistrement et de sa réapparition dans la preview.
- Débordement de l’en-tête en petite largeur.
- Champs de métadonnées du provider exclus du formulaire de mise à jour.
- Création explicite OpenViking après `replace` renvoyant 404.
- Respect des permissions de réindexation OpenViking, distinctes des permissions d’écriture.
- Exclusion des décisions humaines obsolètes du retrieval interne.
- Rejet des réponses tronquées, paragraphes manquants, marqueurs invalides et blocs de reasoning dans la traduction.

## À valider sur le modèle choisi

La qualification d’un roman long reste une tâche distincte : provider réel, langue source/cible, corpus autorisé, prompts retenus, relecture bilingue. Le protocole figure dans `quality-evaluation.md`.

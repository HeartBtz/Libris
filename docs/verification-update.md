# Mise à jour fonctionnelle — septembre 2026

Vérifications exécutées pendant l’implémentation :

- 108 tests backend réussis : import/reconstruction, accès, providers, concurrence indépendante par provider, changement de provider après pause, Codex, reprise, interruptions, refus avec poursuite du livre et provider de remplacement limité à la récupération, récupération obligatoire avant revue finale, verdict final obligatoire sans délégation à l’humain, acceptation ou rejet des propositions IA, couverture, identités, graphes, catalogues, séries, archivage réversible, progression canonique et revue finale bornée (corrections vérifiées, choix humains protégés, reprise et SearXNG facultatif).
- TypeScript strict, build Vite et Ruff validés.
- Schéma SQLite de développement validé ; chaîne Alembic montée, rétrogradée puis remontée sur PostgreSQL 17 temporaire. Le même contrôle est exécuté dans GitLab CI.
- Mémoire inter-volumes vérifiée : glossaire accepté et décision humaine validée du volume antérieur présents, mémoire narrative et convention du volume futur absentes.
- Calcul centralisé de l’étape active, du bilan de revue et des estimations temps/coût validé par le backend et consommé sans recalcul divergent par l’interface.
- Déploiement différencié validé syntaxiquement : mise à jour API sans interruption du worker ou attente d’une absence de job actif avant son redémarrage.
- Test Docker réel : HTTP 503 → attente persistante ; pause volontaire conservée après redémarrage ; SIGTERM pendant l’inférence → reprise sans retraduire les étapes terminées ; pause en cours de requête → interruption auditée.
- Test navigateur import multiple, graphe, fusion, alias, lien validé, conservation d’un brouillon lors d’Actualiser et suppression depuis la bibliothèque : réussi.
- Test navigateur sélection multiple : pause, reprise, annulation des analyses, annulation des traductions et suppression confirmée : réussi.
- Test navigateur sur la bibliothèque existante : une barre unique liée à l’étape active de chaque livre et actualisation effective des métriques : réussi.
- Test navigateur synthétique des cinq étapes : une seule barre visible, sélection de chaque étape, absence de débordement mobile et export marqué terminé uniquement après réception du fichier : réussi.
- Tome 1 utilisé pour la vérification OpenViking : `book.md`, `book-bible.json`, `characters.json` et `relationships.json` lus et identiques au snapshot SQL publié ; catalogue retrouvé dans l’index. Cela ne prouve pas l’indexation exhaustive de chaque événement.
- Audit indicatif des résumés automatiques déjà stockés : aucun candidat évident de refus détecté par le détecteur. Une relecture demeure nécessaire pour qualifier la fidélité littéraire.

Les tests d’incident et d’édition utilisent des projets synthétiques séparés, supprimés à la fin. Les tests de consultation des progressions et des métriques sur un livre existant sont en lecture seule.

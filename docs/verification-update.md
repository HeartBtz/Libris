# Mise à jour fonctionnelle — septembre 2026

Vérifications exécutées pendant l’implémentation :

- 79 tests backend réussis : import/reconstruction, accès, providers, Codex, reprise, interruptions, refus avec poursuite du livre et provider de remplacement, couverture, identités, graphes et catalogues.
- TypeScript strict, build Vite et Ruff validés.
- Migrations SQLite de développement et PostgreSQL Docker vérifiées.
- Test Docker réel : HTTP 503 → attente persistante ; pause volontaire conservée après redémarrage ; SIGTERM pendant l’inférence → reprise sans retraduire les étapes terminées ; pause en cours de requête → interruption auditée.
- Test navigateur import multiple, graphe, fusion, alias, lien validé, conservation d’un brouillon lors d’Actualiser et suppression depuis la bibliothèque : réussi.
- Test navigateur sélection multiple : pause, reprise, annulation des analyses, annulation des traductions et suppression confirmée : réussi.
- Test navigateur sur la bibliothèque existante : deux progressions indépendantes par livre et actualisation effective des métriques : réussi.
- Tome 1 utilisé pour la vérification OpenViking : `book.md`, `book-bible.json`, `characters.json` et `relationships.json` lus et identiques au snapshot SQL publié ; catalogue retrouvé dans l’index. Cela ne prouve pas l’indexation exhaustive de chaque événement.
- Audit indicatif des résumés automatiques déjà stockés : aucun candidat évident de refus détecté par le détecteur. Une relecture demeure nécessaire pour qualifier la fidélité littéraire.

Les tests d’incident et d’édition utilisent des projets synthétiques séparés, supprimés à la fin. Les tests de consultation des progressions et des métriques sur un livre existant sont en lecture seule.

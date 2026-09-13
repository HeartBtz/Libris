# Revue finale des validations

Une traduction de livre complet lance automatiquement une revue finale après les contrôles de cohérence. Pour un livre déjà traduit, **Validations → Lancer la revue IA** crée un job dédié dès qu'aucun autre travail n'occupe le livre. Ce job utilise la limite de concurrence et le provider du livre.

## Ce qui est automatique

1. Sélection des passages à vérifier qui ont une traduction et ne sont ni protégés humainement, ni validés, ni conservés en langue source.
2. Réexamen du texte **actuel** : une ancienne remarque peut avoir déjà été corrigée pendant la traduction.
3. Si le doute subsiste, une tentative de correction complète, avec préservation des identifiants, marqueurs et termes verrouillés.
4. Vérification de cette correction par une nouvelle requête IA et les contrôles déterministes.
5. Application uniquement si ces vérifications ne signalent plus de problème. Sinon, le texte initial reste intact.

Un passage résolu quitte la file sans être déclaré « validé humainement ». Les alertes globales non recalculées restent présentes. Les décisions humaines, y compris celles prises pendant l'inférence, ont priorité.

Chaque passage fait l'objet d'au plus un cycle logique de correction par job. Les appels peuvent avoir les retries habituels du provider. Checkpoints et cache permettent la reprise après interruption ; une réponse invalide ou un refus de relecture laisse le passage à l'humain et n'arrête pas la revue du livre.

## Recherche web optionnelle : SearXNG

Dans **Paramètres → SearXNG**, renseignez l’URL, testez la connexion JSON, cochez l’activation puis enregistrez. La configuration est persistée en base et prise en compte aux prochaines recherches sans redémarrage. Les réglages enregistrés prennent le dessus sur les variables d’environnement, y compris lorsque la recherche est désactivée. L’administration de cette intégration est réservée aux administrateurs.

```dotenv
FINAL_REVIEW_ENABLED=true
SEARXNG_URL=http://your-searxng:8080
```

Configurez votre propre instance SearXNG pour autoriser le format de recherche JSON. Appliquez ensuite la configuration à l'API et au worker. Une URL vide désactive toute recherche. `FINAL_REVIEW_ENABLED=false` désactive seulement le lancement automatique, pas le bouton manuel.

Dans le `settings.yml` de SearXNG, la liste `search.formats` doit inclure `json` (sinon l'API renvoie 403). Voir la [documentation de l'API SearXNG](https://docs.searxng.org/dev/search_api.html).

Si l'IA demande une vérification terminologique, Libris envoie au maximum deux recherches de 200 caractères chacune, avec trois résultats par recherche et un délai de 15 secondes par appel. Les requêtes demandées au modèle doivent porter uniquement sur des termes ou références, pas des extraits complets du livre. Les termes recherchés sont néanmoins transmis à SearXNG et potentiellement à ses moteurs amont : n'activez cette option que si cela convient à vos documents.

Les résultats (URL, titre, extrait) servent d'indices non fiables, jamais d'instructions. Aucune page de résultat n'est téléchargée séparément. Les sources et l'explication sont conservées dans les événements du projet ; le contexte envoyé au modèle est visible dans les traces des requêtes. Une recherche indisponible ne vaut pas confirmation.

Libris n'installe pas automatiquement SearXNG et n'en exige pas la présence. La revue sans recherche s'appuie sur le livre, sa mémoire et le glossaire. La validation par un modèle réduit le travail manuel mais ne garantit pas la fidélité littéraire.

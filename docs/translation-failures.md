# Échecs de traduction et poursuite du livre

- Après **5 réponses invalides** sur un passage (paragraphes ou marqueurs altérés, format inexploitable), Libris tente une [réparation bornée par petits groupes](recovery.md). Si elle est impossible ou échoue à son tour, Libris conserve le texte existant, signale une erreur et poursuit le passage suivant.
- Un refus de contenu conserve sa limite de **2 tentatives**, puis le passage est signalé comme refusé.
- Ces deux catégories alimentent un compteur commun : **10 passages consécutifs en échec arrêtent le job**. Il ne s'agit pas de dix requêtes HTTP, mais de dix passages après épuisement de leurs tentatives.
- Un passage traité avec succès remet le compteur à zéro.
- Les indisponibilités du service et erreurs d'authentification gardent leur mécanisme d'attente ou d'intervention ; elles ne sont pas assimilées à un mauvais passage.

Les passages ignorés sont mémorisés dans le checkpoint : une reprise automatique ne recommence pas en boucle sur eux. Une reprise manuelle remet le compteur de sécurité à zéro. Pour retenter un passage ignoré, lancez une nouvelle traduction ciblée ou un nouveau travail de traduction. Les erreurs restent visibles dans Qualité et ne sont pas comptées comme des traductions réussies. L'export complet conserve ses contrôles de couverture.

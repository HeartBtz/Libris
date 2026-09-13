# Réparation automatique et récupération

## Réponse structurellement invalide

Après l'épuisement des cinq tentatives d'une traduction ou révision, Libris tente une réparation par groupes de quatre unités si le passage comporte entre 2 et 128 unités. Chaque groupe conserve son contexte, ses identifiants et ses marqueurs. Les groupes réussis sont sauvegardés dans le checkpoint et réutilisés après une interruption ; seul le résultat intégral réassemblé et validé peut remplacer la traduction du passage.

La réparation est bornée : une passe de groupes, au plus 32 groupes, avec les tentatives habituelles par groupe. Elle ajoute donc des appels en cas d'échec. Si elle échoue, le passage rejoint les erreurs et le compteur des dix passages consécutifs échoués s'applique. Les indisponibilités restent reprenables. Les choix humains conservent la priorité.

## Bilan & récupération

L'onglet du livre distingue le dernier état du traitement de la couverture réelle. Il présente les passages traduits, manquants, conservés en original, les alertes et les choix humains protégés. EPUBCheck reste exécuté au moment de l'export : la couverture seule n'est pas une certification.

La liste regroupe les passages manquants, en erreur, refusés ou bloqués. Filtrez, sélectionnez jusqu'à 200 passages et choisissez le provider de récupération. La sélection est vérifiée par le serveur et les passages protégés sont exclus. Un autre job actif sur le livre doit finir ou être annulé avant cette reprise ciblée. Le provider principal du projet ne change pas.

## Accepter une proposition contenant des balises

Si une suggestion libre ne préserve pas les marqueurs internes, l'acceptation demande une correction structurée de la seule unité visée au provider du livre. Le résultat est vérifié avant enregistrement. En cas de réponse invalide ou de modification concurrente, le texte actuel est conservé. Cette opération peut prendre le temps d'une requête au modèle.

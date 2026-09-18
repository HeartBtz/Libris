# Réparation automatique et récupération

## Réponse structurellement invalide

Une réponse refusée par la validation (paragraphes manquants, glossaire verrouillé, JSON invalide) est redemandée en indiquant au modèle le motif du rejet, au plus trois fois ; les pannes réseau gardent cinq essais. Après l'épuisement de ces tentatives pour une traduction ou révision, Libris tente une réparation par groupes de quatre unités si le passage comporte entre 2 et 128 unités. Chaque groupe conserve son contexte, ses identifiants et ses marqueurs. Les groupes réussis sont enregistrés dans l'état du job (`job_segment_state`) et réutilisés après une interruption ; seul le résultat intégral réassemblé et validé peut remplacer la traduction du passage.

La réparation est bornée : une passe de groupes, au plus 32 groupes, avec les tentatives habituelles par groupe. Elle ajoute donc des appels en cas d'échec. Si elle échoue, le passage rejoint les erreurs et le compteur des dix passages consécutifs échoués s'applique. Les indisponibilités restent reprenables. Les choix humains conservent la priorité.

## Interruption pendant des passages en parallèle

Plusieurs passages d'un livre peuvent être en cours au moment d'une pause, d'une panne du provider ou d'un arrêt du worker. Tous les appels en vol sont alors interrompus et journalisés comme tels ; aucun passage n'est marqué terminé avant d'avoir enregistré toutes ses étapes. À la reprise, les passages terminés sont sautés sans nouvel appel ni nouvel événement, et les passages interrompus reprennent à partir de leur dernière étape enregistrée (une traduction déjà appliquée n'est pas redemandée, seule l'étape suivante l'est). `WORKER_BOOK_PARALLELISM=1` rétablit le traitement d'un passage à la fois.

## Bilan & récupération

L'onglet du livre distingue le dernier état du traitement de la couverture réelle. Il présente les passages traduits, manquants, conservés en original, les alertes et les choix humains protégés. EPUBCheck reste exécuté au moment de l'export : la couverture seule n'est pas une certification.

La liste regroupe les passages manquants, en erreur, refusés ou bloqués. Filtrez, sélectionnez jusqu'à 200 passages et choisissez le provider de récupération. La sélection est vérifiée par le serveur et les passages protégés sont exclus. Un autre job actif sur le livre doit finir ou être annulé avant cette reprise ciblée. Le provider principal du projet ne change pas.

## Accepter une proposition contenant des balises

Si une suggestion libre ne préserve pas les marqueurs internes, l'acceptation demande une correction structurée de la seule unité visée au provider du livre. Le résultat est vérifié avant enregistrement. En cas de réponse invalide ou de modification concurrente, le texte actuel est conservé. Cette opération peut prendre le temps d'une requête au modèle.

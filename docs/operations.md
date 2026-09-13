# Bibliothèque, reprise et couverture

## Plusieurs livres

La bibliothèque accepte plusieurs EPUB en un import (deux fichiers traités simultanément à l’import). Les cases de sélection permettent de configurer, analyser, traduire, mettre en pause, reprendre, annuler les analyses, annuler les traductions et supprimer les projets sélectionnés.

La suppression est confirmée une seule fois pour la sélection. Elle arrête les travaux concernés et supprime le projet local. Les documents déjà présents dans OpenViking restent distincts.

Le worker exécute au maximum une analyse et une traduction simultanément (`ANALYSIS_CONCURRENCY=1`, `TRANSLATION_CONCURRENCY=1`). Les relectures et contrôles de cohérence partagent le slot de traduction. Les requêtes restent aussi soumises à `max_concurrency` de chaque provider. Les passages d’un même livre restent séquentiels.

## Les deux progressions

- **Analyse, en bleu** : proportion des passages analysés et sections synthétisées dans la Book Bible parmi ces unités de travail. La synthèse compte dans la progression ; 100 % exige que les deux étapes soient terminées. Ce pourcentage représente la couverture, pas une estimation du temps restant.
- **Traduction, en doré** : passages disposant d’une traduction, hors passages explicitement conservés en langue source.

Les détails sont disponibles au survol. Chaque livre possède aussi un bouton **Actualiser**, qui recharge statistiques, jobs et panneaux sans remplacer un brouillon de traduction en cours.

Cliquer **Analyser** sur un livre entièrement analysé est une opération sans recalcul. Une réanalyse complète est une action distincte, confirmée ; les analyses humaines sont conservées.

## Arrêts et erreurs

| Situation                     | État / suite                                                                                                     |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Pause volontaire              | `paused`, reprise explicite uniquement                                                                           |
| Annulation                    | `cancelled`, résultats conservés ; reprise explicite possible                                                    |
| Arrêt propre du worker        | requête interrompue, travail remis en attente au checkpoint                                                      |
| Arrêt brutal                  | récupération après expiration du bail de 60 s                                                                    |
| Réseau, timeout, HTTP 429/5xx | `waiting`, nouvelle tentative planifiée, délai progressif de 30 s à 15 min ; `Retry-After` respecté jusqu’à 24 h |
| Authentification invalide     | `blocked`, reconnexion et reprise nécessaires                                                                    |
| Refus pendant l’analyse       | `blocked` / `content_refusal`, intervention humaine nécessaire                                                   |
| Refus pendant la traduction   | deuxième essai, puis passage marqué `refused` et poursuite du livre                                              |
| JSON ou structure invalide    | retries bornés, puis erreur localisée                                                                            |

Un contrôle de bail toutes les deux secondes détecte les pauses et annulations. Les écritures sont protégées par la révision du passage et le détenteur du bail. Un résultat tardif ne remplace pas une correction humaine ou un état annulé. Les étapes initiale, critique, révision et synthèse ont leurs checkpoints.

## Refus et absence de trous silencieux

Les refus explicites du provider, les filtres de contenu et les réponses contenant un refus à la place d’un résultat sont distingués des pannes. Le texte original est toujours conservé dans le projet. Pour une traduction, Libris effectue exactement deux tentatives, marque ensuite le passage comme refusé et continue avec le passage suivant. Un refus n’est pas comptabilisé comme une traduction réussie.

L’onglet **Validations** regroupe les passages refusés. Une reprise ciblée permet de choisir un autre provider, notamment un modèle non censuré, et de retraduire uniquement ces passages. Le provider principal du livre n’est pas modifié. Chaque nouvelle reprise dispose à nouveau de deux tentatives par passage et laisse les autres traductions intactes.

Résolutions dans le workspace :

1. saisir une traduction humaine ;
2. ouvrir l’inspecteur, onglet **Analyse humaine**, et fournir un résumé utile à la continuité ;
3. choisir explicitement **Conserver l’original pour l’export**.

Conserver l’original ne remplace pas une analyse manquante. Pour un refus de synthèse globale, une Book Bible humaine avec un résumé non vide peut être validée. Le travail peut ensuite être repris.

L’export EPUB normal demande une traduction complète. **EPUB partiel · originaux conservés** inclut le texte source aux endroits non traduits et porte un nom de fichier distinct. Le **rapport de couverture** liste les passages sans analyse, sans traduction et ceux conservés en original. L’original retenu n’est pas présenté comme une traduction validée.

## Personnages et OpenViking

L’onglet **Personnages & liens** propose recherche, déplacement, zoom, fusion d’identités, alias et liens dirigés. Les liens IA ou tirés d’anciennes fiches sont en pointillés ; les validations humaines sont identifiées. Les fusions conservent leurs fiches historiques et leurs snapshots.

Les alias confirmés humainement restent prioritaires. Pronoms et descriptions relationnelles ne deviennent pas automatiquement des alias globaux. Les variantes incertaines sont stockées comme propositions.

En mode Hybrid/OpenViking, un catalogue nommé est publié et actualisé :

- `book.md` : titre, auteur, progression et liens ;
- `book-bible.json` : synthèse éditoriale ;
- `characters.json` et pages associées : identités et alias ;
- `relationships.json` et pages associées : liens, provenance et validation.

Dans **Book Bible → Mémoire OpenViking**, les liens ouvrent les fichiers réellement lus sur l’instance distante à travers le backend. **Synchroniser le livre et le graphe** fonctionne même pendant une analyse. **Vérifier dans OpenViking** distingue lecture et présence dans l’index.

Les documents globaux sont séparés du retrieval narratif, limité aux événements admissibles sous `/events`. Les fichiers de catalogue sont des projections contextuelles compactes ; SQL conserve les données complètes et exactes.

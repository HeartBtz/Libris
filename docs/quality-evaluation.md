# Évaluer la traduction d’un livre

## Corpus

Constituer un texte légalement utilisable avec plusieurs dizaines de chapitres, et un jeu de références relues par une personne bilingue. Inclure des rappels éloignés et des variations de source ; répéter uniquement le même paragraphe ne constitue pas un benchmark de mémoire narrative.

Cas indispensables :

1. Nom inventé et variantes typographiques ; aucune variation injustifiée de traduction.
2. Personnage identifié tardivement ; préserver les pronoms ambigus avant la révélation.
3. Objet offert au chapitre 1, réinterprété au chapitre 15, offert de nouveau au chapitre 30.
4. Tutoiement/vouvoiement, changement de registre motivé et changement non motivé.
5. Narrateur peu fiable ; croyances du personnage distinctes des faits.
6. Idiome ou humour récurrent ; cohérence d’effet sans répétition artificielle.
7. Correction humaine au chapitre 20 qui doit influencer le chapitre 35 et rester restaurable.
8. Phrase avec emphase, liens et appel de note ; conservation du sens et du DOM.

## Comparaison contrôlée

Dupliquer le même projet avant traduction et conserver modèle, paramètres et prompts identiques. Comparer Internal, OpenViking et Hybrid. Archiver les prompts réellement injectés, les choix de retrieval et la version des règles.

Noter séparément, à l’aveugle si possible :

- fidélité du sens, omissions et ajouts ;
- stabilité des noms, voix, pronoms et relations ;
- traitement des ambiguïtés et révélations ;
- naturel de la langue et effet littéraire ;
- respect des décisions humaines ;
- structure EPUB et formatage.

Mesurer le nombre de corrections humaines par 1 000 mots, les violations terminologiques, les références mal résolues, les appels de deep retrieval utiles/inutiles, la latence et la consommation de tokens. Les opinions du même LLM évaluant sa propre traduction ne remplacent pas cette relecture.

## Mesures intégrées aux tests

Les reproductions de l’audit qualité du 18 septembre 2026 font partie de la suite (`tests/test_prompt_hardening.py`, `test_series_conventions.py`, `test_segmentation.py`, `test_book_structure.py`, `test_small_windows.py`, `test_rtl.py`, `test_translation_memory.py`) :

- texte du livre contenant `</TARGET_TEXT>` : une seule section ouverte et fermée ;
- série : terme verrouillé du tome 1 conservé au tome 3 malgré un terme non verrouillé du tome 2, `en`/`en-US` et casse appariés, contrôle en sortie, rien des tomes suivants ni d’un autre propriétaire ;
- paragraphe japonais de 9 000 caractères découpé après 。 ; ruby conservé ; contenu mixte en une unité ;
- fenêtre de 16k : un long passage CJK traduit par parties ; fenêtre de 8k : erreur chiffrée ;
- mémoire de traduction sur un livre synthétique à cinq interludes identiques : 14 passages sur 22 réutilisés (les cinq `<title>` identiques de chaque document comptent), 8 appels au modèle au lieu de 22.

Le compteur `stats.translation_memory_reused` d’un projet donne le même taux sur un vrai livre.

## Critère de livraison

Une sortie techniquement valide autorise l’export. Une qualification de qualité littéraire exige une évaluation documentée sur les textes et langues visés. La version initiale fournit les traces et la boucle de correction nécessaires, mais ne prétend pas avoir déjà démontré une cohérence parfaite sur 500 pages.

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

## Critère de livraison

Une sortie techniquement valide autorise l’export. Une qualification de qualité littéraire exige une évaluation documentée sur les textes et langues visés. La version initiale fournit les traces et la boucle de correction nécessaires, mais ne prétend pas avoir déjà démontré une cohérence parfaite sur 500 pages.

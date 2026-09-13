# Connecter Codex

Libris propose deux connexions distinctes :

| Provider                                 | Authentification              | Exécution                                               |
| ---------------------------------------- | ----------------------------- | ------------------------------------------------------- |
| **Codex · compte ChatGPT**               | Code d’appareil officiel      | CLI Codex 0.154.0 / app-server dans un connecteur dédié |
| **Codex / OpenAI · clé API (Responses)** | Clé API chiffrée côté backend | `POST /v1/responses`                                    |

Les deux utilisent le pipeline existant : contexte hybride OpenViking/SQL, prompts versionnés, schémas JSON, glossaire, contrôles, historique et reprise.

## Compte ChatGPT / abonnement Codex

Sur une nouvelle installation ou pour activer le connecteur :

```bash
python3 scripts/enable_codex.py
docker compose --profile codex up -d --build
```

Le script ajoute seulement une clé interne dédiée à `.env`. Il ne lit ni ne copie aucune authentification Codex personnelle déjà présente sur la machine.

Dans **Paramètres → Providers LLM** :

1. Créer un provider ; choisir **Codex · compte ChatGPT**.
2. Donner un nom et enregistrer. Le modèle peut être choisi après connexion.
3. Cliquer sur **Se connecter avec ChatGPT**.
4. Ouvrir le lien officiel OpenAI affiché et saisir le code temporaire dans son interface.
5. Si nécessaire, activer l’authentification par code d’appareil dans les paramètres de sécurité ChatGPT / du workspace.
6. Lorsque l’état devient **Connecté**, cliquer sur **Vérifier / détecter les modèles Codex**.
7. Choisir un modèle du catalogue retourné, puis **Enregistrer**.
8. Choisir ce provider dans la configuration du projet de traduction.

Le provider est partagé par les projets qui le sélectionnent, comme les autres providers administrés. Pour plusieurs comptes Codex, créer plusieurs providers : chaque identifiant possède son propre `CODEX_HOME` et processus app-server.

Les sessions de connexion persistent dans le volume `codex-state`. Il contient des credentials sensibles et doit être protégé comme `.env`. **Déconnecter ce compte** efface la connexion via l’API officielle Codex. Une nouvelle connexion utilisateur est nécessaire si la session expire et ne peut pas être renouvelée.

### Limites propres à ce transport

- Codex appelle OpenAI ; ce n’est pas une inférence locale. Les quotas et conditions de l’abonnement s’appliquent.
- Les paramètres température et top-p ne sont pas envoyés à app-server.
- La limite de sortie est une **réservation de budget de l’application** : le protocole de tour utilisé ne fournit pas de plafond équivalent à `max_output_tokens`.
- Les livres peuvent utiliser des threads Codex distincts en parallèle, dans la limite du worker et du provider. La connexion de compte est partagée ; une interruption vise uniquement le tour du livre concerné.
- L’inspecteur enregistre les messages fournis à Codex. **Codex construit aussi son propre encadrement de requête** ; ce transport est signalé par `upstream_prompt_managed_by_codex=true` et ne prétend pas exposer l’intégralité du prompt HTTP interne de Codex.
- Chaque appel utilise un thread éphémère neuf. La continuité narrative provient de la mémoire de Libris, pas d’un historique Codex partagé entre livres.

## Clé API OpenAI

Créer un provider **Codex / OpenAI · clé API (Responses)**, renseigner :

```text
Base URL : https://api.openai.com/v1
Clé API : votre clé OpenAI Platform
Modèle : un modèle Responses/Codex autorisé pour votre compte
```

Ce mode ne requiert pas le connecteur Docker Codex. L’API est facturée séparément de l’abonnement ChatGPT. Les tarifs par million de tokens sont facultatifs et à renseigner selon le modèle.

L’adaptateur envoie `tools=[]`, `tool_choice=none`, `store=false`, et utilise `text.format` pour les sorties structurées. Seuls les éléments `output_text` des messages assistant sont traduits ; reasoning et refus sont distingués, et une réponse incomplète est rejetée.

## Isolation

Le connecteur ne publie aucun port sur le LAN. Les échanges internes nécessitent une clé dédiée. Le conteneur n’a ni volume de livres, ni accès aux fichiers de l’application ou au socket Docker. Le processus Codex reçoit un environnement filtré sans cette clé interne ni les secrets SQL.

Shell, recherche web, images, plugins et sous-agents sont désactivés dans sa configuration. Les tours sont en lecture seule avec réseau des outils désactivé. Les requêtes serveur sollicitant un outil ou une autorisation sont refusées par le client. Les réponses de raisonnement et de commentaire ne deviennent pas des traductions finales.

## Références vérifiées

- [Codex App Server](https://developers.openai.com/codex/app-server)
- [Authentification officielle et code d’appareil](https://developers.openai.com/codex/auth)
- [Protocole de la version 0.154.0](https://github.com/openai/codex/tree/rust-v0.154.0/codex-rs/app-server-protocol)

La connexion à un compte réel doit être réalisée par son titulaire. Les essais sans compte vérifient le démarrage du binaire, son catalogue et son état d’authentification ; les tests simulés vérifient le transport et la sélection de réponse finale.

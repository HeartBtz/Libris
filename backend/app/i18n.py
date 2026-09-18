"""English versions of the API's user-facing error messages.

Messages are written in French where they are raised; the exception handlers translate the
`detail` of an error answer when the request's Accept-Language prefers English. Messages with
values in them are matched by templates whose `{name}` placeholders carry the values over.
A test checks statically that every message raised in the code has an entry here.
"""

import re
from functools import lru_cache

MESSAGES = {
    # Accounts and access
    "Connexion nécessaire.": "Sign-in required.",
    "Authentification requise.": "Authentication required.",
    "Compte introuvable.": "Account not found.",
    "Identifiants incorrects.": "Incorrect username or password.",
    "Mot de passe actuel incorrect.": "Current password is incorrect.",
    "Ce nom d’utilisateur est déjà utilisé.": "This username is already taken.",
    "Vous ne pouvez pas retirer votre propre accès administrateur.": "You cannot remove your own administrator access.",
    "Au moins un administrateur actif est nécessaire.": "At least one active administrator is required.",
    "Cette action nécessite un administrateur.": "This action requires an administrator.",
    "Trop de tentatives échouées. Réessayez dans cinq minutes.": "Too many failed attempts. Try again in five minutes.",
    "Utilisateur introuvable.": "User not found.",
    "Origine non autorisée. Configurez ALLOWED_ORIGINS.": "Origin not allowed. Configure ALLOWED_ORIGINS.",
    "Requête intersite refusée.": "Cross-site request refused.",
    "Route API inconnue.": "Unknown API route.",
    "Métriques désactivées. Définissez METRICS_TOKEN.": "Metrics are disabled. Set METRICS_TOKEN.",
    "Jeton de métriques manquant ou invalide.": "Missing or invalid metrics token.",
    "Cette entrée existe déjà, ou une référence est invalide.": "This entry already exists, or a reference is invalid.",
    # Books and jobs
    "Projet introuvable.": "Project not found.",
    "Projet introuvable ou accès insuffisant.": "Project not found or insufficient access.",
    "Chapitre introuvable.": "Chapter not found.",
    "Passage introuvable.": "Passage not found.",
    "Travail introuvable.": "Job not found.",
    "Requête introuvable.": "Request not found.",
    "Version introuvable.": "Version not found.",
    "Problème introuvable.": "Issue not found.",
    "Aucun texte traduisible trouvé dans l’EPUB.": "No translatable text found in the EPUB.",
    "Chaque livre doit apparaître une seule fois.": "Each book must appear only once.",
    "Le nom de série est requis.": "The series name is required.",
    "La numérotation dépasse le volume 10000.": "The numbering goes beyond volume 10000.",
    "Mettez le travail en pause avant de modifier sa configuration.": "Pause the job before changing its configuration.",
    "Provider inconnu.": "Unknown provider.",
    "Annulez le travail actif avant de supprimer le projet.": "Cancel the active job before deleting the project.",
    "Terminez ou annulez le travail avant d’archiver ce projet.": "Finish or cancel the job before archiving this project.",
    "Restaurez ce projet avant de lancer un travail.": "Restore this project before starting a job.",
    "Configurez un provider LLM pour ce projet.": "Configure an LLM provider for this project.",
    "Provider de reprise introuvable.": "Recovery provider not found.",
    "Le filtre des refus est réservé à la traduction.": "The refusal filter is only available for translation.",
    "La sélection de récupération doit être une traduction ciblée seule.": "A recovery selection must be a targeted translation only.",
    "La sélection contient un passage inconnu de ce livre.": "The selection contains a passage that is not in this book.",
    "Un passage sélectionné est protégé ou n’a plus besoin de récupération.": "A selected passage is protected or no longer needs recovery.",
    "Lancez l’analyse du livre avant sa traduction.": "Run the book analysis before translating it.",
    "Seul un travail en pause, en attente, bloqué ou échoué peut être repris.": "Only a paused, waiting, blocked or failed job can be resumed.",
    "Un autre travail est déjà actif pour ce livre.": "Another job is already active for this book.",
    "Restaurez ce projet avant de reprendre un travail.": "Restore this project before resuming a job.",
    "Ce travail est déjà terminé.": "This job has already finished.",
    "Seul un travail actif ou bloqué peut être mis en pause.": "Only an active or blocked job can be paused.",
    "Un travail existe déjà. Reprenez-le ou annulez-le avant d’en lancer un autre.": "A job already exists. Resume or cancel it before starting another.",
    "Seul le propriétaire peut rattacher ce livre à cette série : elle contient des livres que vous ne pouvez pas lire.": "Only the owner can add this book to this series: it contains books you cannot read.",
    "Le serveur suit déjà trop de livres en direct. Réessayez dans quelques instants.": "The server is already following too many books live. Try again in a moment.",
    "Identifiant d’événement invalide.": "Invalid event identifier.",
    # Passages, validation, characters
    "Le passage a changé. Rechargez sa version.": "The passage has changed. Reload its version.",
    "Un résumé humain non vide est nécessaire.": "A non-empty human summary is required.",
    "Un autre travail occupe ce livre ; réessayez une fois terminé.": "Another job is using this book; try again once it has finished.",
    "Le passage a été modifié. Rechargez sa version avant d’enregistrer.": "The passage has been modified. Reload its version before saving.",
    "Modification concurrente détectée.": "Concurrent modification detected.",
    "Le passage a été modifié. Rechargez-le avant d’accepter la proposition.": "The passage has been modified. Reload it before accepting the suggestion.",
    "Proposition IA introuvable.": "AI suggestion not found.",
    "Cette remarque IA ne contient pas de remplacement applicable.": "This AI remark contains no applicable replacement.",
    "L’unité visée par la proposition n’existe plus.": "The unit targeted by the suggestion no longer exists.",
    "Le passage a été modifié. Rechargez-le avant de refuser la proposition.": "The passage has been modified. Reload it before rejecting the suggestion.",
    "Aucune traduction à conserver pour ce passage.": "No translation to keep for this passage.",
    "Identité canonique introuvable.": "Canonical identity not found.",
    "Alias trop long.": "Alias too long.",
    "Une relation doit relier deux identités distinctes de ce projet.": "A relation must link two distinct identities of this project.",
    "Relation introuvable.": "Relation not found.",
    "Cet alias désigne déjà une autre fiche. Utilisez la fusion d’identités.": "This alias already refers to another profile. Use identity merging.",
    "Un résumé global non vide est nécessaire pour valider la Book Bible.": "A non-empty overall summary is required to validate the Book Bible.",
    "Personnage introuvable.": "Character not found.",
    "Cette fiche a été fusionnée. Rechargez sa fiche canonique avant de la modifier.": "This profile has been merged. Reload its canonical profile before editing it.",
    "Nom canonique requis ; noms et alias limités à 300 caractères.": "Canonical name required; names and aliases are limited to 300 characters.",
    "Ce nom ou cet alias désigne déjà une autre fiche. Utilisez la fusion d’identités.": "This name or alias already refers to another profile. Use identity merging.",
    "La fusion doit cibler des fiches distinctes du même projet.": "A merge must target distinct profiles of the same project.",
    "Choisissez des identités canoniques de personnages.": "Choose canonical character identities.",
    "Cette fusion nécessite la validation humaine des identités protégées.": "This merge requires human validation of the protected identities.",
    "Paragraphes manquants, ajoutés ou désordonnés.": "Paragraphs missing, added or out of order.",
    "Traduction vide.": "Empty translation.",
    "Bloc de raisonnement détecté dans la traduction.": "Reasoning block detected in the translation.",
    "La réponse contient une consigne éditoriale au lieu du texte corrigé.": "The answer contains an editorial instruction instead of the corrected text.",
    "Les marqueurs de mise en forme ont été supprimés, ajoutés ou déplacés.": "Formatting markers were removed, added or moved.",
    "Marqueur de mise en forme inconnu.": "Unknown formatting marker.",
    "Caractère XML invalide dans la traduction.": "Invalid XML character in the translation.",
    "Caractère Unicode invalide dans la traduction.": "Invalid Unicode character in the translation.",
    "Découpage non réversible.": "Non-reversible segmentation.",
    "Ancre DOM source introuvable ou ambiguë.": "Source DOM anchor not found or ambiguous.",
    "Le texte contient les délimiteurs réservés ⟦ ⟧.": "The text contains the reserved delimiters ⟦ ⟧.",
    "Nombre de fragments inline incohérent.": "Inconsistent number of inline fragments.",
    # Glossary, memory, search
    "Terme introuvable.": "Term not found.",
    "Glossaire trop volumineux.": "Glossary too large.",
    "Glossaire invalide ou trop volumineux.": "Invalid or too large glossary.",
    "Glossaire JSON invalide : une liste de termes [{...}, ...] est attendue.": "Invalid JSON glossary: a list of terms [{...}, ...] is expected.",
    "Glossaire CSV invalide : colonnes source et traduction attendues.": "Invalid CSV glossary: source and translation columns are expected.",
    "Glossaire TBX invalide : aucun terme source et cible exploitable.": "Invalid TBX glossary: no usable source and target term.",
    "Glossaire illisible : utilisez un fichier JSON, CSV ou TBX encodé en UTF-8.": "Unreadable glossary: use a JSON, CSV or TBX file encoded in UTF-8.",
    "Account et user requis en mode trusted.": "Account and user are required in trusted mode.",
    "Document inconnu.": "Unknown document.",
    "Prompt inconnu.": "Unknown prompt.",
    "Document de catalogue inconnu.": "Unknown catalog document.",
    "URL HTTP(S) sans identifiants, paramètres ou fragment requise.": "An HTTP(S) URL without credentials, parameters or fragment is required.",
    "Renseignez l’URL avant d’activer SearXNG.": "Enter the URL before enabling SearXNG.",
    "Renseignez une URL à tester.": "Enter a URL to test.",
    "Test SearXNG échoué : vérifiez l’URL, le réseau et le format JSON.": "SearXNG test failed: check the URL, the network and the JSON format.",
    "SearXNG refuse la requête (403). Activez search.formats: [html, json] et vérifiez les restrictions d’accès.": "SearXNG refuses the request (403). Enable search.formats: [html, json] and check the access restrictions.",
    "Invalid search response": "Invalid search response",
    "Réponse de recherche trop volumineuse.": "Search response too large.",
    "Utilisez un sous-répertoire dédié sous viking://resources/.": "Use a dedicated subdirectory under viking://resources/.",
    "URI OpenViking non sûre.": "Unsafe OpenViking URI.",
    "URL OpenViking non configurée.": "OpenViking URL not configured.",
    "OpenViking a signalé une erreur applicative.": "OpenViking reported an application error.",
    "Réponse read OpenViking inattendue.": "Unexpected OpenViking read response.",
    "Réponse search OpenViking inattendue.": "Unexpected OpenViking search response.",
    "Identités account/user requises pour le mode trusted.": "Account/user identities are required for trusted mode.",
    "Graphe d’identités importé incohérent ou cyclique.": "Imported identity graph is inconsistent or cyclic.",
    "Relation sans personnage dans l’archive.": "Relation without a character in the archive.",
    # Exports, archives, EPUB
    "Export bloqué : des passages n’ont pas encore de traduction.": "Export blocked: some passages are not translated yet.",
    "La sélection contient des projets en double.": "The selection contains duplicate projects.",
    "La traduction n’est pas encore complète.": "The translation is not complete yet.",
    "Export incomplet : des passages ne sont pas traduits.": "Incomplete export: some passages are not translated.",
    "Archive projet trop volumineuse.": "Project archive too large.",
    "Archive projet invalide.": "Invalid project archive.",
    "Archive projet trop volumineuse après décompression.": "Project archive too large once unpacked.",
    "Archive de projet invalide : project.json n’est pas un JSON lisible.": "Invalid project archive: project.json is not readable JSON.",
    "Version de projet non prise en charge.": "Unsupported project version.",
    "Structure du projet incompatible avec son EPUB original.": "Project structure does not match its original EPUB.",
    "Le texte source du projet ne correspond pas à l’EPUB.": "The project's source text does not match the EPUB.",
    "Version rattachée à un passage absent de l’archive.": "Version attached to a passage missing from the archive.",
    "Chemin ZIP non sûr.": "Unsafe ZIP path.",
    "Traversée de répertoire détectée dans l’archive.": "Directory traversal detected in the archive.",
    "Une ressource structurelle EPUB doit être locale.": "A structural EPUB resource must be local.",
    "Référence EPUB non sûre.": "Unsafe EPUB reference.",
    "Les déclarations d’entités XML ne sont pas autorisées.": "XML entity declarations are not allowed.",
    "Entité XML non résolue.": "Unresolved XML entity.",
    "Fichier importé trop volumineux.": "Imported file too large.",
    "Trop de fichiers dans l’archive.": "Too many files in the archive.",
    "Limite de décompression dépassée (archive bomb possible).": "Decompression limit exceeded (possible archive bomb).",
    "Ratio de compression global excessif (archive bomb possible).": "Excessive overall compression ratio (possible archive bomb).",
    "Liens symboliques et archives ZIP chiffrées non pris en charge.": "Symbolic links and encrypted ZIP archives are not supported.",
    "Noms de fichiers dupliqués dans l’archive.": "Duplicate file names in the archive.",
    "Ratio de compression excessif.": "Excessive compression ratio.",
    "Taille ZIP incohérente.": "Inconsistent ZIP size.",
    "Ce fichier n’est pas un EPUB : mimetype absent ou incorrect.": "This file is not an EPUB: mimetype missing or incorrect.",
    "EPUB sans META-INF/container.xml.": "EPUB without META-INF/container.xml.",
    "Package OPF introuvable.": "OPF package not found.",
    "EPUB protégé par un chiffrement non pris en charge.": "EPUB protected by unsupported encryption.",
    "Fragments de paragraphe manquants ou dupliqués.": "Paragraph fragments missing or duplicated.",
    "Trop de validations EPUB en cours sur le serveur ; réessayez dans un instant.": "Too many EPUB validations running on the server; try again in a moment.",
    "EPUBCheck n’a pas produit de rapport exploitable.": "EPUBCheck did not produce a usable report.",
    "EPUBCheck n’a pas terminé en 90 secondes ; réessayez plus tard.": "EPUBCheck did not finish within 90 seconds; try again later.",
    # Providers and models
    "Une clé API est requise pour ce type de provider.": "An API key is required for this type of provider.",
    "Provider introuvable.": "Provider not found.",
    "Ressaisissez la clé API pour changer l’adresse ou le type de ce provider : la clé enregistrée n’est jamais envoyée à un nouvel hôte.": "Enter the API key again to change this provider's address or type: the stored key is never sent to a new host.",
    "Provider Codex ChatGPT introuvable.": "Codex ChatGPT provider not found.",
    "Le connecteur Codex ne répond pas. Démarrez le profil Docker codex.": "The Codex connector does not answer. Start the codex Docker profile.",
    "Connecteur Codex non activé. Exécutez scripts/enable_codex.py puis redémarrez Compose.": "Codex connector not enabled. Run scripts/enable_codex.py, then restart Compose.",
    "URL HTTP(S) sans identifiants intégrés requise.": "An HTTP(S) URL without embedded credentials is required.",
    "L’URL de base ne doit pas contenir de query ou fragment.": "The base URL must not contain a query or fragment.",
    "URL du provider requise.": "Provider URL required.",
    "La connexion ChatGPT utilise le code officiel, pas une clé API.": "The ChatGPT connection uses the official code, not an API key.",
    "La fenêtre doit réserver au moins 1024 tokens aux entrées.": "The window must keep at least 1024 tokens for inputs.",
    "Choisissez un provider avant de lancer le traitement.": "Choose a provider before starting the processing.",
    "La cible et les règles obligatoires dépassent le budget conservateur. Augmentez la fenêtre ou réduisez les instructions ; aucun texte n’a été retiré.": "The target and the mandatory rules exceed the conservative budget. Increase the window or shorten the instructions; no text was removed.",
    "Fenêtre trop petite pour conserver le voisinage du passage. Augmentez le contexte.": "Window too small to keep the passage's surroundings. Increase the context.",
    "Arrêt après 10 passages consécutifs en échec. Vérifiez le provider avant de reprendre.": "Stopped after 10 consecutive failed passages. Check the provider before resuming.",
    "File du provider saturée ; nouvelle tentative planifiée.": "Provider queue full; a new attempt is scheduled.",
    "Budget de contexte dépassé : réduire les instructions ou augmenter la fenêtre du provider. La cible n’a pas été tronquée.": "Context budget exceeded: shorten the instructions or increase the provider's window. The target was not truncated.",
    "Provider non configuré.": "Provider not configured.",
    "Format de réponse du provider inattendu.": "Unexpected provider answer format.",
    "Réponse du provider sans choices exploitables.": "Provider answer without usable choices.",
    "Le provider a renvoyé du raisonnement sans contenu final (content=null). Désactivez ou réduisez le niveau de raisonnement.": "The provider returned reasoning without final content (content=null). Disable or lower the reasoning level.",
    "Le provider a refusé la requête.": "The provider refused the request.",
    "Le projet ou la requête ont été supprimés pendant le traitement.": "The project or the request was deleted during processing.",
    "Format structuré non supporté ; essai du format de repli.": "Structured format not supported; trying the fallback format.",
    "Une décision d’acceptation ne peut pas conserver de problème.": "An acceptance decision cannot keep an issue.",
    "Une décision de révision doit fournir une correction précise.": "A revision decision must provide a precise correction.",
    "Chaque problème doit fournir une correction directement applicable.": "Each issue must provide a directly applicable correction.",
    "La revue finale doit statuer sans déléguer sa décision.": "The final review must decide without delegating its decision.",
    "Final review references an unknown unit.": "Final review references an unknown unit.",
    # Series, imports and automation
    "Les chapitres d’une webnovel appartiennent obligatoirement à leur série.": "The chapters of a webnovel must belong to their series.",
    "Chemin de stockage hors du dossier de données.": "Storage path outside the data folder.",
    "Aucun texte traduisible trouvé dans la source.": "No translatable text found in the source.",
    "Ce volume vient d’un EPUB : ajoutez les chapitres texte à un autre volume.": "This volume comes from an EPUB: add the text chapters to another volume.",
    "Des chapitres existent déjà avec un autre contenu : confirmez leur remplacement.": "Chapters already exist with a different content: confirm their replacement.",
    "Ce remplacement supprimerait des passages corrigés ou validés par une personne. Confirmez explicitement leur abandon.": "This replacement would delete passages corrected or validated by a person. Explicitly confirm that they may be discarded.",
    "Encodage UTF-32 non pris en charge : enregistrez le fichier en UTF-8.": "UTF-32 encoding is not supported: save the file as UTF-8.",
    "Le fichier annonce un encodage (BOM) que son contenu ne respecte pas.": "The file announces an encoding (BOM) that its content does not follow.",
    "Encodage non reconnu (UTF-16 sans BOM ?) : enregistrez le fichier en UTF-8.": "Unrecognised encoding (UTF-16 without BOM?): save the file as UTF-8.",
    "Encodage non reconnu : enregistrez le fichier en UTF-8.": "Unrecognised encoding: save the file as UTF-8.",
    "Ce fichier n’est pas en UTF-8 : il a été lu en Windows-1252. Vérifiez les accents de l’aperçu.": "This file is not UTF-8: it was read as Windows-1252. Check the accented characters of the preview.",
    "Le texte contient des marqueurs réservés à Libris (⟦t0⟧…) : retirez-les.": "The text contains markers reserved to Libris (⟦t0⟧…): remove them.",
    "Aucun texte traduisible dans ce chapitre.": "No translatable text in this chapter.",
    "mot-clé de volume dans le nom du fichier": "volume keyword in the file name",
    "abréviation « v » suivie d’un numéro dans le nom du fichier": "“v” abbreviation followed by a number in the file name",
    "numéro précédé de « # » dans le nom du fichier": "number preceded by “#” in the file name",
    "numéro entre crochets ou parenthèses dans le nom du fichier": "number in brackets or parentheses in the file name",
    "plusieurs numéros de volume contradictoires dans le nom du fichier": "several contradictory volume numbers in the file name",
    "numéro qui varie entre les noms des fichiers du lot": "number that varies between the file names of the batch",
    "nom du fichier sans son numéro de volume": "file name without its volume number",
    "début commun aux noms des fichiers du lot": "beginning shared by the file names of the batch",
    "aucun nom commun dans les noms de fichiers": "no name shared by the file names",
    "métadonnées de série de l’EPUB": "series metadata of the EPUB",
    "aucun numéro de volume trouvé": "no volume number found",
    "mot-clé de chapitre dans le nom du fichier": "chapter keyword in the file name",
    "numéro au début du nom du fichier": "number at the start of the file name",
    "numéro final qui varie dans le lot": "final number that varies within the batch",
    "aucun numéro trouvé": "no number found",
    "Même nom canonique": "Same canonical name",
    "Nom ou alias partagé": "Shared name or alias",
    "Plusieurs identités de la série portent ce nom : à confirmer": "Several identities of the series bear this name: to be confirmed",
    "Première apparition dans la série": "First appearance in the series",
}  # fmt: skip

TEMPLATES = {
    "Cet EPUB est déjà importé dans « {title} ».": "This EPUB is already imported in “{title}”.",
    "Cet EPUB est déjà importé dans le projet archivé « {title} ». Restaurez-le depuis les archives.": "This EPUB is already imported in the archived project “{title}”. Restore it from the archives.",
    "Le fichier EPUB d’origine de « {title} » est introuvable sur le serveur : l’EPUB, l’archive de projet et l’aperçu sont indisponibles. Les exports TXT, Markdown et Book Bible restent possibles ; restaurez le dossier des livres (DATA_DIR/books) pour retrouver les autres.": "The original EPUB file of “{title}” cannot be found on the server: the EPUB, the project archive and the preview are unavailable. TXT, Markdown and Book Bible exports remain possible; restore the books folder (DATA_DIR/books) to get the others back.",
    "EPUBCheck signale un EPUB invalide : « {title} ».": "EPUBCheck reports an invalid EPUB: “{title}”.",
    "… et {count} autre(s) erreur(s).": "… and {count} more error(s).",
    "Export bloqué, traduction incomplète : {titles}": "Export blocked, incomplete translation: {titles}",
    "Archive de projet trop volumineuse pour être réimportée : {size} Mo pour {limit} Mo autorisés ({setting}). Augmentez ce réglage sur les serveurs d’export et de restauration, ou purgez l’historique des requêtes de ce livre.": "Project archive too large to be imported back: {size} MB for {limit} MB allowed ({setting}). Raise this setting on the exporting and restoring servers, or purge this book's request history.",
    "Archive de projet invalide : champ(s) incorrect(s) {fields}.": "Invalid project archive: incorrect field(s) {fields}.",
    "Trop de suivis en direct ouverts pour ce compte ({limit} au maximum). Fermez des onglets Libris, puis rechargez la page.": "Too many live views open for this account ({limit} at most). Close some Libris tabs, then reload the page.",
    "Ce provider est encore sélectionné par {count} livre(s). Choisissez un autre provider pour ces livres avant de le supprimer.": "This provider is still selected by {count} book(s). Choose another provider for these books before deleting it.",
    "Ce provider ne peut pas être supprimé : {jobs} travail(aux) et {requests} requête(s) de l’historique s’y rapportent, et les statistiques de coût en dépendent. Il n’est plus utilisé par aucun livre : vous pouvez le renommer ou retirer sa clé API.": "This provider cannot be deleted: {jobs} job(s) and {requests} request(s) of the history refer to it, and cost statistics depend on it. No book uses it any more: you can rename it or remove its API key.",
    "Connexion Codex indisponible (HTTP {status}). Vérifiez le connecteur et l’authentification ChatGPT.": "Codex connection unavailable (HTTP {status}). Check the connector and the ChatGPT authentication.",
    "Document OpenViking indisponible (HTTP {status}). Lancez une synchronisation et vérifiez son état.": "OpenViking document unavailable (HTTP {status}). Run a synchronisation and check its state.",
    "OpenViking refuse la réindexation (HTTP {status}). Vérifiez les droits de la clé sur cette racine. La reconstruction par réécriture depuis SQL reste disponible.": "OpenViking refuses the reindexing (HTTP {status}). Check the key's rights on this root. Rebuilding by rewriting from SQL remains available.",
    "Spine EPUB invalide ou document absent de l’archive{detail}.": "Invalid EPUB spine or document missing from the archive{detail}.",
    "Dans un sommaire, le texte doit rester à l’intérieur du lien : « {text} » est placé à côté.": "In a table of contents, the text must stay inside the link: “{text}” is placed next to it.",
    "La clé API enregistrée pour « {name} » ne peut plus être déchiffrée : SECRET_KEY a changé depuis son enregistrement. Ressaisissez la clé dans les paramètres du fournisseur.": "The API key stored for “{name}” can no longer be decrypted: SECRET_KEY has changed since it was saved. Enter the key again in the provider settings.",
    "Réponse tronquée ou arrêt inattendu : {reason}": "Truncated answer or unexpected stop: {reason}",
    "Requête trop volumineuse : {limit} Mo au maximum.": "Request too large: {limit} MB at most.",
    "Un service externe requis par cette action est injoignable ({error}). Vérifiez son adresse et qu’il est démarré, puis réessayez.": "An external service required by this action cannot be reached ({error}). Check its address and that it is running, then try again.",
    "Archive EPUB ou XML invalide ({error}).": "Invalid EPUB archive or XML ({error}).",
    "L’opération a échoué côté serveur. Référence de diagnostic : {reference}. Les traductions déjà enregistrées sont conservées.": "The operation failed on the server. Diagnostic reference: {reference}. Translations already saved are kept.",
    "Glossaire CSV invalide à la ligne {line} : {field} = « {value} »": "Invalid CSV glossary at line {line}: {field} = “{value}”",
    "Terme de glossaire invalide n° {number} : {problem}": "Invalid glossary term no. {number}: {problem}",
    "Fenêtre de {window} tokens trop petite pour ce passage : après {output} tokens réservés à la réponse et {reserve} au format de réponse, il reste {available} tokens, alors que le prompt système ({system}), le texte du passage ({target}) et les règles obligatoires ({rules}) en demandent {needed} (estimation prudente : 1 token par octet). Choisissez un fournisseur avec une fenêtre d’au moins {suggested} tokens ou réduisez sa sortie maximale ; aucun texte n’a été retiré.": "A {window}-token window is too small for this passage: after {output} tokens reserved for the answer and {reserve} for the answer format, {available} tokens remain, while the system prompt ({system}), the passage text ({target}) and the mandatory rules ({rules}) need {needed} (cautious estimate: 1 token per byte). Choose a provider with a window of at least {suggested} tokens or lower its maximum output; no text was removed.",
    "Fenêtre de {window} tokens trop petite pour garder le voisinage du passage : une fois le passage et les règles placés, il reste {available} tokens de contexte, moins qu’un extrait des passages voisins. Choisissez un fournisseur avec une fenêtre plus grande ou réduisez sa sortie maximale.": "A {window}-token window is too small to keep the passage's neighbourhood: once the passage and the rules are placed, {available} tokens of context remain, less than an excerpt of the neighbouring passages. Choose a provider with a larger window or lower its maximum output.",
    # Series, imports and automation
    'Un travail est en cours sur « {title} » : mettez-le en pause avant d’y ajouter des chapitres.': 'A job is running on “{title}”: pause it before adding chapters to it.',
    'Chapitre trop long : {length} caractères pour {limit} autorisés.': 'Chapter too long: {length} characters for {limit} allowed.',
    '{count} caractère(s) de contrôle supprimé(s).': '{count} control character(s) removed.',
    'Le nom du fichier indique aussi le volume {number} ; le numéro retenu vient du lot.': 'The file name also indicates volume {number}; the number kept comes from the batch.',
    'Les métadonnées de l’EPUB indiquent le volume {number}.': 'The EPUB metadata indicate volume {number}.',
}  # fmt: skip


@lru_cache
def _patterns() -> list[tuple[re.Pattern, str]]:
    compiled = []
    for french, english in TEMPLATES.items():
        parts = re.split(r"\{(\w+)\}", french)
        pattern = "".join(
            re.escape(part) if index % 2 == 0 else f"(?P<{part}>.*?)" for index, part in enumerate(parts)
        )
        compiled.append((re.compile(pattern + r"\Z", re.S), english))
    return compiled


def english(message: str) -> str | None:
    """The English message, or None when the catalog does not know this one."""
    if message in MESSAGES:
        return MESSAGES[message]
    for pattern, template in _patterns():
        if match := pattern.match(message):
            values = {key: english(value) or value for key, value in match.groupdict().items()}
            return template.format(**values)
    return None


def preferred_language(header: str | None) -> str:
    """"en" when Accept-Language ranks English above French; French stays the default."""
    weights = {"en": 0.0, "fr": 0.0}
    for item in (header or "").split(","):
        tag, _, parameters = item.strip().partition(";")
        primary = tag.strip().split("-")[0].casefold()
        if primary not in weights:
            continue
        quality = 1.0
        for parameter in parameters.split(";"):
            key, _, value = parameter.strip().partition("=")
            if key.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        weights[primary] = max(weights[primary], quality)
    return "en" if weights["en"] > weights["fr"] else "fr"


def localize(detail, language: str):
    """Translate an error `detail` (a message, or a dict with a message and a list of errors)."""
    if language != "en":
        return detail
    if isinstance(detail, str):
        return english(detail) or detail
    if isinstance(detail, dict):
        return {
            key: localize(value, language) if key in {"message", "errors"} else value
            for key, value in detail.items()
        }
    if isinstance(detail, list):
        return [localize(item, language) for item in detail]
    return detail

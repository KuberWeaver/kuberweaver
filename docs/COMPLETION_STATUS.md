# Complétion du prototype : preuves et limites

État vérifié le 6 novembre 2026. Les résultats ci-dessous décrivent la recette locale avant publication GitHub. Les secrets et états d'exécution sont exclus de la publication.

## Besoin IA retenu

KuberWeaver est pilotable par des agents externes via son API et un adaptateur MCP stdio. Il n'a pas besoin d'embarquer un modèle, de clé de fournisseur IA ou d'abonnement de modèle. Le fournisseur de diagnostic LLM développé séparément demeure optionnel et n'est pas enregistré par défaut.

- Jetons émis, listés et révoqués uniquement depuis une session administrateur humaine.
- Rôle maximal et scopes cumulatifs, expiration et révocation persistantes.
- Scopes de lecture ciblés, notamment `read:finops`, sans lecture implicite des jobs ou de l'infrastructure.
- Approbation déléguée uniquement avec rôle admin et scope `approve` explicite.
- Aucun outil MCP pour créer ses propres privilèges, résoudre directement un secret, appeler une URL arbitraire ou lancer une commande système.
- Jeton affiché une seule fois dans la page Accès agents ; aucune persistance dans localStorage/sessionStorage.
- Les flux d'événements revalident le jeton à chaque itération ; les sessions navigateur gardent leur protection CSRF.

Guides : [droits API](AGENT_ACCESS.md), [MCP](MCP.md).

## Vérifications exécutées

Commande backend : `.venv/bin/python -m pytest -q --tb=short --junitxml=/home/ubu/.hermes/cache/scratch/kuberweaver-final-backend.xml`.

- 527 tests backend passants, 1 ignoré, aucun échec.
- Le seul test ignoré exige un exécutable local `redis-server`, absent du poste. Il ne prouve donc pas une persistance Redis réelle.
- 47 tests frontend passants dans 9 fichiers.
- Build TypeScript/Vite réussi.
- Vrais échanges MCP : SDK ClientSession → processus stdio → véritable API Uvicorn isolée ; lecture, validation, sauvegarde de blueprint avec relecture HTTP indépendante, soumission restant en attente, refus d'approbation 403 sans scope et refus 401 après révocation dans la même session.
- Vrais échanges HTTP locaux pour livraison de notifications, y compris validation sans job, et approbation ChatOps signée ; replay refusé.
- Publication Git et relecture de SHA réellement exercées sur des dépôts bare jetables, pas sur un hébergeur externe.
- Revue indépendante API/MCP/UI approuvée ; relecture finale qualité/conformité des correctifs GitOps, credentials/Redis, notifications et OpenTofu également approuvée pour leur périmètre implémenté et testé, sans certification distante.
- Scan Gitleaks sur un instantané de 176 fichiers source, hors données privées et présentations : aucune fuite détectée.

Les comptes, tokens, configurations et destinations de ces tests sont jetables et isolés de `data/` et des services externes réels.

## État des fonctions ajoutées

| Fonction | Implémentation et preuve | Limite persistante |
| --- | --- | --- |
| API et MCP agents | 29 outils métier typés, jetons révocables, rôles et scopes, protocole réel exercé | Pas de configuration automatique d'un client Hermes, pas d'exposition Internet ajoutée |
| Notifications | Outbox durable, dispatcher séparé du worker, retry borné, états uncertain non rejoués automatiquement, relecture API | Aucun SaaS destinataire réel configuré ; pas de promesse exactly-once côté destinataire |
| GitOps distant | Destination explicite, commit exact et SHA distant vérifié ; publication hors verrou partagé ; réservation durable d'approbation et revalidation avant queue | Dépôts de test uniquement ; configuration Git/SSH et politique distante réelles restent à fournir |
| Secrets d'exécution | Références administrées serveur, clé éphémère 0600, isolation d'identité/environnement, suppression des résultats sensibles, cleanup ; mapping et sources épinglés avant chaque commande | Pas de remplacement des clés du laboratoire ni de recette OpenBao → invité réel dans cette tranche |
| ChatOps | Action liée au plan, callback HMAC horodaté, acteur externe mappé à un admin, nonce durable ; réutilisation des services d'approbation | Adaptateur générique, pas d'authentification Slack/Teams certifiée ni de passerelle externe déployée |
| Supervision | Snapshot authentifié et métriques raccordés ; règles Prometheus et dashboard Grafana validés avec promtool | Stack de collecte/visualisation non déployée ; configuration n'est pas preuve de joignabilité |
| Redis persistant | Cycle spécifique, propriété, AOF, sauvegarde arrêtée, restauration et retry partiel ; injection SSH corrigée et testée | Manifeste planned, extension désactivée, précondition de pare-feu non modifiée ; aucune validation distante |
| OpenTofu/libvirt | Plans sauvegardés et digests, garde CLI/provider/transport, suppression séparée et contrôle disques live/persistants ; 41 tests dédiés passants | Manifeste planned, non raccordé à un parcours de création autorisé ; transport SSH actuel refusé par le garde de confiance ; aucune VM créée ou supprimée |
| Cisco/pfSense, stack front/back | Besoin et emplacements conservés | Pas d'adaptateur/cible validés, pas de câblage réseau automatique |
| FinOps mesuré | Estimation existante et source indiquée | Pas de métriques de facturation cloud, paiement ou allocation complète de consommation réelle |

Un état planned, une déclaration disponible, une configuration présente ou un test simulé ne valent jamais acceptation distante.

## Serveur local après intégration

Le service utilisateur `kuberweaver.service` a été redémarré après constat d'absence de jobs actifs et sauvegarde privée de SQLite. Relectures effectuées :

- service active/running ;
- `http://127.0.0.1:8088/healthz` : `{"status":"ok","service":"kuberweaver"}` ;
- portail HTTP 200 ;
- schéma API contenant les routes agents, ChatOps et opérations ;
- métriques confirmant stockage lisible et worker vivant.

Aucun jeton réel n'a été émis automatiquement, aucun connecteur externe activé, aucune VM ou règle réseau modifiée. Les données et présentations existantes sont conservées. Les approbations ambiguës restent bloquées pour revue manuelle, sans nettoyage ou reprise automatique.

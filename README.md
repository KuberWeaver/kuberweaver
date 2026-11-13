# KuberWeaver, premier prototype fonctionnel

## Documents du projet

- [Note de cadrage, deux pages avec infographie](docs/note-cadrage/KuberWeaver_note_de_cadrage.pdf), avec [version texte](docs/note-cadrage/KuberWeaver_note_de_cadrage.txt).
- [Architecture cible et gestion des plugins](docs/ARCHITECTURE_CIBLE.md), propositions pour PostgreSQL, le cycle de vie des services et le catalogue, distinctes des fonctions déjà livrées.

- [Cahier des charges simplifié](docs/CAHIER_DES_CHARGES_KUBERWEAVER.md), également disponible en [PDF](docs/CAHIER_DES_CHARGES_KUBERWEAVER.pdf), avec périmètre, tests d’acceptation et répartition de l’équipe EFREI Paris.
- [Cadrage original](docs/cadrage-original/), vision initiale et infographie, à distinguer des fonctions effectivement livrées.
- [Recette et limites](docs/ACCEPTANCE.md) et [résultats de vérification](docs/VERIFICATION.md).
- [Contrats des extensions](docs/EXTENSIONS.md) et [cycle d'exécution](docs/EXECUTION.md).
- [Accès agents et droits API](docs/AGENT_ACCESS.md), [serveur MCP](docs/MCP.md) et [recette de complétion](docs/COMPLETION_STATUS.md).

Ce dépôt contient les sources, tests, manifestes, scripts et documents du prototype. Les secrets, comptes locaux, états d'exécution, snapshots, fichiers d'état IaC et dépendances installées sont exclus. Les VM du laboratoire, images système et accès SSH existants ne sont pas embarqués : un clone ne reconstitue pas automatiquement le laboratoire. Les chemins des unités systemd et les accès de lab doivent être adaptés à l'environnement d'installation.


Portail d'infrastructure auto-hébergé, construit à partir du cadrage situé dans le dossier parent `Prototype/`. Le contrôle tourne sur le poste local ; les déploiements tournent réellement dans les VMs du laboratoire GNS3 sur `192.168.10.55`. Le nom ne signifie pas que Kubernetes est installé.

## Accès

Ouvrir `http://127.0.0.1:8088`.

Les comptes locaux `admin`, `operator` et `viewer` ont des mots de passe aléatoires distincts dans :

`/home/ubu/Desktop/infra-platform-lab/Prototype/kuberweaver/data/bootstrap-credentials.txt`

Ce fichier est privé, mode 0600. Ne pas le publier. Il n'y a pas de mot de passe de démonstration codé dans le frontend.

- Administrateur : politiques, fournisseurs, activation explicite d'extensions, approbation et rollback.
- Opérateur : conception, sauvegarde et demande de déploiement, sans droit d'approbation.
- Lecteur : consultation, sans mutation.

Le serveur écoute uniquement sur le loopback du poste. Ce n'est pas un déploiement public ou un service SaaS multi-tenant.

## Pilotage par un agent externe

Aucun modèle IA embarqué n'est nécessaire. Un administrateur humain ouvre **Accès agents**, choisit un rôle maximal, des scopes explicites et une expiration, puis conserve le jeton affiché une seule fois dans un fichier privé 0600. La révocation est immédiate pour les nouvelles requêtes. Les tokens ne sont pas conservés dans le stockage du navigateur.

Pour une lecture strictement financière, sélectionner `read:finops` sans ajouter le scope global `read`. Pour un agent proposant des déploiements, attribuer les lectures nécessaires, `plan` et `submit`, sans `approve`. Le rôle et les scopes sont deux limites cumulatives ; le MCP ne remplace pas les contrôles de l'API.

```sh
.venv/bin/python scripts/mcp_server.py --base-url http://127.0.0.1:8088 --token-file /chemin/prive/agent.token
```

Le serveur stdio expose 29 outils métier typés, pas de commande système libre, d'URL arbitraire ou d'accès direct aux secrets. Aucun jeton n'est émis automatiquement et aucun MCP n'est ajouté automatiquement à Hermes. Voir `docs/MCP.md` pour installer son SDK et configurer un client.

## Parcours de démonstration

1. Se connecter et ouvrir Canvas, initialement vide pour un nouvel utilisateur.
2. Ajouter NGINX ou Apache depuis le catalogue. Les formulaires viennent des schémas des plugins.
3. Choisir les cibles `app` et `branch-client`, ainsi qu'un port libre entre 18090 et 18109. Chaque ressource possède son port sur chaque cible.
4. Valider la gouvernance et l'estimation, enregistrer le blueprint et soumettre une demande.
5. Un administrateur inspecte le plan, puis approuve. Le noyau versionne le plan approuvé dans Git et exécute sauvegarde, canary, vérification, rolling et vérification finale.
6. Consulter les événements, le commit et les preuves HTTP dans Déploiements.
7. Pour observer un retour arrière, modifier la version du même blueprint et sélectionner un échec rolling intentionnel, réservé à l'administrateur et au lab. Après l'échec, les deux cibles doivent servir la version précédente.

Ne pas créer un autre blueprint sur un port déjà réservé. Le port par défaut 18090 est laissé libre après les acceptations ; 18091 reste réservé au déploiement de démonstration du portail. Les identités graph/environment/node définissent la propriété des ressources. Les liens du canvas représentent des dépendances d'ordre de déploiement, pas une création de câblage réseau ou de règles de pare-feu.

Les endpoints fournis par les jobs appartiennent au réseau de management `192.168.122.0/24`, inaccessible directement depuis le navigateur du poste sans tunnel. Une réponse verte du portail est appuyée par les probes dans les invités, pas par une publication automatique sur le LAN.

## Architecture modulaire

Le noyau gère identité, admission, approbations, état durable, audit et orchestration sérialisée. Les fonctionnalités métier passent par des contrats de fournisseurs. Les manifestes et schémas des blueprints sont distincts du code du portail.

```
backend/kuberweaver/
  app.py, api.py                HTTP, sessions, RBAC, CSRF
  agent_auth.py, mcp_server.py  jetons révocables, scopes, outils MCP
  models.py, policy.py         enveloppes et admission
  storage.py, jobs.py          état durable, queue, audit, GitOps
  catalog.py                   découverte des blueprints déclaratifs
  extensions.py, runtime.py    SDK, activation et configuration
  providers.py                 fournisseurs intégrés
  execution.py                 exécuteur Ansible du lab
  secrets.py                   sources de secrets remplaçables
frontend/                      React, TypeScript, React Flow
plugins/                       manifestes, schémas et assets Ansible
examples/extensions/           exemple Python installable séparément
scripts/                       commandes de cycle de vie et acceptation
ops/                           unités systemd utilisateur
```

Pour ajouter un service compatible, livrer son manifeste, son JSON Schema et ses assets d'exécution, sans ajouter de `if plugin_id` au noyau. Pour ajouter une nouvelle famille de fonctionnalité, écrire une extension qui enregistre un fournisseur ou un hook par le SDK.

Installation locale de l'exemple, désactivé par défaut :

```sh
.venv/bin/python scripts/plugins.py install examples/extensions/lab-notes
.venv/bin/python scripts/plugins.py doctor lab-notes
.venv/bin/python scripts/plugins.py enable lab-notes --trust
.venv/bin/python scripts/plugins.py disable lab-notes
```

Ne pas réinstaller un ID déjà présent. La CLI indique quand un redémarrage est requis. Les commandes `doctor` sont statiques par défaut ; exécuter du Python exige une confiance explicite. Les extensions Python sont du code de confiance avec les privilèges du processus, pas une sandbox. Les vérifications locales des blueprints ne sont pas une certification de sécurité d'un marketplace public.

Voir `docs/EXTENSIONS.md`, `docs/EXECUTION.md` et `docs/SECRETS.md` pour les contrats et limites précis.

Dans Intégrations, la sélection des fournisseurs consomme le registre par capacité. `GET /api/providers` expose les liaisons actives et les implémentations disponibles ; l'administrateur les modifie avec `PATCH /api/providers`. Une extension doit d'abord être installée et activée explicitement. Désactiver le fournisseur sélectionné rend uniquement sa fonction indisponible, sans bascule silencieuse vers un autre. La sélection est persistante et refusée pendant une exécution. Les fournisseurs d'exécution sont épinglés dans l'approbation avec leur provenance.

Les hooks canoniques sont `before_plan`, `after_approval`, `job_started` et `job_finished`. Ils observent des copies des événements et ne remplacent jamais les validations ou l'approbation. L'exemple `lab-notes` a été sélectionné depuis le portail, a reçu la fin d'un vrai déploiement et a rendu la provenance par l'API de diagnostic ; puis sa désactivation a été vérifiée sans modification du noyau. Il est laissé désactivé après la démonstration.

## Ce qui fonctionne et ce qui reste ouvert

| Fonction | État du prototype |
| --- | --- |
| Canvas, catalogue, formulaires déclaratifs, sauvegarde de blueprints | Fonctionnel |
| Sessions, RBAC, CSRF, quotas par plan, approbation | Fonctionnel, comptes locaux |
| NGINX et Apache sur deux invités GNS3 | Déploiements réels par Ansible |
| Canary, rolling, rollback automatique et manuel | Exercés avec readbacks HTTP indépendants |
| GitOps | Commits locaux ; publication distante optionnelle avec relecture du SHA, testée sur dépôts de test, pas de pipeline client hébergé validé |
| Infrastructure | Inventaire GNS3 réel en lecture seule |
| FinOps | Estimations selon une grille interne, pas de facturation cloud ni de mesures de consommation |
| Diagnostic | Règles déterministes et fournisseurs optionnels, pas d'inférence LLM configurée |
| Notifications et ChatOps | Outbox, webhook durable et callback signé testés par vrais échanges locaux ; aucun connecteur SaaS externe configuré ni certification Slack/Teams |
| OpenBao | Service persistant initialisé dans le lab, provider KV v2 testé ; bootstrap et tunnel privés |
| Identifiants d'exécution | Injection éphémère depuis le fournisseur de secrets sélectionné, isolation et nettoyage testés ; aucun secret de déploiement réel reconfiguré |
| OpenTofu, cloud, nouvelles VMs | Adaptateur plan/application/suppression gardé désactivé ; SSH actuel refusé par les contrôles de confiance ; aucune création/suppression de VM validée |
| Redis persistant | Cycle spécifique et restauration testés, manifeste planned et extension désactivée ; aucune validation distante |
| API et MCP pour agents externes | Jetons par rôle et scopes, expiration/révocation, 29 outils ; protocole stdio et API réelle isolée exercés |
| Supervision applicative | Métriques et snapshot read-only raccordés ; règles Prometheus et dashboard Grafana validés, stack non déployée |
| Stack front/back, Cisco IOS, pfSense | Entrées prévues, désactivées faute d'adaptateurs/cibles validés |
| Marketplace communautaire, paiements, signatures, isolation de code tiers | Hors périmètre de cette version |

`disk_gb` est actuellement une déclaration budgétaire, pas un quota disque appliqué. `public_cidr` est un garde-fou de demande, pas une règle de pare-feu installée. L'exécution est limitée aux cibles et ports du lab ; l'ajout d'un vrai fournisseur cloud ou réseau doit apporter son propre contrat et ses tests.

## Données et secrets

`data/` est privé et exclu du dépôt. Il contient SQLite, les jobs, snapshots, journaux, plans GitOps, activation et données durables des extensions, ainsi que les fichiers de bootstrap. Ne jamais publier le dossier du projet en bloc sans revue des secrets.

OpenBao tourne dans le conteneur `kw-openbao` sur `infra-operations`, avec stockage Raft chiffré persistant. Son HTTP est lié à `127.0.0.1:18200` dans l'invité et atteint par tunnel SSH local. Cela ne constitue pas du TLS de bout en bout. Le redémarrage du conteneur nécessite un unseal, et le token applicatif a une durée de vie limitée. Les parts de récupération et le root token restent dans un fichier opérateur privé pour le lab, pas sous séparation de responsabilités de production.

Les données d'un plugin restent séparées de son code remplaçable. La désactivation ne supprime pas son état. Les travaux interrompus au redémarrage passent en revue manuelle et ne sont pas rejoués automatiquement.

## Lancer et arrêter

Les unités utilisateur sont livrées dans `ops/` et liées au gestionnaire systemd du poste. Elles ne sont pas activées automatiquement au démarrage.

```sh
systemctl --user start kuberweaver.service
systemctl --user status kuberweaver.service kuberweaver-bao-tunnel.service
journalctl --user -u kuberweaver.service -n 80
systemctl --user stop kuberweaver.service kuberweaver-bao-tunnel.service
```

Arrêt gracieux : le noyau attend l'opération en cours avant de libérer son verrou. Ne pas utiliser plusieurs workers Uvicorn sur le même état.

Alternative premier plan depuis ce dossier :

```sh
PYTHONPATH=backend .venv/bin/uvicorn kuberweaver.app:app --host 127.0.0.1 --port 8088
```

Le lab doit être démarré pour les déploiements et l'accès OpenBao. Le portail peut toujours exposer des fonctions locales et des indisponibilités explicites si le lab n'est pas joignable. Aucun démarrage automatique de tous les nœuds GNS3 n'est ajouté.

## Développement et vérification

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-execution.txt -r requirements-mcp.txt
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
.venv/bin/python -m pytest tests -q
```

Le build frontend est servi par FastAPI sur la même origine. `/healthz`, `/metrics` et `/docs` sont disponibles sur le serveur local. Les métriques décrivent le processus et les jobs ; aucune connexion Grafana/Prometheus distante n'est prétendue.

Les scripts `scripts/browser_acceptance.py` et `scripts/api_acceptance.py` sont des acceptations avec effets réels sur les ressources de démonstration, pas de simples tests unitaires. Leur état et leurs ports doivent être préparés avant de les relancer. Le navigateur de test utilise Playwright, installé séparément de l'environnement applicatif.

Preuves non secrètes dans `data/evidence/` : résultats de tests, acceptation navigateur, jobs API et readbacks indépendants, déploiements directs et OpenBao. `docs/ACCEPTANCE.md` énumère les gates. Les documents de cadrage originaux sont conservés sans modification.

## Limites de sécurité

Ce prototype local n'est pas prêt à être exposé sur Internet : pas de SSO, de multi-tenant, de permissions par objet, de haute disponibilité ou de marketplace sandboxée. Le serveur GNS3 existant conserve son API LAN sans authentification ; le portail ne la sécurise pas pour les autres clients. Les comptes root des invités et les administrateurs du poste sont dans le périmètre de confiance. Aucune modification des routes du serveur, d'un autre projet GNS3 ou de la production n'est nécessaire.

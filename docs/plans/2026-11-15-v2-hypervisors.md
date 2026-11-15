# Connexions hyperviseur et provisionnement V2, plan d’implémentation

> For Hermes: Use subagent-driven-development to implement this plan task-by-task.

Goal: Ajouter un parcours ESXi remplaçable au prototype sans réécriture, fournisseur global imposé ou deuxième orchestrateur.

Architecture: FastAPI, SQLite, catalogue et Worker existants restent le socle. Les connexions désignent des pilotes du registre `hypervisor`; les modèles de confiance peuvent désigner un fournisseur `execution`. Une demande utilise un seul exécuteur, les mélanges sont explicitement refusés. Les secrets ne sont que des références dans les connexions, graphes et journaux.

Tech Stack: Python, FastAPI, SQLite, React/TypeScript, pyVmomi optionnel pour le pilote ESXi, OpenTofu et vmware/vsphere versionnés pour le provisionnement.

## Périmètre et preuves de départ

L’utilisateur autorise l’évolution locale à partir des documents `docs/prompt/` et demande de rester modulaire, sans usine à gaz. Aucun push ni modification des VM préexistantes. La première tranche couvre connexions, découverte, formulaire VM, plan sauvegardé, approbation et création contrôlée. Les autres éléments d’origine restent suivis ci-dessous, pas implicitement livrés.

Baseline réellement exécutée : 527 tests Python réussis, 1 ignoré, 47 tests frontend réussis, build réussi. Une alerte de finalisation HTTP préexistante a été observée après pytest.

ESXi 192.168.1.100 répond en TCP/443. Certificat non reconnu par la PKI, empreinte SHA-256 observée puis confirmée explicitement par l’utilisateur : `1842c59eb9ffb4181d85b21681551c4325d60400f213b05ddba230430eda3ccd`. La confiance par empreinte du SDK Python n’autorise pas `allow_unverified_ssl` dans OpenTofu. Si le fournisseur ne sait pas appliquer cette confiance, le provisionnement reste bloqué jusqu’à mise en place d’un certificat validable.

Le prompt a entre-temps été expurgé du mot de passe. Ne pas le réintroduire, ne pas essayer de le récupérer d’un historique. L’OpenBao du laboratoire existant n’est pas joignable depuis ce poste; ne pas remplacer silencieusement sa configuration ni démarrer tout GNS3. Prévoir une source OpenBao locale distincte et explicitement sélectionnée pour la V2, avec saisie humaine protégée des identifiants.

## Contrats gelés

### Connexion, API et découverte

Nouveau `backend/kuberweaver/connections.py`, sans imports VMware.

ConnectionSpec contient `label`, `driver`, `endpoint` (origine HTTPS sans identifiant, chemin, requête ou fragment), `secret_ref`, `secrets_provider` (nom de fournisseur, pas chemin/import), `tls_sha256` (64 hex minuscules ou null), `enabled`. Les identifiants de connexion sont générés sur 32 hex; `revision` est serveur. Les destinations loopback, link-local, multicast et indéfinies sont refusées. Première version: adresse IP explicite, sans résolution DNS implicite. Un pilote ultérieur peut faire évoluer le contrat sans branches fournisseur dans le noyau.

Le service Connections(app) expose `get(id)`, `list()`, `create(spec, actor)`, `update(id, spec, actor)`, `fingerprint(id)`, `driver(connection)`, `access(id)` context manager et `discover(id, actor)`. `access` livre `(connection, bytes)` depuis la source de secrets explicitement sélectionnée. Les erreurs de fournisseur sont statiques; aucune valeur ou exception brute n’est persistée. Aucune connexion réseau dans les GET de consultation. Les observations ont leur révision, date, statut, inventaire et erreur statique. Revalider la révision après un appel externe.

Pilote enregistré sous `hypervisor/<driver>`: `discover(connection: dict, credentials: bytes) -> dict`. Il renvoie `server` (name/version/api_type, host_id, write_api_supported bool|null), `datastores` (id/name/capacity_bytes/free_bytes/accessible), `networks` (id/name), `resource_pools` (id/name), `virtual_machines` (id/name/uuid/power_state/cpu/memory_mb). Le consommateur ferme et valide ces objets, limite tailles et longueurs. Les identifiants et noms d’infrastructure sont des données, jamais des commandes.

Routes: GET `/api/connection-drivers` -> `{drivers:[{id,label,available}], secret_sources:[{id,available,can_store,references:[]}]}`; GET/POST `/api/connections`; GET/PATCH `/api/connections/{id}`; POST `/api/connections/{id}/test` et `/discover`; PUT `/api/connections/{id}/credentials` réservé à une session administrateur humaine. GET connexion inclut `observation` null ou dernier résultat. Création/modification exige admin et scope configure pour une machine; test/découverte exige admin/operator et scope read:infrastructure. GET exige lecture infrastructure. Saisie de mot de passe hors MCP, aucune lecture brute exposée.

### Plan et exécution

Un manifeste de confiance peut déclarer `execution_provider` et `admission_profile: virtual-machine`. Sans ces champs, comportement historique inchangé. Le profil VM retire seulement les contraintes propres aux ports/cibles conteneurs; schéma fermé, secrets interdits, quotas et droits restent communs. Refuser le mélange des profils/exécuteurs, les mises à jour/imports et les graphes VM multiples pour cette première tranche.

Protocole optionnel d’exécuteur : `prepare_plan(job, emit) -> {plan_digest, actions, summary, bindings}` et `validate_plan(job) -> None`. Les exécuteurs historiques n’ont rien à implémenter. Les préparations passent par le même Worker, avec opération `prepare`, puis état `pending_approval`. L’approbation lie le résumé/digest sauvegardé, le graphe et les empreintes; l’application ne replanifie jamais. Source, connexion, confiance TLS et artefacts sont revérifiés au dequeue et à chaque commande.

Créer une VM neuve par demande avec disque neuf et, si supporté, ISO existante autorisée. Un disque vide ou une ISO attachée ne prouve pas un OS installé. Pas de destruction automatique après échec ambigu. La suppression ne devient disponible qu’avec un plan séparé et une approbation distincte; aucun rollback générique offert par défaut pour un fournisseur qui ne le permet pas.

## Tâche 1: connexions et API génériques

Files: create `backend/kuberweaver/connections.py`, `tests/test_connections.py`. Parent integrates one installer call in `api.py` and explicit routes in `agent_auth.py`.

1. Écrire et exécuter un test HTTP échouant sur POST/GET connexion sans secret persisté.
2. Implémenter le minimum, relire la même connexion et vérifier la persistance après redémarrage.
3. Ajouter chaque comportement par RED/GREEN: droits/CSRF/scopes, pilote remplaçable, test explicite, erreurs expurgées, révision concurrente, source de secrets indisponible, absence d’accès réseau sur GET.
4. Test command: `.venv/bin/python -m pytest tests/test_connections.py -q`.

## Tâche 2: pilote ESXi indépendant

Files: create `examples/extensions/esxi-runtime/plugin.yaml`, `examples/extensions/esxi-runtime/module.py`, `tests/test_esxi_driver.py`, `requirements-infrastructure.txt`.

1. Écrire un test échouant d’inventaire normalisé en injectant la frontière SDK.
2. Implémenter SmartConnect/Disconnect, contrôle strict PKI ou empreinte SHA-256, suppression du ContainerView en finally. Aucun appel créant/modifiant/supprimant une VM.
3. Écrire puis passer tests de certificat différent, timeout, authentification échouée, absence de fuite, fermeture de session et lecture partielle de licence. Licence gratuite => write_api_supported false, autre licence sans preuve d’écriture => null.
4. Test command: `.venv/bin/python -m pytest tests/test_esxi_driver.py -q`.

## Tâche 3: portail de connexions

Files: create `frontend/src/Connections.tsx`, `frontend/src/Connections.test.tsx`; modify only navigation in `frontend/src/App.tsx`.

1. Test échouant de liste des pilotes provenant de l’API et formulaire enregistrant uniquement références et confiance TLS.
2. Implémenter liste/ajout/modification/désactivation, actions explicites test/découverte et affichage daté de l’inventaire réel.
3. Vérifier lecture après écriture, refus de réponses malformées, pilote absent, droits admin/operator/viewer et états d’erreur.
4. Ajouter la saisie protégée des identifiants seulement via PUT credentials disponible pour l’administrateur humain; aucun secret dans stockage navigateur, logs ou modèle de connexion.
5. Commands: `npm --prefix frontend test -- --run`, `npm --prefix frontend run build`.

## Tâche 4: préparation et choix d’exécuteur dans le circuit existant

Files: modify `jobs.py`, `providers.py`, `policy.py`, `agent_auth.py`, `mcp_server.py`; add dedicated tests.

1. RED/GREEN choix d’un fournisseur par manifeste sans changer la liaison par défaut Ansible.
2. RED/GREEN profil VM conservant quotas/sécurité; refus des graphes mixtes.
3. RED/GREEN préparation sérialisée avant approbation, invalidation de dérive et interruption en revue manuelle.
4. RED/GREEN absence de rollback non supporté, propagation de requires_manual_review du résultat au job.
5. Outils MCP typés sur les routes autorisées, jamais une voie privilégiée VMware.

## Tâche 5: provisionnement OpenTofu

Files: create `backend/kuberweaver/provisioning.py`, `examples/extensions/vsphere-provisioning/`, `plugins/vsphere-vm/`, `tests/test_provisioning.py`.

1. Tests échouants de variables dérivées de références réelles, exclusion de secrets, plan de création seul et refus des imports/remplacements.
2. Implémenter un exécuteur opt-in, état privé par demande et verrous, vrai init/validate/plan/show, application du fichier exact approuvé, empreintes du binaire/provider/configuration/connexion/template/plan.
3. Ne jamais hériter TF_CLI_ARGS, TF_VAR, VSPHERE_* ni journaux de débogage. Injecter uniquement les identifiants résolus au processus, pas dans HCL/tfvars/arguments. Ne pas publier sortie brute/plan/state.
4. Vérification indépendante par le pilote après application. Après interruption, persister tentative ambiguë et interdire relance/destruction aveugle.
5. Vérifier réellement init/validate du template dans un état jetable. Les tests injectés ne prouvent pas une VM distante.

## Tâche 6: coffre, recette et compte rendu

Files: `secrets.py` et tests pour écriture explicitement autorisée, script de coffre local isolé, documentation V2 et preuves privées.

1. Écriture facultative de SecretValue sous namespace administré, lecture de vérification, erreurs expurgées. Fournisseur historique reste read-only par défaut. Aucun fallback non chiffré.
2. Coffre local séparé si le lab distant reste indisponible, lié au loopback, stockage Raft persistant, conteneur/données possédés exclusivement par ce prototype. Préserver le coffre distant et la liaison globale existante.
3. Recette API sur vrai serveur loopback en état isolé, build/tests complets, contrôles de non-fuite et git diff --check. Tout compte local/jeton de recette reste privé.
4. Recette ESXi uniquement après saisie humaine du secret dans le coffre. Afficher les blocages de TLS/licence/permissions, ne jamais les remplacer par des réponses plausibles.
5. Rapport final distinguant implémenté, testé localement, validé sur ESXi et bloqué. Aucun push ni commit automatique.

## Éléments d’origine restant suivis

- ESXi réel, première cible de la tranche ci-dessus; Hyper-V, Proxmox et cloud: futurs pilotes, aucune carte faussement fonctionnelle.
- Coffre de secrets: critère obligatoire pour les nouveaux identifiants.
- Cycle de vie Ansible: contrat sauvegarde/application/vérification/restauration; ne pas promettre une réversibilité universelle.
- Stack supervision/logs/Wazuh: prochain lot distinct, avec hôtes autorisés et preuves de collecte/détection, pas simple présence au catalogue.
- Intégration site web, Cloudflare Tunnel, supervision et logs: composition future, validation des identités/réseaux/coûts avant mutation externe.
- Documentation système open source: documentation du socle et contrats V2, pas une nouvelle plateforme imposée.
- Catalogue Ansible Galaxy d’organisation: import de contenu épinglé/revu et contrat de cycle de vie avant exécution; publication distante hors autorisation actuelle.
- API, CLI et MCP: même sécurité et mêmes services, couverture des fonctions retenues par outils typés.
- FinOps: conserver l’estimation existante sans en faire une obligation universelle pour tous les prochains fournisseurs.
- Marketplace tiers, messageries et tableaux de bord externes: options séparées, aucune dépendance pour connecter un hyperviseur.

# KuberWeaver V2, connexions et hyperviseurs

Évolution locale du prototype existant. La recette intégrée n’est pas clôturée ; ce document ne constitue pas une validation de création de VM sur l’ESXi.

## Qualification locale réalisée

Les défauts Worker ont été corrigés et les revues SPEC puis qualité sont validées : interruption ambiguë avec revue manuelle durable, résultat canonique capturé sous verrou, notifications hors verrou et préservation d’une approbation ou d’un rollback concurrent même si une journalisation après publication échoue.

L’instance a été basculée sur PostgreSQL et le portail est actif sur `http://127.0.0.1:8088`, via son service utilisateur systemd. La migration réelle et la relecture indépendante conservent les trois comptes, quinze demandes, vingt-neuf sessions et cent événements d’audit initiaux. Les connexions/déconnexions de vérification ajoutent quatre événements d’audit dans PostgreSQL, sans modifier les comptes, sessions antérieures ou SQLite. L’authentification HTTP avec un compte existant, les demandes, les connexions et l’état de stockage ont été relus après démarrage. Voir `POSTGRESQL.md` et les preuves privées `data/evidence/final-runtime.json` et `postgresql-live-authenticated.json`.

Le fournisseur de création ESXi reste indépendamment désactivé : cette qualification locale ne certifie pas une création de VM réelle.

## Coffre local simple actif, ancienne recette OpenBao conservée

La divergence Docker `NetworkMode="bridge"` / `"default"` a été corrigée sans assouplir les contrôles d’image, propriété, montages et permissions. L’ancien Raft non initialisé a été archivé en privé. La nouvelle tentative s’est ensuite terminée avec un coffre effectivement initialisé et scellé, mais sans bootstrap récupérable ni jeton applicatif sauvegardé. Une perte de réponse lors de l’initialisation est suspectée ; ce n’est pas une recette réussie.

Le conteneur exact `kw-openbao-v2` est arrêté et son stockage initialisé est conservé. Ne pas multiplier les initialisations ni effacer son Raft. Preuve privée actuelle : `data/evidence/v2-local-bao-recipe.json` ; l’ancienne tentative reste documentée dans `v2-local-bao-attempt.json`.

Le fournisseur optionnel local chiffré Fernet/cryptography est installé, activé et disponible dans l’API des sources de secrets sous `local-vault`. Aucun service Docker supplémentaire n’est nécessaire. Sa clé et son marqueur ont été relus, ses fichiers sont 0600 et son répertoire privé 0700 ; l’installation est idempotente. Les autres activations et la liaison de secrets globale sont inchangées. Choisir explicitement cette source pour une nouvelle connexion. Aucun identifiant ESXi réel n’y a été déposé. Le connecteur OpenBao/Vault reste une alternative ; la clé locale ne protège pas contre une compromission du compte système ou de root.

## Le principe

Le noyau conserve les comptes, les droits, les quotas, les demandes, les approbations, l’audit et une seule file d’exécution. Une connexion choisit son pilote et sa source de secrets par leur nom dans le registre. Le noyau ne choisit pas un constructeur à partir d’une série de conditions VMware, Proxmox ou Hyper-V.

Le catalogue et ses formulaires restent déclaratifs. Un nouveau service compatible utilise l’exécuteur existant. Un nouveau transport ou cycle de vie apporte son fournisseur. Les demandes nécessitant plusieurs exécuteurs sont refusées dans cette première tranche, plutôt que déclencher un second système d’orchestration.

Les connexions ne nécessitent ni courtier de messages ni modèle d’IA obligatoire. À la demande de l’utilisateur, le stockage de l’instance a été migré séparément de SQLite vers PostgreSQL, sans modifier les contrats des modules. Le coffre de secrets reste un fournisseur remplaçable et explicitement choisi, sans mots de passe de connexions en clair dans la base applicative.

## Les modules

- `backend/kuberweaver/connections.py` : configurations de connexion, références, observations datées et API. Aucune dépendance VMware.
- `examples/extensions/esxi-runtime/` : pilote `hypervisor/esxi`, installé et activé sur l’instance locale pour la découverte en lecture seule avec pyVmomi. Sa disponibilité a été relue par HTTP ; aucune découverte authentifiée n’a été lancée.
- `examples/extensions/local-vault/` : coffre Fernet simple actif pour les nouvelles connexions qui le choisissent explicitement.
- `examples/extensions/openbao-local/` : ancienne alternative Docker, recette interrompue et stockage conservé, distincte du coffre distant historique.
- `examples/extensions/vsphere-provisioning/` et `plugins/vsphere-vm/` : provisionnement en qualification par plan OpenTofu sauvegardé. Ils ne constituent pas une capacité distante validée.
- `frontend/src/Connections.tsx` : configuration, dépôt protégé et actions explicites de découverte.
- `frontend/src/DiscoverySchemaFields.tsx` : choix issus des observations grâce aux annotations `x-options-source` du schéma, sans branche VMware dans le Canvas.

Désactiver une extension ne doit pas remplacer silencieusement son fournisseur par un autre. La fonction concernée devient indisponible, le reste du portail reste utilisable.

## Parcours Connexions

1. Installer et autoriser un pilote de confiance et une source de secrets.
2. Dans Connexions, enregistrer un libellé, le pilote, l’origine HTTPS du serveur, la source et le nom logique du secret. La première version accepte des adresses IP explicites, pas des URL libres ou des noms DNS.
3. Si la source autorise explicitement l’écriture, déposer les identifiants dans le formulaire administrateur. Le mot de passe n’est pas relu par le navigateur. Il ne figure pas dans la connexion, les événements ou le MCP.
4. Cliquer sur Tester ou Découvrir. Ces actions sont explicites, les consultations ordinaires n’effectuent pas de sonde réseau.
5. Lire la date, la révision, le serveur, les stockages, les réseaux, les pools et les VM observées. Une erreur ou une absence d’observation reste visible. Une VM inventoriée ne prouve pas qu’un OS invité fonctionne.

L’administrateur configure et dépose les identifiants. L’opérateur peut tester et découvrir. Le lecteur consulte les observations. Les agents utilisent les mêmes routes et les mêmes limites de rôle et de portée; le dépôt de valeurs secrètes reste réservé à une session administrateur humaine.

## Confiance et secrets

Sans empreinte déclarée, le pilote utilise la validation habituelle du certificat et du nom du serveur. Une empreinte SHA-256 confirmée par l’administrateur peut être utilisée pour un hôte à certificat privé : elle est contrôlée sur chaque socket TLS, avant l’envoi de données HTTP. Une sonde préalable suivie d’une connexion non vérifiée n’est pas acceptée.

La confiance par empreinte du pilote Python ne remplace pas les capacités TLS du fournisseur OpenTofu. Pour celui-ci, un certificat validable et un nom correspondant restent nécessaires. Aucun `allow_unverified_ssl` ni proxy de contournement n’est introduit.

OpenBao est en lecture seule par défaut. L’écriture est une capacité facultative, limitée à un espace de noms administré, avec relecture exacte obligatoire. Les nouvelles valeurs écrites sont bornées pour rester lisibles après sérialisation JSON. Une panne du coffre ne déclenche aucun repli vers un fichier en clair.

Le coffre historique du laboratoire n’étant pas joignable lors de cette évolution, son emplacement, sa liaison globale et ses données doivent être conservés. Un éventuel coffre local V2 possède son conteneur, son stockage et son fournisseur distincts. Il ne doit être choisi que pour les connexions qui le demandent explicitement. Le stockage est chiffré, mais un coffre de laboratoire avec ses moyens de récupération sous un même compte local n’assure pas une séparation des responsabilités de production.

## Provisionnement en qualification

La cible retenue est le fournisseur officiel `vmware/vsphere` versionné, compatible avec la création d’une VM neuve sur un ESXi autonome disposant des droits API nécessaires. Le clonage et le déploiement OVF ne sont pas assimilés à cette création. Une licence ESXi gratuite ne doit pas être annoncée compatible avec les écritures API.

Un blocage supplémentaire a été confirmé localement sur la version **2.18.0** : le schéma réel expose `power_state` en lecture seule, sans argument `power_on`, et [la fonction de création du fournisseur](https://github.com/vmware/terraform-provider-vsphere/blob/v2.18.0/vsphere/resource_vsphere_virtual_machine.go) appelle `PowerOn`. Elle ne satisfait donc pas le contrat retenu de création d’une VM arrêtée. L’exécuteur refuse préparation et application, sans option permettant de contourner cette barrière. Il faut une décision de périmètre ou un autre fournisseur qualifié, pas un arrêt ajouté après coup hors plan. Les vrais contrôles locaux `version`, `init -lockfile=readonly`, `validate` et `providers schema` passent, mais ne lèvent pas ce blocage.

Le parcours à qualifier utilise la même file : préparation non mutante du plan, affichage du résumé et de son empreinte, approbation, application du fichier exact, puis relecture indépendante. Les paramètres du formulaire sont des références d’inventaire et des ressources bornées, jamais du HCL, un chemin ou une commande fournis librement.

Les identifiants sont résolus pour le processus, pas écrits dans les variables. Plan, état et traces techniques restent privés. Une modification de la connexion ou des dépendances approuvées invalide l’autorisation. Une tentative interrompue ne doit pas être relancée ou détruite automatiquement.

Le modèle de VM reste prévu et l’extension de provisionnement reste désactivée tant que la recette réelle n’est pas acquise. La suppression d’une VM n’est pas un retour arrière universel; elle nécessiterait son propre plan et une autorisation distincte. Aucune suppression automatique après un échec ambigu n’est autorisée.

## Ce qui a réellement été observé

- Le serveur `192.168.1.100` répond et expose publiquement ESXi `8.0.3`, build `24280767`, API `HostAgent`.
- L’empreinte a été confirmée par l’utilisateur. Le contexte TLS du pilote a ensuite permis cette lecture publique et refusé une empreinte volontairement incorrecte.
- Le mot de passe ne figure plus dans le prompt. Il n’est pas récupéré depuis un historique.
- L’inventaire authentifié, la licence, les droits d’écriture et la création d’une VM ne sont donc pas déclarés validés.
- Le parcours navigateur, HTTP et MCP a été exercé sur un vrai serveur local isolé avec un pilote de test explicitement nommé. Cette preuve valide les interfaces et leurs contrôles, pas une connexion ESXi authentifiée.

Preuves privées : `data/evidence/v2-esxi-public.json`, sauvegarde préalable et état initial référencés dans `data/evidence/v2-baseline.json`.

## Vérifications reproductibles

Validation intégrée finale, après PostgreSQL et coffre Fernet : **1 244 tests backend réussis, 1 ignoré** (`redis-server` absent), **235 tests frontend dans 12 fichiers**, compilation TypeScript/Vite et `git diff --check` réussis. Les 113 fichiers de code du périmètre ont été rehachés pour vérifier l’absence de modification pendant cette exécution. Preuves privées : `data/evidence/final-verification.json`, `final-source-manifest.json` et `postgresql-and-vault-tests.xml`. Les tests utilisent un état par défaut jetable, des schémas PostgreSQL UUID dédiés et les frontières HTTP/MCP/navigateur ; la connexion au portail réel avec un compte existant est documentée séparément dans `final-live-capabilities.json`.

Référence V2 historique, avant PostgreSQL : **1 146 tests backend réussis, 1 ignoré** (`redis-server` local absent), **235 tests frontend réussis dans 12 fichiers**, puis build TypeScript/Vite réussi. Le contrôle OpenTofu réel était activé : `version`, `init -lockfile=readonly`, `validate` et `providers schema` ont été exécutés. Ce rapport ancien est conservé dans `data/evidence/v2-backend-tests.xml`.

Les correctifs Worker et le coffre local Fernet ont depuis été qualifiés séparément, comme décrit plus haut. Les tests historiques ne prouvent pas un inventaire ESXi authentifié ou une création de VM.

Depuis la racine du projet :

```sh
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-execution.txt -r requirements-mcp.txt -r requirements-infrastructure.txt -r requirements-postgresql.txt -r requirements-local-vault.txt
state=$(mktemp -d "${TMPDIR:-$HOME/.cache}/kw-tests.XXXXXX")
env -u KUBERWEAVER_DATABASE_URL_FILE -u KUBERWEAVER_DATABASE_URL KUBERWEAVER_STATE_DIR="$state" KW_TOFU_LOCAL_CHECK=1 .venv/bin/python -m pytest tests -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

La recette avec navigateur utilise des dépendances facultatives. Conserver aussi un état par défaut jetable : le module ASGI instancie une application lors de l’import, et partager un même état PostgreSQL entre le processus pytest et ses serveurs enfants déclenche volontairement le verrou de contrôleur unique. Les fixtures PostgreSQL créent leurs propres schémas UUID dans `data/postgresql/test-url` ; ce fichier doit désigner la base de test, jamais la base active.

```sh
uv pip install --python .venv/bin/python -r requirements-acceptance.txt
# Si aucun Chrome/Chromium système n’est disponible :
.venv/bin/python -m playwright install chromium
state=$(mktemp -d "${TMPDIR:-$HOME/.cache}/kw-browser-tests.XXXXXX")
env -u KUBERWEAVER_DATABASE_URL_FILE -u KUBERWEAVER_DATABASE_URL KUBERWEAVER_STATE_DIR="$state" .venv/bin/python -m pytest tests/test_v2_real_protocol.py -q
```

Ce test utilise des états temporaires et un pilote de test, jamais le compte ESXi ni les ressources du laboratoire. Les scripts historiques d’acceptation de déploiement, eux, peuvent modifier de vraies cibles; ils ne doivent pas être lancés comme de simples tests unitaires.

## Suite, sans dépendance imposée

Les connecteurs Hyper-V, Proxmox et cloud, le catalogue d’organisation Ansible Galaxy, les stacks de supervision et de sécurité, Cloudflare et les notifications externes restent des lots distincts. Leur absence ne doit pas empêcher la gestion d’une connexion ou un déploiement utilisant les outils existants.

Le suivi complet du besoin d’origine et les critères de cette tranche sont dans `plans/2026-11-15-v2-hypervisors.md`. Les contrats historiques restent dans `EXTENSIONS.md`, `EXECUTION.md`, `SECRETS.md` et `AGENT_ACCESS.md`.

# Provisionnement vSphere, prototype opt-in, non validé sur ESXi

Le module Python du protocole de plan sauvegardé est implémenté et testé localement.
**La création de VM n'est pas annoncée opérationnelle** : le blueprint livré reste
`capability: planned`, l'extension reste désactivée par défaut et son état est
`blocked`. La découverte ESXi et le coffre sont des capacités séparées : cet état
ne les désactive pas.

## Deux prérequis bloquants

1. **PKI standard valide pour l'endpoint HTTPS.** Une empreinte acceptée par le SDK
   n'est pas une option de confiance du fournisseur officiel. Il faut une chaîne
   approuvée et un nom/SAN correspondant à l'endpoint. Aucun `allow_unverified_ssl`,
   proxy spécial ou correctif fournisseur ne contourne ce contrôle. Python et Go
   utilisent le même bundle CA explicite et épinglé ; aucune configuration TLS ou
   de proxy héritée n'est transmise. Une autre CA ne peut venir que de code de
   confiance administrateur (`executor.ca_bundle`), jamais d'un graphe.
2. **Création d'une VM arrêtée.** Le schéma réel de `vmware/vsphere 2.18.0` déclare
   `power_state` calculé seulement, sans argument `power_on`. Le workflow
   `resourceVSphereVirtualMachineCreateBare` appelle systématiquement `PowerOn`.
   `force_power_off` concerne l'arrêt pour modification/suppression, pas la création.
   Le template est syntaxiquement valide mais **ne garantit pas une VM arrêtée**.
   L'exécuteur refuse donc préparation et application avec un message statique,
   plutôt que de créer puis éteindre hors plan. Aucun réglage ne lève cette barrière.
   Une évolution exige une décision de périmètre et une nouvelle revue/recette.

Aucun identifiant ESXi n'est nécessaire pour les vérifications locales ci-dessous.
Aucun vrai plan, apply, readback distant ou OS démarré n'a été validé par ces tests.

## Contrat d'intégration

```python
SavedPlanExecutor(state_dir, template_dir, binary=None, runner=None)
provider.connections = connections_service  # injection par resolve_provider
prepared = provider.prepare_plan(job, emit)
# Le cœur conserve prepared dans job['prepared_plan'], puis affiche le résumé.
provider.validate_plan(job)  # strictement local : fichiers + fingerprint local
# Après approbation humaine, le cœur fixe SHA256(JSON canonique(prepared)) dans
# job['approved_prepared_plan'], plus approved_by/at/graph_hash/provider.
result = provider.execute(job, emit)  # même job approuvé ; jamais de re-plan
```

`prepared` contient seulement `plan_digest`, `actions`, `summary`, `bindings`.
`plan_digest` est l'empreinte du dossier de revue privé incluant le plan binaire,
les entrées et leurs empreintes ; ce n'est pas une signature d'identité autonome.
La valeur `approved_prepared_plan` est l'empreinte du **résumé public complet**, pas
une copie directe de `plan_digest`. Le contrat suppose la persistance et le contrôle
d'approbation du noyau ; aucune branche vSphere n'est nécessaire dans Jobs.
`validate_plan` peut être appelée sous le verrou Jobs : elle ne fait ni accès au
coffre, ni décodage de secret, ni connexion TLS/SDK/CLI. Elle compare les fichiers
et le fingerprint local connexion/révision/provenance. Les octets du secret et le
transport sont contrôlés à nouveau uniquement dans `prepare_plan`/`execute`, hors
verrou partagé Jobs ; une rotation sans révision est donc refusée à l'exécution.
Ce contrôle est réentrant et ne reprend pas `flock` : le noyau l'appelle aussi à
chaque frontière de commande. L'état de la tentative active n'est admis que pour
le même job et le même thread déjà propriétaire de l'exécution ; après retour,
interruption, changement de thread ou redémarrage, il interdit toute reprise.
Les références de schéma sont refusées et leur registre n'autorise aucune récupération.

`Connections.access(id)` fournit `(connection, credential_bytes)` dans un contexte.
Le document de secret contient exclusivement `username` et `password`. Le service
fournit également `get`, `fingerprint`, `driver`; le pilote fournit `discover` pour
la relecture indépendante. Aucun accès à un objet `app` n'est présumé dans le SDK.
`register(ctx)` utilise uniquement `ctx.plugin_data_dir()` et `ctx.register(...)`.

Le graphe est limité à une VM indépendante `dev`, sans dépendances ni mode de
panne injecté. Les métadonnées standard `strategy`, `label`, `position` de Graph
sont acceptées ; `strategy` n'ajoute aucune vague d'exécution à une VM indépendante.
Configuration fermée : référence de connexion, nom, IDs réels du
datastore/réseaux/pool, CPU 1–8, RAM 128–16384 MiB, disque vierge 1–100 GiB et
`otherLinux64Guest` fixe. Le nom distant est `kw-<12 premiers caractères job>-<nom>`.
Les annotations `x-options-source` alimentent les sélecteurs depuis les observations
existantes, sans déclencher une découverte implicite. Aucun port ou endpoint invité
n'est inventé ; aucun OS ni bootstrap n'est installé.

## Protocole privé et limites de sécurité

- OpenTofu **1.13.9 Linux amd64**, SHA-256 du binaire
  `9ce9173bf161f6466ef57dab7edebe8f55089fdf81caa967415323c25d004990`.
- Fournisseur officiel **vmware/vsphere 2.18.0** et lockfile réel produit par
  `tofu init`. Initialisation avec `-lockfile=readonly`, sans override ni cache de
  fournisseur hérité. Binaire local du fournisseur et lockfile épinglés ensuite.
- Empreintes du template, manifeste, sources Python, HCL/variables générés,
  configuration CLI exacte (`disable_checkpoint = true\n`), bundle CA, connexion,
  document de secret et plan binaire. Contrôles avant/après chaque commande ; seul
  `apply ... create.plan` est autorisé après approbation.
- Le plan doit créer exactement `vsphere_virtual_machine.vm` avec `['create']`.
  Import, adoption, drift, mise à jour, remplacement, suppression et autre ressource
  gérée sont refusés ; des lectures de sources de données restent admissibles.
- Précontrôles sur ESXi 8 autonome, entitlement API-write explicitement vrai,
  références découvertes exactes, accessibilité/espace disponible et absence du nom.
  Cela ne constitue **pas** une preuve exhaustive des permissions de création.
- Répertoires privés 0700, fichiers 0600 (exécutables fournisseurs 0700), verrou
  `flock` local et liste fermée d'artefacts. Liens symboliques/hardlinks et fichiers
  de surcharge refusés. Le verrou n'est ni distribué ni un verrou sur l'hyperviseur.
- Identifiants uniquement dans `VSPHERE_USER`/`VSPHERE_PASSWORD` du sous-processus,
  sans `TF_VAR`, logs/debug/proxy hérités ni persistance de session sur disque.
  Stdout/stderr bruts ne sont ni publiés ni journalisés. JSON `show` et artefacts,
  y compris membres ZIP du plan, sont contrôlés contre les secrets connus. Un
  artefact suspect reste privé, non approuvable ; ce n'est pas un détecteur DLP universel.
- Un marqueur de tentative est créé et synchronisé **avant** apply. Toute tentative
  ou tout état préexistant interdit la reprise. Après erreur/interruption ambiguë,
  `failed` + `requires_manual_review: true`, même après redémarrage. Aucun nettoyage
  distant destructif. `supports_rollback=False`, `can_rollback=False`; rollback
  renvoie un échec explicite sans commande. Pas d'import/update/delete/destroy.
- Le succès simulé exige une UUID d'état cohérente et une découverte SDK indépendante
  correspondant à l'UUID, au nom, aux CPU/RAM et à l'état d'alimentation attendus.

Ce sont du code Python de confiance et un contrôle de provenance, pas une sandbox
contre un administrateur local malveillant. Les empreintes ne constituent pas une
réservation distante ; les autres acteurs de l'hyperviseur restent un domaine de
concurrence distinct. Le fournisseur peut conserver des sessions côté serveur ;
`persist_session=false` interdit leur sauvegarde locale, sans promettre un logout.

## Vérifications reproductibles

```sh
.venv/bin/python -m pytest tests/test_provisioning.py -q
KW_TOFU_LOCAL_CHECK=1 .venv/bin/python -m pytest \
  tests/test_provisioning.py::test_local_template_init_validate_and_real_schema -q
```

Le premier groupe injecte explicitement les frontières CLI/TLS/SDK. Les tests de
la mécanique de plan lèvent **dans la fixture seulement** la barrière arrêtée ;
ils ne prouvent donc pas cette capacité du fournisseur réel. Les tests Jobs/engine
peuvent copier le manifeste dans un répertoire jetable puis y fixer explicitement
`capability: live`, sans changer le blueprint livré.

Le second test exécute réellement `version`, `init -backend=false -lockfile=readonly`,
`validate` et `providers schema` dans `TMPDIR`, avec des valeurs publiques factices
pour satisfaire les defaults obligatoires du fournisseur. Il ne fait ni plan ni
apply, ne configure aucun endpoint et n'utilise aucun identifiant réel. Le réseau
éventuel concerne seulement le registre public/distribution du fournisseur.

### Sources amont versionnées

- [Ressource VM et limitations](https://github.com/vmware/terraform-provider-vsphere/blob/v2.18.0/docs/resources/virtual_machine.md)
- [Schéma et workflow create bare](https://github.com/vmware/terraform-provider-vsphere/blob/v2.18.0/vsphere/resource_vsphere_virtual_machine.go)
- [Authentification environnement, TLS et sessions](https://github.com/vmware/terraform-provider-vsphere/blob/v2.18.0/vsphere/provider.go)
- [Compatibilité ESXi/API write](https://github.com/vmware/terraform-provider-vsphere/blob/v2.18.0/docs/index.md)

Une promotion vers `live` exige une recette distante explicitement autorisée avec
licence/permissions, PKI conforme, périmètre d'alimentation résolu, vrai plan approuvé,
application exacte et relecture SDK ; rien de cela n'est affirmé ici.

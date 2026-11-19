# KuberWeaver, cadrage novembre 2026

Deux rendus PDF de 2 pages (recto verso) dans ce dossier, plus ce résumé lisible au terminal.
Sources analysées le 19/11/2026, capacités à re-vérifier par édition.

## Rendu 1 : Vision & état de l'art
`Rendu1_Vision_Etat_de_l_art.pdf`

- Le problème : outils dispersés (hyperviseurs, IaC, monitoring, ITSM, coûts), procédures manuelles, coût non gouverné au moment de la décision.
- L'idée : un plan de contrôle auto-hébergé, modulaire, qui transforme une procédure revue en service demandé, approuvé, exécuté, vérifié, retiré, sur l'infra qu'on possède, avec le coût au centre.
- Philosophie empruntée à Hermes : noyau minimal stable, tout le reste en briques qu'on branche, base plug-and-play avec l'essentiel, divulgation progressive, la plateforme grandit et apprend avec l'entreprise (capitaliser les procédures revues et les checks, pas un ML obligatoire).
- Diagramme simple : catalogue -> demande -> plan exact + coût -> approbation humaine -> exécution -> vérification indépendante -> service géré + coût suivi -> retrait vérifié, avec une boucle coût / drift / apprentissage.

### État de l'art (honnête)
La modularité n'est pas neuve :
- Waldur : marketplace/CMP open-source, processors par type de service. Portail lourd.
- CloudBolt : CMP hybrid self-service, Terraform/Ansible. Commercial.
- ManageIQ : CMP open-source, lifecycle, chargeback, providers + Automate. Appliance monolithique.
- Backstage : portail développeur, plugins front/back, scaffolder. Pas d'exécution gouvernée ni de coût.
- Crossplane : control plane K8s, desired state + drift, providers + compositions + packages. Suppose Kubernetes, pas de gate humain/coût.
- Spacelift / Scalr / env0 : orchestration IaC, policy OPA, drift. Centré run IaC, pas service complet.
- OpenCost / Kubecost : allocation, showback, chargeback. Brique coût, pas un plan de contrôle.

Différenciation (hypothèse à valider, pas une promesse) : la combinaison noyau plug-and-play + cycle de vie gouverné (approbation, pins, vérification, retrait) + coût au centre + auto-hébergé + agent-capable. On gouverne les moteurs existants (OpenTofu/Terraform, Ansible, OpenCost/Infracost, Grafana/Prometheus), on ne les réinvente pas. À mesurer sur un même scénario vs ticket+script ou un portail déjà en place ; si aucun gain, on retire le pitch.

Cible & MVP : petite équipe infra, un service jetable, une cible existante, un parcours bout-en-bout prouvé via le CLI. Le reste est gelé tant que ce parcours n'est pas prouvé.

## Rendu 2 : Design technique de A à Z
`Rendu2_Design_Technique_A_a_Z.pdf`

- Microkernel : le noyau porte identité/rôles, admission/politique, approbation, jobs sérialisés, état durable PostgreSQL, audit, registre de providers. Le reste est remplaçable. CLI, GUI et agent ne sont que des clients de la même API.
- Diagramme technique : clients (CLI kw / GUI React / Agent MCP) -> API noyau -> registre de providers -> providers (inventory, execution, secrets, notification, diagnostics, cost) -> cibles existantes (ESXi, QEMU/KVM, OCI, cloud, réseau). Colonne transverse : PostgreSQL, Vault, Git, observabilité.
- 3 surfaces d'extension : template déclaratif, plugin exécutable (provider à contrat), interface spécialisée. Contrats versionnés, pas d'accès aux tables du noyau, approbation obligatoire.
- Contrat de provider (la clé pour tout brancher) : interface fermée (inputs, résultat, erreurs, opérations read/prepare/execute/verify/rollback/decommission), provenance et pins, readiness locale, aucun secret dans les plans/logs/réponses.

### (Re)construire de A à Z
0. Noyau minimal + CLI kw comme client de référence.
1. Premier provider + premier template : MVP prouvé bout-en-bout sur une cible, avec vérification et retrait.
2. Couche coût : estimation à la demande + allocation/showback, brancher OpenCost/Infracost comme provider cost.
3. Catalogue gouverné : artefacts en metadata -> recette revue -> offre versionnée. Importer n'exécute rien.
4. Cycle de vie complet : update/retire/drift par provider, preuve de retrait, reprise sans replay.
5. Croissance : on ajoute providers et couches à la demande, le noyau ne change pas. Apprend = capitaliser procédures revues + checks.

Garde-fous immuables : modularité, une seule porte (API), approbation humaine, pins/provenance, pas de secret dans les plans, PostgreSQL requis, retrait vérifié.
Risques tranchés : étendue -> geler le hors-MVP ; apprendre sans ML -> catalogue de procédures + checks ; modulaire n'est pas usine à gaz -> contrats fermés, noyau petit.

## Références
docs.crossplane.io (What's Crossplane) · backstage.io/docs/overview/architecture-overview ·
manageiq.org/docs/get-started/concepts · docs.waldur.com (marketplace-orders) ·
cloudbolt.io/cmp/self-service · developer.morpheusdata.com/docs · opencost.io ·
scalr.com/learning-center/spacelift-alternatives · opentofu.org/docs/cli/commands/providers/lock

Design proposé, distinct des capacités livrées (voir docs/COMPLETION_STATUS.md).

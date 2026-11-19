# Cahier des charges KuberWeaver, CLI kw

Version 1.2, 19/11/2026. Statut : référence pour l'implémentation du CLI.
Périmètre de cette version : le CLI `kw` complet, prêt à brancher sur les connecteurs via l'API, et le cœur (API) avec les services réels. L'ancienne GUI est retirée ; une GUI modulable viendra ensuite (voir §1).

## 1. Objet

KuberWeaver est une plateforme modulaire auto-hébergée qui centralise la gestion d'infrastructure et le contrôle des coûts. Une procédure revue devient un service demandé, approuvé, déployé, vérifié, puis retiré, sur l'infrastructure existante.

- L'API est le produit : un seul serveur, une seule porte. Le CLI, la GUI et les agents sont des clients équivalents.
- Le CLI `kw` est le client de référence et la surface d'acceptation : s'il peut tout faire, l'API est complète.
- Le noyau (kernel) est minimal ; toute capacité ajoutée est un plugin à contrat fermé, sans modifier le cœur.
- GUI modulable (interface graphique) : la GUI est un client de l'API comme les autres ; chacun peut construire la sienne sans modifier le cœur. Une GUI sera réalisée pour les présentations. Pour l'instant, on se concentre sur le cœur, le CLI et les services réels.

## 2. Vocabulaire

Terme anglais retenu, traduction française entre parenthèses.

| Terme | Définition |
| --- | --- |
| Vault (coffre-fort de secrets) | OpenBao, ou vault interne (.env). Stocke les credentials des connecteurs et les clés SSH privées. Le noyau ne manipule que des références ; aucune valeur secrète dans les plans, logs ou réponses. |
| SSH keys (clés SSH) | Générées avant la création, stockées dans le vault, injectées par OpenTofu à la création de la ressource. Système de gestion et de révocation. |
| Resource (ressource) | Gérée par OpenTofu : machine virtuelle (VM), container (conteneur), service cloud. |
| Connector (connecteur) | Cible d'infrastructure : hyperviseur (ESXi, Proxmox, Hyper-V, KVM), cloud, routeur, switch. Porte les infos de connexion ; les secrets vont au vault. |
| Plugin (brique exécutable) | Brique qu'on branche sur le noyau pour ajouter une capacité (un provider, une cible), sans modifier le cœur. Contrat versionné, provenance et pins. |
| Service (service) | Géré par Ansible, ex. serveur web. Déployé sur une ressource. |
| Blueprint (plan de déploiement) | Ressources + services fournis ensemble. Géré par Ansible + OpenTofu. Déployé sous un même deployment ID. |
| Module (module) | Unité de catalogue : nom, description, coût par mois (champ libre 0..n, ou branché à un plugin). |
| Rollback (retour arrière) | Géré par Ansible via un système de snapshot (instantané) : revenir à un état antérieur. |
| Decommission (décommissionnement) | Suppression complète des ressources et services d'un déploiement, par deployment ID, sans toucher aux autres éléments même similaires. |
| Deployment ID (identifiant de déploiement) | Identifiant unique d'un déploiement de blueprint ; unité de décommissionnement isolée. |
| Inventory (inventaire) | Ressources et services déployés, enregistrés dans la base de données. |
| FinOps (optimisation des coûts) | Analyse des coûts à partir des données conservées ; agrégation par module, service, déploiement. |
| IAM (Identity and Access Management, gestion des identités et des accès) | IAM/annuaire de l'entreprise (OIDC/OAuth2, LDAP/Active Directory, SAML) auquel on branche les utilisateurs, avec mapping des groupes vers les rôles KuberWeaver. |

## 3. Principes immuables (gouvernance)

1. Une seule porte : l'API. Le CLI applique les mêmes routes, rôles et pins que la GUI ; aucun raccourci, aucune écriture directe en base, aucun contournement d'approbation.
2. Approbation humaine avant toute exécution. Rien ne se déploie sans accord.
3. Pins de source et de plan exact ; un changement invalide l'approbation.
4. Vérification indépendante après exécution ; retrait vérifié (ressources absentes, preuve).
5. Worker sérialisé, non-rejeu au redémarrage.
6. Secrets jamais dans les plans, logs ou réponses ; vault par références, résolution à l'exécution.
7. PostgreSQL est la base de production, obligatoire, sans fallback SQLite. SQLite n'est utilisé que pour les tests isolés et le dev local jetable, jamais en production.
8. Propriété des ressources : on ne touche qu'à ce que le déploiement possède ; décommissionner par ID sans affecter d'autres éléments.
9. Le rollback et le décommissionnement sont des mutations : approbation humaine requise ; un jeton agent ne peut ni approuver, ni rollback, ni décommissionner.

## 4. Parcours attendu (CLI)

Chaîne complète, de la clé SSH au décommissionnement. Chaque étape est une commande `kw` qui appelle l'API.

1. Générer une clé SSH, stockée dans le vault. `kw keys generate`
2. Ajouter un connecteur : infos de connexion (adresse IP, etc.) ; les secrets vont au vault. `kw connectors add`
3. Tester et découvrir le connecteur. `kw connectors test` / `kw connectors discover`
4. Sélectionner le connecteur pour la création d'une ressource.
5. Créer un module : nom, description, coût par mois (champ libre 0..n ou branché à un plugin). `kw modules create`
6. Créer la ressource, au choix :
   - Default (défaut) : formulaire fourni par nos équipes, couvrant les besoins de base d'une entreprise (carte réseau, stockage, emplacement, RAM, etc.). Les clés SSH générées avant sont injectées par OpenTofu à la création. `kw resources create --default`
   - Import (import) : code OpenTofu existant et éprouvé. La sécurité, la configuration et le paramétrage sont à l'unique charge de l'utilisateur. `kw resources create --import`
7. Ajouter ou non d'autres ressources ou services, soit indépendamment, soit chaînés à la première ressource (ex. installer un service avec Ansible sur une VM déployée avec OpenTofu). `kw resources add` / `kw services add`
8. Créer un blueprint, ou le créer à partir d'un blueprint existant en ajoutant ressources/services (édition, copie, duplication, modification du module). `kw blueprints create` / `kw blueprints copy`
9. Demande de déploiement. `kw deploy request`
10. Validation du déploiement (approbation humaine). `kw deploy approve`
11. Les ressources et services du blueprint sont déployés et inventoriés en base. `kw inventory`
12. Logs : choix de verbosité et affichage pendant l'installation pour le valideur ; conservation des logs d'installation avec rotation. `kw logs`
13. Rollback possible via snapshot. `kw rollback`
14. Décommissionner ce qui a été déployé ensemble (ressources + services) par deployment ID, sans toucher aux autres. `kw decommission`
15. Données conservées en base pour analyse avec d'autres outils, FinOps ; libre aux équipes IT d'intégrer au mieux leurs infrastructures. `kw finops`

## 5. Exigences du CLI

- Client mince : httpx + click, aucune logique métier, pas de second service. Il appelle l'API et suit les jobs.
- Auth humaine : session + CSRF (login, cookie, header X-CSRF-Token). Session persistée côté client dans un fichier 0600, réutilisée jusqu'à expiration ; `kw logout` l'efface.
- Auth automation : jeton agent scopé (rôle, scopes, expiration, révocation). Limité à lire, valider, demander, suivre ; pas d'approbation, rollback ou décommissionnement.
- Secrets via variable d'environnement ou fichier 0600, jamais en argument de commande (fuite ps).
- Sortie : table par défaut, `--json` pour l'automatisation, codes de sortie non nuls en cas d'échec, `--dry-run` pour la validation.
- Verbosité des logs configurable ; affichage pour le valideur ; rotation et conservation.
- Prêt à brancher : surface de commandes complète. Une capacité sans route API correspondante est marquée « non disponible » et le gap backend est loggué pour une tâche ultérieure (pas de stub côté client).

## 6. Sécurité

- Vault : OpenBao ou interne (.env). Références uniquement dans le noyau ; résolution à l'exécution.
- Clés SSH : génération, stockage vault, injection OpenTofu, gestion et révocation. Pas d'export de clé privée en clair hors opération explicite autorisée.
- Import OpenTofu : le noyau épingle source et plan et vérifie la provenance, mais ne valide pas le code utilisateur ; la sécurité et la configuration sont à la charge de l'utilisateur.
- Moindre privilège, cibles autorisées, aucun secret dans les logs ; sanitisation avant tout diagnostic externe.
- IAM (Identity and Access Management, gestion des identités et des accès) : brancher les utilisateurs sur l'IAM existant de l'entreprise plutôt que des comptes locaux en silo. SSO via OIDC/OAuth2 (déjà supporté par le backend, voir docs/OIDC.md), à terme LDAP/Active Directory et SAML. Mapping des groupes de l'annuaire vers les rôles KuberWeaver (admin, operator, viewer) ; provisioning et révocation centralisés par l'IAM de l'entreprise. Les comptes locaux bootstrap restent réservés au dev/labo, pas à la production.

## 7. Données et FinOps

- Base de production : PostgreSQL (obligatoire, aucun fallback SQLite). Inventaire des ressources et services en base, avec attribution par deployment ID. SQLite est réservé aux tests isolés, pas à la production.
- Coût par mois par module (champ libre ou plugin) ; agrégation FinOps par module, service, déploiement.
- Conservation des données et des logs (avec rotation) pour analyse par des outils externes ; intégration libre par les équipes IT.

## 8. Hors périmètre (cette version)

GUI modulable (une GUI de présentation sera réalisée ultérieurement ; chacun peut construire la sienne), multi-tenant, marketplace publique, paiement, cloud managé, remédiation drift automatique, ChatOps externe. Ces éléments ne sont pas implémentés dans cette version, centrée sur le cœur, le CLI et les services réels.

## 9. Critères d'acceptation

1. Le parcours de la section 4 est jouable via `kw` contre l'API, avec preuve datée à chaque étape.
2. Tests verts : unitaires et intégration in-process (vrai serveur uvicorn local sur create_app(testing=True), SQLite isolé pour les tests uniquement, vault local chiffré), sans labo d'hyperviseur. La production, elle, tourne sur PostgreSQL.
3. Gouvernance respectée : approbation humaine, pins, propriété des ressources, décommissionnement isolé par ID.
4. Aucun secret dans les plans, logs, réponses ou arguments de commande.
5. Suivi d'avancement par versionning git (branche dédiée, commits par tranche, arbre propre).

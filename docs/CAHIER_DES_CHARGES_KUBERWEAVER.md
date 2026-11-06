# KuberWeaver
## Présentation du projet et cahier des charges

Version 1.2, 6 novembre 2026  
Établissement et campus : EFREI Paris  
Destinataires : mentor, intervenants techniques et équipe projet  
Statut : proposition de cadrage à relire et à valider humainement

> Ce document distingue la vision produit, le prototype déjà réalisé et les évolutions proposées. Une fonctionnalité décrite au futur n'est pas une fonctionnalité livrée. Les priorités et jalons ci-dessous constituent une proposition, pas un engagement de calendrier approuvé.

## Sommaire

1. Synthèse pour un lecteur extérieur
2. Besoin et objectifs
3. Périmètre, utilisateurs et responsabilités
4. État réel du prototype
5. Parcours fonctionnels attendus
6. Architecture et choix techniques
7. Modèle de plugins et intégration frontend
8. Exigences fonctionnelles et critères de recette
9. Exigences non fonctionnelles
10. Données, sécurité et exploitation
11. Stratégie de tests et démonstration
12. Organisation, jalons et livrables
13. Risques et décisions à prendre
14. Méthode de cadrage, traçabilité et sources

## 1. Synthèse pour un lecteur extérieur

KuberWeaver est un projet de plateforme interne de développement et d'infrastructure, ou IDP. Son objectif est de permettre à un utilisateur de décrire un environnement dans une interface graphique, puis d'en demander le déploiement de manière contrôlée et traçable.

L'utilisateur compose un diagramme avec des briques issues d'un catalogue : serveur web, application, base de données, machine virtuelle ou configuration réseau. Chaque brique porte ses paramètres et sa logique de déploiement. La plateforme valide la demande, présente une estimation de coût, impose les droits et approbations nécessaires, puis orchestre l'exécution et ses vérifications.

Le principe structurant est une architecture modulaire : un noyau commun porte les garanties transverses ; les fonctionnalités et intégrations sont ajoutées par des plugins et des fournisseurs remplaçables. Le but n'est pas de réimplémenter un hyperviseur, un cloud ou Ansible, mais de les rendre utilisables depuis un parcours gouverné.

Un premier prototype fonctionnel existe. Il déploie réellement NGINX et Apache dans un laboratoire virtualisé, avec approbation, sauvegarde, canary, rolling, vérification et retour arrière. Il ne constitue pas encore une plateforme généraliste prête pour la production.

Le nom KuberWeaver n'implique pas l'utilisation de Kubernetes : aucun cluster Kubernetes n'est installé ou orchestré par le prototype actuel.

### Exemple concret

Un opérateur ajoute un serveur web au canvas, choisit les cibles et les ressources, puis soumet son plan. Un administrateur examine les coûts et approuve. La plateforme sauvegarde l'état initial, déploie sur une première cible, vérifie le résultat, puis poursuit sur les autres. Si une vérification échoue, elle tente de restaurer les ressources déjà modifiées et expose les preuves du résultat.

### Valeur à démontrer

- Réduire les opérations manuelles répétitives et les écarts entre procédures.
- Rendre les déploiements accessibles à travers des formulaires plutôt que des commandes libres.
- Centraliser les droits, politiques, approbations, coûts estimés et traces.
- Réduire le rayon d'impact d'une erreur grâce aux phases et à la restauration.
- Ajouter de nouvelles capacités sans multiplier les cas particuliers dans le noyau.

Il ne s'agit pas d'affirmer que toutes ces capacités sont inédites sur le marché. Une étude comparative serait nécessaire pour établir le positionnement commercial.

## 2. Besoin et objectifs

### 2.1 Problème initial

La demande initiale porte sur une interface visuelle, initialement vide, permettant de proposer de l'infrastructure en self-service : environnements front/back sur VM ou conteneurs, configuration en masse, équipements Cisco et pfSense, gouvernance, GitOps, secrets, messageries, monitoring, FinOps et diagnostic IA en lecture seule.

Sans plateforme commune, ces activités reposent sur des outils et procédures séparés. Le risque est de perdre la cohérence entre ce qui a été demandé, approuvé, exécuté et effectivement obtenu.

### 2.2 Objectifs du projet

| Objectif | Résultat recherché |
| --- | --- |
| O1, self-service encadré | Composer une demande sans écrire soi-même tout le code d'automatisation. |
| O2, gouvernance | Bloquer une demande non conforme et soumettre les changements sensibles à approbation. |
| O3, exécution vérifiable | Déployer réellement et distinguer succès, échec, restauration et revue manuelle. |
| O4, modularité | Ajouter une brique ou un fournisseur compatible sans modifier le noyau. |
| O5, traçabilité | Relier identité, version du plan, approbation, journal et preuves de résultat. |
| O6, exploitation | Comprendre les incidents, les coûts estimés et l'état des ressources. |

### 2.3 Ce que le prototype doit prouver

Le démonstrateur doit prouver un cycle complet sur des ressources réelles, pas seulement afficher une topologie ou produire des journaux plausibles. L'ajout d'une nouvelle catégorie de service doit ensuite éprouver la modularité au-delà des deux serveurs web déjà intégrés.

## 3. Périmètre, utilisateurs et responsabilités

### 3.1 Niveaux de périmètre

- **P0, socle de recette :** catalogue déclaratif, canvas, formulaires, gouvernance, rôles, approbation, jobs persistants, déploiement réel, restauration, Git local, audit et preuve de modularité. Il s'agit largement de capacités déjà présentes, à préserver et à revalider.
- **P1, extension prioritaire proposée :** une brique réellement différente des serveurs web, secrets d'exécution issus du coffre, un connecteur de notification réel, exploitabilité et documentation reproductible.
- **P2, extensions conditionnelles :** OpenTofu sur un fournisseur choisi, réseau Cisco/pfSense, ChatOps interactif, diagnostic LLM, consommation FinOps et observabilité externe.
- **Vision long terme :** plateforme hybride étendue, marketplace communautaire sécurisée, multi-tenant et éventuelles offres commerciales.

Le mentor doit arbitrer le contenu de P1 et P2 en fonction du temps, des compétences et des accès disponibles. La vision entière n'est pas assimilée au minimum académique.

### 3.2 Acteurs

| Acteur | Responsabilités |
| --- | --- |
| Lecteur | Consulter les ressources et les exécutions autorisées, sans mutation. |
| Opérateur | Composer, configurer, enregistrer et soumettre une demande. |
| Administrateur | Approuver ou refuser, gérer les politiques et fournisseurs, autoriser les extensions et traiter les incidents. |
| Auteur de plugin | Livrer un paquet documenté avec schéma, exécution, vérification et restauration adaptées. |
| Exploitant du laboratoire | Fournir les cibles, accès, sauvegardes et conditions de sécurité du terrain de test. |
| Mentor ou expert | Challenger le périmètre, les choix, les risques et les preuves, sans être remplacé par l'IA. |
| Équipe projet | Développer, tester, documenter et faire valider les livrables. |

La séparation opérateur/approbateur existe par rôles. Une séparation stricte interdisant à une même personne d'approuver sa propre demande reste une décision de gouvernance à préciser et à tester.

### 3.3 Non-objectifs du socle

- Construire un hyperviseur, un cloud ou un moteur d'automatisation maison.
- Garantir une absence totale de panne ou un rollback universel.
- Exécuter du code tiers non fiable directement dans le processus serveur.
- Donner à l'IA un droit autonome de changement sur l'infrastructure.
- Réaliser tous les fournisseurs cloud et équipements réseau en une itération.
- Livrer un système de paiement, une facturation commerciale ou un SaaS multi-tenant dans P0.
- Exposer le prototype actuel sur Internet sans durcissement préalable.

## 4. État réel du prototype

Les états ci-dessous proviennent du README, des contrats et des rapports locaux de vérification. Les acceptations historiques ne sont pas présentées comme une nouvelle recette complète exécutée lors de la rédaction de ce document.

| Capacité | État constaté ou documenté | Limite |
| --- | --- | --- |
| Portail React/TypeScript et canvas React Flow | Réalisé | Interface standard, pas de SDK de pages frontend tierces documenté. |
| Catalogue et formulaires issus des manifestes | Réalisé | Sous-ensemble de champs pris en charge, pas toute la richesse possible de JSON Schema. |
| Enregistrement et rechargement des blueprints | Réalisé | État local au prototype. |
| Sessions, rôles, CSRF, admission et quotas | Réalisé | Comptes locaux ; pas de SSO ni de permissions par objet. |
| Approbation et refus | Réalisé | Depuis le portail, pas depuis une messagerie. |
| NGINX et Apache | Déploiements réels documentés | Conteneurs web statiques sur deux invités du laboratoire. |
| Canary, rolling et rollback | Exercés sur les services web | Ne démontre pas une restauration de base de données ou d'équipement réseau. |
| GitOps | Commits locaux des plans approuvés | Pas de push distant ni de réconciliation continue depuis Git. |
| Inventaire GNS3 | Lecture réelle | Ne provisionne pas de nouvelles VM. |
| FinOps | Estimations internes | Ni factures cloud ni consommation réellement mesurée. |
| OpenBao | Source de secrets KV v2 et persistance testées | Clés Ansible non injectées depuis le coffre ; bootstrap et unseal manuels. |
| Extensions Python | Installation, activation et sélection testées | Code de confiance avec les privilèges du serveur, pas de sandbox. |
| Notifications | Outbox locale | Aucun envoi externe configuré dans le prototype documenté. |
| Diagnostic | Règles déterministes | Pas d'inférence LLM configurée. |
| Santé et métriques | Endpoints locaux | Pas de stack Prometheus/Grafana/Loki intégrée et acceptée. |
| Cloud, OpenTofu, Cisco, pfSense, stack front/back | Prévus, non implémentés | Les entrées désactivées ne sont pas des services livrés. |
| Marketplace, signatures, paiements | Non implémentés | Les contrôles locaux ne valent pas certification de sécurité. |

### 4.1 Preuves disponibles

Le rapport `VERIFICATION.md` consigne 159 tests backend et 35 tests frontend passants, un build TypeScript/Vite réussi et des acceptations navigateur/API. Il décrit également un rollback réel de deux cibles et le chargement/déchargement d'une extension sans modification des fichiers Python du noyau.

Ces nombres sont un résultat de vérification antérieure, pas une mesure actualisée à chaque lecture de ce cahier des charges. Les rapports bruts restent dans le répertoire privé d'exécution ; leur partage nécessite une revue des secrets.

## 5. Parcours fonctionnels attendus

### 5.1 Composer et soumettre

1. L'opérateur s'authentifie et ouvre un canvas vide ou un blueprint existant.
2. Il ajoute des briques du catalogue et complète les formulaires.
3. Les liens expriment des dépendances explicites.
4. Le serveur valide le schéma, les droits, les cibles, les quotas et les règles.
5. Le portail affiche les erreurs ou le plan avec son estimation de coût.
6. L'opérateur enregistre et soumet sa demande.

### 5.2 Approuver et exécuter

1. L'administrateur consulte le plan exact, les cibles, la provenance des plugins et le coût estimé.
2. Il approuve ou refuse, avec une trace de sa décision.
3. Le système fige la version du plan et des sources exécutables approuvées.
4. Le job effectue les contrôles préalables puis sauvegarde l'état réel.
5. Il applique le changement au canary et vérifie le service.
6. Il poursuit par lots si les vérifications réussissent.
7. Il persiste les événements et le résultat vérifiable.

Une modification du plan ou des sources exécutables après approbation doit invalider l'autorisation correspondante ou bloquer l'exécution, jamais changer silencieusement ce qui sera exécuté.

### 5.3 Gérer un échec

La plateforme arrête la progression et restaure les ressources déjà modifiées lorsque le contrat du plugin le permet. Le résultat distingue l'échec initial et l'issue de la restauration. Si celle-ci échoue, le job reste explicitement en échec de rollback et requiert une intervention humaine.

Un service inaccessible n'est pas automatiquement restaurable par le même chemin d'administration. Les futurs plugins réseau doivent prévoir un accès hors bande ou un mécanisme adapté avant toute promesse de rollback.

### 5.4 Étendre la plateforme

Un auteur livre un plugin avec sa documentation et ses tests. L'administrateur le fait contrôler et l'installe. Un blueprint compatible est découvert par le catalogue ; une extension exécutable reste désactivée tant que sa confiance et son activation n'ont pas été autorisées. La désactivation d'une capacité optionnelle ne doit pas rendre tout le portail inutilisable.

## 6. Architecture et choix techniques

### 6.1 Vue logique

```text
Navigateur
  Catalogue, canvas, formulaires, approbations, suivi
                           |
                           v
Noyau de contrôle
  Identité, validation, politiques, approbation, état, audit, jobs
                           |
             Registres et contrats de fournisseurs
       /          |          |          |          \
 Inventaire   Exécution   Secrets   Notifications  Diagnostic
                  |
       Automatisation et systèmes cibles
       Ansible, conteneurs et invités du laboratoire
       Puis fournisseurs optionnels : OpenTofu, cloud, réseau
```

Le noyau porte les invariants de sécurité et le cycle de vie. Les plugins ne doivent pas pouvoir supprimer les contrôles d'identité, d'approbation, de validation ou de sérialisation.

### 6.2 Stack actuelle et options futures

| Couche | Implémentation actuelle | Évolution possible, non imposée |
| --- | --- | --- |
| Frontend | React, TypeScript, Vite, React Flow | Composants spécialisés si un besoin réel le justifie. |
| API | FastAPI, modèles et règles Python | Contrats publics versionnés. |
| État | SQLite et fichiers privés | PostgreSQL si concurrence et exploitation le nécessitent. |
| Jobs | File persistante et worker applicatif sérialisé | File distribuée seulement si le besoin est établi. |
| Exécution | Ansible et lifecycle de conteneurs | Fournisseur OpenTofu ou exécuteur spécialisé. |
| Secrets | Fournisseurs configurables, dont OpenBao | Injection des identifiants d'exécution et rotation. |
| Versionnement | Git local | Dépôt distant et parcours PR si retenus. |
| Supervision | systemd utilisateur, santé et métriques locales | Collecte externe de métriques et logs. |

Les documents de vision évoquaient Next.js, PostgreSQL, Celery/Redis ou Temporal, OPA et différents outils d'observabilité. Ce sont des pistes initiales, pas la description de la stack livrée ni des dépendances obligatoires.

### 6.3 Séparation de l'intention et de l'observation

Le blueprint décrit ce que l'utilisateur souhaite. L'inventaire et les probes décrivent ce qui existe ou répond effectivement. Un nœud posé sur le canvas ne prouve ni sa création ni sa disponibilité.

Les liens actuels ordonnent les déploiements. Ils ne créent ni câblage, ni règle firewall, ni découverte de service, ni configuration automatique des URL front/back. Ces effets doivent être spécifiés explicitement dans de futurs contrats.

## 7. Modèle de plugins et intégration frontend

### 7.1 Deux mécanismes complémentaires

**Blueprint déclaratif :** service utilisant un exécuteur déjà disponible. Il contient un manifeste, un schéma de configuration, des valeurs par défaut et des assets de cycle de vie.

```text
plugins/mon-service/
  manifest.yaml
  iac/snapshot.yml
  iac/playbook.yml
  iac/verify.yml
  iac/rollback.yml
```

**Extension Python :** capacité nouvelle ou fournisseur alternatif d'inventaire, d'exécution, de secrets, de notifications ou de diagnostic. Le paquet contient `plugin.yaml` et `module.py`, et utilise le protocole d'extension versionné.

Ajouter un blueprint et installer une extension ne sont pas la même opération.

### 7.2 Ce que le frontend fait automatiquement aujourd'hui

Pour un blueprint valide, le backend découvre les manifestes sur disque. Après rechargement du catalogue dans le navigateur, le portail affiche les métadonnées de la brique et utilise son schéma pour construire le formulaire standard. Une brique compatible ne nécessite donc pas de modifier React ou de reconstruire le frontend.

Cela ne signifie pas que n'importe quel schéma complexe ou n'importe quel pictogramme sera interprété sans développement. Le contrat de champs et de représentation supportés doit être documenté.

L'activation d'un fournisseur Python ne crée pas automatiquement une page frontend. Une console interactive, un éditeur de règles réseau, une table métier avancée ou une vue propre à une base de données demandent une interface spécifique. Le chargement de composants frontend livrés par un plugin n'est pas une capacité actuellement documentée.

### 7.3 Contrat attendu pour une nouvelle brique

- Identifiant, version, description, compatibilité et provenance.
- Schéma de configuration avec valeurs par défaut et contraintes.
- Cibles autorisées et droits nécessaires.
- Plan et estimation compréhensibles avant mutation.
- Sauvegarde de l'état réel, y compris l'absence initiale.
- Application reproductible et vérification spécifique au service.
- Restauration documentée, limites et procédure de reprise manuelle.
- Tests de succès, d'échec et de restauration.
- Aucun secret intégré dans le paquet ou les journaux.

Un service persistant ne doit pas réutiliser aveuglément le rollback d'un serveur web statique. Une restauration de conteneur ne prouve pas la cohérence transactionnelle de ses données.

### 7.4 Condition de réussite de la modularité

Ajouter une brique d'une autre catégorie et démontrer son cycle de vie sans branche conditionnelle métier dans le noyau. Le temps d'intégration, les fichiers modifiés et les limites du contrat seront relevés. Aucun délai universel d'ajout de plugin n'est garanti à ce stade.

## 8. Exigences fonctionnelles et critères de recette

Les priorités ci-dessous sont proposées. Chaque ligne associe une exigence à un critère observable. Les états actuels sont détaillés en section 4 ; la présence d'une ligne ne signifie pas que sa recette est déjà passée.

| ID | Priorité | Exigence | Critère de recette |
| --- | --- | --- | --- |
| EXG-001 | P0 | Authentifier et appliquer les rôles côté serveur. | CRT-001 : un lecteur ne peut soumettre ni approuver ; un opérateur ne peut administrer ; les refus sont vérifiés par appels API directs. |
| EXG-002 | P0 | Découvrir les blueprints déclaratifs sans éditer le noyau. | CRT-002 : un nouveau manifeste valide apparaît après rechargement ; un manifeste invalide est signalé sans masquer les autres. |
| EXG-003 | P0 | Composer et sauvegarder un graphe sur le canvas. | CRT-003 : enregistrer puis recharger conserve nœuds, positions, paramètres et dépendances ; cycles interdits. |
| EXG-004 | P0 | Générer les formulaires à partir du schéma supporté. | CRT-004 : valeurs par défaut, listes de choix et bornes numériques sont rendues ; les données invalides sont aussi refusées par le serveur. |
| EXG-005 | P0 | Appliquer les politiques et quotas avant déploiement. | CRT-005 : une cible inconnue, une commande arbitraire, un dépassement et une configuration interdite sont refusés avant mutation. |
| EXG-006 | P0 | Soumettre un plan exact à approbation ou refus. | CRT-006 : décision, acteur et date sont persistés ; un changement du plan ou des sources approuvées bloque une exécution devenue non conforme. |
| EXG-007 | P0 | Versionner et auditer les demandes exécutées. | CRT-007 : chaque exécution approuvée retrouve son plan expurgé, son vrai commit Git et ses événements, sans secret. |
| EXG-008 | P0 | Exécuter des jobs persistants et sérialisés. | CRT-008 : un redémarrage conserve l'historique ; une opération interrompue reste en revue, sans rejeu silencieux. |
| EXG-009 | P0 | Déployer progressivement sur des ressources réelles. | CRT-009 : les traces et lectures HTTP indépendantes prouvent sauvegarde, canary, vérification et rolling sur deux cibles. |
| EXG-010 | P0 | Restaurer après échec et proposer un rollback manuel éligible. | CRT-010 : un échec rolling restaure aussi le canary ; l'absence initiale est restaurée ; un échec de restauration n'est jamais déclaré réussi. |
| EXG-011 | P0 | Distinguer inventaire observé et intention graphique. | CRT-011 : l'inventaire vient d'une lecture réelle ; une indisponibilité est affichée et non remplacée par des données fabriquées. |
| EXG-012 | P0 | Présenter une estimation FinOps avant approbation. | CRT-012 : total, détail par ressource, devise et nature estimative sont visibles et cohérents avec le calcul serveur. |
| EXG-013 | P0 | Installer et activer explicitement les extensions. | CRT-013 : une extension désactivée n'est pas importée ; une extension défaillante ne laisse pas d'enregistrement partiel et ne casse pas le noyau. |
| EXG-014 | P1 | Ajouter une catégorie de service différente des serveurs web. | CRT-014 : le service choisi passe déploiement, vérification et restauration sans modifier le noyau ; les limites de persistance sont documentées. |
| EXG-015 | P1 | Obtenir les secrets d'exécution depuis un coffre. | CRT-015 : un déploiement réel utilise un secret à portée limitée issu du coffre ; révocation/expiration bloquent proprement ; aucune fuite dans Git ou les logs. |
| EXG-016 | P1 | Envoyer une notification par un connecteur réel. | CRT-016 : un événement de job produit un message effectivement reçu, avec gestion d'échec et protection contre les doublons. |
| EXG-017 | P1 | Rendre l'exploitation du portail observable. | CRT-017 : santé, état des jobs et erreurs sont consultables ; une panne contrôlée est visible ; la rétention est documentée. |
| EXG-018 | P2 | Provisionner une ressource avec OpenTofu sur un fournisseur choisi. | CRT-018 : plan, création, lecture indépendante et destruction contrôlée sont prouvés ; état et verrouillage sont protégés. |
| EXG-019 | P2 | Configurer un équipement réseau pilote. | CRT-019 : une modification autorisée puis sa restauration sont vérifiées depuis un accès de secours préservé sur une cible de lab autorisée. |
| EXG-020 | P2 | Autoriser une approbation ChatOps sécurisée. | CRT-020 : un acteur authentifié approuve le plan exact ; un clic rejoué, expiré ou non autorisé est refusé et audité. |
| EXG-021 | P2 | Fournir un diagnostic LLM strictement en lecture seule. | CRT-021 : l'agent cite ses événements sources, exprime l'incertitude et ne possède aucun outil de mutation ; toute correction passe par une nouvelle demande approuvée. |
| EXG-022 | P2 | Étendre GitOps à un dépôt distant contrôlé. | CRT-022 : un dépôt de test reçoit le plan expurgé et permet de relier demande, commit et résultat ; échec Git et divergence sont gérés explicitement. |
| EXG-023 | P2 | Exploiter des mesures de consommation et des recommandations FinOps. | CRT-023 : chaque valeur mesurée indique source, période et fraîcheur ; les estimations restent distinctes des factures. |
| EXG-024 | Vision | Distribuer des plugins tiers avec une chaîne de confiance. | CRT-024 : intégrité, provenance, versions, scans, revue, révocation et isolation sont définis et testés avant l'exécution de code non fiable. |

## 9. Exigences non fonctionnelles

### 9.1 Sécurité et fiabilité

- Refus par défaut des cibles, actions et capacités non explicitement autorisées.
- Validation serveur systématique, indépendante du formulaire.
- Pas d'exécution de commandes arbitraires fournies par l'utilisateur.
- Contrôle de propriété des ressources pour ne pas adopter ou supprimer un service tiers.
- Verrouillage des opérations concurrentes sur le même état.
- Désactivation d'un fournisseur sans bascule silencieuse vers un autre.
- États d'erreur explicites : restauration partielle, secrets indisponibles, source modifiée, cible inaccessible.
- Test de sauvegarde/restauration de l'état du portail avant toute exploitation durable.

### 9.2 Ergonomie et accessibilité

Le parcours principal doit rester compréhensible pour un opérateur non auteur des playbooks. Les erreurs doivent nommer le problème et l'action possible. Les formulaires doivent avoir des libellés, les actions sensibles être identifiables et les états ne pas dépendre uniquement d'une couleur.

Le prototype a fait l'objet de vérifications à largeur mobile ; cela ne constitue pas un audit d'accessibilité complet. La navigation clavier et les alternatives aux manipulations du canvas restent à recetter.

### 9.3 Performance et capacité

Aucun SLA de production n'est établi. La recette devra relever les temps de chargement du catalogue, de soumission et d'affichage des événements, ainsi que la durée des jobs sur le lab de référence. Les seuils acceptables, le nombre d'utilisateurs et le nombre de ressources visés doivent être approuvés avant un test de charge.

Une durée de rollback ne peut pas être garantie indépendamment du plugin, de l'état des données et de l'accès à la cible. La promesse initiale d'un retour réseau en quelques secondes n'est donc pas reprise comme engagement.

### 9.4 Maintenabilité et réversibilité

Contrats versionnés, schémas documentés, dépendances explicites, tests automatiques et procédures reproductibles sont requis. Les plans doivent être exportables dans un format documenté. Une migration d'état ou une mise à jour incompatible doit prévoir sauvegarde et procédure de retour.

### 9.5 Coûts, licences et confidentialité

Privilégier les composants utilisables sans abonnement obligatoire. Ne pas confondre logiciel libre et absence de coûts d'hébergement, d'API, de modèle ou de messagerie. Les licences des outils, images et systèmes réseau doivent être vérifiées avant distribution. La licence propre du projet reste à confirmer.

Les données d'infrastructure et les journaux ne doivent pas être envoyés à un service IA externe sans autorisation et revue préalable de leur contenu.

## 10. Données, sécurité et exploitation

### 10.1 Objets métier

Le système manipule des utilisateurs et rôles, blueprints, nœuds et dépendances, demandes, approbations, versions et empreintes, jobs, événements, snapshots, cibles observées, estimations, fournisseurs configurés, références de secrets et notifications.

Chaque opération doit permettre de retrouver le lien entre ces objets. Un secret lui-même n'est pas une métadonnée de catalogue ou une valeur à publier dans un plan.

### 10.2 Zones de confiance

1. Navigateur et utilisateur, entrées non fiables.
2. Noyau du portail, responsable de l'autorisation.
3. Extensions exécutables, aujourd'hui du code administrateur de confiance.
4. Outils d'automatisation et accès aux cibles.
5. Coffre et opérateur chargé du bootstrap.
6. Éventuels services externes de notification ou d'IA.

Les scopes de chaque compte technique doivent être limités à ses fonctions. Les secrets d'administration et de récupération du coffre ne doivent pas être confiés aux plugins ni livrés avec le code.

### 10.3 Contraintes actuelles du laboratoire

Le contrôle tourne sur le poste local et les déploiements sur des invités GNS3. Le portail écoute sur le loopback. L'accès aux services déployés utilise un réseau de management isolé, pas une publication automatique sur le LAN ou Internet.

Le champ de disque actuel est une déclaration budgétaire, pas un quota disque appliqué. Le CIDR actuel est un garde-fou de demande, pas une règle firewall installée. La topologie ne doit pas faire croire le contraire.

L'API GNS3 existante n'est pas sécurisée par la simple présence du portail. Les accès administrateur au poste et aux invités restent dans le périmètre de confiance du lab.

### 10.4 Éléments nécessaires avant une exposition de production

HTTPS, gestion d'identité adaptée, permissions par ressource, protection et rotation des secrets, séparation des privilèges, isolation d'exécution, sauvegardes restaurables, politique de journaux, durcissement des dépendances et revue du modèle de menace. SSO, multi-tenant et haute disponibilité ne sont pas acquis.

## 11. Stratégie de tests et démonstration

### 11.1 Niveaux de tests

- Unitaires : schémas, règles, calculs, transitions d'état et contrats.
- Intégration : API, stockage, registre, sélection réelle des fournisseurs et gestion des secrets.
- Frontend : rendu des formulaires, droits, erreurs et navigation.
- Bout en bout : navigateur réel avec backend réel.
- Infrastructure : lectures indépendantes des services et de leur état après mutation.
- Résilience : échec du canary, échec rolling, restauration partielle et redémarrage du contrôle.
- Sécurité : accès hors rôle, rejeu, données invalides, dérive après approbation et ressources étrangères.

Un test avec frontière Ansible simulée reste utile, mais ne vaut pas preuve d'une panne réellement provoquée sur le lab.

### 11.2 Scénario de démonstration minimal

1. Montrer un canvas et le catalogue réellement disponible.
2. Ajouter un serveur web et provoquer une validation refusée.
3. Corriger la demande et montrer le coût estimé avant approbation.
4. Approuver et suivre un vrai déploiement sur deux cibles.
5. Vérifier séparément les réponses des deux services.
6. Mettre à jour avec une panne contrôlée, puis prouver le retour à l'état précédent.
7. Retrouver plan, décision, commit et événements.
8. Montrer l'ajout ou l'activation d'une capacité sans modification du noyau.

Les futures fonctions ne sont montrées comme fonctionnelles que si elles passent leur propre recette. Une indisponibilité explicitée vaut mieux qu'une réussite simulée.

### 11.3 Protection du terrain de test

Toutes les mutations doivent rester dans les ressources du projet et les cibles autorisées. Relever l'état initial, utiliser des ports libres, préserver l'application préexistante, restaurer les ressources temporaires et ne pas toucher aux équipements physiques ou à la production pour la démonstration.

## 12. Organisation, jalons et livrables

### 12.1 Présentation de l'équipe et répartition des responsabilités

L'équipe du projet KuberWeaver, sur le campus EFREI Paris, est composée des six personnes ci-dessous. Les rôles principaux ont été communiqués par le porteur du projet. Leur déclinaison en sous-lots, livrables et revues constitue l'organisation opérationnelle proposée dans ce cahier des charges ; elle reste ajustable collectivement sans changer les rôles de référence.

**Sécurité à chaque étape du processus !** La sécurité est une responsabilité partagée, intégrée à chaque lot, et non une tâche confiée à une seule personne en fin de développement.

| Personne | Rôle de référence | Périmètre principal dans le projet |
| --- | --- | --- |
| Etienne MURZEAU | Référent / Déposant, Intégration AI / Infrastructure | Coordination technique, cohérence d'ensemble, intégration IA en lecture seule, intégrations transverses et préparation de la démonstration. |
| Rayan CHOUCHANE | Infrastructure Terraform / Ansible + GitOPS, Pipeline : Lint + Conteneur | Chaîne GitOps et CI/CD, contrôles automatisés, conteneurisation des outils d'exécution et intégration des automatisations. |
| Alex YAO | Infrastructure Terraform / Ansible | Provisionnement, inventaire et préparation des cibles ; développement du futur fournisseur IaC si retenu. |
| Vidjay VELAYOUDAM | FinOps / Business | Modèle de coût, indicateurs, besoins métier, proposition de valeur et cadrage économique. |
| Abdel-Waheb SAKKAL | Infrastructure Terraform / Ansible | Configuration des services et équipements, plugins d'automatisation, déploiement progressif, vérification et restauration. |
| Priyank SOLANKI | Développeur web, Full Stack | Frontend et backend du portail, contrats API, parcours utilisateur, droits applicatifs et intégration des capacités dans l'interface. |

L'enveloppe d'environ 510 heures évoquée dans la vision initiale reste une hypothèse de cadrage. Elle ne constitue ni un relevé des heures effectuées, ni une allocation confirmée par personne, ni une estimation du travail restant. Les charges et disponibilités seront précisées après validation du périmètre.

#### 12.1.1 Etienne MURZEAU, Référent / Déposant, Intégration AI / Infrastructure

**Grandes lignes :**

- Porter le besoin, centraliser les échanges avec le mentor et préparer les arbitrages de périmètre avec l'équipe.
- Maintenir la cohérence entre architecture, contrats de plugins, contraintes du laboratoire et scénarios de démonstration.
- Coordonner l'intégration des contributions et les dépendances entre portail, automatisation, coûts et exploitation.
- Définir le fournisseur de diagnostic et, si cet axe est retenu, intégrer un modèle IA sur des données d'infrastructure autorisées en lecture seule.
- Encadrer les propositions de correction : sources identifiables, limites explicites et nouvelle demande humaine avant toute mutation.
- Coordonner l'intégration des secrets, de l'observabilité et des connecteurs externes avec les responsables de chaque couche.
- Préparer avec l'équipe les revues, la démonstration finale et la présentation des résultats et limites.

**Livrables :** architecture d'ensemble, registre des décisions, contrat de diagnostic, démonstration du diagnostic retenu et dossier de synthèse technique.

**Sécurité du lot :** modèle de menace transverse, revue des privilèges, autorisation des flux vers une IA externe, expurgation des données et absence d'outils de mutation accessibles au diagnostic IA.

**Collaborations :** Priyank pour l'API et le rendu du diagnostic, les trois responsables infrastructure pour les sources et preuves, Vidjay pour les recommandations FinOps. Le rôle de référent ne remplace pas les responsabilités de réalisation et de revue des autres membres.

#### 12.1.2 Rayan CHOUCHANE, Infrastructure Terraform / Ansible + GitOPS

**Grandes lignes :**

- Concevoir et maintenir la chaîne GitOps reliant les sources, les plans approuvés, leurs versions et les résultats d'exécution.
- Mettre en place le pipeline de contrôles : validation des manifestes, lint du code et de l'IaC, tests et construction des images de conteneur retenues.
- Définir des images d'exécution reproductibles avec des dépendances identifiées ; l'exécution conteneurisée est un livrable à réaliser, pas une capacité supposée déjà présente.
- Intégrer les playbooks et modules des autres responsables infrastructure dans cette chaîne, sans dupliquer leur logique métier dans le pipeline.
- Prévoir le traitement des échecs de pipeline, la conservation des artefacts utiles et la traçabilité de la version réellement exécutée.
- Préparer l'intégration à un dépôt distant si EXG-022 est retenue, tout en conservant la distinction entre Git local et véritable livraison distante.
- Fournir les procédures de contribution, de revue et de livraison des automatisations.

**Livrables :** définition du pipeline, règles de lint, images et recettes de construction, conventions GitOps, rapports de contrôles et procédure de livraison.

**Sécurité du lot :** permissions minimales des runners, absence de secrets dans les images et artefacts, analyse des dépendances et images selon les outils retenus, protection des branches et approbation avant déploiement sensible. Un lint ou un scan réussi ne vaut pas certification d'un plugin ni sandbox d'exécution.

**Collaborations :** Alex et Abdel-Waheb fournissent les automatismes et tests d'infrastructure ; Priyank fournit les interfaces nécessaires avec les jobs du portail ; Etienne arbitre avec l'équipe la cohérence des versions et du parcours d'approbation.

#### 12.1.3 Alex YAO, Infrastructure Terraform / Ansible

**Grandes lignes :**

- Formaliser les prérequis d'infrastructure et l'inventaire des cibles autorisées.
- Préparer les environnements d'exécution et les ressources nécessaires aux scénarios retenus, sans altérer les ressources étrangères au projet.
- Concevoir les modules de provisionnement déclaratif et leurs variables ; implémenter un fournisseur Terraform/OpenTofu uniquement si le jalon correspondant est validé.
- Gérer le cycle de vie des ressources : planification, création, lecture de l'état obtenu et suppression contrôlée des ressources possédées par le projet.
- Définir le stockage et le verrouillage de l'état IaC, ainsi que les sorties consommables par les playbooks de configuration.
- Fournir un inventaire exploitable par Ansible et par la couche d'inventaire du portail, en distinguant désiré et observé.
- Documenter les limites du fournisseur, la reproductibilité et les procédures de nettoyage du laboratoire.

**Livrables :** inventaire, prérequis du lab, modules IaC du fournisseur choisi, contrat des sorties, procédures de création/nettoyage et preuves de lecture indépendante des ressources.

**Sécurité du lot :** accès techniques limités, protection des fichiers d'état potentiellement sensibles, réseaux de management isolés, contrôle de propriété et absence de destruction non autorisée.

**Collaborations :** Abdel-Waheb consomme les cibles et sorties pour configurer les services ; Rayan intègre les contrôles et le versionnement ; Priyank expose l'inventaire et le plan via les contrats applicatifs ; Etienne coordonne les dépendances de lab.

#### 12.1.4 Vidjay VELAYOUDAM, FinOps / Business

**Grandes lignes :**

- Formaliser les utilisateurs visés, leurs besoins prioritaires et les cas d'usage qui justifient la plateforme.
- Structurer la proposition de valeur et une comparaison documentée avec les solutions pertinentes, sans prétendre à une unicité non démontrée.
- Définir la grille tarifaire interne : unités, devise, hypothèses, composantes et règles de calcul des estimations.
- Spécifier les indicateurs et tableaux de bord : coût par ressource, par blueprint ou environnement et, si les données existent, évolution et consommation.
- Définir les règles de budget ou seuils d'alerte à faire appliquer par le serveur, avec Priyank.
- Distinguer estimation, mesure et facturation ; préciser la source et la fraîcheur de chaque indicateur.
- Étudier la viabilité et les coûts d'exploitation : hébergement, maintenance, API et services externes ; cadrer les hypothèses gratuites/payantes de la vision marketplace sans imposer un système de paiement au MVP.

**Livrables :** cadrage métier, modèle de coûts documenté, jeux de calcul de référence, spécification des dashboards et synthèse économique avec hypothèses et limites.

**Sécurité du lot :** confidentialité des données économiques, visibilité des coûts selon les droits et minimisation des données transmises aux services externes.

**Collaborations :** Priyank implémente le calcul serveur et les vues selon la spécification ; Alex et Abdel-Waheb définissent les ressources réellement allouées ; Etienne relie les recommandations au diagnostic et au périmètre général.

#### 12.1.5 Abdel-Waheb SAKKAL, Infrastructure Terraform / Ansible

**Grandes lignes :**

- Concevoir et maintenir les rôles et playbooks de configuration des services retenus.
- Livrer les blueprints de catalogue avec manifeste, paramètres, valeurs par défaut et assets d'exécution.
- Étendre le catalogue à une nouvelle catégorie réellement différente, choisie avec l'équipe, pour tester les limites du contrat actuel.
- Implémenter les contrôles préalables, sauvegardes, canary, déploiements par lots et vérifications propres à chaque service.
- Définir et tester la restauration automatique et manuelle, y compris l'absence initiale, les échecs partiels et les limites des services persistants.
- Préparer les configurations Cisco/pfSense uniquement si cet axe est retenu, sur des équipements de laboratoire autorisés avec accès de secours.
- Documenter les prérequis, effets de bord et procédures de reprise des plugins ; ne pas confondre ordre de déploiement et configuration du réseau applicatif.

**Livrables :** plugins et playbooks documentés, contrats de vérification/restauration, tests de lifecycle et preuves de fonctionnement et de retour arrière.

**Sécurité du lot :** configurations durcies selon le service, ports et cibles autorisés, contrôle des changements, sauvegardes protégées et vérification qu'un rollback ne détériore pas les données ou les ressources tierces.

**Collaborations :** Alex fournit les ressources et l'inventaire ; Rayan industrialise les contrôles et l'exécution ; Priyank relie les paramètres et événements au portail ; Etienne coordonne les secrets et les limites d'intégration.

#### 12.1.6 Priyank SOLANKI, Développeur web Full Stack

**Grandes lignes :**

- Développer et maintenir le portail frontend et le backend applicatif, sans transférer dans le noyau la logique métier des playbooks.
- Prendre en charge le canvas, le catalogue, les formulaires générés, les blueprints et les vues de demandes et déploiements.
- Implémenter les contrats API, la persistance et les transitions des demandes, approbations, jobs et événements, en coordination avec la chaîne d'exécution.
- Porter l'authentification, les sessions, les contrôles de rôles, les protections CSRF et les validations côté serveur.
- Intégrer les vues de coûts spécifiées par Vidjay, l'inventaire fourni par Alex et les résultats d'automatisation fournis par Rayan et Abdel-Waheb.
- Fournir les interfaces de sélection et configuration des fournisseurs et le rendu des diagnostics coordonnés par Etienne.
- Assurer les tests frontend/API, l'accessibilité du parcours, les erreurs compréhensibles et la documentation des interfaces.

**Livrables :** portail et API, contrats documentés, composants de formulaires, parcours d'approbation/suivi, tableaux de bord intégrés et tests applicatifs.

**Sécurité du lot :** autorisation côté serveur sur toutes les mutations, validation des entrées, prévention des injections et fuites dans les réponses, gestion des sessions, masquage des secrets et protection des actions sensibles. Une protection visuelle dans l'interface ne remplace jamais un contrôle API.

**Collaborations :** chaque responsable de domaine définit ses données et critères ; Priyank les intègre au produit. Il n'est pas seul responsable des playbooks, du modèle économique, du fournisseur IA ou de toutes les recettes d'infrastructure.

#### 12.1.7 Frontières entre les trois contributions infrastructure

Les trois rôles infrastructure partagent Terraform/Ansible, mais ont des dominantes complémentaires pour éviter le chevauchement :

| Sujet | Pilote opérationnel proposé | Interface avec les autres lots |
| --- | --- | --- |
| Ressources, provisionnement et inventaire | Alex YAO | Fournit les cibles et sorties documentées à la configuration et au portail. |
| Configuration des services et rollback | Abdel-Waheb SAKKAL | Consomme l'inventaire ; produit des plugins et preuves de lifecycle. |
| GitOps, lint, pipeline et conteneurs d'exécution | Rayan CHOUCHANE | Contrôle, versionne et assemble les livrables, sans recoder le provisionnement ou la configuration. |

Le terme Terraform dans les rôles décrit le domaine de compétence IaC. Le prototype actuel utilise Ansible ; le choix Terraform/OpenTofu pour une extension doit être arbitré selon les licences, les coûts, le fournisseur et les contraintes du projet. Aucun changement de moteur ni provisionnement supplémentaire n'est déclenché par cette répartition documentaire.

#### 12.1.8 Couverture des exigences et revue croisée

Le pilote porte la coordination et la preuve de recette de son lot ; les contributeurs réalisent leur partie et le relecteur apporte une vérification distincte. Cette organisation ne délègue pas l'approbation pédagogique ou la mise en production à une seule personne.

| Exigences | Pilote proposé | Contributions et revue croisée |
| --- | --- | --- |
| EXG-001 à EXG-006, EXG-008, EXG-013 : portail, droits, demandes, jobs et registre | Priyank SOLANKI | Etienne revoit les invariants ; Abdel-Waheb vérifie la compatibilité des schémas et paramètres ; Rayan vérifie le lien aux sources exécutées. |
| EXG-007, EXG-022 : GitOps et traçabilité distante | Rayan CHOUCHANE | Priyank relie jobs, décisions et commits ; Etienne revoit la fidélité au plan approuvé. |
| EXG-009, EXG-010, EXG-014, EXG-019 : lifecycle, nouvelle brique et réseau conditionnel | Abdel-Waheb SAKKAL | Alex fournit les cibles et vérifie indépendamment l'état obtenu ; Rayan contrôle les assets ; Etienne revoit les limites de restauration. |
| EXG-011, EXG-018 : inventaire et provisionnement conditionnel | Alex YAO | Priyank intègre les API ; Abdel-Waheb éprouve l'inventaire ; Rayan contrôle le pipeline et la protection de l'état. |
| EXG-012, EXG-023 : estimation, indicateurs et FinOps | Vidjay VELAYOUDAM | Priyank implémente et teste le calcul serveur ; Alex et Abdel-Waheb confirment les ressources ; Etienne revoit les recommandations. |
| EXG-015 : secrets d'exécution | Etienne MURZEAU | Rayan et Abdel-Waheb intègrent l'injection dans l'exécution ; Alex précise les comptes cibles ; Priyank protège API et interfaces. |
| EXG-016, EXG-017, EXG-020, EXG-021 : intégrations, observabilité, ChatOps et diagnostic | Etienne MURZEAU | Priyank intègre les parcours ; Rayan fournit événements et métriques d'exécution ; Vidjay revoit les aspects économiques ; les responsables infrastructure revoient les privilèges et sources. |
| EXG-024 : chaîne de confiance des plugins tiers, vision long terme | Rayan CHOUCHANE | Etienne coordonne le modèle de confiance ; Priyank traite le parcours d'administration ; Alex et Abdel-Waheb évaluent les effets sur les cibles. |

Les exigences P2 et Vision ne deviennent pas des engagements de livraison parce qu'un pilote est identifié. Leur réalisation reste soumise à l'arbitrage de périmètre.

#### 12.1.9 Sécurité transverse à chaque étape

| Étape | Contrôles attendus | Coordination principale |
| --- | --- | --- |
| Cadrage et conception | Définir données sensibles, zones de confiance, rôles, risques et limites de mutation. | Etienne avec toute l'équipe. |
| Développement et catalogue | Valider les entrées, schémas, chemins, contrats et dépendances ; ne pas intégrer de secrets au code. | Priyank pour l'application ; Alex et Abdel-Waheb pour l'IaC et les plugins. |
| Intégration et construction | Lint, tests, contrôles de dépendances/images et revue des artefacts avant promotion. | Rayan, avec revue des auteurs concernés. |
| Demande et approbation | Droits, quotas, coût, conformité et vérification de la version exacte à exécuter. | Priyank ; Vidjay pour les règles économiques ; Etienne pour la cohérence transverse. |
| Accès et exécution | Comptes limités, secrets issus du fournisseur autorisé, contrôle de propriété et périmètre des cibles. | Etienne, Rayan, Alex et Abdel-Waheb selon la couche. |
| Déploiement et restauration | Sauvegarde, canary, probes indépendantes, interruption et rollback adapté au service. | Abdel-Waheb, avec vérification indépendante par Alex et contrôles de pipeline par Rayan. |
| Exploitation et diagnostic | Logs expurgés, métriques, rétention, gestion des accès et IA en lecture seule. | Etienne et Priyank, avec les responsables infrastructure et Vidjay selon les données. |
| Démonstration et partage | Revoir captures, exports, plans et preuves ; conserver les secrets hors du dossier partagé. | Chaque auteur, avec revue croisée coordonnée par Etienne. |

Chaque membre documente les risques et tests de son lot. Un changement sensible fait l'objet d'une revue par un autre membre. Une lacune de sécurité bloque la livraison concernée tant qu'elle n'est pas corrigée ou formellement traitée dans le cadre de risque convenu ; la fin d'un sprint ne vaut pas acceptation implicite.

#### 12.1.10 Modalités de coordination et de remise

- Chaque lot dispose d'un pilote identifié, d'interfaces documentées, de critères de recette et d'un relecteur.
- Les contrats entre portail, inventaire, exécution, estimation et diagnostic sont définis avant les développements dépendants.
- Chaque membre fournit le code ou la configuration de son lot, les tests, la procédure de reproduction et les limites connues.
- Les revues d'intégration suivent un parcours utilisateur complet ; elles ne se limitent pas à constater que chaque composant démarre isolément.
- Etienne centralise les points bloquants et les demandes d'arbitrage ; le mentor et l'équipe valident le périmètre et les décisions qui les concernent.
- Les charges, échéances et disponibilités individuelles restent à renseigner collectivement. Cette répartition ne présume pas que les tâches sont déjà réalisées.

### 12.2 Jalons proposés

| Jalon | Livrable | Condition de sortie |
| --- | --- | --- |
| J0, validation du cadrage | Périmètre P0/P1, scénarios et critères approuvés | Le mentor confirme ce qui sera évalué et ce qui reste hors périmètre. |
| J1, baseline reproductible | Procédure de lancement, tests et preuve du parcours web | Une autre personne reproduit le déploiement et le rollback dans le lab. |
| J2, extensibilité élargie | Une nouvelle catégorie de plugin | Son cycle de vie passe sans modification métier du noyau. |
| J3, exploitation minimale | Secrets d'exécution et notification réelle si retenus | Fonctionnement et indisponibilités sont testés de bout en bout. |
| J4, extension choisie | Un seul axe P2 prioritaire | Fournisseur ou fonctionnalité accepté sur une cible réelle autorisée. |
| J5, recette et transmission | Dossier, preuves expurgées, démonstration et limites | Les exigences retenues ont un résultat, les écarts restants sont explicitement acceptés ou reportés. |

Les dates, efforts et dépendances humaines doivent être renseignés après arbitrage. Ne pas lancer simultanément cloud, réseau, marketplace et IA avant la stabilisation du socle.

### 12.3 Livrables attendus

- Ce cahier des charges et ses arbitrages versionnés.
- Code du portail et contrats des fournisseurs.
- Plugins avec manifests, schémas, assets et documentation.
- Procédures d'installation, démarrage, arrêt, sauvegarde et reprise.
- Environnement de démonstration et inventaire des prérequis.
- Tests automatisés et matrice de recette renseignée.
- Preuves partageables expurgées et registre des limites.
- Guide auteur de plugin et guide opérateur/administrateur.
- Support de démonstration et déclaration des usages de l'IA.

## 13. Risques et décisions à prendre

### 13.1 Registre initial des risques

| Risque | Conséquence | Réduction proposée |
| --- | --- | --- |
| R1, dispersion fonctionnelle | Beaucoup d'écrans, peu de parcours réellement terminés | Geler P0 et sélectionner peu d'extensions. |
| R2, modularité trop spécialisée | Chaque nouvelle brique impose des changements du noyau | Tester tôt une catégorie différente et relever les modifications. |
| R3, rollback inadapté aux données | Perte ou incohérence malgré un statut de succès | Contrat de sauvegarde par service et preuves indépendantes de restauration. |
| R4, perte d'accès réseau | Impossible de restaurer par le chemin cassé | Accès hors bande et cible de lab avant toute intégration réseau. |
| R5, code tiers privilégié | Compromission du poste et des secrets | Confiance explicite ; pas de marketplace publique avant isolation réelle. |
| R6, dérive après approbation | Exécution différente du plan relu | Empreintes, sources figées et revalidation avant mutation. |
| R7, fuite de secrets | Publication dans Git, captures ou diagnostic IA | Expurgation, comptes limités et revue avant partage. |
| R8, dépendance au laboratoire | Démo indisponible malgré un portail accessible | Vérification préalable des invités, accès, coffre et ports. |
| R9, confusion estimation/facture | Mauvaise interprétation du coût affiché | Source, devise, hypothèses et période clairement visibles. |
| R10, diagnostic IA trop affirmatif | Correction dangereuse ou cause inventée | Sources citées, incertitude, lecture seule et décision humaine. |

### 13.2 Arbitrages attendus du mentor

1. Confirmer le problème utilisateur prioritaire et le public réellement visé.
2. Valider le périmètre de recette académique et sa différence avec la vision commerciale.
3. Choisir la prochaine catégorie de brique : application, base de données ou autre service.
4. Décider si OpenTofu, Cisco/pfSense ou diagnostic IA est l'extension P2 prioritaire.
5. Définir l'environnement cible, les images autorisées et les accès disponibles.
6. Valider les sous-lots proposés pour l’équipe présentée en section 12.1, puis confirmer les charges, le calendrier, le budget et les critères d’évaluation.
7. Décider des contraintes de séparation demandeur/approbateur.
8. Fixer la politique de conservation des traces et de partage des preuves.
9. Choisir le niveau de démonstration des plugins : paquets de confiance locaux ou véritable distribution tierce.
10. Préciser si les interfaces métier spécifiques sont attendues, ou si les formulaires génériques suffisent.

### 13.3 Définition de « terminé »

Une capacité est livrée lorsqu'elle a un contrat documenté, une implémentation réelle, des tests adaptés, un résultat de recette observable et des limites explicites. Une entrée dans le catalogue, une interface de fournisseur ou une capture d'écran ne suffisent pas.

Le projet est recevable sur le périmètre convenu lorsque les critères retenus sont satisfaits ou que les écarts sont acceptés explicitement par les personnes responsables. Ce document ne s'auto-valide pas.

## 14. Méthode de cadrage, traçabilité et sources

### 14.1 Utilisation de l'outil proposé

Le dépôt `charroux/assistant-IA-projets` a été consulté dans la révision `b9c0eae5a64c80c7714f27021cee18a5bfd69483`. Sa méthode distingue besoin, exigences, critères, conception, décisions, preuves et écarts. Ses contrôles déterministes sont distincts d'un audit IA et ne remplacent pas le mentor.[1]

Le présent document reprend cette séparation. Les couples EXG/CRT forment la matrice de traçabilité ; les preuves existantes ne valent que pour leur périmètre testé. Une exportation documentaire accompagne le document pour permettre un contrôle structurel avec cet outil. Elle ne constitue ni une validation pédagogique ni un audit technique du logiciel.

Le campus a été confirmé par le porteur du projet : **EFREI Paris**, représenté par `paris` dans l'outil. Le contrôle déterministe strict a été relancé sur l'export `cadrage-mentor/` : 24 exigences et 24 critères, zéro erreur et zéro avertissement. Le rapport exact est conservé dans `cadrage-mentor/rapport-coherence.md`. Les dates du calendrier et la grille institutionnelle restent à confirmer. Le fichier de calendrier est présent, avec des jalons volontairement vides ; les dates du modèle amont ne sont pas adoptées automatiquement. Le succès de ce contrôle structurel ne valide pas ces informations encore manquantes.

Ce contrôle vérifie la structure de l'export, pas la justesse technique de tout le Markdown ni l'exécution des recettes. Aucun appel IA n'a été fait par l'outil `coherence`. Les nouveaux arbitrages restent au statut proposé jusqu'à validation humaine.

### 14.2 Sources locales faisant référence

Les chemins ci-dessous sont relatifs au dossier `Prototype/` ou au projet `kuberweaver/`. Les documents de vision ont été conservés ; leurs suggestions et formulations promotionnelles ne sont pas reprises comme des faits techniques vérifiés.

| Référence | Document | Utilisation |
| --- | --- | --- |
| S1 | `Prototype/prompt humain` | Besoin initial exprimé par le porteur du projet. |
| S2 | `Prototype/Cette vision décrit l'architecture complète d'un Internal Developer & Infrastructure Platform (IDP) orienté SaaS_Marketplace.md` | Vision large, pistes techniques et hypothèse d'organisation. |
| S3 | `Prototype/Le flux d'orchestration étape par étape (De la demande au Rollback).md` | Parcours cible et articulation des sous-systèmes. |
| S4 | `kuberweaver/README.md` | Périmètre implémenté, lancement et limites. |
| S5 | `kuberweaver/docs/ACCEPTANCE.md` | Gates du prototype et frontières de sécurité. |
| S6 | `kuberweaver/docs/VERIFICATION.md` | Résultats historiques et index des preuves. |
| S7 | `kuberweaver/docs/EXECUTION.md` | Contrat réel d'exécution, propriété et restauration. |
| S8 | `kuberweaver/docs/EXTENSIONS.md` | SDK Python, activation, registres et limites de confiance. |
| S9 | `kuberweaver/backend/kuberweaver/catalog.py` et `frontend/src/App.tsx` | Découverte des manifestes et chargement du catalogue dans l'interface. |

Les preuves privées ne sont pas embarquées dans ce document. Les adresses internes détaillées, identifiants de connexion et secrets ne sont pas nécessaires à la compréhension du mentor et ne sont pas reproduits.

### 14.3 Usage de l'IA et validation

Document rédigé avec l'assistance d'Hermes à partir des sources ci-dessus. La structuration, les priorités proposées, les critères futurs et l'analyse des risques sont des propositions de synthèse. Leur justesse et leur faisabilité doivent être revues par l'équipe et le mentor. Aucun engagement budgétaire, délai institutionnel ou décision de mise en production n'est créé par cette rédaction.

## Sources

[1] https://github.com/charroux/assistant-IA-projets , Assistant IA aux projets

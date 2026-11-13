# KuberWeaver, cahier des charges

Version 2.0, rédaction simplifiée · EFREI Paris

Proposition à valider par l’équipe et le mentor. Les responsabilités de référence sont conservées ; les priorités, dates et moyens restent à confirmer. Les résultats techniques cités proviennent des comptes rendus existants, pas de nouveaux essais.

## 1. Le projet et son périmètre

### Le besoin

Déployer un service informatique demande souvent de coordonner plusieurs outils et des procédures manuelles. KuberWeaver doit réunir ces opérations dans un portail interne : choisir un service, préparer sa configuration, faire approuver le changement et vérifier le résultat.

Le bénéfice recherché est de réutiliser les procédures validées de l’organisation, sans supprimer ses règles de sécurité. Le projet n’a pas vocation à remplacer les outils d’automatisation existants.

### Les utilisateurs et le parcours

- L’opérateur prépare, enregistre et soumet une demande.
- L’administrateur approuve ou refuse, gère les règles et autorise les extensions.
- Le lecteur consulte les informations auxquelles il a accès, sans les modifier.
- Un agent d’intelligence artificielle externe utilise les mêmes fonctions, dans les limites des droits explicitement accordés par un administrateur humain.

Parcours attendu : choisir un service → renseigner les paramètres → vérifier les règles et le coût estimé → faire approuver → sauvegarder et déployer progressivement → contrôler le résultat.

En cas d’échec, la plateforme arrête la progression et tente une restauration adaptée au service. Un rétablissement incomplet doit rester visible et demander une intervention humaine.

### Ce qui existe, ce qui reste à prouver

| Situation | Périmètre |
| --- | --- |
| Déjà exercé dans le laboratoire | Portail, catalogue, droits, approbation, déploiement de NGINX et Apache, contrôles et retour à la version précédente. |
| Testé localement, intégration réelle encore à valider | Notifications, approbation par messagerie, publication Git distante et injection des secrets d’exécution. |
| Préparé mais non validé sur une cible réelle | Création de machines avec OpenTofu, service Redis persistant et configuration Cisco/pfSense. |
| Hors livraison initiale | Place de marché publique, paiements, service commercial partagé entre plusieurs organisations et exécution de code tiers non fiable. |

Le prototype n’est pas prêt pour la production. Malgré son nom, il ne pilote pas Kubernetes. L’estimation de coût n’est pas une facture ; les liens du schéma ordonnent les déploiements sans configurer le réseau.

<!-- pagebreak -->

## 2. Les fonctions indispensables

Le socle ci-dessous est à préserver et à vérifier lors de la réception du projet. Chaque exigence est associée à un test observable. Les identifiants historiques sont conservés : le test CRT-001 correspond à EXG-001, et ainsi de suite.

| Référence | Fonction attendue | Test d’acceptation |
| --- | --- | --- |
| EXG-001 | Appliquer les droits côté serveur. | Un lecteur ne peut ni soumettre ni approuver ; un opérateur ne peut administrer, même par accès direct à l’API. |
| EXG-002 | Découvrir les modèles de service du catalogue. | Un modèle valide apparaît après rechargement ; un modèle invalide est signalé sans masquer les autres. |
| EXG-003 | Composer et sauvegarder un schéma. | Le rechargement conserve les éléments, leurs paramètres, positions et dépendances ; les cycles sont refusés. |
| EXG-004 | Produire les formulaires depuis les modèles. | Les valeurs par défaut et contraintes sont affichées ; le serveur refuse aussi les valeurs invalides. |
| EXG-005 | Contrôler règles, cibles et quotas. | Une cible inconnue, un dépassement, une configuration interdite ou une commande libre sont bloqués avant modification. |
| EXG-006 | Faire approuver le plan exact. | La décision est datée et attribuée ; modifier le plan ou ses sources après approbation bloque l’exécution concernée. |
| EXG-007 | Conserver une trace de chaque changement. | On retrouve le plan sans secrets, sa version Git, la décision et le résultat de l’exécution. |
| EXG-008 | Conserver les opérations et leur historique. | Après redémarrage, l’historique subsiste ; une opération interrompue attend une revue, sans relance automatique. |
| EXG-009 | Déployer progressivement. | Deux cibles réelles sont utilisées : sauvegarde, première cible, contrôle, puis poursuite ; le service est vérifié indépendamment. |
| EXG-010 | Restaurer après un échec. | Toutes les cibles modifiées retrouvent leur état initial, y compris l’absence de service ; une restauration ratée n’est jamais affichée comme réussie. Le retour manuel n’est proposé que s’il est autorisé et possible. |
| EXG-011 | Afficher l’inventaire réellement observé. | Une cible indisponible est indiquée comme telle, sans résultat inventé ni confusion avec le schéma demandé. |
| EXG-012 | Présenter le coût avant approbation. | Total, détail par ressource, devise et caractère estimatif correspondent au calcul du serveur. |
| EXG-013 | Contrôler l’activation des extensions. | Le code d’une extension désactivée n’est pas chargé ; une extension défaillante n’empêche pas le portail de fonctionner. |

Le succès d’un test automatique ne remplace pas un essai sur les ressources réelles lorsqu’il s’agit de démontrer un déploiement ou une restauration.

<!-- pagebreak -->

## 3. La suite proposée et les options

Les quatre lignes « Suite » sont prioritaires après consolidation du socle. Les options ne sont engagées qu’après arbitrage de l’équipe et du mentor. Un responsable identifié ne vaut pas engagement de livraison.

| Référence | Résultat attendu | Test d’acceptation |
| --- | --- | --- |
| EXG-014 · Suite | Ajouter une autre catégorie de service. | Déployer, vérifier et restaurer ce service sans modifier le cœur ; documenter les limites de conservation des données. |
| EXG-015 · Suite | Utiliser un coffre de secrets. | Un déploiement réel obtient un accès limité depuis le coffre ; expiration ou révocation bloquent proprement, sans fuite. |
| EXG-016 · Suite | Envoyer une notification réelle. | Le destinataire reçoit l’événement ; les erreurs et les risques de doublons sont traités explicitement. |
| EXG-017 · Suite | Suivre l’état du portail. | Santé, opérations et erreurs sont consultables ; une panne est visible et la durée de conservation des traces est documentée. |
| EXG-021 · À préserver | Permettre le pilotage par un agent externe. | Des échanges MCP réels lisent et soumettent via l’API ; droits insuffisants, expiration et révocation bloquent l’accès. Une approbation exige une délégation explicite. |
| EXG-018 · Option | Créer une ressource avec OpenTofu. | Approuver le plan sauvegardé exact, créer, vérifier indépendamment et supprimer avec autorisation ; protéger l’état. |
| EXG-019 · Option | Configurer un équipement réseau. | Modifier puis restaurer une cible autorisée en préservant un accès de secours. |
| EXG-020 · Option | Approuver depuis une messagerie. | L’acteur est identifié et lié au plan exact ; une action répétée, expirée ou interdite est refusée et tracée. |
| EXG-022 · Option | Publier les plans dans un dépôt Git distant. | Le dépôt reçoit la version attendue, sans secrets ; les échecs de publication et divergences sont signalés. |
| EXG-023 · Option | Exploiter les mesures de consommation. | Chaque mesure indique sa source, sa période et sa fraîcheur ; elle reste distincte d’une estimation ou d’une facture. |
| EXG-024 · Long terme | Distribuer des extensions tierces. | Définir et tester provenance, intégrité, versions, contrôles, révocation et isolation avant toute exécution non fiable. |

### Principe technique

Le portail React/TypeScript utilise une API FastAPI ; SQLite conserve l’état, Git les versions et Ansible exécute les changements dans le laboratoire GNS3. Des composants remplaçables gèrent l’inventaire, l’exécution, les secrets et les notifications.

Un plugin, mot anglais pour « extension », ajoute une capacité. Les modèles compatibles alimentent le catalogue et ses formulaires, mais pas de nouvelles pages spécialisées. Aucun modèle d’IA intégré ni abonnement IA n’est obligatoire.

<!-- pagebreak -->

## 4. Organisation et sécurité

### L’équipe

Les rôles de référence ci-dessous restent inchangés. La répartition pratique est proposée ; la sécurité relève de toute l’équipe, avec une revue croisée des changements sensibles.

| Personne | Rôle de référence | Contribution principale proposée |
| --- | --- | --- |
| Etienne MURZEAU | Référent / Déposant, Intégration AI / Infrastructure | Coordination, intégration des agents, cohérence technique et démonstration. Diagnostic IA en lecture seule si retenu. |
| Rayan CHOUCHANE | Infrastructure Terraform / Ansible + GitOPS, Pipeline : Lint + Conteneur | Versionnement, contrôles automatiques et chaîne de livraison. |
| Alex YAO | Infrastructure Terraform / Ansible | Ressources, inventaire et préparation des cibles. |
| Vidjay VELAYOUDAM | FinOps / Business | Besoins métier, modèle de coûts et analyse économique. |
| Abdel-Waheb SAKKAL | Infrastructure Terraform / Ansible | Configuration des services, extensions, vérifications et restauration. |
| Priyank SOLANKI | Développeur web, Full Stack | Portail, API, droits et parcours utilisateur. |

### Les règles de sécurité et de qualité

- Limiter les actions aux cibles et ressources autorisées. Ne pas modifier la production pour une démonstration.
- Garder les secrets hors du code, des plans partagés, des journaux et des captures. Limiter les accès techniques ; exiger une autorisation avant tout transfert de données vers une IA externe.
- N’activer une extension exécutable qu’après autorisation explicite. Le prototype n’isole pas le code tiers non fiable.
- Sauvegarder avant modification, vérifier le service obtenu et prévoir une reprise manuelle. Une restauration de conteneur ne prouve pas la cohérence des données.
- Afficher des erreurs compréhensibles et des états qui ne reposent pas seulement sur une couleur. Mesurer la performance avant de fixer un engagement de délai.

<!-- pagebreak -->

## 5. Livraison et décisions

### Les étapes et les livrables

1. Valider le périmètre et les tests retenus avec le mentor.
2. Faire reproduire le parcours web et sa restauration par une autre personne.
3. Intégrer le nouveau service et les fonctions « Suite » retenues, puis une option seulement si les moyens le permettent.
4. Remettre le code, les modèles de service, les procédures d’installation et de reprise, les résultats des tests et la liste des limites.

La réception finale doit montrer une demande interdite bloquée, un service réellement disponible, une restauration vérifiée et une extension ajoutée sans modification du cœur. Toute exigence retenue a un résultat et une preuve ; un écart est corrigé ou accepté explicitement.

### Les décisions encore ouvertes

Choisir le nouveau service et l’éventuelle option, confirmer les tâches, dates, moyens et licences. Fixer la conservation des traces et décider si une personne peut approuver sa propre demande. Principaux risques : dispersion du périmètre, perte de données, perte d’accès réseau et fuite de secrets.

La trajectoire PostgreSQL, les exécuteurs et le catalogue de plugins sont précisés dans [Architecture cible](ARCHITECTURE_CIBLE.md). Ce sont des propositions, pas des fonctions déjà livrées.

### Repères et sources

API signifie interface de programmation ; MCP est le protocole d’accès des agents. FinOps désigne la gestion économique des ressources informatiques. Les rôles historiques conservent AI, GitOPS, Lint et Full Stack : intelligence artificielle, gestion des changements par Git, contrôles statiques et développement de l’interface comme du serveur. Rollback signifie retour à l’état précédent.

Sources : besoin et vision d’origine, README, COMPLETION_STATUS.md, EXECUTION.md et EXTENSIONS.md. Le compte rendu du 6 novembre 2026 fait référence pour les essais récents. Les détails techniques restent dans ces documents, pas dans ce cahier des charges. Ancienne version : archives/CAHIER_DES_CHARGES_KUBERWEAVER_v1.3.md. Rédaction assistée par IA, validation humaine attendue.

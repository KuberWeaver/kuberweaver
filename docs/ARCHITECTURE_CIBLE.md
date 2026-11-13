# Architecture cible et gestion des plugins

Proposition issue des échanges de cadrage, à valider par l’équipe. Ce document précise la trajectoire du produit ; il ne décrit pas une migration ou une isolation déjà réalisée. Le périmètre exécuté reste décrit dans le README et COMPLETION_STATUS.md.

## 1. Les choix proposés

- Conserver React/TypeScript pour le portail et Python/FastAPI pour l’API. PHP/Laravel et Node.js sont des alternatives possibles, pas des changements décidés.
- Retenir PostgreSQL comme base cible de la plateforme partagée. SQLite reste le stockage actuel du prototype et une option possible pour une installation autonome.
- Garder un cœur modulaire et séparer les exécuteurs des requêtes du portail. Ne pas imposer des microservices, Kubernetes ou Redis sans besoin démontré.
- Commencer par un catalogue interne de paquets de confiance, pas une place de marché publique de code exécutable.

## 2. Fournir et maintenir un service

Distinguer trois objets : la demande décrit ce qui est souhaité ; l’exécution représente une tentative de changement ; le service représente ce qui a été fourni et reste géré après le déploiement.

Le cycle de vie doit couvrir création, consultation de l’état, modification et suppression autorisée, avec sauvegarde et restauration lorsque le service les permet. Un contrôle périodique compare configuration souhaitée et état réel.

Structure de données proposée :

| Ensemble | Contenu |
| --- | --- |
| Accès | Utilisateurs, équipes, projets, rôles et autorisations des agents. Organisations distinctes seulement si plusieurs clients sont prévus. |
| Catalogue | Modèles versionnés, schémas de paramètres, capacités et extensions autorisées. |
| Demandes | Configuration, plan exact, estimation et décisions d’approbation. |
| Services | Propriété, version du modèle, configuration souhaitée, état observé et identifiants des ressources réellement créées. |
| Exécution | Tâches, étapes, tentatives, réservations, vérifications et restauration. |
| Exploitation | Audit, notifications à transmettre, références de sauvegardes et données de coût. |

Les clés et mots de passe restent dans un coffre ; la base conserve leurs références. Les sauvegardes et journaux volumineux peuvent être conservés dans un stockage de fichiers ou d’objets, avec accès contrôlé.

## 3. Concurrence et reprise

SQLite autorise une seule transaction d’écriture à la fois par fichier. Le verrou Python actuel coordonne les threads d’un même processus, pas plusieurs serveurs. PostgreSQL est proposé pour faciliter les écritures concurrentes et la coordination des exécuteurs, sans garantir à lui seul une architecture distribuée fiable.

À mettre en œuvre et tester :

- Réserver une tâche atomiquement et empêcher deux changements incompatibles sur le même service.
- Détecter un exécuteur interrompu et empêcher une ancienne tentative de poursuivre après réattribution.
- Rendre les opérations répétables sans créer de ressources en double, principe appelé idempotence.
- Après interruption, relire l’infrastructure avant de réessayer ; placer les cas ambigus en revue manuelle.
- Conserver les tâches dans la base ; une file PostgreSQL suffit comme première option, sous réserve de tests de concurrence.
- Tester la restauration de la base et des autres états nécessaires, pas seulement leur sauvegarde.

Une transaction SQL ne peut pas annuler directement une ressource créée chez un fournisseur. Chaque intégration doit définir sa reprise et ses compensations.

## 4. Trois surfaces d’extension

| Type | Usage | Limite |
| --- | --- | --- |
| Modèle déclaratif de service | Paramètres et procédures utilisant un moteur déjà disponible. | Pas de code spécifique dans le cœur ou React pour un modèle compatible. |
| Plugin exécutable | Nouveau fournisseur d’inventaire, d’exécution, de secrets, de notifications ou de diagnostic. | Contrat explicite par famille, dépendances et privilèges maîtrisés. |
| Interface spécialisée | Console ou éditeur qui dépasse un formulaire standard. | Développement et contrat d’interface distincts ; pas de chargement automatique de JavaScript tiers. |

Plugin signifie « extension » en anglais. Le SDK, kit de développement, expose les interfaces officielles : entrées, résultats, erreurs et opérations disponibles. Le plugin ne doit pas dépendre des classes internes du cœur ni écrire directement dans ses tables. Les autorisations et approbations restent imposées par la plateforme.

Le SDK actuel possède un protocole versionné et des registres par capacité. Les signatures des fournisseurs sont définies par leurs consommateurs ; un registre générique ne garantit pas leur compatibilité. Les extensions Python actuelles sont du code de confiance chargé dans le serveur, pas du code isolé.

## 5. Paquets et publication

Chaque paquet doit annoncer identifiant, version, compatibilité, responsable, dépendances et capacités réelles. Il fournit paramètres, procédures, tests, prérequis et limites. Une opération non prise en charge reste indisponible. Les modèles déclaratifs et extensions Python gardent leurs formats actuels distincts, documentés dans EXECUTION.md et EXTENSIONS.md.

Sources dans Git → contrôles automatiques et revue → paquet immuable → publication au catalogue → installation autorisée.

Un dépôt de catalogue avec un dossier par paquet suffit au départ. Un registre OCI, format également utilisé pour distribuer les images de conteneurs, est une option ultérieure. L’installation actuelle accepte uniquement des dossiers locaux ; le téléchargement et la mise à jour depuis un registre restent à développer.

Chaque service conserve la version et l’empreinte du paquet ainsi que la provenance du moteur utilisé. Une nouvelle publication ne modifie jamais silencieusement les services existants. Une mise à jour nécessite une vérification de compatibilité et une opération approuvée. Revenir à un ancien paquet ne garantit pas la restauration des données.

Le code remplaçable et les données persistantes du plugin restent séparés. Désactiver ou retirer un paquet des nouvelles installations ne supprime pas les services existants ; prévoir comment continuer à les gérer, migrer ou arrêter.

## 6. Maintenance et sécurité

Fournir un kit commun de tests : configuration, compatibilité des contrats, gestion des erreurs, activation/désactivation et reprise. Ajouter les essais métier nécessaires sur les cibles réelles : deux serveurs web ne prouvent pas la prise en charge d’une base de données persistante.

Avant publication, contrôler les manifestes, rechercher les secrets, analyser les dépendances et exécuter les tests. Afficher un périmètre de validation précis plutôt qu’un badge « sécurisé » sans justification.

Pour les plugins exécutables externes, prévoir un processus ou conteneur distinct et des limites effectives sur fichiers, réseau, secrets et ressources. Un processus séparé n’est pas une isolation de sécurité à lui seul. Éviter d’installer toutes les dépendances des plugins dans l’environnement Python du serveur.

Chaque paquet a un responsable, des versions prises en charge, une politique de maintenance et une procédure de retrait. Une vulnérabilité doit pouvoir bloquer les nouvelles installations et identifier les services concernés.

## 7. Ordre de réalisation proposé

1. Valider les objets demande/exécution/service et les contrats des fournisseurs.
2. Migrer vers PostgreSQL avec reprise des données et essais de concurrence.
3. Séparer les exécuteurs et éprouver réservation, interruption et reprise sans doublon.
4. Fournir le kit de tests et un service d’une autre catégorie, sans modification métier du cœur.
5. Développer la distribution des paquets puis l’isolation nécessaire avant toute ouverture publique.

Les dates, charges, choix de registre et niveau d’ouverture restent à décider. Ces propositions ne remplacent pas les engagements validés du cahier des charges.

## Références

- README.md et docs/COMPLETION_STATUS.md : état et limites du prototype.
- docs/EXTENSIONS.md et docs/EXECUTION.md : contrats existants.
- https://www.sqlite.org/whentouse.html : limites et usages de SQLite.
- https://www.postgresql.org/docs/current/explicit-locking.html : coordination par verrous dans PostgreSQL.

Rédaction assistée par IA, validation humaine attendue.

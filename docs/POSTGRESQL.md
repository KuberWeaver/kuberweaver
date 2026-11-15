# PostgreSQL, instance locale basculée

Les données de l’instance ont été importées dans la base cible `kuberweaver` puis relues indépendamment : chaque ligne, son ordre historique et ses empreintes correspondent à SQLite. La configuration privée est publiée et le portail est actif sur `http://127.0.0.1:8088` avec PostgreSQL. La sélection effective du service et son verrou contrôleur PostgreSQL ont été vérifiés, ainsi que les réponses HTTP 200 du portail et 401 des routes privées sans authentification. SQLite et ses sauvegardes sont conservés intacts. Les secrets ne doivent apparaître ni dans les API, ni dans les commandes affichées ou les rapports.

Preuve privée d’import : `data/evidence/postgresql-cutover.json`. Une archive native PostgreSQL a également été produite dans `data/backups/postgresql-after-import.dump`, mode 0600 ; son catalogue de neuf tables de données est lisible par `pg_restore --list`. Cette inspection n’est pas un test complet de restauration.

## Infrastructure réellement démarrée

- PostgreSQL **18.7**, image officielle épinglée : `postgres@sha256:2ea8630df176ad090ec92136dee850397c5af8781e4bcf2e4162593691c61bbc`.
- Conteneur propre au projet : `kw-postgresql-v2`.
- Volume persistant dédié : `kw-postgresql-v2-data`.
- Publication locale seulement : `127.0.0.1:15433`.
- Bases séparées : `kuberweaver` pour la migration, `kuberweaver_test` pour les tests isolés par schéma.
- Rôle applicatif propriétaire de ces bases, sans superutilisateur, création de bases, création de rôles ou réplication.
- Client installé dans `.venv` : `psycopg[binary]==3.3.9`.

Les connexions sur le loopback sont une configuration de laboratoire sans TLS de bout en bout. Ce montage mono-hôte n’est pas une installation HA ni une autorisation de lancer plusieurs Workers.

## Fichiers privés

Les URL de connexion sont préparées dans `data/postgresql/target-url` et `data/postgresql/test-url`, mode 0600 sous répertoire 0700. Leur contenu ne doit pas être affiché. Le bootstrap administratif est également privé ; il n’est pas un compte utilisé par l’API.

La configuration active est `data/config/database-url`, mode 0600, explicitement sélectionnée par `KUBERWEAVER_DATABASE_URL_FILE` dans le service local. Elle a été publiée après migration et vérification complète. Une configuration PostgreSQL invalide ou un serveur indisponible ne doit jamais provoquer de repli silencieux vers SQLite.

## Conservation des données

Sauvegarde cohérente obtenue par l’API SQLite backup, vérifiée par `integrity_check` :

`data/backups/pre-postgresql-20261115T214121Z/manifest.json`

Ce manifeste conserve les comptes et empreintes de chaque table. La source contient notamment trois comptes, quinze demandes, vingt-neuf sessions et cent événements d’audit. Les hashes de mots de passe, identifiants, sessions, paramètres et journaux doivent être conservés ; aucun réensemencement de comptes ne doit remplacer les données migrées.

Le backend a changé sans refonte des objets métier. Les documents JSON existants restent sous leur contrat actuel ; la migration n’introduit ni nouvel orchestrateur, ni schéma universel de CMDB. Les notifications utilisent également PostgreSQL quand celui-ci est sélectionné, sans nouveau fichier SQLite.

Le plan détaillé est dans `plans/postgresql-cutover.md`.

## Retour arrière explicite, jamais automatique

La sauvegarde SQLite a été réellement réimportée et vérifiée sur une base PostgreSQL jetable, puis exercée par HTTP. L’ancienne base reste intacte ; elle représente l’état au moment de la migration, pas les futures écritures PostgreSQL.

1. Arrêter le portail avec `systemctl --user stop kuberweaver.service` et conserver une sauvegarde PostgreSQL courante avant toute décision.
2. Comparer l’état PostgreSQL à la preuve de migration. Si des écritures ont eu lieu depuis, ne pas reprendre aveuglément SQLite : cela abandonnerait ces données. La migration inverse PostgreSQL vers SQLite n’est pas implémentée ; une exportation et réconciliation contrôlées sont alors nécessaires.
3. Un retour à l’instantané pré-migration exige de choisir explicitement cet ancien état, de déplacer la configuration `data/config/database-url` vers un emplacement privé conservé et de retirer explicitement les variables de sélection PostgreSQL du service. Son environnement impose volontairement `KUBERWEAVER_DATABASE_URL_FILE` : effacer uniquement le fichier fait échouer le démarrage, sans activer SQLite.
4. Vérifier les comptes et demandes de l’état choisi avant de rouvrir le portail. Ne pas supprimer le volume PostgreSQL ni les sauvegardes pour effectuer ce retour.

## Démarrage local et secrets

L’unité `ops/kuberweaver.service`, également utilisée par le service utilisateur local, sélectionne explicitement le fichier privé PostgreSQL. Elle ne démarre plus implicitement le tunnel vers le coffre distant. Cette modification ne change ni les liaisons de fournisseurs ni la configuration de cet ancien coffre.

Le nouveau coffre simple est l’extension Fernet `local-vault`, installée et activée, documentée dans `../examples/extensions/local-vault/README.md`. Le pilote ESXi en lecture seule est également disponible, mais aucune découverte authentifiée ou création de VM n’a été exécutée. Il conserve les références de secrets, les contrôles humains/agents et le connecteur OpenBao existant, sans nouveau service réseau. Sa clé locale ne protège pas contre un accès au compte système ou à root.

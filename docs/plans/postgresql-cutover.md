# PostgreSQL et clôture des correctifs, plan d’implémentation

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Corriger les deux défauts Worker et le démarrage du coffre, puis basculer les données existantes vers PostgreSQL sans perte ni modification de ressources ESXi.

**Architecture:** Garder FastAPI, le Worker unique et les contrats de modules. PostgreSQL devient le stockage de l’instance locale par configuration privée explicite ; SQLite reste lisible pour migration et tests de compatibilité, jamais comme repli silencieux si PostgreSQL est configuré et indisponible. Préserver les documents et les identifiants existants, sans refonte de CMDB.

**Tech Stack:** Python, psycopg 3, PostgreSQL stable en conteneur officiel épinglé, pytest, React/Vite existants.

## Contrats gelés et propriété

- Worker : `jobs.py` et nouveaux tests de sûreté Worker uniquement.
- Coffre historique : correctif Docker livré, mais le coffre est désormais initialisé sans bootstrap récupérable ; conteneur arrêté et Raft conservé. Décision utilisateur : privilégier un coffre basique, éventuellement une bibliothèque, sans imposer Docker/OpenBao.
- Nouveau coffre local : `local_vault.py`, `setup_local_vault.py`, nouvelle extension `local-vault` et tests dédiés uniquement. Fernet via cryptography, clé locale et données privées, aucune modification du connecteur OpenBao ni des liaisons globales.
- Stockage : `storage.py`, nouveau `postgres.py`, migration, dépendances et tests PostgreSQL. Contrat Store préservé (`path`, `lock`, `conn`, `execute`, `all`, `one`, `put`, `get`, `list`, `audit`, sessions).
- Interface nouvelle : `store.backend` vaut `sqlite` ou `postgresql` ; `store.open_postgres_connection()` fournit une connexion fraîche compatible avec `execute(sql, args)` à paramètres qmark, lignes mappées, commit/rollback et contexte transactionnel. Ne jamais exposer le DSN dans les API ou les erreurs.
- Configuration : `KUBERWEAVER_DATABASE_URL_FILE`, ou fichier privé `state_dir/config/database-url`, puis variable explicite `KUBERWEAVER_DATABASE_URL`. Aucune sélection de base via une donnée de graphe. Le parent publie la configuration de bascule seulement après comparaison complète.
- Notifications : `notifications.py` et ses nouveaux tests PostgreSQL uniquement. En PostgreSQL utiliser `notification_deliveries`, colonnes historiques `id,payload,status,attempts,error,updated` et `_kw_rowid` pour l’ordre ; conserver les envois incertains, pas de rejeu automatique.
- Le parent possède l’installation de PostgreSQL, la sauvegarde, la bascule réelle, la documentation générale et les tests intégrés. Aucun commit/push, aucun apply ESXi.

## 1. Worker, TDD

Reproduire `SystemExit` et `KeyboardInterrupt` après la première mutation : attendre `requires_manual_review=true`, aucune reprise au redémarrage. Reproduire une approbation entre publication du plan et notification : conserver `pending_approval` dans la complétion de préparation. Implémenter uniquement ces correctifs. Exécuter les tests Worker et préparation ; revue indépendante.

## 2. Coffre, décision simplifiée et recette locale

Le correctif Docker `NetworkMode=bridge` a été livré et testé sans assouplir les autres contrôles. La recette a ensuite rencontré une initialisation effective sans bootstrap récupérable. Le conteneur est arrêté, le Raft et son archive sont conservés.

L’utilisateur autorise désormais de repartir sur une solution basique, y compris une bibliothèque. Implémenter un fournisseur optionnel `local-vault` avec Fernet (cryptography), clé 0600 et répertoire 0700, fichiers chiffrés publiés atomiquement, références validées et liées au ciphertext. Lecture seule par défaut, écriture opt-in, métadonnées et erreurs sans secrets. Vérifier persistance, concurrence, clé perdue, corruption, liens et limites de taille, puis une vraie recette privée. Conserver OpenBao/Vault comme connecteur interchangeable ; ne pas changer la liaison de secrets globale. Documenter la limite de la clé locale contre un compte système compromis. Aucun nouveau service Docker nécessaire.

## 3. PostgreSQL, TDD et migration

1. Écrire un test de Store sur PostgreSQL réel, constater RED, implémenter le backend minimal et revérifier GREEN.
2. Couvrir SQL paramétré, upsert, ordre historique, audit/séquences, authentification, tokens, connexions, rollback transactionnel, panne et erreurs sans DSN.
3. Ajouter une migration explicite en transaction vers une cible vide : toutes les tables métier existantes, lignes et ordre conservés ; refus d’une cible non vide, d’un schéma inattendu ou d’une source active.
4. Sauvegarder SQLite par son API backup, vérifier les empreintes et permissions ; laisser la source intacte. Migrer également l’outbox SQLite si présente.
5. Comparer les comptes et empreintes canoniques de chaque table, y compris hashes de mots de passe, sessions, audit, jobs, paramètres et tokens. Réinitialiser correctement les séquences PostgreSQL.

## 4. Notifications PostgreSQL

TDD réel sur la connexion dédiée : stockage durable et ordonné, déduplication, transactions et reprise `in_flight -> uncertain`, lecture sans envoi réseau. Ne plus écrire `deliveries.sqlite3` quand PostgreSQL est sélectionné. Garder l’isolation du module et les résultats publics expurgés.

## 5. Vérification et bascule

- Exécuter pytest complet, tests frontend, build et protocoles HTTP/MCP/navigateur.
- Tester les parcours métier avec une instance PostgreSQL isolée avant la vraie migration.
- Publier atomiquement la configuration privée après migration vérifiée ; redémarrer uniquement le portail local si toutes les revues bloquantes sont closes.
- Relire via l’API les comptes, demandes, connexions et le backend actif sans exposer les credentials. Conserver sauvegardes et procédure de retour SQLite explicite, sans repli automatique ni double écriture.
- Documenter séparément migrations réussies, limites ESXi et toute recette encore bloquée.

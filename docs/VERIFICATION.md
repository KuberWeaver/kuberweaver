# Vérification du prototype

## Exécution indépendante depuis le portail

Le contrôle FastAPI sert le frontend construit sur `http://127.0.0.1:8088`. Le portail a été testé avec un navigateur Chromium réel, sans backend simulé pour ces acceptations.

- Connexion, canvas initialement vide, ajout NGINX, formulaire issu du schéma.
- Validation serveur, sauvegarde du blueprint et création d'une demande.
- Approbation dans le navigateur, puis vraie exécution Ansible sur `app` et `branch-client`.
- Réponses HTTP sur les deux management IPs, confirmées séparément par SSH.
- Coût calculé côté serveur et détail par ressource visibles avant le bouton d'approbation.
- Refus d'une demande dans le navigateur, état rejeté relu par API.
- Neuf sections testées à 390 pixels sans débordement horizontal de page.
- Contrôles d'édition interdits pour le lecteur.
- Aucune exception JavaScript pendant les acceptations navigateur finales.

Preuves : `data/evidence/browser-acceptance.json`, `browser-final.json` et captures `portal-*.png`.

## Retours arrière réels

Un état NGINX `1.0.0` a été installé sur les deux cibles. Une mise à jour `2.0.0` avec échec volontaire de la vérification rolling a restauré `1.0.0` sur les deux cibles, y compris le canary déjà réussi. Une seconde mise à jour sans échec a effectivement servi `2.0.0`, puis le rollback manuel a restauré `1.0.0`.

Les vérifications des deux invités utilisent des processus SSH indépendants de l'exécuteur. Le test direct de l'exécuteur couvre aussi Apache et la restauration d'absence quand aucun conteneur n'existait auparavant.

Preuves : `data/evidence/api-live-acceptance.json`, `execution-live.json`, journaux/snapshots privés sous `data/jobs/` et `data/execution/`.

La reprise d'un rollback partiellement échoué est couverte à travers l'API et le vrai exécuteur avec une frontière Ansible injectée en test. Elle n'est pas présentée comme une nouvelle panne réseau provoquée sur le lab réel.

## Modularité exercée, pas seulement déclarée

L'exemple `lab-notes`, livré séparément du noyau, a été installé désactivé, activé explicitement et sélectionné depuis Intégrations.

Un vrai job a émis `job_finished`. L'extension a persisté cet événement, et `/api/diagnostics` a rendu le même identifiant de job avec sa provenance. Désactiver cette extension alors qu'elle restait sélectionnée a donné une indisponibilité explicite sur ce seul endpoint, tandis que l'inventaire et les jobs restaient consultables. La sélection explicite du fournisseur `rules` a rétabli le diagnostic. Les empreintes de tous les fichiers Python du noyau sont restées identiques pendant ce cycle.

Preuve : `data/evidence/modularity-live.json`. L'exemple est laissé désactivé ; les liaisons finales incluent la source de secrets OpenBao, sans prétendre injecter les clés de déploiement depuis le coffre.

## Tests et build

Résultat relu par le contrôleur parent :

- Backend, SDK, exécuteur et secrets : 159 tests, zéro échec et zéro erreur.
- Frontend : 35 tests passants.
- TypeScript et build Vite : réussis.
- `npm audit` : zéro vulnérabilité signalée au moment de la vérification, ce qui n'est pas une certification de sécurité.

Rapport JUnit : `data/evidence/parent-suite.xml`.

## Persistance et exploitation

Le portail et le tunnel OpenBao sont des services systemd utilisateur actifs. Ils sont liés mais non activés au démarrage automatique. Un redémarrage réel du service applicatif a conservé les jobs et les liaisons de fournisseurs. Les opérations interrompues ne sont pas rejouées sans revue.

Preuve : `data/evidence/restart.json`.

OpenBao a été relu via son tunnel supervisé : service initialisé/unsealed, stockage persistant, token applicatif à privilèges limités, refus des opérations hors politique. Le service et les fichiers de bootstrap restent soumis aux limites détaillées dans `docs/SECRETS.md`.

Preuve : `data/evidence/openbao.json`.

## Périmètre préservé

- Les quatre fichiers du cadrage original ont conservé leurs SHA-256.
- Les identifiants des projets GNS3 existants sont inchangés.
- Les vingt identifiants de liens du lab sont inchangés.
- L'application d'origine sur le port 8080 sert toujours sa version `1.0.2`.
- Les déploiements du prototype occupent uniquement leurs ressources nommées et leurs ports de management.
- Aucun push GitHub ni déploiement de production.

Preuves : `data/evidence/source-brief-hashes.json`, `parent-baseline.json`, `parent-boundaries.json`.

## Relectures

La revue indépendante de conformité a d'abord bloqué des parcours qui contournaient la sélection des fournisseurs. Ces écarts ont été corrigés, avec des tests de consommation réelle des providers et d'événements. La seconde revue de conformité a rendu PASS sur les quatre écarts identifiés.

Les limites OpenTofu, cloud, Cisco/pfSense, ChatOps, secrets d'exécution et inférence IA restent explicitement visibles dans le README. Aucun résultat de déploiement n'est simulé pour ces fonctions non implémentées.

La revue qualité indépendante a identifié un dernier défaut : les dépendances exécutables partagées n'entraient pas toutes dans l'empreinte d'approbation. La correction inclut désormais le répertoire de lifecycle partagé, l'implémentation réelle de l'exécuteur et ses sources héritées, avec revalidation avant les commandes. Vingt-trois tests supplémentaires couvrent ces dérives. Le contrôleur parent a relancé les 159 tests et les acceptations API, navigateur et modularité sur le build final. La relecture ciblée de cette correction a rendu PASS, sans défaut bloquant restant dans le périmètre examiné. Cela n'est ni une sandbox ni une certification contre les courses de fichiers hostiles.

Les ressources de la démonstration directe sur le port 18090 ont ensuite été restaurées à leur absence initiale sur les deux cibles, pour laisser le port par défaut libre au premier essai utilisateur. Le déploiement de démonstration du portail sur 18091 reste présent, en version 1.0.0. Preuve de nettoyage : `data/evidence/default-port-cleanup.json`.

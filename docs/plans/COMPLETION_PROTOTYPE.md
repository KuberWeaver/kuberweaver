# Complétion des fonctions manquantes du prototype

> For Hermes: Use subagent-driven-development with spec review followed by quality review.

Goal: implémenter des parcours réels pour les fonctions annoncées mais absentes, sans modifier les ressources étrangères au laboratoire, publier ni simuler des succès.

Architecture: conserver identité, admission, approbations, provenance et jobs dans le noyau. Ajouter les intégrations dans des modules et fournisseurs indépendants, avec configuration administrateur locale, références de secrets et états explicites. Aucune clé ou destination de production n'est présumée.

Stack: FastAPI/Python, SDK de fournisseurs, Ansible, React/TypeScript, pytest/Vitest.

## Correction de périmètre IA demandée par le porteur

L'objectif est une plateforme **agent-capable**, pas une IA embarquée obligatoire : un agent externe (Hermes ou autre) doit pouvoir piloter les fonctions de KuberWeaver par la même API que le portail, avec un adaptateur MCP typé. API = autorité des droits, scopes, approbations, quotas et audit ; MCP = interface de commodité, jamais une voie de contournement. Jetons de service humains explicitement émis, limités par rôle et opérations, expirables et révocables. Aucune clé de modèle n'est nécessaire dans KuberWeaver. Le fournisseur LLM développé en parallèle demeure facultatif et n'est pas activé ni requis dans le registre par défaut.

Lots complémentaires : `agent_auth.py` et middleware bearer sans affaiblissement des sessions/CSRF ; serveur MCP stdio avec outils métiers fermés ; page Accès agents pour émission et révocation ; preuve par de vrais appels MCP sur API isolée, refus d'approbation pour un jeton opérateur et révocation effective.

## Frontières

- Branche de travail locale. Préserver `presentations/` non suivi et l'état privé `data/`.
- Aucun push, aucun message à une destination externe sans configuration autorisée, aucune modification de la production.
- Tests TDD par tranche verticale : test RED constaté, implémentation minimale, GREEN, régression.
- Les contrôles avec endpoints locaux prouvent le transport et le protocole, pas un déploiement chez un fournisseur distant.

## Lots parallèles et propriété exclusive

1. Notifications réelles et livraison durable : nouveau `backend/kuberweaver/notifications.py`, `tests/test_notifications_delivery.py`, documentation dédiée. Fournisseur `send(payload)`, envoi webhook JSON/Discord/Teams configuré par administrateur avec référence de secret, timeout, retries bornés, déduplication, statuts persistants, journaux expurgés. Pas de modification du registre global par l'agent ; intégration par le parent.
2. GitOps distant : nouveau `backend/kuberweaver/gitops.py`, modification exclusive de `jobs.py`, tests de vrais dépôts bare locaux. Push facultatif explicite sur une destination autorisée, vérification de la référence distante exacte, refus non fast-forward, conservation du commit local et échec avant queue si push requis. Pas de push au dépôt du projet.
3. Secrets d'exécution : nouveau module de credentials, modification exclusive de `execution.py`, tests. Référence administrateur par cible résolue à l'exécution via le fournisseur sélectionné ; clé temporaire privée, host-key checking, nettoyage garanti, pas de secret dans graph/Git/jobs/events/inventory permanent. Configuration absente garde l'accès SSH actuel, configuration invalide refuse sans fallback.
4. Diagnostic IA lecture seule : nouveau `backend/kuberweaver/llm_diagnostics.py`, tests. API compatible OpenAI configurée administrativement ; envoi de résumés expurgés et bornés uniquement ; réponse structurée validée avec références existantes, pas de tool-call/exécution ; token par référence de secret ; indisponibilité explicite sans faux diagnostic.
5. Nouvelle catégorie de service : Redis avec contrat propre, fournisseur exécutable séparé et plugin déclaratif. Pas de changement du lifecycle HTTP existant ni d'activation globale automatique. Stockage/sauvegarde/restauration réelle à documenter et tester ; pas d'affirmation live sans cible et commande réelle. Privilégier un périmètre clair plutôt qu'une imitation de base transactionnelle.

## Intégration parent

- Ajouter registre et endpoints administrateur aux nouveaux modules sans exposer de valeurs secrètes.
- Les endpoints lecture doivent rendre configuration sûre et état de livraison ; activation reste explicite, sélection des providers existante.
- Fournir guides et fichiers exemples inertes, pas de clés par défaut.
- Exécuter tests complets backend/frontend/build, tests de transports locaux, redémarrage du serveur seulement après queue inactive et sauvegarde SQLite via API de backup.
- Vérifier readbacks des cibles exactes après chaque écriture externe.

## Écarts nécessitant environnement ou jalon complémentaire

- OpenTofu sur hyperviseur/cloud et Cisco/pfSense : requièrent fournisseur/cibles/images autorisés et protocole de secours. Ne pas requalifier les entrées planned en live avant recette indépendante.
- ChatOps d'approbation : requiert identité du fournisseur, vérification de signature/rejeu et destination autorisée. Notifications seules ne valent pas approbation ChatOps.
- Stack front/back : requiert véritable liaison applicative et données, pas seulement ordre du graphe.
- SSO, multi-tenant, HA et marketplace isolée : projets de durcissement distincts, aucune promesse de production issue des nouveaux adaptateurs.

## Gates

Spec review indépendant des lots : exigences, intégration consommateur, absence de faux succès, non-régression des garanties. Puis revue qualité/sécurité indépendante. Corriger les défauts importants avant validation finale.

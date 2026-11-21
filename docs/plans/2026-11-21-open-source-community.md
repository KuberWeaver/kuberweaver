# KuberWeaver: passer de projet perso à projet open source communautaire

> Plan de cadrage. Aucune ligne de code produit ici, sauf mention contraire.

## 1. Etat des lieux (vérifié)

- Repo: `git@github.com:EMRD95/kuberweaver.git`, **privé**, pas de licence, pas de topics.
- Branches locales restantes après nettoyage: `main`, `feat/enterprise-services`.
- Travail en cours sécurisé: commit `edd1834` sur `feat/enterprise-services`, poussé.
- 71 000 lignes Python, 79 fichiers de tests, 21 briques `plugins/*`, 8 extensions `examples/extensions/*`.
- CI existante (`.github/workflows/ci.yml`): secret scan, hygiene, tests pytest + PostgreSQL.
- Branch protection `main`: `tests` requis, `enforce_admins=true`, 0 review requise.
- Documentation abondante mais bilingue et partiellement périmée (README décrit un front React supprimé; `docs/THEMING.md`, `NAVIGATION.md` trackés; CHANGELOG énumère le portail).

## 2. Décision de licence (point bloquant, à trancher avec l'utilisateur)

Exigence exprimée: « si tu l'utilises dans ton business et que tu fais de l'argent, tu me dois de l'argent ».
Une licence purement permissive (MIT/Apache) ne peut pas porter cette clause. Options:

| Option | Gain | Coût |
| --- | --- | --- |
| **Dual: GPL-3.0 + commerciale** | Open source réel, IA libre de contribuer, l'usage commercial est interdit sans accord, donc payant | Nécessite un titulaire de droits unique (l'utilisateur) et un processus de vente de licence |
| AGPL-3.0 seule | Idem, plus fort sur la revente en SaaS | Effet dissuasif connu sur l'adoption entreprise |
| Source-available + licence commerciale par défaut | Contrôle maximal | Ce n'est plus de l'open source au sens OSI, les contributeurs hésiteront |

Recommandation: **GPL-3.0-or-later + licence commerciale**, avec `LICENSE` (GPL) et un fichier `COMMERCIAL-LICENSE.md` décrivant la demande d'exemption. C'est le modèle MySQL/Zend. Point d'éthique: le projet reste libre pour usage perso, asso, recherche, interne; la redevance ne porte que sur la monétisation par un tiers.

## 3. Stratégie communautaire (le vrai sujet)

Le problème n'est pas « ajouter des fichiers », c'est **pourquoi quelqu'un contribuerait**. Réponse honnête: KuberWeaver n'a pas encore d'utilisateurs, donc le cercle vertueux « j'utilise, donc je corrige » n'existe pas. Il faut donc fabriquer la contribution à froid, sur trois axes:

1. **Contribution à coût minimal**: une brique = un dossier + un manifest + des playbooks. Zéro branchagement dans le noyau. C'est déjà vrai techniquement (manifest `schema` + `lifecycle`), il faut le documenter comme le chemin principal et le rendre copier-coller possible (`kw plugin init <id>`).
2. **Gains pour le contributeur**: brique publiée dans le catalogue, attribution visible (contributeur listé, entrée au changelog), et surtout **elle déploie chez lui** (réutilisable immédiatement). La monnaie d'échange est l'usage, pas l'argent.
3. **Réduction du risque du mainteneur**: pas de promesse de merge rapide, pas de SLA, mais une checklist publique et un tri honnête par labels.

## 4. Livrables de la passe (TODO swarm)

Phase A, fondations:
- `LICENSE` (à décider), `CONTRIBUTING.md` réécrit pour un externe, `SECURITY.md`, `SUPPORT.md`.
- `.github/PULL_REQUEST_TEMPLATE.md`, `.github/ISSUE_TEMPLATE/{bug_report,feature_request,plugin_submission}.yml`, `.github/ISSUE_TEMPLATE/config.yml`.
- `AGENTS.md` court (< 120 lignes): ce qui consomme peu de tokens, commandes de check, règles de commit/push/merge, limites.
- Labels community + `CODEOWNERS` léger.

Phase B, site vitrine:
- Site statique sous `/site` (ou repo dédié) servi par GitHub Pages, en anglais.
- Argumentaire commercial honnête: ce que le produit fait, ce qu'il ne fait pas (reprendre la table des limites).
- Le site est lui-même un cas d'usage de KuberWeaver (auto-hébergement démontré), voir audit technique.

Phase C,Discord:
- Serveur Discord + rôle/bot minimal, invitation dans le README et sur le site.
- Le bot demande un token: **action utilisateur**, ne pas le créer à sa place.

## 5. Séquence d'exécution

1. Trancher la licence.
2. Phase A (fichiers community), revus par le swarm, poussés sur `develop`.
3. Nettoyer les docs incohérentes (front supprimé).
4. Phase B (site), validé visuellement par l'utilisateur.
5. Phase C (Discord), l'utilisateur crée le serveur, je branche.
6. Publication du repo: décision finale explicite de l'utilisateur.

## 6. Risques

- Rendre le repo public expose l'historique complet (10 branches supprimées, 2 stashes privés): un `gitleaks --history` est requis avant toute publication.
- Un `AGENTS.md` mal écrit coûte plus de tokens qu'il n'en économise: le garder impératif et court.
- Le site promis « géré par KuberWeaver » implique une cible d'hébergement réelle (VM/SSH): non prouvée aujourd'hui, à vérifier avec l'audit technique.

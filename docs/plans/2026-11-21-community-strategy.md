# KuberWeaver en projet communautaire : ce qui a été fait, ce qui reste

## 1. Pourquoi c'est plus qu'une question de fichiers

Un dépôt avec un `CONTRIBUTING.md` vertueux ne crée pas une communauté. Les
projets qui réussissent ont une raison pour laquelle un inconnu passe du
« je vois » au « je corrige ». Pour KuberWeaver, cette raison existe déjà dans
le code, mais elle était invisible : **ajouter un service = ajouter un dossier
avec un manifest, sans toucher au noyau**. C'est la contribution la moins chère
qui soit, et c'est exactement le message à mettre en avant.

Les trois leviers retenus :

1. Coût de contribution minimal (déjà vrai techniquement, à documenter).
2. Gain réel pour le contributeur : sa brique est immédiatement déployable chez
   lui, et son nom apparaît dans le changelog. La monnaie d'échange est l'usage.
3. Risque du mainteneur encadré : pas de promesse de merge, mais des règles
   publiques et un tri honnête aux labels.

## 2. Le problème de fond, honnêtement

Le projet est à **0 utilisateur externe connu, 0 star, dépôt privé**. Personne ne
contribue à un outil qu'il n'utilise pas. Deux conséquences :

- Il faut fabriquer les premières contributions à froid, en les payant autrement
  qu'en notoriété : documentation claire, tri rapide des issues, et surtout un
  premier cercle qui teste vraiment (camarades, promo EFREI, forums d'admin
  sys francophones). Ce n'est pas du growth hacking, c'est du bouche-à-oreille
  assumé.
- Un serveur Discord vide tue plus qu'il n'aide. Mieux vaut l'ouvrir quand il y a
  quelque chose à y mettre (une release, un premier contributeur, un cas d'usage
  publié). D'où le statut « structure prête, pas encore d'invitation ».

## 3. Licence : le point qui t'a appartenu

Ta consigne (« si tu fais de l'argent avec, tu me dois de l'argent ») exclut MIT
et Apache. Retenu : **GPL-3.0-or-later + licence commerciale** (modèle MySQL/Zend).

Ce que ça implique concrètement, sans jargon :

- Particulier, asso, recherche, usage interne : libre, gratuit, sans accord.
- Intégration dans un produit que tu revends ou SaaS que tu factures à des tiers :
  il faut une licence commerciale, donc un accord avec toi.
- Point d'attention : le droit moral d'auteur t'appartient, mais pour vendre une
  licence commerciale proprement, il faut que **tous** les contributeurs te
  cèdent leurs droits (CLA) ou que tu obtiennes leur accord. Sans ça, un
  contributeur peut bloquer la vente d'une licence sur sa partie. C'est le vrai
  sujet juridique, et il se traite au moment où arrive le premier contributeur,
  pas avant. Ne l'ouvre pas dans le README comme une menace, mets-le dans un
  document `LICENSE.md` à part quand un premier PR arrive.

## 4. Le site vitrine

`site/` est statique, anglais, sans CDN ni tracker, ouvrable hors-ligne. Sa
section la plus importante est la table des limites recopiée du README : c'est
elle qui donne confiance, parce qu'elle dit ce qui n'est **pas** fait.

Il est servi par GitHub Pages (`/.github/workflows/pages.yml`). Il peut aussi être
servi **par KuberWeaver lui-même** via la brique `web-nginx`, ce qui en fait une
démonstration vivante. Deux freins honnêtes :

- la brique sait servir un document root, mais il n'existe **aucun canal de
  contenu** dans le provider (pas de champ pour pousser des fichiers). Il faut
  passer par un rôle upstream qui va chercher une archive épinglée, ou créer une
  petite brique dédiée. C'est écrit dans `docs/STATIC_SITE.md`.
- rien n'a été déployé ni enregistré comme preuve. La doc le dit.

## 5. Ce qui a été livré (vérifiable)

| Élément | Fichier |
| --- | --- |
| Contribution externalisée | `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`, `CODEOWNERS` |
| Templates GitHub | `.github/ISSUE_TEMPLATE/*`, `.github/PULL_REQUEST_TEMPLATE.md` |
| Licence | `LICENSE` (GPL-3.0), `COMMERCIAL-LICENSE.md` |
| Site | `site/index.html`, `site/assets/*`, `site/README.md`, workflow Pages |
| Auto-hébergement du site | `docs/STATIC_SITE.md` |
| Discord (structure prête) | `docs/DISCORD.md` |
| Docs périmées corrigées | `docs/THEMING.md`, `NAVIGATION.md`, `SERVICE_STUDIO.md`, `IMPLEMENTATION.md`, `CHANGELOG.md` |
| Labels | `plugin`, `brick`, `extension`, `kernel`, `cli`, `ci`, `security`, `needs-review`, `triage` |

Non livré, volontairement : `AGENTS.md` (bloqué par le contrôle d'accès de
l'environnement), le serveur Discord (nécessite ton compte), la publication du
dépôt (décision t'appartenant).

## 6. Prochaines étapes conseillées

1. Ouvrir le repo en public, **après** un scan d'historique complet
   (`secret_scan.py --history`) et un passage de `gitleaks --history`.
2. Première release taggée `v0.1.0` depuis `develop`, avec le changelog déjà prêt.
3. Inviter 3 à 5 personnes à tester l'installation sur une machine propre.
   Chaque blocage devient une issue étiquetée `good first issue` : c'est comme
   ça qu'on construit la liste des tâches faciles.
4. Créer le serveur Discord quand il y a au moins une release et un premier
   contributeur.
5. Décider du CLA avant le premier don de code externe.

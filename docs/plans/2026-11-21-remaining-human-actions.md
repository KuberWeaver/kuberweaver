# Ce qui reste à faire (humain uniquement)

Trois actions ne peuvent pas être faites par un agent. Tout le reste est livré.

## 1. `AGENTS.md` (racine) : écriture bloquée dans l'environnement

Le système a refusé d'écrire `AGENTS.md` (chemin protégé des instructions
d'agent). Le contenu est prêt: copie `docs/AGENT_GUIDE.md` à la racine sous le nom
`AGENTS.md`, ou plus simplement:

```sh
cd ~/Desktop/infra-platform-lab/Prototype/kuberweaver
cp docs/AGENT_GUIDE.md AGENTS.md
git add AGENTS.md && git commit -m "chore: add root agent instructions"
```

`AGENTS.md` deviendrait alors l'instruction canonique (elle est courte, impérative,
orientée action). `docs/AGENT_GUIDE.md` reste la version longue.

## 2. Serveur Discord : il faut un compte

La structure, les salons, les rôles et les règles sont dans `docs/DISCORD.md`. Un
bot ne peut pas créer de serveur: il faut un compte Discord. Une fois créé,
colle l'invitation permanente dans `README.md` (section Community), `SUPPORT.md`
(première entrée de « Where to ask ») et `site/index.html` (footer).

Tant que le serveur n'existe pas, le README dit honnêtement qu'il n'y a pas encore
d'invitation. Ne mets pas de lien inventé.

## 3. Rendre le dépôt public : décision t'appartenant

Le dépôt est privé. Le rendre public est irréversible comme décision (l'historique
reste accessible par ceux qui l'ont vu). Avant de le faire, deux vérifications
déjà faites ici et à refaire:

```sh
python3 ops/ci/secret_scan.py --history   # doit dire "secret scan clean"
```

Fait: historique propre, aucune donnée sous `data/` ou `docs/prompt/` n'a jamais
été suivie, identités de commit cohérentes, 9 commits en « E » = erreurs de clé de
signature sur des merges (pas des secrets).

Ensuite:

```sh
gh repo edit EMRD95/kuberweaver --visibility public --accept-visibility-change-consequences
gh repo view EMRD95/kuberweaver --json visibility,isPrivate
```

À faire juste après: activer GitHub Pages (Settings > Pages > source: GitHub Actions)
pour que `.github/workflows/pages.yml` déploie `site/`.

## 4. Optionnel mais recommandé avant le premier contributeur externe

Un CLA (contributor license agreement) ou une cession de droits. Sans lui, tu ne
peux pas vendre de licence commerciale sur le code apporté par autrui. Le sujet
est décrit dans `COMMERCIAL-LICENSE.md` et `CONTRIBUTING.md`. À traiter quand le
premier PR arrive, pas avant.

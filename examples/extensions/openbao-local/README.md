# OpenBao local, optionnel, pour les nouvelles connexions V2

Ce fournisseur distinct est enregistré sous `secrets/openbao-local`. Une nouvelle
connexion doit choisir explicitement `secrets_provider: "openbao-local"` ; ne pas
modifier la liaison globale `data/config/providers.json`. Aucun secret existant
n'est migré, aucun mot de passe ESXi n'est créé ou recherché. Le coffre distant,
GNS3 et son tunnel SSH ne sont jamais appelés par ce setup.

## Périmètre et limites

- Image officielle **2.7.2**, épinglée dans le script au digest
  `openbao/openbao@sha256:5046911075e137e154ce49ab051502f4ec7cf367d484ae4a4dce665d9b90561f`.
- Seul conteneur autorisé : `kw-openbao-v2`, avec étiquette de propriété stable
  liée au chemin du projet. Docker est ciblé explicitement sur le socket local
  `/var/run/docker.sock`, pas sur un contexte distant hérité de l'environnement.
- Port hôte `127.0.0.1:19200` par défaut (`--port`, entier de 1024 à 65535).
  HTTP de laboratoire, **pas de TLS de bout en bout**. Le serveur écoute sur le
  réseau Docker ; la publication hôte en loopback n'isole pas tous les autres
  conteneurs du bridge. L'administration ignore les proxies et les redirections.
- Raft persistant chiffré par OpenBao, sans mode `-dev`, sans `IPC_LOCK`, sous
  l'UID/GID de l'opérateur. `--cap-drop ALL`, `no-new-privileges`, configuration
  montée en lecture seule. L'entrypoint direct est `/usr/bin/bao` : c'est le chemin
  du binaire indiqué par le [Dockerfile officiel v2.7.2](https://github.com/openbao/openbao/blob/v2.7.2/Dockerfile).
- Aucun conteneur étranger n'est démarré, redémarré ou supprimé. Un conteneur
  existant doit avoir exactement l'image, les montages et le durcissement attendus.
  Les valeurs Docker `NetworkMode=default` et `NetworkMode=bridge` désignent ici
  le bridge par défaut et sont acceptées ; les autres réseaux restent refusés.
  Un conteneur déjà actif n'est pas redémarré. La politique de redémarrage est `no`.
- Le fournisseur applicatif n'a que `read/create/update` sur
  `secret/data/kuberweaver/connections/*`. Pas de `list`, `delete` ou privilège
  d'administration. Jeton orphelin sans politique `default`, non renouvelable,
  TTL demandé et maximum explicite de 168 h. Un jeton valide est conservé.
- La référence exacte `setup-check` est réservée au diagnostic interne : le
  fournisseur `secrets/openbao-local` la refuse en lecture comme en écriture,
  avant tout appel réseau. Choisir une autre référence pour les identifiants des
  connexions. Le script conserve son propre accès au marqueur ; cette réservation
  ne s'applique pas aux autres fournisseurs de secrets.

Ce montage mono-nœud est destiné au laboratoire : pas de HA, d'auto-unseal ni de
certification cryptographique. Les administrateurs Docker et l'utilisateur hôte
restent de confiance. Les tests automatisés utilisent des fixtures de protocole ;
ils ne sont pas une preuve d'exécution de l'image réelle.

## Fichiers privés

Avec `--state-dir "$STATE"` (défaut : `data/` à la racine du projet) :

| Emplacement relatif | Protection / contenu |
|---|---|
| `local-bao/`, `local-bao/raft/` | Répertoires `0700`, données persistantes |
| `local-bao/config.hcl` | Fichier `0600`, configuration contrôlée |
| `local-bao/bootstrap.json` | `0600`, jeton root et trois parts de déscellement |
| `local-bao/app-token` | `0600`, seul jeton consommé par le fournisseur |
| `local-bao/pending-revocation.json` | `0600`, ancien jeton et candidat pendant une rotation ; `{}` après révocation vérifiée |
| `plugin-data/openbao-local/provider.json` | `0600`, configuration locale du fournisseur |

**Le bootstrap contient les clés de récupération en clair**, protégées par le
système de fichiers. Les trois parts sont détenues sur le même hôte : le seuil
2/3 n'est donc pas une séparation entre gardiens indépendants. Conserver une
sauvegarde privée et cohérente du bootstrap et de Raft ; ne jamais les afficher,
les copier dans Git, les joindre à une preuve ou les publier dans un journal.
Les écritures de fichiers sont atomiques, synchronisées et relues ; les liens
symboliques, les fichiers partagés par liens physiques et les permissions trop
larges sont refusés, pas réparés silencieusement.

Si Raft préexiste sans bootstrap, ou si le serveur est initialisé sans bootstrap
local, le script s'arrête pour récupération manuelle. **Ne pas supprimer Raft ni
réinitialiser le coffre pour contourner ce refus.** Une erreur après initialisation
peut nécessiter une intervention manuelle ; aucun nettoyage destructif n'est tenté.

## Recette opérateur, commandes à exécuter après revue

Depuis la racine du dépôt, avec Docker local disponible. Ces commandes ne lancent
aucun lab GNS3 ; `setup` démarre uniquement le nouveau conteneur local ou son propre
conteneur compatible arrêté.

```bash
STATE="$PWD/data"  # ou un répertoire durable dédié à cette instance du portail
.venv/bin/python scripts/setup_local_bao.py setup --state-dir "$STATE" --port 19200
.venv/bin/python scripts/setup_local_bao.py verify --state-dir "$STATE" --port 19200
# Répéter setup : même conteneur et même jeton valide, nouveau marqueur aléatoire.
.venv/bin/python scripts/setup_local_bao.py setup --state-dir "$STATE" --port 19200
```

Le JSON de succès doit indiquer `initialized: true`, `sealed: false`, le digest
épinglé, `setup_roundtrip: true` pour `setup`, et les refus HTTP 403 vérifiés hors
namespace, pour l'administration, la suppression et l'énumération. `verify` ne
réécrit pas de marqueur ni de configuration : `setup_marker_read: true` et
`setup_roundtrip: false` y distinguent lecture et aller-retour d'écriture.
`credentials_written: false` signifie qu'aucun identifiant de connexion réel n'a
été écrit ; le bootstrap et le jeton applicatif sont bien des fichiers privés.

Le script **n'installe ni n'active l'extension**. Après revue de son code :

```bash
.venv/bin/python scripts/plugins.py --data-dir "$STATE" doctor examples/extensions/openbao-local
.venv/bin/python scripts/plugins.py --data-dir "$STATE" install examples/extensions/openbao-local
.venv/bin/python scripts/plugins.py --data-dir "$STATE" enable openbao-local --trust
```

Installer une seule fois ; l'installateur refuse un remplacement implicite.
Redémarrer ensuite le portail avec son mécanisme habituel et le **même répertoire
d'état**. Choisir le fournisseur local uniquement dans les nouvelles connexions
V2. Une configuration absente donne `unconfigured`, une configuration invalide
`unavailable`, sans faire échouer l'enregistrement du portail. `configured` décrit
une configuration valide, pas une sonde réseau effectuée au chargement.

Après un redémarrage du conteneur existant, déjà actif mais scellé :

```bash
.venv/bin/python scripts/setup_local_bao.py unseal --state-dir "$STATE" --port 19200
```

Pour un conteneur arrêté, utiliser `setup` : seul ce mode peut le démarrer. Si le
jeton a expiré ou doit être remplacé, la rotation est explicite ; elle vérifie le
nouveau jeton, le sauvegarde, puis révoque l'ancien et contrôle sa révocation :

```bash
.venv/bin/python scripts/setup_local_bao.py setup --state-dir "$STATE" --port 19200 --rotate-token
```

Avant de remplacer `app-token`, la rotation enregistre durablement l'ancien jeton
et le candidat dans `pending-revocation.json`, sous le répertoire privé `0700`.
Après une erreur ou une interruption, relancer **`setup` sans `--rotate-token`**
avec le même état et le même port : il reprend cette rotation, conserve le candidat
et vérifie le refus de l'ancien jeton avant tout succès. Une révocation déjà
effectuée est récupérable. `verify` reste en lecture seule et refuse une rotation
en attente ; il ne la termine pas. Ne pas supprimer ni publier le journal privé.
Cette reprise protège la référence de l'ancien jeton ; elle ne garantit pas une
transaction distribuée sur la création du candidat, notamment avant que celui-ci
ait pu être enregistré dans le journal.

## État de la recette Docker réelle

La correction du bridge a été vérifiée en RED puis GREEN sur l'inspection du
conteneur détenu, ainsi que par les tests automatisés. La recette réelle reste
**bloquée**, et non validée : après archivage privé du Raft partiel dont l'API
confirmait `initialized: false`, le setup normal a initialisé le coffre sans
produire de `bootstrap.json` ni de jeton applicatif. Le serveur indiquait ensuite
`initialized: true`. Le délai client de cinq secondes pendant l'initialisation est
une cause possible, pas une garantie de diagnostic.

Le seul conteneur détenu a été arrêté, son Raft initialisé préservé et aucune
réinitialisation tentée. `verify`, la politique applicative et l'idempotence réelle
ne sont donc pas certifiés. La preuve expurgée, l'identifiant exact et le chemin
de l'archive sont dans `data/evidence/v2-local-bao-recipe.json`. Une intervention
explicitement autorisée est nécessaire ; ne pas supprimer cet état pour relancer
la recette. Aucune extension ni liaison globale n'a été activée ou modifiée.

## Tests sans Docker ni données réelles

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_local_bao.py -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_local_bao.py tests/test_secrets.py tests/test_secret_writes.py \
  tests/test_extensions.py tests/test_provider_bindings.py -q
```

Les appels Docker sont injectés à la frontière `subprocess.run`. Le serveur HTTP
loopback de test exerce le véritable `OpenBaoProvider.store/resolve`, les refus de
redirection et de privilèges, les erreurs et la conservation des fichiers privés.
Aucune commande Docker réelle n'est exécutée par ces tests locaux.

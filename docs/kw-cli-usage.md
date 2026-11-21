# kw — CLI KuberWeaver

Client mince de l'API KuberWeaver. Une seule porte : les mêmes routes, rôles et pins que la GUI.
Aucune logique métier, aucune écriture directe en base, aucun contournement d'approbation.
Spécification : `docs/cahier-des-charges-kw-cli.md`.

## Installation / invocation

```
uv pip install --python .venv/bin/python -e . --no-deps   # fournit la commande `kw`
kw --help
# ou sans installation :
.venv/bin/python -m kw --help
```

## Configuration

| Variable / fichier | Rôle |
| --- | --- |
| `KW_URL` | URL de l'API (défaut `http://127.0.0.1:8088`). Sinon `--url` ou `config.json`. |
| `KW_CONFIG_DIR` | Répertoire de config (défaut `~/.config/kuberweaver`, 0700). |
| `KW_PASSWORD` / `KW_PASSWORD_FILE` | Mot de passe (jamais en argument de commande). Sinon prompt. |
| `KW_AGENT_TOKEN` | Jeton agent scopé pour l'automation (mode machine). |

## Authentification

Humain (session cookie + CSRF, persistée dans un fichier 0600, effacée au logout) :
```
kw auth login --username admin
kw auth whoami
kw auth logout
```
Automation (jeton agent scopé ; ne peut ni approuver, ni rollback, ni décommissionner) :
```
export KW_AGENT_TOKEN=...
kw deploy list
```

## Parcours (spécification section 4)

```
# 1. Clé SSH -> vault (la clé privée ne sort jamais en clair)
kw keys generate --name deploy-key
kw keys list
kw keys revoke <key-id>
kw keys export <key-id> --output ./deploy.key      # ré-authentification admin, fichier 0600

# 2-3. Connecteur : infos de connexion, secrets dans le vault
kw connectors drivers
kw connectors add --label "ESXi lab" --driver esxi --endpoint https://esxi.lab \
    --secret-ref hypervisors/lab --secrets-provider local-vault
kw connectors credentials <conn-id> --username root     # mot de passe via env/prompt
kw connectors test <conn-id>
kw connectors discover <conn-id>

# 5. Module : artefact épinglé (métadonnées) puis offre immuable
kw modules artifacts create --kind oci --reference registry.lab/web:1.0.0 --pin sha256:...
kw blueprints create --name "Stack web" --graph-file graph.json
kw modules offerings create --title "Stack web" --blueprint-id <bp-id>

# 6-7. Ressource : import d'un plan OpenTofu épinglé, ou adoption d'une VM déjà possédée
#      (le formulaire --default n'a pas de route API ; sécurité du code importé = utilisateur)
kw resources create --import --environment dev --stack web \
    --revision <git-commit> --bundle-path bundles/web --bundle-sha256 <sha256> --dry-run
kw resources adopt --environment dev --stack web --connection-id <conn-id> \
    --uuid <vm-uuid> --marker kuberweaver-job:<job-id>
kw resources status

# 8. Blueprint (édition / copie / duplication)
kw blueprints list
kw blueprints copy <bp-id> --name "Stack web v2"

# 9-10. Demande de déploiement puis validation humaine
kw deploy request --offering-id <off-id>        # ou --blueprint-id / --graph-file
kw deploy validate --graph-file graph.json      # dry-run, ne crée pas de job
kw deploy approve <job-id>                      # admin humain uniquement
kw deploy watch <job-id>

# 11-12. Inventaire et logs
kw inventory list
kw inventory get <state-id>
kw logs <job-id> --follow
kw audit

# 13. Rollback (mutation humaine gouvernée)
kw rollback <job-id>

# 14. Décommissionnement par identifiant de déploiement, isolé
kw services decommission <job-id>               # service retiré vérifié
kw decommission request <state-id> --connection-id <c> --uuid <u> --marker <m>
kw decommission execute <state-id> --destroy-id <d>   # après la fenêtre de quarantaine
kw decommission status <state-id>

# 15. Données conservées pour analyse / FinOps
kw finops
kw finops overview
kw finops reporting
```

## Sortie et codes de retour

- Table par défaut ; `--json` pour l'automatisation ; `-v` pour la verbosité.
- Codes de sortie : 0 succès, 1 erreur API, 2 authentification requise, 3 permission refusée (rôle/CSRF).
- Les mutations destructrices (approve, rollback, decommission, keys revoke/export) exigent une session
  admin humaine ; un jeton agent est refusé.

## Gaps backend assumés (pas de stub côté client)

Ces capacités n'ont pas de route API à ce jour ; le CLI les marque « unavailable » :
- `modules artifacts get` / `modules offerings get` (pas de GET par id) et le coût/mois par module
  (l'offre stocke une estimation interne à la publication mais aucune route ne la retourne).
- `resources create --default` (formulaire) et `resources add` (ajout à une stack existante).
L'injection des clés SSH à la création est le rôle d'OpenTofu, pas du CLI.

## Invariants de gouvernance

Une seule porte (API) ; approbation humaine avant exécution ; pins de source/plan ; vérification
indépendante et retrait vérifié ; worker sérialisé non-rejeu ; secrets jamais dans plans/logs/réponses ;
PostgreSQL requis en production ; propriété des ressources et décommissionnement isolé par id.

## Tests

```
.venv/bin/python -m pytest tests/test_cli.py tests/test_cli_journey.py -q -p no:cacheprovider
```
Intégration in-process : vrai serveur uvicorn local (port éphémère), SQLite isolé, plugin
`sample-service`, vault local chiffré réel. Seules les frontières provider sont simulées
(exécuteurs d'enregistrement, hyperviseur lecture/écriture). Le test e2e chaîne tout le parcours.

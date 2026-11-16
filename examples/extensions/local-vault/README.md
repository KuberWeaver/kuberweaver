# Coffre local Fernet (optionnel)

Aucun Docker, service réseau, base de données ou nouvel orchestrateur. Le connecteur
OpenBao reste indépendant et disponible ; ne pas supprimer son conteneur ni son Raft.

Depuis la racine du dépôt :

```sh
.venv/bin/python -m pip install -r requirements-local-vault.txt
.venv/bin/python scripts/setup_local_vault.py --data-dir "$PWD/data"
# Facultatif : accepte le Python de confiance de CETTE extension seulement.
.venv/bin/python scripts/setup_local_vault.py --data-dir "$PWD/data" --install
```

Sans `--install`, aucune installation/activation ni liaison globale n'est modifiée.
L'hôte utilise `data/plugin-data/local-vault` (via `ctx.plugin_data_dir()`), et non
`data/plugins`. La clé `key`, le marqueur authentifié `check` et les fichiers
`sha256(reference).fernet` sont séparés des sources `data/extensions/local-vault`.
Le parent de `--data-dir` doit déjà exister. Les répertoires privés sont 0700 et
les fichiers 0600. Une installation existante différente est refusée, jamais écrasée.
L'activation respecte `data/config/extensions.json` s'il est l'unique autorité.
Redémarrer l'application après installation ; un CLI n'atteste pas le runtime actif.

Sélectionner explicitement la source de secrets `local-vault` pour les nouvelles
connexions. Les contrôles humains/agents restent ceux de l'API de connexions de
l'hôte ; l'extension n'ajoute pas une route d'écriture parallèle. Aucun binding
secrets global n'est modifié. Le fournisseur Python est **lecture seule par défaut** ;
`allow_write=True` doit être un booléen explicite. L'extension activée autorise le
stockage des nouvelles connexions à travers les contrôles de l'hôte.

Les références suivent le contrat logique de l'hôte (128 caractères maximum).
Les valeurs binaires non vides sont limitées à 128 KiB avant toute écriture.
Fernet authentifie aussi la référence dans le texte chiffré : substituer deux
fichiers de références différentes échoue. Lectures et métadonnées ne créent rien.
L'hôte peut créer le répertoire de plugin lors de l'enregistrement, mais jamais la clé.
Les IO utilisent des descripteurs relatifs NOFOLLOW, y compris les ancêtres ; les
liens et propriétaires autres que root ou l'utilisateur courant sont refusés.
Les ancêtres appartenant à root ou à cet utilisateur peuvent être 0775 : cela
ne signifie pas que leur groupe est de confiance. Avant une barrière 0700 de
l'utilisateur courant, les ancêtres accessibles en écriture à tous sont refusés.
Le coffre final exige toujours exactement 0700 et l'utilisateur courant ; clé et
fichiers exigent 0600, le même utilisateur et un seul lien physique. Un membre du
groupe pouvant renommer un ancêtre ne peut ni traverser le coffre privé existant,
ni rediriger les IO déjà épinglées sur ses descripteurs, ni fournir un coffre lui
appartenant. Il peut cependant déplacer/supprimer un chemin, provoquer un déni de
service ou présenter un ancien coffre valide du même propriétaire : disponibilité
et anti-rollback ne sont pas garantis. Aucun mode des ancêtres n'est modifié.
Un ancêtre propriétaire 0700 constitue une barrière pour les descendants. Verrous `flock`
Linux, fichiers temporaires exclusifs et remplacement atomique sérialisent les
écritures ; le résultat est relu et vérifié, avec restauration sur échec de vérification.
Une interruption avant publication préserve l'ancienne valeur ; une perte de
courant après publication mais avant confirmation nécessite inspection manuelle.
Clé manquante avec données, mauvaise clé ou ciphertext altéré : refus, pas de reset.

## Limites et sauvegardes

La clé locale à côté des données **ne protège pas contre un compte OS ou root
compromis**. Sauvegarder **ensemble et privément** clé, marqueur et données ; ne
jamais les committer ou afficher. La rotation de clé, l'anti-rejeu d'une ancienne
valeur de la même référence, les ACL multiutilisateurs, l'effacement garanti de la
mémoire Python et un protocole Vault ne sont pas implémentés. Les noms hachés et
le chiffrement ne cachent pas complètement les métadonnées (taille, date Fernet).
Pour des garanties centralisées, sélectionner le fournisseur OpenBao existant.

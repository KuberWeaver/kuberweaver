# Présentation investisseurs KuberWeaver

- `KuberWeaver_Presentation_Investisseurs.pptx` : présentation éditable, 14 slides.
- `KuberWeaver_Presentation_Investisseurs.pdf` : version de lecture.
- `NOTES_PRESENTATION.md` : notes orateur, références et limites.
- `build_presentation.py` : source commune des deux exports.

## Régénérer sous Linux

Créer un environnement Python, installer `requirements-presentation.txt`, puis exécuter `python build_presentation.py`. La police locale Noto Sans et `fc-match` sont nécessaires. Le script retrouve le schéma original dans `../../docs/cadrage-original/`. Les autres images sont dans `assets/`, aucun chargement réseau n’est effectué par le générateur.

Le PDF est généré directement, pas converti du PowerPoint. Le texte du PPTX est éditable. Les sources sont indiquées dans les notes de chaque slide et dans le document des notes. Aucune donnée client, traction commerciale ou économie chiffrée n’est inventée.

Le lien Catbox fourni était inaccessible comme image, réponse vide. La slide de structure utilise le schéma original local du projet, sans affirmer qu’il est identique au fichier distant. Le schéma illustre la vision cible, pas la totalité des capacités livrées.

Avant une diffusion publique/commerciale, confirmer les droits de réutilisation des illustrations tierces. Aucun push GitHub n’a été effectué lors de la création de cette présentation.

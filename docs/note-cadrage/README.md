# Documents courts du projet

- KuberWeaver_note_de_cadrage.pdf : deux pages avec une infographie du parcours.
- KuberWeaver_note_de_cadrage.txt : texte modifiable de cette note.
- ../CAHIER_DES_CHARGES_KUBERWEAVER.pdf : cahier des charges simplifié.
- ../CAHIER_DES_CHARGES_KUBERWEAVER.md : source principale du cahier des charges.
- ../ARCHITECTURE_CIBLE.md : propositions sur PostgreSQL, les services et les plugins, non encore implémentées.

L’ancien cahier des charges est conservé dans ../archives/. L’export ../cadrage-mentor/ reste historique et n’est pas synchronisé avec la version simplifiée.

## Régénérer les PDF

Depuis la racine du dépôt, avec un environnement documentaire distinct de celui de l’application :

```sh
python3 -m venv .venv-docs
.venv-docs/bin/pip install reportlab pymupdf
.venv-docs/bin/python docs/note-cadrage/generer_note.py
.venv-docs/bin/python docs/generer_cahier_des_charges.py
```

Les scripts utilisent les polices DejaVu installées dans /usr/share/fonts/truetype/dejavu/ sous Linux. Adapter ce chemin sur un autre système. Les aperçus PNG servent au contrôle visuel local et sont exclus de Git ; les PDF finaux sont publiés.

La note conserve son propre texte dans generer_note.py ; après modification de ce script, régénérer simultanément le PDF et le TXT. Le cahier des charges PDF est généré directement depuis son Markdown.

Aucun secret, compte local, fichier SQLite ou état privé du laboratoire n’est nécessaire pour lire ces documents.

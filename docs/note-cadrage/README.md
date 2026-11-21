# Project presentation documents

- `KuberWeaver_note_de_cadrage.pdf`: historical two-page brief with a workflow infographic.
- `KuberWeaver_note_de_cadrage.txt`: editable text of that brief.
- `../CAHIER_DES_CHARGES_KUBERWEAVER.pdf`: generated requirements presentation.
- `../CAHIER_DES_CHARGES_KUBERWEAVER.md`: current requirements source.
- `../ARCHITECTURE_CIBLE.md`: architecture direction, distinguishing the delivered PostgreSQL migration from proposed service lifecycle and plugin work.

The old requirements are retained in `../archives/`. `../cadrage-mentor/` is a historical export not synchronized with the simplified wording. The English PDF/TXT brief retains explicitly historical evidence. The requirements PDF is regenerated from its current English Markdown source; both generators and extracted output have been checked.

## Regenerate PDFs

From the repository root, using a documentation environment separate from the application:

```sh
python3 -m venv .venv-docs
.venv-docs/bin/pip install reportlab pymupdf
.venv-docs/bin/python docs/note-cadrage/generer_note.py
.venv-docs/bin/python docs/generer_cahier_des_charges.py
```

The scripts use Linux DejaVu fonts under `/usr/share/fonts/truetype/dejavu/`; adapt that path on another OS. PNG previews are for local visual inspection and ignored by Git; final PDFs are published artifacts.

The brief embeds its own text in `generer_note.py`; updating that source requires regenerating PDF and TXT together. The requirements PDF is generated from Markdown. Review generated output before publication, including language and stale claims.

No secret, local account, SQLite file or private lab state is required to read these documents.

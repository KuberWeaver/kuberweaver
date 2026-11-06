from pathlib import Path
import json
import zipfile
from pptx import Presentation
import fitz

root = Path(__file__).resolve().parent
pptx = root / 'KuberWeaver_Presentation_Investisseurs.pptx'
pdf = root / 'KuberWeaver_Presentation_Investisseurs.pdf'
prs = Presentation(pptx)
manifest = json.loads((root / 'slides-manifest.json').read_text())
checks = json.loads((root / 'layout-checks.json').read_text())
assert len(prs.slides) == len(manifest) == 14
assert zipfile.ZipFile(pptx).testzip() is None
assert all(item['required_h'] <= item['box'][3] + .01 for item in checks)
pictures = 0
all_text = ''
for index, slide in enumerate(prs.slides):
    assert slide.has_notes_slide
    assert slide.notes_slide.notes_text_frame.text.strip()
    for shape in slide.shapes:
        assert shape.left >= 0 and shape.top >= 0
        assert shape.left + shape.width <= prs.slide_width + 500
        assert shape.top + shape.height <= prs.slide_height + 500
        if shape.shape_type == 13:
            pictures += 1
        if shape.has_text_frame:
            all_text += '\n' + shape.text
assert pictures == 2
names = ['Etienne MURZEAU', 'Rayan CHOUCHANE', 'Alex YAO', 'Vidjay VELAYOUDAM', 'Abdel-Waheb SAKKAL', 'Priyank SOLANKI']
assert all(name in all_text for name in names)
assert '\u2014' not in all_text
pdfdoc = fitz.open(pdf)
assert len(pdfdoc) == 14
pdf_text = ''
for i, page in enumerate(pdfdoc):
    assert abs(page.rect.width/page.rect.height - 16/9) < .001
    for block in page.get_text('blocks'):
        if len(block) > 6 and block[6] == 0:
            assert block[0] >= 0 and block[1] >= 0, (i, block)
            assert block[2] <= page.rect.width + 1 and block[3] <= page.rect.height + 1, (i, block)
    pdf_text += page.get_text()
assert all(name in pdf_text for name in names)
result = {
    'slides': len(prs.slides),
    'pdf_pages': len(pdfdoc),
    'editable_text_boxes_checked': len(checks),
    'embedded_images': pictures,
    'speaker_notes': 'present on all slides',
    'all_team_members_present': True,
    'pptx_structure_and_bounds': 'PASS',
    'pdf_text_bounds': 'PASS',
    'pdf_visual_review': '14 rendered pages inspected through two contact sheets',
    'pptx_rendering': 'PPTX structure verified; PDF generated from common geometry, not a LibreOffice rendering of PPTX',
    'catbox_image': 'empty response; substituted with explicitly attributed local original project diagram',
    'source_attribution': 'in slide notes and NOTES_PRESENTATION.md',
    'commercial_claims': 'vision, prototype and hypotheses explicitly distinguished',
    'github_push': False,
    'files': {f.name: f.stat().st_size for f in [pptx, pdf]},
}
(root / 'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))

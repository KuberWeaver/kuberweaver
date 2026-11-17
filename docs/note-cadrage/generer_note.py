"""Short brief with a vertical vector workflow for non-specialist readers.
Sources: initial vision, README, COMPLETION_STATUS (historical 6 November 2026 acceptance).
No private data. PDF and text are generated together.
"""
from pathlib import Path
import re
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
import pymupdf as fitz

OUT = Path(__file__).resolve().parent
FONT = Path('/usr/share/fonts/truetype/dejavu')
for name, file in [('Regular','DejaVuSans.ttf'),('Bold','DejaVuSans-Bold.ttf')]:
    pdfmetrics.registerFont(TTFont(name, str(FONT / file)))
W,H = 595.276,841.89
INK='#172C40'; MUTED='#526577'; TEAL='#087F82'; LIGHT='#EDF6F5'
PDF=OUT/'KuberWeaver_note_de_cadrage.pdf'
c=canvas.Canvas(str(PDF),pagesize=(W,H))
c.setTitle('KuberWeaver | Project brief')
c.setAuthor('Project team, AI-assisted writing')
texts=[]
def p(text,x,y,w=499,size=10.5,bold=False,color=INK,leading=None):
    style=ParagraphStyle('s',fontName='Bold' if bold else 'Regular',fontSize=size,leading=leading or size*1.45,textColor=HexColor(color))
    para=Paragraph(text,style); _,h=para.wrap(w,700)
    assert y+h<(818 if y>=798 else 794), (text,y,h)
    para.drawOn(c,x,H-y-h)
    texts.append(re.sub('<[^>]+>',' ',text))
    return y+h

def base(num,label):
    c.setFillColor(HexColor(TEAL)); c.rect(0,H-9,W,9,fill=1,stroke=0)
    p('KUBERWEAVER  /  PROJECT BRIEF',48,29,size=9,bold=True,color=TEAL)
    p(label,48,60,size=25,bold=True)
    c.setStrokeColor(HexColor('#DCE4E9')); c.line(48,53,W-48,53)
    p('EFREI Paris · Proposal subject to approval · AI-assisted writing',48,798,w=455,size=7,color=MUTED)
    c.setFont('Regular',8); c.setFillColor(HexColor(MUTED)); c.drawRightString(W-48,31,f'{num} / 2')

def section(title,text,y):
    y=p(title,48,y,size=12,bold=True)
    return p(text,48,y+5)+15

base(1,'Deploy without guesswork')
p('An internal platform to request an IT service,<br/>obtain approval and verify its deployment.',48,107,size=12,color=MUTED)
y=section('01  The need',
    'Installing a server or changing an environment often involves several tools and manual procedures. KuberWeaver turns reviewed organizational procedures into reusable services available through one portal.',164)
y=section('02  Who is it for?',
    'Operators prepare requests. Administrators check permissions, policy and estimated cost before authorizing execution. Viewers inspect results. The aim is less repetition, clear responsibilities and a record of each change.',y)
y=p('03  From request to result',48,y,size=12,bold=True)+29
steps=[
 ('Compose','Choose a catalogue service and enter its parameters.'),
 ('Check','Validate policy and display an estimated cost.'),
 ('Approve','Have an administrator review the exact plan before execution.'),
 ('Deploy','Back up, test on a first target, then continue where supported.'),
 ('Verify','Check the resulting service and retain evidence of the outcome.'),
]
for i,(title,desc) in enumerate(steps):
    top=y+i*49
    c.setFillColor(HexColor(LIGHT)); c.roundRect(48,H-top-41,499,41,7,fill=1,stroke=0)
    c.setFillColor(HexColor(TEAL)); c.circle(68,H-top-20.5,12,fill=1,stroke=0)
    c.setFont('Bold',10); c.setFillColor(white); c.drawCentredString(68,H-top-24,str(i+1))
    p(title,91,top+4,w=430,size=10,bold=True)
    p(desc,91,top+20,w=430,size=8.5)
    if i<4:
        c.setStrokeColor(HexColor(TEAL)); c.line(68,H-top-42,68,H-top-48)
        c.line(65,H-top-45,68,H-top-48); c.line(71,H-top-45,68,H-top-48)
y+=249
p('If verification fails, attempt service-appropriate recovery only where supported. Uncertain recovery requires human intervention; create-only provisioning is not universal rollback.',48,y,size=9.5,bold=True,color=TEAL)
c.showPage()
base(2,'A controlled scope')
y=section('04  The evidence baseline',
 'The historical report dated 6 November 2026 records real NGINX and Apache web-server deployments in a virtualized lab, including approval, checks and previous-version restoration. These results are historical evidence, not a new acceptance run or production certification.',111)
y=section('05  Proposed next work',
 'Consolidate this workflow and add a different service category to exercise modularity. Plugins add capabilities without rewriting the platform core.<br/><br/>'
 'VM provisioning follows separate contracts: OVF import is create-only; blank-disk paths retain their documented blockers. Local tests do not prove guest OS or SSH startup. Cisco, pfSense and cloud integrations still require real-target acceptance.',y)
y=section('06  Limits that remain visible',
 'Displayed cost is an internal estimate, not an invoice. Dependency edges do not configure networks. Despite its name, KuberWeaver does not orchestrate Kubernetes.<br/><br/>'
 'An external AI agent may use the platform with limited rights explicitly delegated by an administrator. No embedded AI model is required. Agents remain subject to the same access and approval controls as portal users.',y)
y=section('07  How success is assessed',
 'Acceptance must show a forbidden request blocked, an approved service actually available, independently verified recovery where supported, and a new service added without core business changes. Deliverables include the platform, installation procedures and evidence of these checks.',y)
y=section('08  Decisions still required',
 'Choose the next service, confirm task and review responsibilities, then agree dates and available resources. These commitments require human approval.',y)
p('Rollback means returning to a previous state where supported; it is not a guarantee that every provider can undo every change.',48,y,w=499,size=8.5,color=MUTED)
p('Sources: original vision, project README and docs/COMPLETION_STATUS.md.<br/>Reported evidence only; no new technical acceptance tests were run for this brief.',48,752,w=499,size=7.5,color=MUTED)
c.save()
text='KuberWeaver project brief\n\n'+'\n\n'.join(texts)+'\n'
(OUT/'KuberWeaver_note_de_cadrage.txt').write_text(text,encoding='utf-8')
doc=fitz.open(PDF)
assert len(doc)==2
for i,page in enumerate(doc):
    assert page.get_text().strip()
    for b in page.get_text('blocks'):
        assert b[0]>=0 and b[1]>=0 and b[2]<=W+1 and b[3]<=H+1,b
    page.get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(OUT/f'apercu-page-{i+1}.png')
words=len(re.findall(r'\S+',text))
print({'pdf':str(PDF),'pages':len(doc),'words':words,'minutes_at_220_words_per_minute':round(words/220,2),'text_bounds':'OK'})

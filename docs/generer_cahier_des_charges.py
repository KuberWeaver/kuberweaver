"""Produce the readable PDF from the authoritative Markdown, without duplicate text."""
from pathlib import Path
import re
from xml.sax.saxutils import escape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_LEFT
import pymupdf

ROOT=Path(__file__).resolve().parent
SRC=ROOT/'CAHIER_DES_CHARGES_KUBERWEAVER.md'
PDF=SRC.with_suffix('.pdf')
for name,filename in [('Regular','DejaVuSans.ttf'),('Bold','DejaVuSans-Bold.ttf')]:
    pdfmetrics.registerFont(TTFont(name,'/usr/share/fonts/truetype/dejavu/'+filename))
styles={
 'body':ParagraphStyle('body',fontName='Regular',fontSize=9.5,leading=13,spaceAfter=6,textColor=HexColor('#172C40')),
 'h1':ParagraphStyle('h1',fontName='Bold',fontSize=22,leading=27,spaceAfter=10,textColor=HexColor('#172C40')),
 'h2':ParagraphStyle('h2',fontName='Bold',fontSize=17,leading=22,spaceAfter=12,textColor=HexColor('#087F82'),keepWithNext=True),
 'h3':ParagraphStyle('h3',fontName='Bold',fontSize=11,leading=15,spaceBefore=7,spaceAfter=5,textColor=HexColor('#172C40'),keepWithNext=True),
 'cell':ParagraphStyle('cell',fontName='Regular',fontSize=9,leading=12.5,textColor=HexColor('#172C40')),
 'th':ParagraphStyle('th',fontName='Bold',fontSize=8.5,leading=11.5,textColor=white),
 'small':ParagraphStyle('small',fontName='Regular',fontSize=7.7,leading=10.5,spaceAfter=6,textColor=HexColor('#526577')),
}
def para(text,kind='body'):
    text=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',r'\1 (\2)',text)
    return Paragraph(escape(text),styles[kind])
text=SRC.read_text()
lines=text.splitlines()
story=[]
i=0
page=1
small=False
while i<len(lines):
    line=lines[i].strip()
    if not line:
        i+=1;continue
    if line=='<!-- pagebreak -->':
        story.append(PageBreak());page+=1;i+=1;continue
    if line.startswith('|'):
        rows=[]
        while i<len(lines) and lines[i].startswith('|'):
            cells=[x.strip() for x in lines[i].strip().strip('|').split('|')]
            if not all(re.fullmatch(r'[:\- ]+',x) for x in cells): rows.append(cells)
            i+=1
        widths=([160,339] if page==1 else [71,158,270] if page==2 else [89,139,271] if page==3 else [103,185,211])
        table=Table([[para(cell,'th' if n==0 else 'cell') for cell in row] for n,row in enumerate(rows)],colWidths=widths,repeatRows=1,hAlign='LEFT')
        table.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),HexColor('#087F82')),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[HexColor('#EDF6F5'),white]),
            ('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
            ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
            ('LINEBELOW',(0,0),(-1,0),.5,HexColor('#087F82')),
        ]))
        story.extend([table,Spacer(1,9)])
        continue
    if line.startswith('### '):
        heading=line[4:]
        small=heading=='Terms and references'
        story.append(para(heading,'h3'));i+=1;continue
    if line.startswith('## '): story.append(para(line[3:],'h2'));i+=1;continue
    if line.startswith('# '): story.append(para(line[2:],'h1'));i+=1;continue
    if line.startswith('- ') or re.match(r'^\d+\. ',line):
        story.append(para(('• '+line[2:]) if line.startswith('- ') else line));i+=1;continue
    paragraph=[line];i+=1
    while i<len(lines) and lines[i].strip() and not lines[i].startswith(('#','|','<!--','- ')) and not re.match(r'^\d+\. ',lines[i]):
        paragraph.append(lines[i].strip());i+=1
    story.append(para(' '.join(paragraph),'small' if small or line.startswith(('Version 2.0','Proposal subject to approval')) else 'body'))

def footer(c,doc):
    w,h=doc.pagesize
    c.setFillColor(HexColor('#087F82')); c.rect(0,h-7,w,7,fill=1,stroke=0)
    c.setStrokeColor(HexColor('#DCE4E9')); c.line(48,42,w-48,42)
    c.setFillColor(HexColor('#526577')); c.setFont('Regular',7)
    c.drawString(48,28,'KuberWeaver · Requirements v2.0 · EFREI Paris · Proposal subject to approval')
    c.drawRightString(w-48,28,str(doc.page))

doc=SimpleDocTemplate(str(PDF),pagesize=(595.276,841.89),leftMargin=48,rightMargin=48,topMargin=32,bottomMargin=56,title='KuberWeaver requirements',author='Project team, AI-assisted writing')
doc.build(story,onFirstPage=footer,onLaterPages=footer)
pdf=pymupdf.open(PDF)
expected=set(range(1,25))
ids=[int(s) for s in re.findall(r'\| EXG-(\d{3})',text)]
assert len(ids)==24 and set(ids)==expected,(len(ids),ids)
old=(ROOT/'archives/CAHIER_DES_CHARGES_KUBERWEAVER_v1.3.md').read_text()
for name in ['Etienne MURZEAU','Rayan CHOUCHANE','Alex YAO','Vidjay VELAYOUDAM','Abdel-Waheb SAKKAL','Priyank SOLANKI']:
    assert name in text and name in old,name
for num,p in enumerate(pdf):
    for block in p.get_text('blocks'):
        assert 0<=block[0]<block[2]<=596 and 0<=block[1]<block[3]<=842,block
    p.get_pixmap(matrix=pymupdf.Matrix(1.4,1.4)).save(ROOT/'note-cadrage'/f'cahier-apercu-{num+1}.png')
words=len(text.split());old_words=len(old.split())
print({'pdf':str(PDF),'pages':len(pdf),'page_words':[len(p.get_text().split()) for p in pdf],'words':words,'original_words':old_words,'reduction_percent':round(100*(1-words/old_words),1),'requirements':len(ids),'team_members':6,'bounds':'OK'})
assert len(pdf)==5, f'Expected five designed pages, got {len(pdf)}'

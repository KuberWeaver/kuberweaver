"""Note courte, infographie vectorielle : parcours vertical, lecture non spécialiste.
Sources : vision initiale, README, COMPLETION_STATUS (recette du 6 novembre 2026).
Aucune donnée privée. Le PDF et le texte sont produits ensemble.
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
c.setTitle('KuberWeaver | Note de cadrage')
c.setAuthor('Équipe projet, rédaction assistée par IA')
texts=[]
def p(text,x,y,w=499,size=10.5,bold=False,color=INK,leading=None):
    style=ParagraphStyle('s',fontName='Bold' if bold else 'Regular',fontSize=size,leading=leading or size*1.45,textColor=HexColor(color))
    para=Paragraph(text,style); _,h=para.wrap(w,700)
    assert y+h<(818 if y>=798 else 794), (text,y,h)
    para.drawOn(c,x,H-y-h)
    texts.append(re.sub('<[^>]+>','',text))
    return y+h

def base(num,label):
    c.setFillColor(HexColor(TEAL)); c.rect(0,H-9,W,9,fill=1,stroke=0)
    p('KUBERWEAVER  /  NOTE DE CADRAGE',48,29,size=9,bold=True,color=TEAL)
    p(label,48,60,size=25,bold=True)
    c.setStrokeColor(HexColor('#DCE4E9')); c.line(48,53,W-48,53)
    p('EFREI Paris · Proposition à valider · Rédaction assistée par IA',48,798,w=455,size=7,color=MUTED)
    c.setFont('Regular',8); c.setFillColor(HexColor(MUTED)); c.drawRightString(W-48,31,f'{num} / 2')

def section(title,text,y):
    y=p(title,48,y,size=12,bold=True)
    return p(text,48,y+5)+15

base(1,'Déployer sans improviser')
p('Une plateforme interne pour demander un service informatique,<br/>le faire approuver et vérifier son déploiement.',48,107,size=12,color=MUTED)
y=section('01  Le besoin',
    'Installer un serveur ou modifier un environnement mobilise souvent plusieurs outils et des procédures manuelles. KuberWeaver vise à transformer les procédures validées de l’organisation en services réutilisables, accessibles depuis un même portail.',164)
y=section('02  Pour qui, et pour quoi ?',
    'L’opérateur prépare une demande. L’administrateur contrôle les droits, les règles et le coût estimé avant d’autoriser l’exécution. Le lecteur consulte les résultats. Objectif : moins de répétitions, des responsabilités claires et une trace de chaque changement.',y)
y=p('03  De la demande au résultat',48,y,size=12,bold=True)+29
steps=[
 ('Composer','Choisir un service dans le catalogue et renseigner ses paramètres.'),
 ('Contrôler','Vérifier les règles et afficher une estimation du coût.'),
 ('Approuver','Faire examiner le plan par un administrateur avant exécution.'),
 ('Déployer','Sauvegarder, tester sur une première cible, puis poursuivre.'),
 ('Vérifier','Contrôler le service obtenu et conserver les preuves du résultat.'),
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
p('Si une vérification échoue : tenter de restaurer l’état précédent. Si le rétablissement reste incertain, demander une intervention humaine.',48,y,size=9.5,bold=True,color=TEAL)
c.showPage()
base(2,'Un périmètre maîtrisé')
y=section('04  Le point de départ',
 'Un prototype existe. Le compte rendu des essais du 6 novembre 2026 rapporte des déploiements réels de NGINX et Apache, deux serveurs web, dans un laboratoire virtualisé, avec approbation, contrôles et retour à la version précédente. Ce n’est pas encore une plateforme prête pour la production.',111)
y=section('05  La suite proposée',
 'Consolider ce parcours, puis ajouter une catégorie de service différente pour éprouver la modularité. Les extensions, appelées <b>plugins</b> en anglais, servent à ajouter des capacités sans réécrire le cœur de la plateforme.<br/><br/>'
 'La création de machines virtuelles, les équipements réseau Cisco et pfSense, ainsi que les services d’infrastructure en ligne restent à valider sur des cibles réelles. Ils ne doivent pas être présentés comme acquis.',y)
y=section('06  Les limites à garder visibles',
 'Le coût affiché est une estimation interne, pas une facture. Un schéma de dépendances ne configure pas le réseau. Malgré son nom, le prototype ne pilote pas Kubernetes, un outil de gestion de conteneurs.<br/><br/>'
 'Un agent d’intelligence artificielle externe peut utiliser la plateforme avec des droits limités, accordés par un administrateur. Aucun modèle d’IA intégré n’est nécessaire. L’agent reste soumis aux mêmes contrôles d’accès et d’approbation que le portail.',y)
y=section('07  Comment juger la réussite ?',
 'La démonstration devra montrer : une demande interdite bloquée ; un service approuvé réellement accessible ; un échec suivi d’une restauration vérifiée ; l’ajout d’un nouveau service sans modification du cœur. Les livrables attendus sont le prototype, sa procédure d’installation et les preuves de ces essais.',y)
y=section('08  Les décisions à prendre',
 'Choisir le prochain service, confirmer la répartition des tâches et des validations, puis fixer le calendrier et les moyens disponibles. Ces engagements restent à confirmer.',y)
p('Repère de vocabulaire : « rollback » est le terme anglais pour « retour à l’état précédent ». Cette note privilégie les équivalents français.',48,y,w=499,size=8.5,color=MUTED)
p('Sources : vision initiale, README et docs/COMPLETION_STATUS.md du projet.<br/>État rapporté par ces documents, sans nouveaux essais techniques pour cette note.',48,752,w=499,size=7.5,color=MUTED)
c.save()
text='KuberWeaver, note de cadrage\n\n'+'\n\n'.join(texts)+'\n'
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

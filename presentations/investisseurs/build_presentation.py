"""Generate editable PPTX and companion PDF from a common slide layout.

The PDF is generated directly from the same layout, not converted from PPTX.
Run with python-pptx, reportlab and Pillow installed. All fonts/assets are local.
"""
from pathlib import Path
import json
import re
import subprocess
import math
from PIL import Image
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / 'assets'
OUT = ROOT
W, H = 13.333333, 7.5
BG = 'FAFBFD'
INK = '18243D'
MUTED = '5B687C'
BLUE = '2558D8'
TEAL = '167E75'
PALE = 'EDF3FF'
LINE = 'DDE4EF'
WHITE = 'FFFFFF'
FONT = 'Noto Sans'
SOURCES = {
    1: 'https://platformengineering.org/blog/what-is-infrastructure-platform-engineering',
    2: 'https://hermes-agent.nousresearch.com/docs/',
    3: 'https://files.catbox.moe/cwo3fv.jpeg',
    4: 'https://cdn.prod.website-files.com/6489e23dd070ba71d41a33b2/67b73877af69377f1ad55b9a_AD_4nXeSisWft9MHyHrKbXH-_MePMav-wP-J6mJh6hURVvao_Na39loV3kwHS6fDC-L0pmb4VczhjstgXNlDMYyUSFkFhD1U8p11X-9XInGZQ98tEaVlGg2NktMquYKXDeIfYFuAEIEg.avif',
}
REG = subprocess.check_output(['fc-match', '-f', '%{file}', FONT], text=True)
BOLD = subprocess.check_output(['fc-match', '-f', '%{file}', FONT + ':style=Bold'], text=True)
pdfmetrics.registerFont(TTFont('Deck', REG))
pdfmetrics.registerFont(TTFont('DeckBold', BOLD))
prs = Presentation()
prs.slide_width = Inches(W)
prs.slide_height = Inches(H)
prs.core_properties.title = 'KuberWeaver, standardiser le sur-mesure'
prs.core_properties.subject = 'Présentation investisseurs non techniques, EFREI Paris'
prs.core_properties.author = 'Équipe KuberWeaver'
prs.core_properties.keywords = 'infrastructure, platform engineering, modularité, gouvernance, FinOps'
pdf = canvas.Canvas(str(OUT / 'KuberWeaver_Presentation_Investisseurs.pdf'), pagesize=(W*72, H*72))
pdf.setTitle(prs.core_properties.title)
pdf.setAuthor('Équipe KuberWeaver')
slides = []
checks = []
current = None

def rgb(color):
    return tuple(int(color[i:i+2], 16)/255 for i in (0,2,4))

def rect(x,y,w,h,fill=WHITE,line=None,radius=0.15):
    sh = current.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.fill.solid()
    sh.fill.fore_color.rgb = RGBColor.from_string(fill)
    if line:
        sh.line.color.rgb = RGBColor.from_string(line)
        sh.line.width = Pt(0.8)
    else:
        sh.line.fill.background()
    if radius:
        sh.adjustments[0] = min(0.12, radius/min(w,h))
    pdf.setFillColorRGB(*rgb(fill))
    pdf.setStrokeColorRGB(*rgb(line or fill))
    if radius:
        pdf.roundRect(x*72,(H-y-h)*72,w*72,h*72,radius*72,stroke=bool(line),fill=1)
    else:
        pdf.rect(x*72,(H-y-h)*72,w*72,h*72,stroke=bool(line),fill=1)
    return sh

def wrap(text,width,size,bold=False):
    face='DeckBold' if bold else 'Deck'
    out=[]
    for para in text.split('\n'):
        if not para:
            out.append('')
            continue
        row=''
        for word in para.split():
            trial=(row+' '+word).strip()
            if row and pdfmetrics.stringWidth(trial,face,size)>width*72:
                out.append(row)
                row=word
            else:
                row=trial
        out.append(row)
    return out

def text(txt,x,y,w,h,size=20,color=INK,bold=False,leading=1.24,link=None):
    lines=wrap(txt,w,size,bold)
    required=(len(lines)*size*leading+3)/72
    original_size=size
    while required > h+0.01 and size > min(original_size, 14):
        size -= 0.5
        lines=wrap(txt,w,size,bold)
        required=(len(lines)*size*leading+3)/72
    assert required <= h+0.01, (len(slides),txt[:80],required,h)
    assert x>=0 and y>=0 and x+w<=W+0.01 and y+h<=H+0.01
    for row in lines:
        assert pdfmetrics.stringWidth(row,'DeckBold' if bold else 'Deck',size)<=w*72+1, row
    sh=current.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h))
    tf=sh.text_frame
    tf.clear()
    tf.word_wrap=False
    tf.margin_left=tf.margin_right=tf.margin_top=tf.margin_bottom=0
    tf.vertical_anchor=MSO_ANCHOR.TOP
    for i,row in enumerate(lines):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph()
        p.space_before=Pt(0)
        p.space_after=Pt(0)
        p.line_spacing=Pt(size*leading)
        run=p.add_run()
        run.text=row
        run.font.name=FONT
        run.font.size=Pt(size)
        run.font.bold=bold
        run.font.color.rgb=RGBColor.from_string(color)
        if link:
            run.hyperlink.address=link
    pdf.setFont('DeckBold' if bold else 'Deck',size)
    pdf.setFillColorRGB(*rgb(color))
    for i,row in enumerate(lines):
        pdf.drawString(x*72,(H-y)*72-size*0.94-i*size*leading,row)
    if link:
        pdf.linkURL(link,(x*72,(H-y-h)*72,(x+w)*72,(H-y)*72),relative=0,thickness=0)
    checks.append({'slide':len(slides),'text':txt,'lines':len(lines),'size':size,'required_h':required,'box':[x,y,w,h]})
    return sh

def picture(path,x,y,w,h):
    im=Image.open(path)
    iw,ih=im.size
    scale=min(w/iw,h/ih)
    dw,dh=iw*scale,ih*scale
    dx,dy=x+(w-dw)/2,y+(h-dh)/2
    current.shapes.add_picture(str(path),Inches(dx),Inches(dy),width=Inches(dw),height=Inches(dh))
    pdf.drawImage(ImageReader(str(path)),dx*72,(H-dy-dh)*72,dw*72,dh*72)

def begin(kicker,title,subtitle='',note='',source=None):
    global current
    current=prs.slides.add_slide(prs.slide_layouts[6])
    slides.append({'title':title,'note':note,'sources':source or []})
    current.background.fill.solid()
    current.background.fill.fore_color.rgb=RGBColor.from_string(BG)
    pdf.setFillColorRGB(*rgb(BG)); pdf.rect(0,0,W*72,H*72,fill=1,stroke=0)
    text(kicker.upper(),0.58,0.29,11.8,0.28,10,BLUE,True)
    text(title,0.58,0.83,12.1,0.95,31,bold=True,leading=1.14)
    if subtitle:
        text(subtitle,0.60,1.82,12.0,0.63,16,MUTED)

def footer(note='KuberWeaver  /  EFREI Paris  /  Présentation investisseurs'):
    rect(0.58,7.02,12.16,0.012,LINE,radius=0)
    text(note,0.60,7.14,11.35,0.21,9,MUTED)
    text(f'{len(slides):02}',12.24,7.11,0.5,0.25,10,MUTED,True)

def finish(foot=None):
    if foot: footer(foot)
    else: footer()
    d=slides[-1]
    notes=d['note']
    if d['sources']:
        notes+='\n\nSources :\n'+'\n'.join(f'[{n}] {SOURCES[n]}' for n in d['sources'])
    current.notes_slide.notes_text_frame.text=notes
    pdf.showPage()

def card(x,y,w,h,label,title,body,accent=BLUE):
    rect(x,y,w,h,WHITE,LINE)
    rect(x+0.23,y+0.25,0.11,0.28,accent,radius=0)
    text(label.upper(),x+0.48,y+0.24,w-0.73,0.36,11,accent,True)
    text(title,x+0.26,y+0.91,w-0.52,1.02,23,bold=True,leading=1.2)
    text(body,x+0.26,y+2.03,w-0.52,h-2.18,16,MUTED,leading=1.35)

# Asset preparation, preserve original source images and attribution.
im=Image.open(ASSETS/'ipe-pyramide.avif').convert('RGB')
im.save(ASSETS/'ipe-pyramide.png')
local=ROOT.parent.parent/'docs/cadrage-original/Gemini_Generated_Image_6bodd86bodd86bod.jpeg'
im=Image.open(local).convert('RGB')
im.crop((50,255,2700,1510)).save(ASSETS/'structure-projet-cadree.png')

# 01
begin('La vision','KuberWeaver',note='Positionnement de vision : rendre les environnements propres à une entreprise reproductibles et gouvernés. Il ne s’agit pas de dire que tous les secteurs sont déjà couverts. Le projet est au stade prototype. Aucun montant levé, revenu ou client n’est revendiqué.')
text('Standardiser le sur-mesure.',0.62,2.04,10.6,0.92,43,bold=True)
text('Le produit s’adapte à l’entreprise.\nPas l’inverse.',0.65,3.25,10.7,1.24,28,MUTED)
for x,label in [(0.65,'Votre infrastructure'),(4.78,'Vos règles'),(8.91,'Votre rythme')]:
    rect(x,5.33,3.76,0.77,PALE)
    text(label,x+0.24,5.51,3.28,0.39,18,BLUE,True)
finish('Prototype fonctionnel  /  Vision produit  /  EFREI Paris')

# 02
begin('Comprendre le contexte','L’infrastructure devient un service interne.',note='L’infrastructure correspond aux moyens informatiques qui font fonctionner les applications : serveurs, stockage, réseaux et bases de données. L’IPE les transforme en produits internes consommables avec standardisation, automatisation et self-service. Le schéma original situe ces leviers entre les difficultés opérationnelles et les résultats métier. Les gains sont des objectifs, pas des chiffres mesurés pour KuberWeaver.',source=[1,4])
picture(ASSETS/'ipe-pyramide.png',0.58,2.12,7.6,4.32)
text('Infrastructure Platform\nEngineering, ou IPE',8.50,2.22,4.10,0.95,23,bold=True)
text('Transformer serveurs, réseaux et données en services faciles à demander.',8.50,3.40,4.05,1.13,20,MUTED)
rect(8.44,4.92,4.30,1.15,PALE)
text('Comme un catalogue interne :\non choisit, les règles sont intégrées.',8.65,5.12,3.88,0.80,17,BLUE,True)
finish('Schéma : Platform Engineering [1, 4]  /  Standardiser + automatiser pour mieux servir les équipes')

# 03
begin('Le problème','Le sur-mesure finit trop souvent en bricolage.','Des besoins différents ne devraient pas produire des pratiques incontrôlables.',note='Problèmes types à valider lors des entretiens clients. Le projet ne dispose pas d’une étude chiffrée de leur prévalence. Les difficultés de ticket-ops, de faible standardisation et de coûts non maîtrisés sont décrites dans la source IPE.',source=[1])
card(0.60,2.68,3.88,3.86,'Temps','Chaque demande\nrepart de zéro.','Des échanges et des gestes manuels se répètent.')
card(4.73,2.68,3.88,3.86,'Risque','Chaque équipe fait\nun peu autrement.','La sécurité et la qualité deviennent difficiles à suivre.')
card(8.86,2.68,3.88,3.86,'Visibilité','Les coûts restent\ndispersés.','Les décideurs peinent à relier dépenses et besoins.')
finish()

# 04
begin('La réponse KuberWeaver','Transformer votre savoir-faire en services réutilisables.','Vous définissez vos standards. Vos équipes les utilisent sans les réinventer.',note='Le parcours de demande, approbation et déploiement est implémenté sur des services web du laboratoire. Les modèles réutilisables sont appelés blueprints. Le retour arrière dépend du service et n’est pas une garantie universelle.')
steps=[('01','Choisir','Un environnement du catalogue.'),('02','Adapter','Les paramètres autorisés.'),('03','Valider','Les règles, le coût et les droits.'),('04','Déployer','Vérifier et garder une trace.')]
for i,(n,t,b) in enumerate(steps):
    x=0.60+i*3.07
    rect(x,2.90,2.85,2.68,WHITE,LINE)
    text(n,x+0.23,3.12,2.35,0.45,24,BLUE,True)
    text(t,x+0.23,3.86,2.35,0.46,23,bold=True)
    text(b,x+0.23,4.57,2.35,0.85,16,MUTED)
rect(0.60,5.95,12.14,0.64,PALE)
text('Un environnement spécifique, une méthode reproductible.',0.90,6.08,11.5,0.39,20,BLUE,True)
finish()

# 05
begin('La modularité, simplement','Des briques métier autour d’un socle commun.','Un plugin ajoute un service ou connecte un outil, sans tout reconstruire.',note='Un blueprint compatible apporte ses paramètres et son cycle de vie ; le formulaire standard est généré. Une nouvelle famille technique peut nécessiter un fournisseur développé et testé. L’adaptation n’est pas sans effort et les pages frontend spécialisées ne sont pas chargées automatiquement. L’intégration à l’existant et la portabilité des automatisations sont des objectifs, à éprouver chez les pilotes.')
rect(0.60,2.78,3.34,3.63,WHITE,LINE)
text('Vous conservez',0.88,3.10,2.8,0.45,23,bold=True)
text('Vos équipements\nVos outils\nVos contraintes métier',0.88,4.00,2.80,1.80,20,MUTED,leading=1.6)
rect(4.23,2.78,4.85,3.63,PALE)
text('KuberWeaver',4.55,3.10,4.2,0.55,29,BLUE,True)
text('Un socle commun :\ndroits, règles, validation,\ntraçabilité et coûts estimés.',4.55,4.04,4.15,1.82,22,INK,leading=1.4)
rect(9.38,2.78,3.34,3.63,WHITE,LINE)
text('Vous ajoutez',9.65,3.10,2.80,0.45,23,bold=True)
text('Les services utiles\nLes connexions requises\nLes modèles propres à vous',9.65,4.02,2.78,1.93,18,MUTED,leading=1.5)
finish('Philosophie produit  /  Adapter l’outil à l’existant plutôt qu’imposer un nouvel environnement')

# 06
begin('Des besoins spécifiques','Le même socle. Des catalogues différents.','Trois exemples de marchés à explorer, pas des références clients.',note='Cas illustratifs de positionnement. Industriels : environnements de test et contraintes de sites. Fintech : séparation des accès et traçabilité des changements. Artisanat : cas à qualifier, notamment pour les structures ayant un besoin récurrent d’environnements ; un petit artisan sans équipe IT pourrait être servi par son prestataire plutôt qu’exploiter le produit lui-même. Aucune conformité réglementaire automatique n’est revendiquée.')
card(0.60,2.68,3.88,3.86,'Industrie','Reproduire un\nenvironnement de site.','Des modèles adaptés aux équipements et aux règles locales.')
card(4.73,2.68,3.88,3.86,'Fintech','Encadrer les\nchangements sensibles.','Des accès maîtrisés, des validations et des traces exploitables.')
card(8.86,2.68,3.88,3.86,'Artisanat & PME','Simplifier les\nbesoins récurrents.','Un catalogue adapté, éventuellement opéré par un prestataire IT.')
finish('Cas d’usage envisagés  /  Validation métier et intégrations à réaliser avec les entreprises pilotes')

# 07
begin('La promesse de contrôle','Des pratiques de grande entreprise, à votre échelle.','Les mêmes principes de maîtrise, sans prétendre à une certification équivalente.',note='L’ambition est de rendre accessibles les mécanismes de gouvernance, sécurité et pilotage économique habituellement structurés dans les grandes organisations. Les rôles, approbations et estimations existent dans le prototype. La conformité, la résilience et l’égalité de niveau de sécurité avec une grande entreprise ne sont pas garanties. Le durcissement production reste à effectuer.')
card(0.60,2.68,3.88,3.86,'Gouvernance','Qui peut demander ?\nQui doit valider ?','Des responsabilités et une trace des décisions.',TEAL)
card(4.73,2.68,3.88,3.86,'Sécurité','Des règles avant\nchaque changement.','Des droits limités et des vérifications à chaque étape.',TEAL)
card(8.86,2.68,3.88,3.86,'Coûts IT','Savoir ce que\nl’on engage.','Un coût estimé avant décision, détaillé par ressource.',TEAL)
finish('Sécurité à chaque étape  /  FinOps = comprendre et piloter les coûts informatiques')

# 08
begin('La structure du produit','Une porte d’entrée, plusieurs capacités.',note='Schéma de vision original du projet, recadré pour enlever son titre et améliorer la lisibilité. Le lien Catbox fourni renvoyait un corps vide et n’a pas pu être utilisé ; cette illustration provient de docs/cadrage-original. OPA, Vault, OpenTofu, ChatOps et les cibles hybrides sont des éléments de vision : le prototype réel utilise des règles Python, une source OpenBao et un exécuteur Ansible sur des conteneurs web. Toutes les fonctions de ce schéma ne sont pas implémentées.')
picture(ASSETS/'structure-projet-cadree.png',0.60,1.68,12.14,4.90)
finish('Schéma de vision, source : cadrage original du projet  /  Certaines intégrations restent à développer')

# 09
begin('Le rôle de l’IA','Hermes : un assistant outillé, pas un décideur.',note='Hermes Agent, développé par Nous Research, est un logiciel qui relie un modèle IA à des outils et à une mémoire. Il ne doit pas être confondu avec le modèle de langage lui-même. Le rôle envisagé dans KuberWeaver est un diagnostic optionnel en lecture seule, avec recommandations soumises à l’humain. Hermes peut techniquement agir selon ses outils et permissions ; le mode lecture seule est un choix d’intégration à faire respecter. Aucun agent Hermes de diagnostic n’est actuellement connecté au prototype ; le diagnostic livré est déterministe.',source=[2])
text('Un agent IA combine :',0.65,2.40,7.6,0.48,25,bold=True)
for i,(title,body) in enumerate([('Un modèle','Comprendre et expliquer.'),('Des outils autorisés','Consulter les informations utiles.'),('Du contexte','Retenir les procédures et les échanges.')]):
    y=3.13+i*0.95
    rect(0.65,y,6.92,0.76,WHITE,LINE)
    text(title,0.88,y+0.16,2.55,0.40,18,BLUE,True)
    text(body,3.50,y+0.16,3.83,0.42,15,MUTED)
rect(8.02,2.48,4.70,3.72,PALE)
text('Dans KuberWeaver,\nle rôle envisagé',8.32,2.82,4.10,1.00,25,BLUE,True)
text('Observer → expliquer → proposer\n\nL’humain garde la décision.',8.32,4.15,4.08,1.61,20,bold=True)
finish('Hermes Agent : Nous Research [2]  /  Intégration envisagée, non connectée au prototype actuel')

# 10
begin('Une plateforme qui accompagne la croissance','Commencer petit. Étendre sans repartir de zéro.','Une trajectoire de produit, pas une capacité de grande échelle déjà démontrée.',note='Le noyau et les contrats de fournisseurs sont modulaires. La montée en charge entreprise reste un objectif de roadmap : SSO, permissions par objet, isolation, haute disponibilité et tests de charge ne sont pas livrés. On ne revendique donc pas une extensibilité technique infinie ni une capacité enterprise éprouvée.')
phases=[('Une équipe','Quelques services utiles.\nDes règles communes.'),('Plusieurs équipes','Davantage de modèles.\nDes responsabilités distinctes.'),('Une organisation étendue','Plus de sites et de fournisseurs.\nUne exploitation renforcée.')]
for i,(title,body) in enumerate(phases):
    x=0.60+i*4.13
    rect(x,3.03,3.88,2.70,PALE if i==0 else WHITE,LINE)
    text(f'0{i+1}',x+0.27,3.27,3.33,0.46,24,BLUE,True)
    text(title,x+0.27,4.00,3.33,0.87,22,bold=True)
    text(body,x+0.27,4.95,3.33,0.65,15,MUTED)
text('La même logique de service, avec des capacités ajoutées au besoin.',0.66,6.20,12.0,0.50,21,BLUE,True)
finish('Cible : PME, ETI et grandes organisations  /  Durcissement et montée en charge à valider')

# 11
begin('La logique économique','Un budget pour les services utiles, pas pour le superflu.','L’avantage recherché : un coût global proportionné au besoin.',note='Aucun prix de vente, pourcentage d’économie, coût client ou avantage chiffré face à ServiceNow n’a été validé. Les leviers proposés sont la réutilisation de l’existant, la réduction des gestes répétitifs et un périmètre proportionné. Le coût total inclut intégration, hébergement, maintenance et support. ServiceNow peut aussi être modulaire et auto-hébergé ; ce ne sont pas des exclusivités de KuberWeaver. La tarification, la licence et le modèle de support restent à définir.')
card(0.60,2.68,3.88,3.86,'Réutiliser','Préserver\nl’investissement existant.','Capitaliser sur les outils et procédures déjà maîtrisés.')
card(4.73,2.68,3.88,3.86,'Répéter moins','Réduire le travail\nfait à la main.','Transformer une procédure validée en service réutilisable.')
card(8.86,2.68,3.88,3.86,'Mesurer','Vérifier la valeur\nchez les pilotes.','Comparer délai, effort, incidents et coût total avant / après.')
finish('Hypothèse économique à valider  /  Aucun gain chiffré ni prix commercial revendiqué')

# 12
begin('Où nous en sommes','Un prototype réel. Une industrialisation à construire.',note='Prototype : catalogue, canvas, rôles, approbation, déploiements réels NGINX/Apache sur deux cibles, canary/rolling, restauration, Git local et estimation interne. Tests relancés lors de la mise en dépôt privé : 159 backend, 35 frontend, build réussi. Ces tests ne constituent pas une certification sécurité ni une recette de production. Prochaines étapes à arbitrer : service réellement différent, secrets d’exécution, notifications, durcissement et validation terrain. Aucun client pilote acquis n’est revendiqué.')
rect(0.60,2.15,7.13,3.16,WHITE,LINE)
text('DÉJÀ DÉMONTRÉ',0.90,2.43,6.5,0.36,12,TEAL,True)
text('Composer, approuver, déployer\net revenir à l’état précédent.',0.90,3.14,6.4,1.12,27,bold=True)
text('Des services web réellement exécutés sur deux cibles de laboratoire.',0.90,4.57,6.35,0.55,16,MUTED)
rect(8.02,2.15,4.70,3.16,PALE)
text('159',8.36,2.57,1.75,0.80,39,BLUE,True)
text('tests backend',10.07,2.80,2.2,0.40,17,MUTED)
text('35',8.36,3.70,1.75,0.80,39,BLUE,True)
text('tests frontend',10.07,3.93,2.2,0.40,17,MUTED)
text('Prochaine étape : éprouver une brique différente et un cas d’entreprise pilote.',0.67,5.93,12.0,0.84,23,bold=True)
finish('État du prototype documenté et testé  /  Pas encore prêt pour une exploitation critique en production')

# 13
begin('L’équipe et le cap','Six profils pour relier produit, infrastructure et valeur.',note='Équipe fournie par le porteur du projet. Les marchés et le modèle économique restent à valider. La prochaine conversation investisseurs peut porter sur des introductions à des entreprises pilotes et les critères de preuve attendus ; aucun besoin de financement chiffré n’est inventé.')
team=[('Etienne MURZEAU','Référent, IA et infrastructure'),('Rayan CHOUCHANE','Infrastructure, GitOps et pipeline'),('Alex YAO','Infrastructure et provisionnement'),('Vidjay VELAYOUDAM','FinOps et business'),('Abdel-Waheb SAKKAL','Infrastructure, services et restauration'),('Priyank SOLANKI','Développement web full stack')]
for i,(name,role) in enumerate(team):
    col=i%2; row=i//2; x=0.65+col*6.25; y=2.19+row*0.96
    text(name,x,y,5.7,0.39,18,bold=True)
    text(role,x,y+0.43,5.7,0.36,14,MUTED)
rect(0.60,5.55,12.14,1.08,PALE)
text('Notre cap : rendre reproductible ce qui fait votre différence.',0.92,5.80,11.50,0.60,25,BLUE,True)
finish('EFREI Paris  /  Prochaine preuve : un pilote métier et un bilan de valeur mesuré')

# 14
begin('Annexe / sources et périmètre','Les références, les images et les limites.',note='Sources consultées pour la présentation. Le lien Catbox a renvoyé un corps vide et n’est pas embarqué ; le schéma de structure est le cadrage original local, sans affirmer que les deux images sont identiques. La pyramide est reproduite avec attribution à Platform Engineering ; droits de réutilisation à confirmer avant diffusion publique/commerciale. PDF produit directement depuis le même code de mise en page que le PPTX, pas par conversion de PowerPoint. Aucun push GitHub effectué pour cette présentation.',source=[1,2,3,4])
refs=[('01','IPE : principes et contexte','Platform Engineering, article et pyramide fournis.',SOURCES[1]),('02','Hermes Agent : définition','Documentation officielle de Nous Research.',SOURCES[2]),('03','KuberWeaver : état et architecture','Cahier des charges, README, vérifications et schéma local.',None)]
for i,(n,title,body,url) in enumerate(refs):
    y=2.11+i*1.03
    text(n,0.65,y,0.51,0.41,19,BLUE,True)
    text(title,1.29,y,10.9,0.43,20,bold=True,link=url)
    text(body,1.29,y+0.49,10.9,0.40,15,MUTED)
rect(0.65,5.60,12.07,0.95,PALE)
text('Vision ≠ fonctionnalités livrées. Cas sectoriels ≠ clients acquis.\nÉconomies, conformité et passage à l’échelle restent à valider.',0.91,5.79,11.55,0.65,16,BLUE,True)
finish('Images attribuées  /  Droits à confirmer avant diffusion publique  /  Notes détaillées dans le PowerPoint')

pdf.save()
prs.save(OUT/'KuberWeaver_Presentation_Investisseurs.pptx')
(OUT/'layout-checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'slides-manifest.json').write_text(json.dumps(slides,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'slides':len(slides),'text_boxes_checked':len(checks),'pptx':str(OUT/'KuberWeaver_Presentation_Investisseurs.pptx'),'pdf':str(OUT/'KuberWeaver_Presentation_Investisseurs.pdf')},ensure_ascii=False))

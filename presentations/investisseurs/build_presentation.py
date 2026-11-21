"""Generate editable PPTX and companion PDF from a common slide layout.

The PDF is generated directly from the same layout, not converted from PPTX.
Run with python-pptx, reportlab and Pillow installed. All fonts/assets are local.
"""
from pathlib import Path
import json
import sys
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
prs.core_properties.title = 'KuberWeaver, standardize the custom-built'
prs.core_properties.subject = 'Non-technical investor presentation, EFREI Paris'
prs.core_properties.author = 'KuberWeaver team'
prs.core_properties.keywords = 'infrastructure, platform engineering, modularity, governance, FinOps'
pdf = canvas.Canvas(str(OUT / 'KuberWeaver_Presentation_Investisseurs.pdf'), pagesize=(W*72, H*72))
pdf.setTitle(prs.core_properties.title)
pdf.setAuthor('KuberWeaver team')
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
    im=Image.open(path).convert('RGB')
    if path.name == 'structure-projet-cadree.png':
        # Localize labels in memory; retain the attributed original graphic and icons.
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(im)
        sx, sy = im.width / 1325, im.height / 627
        labels = [
            ((140, 23, 980, 58), '1. Web portal, drag-and-drop canvas & plugin marketplace', 26),
            ((130, 186, 980, 222), '2. FastAPI core, OPA governance, FinOps & Vault', 26),
            ((130, 350, 980, 385), '4. GitOps & IaC engine (OpenTofu + Ansible), automatic rollback', 24),
            ((105, 512, 1000, 546), '5. Hybrid targets: VMware, cloud, Docker, Cisco switches & pfSense', 24),
            ((1095, 187, 1290, 302), '3. AI agent\nRead-only &\nChatOps (Teams,\nDiscord, WhatsApp)', 21),
        ]
        for (x1,y1,x2,y2), label, size in labels:
            box = tuple(round(v * (sx if i % 2 == 0 else sy)) for i,v in enumerate((x1,y1,x2,y2)))
            fill = im.getpixel((round((x1-5)*sx), round((y1+4)*sy)))
            draw.rectangle(box, fill=fill)
            font = ImageFont.truetype(BOLD, round(size*sy))
            draw.multiline_text(((box[0]+box[2])/2, box[1]), label, font=font, fill='white', anchor='ma', align='center', spacing=2*sy)
    iw,ih=im.size
    scale=min(w/iw,h/ih)
    dw,dh=iw*scale,ih*scale
    dx,dy=x+(w-dw)/2,y+(h-dh)/2
    from io import BytesIO
    image_data = BytesIO()
    im.save(image_data, format='PNG')
    image_data.seek(0)
    current.shapes.add_picture(image_data,Inches(dx),Inches(dy),width=Inches(dw),height=Inches(dh))
    pdf.drawImage(ImageReader(im),dx*72,(H-dy-dh)*72,dw*72,dh*72)

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

def footer(note='KuberWeaver  /  EFREI Paris  /  Investor presentation'):
    rect(0.58,7.02,12.16,0.012,LINE,radius=0)
    text(note,0.60,7.14,11.35,0.21,9,MUTED)
    text(f'{len(slides):02}',12.24,7.11,0.5,0.25,10,MUTED,True)

def finish(foot=None):
    if foot: footer(foot)
    else: footer()
    d=slides[-1]
    notes=d['note']
    if d['sources']:
        notes+='\n\nSources:\n'+'\n'.join(f'[{n}] {SOURCES[n]}' for n in d['sources'])
    current.notes_slide.notes_text_frame.text=notes
    pdf.showPage()

def card(x,y,w,h,label,title,body,accent=BLUE):
    rect(x,y,w,h,WHITE,LINE)
    rect(x+0.23,y+0.25,0.11,0.28,accent,radius=0)
    text(label.upper(),x+0.48,y+0.24,w-0.73,0.36,11,accent,True)
    text(title,x+0.26,y+0.91,w-0.52,1.02,23,bold=True,leading=1.2)
    text(body,x+0.26,y+2.03,w-0.52,h-2.18,16,MUTED,leading=1.35)

# Reuse the attributed local graphics without rewriting source assets.

# 01
begin('The vision','KuberWeaver',note='Product vision: make organization-specific environments reproducible and governed. This does not mean every sector is supported. No funding raised, revenue or customers are claimed.')
text('Standardize the custom-built.',0.62,2.04,10.6,0.92,43,bold=True)
text('The product adapts to your organization.\nNot the other way around.',0.65,3.25,10.7,1.24,28,MUTED)
for x,label in [(0.65,'Your infrastructure'),(4.78,'Your rules'),(8.91,'Your pace')]:
    rect(x,5.33,3.76,0.77,PALE)
    text(label,x+0.24,5.51,3.28,0.39,18,BLUE,True)
finish('Internal infrastructure platform  /  Product vision  /  EFREI Paris')

# 02
begin('Understanding the context','Infrastructure becomes an internal service.',note='Infrastructure includes servers, storage, networks and databases. IPE turns these into consumable internal products through standardization, automation and self-service. The original diagram connects operational difficulties to business outcomes. Benefits are goals, not measured KuberWeaver results.',source=[1,4])
picture(ASSETS/'ipe-pyramide.png',0.58,2.12,7.6,4.32)
text('Infrastructure Platform\nEngineering, or IPE',8.50,2.22,4.10,0.95,23,bold=True)
text('Turn servers, networks and data into services that are easy to request.',8.50,3.40,4.05,1.13,20,MUTED)
rect(8.44,4.92,4.30,1.15,PALE)
text('An internal catalogue:\nchoose a service with rules built in.',8.65,5.12,3.88,0.80,17,BLUE,True)
finish('Diagram: Platform Engineering [1, 4]  /  Standardize and automate to serve teams better')

# 03
begin('The problem','Custom work too often becomes improvisation.','Different needs should not lead to uncontrolled practices.',note='Typical problems to validate in customer interviews; no quantified prevalence study is claimed. Ticket-ops, low standardization and uncontrolled costs are discussed by the IPE source.',source=[1])
card(0.60,2.68,3.88,3.86,'Time','Every request\nstarts from scratch.','Discussions and manual steps keep repeating.')
card(4.73,2.68,3.88,3.86,'Risk','Every team works\na little differently.','Security and quality become difficult to track.')
card(8.86,2.68,3.88,3.86,'Visibility','Costs remain\nscattered.','Decision-makers struggle to connect spending with needs.')
finish()

# 04
begin('The KuberWeaver response','Turn your expertise into reusable services.','You define standards. Teams use them without reinventing them.',note='Request, approval and deployment have been exercised on lab web services. Reusable templates are called blueprints. Recovery depends on the service; it is not a universal guarantee.')
steps=[('01','Choose','A catalogue environment.'),('02','Adapt','The permitted parameters.'),('03','Approve','Policy, cost and permissions.'),('04','Deploy','Verify and retain evidence.')]
for i,(n,t,b) in enumerate(steps):
    x=0.60+i*3.07
    rect(x,2.90,2.85,2.68,WHITE,LINE)
    text(n,x+0.23,3.12,2.35,0.45,24,BLUE,True)
    text(t,x+0.23,3.86,2.35,0.46,23,bold=True)
    text(b,x+0.23,4.57,2.35,0.85,16,MUTED)
rect(0.60,5.95,12.14,0.64,PALE)
text('A specific environment. A reproducible method.',0.90,6.08,11.5,0.39,20,BLUE,True)
finish()

# 05
begin('Modularity, explained','Service building blocks around a common core.','A plugin adds a service or connects a tool without rebuilding everything.',note='Compatible blueprints provide parameters and lifecycle; standard forms are generated. A new technical family may require a provider to be implemented and tested. Adaptation requires work; specialized frontend pages are not loaded automatically. Integration and automation portability remain goals to validate with pilots.')
rect(0.60,2.78,3.34,3.63,WHITE,LINE)
text('You retain',0.88,3.10,2.8,0.45,23,bold=True)
text('Your equipment\nYour tools\nYour business constraints',0.88,4.00,2.80,1.80,20,MUTED,leading=1.6)
rect(4.23,2.78,4.85,3.63,PALE)
text('KuberWeaver',4.55,3.10,4.2,0.55,29,BLUE,True)
text('A shared core:\npermissions, policy, approval,\naudit trails and cost estimates.',4.55,4.04,4.15,1.82,22,INK,leading=1.4)
rect(9.38,2.78,3.34,3.63,WHITE,LINE)
text('You add',9.65,3.10,2.80,0.45,23,bold=True)
text('Useful services\nRequired connections\nYour own templates',9.65,4.02,2.78,1.93,18,MUTED,leading=1.5)
finish('Product philosophy  /  Adapt to existing tools rather than impose a new environment')

# 06
begin('Specific needs','The same core. Different catalogues.','Three markets to explore, not existing customer references.',note='Illustrative positioning: industrial test environments and site constraints; fintech access separation and change traceability; craft businesses with recurring environment needs, potentially served by an IT provider rather than operating the platform themselves. No automatic regulatory compliance is claimed.')
card(0.60,2.68,3.88,3.86,'Industry','Reproduce a\nsite environment.','Templates adapted to local equipment and rules.')
card(4.73,2.68,3.88,3.86,'Fintech','Govern sensitive\nchanges.','Controlled access, approvals and usable audit trails.')
card(8.86,2.68,3.88,3.86,'Craft firms & SMEs','Simplify\nrecurring needs.','A tailored catalogue, potentially run by an IT provider.')
finish('Potential use cases  /  Business validation and integrations require pilot organizations')

# 07
begin('The control objective','Enterprise practices at your scale.','The same control principles, without claiming equivalent certification.',note='The aim is accessible governance, security and cost management. Roles, approvals and estimates are implemented. Compliance, resilience and security equivalence with a large enterprise are not guaranteed; operational hardening must be assessed for the intended environment.')
card(0.60,2.68,3.88,3.86,'Governance','Who can request?\nWho must approve?','Clear responsibilities and a record of decisions.',TEAL)
card(4.73,2.68,3.88,3.86,'Security','Rules before\nevery change.','Limited permissions and checks at each stage.',TEAL)
card(8.86,2.68,3.88,3.86,'IT costs','Know what\nyou commit to.','Estimated costs before approval, broken down by resource.',TEAL)
finish('Security at each stage  /  FinOps means understanding and managing IT costs')

# 08
begin('Product structure','One entry point. Multiple capabilities.',note='Historical vision diagram, cropped for readability. The supplied Catbox URL returned an empty body; this image comes from the original local project framing and is not claimed identical. OPA, Vault, OpenTofu, ChatOps and hybrid targets are vision elements, not proof of delivered integrations. The historical implementation used Python policy, an OpenBao source and Ansible on web containers. Some integrations remain unavailable.')
picture(ASSETS/'structure-projet-cadree.png',0.60,1.68,12.14,4.90)
finish('Historical vision diagram  /  Original project framing  /  Not all integrations are implemented')

# 09
begin('The role of AI','Hermes: a tool-using assistant, not the decision-maker.',note='Hermes Agent by Nous Research connects an AI model to tools and memory; it is not the language model itself. Optional read-only diagnostics with human-reviewed recommendations are envisaged. Hermes can act according to its tools and permissions: read-only integration must be enforced. No Hermes diagnostic agent is connected; delivered diagnostics are deterministic.',source=[2])
text('An AI agent combines:',0.65,2.40,7.6,0.48,25,bold=True)
for i,(title,body) in enumerate([('A model','Understand and explain.'),('Permitted tools','Read relevant information.'),('Context','Retain procedures and exchanges.')]):
    y=3.13+i*0.95
    rect(0.65,y,6.92,0.76,WHITE,LINE)
    text(title,0.88,y+0.16,2.55,0.40,18,BLUE,True)
    text(body,3.50,y+0.16,3.83,0.42,15,MUTED)
rect(8.02,2.48,4.70,3.72,PALE)
text('The envisaged role\nin KuberWeaver',8.32,2.82,4.10,1.00,25,BLUE,True)
text('Observe → explain → propose\n\nHumans retain the decision.',8.32,4.15,4.08,1.61,20,bold=True)
finish('Hermes Agent: Nous Research [2]  /  Diagnostic integration envisaged, not currently connected')

# 10
begin('A platform that can grow','Start small. Extend without starting over.','A product direction, not a demonstrated large-scale capability.',note='Core and provider contracts are modular. Enterprise scaling remains a roadmap objective: SSO, object-level permissions, isolation, high availability and load testing are not delivered. No infinite extensibility or proven enterprise capacity is claimed.')
phases=[('One team','A few useful services.\nShared rules.'),('Several teams','More templates.\nDistinct responsibilities.'),('A wider organization','More sites and providers.\nStronger operations.')]
for i,(title,body) in enumerate(phases):
    x=0.60+i*4.13
    rect(x,3.03,3.88,2.70,PALE if i==0 else WHITE,LINE)
    text(f'0{i+1}',x+0.27,3.27,3.33,0.46,24,BLUE,True)
    text(title,x+0.27,4.00,3.33,0.87,22,bold=True)
    text(body,x+0.27,4.95,3.33,0.65,15,MUTED)
text('The same service model, with capabilities added as needed.',0.66,6.20,12.0,0.50,21,BLUE,True)
finish('Target: SMEs, mid-sized firms and larger organizations  /  Hardening and scale require validation')

# 11
begin('The economic model','Budget for useful services, not unnecessary extras.','The intended benefit: total cost proportionate to actual needs.',note='No sale price, savings percentage, customer cost or quantified advantage over ServiceNow has been validated. Proposed levers are reuse, reduced repetition and proportionate scope. Total cost includes integration, hosting, maintenance and support. ServiceNow can also be modular and self-hosted: these are not exclusive advantages. Pricing, licensing and support remain to be defined.')
card(0.60,2.68,3.88,3.86,'Reuse','Preserve\nexisting investment.','Build on tools and procedures already understood.')
card(4.73,2.68,3.88,3.86,'Repeat less','Reduce\nmanual work.','Turn a reviewed procedure into a reusable service.')
card(8.86,2.68,3.88,3.86,'Measure','Verify value\nwith pilots.','Compare time, effort, incidents and total cost before and after.')
finish('Economic hypothesis to validate  /  No quantified savings or commercial price claimed')

# 12
begin('Evidence and current scope','Real lab evidence. Explicit operational limits.',note='Historical baseline: catalogue, canvas, roles, approval, real NGINX/Apache deployments on two targets, canary/rolling, restoration, local Git and internal estimates. The original repository-publication report records 159 backend tests, 35 frontend tests and a successful build. These are historical counts, not rerun for this presentation, security certification or production acceptance. Next work requires prioritization: another service, execution secrets, notifications, hardening and field validation. No acquired pilot customer is claimed. Current provisioning contracts remain separate: OVF import is create-only and local tests do not prove guest OS or SSH startup.')
rect(0.60,2.15,7.13,3.16,WHITE,LINE)
text('HISTORICALLY DEMONSTRATED',0.90,2.43,6.5,0.36,12,TEAL,True)
text('Compose, approve, deploy\nand restore the previous version.',0.90,3.14,6.4,1.12,27,bold=True)
text('Web services actually executed on two lab targets.',0.90,4.57,6.35,0.55,16,MUTED)
rect(8.02,2.15,4.70,3.16,PALE)
text('159',8.36,2.57,1.75,0.80,39,BLUE,True)
text('backend tests',10.07,2.80,2.2,0.40,17,MUTED)
text('35',8.36,3.70,1.75,0.80,39,BLUE,True)
text('frontend tests',10.07,3.93,2.2,0.40,17,MUTED)
text('Next: exercise another service category and a pilot business use case.',0.67,5.93,12.0,0.84,23,bold=True)
finish('Historical test counts, not rerun here  /  Recovery is service-specific, not universal')

# 13
begin('The team and direction','Six profiles connecting product, infrastructure and value.',note='Reference team supplied by the project owner, not newly approved staffing. Markets and business model still require validation. Investor discussions may seek introductions to pilot organizations and evidence criteria. No funding amount or approved budget is invented.')
team=[('Etienne MURZEAU','Project lead, AI and infrastructure'),('Rayan CHOUCHANE','Infrastructure, GitOps and pipeline'),('Alex YAO','Infrastructure and provisioning'),('Vidjay VELAYOUDAM','FinOps and business'),('Abdel-Waheb SAKKAL','Infrastructure, services and recovery'),('Priyank SOLANKI','Full-stack web engineering')]
for i,(name,role) in enumerate(team):
    col=i%2; row=i//2; x=0.65+col*6.25; y=2.19+row*0.96
    text(name,x,y,5.7,0.39,18,bold=True)
    text(role,x,y+0.43,5.7,0.36,14,MUTED)
rect(0.60,5.55,12.14,1.08,PALE)
text('Our direction: make your specific expertise reproducible.',0.92,5.80,11.50,0.60,25,BLUE,True)
finish('EFREI Paris  /  Next evidence: a business pilot and measured value assessment')

# 14
begin('Appendix / sources and scope','References, images and limitations.',note='Sources consulted for this presentation. The Catbox URL returned an empty body and is not embedded. The structure image is the original local framing; no identity between these images is asserted. The pyramid is attributed to Platform Engineering; reuse rights require confirmation before public or commercial distribution. PDF is generated directly from the same layout code as PPTX, not converted from PowerPoint. No GitHub push was performed for this presentation.',source=[1,2,3,4])
refs=[('01','IPE: principles and context','Platform Engineering article and supplied pyramid.',SOURCES[1]),('02','Hermes Agent: definition','Official Nous Research documentation.',SOURCES[2]),('03','KuberWeaver: scope and architecture','Requirements, README, verification records and local diagram.',None)]
for i,(n,title,body,url) in enumerate(refs):
    y=2.11+i*1.03
    text(n,0.65,y,0.51,0.41,19,BLUE,True)
    text(title,1.29,y,10.9,0.43,20,bold=True,link=url)
    text(body,1.29,y+0.49,10.9,0.40,15,MUTED)
rect(0.65,5.60,12.07,0.95,PALE)
text('Vision ≠ delivered features. Sector examples ≠ acquired customers.\nSavings, compliance and scale still require validation.',0.91,5.79,11.55,0.65,16,BLUE,True)
finish('Images attributed  /  Confirm reuse rights before public distribution  /  Detailed PowerPoint notes')

pdf.save()
if "--pdf-only" not in sys.argv:
    prs.save(OUT/'KuberWeaver_Presentation_Investisseurs.pptx')
    (OUT/'layout-checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'slides-manifest.json').write_text(json.dumps(slides,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'slides':len(slides),'text_boxes_checked':len(checks),'pdf_only':'--pdf-only' in sys.argv,'pdf':str(OUT/'KuberWeaver_Presentation_Investisseurs.pdf')},ensure_ascii=False))

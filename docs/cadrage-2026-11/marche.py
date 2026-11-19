#!/usr/bin/env python3
"""KuberWeaver market & benchmark state of the art, 2 pages, clickable links + nav."""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, PageBreak,
                                Table, TableStyle, HRFlowable)

FD = "/usr/share/fonts/truetype/dejavu"
pdfmetrics.registerFont(TTFont("DV", f"{FD}/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DVB", f"{FD}/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("DVI", f"{FD}/DejaVuSans-Oblique.ttf"))
OUT = os.path.dirname(os.path.abspath(__file__))
INK = colors.HexColor("#1a2233"); ACC = colors.HexColor("#2f6fed")
ACC2 = colors.HexColor("#0f9d8a"); MUT = colors.HexColor("#5b6472")
LINE = colors.HexColor("#c9d2e0"); BG = colors.HexColor("#f3f6fb")
LINK = "#2f6fed"

S = {
    "title": ParagraphStyle("title", fontName="DVB", fontSize=19, leading=23, textColor=INK, spaceAfter=3),
    "sub": ParagraphStyle("sub", fontName="DVI", fontSize=11, leading=15, textColor=ACC, spaceAfter=7),
    "nav": ParagraphStyle("nav", fontName="DV", fontSize=9, leading=13, textColor=MUT, spaceAfter=9),
    "h": ParagraphStyle("h", fontName="DVB", fontSize=12.5, leading=16, textColor=ACC, spaceBefore=10, spaceAfter=4),
    "b": ParagraphStyle("b", fontName="DV", fontSize=10.2, leading=14.6, textColor=INK, spaceAfter=5),
    "cell": ParagraphStyle("cell", fontName="DV", fontSize=8.4, leading=11, textColor=INK),
    "cellh": ParagraphStyle("cellh", fontName="DVB", fontSize=8.6, leading=11, textColor=colors.white),
    "ref": ParagraphStyle("ref", fontName="DV", fontSize=8, leading=10.6, textColor=MUT, spaceBefore=7),
}
def P(t, s="b"): return Paragraph(t, S[s])
def L(url, text): return f'<a href="{url}" color="{LINK}">{text}</a>'
def bullets(items): return [Paragraph("•&nbsp;&nbsp;" + t, S["b"]) for t in items]
def H(num, anchor, text):
    return Paragraph(f'<a name="{anchor}"/>{num}. {text}', S["h"])

def summary_box(text, width):
    t = Table([[Paragraph(text, S["b"])]], colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#e7f4f1")),
        ("BOX", (0, 0), (-1, -1), 0.6, ACC2),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 11), ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
    ]))
    return t

W = A4[0] - 2.8 * cm
story = []
story += [P("KuberWeaver, étude de marché & benchmark", "title"),
          P("Besoins, frictions clients, concurrence. Pour bien se lancer.", "sub"),
          P('Sommaire : '
            + L("#s1", "1 Marché & demande") + " · "
            + L("#s2", "2 Besoins & frictions") + " · "
            + L("#s3", "3 Benchmark") + " · "
            + L("#s4", "4 Positionnement") + " · "
            + L("#s5", "5 Pour se lancer"), "nav")]

# 1. Marché
story += [H(1, "s1", "Le marché & la demande")]
story += bullets([
    "<b>Le coût est le défi n°1.</b> 84% des organisations citent la gestion des dépenses cloud comme premier défi ; "
    "budgets dépassés de 17%, dépenses attendues en hausse de 28% ("
    + L("https://www.flexera.com/about-us/press-center/new-flexera-report-finds-84-percent-of-organizations-struggle-to-manage-cloud-spend", "Flexera 2025", )
    + ", &gt;750 répondants).",
    "<b>Le gaspillage repart à la hausse.</b> ~29% de dépenses IaaS/PaaS gaspillées en 2026, première hausse en cinq ans, "
    "tirée par la complexité de l'IA ("
    + L("https://www.flexera.com/blog/finops/flexera-2026-state-of-the-cloud-report-the-convergence-of-cloud-and-value/", "Flexera 2026")
    + ", N=753).",
    "<b>FinOps se structure et s'étend.</b> Optimisation et réduction du gaspillage en priorité n°1 ; le périmètre dépasse "
    "le cloud public vers SaaS, licences, cloud privé et datacenter (« Cloud+ »), avec une gestion du cloud privé en progression ("
    + L("https://data.finops.org/2025-report/", "State of FinOps 2025") + ").",
    "<b>Les plateformes internes se généralisent.</b> Portails développeur (IDP) et platform engineering pour réduire la "
    "dispersion des outils (" + L("https://backstage.io/", "Backstage") + ", écosystème "
    + L("https://www.cncf.io/", "CNCF") + ").",
    "<b>Le périmètre s'élargit vite.</b> Les équipes FinOps gèrent déjà le SaaS et les licences, et la gestion du cloud privé "
    "progresse nettement : la demande de contrôle dépasse le seul cloud public ("
    + L("https://data.finops.org/2025-report/", "State of FinOps 2025") + ").",
])
story += [P("<b>Implication.</b> La demande porte sur un contrôle de coût gouverné, y compris sur l'infrastructure existante "
            "et on-prem, pas seulement le cloud public. C'est exactement le terrain de KuberWeaver.", "b")]

# 2. Frictions
story += [H(2, "s2", "Besoins & frictions clients")]
story += [P("Frictions observées dans le marché et la documentation des acteurs, à confirmer par entretiens :", "b")]
story += bullets([
    "<b>Trop d'outils, pas de point unique</b> : machines, déploiement, supervision, coûts chacun dans leur coin.",
    "<b>Procédures manuelles</b>, peu réutilisables, peu traçables d'une équipe à l'autre.",
    "<b>Coût découvert après coup</b>, sans lien avec la décision qui l'a engagé.",
    "<b>Gouvernance faible</b> : qui a approuvé quoi, avec quelle preuve, et comment retirer proprement.",
    "<b>Peur du verrouillage</b> et du « tout remplacer » : on veut garder ses outils et ses données.",
    "<b>Compétences rares</b> : les petites équipes n'ont pas de platform team dédiée pour opérer un portail lourd.",
])
story += [P("Ces frictions convergent vers un même besoin : un point unique, gouverné, qui montre le coût avant d'agir "
            "et prouve le retrait, sans imposer de tout remplacer.", "b")]
story += [Spacer(1, 8), summary_box(
    "<b>À retenir.</b> La demande est là : coût défi n°1, gaspillage en hausse, FinOps qui s'étend jusqu'au cloud privé. "
    "Le manque : un outil simple, gouverné et centré coût, pour les équipes qui veulent garder leur infrastructure "
    "existante plutôt que tout migrer ou tout remplacer.", W)]
story += [HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=12, spaceAfter=4),
          P("KuberWeaver · étude de marché & benchmark · EMRD95 · novembre 2026.", "ref")]
story += [PageBreak()]

# 3. Benchmark
story += [H(3, "s3", "Benchmark concurrence")]
rows = [
    [L("https://www.waldur.com/", "Waldur"), "CMP / marketplace OSS", "Processeurs par service, gouvernance, open-source", "Portail lourd à opérer et à maintenir", "OSS"],
    [L("https://www.cloudbolt.io/", "CloudBolt"), "CMP hybride", "Self-service, Terraform / Ansible, gouvernance", "Commercial, coût et dépendance éditeur", "Éditeur"],
    [L("https://www.manageiq.org/", "ManageIQ"), "CMP OSS", "Lifecycle, chargeback, providers + Automate", "Appliance monolithique, lourde", "OSS"],
    [L("https://www.morpheusdata.com/", "Morpheus"), "CMP", "90+ intégrations, plugins, provisioning", "Propriétaire, complexe pour une petite équipe", "Éditeur (HPE)"],
    [L("https://backstage.io/", "Backstage"), "IDP OSS", "Catalogue, plugins, scaffolder, écosystème CNCF", "Pas d'exécution gouvernée ni de coût", "OSS"],
    [L("https://www.crossplane.io/", "Crossplane"), "Control plane", "Providers, compositions, drift, packages", "Suppose Kubernetes, pas de gate humain/coût", "OSS"],
    [L("https://spacelift.io/", "Spacelift / Scalr / env0"), "Orchestration IaC", "Policy OPA, multi-IaC, drift, self-service", "Centré run IaC, pas un service complet", "SaaS / usage"],
    [L("https://opencost.io/", "OpenCost / Kubecost / Infracost"), "FinOps", "Allocation, showback, estimation de coût", "Brique coût, pas un plan de contrôle", "OSS / SaaS"],
]
data = [[Paragraph(h, S["cellh"]) for h in ["Acteur", "Segment", "Force", "Limite pour une petite équipe", "Éco"]]]
for r in rows:
    data.append([Paragraph(c, S["cell"]) for c in r])
t = Table(data, colWidths=[W*0.185, W*0.135, W*0.27, W*0.27, W*0.14], repeatRows=1)
t.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), ACC),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG]),
    ("GRID", (0, 0), (-1, -1), 0.4, LINE),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
]))
story += [t, Spacer(1, 3),
          P("La modularité n'est pas neuve : tous ces acteurs ont déjà plugins, providers ou compositions. "
            "Le benchmark se joue donc sur le cycle de vie gouverné, le coût et la simplicité d'adoption, pas sur le seul mot « modulaire ».", "b")]

# 4. Positionnement
story += [H(4, "s4", "Positionnement : où est la place")]
story += bullets([
    "<b>Cible mal servie</b> : petites équipes infra et labos, sans platform team, qui veulent réutiliser leurs procédures sans tout remplacer.",
    "<b>Proposition</b> : noyau minimal plug-and-play + cycle de vie gouverné (approbation, preuve, retrait) + coût au centre + orchestre l'existant (on-prem inclus) + auto-hébergé.",
    "<b>Hypothèse à valider</b>, pas une promesse : la combinaison est le différenciant, à mesurer face à un ticket+script ou un portail déjà en place.",
    "<b>Pourquoi maintenant</b> : l'IA accroît la complexité et le gaspillage des coûts (Flexera 2026), FinOps arrive à maturité, et les petites équipes restent mal servies par des portails pensés pour les grandes organisations.",
])

# 5. GTM
story += [H(5, "s5", "Pour bien se lancer")]
story += bullets([
    "<b>Valider les frictions</b> : 5 à 10 entretiens avec petites équipes infra / labos, avant d'élargir le périmètre.",
    "<b>Un cas pilote</b> (service jetable) benchmarké sur le même scénario : temps, interventions, coût, preuve de retrait.",
    "<b>Éviter le frontal</b> avec les CMP/IDP établis ; entrer par le segment mal servi (petites équipes, on-prem, coût gouverné).",
    "<b>Risques</b> : marché encombré, acteurs installés, « modulaire » n'est pas une barrière à l'entrée. Preuve par l'usage, pas par le discours.",
    "<b>Mesures de succès</b> : temps de mise à disposition, nombre d'interventions humaines, coût par service, taux de retrait vérifié, et questions de support évitées.",
])
story += [P("Sources : "
            + L("https://www.flexera.com/about-us/press-center/new-flexera-report-finds-84-percent-of-organizations-struggle-to-manage-cloud-spend", "Flexera 2025")
            + " · " + L("https://www.flexera.com/blog/finops/flexera-2026-state-of-the-cloud-report-the-convergence-of-cloud-and-value/", "Flexera 2026")
            + " · " + L("https://data.finops.org/2025-report/", "State of FinOps 2025")
            + " · " + L("https://docs.waldur.com/latest/developer-guide/core-concepts/marketplace-orders/", "Waldur")
            + " · " + L("https://backstage.io/docs/overview/architecture-overview/", "Backstage")
            + " · " + L("https://docs.crossplane.io/latest/whats-crossplane/", "Crossplane")
            + " · " + L("https://developer.morpheusdata.com/docs", "Morpheus")
            + " · " + L("https://opencost.io/", "OpenCost")
            + ". Analyse du 19/11/2026 ; chiffres et capacités à re-vérifier par édition.", "ref")]

doc = SimpleDocTemplate(os.path.join(OUT, "KuberWeaver_Etat_de_l_art_Marche.pdf"),
                        pagesize=A4, leftMargin=1.4*cm, rightMargin=1.4*cm,
                        topMargin=1.4*cm, bottomMargin=1.4*cm,
                        title="KuberWeaver, étude de marché & benchmark", author="EMRD95")
doc.build(story)
import pymupdf
d = pymupdf.open(os.path.join(OUT, "KuberWeaver_Etat_de_l_art_Marche.pdf"))
print("pages =", d.page_count)
links = [l for pg in d for l in pg.get_links()]
ext = sum(1 for l in links if l.get("kind") == pymupdf.LINK_URI)
internal = sum(1 for l in links if l.get("kind") == pymupdf.LINK_GOTO)
print("links total =", len(links), "external =", ext, "internal =", internal)
for i, pg in enumerate(d):
    pg.get_pixmap(dpi=110).save(os.path.join(OUT, f"marche_p{i+1}.png"))

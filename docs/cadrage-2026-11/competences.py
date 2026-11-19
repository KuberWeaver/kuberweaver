#!/usr/bin/env python3
"""KuberWeaver team skills cartography, 2 pages, heatmap + per-person cards, public LinkedIn links."""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
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
LINE = colors.HexColor("#c9d2e0"); BG = colors.HexColor("#f3f6fb"); LINK = "#2f6fed"

S = {
    "title": ParagraphStyle("title", fontName="DVB", fontSize=18, leading=22, textColor=INK, spaceAfter=2),
    "sub": ParagraphStyle("sub", fontName="DVI", fontSize=10.5, leading=14, textColor=ACC, spaceAfter=6),
    "h": ParagraphStyle("h", fontName="DVB", fontSize=12, leading=15, textColor=ACC, spaceBefore=8, spaceAfter=4),
    "b": ParagraphStyle("b", fontName="DV", fontSize=9.4, leading=13, textColor=INK, spaceAfter=4),
    "small": ParagraphStyle("small", fontName="DV", fontSize=8, leading=10.6, textColor=MUT, spaceAfter=2),
    "ref": ParagraphStyle("ref", fontName="DV", fontSize=7.6, leading=10, textColor=MUT, spaceBefore=6),
    "cn": ParagraphStyle("cn", fontName="DVB", fontSize=9.6, leading=12.5, textColor=INK, spaceAfter=1),
    "cr": ParagraphStyle("cr", fontName="DVB", fontSize=8.2, leading=11, textColor=ACC, spaceAfter=2),
    "cb": ParagraphStyle("cb", fontName="DV", fontSize=8.2, leading=11, textColor=INK, spaceAfter=2),
    "cm": ParagraphStyle("cm", fontName="DV", fontSize=7.6, leading=10, textColor=MUT, spaceAfter=1),
    "cellc": ParagraphStyle("cellc", fontName="DV", fontSize=8, leading=10, textColor=INK, alignment=TA_CENTER),
    "rowh": ParagraphStyle("rowh", fontName="DVB", fontSize=8.2, leading=10.4, textColor=INK),
    "rowr": ParagraphStyle("rowr", fontName="DV", fontSize=6.8, leading=8.6, textColor=MUT),
    "colh": ParagraphStyle("colh", fontName="DVB", fontSize=7.6, leading=9.6, textColor=colors.white, alignment=TA_CENTER),
}
def P(t, s="b"): return Paragraph(t, S[s])
def L(url, text): return f'<a href="{url}" color="{LINK}">{text}</a>'

DOMAINS = ["Sysadmin / Infra", "DevOps / CI-CD", "Dev web / API", "IA / Agents", "FinOps / Coûts", "Gouv. / Sécu / Revue"]
# levels: 3 confirmé, 2 intermédiaire, 1 notions, 0 non revendiqué  (INDICATIF, à valider)
PEOPLE = [
    ("Etienne MURZEAU", "Lead · Infra & IA", [3, 1, 1, 2, 1, 2]),
    ("Rayan CHOUCHANE", "DevOps · GitOps & pipeline", [2, 3, 2, 0, 0, 2]),
    ("Priyank SOLANKI", "Dev web · Portail & API", [1, 1, 3, 1, 1, 1]),
    ("Vidjay VELAYOUDAM", "Sysadmin · Infrastructure", [3, 1, 0, 0, 2, 1]),
    ("Abdel-Waheb SAKKAL", "Sysadmin · Services & vérif.", [2, 1, 1, 0, 0, 2]),
    ("Alex YAO", "FinOps · Gouv. · Revue", [2, 1, 1, 1, 2, 2]),
]
LVL_BG = {3: colors.HexColor("#d8efe9"), 2: colors.HexColor("#e3ecfd"),
          1: colors.HexColor("#eef1f6"), 0: colors.white}
LVL_FG = {3: "#0f9d8a", 2: "#2f6fed", 1: "#8a93a3", 0: "#b8c0cc"}
LVL_TXT = {3: "●●●", 2: "●●", 1: "●", 0: "○"}

W = A4[0] - 2.8 * cm

def heatmap():
    name_w = W * 0.235
    col_w = (W - name_w) / 6.0
    data = [[Paragraph("Membre", S["colh"])] + [Paragraph(d, S["colh"]) for d in DOMAINS]]
    for name, role, lv in PEOPLE:
        row = [Paragraph(f'{name}<br/><font size="6.8" color="#5b6472">{role}</font>', S["rowh"])]
        for v in lv:
            row.append(Paragraph(f'<font color="{LVL_FG[v]}">{LVL_TXT[v]}</font>', S["cellc"]))
        data.append(row)
    t = Table(data, colWidths=[name_w] + [col_w] * 6, rowHeights=[28] + [34] * 6)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ACC),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BACKGROUND", (0, 1), (0, -1), BG),
    ]
    for ri, (_, _, lv) in enumerate(PEOPLE, start=1):
        for ci, v in enumerate(lv, start=1):
            style.append(("BACKGROUND", (ci, ri), (ci, ri), LVL_BG[v]))
    t.setStyle(TableStyle(style))
    return t

def box(text, width, bg=colors.HexColor("#e7f4f1"), border=ACC2):
    t = Table([[Paragraph(text, S["b"])]], colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), 0.6, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 11), ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return t

story = []
# ---- Page 1 ----
story += [P("KuberWeaver, cartographie des compétences", "title"),
          P("Six étudiants de l'EFREI Paris, leurs domaines et leur niveau au regard du projet.", "sub"),
          P("L'équipe couvre l'administration système, le DevOps, le développement web, l'infrastructure et l'IA, "
            "ainsi que le FinOps et la gouvernance. La cartographie croise les rôles du cahier des charges, les profils "
            "publics (LinkedIn, site personnel) et une auto-évaluation indicative des niveaux.", "b"),
          P("Méthode : rôles du cahier des charges v1.3/v2.0 + éléments publics vérifiés. Les niveaux sont indicatifs "
            "et doivent être validés par chaque membre ; ce n'est pas une évaluation certifiée.", "small"),
          P("Matrice de compétences (vue projet)", "h"),
          heatmap(),
          Spacer(1, 4),
          P('<font color="#0f9d8a">●●●</font> Confirmé (pratique pro / alternance) &nbsp;&nbsp; '
            '<font color="#2f6fed">●●</font> Intermédiaire (pratique projet / formation) &nbsp;&nbsp; '
            '<font color="#8a93a3">●</font> Notions &nbsp;&nbsp; '
            '<font color="#b8c0cc">○</font> Non revendiqué. &nbsp; Niveaux indicatifs, à valider par chacun.', "small")]
story += [Spacer(1, 6), box(
    "<b>Couverture du projet.</b> Sysadmin / infra : Etienne, Vidjay, Abdel-Waheb (appui réseau Rayan). "
    "DevOps / CI-CD : Rayan. Dev web / API : Priyank (appui Rayan). IA / agents : Etienne (diagnostics en lecture seule). "
    "FinOps / coûts : Alex (appui Vidjay). Gouvernance / sécurité / revue : Alex et Etienne (appui Rayan).", W)]
story += [Spacer(1, 6), box(
    "<b>Forces de l'équipe.</b> Une couverture complète du cycle de vie : infrastructure et sysadmin (Etienne, Vidjay, "
    "Abdel-Waheb), livraison automatisée (Rayan), portail et API (Priyank), contrôle des coûts et gouvernance (Alex, Vidjay), "
    "intégration IA et architecture (Etienne). Plusieurs membres sont en alternance, avec une pratique professionnelle "
    "réelle de l'infrastructure, du réseau et du développement.", W,
    bg=colors.HexColor("#eef3fe"), border=ACC)]
story += [HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=10, spaceAfter=3),
          P("KuberWeaver · cartographie des compétences · EFREI Paris · novembre 2026.", "ref")]
story += [PageBreak()]

# ---- Page 2 : fiches individuelles ----
story += [P("Fiches individuelles & profils publics", "h")]

def card(name, role, profile, domains, contrib, conf):
    return [Paragraph(name, S["cn"]), Paragraph(role, S["cr"]),
            Paragraph(profile, S["cm"]), Paragraph(conf, S["cm"]),
            Paragraph("<b>Domaines :</b> " + domains, S["cb"]),
            Paragraph("<b>Projet :</b> " + contrib, S["cb"])]

cards = [
    card("Etienne MURZEAU", "Lead · Infrastructure & IA",
         "Apprenti ingénieur infrastructure @ Air Liquide (IT/OT). EFREI M1 Réseaux & Sécurité, BTS SIO SISR. "
         + L("https://fr.linkedin.com/in/etienne-murzeau", "LinkedIn"),
         "Infra ●●●, IA ●●, gouvernance/sécurité ●●, DevOps ●, dev web ●, FinOps ●.",
         "Architecture, coordination, intégration agent, cohérence et présentation de l'acceptation ; diagnostics IA en lecture seule.",
         "Profil public vérifié."),
    card("Rayan CHOUCHANE", "DevOps · GitOps & pipeline",
         "Apprenti ingénieur réseaux & sécurité, EFREI (BUT informatique). Docker, GitHub Actions, GitLab, Python, Angular, PostgreSQL. "
         + L("https://fr.linkedin.com/in/rayan-chouchane", "LinkedIn") + " · " + L("https://rayanchouchane.com/", "site"),
         "DevOps/CI-CD ●●●, dev web ●●, infra ●●, gouvernance/sécurité ●●.",
         "Versioning, checks automatisés, pipeline de livraison, conteneurs d'exécution, conventions GitOps.",
         "Profil public vérifié."),
    card("Priyank SOLANKI", "Dev web · Portail & API",
         "EFREI cycle ingénieur LSI (Logiciels & Systèmes d'Information), développeur @ Medifirst. "
         + L("https://fr.linkedin.com/in/priyank-solanki-114b182a0", "LinkedIn"),
         "Dev web/API ●●●, DevOps ●, infra ●, IA ●, FinOps ●, gouvernance ●.",
         "Portail, API, autorisation, workflow utilisateur, composants de formulaire, approbation et suivi, tests.",
         "Profil public vérifié."),
    card("Vidjay VELAYOUDAM", "Sysadmin · Infrastructure",
         "Chargé d'infrastructure @ LMC France. EFREI M2 (5e année). "
         + L("https://fr.linkedin.com/in/vidjay-velayoudam", "LinkedIn"),
         "Sysadmin/infra ●●●, FinOps ●●, DevOps ●, gouvernance/sécurité ●.",
         "Infrastructure et cibles ; anciennement cadrage business, modèle de coût et analyse économique (appui FinOps).",
         "Profil public vérifié."),
    card("Abdel-Waheb SAKKAL", "Sysadmin · Services & vérification",
         "EFREI Paris. Activités cyber / CTF (posts publics EFREI, association One Panthéon). "
         + L("https://fr.linkedin.com/company/ctfrei", "contexte : CTF EFREI"),
         "Sysadmin/infra ●●, gouvernance/sécurité ●●, DevOps ●, dev web ●.",
         "Configuration de services, extensions/plugins, déploiement, vérification et restauration ; preuves de cycle de vie.",
         "Profil LinkedIn direct non confirmé."),
    card("Alex YAO", "FinOps · Gouvernance · Revue de code",
         "EFREI Paris (profil probable : Paul-Alex YAO). "
         + L("https://fr.linkedin.com/in/movane", "LinkedIn (probable)"),
         "FinOps ●●, gouvernance/sécurité/revue ●●, infra/IaC ●●, DevOps ●, dev web ●, IA ●.",
         "Modèle de coût et indicateurs, gouvernance, revue de code ; anciennement provisioning Terraform/Ansible et inventaire.",
         "Profil probable, à confirmer."),
]
grid = Table([[cards[0], cards[1]], [cards[2], cards[3]], [cards[4], cards[5]]],
             colWidths=[W / 2.0] * 2)
grid.setStyle(TableStyle([
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("BACKGROUND", (0, 0), (-1, -1), BG),
    ("BOX", (0, 0), (0, 0), 0.5, LINE), ("BOX", (1, 0), (1, 0), 0.5, LINE),
    ("BOX", (0, 1), (0, 1), 0.5, LINE), ("BOX", (1, 1), (1, 1), 0.5, LINE),
    ("BOX", (0, 2), (0, 2), 0.5, LINE), ("BOX", (1, 2), (1, 2), 0.5, LINE),
    ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
    ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
]))
story += [grid, Spacer(1, 6)]
story += [box(
    "<b>Points de vigilance.</b> FinOps à consolider (Alex, appui Vidjay), pas d'alternance FinOps dédiée. "
    "IA limitée à Etienne, en lecture seule, sans ML dédié. Revue de code et gouvernance à formaliser. "
    "Niveaux indicatifs à valider par chacun ; profils publics d'Abdel-Waheb non confirmé et d'Alex probable.",
    W, bg=colors.HexColor("#fbf3e6"), border=colors.HexColor("#d9a441"))]
story += [P("Sources : cahier des charges KuberWeaver v1.3/v2.0 (noms et rôles) · profils publics LinkedIn "
            "(Etienne Murzeau, Rayan Chouchane, Priyank Solanki, Vidjay Velayoudam) · site rayanchouchane.com · "
            "posts publics EFREI (Abdel-Waheb Sakkal) · profil probable Paul-Alex YAO. Éléments publics uniquement, "
            "aucune donnée personnelle sensible. Cartographie indicative, novembre 2026.", "ref")]
story += [HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=8, spaceAfter=3),
          P("KuberWeaver · cartographie des compétences · EFREI Paris · novembre 2026.", "ref")]

doc = SimpleDocTemplate(os.path.join(OUT, "KuberWeaver_Competences_Equipe.pdf"),
                        pagesize=A4, leftMargin=1.4*cm, rightMargin=1.4*cm,
                        topMargin=1.4*cm, bottomMargin=1.4*cm,
                        title="KuberWeaver, cartographie des compétences", author="EMRD95")
doc.build(story)
import pymupdf
d = pymupdf.open(os.path.join(OUT, "KuberWeaver_Competences_Equipe.pdf"))
print("pages =", d.page_count)
ls = [l for pg in d for l in pg.get_links()]
print("liens =", len(ls), "externes =", sum(1 for l in ls if l.get("kind") == pymupdf.LINK_URI))
for i, pg in enumerate(d):
    pg.get_pixmap(dpi=110).save(os.path.join(OUT, f"comp_p{i+1}.png"))

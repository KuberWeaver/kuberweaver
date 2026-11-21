#!/usr/bin/env python3
"""KuberWeaver presentation document (external reader), 2 pages, nice flowchart."""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, PageBreak,
                                Table, TableStyle, Flowable)

FD = "/usr/share/fonts/truetype/dejavu"
pdfmetrics.registerFont(TTFont("DV", f"{FD}/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DVB", f"{FD}/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("DVI", f"{FD}/DejaVuSans-Oblique.ttf"))

OUT = os.path.dirname(os.path.abspath(__file__))
INK = colors.HexColor("#1a2233"); ACC = colors.HexColor("#2f6fed")
ACC2 = colors.HexColor("#0f9d8a"); MUT = colors.HexColor("#5b6472")
LINE = colors.HexColor("#c9d2e0"); BG = colors.HexColor("#eef3fe")
BG2 = colors.HexColor("#e7f4f1"); CARD = colors.HexColor("#f3f6fb")

S = {
    "title": ParagraphStyle("title", fontName="DVB", fontSize=24, leading=28, textColor=INK, spaceAfter=3),
    "tag": ParagraphStyle("tag", fontName="DVB", fontSize=13, leading=18, textColor=ACC, spaceAfter=10),
    "h": ParagraphStyle("h", fontName="DVB", fontSize=13, leading=17, textColor=ACC, spaceBefore=13, spaceAfter=6),
    "b": ParagraphStyle("b", fontName="DV", fontSize=11, leading=18, textColor=INK, spaceAfter=7),
    "cardt": ParagraphStyle("cardt", fontName="DVB", fontSize=11, leading=15, textColor=ACC),
    "cards": ParagraphStyle("cards", fontName="DV", fontSize=9.2, leading=12.5, textColor=MUT),
}
def P(t, s="b"): return Paragraph(t, S[s])
def bullets(items): return [Paragraph("•&nbsp;&nbsp;" + t, S["b"]) for t in items]


class FlowChart(Flowable):
    def __init__(self, width):
        super().__init__(); self.width = width; self.height = 272
        self.steps = [
            ("Choisir un service", "catalogue d'offres revues et versionnées", False),
            ("Demander, voir le coût", "estimation affichée avant toute décision", False),
            ("Validation humaine", "un administrateur approuve le plan exact", True),
            ("Déploiement", "sur vos machines et outils existants", False),
            ("Vérification indépendante", "le service répond, avec une preuve", True),
            ("Service suivi, coût visible", "état et dépense consultables à tout moment", False),
            ("Retrait vérifié", "ressources supprimées, preuve conservée", True),
        ]
    def draw(self):
        c = self.canv; n = len(self.steps)
        bw = self.width * 0.56; bx = (self.width - bw) / 2 + 12; bh = 29
        gap = (self.height - n * bh) / (n - 1); ys = []
        for i, (t, sub, gate) in enumerate(self.steps):
            y = self.height - (i + 1) * bh - i * gap; ys.append(y)
            cx = bx - 20; cy = y + bh / 2
            c.setFillColor(ACC2 if gate else ACC); c.circle(cx, cy, 9.5, stroke=0, fill=1)
            c.setFillColor(colors.white); c.setFont("DVB", 9.5); c.drawCentredString(cx, cy - 3.5, str(i + 1))
            c.setFillColor(BG2 if gate else BG)
            c.setStrokeColor(ACC2 if gate else LINE); c.setLineWidth(1.3 if gate else 0.8)
            c.roundRect(bx, y, bw, bh, 5, stroke=1, fill=1)
            c.setFillColor(INK); c.setFont("DVB", 9.8); c.drawString(bx + 10, y + bh - 12, t)
            c.setFillColor(MUT); c.setFont("DV", 7.8); c.drawString(bx + 10, y + 6, sub)
            if i < n - 1:
                xm = bx + bw / 2; c.setStrokeColor(ACC); c.setLineWidth(1.3)
                c.line(xm, y, xm, y - gap); c.setFillColor(ACC)
                p = c.beginPath(); p.moveTo(xm - 3.5, y - gap + 5); p.lineTo(xm + 3.5, y - gap + 5)
                p.lineTo(xm, y - gap); p.close(); c.drawPath(p, fill=1, stroke=0)
        rx = bx + bw + 18; top = ys[0] + bh / 2; bot = ys[-1] + bh / 2
        c.setStrokeColor(ACC2); c.setLineWidth(1.2); c.setDash(3, 2)
        c.line(rx, bot, rx, top); c.line(bx + bw, bot, rx, bot); c.line(rx, top, bx + bw, top)
        c.setDash(); c.setFillColor(ACC2)
        p = c.beginPath(); p.moveTo(bx + bw + 5, top - 3.5); p.lineTo(bx + bw + 5, top + 3.5)
        p.lineTo(bx + bw, top); p.close(); c.drawPath(p, fill=1, stroke=0)
        c.saveState(); c.translate(rx + 13, (top + bot) / 2); c.rotate(90)
        c.setFont("DVB", 8); c.setFillColor(ACC2)
        c.drawCentredString(0, 0, "coût suivi · amélioration continue"); c.restoreState()


def cards(width):
    data = [[Paragraph("<b>Auto-hébergé</b>", S["cardt"]),
             Paragraph("<b>Modulaire</b>", S["cardt"]),
             Paragraph("<b>Coût maîtrisé</b>", S["cardt"])],
            [Paragraph("vos données restent chez vous", S["cards"]),
             Paragraph("des briques qu'on branche", S["cards"]),
             Paragraph("visible avant de décider", S["cards"])]]
    t = Table(data, colWidths=[width / 3.0] * 3)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD),
        ("BOX", (0, 0), (0, -1), 0.5, LINE),
        ("BOX", (1, 0), (1, -1), 0.5, LINE),
        ("BOX", (2, 0), (2, -1), 0.5, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, 0), 9), ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
        ("TOPPADDING", (0, 1), (-1, 1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def summary_box(text, width):
    t = Table([[Paragraph(text, S["b"])]], colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BG2),
        ("BOX", (0, 0), (-1, -1), 0.6, ACC2),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 11), ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
    ]))
    return t


W = A4[0] - 3.0 * cm
story = []
# ---- Page 1 : le problème, ce que c'est, le diagramme ----
story += [P("KuberWeaver", "title"),
          P("Centraliser la gestion de votre infrastructure, en gardant le contrôle des coûts.", "tag"),
          cards(W)]
story += [P("Le problème", "h")]
story += bullets([
    "Trop d'outils séparés : machines, déploiement, supervision, coûts.",
    "Des procédures manuelles, peu réutilisables, difficiles à tracer.",
    "Des dépenses découvertes après coup, sans lien avec la décision.",
])
story += [P("Qu'est-ce que KuberWeaver ?", "h"),
          P("Un portail auto-hébergé qui réunit la gestion d'infrastructure en un seul endroit. On y choisit "
            "un service, on voit son coût, une personne le valide, puis la plateforme le déploie, le vérifie et "
            "le retire proprement quand on n'en a plus besoin. KuberWeaver orchestre vos outils existants "
            "(machines, scripts de déploiement, supervision) au lieu de les remplacer, avec une trace de chaque action.")]
story += [P("Comment ça marche", "h"),
          P("Le parcours d'un service, de la demande au retrait. Les étapes en vert sont les points de "
            "contrôle humain et de preuve.", "b"),
          FlowChart(W)]
story += [PageBreak()]
# ---- Page 2 : bénéfices, public, exemple, statut ----
story += [P("Ce que ça apporte", "h")]
story += bullets([
    "<b>Un point d'entrée unique</b> pour demander, suivre et retirer un service.",
    "<b>Le coût visible avant de décider</b>, et suivi pendant toute la vie du service.",
    "<b>Rien sans validation humaine</b> ; chaque étape laisse une preuve vérifiable.",
    "<b>Vos outils conservés</b> : on les orchestre, on ne les réécrit pas.",
])
story += [P("Pour qui ?", "h")]
story += bullets([
    "Les équipes infrastructure et les PME qui veulent réutiliser leurs procédures sans tout remplacer.",
    "Les laboratoires et environnements de test qui ont besoin de services jetables, propres et tracés.",
    "Toute organisation qui veut garder la main sur ses décisions et ses coûts.",
])
story += [P("Un exemple concret", "h"),
          P("Une équipe a besoin d'un environnement de test pour quelques jours. Elle choisit un service "
            "« environnement web », voit l'estimation (quelques euros) et soumet la demande. Un administrateur "
            "valide le plan exact. KuberWeaver déploie sur une machine existante, vérifie que le service répond, "
            "puis affiche l'état et le coût. Le test terminé, le retrait est demandé et vérifié : les ressources "
            "sont supprimées et il en reste une preuve. Plus de machine oubliée qui coûte en silence.")]
story += [P("Ce qui nous distingue", "h")]
story += bullets([
    "On <b>orchestre l'existant</b> (déploiement, supervision, coûts) au lieu de tout réécrire.",
    "Un <b>noyau simple et modulaire</b> : on ajoute les briques utiles, la plateforme grandit avec vous.",
    "<b>Contrôle humain et preuve</b> à chaque étape ; le coût au centre, pas en option.",
])
story += [P("Où nous en sommes", "h"),
          P("Projet étudiant EFREI Paris. Prototype fonctionnel démontré sur un laboratoire local ; le parcours "
            "de référence (demander, approuver, déployer, vérifier, retirer) est en cours de validation. Ce n'est "
            "pas encore un service multi-entreprises hébergé.")]
story += [Spacer(1, 10), summary_box(
    "<b>En résumé.</b> KuberWeaver transforme une procédure revue en service demandé, approuvé, déployé, "
    "vérifié puis retiré, sur votre infrastructure existante, avec le coût sous contrôle et une preuve à chaque "
    "étape. Une base simple, modulaire, qui grandit avec votre organisation.", W)]

def footer(canvas, doc):
    canvas.saveState(); canvas.setFont("DV", 8); canvas.setFillColor(MUT)
    canvas.drawString(1.5*cm, 1.0*cm,
                      "KuberWeaver · projet EFREI Paris · EMRD95 · novembre 2026 · document de présentation.")
    canvas.restoreState()

doc = SimpleDocTemplate(os.path.join(OUT, "KuberWeaver_Presentation.pdf"),
                        pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                        topMargin=1.5*cm, bottomMargin=1.6*cm,
                        title="KuberWeaver", author="EMRD95")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
import pymupdf
d = pymupdf.open(os.path.join(OUT, "KuberWeaver_Presentation.pdf"))
print("pages =", d.page_count)
for i, pg in enumerate(d):
    pg.get_pixmap(dpi=110).save(os.path.join(OUT, f"pres_p{i+1}.png"))

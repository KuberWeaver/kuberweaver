#!/usr/bin/env python3
"""Generate two 2-page (recto-verso) KuberWeaver framing PDFs with diagrams."""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, PageBreak,
                                Table, TableStyle, Flowable)

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
pdfmetrics.registerFont(TTFont("DV", f"{FONT_DIR}/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DVB", f"{FONT_DIR}/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("DVI", f"{FONT_DIR}/DejaVuSans-Oblique.ttf"))

OUT = os.path.dirname(os.path.abspath(__file__))
INK = colors.HexColor("#1a2233")
ACC = colors.HexColor("#2f6fed")
ACC2 = colors.HexColor("#0f9d8a")
MUT = colors.HexColor("#5b6472")
LINE = colors.HexColor("#c9d2e0")
BG = colors.HexColor("#f3f6fb")
BG2 = colors.HexColor("#eaf3f1")

S = {
    "title": ParagraphStyle("title", fontName="DVB", fontSize=17, leading=21, textColor=INK, spaceAfter=2),
    "sub": ParagraphStyle("sub", fontName="DVI", fontSize=10.5, leading=14, textColor=ACC, spaceAfter=8),
    "h": ParagraphStyle("h", fontName="DVB", fontSize=11.5, leading=14, textColor=ACC, spaceBefore=8, spaceAfter=3),
    "b": ParagraphStyle("b", fontName="DV", fontSize=9.6, leading=13.2, textColor=INK, spaceAfter=4, alignment=TA_LEFT),
    "small": ParagraphStyle("small", fontName="DV", fontSize=8.2, leading=11, textColor=MUT, spaceAfter=2),
    "cell": ParagraphStyle("cell", fontName="DV", fontSize=8.3, leading=10.6, textColor=INK),
    "cellh": ParagraphStyle("cellh", fontName="DVB", fontSize=8.4, leading=10.6, textColor=colors.white),
    "ref": ParagraphStyle("ref", fontName="DV", fontSize=7.4, leading=9.6, textColor=MUT, spaceBefore=6),
}

def P(t, s="b"): return Paragraph(t, S[s])

def bullets(items, s="b"):
    return [Paragraph("•&nbsp;&nbsp;" + t, S[s]) for t in items]

def table(headers, rows, widths):
    data = [[Paragraph(h, S["cellh"]) for h in headers]]
    for r in rows:
        data.append([Paragraph(c, S["cell"]) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACC),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG]),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


class SimpleFlow(Flowable):
    """Vertical request->retire flow with a feedback loop."""
    def __init__(self, width):
        super().__init__()
        self.width = width
        self.height = 232
        self.steps = [
            ("Catalogue de services", "offres revues, versionnées"),
            ("Demande + paramètres", "opérateur ou agent scopé"),
            ("Plan exact + coût estimé", "digest, pins, estimation"),
            ("Approbation humaine", "admin, rien sans accord"),
            ("Exécution (provider)", "IaC / Ansible / runtime"),
            ("Vérification indépendante", "preuve, pas juste 'vert'"),
            ("Service géré + coût suivi", "état désiré / observé"),
            ("Retrait vérifié", "ressources absentes, preuve"),
        ]

    def draw(self):
        c = self.canv
        n = len(self.steps)
        bw = self.width * 0.60
        bx = (self.width - bw) / 2 - 18
        bh = 22
        gap = (self.height - n * bh) / (n - 1)
        ys = []
        for i, (title, sub) in enumerate(self.steps):
            y = self.height - (i + 1) * bh - i * gap
            ys.append(y)
            fill = BG2 if i in (3, 5, 7) else BG
            c.setFillColor(fill)
            c.setStrokeColor(LINE)
            c.roundRect(bx, y, bw, bh, 4, stroke=1, fill=1)
            c.setFillColor(INK)
            c.setFont("DVB", 8.6)
            c.drawString(bx + 8, y + bh - 9, title)
            c.setFillColor(MUT)
            c.setFont("DV", 7.2)
            c.drawString(bx + 8, y + 4, sub)
            if i < n - 1:
                xmid = bx + bw / 2
                c.setStrokeColor(ACC)
                c.setLineWidth(1.1)
                c.line(xmid, y, xmid, y - gap)
                c.setFillColor(ACC)
                p = c.beginPath()
                p.moveTo(xmid - 3, y - gap + 4)
                p.lineTo(xmid + 3, y - gap + 4)
                p.lineTo(xmid, y - gap)
                p.close()
                c.drawPath(p, fill=1, stroke=0)
        # feedback loop on the right
        rx = bx + bw + 16
        top = ys[0] + bh / 2
        bot = ys[-1] + bh / 2
        c.setStrokeColor(ACC2)
        c.setLineWidth(1.1)
        c.setDash(3, 2)
        c.line(rx, bot, rx, top)
        c.line(bx + bw, bot, rx, bot)
        c.line(rx, top, bx + bw, top)
        c.setDash()
        c.setFillColor(ACC2)
        p = c.beginPath()
        p.moveTo(bx + bw + 4, top - 3)
        p.lineTo(bx + bw + 4, top + 3)
        p.lineTo(bx + bw, top)
        p.close()
        c.drawPath(p, fill=1, stroke=0)
        c.saveState()
        c.translate(rx + 11, (top + bot) / 2)
        c.rotate(90)
        c.setFont("DVB", 7.6)
        c.setFillColor(ACC2)
        c.drawCentredString(0, 0, "coût · drift · apprentissage (boucle continue)")
        c.restoreState()


class TechArch(Flowable):
    """Layered microkernel architecture."""
    def __init__(self, width):
        super().__init__()
        self.width = width
        self.height = 250

    def _box(self, c, x, y, w, h, title, sub, fill, tcolor=INK, fs=8.2):
        c.setFillColor(fill)
        c.setStrokeColor(LINE)
        c.roundRect(x, y, w, h, 4, stroke=1, fill=1)
        c.setFillColor(tcolor)
        c.setFont("DVB", fs)
        c.drawCentredString(x + w / 2, y + h - 11, title)
        if sub:
            c.setFont("DV", 6.8)
            c.setFillColor(MUT if tcolor == INK else colors.HexColor("#e8eefc"))
            c.drawCentredString(x + w / 2, y + 5, sub)

    def _arrow(self, c, x, y1, y2):
        c.setStrokeColor(ACC)
        c.setLineWidth(1.1)
        c.line(x, y1, x, y2)
        c.setFillColor(ACC)
        p = c.beginPath()
        p.moveTo(x - 3, y2 + 4)
        p.lineTo(x + 3, y2 + 4)
        p.lineTo(x, y2)
        p.close()
        c.drawPath(p, fill=1, stroke=0)

    def draw(self):
        c = self.canv
        W = self.width
        main_w = W * 0.74
        side_x = main_w + 12
        side_w = W - side_x
        # layer 1: clients
        cw = (main_w - 2 * 8) / 3
        y = self.height - 34
        for i, (t, s) in enumerate([("CLI kw", "référence / script"), ("GUI React", "brique présentation"), ("Agent / MCP", "jeton scopé")]):
            self._box(c, i * (cw + 8), y, cw, 30, t, s, BG)
        self._arrow(c, main_w / 2, y, y - 14)
        # layer 2: kernel
        y2 = y - 14 - 40
        self._box(c, 0, y2, main_w, 40, "API, le noyau (microkernel)",
                  "auth/rôles · admission · approbation · jobs sérialisés · état · audit", ACC, colors.white, 9)
        self._arrow(c, main_w / 2, y2, y2 - 14)
        # layer 3: registry
        y3 = y2 - 14 - 24
        self._box(c, 0, y3, main_w, 24, "Registre de providers (contrats versionnés, pins, provenance)", "", BG2, INK, 8)
        self._arrow(c, main_w / 2, y3, y3 - 14)
        # layer 4: providers
        y4 = y3 - 14 - 30
        provs = ["inventory", "execution", "secrets", "notification", "diagnostics", "cost"]
        pw = (main_w - 5 * 5) / 6
        for i, t in enumerate(provs):
            self._box(c, i * (pw + 5), y4, pw, 28, t, "", BG, INK, 7.4)
        self._arrow(c, main_w / 2, y4, y4 - 14)
        # layer 5: targets
        y5 = y4 - 14 - 26
        self._box(c, 0, y5, main_w, 26, "Cibles existantes : ESXi · QEMU/KVM · OCI · cloud · réseau",
                  "on gouverne l'existant, on ne le remplace pas", BG2, INK, 8)
        # side column: cross-cutting stores
        sy = self.height - 34
        for t, s in [("PostgreSQL", "état durable"), ("Vault", "secrets (réf.)"), ("Git", "déclarations"), ("Observabilité", "Grafana / Prom.")]:
            self._box(c, side_x, sy, side_w, 30, t, s, colors.HexColor("#fbf3e6"), INK, 7.8)
            sy -= 38
        c.setStrokeColor(LINE)
        c.setDash(2, 2)
        c.line(main_w, y2 + 20, side_x, y2 + 20)
        c.setDash()


def build(path, story):
    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.4 * cm, bottomMargin=1.3 * cm,
                            title="KuberWeaver", author="EMRD95")
    doc.build(story)


W = A4[0] - 3.0 * cm

# ---------------- Rendu 1 ----------------
r1 = []
r1 += [P("KuberWeaver, Vision & état de l'art", "title"),
       P("Centraliser la gestion d'infra, contrôler les coûts, grandir avec l'entreprise.", "sub")]
r1 += [P("Le problème", "h"),
       P("Gérer une infra aujourd'hui, c'est jongler entre hyperviseurs, IaC, monitoring, ITSM et tableurs de coûts. "
         "Les procédures sont manuelles, dispersées, peu réutilisables, et le coût n'est pas gouverné au moment de la décision. "
         "Résultat : prolifération d'outils, peu de traçabilité, et des dépenses découvertes après coup.")]
r1 += [P("L'idée en une phrase", "h"),
       P("Un plan de contrôle auto-hébergé et modulaire qui transforme une procédure revue en service "
         "<b>demandé → approuvé → exécuté → vérifié → retiré</b>, sur l'infra qu'on possède déjà, avec le coût au centre de chaque décision.")]
r1 += [P("La philosophie : modulaire comme Hermes", "h")]
r1 += bullets([
    "<b>Noyau minimal, stable.</b> Le cœur ne fait que l'essentiel : identité, politique, approbation, jobs, état, audit. Il ne grossit pas.",
    "<b>Tout le reste est une brique qu'on branche.</b> Providers, templates, interfaces : on ajoute la couche qu'on veut, quand on veut, sans toucher au noyau.",
    "<b>Plug-and-play minimaliste.</b> On part d'une base simple avec l'essentiel, configurable, puis la plateforme grandit avec l'entreprise.",
    "<b>Divulgation progressive.</b> Comme les skills d'Hermes, on ne charge que ce dont on a besoin ; pas de usine à gaz imposée.",
    "<b>Elle apprend.</b> Chaque procédure revue et chaque check devient un acquis réutilisable du catalogue : la mémoire de l'organisation, pas un ML obligatoire.",
])
r1 += [P("Comment ça marche (vue simple)", "h"), SimpleFlow(W), Spacer(1, 4)]
r1 += [PageBreak()]

r1 += [P("État de l'art : ce qui existe déjà", "h"),
       P("La modularité n'est pas neuve. Plusieurs acteurs ont déjà des providers / plugins / compositions. "
         "Notre valeur doit donc être la <b>combinaison</b>, pas le seul mot « modulaire ».", "b")]
r1 += [table(
    ["Projet", "Ce qu'il fait", "Modularité", "Limite pour nous"],
    [
        ["Waldur", "Marketplace / CMP open-source, orders & processors", "Processeurs par type de service", "Portail lourd, cycle de vie à câbler"],
        ["CloudBolt", "CMP hybrid self-service, Terraform/Ansible", "Scripts & intégrations", "Commercial, éditeur-dépendant"],
        ["ManageIQ", "CMP open-source, lifecycle, chargeback", "Providers + Automate (Ruby)", "Appliance monolithique, lourd"],
        ["Backstage", "Portail développeur (IDP), catalogue", "Plugins front/back, scaffolder", "Pas d'exécution gouvernée ni de coût"],
        ["Crossplane", "Control plane K8s, desired state + drift", "Providers + compositions + packages", "Suppose Kubernetes, pas de gate humain/coût"],
        ["Spacelift / Scalr / env0", "Orchestration IaC, policy OPA, drift", "Multi-IaC, policy-as-code", "Centré run IaC, pas service complet"],
        ["OpenCost / Kubecost", "Allocation / showback / chargeback", "Standard ouvert, multi-cloud", "Brique coût, pas un plan de contrôle"],
    ],
    [W * 0.20, W * 0.30, W * 0.26, W * 0.24])]
r1 += [Spacer(1, 4), P("Notre différenciation (hypothèse à valider, pas une promesse)", "h")]
r1 += bullets([
    "<b>Noyau plug-and-play + cycle de vie gouverné</b> (approbation, pins, vérification, retrait) <b>+ coût au centre</b>, auto-hébergé, agent-capable.",
    "On ne réinvente pas les moteurs : on <b>gouverne</b> OpenTofu/Terraform, Ansible, OpenCost/Infracost, Grafana/Prometheus existants.",
    "À mesurer sur un même scénario vs ticket+script ou un portail déjà en place : temps, interventions, coût, preuve de retrait. Si aucun gain, on retire le pitch.",
])
r1 += [P("Cible & MVP", "h"),
       P("Premier client : une petite équipe infra. Un service jetable, une cible existante, un parcours bout-en-bout prouvé via le CLI "
         "(demander → approuver → exécuter → vérifier → retirer). Le reste est gelé tant que ce parcours n'est pas prouvé.")]
r1 += [P("Références : docs.crossplane.io (What's Crossplane) · backstage.io/docs/overview/architecture-overview · "
         "manageiq.org/docs/get-started/concepts · docs.waldur.com (marketplace-orders) · cloudbolt.io/cmp/self-service · "
         "developer.morpheusdata.com/docs · opencost.io · scalr.com/learning-center/spacelift-alternatives. "
         "Analyse du 19/11/2026 ; capacités à re-vérifier par édition.", "ref")]
build(os.path.join(OUT, "Rendu1_Vision_Etat_de_l_art.pdf"), r1)

# ---------------- Rendu 2 ----------------
r2 = []
r2 += [P("KuberWeaver, Design technique de A à Z", "title"),
       P("Microkernel + briques à contrat : comment (re)construire la plateforme.", "sub")]
r2 += [P("Principe d'architecture", "h"),
       P("Un <b>microkernel</b> : le noyau porte identité/rôles, admission/politique, approbation, jobs sérialisés, état durable (PostgreSQL), "
         "audit et registre de providers. Tout le reste est une brique remplaçable. Les clients (CLI, GUI, agent) ne sont que des clients de la même API.")]
r2 += [P("Architecture technique", "h"), TechArch(W), Spacer(1, 4)]
r2 += [P("Les 3 surfaces d'extension", "h"),
       table(
           ["Surface", "Usage", "Frontière"],
           [
               ["Template déclaratif", "Paramètres + procédure sur un moteur existant", "Aucune logique noyau ni React requise"],
               ["Plugin exécutable", "Nouveau provider (inventory, execution, secrets, cost…)", "Contrat de famille, dépendances, privilèges explicites"],
               ["Interface spécialisée", "Console / éditeur au-delà du formulaire standard", "Contrat UI séparé, pas de JS tiers auto-chargé"],
           ],
           [W * 0.24, W * 0.42, W * 0.34])]
r2 += [PageBreak()]

r2 += [P("Le contrat de provider : la clé pour « tout brancher »", "h")]
r2 += bullets([
    "<b>Interface fermée et versionnée</b> : schéma d'inputs, résultat, erreurs, opérations (read / prepare / execute / verify / rollback / decommission).",
    "<b>Provenance & pins</b> : on épingle la source et la config ; un changement invalide l'approbation. Readiness locale, jamais un healthcheck distant implicite.",
    "<b>Aucun secret dans les plans / logs / réponses</b> : le vault ne stocke que des références, résolues à l'exécution.",
    "<b>Le noyau reste la seule porte</b> : rôles, approbation et audit s'appliquent à tous les providers, sans chemin privilégié.",
])
r2 += [P("(Re)construire de A à Z, les phases", "h")]
r2 += bullets([
    "<b>0. Noyau minimal</b> : auth/rôles, admission, approbation, un worker sérialisé, état PostgreSQL, audit. CLI <font name='DVB'>kw</font> comme client de référence.",
    "<b>1. Premier provider + premier template</b> : le MVP prouvé bout-en-bout sur une cible (runtime local ou ESXi), avec vérification et retrait.",
    "<b>2. Couche coût</b> : estimation à la demande + allocation/showback. Brancher OpenCost/Infracost comme provider cost, ne pas réinventer.",
    "<b>3. Catalogue gouverné</b> : artefacts en metadata → recette revue → offre versionnée. Importer n'exécute rien.",
    "<b>4. Cycle de vie complet</b> : update / retire / drift par provider, preuve de retrait, reprise après interruption sans replay.",
    "<b>5. Croissance</b> : on ajoute providers et couches à la demande ; le noyau ne change pas. « Apprend » = capitaliser procédures revues + checks dans le catalogue.",
])
r2 += [P("Ce qu'on branche, ce qu'on ne refait pas", "h"),
       P("IaC = OpenTofu/Terraform existant · coût = OpenCost/Infracost · monitoring = Grafana/Prometheus externes · "
         "secrets = vault remplaçable. On gouverne ces moteurs, on ne les réécrit pas. C'est ça qui garde le noyau petit.")]
r2 += [P("Garde-fous immuables & risques", "h")]
r2 += bullets([
    "<b>Immuables</b> : modularité, une seule porte (API), approbation humaine, pins/provenance, pas de secret dans les plans, PostgreSQL requis, retrait vérifié.",
    "<b>Étendue</b> → geler le hors-MVP (marketplace exécutable, multi-tenant, cloud, Redis, drift auto). <b>« Apprend » sans ML</b> → catalogue de procédures + checks. <b>Modulaire ≠ usine à gaz</b> → contrats fermés, noyau petit.",
])
r2 += [P("Références : docs.crossplane.io · backstage.io/docs/overview/architecture-overview · developer.morpheusdata.com/docs · "
         "docs.waldur.com (marketplace-orders) · opencost.io · opentofu.org/docs/cli/commands/providers/lock. "
         "Design proposé, distinct des capacités livrées (voir COMPLETION_STATUS.md). 19/11/2026.", "ref")]
build(os.path.join(OUT, "Rendu2_Design_Technique_A_a_Z.pdf"), r2)

# verify page counts
import pymupdf
for f in ["Rendu1_Vision_Etat_de_l_art.pdf", "Rendu2_Design_Technique_A_a_Z.pdf"]:
    d = pymupdf.open(os.path.join(OUT, f))
    print(f, "pages =", d.page_count)

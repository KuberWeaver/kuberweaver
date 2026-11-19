#!/usr/bin/env python3
import re, os
OUT = os.path.dirname(os.path.abspath(__file__))
svg = open(os.path.join(OUT, "arch.svg"), encoding="utf-8").read()
svg = re.sub(r'<\?xml[^>]*\?>', '', svg).strip()
# let the SVG scale to its container width
svg = svg.replace('width="100%"', 'width="100%" preserveAspectRatio="xMidYMid meet"', 1)

html = """<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><style>
@page { size: A4 landscape; margin: 9mm; }
* { box-sizing: border-box; }
html,body { margin:0; padding:0; font-family: "DejaVu Sans", Arial, sans-serif; color:#1a2233; }
.page { width:100%; }
h1 { font-size:17pt; margin:0 0 1pt 0; }
.sub { font-size:9.5pt; color:#2f6fed; font-weight:bold; margin:0 0 6pt 0; }
.diagram { width:100%; border:0.6pt solid #c9d2e0; border-radius:6pt; background:#fff; padding:3pt; text-align:center; }
.diagram svg { width:auto; max-width:100%; max-height:355pt; height:auto; display:inline-block; }
.row { display:flex; gap:7pt; margin-top:6pt; }
.card { flex:1; border:0.6pt solid #c9d2e0; border-radius:5pt; padding:5pt 7pt; background:#f3f6fb; }
.card h2 { font-size:9pt; margin:0 0 2pt 0; color:#2f6fed; }
.card p, .card li { font-size:7.6pt; line-height:1.28; margin:0; }
.card ul { margin:0; padding-left:11pt; }
.legend span { display:inline-block; margin-right:8pt; font-size:7.6pt; }
.dot { display:inline-block; width:7pt; height:7pt; border-radius:2pt; margin-right:3pt; vertical-align:middle; }
.flow { display:flex; gap:3pt; margin-top:6pt; align-items:stretch; }
.step { flex:1; background:#e7f4f1; border:0.6pt solid #0f9d8a; border-radius:5pt; padding:3pt 4pt; font-size:7pt; line-height:1.2; }
.step b { color:#0f9d8a; display:block; font-size:7.6pt; }
.arrow { align-self:center; color:#0f9d8a; font-size:10pt; }
.foot { margin-top:5pt; font-size:6.8pt; color:#5b6472; border-top:0.5pt solid #c9d2e0; padding-top:2pt; }
</style></head><body><div class="page">
<h1>KuberWeaver — Architecture technique</h1>
<div class="sub">Un noyau minimal, des briques qu'on branche, le coût au centre — sur l'infra existante. Vue complète, une page.</div>
<div class="diagram">__SVG__</div>
<div class="row">
  <div class="card legend" style="flex:1.3">
    <h2>Légende & outils branchés</h2>
    <p>
      <span><i class="dot" style="background:#e3ecfd;border:0.6pt solid #2f6fed"></i>Clients</span>
      <span><i class="dot" style="background:#d8efe9;border:0.6pt solid #0f9d8a"></i>Noyau / cycle de vie</span>
      <span><i class="dot" style="background:#fbf3e6;border:0.6pt solid #d9a441"></i>Providers</span>
      <span><i class="dot" style="background:#eef1f6;border:0.6pt solid #8a93a3"></i>Cibles</span>
      <span><i class="dot" style="background:#efe9fb;border:0.6pt solid #a78bfa"></i>Transverse</span>
    </p>
    <p style="margin-top:3pt">execution = OpenTofu / Ansible · cost = OpenCost / Infracost · réseau = GNS3 / Cisco / pfSense · secrets = Vault (références) · état = PostgreSQL · observation = Grafana / Prometheus (externes).</p>
  </div>
  <div class="card" style="flex:1.7">
    <h2>Règles immuables (quel que soit le client ou le provider)</h2>
    <ul>
      <li>Une seule porte : l'API. CLI, GUI et agent appliquent les mêmes rôles et pins.</li>
      <li>Approbation humaine avant toute exécution ; rien ne se déploie sans accord.</li>
      <li>Pins de source et de plan exact ; un changement invalide l'approbation.</li>
      <li>Vérification indépendante, puis retrait vérifié (ressources absentes, preuve).</li>
      <li>Worker sérialisé, non-rejeu au redémarrage ; aucun secret dans les plans / logs.</li>
    </ul>
  </div>
</div>
<div class="flow">
  <div class="step"><b>1 · Demande</b>Choix d'une offre revue, paramètres.</div><div class="arrow">→</div>
  <div class="step"><b>2 · Plan + coût</b>Plan exact épinglé, coût estimé.</div><div class="arrow">→</div>
  <div class="step"><b>3 · Approbation</b>Un admin valide le plan.</div><div class="arrow">→</div>
  <div class="step"><b>4 · Exécution</b>Le provider déploie sur la cible.</div><div class="arrow">→</div>
  <div class="step"><b>5 · Vérification</b>Preuve indépendante que ça marche.</div><div class="arrow">→</div>
  <div class="step"><b>6 · Service + coût</b>État et dépense suivis.</div><div class="arrow">→</div>
  <div class="step"><b>7 · Retrait</b>Ressources supprimées, preuve.</div>
</div>
<div class="foot">KuberWeaver · architecture technique · EFREI Paris · novembre 2026 · source interactive : arch.md (Mermaid, cliquable / zoomable dans VS Code, GitHub ou mermaid.live).</div>
</div></body></html>"""
html = html.replace("__SVG__", svg)
open(os.path.join(OUT, "arch_page.html"), "w", encoding="utf-8").write(html)
print("html written", len(html))

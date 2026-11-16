import { expect, it } from "vitest";
import indexHtml from "../index.html?raw";
const sources = import.meta.glob<string>("./*.{ts,tsx}", { query: "?raw", import: "default", eager: true });
import { money, date, statuses, emptyGraph } from "./model";
it("serves an English portal with English status, date, currency and default draft labels", () => {
  expect(indexHtml).toContain('lang="en"');
  expect(statuses.pending_approval).toBe("Awaiting approval");
  expect(emptyGraph().name).toBe("My blueprint");
  expect(date("2026-01-10T12:00:00Z")).toMatch(/Jan/);
  expect(money(1234)).toContain("1,234");
});
it("contains no French UI literals or prototype badges", () => {
  for (const [name, source] of Object.entries(sources).filter(([n]) => !n.includes("test"))) {
    expect(source, name).not.toMatch(/Chargement|Réessayer|Sélectionner|Déploiement|Aucun |Désactiv|Connexions|Réponse serveur|Planifié|prototype|fr-FR|Disponible|Erreur|Actif|Modèle|Constat|Demande…|livraison confirmée/i);
  }
});

import { expect, it } from "vitest";
import { capabilityState } from "./capabilities";
it("ne présente jamais un provider seulement configuré comme connecté", () => {
  expect(capabilityState({ status: "configured" })).toEqual({
    label: "Configuré",
    tone: "neutral",
  });
  expect(capabilityState({ status: "connected" }).label).toBe("Connecté");
});
it("rend explicites les extensions chargées, désactivées et en erreur", () => {
  expect(capabilityState({ status: "loaded" })).toEqual({
    label: "Activée",
    tone: "success",
  });
  expect(capabilityState({ enabled: false }).label).toBe("Désactivée");
  expect(capabilityState({ status: "error" }).tone).toBe("danger");
});

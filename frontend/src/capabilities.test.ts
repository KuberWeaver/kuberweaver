import { expect, it } from "vitest";
import { capabilityState } from "./capabilities";
it("ne présente jamais un provider seulement configuré comme connecté", () => {
  expect(capabilityState({ status: "configured" })).toEqual({
    label: "Configured",
    tone: "neutral",
  });
  expect(capabilityState({ status: "connected" }).label).toBe("Connected");
});
it("rend explicites les extensions chargées, désactivées et en erreur", () => {
  expect(capabilityState({ status: "loaded" })).toEqual({
    label: "Enabled",
    tone: "success",
  });
  expect(capabilityState({ enabled: false }).label).toBe("Disabled");
  expect(capabilityState({ status: "error" }).tone).toBe("danger");
});

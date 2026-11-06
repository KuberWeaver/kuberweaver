// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Operations } from "./Operations";

const admin = { id: "admin", username: "admin", role: "admin" };
const payload = { schema_version: 1, capabilities: [
  { id: "notifications", label: "Notifications externes", kind: "notifications", provider: "webhook", configured: true, selected: true, status: "configured", config_file: "notifications.json" },
  { id: "diagnostics-llm", label: "Diagnostic IA en lecture seule", kind: "diagnostics", provider: "llm", configured: false, selected: false, status: "unconfigured", config_file: "diagnostics-llm.json" },
] };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("renders explicit unavailable integration state without claiming activation", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(payload))));
  render(<Operations user={admin} />);
  expect(await screen.findByText("Diagnostic IA en lecture seule")).toBeTruthy();
  expect(screen.getAllByText("À configurer")).toHaveLength(1);
  expect(screen.getByText("diagnostics-llm.json")).toBeTruthy();
});
it("retries notifications only after user action and confirms readback", async () => {
  const fetcher = vi.fn(async (_path: string, options?: RequestInit) => new Response(JSON.stringify(options?.method === "POST" ? { result: { delivered: 1, failed: 0 } } : payload)));
  vi.stubGlobal("fetch", fetcher);
  render(<Operations user={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Réessayer les notifications en attente" }));
  expect(await screen.findByText(/1 livraison confirmée/)).toBeTruthy();
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
  expect(fetcher.mock.calls.filter(([, options]) => !options?.method)).toHaveLength(2);
});
it("does not fetch privileged state or render retry for non administrators", () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  render(<Operations user={{ ...admin, role: "viewer" }} />);
  expect(screen.getByText(/Réservé aux administrateurs/)).toBeTruthy();
  expect(fetcher).not.toHaveBeenCalled();
});
it.each([null, {}, { schema_version: 1, capabilities: [null] }, { schema_version: 1, capabilities: [{ id: "bad" }] }])("handles malformed operations response without rendering unvalidated entries: %j", async (data) => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(data))));
  render(<Operations user={admin} />);
  expect(await screen.findByText("Réponse serveur non prise en charge.")).toBeTruthy();
});

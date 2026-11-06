// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Diagnostics } from "./pages";
const state = { mode: "llm", model: "local-test", status: "not_run", read_only: true, findings: [], summary: { total: 0 } };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("describes selected LLM without triggering paid inference on mount", async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify(state)));
  vi.stubGlobal("fetch", fetcher);
  render(<Diagnostics />);
  expect(await screen.findByText("Diagnostic IA")).toBeTruthy();
  expect(screen.getByText(/Aucune analyse IA exécutée/)).toBeTruthy();
  expect(fetcher.mock.calls).toHaveLength(1);
  expect(screen.queryByText(/Aucun modèle d’IA configuré/)).toBeNull();
});
it("runs selected provider only on explicit click and displays returned provenance", async () => {
  const fetcher = vi.fn(async (_path: string, options?: RequestInit) => new Response(JSON.stringify(options?.method === "POST" ? { ...state, status: "cached", findings: [{ id: "f", severity: "warning", title: "Constat contrôlé", message: "Analyse", resource: "job-1", recommendation: "Revue humaine", citations: [{ job_id: "job-1", event_id: 2 }] }] } : state)));
  vi.stubGlobal("fetch", fetcher);
  render(<Diagnostics />);
  fireEvent.click(await screen.findByRole("button", { name: "Lancer une analyse explicite" }));
  expect(await screen.findByText("Constat contrôlé")).toBeTruthy();
  expect(screen.getByText(/job-1.*2/)).toBeTruthy();
  expect(fetcher.mock.calls.some(([p,o]) => p === "/api/diagnostics/run" && o?.method === "POST")).toBe(true);
});

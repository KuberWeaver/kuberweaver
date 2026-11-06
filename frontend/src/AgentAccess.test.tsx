// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AgentAccess } from "./AgentAccess";
const admin = { id: "human", username: "admin", role: "admin" };
const state = { tokens: [], available_scopes: ["read", "plan", "submit", "approve"], role_scopes: { viewer: ["read"], operator: ["read", "plan", "submit"], admin: ["read", "plan", "submit", "approve"] } };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear(); });
it("issues narrowly scoped token only on explicit action and never persists clear value in browser storage", async () => {
  const meta = { id: "service-1", label: "Agent de test", role: "operator", scopes: ["read"], expires_at: "2027-01-01T00:00:00Z", revoked_at: null };
  let created = false;
  const fetcher = vi.fn(async (_path: string, options?: RequestInit) => {
    if (options?.method === "POST") { created = true; return new Response(JSON.stringify({ token: "kw-test-one-time-value", metadata: meta })); }
    return new Response(JSON.stringify({ ...state, tokens: created ? [meta] : [] }));
  });
  vi.stubGlobal("fetch", fetcher);
  render(<AgentAccess user={admin} />);
  fireEvent.change(await screen.findByLabelText("Nom de l’agent"), { target: { value: "Agent de test" } });
  fireEvent.click(screen.getByLabelText("read"));
  fireEvent.click(screen.getByRole("button", { name: "Créer un accès agent" }));
  expect(await screen.findByText("kw-test-one-time-value")).toBeTruthy();
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  expect(fetcher.mock.calls.some(([,o]) => o?.method === "POST" && JSON.parse(o.body as string).role === "operator")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Masquer le jeton" }));
  expect(screen.queryByText("kw-test-one-time-value")).toBeNull();
});
it("accepts the numeric expiry timestamps emitted by the real API", async () => {
  const row = { id: "numeric", label: "Timestamp agent", role: "viewer", scopes: ["read"], expires_at: 1800000000, revoked_at: null };
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...state, tokens: [row] }))));
  render(<AgentAccess user={admin} />);
  expect(await screen.findByText("Timestamp agent")).toBeTruthy();
  expect(screen.getByText(`Expire : ${new Date(row.expires_at * 1000).toISOString()}`)).toBeTruthy();
});
it("does not fetch or expose creation controls to non administrators", () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  render(<AgentAccess user={{ ...admin, role: "operator" }} />);
  expect(screen.getByText(/Réservé aux administrateurs/)).toBeTruthy();
  expect(fetcher).not.toHaveBeenCalled();
});

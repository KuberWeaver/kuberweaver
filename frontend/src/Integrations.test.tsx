// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Integrations } from "./pages";
import { setCsrf } from "./api";

const admin = { id: "admin", username: "admin", role: "admin" };
const providers = {
  bindings: { inventory: "local", diagnostics: "rules", secrets: "missing" },
  available: [
    { kind: "inventory", name: "local", available: true, status: "configured" },
    { kind: "inventory", name: "replacement", available: true, description: "Replacement inventory" },
    { kind: "diagnostics", name: "rules", available: true },
    { kind: "inventory", name: "offline", available: false },
    { kind: "secrets", name: "missing", available: false },
  ], errors: [{ kind: "secrets", error: "Selected provider unavailable" }],
};
function serve(value: unknown = providers, status = 200) {
  const fetcher = vi.fn(async (path: string) => new Response(JSON.stringify(
    path === "/api/providers" ? value : path === "/api/extensions" ? { extensions: [] } : [],
  ), { status: path === "/api/providers" ? status : 200 }));
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("lists API-driven choices per kind and marks unavailable active bindings", async () => {
  serve();
  render(<Integrations user={admin} />);
  const inventory = await screen.findByRole("combobox", { name: "Provider inventory" });
  expect(within(inventory).getByRole("option", { name: /replacement/ })).toBeTruthy();
  expect(within(inventory).queryByRole("option", { name: /rules/ })).toBeNull();
  expect((within(inventory).getByRole("option", { name: /offline/ }) as HTMLOptionElement).disabled).toBe(true);
  expect(screen.getByText("Selected provider unavailable")).toBeTruthy();
  expect(screen.getByText(/missing · Indisponible/, { selector: "small" })).toBeTruthy();
});
it.each([
  [null, 200],
  [{ bindings: [], available: {} }, 200],
  [{ detail: "Not Found" }, 404],
])("keeps integrations usable when optional provider config is unavailable: %j", async (value, status) => {
  serve(value, status as number);
  render(<Integrations user={admin} />);
  expect(await screen.findByText(/Configuration des providers indisponible/)).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Registre des extensions" })).toBeTruthy();
  expect(screen.queryByRole("combobox")).toBeNull();
});

it("saves only changed bindings using CSRF then verifies a fresh GET before success", async () => {
  const fetcher = serve();
  let changed = false;
  fetcher.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path !== "/api/providers") return new Response(JSON.stringify(path === "/api/extensions" ? { extensions: [] } : []));
    if (options?.method === "PATCH") { changed = true; return new Response("{}"); }
    return new Response(JSON.stringify(changed ? { ...providers, bindings: { ...providers.bindings, inventory: "replacement" } } : providers));
  });
  setCsrf("provider-csrf");
  render(<Integrations user={admin} />);
  fireEvent.change(await screen.findByRole("combobox", { name: "Provider inventory" }), { target: { value: "replacement" } });
  fireEvent.click(screen.getByRole("button", { name: "Enregistrer les providers" }));
  await screen.findByText("Liaisons enregistrées et vérifiées depuis le serveur.");
  const calls = fetcher.mock.calls.filter(([path]) => path === "/api/providers") as unknown as [string, RequestInit][];
  expect(calls.map(([, options]) => options?.method || "GET")).toEqual(["GET", "PATCH", "GET"]);
  expect(JSON.parse(calls[1][1].body as string)).toEqual({ bindings: { inventory: "replacement" } });
  expect(calls[1][1].headers).toMatchObject({ "X-CSRF-Token": "provider-csrf" });
  expect(calls[1][1].credentials).toBe("include");
  expect(screen.getByText("replacement · Actif")).toBeTruthy();
});

it("does not claim success when provider readback differs", async () => {
  const fetcher = serve();
  render(<Integrations user={admin} />);
  fireEvent.change(await screen.findByRole("combobox", { name: "Provider inventory" }), { target: { value: "replacement" } });
  fireEvent.click(screen.getByRole("button", { name: "Enregistrer les providers" }));
  expect(await screen.findByText(/Vérification des liaisons échouée/)).toBeTruthy();
  expect(screen.queryByText("Liaisons enregistrées et vérifiées depuis le serveur.")).toBeNull();
  await waitFor(() => expect(fetcher.mock.calls.filter(([path]) => path === "/api/providers")).toHaveLength(3));
});

it.each(["viewer", "operator"])("reserves provider mutation for administrators, not %s", async (role) => {
  serve();
  render(<Integrations user={{ ...admin, role }} />);
  const input = await screen.findByRole("combobox", { name: "Provider inventory" });
  expect((input as HTMLSelectElement).disabled).toBe(true);
  expect(screen.queryByRole("button", { name: "Enregistrer les providers" })).toBeNull();
});

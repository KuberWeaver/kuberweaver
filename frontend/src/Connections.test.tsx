// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Connections } from "./Connections";
import { App } from "./App";
import { setCsrf } from "./api";

const admin = { id: "human-admin", username: "admin", role: "admin" };
const id = "0123456789abcdef0123456789abcdef";
const registry = {
  drivers: [
    { id: "registered-driver", label: "Driver enregistré", available: true },
    { id: "offline-driver", label: "Driver unavailable", available: false },
  ],
  secret_sources: [
    { id: "vault", available: true, can_store: true, references: ["lab-admin"] },
    { id: "readonly-vault", available: true, can_store: false, references: [] },
    { id: "offline-vault", available: false, can_store: false, references: [] },
  ],
};
const connection = {
  id, label: "Hyperviseur labo", driver: "registered-driver", endpoint: "https://192.0.2.10",
  secret_ref: "lab-admin", secrets_provider: "vault", tls_sha256: null,
  enabled: true, revision: 1, observation: null,
};
const inventory = {
  server: { name: "serveur-observé", version: "8.0", api_type: "HostAgent", write_api_supported: null },
  datastores: [{ id: "ds-1", name: "Stockage laboratoire", capacity_bytes: 1000000, free_bytes: 700000, accessible: true }],
  networks: [{ id: "net-1", name: "Réseau laboratoire" }],
  resource_pools: [{ id: "pool-1", name: "Pool laboratoire" }],
  virtual_machines: [{ id: "vm-1", name: "VM observée", uuid: "vm-uuid", power_state: "poweredOff", cpu: 2, memory_mb: 2048 }],
};
const observation = {
  connection_id: id, revision: 1, status: "reachable", observed_at: "2026-11-15T18:00:00Z", inventory, error: null,
};
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
type Handler = (path: string, options?: RequestInit) => Promise<Response> | Response;
function serve(handler?: Handler) {
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => {
    if (handler) return handler(path, options);
    if (path === "/api/connection-drivers") return response(registry);
    if (path === "/api/connections") return response([connection]);
    return response({ detail: "Unexpected request" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
const newSpec = {
  label: "Autre hyperviseur", driver: "registered-driver", endpoint: "https://192.0.2.11",
  secret_ref: "new-ref", secrets_provider: "vault", tls_sha256: null, enabled: true,
};
async function fillCreation() {
  fireEvent.click(await screen.findByRole("button", { name: "New connection" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: newSpec.label } });
  fireEvent.change(screen.getByLabelText("Connection driver"), { target: { value: newSpec.driver } });
  fireEvent.change(screen.getByLabelText("HTTPS endpoint (IP address)"), { target: { value: newSpec.endpoint } });
  fireEvent.change(screen.getByLabelText("Secret source"), { target: { value: newSpec.secrets_provider } });
  fireEvent.change(screen.getByLabelText("Logical secret reference"), { target: { value: newSpec.secret_ref } });
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setCsrf(""); localStorage.clear(); sessionStorage.clear(); window.history.replaceState(null, "", window.location.pathname); });

it("charge les références sans sonde implicite et ne présume pas un accès réel", async () => {
  const fetcher = serve();
  render(<Connections user={admin} />);
  expect(await screen.findByRole("heading", { name: "Hyperviseur labo" })).toBeTruthy();
  expect(screen.getByText("https://192.0.2.10")).toBeTruthy();
  expect(screen.getByText("lab-admin")).toBeTruthy();
  expect(screen.getByText("No observations recorded. Access has not been verified.")).toBeTruthy();
  expect(fetcher.mock.calls.map(([path]) => path).sort()).toEqual(["/api/connection-drivers", "/api/connections"]);
  expect(fetcher.mock.calls.every(([, options]) => !options?.method || options.method === "GET")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Refresh connections" }));
  await screen.findByRole("heading", { name: "Hyperviseur labo" });
  expect(fetcher.mock.calls.every(([, options]) => !options?.method || options.method === "GET")).toBe(true);
  expect(within(screen.getByRole("region", { name: "Connection details" })).queryByText("Accessible")).toBeNull();
});

it.each([
  null, {}, [null], [{ ...connection, id: "malformed" }], [{ ...connection, enabled: "true" }],
  [{ ...connection, revision: -1 }], [{ ...connection, secret_ref: null }],
  [{ ...connection, tls_sha256: "not-a-fingerprint" }], [connection, connection],
  [{ ...connection, observation: {} }],
  [{ ...connection, observation: { ...observation, connection_id: "f".repeat(32) } }],
  [{ ...connection, observation: { ...observation, status: "success" } }],
  [{ ...connection, observation: { ...observation, observed_at: "not-a-date" } }],
  ...["datastores", "networks", "resource_pools", "virtual_machines"].flatMap((field) => [null, [null], [{}]].map((value) => [
    { ...connection, observation: { ...observation, inventory: { ...inventory, [field]: value } } },
  ])),
  [{ ...connection, observation: { ...observation, inventory: { ...inventory, server: null } } }],
  [{ ...connection, observation: { ...observation, inventory: { ...inventory, server: { ...inventory.server, write_api_supported: "yes" } } } }],
] .map((value) => ({ value })))("rejette une collection de connexions ou un inventaire malformé : %j", async ({ value }) => {
  serve((path) => response(path === "/api/connection-drivers" ? registry : value));
  render(<Connections user={admin} />);
  expect((await screen.findByRole("alert")).textContent).toMatch(/Unsupported server response/);
  expect(screen.queryByRole("region", { name: "Connection details" })).toBeNull();
});

it.each([
  { cause: "inventaire absent", value: { ...observation, inventory: null } },
  { cause: "erreur malgré le statut reachable", value: { ...observation, error: "Connection discovery unavailable" } },
  { cause: "inventaire malgré le statut unavailable", value: { ...observation, status: "unavailable", error: "Connection discovery unavailable" } },
  { cause: "erreur absente pour unavailable", value: { ...observation, status: "unavailable", inventory: null, error: null } },
  { cause: "erreur vide pour unavailable", value: { ...observation, status: "unavailable", inventory: null, error: "" } },
  { cause: "erreur blanche pour unavailable", value: { ...observation, status: "unavailable", inventory: null, error: " \t " } },
])("refuse au chargement une observation sémantiquement incohérente ($cause)", async ({ value }) => {
  const fetcher = serve((path) => response(path === "/api/connection-drivers" ? registry : [{ ...connection, observation: value }]));
  render(<Connections user={admin} />);
  await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());
  expect(screen.queryByText("Accessible")).toBeNull();
  expect(screen.queryByText("Unavailable")).toBeNull();
  expect(screen.queryByText("Observation saved and verified by readback.")).toBeNull();
  expect(screen.getByRole("alert").textContent).toMatch(/Unsupported server response/);
  expect(screen.queryByRole("region", { name: "Connection details" })).toBeNull();
  expect(fetcher.mock.calls.every(([, options]) => !options?.method || options.method === "GET")).toBe(true);
});

it.each([
  null, {}, { drivers: [null], secret_sources: [] }, { ...registry, drivers: [{ id: "bad", label: {}, available: true }] },
  { ...registry, secret_sources: [null] }, { ...registry, secret_sources: [{ ...registry.secret_sources[0], references: [null] }] },
  { ...registry, secret_sources: [{ ...registry.secret_sources[0], can_store: "true" }] },
])("rejette un registre de pilotes ou de coffres malformé : %j", async (value) => {
  serve((path) => response(path === "/api/connection-drivers" ? value : [connection]));
  render(<Connections user={admin} />);
  expect((await screen.findByRole("alert")).textContent).toMatch(/Unsupported server response/);
});

it("affiche la date, les révisions et les quatre inventaires observés sans prétendre installer un OS", async () => {
  serve((path) => response(path === "/api/connection-drivers" ? registry : [{ ...connection, revision: 2, observation }]));
  render(<Connections user={{ ...admin, role: "viewer" }} />);
  expect(await screen.findByText("Accessible")).toBeTruthy();
  expect(screen.getByText("Stale observation: configuration has changed since this observation.")).toBeTruthy();
  expect(document.querySelector("time")?.getAttribute("datetime")).toBe(observation.observed_at);
  expect(screen.getByText(/Observed revision: 1 · Configuration revision: 2/)).toBeTruthy();
  expect(screen.getByText("serveur-observé")).toBeTruthy();
  expect(screen.getByText("API writes: unknown capability")).toBeTruthy();
  expect(within(screen.getByRole("table", { name: "Datastores" })).getByText("Stockage laboratoire")).toBeTruthy();
  expect(within(screen.getByRole("table", { name: "Networks" })).getByText("Réseau laboratoire")).toBeTruthy();
  expect(within(screen.getByRole("table", { name: "Resource pools" })).getByText("Pool laboratoire")).toBeTruthy();
  const vms = screen.getByRole("table", { name: "Virtual machines" });
  expect(within(vms).getByText("VM observée")).toBeTruthy();
  expect(within(vms).getByText("poweredOff")).toBeTruthy();
  expect(within(vms).getByText("2048")).toBeTruthy();
  expect(within(vms).queryByRole("columnheader", { name: /OS|système installé/i })).toBeNull();
  expect(screen.queryByRole("button", { name: "Test connection" })).toBeNull();
});

it("affiche un résultat unavailable et son erreur sans fabriquer d’inventaire", async () => {
  serve((path) => response(path === "/api/connection-drivers" ? registry : [{ ...connection, observation: { ...observation, status: "unavailable", inventory: null, error: "Coffre non configuré" } }]));
  render(<Connections user={admin} />);
  expect(await screen.findByText("Unavailable")).toBeTruthy();
  expect((await screen.findByRole("alert")).textContent).toContain("Coffre non configuré");
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.getByText("No inventory returned by the server.")).toBeTruthy();
});

it("crée une référence seule avec CSRF et attend la relecture exacte avant de confirmer la configuration", async () => {
  const created = { ...connection, ...newSpec, id: "a".repeat(32) };
  let release!: (result: Response) => void;
  const readback = new Promise<Response>((resolve) => { release = resolve; });
  const fetcher = serve((path, options) => {
    if (path === "/api/connection-drivers") return response(registry);
    if (path === "/api/connections" && options?.method === "POST") return response(created, 201);
    if (path === `/api/connections/${created.id}`) return readback;
    return response([connection]);
  });
  setCsrf("native-connection-csrf");
  render(<Connections user={admin} />);
  await fillCreation();
  expect(screen.queryByLabelText("Hypervisor password")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Add connection" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([path]) => path === `/api/connections/${created.id}`)).toBe(true));
  expect(screen.queryByText("Configuration saved and verified by readback.")).toBeNull();
  expect((screen.getByRole("button", { name: "Add connection" }) as HTMLButtonElement).disabled).toBe(true);
  release(response(created));
  expect(await screen.findByText("Configuration saved and verified by readback.")).toBeTruthy();
  expect(await screen.findByRole("heading", { name: newSpec.label })).toBeTruthy();
  const writes = fetcher.mock.calls.filter(([, options]) => options?.method === "POST");
  expect(writes).toHaveLength(1);
  expect(JSON.parse(writes[0][1]!.body as string)).toEqual(newSpec);
  expect(writes[0][1]!.headers).toMatchObject({ "X-CSRF-Token": "native-connection-csrf" });
  expect(writes[0][1]!.credentials).toBe("include");
  expect(fetcher.mock.calls.some(([path]) => /test|discover|credentials/.test(path))).toBe(false);
  expect(screen.getByText("No observations recorded. Access has not been verified.")).toBeTruthy();
});

it("propose uniquement les pilotes et les sources de l’API, et désactive les entrées indisponibles", async () => {
  const fetcher = serve();
  render(<Connections user={admin} />);
  await fillCreation();
  const drivers = screen.getByLabelText("Connection driver");
  expect(within(drivers).getAllByRole("option").map((o) => (o as HTMLOptionElement).value)).toEqual(["", "registered-driver", "offline-driver"]);
  expect((within(drivers).getByRole("option", { name: /Driver unavailable/ }) as HTMLOptionElement).disabled).toBe(true);
  const sources = screen.getByLabelText("Secret source");
  expect((within(sources).getByRole("option", { name: /offline-vault/ }) as HTMLOptionElement).disabled).toBe(true);
  expect(screen.getByText(/Declared references: lab-admin/)).toBeTruthy();
  fireEvent.change(drivers, { target: { value: "offline-driver" } });
  expect((screen.getByRole("button", { name: "Add connection" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.submit(screen.getByRole("form", { name: "Connection configuration" }));
  expect(fetcher.mock.calls.every(([, options]) => !options?.method)).toBe(true);
});

it("modifie seulement les champs changés avec la révision puis relit la connexion ciblée", async () => {
  const updated = { ...connection, label: "Hyperviseur renommé", tls_sha256: "ab".repeat(32), revision: 2 };
  const fetcher = serve((path) => {
    if (path === "/api/connection-drivers") return response(registry);
    if (path === `/api/connections/${id}`) return response(updated);
    return response([connection]);
  });
  render(<Connections user={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit connection" }));
  expect((screen.getByLabelText("Label") as HTMLInputElement).value).toBe(connection.label);
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: updated.label } });
  fireEvent.change(screen.getByLabelText("TLS SHA-256 fingerprint (optional)"), { target: { value: updated.tls_sha256 } });
  fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
  await screen.findByText("Configuration saved and verified by readback.");
  expect(screen.getByRole("heading", { name: updated.label })).toBeTruthy();
  const targetCalls = fetcher.mock.calls.filter(([path]) => path === `/api/connections/${id}`);
  expect(targetCalls.map(([, options]) => options?.method || "GET")).toEqual(["PATCH", "GET"]);
  expect(JSON.parse(targetCalls[0][1]!.body as string)).toEqual({ label: updated.label, tls_sha256: updated.tls_sha256, revision: 1 });
});

it("désactive avec une révision et une relecture, même si le pilote enregistré est indisponible", async () => {
  const unavailableRegistry = { ...registry, drivers: registry.drivers.map((driver) => ({ ...driver, available: false })) };
  const disabled = { ...connection, enabled: false, revision: 2 };
  const fetcher = serve((path) => response(path === "/api/connection-drivers" ? unavailableRegistry : path === `/api/connections/${id}` ? disabled : [connection]));
  render(<Connections user={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Disable connection" }));
  await screen.findByText("Configuration saved and verified by readback.");
  const targetCalls = fetcher.mock.calls.filter(([path]) => path === `/api/connections/${id}`);
  expect(targetCalls.map(([, options]) => options?.method || "GET")).toEqual(["PATCH", "GET"]);
  expect(JSON.parse(targetCalls[0][1]!.body as string)).toEqual({ enabled: false, revision: 1 });
  expect(screen.getByText("Disabled")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Disable connection" }) as HTMLButtonElement).disabled).toBe(true);
});

it.each(["viewer", "operator", "unknown"])("interdit toute configuration et tout dépôt d’identifiants au rôle %s", async (role) => {
  const fetcher = serve();
  render(<Connections user={{ ...admin, role }} />);
  await screen.findByRole("heading", { name: connection.label });
  for (const name of ["New connection", "Edit connection", "Disable connection", "Store credentials"]) expect(screen.queryByRole("button", { name })).toBeNull();
  expect(screen.queryByRole("form")).toBeNull();
  expect(fetcher.mock.calls.every(([, options]) => !options?.method)).toBe(true);
});

it.each([
  ["admin", "test", "Test connection"],
  ["operator", "test", "Test connection"],
  ["operator", "discover", "Discover inventory"],
])("réserve %s/%s à une action explicite et vérifie le résultat via GET", async (role, action, button) => {
  const result = { ...observation };
  let release!: (value: Response) => void;
  const readback = new Promise<Response>((resolve) => { release = resolve; });
  const fetcher = serve((path, options) => {
    if (path === "/api/connection-drivers") return response(registry);
    if (path === `/api/connections/${id}/${action}` && options?.method === "POST") return response(result);
    if (path === `/api/connections/${id}`) return readback;
    return response([connection]);
  });
  setCsrf("test-action-csrf");
  render(<Connections user={{ ...admin, role }} />);
  const control = await screen.findByRole("button", { name: button });
  expect(fetcher.mock.calls).toHaveLength(2);
  fireEvent.click(control);
  await waitFor(() => expect(fetcher.mock.calls.some(([path]) => path === `/api/connections/${id}`)).toBe(true));
  expect(screen.queryByText("Accessible")).toBeNull();
  expect((control as HTMLButtonElement).disabled).toBe(true);
  release(response({ ...connection, observation: result }));
  expect(await screen.findByText("Observation saved and verified by readback.")).toBeTruthy();
  expect(screen.getByText("Accessible")).toBeTruthy();
  const targetCalls = fetcher.mock.calls.filter(([path]) => path.startsWith(`/api/connections/${id}`));
  expect(targetCalls.map(([path, options]) => [path, options?.method || "GET"])).toEqual([[`/api/connections/${id}/${action}`, "POST"], [`/api/connections/${id}`, "GET"]]);
  expect(targetCalls[0][1]!.headers).toMatchObject({ "X-CSRF-Token": "test-action-csrf" });
  expect(screen.getAllByRole("table")).toHaveLength(4);
  expect(screen.getByText("serveur-observé")).toBeTruthy();
});

it.each([
  { action: "test", button: "Test connection", result: { ...observation, inventory: null } },
  { action: "discover", button: "Discover inventory", result: { ...observation, error: "Connection discovery unavailable" } },
  { action: "test", button: "Test connection", result: { ...observation, status: "unavailable", error: "Connection discovery unavailable" } },
  { action: "discover", button: "Discover inventory", result: { ...observation, status: "unavailable", inventory: null, error: null } },
])("refuse un POST $action sémantiquement incohérent même si le GET renvoie la même observation", async ({ action, button, result }) => {
  const fetcher = serve((path, options) => {
    if (path === "/api/connection-drivers") return response(registry);
    if (path === `/api/connections/${id}/${action}` && options?.method === "POST") return response(result);
    if (path === `/api/connections/${id}`) return response({ ...connection, observation: result });
    return response([connection]);
  });
  render(<Connections user={{ ...admin, role: "operator" }} />);
  const control = await screen.findByRole("button", { name: button });
  fireEvent.click(control);
  await waitFor(() => expect((control as HTMLButtonElement).disabled).toBe(false));
  expect(screen.queryByText("Accessible")).toBeNull();
  expect(screen.queryByText("Unavailable")).toBeNull();
  expect(screen.queryByText("Observation saved and verified by readback.")).toBeNull();
  expect(screen.getByRole("alert").textContent).toMatch(/Observation: unsupported server response or inconsistent result/);
  expect(screen.getByText("Action result not confirmed. Showing the last validated read.")).toBeTruthy();
  expect(screen.getByText("No observations recorded. Access has not been verified.")).toBeTruthy();
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
});

it("relit et affiche réellement unavailable après un test, sans le convertir en succès réseau", async () => {
  const result = { ...observation, status: "unavailable", inventory: null, error: "Identifiants non disponibles" };
  serve((path, options) => response(path === "/api/connection-drivers" ? registry : options?.method === "POST" ? result : path === `/api/connections/${id}` ? { ...connection, observation: result } : [connection]));
  render(<Connections user={{ ...admin, role: "operator" }} />);
  fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
  expect(await screen.findByText("Unavailable")).toBeTruthy();
  expect((await screen.findByRole("alert")).textContent).toContain("Identifiants non disponibles");
  expect(screen.queryByText("Accessible")).toBeNull();
});

it.each([
  { stage: "action", value: null },
  { stage: "action", value: { ...observation, connection_id: "f".repeat(32) } },
  { stage: "action", value: { ...observation, revision: 2 } },
  { stage: "action", value: { ...observation, inventory: { ...inventory, networks: [null] } } },
  { stage: "relecture", value: null },
  { stage: "relecture", value: { ...connection, id: "f".repeat(32), observation: null } },
  { stage: "relecture", value: { ...connection, revision: 2, observation } },
  { stage: "relecture", value: { ...connection, endpoint: "https://192.0.2.99", observation } },
  { stage: "relecture", value: { ...connection, observation: null } },
  { stage: "relecture", value: { ...connection, observation: { ...observation, status: "unavailable", inventory: null, error: "changed" } } },
  { stage: "relecture", value: { ...connection, observation: { ...observation, inventory: { ...inventory, virtual_machines: [null] } } } },
])("refuse un résultat de sonde incohérent ou malformé ($stage)", async ({ stage, value }) => {
  const fetcher = serve((path, options) => {
    if (path === "/api/connection-drivers") return response(registry);
    if (options?.method === "POST") return response(stage === "action" ? value : observation);
    if (path === `/api/connections/${id}`) return response(value);
    return response([connection]);
  });
  render(<Connections user={{ ...admin, role: "operator" }} />);
  fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
  expect((await screen.findByRole("alert")).textContent).toMatch(/unsupported|not confirmed|inconsistent/);
  expect(screen.queryByText("Observation saved and verified by readback.")).toBeNull();
  expect(screen.queryByText("Accessible")).toBeNull();
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
});

it.each(["viewer", "unknown"])("n’expose aucune sonde au rôle %s", async (role) => {
  serve(); render(<Connections user={{ ...admin, role }} />);
  await screen.findByRole("heading", { name: connection.label });
  expect(screen.queryByRole("button", { name: "Test connection" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Discover inventory" })).toBeNull();
});

it.each([true, false])("dépose uniquement par PUT en session admin et efface le mot de passe, succès=%s", async (success) => {
  const password = "fixture-password-never-real";
  const storage = vi.spyOn(Storage.prototype, "setItem");
  const logs = ["log", "warn", "error"].map((method) => vi.spyOn(console, method as "log" | "warn" | "error").mockImplementation(() => {}));
  const fetcher = serve((path, options) => {
    if (path === "/api/connection-drivers") return response(registry);
    if (path === `/api/connections/${id}/credentials`) return success ? response({ stored: true, connection_id: id }) : response({ detail: password }, 500);
    if (path === `/api/connections/${id}`) return response(connection);
    if (options?.method) return response({ detail: "Unexpected mutation" }, 500);
    return response([connection]);
  });
  setCsrf("credentials-native-csrf");
  render(<Connections user={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Store credentials" }));
  const input = screen.getByLabelText("Hypervisor password") as HTMLInputElement;
  expect(input.type).toBe("password");
  fireEvent.change(screen.getByLabelText("Hypervisor username"), { target: { value: "lab-user" } });
  fireEvent.change(input, { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Save credentials" }));
  if (success) await screen.findByText("Vault reports credentials stored; connection read back, hypervisor access not tested.");
  else expect((await screen.findByRole("alert")).textContent).toMatch(/Storage not confirmed/);
  expect(input.value).toBe("");
  expect(document.body.textContent).not.toContain(password);
  expect(document.body.innerHTML).not.toContain(password);
  expect(storage).not.toHaveBeenCalled();
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  logs.forEach((log) => expect(log.mock.calls.flat().join(" ")).not.toContain(password));
  const writes = fetcher.mock.calls.filter(([, options]) => options?.method === "PUT");
  expect(writes).toHaveLength(1);
  expect(writes[0][0]).toBe(`/api/connections/${id}/credentials`);
  expect(JSON.parse(writes[0][1]!.body as string)).toEqual({ username: "lab-user", password });
  expect(writes[0][1]!.headers).toMatchObject({ "X-CSRF-Token": "credentials-native-csrf" });
  expect(fetcher.mock.calls.filter(([path]) => path === `/api/connections/${id}`)).toHaveLength(success ? 1 : 0);
  expect(fetcher.mock.calls.some(([path]) => path.endsWith("/test") || path.endsWith("/discover"))).toBe(false);
});

it.each([
  { stage: "dépôt", value: null },
  { stage: "dépôt", value: { stored: false, connection_id: id } },
  { stage: "dépôt", value: { stored: true, connection_id: "f".repeat(32) } },
  { stage: "relecture", value: null },
  { stage: "relecture", value: { ...connection, secrets_provider: "readonly-vault" } },
])("ne confirme aucun dépôt incohérent ($stage) et vide toujours le mot de passe", async ({ stage, value }) => {
  serve((path, options) => {
    if (path === "/api/connection-drivers") return response(registry);
    if (options?.method === "PUT") return response(stage === "dépôt" ? value : { stored: true, connection_id: id });
    if (path === `/api/connections/${id}`) return response(value);
    return response([connection]);
  });
  render(<Connections user={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Store credentials" }));
  const password = screen.getByLabelText("Hypervisor password") as HTMLInputElement;
  fireEvent.change(screen.getByLabelText("Hypervisor username"), { target: { value: "lab-user" } });
  fireEvent.change(password, { target: { value: "fixture-disposable-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Save credentials" }));
  expect((await screen.findByRole("alert")).textContent).toMatch(/Storage not confirmed/);
  expect(password.value).toBe("");
  expect(screen.queryByText("Vault reports credentials stored; connection read back, hypervisor access not tested.")).toBeNull();
});

it.each(["readonly-vault", "offline-vault", "unregistered-vault"])("ne propose pas de dépôt sur une source qui ne peut pas stocker : %s", async (source) => {
  serve((path) => response(path === "/api/connection-drivers" ? registry : [{ ...connection, secrets_provider: source }]));
  render(<Connections user={admin} />);
  await screen.findByRole("heading", { name: connection.label });
  expect(screen.queryByRole("button", { name: "Store credentials" })).toBeNull();
  expect(screen.queryByLabelText("Hypervisor password")).toBeNull();
});

it.each([false, true])("branche la navigation Connections et son icône Server sans modifier le design, lien direct=%s", async (direct) => {
  window.history.replaceState(null, "", direct ? "#connections" : "#agents");
  const fetcher = serve((path) => {
    if (path === "/api/auth/me") return response({ user: { ...admin, role: "viewer" }, csrf_token: "human-session-csrf" });
    if (path === "/api/plugins") return response([]);
    if (path === "/api/connection-drivers") return response(registry);
    if (path === "/api/connections") return response([connection]);
    return response({ detail: "Unexpected request" }, 404);
  });
  render(<App />);
  const nav = await screen.findByRole("navigation", { name: "Main navigation" });
  const button = within(nav).getByRole("button", { name: "Connections" });
  expect(button.querySelector("svg.lucide-server")).toBeTruthy();
  const operate = within(nav).getByText("Operate").closest(".nav-group");
  expect(operate && within(operate as HTMLElement).getByRole("button", { name: "Connections" })).toBeTruthy();
  expect(within(nav).getByText("Govern")).toBeTruthy();
  expect(within(nav).queryByRole("button", { name: "Agent access" })).toBeNull();
  expect(within(nav).queryByRole("button", { name: "Operations" })).toBeNull();
  if (!direct) fireEvent.click(button);
  expect(await screen.findByRole("heading", { name: "Hypervisor connections" })).toBeTruthy();
  await screen.findByRole("heading", { name: connection.label });
  expect(window.location.hash).toBe("#connections");
  expect(button.getAttribute("aria-current")).toBe("page");
  expect(fetcher.mock.calls.every(([, options]) => !options?.method)).toBe(true);
});

it("accepte les valeurs VM scalaires inconnues renvoyées par le backend sans les présenter comme zéro", async () => {
  const vm = { ...inventory.virtual_machines[0], uuid: null, cpu: null, memory_mb: null };
  serve((path) => response(path === "/api/connection-drivers" ? registry : [{ ...connection, observation: { ...observation, inventory: { ...inventory, virtual_machines: [vm] } } }]));
  render(<Connections user={admin} />);
  const table = await screen.findByRole("table", { name: "Virtual machines" });
  expect(within(table).getByText(vm.name)).toBeTruthy();
  expect(within(table).getAllByText("Not specified")).toHaveLength(3);
  expect(screen.queryByRole("alert")).toBeNull();
});

it.each([
  ["HTTPS endpoint (IP address)", "http://192.0.2.10"],
  ["HTTPS endpoint (IP address)", "https://192.0.2.10/"],
  ["HTTPS endpoint (IP address)", "https://hypervisor.example"],
  ["HTTPS endpoint (IP address)", "https://user:pass@192.0.2.10"],
  ["HTTPS endpoint (IP address)", "https://192.0.2.10/sdk"],
  ["HTTPS endpoint (IP address)", "https://192.0.2.10?"],
  ["HTTPS endpoint (IP address)", "HTTPS://192.0.2.10"],
  ["TLS SHA-256 fingerprint (optional)", "AB".repeat(32)],
  ["TLS SHA-256 fingerprint (optional)", "ab:cd"],
  ["Logical secret reference", "secret value with spaces"],
])("explique et refuse une saisie invalid sans la normaliser : %s", async (label, value) => {
  const fetcher = serve(); render(<Connections user={admin} />);
  await fillCreation();
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
  expect((await screen.findByRole("alert")).textContent).toMatch(/Invalid/);
  expect((screen.getByRole("button", { name: "Add connection" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.submit(screen.getByRole("form", { name: "Connection configuration" }));
  expect(fetcher.mock.calls.every(([, options]) => !options?.method)).toBe(true);
  expect((screen.getByLabelText(label) as HTMLInputElement).value).toBe(value);
});

it.each([
  { stage: "écriture", value: null },
  { stage: "écriture", value: {} },
  { stage: "écriture", value: { ...connection, ...newSpec, id: "malformed" } },
  { stage: "écriture", value: { ...connection, ...newSpec, id: "a".repeat(32), label: "Autre valeur" } },
  { stage: "relecture", value: null },
  { stage: "relecture", value: {} },
  { stage: "relecture", value: { ...connection, ...newSpec, id: "f".repeat(32) } },
  { stage: "relecture", value: { ...connection, ...newSpec, id: "a".repeat(32), revision: 2 } },
  { stage: "relecture", value: { ...connection, ...newSpec, id: "a".repeat(32), enabled: false } },
  { stage: "relecture", value: { ...connection, ...newSpec, id: "a".repeat(32), observation: { ...observation, connection_id: "a".repeat(32), inventory: { ...inventory, networks: [null] } } } },
])("ne confirme pas une création malformée ou divergente ($stage), même après un HTTP 201", async ({ stage, value }) => {
  const created = { ...connection, ...newSpec, id: "a".repeat(32) };
  serve((path, options) => {
    if (path === "/api/connection-drivers") return response(registry);
    if (options?.method === "POST") return response(stage === "écriture" ? value : created, 201);
    if (path === `/api/connections/${created.id}`) return response(value);
    return response([connection]);
  });
  render(<Connections user={admin} />); await fillCreation();
  fireEvent.click(screen.getByRole("button", { name: "Add connection" }));
  expect((await screen.findByRole("alert")).textContent).toMatch(/Write not confirmed/);
  expect(screen.queryByText("Configuration saved and verified by readback.")).toBeNull();
  expect(screen.queryByRole("heading", { name: newSpec.label })).toBeNull();
});

it("refuse qu’une création réutilise l’identifiant d’une connexion déjà connue", async () => {
  const duplicate = { ...connection, ...newSpec };
  const fetcher = serve((path, options) => response(path === "/api/connection-drivers" ? registry : options?.method === "POST" || path === `/api/connections/${id}` ? duplicate : [connection], options?.method === "POST" ? 201 : 200));
  render(<Connections user={admin} />); await fillCreation();
  fireEvent.click(screen.getByRole("button", { name: "Add connection" }));
  expect((await screen.findByRole("alert")).textContent).toMatch(/Write not confirmed/);
  expect(screen.queryByText("Configuration saved and verified by readback.")).toBeNull();
  expect(fetcher.mock.calls.some(([path]) => path === `/api/connections/${id}`)).toBe(false);
});

it("affiche le refus de révision sans affirmer une modification et sans répéter le PATCH", async () => {
  const fetcher = serve((path, options) => path === "/api/connection-drivers" ? response(registry) : options?.method === "PATCH" ? response({ detail: "Connection revision changed" }, 409) : response([connection]));
  render(<Connections user={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit connection" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Modification refusée" } });
  fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
  expect((await screen.findByRole("alert")).textContent).toContain("HTTP 409");
  expect(screen.queryByText("Configuration saved and verified by readback.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Refresh connections" }));
  await screen.findByRole("heading", { name: connection.label });
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "PATCH")).toHaveLength(1);
  expect(fetcher.mock.calls.some(([path]) => path.endsWith("/test") || path.endsWith("/discover"))).toBe(false);
});

it.each(["HTTP", "network"])("gère un échec de chargement %s et le réessaie uniquement par GET", async (failure) => {
  let failed = true;
  const fetcher = serve((path) => {
    if (path === "/api/connection-drivers") return response(registry);
    if (failed && failure === "network") throw new Error("Network down");
    return failed ? response({ detail: "Temporarily unavailable" }, 503) : response([connection]);
  });
  render(<Connections user={admin} />);
  expect((await screen.findByRole("alert")).textContent).toMatch(/HTTP 503|Server unreachable/);
  expect(screen.queryByRole("region", { name: "Connection details" })).toBeNull();
  failed = false;
  fireEvent.click(screen.getByRole("button", { name: "Refresh connections" }));
  await screen.findByRole("heading", { name: connection.label });
  expect(screen.queryByRole("alert")).toBeNull();
  expect(fetcher.mock.calls.every(([, options]) => !options?.method)).toBe(true);
});

it("n’invente aucune connexion ni aucun inventaire quand le registre est vide", async () => {
  const fetcher = serve((path) => response(path === "/api/connection-drivers" ? { drivers: [], secret_sources: [] } : []));
  render(<Connections user={admin} />);
  expect(await screen.findByText("No saved connections")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "New connection" }));
  expect(within(screen.getByLabelText("Connection driver")).getAllByRole("option")).toHaveLength(1);
  expect((screen.getByRole("button", { name: "Add connection" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole("table")).toBeNull();
  expect(fetcher.mock.calls).toHaveLength(2);
});

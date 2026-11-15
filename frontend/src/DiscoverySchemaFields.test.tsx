// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { Canvas } from "./Canvas";
import { DiscoverySchemaFields } from "./DiscoverySchemaFields";
import { date, emptyGraph } from "./model";
import type { Plugin, Schema } from "./model";

vi.mock("@xyflow/react", async (importOriginal) => ({
  ...await importOriginal<typeof import("@xyflow/react")>(),
  ReactFlow: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Background: () => null,
  Controls: () => null,
  MiniMap: () => null,
  useReactFlow: () => ({ fitView: vi.fn(), screenToFlowPosition: (point: unknown) => point }),
}));
const schema = {
  type: "object",
  required: ["connection_id", "datastore_id", "network_ids"],
  properties: {
    connection_id: { type: "string", title: "Connexion", "x-options-source": "connections" },
    datastore_id: { type: "string", title: "Stockage", "x-options-source": "datastores" },
    network_ids: { type: "array", title: "Réseaux", items: { type: "string", "x-options-source": "networks" } },
    resource_pool_id: { type: "string", title: "Groupe de ressources", "x-options-source": "resource_pools" },
  },
} as Schema;
const observation = {
  revision: 3, status: "reachable", observed_at: "2026-01-10T12:30:00Z",
  inventory: {
    datastores: [{ id: "store-17", name: "Stockage principal", capacity_bytes: 1024 }],
    networks: [{ id: "net-42", name: "Réseau atelier" }],
    resource_pools: [{ id: "pool-8", name: "Groupe calcul" }],
  },
};
const connection = { id: "connection-5", label: "Atelier", enabled: true, revision: 3, observation };
const user = { id: "editor", username: "editor", role: "operator" };
function mountCanvas(fieldSchema = schema, config: Record<string, unknown> = { connection_id: connection.id }, response: unknown = [connection]) {
  const plugin: Plugin = {
    id: "generic-compute", name: "Ressource générique", version: "1", description: "Test de contrat",
    category: "compute", price_monthly: 0, capability: "live", engine: "manifest-engine",
    schema: fieldSchema, defaults: config,
  };
  localStorage.setItem(`kuberweaver.draft.${user.id}`, JSON.stringify(emptyGraph()));
  sessionStorage.setItem("kuberweaver.add", plugin.id);
  const fetcher = vi.fn(async (path: string, _options?: RequestInit) => new Response(JSON.stringify(
    path === "/api/connections" ? response : [],
  )));
  vi.stubGlobal("fetch", fetcher);
  render(<Canvas user={user} plugins={[plugin]} pluginsLoading={false} pluginsError="" retryPlugins={() => {}} onJob={() => {}} />);
  return fetcher;
}
function mountFields(response: unknown = [connection], value: Record<string, unknown> = { connection_id: connection.id }, fieldSchema = schema) {
  const fetcher = vi.fn(async (_path: string, _options?: RequestInit) => new Response(JSON.stringify(response)));
  vi.stubGlobal("fetch", fetcher);
  const onChange = vi.fn();
  const view = render(<DiscoverySchemaFields schema={fieldSchema} value={value} onChange={onChange} />);
  return { fetcher, onChange, ...view };
}

afterEach(() => { cleanup(); localStorage.clear(); sessionStorage.clear(); vi.unstubAllGlobals(); });

it("ne propose que les connexions activées", async () => {
  mountFields([connection, { ...connection, id: "disabled", label: "Désactivée", enabled: false }]);
  await screen.findByRole("option", { name: "Atelier" });
  expect(screen.queryByRole("option", { name: "Désactivée" })).toBeNull();
});

it.each([
  { ...connection, observation: null },
  { ...connection, observation: { ...observation, status: "unavailable" } },
  { ...connection, revision: 4 },
  { ...connection, enabled: false },
])("n’utilise aucun inventaire sans observation joignable à la révision active : %j", async (entry) => {
  const { fetcher, onChange } = mountFields([entry]);
  await screen.findByText(/Ouvrez Connexions et lancez une découverte/);
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole("option", { name: "Stockage principal" })).toBeNull();
  expect(screen.queryByRole("checkbox", { name: "Réseau atelier" })).toBeNull();
  expect(screen.queryByRole("option", { name: "Groupe calcul" })).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
  if (entry.observation) expect(await screen.findByText(date(observation.observed_at))).toBeTruthy();
  expect(fetcher.mock.calls.every(([path, options]) => path === "/api/connections" && (!options?.method || options.method === "GET"))).toBe(true);
});

it.each([
  ["réponse nulle", null],
  ["objet au lieu d’un tableau", { connections: [connection] }],
  ["entrée nulle", [null]],
  ["entrée tableau", [[]]],
  ["identifiant absent", [{ ...connection, id: null }]],
  ["identifiants dupliqués", [connection, connection]],
  ["observation tableau", [{ ...connection, observation: [] }]],
  ["inventaire nul joignable", [{ ...connection, observation: { ...observation, inventory: null } }]],
  ["révision non entière", [{ ...connection, revision: "3" }]],
  ["libellé absent", [{ ...connection, label: null }]],
  ["activation non booléenne", [{ ...connection, enabled: "true" }]],
  ["état non textuel", [{ ...connection, observation: { ...observation, status: ["reachable"] } }]],
  ["état inconnu", [{ ...connection, observation: { ...observation, status: "unknown" } }]],
  ["date non valide", [{ ...connection, observation: { ...observation, observed_at: "not-a-date" } }]],
])("rejette une enveloppe de connexions malformée (%s) avec une erreur lisible", async (_label, response) => {
  mountFields(response);
  expect((await screen.findByRole("alert")).textContent).toMatch(/connexions.*invalide/i);
  expect(screen.queryByRole("option", { name: "Stockage principal" })).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.getByText(/Options indisponibles.*Ouvrez Connexions/)).toBeTruthy();
});

it.each([
  ["nom absent", [{ id: "net-bad", name: null }]],
  ["identifiant vide", [{ id: "", name: "Réseau invalide" }]],
  ["identifiants dupliqués", [{ id: "net-bad", name: "Un" }, { id: "net-bad", name: "Deux" }]],
  ["collection nulle", null],
  ["collection objet", {}],
  ["collection texte", "not-an-array"],
  ["entrée nulle", [null]],
  ["entrée scalaire", ["not-an-entry"]],
  ["liste partiellement invalide", [{ id: "net-valid", name: "Choix partiel interdit" }, null]],
])("refuse une collection d’inventaire invalide (%s) sans choix partiel ni saisie libre", async (_label, networks) => {
  mountFields([{ ...connection, observation: { ...observation, inventory: { ...observation.inventory, networks } } }]);
  expect((await screen.findByRole("alert")).textContent).toMatch(/inventaire.*networks.*invalide/i);
  expect(screen.getByRole("option", { name: "Stockage principal" })).toBeTruthy();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
});

it.each(["virtual_machines", "https://invalid.example/inventory", ""])("traite la source %j comme une métadonnée inconnue, jamais une URL", async (source) => {
  const response = [{ ...connection, observation: { ...observation, inventory: { [source]: [{ id: "not-an-option", name: "Choix interdit" }] } } }];
  const { fetcher } = mountFields(response, { connection_id: connection.id }, {
    properties: { arbitrary: { type: "string", title: "Source inconnue", "x-options-source": source } },
  });
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  expect((await screen.findByRole("alert")).textContent).toMatch(/Source d’options inconnue/);
  expect(screen.queryByRole("option", { name: "Choix interdit" })).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(fetcher.mock.calls.map(([path]) => path)).toEqual(["/api/connections"]);
});

it.each([{}, { datastore_id: "store-removed", network_ids: ["net-removed"] }])("garde un placeholder obligatoire et préserve les valeurs non modifiées : %j", async (saved) => {
  const value = { connection_id: connection.id, ...saved };
  const { onChange } = mountFields([connection], value);
  await screen.findByRole("option", { name: "Stockage principal" });
  const storage = screen.getByRole("combobox", { name: /Stockage/ });
  expect(storage).toHaveProperty("value", "");
  expect(within(storage).getByRole("option", { name: "Sélectionner" })).toHaveProperty("selected", true);
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: "Réseau atelier" }));
  expect(onChange).toHaveBeenLastCalledWith({ ...value, network_ids: [...(saved.network_ids || []), "net-42"] });
});

it("résout aussi les annotations imbriquées sans perdre les props de SchemaFields", async () => {
  const nested: Schema = { properties: { advanced: { type: "object", title: "Avancé", properties: {
    storage: { type: "string", title: "Stockage imbriqué", "x-options-source": "datastores" },
    links: { type: "array", items: { type: "string", "x-options-source": "networks" } },
  } } } };
  const value = { connection_id: connection.id, advanced: { links: ["net-42"] } };
  const { onChange, rerender, fetcher } = mountFields([connection], value, nested);
  const network = await screen.findByRole("checkbox", { name: "Réseau atelier" });
  expect(network).toHaveProperty("checked", true);
  fireEvent.change(screen.getByLabelText("Stockage imbriqué"), { target: { value: "store-17" } });
  expect(onChange).toHaveBeenLastCalledWith({ ...value, advanced: { links: ["net-42"], storage: "store-17" } });
  rerender(<DiscoverySchemaFields schema={nested} value={value} onChange={onChange} disabled prefix="nested" />);
  expect(screen.getByLabelText("Stockage imbriqué")).toHaveProperty("id", "nested-advanced-storage");
  expect(network).toHaveProperty("disabled", true);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("revient au formulaire historique sans message ni observation de découverte résiduelle", async () => {
  const { rerender, fetcher } = mountFields({ invalid: true });
  await screen.findByRole("alert");
  rerender(<DiscoverySchemaFields schema={{ properties: { version: { type: "string", title: "Version" } } }} value={{ version: "1" }} onChange={() => {}} />);
  expect(screen.getByLabelText("Version")).toHaveProperty("value", "1");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByText(/Ouvrez Connexions/)).toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("ne lit pas les connexions pour un formulaire historique sans annotation", async () => {
  const fetcher = mountCanvas({ properties: { version: { type: "string", title: "Version" } } }, { version: "1" });
  expect(await screen.findByLabelText("Version")).toHaveProperty("value", "1");
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith("/api/blueprints", expect.anything()));
  expect(fetcher.mock.calls.map(([path]) => path)).toEqual(["/api/blueprints"]);
});

it("suit config.connection_id sans réécrire les sélections ni refaire une découverte lors du changement", async () => {
  const other = { ...connection, id: "connection-other", label: "Autre atelier", observation: { ...observation, inventory: {
    datastores: [{ id: "store-other", name: "Autre stockage" }],
    networks: [{ id: "net-other", name: "Autre réseau" }], resource_pools: [],
  } } };
  const original = { connection_id: connection.id, datastore_id: "store-17", network_ids: ["net-42"] };
  const fetcher = mountCanvas(schema, original, [other, connection]);
  await screen.findByRole("checkbox", { name: "Réseau atelier" });
  expect(screen.queryByRole("checkbox", { name: "Autre réseau" })).toBeNull();
  fireEvent.change(screen.getByRole("combobox", { name: /Connexion/ }), { target: { value: other.id } });
  expect(await screen.findByRole("checkbox", { name: "Autre réseau" })).toHaveProperty("checked", false);
  expect(screen.queryByRole("checkbox", { name: "Réseau atelier" })).toBeNull();
  expect(screen.getByRole("combobox", { name: /Stockage/ })).toHaveProperty("value", "");
  await waitFor(() => {
    const saved = JSON.parse(localStorage.getItem(`kuberweaver.draft.${user.id}`)!);
    expect(saved.nodes[0].config).toEqual({ ...original, connection_id: other.id });
  });
  expect(fetcher.mock.calls.filter(([path]) => path !== "/api/blueprints").map(([path]) => path)).toEqual(["/api/connections"]);
});

it("branche le Canvas aux identifiants et libellés de l’inventaire par annotations sans découverte implicite", async () => {
  const fetcher = mountCanvas();
  expect(await screen.findByRole("option", { name: "Stockage principal" })).toHaveProperty("value", "store-17");
  expect(screen.getByRole("option", { name: "Atelier" })).toHaveProperty("value", "connection-5");
  expect(screen.getByRole("option", { name: "Groupe calcul" })).toHaveProperty("value", "pool-8");
  fireEvent.change(screen.getByRole("combobox", { name: /Stockage/ }), { target: { value: "store-17" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Réseau atelier" }));
  await waitFor(() => {
    const saved = JSON.parse(localStorage.getItem(`kuberweaver.draft.${user.id}`)!);
    expect(saved.nodes[0].config).toEqual({ connection_id: "connection-5", datastore_id: "store-17", network_ids: ["net-42"] });
  });
  const calls = fetcher.mock.calls.filter(([path]) => path !== "/api/blueprints");
  expect(calls).toHaveLength(1);
  expect(calls[0][0]).toBe("/api/connections");
  expect(calls[0][1]?.method || "GET").toBe("GET");
});

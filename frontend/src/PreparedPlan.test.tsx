// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Jobs } from "./pages";
import { emptyGraph } from "./model";

const admin = { id: "admin", username: "admin", role: "admin" };
const plan = {
  plan_digest: "saved-plan-digest-17",
  actions: [{ action: "create", resource: "resource-1" }],
  summary: { add: 1, change: 0, destroy: 0, note: "Création prévue", reversible: false },
  bindings: { private_path: "/internal/saved-plan", secret_fingerprint: "private-binding-evidence" },
};
const job = {
  id: "prepared-request", name: "Resource à préparer", environment: "dev",
  status: "pending_approval", created_at: "2026-01-01T00:00:00Z", created_by: "operator",
  graph: emptyGraph(), events: [], cost: { monthly: 12, currency: "EUR", breakdown: [] },
  prepared_plan: plan, requires_saved_plan: true, can_rollback: false,
};
function loadJob(overrides: Record<string, unknown> = {}, role = "admin") {
  let value = { ...job, ...overrides };
  sessionStorage.setItem("kuberweaver.job", value.id);
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => {
    if (path.endsWith("/approve") && options?.method === "POST") value = { ...value, status: "queued" };
    return new Response(JSON.stringify(path === "/api/jobs" ? [value] : value));
  });
  vi.stubGlobal("fetch", fetcher);
  render(<Jobs user={{ ...admin, role }} />);
  return fetcher;
}
afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each([
  { status: "pending_approval", can_rollback: false, button: "Approve" },
  { status: "succeeded", can_rollback: true, button: "Manual rollback" },
])("retire le plan de A ($status) dès la sélection de B et attend sa réponse exacte avant toute action", async ({ button, ...overrides }) => {
  const a = { ...job, ...overrides, id: "request-a", name: "Demande A" };
  const b = { ...job, id: "request-b", name: "Demande B", prepared_plan: null, requires_manual_review: true };
  let release!: (value: Response) => void;
  const pendingB = new Promise<Response>((resolve) => { release = resolve; });
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => {
    if (options?.method === "POST") return new Response(JSON.stringify({}));
    if (path === "/api/jobs") return new Response(JSON.stringify([a, b]));
    if (path === "/api/jobs/request-a") return new Response(JSON.stringify(a));
    if (path === "/api/jobs/request-b") return pendingB;
    throw new Error(`Requête inattendue : ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
  sessionStorage.setItem("kuberweaver.job", a.id);
  render(<Jobs user={admin} />);
  expect((await screen.findByRole("region", { name: "Saved plan" })).textContent).toContain(plan.plan_digest);
  expect(screen.getByRole("button", { name: button })).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: /Demande B/ }));
  await waitFor(() => expect(fetcher.mock.calls.some(([path]) => path === "/api/jobs/request-b")).toBe(true));
  expect(screen.queryByText(plan.plan_digest)).toBeNull();
  expect(screen.queryByRole("region", { name: "Saved plan" })).toBeNull();
  for (const name of ["Approve", "Reject", "Manual rollback"]) {
    expect(screen.queryByRole("button", { name })).toBeNull();
  }
  expect(screen.getByText("Loading…")).toBeTruthy();

  await act(async () => { release(new Response(JSON.stringify(b))); });
  expect((await screen.findByRole("alert")).textContent).toMatch(/manual review.*approval blocked/i);
  expect(screen.getByRole("heading", { name: b.name })).toBeTruthy();
  expect(screen.queryByText(plan.plan_digest)).toBeNull();
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(0);
});

it.each(["request-a", "request-b"])("ignore un GET tardif de B renvoyant %s après une nouvelle sélection de A", async (responseId) => {
  const a = { ...job, id: "request-a", name: "Demande A" };
  const b = { ...job, id: "request-b", name: "Demande B", prepared_plan: { ...plan, plan_digest: "plan-b-digest" } };
  let loadsA = 0;
  let releaseA!: (value: Response) => void;
  let releaseB!: (value: Response) => void;
  const pendingA = new Promise<Response>((resolve) => { releaseA = resolve; });
  const pendingB = new Promise<Response>((resolve) => { releaseB = resolve; });
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => {
    if (options?.method === "POST") return new Response(JSON.stringify({}));
    if (path === "/api/jobs") return new Response(JSON.stringify([a, b]));
    if (path === "/api/jobs/request-a") return ++loadsA === 1 ? new Response(JSON.stringify(a)) : pendingA;
    if (path === "/api/jobs/request-b") return pendingB;
    throw new Error(`Requête inattendue : ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
  sessionStorage.setItem("kuberweaver.job", a.id);
  render(<Jobs user={admin} />);
  await screen.findByText(plan.plan_digest);
  fireEvent.click(screen.getByRole("button", { name: /Demande B/ }));
  await waitFor(() => expect(fetcher.mock.calls.some(([path]) => path === "/api/jobs/request-b")).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: /Demande A/ }));
  await waitFor(() => expect(loadsA).toBe(2));
  expect(screen.queryByText(plan.plan_digest)).toBeNull();
  for (const name of ["Approve", "Reject", "Manual rollback"]) {
    expect(screen.queryByRole("button", { name })).toBeNull();
  }

  await act(async () => { releaseA(new Response(JSON.stringify({ ...a, requires_manual_review: true, prepared_plan: null }))); });
  await screen.findByRole("alert");
  await act(async () => { releaseB(new Response(JSON.stringify({ ...b, id: responseId }))); });
  expect(screen.getByRole("heading", { name: a.name })).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toMatch(/manual review.*approval blocked/i);
  expect(screen.queryByRole("region", { name: "Saved plan" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(0);
});

it.each([
  { status: "pending_approval", can_rollback: false },
  { status: "succeeded", can_rollback: true },
])("refuse un GET de B contenant l’identité et le plan de A, quel que soit l’état : %j", async (overrides) => {
  const a = { ...job, ...overrides, id: "request-a", name: "Demande A" };
  const b = { ...job, id: "request-b", name: "Demande B" };
  const fetcher = vi.fn(async (path: string, _options?: RequestInit) =>
    new Response(JSON.stringify(path === "/api/jobs" ? [a, b] : a)),
  );
  vi.stubGlobal("fetch", fetcher);
  sessionStorage.setItem("kuberweaver.job", b.id);
  render(<Jobs user={admin} />);

  expect((await screen.findByRole("alert")).textContent).toMatch(/identifier.*selected request/i);
  expect(fetcher.mock.calls.some(([path]) => path === "/api/jobs/request-b")).toBe(true);
  expect(screen.queryByText(plan.plan_digest)).toBeNull();
  expect(screen.queryByRole("region", { name: "Saved plan" })).toBeNull();
  expect(screen.queryByRole("heading", { name: a.name })).toBeNull();
  for (const name of ["Approve", "Reject", "Manual rollback"]) {
    expect(screen.queryByRole("button", { name })).toBeNull();
  }
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(0);
});

it.each([
  { action: "approve", button: "Approve", status: "pending_approval", nextStatus: "queued" },
  { action: "reject", button: "Reject", status: "pending_approval", nextStatus: "rejected" },
  { action: "rollback", button: "Manual rollback", status: "succeeded", nextStatus: "rolling_back" },
])("ne confirme pas $action quand la relecture après POST contient une autre demande", async ({ action, button, status, nextStatus }) => {
  const b = { ...job, id: "request-b", name: "Demande B", status, can_rollback: status === "succeeded" };
  const foreign = { ...job, id: "request-a", name: "Demande A", status: nextStatus };
  let posted = false;
  let release!: (value: Response) => void;
  const readback = new Promise<Response>((resolve) => { release = resolve; });
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => {
    if (path === `/api/jobs/request-b/${action}` && options?.method === "POST") {
      posted = true;
      return new Response(JSON.stringify({ ...b, status: nextStatus }));
    }
    if (path === "/api/jobs") return new Response(JSON.stringify([b]));
    if (path === "/api/jobs/request-b") return posted ? readback : new Response(JSON.stringify(b));
    throw new Error(`Requête inattendue : ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  sessionStorage.setItem("kuberweaver.job", b.id);
  render(<Jobs user={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: button }));
  await waitFor(() => expect(fetcher.mock.calls.filter(([path]) => path === "/api/jobs/request-b")).toHaveLength(2));

  await act(async () => { release(new Response(JSON.stringify(foreign))); });
  expect(fetcher.mock.calls.filter(([path]) => path === "/api/jobs")).toHaveLength(1);
  expect((await screen.findByRole("alert")).textContent).toMatch(/identifier.*selected request/i);
  expect(screen.queryByText(/Request approved|Request rejected|Rollback requested/)).toBeNull();
  expect(screen.queryByRole("heading", { name: foreign.name })).toBeNull();
  for (const name of ["Approve", "Reject", "Manual rollback"]) {
    expect(screen.queryByRole("button", { name })).toBeNull();
  }
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST").map(([path]) => path)).toEqual([`/api/jobs/request-b/${action}`]);
});

it.each([
  { action: "approve", button: "Approve", status: "pending_approval", nextStatus: "queued" },
  { action: "reject", button: "Reject", status: "pending_approval", nextStatus: "rejected" },
  { action: "rollback", button: "Manual rollback", status: "succeeded", nextStatus: "rolling_back" },
])("refuse une réponse POST $action d’une autre demande même si le GET suivant serait correct", async ({ action, button, status, nextStatus }) => {
  const b = { ...job, id: "request-b", name: "Demande B", status, can_rollback: status === "succeeded" };
  const foreign = { ...job, id: "request-a", name: "Demande A", status: nextStatus };
  let release!: (value: Response) => void;
  const mutation = new Promise<Response>((resolve) => { release = resolve; });
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => {
    if (path === `/api/jobs/request-b/${action}` && options?.method === "POST") return mutation;
    if (path === "/api/jobs") return new Response(JSON.stringify([b]));
    if (path === "/api/jobs/request-b") return new Response(JSON.stringify(b));
    throw new Error(`Requête inattendue : ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  sessionStorage.setItem("kuberweaver.job", b.id);
  render(<Jobs user={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: button }));
  await waitFor(() => expect(fetcher.mock.calls.some(([, options]) => options?.method === "POST")).toBe(true));

  await act(async () => { release(new Response(JSON.stringify(foreign))); });
  expect(fetcher.mock.calls.filter(([path]) => path === "/api/jobs/request-b")).toHaveLength(1);
  expect(fetcher.mock.calls.filter(([path]) => path === "/api/jobs")).toHaveLength(1);
  expect((await screen.findByRole("alert")).textContent).toMatch(/identifier.*selected request/i);
  expect(screen.queryByText(/Request approved|Request rejected|Rollback requested/)).toBeNull();
  for (const name of ["Approve", "Reject", "Manual rollback"]) {
    expect(screen.queryByRole("button", { name })).toBeNull();
  }
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST").map(([path]) => path)).toEqual([`/api/jobs/request-b/${action}`]);
});

it.each(["POST", "GET"])("ignore le retour %s retardé d’une action de A après sélection de B", async (stage) => {
  const a = { ...job, id: "request-a", name: "Demande A" };
  const b = { ...job, id: "request-b", name: "Demande B", prepared_plan: { ...plan, plan_digest: "plan-b-digest" } };
  let postedA = false;
  let postedB = false;
  let release!: (value: Response) => void;
  const pending = new Promise<Response>((resolve) => { release = resolve; });
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => {
    if (path === "/api/jobs/request-a/approve" && options?.method === "POST") {
      postedA = true;
      return stage === "POST" ? pending : new Response(JSON.stringify({ ...a, status: "queued" }));
    }
    if (path === "/api/jobs/request-b/approve" && options?.method === "POST") {
      postedB = true;
      return new Response(JSON.stringify({ ...b, status: "queued" }));
    }
    if (path === "/api/jobs") return new Response(JSON.stringify([a, b]));
    if (path === "/api/jobs/request-a") return postedA && stage === "GET" ? pending : new Response(JSON.stringify(a));
    if (path === "/api/jobs/request-b") return new Response(JSON.stringify({ ...b, status: postedB ? "queued" : b.status }));
    throw new Error(`Requête inattendue : ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
  sessionStorage.setItem("kuberweaver.job", a.id);
  render(<Jobs user={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
  await waitFor(() => expect(stage === "POST" ? postedA : fetcher.mock.calls.filter(([path]) => path === "/api/jobs/request-a").length === 2).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: /Demande B/ }));
  await screen.findByText(b.prepared_plan.plan_digest);
  const callsBeforeRelease = fetcher.mock.calls.length;

  await act(async () => { release(new Response(JSON.stringify({ ...a, status: "queued" }))); });
  expect(fetcher.mock.calls.slice(callsBeforeRelease)).toEqual([]);
  expect(screen.getByRole("heading", { name: b.name })).toBeTruthy();
  expect(screen.queryByText(plan.plan_digest)).toBeNull();
  expect(screen.getByText(b.prepared_plan.plan_digest)).toBeTruthy();
  expect(screen.queryByText("Request approved. Tracking deployment progress.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  await screen.findByText("Request approved. Tracking deployment progress.");
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST").map(([path]) => path)).toEqual([
    "/api/jobs/request-a/approve", "/api/jobs/request-b/approve",
  ]);
});

it.each([
  { label: "null", value: null },
  { label: "identifiant absent", value: { ...job, id: undefined } },
  { label: "identifiant numérique", value: { ...job, id: 17 } },
  { label: "espace supplémentaire", value: { ...job, id: `${job.id} ` } },
  { label: "casse différente", value: { ...job, id: job.id.toUpperCase() } },
])("signale une réponse de détail sans identité exacte : $label", async ({ value }) => {
  const fetcher = vi.fn(async (path: string, _options?: RequestInit) =>
    new Response(JSON.stringify(path === "/api/jobs" ? [job] : value)),
  );
  vi.stubGlobal("fetch", fetcher);
  sessionStorage.setItem("kuberweaver.job", job.id);
  render(<Jobs user={admin} />);
  expect((await screen.findByRole("alert")).textContent).toMatch(/identifier.*selected request/i);
  expect(screen.queryByRole("region", { name: "Saved plan" })).toBeNull();
  for (const name of ["Approve", "Reject", "Manual rollback"]) {
    expect(screen.queryByRole("button", { name })).toBeNull();
  }
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(0);
});

it.each([
  { requires_manual_review: true },
  { prepared_plan: undefined, requires_saved_plan: true },
  { prepared_plan: null, requires_saved_plan: true },
])("bloque l’approbation si une revue manuelle ou un plan sauvegardé manque : %j", async (overrides) => {
  const fetcher = loadJob(overrides);
  await screen.findByRole("region", { name: "Internal deployment estimate" });
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(screen.getByText(/approval.*blocked/i)).toBeTruthy();
  expect(fetcher.mock.calls.every(([, options]) => !options?.method || options.method === "GET")).toBe(true);
});

it.each([
  {},
  [],
  0,
  { ...plan, actions: null },
  { ...plan, plan_digest: {} },
  { ...plan, plan_digest: "" },
  { ...plan, plan_digest: 17 },
  { ...plan, actions: "create" },
  { ...plan, summary: [] },
  { ...plan, bindings: null },
].map((prepared_plan) => ({ prepared_plan })))("refuse un plan sauvegardé malformé sans exposer le JSON ni autoriser l’approbation : %j", async ({ prepared_plan }) => {
  loadJob({ prepared_plan });
  expect((await screen.findByRole("alert")).textContent).toMatch(/invalid saved plan/i);
  expect(screen.queryByRole("region", { name: "Saved plan" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(document.body.textContent).not.toContain("private-binding-evidence");
});

it("nomme la phase planning sans proposer une approbation ou annoncer un déploiement", async () => {
  loadJob({ status: "planning", operation: "prepare", prepared_plan: null });
  await screen.findByRole("region", { name: "Internal deployment estimate" });
  expect(screen.getAllByText("Preparing plan")).toHaveLength(2);
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Saved plan" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Manual rollback" })).toBeNull();
});

it.each([
  { actions: { "resource-1": ["create"] } },
  { actions: [{ resource: "resource-1", actions: ["create"] }] },
])("rend les actions structurées du contrat public sans transformer le résumé en JSON : %j", async ({ actions }) => {
  loadJob({ prepared_plan: { ...plan, actions, summary: { add: 1, internal: { raw: "nested-summary-evidence" }, list: ["summary-array-evidence"], none: null } } });
  const saved = await screen.findByRole("region", { name: "Saved plan" });
  expect(saved.textContent).toContain("resource-1");
  expect(saved.textContent).toContain("create");
  expect(saved.textContent).not.toContain("nested-summary-evidence");
  expect(saved.textContent).not.toContain("summary-array-evidence");
  expect(saved.textContent).not.toContain("[object Object]");
  expect(within(saved).getByText("add").nextElementSibling?.textContent).toBe("1");
});

it("approuve par la route historique puis relit le job exact avant de confirmer", async () => {
  const fetcher = loadJob();
  fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
  await screen.findByText("Request approved. Tracking deployment progress.");
  await waitFor(() => expect(screen.queryByRole("button", { name: "Approve" })).toBeNull());
  const post = fetcher.mock.calls.findIndex(([path, options]) => path === "/api/jobs/prepared-request/approve" && options?.method === "POST");
  expect(post).toBeGreaterThan(-1);
  expect(fetcher.mock.calls[post + 1][0]).toBe("/api/jobs/prepared-request");
  expect(fetcher.mock.calls[post + 1][1]?.method || "GET").toBe("GET");
});

it.each(["admin", "operator", "viewer"])("préserve les permissions historiques sans préparation requise pour le rôle %s", async (role) => {
  loadJob({ prepared_plan: undefined, requires_saved_plan: false }, role);
  await screen.findByRole("region", { name: "Internal deployment estimate" });
  expect(Boolean(screen.queryByRole("button", { name: "Approve" }))).toBe(role === "admin");
  expect(screen.queryByRole("region", { name: "Saved plan" })).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("affiche l’empreinte, les actions et le résumé sauvegardés avant l’approbation, sans bindings internes", async () => {
  loadJob();
  const saved = await screen.findByRole("region", { name: "Saved plan" });
  expect(saved.textContent).toContain(plan.plan_digest);
  expect(saved.textContent).toContain("create");
  expect(saved.textContent).toContain("resource-1");
  expect(saved.textContent).toContain("Création prévue");
  expect(within(saved).getByText("add").nextElementSibling?.textContent).toBe("1");
  expect(within(saved).getByText("reversible").nextElementSibling?.textContent).toBe("false");
  expect(saved.textContent).toMatch(/preparation only/i);
  expect(saved.textContent).not.toContain("private-binding-evidence");
  expect(saved.textContent).not.toContain("/internal/saved-plan");
  expect(saved.querySelector("pre")).toBeNull();
  const approve = screen.getByRole("button", { name: "Approve" });
  const estimate = screen.getByRole("region", { name: "Internal deployment estimate" });
  expect(saved.compareDocumentPosition(approve) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(estimate.compareDocumentPosition(approve) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Manual rollback" })).toBeNull();
});

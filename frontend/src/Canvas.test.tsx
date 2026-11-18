// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Canvas } from "./Canvas";
import type { Graph, Plugin } from "./model";
import { parseResourceVariables } from "./resourceWorkspace";

const common = { version: "1", description: "Test adapter", price_monthly: 3, capability: "live", defaults: { cpu: 1 }, schema: { properties: { cpu: { type: "integer", title: "CPU" } } } } as const;
const image: Plugin = { ...common, id: "image-one", name: "Web image", category: "web", engine: "ansible", service: { image: "library/nginx:1.28" } };
const role: Plugin = { ...common, id: "role-one", name: "Guest setup", category: "web", engine: "ansible", kind: "workload", implementation: { role: "owner.setup", version: "2" } };
const vm: Plugin = { ...common, id: "future-vm", name: "Future VM", category: "compute", engine: "opentofu", capability: "planned" };
const response = (value: unknown) => new Response(JSON.stringify(value));

afterEach(() => { cleanup(); localStorage.clear(); sessionStorage.clear(); vi.unstubAllGlobals(); });

function mount(roleName = "admin", allowed = true, execution?: object) {
  const submitted: { path: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, options: RequestInit) => {
    if (options.method === "POST") submitted.push({ path, body: JSON.parse(String(options.body)) });
    if (path === "/api/plans/validate") return response({ allowed, execution, cost: { monthly: 6 }, violations: allowed ? [] : [{ code: "mixed_profiles", message: "Not supported" }] });
    if (path === "/api/blueprints" && options.method === "POST") return response({ id: "saved-graph" });
    if (path === "/api/studio/offerings") return response({ title: "My blueprint", version: 1 });
    return response([]);
  }));
  render(<Canvas user={{ id: "editor", username: "editor", role: roleName }} plugins={[image, role, vm]} pluginsLoading={false} pluginsError="" retryPlugins={() => {}} onJob={() => {}} />);
  return submitted;
}

it("starts blank, configures two independent squares and publishes the admitted blueprint", async () => {
  const submitted = mount();
  const add = () => fireEvent.click(screen.getByRole("button", { name: "Add resource" }));
  add();
  expect(screen.getByRole("button", { name: "Validate blueprint" })).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByRole("button", { name: "Container" }));
  expect(screen.getByRole("link", { name: "Docker Hub" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Deployment adapter"), { target: { value: image.id } });
  expect(screen.getByText("library/nginx:1.28")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Configuration JSON"), { target: { value: '{"cpu":3}' } });
  fireEvent.click(screen.getByRole("button", { name: "Apply values" }));
  expect(screen.getByLabelText("CPU")).toHaveProperty("value", "3");
  add();
  fireEvent.click(screen.getByRole("button", { name: "Ansible configuration" }));
  expect(screen.getByRole("link", { name: "Ansible Galaxy" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Deployment adapter"), { target: { value: role.id } });
  expect(screen.getByText("owner.setup @ 2")).toBeTruthy();
  fireEvent.click(screen.getByRole("checkbox", { name: "Web image" }));
  fireEvent.click(screen.getByRole("button", { name: /^Web image — Container$/ }));
  expect(screen.getByRole("checkbox", { name: /Guest setup.*cycle/ })).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByRole("button", { name: "Validate blueprint" }));
  await screen.findByText("Policies allow this blueprint.");
  fireEvent.click(screen.getByRole("button", { name: "Publish to Service Catalog" }));
  await screen.findByText(/Published to Service Catalog/);
  expect(submitted.map((post) => post.path)).toEqual(["/api/plans/validate", "/api/blueprints", "/api/studio/offerings"]);
  const graph = submitted[1].body.graph as Graph;
  expect(graph.nodes.map((n) => n.config.cpu)).toEqual([3, 1]);
  expect(graph.edges).toEqual([{ id: expect.any(String), source: graph.nodes[0].id, target: graph.nodes[1].id }]);
  expect(submitted[2].body).toEqual({ title: "My blueprint", blueprint_id: "saved-graph" });
});

it("shows unavailable adapters without letting them count as configured", () => {
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Add resource" }));
  fireEvent.click(screen.getByRole("button", { name: "Virtual machine" }));
  const adapter = screen.getByLabelText("Deployment adapter");
  expect(within(adapter).getByRole("option", { name: /Future VM.*unavailable/ })).toHaveProperty("disabled", true);
  expect(screen.getByRole("link", { name: "OpenTofu Registry" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Save" })).toHaveProperty("disabled", true);
});

it("keeps publication disabled for a graph blocked by the server", async () => {
  const submitted = mount("admin", false);
  fireEvent.click(screen.getByRole("button", { name: "Add resource" }));
  fireEvent.change(screen.getByLabelText("Deployment adapter"), { target: { value: image.id } });
  fireEvent.click(screen.getByRole("button", { name: "Validate blueprint" }));
  await screen.findByText(/Not supported/);
  expect(screen.getByRole("button", { name: "Publish to Service Catalog" })).toHaveProperty("disabled", true);
  expect(submitted).toHaveLength(1);
});

it("refuses undeclared variables, secret fields and non-object imports", () => {
  expect(() => parseResourceVariables('{"__proto__":{}}', image)).toThrow(/declared/);
  expect(() => parseResourceVariables('[{"cpu":2}]', image)).toThrow(/object/);
  const passwordPlugin: Plugin = { ...image, schema: { properties: { password: { type: "string" } } } };
  expect(() => parseResourceVariables('{"password":"not-a-real-password"}', passwordPlugin)).toThrow(/credential references/);
});


it("distinguishes a policy pass from an unavailable executor", async () => {
  mount("admin", true, { status: "unavailable", reason: "Provider registration unavailable" });
  fireEvent.click(screen.getByRole("button", { name: "Add resource" }));
  fireEvent.change(screen.getByLabelText("Deployment adapter"), { target: { value: image.id } });
  fireEvent.click(screen.getByRole("button", { name: "Validate blueprint" }));
  await screen.findByText("Provider registration unavailable");
  expect(screen.getByRole("button", { name: "Publish to Service Catalog" })).toHaveProperty("disabled", true);
  expect(screen.getByRole("button", { name: "Submit for approval" })).toHaveProperty("disabled", true);
});

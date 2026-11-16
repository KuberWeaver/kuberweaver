// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { Jobs } from "./pages";
import { emptyGraph, money } from "./model";

const admin = { id: "admin", username: "admin", role: "admin" };
const job = {
  id: "request-1", name: "Server-priced plan", environment: "dev",
  status: "pending_approval", created_at: "2026-01-01T00:00:00Z",
  created_by: "operator", graph: emptyGraph(), events: [],
  cost: { monthly: 42.75, currency: "EUR", breakdown: [
    { node_id: "web-1", plugin_id: "web", targets: 2, monthly: 42.75,
      resources: { cpu: 1, memory_mb: 512, disk_gb: 4 } },
  ] },
};
function loadJob(overrides = {}) {
  const value = { ...job, ...overrides };
  sessionStorage.setItem("kuberweaver.job", value.id);
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(
    JSON.stringify(path === "/api/jobs" ? [value] : value), { status: 200 },
  )));
  return render(<Jobs user={admin} />);
}
afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); });
it("shows server-priced internal monthly estimate and per-resource costs before approval", async () => {
  loadJob();
  const estimate = await screen.findByRole("region", { name: "Internal deployment estimate" });
  expect(estimate.textContent).toContain(`${money(42.75)} / month`);
  expect(estimate.textContent).toContain("EUR / month");
  expect(estimate.textContent).toContain("not metered billing");
  expect(estimate.textContent).toContain("web-1");
  expect(estimate.textContent).toContain("web");
  expect(within(estimate).getByRole("columnheader", { name: "Targets" })).toBeTruthy();
  expect(estimate.compareDocumentPosition(screen.getByRole("button", { name: "Approve" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it.each([
  { status: "failed" },
  { status: "succeeded", can_rollback: false },
  { status: "rollback_failed", requires_manual_review: true },
])("hides rollback for an ineligible job: %j", async (overrides) => {
  loadJob(overrides);
  await screen.findByRole("region", { name: "Internal deployment estimate" });
  expect(screen.queryByRole("button", { name: "Manual rollback" })).toBeNull();
});

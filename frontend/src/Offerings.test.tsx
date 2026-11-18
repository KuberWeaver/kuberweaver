// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MyServices, ServiceOfferings } from "./Offerings";
import { money } from "./model";

const admin = { id: "u-admin", username: "ada", role: "admin" };
const viewer = { id: "u-viewer", username: "vic", role: "viewer" };
const offerings = [
  { id: "off-1", title: "Platform VM", version: 2 },
  { id: "off-2", title: "Managed database", version: 1 },
];
const services = [
  { job_id: "job-1", title: "Platform VM", status: "pending_approval", monthly_estimate: 42.75 },
  { job_id: "job-2", title: "Managed database", status: "succeeded", monthly_estimate: 12 },
  { job_id: "job-3", title: "Edge proxy", status: "queued" },
];

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status });
}

function stubFetch(handler: (path: string, init: RequestInit) => unknown) {
  const calls: { path: string; method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      calls.push({
        path,
        method: (init.method || "GET").toUpperCase(),
        body: init.body ? JSON.parse(String(init.body)) : undefined,
      });
      return json(handler(path, init));
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it("shows every signed-in role the offering catalogue with versions", async () => {
  stubFetch((path) => (path === "/api/studio/offerings" ? offerings : []));
  render(<ServiceOfferings user={viewer} />);

  expect(await screen.findByRole("heading", { name: /Platform VM/ })).toBeTruthy();
  expect(screen.getByText(/version 2|v2/i)).toBeTruthy();
  expect(screen.getAllByText(/pending approval/i).length).toBeGreaterThan(0);
  expect(screen.getByText(/raw building blocks|extensions catalog|extension catalog/i)).toBeTruthy();
});

it("keeps viewers from requesting while offering operators a clear path", async () => {
  const calls = stubFetch((path) => (path === "/api/studio/offerings" ? offerings : []));
  render(<ServiceOfferings user={viewer} />);
  await screen.findByRole("heading", { name: /Platform VM/ });

  const request = screen.getByRole("button", { name: "Request Platform VM" }) as HTMLButtonElement;
  expect(request.disabled).toBe(true);
  expect(screen.getByText(/operator or administrator/i)).toBeTruthy();
  expect(calls.some((call) => call.method === "POST")).toBe(false);
});

it("requests an offering as an administrator and points at My services", async () => {
  const calls = stubFetch((path, init) => {
    if (path === "/api/studio/offerings") return offerings;
    if (path === "/api/studio/offerings/off-2/request" && init.method === "POST") {
      return { job_id: "job-99" };
    }
    return [];
  });
  let opened = 0;
  render(<ServiceOfferings user={admin} onOpenServices={() => { opened += 1; }} />);
  await screen.findByRole("heading", { name: /Managed database/ });

  fireEvent.click(screen.getByRole("button", { name: "Request Managed database" }));

  const message = await screen.findByText(/job-99/);
  expect(message.textContent).toContain("pending approval");
  const post = calls.find((call) => call.path === "/api/studio/offerings/off-2/request");
  expect(post?.method).toBe("POST");
  expect(post?.body).toEqual({});
  fireEvent.click(screen.getByRole("button", { name: "Open my services" }));
  expect(opened).toBe(1);
});

it("lists services with status and estimate, marking approval as pending", async () => {
  stubFetch((path) => (path === "/api/studio/services" ? services : []));
  render(<MyServices />);

  expect(await screen.findByRole("columnheader", { name: "Service" })).toBeTruthy();
  expect(screen.getByText("Platform VM")).toBeTruthy();
  expect(screen.getByText("Awaiting approval")).toBeTruthy();
  expect(screen.getAllByText(/pending approval/i).length).toBeGreaterThan(0);
  expect(screen.getByText(`${money(42.75)} / month`)).toBeTruthy();
  expect(screen.getByText("Not estimated")).toBeTruthy();
  expect(screen.getAllByText(/not bills|not a bill/i).length).toBeGreaterThan(0);
  expect(screen.queryByText(/invoice|charged/i)).toBeNull();
});

it("opens the deployment behind a service and remembers the selection", async () => {
  stubFetch((path) => (path === "/api/studio/services" ? services : []));
  let opened = 0;
  render(<MyServices onOpenDeployments={() => { opened += 1; }} />);
  await screen.findByText("Platform VM");

  fireEvent.click(screen.getByRole("button", { name: "Open deployment job-2" }));

  expect(sessionStorage.getItem("kuberweaver.job")).toBe("job-2");
  expect(opened).toBe(1);
});

it("offers the service catalog when there are no services yet", async () => {
  stubFetch(() => []);
  let opened = 0;
  render(<MyServices onOpenCatalogue={() => { opened += 1; }} />);

  expect(await screen.findByText("No services yet")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Browse service catalog" }));
  expect(opened).toBe(1);
});

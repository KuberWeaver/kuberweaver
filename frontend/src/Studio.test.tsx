// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { EngineeringStudio } from "./Studio";
import type { StudioArtifact } from "./studio";

const admin = { id: "u-admin", username: "ada", role: "admin" };
const operator = { id: "u-op", username: "ops", role: "operator" };
const offerings = [{ id: "off-1", title: "Platform VM", version: 1 }];
const blueprints = [
  { id: "bp-1", name: "Three-tier web", graph: { name: "Three-tier web" } },
  { id: "bp-2", name: "Edge proxy", graph: { name: "Edge proxy" } },
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
      const result = handler(path, init);
      const refused =
        !!result && typeof result === "object" && "detail" in (result as object);
      return json(result, refused ? 409 : 200);
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("lists registered artifacts as metadata and registers a new reference", async () => {
  let artifacts: StudioArtifact[] = [
    { id: "art-1", kind: "oci", reference: "registry.example/base:1.2", pin: "sha256:deadbeef" },
  ];
  const calls = stubFetch((path, init) => {
    if (path === "/api/studio/artifacts" && (init.method || "GET") === "GET") return artifacts;
    if (path === "/api/studio/artifacts") {
      const created = { id: "art-2", ...JSON.parse(String(init.body)) };
      artifacts = [...artifacts, created];
      return created;
    }
    if (path === "/api/studio/offerings") return offerings;
    if (path === "/api/blueprints") return blueprints;
    return [];
  });
  render(<EngineeringStudio user={admin} />);

  expect(await screen.findByText("registry.example/base:1.2")).toBeTruthy();
  const registry = screen.getByRole("table");
  expect(within(registry).getByText("OCI image")).toBeTruthy();
  expect(within(registry).getByText("sha256:deadbeef")).toBeTruthy();
  expect(screen.getByText(/metadata only/i)).toBeTruthy();
  expect(screen.getByText(/never fetch|never fetched|no download|not a download/i)).toBeTruthy();

  fireEvent.change(screen.getByLabelText("Artifact kind"), { target: { value: "vm-image" } });
  fireEvent.change(screen.getByLabelText("Reference"), {
    target: { value: "vault://images/debian-12" },
  });
  fireEvent.change(screen.getByLabelText("Pin (optional)"), {
    target: { value: " 2026.01 " },
  });
  fireEvent.click(screen.getByRole("button", { name: "Register artifact" }));

  await waitFor(() =>
    expect(screen.getByText("vault://images/debian-12")).toBeTruthy(),
  );
  const post = calls.find((call) => call.method === "POST" && call.path === "/api/studio/artifacts");
  expect(post?.body).toEqual({
    kind: "vm-image",
    reference: "vault://images/debian-12",
    pin: "2026.01",
  });
  expect(within(screen.getByRole("table")).getByText("VM image")).toBeTruthy();
  expect(screen.getByText(/Artifact registered/)).toBeTruthy();
});

it("refuses an empty artifact reference without calling the API", async () => {
  const calls = stubFetch((path) => {
    if (path === "/api/studio/artifacts") return [];
    if (path === "/api/studio/offerings") return offerings;
    if (path === "/api/blueprints") return blueprints;
    return [];
  });
  render(<EngineeringStudio user={admin} />);
  await screen.findByText("No artifacts registered");

  fireEvent.click(screen.getByRole("button", { name: "Register artifact" }));

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("Reference is required.");
  expect(calls.some((call) => call.method === "POST")).toBe(false);
});

it("publishes an offering from an existing blueprint as an administrator", async () => {
  let offeringList = [...offerings];
  const calls = stubFetch((path, init) => {
    if (path === "/api/studio/artifacts") return [];
    if (path === "/api/studio/offerings" && (init.method || "GET") === "GET") return offeringList;
    if (path === "/api/studio/offerings") {
      const body = JSON.parse(String(init.body));
      const created = { id: "off-2", title: body.title, version: 1 };
      offeringList = [...offeringList, created];
      return created;
    }
    if (path === "/api/blueprints") return blueprints;
    return [];
  });
  render(<EngineeringStudio user={admin} />);
  expect(await screen.findByText("Platform VM")).toBeTruthy();

  fireEvent.change(screen.getByLabelText("Offering title"), {
    target: { value: "Managed database" },
  });
  fireEvent.change(screen.getByLabelText("Blueprint"), { target: { value: "bp-2" } });
  fireEvent.click(screen.getByRole("button", { name: "Publish offering" }));

  expect(await screen.findByText("Managed database")).toBeTruthy();
  const post = calls.find((call) => call.method === "POST" && call.path === "/api/studio/offerings");
  expect(post?.body).toEqual({ title: "Managed database", blueprint_id: "bp-2" });
  expect(screen.getByText(/Published/)).toBeTruthy();
});

it("shows operators the offering catalogue but not the publish form", async () => {
  stubFetch((path) => {
    if (path === "/api/studio/artifacts") return [];
    if (path === "/api/studio/offerings") return offerings;
    if (path === "/api/blueprints") return blueprints;
    return [];
  });
  render(<EngineeringStudio user={operator} />);
  expect(await screen.findByText("Platform VM")).toBeTruthy();
  expect(screen.getByText(/requires an administrator/i)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Publish offering" })).toBeNull();
  expect(screen.queryByLabelText("Offering title")).toBeNull();
});

it("requests an offering and reports the pending approval with a deployment link", async () => {
  const calls = stubFetch((path, init) => {
    if (path === "/api/studio/artifacts") return [];
    if (path === "/api/studio/offerings") return offerings;
    if (path === "/api/studio/offerings/off-1/request" && init.method === "POST") {
      return { job_id: "job-42" };
    }
    if (path === "/api/blueprints") return blueprints;
    return [];
  });
  let opened = 0;
  render(
    <EngineeringStudio user={operator} onOpenDeployments={() => { opened += 1; }} />,
  );
  await screen.findByText("Platform VM");

  fireEvent.click(screen.getByRole("button", { name: "Request Platform VM" }));

  const success = await screen.findByText(/job-42/);
  expect(success.textContent).toContain("pending approval");
  expect(success.textContent?.toLowerCase()).toContain("no infrastructure is created");
  const post = calls.find((call) => call.path === "/api/studio/offerings/off-1/request");
  expect(post?.method).toBe("POST");
  expect(post?.body).toEqual({});
  fireEvent.click(screen.getByRole("button", { name: "Open deployments" }));
  expect(opened).toBe(1);
});

it("reports a refused request instead of pretending it succeeded", async () => {
  stubFetch((path, init) => {
    if (path === "/api/studio/artifacts") return [];
    if (path === "/api/studio/offerings") return offerings;
    if (path === "/api/studio/offerings/off-1/request" && init.method === "POST") {
      return { detail: "Offering is not requestable yet" };
    }
    if (path === "/api/blueprints") return blueprints;
    return [];
  });
  render(<EngineeringStudio user={operator} />);
  await screen.findByText("Platform VM");

  fireEvent.click(screen.getByRole("button", { name: "Request Platform VM" }));

  expect((await screen.findByRole("alert")).textContent).toContain("not requestable");
  expect(screen.queryByText(/job-42/)).toBeNull();
});

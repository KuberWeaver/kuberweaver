// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { App } from "./App";

const admin = { id: "u-admin", username: "ada", role: "admin" };
const viewer = { id: "u-viewer", username: "vic", role: "viewer" };

function stubWorkspace(user: { id: string; username: string; role: string }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      const body =
        path === "/api/auth/me"
          ? { user, csrf_token: "token" }
          : path === "/api/studio/offerings" || path === "/api/studio/services"
            ? []
            : [];
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

it("gives an administrator the engineer studio beside the service pages", async () => {
  window.location.hash = "#services";
  stubWorkspace(admin);
  render(<App />);

  const nav = await screen.findByRole("navigation", { name: "Main navigation" });
  expect(within(nav).getByRole("button", { name: "Engineer Studio" })).toBeTruthy();
  expect(within(nav).getByRole("button", { name: "Service Catalog" })).toBeTruthy();
  expect(within(nav).getByRole("button", { name: "My services" })).toBeTruthy();
  expect(within(nav).getByRole("button", { name: "Extension Catalog" })).toBeTruthy();
  expect(
    within(nav).getByRole("button", { name: "My services" }).getAttribute("aria-current"),
  ).toBe("page");
});

it("keeps the engineer studio out of a customer's navigation but leaves services reachable", async () => {
  window.location.hash = "#offerings";
  stubWorkspace(viewer);
  render(<App />);

  const nav = await screen.findByRole("navigation", { name: "Main navigation" });
  expect(within(nav).queryByRole("button", { name: "Engineer Studio" })).toBeNull();
  expect(within(nav).getByRole("button", { name: "Service Catalog" })).toBeTruthy();
  expect(within(nav).getByRole("button", { name: "My services" })).toBeTruthy();
  expect(await screen.findByRole("heading", { name: "Service Catalog" })).toBeTruthy();
});

it("routes from the sidebar to the customer-facing catalogue", async () => {
  window.location.hash = "#services";
  stubWorkspace(viewer);
  render(<App />);

  const nav = await screen.findByRole("navigation", { name: "Main navigation" });
  fireEvent.click(within(nav).getByRole("button", { name: "Service Catalog" }));

  expect(await screen.findByRole("heading", { name: "Service Catalog" })).toBeTruthy();
  expect(window.location.hash).toBe("#offerings");
});

it("refuses a guest's direct address to the engineer studio", async () => {
  window.location.hash = "#studio";
  stubWorkspace(viewer);
  render(<App />);

  expect(await screen.findByText("Page not available")).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Artifact registry" })).toBeNull();
});

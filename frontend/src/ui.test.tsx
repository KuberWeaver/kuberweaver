// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Empty, LoadingState, ResourceBoundary } from "./ui";

afterEach(() => { cleanup(); });

it("renders an empty state with a title, explanation and action", () => {
  let created = 0;
  render(
    <Empty
      title="Nothing here"
      action={<button onClick={() => { created += 1; }}>Create</button>}
    >
      Add the first item.
    </Empty>,
  );
  expect(screen.getByRole("heading", { name: "Nothing here" })).toBeTruthy();
  expect(screen.getByText("Add the first item.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  expect(created).toBe(1);
});

it("announces loading through a labelled status region", () => {
  render(<LoadingState label="Loading deployments" />);
  const status = screen.getByRole("status", { name: "Loading deployments" });
  expect(status.getAttribute("aria-busy")).toBe("true");
  expect(status.textContent).toContain("Loading…");
});

it("lets the resource boundary own the loading and error states", () => {
  const { rerender } = render(
    <ResourceBoundary loading error="" retry={() => {}}>
      <p>content</p>
    </ResourceBoundary>,
  );
  expect(screen.getByRole("status", { name: "Loading content" })).toBeTruthy();
  expect(screen.queryByText("content")).toBeNull();

  let retried = 0;
  rerender(
    <ResourceBoundary loading={false} error="Server unavailable" retry={() => { retried += 1; }}>
      <p>content</p>
    </ResourceBoundary>,
  );
  expect(screen.queryByText("content")).toBeNull();
  expect(screen.getByRole("alert").textContent).toContain("Server unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(retried).toBe(1);

  rerender(
    <ResourceBoundary loading={false} error="" retry={() => {}}>
      <p>content</p>
    </ResourceBoundary>,
  );
  expect(screen.getByText("content")).toBeTruthy();
});

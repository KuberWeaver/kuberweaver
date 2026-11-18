import { expect, it } from "vitest";
import {
  defaultPageId,
  navigationFor,
  navigationGroups,
  pageFor,
  pageHeading,
  pageIds,
  pages,
} from "./navigation";
import type { PageId } from "./navigation";

it("keeps the page registry stable", () => {
  expect({ defaultPageId, navigationGroups, pages }).toMatchSnapshot();
});

it("keeps the grouped navigation stable per role", () => {
  expect(navigationFor("admin")).toMatchSnapshot();
  expect(navigationFor("operator")).toMatchSnapshot();
  expect(navigationFor("viewer")).toMatchSnapshot();
});

it("registers every page id once and orders pages by group", () => {
  expect([...new Set(pages.map((page) => page.id))]).toHaveLength(pages.length);
  expect(pages.map((page) => page.id).sort()).toEqual([...pageIds].sort());
  const order = navigationGroups.map((group) => group.id);
  expect(pages.map((page) => order.indexOf(page.group))).toEqual(
    [...pages.map((page) => order.indexOf(page.group))].sort((a, b) => a - b),
  );
});

it("hides administration pages from roles without the admin role", () => {
  const idsFor = (role: string) =>
    navigationFor(role).flatMap((group) => group.pages.map((page) => page.id));
  expect(idsFor("admin")).toEqual(pages.map((page) => page.id));
  expect(idsFor("operator")).not.toContain("operations");
  expect(idsFor("operator")).not.toContain("agents");
  expect(idsFor("viewer")).not.toContain("agents");
  expect(idsFor("viewer")).toContain("audit");
});

it("keeps the engineer studio away from the customer-facing roles", () => {
  const idsFor = (role: string) =>
    navigationFor(role).flatMap((group) => group.pages.map((page) => page.id));
  expect(idsFor("admin")).toContain("studio");
  expect(idsFor("operator")).toContain("studio");
  expect(idsFor("viewer")).not.toContain("studio");
  expect(idsFor("viewer")).toContain("offerings");
  expect(idsFor("viewer")).toContain("services");
  expect(idsFor("viewer")).toContain("catalogue");
});

it("resolves a page id and refuses unknown addresses", () => {
  expect(pageFor("jobs")?.label).toBe("Deployments");
  expect(pageFor("#jobs")).toBeNull();
  expect(pageFor("missing")).toBeNull();
  expect(pageFor("offerings")?.label).toBe("Service Catalog");
  expect(pageFor("services")?.label).toBe("My services");
  expect(pageFor("studio")?.label).toBe("Engineer Studio");
});

it("returns heading copy for a known page and refuses an unknown id", () => {
  expect(pageHeading("jobs")).toEqual({
    eyebrow: "OPERATE · DEPLOYMENTS",
    title: "Deployments",
    description: "From approval to rollback: every step leaves an audit trail.",
  });
  expect(() => pageHeading("missing" as PageId)).toThrow(/Unknown page/);
});

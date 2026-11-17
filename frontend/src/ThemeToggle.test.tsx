// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ThemeToggle } from "./ThemeToggle";
import { THEME_COLORS, THEME_STORAGE_KEY } from "./theme";

function meta(): HTMLElement {
  return document.querySelector('meta[name="theme-color"]') as HTMLElement;
}

beforeEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  document.head.innerHTML = `<meta name="theme-color" content="${THEME_COLORS.dark}" />`;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function toggle(): HTMLElement {
  return screen.getByRole("button", { name: "Black theme" });
}

it("opens on the black theme and persists both switches", () => {
  render(<ThemeToggle />);
  expect(toggle().getAttribute("aria-pressed")).toBe("true");
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(meta().getAttribute("content")).toBe(THEME_COLORS.dark);

  fireEvent.click(toggle());
  expect(toggle().getAttribute("aria-pressed")).toBe("false");
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  expect(meta().getAttribute("content")).toBe(THEME_COLORS.light);
  expect(toggle().getAttribute("title")).toBe("Switch to the black theme");

  fireEvent.click(toggle());
  expect(toggle().getAttribute("aria-pressed")).toBe("true");
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  expect(meta().getAttribute("content")).toBe(THEME_COLORS.dark);
});

it("starts from the theme the boot script already applied", () => {
  document.documentElement.dataset.theme = "light";
  window.localStorage.setItem(THEME_STORAGE_KEY, "light");
  render(<ThemeToggle />);
  expect(toggle().getAttribute("aria-pressed")).toBe("false");
});

it("starts from the stored preference when no theme was applied", () => {
  window.localStorage.setItem(THEME_STORAGE_KEY, "light");
  render(<ThemeToggle />);
  expect(toggle().getAttribute("aria-pressed")).toBe("false");
  expect(document.documentElement.dataset.theme).toBe("light");
});

it("treats an unknown stored value as the default", () => {
  window.localStorage.setItem(THEME_STORAGE_KEY, "solarized");
  render(<ThemeToggle />);
  expect(toggle().getAttribute("aria-pressed")).toBe("true");
  expect(document.documentElement.dataset.theme).toBe("dark");
});

it("follows a switch made in another tab", async () => {
  render(<ThemeToggle />);
  window.localStorage.setItem(THEME_STORAGE_KEY, "light");
  window.dispatchEvent(
    new StorageEvent("storage", {
      key: THEME_STORAGE_KEY,
      newValue: "light",
    }),
  );
  await waitFor(() =>
    expect(toggle().getAttribute("aria-pressed")).toBe("false"),
  );
  expect(document.documentElement.dataset.theme).toBe("light");

  window.dispatchEvent(
    new StorageEvent("storage", {
      key: "kuberweaver.unrelated",
      newValue: "dark",
    }),
  );
  expect(toggle().getAttribute("aria-pressed")).toBe("false");
});

it("keeps working when the browser refuses to store the preference", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("denied");
  });
  render(<ThemeToggle />);
  fireEvent.click(toggle());
  expect(toggle().getAttribute("aria-pressed")).toBe("false");
  expect(document.documentElement.dataset.theme).toBe("light");
});

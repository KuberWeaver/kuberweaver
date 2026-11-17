import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_THEME,
  THEME_COLORS,
  THEME_STORAGE_KEY,
  isTheme,
  readTheme,
} from "./theme";

const css = readFileSync(new URL("./style.css", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const boot = readFileSync(
  new URL("../public/theme-boot.js", import.meta.url),
  "utf8",
);

function blockRange(selector: string): [number, number] {
  const start = css.indexOf(selector);
  if (start < 0) throw new Error(`missing block ${selector}`);
  const open = css.indexOf("{", start);
  let depth = 0;
  for (let index = open; index < css.length; index += 1) {
    if (css[index] === "{") depth += 1;
    else if (css[index] === "}") {
      depth -= 1;
      if (depth === 0) return [open, index];
    }
  }
  throw new Error(`unterminated block ${selector}`);
}

function tokenBlock(selector: string): Map<string, string> {
  const [open, close] = blockRange(selector);
  const tokens = new Map<string, string>();
  for (const line of css.slice(open + 1, close).split("\n")) {
    const match = /^\s*--([a-z-]+):\s*([^;]+);/.exec(line);
    if (match) tokens.set(match[1], match[2].trim());
  }
  return tokens;
}

const dark = tokenBlock(":root {");
const light = tokenBlock(':root[data-theme="light"] {');

function blockEnd(selector: string): number {
  return blockRange(selector)[1] + 1;
}

function channel(value: number): number {
  return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function luminance(color: string): number {
  const raw = color.trim().replace("#", "").slice(0, 6);
  const channels = [0, 2, 4].map((index) =>
    channel(parseInt(raw.slice(index, index + 2), 16) / 255),
  );
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(foreground: string, background: string): number {
  const [high, low] = [luminance(foreground), luminance(background)].sort(
    (a, b) => b - a,
  );
  return (high + 0.05) / (low + 0.05);
}

function storage(value: string | null | Error): Storage {
  return {
    getItem: () => {
      if (value instanceof Error) throw value;
      return value;
    },
  } as unknown as Storage;
}

function runBoot(value: string | null | Error): {
  theme?: string;
  color: string;
} {
  const attributes: Record<string, string> = {};
  const meta = {
    content: "",
    setAttribute(name: string, next: string) {
      if (name === "content") this.content = next;
    },
  };
  const evaluate = new Function("window", "document", boot) as (
    window: unknown,
    document: unknown,
  ) => void;
  evaluate(
    {
      localStorage: {
        getItem: () => {
          if (value instanceof Error) throw value;
          return value;
        },
      },
    },
    {
      documentElement: {
        setAttribute: (name: string, next: string) => {
          attributes[name] = next;
        },
      },
      querySelector: () => meta,
    },
  );
  return { theme: attributes["data-theme"], color: meta.content };
}

describe("theme preference", () => {
  it("defaults to the black theme", () => {
    expect(DEFAULT_THEME).toBe("dark");
    expect(readTheme(storage(null))).toBe("dark");
    expect(readTheme(null)).toBe("dark");
  });

  it("honours a stored light preference", () => {
    expect(readTheme(storage("light"))).toBe("light");
  });

  it("refuses unknown values instead of trusting them", () => {
    expect(readTheme(storage("solarized"))).toBe("dark");
    expect(readTheme(storage("Dark"))).toBe("dark");
    expect(readTheme(storage(""))).toBe("dark");
    expect(isTheme("light")).toBe(true);
    expect(isTheme("dark")).toBe(true);
    expect(isTheme("LIGHT")).toBe(false);
    expect(isTheme(null)).toBe(false);
    expect(isTheme(undefined)).toBe(false);
    expect(isTheme(1)).toBe(false);
  });

  it("falls back to the default when the store cannot be read", () => {
    expect(readTheme(storage(new Error("denied")))).toBe("dark");
  });
});

describe("first paint", () => {
  it("applies the stored theme before the app module loads", () => {
    expect(boot).toContain(THEME_STORAGE_KEY);
    expect(boot).not.toMatch(/https?:/);
    expect(html).toContain('<script src="/theme-boot.js"></script>');
    expect(html.indexOf("/theme-boot.js")).toBeLessThan(
      html.indexOf("/src/main.tsx"),
    );
    expect(html).toMatch(/<html[^>]*data-theme="dark"/);
    expect(html).toContain(`content="${THEME_COLORS.dark}"`);
  });

  it("marks the document black when nothing is stored", () => {
    expect(runBoot(null)).toEqual({ theme: "dark", color: THEME_COLORS.dark });
  });

  it("marks the document light for a stored light preference", () => {
    expect(runBoot("light")).toEqual({
      theme: "light",
      color: THEME_COLORS.light,
    });
  });

  it("ignores an unknown stored value and a refused store", () => {
    expect(runBoot("solarized").theme).toBe("dark");
    expect(runBoot(new Error("denied"))).toEqual({
      theme: "dark",
      color: THEME_COLORS.dark,
    });
  });

  it("keeps the colours in step with the token blocks", () => {
    expect(THEME_COLORS.dark).toBe(dark.get("bg"));
    expect(THEME_COLORS.light).toBe(light.get("bg"));
  });
});

describe("style sheet", () => {
  it("defines the same tokens for both themes", () => {
    expect([...light.keys()].sort()).toEqual([...dark.keys()].sort());
    expect(dark.size).toBeGreaterThan(30);
    expect(dark.get("color-scheme")).toBeUndefined();
    expect(css).toContain("color-scheme: dark");
    expect(css).toContain("color-scheme: light");
  });

  it("defines every token it reads", () => {
    /* --radius is geometry, not colour: it lives with the typography block. */
    const geometry = new Set(["radius"]);
    const used = [...css.matchAll(/var\(--([a-z-]+)\)/g)].map((m) => m[1]);
    expect(used.length).toBeGreaterThan(100);
    for (const name of new Set(used)) {
      if (geometry.has(name)) continue;
      expect(dark.has(name), `dark is missing --${name}`).toBe(true);
      expect(light.has(name), `light is missing --${name}`).toBe(true);
    }
  });

  it("keeps literal colours inside the token blocks", () => {
    const outside =
      css.slice(0, css.indexOf(":root {")) +
      css.slice(blockEnd(':root[data-theme="light"] {'));
    expect(outside).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(outside).not.toMatch(/\b(?:rgb|hsl|color-mix)\(/i);
  });

  it("never loads a remote font", () => {
    expect(css).not.toMatch(/@import/i);
    expect(css).not.toMatch(/fonts\.googleapis|fonts\.gstatic/i);
    expect(css).not.toMatch(/url\(\s*["']?https?:/i);
  });

  it("keeps text readable on every surface in both themes", () => {
    const pairs: Array<[string, string, number]> = [
      ["ink", "surface", 4.5],
      ["text", "surface", 4.5],
      ["text", "bg", 4.5],
      ["text-secondary", "surface", 4.5],
      ["muted", "surface", 4.5],
      ["faint", "surface", 3.5],
      ["ghost", "surface", 2.9],
      ["accent", "surface", 4.5],
      ["accent", "bg", 4.5],
      ["on-accent", "accent-fill", 4.5],
      ["on-accent", "accent-fill-hover", 4.5],
      ["success-text", "success-bg", 4.5],
      ["warning-text", "warning-bg", 4.5],
      ["danger-text", "danger-bg", 4.5],
      ["info-text", "info-bg", 4.5],
      ["on-dark", "sidebar", 4.5],
      ["on-dark-muted", "sidebar", 4.5],
      ["on-dark-faint", "sidebar", 4],
      ["accent-on-dark", "sidebar-active", 4.5],
      ["console-text", "console-bg", 4.5],
      ["console-muted", "console-bg", 4.5],
      ["console-error", "console-bg", 4.5],
      ["console-warning", "console-bg", 4.5],
      ["console-phase", "console-bg", 4.5],
    ];
    for (const [name, tokens] of [
      ["black", dark],
      ["light", light],
    ] as const) {
      for (const [foreground, background, minimum] of pairs) {
        const value = contrast(
          tokens.get(foreground) ?? "",
          tokens.get(background) ?? "",
        );
        expect(
          value,
          `${name}: --${foreground} on --${background} is ${value.toFixed(2)}`,
        ).toBeGreaterThanOrEqual(minimum);
      }
    }
  });
});

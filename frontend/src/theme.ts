import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";

/* The boot script in public/theme-boot.js reads the same key before the first
   paint, so keep the three constants below in sync with it. theme.test.ts
   refuses a divergence. */
export const THEME_STORAGE_KEY = "kuberweaver.theme";
export const DEFAULT_THEME: Theme = "dark";
export const THEME_COLORS: Record<Theme, string> = {
  dark: "#0b1116",
  light: "#f5f7f9",
};

export function isTheme(value: unknown): value is Theme {
  return value === "dark" || value === "light";
}

/* An unreadable or unavailable store is not an error: the browser may run with
   storage disabled, and the default theme still has to render. */
export function browserStorage(): Storage | null {
  try {
    return window.localStorage ?? null;
  } catch {
    return null;
  }
}

export function readTheme(store: Storage | null = browserStorage()): Theme {
  try {
    const stored = store?.getItem(THEME_STORAGE_KEY) ?? null;
    return isTheme(stored) ? stored : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function applyTheme(
  theme: Theme,
  root: HTMLElement = document.documentElement,
): void {
  root.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", THEME_COLORS[theme]);
}

export function storeTheme(
  theme: Theme,
  store: Storage | null = browserStorage(),
): void {
  try {
    store?.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* Quota or a disabled store only costs persistence, not the theme. */
  }
}

export function setTheme(theme: Theme): void {
  storeTheme(theme);
  applyTheme(theme);
}

function appliedTheme(): Theme {
  const applied = document.documentElement.dataset.theme;
  return isTheme(applied) ? applied : readTheme();
}

export function useTheme(): {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggle: () => void;
} {
  const [theme, setThemeState] = useState<Theme>(appliedTheme);
  /* The boot script owns the first paint; this keeps the document in step when
     the component mounts without it, for instance in tests or after an error. */
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);
  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key !== null && event.key !== THEME_STORAGE_KEY) return;
      const next = isTheme(event.newValue) ? event.newValue : DEFAULT_THEME;
      applyTheme(next);
      setThemeState(next);
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);
  const select = useCallback((next: Theme) => {
    storeTheme(next);
    applyTheme(next);
    setThemeState(next);
  }, []);
  const toggle = useCallback(() => {
    select(theme === "dark" ? "light" : "dark");
  }, [select, theme]);
  return { theme, setTheme: select, toggle };
}

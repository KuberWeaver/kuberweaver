import { Moon, Sun } from "lucide-react";
import { useTheme } from "./theme";

/* The label names the state, not the action, so a screen reader announces the
   pressed state of the control instead of a button whose name flips. */
export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const dark = theme === "dark";
  return (
    <button
      type="button"
      className="icon-button theme-toggle"
      aria-label="Black theme"
      aria-pressed={dark}
      title={dark ? "Switch to the light theme" : "Switch to the black theme"}
      onClick={toggle}
    >
      {dark ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  );
}

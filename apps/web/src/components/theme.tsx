"use client";

import { MonitorIcon, MoonIcon, SunIcon } from "lucide-react";
import { type ReactNode, createContext, use, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export type Theme = "light" | "dark" | "system";

const KEY = "theme";

/** Runs in <head> before the first paint, so a dark page never flashes light. */
export const THEME_SCRIPT = `(() => {
  try {
    const theme = localStorage.getItem("${KEY}");
    const dark = theme === "dark" || (theme !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", dark);
  } catch {}
})();`;

interface ThemeState {
  theme: Theme;
  resolved: "light" | "dark";
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeState>({ theme: "system", resolved: "light", setTheme: () => {} });

export function useTheme(): ThemeState {
  return use(ThemeContext);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // null until read from storage after mounting, so the class set by THEME_SCRIPT stays put.
  const [theme, setTheme] = useState<Theme | null>(null);
  const [systemDark, setSystemDark] = useState(false);

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(KEY);
    } catch {
      // Storage can be blocked; the system setting still applies.
    }
    setTheme(stored === "light" || stored === "dark" ? stored : "system");
    const media = matchMedia("(prefers-color-scheme: dark)");
    setSystemDark(media.matches);
    const onChange = () => setSystemDark(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const resolved = theme === "dark" || (theme !== "light" && systemDark) ? "dark" : "light";
  useEffect(() => {
    if (theme !== null) document.documentElement.classList.toggle("dark", resolved === "dark");
  }, [theme, resolved]);

  const choose = (next: Theme) => {
    setTheme(next);
    try {
      if (next === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, next);
    } catch {
      // Not remembered, but still applied.
    }
  };

  return <ThemeContext value={{ theme: theme ?? "system", resolved, setTheme: choose }}>{children}</ThemeContext>;
}

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Theme">
          <SunIcon className="dark:hidden" />
          <MoonIcon className="hidden dark:block" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-36">
        <DropdownMenuRadioGroup value={theme} onValueChange={(value) => setTheme(value as Theme)}>
          <DropdownMenuRadioItem value="light">
            <SunIcon /> Light
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">
            <MoonIcon /> Dark
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system">
            <MonitorIcon /> System
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

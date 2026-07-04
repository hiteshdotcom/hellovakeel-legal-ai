import { create } from "zustand";

export type Theme = "light" | "dark";
export type SourcesTab = "graph" | "list";

const THEME_KEY = "nyaya-theme";

function initialTheme(): Theme {
  if (typeof window === "undefined") return "light";
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem(THEME_KEY, theme);
}

interface UIState {
  theme: Theme;
  sidebarCollapsed: boolean;
  mobileNavOpen: boolean;
  sourcesDrawerOpen: boolean;
  sourcesTab: SourcesTab;
  toggleTheme: () => void;
  toggleSidebar: () => void;
  setMobileNav: (open: boolean) => void;
  setSourcesDrawer: (open: boolean) => void;
  setSourcesTab: (tab: SourcesTab) => void;
}

export const useUI = create<UIState>((set, get) => {
  const theme = initialTheme();
  if (typeof document !== "undefined") applyTheme(theme);
  return {
    theme,
    sidebarCollapsed: false,
    mobileNavOpen: false,
    sourcesDrawerOpen: false,
    sourcesTab: "graph",
    toggleTheme: () => {
      const next: Theme = get().theme === "light" ? "dark" : "light";
      applyTheme(next);
      set({ theme: next });
    },
    toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
    setMobileNav: (mobileNavOpen) => set({ mobileNavOpen }),
    setSourcesDrawer: (sourcesDrawerOpen) => set({ sourcesDrawerOpen }),
    setSourcesTab: (sourcesTab) => set({ sourcesTab }),
  };
});

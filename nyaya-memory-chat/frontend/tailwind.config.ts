import type { Config } from "tailwindcss";

/**
 * Tokens are CSS variables (see src/index.css) that flip under
 * [data-theme="dark"]. Tailwind consumes them so `bg-surface text-ink`
 * auto-themes with zero per-component dark: variants.
 */
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        canvas: "var(--canvas)",
        surface: "var(--surface)",
        raised: "var(--surface-raised)",
        ink: {
          DEFAULT: "var(--ink)",
          2: "var(--ink-2)",
          3: "var(--ink-3)",
        },
        navy: "var(--navy)",
        gold: "var(--gold)",
        "gold-soft": "var(--gold-soft)",
        divider: "var(--divider)",
        "accent-soft": "var(--accent-soft)",
        "accent-ink": "var(--accent-ink)",
        good: { DEFAULT: "var(--good)", bg: "var(--good-bg)", bd: "var(--good-bd)" },
        warn: { DEFAULT: "var(--warn)", bg: "var(--warn-bg)", bd: "var(--warn-bd)" },
        bad: { DEFAULT: "var(--bad)", bg: "var(--bad-bg)", bd: "var(--bad-bd)" },
      },
      fontFamily: {
        serif: ["Newsreader", "Georgia", "serif"],
        sans: ["Outfit", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "monospace"],
      },
      boxShadow: {
        pop: "0 10px 30px rgba(28,25,23,0.10)",
        modal: "0 24px 70px rgba(28,25,23,0.28)",
        card: "0 2px 8px rgba(28,25,23,0.05)",
      },
      borderRadius: {
        xl2: "1.25rem",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "none" },
        },
        blink: { "0%,49%": { opacity: "1" }, "50%,100%": { opacity: "0" } },
        dot: { "0%,80%,100%": { opacity: "0.25" }, "40%": { opacity: "1" } },
        shimmer: {
          "0%": { backgroundPosition: "-400px 0" },
          "100%": { backgroundPosition: "400px 0" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.22s ease both",
        blink: "blink 1s steps(1) infinite",
      },
    },
  },
  plugins: [],
};

export default config;

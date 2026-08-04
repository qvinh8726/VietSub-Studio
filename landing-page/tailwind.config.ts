import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#14211d",
        paper: "#fffaf0",
        canvas: "#eee8dc",
        forest: "#173f35",
        "forest-2": "#245e4f",
        mint: "#b9dbc8",
        signal: "#ff6b21",
        sun: "#edae49",
        "sun-soft": "#f8dfad",
        coral: "#983b2e",
        blue: "#427d96",
      },
      fontFamily: {
        sans: ["var(--font-body)", "sans-serif"],
        display: ["var(--font-display)", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      boxShadow: {
        soft: "0 20px 70px rgba(29, 34, 48, 0.12)",
        card: "0 12px 35px rgba(29, 34, 48, 0.08)",
      },
    },
  },
  plugins: [],
};

export default config;

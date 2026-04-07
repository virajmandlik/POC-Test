import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: "#2E7D32",
          dark: "#1B5E20",
          light: "#66BB6A",
        },
        accent: "#F9A825",
        warn: "#F57F17",
        danger: "#C62828",
        bg: {
          main: "#FAFDF7",
          card: "#FFFFFF",
          sidebar: "#1B3A1B",
        },
        border: "#C8E6C9",
        text: {
          DEFAULT: "#1B3A1B",
          muted: "#5D7A5D",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
} satisfies Config;

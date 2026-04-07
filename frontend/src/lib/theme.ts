export const palette = {
  primary: "#2E7D32",
  primaryDark: "#1B5E20",
  primaryLight: "#66BB6A",
  accent: "#F9A825",
  warn: "#F57F17",
  danger: "#C62828",
  bgMain: "#FAFDF7",
  bgCard: "#FFFFFF",
  bgSidebar: "#1B3A1B",
  border: "#C8E6C9",
  text: "#1B3A1B",
  textMuted: "#5D7A5D",
} as const;

export const statusColors: Record<string, { bg: string; text: string }> = {
  pending: { bg: "bg-amber-50", text: "text-amber-700" },
  running: { bg: "bg-green-50", text: "text-primary" },
  completed: { bg: "bg-green-100", text: "text-primary-dark" },
  failed: { bg: "bg-red-50", text: "text-danger" },
  cancelled: { bg: "bg-gray-100", text: "text-gray-600" },
};

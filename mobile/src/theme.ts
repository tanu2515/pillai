// Exact color parity with the web app's Tailwind config (frontend/events.html etc.)
// — same brand palette, same semantic names, so screens read as one system.
export const colors = {
  bg: "#F5FAFF",
  panel: "#FFFFFF",
  surface: "#FFFFFF",
  border: "#E7EEF7",
  muted: "#64748B",
  ink: "#123B6D",
  accent: "#1976D2",
  accentDim: "#123B6D",
  green: "#20C997",
  greenLight: "#5BE7A9",
  warn: "#F59E0B",
  ok: "#22C55E",
  high: "#F87171",
  danger: "#DC2626",
  pastelBlue: "#E4F1FF",
  pastelGreen: "#DFFBF1",
  white: "#FFFFFF",
};

export const levelColor: Record<string, string> = {
  LOW: colors.ok,
  NORMAL: colors.ok,
  MODERATE: colors.warn,
  HIGH: colors.high,
  CRITICAL: colors.danger,
};

export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 };

export const radius = { sm: 8, md: 12, lg: 16, xl: 20, pill: 999 };

export const font = {
  regular: "System",
  bold: "System",
};

export const shadow = {
  card: {
    shadowColor: colors.ink,
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
};

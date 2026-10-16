/**
 * Shared design tokens for the VOXAGENT Companion shell.
 * Dark-on-light palette, kept intentionally small — this is a functional
 * shell, not a design system.
 */

export const colors = {
  background: "#F5F6F8",
  surface: "#FFFFFF",
  border: "#E1E4E8",
  textPrimary: "#111318",
  textSecondary: "#5B6270",
  textMuted: "#8A909C",
  accent: "#3155F5",
  accentSoft: "#E7EBFF",
  danger: "#D64545",
  dangerSoft: "#FCEEEE",
  success: "#1F9254",
  successSoft: "#EAF7F0",
  warning: "#B5790A",
  warningSoft: "#FFF7E5",
  chipBackground: "#EEF0F4",
  chipBackgroundActive: "#111318",
  chipTextActive: "#FFFFFF",
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
};

export const radius = {
  sm: 8,
  md: 12,
  lg: 20,
  pill: 999,
  circle: 9999,
};

export const typography = {
  title: { fontSize: 24, fontWeight: "700" as const },
  heading: { fontSize: 18, fontWeight: "600" as const },
  body: { fontSize: 15, fontWeight: "400" as const },
  caption: { fontSize: 12, fontWeight: "500" as const },
};

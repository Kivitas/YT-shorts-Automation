// Mirrors src/autoshorts/core/theme.py. Keep these values in sync with the Python tokens.

export const BG_DEEP = "#0E1226"; // (14, 18, 38)
export const BG_SURFACE = "#1C223E"; // (28, 34, 62)

export const ACCENT_YELLOW = "#FFC83C"; // (255, 200, 60)
export const ACCENT_TEAL = "#3CC8C8"; // (60, 200, 200)
export const ACCENT_CORAL = "#FF6E64"; // (255, 110, 100)
export const ACCENT_PURPLE = "#966EE6"; // (150, 110, 230)
export const ACCENT_GREEN = "#78D28C"; // (120, 210, 140)

export const TEXT_PRIMARY = "#F5F7FF"; // (245, 247, 255)
export const TEXT_MUTED = "#A0AAC8"; // (160, 170, 200)

export const FONT_DISPLAY = "Montserrat, Arial, sans-serif";
export const FONT_BODY = "Arial, sans-serif";

// Motion timing in seconds, shared with MOTION_FAST / MOTION_BASE / MOTION_SLOW.
export const MOTION_FAST = 0.18;
export const MOTION_BASE = 0.35;
export const MOTION_SLOW = 0.6;

export const easeInOutCubic = (t: number): number => {
  const c = Math.min(1, Math.max(0, t));
  return c < 0.5 ? 4 * c * c * c : 1 - Math.pow(-2 * c + 2, 3) / 2;
};

export const easeOutCubic = (t: number): number => {
  const c = Math.min(1, Math.max(0, t));
  return 1 - Math.pow(1 - c, 3);
};

// Eased 0..1 progress for an element that starts at `startSec` and lasts `durationSec`.
export const easedProgress = (
  timeSec: number,
  startSec: number,
  durationSec: number,
  ease: (t: number) => number = easeOutCubic,
): number => ease((timeSec - startSec) / Math.max(durationSec, 1e-6));

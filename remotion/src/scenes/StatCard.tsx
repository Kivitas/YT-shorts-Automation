import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { BG_SURFACE, FONT_DISPLAY, TEXT_PRIMARY, ACCENT_YELLOW, easeOutCubic } from "../theme";

export type StatCardProps = {
  value: string;
  label: string;
  accent?: string;
};

// Flat stat card: rounded surface, large number counts into place with an eased rise.
export const StatCard: React.FC<StatCardProps> = ({ value, label, accent = ACCENT_YELLOW }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 200 } });
  const fade = interpolate(frame, [durationInFrames - Math.round(0.3 * fps), durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: easeOutCubic,
  });
  const scale = interpolate(enter, [0, 1], [0.94, 1]);

  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
      <div
        style={{
          minWidth: 640,
          padding: "56px 64px",
          borderRadius: 32,
          backgroundColor: BG_SURFACE,
          textAlign: "center",
          fontFamily: FONT_DISPLAY,
          opacity: enter * fade,
          transform: `scale(${scale})`,
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 150, lineHeight: 1, color: accent }}>{value}</div>
        <div style={{ marginTop: 24, fontWeight: 500, fontSize: 42, color: TEXT_PRIMARY }}>{label}</div>
      </div>
    </AbsoluteFill>
  );
};

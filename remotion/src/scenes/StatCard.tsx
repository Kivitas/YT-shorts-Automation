import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { BG_SURFACE, FONT_DISPLAY, TEXT_PRIMARY, ACCENT_YELLOW } from "../theme";
import { useGsapTimeline } from "../motion/gsapFrame";

export type StatCardProps = {
  value: string;
  label: string;
  accent?: string;
};

type StatState = { opacity: number; scale: number };

// Flat stat card: rounded surface that eases in with a small scale rise, then fades out at the end.
export const StatCard: React.FC<StatCardProps> = ({ value, label, accent = ACCENT_YELLOW }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const durationSec = durationInFrames / fps;
  const exitSec = 0.3;

  const { state, frameState } = useGsapTimeline<StatState>(
    () => ({ opacity: 0, scale: 0.94 }),
    (tl, s) => {
      tl.to(s, { opacity: 1, scale: 1, duration: 0.5, ease: "power3.out" }, 0);
      tl.to(s, { opacity: 0, duration: exitSec, ease: "power3.out" }, durationSec - exitSec);
    },
    durationSec,
    [durationSec],
  );
  frameState(frame, fps);

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
          opacity: state.opacity,
          transform: `scale(${state.scale})`,
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 150, lineHeight: 1, color: accent }}>{value}</div>
        <div style={{ marginTop: 24, fontWeight: 500, fontSize: 42, color: TEXT_PRIMARY }}>{label}</div>
      </div>
    </AbsoluteFill>
  );
};

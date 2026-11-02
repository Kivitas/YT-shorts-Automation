import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { BG_SURFACE, FONT_DISPLAY, TEXT_MUTED, TEXT_PRIMARY, ACCENT_TEAL, easeInOutCubic } from "../theme";

export type LowerThirdProps = {
  name: string;
  role?: string;
  accent?: string;
};

// Flat lower third: semi-transparent surface pill that slides in from the left and eases out.
export const LowerThird: React.FC<LowerThirdProps> = ({ name, role, accent = ACCENT_TEAL }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 200 } });
  const exitStart = durationInFrames - Math.round(0.4 * fps);
  const exit = interpolate(frame, [exitStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: easeInOutCubic,
  });
  const x = interpolate(enter, [0, 1], [-80, 0]);

  return (
    <div
      style={{
        position: "absolute",
        left: 60,
        bottom: Math.round(1920 * 0.26),
        display: "flex",
        alignItems: "center",
        gap: 20,
        padding: "22px 32px",
        borderRadius: 999,
        backgroundColor: BG_SURFACE,
        opacity: enter * exit,
        transform: `translateX(${x}px)`,
        fontFamily: FONT_DISPLAY,
      }}
    >
      <div style={{ width: 8, alignSelf: "stretch", borderRadius: 4, backgroundColor: accent }} />
      <div>
        <div style={{ fontWeight: 600, fontSize: 44, color: TEXT_PRIMARY }}>{name}</div>
        {role ? <div style={{ fontWeight: 500, fontSize: 30, color: TEXT_MUTED }}>{role}</div> : null}
      </div>
    </div>
  );
};

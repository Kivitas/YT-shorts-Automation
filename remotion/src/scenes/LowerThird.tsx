import { useCurrentFrame, useVideoConfig } from "remotion";
import { BG_SURFACE, FONT_DISPLAY, TEXT_MUTED, TEXT_PRIMARY, ACCENT_TEAL } from "../theme";
import { useGsapTimeline } from "../motion/gsapFrame";

export type LowerThirdProps = {
  name: string;
  role?: string;
  accent?: string;
};

type LowerThirdState = { x: number; opacity: number };

// Flat lower third: semi-transparent surface pill that slides in from the left and fades out at the end.
export const LowerThird: React.FC<LowerThirdProps> = ({ name, role, accent = ACCENT_TEAL }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const durationSec = durationInFrames / fps;
  const exitSec = 0.4;

  const { state, frameState } = useGsapTimeline<LowerThirdState>(
    () => ({ x: -80, opacity: 0 }),
    (tl, s) => {
      tl.to(s, { x: 0, opacity: 1, duration: 0.5, ease: "power3.out" }, 0);
      tl.to(s, { opacity: 0, duration: exitSec, ease: "power2.inOut" }, durationSec - exitSec);
    },
    durationSec,
    [durationSec],
  );
  frameState(frame, fps);

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
        opacity: state.opacity,
        transform: `translateX(${state.x}px)`,
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

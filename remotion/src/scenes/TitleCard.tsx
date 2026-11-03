import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { BG_DEEP, FONT_DISPLAY, TEXT_PRIMARY, ACCENT_YELLOW, MOTION_BASE, MOTION_FAST } from "../theme";
import { useGsapTimeline } from "../motion/gsapFrame";

export type TitleCardProps = {
  title: string;
  subtitle?: string;
  accent?: string;
};

type TitleState = { barOpacity: number; titleOpacity: number; titleY: number; subOpacity: number };

// Flat title card: solid background, one accent bar, GSAP-driven entrance and exit.
export const TitleCard: React.FC<TitleCardProps> = ({ title, subtitle, accent = ACCENT_YELLOW }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const durationSec = durationInFrames / fps;

  const { state, frameState } = useGsapTimeline<TitleState>(
    () => ({ barOpacity: 0, titleOpacity: 0, titleY: 40, subOpacity: 0 }),
    (tl, s) => {
      tl.to(s, { barOpacity: 1, duration: MOTION_BASE * 2, ease: "power3.out" }, 0);
      tl.to(s, { titleOpacity: 1, titleY: 0, duration: MOTION_BASE * 2, ease: "power3.out" }, 0);
      tl.to(s, { subOpacity: 1, duration: MOTION_BASE, ease: "power3.out" }, 0.2);
      // Exit: everything fades out over the last fraction of a second.
      tl.to(
        s,
        { barOpacity: 0, titleOpacity: 0, subOpacity: 0, duration: MOTION_FAST * 2, ease: "power2.in" },
        durationSec - MOTION_FAST * 2,
      );
    },
    durationSec,
    [durationSec],
  );
  frameState(frame, fps);

  return (
    <AbsoluteFill style={{ backgroundColor: BG_DEEP, justifyContent: "center", padding: "0 80px" }}>
      <div
        style={{
          width: 120,
          height: 8,
          backgroundColor: accent,
          borderRadius: 4,
          opacity: state.barOpacity,
        }}
      />
      <div
        style={{
          marginTop: 36,
          fontFamily: FONT_DISPLAY,
          fontWeight: 600,
          fontSize: 96,
          lineHeight: 1.15,
          color: TEXT_PRIMARY,
          opacity: state.titleOpacity,
          transform: `translateY(${state.titleY}px)`,
        }}
      >
        {title}
      </div>
      {subtitle ? (
        <div
          style={{
            marginTop: 28,
            fontFamily: FONT_DISPLAY,
            fontWeight: 500,
            fontSize: 44,
            color: TEXT_PRIMARY,
            opacity: state.subOpacity,
          }}
        >
          {subtitle}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

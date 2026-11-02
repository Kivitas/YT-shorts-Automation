import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { BG_DEEP, FONT_DISPLAY, TEXT_PRIMARY, ACCENT_YELLOW, MOTION_BASE, easeOutCubic } from "../theme";

export type TitleCardProps = {
  title: string;
  subtitle?: string;
  accent?: string;
};

// Flat title card: solid background, one accent bar, eased fade and rise.
export const TitleCard: React.FC<TitleCardProps> = ({ title, subtitle, accent = ACCENT_YELLOW }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 200 }, durationInFrames: Math.round(MOTION_BASE * fps * 2) });
  const sub = interpolate(frame, [Math.round(0.2 * fps), Math.round(0.2 * fps + MOTION_BASE * fps)], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: easeOutCubic,
  });
  const rise = interpolate(enter, [0, 1], [40, 0]);

  return (
    <AbsoluteFill style={{ backgroundColor: BG_DEEP, justifyContent: "center", padding: "0 80px" }}>
      <div style={{ width: 120, height: 8, backgroundColor: accent, borderRadius: 4, opacity: enter }} />
      <div
        style={{
          marginTop: 36,
          fontFamily: FONT_DISPLAY,
          fontWeight: 600,
          fontSize: 96,
          lineHeight: 1.15,
          color: TEXT_PRIMARY,
          opacity: enter,
          transform: `translateY(${rise}px)`,
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
            opacity: sub,
          }}
        >
          {subtitle}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

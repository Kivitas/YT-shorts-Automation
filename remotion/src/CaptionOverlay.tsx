import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { activeWordIndex } from "./captionTiming";
import { ACCENT_YELLOW, BG_DEEP, FONT_DISPLAY, TEXT_PRIMARY } from "./theme";

export type CaptionWord = {
  text: string;
  start: number;
  end: number;
};

export type CaptionOverlayProps = {
  words: CaptionWord[];
  durationSec: number;
  fps: number;
  wordsPerPage: number;
  activeColor: string;
  primaryColor: string;
  fontSize: number;
  bottomPercent: number;
};

export const defaultCaptionProps: CaptionOverlayProps = {
  words: [],
  durationSec: 1,
  fps: 30,
  wordsPerPage: 2,
  activeColor: ACCENT_YELLOW,
  primaryColor: TEXT_PRIMARY,
  fontSize: 60,
  bottomPercent: 38,
};

// Text disappears this long after its last word ends, so the page does not flicker between words.
const PAGE_TAIL_SEC = 0.15;

const paginate = (words: CaptionWord[], wordsPerPage: number): CaptionWord[][] => {
  const pages: CaptionWord[][] = [];
  for (let i = 0; i < words.length; i += wordsPerPage) {
    pages.push(words.slice(i, i + wordsPerPage));
  }
  return pages;
};

export const CaptionOverlay: React.FC<CaptionOverlayProps> = ({
  words,
  fps,
  wordsPerPage,
  activeColor,
  primaryColor,
  fontSize,
  bottomPercent,
}) => {
  const frame = useCurrentFrame();
  const { fps: configFps, width, height } = useVideoConfig();
  const t = frame / (fps || configFps);

  const page = paginate(words, Math.max(1, wordsPerPage)).find((p) => {
    const first = p[0];
    const last = p[p.length - 1];
    return t >= first.start && t < last.end + PAGE_TAIL_SEC;
  });

  // Transparent background: the overlay is composited onto the finished Short by FFmpeg.
  if (!page) {
    return <AbsoluteFill style={{ backgroundColor: "transparent" }} />;
  }

  const activeIndex = activeWordIndex(page, t);
  const activeWord = page[activeIndex];
  // Soft eased pop: a gentle spring with no overshoot, so the pill settles rather than bounces.
  const pop = spring({
    frame: frame - Math.round(activeWord.start * configFps),
    fps: configFps,
    config: { damping: 200, stiffness: 120 },
  });
  const scale = interpolate(pop, [0, 1], [1.06, 1]);

  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: `${bottomPercent}%`,
          width,
          textAlign: "center",
          fontFamily: FONT_DISPLAY,
          fontWeight: 500,
          fontSize,
          lineHeight: 1.25,
          padding: `0 ${Math.round(width * 0.06)}px`,
          boxSizing: "border-box",
          height: Math.round(height * 0.2),
        }}
      >
        {page.map((w, i) => {
          const isActive = i === activeIndex;
          return (
            <span
              key={`${w.start}-${i}`}
              style={{
                color: isActive ? BG_DEEP : primaryColor,
                display: "inline-block",
                marginRight: "0.3em",
                transform: isActive ? `scale(${scale})` : "none",
                backgroundColor: isActive ? activeColor : "transparent",
                borderRadius: "999px",
                padding: "0.1em 0.4em",
              }}
            >
              {w.text}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

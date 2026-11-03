import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { activeWordIndex } from "./captionTiming";
import { useGsapTimeline } from "./motion/gsapFrame";
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

// Each word pops from a slightly larger scale back to 1 when it starts being spoken.
const POP_FROM_SCALE = 1.06;
const POP_SEC = 0.35;

type PopState = { pops: { scale: number }[] };

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
  const { fps: configFps, width, height, durationInFrames } = useVideoConfig();
  const t = frame / (fps || configFps);
  const durationSec = durationInFrames / configFps;

  // One GSAP tween per word, placed at that word's start time. Word i's scale is read while it is active.
  const { state: pop, frameState } = useGsapTimeline<PopState>(
    () => ({ pops: words.map(() => ({ scale: POP_FROM_SCALE })) }),
    (tl, s) => {
      words.forEach((w, i) => {
        tl.fromTo(s.pops[i], { scale: POP_FROM_SCALE }, { scale: 1, duration: POP_SEC, ease: "power2.out" }, w.start);
      });
    },
    durationSec,
    [words, durationSec],
  );
  frameState(frame, configFps);

  const pages = paginate(words, Math.max(1, wordsPerPage));
  const pageIndex = pages.findIndex((p) => {
    const first = p[0];
    const last = p[p.length - 1];
    return t >= first.start && t < last.end + PAGE_TAIL_SEC;
  });
  const page = pageIndex >= 0 ? pages[pageIndex] : undefined;

  // Transparent background: the overlay is composited onto the finished Short by FFmpeg.
  if (!page) {
    return <AbsoluteFill style={{ backgroundColor: "transparent" }} />;
  }

  const activeIndex = activeWordIndex(page, t);
  const globalIndex = pageIndex * Math.max(1, wordsPerPage) + activeIndex;
  const scale = pop.pops[globalIndex]?.scale ?? 1;

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

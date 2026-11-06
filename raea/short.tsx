// AutoShorts 9:16 short composition for raea (https://github.com/shinyobjectz/raea).
//
// Rendered by src/autoshorts/media/raea_renderer.py, which prepends
//     const __SHORTS_PROPS__ = { ... };
// to a copy of this file (raea documents no props flag, so the data is baked into the file).
// Run standalone (`raea render raea/short.tsx --frame 30 -o frame.png`) the placeholder
// DEFAULT_PROPS below are used, which is handy for previewing the layout without a job.
//
// Every layout is computed in Python (panel boxes and crop placement in canvas pixels), so
// this file only draws what the props describe.
import {
  AbsoluteFill,
  Audio,
  Composition,
  Sequence,
  Video,
  interpolate,
  registerRoot,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "raea";

// __SHORTS_PROPS__ is NOT declared here: the job file prepends `const __SHORTS_PROPS__ = {...}`
// and raea's bundler rejects a second declaration (`declare const` counts as one).
type Box = { x: number; y: number; w: number; h: number };
type Placement = { left: number; top: number; width: number; height: number };
type Panel = {
  media: string;
  box: Box;
  video: Placement;
  blur: number;
  dim: number;
  mirror: boolean;
  radius: number;
  border: string | null;
  audio: boolean;
};
type MediaInfo = { path: string; static: string; width: number; height: number; hasAudio: boolean; durationSec: number };
type Segment = { start: number; end: number; outStart: number };
type CaptionWord = { text: string; start: number; end: number };
type Captions = {
  words: CaptionWord[];
  wordsPerPage: number;
  fontSize: number;
  uppercase: boolean;
  activeColor: string;
  primaryColor: string;
  stroke: number;
  glow: string | null;
  pill: string | null;
  bottomPercent: number;
};
type Chyron = {
  badge: string;
  text: string;
  badgeBg: string;
  badgeFg: string;
  bannerBg: string;
  bannerFg: string;
  accent: string;
  startSec: number;
  durationSec: number | null;
  position: string;
};

export type ShortProps = {
  width: number;
  height: number;
  fps: number;
  durationSec: number;
  durationInFrames: number;
  layout: string;
  mediaMode: "static" | "path";
  media: Record<string, MediaInfo>;
  segments: Segment[];
  panels: Panel[];
  seamY: number | null;
  captions: Captions | null;
  hook: { text: string; durationSec: number } | null;
  progressBar: { color: string; height: number; y: number } | null;
  chyron: Chyron | null;
  watermark: { text: string; position: string; opacity: number } | null;
};

const FONT = "Montserrat, Arial, sans-serif";

const DEFAULT_PROPS: ShortProps = {
  width: 1080,
  height: 1920,
  fps: 30,
  durationSec: 6,
  durationInFrames: 180,
  layout: "split_50_50",
  mediaMode: "static",
  media: {},
  segments: [{ start: 0, end: 6, outStart: 0 }],
  panels: [
    { media: "source", box: { x: 0, y: 0, w: 1080, h: 960 }, video: { left: 0, top: 0, width: 1080, height: 960 }, blur: 0, dim: 0, mirror: false, radius: 0, border: null, audio: true },
    { media: "background", box: { x: 0, y: 960, w: 1080, h: 960 }, video: { left: 0, top: 0, width: 1080, height: 960 }, blur: 0, dim: 0, mirror: false, radius: 0, border: null, audio: false },
  ],
  seamY: 960,
  captions: {
    words: [
      { text: "Preview", start: 0.2, end: 0.8 },
      { text: "caption", start: 0.8, end: 1.4 },
      { text: "words", start: 1.4, end: 2.0 },
    ],
    wordsPerPage: 3,
    fontSize: 76,
    uppercase: true,
    activeColor: "#FFE600",
    primaryColor: "#FFFFFF",
    stroke: 6,
    glow: null,
    pill: null,
    bottomPercent: 38,
  },
  hook: { text: "THIS IS THE HOOK", durationSec: 3 },
  progressBar: { color: "#FFE600", height: 12, y: 954 },
  chyron: null,
  watermark: { text: "@channel", position: "top_right", opacity: 0.65 },
};

// @ts-ignore  (injected by the backend; undeclared when the template is previewed on its own)
const P: ShortProps = typeof __SHORTS_PROPS__ !== "undefined" && __SHORTS_PROPS__ ? __SHORTS_PROPS__ : DEFAULT_PROPS;

const src = (key: string): string | null => {
  const m = P.media[key];
  if (!m) return null;
  return P.mediaMode === "path" ? m.path : staticFile(m.static);
};

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

// Text outline built from shadows (a ring of offsets) so it does not rely on -webkit-text-stroke.
const outline = (w: number, color = "#000"): string => {
  if (w <= 0) return "none";
  const out: string[] = [];
  for (let i = 0; i < 12; i++) {
    const a = (i / 12) * Math.PI * 2;
    out.push(`${(Math.cos(a) * w).toFixed(1)}px ${(Math.sin(a) * w).toFixed(1)}px 0 ${color}`);
  }
  return out.join(", ");
};

// ---- video panels ---------------------------------------------------------------------

const PanelVideo = ({ panel }: { panel: Panel }) => {
  const { fps } = useVideoConfig();
  const url = src(panel.media);
  if (!url) return null;
  const v = panel.video;
  const style = {
    position: "absolute" as const,
    left: v.left,
    top: v.top,
    width: v.width,
    height: v.height,
    objectFit: "fill" as const,
    transform: panel.mirror ? "scaleX(-1)" : undefined,
    filter: panel.blur > 0 ? `blur(${panel.blur}px)` : undefined,
  };
  const isSource = panel.media === "source" || panel.media === "cam";
  if (!isSource) {
    // Background/gameplay footage: loops for the whole short and never plays sound.
    const dur = P.media[panel.media]?.durationSec ?? 0;
    const endAt = dur > 1 ? Math.floor(dur * fps) : undefined;
    return <Video src={url} muted loop={endAt !== undefined} endAt={endAt} style={style} />;
  }
  return (
    <>
      {P.segments.map((s, i) => (
        <Sequence key={i} from={Math.round(s.outStart * fps)} durationInFrames={Math.max(1, Math.round((s.end - s.start) * fps))}>
          <Video src={url} startFrom={Math.round(s.start * fps)} muted={!panel.audio} style={style} />
        </Sequence>
      ))}
    </>
  );
};

const PanelView = ({ panel }: { panel: Panel }) => (
  <div
    style={{
      position: "absolute",
      left: panel.box.x,
      top: panel.box.y,
      width: panel.box.w,
      height: panel.box.h,
      overflow: "hidden",
      borderRadius: panel.radius,
      border: panel.border ? `6px solid ${panel.border}` : undefined,
    }}
  >
    <PanelVideo panel={panel} />
    {panel.dim > 0 ? <div style={{ position: "absolute", left: 0, top: 0, width: "100%", height: "100%", background: `rgba(0,0,0,${panel.dim})` }} /> : null}
  </div>
);

// ---- captions -------------------------------------------------------------------------

const CaptionLayer = ({ c }: { c: Captions }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const words = c.words;
  let idx = -1;
  for (let i = 0; i < words.length; i++) {
    if (words[i].start <= t) idx = i;
    else break;
  }
  if (idx < 0) return null;
  const page = Math.floor(idx / Math.max(1, c.wordsPerPage));
  const first = page * c.wordsPerPage;
  const group = words.slice(first, first + c.wordsPerPage);
  if (t > group[group.length - 1].end + 0.35) return null;
  const active = idx - first;
  const pop = interpolate(t - words[idx].start, [0, 0.08, 0.18], [0.85, 1.12, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const textShadow = [outline(c.stroke), c.glow ? `0 0 24px ${c.glow}, 0 0 48px ${c.glow}` : ""].filter(Boolean).join(", ");
  return (
    <div
      style={{
        position: "absolute",
        left: 60,
        right: 60,
        bottom: `${c.bottomPercent}%`,
        display: "flex",
        flexDirection: "row",
        justifyContent: "center",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 18,
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "row",
          flexWrap: "wrap",
          justifyContent: "center",
          gap: 18,
          padding: c.pill ? "14px 30px" : 0,
          background: c.pill ?? undefined,
          borderRadius: c.pill ? 36 : 0,
        }}
      >
        {group.map((w, i) => (
          <span
            key={first + i}
            style={{
              fontFamily: FONT,
              fontWeight: 900,
              fontSize: c.fontSize,
              lineHeight: 1.1,
              color: i === active ? c.activeColor : c.primaryColor,
              textShadow,
              transform: i === active ? `scale(${pop})` : undefined,
            }}
          >
            {c.uppercase ? w.text.toUpperCase() : w.text}
          </span>
        ))}
      </div>
    </div>
  );
};

// ---- hook, progress bar, chyron, watermark ---------------------------------------------

const HookCard = ({ text, durationSec }: { text: string; durationSec: number }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  if (t > durationSec) return null;
  const inP = clamp01(t / 0.25);
  const outP = clamp01((durationSec - t) / 0.25);
  const opacity = Math.min(inP, outP);
  const scale = interpolate(inP, [0, 1], [0.8, 1]);
  return (
    <div style={{ position: "absolute", left: 60, right: 60, top: 230, display: "flex", justifyContent: "center", opacity, transform: `scale(${scale})` }}>
      <div style={{ background: "rgba(10,10,20,0.82)", border: "6px solid #FFE600", borderRadius: 28, padding: "28px 40px" }}>
        <span style={{ fontFamily: FONT, fontWeight: 900, fontSize: 72, lineHeight: 1.12, color: "#FFFFFF", textAlign: "center", textShadow: outline(5) }}>{text.toUpperCase()}</span>
      </div>
    </div>
  );
};

const ProgressBar = ({ bar }: { bar: NonNullable<ShortProps["progressBar"]> }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const w = P.width * clamp01(frame / Math.max(1, durationInFrames - 1));
  return <div style={{ position: "absolute", left: 0, top: bar.y, width: w, height: bar.height, background: bar.color }} />;
};

const ChyronLayer = ({ c }: { c: Chyron }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps - c.startSec;
  const dur = c.durationSec ?? P.durationSec - c.startSec;
  if (t < 0 || t > dur) return null;
  const opacity = Math.min(clamp01(t / 0.3), clamp01((dur - t) / 0.3));
  const bannerH = 72;
  const full = P.layout === "full_center_crop" || P.layout === "full_blur_bg" || P.layout === "pip_gameplay";
  const bottom =
    c.position === "top" ? 220 + bannerH + 40 : c.position === "lower_third" || P.seamY === null || full ? 1450 + bannerH : P.seamY - 14;
  const top = c.position === "top" ? 220 : bottom - bannerH;
  return (
    <div style={{ position: "absolute", left: 40, right: 40, top: top - 44, opacity }}>
      <div style={{ display: "flex", flexDirection: "row" }}>
        <div style={{ background: c.badgeBg, color: c.badgeFg, fontFamily: FONT, fontWeight: 900, fontSize: 30, padding: "6px 22px" }}>{c.badge}</div>
      </div>
      <div style={{ display: "flex", flexDirection: "row", alignItems: "center", background: c.bannerBg, borderLeft: `12px solid ${c.accent}`, height: bannerH, padding: "0 24px" }}>
        <span style={{ color: c.bannerFg, fontFamily: FONT, fontWeight: 800, fontSize: 36 }}>{c.text}</span>
      </div>
    </div>
  );
};

const Watermark = ({ w }: { w: NonNullable<ShortProps["watermark"]> }) => {
  const left = w.position.endsWith("left");
  const bottom = w.position.startsWith("bottom");
  const center = w.position.endsWith("center");
  return (
    <div
      style={{
        position: "absolute",
        top: bottom ? undefined : 70,
        bottom: bottom ? 110 : undefined,
        left: left ? 50 : center ? 0 : undefined,
        right: !left && !center ? 50 : center ? 0 : undefined,
        display: "flex",
        justifyContent: center ? "center" : left ? "flex-start" : "flex-end",
        opacity: w.opacity,
      }}
    >
      <span style={{ fontFamily: FONT, fontWeight: 800, fontSize: 38, color: "#FFFFFF", textShadow: outline(3) }}>{w.text}</span>
    </div>
  );
};

// ---- composition ----------------------------------------------------------------------

export const Short = () => (
  <AbsoluteFill style={{ background: "#000" }}>
    {P.panels.map((panel, i) => (
      <PanelView key={i} panel={panel} />
    ))}
    {P.progressBar ? <ProgressBar bar={P.progressBar} /> : null}
    {P.chyron ? <ChyronLayer c={P.chyron} /> : null}
    {P.captions ? <CaptionLayer c={P.captions} /> : null}
    {P.hook ? <HookCard text={P.hook.text} durationSec={P.hook.durationSec} /> : null}
    {P.watermark ? <Watermark w={P.watermark} /> : null}
  </AbsoluteFill>
);

const Root = () => <Composition id="short" component={Short} width={P.width} height={P.height} fps={P.fps} durationInFrames={P.durationInFrames} />;

registerRoot(Root);

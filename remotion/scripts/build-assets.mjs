// Builds longform sprites (characters, props, backgrounds) from satori-compatible SVG.
// Usage: cd remotion && npm run build:assets
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import React from "react";
import satori from "satori";
import { Resvg } from "@resvg/resvg-js";

const h = React.createElement;
// Element factory: flattens child arrays and drops empty children so no React keys are needed.
const E = (type, props, ...kids) =>
  h(type, props ?? {}, ...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const REMOTION_DIR = path.resolve(SCRIPT_DIR, "..");
const REPO_ROOT = path.resolve(REMOTION_DIR, "..");
const SPRITE_DIR = path.resolve(REPO_ROOT, "assets", "longform", "sprites");
const SRC_DIR = path.resolve(REMOTION_DIR, "assets-src");

const FONT_CANDIDATES = [
  "C:\\Windows\\Fonts\\arialbd.ttf",
  "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
];

const INK = "#10101A";
const CREAM = "#EAE6DF";
const SHADE = "#B8B4AC";
// Character palette matched to the reference figure: peach skin, red shirt, navy trousers.
const SKIN = "#F2C79E";
const SKIN_SHADE = "#D9A272";
const RED = "#E5483D";
const RED_SHADE = "#B53329";
const NAVY = "#2C3E75";
const NAVY_SHADE = "#1B2750";

// ---- SVG primitives -------------------------------------------------------
const P = (d, props = {}) => E("path", { d, ...props });
const OUTLINE = { stroke: INK, strokeWidth: 6, strokeLinejoin: "round" };
const STROKE_LINE = { fill: "none", stroke: INK, strokeWidth: 6, strokeLinecap: "round" };
const lin = (id, stops, x1 = 0, y1 = 0, x2 = 0, y2 = 1) =>
  E("linearGradient", { id, x1, y1, x2, y2 }, ...stops.map(([o, c, a]) => E("stop", { offset: o, stopColor: c, stopOpacity: a ?? 1 })));
const rad = (id, stops, cx = 0.5, cy = 0.5, r = 0.5) =>
  E("radialGradient", { id, cx, cy, r }, ...stops.map(([o, c, a]) => E("stop", { offset: o, stopColor: c, stopOpacity: a ?? 1 })));
const svgRoot = (width, height, defs, nodes) =>
  E("svg", { width, height, viewBox: `0 0 ${width} ${height}` }, E("defs", {}, ...defs), ...nodes);

// Limb as a round-capped stroke: ink outline, body colour, shade band on the right side.
function pill(x1, y1, x2, y2, w, body = CREAM, shade = SHADE) {
  const d = `M${x1} ${y1} L${x2} ${y2}`;
  const ds = `M${x1 + w * 0.32} ${y1} L${x2 + w * 0.32} ${y2}`;
  return [
    P(d, { stroke: INK, strokeWidth: w + 8, strokeLinecap: "round", fill: "none" }),
    P(d, { stroke: body, strokeWidth: w, strokeLinecap: "round", fill: "none" }),
    P(ds, { stroke: shade, strokeWidth: w * 0.35, strokeLinecap: "round", fill: "none" }),
  ];
}

// ---- Character parts (400x800 canvas, figure centred on x=200) -----------
const CHAR_DEFS = [
  rad("headGrad", [[0, "#FBE3C6"], [0.55, SKIN], [1, SKIN_SHADE]], 0.36, 0.32, 0.7),
  lin("torsoGrad", [[0, RED], [0.6, RED], [1, RED_SHADE]], 0, 0, 1, 0),
];

const partHead = () => [E("circle", { cx: 200, cy: 150, r: 72, fill: "url(#headGrad)", ...OUTLINE })];

const partEyes = () =>
  [170, 230].flatMap((cx) => [
    E("ellipse", { cx, cy: 146, rx: 15, ry: 19, fill: "#FFFFFF", ...OUTLINE }),
    E("circle", { cx, cy: 150, r: 8, fill: INK }),
    E("circle", { cx: cx + 3, cy: 144, r: 3, fill: "#FFFFFF" }),
  ]);

const partEyebrows = () => [
  P("M150 114 Q168 102 186 112", STROKE_LINE),
  P("M214 112 Q232 102 250 114", STROKE_LINE),
];

const partMouth = () => [P("M176 184 Q200 206 224 184", STROKE_LINE)];

const partTorso = () => [
  P("M132 240 Q132 222 152 222 L248 222 Q268 222 268 240 L274 430 Q276 472 240 480 L160 480 Q124 472 126 430 Z", {
    fill: "url(#torsoGrad)",
    ...OUTLINE,
  }),
];

const partUpperArm = () => [
  ...pill(138, 250, 112, 350, 44, RED, RED_SHADE),
  ...pill(262, 250, 288, 350, 44, RED, RED_SHADE),
];
const partForearm = () => [
  ...pill(112, 350, 118, 448, 40, RED, RED_SHADE),
  ...pill(288, 350, 282, 448, 40, RED, RED_SHADE),
];
const partHand = () => [
  E("circle", { cx: 118, cy: 472, r: 24, fill: SKIN, ...OUTLINE }),
  E("circle", { cx: 282, cy: 472, r: 24, fill: SKIN, ...OUTLINE }),
];
const partUpperLeg = () => [
  ...pill(180, 470, 176, 600, 58, NAVY, NAVY_SHADE),
  ...pill(220, 470, 224, 600, 58, NAVY, NAVY_SHADE),
];
const partLowerLeg = () => [
  ...pill(176, 600, 172, 720, 48, NAVY, NAVY_SHADE),
  ...pill(224, 600, 228, 720, 48, NAVY, NAVY_SHADE),
];
const partShoe = () => [
  E("ellipse", { cx: 158, cy: 744, rx: 38, ry: 18, fill: NAVY_SHADE, ...OUTLINE }),
  E("ellipse", { cx: 242, cy: 744, rx: 38, ry: 18, fill: NAVY_SHADE, ...OUTLINE }),
];

// Full figure: back-to-front draw order so limbs tuck under torso and head.
const figureNodes = () => [
  partUpperLeg(),
  partLowerLeg(),
  partShoe(),
  partUpperArm(),
  partForearm(),
  partTorso(),
  partHand(),
  partHead(),
  partEyes(),
  partEyebrows(),
  partMouth(),
];

const charRoot = (nodes) => svgRoot(400, 800, CHAR_DEFS, nodes);

// ---- Props ----------------------------------------------------------------
const SEVEN_SEG = {
  0: "abcdef", 1: "bc", 2: "abdeg", 3: "abcdg", 4: "bcfg",
  5: "acdfg", 6: "acdefg", 7: "abc", 8: "abcdefg", 9: "abcdfg",
};
function sevenSegDigit(digit, x, y) {
  const w = 30, hgt = 80, t = 8, half = hgt / 2;
  const seg = {
    a: [x + 4, y, w - 8, t],
    g: [x + 4, y + half - t / 2, w - 8, t],
    d: [x + 4, y + hgt - t, w - 8, t],
    f: [x, y + 4, t, half - 8],
    b: [x + w - t, y + 4, t, half - 8],
    e: [x, y + half + 4, t, half - 8],
    c: [x + w - t, y + half + 4, t, half - 8],
  };
  return SEVEN_SEG[digit].split("").map((s) => {
    const [rx, ry, rw, rh] = seg[s];
    return E("rect", { x: rx, y: ry, width: rw, height: rh, rx: 2, fill: "#5CF0FF" });
  });
}

const propWindow = () => ({
  width: 400,
  height: 400,
  element: svgRoot(400, 400, [lin("winSky", [[0, "#6EC3EA"], [1, "#D6F1FF"]], 0, 0, 0, 1)], [
    E("rect", { x: 20, y: 20, width: 360, height: 360, rx: 14, fill: CREAM, ...OUTLINE }),
    E("rect", { x: 44, y: 44, width: 312, height: 312, rx: 4, fill: "url(#winSky)" }),
    E("ellipse", { cx: 150, cy: 180, rx: 46, ry: 22, fill: "#FFFFFF", opacity: 0.95 }),
    E("ellipse", { cx: 190, cy: 166, rx: 38, ry: 27, fill: "#FFFFFF", opacity: 0.95 }),
    E("ellipse", { cx: 228, cy: 184, rx: 32, ry: 18, fill: "#FFFFFF", opacity: 0.95 }),
    E("rect", { x: 196, y: 44, width: 8, height: 312, fill: CREAM, stroke: INK, strokeWidth: 3 }),
    E("rect", { x: 44, y: 196, width: 312, height: 8, fill: CREAM, stroke: INK, strokeWidth: 3 }),
  ]),
});

const propPhone = () => ({
  width: 240,
  height: 440,
  element: svgRoot(240, 440, [
    rad("phoneGlow", [[0, "#33E0FF", 0.75], [0.5, "#33E0FF", 0.25], [1, "#33E0FF", 0]]),
  ], [
    E("rect", { x: 20, y: 20, width: 200, height: 400, rx: 30, fill: "#1E1E2C", ...OUTLINE }),
    E("rect", { x: 36, y: 40, width: 168, height: 360, rx: 14, fill: "#0E2A3A" }),
    E("circle", { cx: 120, cy: 210, r: 76, fill: "url(#phoneGlow)" }),
    E("rect", { x: 96, y: 28, width: 48, height: 6, rx: 3, fill: INK }),
    E("circle", { cx: 120, cy: 414, r: 8, fill: "#3A3A4C" }),
  ]),
});

const propAlarmClock = () => {
  const digits = [["0", 76], ["7", 116]].concat([["3", 176], ["0", 216]]);
  return {
    width: 320,
    height: 320,
    element: svgRoot(320, 320, [], [
      E("circle", { cx: 90, cy: 58, r: 30, fill: CREAM, ...OUTLINE }),
      E("circle", { cx: 230, cy: 58, r: 30, fill: CREAM, ...OUTLINE }),
      E("rect", { x: 30, y: 60, width: 260, height: 220, rx: 56, fill: CREAM, ...OUTLINE }),
      E("rect", { x: 64, y: 270, width: 36, height: 30, rx: 8, fill: INK }),
      E("rect", { x: 220, y: 270, width: 36, height: 30, rx: 8, fill: INK }),
      E("rect", { x: 58, y: 110, width: 204, height: 110, rx: 14, fill: INK }),
      digits.map(([d, x]) => sevenSegDigit(d, x, 125)),
      E("rect", { x: 160, y: 140, width: 8, height: 8, rx: 2, fill: "#5CF0FF" }),
      E("rect", { x: 160, y: 178, width: 8, height: 8, rx: 2, fill: "#5CF0FF" }),
    ]),
  };
};

const propMountain = () => ({
  width: 480,
  height: 300,
  element: svgRoot(480, 300, [
    lin("mtnBack", [[0, "#7A8AD0"], [1, "#3C4A8A"]]),
    lin("mtnFront", [[0, "#9FB0E8"], [1, "#4E5FA6"]]),
  ], [
    P("M20 296 L190 70 L360 296 Z", { fill: "url(#mtnBack)", ...OUTLINE }),
    P("M190 70 L218 118 L204 110 L190 124 L176 110 L162 118 Z", { fill: "#FFFFFF", stroke: INK, strokeWidth: 4 }),
    P("M130 296 L300 110 L470 296 Z", { fill: "url(#mtnFront)", ...OUTLINE }),
    P("M300 110 L322 146 L310 138 L300 150 L290 138 L278 146 Z", { fill: "#FFFFFF", stroke: INK, strokeWidth: 4 }),
  ]),
});

const propHeavyBag = () => ({
  width: 240,
  height: 420,
  element: svgRoot(240, 420, [lin("bagGrad", [[0, "#9A3A3A"], [1, "#5A1C1C"]], 0, 0, 1, 1)], [
    E("rect", { x: 40, y: 0, width: 160, height: 22, rx: 6, fill: "#6B6B7A", ...OUTLINE }),
    P("M80 22 L80 78 M160 22 L160 78", { fill: "none", stroke: INK, strokeWidth: 12 }),
    P("M80 22 L80 78 M160 22 L160 78", { fill: "none", stroke: "#B8B8C6", strokeWidth: 5 }),
    E("rect", { x: 24, y: 74, width: 192, height: 320, rx: 70, fill: "url(#bagGrad)", ...OUTLINE }),
    P("M120 110 L120 360", { fill: "none", stroke: "#3A1010", strokeWidth: 4, opacity: 0.5 }),
    E("ellipse", { cx: 66, cy: 170, rx: 14, ry: 60, fill: "#FFFFFF", opacity: 0.18 }),
  ]),
});

const bgNavy = () => ({
  width: 1920,
  height: 1080,
  element: svgRoot(1920, 1080, [
    lin("navyBg", [[0, "#2A3868"], [1, "#12183A"]]),
    rad("tealGlow", [[0, "#1FB5B0", 0.35], [1, "#1FB5B0", 0]]),
  ], [
    E("rect", { x: 0, y: 0, width: 1920, height: 1080, fill: "url(#navyBg)" }),
    E("ellipse", { cx: 960, cy: 540, rx: 900, ry: 600, fill: "url(#tealGlow)" }),
  ]),
});

// ---- Extra props and backgrounds (same palette, ink outlines, 2x sizes) ----
const r2 = (n) => Math.round(n * 100) / 100;
// Point on a clock-style circle: angle in degrees clockwise from 12 o'clock.
const polar = (cx, cy, len, deg) => [
  r2(cx + len * Math.sin((deg * Math.PI) / 180)),
  r2(cy - len * Math.cos((deg * Math.PI) / 180)),
];
// Five-point star path, outer radius R, inner radius ri.
function starPath(cx, cy, R, ri) {
  const pts = [];
  for (let i = 0; i < 10; i++) {
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    const rr = i % 2 === 0 ? R : ri;
    pts.push(`${r2(cx + rr * Math.cos(a))} ${r2(cy + rr * Math.sin(a))}`);
  }
  return `M${pts.join(" L")} Z`;
}

const GREEN = "#3FA66B";
const GREEN_DARK = "#2F8A58";
const CORAL = "#FF7A6B";
const TEAL = "#1FB5B0";
const AMBER = "#FFB84D";

const propPlant = () => ({
  width: 300,
  height: 400,
  element: svgRoot(300, 400, [], [
    P("M150 240 Q80 220 70 150 Q140 160 150 240 Z", { fill: GREEN_DARK, ...OUTLINE }),
    P("M150 240 Q220 220 230 150 Q160 160 150 240 Z", { fill: GREEN_DARK, ...OUTLINE }),
    P("M150 240 Q120 150 150 80 Q180 160 150 240 Z", { fill: GREEN, ...OUTLINE }),
    P("M150 232 Q140 160 150 92", { fill: "none", stroke: "#8BE0A8", strokeWidth: 4, strokeLinecap: "round" }),
    E("rect", { x: 80, y: 240, width: 140, height: 28, rx: 8, fill: "#E0785F", ...OUTLINE }),
    P("M90 268 L210 268 L196 372 Q194 386 180 386 L120 386 Q106 386 104 372 Z", { fill: "#D9825F", ...OUTLINE }),
  ]),
});

const propLamp = () => ({
  width: 240,
  height: 520,
  element: svgRoot(240, 520, [rad("lampGlow", [[0, "#FFD27A", 0.6], [1, "#FFD27A", 0]])], [
    E("ellipse", { cx: 120, cy: 210, rx: 120, ry: 110, fill: "url(#lampGlow)" }),
    E("ellipse", { cx: 120, cy: 500, rx: 64, ry: 14, fill: "#3A3A4C", ...OUTLINE }),
    E("rect", { x: 114, y: 190, width: 12, height: 300, fill: "#5A5A6C", ...OUTLINE }),
    P("M70 60 L170 60 L212 190 L28 190 Z", { fill: AMBER, ...OUTLINE }),
    E("ellipse", { cx: 120, cy: 60, rx: 50, ry: 10, fill: "#FFE7A8", ...OUTLINE }),
    P("M92 84 L100 170", { fill: "none", stroke: "#FFF0C2", strokeWidth: 6, strokeLinecap: "round", opacity: 0.8 }),
  ]),
});

const SPINE_COLS = ["#FF7A6B", "#1FB5B0", "#FFB84D", "#4D7CFE", "#EAE6DF", "#3FA66B", "#8E6CEF"];
function spines(y, h, x0, widths, offset) {
  let x = x0;
  return widths.map((w, i) => {
    const el = E("rect", {
      x, y, width: w, height: h, fill: SPINE_COLS[(i + offset) % SPINE_COLS.length], stroke: INK, strokeWidth: 4,
    });
    x += w;
    return el;
  });
}

const propBookshelf = () => ({
  width: 360,
  height: 400,
  element: svgRoot(360, 400, [], [
    E("rect", { x: 20, y: 20, width: 320, height: 360, rx: 8, fill: "#A0643A", ...OUTLINE }),
    E("rect", { x: 40, y: 40, width: 280, height: 320, fill: "#7A4A2A" }),
    spines(60, 90, 48, [26, 20, 32, 24, 18, 28, 22, 30, 20, 26], 0),
    E("rect", { x: 40, y: 150, width: 280, height: 14, rx: 3, fill: "#C98A56", ...OUTLINE }),
    spines(164, 106, 48, [22, 30, 18, 26, 34, 20, 24, 28, 20], 2),
    E("rect", { x: 40, y: 270, width: 280, height: 14, rx: 3, fill: "#C98A56", ...OUTLINE }),
    spines(284, 76, 48, [28, 24, 20, 32, 26, 22], 4),
  ]),
});

const propLaptop = () => ({
  width: 400,
  height: 300,
  element: svgRoot(400, 300, [lin("lapScr", [[0, "#5FE3DC"], [1, TEAL]])], [
    E("rect", { x: 70, y: 20, width: 260, height: 180, rx: 12, fill: "#2E2E3E", ...OUTLINE }),
    E("rect", { x: 88, y: 36, width: 224, height: 148, rx: 4, fill: "url(#lapScr)" }),
    E("rect", { x: 110, y: 60, width: 120, height: 10, rx: 5, fill: "#FFFFFF", opacity: 0.85 }),
    E("rect", { x: 110, y: 82, width: 180, height: 8, rx: 4, fill: "#FFFFFF", opacity: 0.55 }),
    E("rect", { x: 110, y: 100, width: 150, height: 8, rx: 4, fill: "#FFFFFF", opacity: 0.55 }),
    E("circle", { cx: 280, cy: 140, r: 14, fill: AMBER }),
    P("M30 200 L370 200 L350 252 Q344 262 330 262 L70 262 Q56 262 50 252 Z", { fill: "#C9CAD6", ...OUTLINE }),
    E("rect", { x: 170, y: 200, width: 60, height: 10, rx: 4, fill: "#8A8BA0" }),
  ]),
});

const propCoffeeCup = () => ({
  width: 280,
  height: 300,
  element: svgRoot(280, 300, [], [
    P("M220 110 Q272 110 272 160 Q272 210 218 210", { fill: "none", stroke: INK, strokeWidth: 30, strokeLinecap: "round" }),
    P("M220 110 Q272 110 272 160 Q272 210 218 210", { fill: "none", stroke: CORAL, strokeWidth: 14, strokeLinecap: "round" }),
    P("M40 90 L220 90 L212 250 Q210 272 188 272 L72 272 Q50 272 48 250 Z", { fill: CORAL, ...OUTLINE }),
    E("ellipse", { cx: 130, cy: 90, rx: 90, ry: 16, fill: "#FF9A8C", ...OUTLINE }),
    E("ellipse", { cx: 130, cy: 90, rx: 76, ry: 9, fill: "#5A2A24" }),
    P("M70 120 L72 226", { fill: "none", stroke: "#FFD2C9", strokeWidth: 8, strokeLinecap: "round", opacity: 0.8 }),
    P("M100 58 Q88 42 100 28 Q112 14 100 4", { fill: "none", stroke: SHADE, strokeWidth: 6, strokeLinecap: "round" }),
  ]),
});

const propSofa = () => ({
  width: 480,
  height: 260,
  element: svgRoot(480, 260, [], [
    E("rect", { x: 40, y: 210, width: 26, height: 30, rx: 6, fill: INK }),
    E("rect", { x: 414, y: 210, width: 26, height: 30, rx: 6, fill: INK }),
    E("rect", { x: 40, y: 40, width: 400, height: 130, rx: 36, fill: "#8E6CEF", ...OUTLINE }),
    E("rect", { x: 20, y: 130, width: 440, height: 80, rx: 30, fill: "#A285F5", ...OUTLINE }),
    E("rect", { x: 110, y: 138, width: 110, height: 62, rx: 20, fill: "#B49CFA", ...OUTLINE }),
    E("rect", { x: 260, y: 138, width: 110, height: 62, rx: 20, fill: "#B49CFA", ...OUTLINE }),
    E("rect", { x: 0, y: 110, width: 70, height: 120, rx: 30, fill: "#7A58DB", ...OUTLINE }),
    E("rect", { x: 410, y: 110, width: 70, height: 120, rx: 30, fill: "#7A58DB", ...OUTLINE }),
  ]),
});

const propTv = () => ({
  width: 400,
  height: 320,
  element: svgRoot(400, 320, [lin("tvBlue", [[0, "#5B8CFF"], [1, "#1E3FA8"]])], [
    P("M170 60 L130 14 M230 60 L270 14", STROKE_LINE),
    E("rect", { x: 20, y: 56, width: 360, height: 220, rx: 22, fill: "#2E2E3E", ...OUTLINE }),
    E("rect", { x: 46, y: 82, width: 230, height: 170, rx: 12, fill: "url(#tvBlue)", ...OUTLINE }),
    P("M64 100 L120 100 L76 160 L64 160 Z", { fill: "#FFFFFF", opacity: 0.15 }),
    E("rect", { x: 292, y: 96, width: 62, height: 22, rx: 6, fill: "#8A8BA0" }),
    E("circle", { cx: 323, cy: 150, r: 10, fill: CORAL }),
    E("rect", { x: 292, y: 180, width: 62, height: 10, rx: 5, fill: "#8A8BA0" }),
    E("rect", { x: 110, y: 276, width: 180, height: 16, rx: 8, fill: "#3A3A4C", ...OUTLINE }),
  ]),
});

const propClock = () => {
  const c = 150;
  const ticks = Array.from({ length: 12 }, (_, i) => {
    const [x1, y1] = polar(c, c, 98, i * 30);
    const [x2, y2] = polar(c, c, 116, i * 30);
    return P(`M${x1} ${y1} L${x2} ${y2}`, { fill: "none", stroke: INK, strokeWidth: 8, strokeLinecap: "round" });
  });
  const [hx, hy] = polar(c, c, 60, -60);
  const [mx, my] = polar(c, c, 96, 60);
  return {
    width: 300,
    height: 300,
    element: svgRoot(300, 300, [], [
      E("circle", { cx: c, cy: c, r: 130, fill: "#F6F3EE", ...OUTLINE }),
      ticks,
      P(`M${c} ${c} L${hx} ${hy}`, { fill: "none", stroke: INK, strokeWidth: 12, strokeLinecap: "round" }),
      P(`M${c} ${c} L${mx} ${my}`, { fill: "none", stroke: INK, strokeWidth: 8, strokeLinecap: "round" }),
      E("circle", { cx: c, cy: c, r: 9, fill: CORAL, ...OUTLINE }),
    ]),
  };
};

const propWindowNight = () => ({
  width: 400,
  height: 400,
  element: svgRoot(400, 400, [lin("nightSky", [[0, "#14123A"], [1, "#2E2A6A"]])], [
    E("rect", { x: 20, y: 20, width: 360, height: 360, rx: 14, fill: CREAM, ...OUTLINE }),
    E("rect", { x: 44, y: 44, width: 312, height: 312, rx: 4, fill: "url(#nightSky)" }),
    E("circle", { cx: 270, cy: 130, r: 48, fill: "#FFF4C2" }),
    E("circle", { cx: 286, cy: 118, r: 44, fill: "#1B1A52" }),
    [[90, 90, 2.5], [130, 150, 2], [120, 250, 3], [320, 240, 2], [80, 320, 2], [300, 300, 2.5], [200, 80, 2], [230, 200, 1.8]]
      .map(([x, y, r]) => E("circle", { cx: x, cy: y, r, fill: "#FFFFFF" })),
    E("rect", { x: 196, y: 44, width: 8, height: 312, fill: CREAM, stroke: INK, strokeWidth: 3 }),
    E("rect", { x: 44, y: 196, width: 312, height: 8, fill: CREAM, stroke: INK, strokeWidth: 3 }),
  ]),
});

const propBeachUmbrella = () => {
  const dome = "M40 190 Q40 40 200 40 Q360 40 360 190 Q340 200 320 190 Q300 200 280 190 Q260 200 240 190 Q220 200 200 190 Q180 200 160 190 Q140 200 120 190 Q100 200 80 190 Q60 200 40 190 Z";
  return {
    width: 400,
    height: 400,
    element: svgRoot(400, 400, [], [
      E("rect", { x: 194, y: 150, width: 12, height: 230, fill: CREAM, stroke: INK, strokeWidth: 4 }),
      P(dome, { fill: "#FFFFFF" }),
      P("M200 40 L40 190 L120 190 Z", { fill: CORAL }),
      P("M200 40 L200 190 L280 190 Z", { fill: CORAL }),
      P(dome, { fill: "none", ...OUTLINE }),
      E("circle", { cx: 200, cy: 34, r: 8, fill: INK }),
      E("ellipse", { cx: 200, cy: 384, rx: 70, ry: 14, fill: "#F2D28E", ...OUTLINE }),
    ]),
  };
};

const propCloud = () => {
  const shapes = (p) => [
    E("circle", { cx: 110, cy: 150, r: 60, ...p }),
    E("circle", { cx: 190, cy: 110, r: 80, ...p }),
    E("circle", { cx: 280, cy: 140, r: 64, ...p }),
    E("rect", { x: 50, y: 150, width: 300, height: 60, rx: 30, ...p }),
  ];
  return {
    width: 400,
    height: 240,
    element: svgRoot(400, 240, [], [
      shapes({ fill: INK, stroke: INK, strokeWidth: 14, strokeLinejoin: "round" }),
      shapes({ fill: "#FFFFFF" }),
      E("ellipse", { cx: 200, cy: 190, rx: 120, ry: 12, fill: "#DDE8F5" }),
    ]),
  };
};

const propStarCluster = () => ({
  width: 300,
  height: 260,
  element: svgRoot(300, 260, [], [
    P(starPath(60, 190, 50, 21), { fill: "#FFD84D", ...OUTLINE }),
    P(starPath(240, 70, 42, 18), { fill: "#FFD84D", ...OUTLINE }),
    P(starPath(150, 130, 84, 36), { fill: "#FFD84D", ...OUTLINE }),
    E("circle", { cx: 150, cy: 120, r: 10, fill: "#FFF6C2" }),
  ]),
});

const propRocket = () => ({
  width: 300,
  height: 420,
  element: svgRoot(300, 420, [], [
    P("M110 260 L70 320 L110 310 Z", { fill: CORAL, ...OUTLINE }),
    P("M190 260 L230 320 L190 310 Z", { fill: CORAL, ...OUTLINE }),
    P("M124 302 Q150 400 176 302 Z", { fill: AMBER, ...OUTLINE }),
    P("M138 302 Q150 350 162 302 Z", { fill: "#FFF0A0" }),
    P("M150 30 Q196 90 196 200 L196 290 Q196 300 186 300 L114 300 Q104 300 104 290 L104 200 Q104 90 150 30 Z", {
      fill: "#FFFFFF",
      ...OUTLINE,
    }),
    P("M104 240 L196 240 L196 290 Q196 300 186 300 L114 300 Q104 300 104 290 Z", { fill: CORAL }),
    E("circle", { cx: 150, cy: 150, r: 26, fill: "#5CC8FF", ...OUTLINE }),
    E("circle", { cx: 142, cy: 142, r: 6, fill: "#FFFFFF" }),
  ]),
});

const propBookStack = () => ({
  width: 340,
  height: 260,
  element: svgRoot(340, 260, [], [
    E("rect", { x: 30, y: 190, width: 280, height: 50, rx: 6, fill: CORAL, ...OUTLINE }),
    E("rect", { x: 296, y: 196, width: 10, height: 38, fill: CREAM, stroke: INK, strokeWidth: 3 }),
    E("rect", { x: 56, y: 136, width: 250, height: 54, rx: 6, fill: TEAL, ...OUTLINE }),
    E("rect", { x: 280, y: 140, width: 12, height: 46, fill: CREAM, stroke: INK, strokeWidth: 3 }),
    E("rect", { x: 80, y: 84, width: 210, height: 52, rx: 6, fill: AMBER, ...OUTLINE }),
    E("rect", { x: 268, y: 88, width: 12, height: 44, fill: CREAM, stroke: INK, strokeWidth: 3 }),
    E("rect", { x: 60, y: 213, width: 160, height: 6, rx: 3, fill: "#FFFFFF", opacity: 0.4 }),
    E("rect", { x: 96, y: 110, width: 150, height: 6, rx: 3, fill: "#FFFFFF", opacity: 0.4 }),
    E("rect", { x: 104, y: 158, width: 130, height: 6, rx: 3, fill: "#FFFFFF", opacity: 0.4 }),
  ]),
});

const bgBeachSunset = () => ({
  width: 1920,
  height: 1080,
  element: svgRoot(1920, 1080, [
    lin("sunsetSky", [[0, CORAL], [0.6, "#FFB36B"], [1, "#FFD84D"]]),
    lin("seaGrad", [[0, TEAL], [1, "#0F6E6C"]]),
    rad("sunGlow", [[0, "#FFF0A0", 0.9], [1, "#FFF0A0", 0]]),
  ], [
    E("rect", { x: 0, y: 0, width: 1920, height: 1080, fill: "url(#sunsetSky)" }),
    E("ellipse", { cx: 960, cy: 660, rx: 520, ry: 420, fill: "url(#sunGlow)" }),
    E("circle", { cx: 960, cy: 640, r: 150, fill: "#FFF4B8" }),
    E("rect", { x: 0, y: 700, width: 1920, height: 380, fill: "url(#seaGrad)" }),
    P("M260 760 Q380 744 500 760 M1380 800 Q1500 784 1620 800", {
      fill: "none", stroke: "#8FF0E8", strokeWidth: 6, strokeLinecap: "round", opacity: 0.5,
    }),
  ]),
});

const bgSpaceNight = () => {
  let seed = 42; // fixed seed so the star field is the same on every build
  const rnd = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  const stars = Array.from({ length: 90 }, () =>
    E("circle", {
      cx: Math.round(rnd() * 1920),
      cy: Math.round(rnd() * 1080),
      r: r2(1 + rnd() * 2.5),
      fill: "#FFFFFF",
      opacity: r2(0.4 + rnd() * 0.6),
    }),
  );
  return {
    width: 1920,
    height: 1080,
    element: svgRoot(1920, 1080, [
      lin("spaceSky", [[0, "#2A1B5E"], [1, "#0B0520"]]),
      rad("nebulaGlow", [[0, "#B06BFF", 0.35], [1, "#B06BFF", 0]]),
    ], [
      E("rect", { x: 0, y: 0, width: 1920, height: 1080, fill: "url(#spaceSky)" }),
      E("ellipse", { cx: 1400, cy: 300, rx: 800, ry: 460, fill: "url(#nebulaGlow)" }),
      stars,
      E("circle", { cx: 420, cy: 260, r: 6, fill: "#FFF6C2" }),
    ]),
  };
};

const bgSnowDay = () => ({
  width: 1920,
  height: 1080,
  element: svgRoot(1920, 1080, [lin("snowSky", [[0, "#A9D8FF"], [1, "#E8F5FF"]])], [
    E("rect", { x: 0, y: 0, width: 1920, height: 1080, fill: "url(#snowSky)" }),
    P("M0 700 Q480 560 960 690 Q1440 820 1920 660 L1920 1080 L0 1080 Z", {
      fill: "#FFFFFF", stroke: "#C9DDEF", strokeWidth: 4,
    }),
    P("M0 860 Q520 740 1080 850 Q1500 930 1920 820 L1920 1080 L0 1080 Z", {
      fill: "#F7FBFF", stroke: "#C9DDEF", strokeWidth: 4,
    }),
  ]),
});

const bgCafeWarm = () => ({
  width: 1920,
  height: 1080,
  element: svgRoot(1920, 1080, [
    lin("cafeWall", [[0, "#5A3A24"], [1, "#2A1A10"]]),
    rad("amberGlow", [[0, AMBER, 0.55], [0.5, AMBER, 0.18], [1, AMBER, 0]]),
  ], [
    E("rect", { x: 0, y: 0, width: 1920, height: 1080, fill: "url(#cafeWall)" }),
    E("ellipse", { cx: 960, cy: 480, rx: 820, ry: 560, fill: "url(#amberGlow)" }),
  ]),
});

// ---- Icon and object sprites (400x400 at 2x, centred on 200,200) ----------
const S = 400;
const sprite = (defs, nodes) => ({ width: S, height: S, element: svgRoot(S, S, defs, nodes) });
const GOLD = "#FFD84D";
const BLUE = "#5B8CFF";
const BLUE_DARK = "#1E3FA8";
const WHITE = "#FFFFFF";
const GREY = "#8A8BA0";
const DARK = "#3A3A4C";
const LIGHT_BLUE = "#A9D8FF";
const PINK = "#FF9A8C";
const rad2 = (deg) => (deg * Math.PI) / 180;
const P2 = (p) => p.join(" ");
// Point on a rotated ellipse; t is the parametric angle in radians.
const pt = (cx, cy, rx, ry, deg, t) => {
  const x = rx * Math.cos(t), y = ry * Math.sin(t), a = rad2(deg);
  return [r2(cx + x * Math.cos(a) - y * Math.sin(a)), r2(cy + x * Math.sin(a) + y * Math.cos(a))];
};
// Rotated full ellipse as two arcs (no transform attribute needed).
const ellPath = (cx, cy, rx, ry, deg) => {
  const p0 = pt(cx, cy, rx, ry, deg, 0), p1 = pt(cx, cy, rx, ry, deg, Math.PI);
  return `M${P2(p0)} A${rx} ${ry} ${deg} 1 0 ${P2(p1)} A${rx} ${ry} ${deg} 1 0 ${P2(p0)} Z`;
};
// Ink-outlined stroke with a coloured core, round caps.
const line = (d, color, w) => [
  P(d, { fill: "none", stroke: INK, strokeWidth: w + 10, strokeLinecap: "round", strokeLinejoin: "round" }),
  P(d, { fill: "none", stroke: color, strokeWidth: w, strokeLinecap: "round", strokeLinejoin: "round" }),
];
const disc = (cx, cy, r, fill, extra = {}) => E("circle", { cx, cy, r, fill, ...OUTLINE, ...extra });
const box = (x, y, width, height, rx, fill, extra = {}) => E("rect", { x, y, width, height, rx, fill, ...OUTLINE, ...extra });

const spriteAtom = () => {
  const orbits = [0, 60, 120];
  return sprite([], [
    orbits.map((deg) => P(ellPath(200, 200, 165, 62, deg), { fill: "none", stroke: INK, strokeWidth: 14 })),
    orbits.map((deg) => P(ellPath(200, 200, 165, 62, deg), { fill: "none", stroke: "#5CC8FF", strokeWidth: 6 })),
    disc(184, 190, 26, CORAL), disc(216, 190, 26, TEAL), disc(200, 220, 26, AMBER),
    orbits.map((deg, i) => {
      const [x, y] = pt(200, 200, 165, 62, deg, rad2(i * 110 + 40));
      return disc(x, y, 14, GOLD);
    }),
  ]);
};

const spriteWater = () => sprite([], [
  line("M200 225 L90 110", SHADE, 14),
  line("M200 225 L310 110", SHADE, 14),
  disc(90, 110, 42, WHITE), disc(310, 110, 42, WHITE),
  disc(200, 225, 90, CORAL),
  E("ellipse", { cx: 166, cy: 190, rx: 18, ry: 12, fill: WHITE, opacity: 0.6 }),
]);

const spriteCO2 = () => sprite([], [
  line("M75 186 L325 186", SHADE, 10),
  line("M75 214 L325 214", SHADE, 10),
  disc(75, 200, 58, CORAL), disc(325, 200, 58, CORAL),
  disc(200, 200, 42, GREY),
]);

const spriteDNA = () => {
  const wave = (phase) => {
    const pts = [];
    for (let i = 0; i <= 80; i++) {
      const f = i / 80;
      pts.push(`${r2(200 + 90 * Math.sin(phase + f * Math.PI * 4))} ${r2(30 + f * 340)}`);
    }
    return `M${pts.join(" L")}`;
  };
  const rungs = [];
  for (let k = 0; k < 9; k++) {
    const f = (k + 0.5) / 9;
    const s = Math.sin(f * Math.PI * 4);
    const y = r2(30 + f * 340);
    rungs.push(line(`M${r2(200 + 90 * s)} ${y} L${r2(200 - 90 * s)} ${y}`, CREAM, 8));
  }
  return sprite([], [rungs, line(wave(0), TEAL, 12), line(wave(Math.PI), CORAL, 12)]);
};

const spriteSun = () => {
  const rays = [];
  for (let a = 0; a < 360; a += 45) rays.push(line(`M${P2(polar(200, 200, 118, a))} L${P2(polar(200, 200, 160, a))}`, GOLD, 12));
  return sprite([rad("sunHalo", [[0, "#FFF0A0", 0.95], [0.45, GOLD, 0.45], [1, GOLD, 0]])], [
    E("circle", { cx: 200, cy: 200, r: 195, fill: "url(#sunHalo)" }),
    rays,
    disc(200, 200, 92, GOLD),
    E("circle", { cx: 230, cy: 170, r: 14, fill: WHITE, opacity: 0.7 }),
  ]);
};

const spriteMoon = () => sprite([], [
  P("M273 92.4 A130 130 0 1 0 273 307.6 A110 110 0 1 1 273 92.4 Z", { fill: "#F6F3EE", ...OUTLINE }),
  E("circle", { cx: 110, cy: 175, r: 16, fill: SHADE }),
  E("circle", { cx: 105, cy: 250, r: 10, fill: SHADE }),
]);

const spriteMoonFull = () => sprite([rad("moonGrad", [[0, "#FFFFFF"], [0.6, "#F6F3EE"], [1, SHADE]], 0.4, 0.35, 0.7)], [
  disc(200, 200, 135, "url(#moonGrad)"),
  disc(150, 160, 28, SHADE, { strokeWidth: 4 }),
  disc(248, 236, 22, SHADE, { strokeWidth: 4 }),
  disc(170, 270, 14, SHADE, { strokeWidth: 4 }),
]);

const spriteEarth = () => sprite([rad("earthGrad", [[0, "#7FB5FF"], [0.6, BLUE], [1, BLUE_DARK]], 0.36, 0.32, 0.75)], [
  disc(200, 200, 145, "url(#earthGrad)"),
  E("ellipse", { cx: 160, cy: 170, rx: 44, ry: 30, fill: GREEN }),
  E("ellipse", { cx: 245, cy: 245, rx: 36, ry: 50, fill: GREEN }),
  E("ellipse", { cx: 120, cy: 255, rx: 24, ry: 18, fill: GREEN_DARK }),
  E("ellipse", { cx: 200, cy: 110, rx: 95, ry: 13, fill: WHITE, opacity: 0.9 }),
  E("ellipse", { cx: 262, cy: 88, rx: 40, ry: 9, fill: WHITE, opacity: 0.9 }),
]);

const spriteRingedPlanet = () => {
  const cx = 200, cy = 200, rx = 175, ry = 50, deg = -20;
  const p0 = pt(cx, cy, rx, ry, deg, 0), p1 = pt(cx, cy, rx, ry, deg, Math.PI);
  const back = `M${P2(p1)} A${rx} ${ry} ${deg} 0 1 ${P2(p0)}`;
  const front = `M${P2(p0)} A${rx} ${ry} ${deg} 0 1 ${P2(p1)}`;
  return sprite([rad("planetGrad", [[0, "#FFA08F"], [0.7, CORAL], [1, "#E8564A"]], 0.36, 0.32, 0.75)], [
    line(back, AMBER, 18),
    disc(200, 200, 95, "url(#planetGrad)"),
    line(front, AMBER, 18),
  ]);
};

const spriteStar = () => sprite([], [
  P(starPath(200, 212, 170, 72), { fill: GOLD, ...OUTLINE }),
  E("ellipse", { cx: 170, cy: 180, rx: 18, ry: 12, fill: WHITE, opacity: 0.6 }),
]);

const spriteBulb = () => {
  const rays = [-70, -35, 0, 35, 70].map((a) => line(`M${P2(polar(200, 165, 128, a))} L${P2(polar(200, 165, 156, a))}`, AMBER, 12));
  return sprite([], [
    rays,
    box(150, 258, 100, 32, 8, GREY),
    box(162, 284, 76, 30, 8, GREY),
    disc(200, 165, 100, GOLD),
    E("ellipse", { cx: 160, cy: 130, rx: 14, ry: 26, fill: WHITE, opacity: 0.6 }),
  ]);
};

const spriteGear = () => {
  const rO = 160, rB = 122;
  let d = "";
  for (let k = 0; k < 8; k++) {
    const a = k * 45;
    const b0 = polar(200, 200, rB, a - 13), o0 = polar(200, 200, rO, a - 8);
    const o1 = polar(200, 200, rO, a + 8), b1 = polar(200, 200, rB, a + 13);
    d += (k === 0 ? `M${P2(b0)}` : ` A${rB} ${rB} 0 0 1 ${P2(b0)}`) + ` L${P2(o0)} L${P2(o1)} L${P2(b1)}`;
  }
  d += ` A${rB} ${rB} 0 0 1 ${P2(polar(200, 200, rB, -13))} Z`;
  const hole = "M160 200 A40 40 0 1 0 240 200 A40 40 0 1 0 160 200 Z";
  return sprite([], [P(`${d} ${hole}`, { fill: TEAL, fillRule: "evenodd", ...OUTLINE })]);
};

const spriteMagnifier = () => sprite([], [
  line("M250 250 L345 345", GREY, 36),
  disc(170, 170, 115, GREY),
  disc(170, 170, 92, "#CFF3FF"),
  E("ellipse", { cx: 130, cy: 130, rx: 22, ry: 13, fill: WHITE, opacity: 0.9 }),
]);

const spriteFlask = () => {
  const body = "M165 40 L235 40 L235 170 L334 330 Q344 352 318 352 L82 352 Q56 352 66 330 L165 170 Z";
  return sprite([], [
    P(body, { fill: "#F6F3EE", ...OUTLINE }),
    P("M109 260 L291 260 L320 340 L80 340 Z", { fill: TEAL }),
    E("circle", { cx: 150, cy: 300, r: 8, fill: WHITE, opacity: 0.8 }),
    E("circle", { cx: 250, cy: 292, r: 6, fill: WHITE, opacity: 0.8 }),
    P(body, { fill: "none", ...OUTLINE }),
  ]);
};

const spriteTestTube = () => sprite([], [
  P("M160 58 L240 58 L240 290 A40 40 0 0 1 160 290 Z", { fill: "#E8F7FF", ...OUTLINE }),
  P("M160 190 L240 190 L240 290 A40 40 0 0 1 160 290 Z", { fill: CORAL }),
  P("M176 80 L176 150", { fill: "none", stroke: WHITE, strokeWidth: 8, strokeLinecap: "round", opacity: 0.8 }),
  box(146, 36, 108, 24, 10, GREY),
]);

const spriteMicroscope = () => sprite([], [
  line("M300 320 L300 170 Q300 130 262 130 L224 130", GREY, 26),
  box(186, 264, 24, 42, 6, "#C9CAD6"),
  box(110, 250, 180, 14, 4, GREY),
  box(170, 40, 60, 170, 16, TEAL),
  box(164, 24, 72, 24, 8, DARK),
  box(78, 326, 244, 32, 12, DARK),
]);

const spriteHeart = () => sprite([], [
  P("M200 340 C120 280 50 220 50 150 C50 95 90 60 135 60 C165 60 188 78 200 100 C212 78 235 60 265 60 C310 60 350 95 350 150 C350 220 280 280 200 340 Z", { fill: CORAL, ...OUTLINE }),
  E("circle", { cx: 120, cy: 120, r: 12, fill: WHITE, opacity: 0.7 }),
]);

const spriteCheck = () => sprite([], [
  disc(200, 200, 160, GREEN),
  line("M115 205 L178 268 L290 140", WHITE, 30),
]);

const spriteCross = () => sprite([], [
  disc(200, 200, 160, WHITE),
  line("M130 130 L270 270", CORAL, 30),
  line("M270 130 L130 270", CORAL, 30),
]);

const spriteWarning = () => sprite([], [
  P("M200 48 L362 336 L38 336 Z", { fill: AMBER, ...OUTLINE }),
  E("rect", { x: 186, y: 150, width: 28, height: 110, rx: 12, fill: INK }),
  E("circle", { cx: 200, cy: 294, r: 17, fill: INK }),
]);

const spriteCoin = () => sprite([rad("coinGrad", [[0, "#FFF0A0"], [0.7, GOLD], [1, AMBER]], 0.38, 0.35, 0.75)], [
  disc(200, 200, 150, "url(#coinGrad)"),
  E("circle", { cx: 200, cy: 200, r: 116, fill: "none", stroke: "#E09A2E", strokeWidth: 12 }),
  E("circle", { cx: 200, cy: 200, r: 116, fill: "none", stroke: INK, strokeWidth: 3 }),
  E("ellipse", { cx: 150, cy: 150, rx: 26, ry: 14, fill: WHITE, opacity: 0.5 }),
]);

const spriteChartUp = () => {
  const s = [80, 200], e = [330, 50];
  const ang = Math.atan2(e[1] - s[1], e[0] - s[0]);
  const back = [e[0] - Math.cos(ang) * 34, e[1] - Math.sin(ang) * 34];
  const w = 20;
  const head = `M${r2(e[0])} ${r2(e[1])} L${r2(back[0] - Math.sin(ang) * w)} ${r2(back[1] + Math.cos(ang) * w)} L${r2(back[0] + Math.sin(ang) * w)} ${r2(back[1] - Math.cos(ang) * w)} Z`;
  return sprite([], [
    box(60, 230, 70, 120, 6, CORAL),
    box(160, 180, 70, 170, 6, AMBER),
    box(260, 120, 70, 230, 6, TEAL),
    line(`M${P2(s)} L${P2(back)}`, CORAL, 12),
    P(head, { fill: CORAL, ...OUTLINE }),
  ]);
};

const spriteGlobe = () => {
  const chords = [-75, 0, 75].map((dy) => {
    const hw = Math.sqrt(150 * 150 - dy * dy) - 4;
    return P(`M${r2(200 - hw)} ${200 + dy} L${r2(200 + hw)} ${200 + dy}`, { fill: "none", stroke: WHITE, strokeWidth: 6, opacity: 0.7 });
  });
  return sprite([rad("globeGrad", [[0, "#7FB5FF"], [0.6, BLUE], [1, BLUE_DARK]], 0.36, 0.32, 0.75)], [
    disc(200, 200, 150, "url(#globeGrad)"),
    E("ellipse", { cx: 200, cy: 200, rx: 62, ry: 150, fill: "none", stroke: WHITE, strokeWidth: 6, opacity: 0.7 }),
    chords,
  ]);
};

const spriteMapPin = () => sprite([], [
  P("M200 368 C200 368 90 262 90 160 A110 110 0 0 1 310 160 C310 262 200 368 200 368 Z", { fill: CORAL, ...OUTLINE }),
  disc(200, 160, 42, WHITE),
]);

const spriteSpeechBubble = () => sprite([], [
  P("M120 255 L108 340 L200 268 Z", { fill: WHITE, ...OUTLINE }),
  box(50, 70, 300, 200, 50, WHITE),
  E("rect", { x: 126, y: 266, width: 70, height: 10, fill: WHITE }),
  box(100, 120, 200, 18, 9, SHADE, { stroke: "none" }),
  box(100, 160, 150, 18, 9, SHADE, { stroke: "none" }),
]);

const spriteMegaphone = () => sprite([], [
  box(54, 158, 84, 84, 12, CORAL),
  box(90, 236, 40, 66, 8, GREY),
  P("M130 150 L300 70 L300 330 L130 250 Z", { fill: CORAL, ...OUTLINE }),
  E("ellipse", { cx: 300, cy: 200, rx: 12, ry: 130, fill: PINK, ...OUTLINE }),
  line("M345 130 Q372 200 345 270", AMBER, 12),
  line("M368 170 Q384 200 368 230", AMBER, 12),
]);

const spriteTrophy = () => sprite([lin("cupGold", [[0, "#FFF0A0"], [0.5, GOLD], [1, AMBER]], 0, 0, 1, 0)], [
  line("M125 100 Q70 100 80 150 Q92 190 135 180", AMBER, 16),
  line("M275 100 Q330 100 320 150 Q308 190 265 180", AMBER, 16),
  P("M120 70 L280 70 L270 180 Q262 250 200 255 Q138 250 130 180 Z", { fill: "url(#cupGold)", ...OUTLINE }),
  box(185, 248, 30, 52, 4, AMBER),
  box(130, 298, 140, 38, 8, "#E09A2E"),
  P("M150 96 Q148 140 160 178", { fill: "none", stroke: WHITE, strokeWidth: 8, strokeLinecap: "round", opacity: 0.6 }),
]);

const spriteMedal = () => sprite([rad("medalGold", [[0, "#FFF6C2"], [0.5, GOLD], [1, AMBER]], 0.38, 0.34, 0.72)], [
  P("M135 30 L192 30 L206 186 L166 178 Z", { fill: CORAL, ...OUTLINE }),
  P("M208 30 L265 30 L234 178 L194 186 Z", { fill: TEAL, ...OUTLINE }),
  disc(200, 262, 118, "url(#medalGold)"),
  E("circle", { cx: 200, cy: 262, r: 88, fill: "none", stroke: "#E09A2E", strokeWidth: 10 }),
  P(starPath(200, 262, 56, 24), { fill: "#FFF6C2", stroke: INK, strokeWidth: 4, strokeLinejoin: "round" }),
]);

const spriteShield = () => sprite([], [
  P("M200 40 L345 90 L345 200 Q345 320 200 370 Q55 320 55 200 L55 90 Z", { fill: TEAL, ...OUTLINE }),
  P("M200 78 L318 118 L318 200 Q318 298 200 336 Q82 298 82 200 L82 118 Z", { fill: "#5FE3DC" }),
]);

const spriteLock = () => sprite([], [
  line("M135 200 L135 130 Q135 62 200 62 Q265 62 265 130 L265 200", SHADE, 26),
  box(95, 178, 210, 172, 26, GREY),
  E("circle", { cx: 200, cy: 248, r: 20, fill: INK }),
  E("rect", { x: 189, y: 258, width: 22, height: 52, rx: 6, fill: INK }),
]);

const spriteKey = () => sprite([lin("keyGold", [[0, "#FFF0A0"], [1, AMBER]], 0, 0, 1, 1)], [
  box(190, 182, 170, 36, 10, "url(#keyGold)"),
  box(300, 218, 22, 40, 4, "url(#keyGold)"),
  box(336, 218, 22, 30, 4, "url(#keyGold)"),
  disc(130, 200, 74, "url(#keyGold)"),
  E("circle", { cx: 130, cy: 200, r: 28, fill: INK }),
]);

const spriteBattery = () => sprite([], [
  box(50, 120, 270, 160, 24, GREEN),
  box(318, 172, 36, 56, 8, DARK),
  box(72, 142, 70, 116, 8, "#8BE0A8", { strokeWidth: 4 }),
  box(152, 142, 70, 116, 8, "#8BE0A8", { strokeWidth: 4 }),
  box(232, 142, 70, 116, 8, "#8BE0A8", { strokeWidth: 4 }),
]);

const spriteFire = () => sprite([], [
  P("M200 40 C262 118 330 170 320 262 C312 330 262 360 200 360 C138 360 88 330 80 262 C74 204 118 172 140 124 C160 152 170 172 182 184 C190 112 182 84 200 40 Z", { fill: AMBER, ...OUTLINE }),
  P("M200 176 C232 224 270 244 262 302 C256 340 232 350 200 350 C168 350 144 330 140 302 C136 262 170 244 200 176 Z", { fill: GOLD }),
]);

const spriteWaterDrop = () => sprite([], [
  P("M200 40 C200 40 90 180 90 262 A110 110 0 0 0 310 262 C310 180 200 40 200 40 Z", { fill: "#5CC8FF", ...OUTLINE }),
  P("M142 242 Q142 206 172 178", { fill: "none", stroke: WHITE, strokeWidth: 14, strokeLinecap: "round", opacity: 0.85 }),
]);

const spriteLeaf = () => sprite([], [
  P("M80 330 C50 170 160 56 340 56 C340 236 250 340 80 330 Z", { fill: GREEN, ...OUTLINE }),
  P("M80 330 Q210 230 326 96", { fill: "none", stroke: "#8BE0A8", strokeWidth: 8, strokeLinecap: "round" }),
  P("M150 250 L190 236 M190 190 L230 170 M240 140 L270 118", { fill: "none", stroke: "#8BE0A8", strokeWidth: 6, strokeLinecap: "round" }),
]);

const spriteLightning = () => sprite([], [
  P("M235 30 L105 215 L190 215 L160 370 L300 165 L212 165 L252 30 Z", { fill: GOLD, ...OUTLINE }),
]);

const spriteSnowflake = () => {
  let d = "";
  for (let a = 0; a < 360; a += 60) {
    d += ` M200 200 L${P2(polar(200, 200, 150, a))}`;
    const q = polar(200, 200, 92, a);
    for (const s of [-1, 1]) {
      const e = [r2(q[0] + 40 * Math.sin(rad2(a + s * 40))), r2(q[1] - 40 * Math.cos(rad2(a + s * 40)))];
      d += ` M${P2(q)} L${P2(e)}`;
    }
  }
  d = d.trim();
  return sprite([], [
    P(d, { fill: "none", stroke: INK, strokeWidth: 26, strokeLinecap: "round" }),
    P(d, { fill: "none", stroke: LIGHT_BLUE, strokeWidth: 14, strokeLinecap: "round" }),
    disc(200, 200, 22, WHITE),
  ]);
};

const spriteBrain = () => sprite([], [
  box(180, 300, 40, 50, 12, PINK),
  E("ellipse", { cx: 150, cy: 200, rx: 102, ry: 112, fill: PINK, ...OUTLINE }),
  E("ellipse", { cx: 250, cy: 200, rx: 102, ry: 112, fill: PINK, ...OUTLINE }),
  P("M200 110 Q190 200 200 300", { fill: "none", stroke: INK, strokeWidth: 8, strokeLinecap: "round" }),
  P("M86 176 Q122 150 130 196 Q138 240 104 254", { fill: "none", stroke: INK, strokeWidth: 6, strokeLinecap: "round", strokeLinejoin: "round" }),
  P("M314 176 Q278 150 270 196 Q262 240 296 254", { fill: "none", stroke: INK, strokeWidth: 6, strokeLinecap: "round", strokeLinejoin: "round" }),
]);

const spriteTimer = () => sprite([], [
  E("rect", { x: 330, y: 206, width: 34, height: 22, rx: 6, fill: DARK }),
  box(186, 46, 28, 56, 4, DARK),
  box(170, 22, 60, 30, 8, DARK),
  disc(200, 232, 140, CORAL),
  disc(200, 232, 108, CREAM),
  Array.from({ length: 12 }, (_, i) =>
    P(`M${P2(polar(200, 232, 92, i * 30))} L${P2(polar(200, 232, 104, i * 30))}`, { fill: "none", stroke: INK, strokeWidth: 8, strokeLinecap: "round" })),
  P(`M200 232 L${P2(polar(200, 232, 74, 35))}`, { fill: "none", stroke: INK, strokeWidth: 12, strokeLinecap: "round" }),
  P(`M200 232 L${P2(polar(200, 232, 92, -80))}`, { fill: "none", stroke: DARK, strokeWidth: 6, strokeLinecap: "round" }),
  disc(200, 232, 12, INK),
]);

// ---- Asset registry -------------------------------------------------------
// Characters are 400x800 at 2x; each body part is its own transparent PNG on the same canvas,
// so rig pivots line up with the full figure. Both sides (left/right) are included in each part.
const charPart = (nodes) => ({ width: 400, height: 800, element: charRoot(nodes) });

const ASSETS = {
  head: charPart(partHead()),
  eyes: charPart(partEyes()),
  eyebrows: charPart(partEyebrows()),
  mouth: charPart(partMouth()),
  torso: charPart(partTorso()),
  upper_arm: charPart(partUpperArm()),
  forearm: charPart(partForearm()),
  hand: charPart(partHand()),
  upper_leg: charPart(partUpperLeg()),
  lower_leg: charPart(partLowerLeg()),
  shoe: charPart(partShoe()),
  figure: charPart(figureNodes()),
  window: propWindow(),
  phone: propPhone(),
  alarm_clock: propAlarmClock(),
  mountain: propMountain(),
  heavy_bag: propHeavyBag(),
  navy_gradient: bgNavy(),
  plant: propPlant(),
  lamp: propLamp(),
  bookshelf: propBookshelf(),
  laptop: propLaptop(),
  coffee_cup: propCoffeeCup(),
  sofa_purple: propSofa(),
  tv_blue: propTv(),
  clock_wall: propClock(),
  window_night: propWindowNight(),
  beach_umbrella: propBeachUmbrella(),
  cloud: propCloud(),
  star_cluster: propStarCluster(),
  rocket: propRocket(),
  book_stack: propBookStack(),
  beach_sunset: bgBeachSunset(),
  space_night: bgSpaceNight(),
  snow_day: bgSnowDay(),
  cafe_warm: bgCafeWarm(),
  atom: spriteAtom(),
  molecule_water: spriteWater(),
  molecule_co2: spriteCO2(),
  dna_helix: spriteDNA(),
  sun: spriteSun(),
  moon: spriteMoon(),
  moon_full: spriteMoonFull(),
  earth: spriteEarth(),
  planet_ringed: spriteRingedPlanet(),
  star: spriteStar(),
  lightbulb: spriteBulb(),
  gear: spriteGear(),
  magnifier: spriteMagnifier(),
  flask: spriteFlask(),
  test_tube: spriteTestTube(),
  microscope: spriteMicroscope(),
  heart: spriteHeart(),
  check_mark: spriteCheck(),
  cross_mark: spriteCross(),
  warning_triangle: spriteWarning(),
  coin: spriteCoin(),
  chart_up: spriteChartUp(),
  globe: spriteGlobe(),
  map_pin: spriteMapPin(),
  speech_bubble: spriteSpeechBubble(),
  megaphone: spriteMegaphone(),
  trophy: spriteTrophy(),
  medal: spriteMedal(),
  shield: spriteShield(),
  lock: spriteLock(),
  key: spriteKey(),
  battery: spriteBattery(),
  fire: spriteFire(),
  water_drop: spriteWaterDrop(),
  leaf: spriteLeaf(),
  lightning: spriteLightning(),
  snowflake: spriteSnowflake(),
  brain: spriteBrain(),
  timer: spriteTimer(),
};

// ---- Pipeline -------------------------------------------------------------
function pickFont() {
  for (const p of FONT_CANDIDATES) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function loadFontData(fontPath) {
  const buf = fs.readFileSync(fontPath);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

async function renderAsset(name, def, fontData) {
  const tree = E("div", { style: { display: "flex", width: def.width, height: def.height } }, def.element);
  const svg = await satori(tree, {
    width: def.width,
    height: def.height,
    fonts: [{ name: "Arial", data: fontData, weight: 700, style: "normal" }],
  });
  fs.writeFileSync(path.join(SRC_DIR, `${name}.svg`), svg);
  const png = new Resvg(svg, { fitTo: { mode: "width", value: def.width } }).render().asPng();
  const outPath = path.join(SPRITE_DIR, `${name}.png`);
  fs.writeFileSync(outPath, png);
  return { outPath, bytes: png.length, w: def.width, h: def.height };
}

async function main() {
  const fontPath = pickFont();
  if (!fontPath) {
    console.error("build-assets: no usable bold font found. Tried:\n  " + FONT_CANDIDATES.join("\n  "));
    process.exit(1);
  }
  const fontData = loadFontData(fontPath);
  fs.mkdirSync(SPRITE_DIR, { recursive: true });
  fs.mkdirSync(SRC_DIR, { recursive: true });
  console.log(`font: ${fontPath}`);

  for (const [name, def] of Object.entries(ASSETS)) {
    const r = await renderAsset(name, def, fontData);
    console.log(`${name}: ${r.w}x${r.h} -> ${path.relative(REPO_ROOT, r.outPath)} (${r.bytes} bytes)`);
  }
  console.log(`done: ${Object.keys(ASSETS).length} assets`);
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error("build-assets failed:", err);
    process.exit(1);
  });

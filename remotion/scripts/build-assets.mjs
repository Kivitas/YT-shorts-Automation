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

// Limb as a round-capped stroke: ink outline, cream body, shade band on the right side.
function pill(x1, y1, x2, y2, w) {
  const d = `M${x1} ${y1} L${x2} ${y2}`;
  const ds = `M${x1 + w * 0.32} ${y1} L${x2 + w * 0.32} ${y2}`;
  return [
    P(d, { stroke: INK, strokeWidth: w + 12, strokeLinecap: "round", fill: "none" }),
    P(d, { stroke: CREAM, strokeWidth: w, strokeLinecap: "round", fill: "none" }),
    P(ds, { stroke: SHADE, strokeWidth: w * 0.35, strokeLinecap: "round", fill: "none" }),
  ];
}

// ---- Character parts (400x800 canvas, figure centred on x=200) -----------
const CHAR_DEFS = [
  rad("headGrad", [[0, "#F6F3EE"], [0.55, CREAM], [1, SHADE]], 0.36, 0.32, 0.7),
  lin("torsoGrad", [[0, CREAM], [0.6, CREAM], [1, SHADE]], 0, 0, 1, 0),
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

const partUpperArm = () => [...pill(138, 250, 112, 350, 44), ...pill(262, 250, 288, 350, 44)];
const partForearm = () => [...pill(112, 350, 118, 448, 40), ...pill(288, 350, 282, 448, 40)];
const partHand = () => [
  E("circle", { cx: 118, cy: 472, r: 24, fill: CREAM, ...OUTLINE }),
  E("circle", { cx: 282, cy: 472, r: 24, fill: CREAM, ...OUTLINE }),
];
const partUpperLeg = () => [...pill(180, 470, 176, 600, 58), ...pill(220, 470, 224, 600, 58)];
const partLowerLeg = () => [...pill(176, 600, 172, 720, 48), ...pill(224, 600, 228, 720, 48)];
const partShoe = () => [
  E("ellipse", { cx: 158, cy: 744, rx: 38, ry: 18, fill: "#3A3A4C", ...OUTLINE }),
  E("ellipse", { cx: 242, cy: 744, rx: 38, ry: 18, fill: "#3A3A4C", ...OUTLINE }),
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

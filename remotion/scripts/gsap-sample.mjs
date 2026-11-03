#!/usr/bin/env node
/**
 * Samples a GSAP timeline on a plain state object (no DOM) and prints the values per frame.
 *
 * Usage:
 *   node gsap-sample.mjs '<spec json>'        (JSON spec as argv[2])
 *   echo '<spec json>' | node gsap-sample.mjs  (JSON spec on stdin)
 *
 * Spec fields:
 *   duration  number   timeline length in seconds (default 1). Tweens are placed on this clock.
 *   fps       number   frames per second used when `frames` is absent (default 30).
 *   frames    number   optional exact number of samples N (default round(duration * fps), min 1).
 *   state     object   initial values of the animated properties, e.g. {"zoom": 1}.
 *                      Every key a tween animates must exist here.
 *   tweens    array    items {at, duration, vars, ease?, from?}:
 *                      at       start time in seconds on the timeline
 *                      duration seconds
 *                      vars     target values, e.g. {"zoom": 1.06}
 *                      ease     GSAP ease name, e.g. "sine.inOut" (default "none")
 *                      from     optional start values; when set the tween uses fromTo
 *
 * Output (stdout, one JSON line):
 *   {"frames": N, "values": {key: [v0, ..., vN-1]}}
 * Frame i samples timeline progress i / (N - 1), so frame 0 is the initial state and the
 * last frame is the state after the last tween. Exit code 1 with a message on stderr on error.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const here = path.dirname(fileURLToPath(import.meta.url));
// Resolve "gsap" from remotion/node_modules, not from the script's own location.
const require = createRequire(path.join(here, "..", "package.json"));
const gsapModule = require("gsap");
const gsap = gsapModule.gsap ?? gsapModule.default ?? gsapModule;

function fail(message) {
  process.stderr.write(`gsap-sample: ${message}\n`);
  process.exit(1);
}

function readSpecText() {
  if (process.argv[2] !== undefined) return Promise.resolve(process.argv[2]);
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => (data += chunk));
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

function sample(spec) {
  const duration = Number(spec.duration ?? 1);
  if (!(duration > 0)) throw new Error("duration must be a positive number");
  const fps = Number(spec.fps ?? 30);
  const frames = Math.max(1, Math.round(spec.frames ?? duration * fps));
  const state = { ...(spec.state ?? {}) };
  // GSAP attaches its own "_gsap" cache to animated objects, so report only the spec's keys.
  const keys = Object.keys(state);
  const tweens = Array.isArray(spec.tweens) ? spec.tweens : [];

  const tl = gsap.timeline({ paused: true });
  for (const [i, tw] of tweens.entries()) {
    const vars = tw.vars ?? {};
    for (const key of Object.keys(vars)) {
      if (!(key in state)) throw new Error(`tween ${i} animates "${key}" which is missing from state`);
    }
    const common = { duration: Number(tw.duration ?? 0), ease: tw.ease ?? "none" };
    const at = Number(tw.at ?? 0);
    if (tw.from) {
      tl.fromTo(state, { ...tw.from, ...common, immediateRender: true }, { ...vars, ...common }, at);
    } else {
      tl.to(state, { ...vars, ...common }, at);
    }
  }
  // Pad so the timeline spans the requested duration even when tweens end earlier.
  if (tl.duration() < duration) tl.set({}, {}, duration);

  const values = Object.fromEntries(keys.map((k) => [k, []]));
  // tl.progress() renders the absolute state at that point, so sampling order does not matter.
  for (let i = 0; i < frames; i++) {
    const p = frames > 1 ? i / (frames - 1) : 0;
    tl.progress(p, true);
    for (const k of keys) values[k].push(state[k]);
  }
  return { frames, values };
}

try {
  const text = await readSpecText();
  if (!text || !text.trim()) fail("no JSON spec given on argv[2] or stdin");
  const spec = JSON.parse(text);
  const result = sample(spec);
  process.stdout.write(JSON.stringify(result) + "\n");
  gsap.ticker?.sleep?.();
} catch (err) {
  fail(err && err.message ? err.message : String(err));
}

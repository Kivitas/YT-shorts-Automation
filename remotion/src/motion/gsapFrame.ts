// Frame-driven GSAP timelines for Remotion.
//
// Remotion renders each frame by seeking, so GSAP must never run on its own clock. Each timeline is
// paused and only moved with tl.progress(), which maps the current frame onto the timeline. The same
// frame always gives the same output. No requestAnimationFrame and no gsap.ticker are used here.
//
// Animations write into a plain state object (not DOM nodes), and components render from that state.
// React therefore stays the only owner of the DOM, and GSAP never fights React over inline styles.

import gsap from "gsap";
import { useMemo } from "react";

export type GsapFrameState<T extends object> = {
  state: T;
  // Seek the timeline to the given composition frame. Call it during render, before reading `state`.
  frameState: (frame: number, fps: number) => void;
};

// Builds a paused timeline once per `deps` change.
//   init:            creates the plain state object the tweens animate.
//   build:           adds tweens to the timeline (times are in seconds).
//   durationSeconds: total timeline length. A trailing empty set pins it, so progress maps exactly to
//                    the composition length even when the last tween ends earlier.
export function useGsapTimeline<T extends object>(
  init: () => T,
  build: (tl: gsap.core.Timeline, state: T) => void,
  durationSeconds: number,
  deps: readonly unknown[] = [],
): GsapFrameState<T> {
  const created = useMemo(() => {
    const state = init();
    const tl = gsap.timeline({ paused: true });
    build(tl, state);
    tl.set({}, {}, durationSeconds);
    return { state, tl };
    // init and build must depend only on `deps`, which the caller supplies.
  }, [durationSeconds, ...deps]);

  const { state, tl } = created;
  const frameState = (frame: number, fps: number) => {
    const total = Math.max(durationSeconds * fps, 1e-9);
    tl.progress(Math.min(1, Math.max(0, frame / total)));
  };

  return { state, frameState };
}

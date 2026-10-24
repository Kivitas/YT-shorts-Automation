// Pure timing helpers for the caption overlay. No Remotion imports, so tests can run them directly.

export type TimedCaption = { start: number };

// Index of the word to highlight at time t: the last word that has started. Between words (or
// after the page's last word) the most recently spoken word stays highlighted, instead of the
// first word of the page, which is what a plain findIndex fallback to 0 would show.
export const activeWordIndex = (page: TimedCaption[], t: number): number => {
  let idx = 0;
  for (let i = 0; i < page.length; i++) {
    if (t >= page[i].start) {
      idx = i;
    }
  }
  return idx;
};

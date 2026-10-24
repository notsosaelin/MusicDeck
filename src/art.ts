import { useEffect, useState } from "react";

/**
 * Pictures that loaded and pictures that didn't, by URL, for this session. A cover that isn't there
 * (an album on a player without one) used to flash the browser's broken-image icon each time its card
 * was drawn again (L1/R1 between tabs) before falling back to the kind's icon; now a picture shows
 * only once it has loaded, and one that failed goes straight to the fallback the next time.
 */
const known = new Map<string, boolean>();
const MAX_KNOWN = 3000;

function remember(url: string, loaded: boolean) {
  if (known.size >= MAX_KNOWN) known.clear();
  known.set(url, loaded);
}

/** Whether a picture is known not to load. */
export const isMissingArt = (url: string | undefined) => !url || known.get(url) === false;

/** Pictures can change (artwork found, a tag edit): failed ones get another try after a change. */
export function forgetMissingArt() {
  for (const [url, loaded] of known) if (!loaded) known.delete(url);
}

/**
 * For an <img>: `failed` (show the fallback instead), `visible` (show the picture: it has loaded),
 * and the handlers that find out. Until it loads the picture is hidden, so a missing one never shows
 * the broken-image icon.
 */
export function useArtLoad(url: string | undefined) {
  const [, redraw] = useState(0);
  const status = url ? known.get(url) : undefined;
  return {
    failed: !url || status === false,
    visible: status === true,
    onLoad: () => {
      if (url && known.get(url) !== true) {
        remember(url, true);
        redraw((n) => n + 1);
      }
    },
    onError: () => {
      if (url) {
        remember(url, false);
        redraw((n) => n + 1);
      }
    },
  };
}

/** A picture's URL once it has loaded, else undefined: for where an <img> can't be hidden (SVG). */
export function useLoadedArt(url: string | undefined): string | undefined {
  const [, redraw] = useState(0);
  useEffect(() => {
    if (!url || known.has(url)) return;
    let live = true;
    const img = new Image();
    img.onload = () => {
      remember(url, true);
      if (live) redraw((n) => n + 1);
    };
    img.onerror = () => {
      remember(url, false);
      if (live) redraw((n) => n + 1);
    };
    img.src = url;
    return () => {
      live = false;
    };
  }, [url]);
  return url && known.get(url) === true ? url : undefined;
}

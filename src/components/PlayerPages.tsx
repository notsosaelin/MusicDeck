import { Focusable, PanelSection, PanelSectionRow, Spinner } from "@decky/ui";
import { FC, ReactNode, useEffect, useRef, useState } from "react";
import { openMediaCenter } from "../nav";
import { player } from "../player";
import { Store, useStore } from "../store";
import { ScrollingPlainLyrics, SyncedLyrics, useLyrics } from "./LyricsView";
import { NowPlaying } from "./NowPlaying";
import { Queue, confirmClearQueue } from "./Queue";
import { Visualizer } from "./media/nowplaying";
import { watchSize } from "./common";

/** The second page's view: the song's lyrics or what's up next; kept while the panel is closed. */
const pageTwo = new Store<{ side: "lyrics" | "queue" }>({ side: "lyrics" });

/** How long a page takes to glide into place */
const GLIDE_MS = 220;

/** The panel's scrolling area (Steam's tab content), whose height is one page. */
function scrollParent(el: HTMLElement | null): HTMLElement | null {
  let node = el?.parentElement ?? null;
  while (node && !/auto|scroll/.test(getComputedStyle(node).overflowY)) node = node.parentElement;
  return node;
}

/**
 * Now playing in the Quick Access panel, as two pages a screen tall: the player (cover, title, seek bar,
 * controls, volume), then, below it, the song's lyrics or the queue, switched at its top. Moving between
 * them glides the panel a whole page, so neither is ever seen half; within the queue the panel scrolls
 * as usual.
 */
export const PlayerPages: FC = () => {
  const { queue } = useStore(player.state);
  const { side } = useStore(pageTwo);
  const root = useRef<HTMLDivElement>(null);
  const second = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(0); // the scrolling area's height
  const [above, setAbove] = useState(0); // the tab strip above the first page

  useEffect(() => {
    const scroller = scrollParent(root.current);
    if (!scroller || !root.current) return;
    const measure = () => {
      setPage(scroller.clientHeight);
      setAbove(
        root.current
          ? root.current.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop
          : 0,
      );
    };
    measure();
    return watchSize(scroller, measure);
  }, []);

  // the controller moving between the pages glides the panel to the page it's on
  useEffect(() => {
    const el = root.current;
    const scroller = scrollParent(el);
    if (!el || !scroller) return;
    const win = el.ownerDocument.defaultView ?? window;
    let frame = 0;
    const glide = (to: number) => {
      win.cancelAnimationFrame(frame);
      const from = scroller.scrollTop;
      const target = Math.max(0, Math.min(to, scroller.scrollHeight - scroller.clientHeight));
      if (Math.abs(target - from) < 1) return;
      let start = 0;
      const step = (now: number) => {
        start ||= now;
        const t = Math.min(1, (now - start) / GLIDE_MS);
        scroller.scrollTop = from + (target - from) * (1 - Math.pow(1 - t, 3));
        if (t < 1) frame = win.requestAnimationFrame(step);
      };
      frame = win.requestAnimationFrame(step);
    };
    const onFocus = (e: FocusEvent) => {
      const target = e.target as HTMLElement & { mdPaged?: boolean };
      const top = second.current;
      const onFirst = !top?.contains(target);
      // the second page's top (the switch, the lyrics) snaps the page whole; its queue rows scroll
      // as any list does
      const snaps = onFirst || !!target.closest?.(".md-page-snap");
      if (!snaps) return;
      if (!target.mdPaged) {
        // Steam scrolls what it focuses into view too; the glide is this page's scrolling
        target.mdPaged = true;
        target.scrollIntoView = () => {};
      }
      glide(onFirst ? 0 : top!.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop);
    };
    el.addEventListener("focusin", onFocus);
    return () => {
      el.removeEventListener("focusin", onFocus);
      win.cancelAnimationFrame(frame);
    };
  }, []);

  const height = Math.max(page, 240);
  return (
    <div ref={root}>
      {/* the first page and the tab strip above it fill the panel */}
      <div
        style={{
          minHeight: Math.max(height - above, 0),
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
        }}
      >
        <NowPlaying />
      </div>
      {queue.length > 0 && (
        <div ref={second} style={{ minHeight: height }}>
          <PageSwitch side={side} upNext={queue.length} />
          {side === "lyrics" ? <PanelLyrics height={height - 64} /> : <Queue />}
        </div>
      )}
    </div>
  );
};

/** Lyrics | Queue at the top of the second page. X clears the queue (it asks first). */
const PageSwitch: FC<{ side: "lyrics" | "queue"; upNext: number }> = ({ side, upNext }) => (
  <PanelSection>
    <PanelSectionRow>
      <Focusable
        className="md-page-snap"
        flow-children="row"
        style={{ display: "flex", gap: 6, justifyContent: "center", padding: "10px 0 6px" }}
      >
        {(["lyrics", "queue"] as const).map((s) => (
          <Focusable
            key={s}
            className={side === s ? "md-pill md-on md-page-snap" : "md-pill md-page-snap"}
            noFocusRing
            onActivate={() => pageTwo.set({ side: s })}
            onClick={() => pageTwo.set({ side: s })}
            onOKActionDescription={s === "lyrics" ? "Lyrics" : "Queue"}
            onSecondaryButton={confirmClearQueue}
            onSecondaryActionDescription="Clear queue"
            style={{
              padding: "5px 14px",
              borderRadius: 14,
              fontSize: 13,
              fontWeight: 600,
              color: side === s ? "#0e141b" : "#dcdedf",
              background: side === s ? "#dcdedf" : "rgba(255, 255, 255, 0.08)",
            }}
          >
            {s === "lyrics" ? "Lyrics" : `Queue · ${upNext}`}
          </Focusable>
        ))}
      </Focusable>
    </PanelSectionRow>
  </PanelSection>
);

/**
 * The song's lyrics as big as the page allows; A opens them full screen. A song without (or lyrics the
 * sites couldn't give right now) shows the music drawn instead.
 */
const PanelLyrics: FC<{ height: number }> = ({ height }) => {
  const { queue, index } = useStore(player.state);
  const item = queue[index];
  const lyrics = useLyrics(item);
  const focusable = (children: ReactNode) => (
    <PanelSection>
      <PanelSectionRow>
        <Focusable
          className="md-line md-page-snap"
          noFocusRing
          onActivate={() => openMediaCenter("playing")}
          onOKActionDescription="Full screen"
          style={{ borderRadius: 4, padding: "0 8px", margin: "0 -8px" }}
        >
          {children}
        </Focusable>
      </PanelSectionRow>
    </PanelSection>
  );
  if (lyrics === undefined)
    return focusable(
      <div style={{ height, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Spinner style={{ width: 32, height: 32 }} />
      </div>,
    );
  if (!lyrics || "error" in lyrics)
    return focusable(
      <Visualizer
        height={height}
        caption={lyrics === null ? "No lyrics for this track" : "Lyrics aren't available right now"}
      />,
    );
  return focusable(
    <>
      {lyrics.synced ? (
        <SyncedLyrics lyrics={lyrics} height={height - 24} fontSize={18} seekable={false} />
      ) : (
        <ScrollingPlainLyrics lyrics={lyrics} height={height - 24} fontSize={15} />
      )}
      {lyrics.source && (
        <div style={{ fontSize: 11, opacity: 0.45, padding: "6px 0 0" }}>Lyrics from {lyrics.source}</div>
      )}
    </>,
  );
};

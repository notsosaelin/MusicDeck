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

/** How long a page takes to slide into place */
const SLIDE = "transform 0.24s cubic-bezier(0.25, 0.8, 0.3, 1)";

/** The panel's scrolling area (Steam's tab content). */
function scrollParent(el: HTMLElement | null): HTMLElement | null {
  let node = el?.parentElement ?? null;
  while (node && !/auto|scroll/.test(getComputedStyle(node).overflowY)) node = node.parentElement;
  return node;
}

/**
 * Now playing in the Quick Access panel, as two pages in a frame that fills the panel below the tabs:
 * the player (cover, title, seek bar, controls, volume), and under it the song's lyrics or the queue,
 * switched at its top. The controller moving between them slides the frame a whole page; the panel
 * itself never scrolls, so neither Steam's focus scrolling nor its scroll restore can leave a page
 * half shown. A long queue scrolls inside its own page.
 */
export const PlayerPages: FC = () => {
  const { queue } = useStore(player.state);
  const { side } = useStore(pageTwo);
  const root = useRef<HTMLDivElement>(null);
  const second = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState(0); // the panel's height below the tabs: one page
  const [onSecond, setOnSecond] = useState(false);
  const hasSecond = queue.length > 0;

  useEffect(() => {
    const el = root.current;
    const scroller = scrollParent(el);
    if (!el || !scroller) return;
    const measure = () => {
      const top = el.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
      setRoom(Math.floor(scroller.clientHeight - top));
    };
    measure();
    return watchSize(scroller, measure);
  }, []);

  // which page the controller is on decides which one shows
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const onFocus = (e: FocusEvent) => setOnSecond(!!second.current?.contains(e.target as Node));
    el.addEventListener("focusin", onFocus);
    return () => el.removeEventListener("focusin", onFocus);
  }, []);

  const page = Math.max(room, 240);
  const showing = hasSecond && onSecond;
  return (
    // clip, not hidden: Steam's focus scrolling can move an overflow:hidden box
    <div ref={root} style={{ height: page, overflow: "clip" }}>
      <div style={{ transform: `translateY(${showing ? -page : 0}px)`, transition: SLIDE }}>
        <div
          style={{
            height: page,
            overflowY: "auto", // only if the player is ever taller than the panel
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
          }}
        >
          <NowPlaying />
        </div>
        {hasSecond && (
          <div ref={second} style={{ height: page, overflowY: side === "queue" ? "auto" : "clip" }}>
            <PageSwitch side={side} upNext={queue.length} />
            {side === "lyrics" ? <PanelLyrics height={page - 64} /> : <Queue />}
          </div>
        )}
      </div>
    </div>
  );
};

/** Lyrics | Queue at the top of the second page. X on Queue, while it shows, clears it (it asks first). */
const PageSwitch: FC<{ side: "lyrics" | "queue"; upNext: number }> = ({ side, upNext }) => (
  <PanelSection>
    <PanelSectionRow>
      <Focusable
        flow-children="row"
        style={{ display: "flex", gap: 6, justifyContent: "center", padding: "10px 0 6px" }}
      >
        {(["lyrics", "queue"] as const).map((s) => (
          <Focusable
            key={s}
            className={side === s ? "md-pill md-on" : "md-pill"}
            noFocusRing
            onActivate={() => pageTwo.set({ side: s })}
            onClick={() => pageTwo.set({ side: s })}
            onOKActionDescription={s === "lyrics" ? "Lyrics" : "Queue"}
            // clearing is the queue's: only on its own pill, while it shows
            onSecondaryButton={s === "queue" && side === "queue" ? confirmClearQueue : undefined}
            onSecondaryActionDescription={s === "queue" && side === "queue" ? "Clear queue" : undefined}
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
          className="md-line"
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

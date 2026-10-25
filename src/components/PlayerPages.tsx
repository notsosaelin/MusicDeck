import { Focusable, PanelSection, PanelSectionRow, Spinner } from "@decky/ui";
import { FC, ReactNode, RefObject, useEffect, useRef, useState } from "react";
import { openMediaCenter, showTab } from "../nav";
import { player } from "../player";
import { Store, mediaUrl, useStore } from "../store";
import { ScrollingPlainLyrics, SyncedLyrics, useLyrics } from "./LyricsView";
import { NowPlaying } from "./NowPlaying";
import { Queue, confirmClearQueue } from "./Queue";
import { Visualizer } from "./media/nowplaying";
import { ButtonRow, IconButton, watchSize } from "./common";
import { FaMusic, FaSearch } from "react-icons/fa";
import { saveQueue } from "./TrackMenu";

/** The second page's view: the song's lyrics or what's up next; kept while the panel is closed. */
const pageTwo = new Store<{ side: "lyrics" | "queue" }>({ side: "lyrics" });

/** The Lyrics | Queue switch atop the second page */
const SWITCH_HEIGHT = 40;

/** How long a page takes to slide into place */
const SLIDE = "transform 0.24s cubic-bezier(0.25, 0.8, 0.3, 1)";

/** The panel's scrolling area (Steam's tab content). */
function scrollParent(el: HTMLElement | null): HTMLElement | null {
  let node = el?.parentElement ?? null;
  while (node && !/auto|scroll/.test(getComputedStyle(node).overflowY)) node = node.parentElement;
  return node;
}

/**
 * Now playing in the Quick Access panel, as two pages in a frame as tall as the panel: the tabs and the
 * player (cover, title, seek bar, controls, volume), and under it the song's lyrics or the queue, all
 * the panel's room to themselves. The controller moving between them slides the frame a whole page;
 * the panel itself never scrolls, so neither Steam's focus scrolling nor its scroll restore can leave a
 * page half shown. A long queue scrolls inside its own page.
 */
/** The cover's size in the panel, and the smallest it gets to keep the player on its page. */
const COVER = 76;
const COVER_MIN = 44;
/** Room kept under the volume slider at the foot of the page. */
const AIR = 8;

/**
 * The cover size that keeps the player (`content`) inside its page (`area`): what it runs over by comes
 * off the cover (while the cover is the tallest thing in its row, the row shrinks with it one for one),
 * and room coming back gives it back, up to COVER. A casting line or a long title then squeezes the cover,
 * not the volume slider onto page 2. What counts is how far down the player is drawn, not its box: the
 * panel section's empty padding below the slider can go.
 */
function useCoverThatFits(
  area: RefObject<HTMLElement | null>,
  content: RefObject<HTMLElement | null>,
  deps: unknown[],
) {
  const [cover, setCover] = useState(COVER);
  useEffect(() => {
    const outer = area.current;
    const inner = content.current;
    if (!outer || !inner) return;
    const drawnBottom = () => {
      let bottom = 0;
      for (const el of inner.querySelectorAll("*")) {
        const r = el.getBoundingClientRect();
        if (r.width && r.height) bottom = Math.max(bottom, r.bottom);
      }
      return bottom;
    };
    const fit = () => {
      const room = outer.getBoundingClientRect();
      // a closed panel measures nothing: nothing to fit
      if (!room.height) return;
      const over = Math.ceil(drawnBottom() + AIR - room.bottom);
      setCover((c) => Math.max(COVER_MIN, Math.min(COVER, c - over)));
    };
    fit();
    const unwatchOuter = watchSize(outer, fit);
    const unwatchInner = watchSize(inner, fit);
    return () => {
      unwatchOuter();
      unwatchInner();
    };
  }, deps);
  return cover;
}

export const PlayerPages: FC<{ head: ReactNode }> = ({ head }) => {
  const { queue } = useStore(player.state);
  const { side } = useStore(pageTwo);
  const root = useRef<HTMLDivElement>(null);
  const second = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState(0); // the panel's height: one page
  const [onSecond, setOnSecond] = useState(false);
  const area = useRef<HTMLDivElement>(null);
  const player_ = useRef<HTMLDivElement>(null);
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
  const cover = useCoverThatFits(area, player_, [page, hasSecond]);
  const showing = hasSecond && onSecond;
  return (
    // clip, not hidden: Steam's focus scrolling can move an overflow:hidden box
    <div ref={root} style={{ height: page, overflow: "clip" }}>
      <div style={{ transform: `translateY(${showing ? -page : 0}px)`, transition: SLIDE }}>
        <div style={{ height: page, display: "flex", flexDirection: "column" }}>
          {head}
          {/* the player in the room under the tabs, with what's left over spread round it */}
          <div
            ref={area}
            style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", justifyContent: "space-evenly" }}
          >
            {hasSecond ? (
              <div ref={player_}>
                <NowPlaying cover={cover} />
              </div>
            ) : (
              <NothingPlaying />
            )}
          </div>
        </div>
        {hasSecond && (
          <div ref={second} style={{ height: page, overflowY: side === "queue" ? "auto" : "clip" }}>
            <PageSwitch side={side} upNext={queue.length} />
            {/* the lyrics' and the list's section spacing would make a second gap under the header */}
            {side === "lyrics" ? (
              <div style={{ marginTop: -16 }}>
                <PanelLyrics height={page - SWITCH_HEIGHT + 8} />
              </div>
            ) : (
              <div style={{ marginTop: -20 }}>
                <Queue titled={false} />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

/** Now playing with nothing to play: the two ways to find something, so the controller has somewhere to go. */
const NothingPlaying: FC = () => (
  <PanelSection>
    <ButtonRow>
      <IconButton label="Library" onClick={() => showTab("library")} style={{ height: 40, fontSize: 14 }}>
        <FaMusic /> Library
      </IconButton>
      <IconButton label="Search" onClick={() => showTab("search")} style={{ height: 40, fontSize: 14 }}>
        <FaSearch /> Search
      </IconButton>
    </ButtonRow>
  </PanelSection>
);

/** A word in the second page's header: small and grey as Steam's section titles, white when chosen. */
const HeaderWord: FC<{ on?: boolean; label: string; onPick: () => void; children: ReactNode }> = ({
  on,
  label,
  onPick,
  children,
}) => (
  <Focusable
    className="md-line"
    noFocusRing
    onActivate={onPick}
    onClick={onPick}
    onOKActionDescription={label}
    style={{
      position: "relative",
      padding: "6px 8px",
      borderRadius: 4,
      fontSize: 12,
      fontWeight: 700,
      letterSpacing: "0.06em",
      textTransform: "uppercase",
      color: on ? "#ffffff" : "rgba(255, 255, 255, 0.45)",
    }}
  >
    {children}
    {on && <span style={{ position: "absolute", left: 8, right: 8, bottom: 2, height: 2, background: "#ffffff" }} />}
  </Focusable>
);

/**
 * The second page's header, as quiet as a section title: Lyrics and Queue to pick between and, while
 * the queue shows, Clear at its end (always a move away, however long the queue).
 */
const PageSwitch: FC<{ side: "lyrics" | "queue"; upNext: number }> = ({ side, upNext }) => (
  <PanelSection>
    <PanelSectionRow>
      <Focusable
        flow-children="row"
        // the hairline under it as under the tabs: the header apart from what it heads
        style={{
          display: "flex",
          alignItems: "center",
          height: SWITCH_HEIGHT,
          margin: "0 -8px",
          borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
        }}
      >
        <HeaderWord on={side === "lyrics"} label="Lyrics" onPick={() => pageTwo.set({ side: "lyrics" })}>
          Lyrics
        </HeaderWord>
        <HeaderWord on={side === "queue"} label="Queue" onPick={() => pageTwo.set({ side: "queue" })}>
          Queue <span style={{ opacity: 0.7, fontWeight: 600 }}>{upNext}</span>
        </HeaderWord>
        <div style={{ flex: 1 }} />
        {side === "queue" && (
          <HeaderWord label="Save the queue as a playlist" onPick={() => saveQueue(window)}>
            Save
          </HeaderWord>
        )}
        {side === "queue" && (
          <HeaderWord label="Clear queue" onPick={confirmClearQueue}>
            Clear
          </HeaderWord>
        )}
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
  if (!lyrics || "error" in lyrics) {
    const art = item?.track.art ? mediaUrl("art", item.instanceId, item.track.art) : undefined;
    return focusable(<Visualizer look="record" height={height} art={art} />);
  }
  return focusable(
    <>
      {lyrics.synced ? (
        <SyncedLyrics lyrics={lyrics} height={height} fontSize={18} seekable={false} />
      ) : (
        <ScrollingPlainLyrics lyrics={lyrics} height={height} fontSize={15} />
      )}
    </>,
  );
};

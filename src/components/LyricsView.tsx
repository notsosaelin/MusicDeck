import { Navigation, PanelSection, PanelSectionRow, Spinner } from "@decky/ui";
import { FC, useEffect, useRef, useState } from "react";
import { FaExpand } from "react-icons/fa";
import { Lyrics, getLyrics } from "../api";
import { QueueItem, player } from "../player";
import { useStore } from "../store";
import { ButtonRow, IconButton, Message } from "./common";

/** The full-screen lyrics page (registered in index.tsx). */
export const LYRICS_ROUTE = "/musicdeck/lyrics";

/** Lyrics per track, kept for the session so flipping tabs doesn't refetch. null = the track has none. */
const lyricsCache = new Map<string, Lyrics | null>();

/** undefined while loading, null when the track has none, { error } when they couldn't be looked up. */
export type LyricsResult = Lyrics | null | undefined | { error: string };

/** The lyrics of a queue item. A failed lookup isn't remembered: the next look tries again. */
export function useLyrics(item: QueueItem | undefined): LyricsResult {
  const key = item ? `${item.instanceId}:${item.track.id}` : "";
  const [lyrics, setLyrics] = useState<LyricsResult>(() => (key ? lyricsCache.get(key) : null));
  useEffect(() => {
    if (!item) return setLyrics(null);
    if (lyricsCache.has(key)) return setLyrics(lyricsCache.get(key));
    setLyrics(undefined);
    let cancelled = false;
    getLyrics(item.instanceId, item.track)
      .catch((e) => ({ ok: false as const, error: String(e) }))
      .then((r) => {
        if (r.ok) lyricsCache.set(key, r.data);
        if (!cancelled) setLyrics(r.ok ? r.data : { error: r.error });
      });
    return () => {
      cancelled = true;
    };
  }, [key]);
  return lyrics;
}

/**
 * Lines that follow the song: the current one sits a third of the way down a window of `height` pixels
 * and lines before and after fade out at the edges. Tapping a line jumps there.
 */
export const SyncedLyrics: FC<{ lyrics: Lyrics; height: number; fontSize: number }> = ({
  lyrics,
  height,
  fontSize,
}) => {
  const { current } = useStore(player.position);
  const lineRefs = useRef<(HTMLDivElement | null)[]>([]);
  let active = -1;
  for (let i = 0; i < lyrics.lines.length; i++) {
    if ((lyrics.lines[i].t ?? 0) <= current + 0.25) active = i;
    else break;
  }
  // Lines wrap, so position by the active line's measured offset rather than a fixed line height
  const el = lineRefs.current[Math.max(active, 0)];
  const offset = height / 3 - (el ? el.offsetTop + el.offsetHeight / 2 : 0);
  return (
    <div
      style={{
        height,
        overflow: "hidden",
        position: "relative",
        // fade lines in and out at the edges instead of cutting them off
        maskImage: "linear-gradient(transparent, black 18%, black 82%, transparent)",
        WebkitMaskImage: "linear-gradient(transparent, black 18%, black 82%, transparent)",
      }}
    >
      <div style={{ transform: `translateY(${offset}px)`, transition: "transform 350ms ease" }}>
        {lyrics.lines.map((line, i) => (
          <div
            key={i}
            ref={(node) => {
              lineRefs.current[i] = node;
            }}
            onClick={() => line.t !== null && player.seek(line.t)}
            style={{
              padding: `${fontSize * 0.3}px 0`,
              fontSize,
              lineHeight: 1.3,
              fontWeight: i === active ? 600 : 400,
              color: i === active ? "#ffffff" : undefined,
              opacity: i === active ? 1 : i < active ? 0.35 : 0.55,
              transition: "opacity 250ms, color 250ms",
            }}
          >
            {line.text || "♪"}
          </div>
        ))}
      </div>
    </div>
  );
};

/**
 * Unsynced lyrics have no timings, so they scroll with the song's progress instead: the whole text
 * passes through a window of `height` pixels as the track plays.
 */
export const ScrollingPlainLyrics: FC<{ lyrics: Lyrics; height: number; fontSize: number }> = ({
  lyrics,
  height,
  fontSize,
}) => {
  const { current, duration } = useStore(player.position);
  const inner = useRef<HTMLDivElement>(null);
  const travel = Math.max((inner.current?.scrollHeight ?? 0) - height * 0.6, 0);
  const progress = duration > 0 ? Math.min(current / duration, 1) : 0;
  return (
    <div
      style={{
        height,
        overflow: "hidden",
        maskImage: "linear-gradient(transparent, black 12%, black 88%, transparent)",
        WebkitMaskImage: "linear-gradient(transparent, black 12%, black 88%, transparent)",
      }}
    >
      <div
        ref={inner}
        style={{
          transform: `translateY(${height * 0.2 - travel * progress}px)`,
          transition: "transform 1s linear",
          fontSize,
          lineHeight: 1.6,
          opacity: 0.85,
        }}
      >
        {lyrics.lines.map((line, i) => (
          <div key={i} style={{ minHeight: line.text ? undefined : fontSize * 0.8 }}>
            {line.text}
          </div>
        ))}
      </div>
    </div>
  );
};

export const openFullScreenLyrics = () => {
  Navigation.CloseSideMenus();
  Navigation.Navigate(LYRICS_ROUTE);
};

/** The Lyrics view in the Quick Access panel. */
export const LyricsView: FC = () => {
  const { queue, index } = useStore(player.state);
  const item = queue[index];
  const lyrics = useLyrics(item);

  if (!item) return <Message>Play something to see its lyrics</Message>;
  if (lyrics === undefined)
    return (
      <PanelSection>
        <PanelSectionRow>
          <div style={{ display: "flex", justifyContent: "center", padding: 16 }}>
            <Spinner style={{ width: 32, height: 32 }} />
          </div>
        </PanelSectionRow>
      </PanelSection>
    );
  if (!lyrics) return <Message>No lyrics found for this track</Message>;
  if ("error" in lyrics) return <Message>Couldn't load lyrics: {lyrics.error}</Message>;
  return (
    <PanelSection title="Lyrics">
      <ButtonRow>
        <IconButton onClick={openFullScreenLyrics}>
          <FaExpand /> Full screen
        </IconButton>
      </ButtonRow>
      <PanelSectionRow>
        {lyrics.synced ? (
          <SyncedLyrics lyrics={lyrics} height={300} fontSize={16} />
        ) : (
          <ScrollingPlainLyrics lyrics={lyrics} height={300} fontSize={14} />
        )}
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ fontSize: 11, opacity: 0.4, padding: "8px 0" }}>Lyrics from {lyrics.source}</div>
      </PanelSectionRow>
    </PanelSection>
  );
};

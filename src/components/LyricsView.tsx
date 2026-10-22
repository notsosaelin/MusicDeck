import { PanelSection, PanelSectionRow, Spinner } from "@decky/ui";
import { FC, useEffect, useRef, useState } from "react";
import { Lyrics, getLyrics } from "../api";
import { player } from "../player";
import { useStore } from "../store";
import { Message } from "./common";

/** Lyrics per track, kept for the session so flipping tabs doesn't refetch. null = none found. */
const lyricsCache = new Map<string, Lyrics | null>();

const WINDOW_HEIGHT = 300;
/** Keep the current line about a third of the way down the window, like most players. */
const ANCHOR = WINDOW_HEIGHT / 3;

const SyncedLyrics: FC<{ lyrics: Lyrics }> = ({ lyrics }) => {
  const { current } = useStore(player.position);
  const lineRefs = useRef<(HTMLDivElement | null)[]>([]);
  let active = -1;
  for (let i = 0; i < lyrics.lines.length; i++) {
    if ((lyrics.lines[i].t ?? 0) <= current + 0.25) active = i;
    else break;
  }
  // Lines wrap, so position by the active line's measured offset rather than a fixed line height
  const el = lineRefs.current[Math.max(active, 0)];
  const offset = ANCHOR - (el ? el.offsetTop + el.offsetHeight / 2 : 0);
  return (
    <div
      style={{
        height: WINDOW_HEIGHT,
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
            // tap a line to jump there
            onClick={() => line.t !== null && player.seek(line.t)}
            style={{
              padding: "5px 0",
              fontSize: 16,
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

const PlainLyrics: FC<{ lyrics: Lyrics }> = ({ lyrics }) => (
  <div style={{ fontSize: 14, lineHeight: 1.6, opacity: 0.85 }}>
    {lyrics.lines.map((line, i) => (
      <div key={i} style={{ minHeight: line.text ? undefined : 12 }}>
        {line.text}
      </div>
    ))}
  </div>
);

export const LyricsView: FC = () => {
  const { queue, index } = useStore(player.state);
  const item = queue[index];
  const key = item ? `${item.instanceId}:${item.track.id}` : "";
  const [lyrics, setLyrics] = useState<Lyrics | null | undefined>(() => (key ? lyricsCache.get(key) : null));

  useEffect(() => {
    if (!item) return;
    if (lyricsCache.has(key)) return setLyrics(lyricsCache.get(key));
    setLyrics(undefined);
    let cancelled = false;
    getLyrics(item.instanceId, item.track)
      .then((r) => (r.ok ? r.data : null))
      .catch(() => null)
      .then((found) => {
        lyricsCache.set(key, found);
        if (!cancelled) setLyrics(found);
      });
    return () => {
      cancelled = true;
    };
  }, [key]);

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
  return (
    <PanelSection title="Lyrics">
      <PanelSectionRow>
        {lyrics.synced ? <SyncedLyrics lyrics={lyrics} /> : <PlainLyrics lyrics={lyrics} />}
      </PanelSectionRow>
      <PanelSectionRow>
        <div style={{ fontSize: 11, opacity: 0.4, padding: "8px 0" }}>Lyrics from {lyrics.source}</div>
      </PanelSectionRow>
    </PanelSection>
  );
};

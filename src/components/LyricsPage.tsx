import { Focusable } from "@decky/ui";
import { FC, RefObject, useEffect, useRef, useState } from "react";
import { player } from "../player";
import { mediaUrl, useStore } from "../store";
import { formatTime } from "./common";
import { ScrollingPlainLyrics, SyncedLyrics, useLyrics } from "./LyricsView";

/**
 * The height of an element, kept current. The page renders into Steam's main window while this code
 * runs in its shared context, so `window` here isn't the page's window; measure the element instead.
 */
function useHeight(ref: RefObject<HTMLElement | null>) {
  const [height, setHeight] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setHeight(el.clientHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return height;
}

/**
 * Full-screen lyrics in Steam's main window, over the album art. A plays or pauses, X skips, Y goes
 * back a track; B is Steam's own back.
 */
export const LyricsPage: FC = () => {
  const { queue, index, playing } = useStore(player.state);
  const { current, duration } = useStore(player.position);
  const item = queue[index];
  const lyrics = useLyrics(item);
  const area = useRef<HTMLDivElement>(null);
  const height = useHeight(area);
  const art = item?.track.art ? mediaUrl("art", item.instanceId, item.track.art) : undefined;

  const message = !item
    ? "Play something to see its lyrics"
    : lyrics === undefined
      ? "Looking for lyrics…"
      : lyrics === null
        ? "No lyrics found for this track"
        : "error" in lyrics
          ? `Couldn't load lyrics: ${lyrics.error}`
          : null;

  return (
    <Focusable
      // the page takes the controller's face buttons; it has nothing else to focus
      onOKButton={() => player.toggle()}
      onOKActionDescription={playing ? "Pause" : "Play"}
      onSecondaryButton={() => player.next()}
      onSecondaryActionDescription="Next"
      onOptionsButton={() => player.previous()}
      onOptionsActionDescription="Previous"
      noFocusRing
      preferredFocus
      autoFocus
      style={{ position: "absolute", inset: 0, overflow: "hidden", background: "#0e141b", color: "#dcdedf" }}
    >
      {art && (
        <div
          style={{
            position: "absolute",
            inset: -60,
            backgroundImage: `url("${art}")`,
            backgroundSize: "cover",
            backgroundPosition: "center",
            filter: "blur(48px) brightness(0.3) saturate(1.2)",
          }}
        />
      )}
      <div
        style={{
          position: "relative",
          display: "flex",
          gap: "5vw",
          alignItems: "center",
          height: "100%",
          boxSizing: "border-box",
          // clear of Steam's header and footer bars
          padding: "11vh 6vw 12vh",
        }}
      >
        {/* sized by height too, so a long title wrapping can't push the cover under Steam's header */}
        <div style={{ width: "min(26vw, 40vh)", flexShrink: 0, display: "flex", flexDirection: "column", gap: 14 }}>
          {art ? (
            <img src={art} style={{ width: "100%", aspectRatio: "1", objectFit: "cover", borderRadius: 8 }} />
          ) : (
            <div style={{ width: "100%", aspectRatio: "1", borderRadius: 8, background: "rgba(255,255,255,0.08)" }} />
          )}
          <div style={{ fontSize: 28, fontWeight: 700, lineHeight: 1.2, color: "#ffffff" }}>
            {item?.track.title ?? "Nothing playing"}
          </div>
          {item && <div style={{ fontSize: 20, opacity: 0.75 }}>{item.track.artist}</div>}
          {item && !item.track.live && duration > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
              <div style={{ height: 4, borderRadius: 2, background: "rgba(255,255,255,0.2)" }}>
                <div
                  style={{
                    height: "100%",
                    width: `${Math.min((current / duration) * 100, 100)}%`,
                    borderRadius: 2,
                    background: "#ffffff",
                  }}
                />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 14, opacity: 0.6 }}>
                <span>{formatTime(current)}</span>
                <span>{formatTime(duration)}</span>
              </div>
            </div>
          )}
        </div>
        <div ref={area} style={{ flex: 1, height: "100%", minWidth: 0 }}>
          {message ? (
            <div style={{ fontSize: 24, opacity: 0.6, paddingTop: height / 3 }}>{message}</div>
          ) : lyrics && !("error" in lyrics) && height > 0 ? (
            lyrics.synced ? (
              <SyncedLyrics lyrics={lyrics} height={height} fontSize={34} />
            ) : (
              <ScrollingPlainLyrics lyrics={lyrics} height={height} fontSize={26} />
            )
          ) : null}
        </div>
      </div>
    </Focusable>
  );
};

import { Focusable } from "@decky/ui";
import { FC, useEffect, useState } from "react";
import { NowPlayingKind, TrackDetails, getTrackDetails } from "../../api";
import { QueueItem, player } from "../../player";
import { RecentlyUsed, Store, useStore } from "../../store";
import { useOnAir } from "../NowPlaying";
import { showTrackMenu } from "../TrackMenu";
import { Notice, TrackTable } from "./parts";

/**
 * What Now Playing shows beside the cover, by what's playing: songs get their lyrics and the queue;
 * a station what's on air, the station and stations like it; an episode its notes and the show's other
 * episodes; a book its chapters and blurb; a concert its setlist and notes. R2 goes through them.
 */
export const PANELS: Record<NowPlayingKind, { id: string; label: string }[]> = {
  music: [
    { id: "lyrics", label: "Lyrics" },
    { id: "queue", label: "Queue" },
  ],
  radio: [
    { id: "onair", label: "On air" },
    { id: "station", label: "Station" },
    { id: "related", label: "Similar" },
  ],
  podcast: [
    { id: "notes", label: "Notes" },
    { id: "related", label: "Episodes" },
    { id: "queue", label: "Queue" },
  ],
  book: [
    { id: "lyrics", label: "Chapters" },
    { id: "notes", label: "About" },
    { id: "queue", label: "Queue" },
  ],
  concert: [
    { id: "setlist", label: "Setlist" },
    { id: "notes", label: "Show notes" },
  ],
};

/** Talk (podcasts, books) gets skip and speed controls in place of shuffle and repeat. */
export const isTalk = (kind: NowPlayingKind) => kind === "podcast" || kind === "book";

const detailsCache = new RecentlyUsed<TrackDetails | null>(50);

/** More about the playing track (notes, facts, related), fetched once per track; undefined while it loads. */
export function useDetails(item: QueueItem | undefined, wanted: boolean): TrackDetails | null | undefined {
  const key = item ? `${item.instanceId}:${item.track.id}` : "";
  const [details, setDetails] = useState<TrackDetails | null | undefined>(() =>
    detailsCache.has(key) ? detailsCache.get(key) : undefined,
  );
  useEffect(() => {
    if (!item || !wanted) return setDetails(item ? undefined : null);
    if (detailsCache.has(key)) return setDetails(detailsCache.get(key));
    setDetails(undefined);
    let current = true;
    getTrackDetails(item.instanceId, item.track)
      .then((r) => {
        const found = r.ok ? r.data : null;
        if (r.ok) detailsCache.set(key, found);
        if (current) setDetails(found);
      })
      .catch(() => current && setDetails(null));
    return () => {
      current = false;
    };
  }, [key, wanted]);
  return details;
}

/** A line to rest the controller on, so long text scrolls under it a paragraph at a time. */
const Line: FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) => (
  <Focusable className="mc-line" noFocusRing onActivate={() => {}} style={{ padding: "6px 10px", ...style }}>
    {children}
  </Focusable>
);

const Facts: FC<{ facts: [string, string][] }> = ({ facts }) => (
  <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: 2, marginBottom: 14 }}>
    {facts.map(([label, value]) => (
      <Line key={label} style={{ display: "flex", gap: 16 }}>
        <span style={{ width: 120, flexShrink: 0, opacity: 0.55, fontSize: 14 }}>{label}</span>
        <span style={{ fontSize: 15, minWidth: 0, overflowWrap: "anywhere" }}>{value}</span>
      </Line>
    ))}
  </Focusable>
);

/** Notes (show notes, a blurb), a paragraph to a line, then the facts; or the facts alone (a station). */
export const DetailsPanel: FC<{ details: TrackDetails | null | undefined; show: "notes" | "facts"; empty: string }> = ({
  details,
  show,
  empty,
}) => {
  if (details === undefined) return <Notice>Loading…</Notice>;
  const paragraphs = show === "notes" ? (details?.notes ?? "").split("\n").filter(Boolean) : [];
  const facts = details?.facts ?? [];
  if (!paragraphs.length && !facts.length) return <Notice>{empty}</Notice>;
  return (
    <>
      {paragraphs.length > 0 && (
        <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", marginBottom: 14 }}>
          {paragraphs.map((p, i) => (
            <Line key={i} style={{ fontSize: 16, lineHeight: 1.45, opacity: 0.9 }}>
              {p}
            </Line>
          ))}
        </Focusable>
      )}
      {facts.length > 0 && <Facts facts={facts} />}
    </>
  );
};

/** Similar stations, the show's other episodes: A plays from there. */
export const RelatedPanel: FC<{ item: QueueItem; details: TrackDetails | null | undefined; empty: string }> = ({
  item,
  details,
  empty,
}) => {
  if (details === undefined) return <Notice>Loading…</Notice>;
  const tracks = details?.related?.tracks ?? [];
  if (!tracks.length) return <Notice>{empty}</Notice>;
  return (
    <TrackTable
      instanceId={item.instanceId}
      tracks={tracks}
      showAlbum={false}
      onPlay={(i) => player.playList(item.instanceId, tracks, i)}
      onSecondary={(i) => player.enqueue(item.instanceId, [tracks[i]])}
      secondaryLabel="Add to queue"
      onOptions={(i, e) => showTrackMenu(item.instanceId, tracks[i], e?.currentTarget)}
    />
  );
};

/** What each station played while it was on, newest first (kept while MusicDeck runs). */
const heard = new Store<Record<string, { text: string; at: number }[]>>({});
const HEARD_KEPT = 30;

/** The song on air now, big, and what the station played before it while it was on. */
export const OnAirPanel: FC<{ item: QueueItem }> = ({ item }) => {
  const { playing } = useStore(player.state);
  const song = useOnAir(item, playing);
  const key = `${item.instanceId}:${item.track.id}`;
  const earlier = (useStore(heard)[key] ?? []).filter((h) => h.text !== song?.text);
  useEffect(() => {
    if (!song?.text) return;
    const list = heard.get()[key] ?? [];
    if (list[0]?.text !== song.text)
      heard.update({ [key]: [{ text: song.text, at: Date.now() }, ...list].slice(0, HEARD_KEPT) });
  }, [song?.text, key]);
  const time = (at: number) => new Date(at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return (
    <>
      <div style={{ padding: "4px 10px 18px" }}>
        <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 1, color: "#ff5c5c" }}>● ON AIR</div>
        {song ? (
          <>
            <div style={{ fontSize: 26, fontWeight: 700, color: "#ffffff", marginTop: 6, lineHeight: 1.2 }}>
              {song.title || song.text}
            </div>
            {song.artist && <div style={{ fontSize: 18, opacity: 0.75, marginTop: 4 }}>{song.artist}</div>}
          </>
        ) : (
          <div style={{ fontSize: 16, opacity: 0.6, marginTop: 6 }}>
            {playing ? "This station doesn't say what it's playing" : "Play the station to see what's on"}
          </div>
        )}
      </div>
      {earlier.length > 0 && (
        <>
          <div style={{ fontSize: 13, fontWeight: 700, opacity: 0.5, letterSpacing: "0.06em", padding: "0 10px 6px" }}>
            EARLIER
          </div>
          <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {earlier.map((h) => (
              <Line key={h.at} style={{ display: "flex", gap: 16 }}>
                <span style={{ width: 70, flexShrink: 0, opacity: 0.5, fontSize: 14 }}>{time(h.at)}</span>
                <span style={{ fontSize: 15 }}>{h.text}</span>
              </Line>
            ))}
          </Focusable>
        </>
      )}
    </>
  );
};

/** A concert's whole show in order, the song playing lit; A plays from there. */
export const SetlistPanel: FC = () => {
  const { queue, index } = useStore(player.state);
  const item = queue[index];
  if (!item) return null;
  return (
    <TrackTable
      instanceId={item.instanceId}
      tracks={queue.map((q) => q.track)}
      numbered
      showAlbum={false}
      onPlay={(i) => player.jumpTo(i)}
      onOptions={(i, e) => showTrackMenu(queue[i].instanceId, queue[i].track, e?.currentTarget)}
    />
  );
};

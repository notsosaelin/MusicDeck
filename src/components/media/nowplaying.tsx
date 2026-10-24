import { Focusable } from "@decky/ui";
import { FC, ReactNode, useEffect, useRef, useState } from "react";
import { NowPlayingKind, OnAir, TrackDetails, getTrackDetails } from "../../api";
import { QueueItem, player } from "../../player";
import { RecentlyUsed, Store, useStore } from "../../store";
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
  library: [
    { id: "lyrics", label: "Lyrics" },
    { id: "queue", label: "Queue" },
    { id: "info", label: "Info" },
  ],
  radio: [
    { id: "onair", label: "On air" },
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
/** Songs, with lyrics (or the visualizer in their place): music from a service or from files. */
export const isMusic = (kind: NowPlayingKind) => kind === "music" || kind === "library";

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
const Label: FC<{ children: ReactNode }> = ({ children }) => (
  <div style={{ fontSize: 13, fontWeight: 700, opacity: 0.5, letterSpacing: "0.06em", marginBottom: 8 }}>
    {children}
  </div>
);

/**
 * A station's Now Playing: what's on (its song when it says), how long you've listened, the station at a
 * glance (its language, quality and today's listeners, as text: its place and genres are under its name)
 * and what it played earlier, as much
 * of that as fits `height`: it never scrolls. Stations like it are on Similar.
 */
export const OnAirPanel: FC<{
  item: QueueItem;
  song: OnAir | null;
  details: TrackDetails | null | undefined;
  height: number;
}> = ({ item, song, details, height }) => {
  const key = `${item.instanceId}:${item.track.id}`;
  // what it played earlier: as many as fit under the rest (the song, the station's details)
  const room = height - (song ? 76 : 0) - 2 * 26 - 50;
  const earlier = (useStore(heard)[key] ?? [])
    .filter((h) => h.text !== song?.text)
    .slice(0, Math.max(0, Math.floor(room / 26)));
  useEffect(() => {
    if (!song?.text) return;
    const list = heard.get()[key] ?? [];
    if (list[0]?.text !== song.text)
      heard.update({ [key]: [{ text: song.text, at: Date.now() }, ...list].slice(0, HEARD_KEPT) });
  }, [song?.text, key]);
  const time = (at: number) => new Date(at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const facts = Object.fromEntries(details?.facts ?? []);
  const stationFacts = (
    [
      // two languages at most: a station listing a dozen would push the page down
      [
        "Language",
        (facts.Language ?? "")
          .split(",")
          .map((l: string) => l.trim())
          .filter(Boolean)
          .slice(0, 2)
          .join(", "),
      ],
      ["Listeners today", (facts.Today ?? "").replace(/ listeners$/, "")],
    ] as [string, string][]
  ).filter(([, value]) => value);
  return (
    <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {song && (
        <div>
          <div style={{ fontSize: 26, fontWeight: 700, color: "#ffffff", lineHeight: 1.2 }}>
            {song.title || song.text}
          </div>
          {song.artist && <div style={{ fontSize: 18, opacity: 0.75, marginTop: 4 }}>{song.artist}</div>}
        </div>
      )}
      {/* the station in a few words: what the line under its name doesn't already say */}
      {details !== undefined && stationFacts.length > 0 && <Facts facts={stationFacts} />}
      {earlier.length > 0 && (
        <div>
          <Label>EARLIER</Label>
          <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {earlier.map((h) => (
              <Line key={h.at} style={{ display: "flex", gap: 16 }}>
                <span style={{ width: 70, flexShrink: 0, opacity: 0.5, fontSize: 14 }}>{time(h.at)}</span>
                <span style={{ fontSize: 15 }}>{h.text}</span>
              </Line>
            ))}
          </Focusable>
        </div>
      )}
    </Focusable>
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

/** Bars across the visualizer, and how often it draws (30 a second: smooth, and easy on the battery) */
const BARS = 40;
const FRAME_MS = 1000 / 30;

/**
 * A quiet visualizer where a song's lyrics would be when it has none, from the music as it's heard:
 * "bars" (mirrored about the middle, low notes left, high right) where there's width, as full screen;
 * "wave" (an oscilloscope's line) in a squarer space, as the Quick Access panel's. It settles when
 * paused and then stops drawing until the music plays again, or its size changes (a canvas left at its
 * old size is stretched, its resting dots into tall smears), and draws only while it shows. No line
 * under it: no lyrics, or none to be had right now, it just shows the music.
 */
export const Visualizer: FC<{ height: number; look?: "bars" | "wave" }> = ({ height, look = "bars" }) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const el = canvas.current;
    const win = el?.ownerDocument.defaultView;
    const ctx = el?.getContext("2d");
    if (!el || !win || !ctx) return;
    const bins = new Uint8Array(look === "wave" ? 256 : 128);
    const shown = new Float32Array(BARS);
    let swing = 0; // the wave's height, eased like the bars
    let frame = 0;
    let last = 0;
    const draw = (now: number) => {
      frame = 0;
      if (now - last < FRAME_MS) return void (frame = win.requestAnimationFrame(draw));
      last = now;
      const scale = win.devicePixelRatio || 1;
      const w = el.clientWidth;
      const h = el.clientHeight;
      if (el.width !== Math.round(w * scale) || el.height !== Math.round(h * scale)) {
        el.width = Math.round(w * scale);
        el.height = Math.round(h * scale);
      }
      const playing = player.state.get().playing;
      const heard = playing && (look === "wave" ? player.waveform(bins) : player.levels(bins));
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      ctx.clearRect(0, 0, w, h);
      let moving = false;
      if (look === "wave") {
        swing = heard ? Math.min(1, swing + 0.2) : swing * 0.85;
        moving = swing > 0.01;
        ctx.strokeStyle = `rgba(255, 255, 255, ${0.25 + 0.4 * swing})`;
        ctx.lineWidth = 2;
        ctx.lineJoin = "round";
        ctx.beginPath();
        const points = 64;
        for (let i = 0; i <= points; i++) {
          const v = heard ? (bins[Math.floor((i / points) * (bins.length - 1))] - 128) / 128 : 0;
          const x = (i / points) * w;
          const y = h / 2 + v * swing * h * 0.45;
          if (i) ctx.lineTo(x, y);
          else ctx.moveTo(x, y);
        }
        ctx.stroke();
      } else {
        const gap = Math.max(3, w / BARS / 3);
        const bar = (w - gap * (BARS - 1)) / BARS;
        for (let i = 0; i < BARS; i++) {
          // the lower bins hold most of music; spread the bars over the lower three-quarters, more to the left
          const at = Math.floor(Math.pow(i / BARS, 1.6) * bins.length * 0.75);
          const target = heard ? bins[at] / 255 : 0;
          // up at once, down gently
          shown[i] = target > shown[i] ? target : shown[i] * 0.88;
          if (shown[i] > 0.005) moving = true;
          const length = Math.max(bar, shown[i] * h * 0.8);
          // at rest a faint line of dots; louder, brighter
          ctx.fillStyle = `rgba(255, 255, 255, ${0.22 + 0.4 * shown[i]})`;
          ctx.beginPath();
          ctx.roundRect(i * (bar + gap), (h - length) / 2, bar, length, bar / 2);
          ctx.fill();
        }
      }
      // paused and settled: nothing to draw until the music plays again
      if (playing || moving) frame = win.requestAnimationFrame(draw);
    };
    const wake = () => {
      if (!frame && player.state.get().playing) frame = win.requestAnimationFrame(draw);
    };
    frame = win.requestAnimationFrame(draw);
    const unsubscribe = player.state.subscribe(wake);
    // resized while settled: one frame at the new size (draw() matches the canvas to it)
    const resized = new win.ResizeObserver(() => {
      if (!frame) frame = win.requestAnimationFrame(draw);
    });
    resized.observe(el);
    return () => {
      unsubscribe();
      resized.disconnect();
      if (frame) win.cancelAnimationFrame(frame);
    };
  }, [look]);
  return (
    <div style={{ height, display: "flex", flexDirection: "column", justifyContent: "center", gap: 14 }}>
      <canvas ref={canvas} style={{ width: "100%", height: Math.min(look === "wave" ? 90 : 130, height * 0.4) }} />
    </div>
  );
};

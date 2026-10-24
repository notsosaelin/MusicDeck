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
/** Spokes round the record (half each side, mirrored), and the seconds it takes to turn once */
const SPOKES = 72;
const TURN_SECONDS = 9;

type Rgb = [number, number, number];
/** Steam's blue, for a record without a cover (or one whose colours can't be read) */
const ACCENT_RGB: Rgb = [26, 159, 255];
const rgba = ([r, g, b]: Rgb, a: number) => `rgba(${r}, ${g}, ${b}, ${a})`;

/** A colour at the given saturation and lightness, keeping its hue */
function withTone([r, g, b]: Rgb, saturation: number, lightness: number): Rgb {
  const max = Math.max(r, g, b) / 255;
  const min = Math.min(r, g, b) / 255;
  let hue = 0;
  if (max !== min) {
    const d = max - min;
    const [R, G, B] = [r / 255, g / 255, b / 255];
    hue = max === R ? (G - B) / d + (G < B ? 6 : 0) : max === G ? (B - R) / d + 2 : (R - G) / d + 4;
    hue /= 6;
  }
  const q = lightness < 0.5 ? lightness * (1 + saturation) : lightness + saturation - lightness * saturation;
  const p = 2 * lightness - q;
  const channel = (t: number) => {
    t = (t + 1) % 1;
    const v = t < 1 / 6 ? p + (q - p) * 6 * t : t < 1 / 2 ? q : t < 2 / 3 ? p + (q - p) * (2 / 3 - t) * 6 : p;
    return Math.round(v * 255);
  };
  return [channel(hue + 1 / 3), channel(hue), channel(hue - 1 / 3)];
}

/**
 * A cover's colour, vivid enough to glow on the dark panel: its pixels averaged, the most colourful
 * counting most. A cover with hardly any colour (black and white) gives a cool white.
 */
function coverColour(image: HTMLImageElement, doc: Document): Rgb {
  const small = doc.createElement("canvas");
  small.width = small.height = 16;
  const ctx = small.getContext("2d");
  if (!ctx) return ACCENT_RGB;
  let data: Uint8ClampedArray;
  try {
    ctx.drawImage(image, 0, 0, 16, 16);
    data = ctx.getImageData(0, 0, 16, 16).data;
  } catch {
    return ACCENT_RGB; // a picture from elsewhere that won't let its pixels be read
  }
  let [r, g, b, weight, colour] = [0, 0, 0, 0, 0];
  for (let i = 0; i < data.length; i += 4) {
    const spread = Math.max(data[i], data[i + 1], data[i + 2]) - Math.min(data[i], data[i + 1], data[i + 2]);
    const w = 1 + spread * spread;
    r += data[i] * w;
    g += data[i + 1] * w;
    b += data[i + 2] * w;
    weight += w;
    colour += spread;
  }
  if (colour / (data.length / 4) < 18) return [215, 225, 240];
  return withTone([r / weight, g / weight, b / weight], 0.75, 0.62);
}

/**
 * A quiet visualizer where a song's lyrics would be when it has none, from the music as it's heard.
 * "bars" (mirrored about the middle, low notes left, high right) where there's width, as full screen.
 * "record" where the space is square, as the Quick Access panel's: the cover as a record turning in the
 * middle, the music as spokes round it (the bass at the top, mirrored down both sides), in the cover's
 * colour, with a glow behind that swells with the bass. It settles when paused (the record slows to a
 * stop) and then stops drawing until the music plays again, or its size changes (a canvas left at its
 * old size is stretched, its resting dots into tall smears), and draws only while it shows. No line
 * under it: no lyrics, or none to be had right now, it just shows the music. `art`: the cover's address.
 */
export const Visualizer: FC<{ height: number; look?: "bars" | "record"; art?: string }> = ({
  height,
  look = "bars",
  art,
}) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  // the record's label (the cover) and its colour, as they load; and how to draw again when they do
  const label = useRef<{ image: HTMLImageElement | null; colour: Rgb }>({ image: null, colour: ACCENT_RGB });
  const redraw = useRef(() => {});
  useEffect(() => {
    const el = canvas.current;
    const win = el?.ownerDocument.defaultView;
    const ctx = el?.getContext("2d");
    if (!el || !win || !ctx) return;
    const bins = new Uint8Array(128);
    const shown = new Float32Array(look === "record" ? SPOKES / 2 : BARS);
    const usual = new Float32Array(shown.length); // each spoke's level of late, so a beat stands out from it
    let pulse = 0; // the bass, eased like the spokes
    let spin = 0; // the record's speed, 0 (still) to 1 (turning)
    let angle = 0;
    let frame = 0;
    let last = 0;
    const draw = (now: number) => {
      frame = 0;
      if (now - last < FRAME_MS) return void (frame = win.requestAnimationFrame(draw));
      const elapsed = last ? Math.min(now - last, 100) / 1000 : 0;
      last = now;
      const scale = win.devicePixelRatio || 1;
      const w = el.clientWidth;
      const h = el.clientHeight;
      if (el.width !== Math.round(w * scale) || el.height !== Math.round(h * scale)) {
        el.width = Math.round(w * scale);
        el.height = Math.round(h * scale);
      }
      const playing = player.state.get().playing;
      const heard = playing && player.levels(bins);
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      ctx.clearRect(0, 0, w, h);
      let moving = false;
      if (look === "record") {
        const { image, colour } = label.current;
        const size = Math.min(w, h);
        const [cx, cy] = [w / 2, h / 2];
        const outer = size / 2 - 4;
        const bass = heard ? (bins[1] + bins[2] + bins[3] + bins[4]) / (4 * 255) : 0;
        pulse = bass > pulse ? bass : pulse * 0.9;
        spin = playing ? Math.min(1, spin + elapsed * 1.5) : spin * 0.9;
        angle = (angle + (spin * elapsed * 2 * Math.PI) / TURN_SECONDS) % (2 * Math.PI);
        moving = pulse > 0.005 || spin > 0.005;
        const disc = size * 0.26 * (1 + 0.04 * pulse);
        // the glow behind, swelling with the bass
        const glow = ctx.createRadialGradient(cx, cy, disc * 0.8, cx, cy, outer);
        glow.addColorStop(0, rgba(colour, 0.16 + 0.3 * pulse));
        glow.addColorStop(1, rgba(colour, 0));
        ctx.fillStyle = glow;
        ctx.fillRect(0, 0, w, h);
        // the spokes: low notes at the top, higher down each side; at rest a ring of dots
        const half = shown.length;
        const start = disc + 8;
        const width = Math.max(2, ((2 * Math.PI * start) / SPOKES) * 0.42);
        ctx.lineCap = "round";
        ctx.lineWidth = width;
        for (let i = 0; i < half; i++) {
          const at = Math.floor(Math.pow(i / half, 1.5) * bins.length * 0.7);
          const level = heard ? Math.pow(bins[at] / 255, 1.3) : 0;
          usual[i] += (level - usual[i]) * 0.06;
          const target = Math.min(1, level * 0.8 + Math.max(0, level - usual[i]) * 1.6);
          shown[i] = target > shown[i] ? target : shown[i] * 0.88;
          if (shown[i] > 0.005) moving = true;
          const length = shown[i] * (outer - start);
          ctx.strokeStyle = rgba(colour, 0.3 + 0.7 * shown[i]);
          for (const side of [-1, 1]) {
            const a = -Math.PI / 2 + (side * (i + 0.5) * Math.PI) / half;
            const [dx, dy] = [Math.cos(a), Math.sin(a)];
            ctx.beginPath();
            ctx.moveTo(cx + dx * start, cy + dy * start);
            ctx.lineTo(cx + dx * (start + length + 0.01), cy + dy * (start + length + 0.01));
            ctx.stroke();
          }
        }
        // the record: the cover turning, a groove, the spindle hole
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(angle);
        ctx.beginPath();
        ctx.arc(0, 0, disc, 0, 2 * Math.PI);
        ctx.clip();
        if (image) ctx.drawImage(image, -disc, -disc, disc * 2, disc * 2);
        else {
          ctx.fillStyle = "#1b2129";
          ctx.fillRect(-disc, -disc, disc * 2, disc * 2);
          ctx.strokeStyle = rgba(colour, 0.35);
          ctx.lineWidth = 1;
          for (const r of [0.45, 0.62, 0.8]) {
            ctx.beginPath();
            ctx.arc(0, 0, disc * r, 0, 2 * Math.PI);
            ctx.stroke();
          }
        }
        ctx.restore();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = "rgba(255, 255, 255, 0.18)";
        ctx.beginPath();
        ctx.arc(cx, cy, disc, 0, 2 * Math.PI);
        ctx.stroke();
        ctx.fillStyle = "rgba(14, 20, 27, 0.9)";
        ctx.beginPath();
        ctx.arc(cx, cy, Math.max(3, disc * 0.09), 0, 2 * Math.PI);
        ctx.fill();
      } else {
        const gap = Math.max(3, w / BARS / 3);
        const bar = (w - gap * (BARS - 1)) / BARS;
        for (let i = 0; i < BARS; i++) {
          // the lower bins hold most of music; spread the bars over the lower three-quarters, more to the left
          const at = Math.floor(Math.pow(i / BARS, 1.6) * bins.length * 0.75);
          // read on the range the bars were made for (-100 to -30 dB; the analyser's is wider, for the record)
          const target = heard ? Math.min(1, ((bins[at] / 255) * 75 + 15) / 70) : 0;
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
      else last = 0;
    };
    const wake = () => {
      if (!frame && player.state.get().playing) frame = win.requestAnimationFrame(draw);
    };
    // once more, whatever's playing: resized, or the cover arrived
    redraw.current = () => {
      if (!frame) frame = win.requestAnimationFrame(draw);
    };
    frame = win.requestAnimationFrame(draw);
    const unsubscribe = player.state.subscribe(wake);
    const resized = new win.ResizeObserver(() => redraw.current());
    resized.observe(el);
    return () => {
      unsubscribe();
      resized.disconnect();
      redraw.current = () => {};
      if (frame) win.cancelAnimationFrame(frame);
    };
  }, [look]);
  // the record's label: the cover, and its colour once it's loaded (Steam's blue until then)
  useEffect(() => {
    const win = canvas.current?.ownerDocument.defaultView;
    label.current = { image: null, colour: ACCENT_RGB };
    redraw.current();
    if (look !== "record" || !art || !win) return;
    let gone = false;
    const image = new win.Image();
    image.crossOrigin = "anonymous"; // the stream server allows it, so its colours can be read
    image.onload = () => {
      if (gone) return;
      label.current = { image, colour: coverColour(image, win.document) };
      redraw.current();
    };
    image.src = art;
    return () => {
      gone = true;
    };
  }, [art, look]);
  return (
    <div style={{ height, display: "flex", flexDirection: "column", justifyContent: "center" }}>
      <canvas
        ref={canvas}
        style={{ width: "100%", height: look === "record" ? height : Math.min(130, height * 0.4) }}
      />
    </div>
  );
};

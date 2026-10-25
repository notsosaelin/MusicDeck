/**
 * The Music section's player: the bar along its bottom and the panels it opens beside the page (what's up
 * next, the lyrics), laid out as Spotify's desktop app has them: what's playing on the left, the controls
 * and the song's place in the middle, the views and volume on the right; the rarer things (the equalizer,
 * settings) under "...". As the window narrows, the volume's slider and then the rarer views give way.
 */
import { Menu, MenuItem, showContextMenu } from "@decky/ui";
import { FC, useEffect, useRef, useState } from "react";
import { FaBroadcastTower, FaCompress, FaEllipsisH, FaExpand, FaListUl, FaMicrophoneAlt, FaRandom, FaRedo, FaVolumeDown, FaVolumeMute, FaVolumeUp, FaWindowRestore } from "react-icons/fa";
import { canCast, prepareCastMenu, showCastMenu } from "../components/CastMenu";
import { Lyrics, getLyrics, setSetting } from "../api";
import { SHUFFLE_MODES, SHUFFLE_NOTES, updatePlayback } from "../components/Settings";
import { saveQueue } from "../components/TrackMenu";
import { player } from "../player";
import { mediaUrl, pluginState, useStore } from "../store";
import { desktopActions, go, panel, showPane, toggleFull } from "./data";
import { showEqualizer, showSettings } from "./polish";
import { Button, Controls, Cover, Seek, Slider, Titles, clock } from "./parts";

let volumeSave = 0;
/** the volume before muting, for unmuting */
let unmuted = 1;

/** the player's volume, kept in the settings once it stops changing */
export const setVolume = (v: number) => {
  player.setVolume(v);
  window.clearTimeout(volumeSave);
  volumeSave = window.setTimeout(() => setSetting("volume", v).catch(() => {}), 1000);
};

const Volume: FC = () => {
  const v = useStore(player.state).volume;
  const Icon = v === 0 ? FaVolumeMute : v < 0.5 ? FaVolumeDown : FaVolumeUp;
  return (
    <div className="md-volume">
      <Button
        label={v === 0 ? "Unmute" : "Mute"}
        onClick={() => {
          if (v > 0) unmuted = v;
          setVolume(v === 0 ? unmuted || 0.5 : 0);
        }}
      >
        <Icon />
      </Button>
      <Slider label="Volume" value={v} onInput={setVolume} className="md-volume-range" />
    </div>
  );
};

const shuffleName = () =>
  SHUFFLE_MODES.find(([id]) => id === (pluginState.get()?.playback.shuffle_mode ?? "songs"))?.[1].toLowerCase() ?? "songs";

/** Shuffle's style; choosing one turns shuffle on. */
const showShuffleMenu = (target: EventTarget) => {
  const mode = pluginState.get()?.playback.shuffle_mode ?? "songs";
  showContextMenu(
    <Menu label="Shuffle">
      {SHUFFLE_MODES.map(([id, label]) => (
        <MenuItem key={id} onSelected={async () => {
          await updatePlayback({ shuffle_mode: id });
          if (!player.state.get().shuffle) player.setShuffle(true);
        }}>
          {mode === id ? "✓ " : ""}
          {label}: {SHUFFLE_NOTES[id].toLowerCase()}
        </MenuItem>
      ))}
    </Menu>,
    target,
  );
};

const showMore = (target: EventTarget) =>
  showContextMenu(
    <Menu label="MusicDeck">
      <MenuItem onSelected={showEqualizer}>Equalizer…</MenuItem>
      <MenuItem onSelected={() => desktopActions.toggleMini()}>Mini player</MenuItem>
      <MenuItem onSelected={showSettings}>MusicDeck settings…</MenuItem>
    </Menu>,
    target,
  );

export const PlayerBar: FC = () => {
  const state = useStore(player.state);
  const view = useStore(panel);
  const item = player.current;
  const t = item?.track;
  // lit: what shows now, in whichever view
  const lit = (which: "queue" | "lyrics") => (view.full ? view.pane === which : view.open === which);
  const toAlbum = t?.album_id
    ? () => {
        toggleFull(false);
        go({ type: "collection", collection: { id: t.album_id!, kind: "album", title: t.album, subtitle: t.artist, art: t.art } });
      }
    : undefined;
  const toArtist = t?.artist_id
    ? () => {
        toggleFull(false);
        go({ type: "collection", collection: { id: t.artist_id!, kind: "artist", title: t.artist, subtitle: "", art: null } });
      }
    : undefined;
  const castable = canCast(item?.instanceId ?? pluginState.get()?.active_service ?? "");
  return (
    <div className="md-bar" data-full={view.full ? "" : undefined}>
      <div className="md-bar-left">
        {/* the cover opens the big Now Playing (and closes it) */}
        <div className="md-bar-cover" title={view.full ? "Leave Now Playing" : "Now Playing"} onClick={() => item && toggleFull()}>
          <Cover size={56} />
          {item && <div className="md-bar-cover-hint">{view.full ? <FaCompress /> : <FaExpand />}</div>}
        </div>
        <Titles onTitle={toAlbum} onArtist={toArtist} />
      </div>
      <div className="md-bar-middle" data-idle={item ? undefined : ""}>
        <div className="md-bar-controls">
          {/* right-click: how it shuffles (songs, albums, smart) */}
          <span onContextMenu={(e) => { e.preventDefault(); showShuffleMenu(e.nativeEvent as unknown as EventTarget); }}>
            <Button label={state.shuffle ? `Shuffle: on (${shuffleName()}) · right-click for the style` : "Shuffle · right-click for the style"}
                    on={state.shuffle} onClick={() => player.setShuffle(!state.shuffle)}>
              <FaRandom />
            </Button>
          </span>
          <Controls size={34} />
          <Button label={`Repeat: ${state.repeat}`} on={state.repeat !== "off"} onClick={() => player.cycleRepeat()}>
            <FaRedo />
            {state.repeat === "one" && <span className="md-badge">1</span>}
          </Button>
        </div>
        <Seek />
      </div>
      <div className="md-bar-right">
        <Button label="Lyrics" on={lit("lyrics")} onClick={() => showPane("lyrics")}>
          <FaMicrophoneAlt />
        </Button>
        <Button label="Queue" on={lit("queue")} onClick={() => showPane("queue")}>
          <FaListUl />
        </Button>
        {/* whenever the live source can play on a speaker or TV, playing or not (as Spotify's devices button) */}
        {castable && (
          <button
            className="md-ctl"
            data-on={state.castingTo ? "" : undefined}
            title={state.castingTo ? `Playing on ${state.castingTo.name}` : "Play on a speaker or TV"}
            aria-label="Play on"
            onMouseEnter={() => prepareCastMenu()}
            onClick={(e) => showCastMenu(e.currentTarget)}
          >
            <FaBroadcastTower />
          </button>
        )}
        <Volume />
        <Button label="Mini player" className="md-ctl md-bar-mini" onClick={() => desktopActions.toggleMini()}>
          <FaWindowRestore />
        </Button>
        <Button label={view.full ? "Leave Now Playing" : "Now Playing, big"} on={view.full} onClick={() => item && toggleFull()}>
          {view.full ? <FaCompress /> : <FaExpand />}
        </Button>
        <button className="md-ctl" title="More" aria-label="More" onClick={(e) => showMore(e.currentTarget)}>
          <FaEllipsisH />
        </button>
      </div>
    </div>
  );
};

export const SidePanel: FC = () => {
  const { open, full } = useStore(panel);
  if (!open || full) return null;
  return (
    <aside className="md-panel">
      <div className="md-panel-head">
        <span>{open === "queue" ? "Queue" : "Lyrics"}</span>
        <button className="md-ctl" title="Close" aria-label="Close" onClick={() => showPane(open)}>
          ✕
        </button>
      </div>
      <div className="md-panel-body">{open === "queue" ? <Queue /> : <LyricsPanel />}</div>
    </aside>
  );
};

/** Up next: what plays now, then the rest in the order it'll play; double-click to jump to one. */
export const Queue: FC<{ big?: boolean }> = ({ big }) => {
  const state = useStore(player.state);
  const order = player.playOrder;
  const at = order.indexOf(state.index);
  const next = at >= 0 ? order.slice(at + 1) : order;
  const row = (i: number, now = false) => {
    const q = state.queue[i];
    const t = q?.track;
    if (!t) return null;
    const art = (t.art && mediaUrl("art", q.instanceId, t.art)) || "";
    return (
      <div key={`${i}-${t.id}`} className="md-queue-row" data-big={big ? "" : undefined} data-now={now ? "" : undefined}
           title="Double-click to play" onDoubleClick={() => player.jumpTo(i)}>
        {art ? <img className="md-queue-art" src={art} loading="lazy" draggable={false} /> : <div className="md-queue-art md-noart" />}
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="md-td md-queue-title">{t.title}</div>
          <div className="md-td md-dim" style={{ fontSize: 12 }}>{t.artist}</div>
        </div>
        {!!t.duration && <span className="md-dim md-queue-time">{clock(t.duration)}</span>}
      </div>
    );
  };
  return (
    <>
      <div className="md-queue-heading">Now playing</div>
      {state.index >= 0 ? row(state.index, true) : <div className="md-dim">Nothing</div>}
      <div className="md-queue-heading md-queue-heading-row">
        <span>Next up{next.length ? ` · ${next.length}` : ""}</span>
        {state.index >= 0 && (
          <button className="md-text-button" onClick={(e) => saveQueue(e.currentTarget)}>
            Save as playlist
          </button>
        )}
      </div>
      {!next.length && <div className="md-dim" style={{ padding: "4px 8px" }}>Nothing after this song</div>}
      {next.slice(0, 200).map((i) => row(i))}
      {next.length > 200 && <div className="md-dim">and {next.length - 200} more</div>}
    </>
  );
};

export const LyricsPanel: FC<{ big?: boolean }> = ({ big }) => {
  useStore(player.state);
  const item = player.current;
  const { current } = useStore(player.position);
  const [lyrics, setLyrics] = useState<Lyrics | null | "none">(null);
  const key = item ? `${item.instanceId}/${item.track.id}` : "";
  useEffect(() => {
    setLyrics(null);
    if (!item) return;
    let live = true;
    getLyrics(item.instanceId, item.track)
      .then((r) => live && setLyrics(r.ok && r.data ? r.data : "none"))
      .catch(() => live && setLyrics("none"));
    return () => {
      live = false;
    };
  }, [key]);
  const box = useRef<HTMLDivElement>(null);
  const lines = lyrics && lyrics !== "none" ? lyrics.lines : [];
  let now = -1;
  if (lyrics && lyrics !== "none" && lyrics.synced)
    for (let i = 0; i < lines.length; i++) if (lines[i].t !== null && lines[i].t! <= current + 0.3) now = i;
  useEffect(() => {
    box.current?.querySelector(`[data-line="${now}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [now]);
  const quiet = (text: string) => <div className={big ? "md-lyrics-empty" : "md-dim"}>{text}</div>;
  if (!item) return quiet("Nothing playing");
  if (lyrics === null) return quiet("Looking for lyrics…");
  if (lyrics === "none" || !lines.length) return quiet("No lyrics for this song");
  return (
    <div ref={box} data-synced={lyrics.synced ? "" : undefined}>
      {lines.map((l, i) => (
        <div
          key={i}
          data-line={i}
          className={big ? "md-lyric md-lyric-big" : "md-lyric"}
          data-now={i === now ? "" : undefined}
          data-past={lyrics.synced && i < now ? "" : undefined}
          style={{ cursor: l.t !== null ? "pointer" : "default" }}
          onClick={() => l.t !== null && player.seek(l.t)}
        >
          {l.text || "♪"}
        </div>
      ))}
      {lyrics.source && <div className="md-dim" style={{ marginTop: 24, fontSize: 11 }}>Lyrics from {lyrics.source}</div>}
    </div>
  );
};

export const BAR_CSS = `
.md-bar { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(300px, 1.5fr) minmax(180px, 1fr); align-items: center;
  gap: 16px; height: 80px; padding: 0 16px; box-sizing: border-box; background: #10141a; border-top: 1px solid rgba(255,255,255,0.06); }
.md-bar[data-full] { background: rgba(0,0,0,0.45); border-top-color: transparent; }
.md-bar-left { display: flex; align-items: center; gap: 14px; min-width: 0; }
.md-bar-cover { position: relative; flex-shrink: 0; cursor: pointer; border-radius: 4px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.4); }
.md-bar-cover-hint { position: absolute; top: 4px; right: 4px; width: 22px; height: 22px; border-radius: 50%; display: flex;
  align-items: center; justify-content: center; font-size: 10px; background: rgba(0,0,0,0.7); color: #fff; opacity: 0; transition: opacity 0.15s; }
.md-bar-cover:hover .md-bar-cover-hint { opacity: 1; }
.md-bar-middle { display: flex; flex-direction: column; align-items: center; gap: 4px; min-width: 0; max-width: 720px; width: 100%; justify-self: center; }
.md-bar-middle[data-idle] { opacity: 0.45; pointer-events: none; }
.md-bar-controls { display: flex; align-items: center; gap: 10px; }
.md-bar-right { display: flex; align-items: center; justify-content: flex-end; gap: 2px; min-width: 0; }
.md-volume { display: flex; align-items: center; gap: 2px; margin: 0 4px; }
.md-volume-range { width: 96px; flex: none; }
.md-badge { position: absolute; right: 3px; top: 3px; font-size: 9px; font-weight: 700; }
@media (max-width: 1180px) { .md-volume-range { display: none; } }
@media (max-width: 980px) { .md-bar-mini { display: none; } .md-bar { grid-template-columns: minmax(140px, 1fr) minmax(260px, 1.6fr) auto; } }
.md-panel { width: 320px; min-width: 320px; display: flex; flex-direction: column; background: rgba(0,0,0,0.25);
  border-left: 1px solid rgba(255,255,255,0.06); box-sizing: border-box; }
.md-panel-head { display: flex; align-items: center; justify-content: space-between; padding: 14px 12px 6px 20px; color: #fff; font-size: 16px; font-weight: 600; }
.md-panel-body { flex: 1; overflow-y: auto; padding: 0 12px 20px 20px; }
.md-queue-heading { margin: 18px 0 6px; padding: 0 8px; font-size: 13px; font-weight: 600; color: #fff; }
.md-queue-heading:first-child { margin-top: 4px; }
.md-queue-heading-row { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.md-text-button { border: none; background: none; color: #8b929a; font: inherit; font-size: 12px; font-weight: 400; cursor: pointer; padding: 0; }
.md-text-button:hover { color: #fff; text-decoration: underline; }
.md-queue-row { display: flex; align-items: center; gap: 12px; padding: 6px 8px; border-radius: 4px; cursor: default; }
.md-queue-row:hover { background: rgba(255,255,255,0.07); }
.md-queue-art { width: 40px; height: 40px; min-width: 40px; border-radius: 3px; object-fit: cover; }
.md-queue-title { color: #dcdedf; }
.md-queue-row[data-now] .md-queue-title { color: #1a9fff; }
.md-queue-time { font-size: 12px; font-variant-numeric: tabular-nums; }
.md-queue-row[data-big] { padding: 8px 10px; }
.md-queue-row[data-big] .md-queue-art { width: 48px; height: 48px; min-width: 48px; }
.md-queue-row[data-big] .md-queue-title { font-size: 15px; }
.md-lyric { font-size: 16px; line-height: 1.7; color: #8b929a; transition: color 0.2s; }
.md-lyric[data-now] { color: #fff; }
.md-lyric-big { font-size: 30px; line-height: 1.35; font-weight: 700; padding: 8px 0; color: rgba(255,255,255,0.32); transition: color 0.25s; }
.md-lyric-big:hover { color: rgba(255,255,255,0.75); }
.md-lyric-big[data-past] { color: rgba(255,255,255,0.55); }
.md-lyric-big[data-now] { color: #fff; }
/* lyrics without times: nothing to follow, all of them read alike */
:not([data-synced]) > .md-lyric-big { color: rgba(255,255,255,0.85); }
:not([data-synced]) > .md-lyric { color: #dcdedf; }
.md-lyrics-empty { font-size: 22px; font-weight: 600; color: rgba(255,255,255,0.55); padding-top: 8px; }
`;

/**
 * The Music section's player: the bar along its bottom (what's playing, the controls, volume) and the
 * panels it opens beside the page (what's up next, the lyrics), as Spotify's desktop app has them.
 */
import { FC, useEffect, useRef, useState } from "react";
import { FaListUl, FaMicrophoneAlt, FaRandom, FaRedo, FaSlidersH, FaVolumeDown, FaVolumeMute, FaVolumeUp } from "react-icons/fa";
import { Lyrics, getLyrics, setSetting } from "../api";
import { player } from "../player";
import { useStore } from "../store";
import { go, panel } from "./data";
import { Visualizer, showEqualizer } from "./polish";
import { Controls, Cover, Seek, Titles } from "./parts";

let volumeSave = 0;

const Volume: FC = () => {
  const state = useStore(player.state);
  const v = Math.round(state.volume * 100);
  const Icon = v === 0 ? FaVolumeMute : v < 50 ? FaVolumeDown : FaVolumeUp;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <Icon style={{ opacity: 0.75, width: 14 }} />
      <input
        className="md-range"
        type="range"
        min={0}
        max={100}
        value={v}
        aria-label="Volume"
        onChange={(e) => {
          const next = Number(e.target.value) / 100;
          player.setVolume(next);
          window.clearTimeout(volumeSave);
          volumeSave = window.setTimeout(() => setSetting("volume", next).catch(() => {}), 1000);
        }}
      />
    </div>
  );
};

const Toggle: FC<{ label: string; on: boolean; onClick: () => void; children: any }> = ({ label, on, onClick, children }) => (
  <button className="md-icon" data-on={on ? "" : undefined} title={label} aria-label={label} onClick={onClick}>
    {children}
  </button>
);

export const PlayerBar: FC = () => {
  const state = useStore(player.state);
  const open = useStore(panel).open;
  const item = player.current;
  const flip = (which: "queue" | "lyrics") => panel.set({ open: open === which ? null : which });
  const toAlbum = () => {
    const t = item?.track;
    if (t?.album_id) go({ type: "collection", collection: { id: t.album_id, kind: "album", title: t.album, subtitle: t.artist, art: t.art } });
  };
  return (
    <div className="md-bar">
      <div className="md-bar-side" style={{ cursor: item?.track.album_id ? "pointer" : "default" }} onClick={toAlbum}>
        <Cover size={56} />
        <Titles />
      </div>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4, minWidth: 0, opacity: item ? 1 : 0.5 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <Toggle label="Shuffle" on={state.shuffle} onClick={() => player.setShuffle(!state.shuffle)}>
            <FaRandom />
          </Toggle>
          <Controls big />
          <Toggle label={`Repeat: ${state.repeat}`} on={state.repeat !== "off"} onClick={() => player.cycleRepeat()}>
            <FaRedo />
            {state.repeat === "one" && <span className="md-badge">1</span>}
          </Toggle>
        </div>
        <div style={{ width: "100%", maxWidth: 620 }}>
          <Seek />
        </div>
      </div>
      <div className="md-bar-side" style={{ justifyContent: "flex-end", gap: 10 }}>
        <Visualizer />
        <Toggle label="Equalizer" on={false} onClick={showEqualizer}>
          <FaSlidersH />
        </Toggle>
        <Toggle label="Lyrics" on={open === "lyrics"} onClick={() => flip("lyrics")}>
          <FaMicrophoneAlt />
        </Toggle>
        <Toggle label="Queue" on={open === "queue"} onClick={() => flip("queue")}>
          <FaListUl />
        </Toggle>
        <Volume />
      </div>
    </div>
  );
};

export const SidePanel: FC = () => {
  const open = useStore(panel).open;
  if (!open) return null;
  return <aside className="md-panel">{open === "queue" ? <Queue /> : <LyricsPanel />}</aside>;
};

const Queue: FC = () => {
  const state = useStore(player.state);
  const order = player.playOrder;
  const at = order.indexOf(state.index);
  const next = at >= 0 ? order.slice(at + 1) : order;
  const row = (i: number, now = false) => {
    const t = state.queue[i]?.track;
    if (!t) return null;
    return (
      <div key={`${i}-${t.id}`} className="md-queue-row" data-now={now ? "" : undefined} onDoubleClick={() => player.jumpTo(i)}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="md-td" style={{ color: now ? "#1a9fff" : "#dcdedf" }}>{t.title}</div>
          <div className="md-td md-dim" style={{ fontSize: 12 }}>{t.artist}</div>
        </div>
      </div>
    );
  };
  return (
    <>
      <div className="md-heading" style={{ marginTop: 0 }}>Now playing</div>
      {state.index >= 0 ? row(state.index, true) : <div className="md-dim">Nothing</div>}
      <div className="md-heading">Next up · {next.length}</div>
      {next.slice(0, 200).map((i) => row(i))}
      {next.length > 200 && <div className="md-dim">and {next.length - 200} more</div>}
    </>
  );
};

const LyricsPanel: FC = () => {
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
  if (!item) return <div className="md-dim">Nothing playing</div>;
  if (lyrics === null) return <div className="md-dim">Looking for lyrics…</div>;
  if (lyrics === "none" || !lines.length) return <div className="md-dim">No lyrics for this song</div>;
  return (
    <div ref={box}>
      <div className="md-heading" style={{ marginTop: 0 }}>{lyrics.title || "Lyrics"}</div>
      {lines.map((l, i) => (
        <div
          key={i}
          data-line={i}
          className="md-lyric"
          data-now={i === now ? "" : undefined}
          style={{ cursor: l.t !== null ? "pointer" : "default" }}
          onClick={() => l.t !== null && player.seek(l.t)}
        >
          {l.text || " "}
        </div>
      ))}
      {lyrics.source && <div className="md-dim" style={{ marginTop: 16, fontSize: 11 }}>From {lyrics.source}</div>}
    </div>
  );
};

export const BAR_CSS = `
.md-bar { display: flex; align-items: center; gap: 16px; padding: 10px 20px; background: rgba(0,0,0,0.4);
  border-top: 1px solid rgba(255,255,255,0.06); }
.md-bar-side { display: flex; align-items: center; gap: 12px; width: 300px; min-width: 0; }
.md-icon { position: relative; display: flex; align-items: center; justify-content: center; width: 30px; height: 30px; border: none;
  background: none; color: #8b929a; cursor: pointer; border-radius: 2px; font-size: 13px; }
.md-icon:hover { color: #fff; }
.md-icon[data-on] { color: #1a9fff; }
.md-badge { position: absolute; right: 2px; bottom: 2px; font-size: 9px; }
.md-range { width: 110px; accent-color: #1a9fff; }
.md-panel { width: 320px; min-width: 320px; padding: 20px; overflow-y: auto; background: rgba(0,0,0,0.25);
  border-left: 1px solid rgba(255,255,255,0.06); box-sizing: border-box; }
.md-queue-row { display: flex; padding: 6px 8px; border-radius: 2px; cursor: default; }
.md-queue-row:hover { background: rgba(255,255,255,0.05); }
.md-lyric { font-size: 16px; line-height: 1.7; color: #8b929a; transition: color 0.2s; }
.md-lyric[data-now] { color: #fff; }
`;

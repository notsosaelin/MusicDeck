/**
 * What MusicDeck draws in desktop Steam: the Music section (MusicView, in the main window under the top
 * navigation: the library on the left and the page, the player along the bottom) and the mini player
 * (MiniPlayer, in a small window of its own).
 */
import { FC, useEffect, useRef, useState } from "react";
import { FaExternalLinkAlt, FaRandom, FaThumbtack, FaTimes } from "react-icons/fa";
import { player } from "../player";
import { useStore } from "../store";
import { BAR_CSS, PlayerBar, SidePanel } from "./bar";
import { LIBRARY_CSS, Pages, Sidebar } from "./library";
import { panel } from "./data";
import { fileDrag } from "./manage";
import { Backdrop, NOW_PLAYING_CSS, NowPlayingFull } from "./nowplaying";
import { Button, Controls, Cover, PARTS_CSS, Seek, Titles } from "./parts";
import { POLISH_CSS } from "./polish";

/** The mini player's look, by its window's shape: the cover filling it (square or tall, as Spotify's mini
 *  player is), or a strip of cover, names and controls (short and wide). */
const MINI_CSS = `
.md-mini { position: relative; height: 100%; overflow: hidden; color: #dcdedf; background: #10141a; }
.md-mini-art { position: absolute; inset: 0; }
.md-mini-art > * { width: 100% !important; height: 100% !important; min-width: 0 !important; border-radius: 0 !important; }
.md-mini-shade { position: absolute; inset: 0; pointer-events: none; transition: background 0.2s;
  background: linear-gradient(to bottom, rgba(0,0,0,0) 45%, rgba(0,0,0,0.8) 100%); }
.md-mini:hover .md-mini-shade { background: linear-gradient(to bottom, rgba(0,0,0,0.45) 0%, rgba(0,0,0,0.55) 45%, rgba(0,0,0,0.85) 100%); }
.md-mini-info { position: absolute; left: 14px; right: 14px; bottom: 14px; display: flex; }
.md-mini-controls { position: absolute; left: 0; right: 0; top: 50%; transform: translateY(-50%); display: flex; align-items: center;
  justify-content: center; gap: 10px; opacity: 0; transition: opacity 0.15s; }
.md-mini-tools { position: absolute; top: 6px; right: 6px; display: flex; gap: 2px; opacity: 0; transition: opacity 0.15s; }
.md-mini:hover .md-mini-controls, .md-mini:hover .md-mini-tools { opacity: 1; }
.md-mini .md-ctl { color: #dcdedf; }
.md-mini .md-ctl:hover { color: #fff; }
.md-mini-tools .md-ctl { width: 28px; height: 28px; font-size: 12px; background: rgba(0,0,0,0.35); }
.md-mini-seek { position: absolute; left: 0; right: 0; bottom: -6px; }
.md-mini-seek .md-slider-track { border-radius: 0; height: 3px; }
.md-mini:not(:hover) .md-mini-seek .md-slider-fill { background: #fff; }

.md-mini[data-mode="strip"] { display: flex; align-items: center; }
.md-mini[data-mode="strip"] .md-mini-art { position: relative; inset: auto; height: 100%; aspect-ratio: 1; flex-shrink: 0; }
.md-mini[data-mode="strip"] .md-mini-shade { display: none; }
.md-mini[data-mode="strip"] .md-mini-info { position: static; flex: 1; min-width: 0; padding: 0 10px 0 12px; }
.md-mini[data-mode="strip"] .md-mini-controls { position: static; transform: none; opacity: 1; gap: 2px; padding-right: 10px; }
.md-mini[data-mode="strip"] .md-mini-tools { background: #10141a; border-radius: 14px; top: 4px; right: 4px; }
.md-mini[data-mode="strip"] .md-mini-tools .md-ctl { background: none; }
.md-mini[data-mode="strip"] .md-mini-seek { left: var(--md-mini-art); }

.md-mini[data-mode="tall"] { display: flex; flex-direction: column; }
.md-mini[data-mode="tall"] .md-mini-art { position: relative; inset: auto; width: 100%; aspect-ratio: 1; flex-shrink: 0; }
.md-mini[data-mode="tall"] .md-mini-shade { display: none; }
.md-mini[data-mode="tall"] .md-mini-info { position: static; padding: 14px 16px 0; }
.md-mini[data-mode="tall"] .md-mini-controls { position: static; transform: none; opacity: 1; flex: 1; }
`;

/** Styles our parts use, in whichever document they're drawn (the main window's, or the mini player's). */
export const BASE_CSS = PARTS_CSS + LIBRARY_CSS + BAR_CSS + POLISH_CSS + NOW_PLAYING_CSS + MINI_CSS;

/** The Music section. Music files dragged in from the desktop go into Local files (the drag itself is caught by
 *  catchDrops, before Steam's window can refuse it). While the big Now Playing shows, the library steps aside
 *  and the cover's blur lies behind it all. */
export const MusicView: FC = () => {
  const dropping = useStore(fileDrag).over;
  const full = useStore(panel).full;
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", color: "#dcdedf", position: "relative" }}>
      {full && <Backdrop />}
      <div style={{ flex: 1, display: "flex", minHeight: 0, position: "relative" }}>
        {!full && <Sidebar />}
        {full ? <NowPlayingFull /> : <Pages />}
        <SidePanel />
      </div>
      <div style={{ position: "relative" }}>
        <PlayerBar />
      </div>
      {dropping && <div className="md-drop">Drop to add to Local files</div>}
    </div>
  );
};

type MiniMode = "art" | "strip" | "tall";
/** short or wide: a strip; much taller than wide: the cover square on top, the song below; else the cover fills it */
const modeFor = (w: number, h: number): MiniMode => (h < 150 || w > h * 2 ? "strip" : h > w * 1.25 ? "tall" : "art");

/** The mini player: what's playing, in a small window dragged anywhere and sized at will. */
export const MiniPlayer: FC<{ onClose: () => void; onOpenMusic: () => void; onTop: boolean; onPin: () => void }> = ({
  onClose,
  onOpenMusic,
  onTop,
  onPin,
}) => {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<[number, number]>([300, 300]);
  const shuffle = useStore(player.state).shuffle;
  useEffect(() => {
    const win = box.current?.ownerDocument.defaultView;
    if (!win) return;
    const measure = () => setSize([win.innerWidth, win.innerHeight]);
    measure();
    win.addEventListener("resize", measure);
    return () => win.removeEventListener("resize", measure);
  }, []);
  const [w, h] = size;
  const mode = modeFor(w, h);
  // the controls grow with the window, within reason
  const play = mode === "strip" ? Math.min(36, Math.max(28, h * 0.4)) : Math.min(64, Math.max(40, Math.min(w, h) * 0.18));
  return (
    <div ref={box} className="md-mini" data-mode={mode} style={{ ["--md-mini-art" as any]: `${h}px` }}>
      <div className="md-mini-art" onDoubleClick={onOpenMusic} title="Double-click: open Music">
        <Cover size="100%" />
      </div>
      <div className="md-mini-shade" />
      <div className="md-mini-info">
        <Titles size={mode === "strip" ? 13 : Math.min(18, Math.max(14, w / 20))} />
      </div>
      <div className="md-mini-controls">
        {mode !== "strip" && w >= 240 && (
          <Button label={shuffle ? "Shuffle: on" : "Shuffle"} on={shuffle} onClick={() => player.setShuffle(!shuffle)}>
            <FaRandom />
          </Button>
        )}
        <Controls size={play} />
      </div>
      <div className="md-mini-tools">
        <Button label="Open Music" onClick={onOpenMusic}>
          <FaExternalLinkAlt />
        </Button>
        <Button label={onTop ? "Stop keeping on top" : "Keep on top"} on={onTop} onClick={onPin}>
          <FaThumbtack />
        </Button>
        <Button label="Close" onClick={onClose}>
          <FaTimes />
        </Button>
      </div>
      <div className="md-mini-seek">
        <Seek times={false} />
      </div>
    </div>
  );
};

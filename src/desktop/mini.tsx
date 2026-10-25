/**
 * The mini player: what's playing in a small window of its own, dragged anywhere, in one of three shapes the
 * window snaps to (miniShape) so the cover always shows whole, as Spotify's mini player has them:
 *   square  the cover fills it; the song and a thin progress bar over its foot; under the mouse, the controls in
 *           the middle, the window's buttons and volume along the top, and the bar with its times
 *   card    the cover on top; below it the song (and volume), the progress bar with its times, the controls
 *   strip   the cover at the left, the song over its progress bar, the controls, volume and "..." at the right
 * Volume is a button that opens a slider (the wheel over it, or anywhere over the player, also sets it).
 */
import { FC, ReactNode, useEffect, useRef, useState } from "react";
import { FaEllipsisH, FaExternalLinkAlt, FaRandom, FaRedo, FaThumbtack, FaTimes, FaVolumeDown, FaVolumeMute, FaVolumeUp } from "react-icons/fa";
import { player } from "../player";
import { useStore } from "../store";
import { setVolume } from "./bar";
import { Button, Controls, Cover, Seek, Slider, Titles } from "./parts";

export type MiniMode = "square" | "card" | "strip";
/** what's below the cover in the card: the song, the progress bar, the controls */
const CARD_BELOW = 168;
/** a strip is at most this tall */
const STRIP_MAX = 140;
export const MINI_SMALLEST = { width: 240, height: 84 };

const modeFor = (w: number, h: number): MiniMode => (h <= STRIP_MAX || w >= h * 2 ? "strip" : h > w * 1.12 ? "card" : "square");

/** The size nearest (w, h) at which the cover shows whole: a square, a card (the cover above the song), or a
 *  strip (any width; the cover is its height). The window is snapped to it once it's been resized. */
export function miniShape(w: number, h: number): [number, number] {
  const mode = modeFor(w, h);
  if (mode === "strip") return [Math.max(w, 320), Math.max(MINI_SMALLEST.height, Math.min(h, STRIP_MAX))];
  if (mode === "square") {
    const side = Math.max(240, Math.round((w + h) / 2));
    return [side, side];
  }
  return [Math.max(w, 240), Math.max(w, 240) + CARD_BELOW];
}

const nudgeVolume = (deltaY: number) =>
  setVolume(Math.max(0, Math.min(1, Math.round((player.state.get().volume - Math.sign(deltaY) * 0.05) * 100) / 100)));

/** The volume slider itself: the speaker (mute), the line, the percentage. */
const VolumeSlider: FC<{ percent?: boolean }> = ({ percent = true }) => {
  const v = useStore(player.state).volume;
  const Icon = v === 0 ? FaVolumeMute : v < 0.5 ? FaVolumeDown : FaVolumeUp;
  return (
    <div className="mm-vol-slider">
      <Button label={v === 0 ? "Unmute" : "Mute"} onClick={() => setVolume(v === 0 ? 0.5 : 0)}>
        <Icon />
      </Button>
      <Slider label="Volume" value={v} onInput={setVolume} />
      {percent && <span className="mm-vol-pct">{Math.round(v * 100)}</span>}
    </div>
  );
};

/** Closes `open` on a press anywhere outside `box`, or Escape. */
function useDismiss(open: boolean, close: () => void, box: { current: HTMLElement | null }) {
  useEffect(() => {
    if (!open) return;
    const doc = box.current?.ownerDocument;
    const away = (e: Event) => !box.current?.contains(e.target as Node) && close();
    const key = (e: KeyboardEvent) => e.key === "Escape" && close();
    doc?.addEventListener("pointerdown", away);
    doc?.addEventListener("keydown", key);
    return () => {
      doc?.removeEventListener("pointerdown", away);
      doc?.removeEventListener("keydown", key);
    };
  }, [open]);
}

/** The volume button. Pressed, the slider opens in a bubble beside it (`inPlace` false), or the caller shows it in
 *  place of something (the strip: instead of its controls), told through onOpen. */
const Volume: FC<{ below?: boolean; open: boolean; onOpen: (open: boolean) => void; inPlace?: boolean }> = ({ below, open, onOpen, inPlace }) => {
  const v = useStore(player.state).volume;
  const box = useRef<HTMLDivElement>(null);
  useDismiss(open && !inPlace, () => onOpen(false), box);
  const Icon = v === 0 ? FaVolumeMute : v < 0.5 ? FaVolumeDown : FaVolumeUp;
  return (
    <div ref={box} className="mm-vol" data-open={open ? "" : undefined} onWheel={(e) => { e.stopPropagation(); nudgeVolume(e.deltaY); }}>
      <Button label={`Volume ${Math.round(v * 100)}%`} on={open} onClick={() => onOpen(!open)}>
        <Icon />
      </Button>
      {open && !inPlace && (
        <div className="mm-vol-bubble" data-below={below ? "" : undefined}>
          <VolumeSlider />
        </div>
      )}
    </div>
  );
};

/** Shuffle, previous, play, next, repeat, the play button larger: each in a slot as wide as the play button's,
 *  so their centres are evenly spaced whatever each icon's size. */
const Transport: FC<{ play: number; ends: boolean; gap: number }> = ({ play, ends, gap }) => {
  const state = useStore(player.state);
  return (
    <div className="mm-transport" style={{ gridTemplateColumns: `repeat(${ends ? 5 : 3}, ${play + gap}px)` }}>
      {ends && (
        <Button label={state.shuffle ? "Shuffle: on" : "Shuffle"} on={state.shuffle} onClick={() => player.setShuffle(!state.shuffle)}>
          <FaRandom />
        </Button>
      )}
      <Controls size={play} />
      {ends && (
        <Button label={`Repeat: ${state.repeat}`} on={state.repeat !== "off"} onClick={() => player.cycleRepeat()}>
          <FaRedo />
          {state.repeat === "one" && <span className="md-badge">1</span>}
        </Button>
      )}
    </div>
  );
};

export const MiniPlayer: FC<{ onClose: () => void; onOpenMusic: () => void; onTop: boolean; onPin: () => void }> = ({
  onClose,
  onOpenMusic,
  onTop,
  onPin,
}) => {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<[number, number]>([300, 300]);
  const [menu, setMenu] = useState<[number, number] | null>(null);
  const [volume, setVolumeOpen] = useState(false);
  // the strip's volume, open in place of its controls: closed by a press outside both
  const stripEnd = useRef<HTMLDivElement>(null);
  const stripVolume = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!volume || !stripVolume.current) return;
    const doc = stripVolume.current.ownerDocument;
    const inside = (n: Node) => !!stripVolume.current?.contains(n) || !!stripEnd.current?.querySelector(".mm-vol")?.contains(n);
    const away = (e: Event) => !inside(e.target as Node) && setVolumeOpen(false);
    const key = (e: KeyboardEvent) => e.key === "Escape" && setVolumeOpen(false);
    doc.addEventListener("pointerdown", away);
    doc.addEventListener("keydown", key);
    return () => {
      doc.removeEventListener("pointerdown", away);
      doc.removeEventListener("keydown", key);
    };
  }, [volume, size]);
  useEffect(() => {
    const win = box.current?.ownerDocument.defaultView;
    if (!win) return;
    const measure = () => setSize([win.innerWidth, win.innerHeight]);
    measure();
    win.addEventListener("resize", measure);
    const close = () => setMenu(null);
    win.addEventListener("blur", close);
    return () => {
      win.removeEventListener("resize", measure);
      win.removeEventListener("blur", close);
    };
  }, []);
  const [w, h] = size;
  const mode = modeFor(w, h);
  const pick = (act: () => void) => () => {
    setMenu(null);
    act();
  };
  const openMenu = (x: number, y: number) => setMenu([Math.max(4, Math.min(x, w - 184)), Math.max(4, Math.min(y, h - 118))]);
  const tools: ReactNode = (
    <>
      <Button label="Open Music" onClick={onOpenMusic}>
        <FaExternalLinkAlt />
      </Button>
      <Button label={onTop ? "Stop keeping on top" : "Keep on top"} on={onTop} onClick={onPin}>
        <FaThumbtack />
      </Button>
      <Button label="Close" onClick={onClose}>
        <FaTimes />
      </Button>
    </>
  );
  // the play button and the room for shuffle and repeat, by the space there is
  const play = mode === "strip" ? 36 : Math.round(Math.min(56, Math.max(42, w * 0.15)));
  const stripRoom = w - h;
  const transportWidth = (stripRoom >= 470 ? 5 : 3) * (play + 4);
  return (
    <div
      ref={box}
      className="mm"
      data-mode={mode}
      onContextMenu={(e) => {
        e.preventDefault();
        openMenu(e.clientX, e.clientY);
      }}
      onPointerDown={(e) => menu && !(e.target as Element).closest(".mm-menu") && setMenu(null)}
      onWheel={(e) => nudgeVolume(e.deltaY)}
    >
      <div className="mm-art" onDoubleClick={onOpenMusic} title="Double-click: open Music · right-click: options">
        <Cover size="100%" />
      </div>

      {mode === "square" && (
        <>
          <div className="mm-shade" />
          <div className="mm-top">
            <Volume below open={volume} onOpen={setVolumeOpen} />
            <div style={{ flex: 1 }} />
            {tools}
          </div>
          <div className="mm-middle">
            <Transport play={play} ends={w >= 260} gap={Math.round(w / 28)} />
          </div>
          <div className="mm-foot">
            <Titles size={Math.round(Math.min(17, Math.max(14, w / 22)))} />
            <div className="mm-bar">
              <Seek />
            </div>
          </div>
        </>
      )}

      {mode === "card" && (
        <div className="mm-below">
          <div className="mm-row">
            <Titles size={15} />
            <Volume open={volume} onOpen={setVolumeOpen} />
          </div>
          <div className="mm-bar">
            <Seek />
          </div>
          <Transport play={play} ends={w >= 250} gap={Math.round(w / 28)} />
          <div className="mm-card-tools">{tools}</div>
        </div>
      )}

      {mode === "strip" && (
        <>
          <div className="mm-strip-text">
            <Titles size={13} />
            <div className="mm-bar">
              <Seek />
            </div>
          </div>
          {volume ? (
            // exactly the controls' width, so the song's column keeps its own
            <div ref={stripVolume} className="mm-strip-volume" style={{ width: transportWidth }}>
              <VolumeSlider percent={transportWidth >= 200} />
            </div>
          ) : (
            <Transport play={play} ends={stripRoom >= 470} gap={4} />
          )}
          <div ref={stripEnd} className="mm-strip-end">
            <Volume open={volume} onOpen={setVolumeOpen} inPlace />
            <button className="md-ctl" title="Options" aria-label="Options" onClick={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              openMenu(r.right - 184, r.bottom);
            }}>
              <FaEllipsisH />
            </button>
          </div>
        </>
      )}

      {menu && (
        <div className="mm-menu" style={{ left: menu[0], top: menu[1] }}>
          <div onClick={pick(onOpenMusic)}>Open Music</div>
          <div onClick={pick(onPin)}>{onTop ? "Stop keeping on top" : "Keep on top"}</div>
          <div onClick={pick(onClose)}>Close the mini player</div>
        </div>
      )}
    </div>
  );
};

export const MINI_CSS = `
.mm { position: relative; height: 100%; overflow: hidden; color: #dcdedf; background: #10141a; }
.mm .md-ctl { color: #dcdedf; width: 34px; height: 34px; font-size: 15px; }
.mm .md-ctl:hover { color: #fff; }
.mm-art > * { width: 100% !important; height: 100% !important; min-width: 0 !important; border-radius: 0 !important; }
.mm-transport { display: grid; justify-items: center; align-items: center; justify-content: center; }
/* the progress bar: inset, rounded, its times either side (from Seek), as a bar and never an edge */
.mm-bar .md-seekrow { gap: 8px; font-size: 11px; color: rgba(255,255,255,0.7); }
.mm-bar .md-seekrow > span { min-width: 30px; }

/* square: the cover is the window */
.mm[data-mode="square"] .mm-art { position: absolute; inset: 0; }
.mm-shade { position: absolute; inset: 0; pointer-events: none; transition: background 0.2s;
  background: linear-gradient(to bottom, rgba(0,0,0,0) 55%, rgba(0,0,0,0.75) 100%); }
.mm:hover .mm-shade { background: linear-gradient(to bottom, rgba(0,0,0,0.55), rgba(0,0,0,0.45) 40%, rgba(0,0,0,0.85)); }
.mm-top { position: absolute; top: 8px; left: 8px; right: 8px; display: flex; align-items: center; gap: 4px;
  opacity: 0; transition: opacity 0.15s; }
.mm-top .md-ctl { width: 30px; height: 30px; font-size: 13px; background: rgba(0,0,0,0.45); }
.mm-middle { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; opacity: 0;
  transition: opacity 0.15s; pointer-events: none; }
.mm-middle > * { pointer-events: auto; }
.mm:hover .mm-top, .mm:hover .mm-middle, .mm-top:has([data-open]) { opacity: 1; }
.mm-foot { position: absolute; left: 16px; right: 16px; bottom: 12px; display: flex; flex-direction: column; gap: 6px; }
/* at rest only the bar's line shows, thin; under the mouse the times and a thicker line */
.mm[data-mode="square"]:not(:hover) .mm-bar .md-seekrow > span { display: none; }
.mm[data-mode="square"]:not(:hover) .mm-bar .md-slider-track { height: 3px; }
.mm[data-mode="square"]:not(:hover) .mm-bar .md-slider-fill { background: #fff; }

/* card: the cover on top, the rest below it */
.mm[data-mode="card"] { display: flex; flex-direction: column; }
.mm[data-mode="card"] .mm-art { width: 100%; aspect-ratio: 1; flex-shrink: 0; }
.mm-below { flex: 1; display: flex; flex-direction: column; justify-content: center; gap: 8px; padding: 12px 16px 14px; }
.mm-row { display: flex; align-items: center; gap: 8px; }
.mm-card-tools { position: absolute; top: 8px; right: 8px; display: flex; gap: 4px; opacity: 0; transition: opacity 0.15s; }
.mm-card-tools .md-ctl { width: 30px; height: 30px; font-size: 13px; background: rgba(0,0,0,0.45); }
.mm:hover .mm-card-tools { opacity: 1; }

/* strip: cover, the song over its bar, the controls, volume and options */
.mm[data-mode="strip"] { display: flex; align-items: center; gap: 14px; padding-right: 8px; }
.mm[data-mode="strip"] .mm-art { height: 100%; aspect-ratio: 1; flex-shrink: 0; }
.mm-strip-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; container-type: inline-size; }
@container (max-width: 170px) { .mm-strip-text .md-seekrow > span { display: none; } }
.mm-strip-end { display: flex; align-items: center; }
.mm[data-mode="strip"] .mm-bar .md-seekrow > span { min-width: 26px; font-size: 10px; }

/* volume: a button, and its slider in a bubble */
.mm-vol { position: relative; }
.mm-vol-bubble { position: absolute; right: 0; bottom: calc(100% + 6px); z-index: 5;
  padding: 4px 12px 4px 4px; border-radius: 18px; background: #2a3039; box-shadow: 0 6px 18px rgba(0,0,0,0.6); }
.mm-vol-bubble[data-below] { left: 0; right: auto; top: calc(100% + 6px); bottom: auto; }
.mm-vol-bubble .md-ctl { background: none !important; }
.mm-vol-pct { min-width: 24px; font-size: 11px; color: #b8bcbf; text-align: right; font-variant-numeric: tabular-nums; }
.mm-vol-slider { display: flex; align-items: center; gap: 6px; }
.mm-vol-slider .md-slider { width: 110px; flex: none; }
/* in a strip it takes the controls' place (nothing covered, nothing pushed out) until a press elsewhere */
.mm-strip-volume { display: flex; align-items: center; box-sizing: border-box; padding: 0 12px 0 2px; height: 40px; border-radius: 20px;
  background: rgba(255,255,255,0.07); }
.mm-strip-volume .mm-vol-slider { flex: 1; min-width: 0; }
.mm-strip-volume .md-slider { width: auto; flex: 1; }

.mm-menu { position: fixed; z-index: 10; min-width: 180px; padding: 4px 0; background: #3d4450; border-radius: 3px;
  box-shadow: 0 6px 18px rgba(0,0,0,0.6); font-size: 13px; }
.mm-menu div { padding: 8px 14px; cursor: default; white-space: nowrap; }
.mm-menu div:hover { background: #dcdedf; color: #171d25; }
`;

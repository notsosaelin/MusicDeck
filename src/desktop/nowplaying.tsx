/**
 * The big Now Playing of desktop Steam's Music section, laid out as Apple Music's and Spotify's full-screen
 * players are: the library steps aside and the whole section (the player bar too) sits on a blur of the
 * cover; along the top, a way back and what shows beside the cover (the lyrics, up next, or the art alone);
 * the cover, the song and a visualizer on the left, that view on the right. The bar's Lyrics and Queue switch
 * it too; the bar's cover, the chevron or Escape leave it.
 */
import { FC, useEffect, useRef } from "react";
import { FaChevronDown } from "react-icons/fa";
import { player } from "../player";
import { useStore } from "../store";
import { LyricsPanel, Queue } from "./bar";
import { Pane, go, panel, toggleFull } from "./data";
import { ArtOrIcon } from "./library";
import { coverUrl } from "./parts";
import { Visualizer } from "./polish";

const PANES: [Pane | "art", string][] = [
  ["lyrics", "Lyrics"],
  ["queue", "Up next"],
  ["art", "Art"],
];

/** The cover's blur behind the whole section while the big Now Playing shows. */
export const Backdrop: FC = () => {
  useStore(player.state);
  const cover = coverUrl();
  return (
    <div className="md-np-backdrop-box" aria-hidden>
      {cover && <div className="md-np-backdrop" style={{ backgroundImage: `url("${cover}")` }} />}
    </div>
  );
};

export const NowPlayingFull: FC = () => {
  useStore(player.state);
  const { pane } = useStore(panel);
  const item = player.current;
  const t = item?.track;
  const box = useRef<HTMLDivElement>(null);
  // Escape leaves it (the shortcuts handle the rest of the keyboard)
  useEffect(() => {
    const win = box.current?.ownerDocument.defaultView;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") toggleFull(false);
    };
    win?.addEventListener("keydown", key);
    return () => win?.removeEventListener("keydown", key);
  }, []);
  // nothing playing any more: back to the page
  useEffect(() => {
    if (!item) toggleFull(false);
  }, [item]);
  const leaveFor = (open: () => void) => () => {
    toggleFull(false);
    open();
  };
  const toAlbum = t?.album_id
    ? leaveFor(() => go({ type: "collection", collection: { id: t.album_id!, kind: "album", title: t.album, subtitle: t.artist, art: t.art } }))
    : undefined;
  const toArtist = t?.artist_id
    ? leaveFor(() => go({ type: "collection", collection: { id: t.artist_id!, kind: "artist", title: t.artist, subtitle: "", art: null } }))
    : undefined;
  return (
    <div ref={box} className="md-np" data-pane={pane}>
      <div className="md-np-top">
        <button className="md-np-hide" title="Leave Now Playing (Esc)" onClick={() => toggleFull(false)}>
          <FaChevronDown /> <span>Hide</span>
        </button>
        {/* dots, the chosen one a white pill, its name beside them */}
        <div className="md-dots" role="tablist">
          <span className="md-dots-label">{PANES.find(([id]) => id === pane)?.[1]}</span>
          {PANES.map(([id, label]) => (
            <button key={id} className="md-dot" role="tab" title={label} aria-label={label} aria-selected={pane === id}
                    data-on={pane === id ? "" : undefined} onClick={() => panel.set({ ...panel.get(), pane: id })}>
              <span />
            </button>
          ))}
        </div>
      </div>
      <div className="md-np-body">
        <div className="md-np-side">
          <div className="md-np-art">
            <ArtOrIcon url={coverUrl()} />
          </div>
          <div className="md-np-title" title={t?.title}>{t?.title ?? "Nothing playing"}</div>
          <div className="md-np-sub">
            <span className={toArtist ? "md-link" : undefined} onClick={toArtist}>{t?.artist}</span>
            {t?.album && (
              <>
                <span className="md-np-dot">·</span>
                <span className={toAlbum ? "md-link" : undefined} onClick={toAlbum}>{t.album}</span>
              </>
            )}
          </div>
          <div className="md-np-vis">
            <Visualizer width={260} height={28} />
          </div>
        </div>
        {pane !== "art" && (
          <div className="md-np-pane">
            <div className="md-np-scroll">{pane === "queue" ? <Queue big /> : <LyricsPanel big />}</div>
          </div>
        )}
      </div>
    </div>
  );
};

export const NOW_PLAYING_CSS = `
.md-np-backdrop-box { position: absolute; inset: 0; overflow: hidden; z-index: 0; background: #10141a; }
.md-np-backdrop { position: absolute; inset: -80px; background-size: cover; background-position: center;
  filter: blur(70px) brightness(0.4) saturate(1.4); }
.md-np { position: relative; z-index: 1; flex: 1; min-width: 0; display: flex; flex-direction: column; }
.md-np-top { display: flex; align-items: center; justify-content: space-between; padding: 16px 28px 0; }
.md-np-hide { display: flex; align-items: center; gap: 8px; height: 32px; padding: 0 14px 0 10px; border: none; border-radius: 16px;
  background: rgba(0,0,0,0.25); color: #dcdedf; font: inherit; font-size: 13px; cursor: pointer; }
.md-np-hide:hover { background: rgba(0,0,0,0.45); color: #fff; }
.md-dots { display: flex; align-items: center; height: 32px; padding: 0 10px 0 14px; border-radius: 16px; background: rgba(0,0,0,0.25); }
.md-dots-label { font-size: 13px; color: #dcdedf; margin-right: 8px; }
.md-dot { display: flex; align-items: center; height: 32px; padding: 0 4px; border: none; background: none; cursor: pointer; }
.md-dot > span { display: block; width: 9px; height: 9px; border-radius: 5px; background: rgba(255,255,255,0.28);
  transition: width 0.22s ease, background 0.15s, transform 0.15s; }
.md-dot:hover > span, .md-dot:focus-visible > span { background: rgba(255,255,255,0.65); transform: scale(1.15); }
.md-dot[data-on] > span { width: 26px; background: #fff; transform: none; }
.md-dot:focus { outline: none; }
.md-np-body { flex: 1; min-height: 0; display: flex; gap: clamp(32px, 5vw, 80px); padding: 20px clamp(28px, 5vw, 80px) 28px;
  box-sizing: border-box; container-type: size; }
.md-np[data-pane="art"] .md-np-body { justify-content: center; }
.md-np-side { display: flex; flex-direction: column; justify-content: center; min-width: 0;
  width: min(100cqh - 130px, 40cqw, 560px); flex-shrink: 0; }
.md-np[data-pane="art"] .md-np-side { width: min(100cqh - 130px, 80cqw, 720px); align-items: center; text-align: center; }
.md-np-side > * { flex-shrink: 0; }
.md-np-art { width: 100%; aspect-ratio: 1; border-radius: 8px; overflow: hidden; box-shadow: 0 20px 60px rgba(0,0,0,0.55); }
.md-np-art .md-noart svg { width: 30%; height: 30%; }
.md-np-title { font-size: clamp(22px, 2.4vw, 34px); color: #fff; margin-top: 24px; font-weight: 700; line-height: 1.25; padding-bottom: 2px; max-width: 100%;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.md-np-sub { display: flex; gap: 8px; font-size: 16px; color: #b8bcbf; margin-top: 6px; min-width: 0; max-width: 100%; white-space: nowrap; }
.md-np-sub > span { overflow: hidden; text-overflow: ellipsis; }
.md-np[data-pane="art"] .md-np-sub { justify-content: center; }
.md-np-dot { flex-shrink: 0; }
.md-np-vis { margin-top: 18px; opacity: 0.8; height: 28px; }
.md-np-pane { flex: 1; min-width: 0; display: flex; flex-direction: column; max-width: 760px; }
/* a short fade at the foot only, and room after the last line to scroll it clear of the fade */
.md-np-scroll { flex: 1; overflow-y: auto; padding: 8px 16px 48px 0; scrollbar-width: thin; scrollbar-color: rgba(255,255,255,0.18) transparent;
  mask-image: linear-gradient(to bottom, #000 0, #000 calc(100% - 32px), transparent 100%); }
@container (max-height: 520px) { .md-np-vis { display: none; } .md-np-title { margin-top: 14px; } }
`;

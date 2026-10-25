/**
 * The full Now Playing view of desktop Steam's Music section, in the page's place (as Spotify's desktop app
 * has it): what's playing big, over a blur of its art, beside the lyrics, the queue, or only the art. The
 * player bar stays below it; the bar's Lyrics and Queue switch its pane, its cover or Escape leave it.
 */
import { FC, useEffect, useRef } from "react";
import { player } from "../player";
import { mediaUrl, useStore } from "../store";
import { LyricsPanel, Queue } from "./bar";
import { Pane, go, panel, toggleFull } from "./data";
import { ArtOrIcon } from "./library";

const PANES: [Pane | "art", string][] = [
  ["lyrics", "Lyrics"],
  ["queue", "Up next"],
  ["art", "Art"],
];

export const NowPlayingFull: FC = () => {
  useStore(player.state);
  const { pane } = useStore(panel);
  const item = player.current;
  const t = item?.track;
  const cover = (item && t?.art && mediaUrl("art", item.instanceId, t.art)) || "";
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
  const toAlbum = () => {
    if (t?.album_id) {
      toggleFull(false);
      go({ type: "collection", collection: { id: t.album_id, kind: "album", title: t.album, subtitle: t.artist, art: t.art } });
    }
  };
  return (
    <div ref={box} className="md-np">
      {cover && <div className="md-np-backdrop" style={{ backgroundImage: `url("${cover}")` }} />}
      <div className="md-np-body" data-pane={pane}>
        <div className="md-np-side">
          <div className="md-np-art">
            <ArtOrIcon url={cover} />
          </div>
          <div className="md-np-title">{t?.title ?? "Nothing playing"}</div>
          <div className="md-np-sub">
            <span>{t?.artist}</span>
            {t?.album && (
              <>
                {" · "}
                <span className="md-np-link" onClick={toAlbum}>
                  {t.album}
                </span>
              </>
            )}
          </div>
        </div>
        {pane !== "art" && (
          <div className="md-np-pane">
            <div className="md-np-tabs">
              {PANES.map(([id, label]) => (
                <button key={id} data-on={pane === id ? "" : undefined} onClick={() => panel.set({ ...panel.get(), pane: id })}>
                  {label}
                </button>
              ))}
            </div>
            <div className="md-np-scroll">{pane === "queue" ? <Queue big /> : <LyricsPanel big />}</div>
          </div>
        )}
        {pane === "art" && (
          <div className="md-np-tabs md-np-tabs-float">
            {PANES.map(([id, label]) => (
              <button key={id} data-on={pane === id ? "" : undefined} onClick={() => panel.set({ ...panel.get(), pane: id })}>
                {label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export const NOW_PLAYING_CSS = `
.md-np { position: relative; flex: 1; min-width: 0; overflow: hidden; }
.md-np-backdrop { position: absolute; inset: -60px; background-size: cover; background-position: center;
  filter: blur(60px) brightness(0.35) saturate(1.3); transform: scale(1.1); }
.md-np-body { position: relative; height: 100%; display: flex; gap: 56px; padding: 40px 56px; box-sizing: border-box; align-items: center; }
.md-np-body[data-pane="art"] { justify-content: center; }
.md-np-side { display: flex; flex-direction: column; min-width: 0; max-width: min(46vh, 480px); flex-shrink: 0; }
.md-np-body[data-pane="art"] .md-np-side { max-width: min(62vh, 640px); align-items: center; text-align: center; }
.md-np-art { width: min(46vh, 480px); aspect-ratio: 1; box-shadow: 0 18px 50px rgba(0,0,0,0.6); border-radius: 6px; overflow: hidden; }
.md-np-body[data-pane="art"] .md-np-art { width: min(62vh, 640px); }
.md-np-art .md-noart svg { width: 30%; height: 30%; }
.md-np-title { font-size: 34px; color: #fff; margin-top: 22px; font-weight: 600; line-height: 1.15;
  overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.md-np-sub { font-size: 17px; color: #b8bcbf; margin-top: 8px; }
.md-np-link { cursor: pointer; }
.md-np-link:hover { color: #fff; text-decoration: underline; }
.md-np-pane { flex: 1; min-width: 0; height: 100%; display: flex; flex-direction: column; }
.md-np-tabs { display: flex; gap: 6px; margin-bottom: 14px; }
.md-np-tabs button { border: none; background: rgba(255,255,255,0.08); color: #b8bcbf; padding: 7px 16px; border-radius: 16px;
  font: inherit; font-size: 13px; cursor: pointer; }
.md-np-tabs button:hover { background: rgba(255,255,255,0.16); color: #fff; }
.md-np-tabs button[data-on] { background: #dcdedf; color: #171d25; }
.md-np-tabs-float { position: absolute; top: 24px; right: 32px; }
.md-np-scroll { flex: 1; overflow-y: auto; padding-right: 12px; mask-image: linear-gradient(to bottom, transparent 0, #000 6%, #000 90%, transparent 100%); }
`;

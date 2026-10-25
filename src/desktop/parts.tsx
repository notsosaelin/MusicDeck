/**
 * The small pieces desktop Steam's Music section and mini player share: what's playing (its cover and
 * names), the transport buttons, the seek bar and a slider. They follow the player's stores, as the Quick
 * Access Menu and the media center do, so every view shows the same music.
 */
import { CSSProperties, FC, PointerEvent, useState } from "react";
import { FaMusic, FaPause, FaPlay, FaStepBackward, FaStepForward } from "react-icons/fa";
import { Track } from "../api";
import { player } from "../player";
import { rateTrack } from "../ratings";
import { mediaUrl, useStore } from "../store";

export const clock = (s: number) => {
  const t = Math.max(0, Math.floor(s || 0));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
};

/** What's playing's cover link, or "" */
export function coverUrl(): string {
  const item = player.current;
  return (item?.track.art && mediaUrl("art", item.instanceId, item.track.art)) || "";
}

/** What's playing's cover, or a quiet square where there's none. */
export const Cover: FC<{ size: number | string }> = ({ size }) => {
  useStore(player.state);
  const url = coverUrl();
  const [failed, setFailed] = useState("");
  const box: CSSProperties = { width: size, height: size, minWidth: size, borderRadius: 4, display: "block" };
  if (url && failed !== url)
    return <img src={url} style={{ ...box, objectFit: "cover" }} draggable={false} onError={() => setFailed(url)} />;
  return (
    <div className="md-noart" style={box}>
      <FaMusic style={{ width: "40%", height: "40%" }} />
    </div>
  );
};

/** A bare icon button (the player's: no box, lit when on). */
export const Button: FC<{ label: string; onClick: () => void; on?: boolean; className?: string; children: any }> = ({
  label,
  onClick,
  on,
  className,
  children,
}) => (
  <button
    className={className ?? "md-ctl"}
    data-on={on ? "" : undefined}
    title={label}
    aria-label={label}
    onClick={(e) => {
      e.stopPropagation();
      onClick();
    }}
  >
    {children}
  </button>
);

/** Previous, play/pause (a white disc, as the players people know have it) and next. */
export const Controls: FC<{ size?: number }> = ({ size = 32 }) => {
  const state = useStore(player.state);
  return (
    <>
      <Button label="Previous" onClick={() => player.previous()}>
        <FaStepBackward />
      </Button>
      <button
        className="md-ctl-main"
        title={state.playing ? "Pause" : "Play"}
        aria-label={state.playing ? "Pause" : "Play"}
        style={{ width: size, height: size, fontSize: size * 0.4 }}
        onClick={(e) => {
          e.stopPropagation();
          player.toggle();
        }}
      >
        {/* the play triangle sits a little right of centre, as the eye expects */}
        {state.playing ? <FaPause /> : <FaPlay style={{ marginLeft: size * 0.06 }} />}
      </button>
      <Button label="Next" onClick={() => player.next()}>
        <FaStepForward />
      </Button>
    </>
  );
};

/** A thin line to drag along (seek, volume): white, blue with a knob under the mouse. `value` 0 to 1;
 *  onInput as it's dragged, onCommit when let go. */
export const Slider: FC<{
  value: number;
  label: string;
  onInput?: (fraction: number) => void;
  onCommit?: (fraction: number) => void;
  disabled?: boolean;
  className?: string;
}> = ({ value, label, onInput, onCommit, disabled, className }) => {
  const [dragging, setDragging] = useState<number | null>(null);
  const at = (e: PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
  };
  const shown = dragging ?? value;
  return (
    <div
      className={`md-slider ${className ?? ""}`}
      data-disabled={disabled ? "" : undefined}
      data-dragging={dragging !== null ? "" : undefined}
      role="slider"
      aria-label={label}
      aria-valuenow={Math.round(shown * 100)}
      onPointerDown={(e) => {
        if (disabled) return;
        e.stopPropagation();
        e.currentTarget.setPointerCapture(e.pointerId);
        const f = at(e);
        setDragging(f);
        onInput?.(f);
      }}
      onPointerMove={(e) => {
        if (dragging === null) return;
        const f = at(e);
        setDragging(f);
        onInput?.(f);
      }}
      onPointerUp={(e) => {
        if (dragging === null) return;
        const f = at(e);
        setDragging(null);
        onCommit?.(f);
      }}
      onPointerCancel={() => setDragging(null)}
    >
      <div className="md-slider-track">
        <div className="md-slider-fill" style={{ width: `${shown * 100}%` }} />
      </div>
      <div className="md-slider-knob" style={{ left: `${shown * 100}%` }} />
    </div>
  );
};

/** The song's place in it, the times either side (`times` false: the line alone, as the mini player's edge). */
export const Seek: FC<{ times?: boolean }> = ({ times = true }) => {
  const { current, duration } = useStore(player.position);
  useStore(player.state);
  const track = player.current?.track;
  // a stream with no end (radio) says LIVE; nothing loaded says nothing
  const live = !!track?.live;
  const fixed = !!track && !live && duration > 0;
  const [held, setHeld] = useState<number | null>(null);
  const fraction = fixed ? Math.min(1, current / duration) : 0;
  const line = (
    <Slider
      label="Seek"
      value={fraction}
      disabled={!fixed}
      onInput={(f) => setHeld(f * duration)}
      onCommit={(f) => {
        setHeld(null);
        player.seek(f * duration);
      }}
      className="md-seek"
    />
  );
  if (!times) return line;
  return (
    <div className="md-seekrow">
      <span style={{ textAlign: "right" }}>{track ? clock(held ?? current) : ""}</span>
      {line}
      <span>{live ? "LIVE" : fixed ? clock(duration) : ""}</span>
    </div>
  );
};

export const Titles: FC<{ size?: number; onTitle?: () => void; onArtist?: () => void }> = ({ size = 14, onTitle, onArtist }) => {
  useStore(player.state);
  const track = player.current?.track;
  const line: CSSProperties = { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };
  return (
    <div style={{ minWidth: 0, flex: 1 }}>
      <div style={{ ...line, fontSize: size, color: "#fff" }}>
        <span className={onTitle && track ? "md-link" : undefined} onClick={track ? onTitle : undefined}>
          {track?.title ?? "Nothing playing"}
        </span>
      </div>
      <div style={{ ...line, fontSize: size - 2, color: "#8b929a", marginTop: 2 }}>
        {track ? (
          <span className={onArtist ? "md-link" : undefined} onClick={onArtist}>
            {track.artist}
          </span>
        ) : (
          "Play something from your music"
        )}
      </div>
    </div>
  );
};

export const PARTS_CSS = `
.md-button { display: flex; align-items: center; justify-content: center; border: none; border-radius: 2px;
  background: rgba(255,255,255,0.08); color: #dcdedf; cursor: pointer; padding: 0; }
.md-button:hover { background: rgba(255,255,255,0.18); color: #fff; }
.md-ctl { position: relative; display: flex; align-items: center; justify-content: center; width: 32px; height: 32px; padding: 0;
  border: none; border-radius: 50%; background: none; color: #b8bcbf; cursor: pointer; font-size: 15px; flex-shrink: 0; }
.md-ctl:hover { color: #fff; }
.md-ctl[data-on] { color: #1a9fff; }
.md-ctl[data-on]::after { content: ""; position: absolute; bottom: 1px; left: 50%; width: 4px; height: 4px; margin-left: -2px;
  border-radius: 50%; background: #1a9fff; }
.md-ctl-main { display: flex; align-items: center; justify-content: center; padding: 0; border: none; border-radius: 50%;
  background: #fff; color: #10141a; cursor: pointer; flex-shrink: 0; transition: transform 0.1s; }
.md-ctl-main:hover { transform: scale(1.06); }
.md-slider { position: relative; height: 16px; display: flex; align-items: center; cursor: pointer; flex: 1; min-width: 0; touch-action: none; }
.md-slider[data-disabled] { cursor: default; }
.md-slider-track { flex: 1; height: 4px; border-radius: 2px; background: rgba(255,255,255,0.22); overflow: hidden; }
.md-slider-fill { height: 100%; background: #dcdedf; border-radius: 2px; }
.md-slider:hover .md-slider-fill, .md-slider[data-dragging] .md-slider-fill { background: #1a9fff; }
.md-slider-knob { position: absolute; top: 50%; width: 12px; height: 12px; margin: -6px 0 0 -6px; border-radius: 50%; background: #fff;
  box-shadow: 0 1px 4px rgba(0,0,0,0.5); opacity: 0; pointer-events: none; }
.md-slider:hover .md-slider-knob, .md-slider[data-dragging] .md-slider-knob { opacity: 1; }
.md-slider[data-disabled] .md-slider-knob { opacity: 0; }
.md-seekrow { display: flex; align-items: center; gap: 8px; width: 100%; font-size: 11px; color: #8b929a; font-variant-numeric: tabular-nums; }
.md-seekrow > span { min-width: 36px; }
.md-link { cursor: pointer; }
.md-link:hover { text-decoration: underline; color: #fff; }
.md-nav-item { padding: 6px 16px; color: #b8bcbf; font-size: 14px; cursor: default; border-radius: 2px; }
.md-nav-item[data-current] { color: #fff; background: rgba(255,255,255,0.08); }
.md-noart { display: flex; align-items: center; justify-content: center; color: rgba(255,255,255,0.35);
  background: linear-gradient(135deg, #2a3240 0%, #1b2129 100%); }
.md-nav-heading { padding: 14px 16px 4px; color: #8b929a; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; }
`;

/** A song's stars: shown dim where unset, lit to the mouse's on hover; a click rates it (its own rating again
 *  clears it). Only for sources that keep ratings (ratings.ts). */
export const Stars: FC<{ instanceId: string; track: Track; size?: number }> = ({ instanceId, track, size = 13 }) => {
  const [hover, setHover] = useState(0);
  const shown = hover || track.rating || 0;
  return (
    <span className="md-stars" data-rated={track.rating ? "" : undefined} onMouseLeave={() => setHover(0)}
          onDoubleClick={(e) => e.stopPropagation()}>
      {[1, 2, 3, 4, 5].map((n) => (
        <span
          key={n}
          className="md-star"
          data-on={n <= shown ? "" : undefined}
          data-preview={hover ? "" : undefined}
          style={{ fontSize: size }}
          title={track.rating === n ? "Clear the rating" : `${n} star${n > 1 ? "s" : ""}`}
          onMouseEnter={() => setHover(n)}
          onClick={(e) => {
            e.stopPropagation();
            rateTrack(instanceId, track, n);
          }}
        >
          ★
        </span>
      ))}
    </span>
  );
};

export const STARS_CSS = `
.md-stars { display: inline-flex; gap: 1px; cursor: pointer; line-height: 1; }
.md-star { color: rgba(255,255,255,0.16); transition: color 0.1s; }
.md-star[data-on] { color: #dcdedf; }
.md-star[data-on][data-preview] { color: #1a9fff; }
/* in a table, unrated songs' stars only show under the mouse */
.md-row:not(:hover) .md-stars:not([data-rated]) { visibility: hidden; }
`;

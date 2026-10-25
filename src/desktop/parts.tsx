/**
 * The small pieces desktop Steam's Music section and mini player share: what's playing (its cover and
 * names), the transport buttons and the seek bar. They follow the player's stores, as the Quick Access
 * Menu and the media center do, so every view shows the same music.
 */
import { CSSProperties, FC, useState } from "react";
import { FaBackward, FaForward, FaMusic, FaPause, FaPlay } from "react-icons/fa";
import { player } from "../player";
import { mediaUrl, useStore } from "../store";

const clock = (s: number) => {
  const t = Math.max(0, Math.floor(s || 0));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
};

/** What's playing's cover, or a quiet square where there's none. */
export const Cover: FC<{ size: number }> = ({ size }) => {
  useStore(player.state);
  const item = player.current;
  const url = (item?.track.art && mediaUrl("art", item.instanceId, item.track.art)) || "";
  const [failed, setFailed] = useState("");
  const box: CSSProperties = { width: size, height: size, minWidth: size, borderRadius: 3 };
  if (url && failed !== url)
    return <img src={url} style={{ ...box, objectFit: "cover" }} draggable={false} onError={() => setFailed(url)} />;
  return (
    <div className="md-noart" style={{ ...box, fontSize: size * 0.4 }}>
      <FaMusic />
    </div>
  );
};

export const Button: FC<{ label: string; onClick: () => void; big?: boolean; children: any }> = ({ label, onClick, big, children }) => (
  <button
    className="md-button"
    title={label}
    aria-label={label}
    onClick={(e) => {
      e.stopPropagation();
      onClick();
    }}
    style={{ width: big ? 34 : 28, height: big ? 34 : 28, fontSize: big ? 14 : 12 }}
  >
    {children}
  </button>
);

export const Controls: FC<{ big?: boolean }> = ({ big }) => {
  const state = useStore(player.state);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <Button label="Previous" onClick={() => player.previous()}>
        <FaBackward />
      </Button>
      <Button label={state.playing ? "Pause" : "Play"} onClick={() => player.toggle()} big={big}>
        {state.playing ? <FaPause /> : <FaPlay />}
      </Button>
      <Button label="Next" onClick={() => player.next()}>
        <FaForward />
      </Button>
    </div>
  );
};

/** The song's place in it; click to seek. */
export const Seek: FC = () => {
  const { current, duration } = useStore(player.position);
  useStore(player.state);
  const track = player.current?.track;
  // a stream with no end (radio) says LIVE; nothing loaded says nothing
  const live = !!track?.live;
  const fixed = !!track && !live && duration > 0;
  const fraction = fixed ? Math.min(1, current / duration) : 0;
  const seekAt = (e: { currentTarget: HTMLDivElement; clientX: number }) => {
    const r = e.currentTarget.getBoundingClientRect();
    if (fixed) player.seek(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * duration);
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "#8b929a" }}>
      <span style={{ minWidth: 32, textAlign: "right" }}>{track ? clock(current) : ""}</span>
      <div
        className="md-seek"
        onPointerDown={(e) => {
          e.stopPropagation();
          seekAt(e);
        }}
        style={{ flex: 1, height: 14, display: "flex", alignItems: "center", cursor: fixed ? "pointer" : "default" }}
      >
        <div style={{ flex: 1, height: 3, borderRadius: 2, background: "rgba(255,255,255,0.15)" }}>
          <div style={{ width: `${fraction * 100}%`, height: "100%", borderRadius: 2, background: "#1a9fff" }} />
        </div>
      </div>
      <span style={{ minWidth: 32 }}>{live ? "LIVE" : fixed ? clock(duration) : ""}</span>
    </div>
  );
};

export const Titles: FC<{ size?: number }> = ({ size = 14 }) => {
  useStore(player.state);
  const track = player.current?.track;
  const line: CSSProperties = { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };
  return (
    <div style={{ minWidth: 0, flex: 1 }}>
      <div style={{ ...line, fontSize: size, color: "#fff" }}>{track?.title ?? "Nothing playing"}</div>
      <div style={{ ...line, fontSize: size - 2, color: "#8b929a" }}>
        {track ? [track.artist, track.album].filter(Boolean).join(" — ") : "Play something from your music"}
      </div>
    </div>
  );
};

export const PARTS_CSS = `
.md-button { display: flex; align-items: center; justify-content: center; border: none; border-radius: 2px;
  background: rgba(255,255,255,0.08); color: #dcdedf; cursor: pointer; padding: 0; }
.md-button:hover { background: rgba(255,255,255,0.18); color: #fff; }
.md-nav-item { padding: 6px 16px; color: #b8bcbf; font-size: 14px; cursor: default; border-radius: 2px; }
.md-nav-item[data-current] { color: #fff; background: rgba(255,255,255,0.08); }
.md-noart { display: flex; align-items: center; justify-content: center; color: rgba(255,255,255,0.35);
  background: linear-gradient(135deg, #2a3240 0%, #1b2129 100%); }
.md-nav-heading { padding: 14px 16px 4px; color: #8b929a; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; }
`;

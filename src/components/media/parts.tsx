import { Focusable, Spinner, gamepadDialogClasses } from "@decky/ui";
import { FC, ReactNode, RefObject, useEffect, useState } from "react";
import { FaCompactDisc, FaFolder, FaList, FaUser } from "react-icons/fa";
import { Collection, Result, Track } from "../../api";
import { player } from "../../player";
import { mediaUrl, pluginState, useStore } from "../../store";
import { ACCENT, ScrollingText, formatTime } from "../common";

/**
 * The media center's look, in Steam's own terms: capsules that grow and glow under the controller
 * (as on Steam's home page), list rows with the library's focus fill, and plain text buttons that
 * light up like Steam's. Class names are prefixed so nothing of Steam's is touched.
 */
export const CENTER_CSS =
  `
.mc-card { border-radius: 6px; transition: transform 0.12s ease-out; outline: none; }
.mc-card .mc-art { border-radius: 6px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.45); transition: box-shadow 0.12s; }
.mc-card.gpfocus, .mc-card:hover { transform: scale(1.06); }
.mc-card.gpfocus .mc-art, .mc-card:hover .mc-art { box-shadow: 0 0 0 3px #ffffff, 0 10px 28px rgba(0, 0, 0, 0.6); }
.mc-row { border-radius: 4px; outline: none; }
.mc-row.gpfocus, .mc-row:hover { background: rgba(255, 255, 255, 0.12); }
.mc-row.gpfocus { box-shadow: inset 0 0 0 2px rgba(255, 255, 255, 0.85); }
.mc-shelf::-webkit-scrollbar { display: none; }
.mc-tile { border-radius: 6px; outline: none; transition: transform 0.12s ease-out; }
.mc-tile.gpfocus, .mc-tile:hover { transform: scale(1.04); box-shadow: 0 0 0 3px #ffffff, 0 10px 28px rgba(0, 0, 0, 0.55); }
` + fieldCss(gamepadDialogClasses as Record<string, string> | undefined);

/**
 * Steam's settings fields (dropdowns, toggles, sliders) on the media center's page rather than in the
 * grey boxes Steam's settings pages give them: no box at rest, a soft one when focused; a slider shows
 * focus on its knob instead. Steam's class names are hashed and change between versions; Decky
 * keeps the map from their names (Field, Background), so these follow along.
 */
function fieldCss(dialog: Record<string, string> | undefined): string {
  const field = dialog?.Field && dialog?.Background ? `.${dialog.Field}.${dialog.Background}` : "";
  return `
${field ? `.mc-center ${field} { background: transparent !important; }` : ""}
${field ? `.mc-center ${field}.gpfocuswithin, .mc-center ${field}.gpfocus { background: rgba(255, 255, 255, 0.07) !important; border-radius: 6px; }` : ""}
${field ? `.mc-center ${field}:has([class*="SliderControlPanelGroup"]), .mc-center ${field}.gpfocuswithin:has([class*="SliderControlPanelGroup"]) { background: transparent !important; }` : ""}
${field ? `.mc-center ${field}:has([class*="SliderControlPanelGroup"])::before, .mc-center ${field}:has([class*="SliderControlPanelGroup"])::after { display: none !important; }` : ""}
.mc-center [class*="SliderControlPanelGroup"] { background: transparent !important; }
.mc-center [class*="SliderControlPanelGroup"].gpfocus [class~="SliderHandle"] { box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.35); }
`;
}

/**
 * A button handler that keeps the press to itself. Steam passes gamepad buttons on up the page, and
 * the page behind the media center has an X of its own ("Change artwork").
 */
export const stopping =
  (handler: (e?: any) => void) =>
  (e?: any): void => {
    e?.stopPropagation?.();
    handler(e);
  };

/** A shelf card's width by the Customize choice: about eight, six or four across, whatever the screen and Steam's UI scale */
// ...and never taller than leaves a whole row (cover, title, subtitle) on screen above the now-playing
// bar, however short the screen or large Steam's UI scale
const CARD_SIZES = {
  small: "min(clamp(80px, 10.5vw, 150px), 24vh)",
  medium: "min(clamp(96px, 14vw, 190px), 28vh)",
  large: "min(clamp(120px, 20vw, 260px), 32vh)",
};
const useCardSize = () => CARD_SIZES[useStore(pluginState)?.center?.cards ?? "medium"] ?? CARD_SIZES.medium;

const KIND_ICON: Record<Collection["kind"], ReactNode> = {
  album: <FaCompactDisc />,
  playlist: <FaList />,
  artist: <FaUser />,
  folder: <FaFolder />,
  list: <FaList />,
};

/** A collection's picture (round for artists), or its kind's icon when it has none. */
export const Cover: FC<{ instanceId: string; c: Pick<Collection, "art" | "kind">; size: number | string }> = ({
  instanceId,
  c,
  size,
}) => {
  const [failed, setFailed] = useState(false);
  const url = c.art ? mediaUrl("art", instanceId, c.art) : undefined;
  const round = c.kind === "artist";
  return (
    <div
      className="mc-art"
      style={{
        width: size,
        height: size,
        borderRadius: round ? "50%" : 6,
        background: "rgba(255, 255, 255, 0.08)",
        overflow: "hidden",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: typeof size === "number" ? size * 0.32 : `calc(${size} * 0.32)`,
        color: "rgba(255, 255, 255, 0.45)",
        flexShrink: 0,
      }}
    >
      {url && !failed ? (
        <img src={url} style={{ width: "100%", height: "100%", objectFit: "cover" }} onError={() => setFailed(true)} />
      ) : (
        KIND_ICON[c.kind]
      )}
    </div>
  );
};

/** One collection on a shelf: A opens it, X plays it (albums and playlists), Y its menu. */
export const Card: FC<{
  instanceId: string;
  c: Collection;
  onOpen: () => void;
  onPlay?: () => void;
  onOptions?: (e: any) => void;
}> = ({ instanceId, c, onOpen, onPlay, onOptions }) => {
  const [focused, setFocused] = useState(false);
  const size = useCardSize();
  return (
    <Focusable
      className="mc-card"
      data-mc={c.kind + ":" + c.id}
      noFocusRing
      onActivate={onOpen}
      onClick={onOpen}
      onOKActionDescription="Open"
      onSecondaryButton={onPlay && stopping(onPlay)}
      onSecondaryActionDescription={onPlay ? "Play" : undefined}
      onOptionsButton={onOptions}
      onOptionsActionDescription={onOptions ? "Options" : undefined}
      onGamepadFocus={() => setFocused(true)}
      onGamepadBlur={() => setFocused(false)}
      style={{ width: size, flexShrink: 0, padding: 2 }}
    >
      <Cover instanceId={instanceId} c={c} size={size} />
      <div style={{ marginTop: 10, textAlign: c.kind === "artist" ? "center" : "left" }}>
        <ScrollingText text={c.title} play={focused} style={{ fontSize: 14, fontWeight: 600, color: "#ffffff" }} />
        {c.subtitle && (
          <ScrollingText text={c.subtitle} play={focused} style={{ fontSize: 12, opacity: 0.6, marginTop: 2 }} />
        )}
      </div>
    </Focusable>
  );
};

/** A titled row that scrolls sideways, as on Steam's home page. */
export const Shelf: FC<{ title?: string; children: ReactNode }> = ({ title, children }) => (
  <div style={{ marginBottom: 28 }}>
    {title && <SectionTitle>{title}</SectionTitle>}
    <Focusable
      flow-children="row"
      className="mc-shelf"
      // room for a focused card to grow without being clipped
      // to the screen's edges, as Steam's own rows: cards scroll off the edge rather than being cut at
      // the page's margin; the padding is room for a focused card to grow
      style={{ display: "flex", gap: 22, overflowX: "auto", padding: "10px 2.8vw 14px", margin: "-10px -2.8vw 0" }}
    >
      {children}
    </Focusable>
  </div>
);

export const SectionTitle: FC<{ children: ReactNode; right?: ReactNode }> = ({ children, right }) => (
  <div style={{ display: "flex", alignItems: "baseline", gap: 16, margin: "0 0 12px" }}>
    <div style={{ fontSize: 18, fontWeight: 700, color: "#ffffff" }}>{children}</div>
    {right && <div style={{ marginLeft: "auto" }}>{right}</div>}
  </div>
);

/**
 * Tracks as a table: number (or cover), title and artist, album, length. A plays from that track,
 * X adds it to the queue, Y opens its menu; the playing track is in the accent colour.
 */
export const TrackTable: FC<{
  instanceId: string;
  tracks: Track[];
  onPlay: (i: number) => void;
  onSecondary?: (i: number) => void;
  secondaryLabel?: string;
  onOptions?: (i: number, e: any) => void;
  /** one album's tracks: numbered, no covers, no album column */
  numbered?: boolean;
  showAlbum?: boolean;
  /** shown first; "Show all" reveals the rest */
  limit?: number;
}> = ({ instanceId, tracks, onPlay, onSecondary, secondaryLabel, onOptions, numbered, showAlbum = true, limit }) => {
  const [expanded, setExpanded] = useState(false);
  const { queue, index } = useStore(player.state);
  const current = queue[index];
  const shown = limit && !expanded ? tracks.slice(0, limit) : tracks;
  return (
    <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {shown.map((t, i) => (
        <TrackLine
          key={t.id + i}
          instanceId={instanceId}
          track={t}
          n={numbered ? i + 1 : undefined}
          active={current?.instanceId === instanceId && current.track.id === t.id}
          showAlbum={showAlbum}
          onPlay={() => onPlay(i)}
          onSecondary={onSecondary && (() => onSecondary(i))}
          secondaryLabel={secondaryLabel}
          onOptions={onOptions && ((e) => onOptions(i, e))}
        />
      ))}
      {limit !== undefined && tracks.length > limit && (
        <Focusable
          className="mc-row"
          noFocusRing
          onActivate={() => setExpanded(!expanded)}
          onClick={() => setExpanded(!expanded)}
          style={{ padding: "10px 12px", fontSize: 15, opacity: 0.75 }}
        >
          {expanded ? "Show less" : `Show all ${tracks.length}`}
        </Focusable>
      )}
    </Focusable>
  );
};

export const TrackLine: FC<{
  instanceId: string;
  track: Track;
  n?: number;
  active?: boolean;
  showAlbum?: boolean;
  onPlay: () => void;
  onSecondary?: () => void;
  secondaryLabel?: string;
  onOptions?: (e: any) => void;
}> = ({ instanceId, track, n, active, showAlbum = true, onPlay, onSecondary, secondaryLabel, onOptions }) => {
  const [focused, setFocused] = useState(false);
  return (
    <Focusable
      className="mc-row"
      noFocusRing
      onActivate={onPlay}
      onClick={onPlay}
      onOKActionDescription="Play"
      onSecondaryButton={onSecondary && stopping(onSecondary)}
      onSecondaryActionDescription={onSecondary ? (secondaryLabel ?? "Add to queue") : undefined}
      onOptionsButton={onOptions}
      onOptionsActionDescription={onOptions ? "Options" : undefined}
      onGamepadFocus={() => setFocused(true)}
      onGamepadBlur={() => setFocused(false)}
      style={{ display: "flex", alignItems: "center", gap: 16, padding: "8px 12px", minHeight: 44 }}
    >
      {n !== undefined ? (
        <div
          style={{
            width: 28,
            textAlign: "right",
            fontSize: 15,
            opacity: active ? 1 : 0.55,
            color: active ? ACCENT : undefined,
          }}
        >
          {n}
        </div>
      ) : (
        <Cover instanceId={instanceId} c={{ art: track.art, kind: "album" }} size={44} />
      )}
      <div style={{ flex: 3, minWidth: 0 }}>
        <ScrollingText
          text={track.title}
          play={focused}
          style={{ fontSize: 16, color: active ? ACCENT : "#ffffff", fontWeight: active ? 600 : undefined }}
        />
        {track.artist && <ScrollingText text={track.artist} play={focused} style={{ fontSize: 13, opacity: 0.6 }} />}
      </div>
      {showAlbum && (
        <div style={{ flex: 2, minWidth: 0 }}>
          <ScrollingText text={track.album} play={focused} style={{ fontSize: 14, opacity: 0.6 }} />
        </div>
      )}
      <div style={{ width: 56, textAlign: "right", fontSize: 14, opacity: 0.6 }}>
        {track.live ? "LIVE" : track.duration ? formatTime(track.duration) : ""}
      </div>
    </Focusable>
  );
};

/** A plain button in the page's style: a light box, white when focused (as Steam's own). */
export const PageButton: FC<{
  onClick: () => void;
  children: ReactNode;
  primary?: boolean;
  active?: boolean;
  label?: string;
}> = ({ onClick, children, primary, active, label }) => (
  <Focusable
    className="mc-row"
    noFocusRing
    onActivate={onClick}
    onClick={onClick}
    onOKActionDescription={label}
    style={{
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "0 22px",
      height: 44,
      fontSize: 16,
      fontWeight: 600,
      borderRadius: 4,
      background: primary ? ACCENT : "rgba(255, 255, 255, 0.1)",
      color: primary ? "#ffffff" : active ? ACCENT : "#dcdedf",
    }}
  >
    {children}
  </Focusable>
);

export const Loading: FC<{ result: Result<any> | null }> = ({ result }) =>
  result === null ? (
    <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
      <Spinner style={{ width: 40, height: 40 }} />
    </div>
  ) : !result.ok ? (
    <Notice>{result.error}</Notice>
  ) : null;

export const Notice: FC<{ children: ReactNode }> = ({ children }) => (
  <div style={{ padding: "32px 0", fontSize: 18, opacity: 0.6 }}>{children}</div>
);

/**
 * Puts the controller on something in `container` once its view has rendered: the element marked
 * `data-mc={key}` (the card a page was opened from, going back), else the first search result, else
 * the first thing to press.
 * Retries for a moment, as a view's contents arrive after it loads, unless the user moves first.
 */
export function focusInto(container: HTMLElement | null, key?: string): () => void {
  let tries = 0;
  // where focus was at the first look: Steam's own pick on a tab change (which can be the bottom of the
  // page, from where the tab bar was), overridden; once the user moves it, it's theirs
  let seen: Element | null | undefined;
  const timer = window.setInterval(() => {
    const doc = container?.ownerDocument;
    if (!container || !doc || ++tries > 30) return window.clearInterval(timer);
    const active = doc.activeElement;
    if (seen === undefined) seen = active;
    else if (active !== seen && container.contains(active)) return window.clearInterval(timer);
    const remembered = key ? container.querySelector<HTMLElement>(`[data-mc="${CSS.escape(key)}"]`) : null;
    const target =
      remembered ||
      container.querySelector<HTMLElement>(".mc-results .mc-row, .mc-results .mc-card, .mc-results .mc-tile") ||
      (!container.querySelector(".mc-results") &&
        container.querySelector<HTMLElement>(".mc-row, .mc-card, .mc-tile, input"));
    if (target) {
      target.focus();
      if (remembered) target.scrollIntoView?.({ block: "nearest" });
      // the page's first thing: from the very top, so its heading shows too
      else scrollParent(container)?.scrollTo?.({ top: 0 });
      window.clearInterval(timer);
    }
  }, 100);
  return () => window.clearInterval(timer);
}

/** The nearest ancestor of `el` that scrolls (in the media center, the tab's contents). */
function scrollParent(el: HTMLElement): HTMLElement | null {
  let node = el.parentElement;
  while (node && !/auto|scroll/.test(getComputedStyle(node).overflowY)) node = node.parentElement;
  return node;
}

/** A big labelled tile for a part of the library ("All albums", "Artists"), Media Center style. */
export const Tile: FC<{ c: Collection; onOpen: () => void }> = ({ c, onOpen }) => (
  <Focusable
    className="mc-tile"
    data-mc={c.kind + ":" + c.id}
    noFocusRing
    onActivate={onOpen}
    onClick={onOpen}
    onOKActionDescription="Open"
    style={{
      width: "clamp(120px, 17vw, 230px)",
      flexShrink: 0, // the same size in a row that scrolls as in a grid
      height: "clamp(64px, 9vw, 120px)",
      padding: "12px 14px",
      boxSizing: "border-box",
      display: "flex",
      flexDirection: "column",
      justifyContent: "space-between",
      background: "linear-gradient(135deg, rgba(255, 255, 255, 0.14), rgba(255, 255, 255, 0.04))",
    }}
  >
    <div style={{ fontSize: 20, opacity: 0.7 }}>{KIND_ICON[c.kind]}</div>
    <div style={{ fontSize: 15, fontWeight: 600, color: "#ffffff", lineHeight: 1.2 }}>{c.title}</div>
  </Focusable>
);

/** A track on a shelf (a station, a song): its cover, title and artist. A plays it, X queues it. */
export const TrackCard: FC<{
  instanceId: string;
  track: Track;
  onPlay: () => void;
  onSecondary?: () => void;
}> = ({ instanceId, track, onPlay, onSecondary }) => {
  const [focused, setFocused] = useState(false);
  const size = useCardSize();
  return (
    <Focusable
      className="mc-card"
      noFocusRing
      onActivate={onPlay}
      onClick={onPlay}
      onOKActionDescription="Play"
      onSecondaryButton={onSecondary && stopping(onSecondary)}
      onSecondaryActionDescription={onSecondary ? "Add to queue" : undefined}
      onGamepadFocus={() => setFocused(true)}
      onGamepadBlur={() => setFocused(false)}
      style={{ width: size, flexShrink: 0, padding: 2 }}
    >
      <Cover instanceId={instanceId} c={{ art: track.art, kind: "album" }} size={size} />
      <div style={{ marginTop: 10 }}>
        <ScrollingText text={track.title} play={focused} style={{ fontSize: 14, fontWeight: 600, color: "#ffffff" }} />
        {track.artist && (
          <ScrollingText text={track.artist} play={focused} style={{ fontSize: 12, opacity: 0.6, marginTop: 2 }} />
        )}
      </div>
    </Focusable>
  );
};

/**
 * Whether `ref` has come near the visible part of the page, so rows far down can wait to load.
 * Stays true once it has. Uses the IntersectionObserver of the window the element is in (this code
 * runs in Steam's shared context; see watchSize).
 */
export function useNear(ref: RefObject<HTMLElement | null>): boolean {
  const [near, setNear] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || near) return;
    const Observer: typeof IntersectionObserver =
      (el.ownerDocument.defaultView as any)?.IntersectionObserver ?? IntersectionObserver;
    // measured against the tab's own scrolling area: rows below it are hidden by it, so against the
    // window they'd never come near, and with nothing to focus there the controller can't scroll on
    const observer = new Observer((entries) => entries.some((e) => e.isIntersecting) && setNear(true), {
      root: scrollParent(el),
      rootMargin: "600px 0px",
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [near]);
  return near;
}

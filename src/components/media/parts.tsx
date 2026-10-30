import { Focusable, gamepadDialogClasses } from "@decky/ui";
import { CSSProperties, FC, ReactNode, RefObject, createContext, useContext, useEffect, useRef, useState } from "react";
import { FaCompactDisc, FaFolder, FaList, FaUsb, FaUser } from "react-icons/fa";
import { Collection, NO_DISC, Result, Track } from "../../api";
import { player } from "../../player";
import { useArtLoad } from "../../art";
import { mediaUrl, pluginState, useStore } from "../../store";
import { ACCENT, QuietSpinner, ScrollingText, formatTime } from "../common";
import { sourceMark } from "../sourceIcon";
import { GRID_GAP, GRID_SHARE, gridShape } from "./grid";

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
/* Steam's tab bar sits over the top of the tab's scrolling area: what Steam scrolls into view (lists of
   tracks; rows of cards are snapRows' own) must clear it rather than stop half under the bar */
.mc-center [class*="TabContentsScroll"] { scroll-padding-top: 104px; scroll-padding-bottom: 12px; }
.mc-card, .mc-tile { scroll-margin-top: 46px; scroll-margin-bottom: 10px; scroll-margin-left: 2.8vw; scroll-margin-right: 2.8vw; }
.mc-row { scroll-margin-top: 10px; scroll-margin-bottom: 10px; }
/* pills (Lyrics | Queue, a speed) light up as Steam's own do, without a frame; the chosen one stays white */
.mc-pill { outline: none; transition: background 0.12s; }
.mc-pill.gpfocus:not(.mc-on), .mc-pill:hover:not(.mc-on) { background: rgba(255, 255, 255, 0.24) !important; }
.mc-pill.gpfocus.mc-on { box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.35); }
/* the panel switch beside the title: dull dots, the chosen one a white pill; the focused one lights up */
.mc-dot { display: flex; align-items: center; height: 28px; padding: 0 4px; outline: none; }
/* Browse's sources: dots either side of the showing one's name, tight under the tabs */
.mc-sources { display: flex; align-items: center; gap: 14px; margin: 0 0 -8px; }
.mc-sources-side { flex: 1; display: flex; align-items: center; min-width: 0; }
/* a source's Browse coming in: faded in where it'll be, the page keeping its height meanwhile */
/* a source's Browse coming in: hidden until its rows have settled where they'll be, then faded in; the page keeps
   its height meanwhile */
/* The overlapping pages must fit the viewport: an auto grid track grows to a shelf's full content
   width, leaving nothing to scroll inside the shelf while the tab clips the off-screen cards. */
.mc-browse-stack { display: grid; grid-template-columns: minmax(0, 1fr); }
.mc-browse-stack > .mc-browse-body { grid-area: 1 / 1; min-width: 0; }
.mc-browse-body { min-height: 70vh; }
/* a page opened under Browse's dots: room between them and its header (the dots sit tight on Browse's own rows) */
.mc-browse-body .mc-page-head { margin-top: 26px; }
/* a search's results: room between the dots' line and their first heading */
.mc-browse-body > .mc-results { padding-top: 14px; }
/* the one swapped in fades in where it'll stay */
.mc-in-sight { animation: mc-swap-in 0.18s ease both; }
@keyframes mc-swap-in { from { opacity: 0; } to { opacity: 1; } }
/* either end of the dots' line: what was searched for at the start, the same room at the end */
.mc-sources-end { flex: 0 0 220px; min-width: 0; font-size: 14px; opacity: 0.75; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; display: flex; align-items: center; gap: 8px; }
/* the browser's scroll anchoring moved the page as rows and covers came in above where it was looking: off here, where
   the page's own scrolling (snapRows) decides */
.mc-center, .mc-center * { overflow-anchor: none; }
.mc-sources-name { flex-shrink: 0; width: 180px; text-align: center; font-size: 14px; opacity: 0.6; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }
.mc-dot > span { display: block; width: 9px; height: 9px; border-radius: 5px; background: rgba(255, 255, 255, 0.28);
  transition: width 0.22s ease, background 0.15s, transform 0.15s; }
.mc-dot.gpfocus > span, .mc-dot:hover > span { background: rgba(255, 255, 255, 0.7); transform: scale(1.25); }
.mc-dot.mc-on > span { width: 26px; background: #fff; transform: none; }
.mc-dot.gpfocus.mc-on > span { box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.35); }
/* Browse's dots each keep one slot whichever is lit, so none moves as the pill goes along them, and both sides hold as
   many (an empty slot evens an odd count): the line is centred on the name, under the tabs */
.mc-sources .mc-dot { width: 20px; padding: 0; justify-content: center; flex-shrink: 0; }
.mc-sources .mc-dot.mc-on > span { width: 18px; }
.mc-dot-gap { width: 20px; flex-shrink: 0; }
/* the source a card is from: its mark, small, in the cover's corner (where a row mixes sources) */
.mc-source-badge { position: absolute; top: 7px; right: 7px; width: 22px; height: 22px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center; font-size: 12px; color: #fff;
  background: rgba(10, 12, 16, 0.6); backdrop-filter: blur(6px); box-shadow: 0 1px 4px rgba(0, 0, 0, 0.35);
  pointer-events: none; }
/* Now Playing's cover, and the record a tap on the touchscreen makes of it: round, turning, grooved, with a sheen
   that stays put as the record turns under it, and the spindle hole */
.mc-np-face { position: absolute; inset: 0; border-radius: 8px; box-shadow: 0 8px 30px rgba(0, 0, 0, 0.5);
  transition: border-radius 0.45s ease; }
.mc-record .mc-np-face { border-radius: 50%; animation: mc-record-turn 9s linear infinite;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.6), 0 0 0 2px rgba(0, 0, 0, 0.55); }
.mc-record .mc-np-face::after { content: ""; position: absolute; inset: 0; border-radius: 50%;
  background: repeating-radial-gradient(circle, rgba(0, 0, 0, 0) 0 3px, rgba(0, 0, 0, 0.14) 3px 4px); }
.mc-record::after { content: ""; position: absolute; inset: 0; border-radius: 50%; pointer-events: none;
  background: conic-gradient(from 200deg, rgba(255, 255, 255, 0) 0deg, rgba(255, 255, 255, 0.16) 30deg,
    rgba(255, 255, 255, 0) 70deg, rgba(255, 255, 255, 0) 180deg, rgba(255, 255, 255, 0.1) 210deg,
    rgba(255, 255, 255, 0) 250deg); }
.mc-record-hole { position: absolute; left: 50%; top: 50%; width: 7%; height: 7%; transform: translate(-50%, -50%);
  border-radius: 50%; background: #0e141b; box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.12); z-index: 1; }
@keyframes mc-record-turn { to { transform: rotate(360deg); } }
/* on a round picture (an artist's): at the circle's edge, not off it */
.mc-source-badge.mc-on-round { top: auto; right: 12%; bottom: 6%; }
/* a line of text to rest on while reading (show notes, a station's details): barely marked */
.mc-line { border-radius: 4px; outline: none; scroll-margin-top: 10px; scroll-margin-bottom: 10px; }
.mc-line.gpfocus { background: rgba(255, 255, 255, 0.06); }
/* the transport's plain buttons (shuffle, skip...) as circles that light up softly, like the round play
   button, not the full-width white block Steam's buttons fill with */
.mc-center button.DialogButton.md-plain { border-radius: 50% !important; flex: 0 0 auto !important; width: auto !important; min-width: 0 !important; aspect-ratio: 1; }
.mc-center button.DialogButton.md-plain.gpfocus, .mc-center button.DialogButton.md-plain:hover {
  background: rgba(255, 255, 255, 0.18) !important; color: #ffffff !important; box-shadow: none !important; }
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
  small: "min(clamp(80px, 10.5vw, 150px), 21vh)",
  medium: "min(clamp(96px, 14vw, 190px), 25vh)",
  large: "min(clamp(120px, 20vw, 260px), 28vh)",
};
/** A grid's own cover size (sized to fill its width), over the setting's */
const CardSizeOverride = createContext<string | null>(null);
export const useCardSize = () => {
  const own = useContext(CardSizeOverride);
  const chosen = CARD_SIZES[useStore(pluginState)?.center?.cards ?? "medium"] ?? CARD_SIZES.medium;
  return own ?? chosen;
};

const KIND_ICON: Record<Collection["kind"], ReactNode> = {
  album: <FaCompactDisc />,
  playlist: <FaList />,
  artist: <FaUser />,
  folder: <FaFolder />,
  list: <FaList />,
  device: <FaUsb />,
};

/** A collection's picture (round for artists), or its kind's icon when it has none. */
export const Cover: FC<{ instanceId: string; c: Pick<Collection, "art" | "kind"> & { id?: string }; size: number | string }> = ({
  instanceId,
  c,
  size,
}) => {
  const url = c.art ? mediaUrl("art", instanceId, c.art) : undefined;
  const load = useArtLoad(url);
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
      {/* lazy: a shelf's covers past the screen's edge load as it scrolls to them */}
      {!load.failed ? (
        <img
          src={url}
          loading="lazy"
          decoding="async"
          style={{ width: "100%", height: "100%", objectFit: "cover", opacity: load.visible ? 1 : 0 }}
          onLoad={load.onLoad}
          onError={load.onError}
        />
      ) : c.id === NO_DISC ? (
        <FaCompactDisc />
      ) : (
        KIND_ICON[c.kind]
      )}
    </div>
  );
};

/** One collection on a shelf: A opens it, X plays it (albums and playlists), Y its menu. */
/** Whether cards show their source on their cover: off in a row that's all one source's, named in its title */
export const SourceBadges = createContext(true);

export const Card: FC<{
  instanceId: string;
  c: Collection;
  onOpen: () => void;
  onPlay?: () => void;
  onOptions?: (e: any) => void;
}> = ({ instanceId, c, onOpen, onPlay, onOptions }) => {
  const [focused, setFocused] = useState(false);
  const size = useCardSize();
  // which source it's from, on its cover, where a row mixes them (the same album from two services told apart)
  const services = useStore(pluginState)?.services;
  const source = useContext(SourceBadges) && c.source ? services?.find((s) => s.id === c.source) : undefined;
  const from = source?.name;
  const subtitle = from && c.subtitle?.endsWith(` · ${from}`) ? c.subtitle.slice(0, -` · ${from}`.length) : c.subtitle;
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
      <div style={{ position: "relative" }}>
        <Cover instanceId={instanceId} c={c} size={size} />
        {source && (
          <div className={c.kind === "artist" ? "mc-source-badge mc-on-round" : "mc-source-badge"} title={source.name}>
            {sourceMark(source.type, source.name)}
          </div>
        )}
      </div>
      <div style={{ marginTop: 10, textAlign: c.kind === "artist" ? "center" : "left" }}>
        <ScrollingText text={c.title} play={focused} style={{ fontSize: 14, fontWeight: 600, color: "#ffffff" }} />
        {subtitle && <ScrollingText text={subtitle} play={focused} style={{ fontSize: 12, opacity: 0.6, marginTop: 2 }} />}
      </div>
    </Focusable>
  );
};

/** The end of a row: a square, as big as the row's covers, that opens all of it. */
export const ShowAllCard: FC<{ onOpen: () => void; count?: string; mcKey?: string }> = ({ onOpen, count, mcKey }) => {
  const size = useCardSize();
  return (
    <Focusable
      className="mc-card"
      data-mc={mcKey}
      noFocusRing
      onActivate={onOpen}
      onClick={onOpen}
      onOKActionDescription="Show all"
      style={{ width: size, flexShrink: 0, padding: 2 }}
    >
      <div
        className="mc-art"
        style={{
          width: size,
          height: size,
          borderRadius: 6,
          background: "linear-gradient(135deg, rgba(255, 255, 255, 0.16), rgba(255, 255, 255, 0.05))",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 6,
          textAlign: "center",
          padding: 10,
          boxSizing: "border-box",
        }}
      >
        <div style={{ fontSize: 16, fontWeight: 700, color: "#ffffff" }}>Show all</div>
        {count && <div style={{ fontSize: 12, opacity: 0.6 }}>{count}</div>}
      </div>
    </Focusable>
  );
};

/** A titled row that scrolls sideways, as on Steam's home page. */
export const Shelf: FC<{ title?: string; above?: ReactNode; children: ReactNode }> = ({ title, above, children }) => (
  // mc-row-block: what snapRows brings into view whole when the controller enters the row (with what's
  // above its title: its group's name)
  <div className="mc-row-block mc-slot" style={{ marginBottom: 28 }}>
    {above}
    {title && <SectionTitle>{title}</SectionTitle>}
    <Focusable
      flow-children="row"
      className="mc-shelf"
      // to the screen's edges, as Steam's own rows: cards scroll off the edge rather than being cut at
      // the page's margin; the padding is room for a focused card to grow
      style={{ display: "flex", gap: 22, overflowX: "auto", padding: "10px 2.8vw 14px", margin: "-10px -2.8vw 0" }}
    >
      {children}
    </Focusable>
  </div>
);

export const SectionTitle: FC<{ children: ReactNode; right?: ReactNode }> = ({ children, right }) => (
  <div className="mc-title" style={{ display: "flex", alignItems: "baseline", gap: 16, margin: "0 0 12px" }}>
    <div style={{ fontSize: 18, fontWeight: 700, color: "#ffffff" }}>{children}</div>
    {right && <div style={{ marginLeft: "auto" }}>{right}</div>}
  </div>
);

/** The name of a group of rows (a tab's "Genres", then its "Decades"), above its rows' own titles. */
export const GroupTitle: FC<{ children: ReactNode }> = ({ children }) => (
  <div
    style={{
      fontSize: 13,
      fontWeight: 700,
      letterSpacing: "0.08em",
      textTransform: "uppercase",
      color: "rgba(255, 255, 255, 0.5)",
      margin: "4px 0 14px",
    }}
  >
    {children}
  </div>
);

/** Cards a grid shows at first, and adds each time the controller nears its end */
const GRID_STEP = 40;
/** Tracks a list shows at first, and adds as the controller nears its end */
const LIST_STEP = 150;

/**
 * Every card of a long list (all albums, all artists), wrapping down the page. Made a step at a time as
 * the controller nears the end, so a library of thousands opens at once.
 */
export const CardGrid: FC<{ title: string; count: number; children: (shown: number) => ReactNode }> = ({
  title,
  count,
  children,
}) => {
  const [shown, setShown] = useState(GRID_STEP);
  return (
    <FittedGrid
      title={
        // a grid without a name (one of Your library's pages, all of the page) has no header to take room
        title ? (
          <SectionTitle right={<span style={{ fontSize: 14, opacity: 0.5 }}>{count.toLocaleString()}</span>}>
            {title}
          </SectionTitle>
        ) : undefined
      }

      onNearEnd={() => shown < count && setShown(Math.min(count, shown + GRID_STEP))}
    >
      {children(Math.min(shown, count))}
    </FittedGrid>
  );
};

/** Calls `then` once this (invisible) point comes near the screen. */
const WhenNear: FC<{ then: () => void }> = ({ then }) => {
  const ref = useRef<HTMLDivElement>(null);
  const near = useNear(ref);
  useEffect(() => {
    if (near) then();
  }, [near]);
  return <div ref={ref} style={{ height: 1 }} />;
};

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
  /** what "Show all" does instead of showing the rest here (opens them on a page of their own) */
  onShowAll?: () => void;
}> = ({ instanceId, tracks, onPlay, onSecondary, secondaryLabel, onOptions, numbered, showAlbum = true, limit, onShowAll }) => {
  const [expanded, setExpanded] = useState(false);
  const [made, setMade] = useState(LIST_STEP);
  const { queue, index } = useStore(player.state);
  const current = queue[index];
  const shown = limit && !expanded ? tracks.slice(0, limit) : tracks.slice(0, made);
  return (
    <Focusable className="mc-list" flow-children="column" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
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
          onActivate={onShowAll ?? (() => setExpanded(!expanded))}
          onClick={onShowAll ?? (() => setExpanded(!expanded))}
          style={{ padding: "10px 12px", fontSize: 15, opacity: 0.75 }}
        >
          {expanded ? "Show less" : `Show all ${tracks.length}`}
        </Focusable>
      )}
      {shown.length < tracks.length && (!limit || expanded) && (
        <WhenNear key={made} then={() => setMade(made + LIST_STEP)} />
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
        <Cover instanceId={track.source ?? instanceId} c={{ art: track.art, kind: "album" }} size={44} />
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
      <QuietSpinner size={40} />
    </div>
  ) : !result.ok ? (
    <Notice>{result.error}</Notice>
  ) : null;

/**
 * A sentence in place of a page's contents ("Empty", what fills it, why it couldn't load). The controller
 * can rest on it, unmarked, when there's nothing else on the page: with nothing focused, B wouldn't reach
 * the page to go back (an empty playlist couldn't be left).
 */
export const Notice: FC<{ children: ReactNode }> = ({ children }) => (
  // an action (doing nothing, unhinted) is what makes Steam's focus able to land on it
  <Focusable
    className="mc-notice"
    noFocusRing
    onActivate={() => {}}
    onOKActionDescription=""
    style={{ padding: "32px 0", fontSize: 18, opacity: 0.6 }}
  >
    {children}
  </Focusable>
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
        container.querySelector<HTMLElement>(".mc-row, .mc-card, .mc-tile, input")) ||
      // nothing to press: the page's sentence, so B still goes back
      container.querySelector<HTMLElement>(".mc-notice");
    if (target) {
      // no scroll of the browser's own on the way (it showed as a jump, the page then gliding back)
      target.focus({ preventScroll: true });
      // a card remembered from before: in view (the page's first row: the very top, as snapRows has it)
      const scroller = scrollParent(container);
      const block = target.closest(".mc-row-block") as HTMLElement | null;
      const firstRow = !!scroller && !!block && isFirstRow(scroller, block);
      if (remembered && !firstRow) target.scrollIntoView?.({ block: "nearest" });
      else {
        // the page's first thing: from the very top, so its heading shows too (set, not scrollTo: that's Steam's
        // position restore, which snapRows turns off while the controller is in the page)
        if (scroller) scroller.scrollTop = 0;
      }
      window.clearInterval(timer);
    }
  }, 100);
  return () => window.clearInterval(timer);
}

/** Whether a row is the first thing on its page to go to (of the scrolling tab, or of the Browse or results it's in,
 *  while one source's come in over another's): nothing the controller can land on comes before it (a search's first
 *  row of albums is under its songs: not the first). */
export function isFirstRow(scroller: HTMLElement, block: HTMLElement): boolean {
  const page = (block.closest(".mc-browse-body") as HTMLElement | null) ?? scroller;
  const first = page.querySelector(".mc-row, .mc-card, .mc-tile, .mc-line");
  return !!first && block.contains(first);
}

/** The nearest ancestor of `el` that scrolls (in the media center, the tab's contents). */
export function scrollParent(el: HTMLElement): HTMLElement | null {
  let node = el.parentElement;
  while (node && !/auto|scroll/.test(getComputedStyle(node).overflowY)) node = node.parentElement;
  return node;
}

/** The colour along a picture's edge (its background, for a logo), or null when it can't be read. */
function edgeColour(img: HTMLImageElement): string | null {
  try {
    const canvas = img.ownerDocument.createElement("canvas");
    canvas.width = canvas.height = 16;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, 16, 16);
    const data = ctx.getImageData(0, 0, 16, 16).data;
    let r = 0,
      g = 0,
      b = 0,
      n = 0;
    for (let i = 0; i < 16; i++)
      for (const [x, y] of [
        [i, 0],
        [i, 15],
        [0, i],
        [15, i],
      ]) {
        const at = (y * 16 + x) * 4;
        if (data[at + 3] < 128) continue; // see-through: the tile's own colour shows there
        r += data[at];
        g += data[at + 1];
        b += data[at + 2];
        n++;
      }
    return n ? `rgb(${Math.round(r / n)}, ${Math.round(g / n)}, ${Math.round(b / n)})` : null;
  } catch {
    return null; // a picture that can't be read back: the station's colour instead
  }
}

/**
 * A station's picture, filling its square: a square one edge to edge; a wide or tall logo whole, on the
 * colour of its own edge (its background), so it reads as filling too. A song's cover (`full`) fills.
 * No picture (or a broken link): the station's initials on a colour of its own.
 */
export const StationArt: FC<{ url?: string; name: string; full?: boolean; style?: CSSProperties }> = ({
  url,
  name,
  full,
  style,
}) => {
  const load = useArtLoad(url);
  const [fit, setFit] = useState<{ square: boolean; edge: string | null } | null>(null);
  useEffect(() => {
    setFit(null);
  }, [url]);
  const shown = !load.failed;
  const hue = [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
  const initials = name
    .split(/\s+/)
    .filter((w) => /\w/.test(w))
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");
  const whole = !full && fit && !fit.square;
  return (
    <div
      style={{
        position: "relative",
        overflow: "hidden",
        containerType: "size",
        background:
          whole && fit.edge
            ? fit.edge
            : `linear-gradient(135deg, hsl(${hue} 45% 34%), hsl(${(hue + 50) % 360} 50% 16%))`,
        ...style,
      }}
    >
      {shown && (
        <img
          src={url}
          crossOrigin="anonymous"
          onLoad={(e) => {
            const img = e.currentTarget;
            const ratio = img.naturalWidth / Math.max(img.naturalHeight, 1);
            setFit({ square: ratio > 0.8 && ratio < 1.25, edge: edgeColour(img) });
            load.onLoad();
          }}
          onError={load.onError}
          style={{
            // shown once it's loaded and measured: no broken-image icon, no jump from filled to whole
            opacity: fit ? 1 : 0,
            position: "absolute",
            inset: whole ? "6%" : 0,
            width: whole ? "88%" : "100%",
            height: whole ? "88%" : "100%",
            objectFit: whole ? "contain" : "cover",
          }}
        />
      )}
      {!shown && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "34cqw",
            fontWeight: 800,
            color: "rgba(255, 255, 255, 0.85)",
          }}
        >
          {initials || "♪"}
        </div>
      )}
    </div>
  );
};

/** A big labelled tile for a part of the library ("All albums", "Artists"), Media Center style. */
export const Tile: FC<{ c: Collection; onOpen: () => void; width?: number | string }> = ({ c, onOpen, width }) => (
  <Focusable
    className="mc-tile"
    data-mc={c.kind + ":" + c.id}
    noFocusRing
    onActivate={onOpen}
    onClick={onOpen}
    onOKActionDescription="Open"
    // a square, sized by the grid it's in (TileGrid), or as wide as it's told on a shelf
    style={{
      aspectRatio: "1",
      ...(width !== undefined ? { width, flexShrink: 0 } : {}),
      minHeight: 76,
      padding: "12px 14px",
      boxSizing: "border-box",
      display: "flex",
      flexDirection: "column",
      justifyContent: "flex-end",
      gap: 3,
      background: "linear-gradient(135deg, rgba(255, 255, 255, 0.14), rgba(255, 255, 255, 0.04))",
    }}
  >
    {/* a long name ("The United Kingdom of Great Britain and Northern Ireland") keeps to two lines */}
    <div
      style={{
        fontSize: 15,
        fontWeight: 600,
        color: "#ffffff",
        lineHeight: 1.2,
        display: "-webkit-box",
        WebkitLineClamp: 2,
        WebkitBoxOrient: "vertical",
        overflow: "hidden",
      }}
    >
      {c.title}
    </div>
    {c.subtitle && (
      <div style={{ fontSize: 12, opacity: 0.55, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {c.subtitle}
      </div>
    )}
  </Focusable>
);

/** Genres, moods, countries...: square tiles in a grid like every other (FittedGrid), named inside. */
export const TileGrid: FC<{ title?: ReactNode; children: ReactNode }> = ({ title, children }) => (
  // squares laid out as covers are, their names inside them rather than under
  <FittedGrid title={title} captioned={false}>
    {children}
  </FittedGrid>
);

/** Live stations, not tracks: a page or shelf of them is shown as stations, played one at a time. */
export const allStations = (tracks: Track[]) => tracks.length > 0 && tracks.every((t) => t.live);

/** A station on a shelf or in a grid: its picture filling the square, its name, where it is. A plays it. */
export const StationCard: FC<{
  instanceId: string;
  track: Track;
  onPlay: () => void;
  onOptions?: (e: any) => void;
  width?: number | string;
}> = ({ instanceId, track, onPlay, onOptions, width }) => {
  const [focused, setFocused] = useState(false);
  const size = useCardSize();
  return (
    <Focusable
      className="mc-card"
      noFocusRing
      onActivate={onPlay}
      onClick={onPlay}
      onOKActionDescription="Play"
      onOptionsButton={onOptions}
      onOptionsActionDescription={onOptions ? "Options" : undefined}
      onGamepadFocus={() => setFocused(true)}
      onGamepadBlur={() => setFocused(false)}
      style={{ width: width ?? size, flexShrink: 0, padding: 2 }}
    >
      <StationArt
        url={track.art ? mediaUrl("art", track.source ?? instanceId, track.art) : undefined}
        name={track.title}
        style={{ width: "100%", aspectRatio: "1", borderRadius: 6 }}
      />
      <div style={{ marginTop: 10 }}>
        <ScrollingText text={track.title} play={focused} style={{ fontSize: 14, fontWeight: 600, color: "#ffffff" }} />
        {track.artist && (
          <ScrollingText text={track.artist} play={focused} style={{ fontSize: 12, opacity: 0.6, marginTop: 2 }} />
        )}
      </div>
    </Focusable>
  );
};

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
      <Cover instanceId={track.source ?? instanceId} c={{ art: track.art, kind: "album" }} size={size} />
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
      rootMargin: "1500px 0px", // a few rows ahead: a row's music can take a second to arrive
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [near]);
  return near;
}

/**
 * Where a row's cards will be, while its music loads: blanks shaped like cards (cover, title,
 * subtitle), so the page doesn't move under the controller when the real ones arrive.
 */
export const CardBlanks: FC<{ count?: number }> = ({ count = 6 }) => {
  const size = useCardSize();
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} style={{ width: size, flexShrink: 0, padding: 2 }}>
          <div style={{ width: size, height: size, borderRadius: 6, background: "rgba(255, 255, 255, 0.05)" }} />
          <div
            style={{
              marginTop: 10,
              height: 17,
              width: "70%",
              borderRadius: 3,
              background: "rgba(255, 255, 255, 0.05)",
            }}
          />
          <div
            style={{ marginTop: 2, height: 14, width: "45%", borderRadius: 3, background: "rgba(255, 255, 255, 0.04)" }}
          />
        </div>
      ))}
    </>
  );
};

/** The height of Steam's tab bar over the top of a tab's scrolling area */
export const TAB_BAR = 58;

/** How long the page takes to glide to a row */
const GLIDE_MS = 200;

/**
 * Every page of the media center scrolls the same way, as on Steam's home page, by whole rows:
 * - Entering a row of cards (or a FittedGrid, a screen of whole rows) glides it, title and all, to just
 *   under the tab bar, and it stays there.
 * - In a list of tracks (or a grid not fitted), the page moves as little as shows the focused line
 *   whole, always by whole lines: the top of the view on a line's top, never half a line under the tabs.
 * - Held down, the stick moves focus several times a second: each glide aims from where the one before
 *   was going, not from where it had got to, so the page keeps moving one way instead of stuttering.
 * The media center does this scrolling itself rather than share it with Steam's, as two scrolls at
 * once stutter and leave rows half under the bar:
 * - Steam scrolls a focused card into view (to the nearest edge, which when moving up lands the row
 *   half under the bar); for cards in rows that's cut down to scrolling along the row, elsewhere to nothing.
 * - Steam's scroll area notes its position every 250ms and puts it back when the note differs from
 *   the latest one, which in the middle of a scroll is a stale spot: the page jumps back and stays
 *   there. That's turned off while the controller is in the page (its job is coming back to a tab).
 * - Rows that finish loading above change where a row is: the glide aims anew each frame, and a
 *   change after it ends glides the row back.
 * `under` is how much of the area's top is covered (Steam's tab bar); `own` when the container is the
 * scrolling area itself. Returns the cleanup.
 */
export function snapRows(container: HTMLElement | null, { under = TAB_BAR } = {}): () => void {
  if (!container) return () => {};
  const win = (container.ownerDocument.defaultView ?? window) as Window & typeof globalThis;
  const focused = () => container.ownerDocument.activeElement as HTMLElement | null;
  const focusedBlock = () => {
    const el = focused();
    return container.contains(el) ? (el?.closest(".mc-row-block") as HTMLElement | null) : null;
  };
  let glide: {
    scroller: HTMLElement;
    block: HTMLElement;
    nearest: boolean;
    /** where it ends: a line's is fixed when the glide starts (it depends on where the page was going) */
    to: number;
    from: number;
    start?: number;
    frame: number;
  } | null = null;

  /**
   * Where the page scrolls to for a block: a row's top under the tabs; a list's line (`nearest`) as
   * little as shows it whole (up under the tabs, or down to the bottom edge), and not at all when it
   * shows. Judged from where the page is going (`at`), so quick presses add up.
   */
  const target = (scroller: HTMLElement, block: HTMLElement, nearest = false) => {
    const view = scroller.getBoundingClientRect();
    const box = block.getBoundingClientRect();
    const top = scroller.scrollTop + box.top - view.top;
    const max = scroller.scrollHeight - scroller.clientHeight;
    // the page's first row: the very top, so what's above it (a heading, Browse's source dots) shows with it
    if (!nearest && isFirstRow(scroller, block)) return 0;
    if (!nearest) return Math.max(0, Math.min(top - under - 8, max));
    const at = glide && glide.scroller === scroller ? glide.to : scroller.scrollTop;
    const room = scroller.clientHeight - under - 8 - 24;
    const line = block.parentElement?.closest(".mc-grid, .mc-list");
    const rows = line
      ? [
          ...new Set(
            [...line.children].map((c) => Math.round(scroller.scrollTop + c.getBoundingClientRect().top - view.top)),
          ),
        ].sort((x, y) => x - y)
      : [top];
    const rowTop = (y: number) => Math.max(0, Math.min(y - under - 8, max));
    // the first line: its heading in view with it
    const heading = line?.previousElementSibling;
    const first =
      rows[0] === Math.round(top) && heading?.matches(".mc-title")
        ? scroller.scrollTop + heading.getBoundingClientRect().top - view.top
        : null;
    if (top < at + under + 8) return rowTop(first ?? top); // above: its line right under the tabs
    if (top + box.height <= at + scroller.clientHeight - 24) return at; // in view
    // below: the first line from which it fits (the first line with its heading when they fit together)
    const from =
      [...(first !== null ? [first] : []), ...rows].find((r) => r <= top && top + box.height - r <= room) ?? top;
    return rowTop(from);
  };

  const step = (now: number) => {
    if (!glide) return;
    glide.start ??= now;
    const t = Math.min(1, (now - glide.start) / GLIDE_MS);
    const eased = 1 - Math.pow(1 - t, 3);
    // a row's top is looked up each frame (rows can move as covers load); a line's end point is fixed
    const to = glide.nearest ? glide.to : target(glide.scroller, glide.block);
    glide.scroller.scrollTop = glide.from + (to - glide.from) * eased;
    if (t < 1) glide.frame = win.requestAnimationFrame(step);
    else glide = null;
  };
  const glideTo = (block: HTMLElement, nearest = false) => {
    const scroller = scrollParent(block);
    if (!scroller) return;
    ownScrolling(scroller);
    // worked out before the glide before it stops: it aims from where that one was going
    const to = target(scroller, block, nearest);
    if (glide) win.cancelAnimationFrame(glide.frame);
    glide = null;
    if (Math.abs(to - scroller.scrollTop) < 1) return;
    glide = { scroller, block, nearest, to, from: scroller.scrollTop, frame: win.requestAnimationFrame(step) };
  };
  // Steam's position restore, off while the controller is in this page
  const ownScrolling = (scroller: HTMLElement & { mcOwned?: () => boolean }) => {
    const restore = scroller.mcOwned ? null : scroller.scrollTo;
    scroller.mcOwned = () => container.isConnected && container.contains(focused());
    if (!restore) return;
    scroller.scrollTo = function (this: typeof scroller, ...args: any[]) {
      if (!this.mcOwned?.()) return (restore as any).apply(this, args);
    };
  };

  const onFocus = (e: FocusEvent) => {
    const el = e.target as HTMLElement & { mcAlong?: boolean };
    const block = el?.closest?.(".mc-row-block") as HTMLElement | null;
    if (!block) {
      // a line of a list (or a card of a grid not fitted): this page scrolls to it, smoothly, not Steam
      // (whose own scrolling judders)
      const line = el?.closest?.(".mc-grid, .mc-list");
      if (!line || !container.contains(line)) return;
      let unit: HTMLElement = el;
      while (unit.parentElement && unit.parentElement !== line) unit = unit.parentElement;
      if (!el.mcAlong) {
        el.mcAlong = true;
        el.scrollIntoView = () => {};
      }
      glideTo(unit, true);
      return;
    }
    if (!el.mcAlong) {
      // Steam calls this on what it focuses, after this event: along the row only
      el.mcAlong = true;
      el.scrollIntoView = () => alongShelf(el);
    }
    // already gliding to this row (moving along it, or a grid's rows sliding inside it): nothing new
    if (glide?.block === block) return;
    glideTo(block);
  };
  /**
   * A page's rows on one height (a "slot", the room under the tabs shared by as many rows as fit), each
   * row as many slots as it needs: the view, a row at its top, always holds whole rows, never half of
   * the next one, with nothing hidden. And room after the last row for it to come up under the tabs
   * too (otherwise the end of a page shows the row before half under the bar).
   */
  const layout = () => {
    const scroller = scrollParent(container);
    if (!scroller || !container.isConnected) return;
    // from the row's top (just under the tabs) to the bottom edge: the next row starts past it
    const room = scroller.clientHeight - under - 8;
    const rows = [...container.querySelectorAll<HTMLElement>(".mc-slot")];
    container.style.paddingBottom = "";
    if (room <= 0 || !rows.length) return;
    // the space between rows is the slot's own (the row sits in its middle), not a margin on top of it
    rows.forEach((r) => {
      r.style.minHeight = "";
      r.style.marginBottom = "0";
    });
    const heights = rows.map((r) => r.offsetHeight);
    const typical = [...heights].sort((a, b) => a - b)[Math.floor(heights.length / 2)];
    const slot = Math.floor(room / Math.max(1, Math.floor(room / typical)));
    rows.forEach((r, i) => {
      r.style.minHeight = `${Math.max(1, Math.ceil((heights[i] - 1) / slot)) * slot}px`;
      // the row in the middle of its room, not at its top
      r.style.display = "flex";
      r.style.flexDirection = "column";
      r.style.justifyContent = "center";
    });
    // the page ending in a row: room after it for it to reach the top
    const last = rows[rows.length - 1];
    const end = container.getBoundingClientRect().bottom;
    const lastBox = last.getBoundingClientRect();
    if (end - lastBox.bottom < 48) {
      const below = end - lastBox.top;
      const needed = scroller.clientHeight - under - 8 - below;
      if (needed > 0) container.style.paddingBottom = `${Math.ceil(needed)}px`;
    }
  };
  let laying = false;
  const relayout = () => {
    if (laying) return;
    laying = true;
    try {
      layout();
    } finally {
      laying = false;
    }
  };
  const Observer: typeof ResizeObserver = (win as any).ResizeObserver ?? ResizeObserver;
  const resized = new Observer(() => {
    relayout();
    const block = focusedBlock();
    if (block && !glide) glideTo(block);
  });
  // the page's own parts, as they come and go (a tab's page, the queue in place of the lyrics)
  const parts = () => [container, ...container.children].forEach((c) => resized.observe(c));
  const changed = new MutationObserver(parts);
  parts();
  changed.observe(container, { childList: true });
  container.addEventListener("focusin", onFocus);
  relayout();
  return () => {
    container.removeEventListener("focusin", onFocus);
    container.style.paddingBottom = "";
    resized.disconnect();
    changed.disconnect();
    if (glide) win.cancelAnimationFrame(glide.frame);
    glide = null;
  };
}

/** How a grid lays out (gridShape), and how many rows it has in all, and the room left round its rows */
type GridLayout = { cols: number; size: number; rows: number; pitch: number; total: number; spare: number };

/**
 * Every grid of the media center (a Show all, a genre's or a country's page, stations): cards filling
 * the width edge to edge with their names under them (gridShape), as many whole rows on screen as fit
 * under the title, never one cut off. The rest slide into view a row at a time as the controller moves
 * down; the slide is a transform, so held down the stick runs through the rows smoothly (each slide
 * picks up from where the last one was). The block is a row to snapRows: entering it brings it up under
 * the tabs. `captioned` false for tiles (genres, countries) that carry their name inside; `onNearEnd`:
 * more cards wanted (a long list made a step at a time).
 */
export const FittedGrid: FC<{
  title?: ReactNode;
  captioned?: boolean;
  onNearEnd?: () => void;
  children: ReactNode;
}> = ({ title, captioned = true, onNearEnd, children }) => {
  const block = useRef<HTMLDivElement>(null);
  const grid = useRef<HTMLDivElement>(null);
  const gap = GRID_GAP;
  const share = GRID_SHARE[useStore(pluginState)?.center?.cards ?? "medium"] ?? GRID_SHARE.medium;
  const [layout, setLayout] = useState<GridLayout | null>(null);
  const [offset, setOffset] = useState(0);
  // for the measuring, which outlives a render
  const now = useRef({ layout, share, captioned });
  now.current = { layout, share, captioned };
  const offsetNow = useRef(0);
  const nearEnd = useRef(onNearEnd);
  nearEnd.current = onNearEnd;
  const remeasure = useRef<(() => void) | null>(null);

  // each row's top in the grid (a transform doesn't move these)
  const rowTops = () =>
    [
      ...new Set(
        [...(grid.current?.children ?? [])]
          .map((c) => c as HTMLElement)
          .filter((c) => c.offsetHeight > 0)
          .map((c) => c.offsetTop),
      ),
    ].sort((a, b) => a - b);

  useEffect(() => {
    const b = block.current;
    const g = grid.current;
    const scroller = b && scrollParent(b);
    if (!b || !g || !scroller) return;
    const measure = () => {
      // the title's own height (not where the rows start: that moves with the space centring them)
      const header = b.firstElementChild as HTMLElement | null;
      const head = header ? header.offsetTop + header.offsetHeight : 0;
      const room = scroller.clientHeight - TAB_BAR - 8 - head - 16;
      const width = g.clientWidth;
      if (room <= 0 || width <= 0) return;
      const { layout: was, share: chosen, captioned: named } = now.current;
      // a card's height past its cover as it really is (the name under it, which doesn't change with the
      // cover's size), once the cards are drawn at this grid's size; till then, about two lines (none
      // for tiles)
      const first = g.children[0] as HTMLElement | undefined;
      const caption = was && first ? Math.max(0, first.offsetHeight - was.size - 4) : named ? 46 : 0;
      const shape = gridShape(width, room, caption, gap, chosen);
      const next: GridLayout = {
        ...shape,
        total: rowTops().length,
        spare: Math.max(0, room + gap - shape.rows * shape.pitch),
      };
      const same = was && (Object.keys(next) as (keyof GridLayout)[]).every((k) => Math.abs(next[k] - was[k]) < 1);
      if (!same) setLayout(next);
    };
    remeasure.current = measure;
    measure();
    const Observer: typeof ResizeObserver = (b.ownerDocument.defaultView as any)?.ResizeObserver ?? ResizeObserver;
    const watch = new Observer(measure);
    watch.observe(scroller);
    watch.observe(g);
    return () => watch.disconnect();
  }, []);
  // the cover size chosen in Settings changed: a new shape
  useEffect(() => remeasure.current?.(), [share]);

  const showRow = (row: number) => {
    const f = now.current.layout;
    if (!f || row < 0) return;
    let to = offsetNow.current;
    if (row < to) to = row;
    else if (row >= to + f.rows) to = row - f.rows + 1;
    to = Math.max(0, Math.min(to, Math.max(0, f.total - f.rows)));
    if (to !== offsetNow.current) {
      offsetNow.current = to;
      setOffset(to);
    }
    if (to + f.rows >= f.total - 1) nearEnd.current?.();
  };
  const onFocus = (e: any) => {
    const el = e.target as HTMLElement & { mcAlong?: boolean };
    let unit: HTMLElement = el;
    while (unit.parentElement && unit.parentElement !== grid.current) unit = unit.parentElement;
    if (unit.parentElement !== grid.current) return;
    if (!el.mcAlong) {
      // Steam's own scrolling to it: the slide does it
      el.mcAlong = true;
      el.scrollIntoView = () => {};
    }
    showRow(rowTops().indexOf(unit.offsetTop));
  };
  const paged = !!layout && layout.total > layout.rows;
  return (
    // not a slot (snapRows' rows of one height): it fits itself to the screen
    <div ref={block} className="mc-row-block" style={{ position: "relative", marginBottom: 28 }}>
      <div>{title}</div>
      <div
        style={{
          position: "relative",
          // the rows in the middle of the room under the title; the next row a whole gap below the
          // last, out of sight
          marginTop: paged ? Math.floor(layout.spare / 2) : 0,
          height: paged ? layout.rows * layout.pitch : undefined,
          overflowY: paged ? "clip" : "visible",
          overflowX: "visible",
        }}
      >
        <CardSizeOverride.Provider value={layout ? `${layout.size}px` : null}>
          <Focusable
            ref={grid}
            className="mc-grid mc-fit"
            onFocus={onFocus}
            style={{
              position: "relative",
              display: "grid",
              // till it's measured, the cover size chosen, as many as fit
              gridTemplateColumns: layout
                ? `repeat(${layout.cols}, ${layout.size + 4}px)`
                : `repeat(auto-fill, calc(${CARD_SIZES.medium} + 4px))`,
              justifyContent: "center",
              gap,
              // room for a focused card to grow, inside the clip
              paddingTop: gap / 2,
              transform: `translateY(${-offset * (layout?.pitch ?? 0)}px)`,
              transition: "transform 0.22s cubic-bezier(0.2, 0.8, 0.3, 1)",
            }}
          >
            {children}
          </Focusable>
        </CardSizeOverride.Provider>
      </div>
    </div>
  );
};

/**
 * Scrolls a card's row sideways to show it, leaving the row's margin beside it: an eased glide of its
 * own, aimed from where the glide before was going. (The browser's smooth scroll starts again from a
 * standstill on every call, so the stick held along a row stuttered, then caught up.)
 */
function alongShelf(el: HTMLElement) {
  const shelf = el.closest(".mc-shelf") as
    (HTMLElement & { mcGlide?: { to: number; from: number; start?: number; frame: number } }) | null;
  if (!shelf) return;
  const win = (shelf.ownerDocument.defaultView ?? window) as Window & typeof globalThis;
  const margin = parseFloat(getComputedStyle(shelf).paddingLeft) || 0;
  const card = el.getBoundingClientRect();
  const row = shelf.getBoundingClientRect();
  // in the row's own terms, and judged from where it's going
  const left = shelf.scrollLeft + card.left - row.left;
  const at = shelf.mcGlide?.to ?? shelf.scrollLeft;
  const max = shelf.scrollWidth - shelf.clientWidth;
  const to = Math.max(
    0,
    Math.min(
      max,
      left < at + margin
        ? left - margin
        : left + card.width > at + row.width - margin
          ? left + card.width - row.width + margin
          : at,
    ),
  );
  if (shelf.mcGlide) win.cancelAnimationFrame(shelf.mcGlide.frame);
  shelf.mcGlide = undefined;
  if (Math.abs(to - shelf.scrollLeft) < 1) return;
  const glide = { to, from: shelf.scrollLeft, frame: 0 } as { to: number; from: number; start?: number; frame: number };
  const step = (now: number) => {
    glide.start ??= now;
    const t = Math.min(1, (now - glide.start) / GLIDE_MS);
    shelf.scrollLeft = glide.from + (glide.to - glide.from) * (1 - Math.pow(1 - t, 3));
    if (t < 1) glide.frame = win.requestAnimationFrame(step);
    else shelf.mcGlide = undefined;
  };
  glide.frame = win.requestAnimationFrame(step);
  shelf.mcGlide = glide;
}

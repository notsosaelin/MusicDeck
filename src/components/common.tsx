import {
  DialogButton,
  DialogButtonPrimary,
  DialogButtonSecondary,
  DialogFooter,
  Field,
  Focusable,
  GamepadButton,
  GamepadEvent,
  Marquee,
  PanelSection,
  PanelSectionRow,
} from "@decky/ui";
import { CSSProperties, FC, ReactNode, RefObject, useEffect, useRef, useState } from "react";
import { FaCompactDisc, FaFolder, FaHeart, FaListUl, FaSort, FaUsb, FaUser } from "react-icons/fa";
import { Collection, CollectionKind, Track } from "../api";
import { useArtLoad } from "../art";
import { mediaUrl } from "../store";

export const ACCENT = "#1a9fff";

/** A country's name from its ISO code, in English (the browser's own list). */
export const countryName = (code: string) => {
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
};

export const formatTime = (seconds: number) => {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0:00";
  const s = Math.floor(seconds % 60);
  const m = Math.floor(seconds / 60) % 60;
  const h = Math.floor(seconds / 3600);
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? h + ":" : ""}${mm}:${String(s).padStart(2, "0")}`;
};

const KIND_ICONS: Record<CollectionKind, ReactNode> = {
  album: <FaCompactDisc />,
  artist: <FaUser />,
  playlist: <FaListUl />,
  folder: <FaFolder />,
  list: <FaListUl />,
  device: <FaUsb />,
};

export const Art: FC<{
  instanceId: string;
  art: string | null;
  size: number;
  fallback?: ReactNode;
  round?: boolean;
}> = ({ instanceId, art, size, fallback, round }) => {
  const url = art ? mediaUrl("art", instanceId, art) : undefined;
  const load = useArtLoad(url);
  const box: CSSProperties = {
    width: size,
    height: size,
    minWidth: size,
    borderRadius: round ? "50%" : 4,
    background: "rgba(255,255,255,0.08)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: size * 0.4,
    color: "rgba(255,255,255,0.5)",
    overflow: "hidden",
  };
  if (load.failed) return <div style={box}>{fallback ?? <FaCompactDisc />}</div>;
  return (
    <div style={box}>
      {/* lazy: a long list's covers load as they scroll into view, not all at once; hidden until
          they have (art.ts) */}
      <img
        src={url}
        width={size}
        height={size}
        loading="lazy"
        decoding="async"
        style={{ objectFit: "cover", opacity: load.visible ? 1 : 0 }}
        onLoad={load.onLoad}
        onError={load.onError}
      />
    </div>
  );
};

/**
 * Calls `onResize` whenever `el` changes size; returns the function that stops watching. Uses the
 * ResizeObserver of the window the element is in: this code runs in Steam's shared context, and an
 * observer from that window never reports elements in the Quick Access menu's or the main window.
 */
export function watchSize(el: Element, onResize: () => void): () => void {
  const Observer: typeof ResizeObserver = (el.ownerDocument.defaultView as any)?.ResizeObserver ?? ResizeObserver;
  const observer = new Observer(onResize);
  observer.observe(el);
  return () => observer.disconnect();
}

/** The nearest ancestor of `el` that scrolls (in the Quick Access menu, the panel itself). */
function scrollerOf(el: Element | null): HTMLElement | null {
  for (let node = el?.parentElement; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if ((overflowY === "auto" || overflowY === "scroll") && node.scrollHeight > node.clientHeight) return node;
  }
  return null;
}

/**
 * Steam only scrolls a focused element just into view, so focusing the top of the panel can leave its
 * header hidden. Call from the topmost focusable's onGamepadFocus to show the panel from the very top.
 */
export const scrollPanelToTop = (evt: { currentTarget: EventTarget | null }) =>
  scrollerOf(evt.currentTarget as Element)?.scrollTo({ top: 0, behavior: "smooth" });

export const ellipsis: CSSProperties = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };

/**
 * True while the focused element (Steam moves real DOM focus with the gamepad) is an ancestor of
 * this text, i.e. its row is selected. Listens on the document because the focused row element
 * sits above our component, so its focus events never pass through us.
 */
function useRowFocused(ref: RefObject<HTMLElement | null>) {
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const doc = el.ownerDocument;
    const update = () => {
      const active = doc.activeElement;
      setFocused(!!active && active !== doc.body && active.contains(el));
    };
    // focusout fires before the next element is focused; check once focus has settled
    const later = () => window.setTimeout(update, 0);
    doc.addEventListener("focusin", update);
    doc.addEventListener("focusout", later);
    update();
    return () => {
      doc.removeEventListener("focusin", update);
      doc.removeEventListener("focusout", later);
    };
  }, []);
  return focused;
}

/**
 * One line of text. If it doesn't fit it ends in "…", and while `play` is on it scrolls (Steam's own
 * marquee) to show the rest. Text that fits never moves.
 */
export const ScrollingText: FC<{ text: string; play: boolean; style?: CSSProperties }> = ({ text, play, style }) => {
  const ref = useRef<HTMLDivElement>(null);
  // whether `text` overflows, measured on the plain line (a hidden menu has no width yet, so wait)
  const [fit, setFit] = useState<{ text: string; overflows: boolean } | null>(null);
  const scrolling = play && fit?.text === text && fit.overflows;
  useEffect(() => {
    const el = ref.current;
    if (scrolling || !el) return;
    const measure = () => {
      if (!el.clientWidth) return;
      const overflows = el.scrollWidth > el.clientWidth + 1;
      setFit((f) => (f?.text === text && f.overflows === overflows ? f : { text, overflows }));
    };
    measure();
    return watchSize(el, measure);
  }, [text, scrolling]);
  return scrolling ? (
    // Marquee puts `style` on its inner element; this outer box is what keeps it inside a row
    <div style={{ minWidth: 0, flex: "0 1 auto", overflow: "hidden" }}>
      {/* speed is in pixels per second; 30 is Steam's own */}
      <Marquee play delay={1} speed={30} fadeLength={8} resetOnPause style={style}>
        {text}
      </Marquee>
    </div>
  ) : (
    <div ref={ref} style={{ ...ellipsis, ...style }}>
      {text}
    </div>
  );
};

/**
 * Title + subtitle lines stacked, truncating instead of wrapping so rows stay one height. Text that
 * doesn't fit scrolls while the row is focused, or all the time with `scroll="always"` (headers and
 * the now-playing card, which aren't focused while you read them).
 */
export const RowText: FC<{
  title: string;
  /** one line, or several */
  subtitle?: string | string[];
  /** shown after the title, e.g. a favourite heart */
  badge?: ReactNode;
  active?: boolean;
  size?: "normal" | "large";
  scroll?: "focus" | "always";
}> = ({ title, subtitle, badge, active, size = "normal", scroll = "focus" }) => {
  const ref = useRef<HTMLDivElement>(null);
  const focused = useRowFocused(ref);
  const play = scroll === "always" || focused;
  const lines = (Array.isArray(subtitle) ? subtitle : [subtitle]).filter((l): l is string => !!l);
  return (
    <div
      ref={ref}
      style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1, textAlign: "left", lineHeight: 1.25 }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        <ScrollingText
          text={title}
          play={play}
          style={{
            fontSize: size === "large" ? 16 : 14,
            fontWeight: size === "large" ? 600 : undefined,
            color: active ? ACCENT : undefined,
            minWidth: 0,
          }}
        />
        {badge}
      </div>
      {lines.map((line, i) => (
        <ScrollingText key={i} text={line} play={play} style={{ fontSize: 12, opacity: 0.6 }} />
      ))}
    </div>
  );
};

/**
 * A row's controller actions, named in Steam's hint bar. The Quick Access panel is narrow: keep the names
 * short ("Play", "Queue", "Options"), or three of them wrap the bar onto a second line.
 */
interface RowActions {
  /** A / tap */
  onActivate: () => void;
  /** what A does, in the hint bar (Steam says "Select" otherwise) */
  activateLabel?: string;
  /** X button */
  onSecondary?: () => void;
  secondaryLabel?: string;
  /** Y / options button; receives the event so menus can anchor to the row */
  onOptions?: (e?: any) => void;
  optionsLabel?: string;
}

/**
 * A full-width list row built on Steam's Field, so focus highlight, padding and separators match
 * native Quick Access lists. Extra actions are bound to controller buttons and shown in the footer.
 */
/**
 * Move mode for a row (the Queue's "Move"): up and down on the d-pad carry it along, A or B puts it
 * down. The list keeps its rows in place and redraws their content (moving the focused element itself
 * would lose Steam's focus), then steps the focus to the row now showing the moved track.
 */
export interface RowMoving {
  /** false at either end of the list, where it can't move */
  onMove: (step: -1 | 1) => boolean;
  onDone: () => void;
}

export const ListRow: FC<
  RowActions & {
    leading?: ReactNode;
    title: string;
    subtitle?: string;
    trailing?: ReactNode;
    active?: boolean;
    moving?: RowMoving;
  }
> = ({
  leading,
  title,
  subtitle,
  trailing,
  active,
  onActivate,
  activateLabel,
  onSecondary,
  secondaryLabel,
  onOptions,
  optionsLabel,
  moving,
}) => {
  const direction = (e: GamepadEvent) => {
    const step = e.detail.button === GamepadButton.DIR_UP ? -1 : e.detail.button === GamepadButton.DIR_DOWN ? 1 : 0;
    if (!moving || !step) return;
    e.preventDefault();
    e.stopPropagation();
    if (!moving.onMove(step)) return; // either end of the list
    // once redrawn, the same press again: this row isn't moving any more, so it's an ordinary focus
    // step, onto the row now showing the moved track (FocusNavController is Steam's own navigation)
    const button = e.detail.button;
    window.setTimeout(() => (window as any).FocusNavController?.DispatchVirtualButtonClick?.(button), 0);
  };
  const done = (e?: any) => {
    e?.stopPropagation?.();
    moving?.onDone();
  };
  return (
    <PanelSectionRow>
      <Field
        focusable
        highlightOnFocus
        bottomSeparator="standard"
        childrenLayout="below"
        onActivate={moving ? done : onActivate}
        onClick={moving ? done : onActivate}
        onOKActionDescription={moving ? "Done" : activateLabel}
        onCancelButton={moving ? done : undefined}
        onCancelActionDescription={moving ? "Done" : undefined}
        onGamepadDirection={moving ? direction : undefined}
        onSecondaryButton={moving ? undefined : onSecondary}
        onSecondaryActionDescription={onSecondary && !moving ? secondaryLabel : undefined}
        onOptionsButton={moving ? undefined : onOptions}
        onOptionsActionDescription={onOptions && !moving ? optionsLabel : undefined}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
          {leading}
          <RowText title={title} subtitle={subtitle} active={active || !!moving} />
          {moving ? (
            <FaSort style={{ color: ACCENT, flexShrink: 0 }} />
          ) : trailing ? (
            <div style={{ fontSize: 12, opacity: 0.6, flexShrink: 0 }}>{trailing}</div>
          ) : null}
        </div>
      </Field>
    </PanelSectionRow>
  );
};

export const CollectionRow: FC<{
  instanceId: string;
  item: Collection;
  onOpen: (c: Collection) => void;
  /** X: play it without opening it */
  onPlay?: () => void;
  onOptions?: (e?: any) => void;
}> = ({ instanceId, item, onOpen, onPlay, onOptions }) => (
  <ListRow
    leading={
      <Art
        instanceId={instanceId}
        art={item.art}
        size={40}
        fallback={KIND_ICONS[item.kind]}
        round={item.kind === "artist"}
      />
    }
    title={item.title}
    subtitle={item.subtitle}
    onActivate={() => onOpen(item)}
    onSecondary={onPlay}
    secondaryLabel="Play"
    onOptions={onOptions}
    optionsLabel="Options"
  />
);

/** An album track's number, in a fixed column so titles line up (accent while it plays). */
export const TrackNumber: FC<{ n: number; digits: number; active?: boolean }> = ({ n, digits, active }) => (
  <span
    style={{
      // as wide as the album's longest number, so the column hugs the left edge
      width: `${digits}ch`,
      flexShrink: 0,
      textAlign: "right",
      fontSize: 13,
      fontVariantNumeric: "tabular-nums",
      opacity: active ? 1 : 0.5,
      color: active ? ACCENT : undefined,
    }}
  >
    {n}
  </span>
);

const Heart: FC = () => <FaHeart style={{ color: ACCENT, fontSize: 11, flexShrink: 0 }} />;

export const TrackRow: FC<
  Omit<RowActions, "onActivate"> & {
    track: Track;
    active?: boolean;
    onPlay: () => void;
    leading?: ReactNode;
    showAlbum?: boolean;
    /** show the track's own artwork (lists mixing albums: home, search, playlists) */
    artInstanceId?: string;
    /** the album's artist: not repeated on its own tracks (an album page), only guests show */
    albumArtist?: string;
    moving?: RowMoving;
  }
> = ({ track, active, onPlay, leading, showAlbum = true, artInstanceId, albumArtist, ...actions }) => (
  <ListRow
    leading={leading ?? (artInstanceId ? <Art instanceId={artInstanceId} art={track.art} size={40} /> : undefined)}
    title={track.title}
    subtitle={[track.artist !== albumArtist && track.artist, showAlbum && track.album].filter(Boolean).join(" · ")}
    trailing={
      <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {track.starred && <Heart />}
        {/* some sources (YouTube Music home rows) don't say how long a track is */}
        {track.duration > 0 && formatTime(track.duration)}
      </span>
    }
    active={active}
    onActivate={onPlay}
    activateLabel="Play"
    {...actions}
  />
);

/** A row of equal-width buttons that lines up with Field content. */
export const ButtonRow: FC<{ children: ReactNode }> = ({ children }) => (
  <PanelSectionRow>
    <Focusable flow-children="row" style={{ display: "flex", gap: 8, padding: "4px 0" }}>
      {children}
    </Focusable>
  </PanelSectionRow>
);

export const IconButton: FC<{
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  label?: string;
  children: ReactNode;
  grow?: number;
  /** "plain": no box until focused or touched (see PLAYER_CSS); "round": a circle, for play/pause */
  look?: "box" | "plain" | "round";
  style?: CSSProperties;
  /** X: a second action on the same button, named in the hint bar */
  onSecondary?: () => void;
  secondaryLabel?: string;
  /** takes the controller's focus when it appears (a page's main button) */
  preferredFocus?: boolean;
}> = ({
  onClick,
  active,
  disabled,
  label,
  children,
  grow = 1,
  look = "box",
  style,
  onSecondary,
  secondaryLabel,
  preferredFocus,
}) => (
  <DialogButton
    onClick={onClick}
    preferredFocus={preferredFocus}
    disabled={disabled}
    onOKActionDescription={label}
    onSecondaryButton={onSecondary}
    onSecondaryActionDescription={onSecondary ? secondaryLabel : undefined}
    className={look === "box" ? undefined : `md-${look}`}
    style={{
      minWidth: 0,
      flex: grow,
      height: 36,
      padding: 0,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      gap: 6,
      fontSize: 13,
      color: active ? ACCENT : undefined,
      ...style,
    }}
  >
    {children}
  </DialogButton>
);

/**
 * The player's light buttons. Steam gives a focused or touched DialogButton its own highlight
 * (`.gpfocus`, `:hover`); "plain" buttons only drop the resting box, so that highlight still shows
 * where the controller is. "round" is play/pause: one filled circle the eye finds first.
 */
export const PLAYER_CSS = `
.md-line { outline: none; transition: background 0.15s; }
.md-line.gpfocus { background: rgba(255, 255, 255, 0.06); }
button.DialogButton.md-plain:not(.gpfocus):not(:hover):not(:active) { background: transparent !important; }
button.DialogButton.md-round { border-radius: 50% !important; flex: 0 0 44px !important; width: 44px; height: 44px !important; }
.md-mini { border-radius: 4px; }
.md-header > * { margin-bottom: 8px !important; }
.md-mini.gpfocus, .md-mini:hover { background: rgba(255, 255, 255, 0.1); }
`;

/** Standalone notice; brings its own section so it gets the same inset as everything else. */
export const Message: FC<{ children: ReactNode }> = ({ children }) => (
  <PanelSection>
    <PanelSectionRow>
      <div style={{ padding: "12px 0", opacity: 0.7, fontSize: 13, textAlign: "center" }}>{children}</div>
    </PanelSectionRow>
  </PanelSection>
);

/** A dialog's action row, built like Steam's own (ConfirmModal): spaced footer, primary first. */
export const DialogActions: FC<{
  primary: ReactNode;
  onPrimary: () => void;
  primaryDisabled?: boolean;
  onCancel?: () => void;
}> = ({ primary, onPrimary, primaryDisabled, onCancel }) => (
  <DialogFooter>
    <Focusable className="DialogTwoColLayout _DialogColLayout" flow-children="row">
      <DialogButtonPrimary onClick={onPrimary} disabled={primaryDisabled}>
        {primary}
      </DialogButtonPrimary>
      <DialogButtonSecondary onClick={onCancel}>Cancel</DialogButtonSecondary>
    </Focusable>
  </DialogFooter>
);

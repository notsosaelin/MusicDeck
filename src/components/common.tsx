import {
  DialogButton,
  DialogButtonPrimary,
  DialogButtonSecondary,
  DialogFooter,
  Field,
  Focusable,
  Marquee,
  PanelSection,
  PanelSectionRow,
} from "@decky/ui";
import { CSSProperties, FC, ReactNode, RefObject, useEffect, useRef, useState } from "react";
import { FaCompactDisc, FaFolder, FaHeart, FaListUl, FaUser } from "react-icons/fa";
import { Collection, CollectionKind, Track } from "../api";
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
};

export const Art: FC<{
  instanceId: string;
  art: string | null;
  size: number;
  fallback?: ReactNode;
  round?: boolean;
}> = ({ instanceId, art, size, fallback, round }) => {
  const [failed, setFailed] = useState<string | null>(null);
  const url = art ? mediaUrl("art", instanceId, art) : undefined;
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
  if (!url || failed === url) return <div style={box}>{fallback ?? <FaCompactDisc />}</div>;
  return (
    <div style={box}>
      <img src={url} width={size} height={size} style={{ objectFit: "cover" }} onError={() => setFailed(url)} />
    </div>
  );
};

export const ellipsis: CSSProperties = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };

/** Title + subtitle stacked, truncating instead of wrapping so rows stay one height. */
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

/** Truncates normally; once its row has been focused for a moment, scrolls to reveal the rest. */
const Scrolling: FC<{ focused: boolean; style: CSSProperties; children: ReactNode }> = ({
  focused,
  style,
  children,
}) =>
  focused ? (
    <Marquee play delay={1} speed={0.5} fadeLength={8} resetOnPause style={style}>
      {children}
    </Marquee>
  ) : (
    <div style={{ ...ellipsis, ...style }}>{children}</div>
  );

/** Title + subtitle stacked, truncating instead of wrapping so rows stay one height. */
export const RowText: FC<{ title: ReactNode; subtitle?: ReactNode; active?: boolean; size?: "normal" | "large" }> = ({
  title,
  subtitle,
  active,
  size = "normal",
}) => {
  const ref = useRef<HTMLDivElement>(null);
  const focused = useRowFocused(ref);
  return (
    <div
      ref={ref}
      style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1, textAlign: "left", lineHeight: 1.25 }}
    >
      <Scrolling
        focused={focused}
        style={{
          fontSize: size === "large" ? 16 : 14,
          fontWeight: size === "large" ? 600 : undefined,
          color: active ? ACCENT : undefined,
        }}
      >
        {title}
      </Scrolling>
      {subtitle ? (
        <Scrolling focused={focused} style={{ fontSize: 12, opacity: 0.6 }}>
          {subtitle}
        </Scrolling>
      ) : null}
    </div>
  );
};

interface RowActions {
  /** A / tap */
  onActivate: () => void;
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
const ListRow: FC<
  RowActions & { leading?: ReactNode; title: ReactNode; subtitle?: ReactNode; trailing?: ReactNode; active?: boolean }
> = ({
  leading,
  title,
  subtitle,
  trailing,
  active,
  onActivate,
  onSecondary,
  secondaryLabel,
  onOptions,
  optionsLabel,
}) => (
  <PanelSectionRow>
    <Field
      focusable
      highlightOnFocus
      bottomSeparator="standard"
      childrenLayout="below"
      onActivate={onActivate}
      onClick={onActivate}
      onSecondaryButton={onSecondary}
      onSecondaryActionDescription={onSecondary ? secondaryLabel : undefined}
      onOptionsButton={onOptions}
      onOptionsActionDescription={onOptions ? optionsLabel : undefined}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        {leading}
        <RowText title={title} subtitle={subtitle} active={active} />
        {trailing ? <div style={{ fontSize: 12, opacity: 0.6, flexShrink: 0 }}>{trailing}</div> : null}
      </div>
    </Field>
  </PanelSectionRow>
);

export const CollectionRow: FC<{
  instanceId: string;
  item: Collection;
  onOpen: (c: Collection) => void;
  onOptions?: (e?: any) => void;
}> = ({ instanceId, item, onOpen, onOptions }) => (
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
    onOptions={onOptions}
    optionsLabel="Options"
  />
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
  }
> = ({ track, active, onPlay, leading, showAlbum = true, artInstanceId, ...actions }) => (
  <ListRow
    leading={leading ?? (artInstanceId ? <Art instanceId={artInstanceId} art={track.art} size={40} /> : undefined)}
    title={track.title}
    subtitle={[track.artist, showAlbum ? track.album : ""].filter(Boolean).join(" · ")}
    trailing={
      <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {track.starred && <Heart />}
        {/* some sources (YouTube Music home rows) don't say how long a track is */}
        {track.duration > 0 && formatTime(track.duration)}
      </span>
    }
    active={active}
    onActivate={onPlay}
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
}> = ({ onClick, active, disabled, label, children, grow = 1 }) => (
  <DialogButton
    onClick={onClick}
    disabled={disabled}
    onOKActionDescription={label}
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
    }}
  >
    {children}
  </DialogButton>
);

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

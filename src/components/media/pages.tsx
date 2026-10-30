import { Focusable, TextField } from "@decky/ui";
import { FC, ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  FaCalendarAlt,
  FaChartLine,
  FaCheck,
  FaCompactDisc,
  FaCompass,
  FaDownload,
  FaEject,
  FaExternalLinkAlt,
  FaFeatherAlt,
  FaFire,
  FaDice,
  FaPlusCircle,
  FaBroadcastTower,
  FaAward,
  FaFolder,
  FaGamepad,
  FaGlobe,
  FaGuitar,
  FaHeart,
  FaHistory,
  FaLanguage,
  FaLayerGroup,
  FaList,
  FaMusic,
  FaPlay,
  FaPlus,
  FaPodcast,
  FaListUl,
  FaRandom,
  FaRegClock,
  FaSearch,
  FaSmile,
  FaStar,
  FaTags,
  FaTrophy,
  FaUser,
  FaUsers,
  FaUsb,
} from "react-icons/fa";
import {
  CenterTabInfo,
  Collection,
  Page,
  Section,
  TabInfo,
  Track,
  discCollection,
  ejectDevice,
  getBrowseInfo,
  getCenterTab,
  isLocalPlaylist,
  libraryHome,
  libraryTab,
  openCollection,
  searchHistory,
} from "../../api";
import { bySource, useLibrarySearch, useMoreSongs, useSearchChoice } from "../../library";
import { SourcePager } from "./sources";
import { continueFrom, useOtherSessions } from "../../devices";
import { canPlayWithGame, musicFor, runningGame, toggleGameMusic } from "../../games";
import { SearchPart, View, centerTab, playFrom, setOpenInCenter } from "../../nav";
import { notify } from "../../notify";
import { hasFeature, player } from "../../player";
import { isMissingArt, useArtLoad } from "../../art";
import { Store, mediaUrl, pluginState, useStore } from "../../store";
import {
  KIND_TITLES,
  callAction,
  canSave,
  groupByKind,
  hasCollectionMenu,
  mostCommon,
  playCollection,
  runAction,
  useLibrarySave,
  useResult,
} from "../Browser";
import { confirmClearQueue } from "../Queue";
import { showCollectionMenu, showTrackMenu } from "../TrackMenu";
import {
  Card,
  CardBlanks,
  CardGrid,
  FittedGrid,
  Cover,
  GroupTitle,
  Loading,
  Notice,
  PageButton,
  SectionTitle,
  ShowAllCard,
  SourceBadges,
  StationCard,
  allStations,
  Shelf,
  Tile,
  TileGrid,
  TrackCard,
  TrackLine,
  TrackTable,
  useCardSize,
  useNear,
  TAB_BAR,
  isFirstRow,
  scrollParent,
} from "./parts";
import { watchSize } from "../common";
import { useDownload, useSavedCopy } from "../../download";

/**
 * Where each browsing tab of the media center is: a history of views, B steps back through it. One per
 * source and tab ("<source id>/<tab id>", the search tab's ending in "/search"), so going back to a
 * source finds its tabs as they were.
 */
export type CenterStack = string;
export const centerNav = new Store<Record<CenterStack, View[]>>({});
export const centerStack = (instanceId: string, tab: string): CenterStack => `${instanceId}/${tab}`;

/** A tab's history; a tab not visited yet is at its start (its page, or an empty search). */
export const viewsOf = (stacks: Record<CenterStack, View[]>, stack: CenterStack): View[] =>
  stacks[stack] ?? [stack.endsWith("/search") ? { type: "search", query: "" } : { type: "home" }];

/** Per tab and depth, the card a page was opened from, so B can put the controller back on it. */
const openedFrom: Record<CenterStack, string[]> = {};
export const cardKey = (c: Collection) => c.kind + ":" + c.id;

/** The tile a source's section is opened from on Browse, to come back to it. */
export const sectionKey = (tab: string) => "section:" + tab;

/** A row's Show all, to come back to it. */
export const allKey = (title: string) => "all:" + title;

export function pushCenter(stack: CenterStack, view: View) {
  const views = viewsOf(centerNav.get(), stack);
  const from =
    view.type === "collection"
      ? cardKey(view.collection)
      : view.type === "section"
        ? sectionKey(view.tab)
        : view.type === "all"
          ? allKey(view.title)
          : null;
  if (from) (openedFrom[stack] ??= [])[views.length - 1] = from;
  centerNav.update({ [stack]: [...views, view] });
}

// "Go to album", "Go to artist" from a track's menu: the page opens in Browse (Home for a source with
// nothing past its Home), over what Browse showed, so B goes back there
setOpenInCenter((collection) => {
  const state = pluginState.get();
  const live = state?.active_service;
  if (!live) return;
  const tabs = state?.services.find((s) => s.id === live)?.tabs ?? [];
  const tab = tabs.some((t) => !["home", "playing", "search", "settings"].includes(t.id)) ? "browse" : "home";
  pushCenter(centerStack(live, tab), { type: "collection", collection });
  centerTab.set({ tab });
});

/** Which card the controller should be on when this tab shows its view at `depth` (if any). */
export const focusKeyFor = (stack: CenterStack, depth: number): string | undefined => openedFrom[stack]?.[depth];

/** False at the start of the tab, so B can leave the media center instead. */
export function popCenter(stack: CenterStack): boolean {
  const views = viewsOf(centerNav.get(), stack);
  if (views.length <= 1) return false;
  centerNav.update({ [stack]: views.slice(0, -1) });
  return true;
}

const addToQueue = (instanceId: string, track: Track) => {
  player.enqueue(instanceId, [track]);
  notify("Added to queue", track.title, { instanceId, track });
};

/** Shelves of collections, a card each; A opens one in this tab. */
/** Rows of cards, one a kind (titled by it when `titled`); or with `title`, one row of them all under that title. */
const Shelves: FC<{ instanceId: string; stack: CenterStack; items: Collection[]; titled: boolean; title?: string }> = ({
  instanceId,
  stack,
  items,
  titled,
  title,
}) => {
  useStore(runningGame); // cards get a menu (play with the game) while one runs
  const rows: [string, Collection[]][] = title
    ? items.length
      ? [[title, items]]
      : []
    : groupByKind(items).map(([kind, list]) => [KIND_TITLES[kind], list]);
  return (
    <>
      {rows.map(([name, list]) => (
        <Shelf key={name} title={titled || title ? name : undefined}>
          {list.slice(0, rowCut(list.length)).map((c) => (
            <Card
              key={c.kind + c.id}
              instanceId={instanceId}
              c={c}
              onOpen={() => pushCenter(stack, { type: "collection", collection: { ...c, source: c.source ?? instanceId } })}
              onPlay={c.kind === "album" || c.kind === "playlist" ? () => playCollection(instanceId, c) : undefined}
              onOptions={
                hasCollectionMenu(instanceId, c)
                  ? (e) => showCollectionMenu(instanceId, c, e?.currentTarget)
                  : undefined
              }
            />
          ))}
          {list.length > ROW_SIZE && rowEnd(stack, name, list, [], instanceId)}
        </Shelf>
      ))}
    </>
  );
};

/** A card for an album, artist or playlist; one from the one library's lists is its own source's. */
const collectionCard = (instanceId: string, stack: CenterStack, c: Collection) => {
  const id = c.source ?? instanceId;
  return (
    <Card
      key={`${c.source ?? ""}${c.kind}${c.id}`}
      instanceId={id}
      c={c}
      onOpen={() => pushCenter(stack, { type: "collection", collection: c.source ? c : { ...c, source: id } })}
      onPlay={c.kind === "album" || c.kind === "playlist" ? () => playCollection(id, c) : undefined}
      onOptions={hasCollectionMenu(id, c) ? (e) => showCollectionMenu(id, c, e?.currentTarget) : undefined}
    />
  );
};

/**
 * One of the source's own tabs (MusicService.tab): its sections, each shown as it asks: shelves of
 * cards, a row of music per genre or mood, tiles to pick from, or every card in a grid.
 */
const SECTION_ICONS: Record<string, ReactNode> = {
  albums: <FaCompactDisc />,
  artists: <FaUser />,
  genres: <FaTags />,
  years: <FaCalendarAlt />,
  composers: <FaFeatherAlt />,
  folders: <FaFolder />,
  devices: <FaUsb />,
  charts: <FaChartLine />,
  moods: <FaSmile />,
  new: <FaStar />,
  library: <FaHeart />,
  discover: <FaCompass />,
  browse: <FaCompass />,
  top: <FaTrophy />,
  bands: <FaGuitar />,
  countries: <FaGlobe />,
  languages: <FaLanguage />,
  authors: <FaUsers />,
  series: <FaLayerGroup />,
  categories: <FaList />,
  yours: <FaPodcast />,
  songs: <FaMusic />,
  playlists: <FaListUl />,
  favourites: <FaHeart />,
  recentplayed: <FaHistory />,
  mostplayed: <FaFire />,
  toprated: <FaStar />,
  unplayed: <FaRegClock />,
  liked: <FaHeart />,
  mixes: <FaRandom />,
  added: <FaPlusCircle />,
  played: <FaHistory />,
  frequent: <FaFire />,
  rated: <FaStar />,
  random: <FaDice />,
  stations: <FaBroadcastTower />,
  soundtracks: <FaGamepad />,
  picks: <FaListUl />,
  "musicdeck-playlists": <FaListUl />,
};

/** A tile's icon: its own, or its kind's ("discover:best-sellers", "genre:Rock") */
const iconFor = (id: string) =>
  SECTION_ICONS[id] ?? (id.startsWith("genre:") ? <FaTags /> : id.startsWith("discover:") ? <FaAward /> : <FaMusic />);

/** Browse's groups, in the order they show: the user's own music first. */
const BROWSE_GROUPS: { id: NonNullable<CenterTabInfo["group"]>; title: string }[] = [
  { id: "yours", title: "Your music" },
  { id: "listening", title: "For you" },
  { id: "explore", title: "Explore" },
  { id: "genres", title: "Top genres" },
  { id: "files", title: "On this Deck" },
];

/** A tile's name or line: one line, cut with "…" (tiles in a row stay the same height) */
const ONE_LINE = { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } as const;

const TILE_BACK = "linear-gradient(135deg, rgba(255, 255, 255, 0.14), rgba(255, 255, 255, 0.04))";

/** One of a mosaic's pictures, shown once it has loaded (art.ts); one that doesn't load drops out. */
const MosaicPicture: FC<{ url?: string; onMissing: () => void }> = ({ url, onMissing }) => {
  const load = useArtLoad(url);
  return (
    <img
      src={url}
      loading="lazy"
      decoding="async"
      onLoad={load.onLoad}
      onError={() => {
        load.onError();
        onMissing();
      }}
      style={{ width: "100%", height: "100%", objectFit: "cover", display: "block", opacity: load.visible ? 1 : 0 }}
    />
  );
};

/** A section's covers as one square picture: four in a 2 by 2, or one filling it when it has fewer. */
const Mosaic: FC<{ urls: string[] }> = ({ urls }) => {
  const [, setFailed] = useState<string[]>([]);
  // known missing ones left out from the start, so the square doesn't rearrange (art.ts)
  const shown = urls.filter((a) => a && !isMissingArt(a));
  const four = shown.length >= 4 ? shown.slice(0, 4) : shown.slice(0, 1);
  if (!four.length) return null;
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "grid",
        gridTemplateColumns: four.length === 4 ? "1fr 1fr" : "1fr",
        gridTemplateRows: four.length === 4 ? "1fr 1fr" : "1fr",
      }}
    >
      {four.map((a) => (
        <MosaicPicture key={a} url={a} onMissing={() => setFailed((f) => [...f, a])} />
      ))}
    </div>
  );
};

/**
 * One of "Your music" (Albums, Artists, Playlists...): a card like an album's, its picture a square of
 * four of its covers (or its icon), its name and how many below.
 */
const LibraryTile: FC<{ instanceId: string; t: CenterTabInfo; open: () => void }> = ({ instanceId, t, open }) => {
  const size = useCardSize();
  // the one library's (every source's albums...): its covers and count from the tab itself, fetched for the tile (and
  // so there at once when it's opened); a source's own come with Browse's info
  const ours = t.id.startsWith("library:") ? t.id.slice(8) : null;
  const tab = useResult<Section[]>(
    ours ? `library:tab:${ours}` : "library:tab:none",
    () => (ours ? libraryTab(ours) : Promise.resolve({ ok: true as const, data: [] })),
    !!ours,
  );
  const merged = ours && tab?.ok ? tab.data[0] : undefined;
  const count = merged ? merged.items.length || merged.tracks?.length || 0 : 0;
  const subtitle = merged ? (count ? `${count.toLocaleString()} ${merged.items.length ? t.title.toLowerCase() : "songs"}` : "") : t.subtitle;
  const urls = merged
    ? [...merged.items.map((c) => c.art && mediaUrl("art", c.source ?? instanceId, c.art)),
       ...(merged.tracks ?? []).map((x) => x.art && mediaUrl("art", x.source ?? instanceId, x.art))]
        .filter((u): u is string => !!u)
        .slice(0, 12)
    : (t.art ?? []).map((a) => mediaUrl("art", instanceId, a) ?? "");
  return (
    <Focusable
      className="mc-card"
      data-mc={sectionKey(t.id)}
      noFocusRing
      onActivate={open}
      onClick={open}
      onOKActionDescription="Open"
      style={{ width: size, flexShrink: 0, padding: 2 }}
    >
      <div
        className="mc-art"
        style={{
          position: "relative",
          width: size,
          height: size,
          borderRadius: 6,
          overflow: "hidden",
          background: TILE_BACK,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: `calc(${size} * 0.3)`,
          color: "rgba(255, 255, 255, 0.45)",
        }}
      >
        {urls.length ? <Mosaic urls={urls} /> : iconFor(ours ?? t.id)}
      </div>
      <div style={{ marginTop: 10 }}>
        <div style={{ ...ONE_LINE, fontSize: 14, fontWeight: 600, color: "#ffffff" }}>{t.title}</div>
        {subtitle && <div style={{ ...ONE_LINE, fontSize: 12, opacity: 0.6, marginTop: 2 }}>{subtitle}</div>}
      </div>
    </Focusable>
  );
};

/**
 * The media center's Browse: the source's own sections past its Home, so the tab bar keeps to Now
 * Playing, Home, Browse, Search and Settings. Grouped as the source says (MusicService.center_tabs):
 * the user's own music first, large, with its covers; then the ways into the catalogue (genres, charts,
 * countries...) and folders, compact. Each opens as a page in Browse's history.
 */
export const BrowseIndex: FC<{ instanceId: string; sections: CenterTabInfo[]; stack: CenterStack }> = ({
  instanceId,
  sections,
  stack,
}) => {
  // what's in each tile and row, as it arrives (the tiles show at once, their lines, covers and cards a
  // moment later)
  const info = useResult(`${instanceId}:browseinfo`, () => getBrowseInfo(instanceId), true);
  const filled = sections.map((t): CenterTabInfo => {
    const more = info?.ok ? info.data[t.id] : undefined;
    return more ? { ...t, subtitle: t.subtitle || more.subtitle, art: t.art?.length ? t.art : more.art } : { ...t };
  });
  const groups = BROWSE_GROUPS.map((g) => ({
    ...g,
    tabs: filled.filter((t) => (t.group ?? "explore") === g.id),
  })).filter((g) => g.tabs.length);
  // each library tile's covers, as far as it has them, not ones a tile before it shows
  const used = new Set<string>();
  for (const t of groups.find((g) => g.id === "yours")?.tabs ?? []) {
    if (!t.art?.length) continue;
    const fresh = t.art.filter((a) => !used.has(a));
    if (fresh.length) t.art = [...fresh, ...t.art.filter((a) => used.has(a))];
    t.art.slice(0, 4).forEach((a) => used.add(a));
  }
  const open = (t: CenterTabInfo) => () => pushCenter(stack, { type: "section", tab: t.id, title: t.title });
  return (
    <>
      {groups.map((g) =>
        g.id === "yours" ? (
          // the library: a row of its parts, each a square of its covers, a press into each
          <Shelf key={g.id} title={g.title}>
            {g.tabs.map((t) => (
              <LibraryTile key={t.id} instanceId={instanceId} t={t} open={open(t)} />
            ))}
          </Shelf>
        ) : (
          // the rest: a row of each, its first cards then Show all (its whole grid), each row's title
          // saying what it is
          g.tabs.map((t) => (
            <BrowseRow
              key={t.id}
              instanceId={instanceId}
              stack={stack}
              t={t}
              info={info ? (info.ok ? (info.data[t.id] ?? null) : null) : undefined}
              open={open(t)}
            />
          ))
        ),
      )}
    </>
  );
};

/**
 * One of a source's sections on Browse as a row: its first albums, playlists, songs or stations (or its
 * genres, countries... as tiles), ending in Show all, which opens all of it. `info` undefined while it
 * loads, null when it couldn't (the row is then just its Show all).
 */
const BrowseRow: FC<{
  instanceId: string;
  stack: CenterStack;
  t: CenterTabInfo;
  info: TabInfo | null | undefined;
  above?: ReactNode;
  open: () => void;
}> = ({ instanceId, stack, t, info, above, open }) => {
  if (info === undefined)
    return (
      <Shelf title={t.title} above={above}>
        <CardBlanks />
      </Shelf>
    );
  const items = info?.items ?? [];
  const tracks = info?.tracks ?? [];
  // genres, moods, countries...: a row of each one's music (Pop/Rock's albums, then Jazz's...), each
  // ending in its Show all; past the first of them, a last row's Show all opens the list of every one
  const categories = items.length > 0 && items.every((c) => !c.art && c.kind === "list");
  if (categories)
    return (
      <>
        {items.map((c) => (
          <CategoryRow key={c.kind + c.id} instanceId={instanceId} stack={stack} category={c} />
        ))}
        {(info?.count ?? 0) > items.length && (
          <Shelf title={`All ${t.title.toLowerCase()}`}>
            <ShowAllCard onOpen={open} count={t.subtitle} />
          </Shelf>
        )}
      </>
    );
  return (
    <Shelf title={t.title} above={above}>
      {items.map((c) => collectionCard(instanceId, stack, c))}
      {tracks.map((t, i) =>
        t.live ? (
          <StationCard
            key={t.id + i}
            instanceId={instanceId}
            track={t}
            onPlay={() => player.playList(instanceId, [t], 0)}
            onOptions={(e) => showTrackMenu(instanceId, t, e?.currentTarget)}
          />
        ) : (
          <TrackCard
            key={t.id + i}
            instanceId={instanceId}
            track={t}
            onPlay={() => player.playList(instanceId, tracks, i)}
            onSecondary={() => addToQueue(instanceId, t)}
          />
        ),
      )}
      <ShowAllCard onOpen={open} count={t.subtitle} />
    </Shelf>
  );
};

/** `after`: shown under the tab's sections once they're in (a source's Browse past its Home), so nothing below
 *  them arrives first and takes the controller. */
export const TabPage: FC<{ instanceId: string; tab: string; stack: CenterStack; after?: ReactNode }> = ({
  instanceId,
  tab,
  stack,
  after,
}) => {
  useStore(runningGame); // cards get a menu (play with the game) while one runs
  // the one library's Home and Your music ("library:..."), else the source's own tab
  const ours = tab.startsWith("library:") ? tab.slice(8) : null;
  const result = useResult<Section[]>(
    ours ? `library:tab:${ours}` : `${instanceId}:tab:${tab}`,
    () => (ours === "home" ? libraryHome() : ours ? libraryTab(ours) : getCenterTab(instanceId, tab)),
    true,
  );
  // Home: your devices first (your other MusicDeck devices to continue from, the disc, what's plugged in)
  const home = ours === "home" ? <YourDevicesShelf stack={stack} /> : null;
  if (!result) return null;
  if (!result.ok) return after ? <>{home}{after}</> : <Loading result={result} />;
  const sections = result.data.filter((s) => s.items.length || s.tracks?.length);
  const notes = result.data.filter((s) => s.notice);
  if (!sections.length)
    return (
      <>
        {notes.length ? <Notice>{notes.map((s) => s.notice).join(" ")}</Notice> : home || null}
        {after}
      </>
    );
  // a tab of several groups of rows names each group
  const headed = sections.filter((s) => s.display === "rows").length > 1;
  return (
    <>
      {home}
      {sections.map((s) => (
        <SectionView key={s.title} instanceId={instanceId} stack={stack} section={s} headed={headed} />
      ))}
      {after}
    </>
  );
};

/** What the user's other devices were playing: a card each, played here from where it was (the other stops). */
const DEVICE_KINDS: Record<string, string> = { mtp: "Phone", ios: "iPhone", disc: "Disc", usb: "USB", cd: "CD" };

/** Browse's Your library: what's plugged in (phones, players, sticks, CDs), a card each; each opens its page. */
export const DevicesShelf: FC<{ instanceId: string; stack: CenterStack }> = ({ instanceId, stack }) => {
  const state = useStore(pluginState);
  // what's in the disc drive is the Disc row's, not a device of its own
  const devices = (state?.devices ?? []).filter((d) => d.kind !== "cd" && d.kind !== "disc");
  const disc = state?.disc;
  return (
    <>
      {disc && (
        <Shelf title="Disc">
          {collectionCard(disc.source, stack, discCollection(disc))}
        </Shelf>
      )}
      {devices.length > 0 && (
        <Shelf title="Devices">
          {devices.map((d) =>
            collectionCard(instanceId, stack, {
              id: `dv~${d.id}~`,
              kind: "device",
              title: d.name,
              subtitle: d.kind === "ipod" ? d.status?.model || "iPod" : (DEVICE_KINDS[d.kind] ?? "USB"),
              art: null,
            }),
          )}
        </Shelf>
      )}
    </>
  );
};

/**
 * Home's first row, when there's anything in it: your other MusicDeck devices that have something to continue (its
 * cover, the device's name, what it's playing; played here from where it is there, and it stops), the disc, and what's
 * plugged in (each opens its page).
 */
const YourDevicesShelf: FC<{ stack: CenterStack }> = ({ stack }) => {
  const state = useStore(pluginState);
  const sessions = useOtherSessions();
  const devices = (state?.devices ?? []).filter((d) => d.kind !== "cd" && d.kind !== "disc");
  const disc = state?.disc;
  const local = state?.services.find((s) => s.type === "local")?.id;
  const box = useRef<HTMLDivElement>(null);
  const shown = sessions.length > 0 || !!disc || (!!local && devices.length > 0);
  useEffect(() => {
    // it came after Home opened (the controller went to Jump back in's first card): the controller comes up to it,
    // unless it has been moved since
    if (!shown) return;
    const first = box.current?.querySelector<HTMLElement>(".mc-card");
    const below = box.current?.nextElementSibling?.querySelector<HTMLElement>(".mc-card");
    if (first && below && box.current?.ownerDocument.activeElement === below) first.focus({ preventScroll: true });
  }, [shown]);
  if (!shown) return null;
  const continueHere = async (s: (typeof sessions)[number]) => {
    // its card goes once it plays here (and the row with it, when it was the only one): the focus moves on to the
    // next card, or the next row's first, rather than being lost
    const cards = [...(box.current?.querySelectorAll<HTMLElement>(".mc-card") ?? [])];
    const at = cards.findIndex((c) => c.contains(document.activeElement));
    const after = box.current?.nextElementSibling?.querySelector<HTMLElement>(".mc-card");
    await continueFrom(s);
    window.setTimeout(() => {
      if (document.activeElement && document.activeElement !== document.body && document.activeElement.isConnected) return;
      const next = cards.slice(at + 1).find((c) => c.isConnected) ?? cards.find((c) => c.isConnected) ?? after;
      next?.focus();
    }, 300);
  };
  return (
    <div ref={box} style={{ display: "contents" }}>
      <Shelf title="Your devices">
        {sessions.map((s) => (
          <TrackCard
            key={`peer:${s.peer}`}
            instanceId={s.instanceId}
            track={{ ...s.track, title: s.name, artist: `${s.playing ? "Playing" : "Paused"}: ${s.track.title}` }}
            onPlay={() => void continueHere(s)}
          />
        ))}
        {disc && collectionCard(disc.source, stack, discCollection(disc))}
        {local &&
          devices.map((d) =>
            collectionCard(local, stack, {
              id: `dv~${d.id}~`,
              kind: "device",
              title: d.name,
              subtitle: d.kind === "ipod" ? d.status?.model || "iPod" : (DEVICE_KINDS[d.kind] ?? "USB"),
              art: null,
            }),
          )}
      </Shelf>
    </div>
  );
};

/**
 * A section, its cards saying which source each is from, unless the whole row is one source's and its title says so
 * (Home's "Qobuz · New releases").
 */
const SectionView: FC<{ instanceId: string; stack: CenterStack; section: Section; headed: boolean }> = (
  props,
) => {
  const s = props.section;
  const first = s.items[0]?.source;
  const name = first ? pluginState.get()?.services.find((x) => x.id === first)?.name : undefined;
  const named = !!name && s.title.startsWith(name) && s.items.every((c) => c.source === first);
  return (
    <SourceBadges.Provider value={!named}>
      <SectionContent {...props} />
    </SourceBadges.Provider>
  );
};

const SectionContent: FC<{ instanceId: string; stack: CenterStack; section: Section; headed: boolean }> = ({
  instanceId,
  stack,
  section: s,
  headed,
}) => {
  // the one library's songs from several sources: a list for each, under its name (a list plays from one source)
  const parts = bySource(s.tracks ?? [], instanceId);
  if (parts.length > 1) {
    const names = pluginState.get()?.services ?? [];
    const name = (id: string) => names.find((x) => x.id === id)?.name ?? "";
    return (
      <>
        {s.items.length > 0 && <SectionView instanceId={instanceId} stack={stack} section={{ ...s, tracks: [] }} headed={headed} />}
        {parts.map((p) => (
          <SectionView
            key={p.source}
            instanceId={p.source}
            stack={stack}
            section={{ ...s, title: [s.title, name(p.source)].filter(Boolean).join(" · "), items: [], tracks: p.list }}
            headed={headed}
          />
        ))}
      </>
    );
  }
  if (parts.length === 1 && parts[0].source !== instanceId)
    return <SectionView instanceId={parts[0].source} stack={stack} section={s} headed={headed} />;
  const tracks = s.tracks ?? [];
  const songs = tracks.length > 0 && (
    <div style={{ marginBottom: 28 }}>
      {(!s.items.length || s.display === "grid") && <SectionTitle>{s.title}</SectionTitle>}
      <TrackTable
        instanceId={instanceId}
        tracks={tracks}
        limit={s.display === "grid" ? undefined : 6}
        onPlay={(i) => player.playList(instanceId, tracks, i)}
        onSecondary={(i) => addToQueue(instanceId, tracks[i])}
        onOptions={(i, e) => showTrackMenu(instanceId, tracks[i], e?.currentTarget)}
      />
    </div>
  );
  if (s.display === "rows")
    return (
      <>
        {headed && <GroupTitle>{s.title}</GroupTitle>}
        {s.items.map((c) => (
          <CategoryRow key={`${c.kind}:${c.id}`} instanceId={instanceId} stack={stack} category={c} />
        ))}
      </>
    );
  if (s.display === "tiles")
    return (
      <TileGrid title={<SectionTitle>{s.title}</SectionTitle>}>
        {s.items.map((c) => (
          <Tile key={c.kind + c.id} c={c} onOpen={() => pushCenter(stack, { type: "collection", collection: { ...c, source: c.source ?? instanceId } })} />
        ))}
      </TileGrid>
    );
  if (s.display === "covers")
    return (
      <Shelf title={s.title}>
        {s.items.slice(0, rowCut(s.items.length + tracks.length)).map((c) => collectionCard(instanceId, stack, c))}
        {tracks.slice(0, Math.max(0, rowCut(s.items.length + tracks.length) - s.items.length)).map((t, i) =>
          t.live ? (
            // a station plays on its own: a shelf of them isn't a playlist
            <StationCard
              key={t.id + i}
              instanceId={instanceId}
              track={t}
              onPlay={() => player.playList(instanceId, [t], 0)}
              onOptions={(e) => showTrackMenu(instanceId, t, e?.currentTarget)}
            />
          ) : (
            <TrackCard
              key={t.id + i}
              instanceId={instanceId}
              track={t}
              onPlay={() => player.playList(instanceId, tracks, i)}
              onSecondary={() => addToQueue(instanceId, t)}
            />
          ),
        )}
        {s.items.length + tracks.length > ROW_SIZE && rowEnd(stack, s.title, s.items, tracks)}
      </Shelf>
    );
  if (s.display === "grid")
    return (
      <>
        {s.items.length > 0 && (
          <CardGrid title={s.title} count={s.items.length}>
            {(n) => s.items.slice(0, n).map((c) => collectionCard(instanceId, stack, c))}
          </CardGrid>
        )}
        {songs}
      </>
    );
  return (
    <>
      {s.items.length > 0 && (
        <Shelf title={s.title}>
          {s.items.slice(0, rowCut(s.items.length)).map((c) => collectionCard(instanceId, stack, c))}
          {s.items.length > ROW_SIZE && rowEnd(stack, s.title, s.items, [])}
        </Shelf>
      )}
      {songs}
    </>
  );
};

/** Cards a category row shows before its "All" card */
const ROW_SIZE = 16;

/** How many of a row's items it shows: all of a short one, a long one's first before its Show all */
const rowCut = (n: number) => (n > ROW_SIZE ? ROW_SIZE - 1 : n);

/** A long row's last card: Show all, opening every item as a grid (songs as a list) */
const rowEnd = (stack: CenterStack, title: string, items: Collection[], tracks: Track[], instanceId?: string) => {
  const kinds = new Set(items.map((c) => c.kind));
  const noun = items.length
    ? kinds.size === 1 && ["album", "artist", "playlist"].includes(items[0].kind)
      ? items[0].kind
      : "item"
    : tracks.every((t) => t.live)
      ? "station"
      : "song";
  return (
    <ShowAllCard
      key="all"
      mcKey={allKey(title)}
      onOpen={() => pushCenter(stack, { type: "all", title, items, tracks, instanceId })}
      count={countOf(items.length + tracks.length, noun)}
    />
  );
};

/**
 * Every item of a row, from its Show all: albums, playlists and artists in the standard grid, stations
 * in theirs, songs as a list.
 */
export const AllPage: FC<{
  instanceId: string;
  stack: CenterStack;
  title: string;
  items: Collection[];
  tracks: Track[];
  /** a search's list that can go on ("Load more" at its end) */
  more?: SearchPart;
}> = ({ instanceId, stack, title, items, tracks: first, more }) => {
  const { tracks, load, loading } = useMoreSongs(instanceId, first, more);
  return (
  <>
    {items.length > 0 && (
      <CardGrid title={title} count={items.length}>
        {(n) => items.slice(0, n).map((c) => collectionCard(instanceId, stack, c))}
      </CardGrid>
    )}
    {tracks.length > 0 &&
      (allStations(tracks) ? (
        <FittedGrid title={<SectionTitle>{items.length ? "Stations" : title}</SectionTitle>}>
          {tracks.map((t, i) => (
            <StationCard
              key={t.id + i}
              instanceId={instanceId}
              track={t}
              width="100%"
              onPlay={() => player.playList(instanceId, [t], 0)}
              onOptions={(e) => showTrackMenu(instanceId, t, e?.currentTarget)}
            />
          ))}
        </FittedGrid>
      ) : (
        <div style={{ marginBottom: 28 }}>
          <SectionTitle>{items.length ? "Songs" : title}</SectionTitle>
          <TrackTable
            instanceId={instanceId}
            tracks={tracks}
            onPlay={(i) => player.playList(instanceId, tracks, i)}
            onSecondary={(i) => addToQueue(instanceId, tracks[i])}
            onOptions={(i, e) => showTrackMenu(instanceId, tracks[i], e?.currentTarget)}
          />
          {load && (
            <Focusable
              className="mc-row"
              noFocusRing
              onActivate={load}
              onClick={load}
              style={{ padding: "10px 12px", fontSize: 15, opacity: 0.75 }}
            >
              {loading ? "Loading…" : "Load more"}
            </Focusable>
          )}
        </div>
      ))}
  </>
  );
};

/** "12 albums", "1 song" */
const countOf = (n: number, noun: string) => `${n.toLocaleString()} ${noun}${n === 1 ? "" : "s"}`;

/**
 * One genre, mood or chart on Home: a row of its albums and playlists (or songs and stations), ending
 * in a card for its whole page. Loaded once it comes near the screen; a category with nothing in it
 * leaves no row.
 */
const CategoryRow: FC<{ instanceId: string; stack: CenterStack; category: Collection }> = ({
  instanceId,
  stack,
  category,
}) => {
  const box = useRef<HTMLDivElement>(null);
  const near = useNear(box);
  return (
    <div ref={box}>
      {near ? (
        <CategoryShelf instanceId={instanceId} stack={stack} category={category} />
      ) : (
        <Shelf title={category.title}>
          <CardBlanks />
        </Shelf>
      )}
    </div>
  );
};

const CategoryShelf: FC<{ instanceId: string; stack: CenterStack; category: Collection }> = ({
  instanceId,
  stack,
  category,
}) => {
  const key = `${instanceId}:${category.kind}:${category.id}`;
  const result = useResult<Page>(key, () => openCollection(instanceId, category.kind, category.id));
  if (!result)
    return (
      <Shelf title={category.title}>
        <CardBlanks />
      </Shelf>
    );
  if (!result.ok || (!result.data.collections.length && !result.data.tracks.length)) return null;
  const { collections, tracks } = result.data;
  const open = () => pushCenter(stack, { type: "collection", collection: { ...category, source: category.source ?? instanceId } });
  return (
    <Shelf title={category.title}>
      {collections.length
        ? collections
            .slice(0, ROW_SIZE)
            .map((c) => (
              <Card
                key={c.kind + c.id}
                instanceId={instanceId}
                c={c}
                onOpen={() => pushCenter(stack, { type: "collection", collection: { ...c, source: c.source ?? instanceId } })}
                onPlay={c.kind === "album" || c.kind === "playlist" ? () => playCollection(instanceId, c) : undefined}
              />
            ))
        : tracks
            .slice(0, ROW_SIZE)
            .map((t, i) => (
              <TrackCard
                key={t.id + i}
                instanceId={instanceId}
                track={t}
                onPlay={() => player.playList(instanceId, tracks, i)}
                onSecondary={() => addToQueue(instanceId, t)}
              />
            ))}
      <ShowAllCard
        onOpen={open}
        count={collections.length ? countOf(collections.length, collections[0].kind) : countOf(tracks.length, "song")}
      />
    </Shelf>
  );
};

/**
 * Your library as rows: every source's playlists, albums, artists and songs (each part of the one library), a row
 * or a short list each, ending in Show all; each card with its source on its cover.
 */
export const LibraryRows: FC<{ instanceId: string; stack: CenterStack; parts: CenterTabInfo[] }> = ({
  instanceId,
  stack,
  parts,
}) => (
  <>
    {parts.map((t) => (
      <LibraryRow key={t.id} instanceId={instanceId} stack={stack} part={t} />
    ))}
  </>
);

const LibraryRow: FC<{ instanceId: string; stack: CenterStack; part: CenterTabInfo }> = ({ instanceId, stack, part }) => {
  const id = part.id.startsWith("library:") ? part.id.slice(8) : part.id;
  const result = useResult<Section[]>(`library:tab:${id}`, () => libraryTab(id), true);
  if (!result?.ok) return null;
  const items = result.data.flatMap((s) => s.items);
  const tracks = result.data.flatMap((s) => s.tracks ?? []);
  if (!items.length && !tracks.length) return null;
  return <SectionView instanceId={instanceId} stack={stack} section={{ title: part.title, items, tracks }} headed={false} />;
};

/** A long paragraph as pieces of whole sentences, each short enough to be on screen whole (a line the controller
 *  rests on taller than the screen would hide its end). */
function readable(paragraph: string): string[] {
  const sentences = paragraph.match(/[^.!?]+[.!?]+["')\]]*\s*|[^.!?]+$/g) ?? [paragraph];
  const pieces: string[] = [];
  for (const s of sentences) {
    const last = pieces.length - 1;
    if (last >= 0 && pieces[last].length + s.length <= 420) pieces[last] += s;
    else pieces.push(s);
  }
  return pieces.map((p) => p.trim()).filter(Boolean);
}

/** A page's About (an artist's biography): a paragraph to a line, the controller resting on each as it reads down. */
const AboutText: FC<{ text: string }> = ({ text }) => (
  <Focusable flow-children="column" style={{ marginBottom: 28 }}>
    <SectionTitle>About</SectionTitle>
    {text
      .split("\n")
      .filter((p) => p.trim())
      .flatMap(readable)
      .map((p, i) => (
        <Focusable
          key={i}
          className="mc-line"
          noFocusRing
          onActivate={() => {}}
          style={{ padding: "6px 10px", fontSize: 16, lineHeight: 1.45, opacity: 0.9 }}
        >
          {p}
        </Focusable>
      ))}
  </Focusable>
);

/** What a page holds below its header: shelves of collections and lists of tracks. */
const PageContent: FC<{ instanceId: string; stack: CenterStack; page: Page; from?: Collection }> = ({
  instanceId,
  stack,
  page,
  from,
}) => {
  const showAlbum = new Set(page.tracks.map((t) => t.album)).size > 1;
  const mixed = page.collections.length > 0 || !!page.extra?.length || !!page.shelves?.length;
  const playlistId = from && isLocalPlaylist(from.id) ? from.id : undefined;
  const numbered = from?.kind === "album" && !showAlbum && !mixed && !hasFeature(instanceId, "progress");
  // a long list (a genre's albums, a decade's, a chart's): the standard grid, as every Show all is; a page
  // that's mostly its tracks (an artist's) keeps its albums to a row above them
  const listing = page.collections.length > 0 && (from?.kind === "list" || !page.tracks.length);
  const shelves =
    page.collections.length > 0 &&
    (listing ? (
      groupByKind(page.collections).map(([kind, list]) => (
        <CardGrid key={kind} title={KIND_TITLES[kind] ?? "Albums"} count={list.length}>
          {(n) => list.slice(0, n).map((c) => collectionCard(instanceId, stack, c))}
        </CardGrid>
      ))
    ) : (
      <Shelves instanceId={instanceId} stack={stack} items={page.collections} titled />
    ));
  if (allStations(page.tracks)) {
    // stations: a grid of them, each played on its own, but in a list of your own stations from there on
    // (the previous and next stations are the list's)
    const ownList = !!playlistId;
    return (
      <>
        {shelves}
        <FittedGrid title={mixed && <SectionTitle>Stations</SectionTitle>}>
          {page.tracks.map((t, i) => (
            <StationCard
              key={t.id + i}
              instanceId={instanceId}
              track={t}
              width="100%"
              onPlay={() =>
                ownList && from ? playFrom(instanceId, from, page.tracks, i) : player.playList(instanceId, [t], 0)
              }
              onOptions={(e) => showTrackMenu(instanceId, t, e?.currentTarget, { playlistId })}
            />
          ))}
        </FittedGrid>
      </>
    );
  }
  const popular = from?.kind === "artist" && mixed;
  const tracks = (
    <>
      {page.tracks.length > 0 && (
        <div style={{ marginBottom: 28 }}>
          {mixed && <SectionTitle>{page.tracks_first ? "Songs" : popular ? "Popular" : "Tracks"}</SectionTitle>}
          <TrackTable
            instanceId={instanceId}
            tracks={page.tracks}
            numbered={numbered}
            showAlbum={showAlbum}
            // an artist's songs are their most played: a few, the rest a press away, their releases soon after
            limit={mixed && !from ? 8 : popular ? 10 : undefined}
            onPlay={(i) =>
              from ? playFrom(instanceId, from, page.tracks, i) : player.playList(instanceId, page.tracks, i)
            }
            onSecondary={(i) => addToQueue(instanceId, page.tracks[i])}
            onOptions={(i, e) => showTrackMenu(instanceId, page.tracks[i], e?.currentTarget, { playlistId })}
          />
        </div>
      )}
      {page.extra?.map((x) => (
        <div key={x.title} style={{ marginBottom: 28 }}>
          <SectionTitle>{x.title}</SectionTitle>
          <TrackTable
            instanceId={instanceId}
            tracks={x.tracks}
            limit={8}
            onPlay={(i) => player.playList(instanceId, x.tracks, i)}
            onSecondary={(i) => addToQueue(instanceId, x.tracks[i])}
            onOptions={(i, e) => showTrackMenu(instanceId, x.tracks[i], e?.currentTarget)}
          />
        </div>
      ))}
    </>
  );
  return (
    <>
      {page.notice && <Notice>{page.notice}</Notice>}
      {page.tracks_first ? tracks : shelves}
      {page.tracks_first ? shelves : tracks}
      {page.shelves?.map((shelf) => (
        <Shelves key={shelf.title} instanceId={instanceId} stack={stack} items={shelf.items} titled title={shelf.title} />
      ))}
      {page.about && <AboutText text={page.about} />}
      {!page.tracks.length && !page.collections.length && !page.extra?.length && !page.shelves?.length && !page.notice && (
        <Notice>Empty</Notice>
      )}
    </>
  );
};

/**
 * An album, playlist, artist or folder: a large header (cover, title, Play, Shuffle and what else the
 * page offers), then its contents.
 */
export const CollectionPage: FC<{ instanceId: string; stack: CenterStack; collection: Collection }> = ({
  instanceId,
  stack,
  collection,
}) => {
  const result = useResult<Page>(`${instanceId}:${collection.kind}:${collection.id}`, () =>
    openCollection(instanceId, collection.kind, collection.id),
  );
  const page = result?.ok ? result.data : null;
  const game = useStore(runningGame);
  useStore(pluginState); // the game music choices
  const withGame = !!game && musicFor(game.appid, instanceId)?.collection.id === collection.id;
  const saved = useLibrarySave(instanceId, collection, page);
  const download = useDownload(instanceId, collection);
  const copy = useSavedCopy(instanceId, collection);
  const pictured = collection.kind !== "list" && collection.kind !== "folder";
  // a device's own page (a disc in the drive, a player): what it is, and Eject
  const device =
    collection.kind === "device" ? pluginState.get()?.devices.find((d) => collection.id === `dv~${d.id}~`) : undefined;
  // the disc ejected, the player unplugged: its page goes (there's nothing left to show or play)
  const wasThere = useRef(false);
  useEffect(() => {
    if (device) wasThere.current = true;
    else if (wasThere.current) popCenter(stack);
  }, [!!device]);
  const albumArtist = page && collection.kind === "album" ? mostCommon(page.tracks.map((t) => t.artist)) : undefined;
  const total = page?.tracks.reduce((sum, t) => sum + (t.duration || 0), 0) ?? 0;
  const byline = albumArtist ?? page?.subtitle ?? collection.subtitle;
  // the source's own line may already count them ("114 tracks")
  const stations = !!page && allStations(page.tracks);
  const facts =
    page?.tracks.length && !/\b(tracks?|songs?|episodes?|stations?)\b/i.test(byline)
      ? stations
        ? [`${page.tracks.length} ${page.tracks.length === 1 ? "station" : "stations"}`]
        : [
            `${page.tracks.length} ${page.tracks.length === 1 ? "song" : "songs"}`,
            total ? `${Math.round(total / 60)} min` : "",
          ]
      : [];

  return (
    <>
      {/* a block snapRows brings into view whole: moving up to Play shows the cover and title too */}
      <div className="mc-row-block mc-page-head" style={{ display: "flex", gap: 32, alignItems: "flex-end", marginBottom: 28 }}>
        {pictured && (
          <Cover
            instanceId={instanceId}
            c={{ ...collection, art: page?.art ?? collection.art }}
            size="clamp(96px, 28vh, 220px)"
          />
        )}
        <div style={{ minWidth: 0, flex: 1, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 13, letterSpacing: 1.5, textTransform: "uppercase", opacity: 0.6 }}>
            {device?.kind === "cd" ? "Audio CD" : device?.kind === "disc" ? "Data disc" : KIND_TITLES[collection.kind].replace(/s$/, "")}
          </div>
          <div style={{ fontSize: 26, fontWeight: 700, color: "#ffffff", lineHeight: 1.15 }}>
            {page?.title ?? collection.title}
          </div>
          <div style={{ fontSize: 16, opacity: 0.75 }}>{[byline, ...facts].filter(Boolean).join(" · ")}</div>
          <Focusable flow-children="row" style={{ display: "flex", gap: 12, marginTop: 10, flexWrap: "wrap" }}>
            {page && page.tracks.length > 0 && (!stations || isLocalPlaylist(collection.id)) && (
              <>
                <PageButton
                  primary
                  label="Play"
                  onClick={() => playFrom(instanceId, collection, page.tracks, 0, false)}
                >
                  <FaPlay /> Play
                </PageButton>
                {!stations && (
                  <PageButton label="Shuffle" onClick={() => playFrom(instanceId, collection, page.tracks, 0, true)}>
                    <FaRandom /> Shuffle
                  </PageButton>
                )}
              </>
            )}
            {download && page && page.tracks.length > 0 && (
              <PageButton active={download.on} label={download.label} onClick={download.act}>
                <FaDownload /> {download.label}
              </PageButton>
            )}
            {canSave(instanceId, collection) && page && page.tracks.length > 0 &&
              (copy.copy ? (
                // saved already (from wherever this was opened): it opens your copy
                <PageButton active label="Open your saved playlist" onClick={() => pushCenter(stack, { type: "collection", collection: { ...copy.copy!, source: copy.copy!.source ?? instanceId } })}>
                  <FaCheck /> Saved
                </PageButton>
              ) : (
                <PageButton label="Save playlist" onClick={copy.save}>
                  <FaPlus /> Save
                </PageButton>
              ))}
            {saved && (
              <PageButton active={saved.on} onClick={saved.toggle}>
                {saved.icon} {saved.label}
              </PageButton>
            )}
            {game && canPlayWithGame(collection) && page && page.tracks.length > 0 && (
              <PageButton active={withGame} onClick={() => toggleGameMusic(instanceId, collection)}>
                <FaGamepad /> {withGame ? "Playing with game" : "Play with game"}
              </PageButton>
            )}
            {page?.action && (
              <PageButton
                active={page.action.call === "unsubscribe"}
                onClick={() =>
                  page.action!.call
                    ? callAction(instanceId, collection, page.action!.call)
                    : runAction(page.action!.url ?? "")
                }
              >
                {page.action.call ? (
                  page.action.call === "unsubscribe" ? (
                    <FaCheck />
                  ) : (
                    <FaPlus />
                  )
                ) : page.action.url?.startsWith("steam://") ? (
                  <FaDownload />
                ) : (
                  <FaExternalLinkAlt />
                )}{" "}
                {page.action.label}
              </PageButton>
            )}
            {device && (
              <PageButton
                onClick={async () => {
                  const r = await ejectDevice(device.id);
                  notify(device.name, r.ok ? r.data : r.error);
                  if (r.ok) popCenter(stack);
                }}
              >
                <FaEject /> {device.kind === "cd" || device.kind === "disc" ? "Eject disc" : "Eject"}
              </PageButton>
            )}
          </Focusable>
        </div>
      </div>
      {page ? (
        <PageContent instanceId={instanceId} stack={stack} page={page} from={collection} />
      ) : (
        <Loading result={result} />
      )}
    </>
  );
};

/** A search field (Steam's on-screen keyboard opens on it), recent searches, then results. */
/** What was last typed in the search field: there again when B comes back to it from the results */
const searchDraft = new Store<string>("");

/**
 * Search: the field (and the recent searches under it); a search opens its results as a page of their own, over the
 * field, which B goes back to.
 */
export const SearchPage: FC<{ instanceId: string; stack: CenterStack; query: string }> = ({
  instanceId,
  stack,
  query,
}) => {
  const [draft, setDraft] = useState(query || searchDraft.get());
  const run = (q: string) => {
    if (!q.trim()) return;
    searchDraft.set(q.trim());
    centerNav.update({ [stack]: [{ type: "search", query: "" }, { type: "search", query: q.trim() }] });
  };
  if (query) return <SearchResults instanceId={instanceId} stack={stack} query={query} />;
  return (
    <>
      <Focusable
        flow-children="row"
        style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 24 }}
      >
        <div style={{ flex: 1 }}>
          <TextField
            value={draft}
            placeholder="Songs, albums, artists"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run(draft)}
          />
        </div>
        <PageButton primary label="Search" onClick={() => run(draft)}>
          <FaSearch />
        </PageButton>
      </Focusable>
      <RecentSearches
        instanceId={instanceId}
        onSearch={(q) => {
          setDraft(q);
          run(q);
        }}
      />
    </>
  );
};

/**
 * A search's results, a page of their own: what was searched for, then one source at a time (SourcePager's dots),
 * all of that source's results, songs first. Your files' show first, then music services', then the rest; it stays on
 * the one picked. The live source's recent searches keep the search.
 */
const SearchResults: FC<{ instanceId: string; stack: CenterStack; query: string }> = ({ instanceId, stack, query }) => {
  const found = useLibrarySearch(query, instanceId);
  // the sources in a steady order, the best showing first (your files, then music services...): nothing moves under
  // the controller as slower ones answer
  const { ordered: pages, at, pick } = useSearchChoice(query, found.pages, found.waiting);
  return (
    <>
      {/* still looking: a spinner; every source answered and none found anything: said so */}
      {!at && (found.waiting > 0 || found.pages.length ? <Loading result={null} /> : <Notice>Nothing found for "{query}"</Notice>)}
      {at && (
        <SourcePager
          // what was searched for, at the start of the dots' line
          leading={
            <>
              <FaSearch style={{ flexShrink: 0, fontSize: 12 }} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{query}</span>
            </>
          }
          places={pages.map((p) => ({ id: p.source.id, name: p.source.name }))}
          at={at.source.id}
          onChoose={pick}
          page={(id) => {
            const found = pages.find((p) => p.source.id === id);
            // marked, so the controller lands on the first result
            return found ? (
              <div className="mc-results">
                <ResultsContent instanceId={id} stack={stack} page={found.page} query={query} />
              </div>
            ) : null;
          }}
        />
      )}
    </>
  );
};

/**
 * One source's results, as screens the controller goes down a whole one at a time (each a row block to snapRows,
 * never a line of a list at a time, nor half of one): its songs (and each other list of tracks it has, episodes...),
 * as many as fit on a screen and a Show all; then a row for each kind of thing found, its Show all at its end.
 */
const ResultsContent: FC<{ instanceId: string; stack: CenterStack; page: Page; query: string }> = ({
  instanceId,
  stack,
  page,
  query,
}) => {
  // stations: their own fitted grid
  if (allStations(page.tracks)) return <PageContent instanceId={instanceId} stack={stack} page={{ ...page, tracks_first: true }} />;
  const lists = [
    ...(page.tracks.length ? [{ title: "Songs", tracks: page.tracks }] : []),
    ...(page.extra ?? []).filter((x) => x.tracks.length),
  ];
  return (
    <>
      {page.notice && <Notice>{page.notice}</Notice>}
      {lists.map((x) => (
        <FittedTracks key={x.title} instanceId={instanceId} stack={stack} title={x.title} tracks={x.tracks}
                      more={{ query, part: x.title }} />
      ))}
      <Shelves instanceId={instanceId} stack={stack} items={page.collections} titled />
      {!lists.length && !page.collections.length && !page.notice && <Notice>Nothing found</Notice>}
    </>
  );
};

/**
 * A titled list of tracks that fits on one screen: as many as fit (measured, so on any screen, scale or text size; the
 * page's first part shares its screen with what's above it, the source dots), then Show all, opening every one.
 */
const FittedTracks: FC<{ instanceId: string; stack: CenterStack; title: string; tracks: Track[]; more?: SearchPart }> = ({
  instanceId,
  stack,
  title,
  tracks,
  more,
}) => {
  const block = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState(3);
  const [room, setRoom] = useState(0);
  const showAlbum = new Set(tracks.map((t) => t.album)).size > 1;
  // the room changes with the window
  useEffect(() => {
    const scroller = block.current && scrollParent(block.current);
    if (!scroller) return;
    const measure = () => setRoom(scroller.clientHeight);
    measure();
    return watchSize(scroller, measure);
  }, []);
  // what's drawn against the room it has: lines added or taken away to fit
  useLayoutEffect(() => {
    const box = block.current;
    const inner = content.current;
    const scroller = box && scrollParent(box);
    if (!box || !inner || !scroller || !room) return;
    const lines = [...inner.querySelectorAll<HTMLElement>(".mc-list > .mc-row")];
    if (!lines.length) return;
    const pitch = lines.length > 1 ? lines[1].getBoundingClientRect().top - lines[0].getBoundingClientRect().top : lines[0].offsetHeight;
    if (pitch <= 0) return;
    const top = scroller.scrollTop + box.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    const space = isFirstRow(scroller, box) ? room - top - 16 : room - TAB_BAR - 16;
    const next = Math.max(1, Math.min(tracks.length, fit + Math.floor((space - inner.offsetHeight) / pitch)));
    if (next !== fit) setFit(next);
  });
  return (
    // a row block (entered whole), not a slot: a slot's room is shared out and its content centred in it, which left a gap
    // over the songs under the dots
    <div ref={block} className="mc-row-block" style={{ marginBottom: 28 }}>
      <div ref={content}>
        <SectionTitle>{title}</SectionTitle>
        <TrackTable
          instanceId={instanceId}
          tracks={tracks}
          showAlbum={showAlbum}
          limit={fit}
          onShowAll={() => pushCenter(stack, { type: "all", title, items: [], tracks, instanceId, more })}
          onPlay={(i) => player.playList(instanceId, tracks, i)}
          onSecondary={(i) => addToQueue(instanceId, tracks[i])}
          onOptions={(i, e) => showTrackMenu(instanceId, tracks[i], e?.currentTarget)}
        />
      </div>
    </div>
  );
};

const RecentSearches: FC<{ instanceId: string; onSearch: (q: string) => void }> = ({ instanceId, onSearch }) => {
  const [searches, setSearches] = useState<string[] | null>(null);
  useEffect(() => {
    searchHistory(instanceId)
      .then(setSearches)
      .catch(() => setSearches([]));
  }, [instanceId]);
  if (searches === null) return null;
  if (!searches.length) return null;
  return (
    <>
      <SectionTitle>Recent searches</SectionTitle>
      <Focusable
        className="mc-list"
        flow-children="column"
        style={{ display: "flex", flexDirection: "column", gap: 2 }}
      >
        {searches.map((q) => (
          <Focusable
            key={q}
            className="mc-row"
            noFocusRing
            onActivate={() => onSearch(q)}
            onClick={() => onSearch(q)}
            onOKActionDescription="Search"
            style={{ display: "flex", alignItems: "center", gap: 14, padding: "10px 12px", fontSize: 16 }}
          >
            <FaHistory style={{ opacity: 0.5 }} /> {q}
          </Focusable>
        ))}
      </Focusable>
    </>
  );
};

/** What plays next, in play order, beside Now Playing. A plays a track, X takes it out, Y its menu. */
export const QueueList: FC = () => {
  const { queue, index } = useStore(player.state);
  const list = useRef<HTMLDivElement>(null);
  const order = player.playOrder;
  const at = order.indexOf(index);
  const upcoming = order.slice(at + 1).map((i) => ({ i, item: queue[i] }));
  // taking a track out removes the row the controller was on: it moves to the one that took its place
  const remove = (i: number, row: number) => {
    player.removeAt(i);
    window.setTimeout(() => {
      const rows = list.current?.querySelectorAll<HTMLElement>(".mc-row");
      if (rows?.length) rows[Math.min(row, rows.length - 1)].focus();
    }, 50);
  };
  if (!upcoming.length) return null;
  return (
    <>
      <Focusable
        ref={list}
        className="mc-list"
        flow-children="column"
        style={{ display: "flex", flexDirection: "column", gap: 2 }}
      >
        {upcoming.map(({ i, item }, row) => (
          <TrackLine
            key={`${i}:${item.track.id}`}
            instanceId={item.instanceId}
            track={item.track}
            showAlbum={false}
            onPlay={() => player.jumpTo(i)}
            onSecondary={() => remove(i, row)}
            secondaryLabel="Remove"
            onOptions={(e) => showTrackMenu(item.instanceId, item.track, e?.currentTarget)}
          />
        ))}
      </Focusable>
      {/* last, as in the panel's queue: never where the controller lands first */}
      <div style={{ display: "flex", margin: "16px 0 8px" }}>
        <PageButton label="Clear queue" onClick={confirmClearQueue}>
          Clear queue
        </PageButton>
      </div>
    </>
  );
};

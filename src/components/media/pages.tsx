import { Focusable, TextField } from "@decky/ui";
import { FC, ReactNode, useEffect, useRef, useState } from "react";
import {
  FaCalendarAlt,
  FaChartLine,
  FaCheck,
  FaCompactDisc,
  FaCompass,
  FaDownload,
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
} from "react-icons/fa";
import {
  CenterTabInfo,
  Collection,
  Page,
  Section,
  TabInfo,
  Track,
  getBrowseInfo,
  getCenterTab,
  isLocalPlaylist,
  openCollection,
  search,
  searchHistory,
} from "../../api";
import { canPlayWithGame, musicFor, runningGame, toggleGameMusic } from "../../games";
import { View, centerTab, playFrom, resultCache, setOpenInCenter } from "../../nav";
import { notify } from "../../notify";
import { hasFeature, player } from "../../player";
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
import { saveCollection, showCollectionMenu, showTrackMenu } from "../TrackMenu";
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
} from "./parts";

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
const Shelves: FC<{ instanceId: string; stack: CenterStack; items: Collection[]; titled: boolean }> = ({
  instanceId,
  stack,
  items,
  titled,
}) => {
  useStore(runningGame); // cards get a menu (play with the game) while one runs
  return (
    <>
      {groupByKind(items).map(([kind, list]) => (
        <Shelf key={kind} title={titled ? KIND_TITLES[kind] : undefined}>
          {list.slice(0, rowCut(list.length)).map((c) => (
            <Card
              key={c.kind + c.id}
              instanceId={instanceId}
              c={c}
              onOpen={() => pushCenter(stack, { type: "collection", collection: c })}
              onPlay={c.kind === "album" || c.kind === "playlist" ? () => playCollection(instanceId, c) : undefined}
              onOptions={
                hasCollectionMenu(instanceId, c)
                  ? (e) => showCollectionMenu(instanceId, c, e?.currentTarget)
                  : undefined
              }
            />
          ))}
          {list.length > ROW_SIZE && rowEnd(stack, KIND_TITLES[kind], list, [])}
        </Shelf>
      ))}
    </>
  );
};

const collectionCard = (instanceId: string, stack: CenterStack, c: Collection) => (
  <Card
    key={c.kind + c.id}
    instanceId={instanceId}
    c={c}
    onOpen={() => pushCenter(stack, { type: "collection", collection: c })}
    onPlay={c.kind === "album" || c.kind === "playlist" ? () => playCollection(instanceId, c) : undefined}
    onOptions={
      hasCollectionMenu(instanceId, c) ? (e) => showCollectionMenu(instanceId, c, e?.currentTarget) : undefined
    }
  />
);

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

/** A section's covers as one square picture: four in a 2 by 2, or one filling it when it has fewer. */
const Mosaic: FC<{ instanceId: string; art: string[] }> = ({ instanceId, art }) => {
  const [failed, setFailed] = useState<string[]>([]);
  const shown = art.filter((a) => !failed.includes(a));
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
        <img
          key={a}
          src={mediaUrl("art", instanceId, a)}
          loading="lazy"
          decoding="async"
          onError={() => setFailed((f) => [...f, a])}
          style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
        />
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
        {t.art?.length ? <Mosaic instanceId={instanceId} art={t.art} /> : iconFor(t.id)}
      </div>
      <div style={{ marginTop: 10 }}>
        <div style={{ ...ONE_LINE, fontSize: 14, fontWeight: 600, color: "#ffffff" }}>{t.title}</div>
        {t.subtitle && <div style={{ ...ONE_LINE, fontSize: 12, opacity: 0.6, marginTop: 2 }}>{t.subtitle}</div>}
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

export const TabPage: FC<{ instanceId: string; tab: string; stack: CenterStack }> = ({ instanceId, tab, stack }) => {
  useStore(runningGame); // cards get a menu (play with the game) while one runs
  const result = useResult<Section[]>(`${instanceId}:tab:${tab}`, () => getCenterTab(instanceId, tab), true);
  if (!result?.ok) return <Loading result={result} />;
  const sections = result.data.filter((s) => s.items.length || s.tracks?.length);
  const notes = result.data.filter((s) => s.notice);
  if (!sections.length)
    return <Notice>{notes.length ? notes.map((s) => s.notice).join(" ") : "Nothing here yet"}</Notice>;
  // a tab of several groups of rows names each group
  const headed = sections.filter((s) => s.display === "rows").length > 1;
  return (
    <>
      {sections.map((s) => (
        <SectionView key={s.title} instanceId={instanceId} stack={stack} section={s} headed={headed} />
      ))}
    </>
  );
};

const SectionView: FC<{ instanceId: string; stack: CenterStack; section: Section; headed: boolean }> = ({
  instanceId,
  stack,
  section: s,
  headed,
}) => {
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
          <Tile key={c.kind + c.id} c={c} onOpen={() => pushCenter(stack, { type: "collection", collection: c })} />
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
const rowEnd = (stack: CenterStack, title: string, items: Collection[], tracks: Track[]) => {
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
      onOpen={() => pushCenter(stack, { type: "all", title, items, tracks })}
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
}> = ({ instanceId, stack, title, items, tracks }) => (
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
        </div>
      ))}
  </>
);

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
  const open = () => pushCenter(stack, { type: "collection", collection: category });
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
                onOpen={() => pushCenter(stack, { type: "collection", collection: c })}
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

/** What a page holds below its header: shelves of collections and lists of tracks. */
const PageContent: FC<{ instanceId: string; stack: CenterStack; page: Page; from?: Collection }> = ({
  instanceId,
  stack,
  page,
  from,
}) => {
  const showAlbum = new Set(page.tracks.map((t) => t.album)).size > 1;
  const mixed = page.collections.length > 0 || !!page.extra?.length;
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
  const tracks = (
    <>
      {page.tracks.length > 0 && (
        <div style={{ marginBottom: 28 }}>
          {mixed && <SectionTitle>{page.tracks_first ? "Songs" : "Tracks"}</SectionTitle>}
          <TrackTable
            instanceId={instanceId}
            tracks={page.tracks}
            numbered={numbered}
            showAlbum={showAlbum}
            limit={mixed && !from ? 8 : undefined}
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
      {!page.tracks.length && !page.collections.length && !page.extra?.length && !page.notice && <Notice>Empty</Notice>}
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
  const pictured = collection.kind !== "list" && collection.kind !== "folder";
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
      <div className="mc-row-block" style={{ display: "flex", gap: 32, alignItems: "flex-end", marginBottom: 28 }}>
        {pictured && (
          <Cover
            instanceId={instanceId}
            c={{ ...collection, art: page?.art ?? collection.art }}
            size="clamp(96px, 28vh, 220px)"
          />
        )}
        <div style={{ minWidth: 0, flex: 1, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 13, letterSpacing: 1.5, textTransform: "uppercase", opacity: 0.6 }}>
            {KIND_TITLES[collection.kind].replace(/s$/, "")}
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
            {canSave(instanceId, collection) && page && page.tracks.length > 0 && (
              <PageButton label="Save playlist" onClick={() => saveCollection(instanceId, collection)}>
                <FaPlus /> Save
              </PageButton>
            )}
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
export const SearchPage: FC<{ instanceId: string; stack: CenterStack; query: string }> = ({
  instanceId,
  stack,
  query,
}) => {
  const [draft, setDraft] = useState(query);
  const run = (q: string) => {
    if (!q.trim()) return;
    resultCache.delete(`${instanceId}:search:${q.trim()}`);
    centerNav.update({ [stack]: [{ type: "search", query: q.trim() }] });
  };
  return (
    <>
      <Focusable
        flow-children="row"
        style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 24, maxWidth: 720 }}
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
      {query ? (
        <SearchResults instanceId={instanceId} stack={stack} query={query} />
      ) : (
        <RecentSearches
          instanceId={instanceId}
          onSearch={(q) => {
            setDraft(q);
            run(q);
          }}
        />
      )}
    </>
  );
};

const SearchResults: FC<{ instanceId: string; stack: CenterStack; query: string }> = ({ instanceId, stack, query }) => {
  const result = useResult<Page>(`${instanceId}:search:${query}`, () => search(instanceId, query));
  if (!result?.ok) return <Loading result={result} />;
  return (
    // marked, so the controller lands on the first result rather than back in the search field
    <div className="mc-results">
      <PageContent instanceId={instanceId} stack={stack} page={result.data} />
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
  if (!searches.length) return <Notice>Search this source for songs, albums and artists</Notice>;
  return (
    <>
      <SectionTitle>Recent searches</SectionTitle>
      <Focusable
        className="mc-list"
        flow-children="column"
        style={{ display: "flex", flexDirection: "column", gap: 2, maxWidth: 720 }}
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
  if (!upcoming.length) return <Notice>Nothing after this track</Notice>;
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

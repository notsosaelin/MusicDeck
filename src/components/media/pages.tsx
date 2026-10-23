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
  FaRandom,
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
  Track,
  getCenterTab,
  isLocalPlaylist,
  openCollection,
  search,
  searchHistory,
} from "../../api";
import { canPlayWithGame, musicFor, runningGame, toggleGameMusic } from "../../games";
import { View, playFrom, resultCache } from "../../nav";
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
  useResult,
} from "../Browser";
import { confirmClearQueue } from "../Queue";
import { saveCollection, showCollectionMenu, showTrackMenu } from "../TrackMenu";
import {
  Card,
  CardBlanks,
  CardGrid,
  Cover,
  GroupTitle,
  Loading,
  Notice,
  PageButton,
  SectionTitle,
  Shelf,
  Tile,
  TileGrid,
  TrackCard,
  TrackLine,
  TrackTable,
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

export function pushCenter(stack: CenterStack, view: View) {
  const views = viewsOf(centerNav.get(), stack);
  const from =
    view.type === "collection" ? cardKey(view.collection) : view.type === "section" ? sectionKey(view.tab) : null;
  if (from) (openedFrom[stack] ??= [])[views.length - 1] = from;
  centerNav.update({ [stack]: [...views, view] });
}

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
          {list.map((c) => (
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
};

/** Browse's groups, in the order they show: the user's own music first. */
const BROWSE_GROUPS: { id: NonNullable<CenterTabInfo["group"]>; title: string }[] = [
  { id: "yours", title: "Your music" },
  { id: "explore", title: "Explore" },
  { id: "files", title: "On this Deck" },
];

const TILE_BACK = "linear-gradient(135deg, rgba(255, 255, 255, 0.14), rgba(255, 255, 255, 0.04))";

/** Up to four of a section's covers, as one picture: 2 by 2, or one filling the tile. */
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

/** One of "Your music": large, its own covers behind its name and how many. */
const LibraryTile: FC<{ instanceId: string; t: CenterTabInfo; open: () => void }> = ({ instanceId, t, open }) => (
  <Focusable
    className="mc-tile"
    data-mc={sectionKey(t.id)}
    noFocusRing
    onActivate={open}
    onClick={open}
    onOKActionDescription="Open"
    style={{ position: "relative", overflow: "hidden", aspectRatio: "16 / 10", background: TILE_BACK }}
  >
    <Mosaic instanceId={instanceId} art={t.art ?? []} />
    {/* dark at the foot, so the name reads over any cover */}
    <div
      style={{
        position: "absolute",
        inset: 0,
        background:
          "linear-gradient(to top, rgba(10, 13, 18, 0.92) 0%, rgba(10, 13, 18, 0.35) 55%, rgba(10, 13, 18, 0.1) 100%)",
      }}
    />
    {!t.art?.length && (
      <div style={{ position: "absolute", top: 16, left: 18, fontSize: 26, opacity: 0.6 }}>
        {SECTION_ICONS[t.id] ?? <FaMusic />}
      </div>
    )}
    <div style={{ position: "absolute", left: 18, right: 18, bottom: 14 }}>
      <div style={{ fontSize: 20, fontWeight: 700, color: "#ffffff" }}>{t.title}</div>
      {t.subtitle && <div style={{ fontSize: 13, opacity: 0.7, marginTop: 2 }}>{t.subtitle}</div>}
    </div>
  </Focusable>
);

/** One of Explore (or a folder): compact, its icon beside its name and how many. */
const WayInTile: FC<{ t: CenterTabInfo; open: () => void }> = ({ t, open }) => (
  <Focusable
    className="mc-tile"
    data-mc={sectionKey(t.id)}
    noFocusRing
    onActivate={open}
    onClick={open}
    onOKActionDescription="Open"
    style={{
      display: "flex",
      alignItems: "center",
      gap: 14,
      padding: "14px 16px",
      minHeight: 72,
      boxSizing: "border-box",
      background: TILE_BACK,
    }}
  >
    <div
      style={{
        flexShrink: 0,
        width: 44,
        height: 44,
        borderRadius: "50%",
        background: "rgba(255, 255, 255, 0.1)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 18,
        opacity: 0.85,
      }}
    >
      {SECTION_ICONS[t.id] ?? <FaMusic />}
    </div>
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 16, fontWeight: 600, color: "#ffffff" }}>{t.title}</div>
      {t.subtitle && <div style={{ fontSize: 12, opacity: 0.6, marginTop: 2 }}>{t.subtitle}</div>}
    </div>
  </Focusable>
);

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
  const groups = BROWSE_GROUPS.map((g) => ({
    ...g,
    tabs: sections.filter((t) => (t.group ?? "explore") === g.id),
  })).filter((g) => g.tabs.length);
  const open = (t: CenterTabInfo) => () => pushCenter(stack, { type: "section", tab: t.id, title: t.title });
  return (
    <>
      {groups.map((g) => (
        <div key={g.id} style={{ marginBottom: 32 }}>
          {/* one group needs no name of its own: the tab says Browse */}
          <SectionTitle>{groups.length > 1 ? g.title : "Browse"}</SectionTitle>
          <Focusable
            style={{
              display: "grid",
              gridTemplateColumns:
                g.id === "yours"
                  ? "repeat(auto-fill, minmax(clamp(220px, 24vw, 340px), 1fr))"
                  : "repeat(auto-fill, minmax(clamp(200px, 20vw, 280px), 1fr))",
              gap: 16,
            }}
          >
            {g.tabs.map((t) =>
              g.id === "yours" ? (
                <LibraryTile key={t.id} instanceId={instanceId} t={t} open={open(t)} />
              ) : (
                <WayInTile key={t.id} t={t} open={open(t)} />
              ),
            )}
          </Focusable>
        </div>
      ))}
    </>
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
      <div style={{ marginBottom: 28 }}>
        <SectionTitle>{s.title}</SectionTitle>
        <TileGrid>
          {s.items.map((c) => (
            <Tile key={c.kind + c.id} c={c} onOpen={() => pushCenter(stack, { type: "collection", collection: c })} />
          ))}
        </TileGrid>
      </div>
    );
  if (s.display === "covers")
    return (
      <Shelf title={s.title}>
        {s.items.map((c) => collectionCard(instanceId, stack, c))}
        {tracks.map((t, i) => (
          <TrackCard
            key={t.id + i}
            instanceId={instanceId}
            track={t}
            onPlay={() => player.playList(instanceId, tracks, i)}
            onSecondary={() => addToQueue(instanceId, t)}
          />
        ))}
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
      {s.items.length > 0 && <Shelf title={s.title}>{s.items.map((c) => collectionCard(instanceId, stack, c))}</Shelf>}
      {songs}
    </>
  );
};

/** Cards a category row shows before its "All" card */
const ROW_SIZE = 16;

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
      <Card
        instanceId={instanceId}
        c={{ ...category, title: "All", subtitle: category.title, art: null }}
        onOpen={open}
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
  const shelves = page.collections.length > 0 && (
    <Shelves instanceId={instanceId} stack={stack} items={page.collections} titled />
  );
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
            limit={mixed ? 8 : undefined}
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
  const pictured = collection.kind !== "list" && collection.kind !== "folder";
  const albumArtist = page && collection.kind === "album" ? mostCommon(page.tracks.map((t) => t.artist)) : undefined;
  const total = page?.tracks.reduce((sum, t) => sum + (t.duration || 0), 0) ?? 0;
  const byline = albumArtist ?? page?.subtitle ?? collection.subtitle;
  // the source's own line may already count them ("114 tracks")
  const facts =
    page?.tracks.length && !/\b(tracks?|songs?|episodes?)\b/i.test(byline)
      ? [
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
            {page && page.tracks.length > 0 && (
              <>
                <PageButton
                  primary
                  label="Play"
                  onClick={() => playFrom(instanceId, collection, page.tracks, 0, false)}
                >
                  <FaPlay /> Play
                </PageButton>
                <PageButton label="Shuffle" onClick={() => playFrom(instanceId, collection, page.tracks, 0, true)}>
                  <FaRandom /> Shuffle
                </PageButton>
              </>
            )}
            {canSave(instanceId, collection) && page && page.tracks.length > 0 && (
              <PageButton label="Save playlist" onClick={() => saveCollection(instanceId, collection)}>
                <FaPlus /> Save
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
      <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: 2, maxWidth: 720 }}>
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
      <Focusable ref={list} flow-children="column" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
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

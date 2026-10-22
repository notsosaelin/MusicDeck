import { Focusable, TextField } from "@decky/ui";
import { FC, Fragment, useEffect, useState } from "react";
import {
  FaCheck,
  FaDownload,
  FaExternalLinkAlt,
  FaGamepad,
  FaHistory,
  FaPlay,
  FaPlus,
  FaRandom,
  FaSearch,
} from "react-icons/fa";
import {
  Collection,
  Page,
  Section,
  Track,
  getHome,
  isLocalPlaylist,
  openCollection,
  search,
  searchHistory,
} from "../../api";
import { canPlayWithGame, musicFor, runningGame, toggleGameMusic } from "../../games";
import { View, playFrom, resultCache } from "../../nav";
import { notify } from "../../notify";
import { hasFeature, player } from "../../player";
import { Store, pluginState, useStore } from "../../store";
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
import { Card, Cover, Loading, Notice, PageButton, SectionTitle, Shelf, TrackLine, TrackTable } from "./parts";

/** Where each browsing tab of the media center is: a history of views, B steps back through it. */
export type CenterStack = "home" | "search";
export const centerNav = new Store<Record<CenterStack, View[]>>({
  home: [{ type: "home" }],
  search: [{ type: "search", query: "" }],
});

/** Per tab and depth, the card a page was opened from, so B can put the controller back on it. */
const openedFrom: Record<CenterStack, string[]> = { home: [], search: [] };
export const cardKey = (c: Collection) => c.kind + ":" + c.id;

export function pushCenter(stack: CenterStack, view: View) {
  const views = centerNav.get()[stack];
  if (view.type === "collection") openedFrom[stack][views.length - 1] = cardKey(view.collection);
  centerNav.update({ [stack]: [...views, view] });
}

/** Which card the controller should be on when this tab shows its view at `depth` (if any). */
export const focusKeyFor = (stack: CenterStack, depth: number): string | undefined => openedFrom[stack][depth];

/** False at the start of the tab, so B can leave the media center instead. */
export function popCenter(stack: CenterStack): boolean {
  const views = centerNav.get()[stack];
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

/** The source's home rows, each a shelf (or, for rows of songs, a short list). */
export const HomePage: FC<{ instanceId: string }> = ({ instanceId }) => {
  const result = useResult<Section[]>(`${instanceId}:home`, () => getHome(instanceId), true);
  if (!result?.ok) return <Loading result={result} />;
  if (!result.data.length) return <Notice>Nothing here yet</Notice>;
  return (
    <>
      {result.data.map((s) => (
        <Fragment key={s.title}>
          {s.items.length > 0 && (
            <Shelf title={s.title}>
              {s.items.map((c) => (
                <Card
                  key={c.kind + c.id}
                  instanceId={instanceId}
                  c={c}
                  onOpen={() => pushCenter("home", { type: "collection", collection: c })}
                  onPlay={c.kind === "album" || c.kind === "playlist" ? () => playCollection(instanceId, c) : undefined}
                  onOptions={
                    hasCollectionMenu(instanceId, c)
                      ? (e) => showCollectionMenu(instanceId, c, e?.currentTarget)
                      : undefined
                  }
                />
              ))}
            </Shelf>
          )}
          {!!s.tracks?.length && (
            <div style={{ marginBottom: 28 }}>
              {!s.items.length && <SectionTitle>{s.title}</SectionTitle>}
              <TrackTable
                instanceId={instanceId}
                tracks={s.tracks}
                limit={6}
                onPlay={(i) => player.playList(instanceId, s.tracks!, i)}
                onSecondary={(i) => addToQueue(instanceId, s.tracks![i])}
                onOptions={(i, e) => showTrackMenu(instanceId, s.tracks![i], e?.currentTarget)}
              />
            </div>
          )}
        </Fragment>
      ))}
    </>
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
      <div style={{ display: "flex", gap: 32, alignItems: "flex-end", marginBottom: 28 }}>
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
export const SearchPage: FC<{ instanceId: string; query: string }> = ({ instanceId, query }) => {
  const [draft, setDraft] = useState(query);
  const run = (q: string) => {
    if (!q.trim()) return;
    resultCache.delete(`${instanceId}:search:${q.trim()}`);
    centerNav.update({ search: [{ type: "search", query: q.trim() }] });
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
        <SearchResults instanceId={instanceId} query={query} />
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

const SearchResults: FC<{ instanceId: string; query: string }> = ({ instanceId, query }) => {
  const result = useResult<Page>(`${instanceId}:search:${query}`, () => search(instanceId, query));
  if (!result?.ok) return <Loading result={result} />;
  return (
    // marked, so the controller lands on the first result rather than back in the search field
    <div className="mc-results">
      <PageContent instanceId={instanceId} stack="search" page={result.data} />
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

/** What's playing and what plays next, in play order. A plays a track, X takes it out. */
export const QueuePage: FC = () => {
  const { queue, index } = useStore(player.state);
  const order = player.playOrder;
  const at = order.indexOf(index);
  const current = queue[index];
  const upcoming = order.slice(at + 1).map((i) => ({ i, item: queue[i] }));
  if (!current) return <Notice>The queue is empty. Play something from Home or Search.</Notice>;
  return (
    <>
      <SectionTitle>Now playing</SectionTitle>
      <div style={{ marginBottom: 28 }}>
        <TrackLine
          instanceId={current.instanceId}
          track={current.track}
          active
          onPlay={() => player.toggle()}
          onOptions={(e) => showTrackMenu(current.instanceId, current.track, e?.currentTarget)}
        />
      </div>
      <SectionTitle>{upcoming.length ? `Up next · ${upcoming.length}` : "Up next"}</SectionTitle>
      {upcoming.length ? (
        <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {upcoming.map(({ i, item }) => (
            <TrackLine
              key={`${i}:${item.track.id}`}
              instanceId={item.instanceId}
              track={item.track}
              onPlay={() => player.jumpTo(i)}
              onSecondary={() => player.removeAt(i)}
              secondaryLabel="Remove"
              onOptions={(e) => showTrackMenu(item.instanceId, item.track, e?.currentTarget)}
            />
          ))}
        </Focusable>
      ) : (
        <Notice>Nothing after this track</Notice>
      )}
      {/* last, as in the panel's queue: never where the controller lands first */}
      <div style={{ display: "flex", marginTop: 20 }}>
        <PageButton label="Clear queue" onClick={confirmClearQueue}>
          Clear queue
        </PageButton>
      </div>
    </>
  );
};

import {
  DialogButton,
  Field,
  Focusable,
  Navigation,
  PanelSection,
  PanelSectionRow,
  Spinner,
  TextField,
} from "@decky/ui";
import { FC, Fragment, ReactNode, useEffect, useRef, useState } from "react";
import {
  FaCheck,
  FaDownload,
  FaExternalLinkAlt,
  FaGamepad,
  FaHeart,
  FaHistory,
  FaPlay,
  FaPlus,
  FaRandom,
  FaRegHeart,
  FaSearch,
  FaUserCheck,
  FaUserPlus,
} from "react-icons/fa";
import {
  Collection,
  CollectionKind,
  Page,
  Result,
  SaveState,
  Section,
  Track,
  collectionAction,
  forgetSearch,
  getHome,
  isLocalPlaylist,
  openCollection,
  saveToLibrary,
  searchHistory,
} from "../api";
import { canPlayWithGame, musicFor, runningGame, toggleGameMusic } from "../games";
import { hasFeature, player } from "../player";
import { pluginState, useStore } from "../store";
import {
  ROOTS,
  StackName,
  dataVersion,
  invalidateBrowserCache,
  navigation,
  playFrom,
  pushView,
  resultCache,
  setStack,
} from "../nav";
import { notify } from "../notify";
import { MenuContext, canCopy, canDelete, showCollectionMenu, showTrackMenu } from "./TrackMenu";
import {
  Art,
  ButtonRow,
  CollectionRow,
  IconButton,
  ListRow,
  Message,
  RowText,
  ScrollingText,
  TrackNumber,
  TrackRow,
} from "./common";
import { useDownload, useSavedCopy } from "../download";
import { bestResults, useLibrarySearch } from "../library";

/**
 * A view's data, cached per key so going back doesn't refetch (shared with the media center). With `revalidate` (the home view, whose
 * rows change as you listen: "Jump back in", "Continue listening"), the cached copy shows at once and
 * a fresh one replaces it when it arrives.
 */
export function useResult<T>(key: string, load: () => Promise<Result<T>>, revalidate = false): Result<T> | null {
  const [result, setResult] = useState<Result<T> | null>(() => resultCache.get(key) ?? null);
  const [shownKey, setShownKey] = useState(key);
  const { v } = useStore(dataVersion);
  useEffect(() => {
    const cached = resultCache.get(key);
    if (cached) {
      setShownKey(key);
      setResult(cached);
      if (!revalidate) return;
    }
    // refetching the same view after an edit: keep showing it instead of flashing a spinner
    if (key !== shownKey && !cached) setResult(null);
    setShownKey(key);
    let cancelled = false;
    const started = dataVersion.get().v;
    load()
      .catch((e) => ({ ok: false as const, error: String(e) }))
      .then((r) => {
        // fetched before an edit invalidated the cache: it's from before the edit, so not kept
        if (r.ok && dataVersion.get().v === started) resultCache.set(key, r);
        // a failed refresh doesn't replace what's already showing
        if (!cancelled && (r.ok || !cached)) setResult(r);
      });
    return () => {
      cancelled = true;
    };
  }, [key, v]);
  return result;
}

const Loading: FC<{ result: Result<any> | null }> = ({ result }) =>
  result === null ? (
    <PanelSection>
      <PanelSectionRow>
        <div style={{ display: "flex", justifyContent: "center", padding: 16 }}>
          <Spinner style={{ width: 32, height: 32 }} />
        </div>
      </PanelSectionRow>
    </PanelSection>
  ) : !result.ok ? (
    <Message>{result.error}</Message>
  ) : null;

export const trackActions = (instanceId: string, track: Track, ctx: MenuContext = {}) => ({
  onSecondary: () => {
    player.enqueue(instanceId, [track]);
    notify("Added to queue", track.title, { instanceId, track });
  },
  // short: with Play and Options it has to fit the panel's hint bar on one line
  secondaryLabel: "Queue",
  onOptions: (e?: any) => showTrackMenu(instanceId, track, e?.currentTarget, ctx),
  optionsLabel: "Options",
});

/** Remote playlists/albums can be saved as MusicDeck playlists when the source has no playlists of its own. */
export const canSave = (instanceId: string, c: Collection) =>
  (c.kind === "playlist" || c.kind === "album") && !isLocalPlaylist(c.id) && !hasFeature(instanceId, "playlists");
export const hasCollectionMenu = (instanceId: string, c: Collection) =>
  isLocalPlaylist(c.id) ||
  canSave(instanceId, c) ||
  (!!runningGame.get() && canPlayWithGame(c)) ||
  (c.kind === "album" && hasFeature(instanceId, "tags")) ||
  (c.kind === "artist" && hasFeature(instanceId, "artwork")) ||
  canCopy(instanceId, c) ||
  canDelete(instanceId, c) ||
  c.kind === "device";

/**
 * Plays an album or playlist straight from its row (X). Uses the page if it's been opened already;
 * says why when there's nothing to play (a Steam soundtrack that isn't installed, an empty playlist).
 */
export async function playCollection(instanceId: string, c: Collection) {
  const key = `${instanceId}:${c.kind}:${c.id}`;
  const result: Result<Page> = resultCache.get(key) ?? (await openCollection(instanceId, c.kind, c.id));
  // the live source changed while it loaded: this belongs to the old one
  if (pluginState.get()?.active_service !== instanceId) return;
  if (!result.ok) return notify(`Couldn't play ${c.title}`, result.error);
  if (!result.data.tracks.length) return notify(c.title, result.data.notice || "Nothing to play here");
  resultCache.set(key, result);
  playFrom(instanceId, c, result.data.tracks);
}

const CollectionList: FC<{
  instanceId: string;
  stack: StackName;
  title?: string;
  items: Collection[];
  limit?: number;
}> = ({ instanceId, stack, title, items, limit = 5 }) => {
  const [expanded, setExpanded] = useState(false);
  useStore(runningGame); // rows get a menu (play with the game) while one runs
  const shown = expanded ? items : items.slice(0, limit);
  return (
    <PanelSection title={title}>
      {shown.map((item) => (
        <CollectionRow
          key={item.kind + item.id}
          instanceId={instanceId}
          item={item}
          onOpen={(c) => pushView(stack, { type: "collection", collection: c })}
          onPlay={
            item.kind === "album" || item.kind === "playlist" ? () => playCollection(instanceId, item) : undefined
          }
          onOptions={
            hasCollectionMenu(instanceId, item)
              ? (e) => showCollectionMenu(instanceId, item, e?.currentTarget)
              : undefined
          }
        />
      ))}
      {items.length > limit && (
        <PanelSectionRow>
          <Field
            focusable
            highlightOnFocus
            bottomSeparator="none"
            onActivate={() => setExpanded(!expanded)}
            onClick={() => setExpanded(!expanded)}
          >
            <div style={{ fontSize: 13, opacity: 0.8 }}>{expanded ? "Show less" : `Show all ${items.length}`}</div>
          </Field>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};

const HomeView: FC<{ instanceId: string }> = ({ instanceId }) => {
  const result = useResult<Section[]>(`${instanceId}:home`, () => getHome(instanceId), true);
  if (!result?.ok) return <Loading result={result} />;
  if (!result.data.length) return null;
  return (
    <>
      {result.data.map((s) => (
        <Fragment key={s.title}>
          {s.items.length > 0 && (
            <CollectionList instanceId={instanceId} stack="library" title={s.title} items={s.items} />
          )}
          {!!s.tracks?.length && (
            <TrackList
              instanceId={instanceId}
              title={s.items.length ? undefined : s.title}
              tracks={s.tracks}
              limit={5}
            />
          )}
        </Fragment>
      ))}
    </>
  );
};

export const KIND_TITLES: Record<CollectionKind, string> = {
  artist: "Artists",
  album: "Albums",
  playlist: "Playlists",
  folder: "Folders",
  list: "Browse",
  device: "Devices",
};
const KIND_ORDER: CollectionKind[] = ["artist", "album", "playlist", "folder", "list"];

export function mostCommon(values: string[]): string | undefined {
  const counts = new Map<string, number>();
  for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
}

export function groupByKind(items: Collection[]): [CollectionKind, Collection[]][] {
  return KIND_ORDER.map((k) => [k, items.filter((c) => c.kind === k)] as [CollectionKind, Collection[]]).filter(
    ([, list]) => list.length > 0,
  );
}

/** A titled list of tracks; tapping one plays the list from there. Long lists collapse to `limit`. */
const TrackList: FC<{
  instanceId: string;
  title?: string;
  tracks: Track[];
  limit?: number;
  showAlbum?: boolean;
  playlistId?: string;
  showArt?: boolean;
  /** the album or playlist page these tracks are, so playing from it counts as playing it */
  from?: Collection;
  /** one album's tracks: numbered, and its artist not repeated on each */
  album?: boolean;
}> = ({ instanceId, title, tracks, limit, showAlbum = true, playlistId, showArt = true, from, album }) => {
  const [expanded, setExpanded] = useState(false);
  const { queue, index } = useStore(player.state);
  const current = queue[index];
  const shown = limit && !expanded ? tracks.slice(0, limit) : tracks;
  // the artist most of the album's tracks have; the rest are guests, shown on their rows
  const albumArtist = album ? mostCommon(tracks.map((t) => t.artist)) : undefined;
  return (
    <PanelSection title={title}>
      {shown.map((t, i) => (
        <TrackRow
          key={t.id + i}
          track={t}
          showAlbum={showAlbum}
          albumArtist={albumArtist}
          leading={
            album ? (
              <TrackNumber
                n={i + 1}
                digits={String(tracks.length).length}
                active={current?.instanceId === instanceId && current.track.id === t.id}
              />
            ) : undefined
          }
          artInstanceId={showArt ? instanceId : undefined}
          active={current?.instanceId === instanceId && current.track.id === t.id}
          onPlay={() =>
            // a station from a genre, a country, a search plays on its own; from a list of yours, the list
            t.live && !playlistId
              ? player.playList(instanceId, [t], 0)
              : from
                ? playFrom(instanceId, from, tracks, i)
                : player.playList(instanceId, tracks, i)
          }
          {...trackActions(instanceId, t, { playlistId })}
        />
      ))}
      {limit !== undefined && tracks.length > limit && (
        <PanelSectionRow>
          <Field
            focusable
            highlightOnFocus
            bottomSeparator="none"
            onActivate={() => setExpanded(!expanded)}
            onClick={() => setExpanded(!expanded)}
          >
            <div style={{ fontSize: 13, opacity: 0.8 }}>{expanded ? "Show less" : `Show all ${tracks.length}`}</div>
          </Field>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};

const PageBody: FC<{
  instanceId: string;
  stack: StackName;
  page: Page;
  playlistId?: string;
  /** the collection this page is (not set for search results) */
  from?: Collection;
}> = ({ instanceId, stack, page, playlistId, from }) => {
  // Album pages don't need the album name repeated on every row
  const showAlbum = new Set(page.tracks.map((t) => t.album)).size > 1;
  const mixed = page.collections.length > 0 || !!page.extra?.length;
  const collectionSections = groupByKind(page.collections).map(([kind, items], _i, groups) => (
    <CollectionList
      key={kind}
      instanceId={instanceId}
      stack={stack}
      // a page of only one kind (e.g. "Artists") already says what it lists in its header
      title={page.tracks.length || groups.length > 1 ? KIND_TITLES[kind] : undefined}
      items={items}
      limit={page.tracks.length || groups.length > 1 ? 5 : 1000}
    />
  ));
  const trackSections = (
    <>
      {page.tracks.length > 0 && (
        <TrackList
          instanceId={instanceId}
          title={mixed ? (page.tracks_first ? "Songs" : "Tracks") : undefined}
          tracks={page.tracks}
          showAlbum={showAlbum}
          // one album's tracks share a cover: no need to repeat it on every row
          showArt={showAlbum}
          playlistId={playlistId}
          from={from}
          // an album's own tracks (not a podcast's episodes, whose second line is their date)
          album={from?.kind === "album" && !showAlbum && !mixed && !hasFeature(instanceId, "progress")}
          // full lists (albums, playlists) show everything; mixed pages keep each part short
          limit={mixed ? 8 : undefined}
        />
      )}
      {page.extra?.map((e) => (
        <TrackList key={e.title} instanceId={instanceId} title={e.title} tracks={e.tracks} limit={8} />
      ))}
    </>
  );
  return (
    <>
      {page.notice && <Message>{page.notice}</Message>}
      {/* search results lead with songs and videos; artist pages lead with releases */}
      {page.tracks_first ? trackSections : collectionSections}
      {page.tracks_first ? collectionSections : trackSections}
      {!page.tracks.length && !page.collections.length && !page.extra?.length && !page.notice && (
        <Message>Empty</Message>
      )}
    </>
  );
};

/** Artwork + title at the top of a pushed view. B (or re-selecting the tab) goes back, so no back button. */
const Header: FC<{
  instanceId: string;
  title: string;
  subtitle?: string;
  art?: string | null;
  children?: ReactNode;
}> = ({ instanceId, title, subtitle, art, children }) => (
  // md-header: the header and the list under it are one page, not two sections (see PLAYER_CSS)
  <div className="md-header">
    <PanelSection>
      <PanelSectionRow>
        <div style={{ display: "flex", gap: 12, alignItems: "center", padding: "4px 0 8px" }}>
          {art !== undefined && <Art instanceId={instanceId} art={art} size={56} />}
          <RowText size="large" scroll="always" title={title} subtitle={subtitle} />
        </div>
      </PanelSectionRow>
      {children}
    </PanelSection>
  </div>
);

/** A page's button that asks the source to do something (e.g. subscribe); the page reloads after. */
export async function callAction(instanceId: string, c: Collection, call: string) {
  const result = await collectionAction(instanceId, c.kind, c.id, call);
  notify(c.title, result.ok ? result.data : result.error);
  if (result.ok) invalidateBrowserCache();
}

/** What a page's library button says and shows, by what it holds and how the source keeps it. */
export function saveLook(kind: Collection["kind"], style: SaveState["style"], on: boolean) {
  if (style === "favourite")
    return { label: on ? "In favourites" : "Add to favourites", icon: on ? <FaHeart /> : <FaRegHeart /> };
  if (kind === "artist") return { label: on ? "Following" : "Follow", icon: on ? <FaUserCheck /> : <FaUserPlus /> };
  return { label: on ? "In library" : "Add to library", icon: on ? <FaCheck /> : <FaPlus /> };
}

/**
 * An album's, artist's or playlist's place in the user's library on the service (Page.save): whether it's
 * there, and a toggle that shows the change at once and takes it back if the service refuses. Null when
 * the page has no such button.
 */
export function useLibrarySave(instanceId: string, collection: Collection, page: Page | null) {
  const [on, setOn] = useState(page?.save?.on);
  const busy = useRef(false);
  // the page as fetched again (after a change, or another view's) is what's true
  useEffect(() => setOn(page?.save?.on), [page?.save?.on, collection.id]);
  if (!page?.save || on === undefined) return null;
  const { style } = page.save;
  const toggle = async () => {
    if (busy.current) return;
    busy.current = true;
    const next = !on;
    setOn(next);
    const result = await saveToLibrary(instanceId, collection.kind, collection.id, next).catch((e) => ({
      ok: false as const,
      error: String(e),
    }));
    busy.current = false;
    if (!result.ok) {
      setOn(!next);
      notify(collection.title, result.error);
      return;
    }
    // the library's own lists (Albums, Artists, Favourites) now hold it, or don't
    invalidateBrowserCache();
    const words =
      style === "favourite"
        ? next
          ? "Added to favourites"
          : "Removed from favourites"
        : collection.kind === "artist"
          ? next
            ? "Following"
            : "No longer following"
          : next
            ? "Added to your library"
            : "Removed from your library";
    notify(page.title || collection.title, words);
  };
  return { on, toggle, ...saveLook(collection.kind, style, on) };
}

/**
 * A page's header button: installing a soundtrack runs Steam's own install dialog (in the main window,
 * so the menu closes); web pages open in Steam's browser. No other steam:// command is run, whatever
 * a page asks for.
 */
const STEAM_INSTALL = /^steam:\/\/install\/\d+$/;
export const runAction = (url: string) => {
  if (url.startsWith("steam://") ? !STEAM_INSTALL.test(url) : !/^https:\/\//.test(url)) return;
  Navigation.CloseSideMenus();
  // (@decky/ui's typings mistake SteamClient.URL for the DOM's URL class)
  if (url.startsWith("steam://"))
    (window.SteamClient.URL as unknown as { ExecuteSteamURL(url: string): void }).ExecuteSteamURL(url);
  else Navigation.NavigateToExternalWeb(url);
};

/** A playlist's or album's Download button (download.tsx: wherever it's opened, any source that can download). */
const DownloadButton: FC<{ instanceId: string; collection: Collection }> = ({ instanceId, collection }) => {
  const download = useDownload(instanceId, collection);
  if (!download) return null;
  return (
    <ButtonRow>
      <IconButton active={download.on} onClick={download.act}>
        <FaDownload /> {download.label}
      </IconButton>
    </ButtonRow>
  );
};

const CollectionView: FC<{ instanceId: string; stack: StackName; collection: Collection }> = ({
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
  const copy = useSavedCopy(instanceId, collection);
  const playable = !!page && page.tracks.length > 0;
  return (
    <>
      <Header
        instanceId={instanceId}
        title={page?.title ?? collection.title}
        subtitle={page?.subtitle ?? collection.subtitle}
        art={collection.kind === "list" || collection.kind === "folder" ? undefined : (page?.art ?? collection.art)}
      >
        {page && page.tracks.length > 0 && (
          <ButtonRow>
            {/* stations: no playing them all at once, unless it's a list of yours */}
            {(!page.tracks.every((t) => t.live) || isLocalPlaylist(collection.id)) && (
              <IconButton onClick={() => playFrom(instanceId, collection, page.tracks, 0, false)}>
                <FaPlay /> Play
              </IconButton>
            )}
            {!page.tracks.every((t) => t.live) && (
              <IconButton onClick={() => playFrom(instanceId, collection, page.tracks, 0, true)}>
                <FaRandom /> Shuffle
              </IconButton>
            )}
            {canSave(instanceId, collection) &&
              (copy.copy ? (
                // saved already (from wherever this was opened): it opens your copy
                <IconButton label="Saved · open yours" active grow={0.45}
                            onClick={() => pushView(stack, { type: "collection", collection: copy.copy! })}>
                  <FaCheck />
                </IconButton>
              ) : (
                <IconButton label="Save playlist" grow={0.45} onClick={copy.save}>
                  <FaPlus />
                </IconButton>
              ))}
            {saved && (
              <IconButton label={saved.label} active={saved.on} grow={0.45} onClick={saved.toggle}>
                {saved.icon}
              </IconButton>
            )}
            {game && canPlayWithGame(collection) && (
              <IconButton
                // short: the hint bar has one line, and a game's name can fill it
                label={withGame ? "Don't play with game" : "Play with game"}
                active={withGame}
                grow={0.45}
                onClick={() => toggleGameMusic(instanceId, collection)}
              >
                <FaGamepad />
              </IconButton>
            )}
          </ButtonRow>
        )}
        {/* an artist with no songs of their own on the page: the button gets a row, and its words */}
        {saved && !playable && (
          <ButtonRow>
            <IconButton active={saved.on} onClick={saved.toggle}>
              {saved.icon} {saved.label}
            </IconButton>
          </ButtonRow>
        )}
        {page && page.tracks.length > 0 && <DownloadButton instanceId={instanceId} collection={collection} />}
        {page?.action && (
          <ButtonRow>
            <IconButton
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
            </IconButton>
          </ButtonRow>
        )}
      </Header>
      {page ? (
        <PageBody
          instanceId={instanceId}
          stack={stack}
          page={page}
          playlistId={isLocalPlaylist(collection.id) ? collection.id : undefined}
          from={collection}
        />
      ) : (
        <Loading result={result} />
      )}
    </>
  );
};

// the panel's source dots, as Now Playing's are in the media center (the panel has its own small copy of them)
const DOTS_CSS = `
.md-qdots { display: flex; justify-content: center; align-items: center; padding: 4px 0 0; }
.md-qdot { display: flex; align-items: center; height: 22px; padding: 0 3px; outline: none; }
.md-qdot > span { display: block; width: 7px; height: 7px; border-radius: 4px; background: rgba(255, 255, 255, 0.28);
  transition: width 0.22s ease, background 0.15s, transform 0.15s; }
.md-qdot.gpfocus > span { background: rgba(255, 255, 255, 0.75); transform: scale(1.3); }
.md-qdot[data-on] > span { width: 20px; background: #fff; transform: none; }
.md-qdots-name { text-align: center; font-size: 12px; opacity: 0.6; padding: 2px 0 6px; }
`;

/**
 * Every source's results, one source at a time, songs first: small dots for the sources that found something (the
 * D-pad along them shows each one's), the one showing named under them. Your files' show first, then music services', then the rest.
 */
const SearchResults: FC<{ instanceId: string; query: string }> = ({ instanceId, query }) => {
  const { pages } = useLibrarySearch(query, instanceId);
  // until one's picked, the best that's answered so far (your files, then music services...)
  const [chosen, setChosen] = useState<string | null>(null);
  if (!pages.length) return null;
  const at = pages.find((p) => p.source.id === chosen) ?? bestResults(pages)!;
  return (
    <>
      {pages.length > 1 && (
        // a row of its own, not a section: no section's padding around a line of dots
        <div style={{ margin: "-10px 0 -6px" }}>
          <style>{DOTS_CSS}</style>
          <Focusable flow-children="row" className="md-qdots">
            {pages.map((p) => (
              <Focusable
                key={p.source.id}
                className="md-qdot"
                data-on={p === at ? "" : undefined}
                noFocusRing
                onGamepadFocus={() => setChosen(p.source.id)}
                onClick={() => setChosen(p.source.id)}
              >
                <span />
              </Focusable>
            ))}
          </Focusable>
          <div className="md-qdots-name">{at.source.name}</div>
        </div>
      )}
      <PageBody key={at.source.id} instanceId={at.source.id} stack="search" page={{ ...at.page, tracks_first: true }} />
    </>
  );
};

/** Under an empty search field: the source's last searches. A searches again, X forgets one. */
const RecentSearches: FC<{ instanceId: string; onSearch: (query: string) => void }> = ({ instanceId, onSearch }) => {
  const [searches, setSearches] = useState<string[]>([]);
  const load = () =>
    searchHistory(instanceId)
      .then(setSearches)
      .catch(() => {});
  useEffect(() => {
    void load();
  }, [instanceId]);
  const forget = async (query: string) => {
    await forgetSearch(instanceId, query);
    await load();
  };
  if (!searches.length) return null;
  return (
    <PanelSection title="Recent searches">
      {searches.map((q) => (
        <ListRow
          key={q}
          leading={<FaHistory style={{ opacity: 0.6, flexShrink: 0 }} />}
          title={q}
          onActivate={() => onSearch(q)}
          onSecondary={() => forget(q)}
          secondaryLabel="Remove"
        />
      ))}
      <ButtonRow>
        <IconButton onClick={() => forget("")}>Clear recent searches</IconButton>
      </ButtonRow>
    </PanelSection>
  );
};

const SearchView: FC<{ instanceId: string; query: string }> = ({ instanceId, query }) => {
  const [draft, setDraft] = useState(query);
  const run = (q: string) => {
    // searching again fetches afresh (and so counts as the latest search), rather than reusing old results
    resultCache.delete(`${instanceId}:search:${q}`);
    setStack("search", [{ type: "search", query: q }]);
  };
  const submit = () => {
    const q = draft.trim();
    if (q) run(q);
  };
  return (
    <>
      <PanelSection>
        <PanelSectionRow>
          <Focusable flow-children="row" style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <TextField
                value={draft}
                placeholder="Songs, albums, artists"
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submit()}
              />
            </div>
            <DialogButton
              onClick={submit}
              style={{
                minWidth: 0,
                width: 40,
                height: 40,
                padding: 0,
                flexShrink: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <FaSearch />
            </DialogButton>
          </Focusable>
        </PanelSectionRow>
        {/* only when the field no longer says what the results are for */}
        {query && draft.trim() !== query && (
          <PanelSectionRow>
            <ScrollingText play text={`Results for “${query}”`} style={{ fontSize: 12, opacity: 0.6, paddingTop: 6 }} />
          </PanelSectionRow>
        )}
      </PanelSection>
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

export const Browser: FC<{ instanceId: string; stack: StackName }> = ({ instanceId, stack }) => {
  const { instanceId: navInstance, stacks } = useStore(navigation);

  useEffect(() => {
    if (navInstance !== instanceId) {
      resultCache.clear();
      navigation.set({ instanceId, stacks: { library: [ROOTS.library], search: [ROOTS.search] } });
    }
  }, [instanceId, navInstance]);

  const views = stacks[stack];
  // a history is never empty, but if it were the tab would go blank: its start view instead
  const view = views[views.length - 1] ?? ROOTS[stack];
  if (navInstance !== instanceId || !view) return null;

  // (sections are the media center's Browse pages; the panel's Library is the source's Home)
  if (view.type === "home" || view.type === "section") return <HomeView instanceId={instanceId} />;
  if (view.type === "search") return <SearchView key={view.query} instanceId={instanceId} query={view.query} />;
  // (a row's Show all is the media center's; the panel never opens one)
  if (view.type === "all") return <HomeView instanceId={instanceId} />;
  return (
    <CollectionView
      key={view.collection.kind + view.collection.id}
      instanceId={instanceId}
      stack={stack}
      collection={view.collection}
    />
  );
};

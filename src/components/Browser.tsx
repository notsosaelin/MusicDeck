import {
  DialogButton,
  Field,
  Focusable,
  GamepadButton,
  Navigation,
  PanelSection,
  PanelSectionRow,
  TextField,
} from "@decky/ui";
import { FC, Fragment, ReactNode, useEffect, useRef, useState } from "react";
import {
  FaCheck,
  FaChevronLeft,
  FaChevronRight,
  FaCompactDisc,
  FaDownload,
  FaExternalLinkAlt,
  FaGamepad,
  FaHeart,
  FaHistory,
  FaListUl,
  FaMusic,
  FaPlay,
  FaPlus,
  FaRandom,
  FaRegHeart,
  FaSearch,
  FaUser,
  FaUserCheck,
  FaUserPlus,
} from "react-icons/fa";
import {
  Collection,
  CollectionKind,
  Page,
  ServiceInfo,
  Result,
  SaveState,
  Section,
  Track,
  collectionAction,
  forgetSearch,
  getHome,
  isLocalPlaylist,
  libraryHome,
  libraryTab,
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
  View,
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
  QuietSpinner,
  RowText,
  ScrollingText,
  TrackNumber,
  TrackRow,
} from "./common";
import { useDownload, useSavedCopy } from "../download";
import { librarySources, useLibrarySearch, useMoreSongs, useSearchChoice } from "../library";
import { continueFrom, useOtherSessions } from "../devices";
import { PanelPages } from "./PanelPages";
import { TYPE_ICONS } from "./Settings";

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
          <QuietSpinner />
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
  /** a header of the list's own, inside its section (in place of `title`) */
  head?: ReactNode;
}> = ({ instanceId, stack, title, items, limit = 5, head }) => {
  const [expanded, setExpanded] = useState(false);
  useStore(runningGame); // rows get a menu (play with the game) while one runs
  const shown = expanded ? items : items.slice(0, limit);
  return (
    <PanelSection title={title}>
      {head}
      {shown.map((item) => {
        // a list mixing sources (the one library's): each item its own source's
        const id = item.source ?? instanceId;
        return (
          <CollectionRow
            key={`${item.source ?? ""}${item.kind}${item.id}`}
            instanceId={id}
            item={item}
            // opened as its own source's, whichever is playing (a source's Home opened from the Library)
            onOpen={(c) => pushView(stack, { type: "collection", collection: { ...c, source: c.source ?? id } })}
            onPlay={item.kind === "album" || item.kind === "playlist" ? () => playCollection(id, item) : undefined}
            onOptions={hasCollectionMenu(id, item) ? (e) => showCollectionMenu(id, item, e?.currentTarget) : undefined}
          />
        );
      })}
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

/** A page's name over it (a source's Home opened from the Library, a part of Your music) */
const ViewTitle: FC<{ children: ReactNode }> = ({ children }) => (
  <div style={{ fontSize: 18, fontWeight: 700, color: "#fff", padding: "10px 16px 2px" }}>{children}</div>
);

/** Your music's parts in the Library, each with its icon (the one library's tabs, as the backend has them) */
const PART_ICONS: Record<string, ReactNode> = {
  playlists: <FaListUl />,
  albums: <FaCompactDisc />,
  artists: <FaUser />,
  songs: <FaMusic />,
};
/** Rows Jump back in and Your playlists show before their Show all */
const LIBRARY_ROWS = 4;

/**
 * The panel's Library: every source's music, not just the one playing. What to carry on with first (what your other
 * devices are playing, then what you played lately, from any source); then Your music (every source's playlists,
 * albums, artists and songs, a row each); your playlists; and each source's own Home (its picks and charts) to open
 * without switching to it.
 */
const LibraryHome: FC<{ instanceId: string }> = ({ instanceId }) => {
  const state = useStore(pluginState);
  const result = useResult<Section[]>("library:home", () => libraryHome(), true);
  const sessions = useOtherSessions();
  const sections = result?.ok ? result.data : [];
  const jump = sections.find((s) => s.title === "Jump back in");
  const playlists = sections.find((s) => s.title === "Your playlists");
  const parts = state?.library_tabs ?? [];
  const sources = librarySources(state?.services ?? []);
  const continuing = sessions.map((s) => (
    <TrackRow
      key={`peer:${s.peer}`}
      track={{ ...s.track, artist: `On ${s.name}` }}
      showAlbum={false}
      artInstanceId={s.instanceId}
      onPlay={() => continueFrom(s)}
    />
  ));
  return (
    <>
      {(continuing.length > 0 || !!jump?.items.length) && (
        <CollectionList
          instanceId={instanceId}
          stack="library"
          title="Jump back in"
          head={continuing}
          items={jump?.items ?? []}
          limit={Math.max(1, LIBRARY_ROWS - continuing.length)}
        />
      )}
      {!!jump?.tracks?.length && <TrackList instanceId={instanceId} tracks={jump.tracks} limit={3} />}
      {parts.length > 0 && (
        <PanelSection title="Your music">
          {parts.map((t) => (
            <ListRow
              key={t.id}
              leading={<span style={{ fontSize: 16, opacity: 0.85, width: 40, display: "flex", justifyContent: "center" }}>{PART_ICONS[t.id]}</span>}
              title={t.title}
              onActivate={() => pushView("library", { type: "library", tab: t.id, title: t.title })}
            />
          ))}
        </PanelSection>
      )}
      {!!playlists?.items.length && (
        <CollectionList instanceId={instanceId} stack="library" title="Your playlists" items={playlists.items} limit={LIBRARY_ROWS} />
      )}
      {sources.length > 0 && (
        <PanelSection title="Sources">
          {sources.map((s) => (
            <ListRow
              key={s.id}
              leading={<span style={{ fontSize: 16, opacity: 0.85, width: 40, display: "flex", justifyContent: "center" }}>{TYPE_ICONS[s.type]}</span>}
              title={s.name}
              subtitle={s.id === state?.active_service ? "Playing from it" : undefined}
              onActivate={() => pushView("library", { type: "source", instanceId: s.id, title: s.name })}
            />
          ))}
        </PanelSection>
      )}
      {result === null && !sessions.length && <Loading result={null} />}
    </>
  );
};

/** One of Your music's parts: every source's (each item its own source's), its collections then its songs. */
const LibraryPart: FC<{ instanceId: string; tab: string; title: string }> = ({ instanceId, tab, title }) => {
  const result = useResult<Section[]>(`library:tab:${tab}`, () => libraryTab(tab), true);
  if (!result?.ok) return <Loading result={result} />;
  const items = result.data.flatMap((s) => s.items);
  const tracks = result.data.flatMap((s) => s.tracks ?? []);
  return (
    <>
      <ViewTitle>{title}</ViewTitle>
      {items.length > 0 && <CollectionList instanceId={instanceId} stack="library" items={items} limit={12} />}
      {tracks.length > 0 && <TrackList instanceId={instanceId} tracks={tracks} limit={20} />}
      {!items.length && !tracks.length && <Message>Nothing here yet</Message>}
    </>
  );
};

/** A source's own Home (`title`: its name, over it, when it's one opened from the Library). */
const HomeView: FC<{ instanceId: string; title?: string }> = ({ instanceId, title }) => {
  const result = useResult<Section[]>(`${instanceId}:home`, () => getHome(instanceId), true);
  if (!result?.ok) return <Loading result={result} />;
  if (!result.data.length) return null;
  return (
    <>
      {title && <ViewTitle>{title}</ViewTitle>}
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
  /** a header of the list's own, inside its section (in place of `title`) */
  head?: ReactNode;
}> = ({ instanceId, title, tracks, limit, showAlbum = true, playlistId, showArt = true, from, album, head }) => {
  const [expanded, setExpanded] = useState(false);
  const { queue, index } = useStore(player.state);
  const current = queue[index];
  const shown = limit && !expanded ? tracks.slice(0, limit) : tracks;
  // the artist most of the album's tracks have; the rest are guests, shown on their rows
  const albumArtist = album ? mostCommon(tracks.map((t) => t.artist)) : undefined;
  return (
    <PanelSection title={title}>
      {head}
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
          active={current?.instanceId === (t.source ?? instanceId) && current.track.id === t.id}
          onPlay={() =>
            // a station from a genre, a country, a search plays on its own; from a list of yours, the list
            t.live && !playlistId
              ? player.playList(instanceId, [t], 0)
              : from
                ? playFrom(instanceId, from, tracks, i)
                : player.playList(instanceId, tracks, i)
          }
          {...trackActions(t.source ?? instanceId, t, { playlistId })}
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
                            onClick={() => pushView(stack, { type: "collection", collection: { ...copy.copy!, source: copy.copy!.source ?? instanceId } })}>
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

// GamepadButton.TRIGGER_LEFT and TRIGGER_RIGHT from @decky/ui (an ambient enum, which isolated builds can't import)
const L2 = 7;
const R2 = 8;

/** The panel's source switcher and part headers */
const SWITCHER_CSS = `
.md-switcher { position: relative; display: flex; align-items: center; gap: 4px; padding: 10px 6px 8px; border-radius: 8px;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.07), rgba(255, 255, 255, 0.02)); outline: none;
  transition: background 0.2s, box-shadow 0.2s; }
.md-switcher.gpfocus { background: linear-gradient(135deg, rgba(26, 159, 255, 0.28), rgba(26, 159, 255, 0.08));
  box-shadow: inset 0 0 0 2px rgba(255, 255, 255, 0.85); }
.md-switcher-arrow { flex-shrink: 0; width: 26px; height: 26px; display: flex; align-items: center; justify-content: center;
  border-radius: 13px; color: rgba(255, 255, 255, 0.55); font-size: 12px; transition: color 0.15s, background 0.15s; }
.md-switcher.gpfocus .md-switcher-arrow { color: #fff; background: rgba(255, 255, 255, 0.12); }
.md-switcher-mid { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: center; gap: 3px; }
.md-switcher-name { display: flex; align-items: center; gap: 8px; max-width: 100%; font-size: 16px; font-weight: 700; color: #fff; }
.md-switcher-name > span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.md-switcher-name > svg { flex-shrink: 0; font-size: 18px; opacity: 0.9; }
.md-switcher-what { font-size: 11px; opacity: 0.6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.md-switcher-dots { display: flex; gap: 5px; margin-top: 3px; }
.md-switcher-dots > span { width: 5px; height: 5px; border-radius: 3px; background: rgba(255, 255, 255, 0.28);
  transition: width 0.22s ease, background 0.15s; }
.md-switcher-dots > span[data-on] { width: 16px; background: #fff; }
.md-switcher-name, .md-switcher-what { animation: md-switch-in 0.22s ease both; }
@keyframes md-switch-in { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: none; } }
.md-part-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 2px 0 0; }
.md-part-title { font-size: 12px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; opacity: 0.7; }
.md-part-all { font-size: 12px; padding: 4px 8px; border-radius: 4px; opacity: 0.75; outline: none; white-space: nowrap; }
.md-part-all.gpfocus { opacity: 1; background: rgba(255, 255, 255, 0.15); box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.7); }
`;

/** What a source found, in a few words: "18 songs · 6 albums · 3 artists" */
function foundWords(page: Page): string {
  const parts: string[] = [];
  const n = page.tracks.length;
  const stations = n > 0 && page.tracks.every((t) => t.live);
  if (n) parts.push(`${n} ${stations ? (n === 1 ? "station" : "stations") : n === 1 ? "song" : "songs"}`);
  for (const [kind, items] of groupByKind(page.collections)) parts.push(`${items.length} ${KIND_TITLES[kind].toLowerCase()}`);
  for (const x of page.extra ?? []) if (x.tracks.length) parts.push(`${x.tracks.length} ${x.title.toLowerCase()}`);
  return parts.slice(0, 3).join(" · ");
}

/**
 * Where the results are from, as one control: the source's icon and name between two arrows, what it found under
 * them, and dots for where it is among the sources that found something. Left and right on it go to the source
 * before or after (round from the last to the first), as do L2/R2 from anywhere in the results, and a tap on an arrow;
 * A goes into its results.
 */
const SourceSwitcher: FC<{
  sources: { source: ServiceInfo; page: Page }[];
  at: number;
  onPick: (i: number) => void;
  onEnter: () => void;
}> = ({ sources, at, onPick, onEnter }) => {
  const n = sources.length;
  const go = (by: number) => onPick((at + by + n) % n);
  const here = sources[at];
  // left and right, however Steam hands them over (as a direction, or as the D-pad's buttons)
  const sideways = (e: any) => {
    const button = e?.detail?.button;
    const by = button === GamepadButton.DIR_LEFT ? -1 : button === GamepadButton.DIR_RIGHT ? 1 : 0;
    if (!by || n < 2) return;
    e.preventDefault?.();
    e.stopPropagation?.();
    go(by);
  };
  return (
    <PanelSectionRow>
      <style>{SWITCHER_CSS}</style>
      <Focusable
        className="md-switcher"
        noFocusRing
        onGamepadDirection={sideways}
        onButtonDown={sideways}
        onOKActionDescription="Results"
        onActivate={onEnter}
      >
        <span className="md-switcher-arrow" onClick={() => go(-1)} style={{ visibility: n > 1 ? "visible" : "hidden" }}>
          <FaChevronLeft />
        </span>
        <div className="md-switcher-mid">
          {/* keyed: the name and its words come in fresh with each source */}
          <div className="md-switcher-name" key={`n${here.source.id}`}>
            {TYPE_ICONS[here.source.type]}
            <span>{here.source.name}</span>
          </div>
          <div className="md-switcher-what" key={`w${here.source.id}`}>{foundWords(here.page)}</div>
          {n > 1 && (
            <div className="md-switcher-dots">
              {sources.map((s, i) => (
                <span key={s.source.id} data-on={i === at ? "" : undefined} />
              ))}
            </div>
          )}
        </div>
        <span className="md-switcher-arrow" onClick={() => go(1)} style={{ visibility: n > 1 ? "visible" : "hidden" }}>
          <FaChevronRight />
        </span>
      </Focusable>
    </PanelSectionRow>
  );
};

/** A part's header: its name, and its Show all at the other end of the line (no row of its own under the part). */
const PartHead: FC<{ title: string; count: number; shown: number; open: () => void }> = ({ title, count, shown, open }) => (
  <PanelSectionRow>
    <Focusable flow-children="row" className="md-part-head">
      <span className="md-part-title">{title}</span>
      {count > shown && (
        <Focusable className="md-part-all" noFocusRing onActivate={open} onClick={open} onOKActionDescription="Show all">
          Show all {count} ›
        </Focusable>
      )}
    </Focusable>
  </PanelSectionRow>
);

/**
 * A search in the panel, as pages (as Now Playing's are): the field and where the results are from (SourceSwitcher);
 * then a page for each kind found (songs first, then videos, albums, artists, playlists...), each as many as fit, its
 * Show all in its header. Your files' results show first, then music services', then the rest; it stays on the one
 * picked.
 */
const SearchPages: FC<{ instanceId: string; query: string; field: ReactNode }> = ({ instanceId, query, field }) => {
  const found = useLibrarySearch(query, instanceId);
  // the sources in a steady order, the best showing first (useSearchChoice)
  const { ordered: pages, at, pick: choose } = useSearchChoice(query, found.pages, found.waiting);
  const index = at ? pages.indexOf(at) : 0;
  const pick = (i: number) => pages[i] && choose(pages[i].source.id);
  // the panel's own document (Quick Access is a window of its own, not the plugin's)
  const anchor = useRef<HTMLSpanElement>(null);
  // the first song (or whatever's first), not the Show all in its heading
  const firstResult = () => {
    const doc = anchor.current?.ownerDocument;
    return (
      doc?.querySelector<HTMLElement>(".md-search-pages .md-list-row") ??
      doc?.querySelector<HTMLElement>(".md-search-pages .md-part-all")
    );
  };
  // into the results: the controller onto the first of them (the pages slide to it)
  const enter = () => firstResult()?.focus();
  // the results of a search just run come in: shown at once (the page with them slides up), unless the controller has
  // gone into them (or onto the switcher) in the meantime; results shown again (coming back to the tab) stay put, the
  // field above them
  const shown = !!at;
  useEffect(() => {
    if (!shown || justSearched !== query) return;
    justSearched = null;
    // (the field is drawn anew for the search, so Steam puts the controller somewhere of its choosing, the panel
    // itself or the switcher, and can do so after this: tried again a few times, until it's on a result)
    const timers = [0, 150, 400].map((ms) =>
      window.setTimeout(() => {
        const active = anchor.current?.ownerDocument.activeElement as HTMLElement | null | undefined;
        if (!active?.closest(".md-list-row, .md-part-all")) enter();
      }, ms),
    );
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [shown]);
  const id = at?.source.id ?? instanceId;
  const page = at?.page;
  const all = (title: string, items: Collection[], tracks: Track[], part?: string) =>
    pushView("search", { type: "all", title, items, tracks, instanceId: id, more: part ? { query, part } : undefined });
  const draw = (rows: number) => {
    const first = (
      <>
        <span ref={anchor} />
        {field}
        {at ? (
          <PanelSection>
            <SourceSwitcher sources={pages} at={index} onPick={pick} onEnter={enter} />
          </PanelSection>
        ) : found.waiting > 0 || found.pages.length ? (
          // the sources still answering
          <PanelSection>
            <PanelSectionRow>
              <div style={{ display: "flex", justifyContent: "center", padding: 16 }}>
                <QuietSpinner />
              </div>
            </PanelSectionRow>
          </PanelSection>
        ) : (
          <Message>Nothing found for “{query}”</Message>
        )}
      </>
    );
    const more: ReactNode[] = [];
    const head = (title: string, count: number, open: () => void) => <PartHead title={title} count={count} shown={rows} open={open} />;
    const songs = page?.tracks ?? [];
    // a radio source's are stations, not songs
    const songsTitle = songs.length && songs.every((t) => t.live) ? "Stations" : "Songs";
    if (songs.length)
      more.push(
        <TrackList key={`${id}:songs`} instanceId={id} tracks={songs.slice(0, rows)}
                   head={head(songsTitle, songs.length, () => all(songsTitle, [], songs, "Songs"))} />,
      );
    for (const x of page?.extra ?? [])
      if (x.tracks.length)
        more.push(
          <TrackList key={`${id}:${x.title}`} instanceId={id} tracks={x.tracks.slice(0, rows)}
                     head={head(x.title, x.tracks.length, () => all(x.title, [], x.tracks, x.title))} />,
        );
    for (const [kind, items] of groupByKind(page?.collections ?? []))
      more.push(
        <CollectionList key={`${id}:${kind}`} instanceId={id} stack="search" items={items.slice(0, rows)} limit={rows}
                        head={head(KIND_TITLES[kind], items.length, () => all(KIND_TITLES[kind], items, []))} />,
      );
    return [first, ...more];
  };
  return (
    <PanelPages
      className="md-search-pages"
      pages={draw}
      onButtonDown={(e: any) => {
        const button = e?.detail?.button;
        if ((button === L2 || button === R2) && pages.length > 1) pick((index + (button === R2 ? 1 : -1) + pages.length) % pages.length);
      }}
    />
  );
};

/** The search the user just ran (its results take the controller when they come in); null once they have */
let justSearched: string | null = null;

/** A part's Show all: every song, video or album of it, a page of their own. */
const AllView: FC<{ instanceId: string; view: Extract<View, { type: "all" }> }> = ({ instanceId, view }) => {
  const id = view.instanceId ?? instanceId;
  const { tracks, load, loading } = useMoreSongs(id, view.tracks, view.more);
  return view.tracks.length ? (
    <>
      <TrackList instanceId={id} title={view.title} tracks={tracks} />
      {load && (
        <PanelSection>
          <PanelSectionRow>
            <Field focusable highlightOnFocus bottomSeparator="none" onActivate={load} onClick={load}>
              <div style={{ fontSize: 13, opacity: 0.8 }}>{loading ? "Loading…" : "Load more"}</div>
            </Field>
          </PanelSectionRow>
        </PanelSection>
      )}
    </>
  ) : (
    <CollectionList instanceId={id} stack="search" title={view.title} items={view.items} limit={view.items.length} />
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
    justSearched = q;
    // over the empty field: B (or leaving the tab) goes back to it
    setStack("search", [ROOTS.search, { type: "search", query: q }]);
  };
  const submit = () => {
    const q = draft.trim();
    if (q) run(q);
  };
  const field = (
      <PanelSection>
        <PanelSectionRow>
          <Focusable className="md-search-field" flow-children="row" style={{ display: "flex", gap: 8, alignItems: "center" }}>
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
  );
  if (query) return <SearchPages instanceId={instanceId} query={query} field={field} />;
  return (
    <>
      {field}
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

  // the panel's Library: every source's music (sections are the media center's Browse pages)
  if (view.type === "home" || view.type === "section") return <LibraryHome instanceId={instanceId} />;
  if (view.type === "library") return <LibraryPart instanceId={instanceId} tab={view.tab} title={view.title} />;
  if (view.type === "source") return <HomeView key={view.instanceId} instanceId={view.instanceId} title={view.title} />;
  if (view.type === "search") return <SearchView key={view.query} instanceId={instanceId} query={view.query} />;
  // a search's Show all
  if (view.type === "all") return <AllView instanceId={instanceId} view={view} />;
  return (
    <CollectionView
      key={view.collection.kind + view.collection.id}
      // one of another source's (the one library's, a source's own Home): opened there
      instanceId={view.collection.source ?? instanceId}
      stack={stack}
      collection={view.collection}
    />
  );
};

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
import { FC, Fragment, ReactNode, useEffect, useState } from "react";
import { FaDownload, FaExternalLinkAlt, FaPlay, FaPlus, FaRandom, FaSearch } from "react-icons/fa";
import {
  Collection,
  CollectionKind,
  Page,
  Result,
  Section,
  Track,
  getHome,
  isLocalPlaylist,
  openCollection,
  search,
} from "../api";
import { hasFeature, player } from "../player";
import { useStore } from "../store";
import { ROOTS, StackName, dataVersion, navigation, pushView, resultCache, setStack } from "../nav";
import { notify } from "../notify";
import { MenuContext, saveCollection, showCollectionMenu, showTrackMenu } from "./TrackMenu";
import { Art, ButtonRow, CollectionRow, IconButton, Message, RowText, ScrollingText, TrackRow } from "./common";

function useResult<T>(key: string, load: () => Promise<Result<T>>): Result<T> | null {
  const [result, setResult] = useState<Result<T> | null>(() => resultCache.get(key) ?? null);
  const [shownKey, setShownKey] = useState(key);
  const { v } = useStore(dataVersion);
  useEffect(() => {
    const cached = resultCache.get(key);
    if (cached) {
      setShownKey(key);
      return setResult(cached);
    }
    // refetching the same view after an edit: keep showing it instead of flashing a spinner
    if (key !== shownKey) setResult(null);
    setShownKey(key);
    let cancelled = false;
    load()
      .catch((e) => ({ ok: false as const, error: String(e) }))
      .then((r) => {
        if (r.ok) resultCache.set(key, r);
        if (!cancelled) setResult(r);
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

const trackActions = (instanceId: string, track: Track, ctx: MenuContext = {}) => ({
  onSecondary: () => {
    player.enqueue(instanceId, [track]);
    notify("Added to queue", track.title, { instanceId, track });
  },
  secondaryLabel: "Add to queue",
  onOptions: (e?: any) => showTrackMenu(instanceId, track, e?.currentTarget, ctx),
  optionsLabel: "Options",
});

/** Remote playlists/albums can be saved as MusicDeck playlists when the source has no playlists of its own. */
const canSave = (instanceId: string, c: Collection) =>
  (c.kind === "playlist" || c.kind === "album") && !isLocalPlaylist(c.id) && !hasFeature(instanceId, "playlists");
const hasCollectionMenu = (instanceId: string, c: Collection) => isLocalPlaylist(c.id) || canSave(instanceId, c);

const CollectionList: FC<{
  instanceId: string;
  stack: StackName;
  title?: string;
  items: Collection[];
  limit?: number;
}> = ({ instanceId, stack, title, items, limit = 5 }) => {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? items : items.slice(0, limit);
  return (
    <PanelSection title={title}>
      {shown.map((item) => (
        <CollectionRow
          key={item.kind + item.id}
          instanceId={instanceId}
          item={item}
          onOpen={(c) => pushView(stack, { type: "collection", collection: c })}
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
  const result = useResult<Section[]>(`${instanceId}:home`, () => getHome(instanceId));
  if (!result?.ok) return <Loading result={result} />;
  if (!result.data.length)
    return <Message>Nothing here yet. If you just added a library it may still be scanning.</Message>;
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

const KIND_TITLES: Record<CollectionKind, string> = {
  artist: "Artists",
  album: "Albums",
  playlist: "Playlists",
  folder: "Folders",
  list: "Browse",
};
const KIND_ORDER: CollectionKind[] = ["artist", "album", "playlist", "folder", "list"];

function groupByKind(items: Collection[]): [CollectionKind, Collection[]][] {
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
}> = ({ instanceId, title, tracks, limit, showAlbum = true, playlistId, showArt = true }) => {
  const [expanded, setExpanded] = useState(false);
  const { queue, index } = useStore(player.state);
  const current = queue[index];
  const shown = limit && !expanded ? tracks.slice(0, limit) : tracks;
  return (
    <PanelSection title={title}>
      {shown.map((t, i) => (
        <TrackRow
          key={t.id + i}
          track={t}
          showAlbum={showAlbum}
          artInstanceId={showArt ? instanceId : undefined}
          active={current?.instanceId === instanceId && current.track.id === t.id}
          onPlay={() => player.playList(instanceId, tracks, i)}
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

const PageBody: FC<{ instanceId: string; stack: StackName; page: Page; playlistId?: string }> = ({
  instanceId,
  stack,
  page,
  playlistId,
}) => {
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
  <PanelSection>
    <PanelSectionRow>
      <div style={{ display: "flex", gap: 12, alignItems: "center", padding: "4px 0 8px" }}>
        {art !== undefined && <Art instanceId={instanceId} art={art} size={56} />}
        <RowText size="large" scroll="always" title={title} subtitle={subtitle} />
      </div>
    </PanelSectionRow>
    {children}
  </PanelSection>
);

/** A page's header button: steam:// URLs are Steam's own actions (its install dialog shows in the
 * main window, so the menu closes); anything else opens in Steam's browser. */
const runAction = (url: string) => {
  Navigation.CloseSideMenus();
  // (@decky/ui's typings mistake SteamClient.URL for the DOM's URL class)
  if (url.startsWith("steam://"))
    (window.SteamClient.URL as unknown as { ExecuteSteamURL(url: string): void }).ExecuteSteamURL(url);
  else Navigation.NavigateToExternalWeb(url);
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
            <IconButton onClick={() => player.playList(instanceId, page.tracks, 0, false)}>
              <FaPlay /> Play
            </IconButton>
            <IconButton onClick={() => player.playList(instanceId, page.tracks, 0, true)}>
              <FaRandom /> Shuffle
            </IconButton>
            {canSave(instanceId, collection) && (
              <IconButton
                label="Save to your playlists"
                grow={0.45}
                onClick={() => saveCollection(instanceId, collection)}
              >
                <FaPlus />
              </IconButton>
            )}
          </ButtonRow>
        )}
        {page?.action && (
          <ButtonRow>
            <IconButton onClick={() => runAction(page.action!.url)}>
              {page.action.url.startsWith("steam://") ? <FaDownload /> : <FaExternalLinkAlt />} {page.action.label}
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
        />
      ) : (
        <Loading result={result} />
      )}
    </>
  );
};

const SearchResults: FC<{ instanceId: string; query: string }> = ({ instanceId, query }) => {
  const result = useResult<Page>(`${instanceId}:search:${query}`, () => search(instanceId, query));
  if (!result?.ok) return <Loading result={result} />;
  return <PageBody instanceId={instanceId} stack="search" page={result.data} />;
};

const SearchView: FC<{ instanceId: string; query: string }> = ({ instanceId, query }) => {
  const [draft, setDraft] = useState(query);
  const submit = () => {
    const q = draft.trim();
    if (q) setStack("search", [{ type: "search", query: q }]);
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
        {query && (
          <PanelSectionRow>
            <ScrollingText play text={`Results for “${query}”`} style={{ fontSize: 12, opacity: 0.6, paddingTop: 6 }} />
          </PanelSectionRow>
        )}
      </PanelSection>
      {query && <SearchResults instanceId={instanceId} query={query} />}
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
  const view = views[views.length - 1];
  if (navInstance !== instanceId || !view) return null;

  if (view.type === "home") return <HomeView instanceId={instanceId} />;
  if (view.type === "search") return <SearchView key={view.query} instanceId={instanceId} query={view.query} />;
  return (
    <CollectionView
      key={view.collection.kind + view.collection.id}
      instanceId={instanceId}
      stack={stack}
      collection={view.collection}
    />
  );
};

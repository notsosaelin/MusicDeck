/**
 * The Music section's library: a sidebar (search, the source, its tabs, as the media center has them) and
 * pages (Home, a tab, a search, an album, artist or playlist), from the same calls the media center makes.
 * Laid out as Steam's library is: a list on the left, the page on the right, an album's page headed by its
 * art as a game's is by its hero, with a green Play.
 */
import { CSSProperties, FC, Fragment, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { FaChevronLeft, FaChevronRight, FaDownload, FaCompactDisc, FaEject, FaEllipsisH, FaFolder, FaHdd, FaListUl, FaPlay, FaRandom, FaSearch, FaUser } from "react-icons/fa";
import * as api from "../api";
import { Collection, Page, Section, SmartPlaylist, Track } from "../api";
import { showCollectionMenu, showTrackMenu } from "../components/TrackMenu";
import { lookupOpener } from "../components/TagEditor";
import { NameModal } from "../components/NameModal";
import { dataVersion, invalidateBrowserCache, playFrom } from "../nav";
import { notify } from "../notify";
import { player } from "../player";
import { mediaUrl, pluginState, useStore } from "../store";
import { Place, go, goHome, useLiveSource, usePlace, useResult } from "./data";
import {
  DRAG_TYPE,
  Dragged,
  SmartEditor,
  dropOnDevice,
  dropTargets,
  pickFiles,
  dropOnPlaylist,
  loadSmart,
  showSelectionMenu,
  showSmartMenu,
  smartLists,
} from "./manage";
import { SIZES, desktopDialog } from "./dialog";
import { showSettings } from "./polish";
import { canRate } from "../ratings";
import { Stars } from "./parts";
import { StatsPage } from "./stats";
import { ToolsPage } from "./tools";
import { ColumnsPage } from "./columns";
import { useDownload } from "../download";
import { LIBRARY_SORTS, LibrarySort, filterLibrary, sourcesIn, useLibrarySearch, useSearchChoice } from "../library";
import { continueFrom, useOtherSessions } from "../devices";

/** Where a right-click was, for a menu to open there: Steam's menus take the mouse event itself in place of an
 *  element (Decky's typings say only EventTarget). */
const clickPoint = (e: { nativeEvent: Event }) => e.nativeEvent as unknown as EventTarget;

/** Whether a source keeps tags MusicDeck can fix (Local files): its albums can be looked up. */
const canLookUp = (id: string) => !!pluginState.get()?.services.find((s) => s.id === id)?.features.includes("tags");

const art = (instanceId: string, id: string | null | undefined) => (id ? mediaUrl("art", instanceId, id) || "" : "");

const clock = (s: number) => {
  const t = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}` : `${m}:${String(t % 60).padStart(2, "0")}`;
};

// --- the sidebar ------------------------------------------------------------------------------------

/** Whether a source's own tab is one the one library gathers (it shows under Your music, not the source). */
const merged = (tabId: string, group?: string) =>
  (group ?? "yours") === "yours" && ["playlists", "musicdeck-playlists", "albums", "soundtracks", "artists", "composers", "songs", "liked"].includes(tabId);

/** The sources opened in the sidebar (their own tabs shown), kept between visits. */
const OPEN_KEY = "musicdeck-open-sources";
const readOpen = (): string[] => {
  try {
    const v = JSON.parse(localStorage.getItem(OPEN_KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
};

export const Sidebar: FC = () => {
  const state = useStore(pluginState);
  const place = usePlace();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string[]>(readOpen);
  const at = (p: Place) => JSON.stringify(p) === JSON.stringify(place);
  const services = state?.services ?? [];
  const local = services.find((s) => s.type === "local");
  const toggle = (id: string) => {
    const next = open.includes(id) ? open.filter((x) => x !== id) : [...open, id];
    setOpen(next);
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify(next));
    } catch {}
  };
  return (
    <nav className="md-sidebar">
      <form
        className="md-search"
        onSubmit={(e) => {
          e.preventDefault();
          if (query.trim()) go({ type: "search", query: query.trim() });
        }}
      >
        <FaSearch style={{ opacity: 0.6 }} />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" spellCheck={false}
               title={'Every source at once. In Local files, fields too: artist:nirvana year:>1990 rating:>=4 genre:jazz plays:0 format:flac'} />
      </form>
      <div className="md-nav-item" data-current={at({ type: "home" }) ? "" : undefined} onClick={() => go({ type: "home" })}>
        Home
      </div>
      <div className="md-nav-item" data-current={at({ type: "stats" }) ? "" : undefined} onClick={() => go({ type: "stats" })}>
        Your stats
      </div>
      {!!state?.library_tabs?.length && <div className="md-nav-heading">Your music</div>}
      {(state?.library_tabs ?? []).map((t) => {
        const p: Place = { type: "tab", id: t.id, title: t.title };
        return (
          <div key={t.id} className="md-nav-item" data-current={at(p) ? "" : undefined} onClick={() => go(p)}>
            {t.title}
          </div>
        );
      })}
      {!!services.length && <div className="md-nav-heading">Sources</div>}
      {services.map((s) => {
        const own = s.tabs.filter((t) => t.id !== "home" && t.title.toLowerCase() !== "home" && !merged(t.id, t.group));
        const shown = open.includes(s.id);
        const home: Place = { type: "home", source: s.id };
        return (
          <Fragment key={s.id}>
            <div className="md-nav-item md-nav-source" data-current={at(home) ? "" : undefined} onClick={() => go(home)}>
              <span>{s.name}</span>
              {(own.length > 0 || s.type === "local") && (
                <button
                  className="md-nav-twisty"
                  aria-label={shown ? `Hide ${s.name}'s parts` : `Show ${s.name}'s parts`}
                  data-open={shown ? "" : undefined}
                  onClick={(e) => {
                    e.stopPropagation();
                    toggle(s.id);
                  }}
                >
                  <FaChevronRight />
                </button>
              )}
            </div>
            {shown &&
              own.map((t) => {
                const p: Place = { type: "tab", id: t.id, title: t.title, source: s.id };
                return (
                  <div key={t.id} className="md-nav-item md-nav-sub" data-current={at(p) ? "" : undefined} onClick={() => go(p)}>
                    <span>{t.title}</span>
                    {t.subtitle && <span className="md-nav-count">{t.subtitle.replace(/\D+$/, "")}</span>}
                  </div>
                );
              })}
            {shown && s.features.includes("tags") && (
              <>
                <div className="md-nav-item md-nav-sub" data-current={at({ type: "columns" }) ? "" : undefined} onClick={() => go({ type: "columns" })}>
                  Column browser
                </div>
                <div className="md-nav-item md-nav-sub" data-current={at({ type: "tools", source: s.id }) ? "" : undefined}
                     onClick={() => go({ type: "tools", source: s.id })}>
                  Library tools
                </div>
              </>
            )}
          </Fragment>
        );
      })}
      {local && <Devices local={local.id} />}
      {!!services.length && <Playlists at={at} />}
      <div className="md-nav-item md-nav-add" style={{ marginTop: 16 }} onClick={showSettings}>
        Settings…
      </div>
    </nav>
  );
};

/** A sidebar entry that takes dropped songs and albums. */
const DropItem: FC<{ onDrop: (d: Dragged) => void; current?: boolean; onClick?: () => void; onMenu?: (e: any) => void; children: ReactNode }> = ({
  onDrop,
  current,
  onClick,
  onMenu,
  children,
}) => {
  // the drop itself is caught by catchDrops (manage.tsx), before Steam's window can refuse it
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    dropTargets.set(el, onDrop);
    return () => {
      dropTargets.delete(el);
    };
  }, [onDrop]);
  return (
    <div
      ref={ref}
      className="md-nav-item"
      data-current={current ? "" : undefined}
      onClick={onClick}
      onContextMenu={(e) => {
        if (!onMenu) return;
        e.preventDefault();
        onMenu(e);
      }}
    >
      {children}
    </div>
  );
};

/** MusicDeck's playlists, of every source, and Local files' smart playlists. */
const Playlists: FC<{ at: (p: Place) => boolean }> = ({ at }) => {
  const version = useStore(dataVersion).v;
  const services = useStore(pluginState)?.services ?? [];
  const ids = services.map((s) => s.id).join(",");
  const local = services.find((s) => s.type === "local")?.id;
  const [lists, setLists] = useState<Collection[]>([]);
  const smart = useStore(smartLists);
  useEffect(() => {
    let live = true;
    Promise.all(services.map((s) => api.localPlaylists(s.id).then((l) => l.map((c) => ({ ...c, source: s.id }))).catch(() => [])))
      .then((all) => live && setLists(all.flat()))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [ids, version]);
  useEffect(() => {
    if (local) loadSmart();
  }, [local]);
  // a new playlist is Local files' (or, without it, the live source's): its songs come from there
  const home = local ?? pluginState.get()?.active_service ?? services[0]?.id ?? "";
  const create = () =>
    desktopDialog(
      <NameModal
        title="New playlist"
        action="Create"
        onSubmit={async (name) => {
          const made = await api.createPlaylist(home, name, []);
          invalidateBrowserCache();
          go({ type: "collection", collection: made, source: home });
        }}
      />,
      { ...SIZES.name, title: "New playlist" },
    );
  return (
    <>
      <div className="md-nav-heading">Playlists</div>
      {lists.map((l) => {
        const p: Place = { type: "collection", collection: l, source: l.source };
        return (
          <DropItem
            key={`${l.source}:${l.id}`}
            current={at(p)}
            onClick={() => go(p)}
            onMenu={(e) => showCollectionMenu(l.source ?? home, l, clickPoint(e))}
            onDrop={(d) => dropOnPlaylist(d, l.id, l.title)}
          >
            <span>{l.title}</span>
          </DropItem>
        );
      })}
      {smart.map((s) => {
        const p: Place = { type: "smart", playlist: s };
        return (
          <div
            key={s.id}
            className="md-nav-item"
            data-current={at(p) ? "" : undefined}
            onClick={() => go(p)}
            onContextMenu={(e) => {
              e.preventDefault();
              showSmartMenu(s, clickPoint(e), () => goHome());
            }}
          >
            <span>⚙ {s.name}</span>
          </div>
        );
      })}
      <div className="md-nav-item md-nav-add" onClick={create}>
        + New playlist
      </div>
      {local && (
        <div
          className="md-nav-item md-nav-add"
          onClick={() => desktopDialog(<SmartEditor source={local} onSaved={(s) => go({ type: "smart", playlist: s })} />, SIZES.smart)}
        >
          + New smart playlist
        </div>
      )}
      {local && (
        <div className="md-nav-item md-nav-add" title="Or drag music files onto Music" onClick={(e) => pickFiles(e.currentTarget.ownerDocument)}>
          + Add music files…
        </div>
      )}
    </>
  );
};

const gb = (n: number) => (n >= 1e12 ? `${(n / 1e12).toFixed(1)} TB` : `${(n / 1e9).toFixed(n >= 1e10 ? 0 : 1)} GB`);

/** What's plugged in, with how full it is; drop music on one to copy it there. */
const Devices: FC<{ local: string }> = ({ local }) => {
  const devices = useStore(pluginState)?.devices ?? [];
  if (!devices.length) return null;
  return (
    <>
      <div className="md-nav-heading">Devices</div>
      {devices.map((d) => {
        const space = d.status?.space as { total: number; free: number } | null | undefined;
        const collection: Collection = { id: `dv~${d.id}~`, kind: "device", title: d.name, subtitle: "", art: null };
        const takes = d.kind !== "cd" && d.kind !== "disc" && d.kind !== "ios" && d.status?.writable !== false;
        return (
          <DropItem
            key={d.id}
            onClick={() => go({ type: "collection", collection, source: local })}
            onMenu={(e) => showCollectionMenu(local, collection, clickPoint(e))}
            onDrop={(x) => (takes ? dropOnDevice(x, d.id, d.name) : notify("MusicDeck", `${d.name} can only be read`))}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="md-td">{d.name}</div>
              {space && space.total > 0 && (
                <div className="md-capacity" title={`${gb(space.free)} free of ${gb(space.total)}`}>
                  <div style={{ width: `${Math.min(100, ((space.total - space.free) / space.total) * 100)}%` }} />
                </div>
              )}
            </div>
          </DropItem>
        );
      })}
    </>
  );
};

// --- pages ------------------------------------------------------------------------------------------

export const Pages: FC = () => {
  const place = usePlace();
  const live = useLiveSource();
  const services = useStore(pluginState)?.services ?? [];
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => scroller.current?.scrollTo(0, 0), [place]);
  // a page of one source's (its Home, its tab, an album of it), else the one library's; what has no source of its
  // own (an album opened from the player) is the live source's
  const own = "source" in place && place.source && services.some((s) => s.id === place.source) ? place.source : null;
  const source = live ?? services[0] ?? null;
  if (!source)
    return (
      <div className="md-page">
        <button className="md-button md-wide" style={{ marginTop: 24 }} onClick={showSettings}>
          Add a music source…
        </button>
      </div>
    );
  const id = own ?? source.id;
  const name = (sid: string) => services.find((s) => s.id === sid)?.name ?? "";
  const local = services.find((s) => s.type === "local")?.id ?? id;
  return (
    <div ref={scroller} className="md-page">
      {place.type === "home" &&
        (own ? (
          <SectionsPage id={own} title={name(own)} what="home" fetch={() => api.getHome(own)} />
        ) : (
          <SectionsPage id={id} title="Home" what="library:home" fetch={() => api.libraryHome()} />
        ))}
      {place.type === "tab" &&
        (own ? (
          <SectionsPage id={own} title={place.title} what={`tab:${place.id}`} fetch={() => api.getCenterTab(own, place.id)} />
        ) : (
          <SectionsPage id={id} title={place.title} what={`library:tab:${place.id}`} fetch={() => api.libraryTab(place.id)} />
        ))}
      {place.type === "search" && <SearchPage id={id} query={place.query} />}
      {place.type === "collection" && <CollectionPage id={own ?? place.collection.source ?? id} collection={place.collection} />}
      {place.type === "smart" && <SmartPage id={place.playlist.source || local} playlist={place.playlist} />}
      {place.type === "stats" && <StatsPage />}
      {place.type === "tools" && <ToolsPage id={place.source ?? local} />}
      {place.type === "columns" && <ColumnsPage id={local} />}
    </div>
  );
};

const SmartPage: FC<{ id: string; playlist: SmartPlaylist }> = ({ id, playlist }) => {
  const latest = useStore(smartLists).find((p) => p.id === playlist.id) ?? playlist;
  const result = useResult(`${id}:desktop:smart:${JSON.stringify(latest.rules)}`, () => api.smartTracks(id, latest.rules));
  const tracks = result?.ok ? result.data : [];
  const from: Collection = { id: `smart:${latest.id}`, kind: "list", title: latest.name, subtitle: "", art: null };
  return (
    <>
      <header className="md-hero">
        <div className="md-hero-body">
          <div className="md-hero-cover md-smart-cover">⚙</div>
          <div style={{ minWidth: 0 }}>
            <div className="md-kind">Smart playlist</div>
            <div className="md-hero-title">{latest.name}</div>
            <div className="md-dim">{result?.ok ? `${tracks.length} songs` : ""}</div>
            <div className="md-actions">
              <button className="md-play" disabled={!tracks.length} onClick={() => playFrom(id, from, tracks, 0)}>
                <FaPlay /> Play
              </button>
              <button className="md-button md-wide" disabled={tracks.length < 2} onClick={() => playFrom(id, from, tracks, 0, true)}>
                <FaRandom /> Shuffle
              </button>
              <button className="md-button md-wide" onClick={() => desktopDialog(<SmartEditor source={latest.source} existing={latest} />, SIZES.smart)}>
                Edit rules
              </button>
              <button className="md-button md-wide" aria-label="More" onClick={(e) => showSmartMenu(latest, e.currentTarget, () => goHome())}>
                <FaEllipsisH />
              </button>
            </div>
          </div>
        </div>
      </header>
      <Loading result={result} />
      {!!tracks.length && <TrackTable id={id} tracks={tracks} from={from} />}
    </>
  );
};

/** A device's page: how full it is, and Eject. */
const DeviceBar: FC<{ collection: Collection }> = ({ collection }) => {
  const deviceId = collection.id.replace(/^dv~/, "").replace(/~.*$/, "");
  const device = useStore(pluginState)?.devices.find((d) => d.id === deviceId);
  if (!device) return null;
  const space = device.status?.space as { total: number; free: number } | null | undefined;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 14 }}>
      {space && space.total > 0 && (
        <div style={{ width: 280 }}>
          <div className="md-capacity" style={{ height: 8 }}>
            <div style={{ width: `${Math.min(100, ((space.total - space.free) / space.total) * 100)}%` }} />
          </div>
          <div className="md-dim" style={{ fontSize: 12, marginTop: 4 }}>
            {gb(space.free)} free of {gb(space.total)}
          </div>
        </div>
      )}
      <button
        className="md-button md-wide"
        onClick={async () => {
          const r = await api.ejectDevice(deviceId).catch(() => null);
          if (r && !r.ok) notify("MusicDeck", r.error);
          else goHome();
        }}
      >
        <FaEject /> Eject
      </button>
    </div>
  );
};

const Notice: FC<{ text: string }> = ({ text }) => <div className="md-notice">{text}</div>;

const Loading: FC<{ result: api.Result<unknown> | null }> = ({ result }) =>
  result?.ok === false ? <Notice text={result.error} /> : null;

const SectionsPage: FC<{ id: string; title: string; what: string; fetch: () => Promise<api.Result<Section[]>> }> = ({
  id,
  title,
  what,
  fetch,
}) => {
  const result = useResult(`${id}:desktop:${what}`, fetch);
  // the one library's Your music: one source's or every one's, in the order chosen
  const merged = what.startsWith("library:tab:");
  const services = useStore(pluginState)?.services ?? [];
  const [only, setOnly] = useState("");
  const [sort, setSort] = useState<LibrarySort>("title");
  useEffect(() => setOnly(""), [what]);
  const sources = merged && result?.ok ? sourcesIn(result.data, services) : [];
  const shown = result?.ok ? (merged ? filterLibrary(result.data, only, sort, services) : result.data) : [];
  const sessions = useOtherSessions();
  const lead = what === "library:home" && sessions.length ? continueCards(sessions) : null;
  const jump = lead ? shown.find((s) => s.title === "Jump back in" && s.items.length) : undefined;
  return (
    <>
      <div className="md-page-head">
        <h1 className="md-title">{title}</h1>
        {merged && sources.length > 0 && (
          <div className="md-page-tools">
            {sources.length > 1 && (
              <div className="st-periods" role="tablist">
                {[{ id: "", name: "All" }, ...sources].map((x) => (
                  <button key={x.id} role="tab" aria-selected={only === x.id} data-on={only === x.id ? "" : undefined} onClick={() => setOnly(x.id)}>
                    {x.name}
                  </button>
                ))}
              </div>
            )}
            <select className="md-select md-sort" value={sort} onChange={(e) => setSort(e.target.value as LibrarySort)} aria-label="Sort by">
              {LIBRARY_SORTS.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
      <Loading result={result} />
      {/* Home: what your other devices were playing leads Jump back in (a row of its own when there's none) */}
      {lead && !jump && (
        <section className="md-section">
          <h2 className="md-heading">Jump back in</h2>
          <Cards id={id} items={[]} row lead={lead} />
        </section>
      )}
      {shown.map((s, i) => (
        <SectionView key={`${s.title}-${i}`} id={id} section={s} shelves={what === "home" || what === "library:home"} lead={s === jump ? lead : undefined} />
      ))}
    </>
  );
};

/** What the user's other devices were playing: a card each, played here from where it was (the other stops). */
const continueCards = (sessions: ReturnType<typeof useOtherSessions>) =>
  sessions.map((s) => (
    <div key={`peer:${s.peer}`} className="md-card" title={`${s.track.title} · ${s.name}`} onClick={() => continueFrom(s)}>
      <div className="md-card-art" style={{ borderRadius: 3 }}>
        <ArtOrIcon url={art(s.instanceId, s.track.art)} />
        <button className="md-card-play" aria-label={`Play ${s.track.title} here`}>
          <FaPlay />
        </button>
      </div>
      <div className="md-card-title">{s.track.title}</div>
      <div className="md-card-sub">{[s.track.artist, `on ${s.name}`].filter(Boolean).join(" · ")}</div>
    </div>
  ));

/** A section: on Home, its cards a row that scrolls (as Steam's library home has them); elsewhere, a grid
 *  using the room there is. */
/** `lead`: cards ahead of the section's own in its row (Home's Jump back in: your other devices') */
const SectionView: FC<{ id: string; section: Section; shelves?: boolean; lead?: ReactNode }> = ({ id, section, shelves, lead }) => {
  if (section.notice) return <Notice text={section.notice} />;
  const from: Collection = { id: `section:${section.title}`, kind: "list", title: section.title, subtitle: "", art: null };
  return (
    <section className="md-section">
      {section.title && <h2 className="md-heading">{section.title}</h2>}
      {!!section.items.length &&
        (section.display === "tiles" || section.display === "rows" ? (
          <Tiles id={id} items={section.items} />
        ) : (
          <Cards id={id} items={section.items} row={!!shelves && (!section.display || section.display === "shelf")} lead={lead} />
        ))}
      {!!section.tracks?.length && <TrackTable id={id} tracks={section.tracks} from={from} />}
    </section>
  );
};

/** How much a source's results hold, for its tab: "24" */
const foundCount = (page: Page) =>
  page.tracks.length + page.collections.length + (page.extra ?? []).reduce((n, x) => n + x.tracks.length, 0);

/**
 * A search: every source at once, each adding to All as it answers (a field search, artist:nirvana, is Local files'
 * own: the others just find nothing); a tab for each source that found something, its whole results, in a steady order
 * (the best first, ones answering later after them).
 */
const SearchPage: FC<{ id: string; query: string }> = ({ id, query }) => {
  const found = useLibrarySearch(query);
  const { ordered } = useSearchChoice(query, found.pages, found.waiting);
  const [only, setOnly] = useState("");
  useEffect(() => setOnly(""), [query]);
  const one = ordered.find((p) => p.source.id === only);
  const from: Collection = { id: `search:${query}`, kind: "list", title: query, subtitle: "", art: null };
  return (
    <>
      <div className="md-page-head">
        <h1 className="md-title">“{query}”</h1>
        {ordered.length > 1 && (
          <div className="md-page-tools">
            <div className="st-periods" role="tablist">
              {[{ id: "", name: "All", count: 0 }, ...ordered.map((p) => ({ id: p.source.id, name: p.source.name, count: foundCount(p.page) }))].map((x) => (
                <button key={x.id} role="tab" aria-selected={only === x.id} data-on={only === x.id ? "" : undefined} onClick={() => setOnly(x.id)}>
                  {x.name}
                  {x.count > 0 && <span className="md-dim" style={{ marginLeft: 6 }}>{x.count}</span>}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
      {one ? (
        <PageBody key={one.source.id} id={one.source.id} page={one.page} from={from} />
      ) : (
        found.page && <PageBody id={id} page={found.page} from={from} />
      )}
      {!found.page && found.waiting > 0 && <Loading result={null} />}
      {!found.page && !found.waiting && <Notice text={`Nothing found for “${query}”`} />}
    </>
  );
};

const KIND_NAMES: Record<string, string> = { album: "Album", artist: "Artist", playlist: "Playlist", folder: "Folder", device: "Device", list: "" };

const CollectionPage: FC<{ id: string; collection: Collection }> = ({ id, collection }) => {
  const result = useResult(`${id}:desktop:${collection.kind}:${collection.id}`, () =>
    api.openCollection(id, collection.kind, collection.id),
  );
  const page = result?.ok ? result.data : null;
  const download = useDownload(id, collection);
  const cover = art(id, page?.art ?? collection.art);
  const tracks = page?.tracks ?? [];
  const length = tracks.reduce((n, t) => n + (t.duration || 0), 0);
  return (
    <>
      <header className="md-hero">
        {cover && <div className="md-hero-backdrop" style={{ backgroundImage: `url("${cover}")` }} />}
        <div className="md-hero-body">
          <div className="md-hero-cover" style={{ overflow: "hidden", borderRadius: collection.kind === "artist" ? "50%" : 0 }}>
            <ArtOrIcon url={cover} kind={collection.kind} />
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="md-kind">{KIND_NAMES[collection.kind] ?? ""}</div>
            <div className="md-hero-title">{page?.title ?? collection.title}</div>
            <div className="md-dim">
              {(() => {
                const said = page?.subtitle ?? collection.subtitle;
                // the source's own words, unless they don't say how long it is
                const count = tracks.length && !/\d+\s+(tracks?|songs?|episodes?)/i.test(said) ? `${tracks.length} songs, ${clock(length)}` : "";
                return [said, count].filter(Boolean).join(" · ");
              })()}
            </div>
            <div className="md-actions">
              <button className="md-play" disabled={!tracks.length} onClick={() => playFrom(id, collection, tracks, 0)}>
                <FaPlay /> Play
              </button>
              <button className="md-button md-wide" disabled={tracks.length < 2} onClick={() => playFrom(id, collection, tracks, 0, true)}>
                <FaRandom /> Shuffle
              </button>
              {download && !!tracks.length && (
                <button className="md-button md-wide" data-on={download.on ? "" : undefined} onClick={download.act}>
                  <FaDownload /> {download.label}
                </button>
              )}
              {page?.action && <PageAction id={id} collection={collection} action={page.action} />}
              {collection.kind === "album" && canLookUp(id) && (
                <button className="md-button md-wide" onClick={() => lookupOpener.open(id, collection, tracks.length)}>
                  Look up details…
                </button>
              )}
              <button className="md-button md-wide" aria-label="More" onClick={(e) => showCollectionMenu(id, collection, e.currentTarget)}>
                <FaEllipsisH />
              </button>
            </div>
            {collection.kind === "device" && <DeviceBar collection={collection} />}
          </div>
        </div>
      </header>
      <Loading result={result} />
      {page?.notice && <Notice text={page.notice} />}
      {page && <PageBody id={id} page={page} from={collection} skipHeader />}
    </>
  );
};

const PageAction: FC<{ id: string; collection: Collection; action: NonNullable<Page["action"]> }> = ({ id, collection, action }) => (
  <button
    className="md-button md-wide"
    onClick={() => {
      if (action.url?.startsWith("steam://")) (window as any).SteamClient?.URL?.ExecuteSteamURL?.(action.url);
      else if (action.url) window.open(action.url);
      else if (action.call) api.collectionAction(id, collection.kind, collection.id, action.call).catch(() => {});
    }}
  >
    {action.label}
  </button>
);

const PageBody: FC<{ id: string; page: Page; from: Collection; skipHeader?: boolean }> = ({ id, page, from }) => {
  const songs = !!page.tracks.length && (
    <section className="md-section" key="songs">
      <TrackTable id={id} tracks={page.tracks} from={from} />
    </section>
  );
  const more = !!page.collections.length && (
    <section className="md-section" key="more">
      <Cards id={id} items={page.collections} />
    </section>
  );
  return (
    <>
      {page.tracks_first ? [songs, more] : [more, songs]}
      {page.extra?.map((x) => (
        <section className="md-section" key={x.title}>
          <h2 className="md-heading">{x.title}</h2>
          <TrackTable id={id} tracks={x.tracks} from={from} />
        </section>
      ))}
    </>
  );
};

// --- cards ------------------------------------------------------------------------------------------

/** Albums, artists, playlists as covers: a row that scrolls (Home's shelves: no scrollbar, arrows at its
 *  ends on hover, as Steam's and Spotify's have) or a wrapping grid. */
const Cards: FC<{ id: string; items: Collection[]; row?: boolean; lead?: ReactNode }> = ({ id, items, row, lead }) => {
  const shelf = useRef<HTMLDivElement>(null);
  // more to see either way: its arrows show
  const [more, setMore] = useState<[boolean, boolean]>([false, false]);
  const measure = () => {
    const el = shelf.current;
    if (el) setMore([el.scrollLeft > 4, el.scrollLeft + el.clientWidth < el.scrollWidth - 4]);
  };
  useEffect(() => {
    const el = shelf.current;
    if (!el || !row) return;
    measure();
    const win = el.ownerDocument.defaultView;
    win?.addEventListener("resize", measure);
    return () => win?.removeEventListener("resize", measure);
  }, [row, items.length]);
  const page = (way: 1 | -1) => {
    const el = shelf.current;
    el?.scrollBy({ left: way * Math.max(200, el.clientWidth - 200), behavior: "smooth" });
  };
  // the wheel over a shelf moves it along, as Steam's library shelves do, until its end: then the page scrolls
  useEffect(() => {
    const el = shelf.current;
    if (!el || !row) return;
    const wheel = (e: WheelEvent) => {
      const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
      const room = delta > 0 ? el.scrollWidth - el.clientWidth - el.scrollLeft : el.scrollLeft;
      if (e.shiftKey || room <= 0 || el.scrollWidth <= el.clientWidth) return;
      e.preventDefault();
      el.scrollLeft += delta;
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, [row]);
  const cards = items.map((c) => <Card key={`${c.source ?? ""}:${c.kind}:${c.id}`} id={c.source ?? id} item={c} />);
  if (!row)
    return (
      <div className="md-grid">
        {lead}
        {cards}
      </div>
    );
  return (
    <div className="md-shelf-box">
      <div ref={shelf} className="md-shelf" onScroll={measure}>
        {lead}
        {cards}
      </div>
      {more[0] && (
        <button className="md-shelf-arrow" data-way="back" aria-label="Back" onClick={() => page(-1)}>
          <FaChevronLeft />
        </button>
      )}
      {more[1] && (
        <button className="md-shelf-arrow" data-way="on" aria-label="More" onClick={() => page(1)}>
          <FaChevronRight />
        </button>
      )}
    </div>
  );
};

const KIND_ICONS: Record<string, ReactNode> = { artist: <FaUser />, playlist: <FaListUl />, folder: <FaFolder />, device: <FaHdd /> };

/** A cover, or (none, or one that won't load) the kind's icon on a soft gradient. */
export const ArtOrIcon: FC<{ url: string; kind?: string; size?: number | string }> = ({ url, kind = "album", size = "100%" }) => {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
  if (url && !failed) return <img src={url} draggable={false} loading="lazy" onError={() => setFailed(true)} style={{ width: size, height: size, objectFit: "cover", display: "block" }} />;
  return <div className="md-noart" style={{ width: size, height: size }}>{KIND_ICONS[kind] ?? <FaCompactDisc />}</div>;
};

async function playCollection(id: string, c: Collection) {
  const r = await api.openCollection(id, c.kind, c.id);
  if (r.ok && r.data.tracks.length) playFrom(id, c, r.data.tracks, 0);
}

const Card: FC<{ id: string; item: Collection }> = ({ id, item }) => {
  const url = art(id, item.art);
  // in the one library, where it's from
  const from = useStore(pluginState)?.services.find((s) => s.id === item.source)?.name;
  const round = item.kind === "artist";
  return (
    <div
      className="md-card"
      onClick={() => go({ type: "collection", collection: item, source: id })}
      title={item.title}
      draggable={item.kind !== "device"}
      onDragStart={(e) => {
        e.dataTransfer.setData(DRAG_TYPE, JSON.stringify({ instanceId: id, collection: item } satisfies Dragged));
        e.dataTransfer.effectAllowed = "copy";
      }}
      onContextMenu={(e) => {
        e.preventDefault();
        showCollectionMenu(id, item, clickPoint(e));
      }}
    >
      <div className="md-card-art" style={{ borderRadius: round ? "50%" : 3 }}>
        <ArtOrIcon url={url} kind={item.kind} />
        {item.kind !== "artist" && item.kind !== "device" && (
          <button
            className="md-card-play"
            aria-label={`Play ${item.title}`}
            onClick={(e) => {
              e.stopPropagation();
              playCollection(id, item).catch(() => {});
            }}
          >
            <FaPlay />
          </button>
        )}
      </div>
      <div className="md-card-title">{item.title}</div>
      <div className="md-card-sub">{[item.subtitle, from].filter(Boolean).join(" · ")}</div>
    </div>
  );
};

/** Genres, moods, charts: names in tiles. */
const Tiles: FC<{ id: string; items: Collection[] }> = ({ id, items }) => (
  <div className="md-tiles">
    {items.map((c) => (
      <div key={`${c.kind}:${c.id}`} className="md-tile" onClick={() => go({ type: "collection", collection: c, source: c.source ?? id })}>
        {c.title}
      </div>
    ))}
  </div>
);

// --- the track table --------------------------------------------------------------------------------

type Column = "number" | "title" | "artist" | "album" | "duration" | "rating";
const ROW = 32;
/** longer lists than this scroll in a box of their own, drawing only the rows in sight */
const VIRTUAL_FROM = 200;

export const TrackTable: FC<{ id: string; tracks: Track[]; from: Collection; onRow?: (t: Track, i: number) => void }> = ({
  id,
  tracks,
  from,
}) => {
  const [sort, setSort] = useState<{ by: Column; down: boolean } | null>(null);
  // rows chosen, by their place as shown; the anchor is where a Shift-click range starts
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [anchor, setAnchor] = useState(0);
  useEffect(() => setSelected(new Set()), [tracks, sort]);
  const playing = useStore(player.state);
  const current = playing.index >= 0 ? playing.queue[playing.index]?.track.id : null;
  const showAlbum = from.kind !== "album";
  // the one library's lists mix sources: each row is its own source's
  const of = (t: Track) => t.source ?? id;
  const rates = tracks.some((t) => canRate(of(t)));
  // a list of several sources' songs says where each is from
  const services = useStore(pluginState)?.services ?? [];
  const mixed = new Set(tracks.map(of)).size > 1;
  const sourceName = (t: Track) => services.find((s) => s.id === of(t))?.name ?? "";
  const order = useMemo(() => {
    const idx = tracks.map((_, i) => i);
    if (!sort) return idx;
    const key = (t: Track, i: number): string | number =>
      sort.by === "number" ? i : sort.by === "duration" ? t.duration || 0 : sort.by === "rating" ? t.rating || 0
        : String(t[sort.by] ?? "").toLowerCase();
    idx.sort((a, b) => {
      const x = key(tracks[a], a);
      const y = key(tracks[b], b);
      return (x < y ? -1 : x > y ? 1 : 0) * (sort.down ? -1 : 1);
    });
    return idx;
  }, [tracks, sort]);
  const box = useRef<HTMLDivElement>(null);
  const [top, setTop] = useState(0);
  const virtual = tracks.length > VIRTUAL_FROM;
  const height = virtual ? Math.min(tracks.length * ROW, 640) : tracks.length * ROW;
  const first = virtual ? Math.max(0, Math.floor(top / ROW) - 10) : 0;
  const last = virtual ? Math.min(order.length, first + Math.ceil(height / ROW) + 20) : order.length;
  const head = (c: Column, label: string, style?: CSSProperties) => (
    <div
      className="md-th"
      style={style}
      onClick={() => setSort(sort?.by === c ? (sort.down ? null : { by: c, down: true }) : { by: c, down: false })}
    >
      {label}
      {sort?.by === c ? (sort.down ? " ▾" : " ▴") : ""}
    </div>
  );
  // the sorted order is what plays: double-clicking a row plays from it on, in the order shown
  const play = (at: number) => playFrom(from.source ?? id, from, order.map((i) => tracks[i]), at);
  const choose = (at: number, e: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }) => {
    if (e.shiftKey) {
      const range = new Set<number>();
      for (let n = Math.min(anchor, at); n <= Math.max(anchor, at); n++) range.add(n);
      setSelected(range);
      return;
    }
    if (e.ctrlKey || e.metaKey) {
      const next = new Set(selected);
      next.has(at) ? next.delete(at) : next.add(at);
      setSelected(next);
    } else setSelected(new Set([at]));
    setAnchor(at);
  };
  /** the songs a menu or a drag is about: the selection, when the row is in it; else just that row */
  const about = (at: number): Track[] =>
    selected.has(at) && selected.size > 1 ? [...selected].sort((a, b) => a - b).map((n) => tracks[order[n]]) : [tracks[order[at]]];
  const playlistId = from.kind === "playlist" && api.isLocalPlaylist(from.id) ? from.id : undefined;
  return (
    <div
      className="md-table"
      tabIndex={0}
      onKeyDown={(e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
          e.preventDefault();
          setSelected(new Set(order.map((_, n) => n)));
        } else if (e.key === "Enter" && selected.size) play(Math.min(...selected));
      }}
    >
      <div className="md-tr md-thead">
        {head("number", "#", { width: 44, textAlign: "right" })}
        {head("title", "Title", { flex: 3 })}
        {head("artist", "Artist", { flex: 2 })}
        {showAlbum && head("album", "Album", { flex: 2 })}
        {rates && head("rating", "Rating", { width: 84 })}
        {mixed && <div className="md-th" style={{ width: 130 }}>Source</div>}
        {head("duration", "Time", { width: 64, textAlign: "right" })}
      </div>
      <div
        ref={box}
        style={{ height, overflowY: virtual ? "auto" : "visible", position: "relative" }}
        onScroll={(e) => virtual && setTop(e.currentTarget.scrollTop)}
      >
        <div style={{ height: order.length * ROW, position: "relative" }}>
          {order.slice(first, last).map((i, n) => {
            const t = tracks[i];
            const at = first + n;
            return (
              <div
                key={`${t.id}-${i}`}
                className="md-tr md-row"
                data-selected={selected.has(at) ? "" : undefined}
                data-playing={t.id === current ? "" : undefined}
                style={{ position: "absolute", top: at * ROW, left: 0, right: 0, height: ROW }}
                onClick={(e) => choose(at, e)}
                onDoubleClick={() => play(at)}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData(DRAG_TYPE, JSON.stringify({ instanceId: of(t), tracks: about(at).filter((x) => of(x) === of(t)) } satisfies Dragged));
                  e.dataTransfer.effectAllowed = "copy";
                }}
                // the mouse event, not the row: Steam opens a menu given one where the click was
                onContextMenu={(e) => {
                  e.preventDefault();
                  if (!selected.has(at)) {
                    setSelected(new Set([at]));
                    setAnchor(at);
                  }
                  // a choice across sources: the menu is for this row's source's songs among it
                  const songs = about(at).filter((x) => of(x) === of(t));
                  if (songs.length > 1) showSelectionMenu(of(t), songs, from, clickPoint(e));
                  else showTrackMenu(of(t), songs[0], clickPoint(e), { playlistId });
                }}
              >
                <div className="md-td md-dim" style={{ width: 44, textAlign: "right" }}>
                  {t.id === current ? "▶" : i + 1}
                </div>
                <div className="md-td" style={{ flex: 3 }}>
                  {t.title}
                </div>
                <div className="md-td md-dim" style={{ flex: 2 }}>
                  {t.artist}
                </div>
                {showAlbum && (
                  <div className="md-td md-dim" style={{ flex: 2 }}>
                    {t.album}
                  </div>
                )}
                {rates && (
                  <div className="md-td" style={{ width: 84 }}>
                    {canRate(of(t)) && <Stars instanceId={of(t)} track={t} />}
                  </div>
                )}
                {mixed && (
                  <div className="md-td md-dim" style={{ width: 130 }}>
                    {sourceName(t)}
                  </div>
                )}
                <div className="md-td md-dim" style={{ width: 64, textAlign: "right" }}>
                  {t.live ? "LIVE" : clock(t.duration)}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export const LIBRARY_CSS = `
.md-sidebar { width: 260px; min-width: 260px; padding: 12px 8px; background: rgba(0,0,0,0.22); overflow-y: auto; box-sizing: border-box; }
.md-search { display: flex; align-items: center; gap: 8px; margin: 0 8px 10px; padding: 0 10px; height: 32px; border-radius: 3px;
  background: rgba(255,255,255,0.08); color: #dcdedf; }
.md-search input { flex: 1; min-width: 0; background: none; border: none; outline: none; color: #fff; font: inherit; font-size: 13px; }
.md-select { display: block; width: calc(100% - 16px); margin: 0 8px 8px; height: 30px; border-radius: 3px; border: none;
  background: rgba(255,255,255,0.08); color: #dcdedf; font: inherit; font-size: 13px; padding: 0 8px; }
.md-select option { background: #23262e; }
.md-nav-item { display: flex; justify-content: space-between; align-items: center; }
.md-nav-item:hover { background: rgba(255,255,255,0.05); color: #fff; }
.md-nav-count { font-size: 11px; color: #8b929a; }
.md-nav-item[data-over] { background: rgba(26,159,255,0.3); color: #fff; }
.md-nav-add { color: #8b929a; font-size: 13px; }
.md-nav-sub { padding-left: 30px; font-size: 13px; }
.md-page-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.md-page-tools { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 24px 0 12px; }
.md-sort { width: auto !important; margin: 0 !important; height: 36px !important; }
.md-sidebar { scrollbar-width: none; }
.md-sidebar::-webkit-scrollbar { display: none; }
.md-nav-twisty { border: none; background: none; color: #8b929a; width: 22px; height: 22px; margin: -4px -6px -4px 0; padding: 0;
  display: flex; align-items: center; justify-content: center; font-size: 10px; border-radius: 3px; cursor: pointer; }
.md-nav-twisty:hover { color: #fff; background: rgba(255,255,255,0.08); }
.md-nav-twisty svg { transition: transform 0.15s; }
.md-nav-twisty[data-open] svg { transform: rotate(90deg); }
.md-capacity { height: 3px; margin-top: 4px; border-radius: 2px; background: rgba(255,255,255,0.12); overflow: hidden; }
.md-capacity > div { height: 100%; background: #1a9fff; }
.md-smart-cover { display: flex; align-items: center; justify-content: center; font-size: 72px; color: #8b929a;
  background: linear-gradient(135deg, rgba(26,159,255,0.25), rgba(117,176,34,0.2)) !important; }
.md-table:focus { outline: none; }
.md-drop { position: absolute; inset: 0; z-index: 5; display: flex; align-items: center; justify-content: center;
  background: rgba(23,29,37,0.85); border: 2px dashed #1a9fff; color: #fff; font-size: 20px; pointer-events: none; }
.md-page { flex: 1; overflow-y: auto; padding: 0 32px 32px; min-width: 0; }
.md-title { font-size: 26px; font-weight: 400; color: #fff; margin: 24px 0 12px; }
.md-heading { font-size: 13px; font-weight: 400; letter-spacing: 0.06em; text-transform: uppercase; color: #8b929a; margin: 20px 0 10px; }
.md-notice { color: #8b929a; padding: 24px 0; font-size: 14px; }
.md-dim { color: #8b929a; }
.md-section { margin-bottom: 8px; }
.md-shelf { display: flex; gap: 16px; overflow-x: auto; padding-bottom: 8px; }
.md-shelf .md-card { width: 176px; min-width: 176px; }
.md-shelf { scroll-behavior: auto; scrollbar-width: none; }
.md-shelf::-webkit-scrollbar { display: none; }
.md-shelf-box { position: relative; }
.md-shelf-arrow { position: absolute; top: 72px; width: 40px; height: 40px; border-radius: 50%; border: none; display: flex;
  align-items: center; justify-content: center; background: rgba(16,20,26,0.9); color: #fff; font-size: 15px; cursor: pointer;
  box-shadow: 0 2px 10px rgba(0,0,0,0.6); opacity: 0; transition: opacity 0.15s; }
.md-shelf-box:hover .md-shelf-arrow { opacity: 1; }
.md-shelf-arrow:hover { background: #3d4450; }
.md-shelf-arrow[data-way="back"] { left: -12px; }
.md-shelf-arrow[data-way="on"] { right: -12px; }
.md-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 22px 18px; }
.md-card-art .md-noart svg, .md-hero-cover .md-noart svg { width: 32%; height: 32%; }
.md-card { cursor: pointer; min-width: 0; }
.md-card-art { position: relative; aspect-ratio: 1; background: rgba(255,255,255,0.06); overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.35); }
.md-card-art img { width: 100%; height: 100%; object-fit: cover; display: block; transition: transform 0.2s; }
.md-card:hover .md-card-art img { transform: scale(1.04); }
.md-card-play { position: absolute; right: 8px; bottom: 8px; width: 38px; height: 38px; border-radius: 50%; border: none;
  background: linear-gradient(to right, #75b022 5%, #588a1b 95%); color: #fff; opacity: 0; transition: opacity 0.15s; cursor: pointer; }
.md-card:hover .md-card-play { opacity: 1; }
.md-card-title { margin-top: 8px; font-size: 13px; color: #dcdedf; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.md-card-sub { font-size: 12px; color: #8b929a; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.md-tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; }
.md-tile { padding: 18px 14px; border-radius: 3px; background: rgba(255,255,255,0.07); color: #fff; cursor: pointer; font-size: 14px; }
.md-tile:hover { background: rgba(255,255,255,0.13); }
.md-hero { position: relative; margin: 0 -32px 8px; padding: 40px 32px 24px; overflow: hidden; }
.md-hero-backdrop { position: absolute; inset: -40px; background-size: cover; background-position: center; filter: blur(40px) brightness(0.45); }
.md-hero-body { position: relative; display: flex; gap: 28px; align-items: flex-end; }
.md-hero-cover { width: 200px; height: 200px; min-width: 200px; object-fit: cover; box-shadow: 0 10px 30px rgba(0,0,0,0.5); background: rgba(255,255,255,0.06); }
.md-kind { font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: #b8bcbf; }
.md-hero-title { font-size: 40px; color: #fff; margin: 4px 0 6px; line-height: 1.1; overflow: hidden; text-overflow: ellipsis; }
.md-actions { display: flex; gap: 10px; margin-top: 16px; align-items: center; }
.md-play { display: flex; align-items: center; gap: 10px; height: 40px; padding: 0 28px; border: none; border-radius: 2px; cursor: pointer;
  background: linear-gradient(to right, #75b022 5%, #588a1b 95%); color: #fff; font: inherit; font-size: 16px; }
.md-play:hover { filter: brightness(1.12); }
.md-play:disabled, .md-button:disabled { opacity: 0.4; cursor: default; }
.md-wide { width: auto !important; height: 40px !important; padding: 0 16px; gap: 8px; font: inherit; font-size: 14px !important; }
.md-table { font-size: 13px; user-select: none; }
.md-tr { display: flex; align-items: center; gap: 12px; padding: 0 10px; box-sizing: border-box; }
.md-thead { height: 30px; border-bottom: 1px solid rgba(255,255,255,0.08); color: #8b929a; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; }
.md-th { cursor: pointer; white-space: nowrap; }
.md-th:hover { color: #dcdedf; }
.md-td { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.md-row { border-radius: 2px; color: #dcdedf; cursor: default; }
.md-row:hover { background: rgba(255,255,255,0.05); }
.md-row[data-selected] { background: rgba(26,159,255,0.22); }
.md-row[data-playing] { color: #1a9fff; }
.md-row[data-playing] .md-td { color: #1a9fff; }
`;

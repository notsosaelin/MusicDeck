/**
 * The Music section's library: a sidebar (search, the source, its tabs, as the media center has them) and
 * pages (Home, a tab, a search, an album, artist or playlist), from the same calls the media center makes.
 * Laid out as Steam's library is: a list on the left, the page on the right, an album's page headed by its
 * art as a game's is by its hero, with a green Play.
 */
import { CSSProperties, FC, Fragment, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { FaCompactDisc, FaEject, FaEllipsisH, FaFolder, FaHdd, FaListUl, FaPlay, FaRandom, FaSearch, FaUser } from "react-icons/fa";
import * as api from "../api";
import { Collection, Page, Section, SmartPlaylist, Track } from "../api";
import { showCollectionMenu, showTrackMenu } from "../components/TrackMenu";
import { lookupOpener } from "../components/TagEditor";
import { NameModal } from "../components/NameModal";
import { dataVersion, invalidateBrowserCache, playFrom } from "../nav";
import { notify } from "../notify";
import { player } from "../player";
import { switchSource } from "../sources";
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

const GROUPS: [string, string][] = [
  ["yours", "Your music"],
  ["listening", "Listening"],
  ["explore", "Explore"],
  ["genres", "Genres"],
  ["files", "Files"],
];

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

export const Sidebar: FC = () => {
  const state = useStore(pluginState);
  const source = useLiveSource();
  const place = usePlace();
  const [query, setQuery] = useState("");
  const at = (p: Place) => JSON.stringify(p) === JSON.stringify(place);
  // a source's own "Home" tab is the sidebar's Home
  const tabs = (source?.tabs ?? []).filter((t) => t.id !== "home" && t.title.toLowerCase() !== "home");
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
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" spellCheck={false} />
      </form>
      <select
        className="md-select"
        value={source?.id ?? ""}
        onChange={async (e) => {
          await switchSource(e.target.value);
          goHome();
        }}
      >
        {(state?.services ?? []).map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </select>
      <div className="md-nav-item" data-current={at({ type: "home" }) ? "" : undefined} onClick={() => go({ type: "home" })}>
        Home
      </div>
      {GROUPS.map(([group, heading]) => {
        const mine = tabs.filter((t) => (t.group ?? "yours") === group);
        if (!mine.length) return null;
        return (
          <Fragment key={group}>
            <div className="md-nav-heading">{heading}</div>
            {mine.map((t) => {
              const p: Place = { type: "tab", id: t.id, title: t.title };
              return (
                <div key={t.id} className="md-nav-item" data-current={at(p) ? "" : undefined} onClick={() => go(p)}>
                  <span>{t.title}</span>
                  {t.subtitle && <span className="md-nav-count">{t.subtitle.replace(/\D+$/, "")}</span>}
                </div>
              );
            })}
          </Fragment>
        );
      })}
      {source && <Playlists id={source.id} local={source.type === "local"} at={at} />}
      {source?.type === "local" && <Devices />}
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

/** MusicDeck's playlists of the source, and (Local files) its smart playlists. */
const Playlists: FC<{ id: string; local: boolean; at: (p: Place) => boolean }> = ({ id, local, at }) => {
  const version = useStore(dataVersion).v;
  const [lists, setLists] = useState<Collection[]>([]);
  const smart = useStore(smartLists).filter((p) => p.source === id);
  useEffect(() => {
    api.localPlaylists(id).then(setLists).catch(() => setLists([]));
  }, [id, version]);
  useEffect(() => {
    if (local) loadSmart();
  }, [local]);
  const create = () =>
    desktopDialog(
      <NameModal
        title="New playlist"
        action="Create"
        onSubmit={async (name) => {
          const made = await api.createPlaylist(id, name, []);
          invalidateBrowserCache();
          go({ type: "collection", collection: made });
        }}
      />,
      { ...SIZES.name, title: "New playlist" },
    );
  return (
    <>
      <div className="md-nav-heading">Playlists</div>
      {lists.map((l) => {
        const p: Place = { type: "collection", collection: l };
        return (
          <DropItem
            key={l.id}
            current={at(p)}
            onClick={() => go(p)}
            onMenu={(e) => showCollectionMenu(id, l, clickPoint(e))}
            onDrop={(d) => dropOnPlaylist(d, l.id, l.title)}
          >
            <span>{l.title}</span>
          </DropItem>
        );
      })}
      {local &&
        smart.map((s) => {
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
          onClick={() => desktopDialog(<SmartEditor source={id} onSaved={(s) => go({ type: "smart", playlist: s })} />, SIZES.smart)}
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
const Devices: FC = () => {
  const devices = useStore(pluginState)?.devices ?? [];
  if (!devices.length) return null;
  return (
    <>
      <div className="md-nav-heading">Devices</div>
      {devices.map((d) => {
        const space = d.status?.space as { total: number; free: number } | null | undefined;
        const collection: Collection = { id: `dv~${d.id}~`, kind: "device", title: d.name, subtitle: "", art: null };
        const takes = d.kind !== "cd" && d.kind !== "disc" && d.kind !== "ios" && d.status?.writable !== false;
        const instanceId = pluginState.get()?.active_service ?? "";
        return (
          <DropItem
            key={d.id}
            onClick={() => go({ type: "collection", collection })}
            onMenu={(e) => showCollectionMenu(instanceId, collection, clickPoint(e))}
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
  const source = useLiveSource();
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => scroller.current?.scrollTo(0, 0), [place]);
  if (!source) return <Notice text="Add a music source in Decky → MusicDeck to see your music here." />;
  const id = source.id;
  return (
    <div ref={scroller} className="md-page">
      {place.type === "home" && <SectionsPage id={id} title="Home" what="home" fetch={() => api.getHome(id)} />}
      {place.type === "tab" && (
        <SectionsPage id={id} title={place.title} what={`tab:${place.id}`} fetch={() => api.getCenterTab(id, place.id)} />
      )}
      {place.type === "search" && <SearchPage id={id} query={place.query} />}
      {place.type === "collection" && <CollectionPage id={id} collection={place.collection} />}
      {place.type === "smart" && <SmartPage id={id} playlist={place.playlist} />}
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
  result === null ? <Notice text="Loading…" /> : !result.ok ? <Notice text={result.error} /> : null;

const SectionsPage: FC<{ id: string; title: string; what: string; fetch: () => Promise<api.Result<Section[]>> }> = ({
  id,
  title,
  what,
  fetch,
}) => {
  const result = useResult(`${id}:desktop:${what}`, fetch);
  return (
    <>
      <h1 className="md-title">{title}</h1>
      <Loading result={result} />
      {result?.ok && result.data.map((s, i) => <SectionView key={`${s.title}-${i}`} id={id} section={s} shelves={what === "home"} />)}
    </>
  );
};

/** A section: on Home, its cards a row that scrolls (as Steam's library home has them); elsewhere, a grid
 *  using the room there is. */
const SectionView: FC<{ id: string; section: Section; shelves?: boolean }> = ({ id, section, shelves }) => {
  if (section.notice) return <Notice text={section.notice} />;
  const from: Collection = { id: `section:${section.title}`, kind: "list", title: section.title, subtitle: "", art: null };
  return (
    <section className="md-section">
      {section.title && <h2 className="md-heading">{section.title}</h2>}
      {!!section.items.length &&
        (section.display === "tiles" || section.display === "rows" ? (
          <Tiles id={id} items={section.items} />
        ) : (
          <Cards id={id} items={section.items} row={!!shelves && (!section.display || section.display === "shelf")} />
        ))}
      {!!section.tracks?.length && <TrackTable id={id} tracks={section.tracks} from={from} />}
    </section>
  );
};

const SearchPage: FC<{ id: string; query: string }> = ({ id, query }) => {
  const result = useResult(`${id}:desktop:search:${query}`, () => api.search(id, query));
  return (
    <>
      <h1 className="md-title">“{query}”</h1>
      <Loading result={result} />
      {result?.ok && <PageBody id={id} page={result.data} from={{ id: `search:${query}`, kind: "list", title: query, subtitle: "", art: null }} />}
    </>
  );
};

const KIND_NAMES: Record<string, string> = { album: "Album", artist: "Artist", playlist: "Playlist", folder: "Folder", device: "Device", list: "" };

const CollectionPage: FC<{ id: string; collection: Collection }> = ({ id, collection }) => {
  const result = useResult(`${id}:desktop:${collection.kind}:${collection.id}`, () =>
    api.openCollection(id, collection.kind, collection.id),
  );
  const page = result?.ok ? result.data : null;
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

/** Albums, artists, playlists as covers: a row that scrolls (Home's shelves) or a wrapping grid. */
const Cards: FC<{ id: string; items: Collection[]; row?: boolean }> = ({ id, items, row }) => {
  const shelf = useRef<HTMLDivElement>(null);
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
  return (
    <div ref={shelf} className={row ? "md-shelf" : "md-grid"}>
      {items.map((c) => (
        <Card key={`${c.kind}:${c.id}`} id={id} item={c} />
      ))}
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
  const round = item.kind === "artist";
  return (
    <div
      className="md-card"
      onClick={() => go({ type: "collection", collection: item })}
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
      <div className="md-card-sub">{item.subtitle}</div>
    </div>
  );
};

/** Genres, moods, charts: names in tiles. */
const Tiles: FC<{ id: string; items: Collection[] }> = ({ items }) => (
  <div className="md-tiles">
    {items.map((c) => (
      <div key={`${c.kind}:${c.id}`} className="md-tile" onClick={() => go({ type: "collection", collection: c })}>
        {c.title}
      </div>
    ))}
  </div>
);

// --- the track table --------------------------------------------------------------------------------

type Column = "number" | "title" | "artist" | "album" | "duration";
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
  const order = useMemo(() => {
    const idx = tracks.map((_, i) => i);
    if (!sort) return idx;
    const key = (t: Track, i: number): string | number =>
      sort.by === "number" ? i : sort.by === "duration" ? t.duration || 0 : String(t[sort.by] ?? "").toLowerCase();
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
  const play = (at: number) => playFrom(id, from, order.map((i) => tracks[i]), at);
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
                  e.dataTransfer.setData(DRAG_TYPE, JSON.stringify({ instanceId: id, tracks: about(at) } satisfies Dragged));
                  e.dataTransfer.effectAllowed = "copy";
                }}
                // the mouse event, not the row: Steam opens a menu given one where the click was
                onContextMenu={(e) => {
                  e.preventDefault();
                  if (!selected.has(at)) {
                    setSelected(new Set([at]));
                    setAnchor(at);
                  }
                  const songs = about(at);
                  if (songs.length > 1) showSelectionMenu(id, songs, from, clickPoint(e));
                  else showTrackMenu(id, songs[0], clickPoint(e), { playlistId });
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
.md-shelf { scroll-behavior: auto; scrollbar-width: thin; }
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

import { useEffect, useState } from "react";
import { Collection, Page, Section, ServiceInfo, Track, search } from "./api";
import { pluginState, useStore } from "./store";

/**
 * The one library (py_modules/musicdeck/library.py): every source's music together. Search is put together
 * here, source by source, so what the fast ones find shows at once and a slow one (YouTube) adds to it when it
 * answers, rather than everything waiting for the slowest.
 */

/** of each source, in the one library's search results */
const SEARCH_TRACKS = 5;
const SEARCH_COLLECTIONS = 12;
/** a source that takes longer than this is left out of the results */
const SEARCH_SECONDS = 10;

/** The sources in the one library's order: Local files first (the user's own), then as they're listed. */
export const librarySources = (services: ServiceInfo[]) =>
  [...services].sort((a, b) => Number(a.type !== "local") - Number(b.type !== "local"));

const tagTracks = (tracks: Track[], source: string) => tracks.map((t) => ({ ...t, source: t.source ?? source }));
const tagCollections = (items: Collection[], source: string) => items.map((c) => ({ ...c, source: c.source ?? source }));

/** Each source's results as one page: its best songs, then its albums, artists and playlists, source by source; a
 *  source's other lists (videos) under its name. */
export function mergeSearch(query: string, pages: { source: ServiceInfo; page: Page }[]): Page {
  const merged: Page = { title: query, subtitle: "", art: null, collections: [], tracks: [], extra: [], tracks_first: true };
  for (const { source, page } of pages) {
    merged.tracks.push(...tagTracks(page.tracks.slice(0, SEARCH_TRACKS), source.id));
    merged.collections.push(...tagCollections(page.collections.slice(0, SEARCH_COLLECTIONS), source.id));
    for (const x of page.extra ?? [])
      if (x.tracks.length) merged.extra!.push({ title: `${x.title || "More"} · ${source.name}`, tracks: tagTracks(x.tracks, source.id) });
  }
  return merged;
}

/** One source's part of a mixed list: its tracks, in the order they came. */
export function bySource<T extends { source?: string }>(list: T[], fallback: string): { source: string; list: T[] }[] {
  const parts: { source: string; list: T[] }[] = [];
  for (const x of list) {
    const source = x.source ?? fallback;
    const part = parts.find((p) => p.source === source);
    if (part) part.list.push(x);
    else parts.push({ source, list: [x] });
  }
  return parts;
}

/** A merged page back into each source's own (for views that show one source at a time), in `order` (the
 *  sources' ids) where given. */
export function splitPage(page: Page, fallback: string, order: string[] = []): { source: string; page: Page }[] {
  const sources: string[] = [];
  const add = (s: string) => !sources.includes(s) && sources.push(s);
  page.tracks.forEach((t) => add(t.source ?? fallback));
  page.collections.forEach((c) => add(c.source ?? fallback));
  (page.extra ?? []).forEach((x) => x.tracks.forEach((t) => add(t.source ?? fallback)));
  const rank = (id: string) => (order.includes(id) ? order.indexOf(id) : order.length);
  sources.sort((a, b) => rank(a) - rank(b));
  return sources.map((source) => ({
    source,
    page: {
      ...page,
      tracks: page.tracks.filter((t) => (t.source ?? fallback) === source),
      collections: page.collections.filter((c) => (c.source ?? fallback) === source),
      extra: (page.extra ?? []).filter((x) => x.tracks.some((t) => (t.source ?? fallback) === source)),
    },
  }));
}

/** Every source searched at once: the page so far (null until one answers), and how many are still looking.
 *  `rememberIn`: the source whose recent searches keep it (the media center's live one). */
export function useLibrarySearch(query: string, rememberIn?: string): { page: Page | null; waiting: number } {
  const services = useStore(pluginState)?.services ?? [];
  const ids = services.map((s) => s.id).join(",");
  const [found, setFound] = useState<{ query: string; pages: Record<string, Page | null> }>({ query, pages: {} });
  useEffect(() => {
    let live = true;
    setFound({ query, pages: {} });
    for (const s of services) {
      const timeout = new Promise<null>((r) => setTimeout(() => r(null), SEARCH_SECONDS * 1000));
      Promise.race([search(s.id, query, s.id === rememberIn).then((r) => (r.ok ? r.data : null)).catch(() => null), timeout]).then(
        (page) => live && setFound((f) => (f.query === query ? { query, pages: { ...f.pages, [s.id]: page } } : f)),
      );
    }
    return () => {
      live = false;
    };
  }, [query, ids]);
  const pages = found.query === query ? found.pages : {};
  const answered = librarySources(services)
    .filter((s) => pages[s.id])
    .map((s) => ({ source: s, page: pages[s.id]! }));
  return {
    page: answered.length ? mergeSearch(query, answered) : null,
    waiting: services.filter((s) => !(s.id in pages)).length,
  };
}

/** How the one library's Your music pages can be ordered. */
export type LibrarySort = "title" | "artist" | "source";
export const LIBRARY_SORTS: [LibrarySort, string][] = [
  ["title", "Title"],
  ["artist", "Artist"],
  ["source", "Source"],
];

const sortName = (s: string) => s.replace(/^(the|a|an)\s+/i, "").toLocaleLowerCase();
/** An artist to sort by; a line that names none ("26 tracks", a soundtrack's) goes last. */
const artistName = (s: string) => (/^\d/.test(s) || !s ? "\uffff" : sortName(s.split(" · ")[0]));

/** The sources a page of the one library has something from, in the library's order. */
export function sourcesIn(sections: Section[], services: ServiceInfo[]): ServiceInfo[] {
  const have = new Set<string>();
  for (const s of sections) {
    s.items.forEach((c) => c.source && have.add(c.source));
    (s.tracks ?? []).forEach((t) => t.source && have.add(t.source));
  }
  return librarySources(services).filter((s) => have.has(s.id));
}

/** A Your music page with only one source's (`source`, or every one's: "") and in the order chosen. */
export function filterLibrary(sections: Section[], source: string, sort: LibrarySort, services: ServiceInfo[]): Section[] {
  const rank = new Map(librarySources(services).map((s, i) => [s.id, i]));
  const keep = <T extends { source?: string }>(list: T[]) => (source ? list.filter((x) => x.source === source) : list);
  const by = <T extends { source?: string }>(list: T[], title: (x: T) => string, artist: (x: T) => string) =>
    [...list].sort((a, b) => {
      const key = (x: T): [number | string, string] =>
        sort === "source"
          ? [rank.get(x.source ?? "") ?? 99, sortName(title(x))]
          : [sort === "artist" ? artistName(artist(x)) : sortName(title(x)), sortName(title(x))];
      const [a1, a2] = key(a);
      const [b1, b2] = key(b);
      return a1 < b1 ? -1 : a1 > b1 ? 1 : a2.localeCompare(b2);
    });
  return sections.map((s) => ({
    ...s,
    items: by(keep(s.items), (c) => c.title, (c) => c.subtitle),
    tracks: s.tracks ? by(keep(s.tracks), (t) => t.title, (t) => t.artist) : s.tracks,
  }));
}

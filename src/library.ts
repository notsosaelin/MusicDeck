import { useEffect, useRef, useState } from "react";
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
export function mergeSearch(query: string, pages: { source: ServiceInfo; page: Page }[], whole = false): Page {
  const merged: Page = { title: query, subtitle: "", art: null, collections: [], tracks: [], extra: [], tracks_first: true };
  // `whole`: every result of each (a page that shows a part of them and a Show all), else each one's first few
  for (const { source, page } of pages) {
    merged.tracks.push(...tagTracks(whole ? page.tracks : page.tracks.slice(0, SEARCH_TRACKS), source.id));
    merged.collections.push(...tagCollections(whole ? page.collections : page.collections.slice(0, SEARCH_COLLECTIONS), source.id));
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

/** Which source's results show first: your own files, then music services, then the rest (radio, podcasts, books). */
const SEARCH_FIRST = ["local", "qobuz", "spotify", "tidal", "subsonic", "jellyfin", "emby", "plex", "soulsync", "youtube",
  "soundcloud", "bandcamp", "steam"];
const searchRank = (type: string) => (SEARCH_FIRST.includes(type) ? SEARCH_FIRST.indexOf(type) : SEARCH_FIRST.length);

/** Of the sources that found something, the one to show first. */
export const bestResults = <T extends { source: ServiceInfo }>(pages: T[]): T | undefined =>
  [...pages].sort((a, b) => searchRank(a.source.type) - searchRank(b.source.type))[0];

/** How long after the first source answers a search waits for better ones before its results show */
const SEARCH_SETTLE_MS = 1200;

/**
 * Which of a search's sources shows, and the order they're in (the dots'), steady under the controller: once every
 * source has answered (or a moment after the first did), the best first and on show; from then on it stays, and one
 * answering later goes at the end, nothing moving. `pick` shows another. Nothing until then.
 */
export function useSearchChoice<T extends { source: ServiceInfo }>(
  query: string,
  pages: T[],
  waiting: number,
): { ordered: T[]; at: T | undefined; pick: (id: string) => void } {
  const [fixed, setFixed] = useState<{ query: string; order: string[]; chosen: string } | null>(null);
  const latest = useRef(pages);
  latest.current = pages;
  const mine = fixed?.query === query ? fixed : null;
  const answered = pages.length > 0;
  useEffect(() => {
    if (mine || !answered) return;
    const fix = () => {
      const ranked = [...latest.current].sort((a, b) => searchRank(a.source.type) - searchRank(b.source.type));
      if (ranked.length) setFixed({ query, order: ranked.map((p) => p.source.id), chosen: ranked[0].source.id });
    };
    if (!waiting) return fix();
    const timer = setTimeout(fix, SEARCH_SETTLE_MS);
    return () => clearTimeout(timer);
  }, [query, !!mine, answered, waiting === 0]);
  if (!mine) return { ordered: [], at: undefined, pick: () => {} };
  const ordered = [
    ...mine.order.map((id) => pages.find((p) => p.source.id === id)).filter((p): p is T => !!p),
    ...pages.filter((p) => !mine.order.includes(p.source.id)),
  ];
  return {
    ordered,
    at: ordered.find((p) => p.source.id === mine.chosen) ?? ordered[0],
    pick: (id) => setFixed({ ...mine, chosen: id }),
  };
}

/** Every source searched at once: the page so far (null until one answers), and how many are still looking.
 *  `rememberIn`: the source whose recent searches keep it (the media center's live one). */
export function useLibrarySearch(
  query: string,
  rememberIn?: string,
): { page: Page | null; waiting: number; pages: { source: ServiceInfo; page: Page }[] } {
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
    // each source that found something, its whole results (the media center and the panel show one source at a time)
    pages: answered.filter(({ page }) => page.tracks.length || page.collections.length || page.extra?.some((x) => x.tracks.length)),
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

/**
 * The Music section's state: where it is (its own back and forward, which Steam's arrows drive while Music
 * shows), and what pages say, fetched from the live source and kept in the media center's cache
 * (resultCache, cleared by dataVersion when the library changes).
 */
import { useEffect, useState, useSyncExternalStore } from "react";
import { Collection, Result, SmartPlaylist } from "../api";
import { dataVersion, resultCache } from "../nav";
import { Store, pluginState, useStore } from "../store";

/** A page of Music. `source`: one source's own (its Home, its tabs, its album); without, the one library's (Home,
 *  search and Your music across every source). */
export type Place =
  | { type: "home"; source?: string }
  | { type: "tab"; id: string; title: string; source?: string }
  | { type: "collection"; collection: Collection; source?: string }
  | { type: "search"; query: string }
  | { type: "smart"; playlist: SmartPlaylist }
  | { type: "stats" }
  | { type: "tools"; source?: string }
  | { type: "columns" };

export const places = new Store<{ back: Place[]; current: Place; forward: Place[] }>({
  back: [],
  current: { type: "home" },
  forward: [],
});

/** what the desktop controller does that views ask for (set while desktop Steam shows) */
export const desktopActions = { toggleMini: () => {} };

/** How what's playing shows: a panel beside the page (the queue, the lyrics), or the full Now Playing view in
 *  the page's place (its pane: the lyrics, the queue, or only the art). Never both: the full view hides the
 *  panel, and keeps which it was to give it back. */
export type Pane = "queue" | "lyrics";
export const panel = new Store<{ open: Pane | null; full: boolean; pane: Pane | "art" }>({ open: null, full: false, pane: "lyrics" });

/** The bar's Lyrics or Queue: in the full view, its pane; else the panel beside the page (again: closed). */
export function showPane(which: Pane) {
  const p = panel.get();
  if (p.full) panel.set({ ...p, pane: p.pane === which ? "art" : which });
  else panel.set({ ...p, open: p.open === which ? null : which });
}

/** The full Now Playing view, opened (with the pane the panel had, if one was open) or closed. */
export function toggleFull(open = !panel.get().full) {
  const p = panel.get();
  panel.set({ ...p, full: open, pane: open && p.open ? p.open : p.pane });
}

export const usePlace = () => useSyncExternalStore(places.subscribe, places.get).current;

export function go(place: Place) {
  const { back, current } = places.get();
  if (JSON.stringify(place) === JSON.stringify(current)) return;
  places.set({ back: [...back, current].slice(-50), current: place, forward: [] });
}

/** Steam's back arrow while Music shows: false when there's nowhere back to go (Music is left then). */
export function goBack(): boolean {
  const { back, current, forward } = places.get();
  if (!back.length) return false;
  places.set({ back: back.slice(0, -1), current: back[back.length - 1], forward: [current, ...forward] });
  return true;
}

export function goForward(): boolean {
  const { back, current, forward } = places.get();
  if (!forward.length) return false;
  places.set({ back: [...back, current], current: forward[0], forward: forward.slice(1) });
  return true;
}

/** Back to Home, as when the source changes (its pages aren't the other's). */
export const goHome = () => places.set({ back: [], current: { type: "home" }, forward: [] });

/** The source the queue plays from and Music shows. */
export function useLiveSource() {
  const state = useStore(pluginState);
  return state?.services.find((s) => s.id === state.active_service) ?? null;
}

/** A page's data, from the cache when it's there; fetched again when the library changes. */
export function useResult<T>(key: string | null, fetch: () => Promise<Result<T>>): Result<T> | null {
  const version = useSyncExternalStore(dataVersion.subscribe, dataVersion.get).v;
  const [result, setResult] = useState<Result<T> | null>(() => (key ? resultCache.get(key) ?? null : null));
  useEffect(() => {
    if (!key) return;
    const cached = resultCache.get(key);
    if (cached) {
      setResult(cached);
      return;
    }
    setResult(null);
    let current = true;
    fetch()
      .then((r) => {
        if (r.ok) resultCache.set(key, r);
        if (current) setResult(r);
      })
      .catch((e) => current && setResult({ ok: false, error: String(e) }));
    return () => {
      current = false;
    };
  }, [key, version]);
  return result;
}

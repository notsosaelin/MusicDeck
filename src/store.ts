import { useSyncExternalStore } from "react";
import { PluginState, getState } from "./api";

/** Minimal observable value usable from React via useSyncExternalStore. */
export class Store<T> {
  private listeners = new Set<() => void>();

  constructor(private value: T) {}

  get = () => this.value;

  set(value: T) {
    this.value = value;
    this.listeners.forEach((l) => l());
  }

  update(partial: Partial<T>) {
    this.set({ ...this.value, ...partial });
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
}

/**
 * A Map that keeps only the `limit` most recently used entries, for caches that would otherwise
 * grow for as long as Steam runs.
 */
export class RecentlyUsed<V> {
  private entries = new Map<string, V>();

  constructor(private limit: number) {}

  has = (key: string) => this.entries.has(key);

  get(key: string): V | undefined {
    if (!this.entries.has(key)) return undefined;
    const value = this.entries.get(key)!;
    this.entries.delete(key);
    this.entries.set(key, value);
    return value;
  }

  set(key: string, value: V) {
    this.entries.delete(key);
    this.entries.set(key, value);
    while (this.entries.size > this.limit) this.entries.delete(this.entries.keys().next().value!);
  }

  delete = (key: string) => this.entries.delete(key);

  clear = () => this.entries.clear();
}

export function useStore<T>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get);
}

/** Backend settings and service list. null until the first load completes. */
export const pluginState = new Store<PluginState | null>(null);

export async function refreshState(): Promise<PluginState> {
  const state = await getState();
  pluginState.set(state);
  return state;
}

export function mediaUrl(kind: "stream" | "art", instanceId: string, id: string): string | undefined {
  const state = pluginState.get();
  if (!state) return undefined;
  const { base, key } = state.server;
  return `${base}/${kind}/${encodeURIComponent(instanceId)}/${encodeURIComponent(id)}?k=${encodeURIComponent(key)}`;
}

/** A picture somewhere else (a catalogue's cover), through the stream server: Steam's pages show the server's. */
export function relayUrl(url: string | undefined): string {
  const state = pluginState.get();
  if (!url || !state) return "";
  const b64 = btoa(unescape(encodeURIComponent(url))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${state.server.base}/fetch?k=${encodeURIComponent(state.server.key)}&u=${b64}`;
}

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

import { addEventListener, removeEventListener } from "@decky/api";
import { removeService, setActiveService } from "./api";
import { invalidateBrowserCache } from "./nav";
import { player } from "./player";
import { pluginState, refreshState } from "./store";

/** Changes the live source; followState resets playback and browsing. */
export async function switchSource(instanceId: string) {
  if (pluginState.get()?.active_service === instanceId) return;
  await setActiveService(instanceId);
  await refreshState();
}

/** Removes a source; followState clears playback if anything playing came from it. */
export async function deleteSource(instanceId: string) {
  await removeService(instanceId);
  await refreshState();
}

/**
 * Keeps the player in step with the settings and sources. One source plays at a time, so when the live
 * source changes, whatever came from the previous one stops, the queue (and its saved session) is
 * emptied, and the library views start over; when a source is removed, nothing queued from it stays.
 * Playback options (crossfade, levelling, …) take effect as they change.
 *
 * This reacts to the state rather than living in switchSource or the settings page because a change
 * can come from another copy of the plugin: Decky can keep showing its plugin page from before a
 * reload, and that page talks to its own (unloaded) copy. The backend announces every change
 * ("state_changed"), so the copy that's playing reloads the state and follows. Returns the function
 * that stops it.
 */
export function followState(): () => void {
  // undefined until the first state arrives: loading isn't a change (the saved session is restored)
  let live: string | null | undefined = pluginState.get()?.active_service;
  const unsubscribe = pluginState.subscribe(() => {
    const state = pluginState.get();
    if (!state) return;
    const sources = new Set(state.services.map((s) => s.id));
    const switched = live !== undefined && state.active_service !== live;
    live = state.active_service;
    if (switched || player.state.get().queue.some((q) => !sources.has(q.instanceId))) player.clear();
    if (switched) invalidateBrowserCache();
    player.setPlayback(state.playback);
  });
  const onChange = () => {
    refreshState().catch(() => {});
  };
  addEventListener("state_changed", onChange);
  return () => {
    unsubscribe();
    removeEventListener("state_changed", onChange);
  };
}

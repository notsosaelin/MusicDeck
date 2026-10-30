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
  // which EQ applies: it changes with the output, or with each output getting its own
  let eqFor: string | undefined;
  // the devices plugged in when the state last came (undefined before it first does)
  let plugged: Set<string> | undefined;
  const unsubscribe = pluginState.subscribe(() => {
    const state = pluginState.get();
    if (!state) return;
    const sources = new Set(state.services.map((s) => s.id));
    const switched = live !== undefined && state.active_service !== live;
    live = state.active_service;
    // another device's songs ("peer~...", handed over) aren't a source here, and stay
    const gone = (id: string) => !sources.has(id) && !id.startsWith("peer~");
    if (switched || player.state.get().queue.some((q) => gone(q.instanceId))) player.clear();
    if (switched) invalidateBrowserCache();
    // a disc ejected, a player unplugged: its songs leave the queue (they can't play; left, they were skipped one by
    // one); only on a change, as the devices are only known a moment after start-up
    const pluggedNow = new Set((state.devices ?? []).map((d) => d.id));
    const left = plugged ? [...plugged].filter((id) => !pluggedNow.has(id)) : [];
    plugged = pluggedNow;
    if (left.length) {
      const queue = player.state.get().queue;
      const theirs = queue.flatMap((q, i) => (left.some((id) => q.track.id.startsWith(`dv~${id}~`)) ? [i] : []));
      if (theirs.length === queue.length && queue.length) player.clear();
      else for (const i of theirs.reverse()) player.removeAt(i);
    }
    player.setPlayback(state.playback);
    const now = `${state.eq_per_output}:${state.output?.id ?? ""}`;
    if (eqFor !== undefined && now !== eqFor) player.setEq(state.eq);
    eqFor = now;
  });
  const onChange = () => {
    refreshState().catch(() => {});
  };
  addEventListener("state_changed", onChange);
  // a paired device's playlists came in: pages showing them are fetched again
  addEventListener("playlists_changed", invalidateBrowserCache);
  return () => {
    unsubscribe();
    removeEventListener("state_changed", onChange);
    removeEventListener("playlists_changed", invalidateBrowserCache);
  };
}

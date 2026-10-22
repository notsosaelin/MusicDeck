import { removeService, setActiveService } from "./api";
import { invalidateBrowserCache } from "./nav";
import { player } from "./player";
import { pluginState, refreshState } from "./store";

/**
 * Changes the live source. One source plays at a time, so whatever came from the previous one stops
 * and the queue (and its saved session) is emptied: nothing from the old source stays playable.
 */
export async function switchSource(instanceId: string) {
  if (pluginState.get()?.active_service === instanceId) return;
  player.clear();
  await setActiveService(instanceId);
  invalidateBrowserCache();
  await refreshState();
}

/** Removes a source; if it was live (or anything queued came from it), playback is cleared too. */
export async function deleteSource(instanceId: string) {
  const wasActive = pluginState.get()?.active_service === instanceId;
  const queued = player.state.get().queue.some((q) => q.instanceId === instanceId);
  if (wasActive || queued) player.clear();
  await removeService(instanceId);
  invalidateBrowserCache();
  await refreshState();
}

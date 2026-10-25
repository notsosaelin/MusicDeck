import { Update, checkUpdate } from "./api";
import { notify } from "./notify";
import { pluginState, refreshState } from "./store";

/**
 * New versions: the backend looks on GitHub (updates.py) and puts what it found in the state; installing it is
 * Decky's own installer's job, which asks first, checks the zip's SHA-256, replaces the plugin and loads it again.
 */

// Decky's PluginInstallType: an update says "Update" in its prompt
const UPDATE = 2;
const TOLD = "musicdeck-update-told";

/** Decky's install prompt for `update`; the plugin reloads once it's in. */
export function installUpdate(update: Update): Promise<unknown> {
  return (window as any).DeckyBackend.call("utilities/install_plugin", update.url, "MusicDeck", update.version, update.sha256, UPDATE);
}

/** Looks now; what was found lands in the state. Resolves to the newer version, null, or an error's words. */
export async function lookForUpdate(): Promise<Update | null | string> {
  const r = await checkUpdate().catch((e) => ({ ok: false as const, error: String(e) }));
  await refreshState();
  return r.ok ? r.data : r.error;
}

/** A notification the first time each new version is seen (not again on every start). */
export function watchForUpdates(): () => void {
  const tell = () => {
    const found = pluginState.get()?.update;
    if (!found) return;
    let told = "";
    try {
      told = localStorage.getItem(TOLD) ?? "";
    } catch {}
    if (told === found.version) return;
    try {
      localStorage.setItem(TOLD, found.version);
    } catch {}
    notify(`MusicDeck ${found.version}`, "Update in MusicDeck's settings");
  };
  tell();
  return pluginState.subscribe(tell);
}

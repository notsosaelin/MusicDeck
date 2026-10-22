import { steamLibrary } from "./api";
import { invalidateBrowserCache } from "./nav";
import { pluginState } from "./store";

// EAppType for soundtracks ("Music" in Steam's library filters)
const APP_TYPE_SOUNDTRACK = 0x2000;

/** A soundtrack the account owns, as the Steam soundtracks source receives it. */
export interface OwnedSoundtrack {
  appid: number;
  name: string;
  /** the game it belongs to */
  parent: number | null;
  parent_name: string;
  /** Steam's album art for it (used until it's installed and has a cover of its own) */
  cover: string | null;
}

/**
 * Only Steam's UI knows which soundtracks the account owns and which game is running; the backend
 * only sees what is installed. This reads both from Steam's own stores.
 */
function readSteam(): { owned: OwnedSoundtrack[]; running: number | null } {
  const apps = window.collectionStore?.allAppsCollection?.allApps ?? [];
  const owned = apps
    .filter((a) => a.app_type === APP_TYPE_SOUNDTRACK)
    .map((a) => {
      const parent = a.optional_parent_app_id || null;
      return {
        appid: a.appid,
        name: a.display_name,
        parent,
        parent_name: (parent && window.appStore.GetAppOverviewByAppID(parent)?.display_name) || "",
        // not in @decky/ui's typings; the same call Steam's library uses for album art
        cover: (window.appStore as any).GetAlbumCoverURLsForApp?.(a)?.[0] ?? null,
      };
    });
  return { owned, running: window.SteamUIStore?.MainRunningAppID ?? null };
}

const steamSources = () => pluginState.get()?.services.filter((s) => s.type === "steam") ?? [];

/** Sends the owned soundtracks and the running game to every Steam soundtracks source. */
export async function syncSteam() {
  const sources = steamSources();
  if (!sources.length) return;
  const { owned, running } = readSteam();
  await Promise.all(sources.map((s) => steamLibrary(s.id, owned, running)));
}

/**
 * Keeps the running game current: its soundtrack leads the home view while it's played. Returns
 * the unregister function.
 */
export function watchRunningGame(): () => void {
  const registration = window.SteamClient?.GameSessions?.RegisterForAppLifetimeNotifications?.(() => {
    // the store updates just after the notification
    setTimeout(async () => {
      await syncSteam();
      const active = pluginState.get()?.active_service;
      if (steamSources().some((s) => s.id === active)) invalidateBrowserCache();
    }, 1000);
  });
  return () => registration?.unregister();
}

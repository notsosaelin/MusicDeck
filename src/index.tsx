import { addEventListener, definePlugin, removeEventListener, routerHook } from "@decky/api";
import { quickAccessMenuClasses, showModal, staticClasses } from "@decky/ui";
import { FaMusic } from "react-icons/fa";
import * as api from "./api";
import { MusicPanel } from "./components/MusicPanel";
import { MediaCenter } from "./components/media/MediaCenter";
import { showTrackMenu } from "./components/TrackMenu";
import { lookupOpener } from "./components/TagEditor";
import { notify, releaseToastHooks } from "./notify";
import { followState, switchSource } from "./sources";
import {
  PLAYER_ROUTE,
  browsePreview,
  centerTab,
  invalidateBrowserCache,
  navigation,
  openInLibrary,
  openMediaCenter,
  panelTab,
  reopenMediaCenterIfShowing,
} from "./nav";
import { Settings } from "./components/Settings";
import { ServiceModal } from "./components/ServiceModal";
import { NameModal } from "./components/NameModal";
import { watchCastStatus } from "./cast";
import { player } from "./player";
import { setSleepTimer, sleepTimer } from "./sleep";
import { syncSteam } from "./steam";
import { duckFor, reconcile, runningGame, toggleGameMusic, watchGames } from "./games";
import { syncLibraryEntry, watchLibraryEntry } from "./libraryEntry";
import { installQamPatch } from "./qam";
import { pluginState, refreshState } from "./store";
import { desktopDebug, startDesktop } from "./desktop";

/** The backend can still be starting (e.g. right after an update), so retry with backoff. */
async function loadStateWithRetry(attempts = 8): Promise<api.PluginState> {
  for (let i = 0; ; i++) {
    try {
      return await refreshState();
    } catch (e) {
      if (i >= attempts) throw e;
      await new Promise((r) => setTimeout(r, Math.min(500 * 2 ** i, 5000)));
    }
  }
}

export default definePlugin(() => {
  loadStateWithRetry()
    .then(async (state) => {
      player.setVolume(state.volume);
      player.setEq(state.eq);
      player.setPlayback(state.playback);
      syncSteam();
      // MusicDeck in the Steam library, when it's switched on (and its shortcut was lost)
      syncLibraryEntry().catch((e) => console.warn("[MusicDeck] Library entry", e));
      // Pick up where we left off (here, or on another device via the server's saved queue)
      if (state.playback.resume) {
        const session = await api.loadSession();
        if (session) player.restore(session);
      }
    })
    .catch((e) => console.error("[MusicDeck] Failed to load state", e));

  const stopFollowing = followState();
  routerHook.addRoute(PLAYER_ROUTE, MediaCenter, { exact: true });
  reopenMediaCenterIfShowing();
  const unwatchGames = watchGames();
  const unwatchCast = watchCastStatus();
  const unwatchLibraryEntry = watchLibraryEntry();
  // desktop Steam: the Music tab, the mini player and media keys (src/desktop)
  const stopDesktop = startDesktop();
  // a source's own news (local files: what new albums got): a toast, and lists that show it again
  const sourceNote = (instanceId: string, text: string) => {
    const name = pluginState.get()?.services.find((s) => s.id === instanceId)?.name ?? "MusicDeck";
    invalidateBrowserCache();
    refreshState().catch(() => {});
    notify(name, text);
  };
  addEventListener("source_note", sourceNote);
  const unpatchQam = installQamPatch(
    <MusicPanel />,
    // Native tabs pass a pre-styled element; a bare string renders unstyled
    <div className={quickAccessMenuClasses.Title}>MusicDeck</div>,
    <FaMusic />,
    () => pluginState.get()?.qam_mode ?? "replace",
  );

  // Handle for poking at the player from the CEF debugger (SharedJSContext)
  const debugHandle = {
    player,
    pluginState,
    panelTab,
    // the media center: centerTab.set({ tab: "playing" }) shows a tab, openMediaCenter() opens it
    centerTab,
    openMediaCenter,
    // an album's, artist's or playlist's page ({ id, kind, title }), in the media center when it's showing
    openInLibrary,
    // another source's Home, Browse and Search in the media center, the live one left playing:
    // previewSource("<source id>"), then previewSource() to stop
    previewSource: (id?: string) => browsePreview.set({ id: id || null }),
    navigation,
    api,
    showTrackMenu,
    notify,
    switchSource,
    setSleepTimer,
    sleepTimer,
    syncSteam,
    // desktop Steam: desktop.current?.showMusic(), .openMini(), .mini() (its window), .state()
    desktop: desktopDebug,
    // "Look up details" for any source's album: lookup.open(sourceId, album, files)
    lookup: lookupOpener,
    // pretend which games are running, e.g. games.reconcile([570]) then games.reconcile([]); which app
    // is in front: games.duckFor(570), games.duckFor(769)
    games: { reconcile, runningGame, toggle: toggleGameMusic, duckFor },
    // open dialogs for on-device layout checks: a source's edit dialog, or the add dialog for a type
    sourceDialog: (idOrType: string) => {
      const state = pluginState.get();
      const existing = state?.services.find((s) => s.id === idOrType);
      const type = existing?.type ?? idOrType;
      const typeName = state?.service_types.find((t) => t.type === type)?.display_name ?? type;
      return showModal(<ServiceModal type={type} typeName={typeName} existing={existing} />);
    },
    nameDialog: () => showModal(<NameModal title="New playlist" action="Create" onSubmit={() => {}} />),
  };
  (window as any).__musicdeck = debugHandle;

  return {
    name: "MusicDeck",
    titleView: <div className={staticClasses.Title}>MusicDeck</div>,
    content: <Settings />,
    icon: <FaMusic />,
    onDismount() {
      unpatchQam?.();
      stopFollowing();
      routerHook.removeRoute(PLAYER_ROUTE);
      unwatchGames();
      unwatchCast();
      unwatchLibraryEntry();
      stopDesktop();
      removeEventListener("source_note", sourceNote);
      setSleepTimer(null);
      releaseToastHooks();
      player.destroy();
      // a reload can mount the new instance before this one unmounts; only remove our own handle
      if ((window as any).__musicdeck === debugHandle) delete (window as any).__musicdeck;
    },
  };
});

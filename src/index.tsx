import { definePlugin, routerHook } from "@decky/api";
import { quickAccessMenuClasses, showModal, staticClasses } from "@decky/ui";
import { FaMusic } from "react-icons/fa";
import * as api from "./api";
import { MusicPanel } from "./components/MusicPanel";
import { MediaCenter } from "./components/media/MediaCenter";
import { showTrackMenu } from "./components/TrackMenu";
import { notify, releaseToastHooks } from "./notify";
import { followState, switchSource } from "./sources";
import { PLAYER_ROUTE, navigation, panelTab } from "./nav";
import { Settings } from "./components/Settings";
import { ServiceModal } from "./components/ServiceModal";
import { NameModal } from "./components/NameModal";
import { player } from "./player";
import { setSleepTimer, sleepTimer } from "./sleep";
import { syncSteam } from "./steam";
import { reconcile, runningGame, toggleGameMusic, watchGames } from "./games";
import { installQamPatch } from "./qam";
import { pluginState, refreshState } from "./store";

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
      // Pick up where we left off (here, or on another device via the server's saved queue)
      if (state.playback.resume) {
        const session = await api.loadSession();
        if (session) player.restore(session);
      }
    })
    .catch((e) => console.error("[MusicDeck] Failed to load state", e));

  const stopFollowing = followState();
  routerHook.addRoute(PLAYER_ROUTE, MediaCenter, { exact: true });
  const unwatchGames = watchGames();
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
    navigation,
    api,
    showTrackMenu,
    notify,
    switchSource,
    setSleepTimer,
    sleepTimer,
    syncSteam,
    // pretend which games are running, e.g. games.reconcile([570]) then games.reconcile([])
    games: { reconcile, runningGame, toggle: toggleGameMusic },
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
      setSleepTimer(null);
      releaseToastHooks();
      player.destroy();
      // a reload can mount the new instance before this one unmounts; only remove our own handle
      if ((window as any).__musicdeck === debugHandle) delete (window as any).__musicdeck;
    },
  };
});

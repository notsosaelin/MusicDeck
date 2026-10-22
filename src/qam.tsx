import { ErrorBoundary, Navigation } from "@decky/ui";
import { FC, ReactNode, useSyncExternalStore } from "react";
import { QamMode } from "./api";

/** EQuickAccessTab.Soundtrack in Steam's client (see @decky/ui globals/stores/steamuistore/menu). */
const SOUNDTRACK_TAB = 7;
const OWN_TAB_KEY = "musicdeck";
/** Window key naming the plugin instance that owns the tab (the newest one loaded). */
const OWNER_KEY = "__musicdeckQamOwner";
/** Window key of the PanelRegistry, shared by every copy of the plugin loaded in this Steam session. */
const REGISTRY_KEY = "__musicdeckPanel";

interface QamTab {
  key: number | string;
  title: any;
  tab: ReactNode;
  panel: ReactNode;
  musicdeck?: boolean;
  musicdeckOriginalPanel?: ReactNode;
  musicdeckOriginalTitle?: any;
}

interface TabsHook {
  render(existingTabs: QamTab[], visible: boolean): void;
}

/**
 * The panel the tab should show: the newest loaded copy's. Older copies of the plugin read this
 * too, so its shape must stay the same between versions.
 */
interface PanelRegistry {
  panel: ReactNode;
  listeners: Set<() => void>;
}

function panelRegistry(): PanelRegistry {
  const w = window as any;
  return (w[REGISTRY_KEY] ??= { panel: null, listeners: new Set() });
}

function setCurrentPanel(panel: ReactNode) {
  const registry = panelRegistry();
  registry.panel = panel;
  registry.listeners.forEach((l) => l());
}

const subscribeToPanel = (listener: () => void) => {
  const { listeners } = panelRegistry();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};

/**
 * What the tab holds: a host that draws the current panel. Steam only rebuilds the QAM's tabs when
 * the menu renders, so after a reload or update with the menu open, the tab still holds the previous
 * copy's element. Because that element is a host, it switches to the new copy's panel right away,
 * instead of leaving the unloaded copy on screen, stuck on whatever it was showing.
 */
const PanelHost: FC = () => <>{useSyncExternalStore(subscribeToPanel, () => panelRegistry().panel)}</>;

/**
 * Takes over the Soundtracks tab of the Quick Access Menu.
 *
 * Decky Loader already patches the QAM renderer to append its own tab, and on every render it
 * calls `window.__TABS_HOOK_INSTANCE.render(tabs, visible)` with Steam's tab array. Wrapping that
 * method gives us the same array without a second, independently-maintained React patch. If
 * Decky ever stops exposing it, `installQamPatch` returns null and the plugin still works from
 * the Decky menu.
 *
 * In "replace" mode the Soundtracks tab keeps its icon and position and only its panel changes.
 * If Steam isn't rendering a Soundtracks tab, or in "separate" mode, we add our own tab instead.
 *
 * Only the newest loaded copy patches; wrappers left by older copies step aside, so they can't put
 * back a panel nothing updates any more.
 */
/** The newest loaded copy's tab mode, for openMusicDeck. */
let currentMode: () => QamMode = () => "replace";

/** Opens the Quick Access menu on MusicDeck: the Soundtracks tab it took over, or its own tab. */
export function openMusicDeck() {
  Navigation.OpenQuickAccessMenu((currentMode() === "replace" ? SOUNDTRACK_TAB : OWN_TAB_KEY) as any);
}

export function installQamPatch(
  panel: ReactNode,
  title: ReactNode,
  icon: ReactNode,
  getMode: () => QamMode,
): (() => void) | null {
  const hook: TabsHook | undefined = (window as any).__TABS_HOOK_INSTANCE;
  if (!hook || typeof hook.render !== "function") {
    console.warn("[MusicDeck] Decky TabsHook not found; QAM integration disabled");
    return null;
  }
  const current = <ErrorBoundary>{panel}</ErrorBoundary>;
  setCurrentPanel(current);
  currentMode = getMode;
  const wrapped = <PanelHost />;
  const original = hook.render;
  // Tab objects we've modified, so unloading can put Soundtracks back the way Steam had it
  const touched = new Set<QamTab>();

  const owner = {};
  const w = window as any;
  w[OWNER_KEY] = owner;
  const wrapper = function (this: TabsHook, existingTabs: QamTab[], visible: boolean) {
    const result = original.call(this, existingTabs, visible);
    if (w[OWNER_KEY] !== owner) return result;
    try {
      patchTabs(existingTabs, { panel: wrapped, title, icon }, getMode(), touched);
    } catch (e) {
      console.error("[MusicDeck] Failed to patch QAM tabs", e);
    }
    return result;
  };
  hook.render = wrapper;

  return () => {
    if (w[OWNER_KEY] === owner) delete w[OWNER_KEY];
    if (panelRegistry().panel === current) setCurrentPanel(null);
    // On reload the new instance may already have wrapped us; restoring `original` then would
    // silently unhook it. Only unwrap if we're still outermost; otherwise stay as a passthrough.
    if (hook.render === wrapper) hook.render = original;
    // Only put Soundtracks back if it's still showing our panel (not a newer instance's)
    touched.forEach((tab) => tab.panel === wrapped && restore(tab));
  };
}

function restore(tab: QamTab) {
  if (!tab.musicdeck) return;
  tab.panel = tab.musicdeckOriginalPanel;
  tab.title = tab.musicdeckOriginalTitle;
  tab.musicdeck = false;
}

function patchTabs(
  tabs: QamTab[],
  ours: { panel: ReactNode; title: ReactNode; icon: ReactNode },
  mode: QamMode,
  touched: Set<QamTab>,
) {
  const soundtrack = tabs.find((t) => t.key === SOUNDTRACK_TAB);
  const ownIndex = tabs.findIndex((t) => t.key === OWN_TAB_KEY);

  if (mode === "replace" && soundtrack) {
    // Steam reuses tab objects across renders (and across plugin reloads), so compare by identity
    // rather than a flag: a reloaded plugin must swap in its own, new panel element.
    if (soundtrack.panel !== ours.panel) {
      if (!soundtrack.musicdeck) {
        soundtrack.musicdeckOriginalPanel = soundtrack.panel;
        soundtrack.musicdeckOriginalTitle = soundtrack.title;
      }
      soundtrack.panel = ours.panel;
      // Steam renders `title` as the header above the panel; without this it still says "Soundtracks"
      soundtrack.title = ours.title;
      soundtrack.musicdeck = true;
      touched.add(soundtrack);
    }
    if (ownIndex >= 0) tabs.splice(ownIndex, 1);
    return;
  }

  if (soundtrack) restore(soundtrack);
  if (ownIndex >= 0) {
    Object.assign(tabs[ownIndex], { panel: ours.panel, title: ours.title, tab: ours.icon });
  } else {
    tabs.push({ key: OWN_TAB_KEY, title: ours.title, tab: ours.icon, panel: ours.panel, musicdeck: true });
  }
}

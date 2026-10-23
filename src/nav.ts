import { Navigation, Router } from "@decky/ui";
import { Collection, Result, Track, playedCollection } from "./api";
import { player } from "./player";
import { RecentlyUsed, Store, pluginState } from "./store";

/**
 * Panel navigation state, kept outside React so it survives the QAM closing, and in its own module
 * so anything (track menus, the now-playing card) can navigate without importing the views.
 */

export type PanelTab = "playing" | "library" | "search" | "eq";
export const panelTab = new Store<{ tab: PanelTab }>({ tab: "library" });
/** The tabs in the order the panel shows them. */
export const TAB_ORDER: PanelTab[] = ["playing", "library", "search", "eq"];

/** The panel's tab strip (set by MusicPanel), so a tab change can move focus there first. */
let tabStrip: HTMLElement | null = null;
/** A tab whose button should take focus once a new strip appears (see showTab). */
let focusOnStrip: PanelTab | null = null;
const focusTabButton = (strip: HTMLElement, tab: PanelTab) =>
  strip.querySelectorAll<HTMLElement>("button")[TAB_ORDER.indexOf(tab)]?.focus();
export const setTabStrip = (el: HTMLElement | null) => {
  tabStrip = el;
  if (el && focusOnStrip) {
    focusTabButton(el, focusOnStrip);
    focusOnStrip = null;
  }
};

/**
 * Opens a tab, moving the controller's focus to its button: what had focus may go with the old tab's
 * content (the mini player, a row, the now-playing card), and Steam would then spend the next press
 * finding focus again, or hand it to whatever comes first in the new tab. Now playing's strip is part of
 * its first page (it slides away with it), so going to or from Now playing the strip is a new one: its
 * button takes focus as it appears.
 */
export function showTab(tab: PanelTab) {
  const newStrip = (panelTab.get().tab === "playing") !== (tab === "playing");
  if (tabStrip) focusTabButton(tabStrip, tab);
  focusOnStrip = newStrip ? tab : null;
  panelTab.set({ tab });
}

export type View =
  | { type: "home" }
  | { type: "search"; query: string }
  | { type: "collection"; collection: Collection }
  /** one of a source's own sections (its albums, genres, charts...), opened from the media center's Browse */
  | { type: "section"; tab: string; title: string };

export type StackName = "library" | "search";

/** Navigation history per browsing tab. */
export const navigation = new Store<{ instanceId: string | null; stacks: Record<StackName, View[]> }>({
  instanceId: null,
  stacks: { library: [], search: [] },
});

export const ROOTS: Record<StackName, View> = { library: { type: "home" }, search: { type: "search", query: "" } };

export function setStack(name: StackName, views: View[]) {
  const s = navigation.get();
  navigation.update({ stacks: { ...s.stacks, [name]: views } });
}

export const pushView = (name: StackName, view: View) => setStack(name, [...navigation.get().stacks[name], view]);

/** Back to the root view of a stack (re-selecting its tab). */
export const resetStack = (name: StackName) => {
  const views = navigation.get().stacks[name];
  if (views.length > 1) setStack(name, views.slice(0, 1));
};

/** Returns false when already at the root, so the caller can let B close the menu instead. */
export const popView = (name: StackName): boolean => {
  const views = navigation.get().stacks[name];
  if (views.length < 2) return false;
  setStack(name, views.slice(0, -1));
  return true;
};

/** Open an album/artist/playlist in the Library tab, from anywhere. */
export const openInLibrary = (collection: Collection) => {
  pushView("library", { type: "collection", collection });
  showTab("library");
};

/**
 * Results kept per view, so going back doesn't refetch; cleared when switching service. The 40 most
 * recently used are kept (a long playlist's page is a few hundred KB).
 */
export const resultCache = new RecentlyUsed<Result<any>>(40);
/** Bumped on invalidation so views that are open refetch instead of showing stale data. */
export const dataVersion = new Store<{ v: number }>({ v: 0 });
/** Plays a page's tracks and puts the page first in its source's "Jump back in" row. */
export function playFrom(instanceId: string, from: Collection, tracks: Track[], start = 0, shuffle?: boolean) {
  player.playList(instanceId, tracks, start, shuffle);
  playedCollection(instanceId, from).catch(() => {});
}

export const invalidateBrowserCache = () => {
  resultCache.clear();
  dataVersion.set({ v: dataVersion.get().v + 1 });
};

/** The media center (components/media), full screen in Steam's main window, and its open tab. */
export const PLAYER_ROUTE = "/musicdeck/player";
/**
 * "playing", "search", "settings", or one of the live source's own tabs by its id ("home", "albums",
 * "genres"... see MusicService.center_tabs)
 */
export type CenterTab = string;
export const centerTab = new Store<{ tab: CenterTab }>({ tab: "playing" });

/** The page Steam's main window shows ("/library/home", PLAYER_ROUTE...), or "" if it can't tell. */
function mainWindowPath(): string {
  return (Router as any).WindowStore?.GamepadUIMainWindowInstance?.m_history?.location?.pathname ?? "";
}

/**
 * Opens the media center, at `tab` (by default the one Customize says, or where it was left). When it's
 * already the page behind the menu, the menu just closes: navigating again would stack a second copy in
 * Steam's history, and leaving would then land on the first.
 */
export function openMediaCenter(tab?: CenterTab) {
  const openOn = pluginState.get()?.center?.open_on;
  const start = tab ?? (openOn && openOn !== "last" ? openOn : undefined);
  if (start) centerTab.set({ tab: start });
  Navigation.CloseSideMenus();
  if (mainWindowPath() !== PLAYER_ROUTE) Navigation.Navigate(PLAYER_ROUTE);
}

/**
 * After the plugin reloads (an update), a media center that was showing still shows the unloaded copy's
 * page, whose buttons reach that copy's stopped player. Opening it again shows this copy's.
 */
export function reopenMediaCenterIfShowing() {
  if (mainWindowPath() !== PLAYER_ROUTE) return;
  Navigation.NavigateBack();
  window.setTimeout(() => Navigation.Navigate(PLAYER_ROUTE), 150);
}

/**
 * Leaves the media center for the Quick Access panel: back to the page it was opened over (past any
 * copies of itself an older version stacked), or Steam's home when there's nothing to go back to.
 */
export function leaveMediaCenter(openPanel: () => void) {
  let steps = 0;
  const back = () => {
    if (mainWindowPath() === PLAYER_ROUTE && steps < 5) {
      steps++;
      Navigation.NavigateBack();
      window.setTimeout(back, 60);
      return;
    }
    if (mainWindowPath() === PLAYER_ROUTE) Navigation.Navigate("/library/home");
    window.setTimeout(openPanel, 120);
  };
  back();
}

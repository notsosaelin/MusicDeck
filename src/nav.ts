import { Navigation } from "@decky/ui";
import { Collection, Result, Track, playedCollection } from "./api";
import { player } from "./player";
import { RecentlyUsed, Store, pluginState } from "./store";

/**
 * Panel navigation state, kept outside React so it survives the QAM closing, and in its own module
 * so anything (track menus, the now-playing card) can navigate without importing the views.
 */

export type PanelTab = "playing" | "library" | "search" | "queue" | "eq";
export const panelTab = new Store<{ tab: PanelTab }>({ tab: "library" });
/** The tabs in the order the panel shows them. */
export const TAB_ORDER: PanelTab[] = ["playing", "library", "search", "queue", "eq"];

/** The panel's tab strip (set by MusicPanel), so a tab change can move focus there first. */
let tabStrip: HTMLElement | null = null;
export const setTabStrip = (el: HTMLElement | null) => {
  tabStrip = el;
};

/**
 * Opens a tab, moving the controller's focus to its button first: what had focus may go with the
 * old tab's content (the mini player, a row, the now-playing card), and Steam would then spend the
 * next press finding focus again, or hand it to whatever comes first in the new tab.
 */
export function showTab(tab: PanelTab) {
  tabStrip?.querySelectorAll<HTMLElement>("button")[TAB_ORDER.indexOf(tab)]?.focus();
  panelTab.set({ tab });
}

export type View =
  { type: "home" } | { type: "search"; query: string } | { type: "collection"; collection: Collection };

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
export type CenterTab = "playing" | "home" | "library" | "search" | "queue" | "sound" | "customize";
export const centerTab = new Store<{ tab: CenterTab }>({ tab: "playing" });

/** Opens the media center, at `tab` (by default the one Customize says, or where it was left). */
export function openMediaCenter(tab?: CenterTab) {
  const openOn = pluginState.get()?.center?.open_on;
  const start = tab ?? (openOn && openOn !== "last" ? openOn : undefined);
  if (start) centerTab.set({ tab: start });
  Navigation.CloseSideMenus();
  Navigation.Navigate(PLAYER_ROUTE);
}

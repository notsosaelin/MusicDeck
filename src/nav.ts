import { Collection, Result } from "./api";
import { Store } from "./store";

/**
 * Panel navigation state, kept outside React so it survives the QAM closing, and in its own module
 * so anything (track menus, the now-playing card) can navigate without importing the views.
 */

export type PanelTab = "library" | "search" | "lyrics" | "queue" | "eq";
export const panelTab = new Store<{ tab: PanelTab }>({ tab: "library" });

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
  panelTab.set({ tab: "library" });
};

/** Results are cached per view, so going back doesn't refetch. Cleared when switching service. */
export const resultCache = new Map<string, Result<any>>();
/** Bumped on invalidation so views that are open refetch instead of showing stale data. */
export const dataVersion = new Store<{ v: number }>({ v: 0 });
export const invalidateBrowserCache = () => {
  resultCache.clear();
  dataVersion.set({ v: dataVersion.get().v + 1 });
};

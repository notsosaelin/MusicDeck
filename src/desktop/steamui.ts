/**
 * Finding our way around desktop Steam's own window, which Decky doesn't hook (its router patch is for the
 * gamepad UI only). Steam's desktop UI is the same React app as the gamepad one, run from SharedJSContext,
 * drawing into the main window ("SP Desktop_uid0", a popup of g_PopupManager) and its menus.
 *
 * Steam's class names are minified and change between builds, and its labels are translated, so the top
 * navigation (back, forward, Store, Library, Community, <user>) is found by its shape: a row whose first
 * children are the two arrow icons and the rest are tabs sharing one class.
 */
import { findModule } from "@decky/ui";
import type { ReactNode } from "react";

/** SteamClient.UI's modes (EUIMode) */
export const UI_MODE_DESKTOP = 7;

export interface Root {
  render(node: ReactNode): void;
  unmount(): void;
}

let createRootFn: ((container: Element) => Root) | null | undefined;

/** React 19's createRoot (not on SP_REACTDOM: in a module of its own), Steam's own React. */
export function createRoot(container: Element): Root | null {
  if (createRootFn === undefined) {
    const mod = findModule((m: any) => typeof m?.createRoot === "function" && typeof m?.createPortal !== "function");
    createRootFn = mod ? mod.createRoot : null;
  }
  return createRootFn ? createRootFn(container) : null;
}

/** The desktop main window, when it's open. */
export function mainWindow(): Window | null {
  const pm = (window as any).g_PopupManager;
  for (const popup of pm?.GetPopups?.() ?? []) {
    if (String(popup?.m_strName ?? "").startsWith("SP Desktop") && popup.window && !popup.window.closed) return popup.window;
  }
  return null;
}

export interface TopNav {
  /** the row of tabs */
  row: HTMLElement;
  /** Store, Library, Community, <user>: Steam's own tabs, in order */
  tabs: HTMLElement[];
  /** the class every tab has, and the one only the current section's has */
  tabClass: string;
  selectedClass: string;
}

const classes = (el: Element) => [...el.classList];

/** The top navigation of the main window, or null when it isn't drawn (yet) or isn't recognised. */
export function findTopNav(doc: Document): TopNav | null {
  for (const row of doc.querySelectorAll<HTMLElement>("div")) {
    const kids = [...row.children];
    if (kids.length < 5 || kids[0].tagName.toLowerCase() !== "svg" || kids[1].tagName.toLowerCase() !== "svg") continue;
    const tabs = kids.slice(2).filter((k): k is HTMLElement => k.tagName === "DIV");
    if (tabs.length < 3 || row.getBoundingClientRect().top > 120) continue;
    const shared = classes(tabs[0]).filter((c) => tabs.every((t) => t.classList.contains(c)));
    if (!shared.length) continue;
    // the current section's tab has one more class than the others (when one is current)
    const counts = new Map<string, number>();
    for (const t of tabs) for (const c of classes(t)) if (!shared.includes(c)) counts.set(c, (counts.get(c) ?? 0) + 1);
    const selectedClass = [...counts].find(([, n]) => n === 1)?.[0] ?? "";
    return { row, tabs: tabs.filter((t) => !t.dataset.musicdeck), tabClass: shared[0], selectedClass };
  }
  return null;
}

/** Where a page goes: under the top navigation, above the footer (Add a Game, Downloads, Friends). */
export function contentBounds(win: Window, nav: TopNav): { top: number; bottom: number } {
  // the navigation's bar spans the window: its bottom is where pages start
  let bar: HTMLElement | null = nav.row;
  while (bar?.parentElement && bar.getBoundingClientRect().width < win.innerWidth - 8) bar = bar.parentElement;
  const top = Math.round((bar ?? nav.row).getBoundingClientRect().bottom);
  // the footer: what spans the window at its very bottom, when it's a bar (not the page itself)
  let foot = win.document.elementFromPoint(win.innerWidth / 2, win.innerHeight - 4) as HTMLElement | null;
  while (foot?.parentElement && foot.getBoundingClientRect().width < win.innerWidth - 8) foot = foot.parentElement;
  const footTop = foot ? foot.getBoundingClientRect().top : win.innerHeight;
  const bottom = win.innerHeight - footTop < 90 && footTop > top ? Math.round(win.innerHeight - footTop) : 0;
  return { top, bottom };
}

/** The page's own background (Steam's theme), from what's drawn in the content area. */
export function pageBackground(win: Window, top: number): string {
  let el = win.document.elementFromPoint(win.innerWidth / 2, top + 40) as HTMLElement | null;
  while (el) {
    const style = win.getComputedStyle(el);
    if (style.backgroundImage && style.backgroundImage !== "none") return `${style.backgroundColor} ${style.backgroundImage}`;
    if (style.backgroundColor && !/rgba\(0, 0, 0, 0\)|transparent/.test(style.backgroundColor)) return style.backgroundColor;
    el = el.parentElement;
  }
  return "#1b2838";
}

/** Steam's own fonts (Motiva Sans) for a window of ours: its @font-face rules copied in. */
export function fontFaces(from: Document): string {
  const out: string[] = [];
  for (const sheet of [...from.styleSheets]) {
    let rules: CSSRuleList;
    try {
      rules = sheet.cssRules;
    } catch {
      continue;
    }
    for (const rule of [...rules]) if (rule.type === 5) out.push(rule.cssText); // CSSRule.FONT_FACE_RULE
  }
  return out.join("\n");
}

/**
 * MusicDeck's dialogs in desktop Steam: Decky's showModal, which there opens a window of its own, given a
 * title and a size (Steam's own dialogs say what they are in their title bar; Decky's said "Decky Dialog").
 * Steam makes a dialog's window fixed in size (its creation flags hold only Hidden) and its content as wide
 * as a confirmation box: followDialogs makes ours resizable and filled, whatever size they're dragged to.
 */
import { showModal } from "@decky/ui";
import type { ReactNode } from "react";

export interface DialogSize {
  title: string;
  width: number;
  height: number;
}

export const SIZES = {
  settings: { title: "MusicDeck Settings", width: 860, height: 760 },
  equalizer: { title: "Equalizer", width: 760, height: 640 },
  smart: { title: "Smart playlist", width: 760, height: 600 },
  info: { title: "Get Info", width: 620, height: 560 },
  name: { title: "MusicDeck", width: 480, height: 260 },
  tools: { title: "Tag tools", width: 900, height: 700 },
} satisfies Record<string, DialogSize>;

const RESIZABLE = 16;
/** Decky's name for a dialog opened without a title (code shared with Game Mode: the tag editor, confirmations) */
const UNTITLED = "Decky Dialog";
/** our dialogs' window names (Steam names a dialog's window by its title): their smallest sizes, and their own
 *  styles (a dialog is a window of its own: the main window's styles don't reach it) */
const ours = new Map<string, { width: number; height: number; css?: string }>([[UNTITLED, { width: 420, height: 240 }]]);

export function desktopDialog(node: ReactNode, size: DialogSize, css?: string) {
  ours.set(size.title, { width: Math.min(size.width, 460), height: Math.min(size.height, 260), css });
  return showModal(node, window, { strTitle: size.title, popupWidth: size.width, popupHeight: size.height });
}

/** Steam's dialog content is a confirmation box's width (474 px) and the rest of the window left black:
 *  in ours it fills the window, and follows it as it's resized. */
const FILL_CSS = `
.DialogContent { width: 100% !important; max-width: none !important; box-sizing: border-box; }
.DialogContent_InnerWidth { max-width: none !important; width: 100% !important; }
`;

/** While desktop Steam shows: our dialogs' windows made resizable as they're created (Steam opens them with
 *  window.open, its flags in the address), filled, and an untitled one named MusicDeck's. Returns the teardown. */
export function followDialogs(): () => void {
  const opener = window.open;
  const resizable = function (this: Window, url?: string | URL, name?: string, features?: string) {
    const smallest = typeof name === "string" ? ours.get(name) : undefined;
    if (smallest && typeof url === "string" && url.startsWith("about:blank?createflags=")) {
      url = url.replace(/createflags=(\d+)/, (_, n) => `createflags=${Number(n) | RESIZABLE}`);
      url += `&minwidth=${smallest.width}&minheight=${smallest.height}`;
    }
    return opener.call(this, url, name, features);
  } as typeof window.open;
  window.open = resizable;

  const pm = (window as any).g_PopupManager;
  let live = true;
  pm?.AddPopupCreatedCallback?.((popup: any) => {
    if (!live || !ours.has(popup?.m_strName)) return;
    const win: Window | undefined = popup.window;
    if (!win || win.closed) return;
    const style = win.document.createElement("style");
    style.textContent = FILL_CSS + (ours.get(popup.m_strName)?.css ?? "");
    win.document.head.appendChild(style);
    // its document gets its title once Decky has rendered into it
    window.setTimeout(() => {
      if (win.closed || win.document.title !== UNTITLED) return;
      win.document.title = "MusicDeck";
      const steamWindow = (win as any).SteamClient?.Window;
      steamWindow?.GetWindowDimensions?.().then((d: { width: number; height: number }) => {
        if (d && d.width < 560) steamWindow.ResizeTo(640, Math.max(d.height, 480), true);
      });
    }, 150);
  });
  // Steam keeps no way to take a callback back: it's made harmless instead
  return () => {
    live = false;
    if (window.open === resizable) window.open = opener;
  };
}

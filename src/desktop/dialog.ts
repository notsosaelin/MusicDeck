/**
 * MusicDeck's dialogs in desktop Steam: Decky's showModal, which there opens a window of its own, given a
 * title and a size (Steam's own dialogs say what they are in their title bar; Decky's said "Decky Dialog")
 * and asked to be resizable, as Steam's Settings is. Dialogs opened by code shared with Game Mode (the tag
 * editor, confirmations) get their title and size when they appear (followDialogs).
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
} satisfies Record<string, DialogSize>;

export function desktopDialog(node: ReactNode, size: DialogSize) {
  return showModal(node, window, {
    strTitle: size.title,
    popupWidth: size.width,
    popupHeight: size.height,
    // what Steam's own dialogs pass (its Settings: resizable, with a smallest size); not in Decky's typings
    ...({ resizable: true, minWidth: Math.min(size.width, 420), minHeight: Math.min(size.height, 220) } as object),
  });
}

/** While desktop Steam shows: a dialog Decky opened without a title ("Decky Dialog", from code shared with
 *  Game Mode) is named MusicDeck's and given room. Returns the teardown. */
export function followDialogs(): () => void {
  const pm = (window as any).g_PopupManager;
  if (!pm?.AddPopupCreatedCallback) return () => {};
  let live = true;
  pm.AddPopupCreatedCallback((popup: any) => {
    if (!live) return;
    // its document gets its title once Decky has rendered into it
    window.setTimeout(() => {
      const win: Window | undefined = popup?.window;
      if (!win || win.closed || win.document.title !== "Decky Dialog") return;
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
  };
}

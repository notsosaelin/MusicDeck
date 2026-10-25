/**
 * MusicDeck in desktop Steam: a Music tab in the top navigation (Store, Library, Music, Community), whose
 * section is drawn over the page area while it's chosen; and a mini player, a small window of its own,
 * while music plays and Music isn't showing. Media keys and the system's media controls drive the player.
 *
 * Decky manages none of this outside the gamepad UI, so it's started and torn down here as Steam
 * switches between desktop and Big Picture / Game Mode (SteamClient.UI.RegisterForUIModeChanged), and
 * put back when Steam draws its navigation again (a window closed to the tray and reopened).
 */
import { mediaUrl } from "../store";
import { player } from "../player";
import { Place, desktopActions, go, goBack, goForward } from "./data";
import { followDialogs } from "./dialog";
import { installTagTools, showOrganize, showTagTools } from "./tagtools";
import { installDesktopLookup } from "./identify";
import { catchDrops, loadSmart } from "./manage";
import { SoundtrackButton, TabMenu, currentRoute, shortcuts, showEqualizer, showSettings } from "./polish";
import { BASE_CSS, MusicView } from "./views";
import { MINI_SMALLEST, MiniPlayer, miniShape } from "./mini";
import { Root, TopNav, UI_MODE_DESKTOP, contentBounds, createRoot, findTopNav, fontFaces, mainWindow, pageBackground } from "./steamui";

/** how often the main window and its navigation are looked for (Steam draws them after it starts) */
const CHECK_MS = 1500;
/** its first size (the art, as Spotify's mini player opens), and the smallest: a strip of cover, names and controls */
const MINI = { width: 300, height: 300, minWidth: MINI_SMALLEST.width, minHeight: MINI_SMALLEST.height };
const MINI_PLACE = "musicdeck:mini-player";
const MINI_PIN = "musicdeck:mini-player-on-top";
// Steam's popup creation flags (EPopupCreationFlags, read from its own code): its main window's 274 is
// Hidden | Resizable | Composited, made visible later by Steam; a window of ours must not be Hidden
const RESIZABLE = 16;
const ALWAYS_ON_TOP = 8192;

const pinned = () => {
  try {
    return localStorage.getItem(MINI_PIN) === "1";
  } catch {
    return false;
  }
};

class MiniWindow {
  private win: Window | null = null;
  private root: Root | null = null;

  /** set while MusicDeck itself closes it (any other close is the user's) */
  private closing = false;

  constructor(private openMusic: () => void, private onDismissed: () => void) {}

  get isOpen() {
    return !!this.win && !this.win.closed;
  }

  get window(): Window | null {
    return this.isOpen ? this.win : null;
  }

  open(styleFrom: Document | null) {
    if (this.isOpen) return;
    let place = { x: 80, y: 80, width: MINI.width, height: MINI.height };
    try {
      place = { ...place, ...JSON.parse(localStorage.getItem(MINI_PLACE) || "{}") };
    } catch {}
    // a Steam window like its others (the news popup's kind): made by SharedJSContext, drawn by us
    const flags = RESIZABLE | (pinned() ? ALWAYS_ON_TOP : 0);
    const win = window.open(
      `about:blank?createflags=${flags}&minwidth=${MINI.minWidth}&minheight=${MINI.minHeight}`,
      "MusicDeck_MiniPlayer",
      `width=${place.width},height=${place.height},left=${place.x},top=${place.y}`,
    );
    if (!win) return;
    this.win = win;
    (win as any).SteamClient?.Window?.ShowWindow?.();
    const doc = win.document;
    doc.title = "MusicDeck";
    const style = doc.createElement("style");
    style.textContent = `${styleFrom ? fontFaces(styleFrom) : ""}${BASE_CSS}
      html, body { margin: 0; height: 100%; background: #10141a; overflow: hidden; user-select: none;
        font-family: "Motiva Sans", Arial, Helvetica, sans-serif; }
      #md-mini { height: 100%; }`;
    doc.head.appendChild(style);
    const host = doc.createElement("div");
    host.id = "md-mini";
    doc.body.appendChild(host);
    this.root = createRoot(host);
    this.root?.render(
      <MiniPlayer
        onClose={() => {
          this.close();
          this.onDismissed();
        }}
        onOpenMusic={this.openMusic}
        onTop={pinned()}
        onPin={() => {
          // on top is how the window is made: it's made again
          try {
            localStorage.setItem(MINI_PIN, pinned() ? "0" : "1");
          } catch {}
          this.close();
          this.open(styleFrom);
        }}
      />,
    );
    this.dragging(win);
    // where it was left (window.open's left/top aren't kept by every window manager)
    (win as any).SteamClient?.Window?.MoveTo(place.x, place.y, true);
    (win as any).SteamClient?.Window?.ResizeTo(place.width, place.height, true);
    // the size it's left at is kept with its place
    let resized = 0;
    win.addEventListener("resize", () => {
      win.clearTimeout(resized);
      resized = win.setTimeout(() => {
        // let go at a size that would crop the cover: it settles on the nearest whole one (miniShape)
        const [w, h] = miniShape(win.innerWidth, win.innerHeight);
        if (Math.abs(w - win.innerWidth) > 2 || Math.abs(h - win.innerHeight) > 2)
          (win as any).SteamClient?.Window?.ResizeTo(w, h, true);
        else this.remember(win);
      }, 500);
    });
    // closed some other way (the taskbar, Alt+F4): the user's choice too
    win.addEventListener("beforeunload", () => {
      const byUs = this.closing;
      this.forget();
      if (!byUs) this.onDismissed();
    });
  }

  /** It has no title bar: dragging anywhere but its controls moves it. Where it is comes from Steam
   *  (GetWindowDimensions): a Steam window's screenX/screenY don't follow it once it's moved. */
  private dragging(win: Window) {
    const steamWindow = (win as any).SteamClient?.Window;
    let drag: { pointer: [number, number]; window: [number, number] } | null = null;
    win.document.addEventListener("pointerdown", async (e) => {
      if ((e.target as Element).closest?.("button, .md-slider") || !steamWindow) return;
      (e.target as Element).setPointerCapture?.(e.pointerId);
      const pointer: [number, number] = [e.screenX, e.screenY];
      const at = await steamWindow.GetWindowDimensions();
      drag = { pointer, window: [at.x, at.y] };
    });
    win.document.addEventListener("pointermove", (e) => {
      if (drag) steamWindow.MoveTo(drag.window[0] + e.screenX - drag.pointer[0], drag.window[1] + e.screenY - drag.pointer[1], true);
    });
    win.document.addEventListener("pointerup", () => {
      if (!drag) return;
      drag = null;
      this.remember(win);
    });
  }

  /** where it is and how big, for next time */
  private async remember(win: Window) {
    if (win.closed) return;
    const at = await (win as any).SteamClient?.Window?.GetWindowDimensions?.();
    if (!at) return;
    try {
      localStorage.setItem(MINI_PLACE, JSON.stringify({ x: at.x, y: at.y, width: win.innerWidth, height: win.innerHeight }));
    } catch {}
  }

  private forget() {
    this.root?.unmount();
    this.root = null;
    this.win = null;
  }

  close() {
    const win = this.win;
    this.forget();
    this.closing = true;
    try {
      if (win && !win.closed) win.close();
    } finally {
      this.closing = false;
    }
  }
}

/** Media keys and the system's media controls (Windows' media overlay, MPRIS on Linux desktops). */
function mediaKeys(): () => void {
  const session = navigator.mediaSession;
  if (!session) return () => {};
  const actions: [MediaSessionAction, MediaSessionActionHandler][] = [
    ["play", () => !player.state.get().playing && player.toggle()],
    ["pause", () => player.state.get().playing && player.toggle()],
    ["nexttrack", () => player.next()],
    ["previoustrack", () => player.previous()],
    ["seekto", (d) => d.seekTime != null && player.seek(d.seekTime)],
  ];
  for (const [action, handler] of actions) {
    try {
      session.setActionHandler(action, handler);
    } catch {}
  }
  let shown = "";
  const update = () => {
    const item = player.current;
    const key = item ? `${item.instanceId}/${item.track.id}` : "";
    if (key !== shown) {
      shown = key;
      const cover = item?.track.art ? mediaUrl("art", item.instanceId, item.track.art) : undefined;
      session.metadata = item
        ? new MediaMetadata({
            title: item.track.title,
            artist: item.track.artist,
            album: item.track.album,
            artwork: cover ? [{ src: cover }] : [],
          })
        : null;
    }
    session.playbackState = !item ? "none" : player.state.get().playing ? "playing" : "paused";
  };
  update();
  const unsubscribe = player.state.subscribe(update);
  return () => {
    unsubscribe();
    for (const [action] of actions) {
      try {
        session.setActionHandler(action, null);
      } catch {}
    }
    session.metadata = null;
    session.playbackState = "none";
  };
}

/** The Music tab and its section, in one main window. */
class MusicTab {
  readonly tab: HTMLElement;
  private host: HTMLElement;
  private root: Root | null;
  private style: HTMLStyleElement;
  /** Steam's tab that was current before Music was chosen (its "current" look is lent to Music) */
  private lentFrom: HTMLElement | null = null;
  active = false;

  constructor(readonly win: Window, readonly nav: TopNav, private onLeave: () => void, private onShow: () => void) {
    const doc = win.document;
    // a copy of Steam's own tab: its look, hover and spacing are Steam's
    const model = nav.tabs[Math.min(2, nav.tabs.length - 1)];
    this.tab = model.cloneNode(true) as HTMLElement;
    this.tab.dataset.musicdeck = "tab";
    this.tab.classList.remove(nav.selectedClass);
    let leaf: Element = this.tab;
    while (leaf.firstElementChild) leaf = leaf.firstElementChild;
    leaf.textContent = "Music";
    this.tab.addEventListener("click", this.show);
    nav.row.insertBefore(this.tab, model);
    nav.row.addEventListener("click", this.leaveOnOtherTab, true);

    this.style = doc.createElement("style");
    this.style.textContent = BASE_CSS;
    doc.head.appendChild(this.style);
    this.host = doc.createElement("div");
    this.host.dataset.musicdeck = "music";
    this.host.style.cssText = "position: fixed; left: 0; right: 0; z-index: 50; display: none; overflow: hidden;";
    doc.body.appendChild(this.host);
    this.root = createRoot(this.host);
    this.root?.render(<MusicView />);
    win.addEventListener("resize", this.place);

    const openAt = (place: Place) => {
      this.show();
      go(place);
    };
    this.menu = new TabMenu(win, this.tab, openAt);
    this.soundtrack = new SoundtrackButton(win, nav, () => this.active, openAt);
    this.stopShortcuts = shortcuts(win, () => this.active);
    this.stopDrops = catchDrops(win, this.host, () => this.active);
  }

  private routeWatch = 0;
  private keepCurrent = new MutationObserver(() => {
    const { selectedClass } = this.nav;
    for (const t of this.nav.tabs) if (t.classList.contains(selectedClass)) t.classList.remove(selectedClass);
  });
  private menu: TabMenu;
  private soundtrack: SoundtrackButton;
  private stopShortcuts: () => void;
  private stopDrops: () => void;

  get connected() {
    return this.tab.isConnected && !this.win.closed;
  }

  private place = () => {
    const { top, bottom } = contentBounds(this.win, this.nav);
    Object.assign(this.host.style, { top: `${top}px`, bottom: `${bottom}px` });
  };

  show = () => {
    this.menu?.hide();
    if (this.active) return;
    // Steam's web pages (the store, Community, Help's pages) are browser views drawn over the window, above
    // anything in it: Steam goes back to its library first, which closes them, and Music shows over that
    if (!currentRoute().startsWith("/library")) {
      (window as any).SteamClient?.URL?.ExecuteSteamURL?.("steam://nav/games");
      this.win.setTimeout(this.show, 250);
      return;
    }
    this.active = true;
    const { selectedClass } = this.nav;
    if (selectedClass) {
      this.lentFrom = this.nav.tabs.find((t) => t.classList.contains(selectedClass)) ?? null;
      this.lentFrom?.classList.remove(selectedClass);
      this.tab.classList.add(selectedClass);
      // Steam draws its tabs again on hover (Library's menu), and marks Library current again: not while
      // Music is
      this.keepCurrent.observe(this.nav.row, { attributes: true, attributeFilter: ["class"], subtree: true });
    }
    // Steam going to a web page itself (Help's pages, a store link, the Steam menu) while Music shows: its
    // browser view would be drawn over Music, so Music steps aside
    this.win.clearInterval(this.routeWatch);
    this.routeWatch = this.win.setInterval(() => {
      if (this.active && !currentRoute().startsWith("/library")) this.hide();
    }, 400);
    this.place();
    this.host.style.background = pageBackground(this.win, contentBounds(this.win, this.nav).top);
    this.host.style.display = "block";
    this.onShow();
  };

  hide = () => {
    if (!this.active) return;
    this.active = false;
    this.keepCurrent.disconnect();
    this.win.clearInterval(this.routeWatch);
    const { selectedClass } = this.nav;
    if (selectedClass) {
      this.tab.classList.remove(selectedClass);
      if (this.lentFrom?.isConnected && !this.nav.tabs.some((t) => t.classList.contains(selectedClass)))
        this.lentFrom.classList.add(selectedClass);
    }
    this.lentFrom = null;
    this.host.style.display = "none";
    this.onLeave();
  };

  /** Store, Library, Community: Steam goes there, and Music steps aside first. Steam's back and forward
   *  arrows go back and forward in Music while it has somewhere to go (and back out of it after). */
  private leaveOnOtherTab = (e: Event) => {
    if (!this.active || this.tab.contains(e.target as Node)) return;
    const [back, forward] = this.nav.row.children;
    const target = e.target as Node;
    if ((back?.contains(target) && goBack()) || (forward?.contains(target) && goForward())) {
      e.stopPropagation();
      e.preventDefault();
      return;
    }
    this.hide();
  };

  remove() {
    if (this.active) this.hide();
    this.menu.remove();
    this.soundtrack.remove();
    this.stopShortcuts();
    this.stopDrops();
    this.win.removeEventListener("resize", this.place);
    this.nav.row.removeEventListener("click", this.leaveOnOtherTab, true);
    this.root?.unmount();
    this.root = null;
    this.tab.remove();
    this.host.remove();
    this.style.remove();
  }
}

/** Desktop Steam, from when it shows until it goes (Big Picture, Game Mode, MusicDeck unloaded). */
class Desktop {
  private music: MusicTab | null = null;
  private mini = new MiniWindow(
    () => this.openMusic(),
    () => {
      this.miniDismissed = true;
      this.miniByHand = false;
    },
  );
  /** opened with the player bar's button: it stays, with Music showing too, until it's closed */
  private miniByHand = false;
  private stopDialogs = followDialogs();
  private stopLookup = installDesktopLookup();
  private stopTagTools = installTagTools();
  private timer = window.setInterval(() => this.check(), CHECK_MS);
  private stopKeys = mediaKeys();
  private unsubscribe = player.state.subscribe(() => this.followPlayer());
  /** the mini player was closed by hand: it stays closed until Music is left again */
  private miniDismissed = false;

  constructor() {
    desktopActions.toggleMini = () => this.toggleMini();
    this.check();
    // music already playing when desktop Steam shows: it's in the mini player
    if (this.playing) this.mini.open(mainWindow()?.document ?? null);
  }

  private get playing() {
    return player.state.get().index >= 0 && player.state.get().playing;
  }

  /** The tab put in (again) whenever the main window shows its navigation without it. */
  private check() {
    if (this.music?.connected) return;
    this.music?.remove();
    this.music = null;
    const win = mainWindow();
    const nav = win && findTopNav(win.document);
    if (!win || !nav) return;
    try {
      this.music = new MusicTab(win, nav, () => this.left(), () => this.shown());
    } catch (e) {
      console.warn("[MusicDeck] Couldn't add the Music tab", e);
    }
  }

  private openMusic() {
    const win = mainWindow();
    (win as any)?.SteamClient?.Window?.BringToFront?.();
    this.check();
    this.music?.show();
  }

  private shown() {
    if (!this.miniByHand) this.mini.close();
  }

  /** The player bar's button: the mini player opened (or closed) by hand. */
  toggleMini() {
    if (this.mini.isOpen) {
      this.mini.close();
      this.miniByHand = false;
      return;
    }
    this.miniByHand = true;
    this.miniDismissed = false;
    this.mini.open(this.music?.win.document ?? mainWindow()?.document ?? null);
  }

  /** Music left while something plays: the mini player takes over. */
  private left() {
    this.miniDismissed = false;
    if (this.playing) this.mini.open(this.music?.win.document ?? null);
  }

  private followPlayer() {
    if (player.state.get().index < 0 && !this.miniByHand) {
      this.mini.close();
      this.miniDismissed = false;
    } else if (this.playing && !this.miniDismissed && !this.mini.isOpen && !this.music?.active) {
      // playback started from elsewhere (the Quick Access Menu, media keys) while Music isn't showing
      this.mini.open(this.music?.win.document ?? mainWindow()?.document ?? null);
    }
  }

  stop() {
    window.clearInterval(this.timer);
    this.stopDialogs();
    this.stopLookup();
    this.stopTagTools();
    desktopActions.toggleMini = () => {};
    this.unsubscribe();
    this.stopKeys();
    this.music?.remove();
    this.music = null;
    this.mini.close();
  }

  /** for the debugger (window.__musicdeck.desktop): drive what the user would, without the mouse */
  readonly debug = {
    showMusic: () => this.openMusic(),
    hideMusic: () => this.music?.hide(),
    openMini: () => this.mini.open(mainWindow()?.document ?? null),
    closeMini: () => this.mini.close(),
    reloadSmart: () => loadSmart(),
    showSettings: () => showSettings(),
    showTools: (source?: string) => go({ type: "tools", source }),
    tagTools: (source: string, ids: string[]) => showTagTools(source, ids, `${ids.length} songs`),
    organize: (source: string, ids?: string[]) => showOrganize(source, ids, ids ? `${ids.length} songs` : "Every song"),
    showEqualizer: () => showEqualizer(),
    mini: () => this.mini.window,
    state: () => ({ tab: !!this.music?.connected, musicShowing: !!this.music?.active, miniOpen: this.mini.isOpen,
                    miniDismissed: this.miniDismissed }),
  };
}

/** The desktop parts while they're up, for the debugger. */
export const desktopDebug = { current: null as Desktop["debug"] | null };

/** Follows Steam's UI mode: MusicDeck's desktop parts while it's desktop Steam. Returns the teardown. */
export function startDesktop(): () => void {
  let desktop: Desktop | null = null;
  const leave = () => {
    desktop?.stop();
    desktop = null;
    desktopDebug.current = null;
  };
  const registration = (window as any).SteamClient?.UI?.RegisterForUIModeChanged?.((mode: number) => {
    if (mode === UI_MODE_DESKTOP) {
      desktop ??= new Desktop();
      desktopDebug.current = desktop.debug;
    } else leave();
  });
  return () => {
    registration?.unregister?.();
    leave();
  };
}

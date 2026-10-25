/**
 * The finishing touches of desktop Steam's Music: a Play soundtrack button on a game's page when the account
 * owns its soundtrack; the Music tab's menu on hover (as Store's and Community's have); keyboard shortcuts and
 * the mouse's back and forward buttons; a small visualizer; the equalizer and MusicDeck's settings as dialogs.
 */
import { DialogBody, DialogHeader, ModalRoot, showModal } from "@decky/ui";
import { FC, useEffect, useRef } from "react";
import * as api from "../api";
import { Equalizer } from "../components/Equalizer";
import { Settings } from "../components/Settings";
import { playFrom } from "../nav";
import { notify } from "../notify";
import { player } from "../player";
import { switchSource } from "../sources";
import { pluginState, useStore } from "../store";
import { Place, goBack, goForward, panel } from "./data";
import { TopNav, contentBounds } from "./steamui";

// --- a game's soundtrack, from its page ---------------------------------------------------------------

const APP_TYPE_SOUNDTRACK = 0x2000;

/** The soundtrack the account owns for a game (Steam's own stores: a soundtrack's parent is its game). */
export function soundtrackFor(gameId: number): { appid: number; name: string } | null {
  const apps: any[] = (window as any).collectionStore?.allAppsCollection?.allApps ?? [];
  const found = apps.find((a) => a.app_type === APP_TYPE_SOUNDTRACK && a.optional_parent_app_id === gameId);
  return found ? { appid: found.appid, name: found.display_name } : null;
}

/** The main window's page ("/library/app/570"), from desktop Steam's own window store. */
export function currentRoute(): string {
  const wins = (window as any).SteamUIStore?.WindowStore?.SteamUIWindows ?? [];
  return wins[0]?.m_history?.location?.pathname ?? "";
}

export async function playSoundtrack(appid: number, openMusic: (place: Place) => void) {
  const steam = pluginState.get()?.services.find((s) => s.type === "steam");
  if (!steam) {
    notify("MusicDeck", "Add the Steam soundtracks source in Decky → MusicDeck to play soundtracks here");
    return;
  }
  if (pluginState.get()?.active_service !== steam.id) await switchSource(steam.id);
  const album: api.Collection = { id: String(appid), kind: "album", title: "", subtitle: "", art: `c:${appid}` };
  const page = await api.openCollection(steam.id, "album", album.id);
  if (page.ok && page.data.tracks.length) playFrom(steam.id, { ...album, title: page.data.title }, page.data.tracks, 0);
  // not installed yet: its page, with Steam's Install
  else openMusic({ type: "collection", collection: { ...album, title: page.ok ? page.data.title : "Soundtrack" } });
}

/** The button on a game's page, while it shows (and Music doesn't). */
export class SoundtrackButton {
  private el: HTMLButtonElement;
  private timer: number;
  private shownFor = 0;

  constructor(private win: Window, private nav: TopNav, private musicShowing: () => boolean, openMusic: (p: Place) => void) {
    this.el = win.document.createElement("button");
    this.el.className = "md-soundtrack";
    this.el.dataset.musicdeck = "soundtrack";
    this.el.style.display = "none";
    this.el.addEventListener("click", () => this.shownFor && playSoundtrack(this.shownFor, openMusic));
    win.document.body.appendChild(this.el);
    this.timer = win.setInterval(() => this.check(), 800);
  }

  private check() {
    const m = /^\/library\/app\/(\d+)/.exec(currentRoute());
    const soundtrack = m && !this.musicShowing() ? soundtrackFor(Number(m[1])) : null;
    if (!soundtrack) {
      this.el.style.display = "none";
      this.shownFor = 0;
      return;
    }
    if (this.shownFor !== soundtrack.appid) {
      this.shownFor = soundtrack.appid;
      this.el.textContent = "♪ Play soundtrack";
      this.el.title = soundtrack.name;
    }
    const { top } = contentBounds(this.win, this.nav);
    Object.assign(this.el.style, { display: "block", top: `${top + 14}px` });
  }

  remove() {
    this.win.clearInterval(this.timer);
    this.el.remove();
  }
}

// --- the Music tab's menu, on hover -----------------------------------------------------------------

export class TabMenu {
  private el: HTMLDivElement;
  private hideTimer = 0;

  constructor(private win: Window, private tab: HTMLElement, private open: (place: Place) => void) {
    const doc = win.document;
    this.el = doc.createElement("div");
    this.el.className = "md-supernav";
    this.el.dataset.musicdeck = "supernav";
    this.el.style.display = "none";
    doc.body.appendChild(this.el);
    tab.addEventListener("mouseenter", this.show);
    tab.addEventListener("mouseleave", this.later);
    this.el.addEventListener("mouseenter", () => this.win.clearTimeout(this.hideTimer));
    this.el.addEventListener("mouseleave", this.later);
  }

  private show = () => {
    this.win.clearTimeout(this.hideTimer);
    const source = pluginState.get()?.services.find((s) => s.id === pluginState.get()?.active_service);
    const entries: [string, Place][] = [["Home", { type: "home" }]];
    for (const t of (source?.tabs ?? []).filter((t) => (t.group ?? "yours") === "yours" && t.title.toLowerCase() !== "home").slice(0, 5))
      entries.push([t.title, { type: "tab", id: t.id, title: t.title }]);
    this.el.replaceChildren(
      ...entries.map(([label, place]) => {
        const item = this.win.document.createElement("div");
        item.className = "md-supernav-item";
        item.textContent = label;
        item.addEventListener("click", () => {
          this.hide();
          this.open(place);
        });
        return item;
      }),
    );
    const r = this.tab.getBoundingClientRect();
    Object.assign(this.el.style, { display: "block", left: `${r.left}px`, top: `${r.bottom + 2}px` });
  };

  private later = () => {
    this.win.clearTimeout(this.hideTimer);
    this.hideTimer = this.win.setTimeout(() => this.hide(), 250);
  };

  hide = () => {
    this.el.style.display = "none";
  };

  remove() {
    this.win.clearTimeout(this.hideTimer);
    this.tab.removeEventListener("mouseenter", this.show);
    this.tab.removeEventListener("mouseleave", this.later);
    this.el.remove();
  }
}

// --- the keyboard and the mouse's side buttons, while Music shows -----------------------------------

const typing = (target: EventTarget | null) => {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
};

/** Space play/pause, Ctrl+←/→ previous/next, Ctrl+↑/↓ volume, Ctrl+F search, Alt+←/→ and the mouse's
 *  side buttons back and forward, Ctrl+L lyrics, Ctrl+Q queue. Only while Music shows. */
export function shortcuts(win: Window, active: () => boolean): () => void {
  const keys = (e: KeyboardEvent) => {
    if (!active() || typing(e.target)) return;
    const ctrl = e.ctrlKey || e.metaKey;
    let handled = true;
    if (e.key === " " && !ctrl) player.toggle();
    else if (ctrl && e.key === "ArrowRight") player.next();
    else if (ctrl && e.key === "ArrowLeft") player.previous();
    else if (ctrl && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      const v = Math.max(0, Math.min(1, player.state.get().volume + (e.key === "ArrowUp" ? 0.05 : -0.05)));
      player.setVolume(v);
      api.setSetting("volume", v).catch(() => {});
    } else if (ctrl && e.key.toLowerCase() === "f") (win.document.querySelector(".md-search input") as HTMLInputElement | null)?.focus();
    else if (ctrl && e.key.toLowerCase() === "l") panel.set({ open: panel.get().open === "lyrics" ? null : "lyrics" });
    else if (ctrl && e.key.toLowerCase() === "q") panel.set({ open: panel.get().open === "queue" ? null : "queue" });
    else if (e.altKey && e.key === "ArrowLeft") goBack();
    else if (e.altKey && e.key === "ArrowRight") goForward();
    else handled = false;
    if (handled) {
      e.preventDefault();
      e.stopPropagation();
    }
  };
  const mouse = (e: MouseEvent) => {
    if (!active() || (e.button !== 3 && e.button !== 4)) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.button === 3) goBack();
    else goForward();
  };
  win.addEventListener("keydown", keys, true);
  win.addEventListener("mouseup", mouse, true);
  return () => {
    win.removeEventListener("keydown", keys, true);
    win.removeEventListener("mouseup", mouse, true);
  };
}

// --- the visualizer ---------------------------------------------------------------------------------

const BARS = 24;

/** Bars of what's playing, drawn only while it plays (one requestAnimationFrame loop, 24 bars). */
export const Visualizer: FC<{ width?: number; height?: number }> = ({ width = 96, height = 26 }) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  const playing = useStore(player.state).playing;
  useEffect(() => {
    const c = canvas.current;
    const g = c?.getContext("2d");
    if (!c || !g) return;
    const win = c.ownerDocument.defaultView ?? window;
    const bins = new Uint8Array(64);
    let frame = 0;
    const draw = () => {
      g.clearRect(0, 0, width, height);
      if (player.levels(bins)) {
        const w = width / BARS;
        for (let i = 0; i < BARS; i++) {
          const v = bins[Math.floor((i / BARS) ** 1.6 * bins.length)] / 255;
          g.fillStyle = `rgba(26,159,255,${0.35 + v * 0.65})`;
          g.fillRect(i * w + 1, height - v * height, w - 2, v * height);
        }
      }
      if (playing) frame = win.requestAnimationFrame(draw);
    };
    draw();
    return () => win.cancelAnimationFrame(frame);
  }, [playing, width, height]);
  return <canvas ref={canvas} width={width} height={height} style={{ width, height }} aria-hidden />;
};

// --- dialogs ----------------------------------------------------------------------------------------

export const showEqualizer = () =>
  showModal(
    <ModalRoot>
      <DialogHeader>Equalizer</DialogHeader>
      <DialogBody>
        <Equalizer />
      </DialogBody>
    </ModalRoot>,
  );

export const showSettings = () =>
  showModal(
    <ModalRoot>
      <DialogHeader>MusicDeck</DialogHeader>
      <DialogBody>
        <Settings />
      </DialogBody>
    </ModalRoot>,
  );

export const POLISH_CSS = `
.md-soundtrack { position: fixed; right: 28px; z-index: 40; height: 36px; padding: 0 18px; border: none; border-radius: 2px;
  background: rgba(23,29,37,0.85); color: #dcdedf; font: inherit; font-size: 14px; cursor: pointer;
  box-shadow: 0 2px 10px rgba(0,0,0,0.5); backdrop-filter: blur(6px); }
.md-soundtrack:hover { background: linear-gradient(to right, #75b022 5%, #588a1b 95%); color: #fff; }
.md-supernav { position: fixed; z-index: 60; min-width: 180px; padding: 6px 0; background: #3d4450;
  box-shadow: 0 6px 18px rgba(0,0,0,0.5); }
.md-supernav-item { padding: 7px 16px; color: #dcdedf; font-size: 13px; cursor: pointer; }
.md-supernav-item:hover { background: #dcdedf; color: #171d25; }
`;

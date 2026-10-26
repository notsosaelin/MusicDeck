import { setSetting } from "./api";
import { openMediaCenter } from "./nav";
import { pluginState } from "./store";

/**
 * MusicDeck in the Steam library (Settings → Show in your Library): a non-Steam shortcut whose Play
 * opens the media center, so it can be started from Big Picture's library like a game, not only from
 * the QAM. Steam runs nothing: the launch is caught as it starts (RegisterForGameActionStart) and
 * cancelled. The shortcut's program is a harmless stand-in (true, or cmd /c exit on Windows) in case a
 * launch slips past. Its artwork is drawn here, in the media center's turntable look.
 */

const NAME = "MusicDeck";
const ASSET = { grid: 0, hero: 1, logo: 2, wide: 3 } as const;
const BLUE = "#1a9fff";
// how long Steam takes to settle on the game's page once a launch starts (tried on a Deck: 400 ms is enough)
const LAUNCH_SETTLE_MS = 400;

type LibraryEntry = { enabled: boolean; appid: number };

const steamApps = (): any => (window as any).SteamClient?.Apps;

function standIn(platform: string | undefined): { exe: string; dir: string; options: string } {
  return platform?.startsWith("windows")
    ? { exe: "C:\\Windows\\System32\\cmd.exe", dir: "C:\\Windows\\System32", options: "/c exit" }
    : { exe: "/usr/bin/true", dir: "/usr/bin", options: "" };
}

// EAppType for a non-Steam shortcut
const SHORTCUT_TYPE = 0x40000000;
// how long to wait at most for Steam's library to have loaded its shortcuts
const LIBRARY_WAIT_MS = 20_000;

/** Every MusicDeck shortcut in the library (there should be one; earlier versions could leave several), once Steam has
 *  loaded its apps; null when it never did (nothing's decided then). */
async function ourShortcuts(): Promise<number[] | null> {
  for (let waited = 0; waited <= LIBRARY_WAIT_MS; waited += 500) {
    const map = (window as any).appStore?.m_mapApps;
    if (map?.size) {
      return [...map.values()]
        .filter((a: any) => a?.app_type === SHORTCUT_TYPE && a?.display_name === NAME)
        .map((a: any) => a.appid as number);
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return null;
}

/** A record at rest, the media center's turntable: drawn at (x, y), radius r. */
function drawRecord(ctx: CanvasRenderingContext2D, x: number, y: number, r: number) {
  const shine = ctx.createRadialGradient(x - r * 0.3, y - r * 0.4, r * 0.1, x, y, r);
  shine.addColorStop(0, "#2a3340");
  shine.addColorStop(1, "#0a0d11");
  ctx.fillStyle = shine;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "rgba(255, 255, 255, 0.07)";
  ctx.lineWidth = Math.max(1, r / 90);
  for (let g = 0.9; g > 0.42; g -= 0.1) {
    ctx.beginPath();
    ctx.arc(x, y, r * g, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.fillStyle = BLUE;
  ctx.beginPath();
  ctx.arc(x, y, r * 0.36, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#0e141b";
  ctx.beginPath();
  ctx.arc(x, y, r * 0.04, 0, Math.PI * 2);
  ctx.fill();
}

function canvas(width: number, height: number, background = true): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement("canvas");
  c.width = width;
  c.height = height;
  const ctx = c.getContext("2d")!;
  if (background) {
    const bg = ctx.createLinearGradient(0, 0, width, height);
    bg.addColorStop(0, "#1b2838");
    bg.addColorStop(1, "#0b1018");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);
  }
  return [c, ctx];
}

function title(ctx: CanvasRenderingContext2D, x: number, y: number, size: number, align: CanvasTextAlign = "center") {
  ctx.font = `700 ${size}px "Motiva Sans", Arial, sans-serif`;
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  ctx.fillStyle = "#ffffff";
  ctx.fillText(NAME, x, y);
}

const png = (c: HTMLCanvasElement) => c.toDataURL("image/png").split(",")[1];

/** Its cover (600×900), hero (1920×620), logo and wide cover (920×430), as base64 PNGs. */
function artwork(): Record<keyof typeof ASSET, string> {
  const [grid, g] = canvas(600, 900);
  drawRecord(g, 300, 380, 230);
  title(g, 300, 760, 76);
  const [hero, h] = canvas(1920, 620);
  drawRecord(h, 1500, 310, 260);
  const [logo, l] = canvas(1280, 360, false);
  title(l, 640, 180, 150);
  const [wide, w] = canvas(920, 430);
  drawRecord(w, 690, 215, 165);
  title(w, 70, 215, 84, "left");
  return { grid: png(grid), hero: png(hero), logo: png(logo), wide: png(wide) };
}

async function setArtwork(apps: any, appid: number) {
  const art = artwork();
  for (const [kind, data] of Object.entries(art) as [keyof typeof ASSET, string][]) {
    try {
      await apps.SetCustomArtworkForApp?.(appid, data, "png", ASSET[kind]);
    } catch (e) {
      console.warn("[MusicDeck] Library entry artwork", kind, e);
    }
  }
}

let syncing: Promise<void> | null = null;

/** Adds or removes the Library entry to match the setting (at start, and when it's switched): one entry, with its
 *  artwork; any more of them (left by earlier versions, which could add one before Steam had loaded its library) go.
 *  One sync at a time: a second asked for meanwhile waits for it. */
export function syncLibraryEntry(): Promise<void> {
  syncing ??= sync().finally(() => {
    syncing = null;
  });
  return syncing;
}

async function sync(): Promise<void> {
  const state = pluginState.get();
  const apps = steamApps();
  if (!state || !apps?.AddShortcut) return;
  const entry: LibraryEntry = state.library_entry ?? { enabled: false, appid: 0 };
  const ours = await ourShortcuts();
  if (ours === null) return; // Steam's library never loaded: nothing is added or removed on a guess
  if (!entry.enabled) {
    for (const appid of ours) apps.RemoveShortcut?.(appid);
    if (entry.appid) await setSetting("library_entry", { enabled: false, appid: 0 });
    return;
  }
  const keep = ours.includes(entry.appid) ? entry.appid : ours[0];
  const extra = ours.filter((a) => a !== keep);
  for (const appid of extra) apps.RemoveShortcut?.(appid);
  if (keep) {
    // the one kept may be one made without its pictures: given them again when anything was off
    if (keep !== entry.appid || extra.length) {
      await setArtwork(apps, keep);
      await setSetting("library_entry", { enabled: true, appid: keep });
    }
    return;
  }
  const { exe, dir, options } = standIn(state.platform);
  const appid: number = await apps.AddShortcut(NAME, exe, dir, options);
  if (!appid) return;
  // set one by one: what AddShortcut itself takes differs between Steam versions
  apps.SetShortcutName?.(appid, NAME);
  apps.SetShortcutExe?.(appid, exe);
  apps.SetShortcutStartDir?.(appid, dir);
  apps.SetShortcutLaunchOptions?.(appid, options);
  await setArtwork(apps, appid);
  await setSetting("library_entry", { enabled: true, appid });
}

/** Whether a launch is the Library entry's: Steam gives a shortcut's 64-bit game id (its app id << 32,
 * flagged as a shortcut), not its app id. */
function isEntry(launched: string | number, appid: number): boolean {
  if (!appid) return false;
  try {
    const id = BigInt(String(launched));
    return id === BigInt(appid) || id >> BigInt(32) === BigInt(appid);
  } catch {
    return false;
  }
}

/** Play on the Library entry opens the media center (and Steam launches nothing). */
export function watchLibraryEntry(): () => void {
  const registration = steamApps()?.RegisterForGameActionStart?.(
    (gameActionId: number, appId: string | number, action: string) => {
      const entry = pluginState.get()?.library_entry;
      if (!entry?.enabled || !isEntry(appId, entry.appid) || action !== "LaunchApp") return;
      steamApps()?.CancelGameAction?.(gameActionId);
      // a moment later: Steam shows the game's page as a launch starts, over a page opened at once
      window.setTimeout(() => openMediaCenter(), LAUNCH_SETTLE_MS);
    },
  );
  return () => registration?.unregister?.();
}

import { Collection, GameMusic, openCollection, setGameMusic } from "./api";
import { playFrom } from "./nav";
import { notify } from "./notify";
import { player } from "./player";
import { refreshRunningGame } from "./steam";
import { Store, pluginState, refreshState } from "./store";

/**
 * Games starting and stopping, from Steam's app lifetime notifications: they start the music set to
 * play with a game, pause what's playing while a game runs (if that's turned on), and keep the Steam
 * soundtracks source's "From <game>" row current. Which app is in front, from gamescope's focus
 * changes, lowers the music while it's a game (if that's turned on).
 */

// EAppType values that are something you play (tools and runtimes, like Proton, also start and stop)
const PLAYABLE_TYPES = new Set([1 /* game */, 8 /* demo */, 0x40000000 /* non-Steam shortcut */]);

/** The game in the foreground, or null; what game music is set for. */
export const runningGame = new Store<{ appid: number; name: string } | null>(null);

const gameName = (appid: number) => window.appStore?.GetAppOverviewByAppID(appid)?.display_name ?? "";

const isPlayable = (appid: number) => {
  const type = window.appStore?.GetAppOverviewByAppID(appid)?.app_type;
  return type !== undefined && PLAYABLE_TYPES.has(type);
};

function readRunningGame() {
  const appid = window.SteamUIStore?.MainRunningAppID;
  return appid ? { appid, name: gameName(appid) } : null;
}

/** An explicit source checks its own link; launching uses the latest link across configured sources. */
export function musicFor(appid: number, instanceId?: string): GameMusic | undefined {
  const state = pluginState.get();
  return state?.game_music.slice().reverse().find((m) =>
    m.appid === appid && (instanceId !== undefined
      ? m.instance_id === instanceId
      : state.services.some((s) => s.id === m.instance_id)),
  );
}

/**
 * What games did to playback, so they can undo just that: game music a game started stops with it;
 * music paused for games resumes once none is running. Not if anything was picked, played or paused by
 * hand since (the queue or track changed, or it's no longer in the state the game left it in).
 */
let started: { appid: number; queue: number } | null = null;
let paused: { queue: number; index: number } | null = null;

const PAUSE_FADE_SECONDS = 1;

async function onGameStart(appid: number) {
  const music = musicFor(appid);
  if (!music) {
    if (pluginState.get()?.playback.game_pause && player.state.get().playing) {
      paused = { queue: player.queueId, index: player.state.get().index };
      void player.fadeOutAndPause(PAUSE_FADE_SECONDS);
    }
    return;
  }
  const queue = player.queueId;
  const page = await openCollection(music.instance_id, music.collection.kind, music.collection.id);
  // Don't start a stale request after the game closes, the link changes, or the user picks music.
  const current = musicFor(appid);
  if (!known.has(appid) || player.queueId !== queue || current?.instance_id !== music.instance_id ||
      current?.collection.id !== music.collection.id || current?.collection.kind !== music.collection.kind) return;
  if (!page.ok || !page.data.tracks.length) {
    notify(`Game music for ${music.game}`, page.ok ? `${music.collection.title} is empty` : page.error);
    return;
  }
  playFrom(music.instance_id, music.collection, page.data.tracks);
  started = { appid, queue: player.queueId };
  notify(`Playing for ${music.game}`, music.collection.title);
}

function onGameExit(appid: number) {
  if (started?.appid !== appid) return;
  if (player.queueId === started.queue && player.state.get().playing) player.toggle();
  started = null;
}

/** The last game closed: music paused for games picks up again, if it's still as they left it. */
function onAllGamesClosed() {
  const { playing, index } = player.state.get();
  if (paused && player.queueId === paused.queue && index === paused.index && !playing) player.toggle();
  paused = null;
}

/** The games running at the last check. */
let known = new Set<number>();

const runningGames = (): number[] => (window.SteamUIStore?.RunningApps ?? []).map((a) => a.appid);

/**
 * Compares the games running now with the last check: new ones started, missing ones stopped.
 * Steam's notifications only say *that* something changed; their process ids don't reliably pair a
 * start with its stop (a game can stop under another id than it started with), and a game can report
 * several processes. So the running list decides. Exported for the debug handle, to try launches
 * without launching anything.
 */
export function reconcile(running: number[]) {
  const now = new Set(running.filter(isPlayable));
  for (const appid of now) if (!known.has(appid)) void onGameStart(appid);
  for (const appid of known) if (!now.has(appid)) onGameExit(appid);
  if (known.size && !now.size) onAllGamesClosed();
  known = now;
  runningGame.set(readRunningGame());
  void refreshRunningGame();
}

/** The app in front, as gamescope reports it: a game's id, or Steam's own (769) over a game or with none */
let focusedApp = 0;

/**
 * Music under a game in front plays at the share of its volume set in Settings (playback.game_duck,
 * 1 = as is), and comes back when Steam is in front again. Exported for the debug handle, to try it
 * without launching anything: games.duckFor(570), then games.duckFor(769).
 */
export function duckFor(appid: number) {
  focusedApp = appid;
  applyDuck();
}

function applyDuck() {
  const share = pluginState.get()?.playback.game_duck ?? 1;
  const game = focusedApp > 0 && (known.has(focusedApp) || isPlayable(focusedApp));
  player.setDuck(game ? share : 1);
}

/** Listens for games starting and stopping, and coming to the front. Returns the unregister function. */
export function watchGames(): () => void {
  // games already running when the plugin loads didn't just start: no music for them
  known = new Set(runningGames().filter(isPlayable));
  runningGame.set(readRunningGame());
  const registration = window.SteamClient?.GameSessions?.RegisterForAppLifetimeNotifications?.(() => {
    // Steam's running list updates just after the notification, sometimes a few seconds after
    for (const delay of [1000, 5000]) window.setTimeout(() => reconcile(runningGames()), delay);
  });
  // tells the app in front at once, then on every change
  const focus = window.SteamClient?.System?.UI?.RegisterForFocusChangeEvents?.((e) => duckFor(e?.focusedApp?.appid ?? 0));
  // the setting changing applies straight away
  const unfollow = pluginState.subscribe(applyDuck);
  return () => {
    registration?.unregister();
    focus?.unregister();
    unfollow();
    player.setDuck(1);
  };
}

/** Sets the album or playlist to play with the running game, or unsets it if it's the one already set. */
export async function toggleGameMusic(instanceId: string, c: Collection) {
  const game = runningGame.get();
  if (!game) return;
  const on = musicFor(game.appid, instanceId)?.collection.id !== c.id;
  const result = await setGameMusic(instanceId, game.appid, game.name, on ? c : null);
  if (!result.ok) return notify("Game music", result.error);
  await refreshState();
  notify(on ? `Plays when ${game.name} starts` : `No longer plays with ${game.name}`, c.title);
}

/** Album and playlist pages can be set to play with a game (see GAME_MUSIC_KINDS in main.py). */
export const canPlayWithGame = (c: Collection) => c.kind === "album" || c.kind === "playlist";

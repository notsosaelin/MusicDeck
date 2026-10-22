import { Collection, GameMusic, openCollection, setGameMusic } from "./api";
import { notify } from "./notify";
import { player } from "./player";
import { refreshRunningGame } from "./steam";
import { Store, pluginState, refreshState } from "./store";

/**
 * Games starting and stopping, from Steam's app lifetime notifications: they start the music set to
 * play with a game, and keep the Steam soundtracks source's "From <game>" row current.
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

/** The music set for a game on a source (the live one unless given; only the live source's choices play). */
export function musicFor(appid: number, instanceId = pluginState.get()?.active_service): GameMusic | undefined {
  return pluginState.get()?.game_music.find((m) => m.instance_id === instanceId && m.appid === appid);
}

/** What game music started, so stopping the game can stop it (but not anything chosen since). */
let started: { appid: number; queue: number } | null = null;

async function onGameStart(appid: number) {
  const live = pluginState.get()?.active_service;
  const music = live ? musicFor(appid, live) : undefined;
  if (!live || !music) return;
  const page = await openCollection(live, music.collection.kind, music.collection.id);
  // switched source while it loaded: it isn't the live source's music any more
  if (pluginState.get()?.active_service !== live) return;
  if (!page.ok || !page.data.tracks.length) {
    notify(`Game music for ${music.game}`, page.ok ? `${music.collection.title} is empty` : page.error);
    return;
  }
  player.playList(live, page.data.tracks, 0);
  started = { appid, queue: player.queueId };
  notify(`Playing for ${music.game}`, music.collection.title);
}

function onGameExit(appid: number) {
  if (started?.appid !== appid) return;
  // still the game's music (not something picked since): it stops with the game
  if (player.queueId === started.queue && player.state.get().playing) player.toggle();
  started = null;
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
  known = now;
  runningGame.set(readRunningGame());
  void refreshRunningGame();
}

/** Listens for games starting and stopping. Returns the unregister function. */
export function watchGames(): () => void {
  // games already running when the plugin loads didn't just start: no music for them
  known = new Set(runningGames().filter(isPlayable));
  runningGame.set(readRunningGame());
  const registration = window.SteamClient?.GameSessions?.RegisterForAppLifetimeNotifications?.(() => {
    // Steam's running list updates just after the notification, sometimes a few seconds after
    for (const delay of [1000, 5000]) window.setTimeout(() => reconcile(runningGames()), delay);
  });
  return () => registration?.unregister();
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

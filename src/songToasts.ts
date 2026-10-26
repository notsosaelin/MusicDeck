import { runningGame } from "./games";
import { notify } from "./notify";
import { player } from "./player";
import { Store, pluginState } from "./store";
import { desktopDebug } from "./desktop";

/*
 * A toast when a new song starts, its artwork as the picture: what's playing, said where MusicDeck isn't on screen (in a
 * game, mostly). How often is the user's (settings' song_toasts): never, while a game runs (the default), or always.
 * Never while MusicDeck itself shows (the panel, the media center: it's in front of them already), never in desktop
 * Steam (its mini player says it), and only once a song has played a moment: skipping through several says the one
 * that stays, once.
 */

export type SongToasts = "off" | "games" | "always";
export const SONG_TOASTS: [SongToasts, string][] = [
  ["off", "Off"],
  ["games", "While a game runs"],
  ["always", "Always"],
];

/** How long a song plays before it's told (skipping through doesn't toast each) */
const AFTER_MS = 1500;

/** MusicDeck's own pages on screen: the Quick Access panel and the media center count themselves in while they show */
export const musicDeckShowing = new Store(0);
export const showingMusicDeck = () => {
  musicDeckShowing.set(musicDeckShowing.get() + 1);
  return () => musicDeckShowing.set(Math.max(0, musicDeckShowing.get() - 1));
};

/** Starts telling new songs; returns the stop. */
export function watchSongToasts(): () => void {
  // the song last told (or that was playing when this started: a restored queue isn't news)
  const key = () => {
    const { queue, index } = player.state.get();
    const item = queue[index];
    return item ? `${item.instanceId}:${item.track.id}:${index}` : "";
  };
  let told = key();
  let timer = 0;
  const check = () => {
    const now = key();
    if (now === told) return;
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      const { queue, index, playing } = player.state.get();
      const item = queue[index];
      if (!item || key() !== now || !playing) return;
      told = now;
      const when = pluginState.get()?.song_toasts ?? "games";
      if (when === "off" || (when === "games" && !runningGame.get())) return;
      if (musicDeckShowing.get() > 0 || desktopDebug.current) return;
      const { track } = item;
      const service = pluginState.get()?.services.find((s) => s.id === item.instanceId)?.name;
      // the song's name, then who and from what (the source's name for what has neither: a station)
      const about = [track.artist, track.album].filter(Boolean).join(" · ") || service || "";
      notify(track.title, about, item);
    }, AFTER_MS);
  };
  const unsubscribe = player.state.subscribe(check);
  return () => {
    unsubscribe();
    window.clearTimeout(timer);
  };
}

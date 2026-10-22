import { notify } from "./notify";
import { player } from "./player";
import { Store } from "./store";

/** Seconds of fade-out before the sleep timer pauses playback. */
const FADE_SECONDS = 8;

export type SleepChoice = number | "track" | null;

/** The running timer: when it ends (epoch ms), or "track" for the end of the current track. */
export const sleepTimer = new Store<{ endsAt: number | null; endOfTrack: boolean }>({
  endsAt: null,
  endOfTrack: false,
});

let tick: number | undefined;

/**
 * Pause playback after `choice` minutes (fading out), at the end of the current track ("track"),
 * or cancel the timer (null). Lives alongside the player, so it keeps counting with the QAM closed.
 */
export function setSleepTimer(choice: SleepChoice) {
  window.clearInterval(tick);
  player.stopAfterCurrent = choice === "track";
  if (typeof choice !== "number") {
    sleepTimer.set({ endsAt: null, endOfTrack: choice === "track" });
    return;
  }
  const endsAt = Date.now() + choice * 60_000;
  sleepTimer.set({ endsAt, endOfTrack: false });
  tick = window.setInterval(() => {
    if (Date.now() < endsAt - FADE_SECONDS * 1000) return;
    window.clearInterval(tick);
    sleepTimer.set({ endsAt: null, endOfTrack: false });
    void player.fadeOutAndPause(FADE_SECONDS).then(() => notify("Sleep timer", "Playback paused"));
  }, 1000);
}

/** "23 min left", "End of track", or null when no timer runs. */
export function sleepTimerLabel(): string | null {
  const { endsAt, endOfTrack } = sleepTimer.get();
  if (endOfTrack && player.stopAfterCurrent) return "End of track";
  if (!endsAt) return null;
  return `${Math.max(1, Math.ceil((endsAt - Date.now()) / 60_000))} min left`;
}

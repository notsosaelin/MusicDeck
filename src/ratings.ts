/**
 * Rating songs (sources with feature "rate": Local files): the stars a song is shown with wherever it is, set from
 * the track table, the big Now Playing or a song's menu. The file gets the rating (other players see it there),
 * or MusicDeck keeps it for a kind of file that can't hold one.
 */
import { Track, setRating } from "./api";
import { invalidateBrowserCache } from "./nav";
import { notify } from "./notify";
import { hasFeature, player } from "./player";

export const canRate = (instanceId: string) => hasFeature(instanceId, "rate");

/** Rates a song; the queue and the pages showing it follow. Choosing its own rating again clears it. */
export async function rateTrack(instanceId: string, track: Track, stars: number) {
  const next = track.rating === stars ? 0 : stars;
  const r = await setRating(instanceId, track.id, next).catch((e) => ({ ok: false as const, error: String(e) }));
  if (!r.ok) {
    notify("Couldn't rate it", r.error, { instanceId, track });
    return;
  }
  player.updateTrack(instanceId, track.id, { rating: r.data });
  invalidateBrowserCache();
}

/** "★★★☆☆", for menus */
export const starText = (n: number) => "★".repeat(n) + "☆".repeat(5 - n);

import { Menu, MenuItem, showContextMenu } from "@decky/ui";
import { FC } from "react";
import { CastDevice, Result, castDevices, castKnown } from "../api";
import { notify } from "../notify";
import { player } from "../player";
import { pluginState } from "../store";

/** Where the music plays when not here: a speaker, a TV, or another of the user's devices. */
export const playingOn = (): string | null => player.state.get().castingTo?.name ?? null;

/** Tracks from this source can play on speakers and TVs (all but Spotify). */
export const canCast = (instanceId: string) => !!pluginState.get()?.services.find((s) => s.id === instanceId)?.castable;

/** How long a search's devices are shown without searching again */
const FRESH_MS = 60_000;
/** How long Play on waits for a search when no device was ever found (the first time) */
const FIRST_WAIT_MS = 2500;

/** The last search's devices, and the search running now (one at a time) */
let found: { result: Result<CastDevice[]>; at: number } | null = null;
let searching: Promise<Result<CastDevice[]>> | null = null;

function search(): Promise<Result<CastDevice[]>> {
  searching ??= castDevices()
    .catch((e) => ({ ok: false as const, error: String(e) }))
    .then((result) => {
      found = { result, at: Date.now() };
      searching = null;
      return result;
    });
  return searching;
}

/**
 * Starts looking for speakers and TVs ahead of X being pressed (Now Playing showing, the volume
 * slider focused), so the list is ready when it's asked for. A recent search counts.
 */
export function prepareCastMenu() {
  if (!found || Date.now() - found.at > FRESH_MS) void search();
}

/** What "here" is called: this Deck (or handheld), or this PC on Windows. */
export const thisDevice = () => (pluginState.get()?.platform?.startsWith("windows") ? "This PC" : "This Deck");


/** Plays on `device`, or back here (null); a failure is said. */
export const chooseCast = async (device: CastDevice | null) => {
  if (!device) return player.stopCasting();
  const error = await player.castTo(device);
  if (error) notify(`Couldn't play on ${device.name}`, error, player.current);
};

/**
 * "Play on": this Deck, or the speakers and TVs found. Built once, from devices already found: a
 * context menu that changes while it's open gets "Change Artwork…" added to it by the SteamGridDB
 * plugin, which takes any re-rendered menu for a game's.
 */
const CastMenu: FC<{ result: Result<CastDevice[]> }> = ({ result }) => {
  const where = playingOn();
  const mark = (on: boolean) => (on ? "✓ " : "");
  return (
    <Menu label="Play on">
      <MenuItem onSelected={() => chooseCast(null)}>{mark(!where)}{thisDevice()}</MenuItem>
      {result.ok &&
        result.data.map((d) => (
          <MenuItem key={d.id} onSelected={() => chooseCast(d)}>
            {mark(where === d.name)}
            {d.name}
          </MenuItem>
        ))}
      {result.ok && !result.data.length && <MenuItem disabled>No speakers or TVs found</MenuItem>}
      {!result.ok && <MenuItem disabled>{result.error}</MenuItem>}
    </Menu>
  );
};

/**
 * Opens Play on at once: with the last search's devices, or the ones the backend found lately (your other MusicDeck
 * devices always as they are now), searching again behind it for next time. Only when no speaker or TV was found yet
 * (since MusicDeck started) does it wait, a moment at most, for the search.
 */
export async function showCastMenu(parent?: EventTarget | null) {
  showContextMenu(<CastMenu result={await castChoices()} />, parent ?? window);
}

/** The devices to offer (Play on here, the desktop's list too): at once, as showCastMenu says. */
export async function castChoices(): Promise<Result<CastDevice[]>> {
  if (found && Date.now() - found.at <= FRESH_MS) return found.result;
  const known = await castKnown().catch(() => null);
  const searched = search();
  // speakers and TVs found before (not just your own devices, known at once after a restart): opens with those
  if (known?.ok && known.data.some((d) => d.kind !== "musicdeck")) return known;
  const waited = new Promise<null>((done) => window.setTimeout(() => done(null), FIRST_WAIT_MS));
  return (await Promise.race([searched, waited])) ?? known ?? { ok: true, data: [] };
}

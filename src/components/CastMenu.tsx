import { Menu, MenuItem, showContextMenu } from "@decky/ui";
import { FC } from "react";
import { CastDevice, Result, castDevices, sendToDevice, takeOver } from "../api";
import { notify } from "../notify";
import { player } from "../player";
import { Store, pluginState } from "../store";

/** Another of the user's devices the music was handed to (Play on), while it plays there: "This Deck" brings it
 *  back. Forgotten once something plays here again. */
export const handedTo = new Store<CastDevice | null>(null);
player.state.subscribe(() => {
  if (handedTo.get() && player.state.get().playing) handedTo.set(null);
});

/** Where the music plays when not here: a speaker or TV it's cast to, or another device it was handed to. */
export const playingOn = (): string | null => player.state.get().castingTo?.name ?? handedTo.get()?.name ?? null;

/** Tracks from this source can play on speakers and TVs (all but Spotify). */
export const canCast = (instanceId: string) => !!pluginState.get()?.services.find((s) => s.id === instanceId)?.castable;

/** How long a search's devices are shown without searching again */
const FRESH_MS = 60_000;

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

/** The devices to offer: the ones found lately, or once a search for them finishes. */
export const castChoices = (): Promise<Result<CastDevice[]>> =>
  found && Date.now() - found.at <= FRESH_MS ? Promise.resolve(found.result) : search();

/** Plays on `device`, or back here (null); a failure is said. */
export const chooseCast = async (device: CastDevice | null) => {
  if (!device) return handedTo.get() ? bringBack() : player.stopCasting();
  if (device.kind === "musicdeck") return playOnDevice(device);
  const error = await player.castTo(device);
  if (error) notify(`Couldn't play on ${device.name}`, error, player.current);
};

/** Another of the user's devices: the queue goes there and plays on from here; here it stops. */
async function playOnDevice(device: CastDevice) {
  const { queue, index } = player.state.get();
  if (index < 0 || !queue.length) return;
  const r = await sendToDevice(device.id.replace(/^musicdeck:/, ""), {
    queue,
    index,
    position: player.position.get().current,
    playing: true,
  }).catch((e) => ({ ok: false as const, error: String(e) }));
  if (!r.ok) return notify(`Couldn't play on ${device.name}`, r.error, player.current);
  if (player.state.get().playing) player.toggle();
  handedTo.set(device);
}

/** The music handed to another device, back here: it plays on here from where it is there, and stops there. */
async function bringBack() {
  const device = handedTo.get();
  if (!device) return;
  const r = await takeOver(device.id.replace(/^musicdeck:/, "")).catch((e) => ({ ok: false as const, error: String(e) }));
  if (!r.ok) return notify(`Couldn't bring it back from ${device.name}`, r.error, player.current);
  handedTo.set(null);
  player.takeOver(r.data);
}

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

/** Opens Play on: at once with the devices found lately, or once a search for them finishes. */
export async function showCastMenu(parent?: EventTarget | null) {
  showContextMenu(<CastMenu result={await castChoices()} />, parent ?? window);
}

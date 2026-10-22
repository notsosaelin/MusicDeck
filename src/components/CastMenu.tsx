import { Menu, MenuItem, showContextMenu } from "@decky/ui";
import { FC, useEffect, useState } from "react";
import { CastDevice, castDevices } from "../api";
import { notify } from "../notify";
import { player } from "../player";
import { pluginState, useStore } from "../store";

/** Tracks from this source can play on speakers and TVs (all but Spotify). */
export const canCast = (instanceId: string) =>
  !!pluginState.get()?.services.find((s) => s.id === instanceId)?.castable;

const choose = async (device: CastDevice | null) => {
  if (!device) return player.stopCasting();
  const error = await player.castTo(device);
  if (error) notify(`Couldn't play on ${device.name}`, error, player.current);
};

/** "Play on": this Deck, or a speaker or TV found on the network (searched for as the menu opens). */
const CastMenu: FC = () => {
  const { castingTo } = useStore(player.state);
  const [devices, setDevices] = useState<CastDevice[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    castDevices()
      .then((r) => {
        if (cancelled) return;
        if (r.ok) setDevices(r.data);
        else setError(r.error);
      })
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);
  const mark = (on: boolean) => (on ? "✓ " : "");
  return (
    <Menu label="Play on">
      <MenuItem onSelected={() => choose(null)}>{mark(!castingTo)}This Deck</MenuItem>
      {devices?.map((d) => (
        <MenuItem key={d.id} onSelected={() => choose(d)}>
          {mark(castingTo?.name === d.name)}
          {d.name}
        </MenuItem>
      ))}
      {!devices && !error && <MenuItem disabled>Looking for speakers and TVs…</MenuItem>}
      {devices?.length === 0 && <MenuItem disabled>No speakers or TVs found</MenuItem>}
      {error && <MenuItem disabled>{error}</MenuItem>}
    </Menu>
  );
};

export function showCastMenu(parent?: EventTarget | null) {
  showContextMenu(<CastMenu />, parent ?? window);
}

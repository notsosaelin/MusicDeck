import { CenterSettings, setSetting } from "../../api";
import { pluginState, refreshState } from "../../store";

/** The media center's choices before any are made */
export const DEFAULT_CENTER: CenterSettings = {
  open_on: "last",
  background: "art",
  cards: "medium",
  lyrics: "medium",
  show_library: true,
  show_browse: true,
  idle_off: 0,
};

/** Changes some of the media center's choices (from its Settings or the panel's). */
export async function setCenter(patch: Partial<CenterSettings>) {
  const current: Partial<CenterSettings> = { ...pluginState.get()?.center, ...patch };
  // only the choices there are: the backend refuses the whole setting over one it doesn't know
  const center = Object.fromEntries(
    (Object.keys(DEFAULT_CENTER) as (keyof CenterSettings)[]).map((k) => [k, current[k] ?? DEFAULT_CENTER[k]]),
  ) as unknown as CenterSettings;
  await setSetting("center", center);
  await refreshState();
}

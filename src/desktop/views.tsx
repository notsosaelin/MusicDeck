/**
 * What MusicDeck draws in desktop Steam: the Music section (MusicView, in the main window under the top
 * navigation: the library on the left and the page, the player along the bottom); the mini player is mini.tsx.
 */
import { FC } from "react";
import { useStore } from "../store";
import { BAR_CSS, PlayerBar, SidePanel } from "./bar";
import { LIBRARY_CSS, Pages, Sidebar } from "./library";
import { panel } from "./data";
import { fileDrag } from "./manage";
import { MINI_CSS } from "./mini";
import { Backdrop, NOW_PLAYING_CSS, NowPlayingFull } from "./nowplaying";
import { PARTS_CSS, STARS_CSS } from "./parts";
import { POLISH_CSS } from "./polish";
import { STATS_CSS } from "./stats";
import { TOOLS_CSS } from "./tools";
import { COLUMNS_CSS } from "./columns";

/** Styles our parts use, in whichever document they're drawn (the main window's, or the mini player's). */
export const BASE_CSS = PARTS_CSS + STARS_CSS + LIBRARY_CSS + BAR_CSS + POLISH_CSS + NOW_PLAYING_CSS + MINI_CSS + STATS_CSS + TOOLS_CSS + COLUMNS_CSS;

/** The Music section. Music files dragged in from the desktop go into Local files (the drag itself is caught by
 *  catchDrops, before Steam's window can refuse it). While the big Now Playing shows, the library steps aside
 *  and the cover's blur lies behind it all. */
export const MusicView: FC = () => {
  const dropping = useStore(fileDrag).over;
  const full = useStore(panel).full;
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", color: "#dcdedf", position: "relative" }}>
      {full && <Backdrop />}
      <div style={{ flex: 1, display: "flex", minHeight: 0, position: "relative" }}>
        {!full && <Sidebar />}
        {full ? <NowPlayingFull /> : <Pages />}
        <SidePanel />
      </div>
      <div style={{ position: "relative" }}>
        <PlayerBar />
      </div>
      {dropping && <div className="md-drop">Drop to add to Local files</div>}
    </div>
  );
};

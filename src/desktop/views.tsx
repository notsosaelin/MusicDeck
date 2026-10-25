/**
 * What MusicDeck draws in desktop Steam: the Music section (MusicView, in the main window under the top
 * navigation: the library on the left and the page, the player along the bottom) and the mini player
 * (MiniPlayer, in a small window of its own).
 */
import { FC } from "react";
import { FaThumbtack, FaTimes } from "react-icons/fa";
import { useStore } from "../store";
import { BAR_CSS, PlayerBar, SidePanel } from "./bar";
import { LIBRARY_CSS, Pages, Sidebar } from "./library";
import { panel } from "./data";
import { fileDrag } from "./manage";
import { NOW_PLAYING_CSS, NowPlayingFull } from "./nowplaying";
import { Button, Controls, Cover, PARTS_CSS, Seek, Titles } from "./parts";
import { POLISH_CSS } from "./polish";

/** Styles our parts use, in whichever document they're drawn (the main window's, or the mini player's). */
export const BASE_CSS = PARTS_CSS + LIBRARY_CSS + BAR_CSS + POLISH_CSS + NOW_PLAYING_CSS;

/** The Music section. Music files dragged in from the desktop go into Local files (the drag itself is caught by
 *  catchDrops, before Steam's window can refuse it). */
export const MusicView: FC = () => {
  const dropping = useStore(fileDrag).over;
  const full = useStore(panel).full;
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", color: "#dcdedf", position: "relative" }}>
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <Sidebar />
        {full ? <NowPlayingFull /> : <Pages />}
        <SidePanel />
      </div>
      <PlayerBar />
      {dropping && <div className="md-drop">Drop to add to Local files</div>}
    </div>
  );
};

/** The mini player: what's playing, in a small window dragged anywhere. */
export const MiniPlayer: FC<{ onClose: () => void; onOpenMusic: () => void; onTop: boolean; onPin: () => void }> = ({
  onClose,
  onOpenMusic,
  onTop,
  onPin,
}) => (
  <div style={{ display: "flex", gap: 10, padding: 10, height: "100%", boxSizing: "border-box", color: "#dcdedf" }}>
    <div onDoubleClick={onOpenMusic} title="Double-click: open Music" style={{ cursor: "default" }}>
      <Cover size={72} />
    </div>
    <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "space-between", minWidth: 0 }}>
      <div style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
        <Titles size={13} />
        <Button label={onTop ? "Stop keeping on top" : "Keep on top"} onClick={onPin}>
          <FaThumbtack style={{ opacity: onTop ? 1 : 0.45 }} />
        </Button>
        <Button label="Close" onClick={onClose}>
          <FaTimes />
        </Button>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Controls />
        <div style={{ flex: 1, minWidth: 0 }}>
          <Seek />
        </div>
      </div>
    </div>
  </div>
);

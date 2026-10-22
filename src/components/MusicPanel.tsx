import { Focusable } from "@decky/ui";
import { FC, ReactNode } from "react";
import { FaListOl, FaMusic, FaSearch, FaSlidersH } from "react-icons/fa";
import { MdLyrics } from "react-icons/md";
import { LyricsView } from "./LyricsView";
import { player } from "../player";
import { pluginState, useStore } from "../store";
import { Browser } from "./Browser";
import { PanelTab, panelTab, popView, resetStack } from "../nav";
import { Equalizer } from "./Equalizer";
import { NowPlaying } from "./NowPlaying";
import { Queue } from "./Queue";
import { ButtonRow, IconButton, Message } from "./common";

const TABS: { id: PanelTab; label: string; icon: ReactNode }[] = [
  { id: "library", label: "Library", icon: <FaMusic /> },
  { id: "search", label: "Search", icon: <FaSearch /> },
  { id: "lyrics", label: "Lyrics", icon: <MdLyrics /> },
  { id: "queue", label: "Queue", icon: <FaListOl /> },
  { id: "eq", label: "Equalizer", icon: <FaSlidersH /> },
];

/** The player UI shown in the Quick Access Menu (in place of Soundtracks, or as its own tab). */
export const MusicPanel: FC = () => {
  const state = useStore(pluginState);
  const { tab } = useStore(panelTab);
  const { queue } = useStore(player.state);
  const active = state?.services.find((s) => s.id === state.active_service);

  return (
    <Focusable
      // B steps back through library/search history before it closes the menu
      onCancelButton={(e: any) => {
        const t = panelTab.get().tab;
        if ((t === "library" || t === "search") && popView(t)) e?.stopPropagation?.();
      }}
    >
      <NowPlaying>
        <ButtonRow>
          {TABS.map((t) => (
            <IconButton
              key={t.id}
              label={t.label}
              active={tab === t.id}
              onClick={() => {
                // tapping the tab you're already on goes back to its start, like most music apps
                if (tab === t.id && (t.id === "library" || t.id === "search")) resetStack(t.id);
                panelTab.set({ tab: t.id });
              }}
            >
              {t.icon}
              {t.id === "queue" && queue.length ? <span style={{ fontSize: 11 }}>{queue.length}</span> : null}
            </IconButton>
          ))}
        </ButtonRow>
      </NowPlaying>
      {(tab === "library" || tab === "search") &&
        (!state ? null : active ? (
          <Browser instanceId={active.id} stack={tab} />
        ) : (
          <Message>No music source set up yet. Open Decky → MusicDeck to add your library or a server.</Message>
        ))}
      {tab === "queue" && <Queue />}
      {tab === "lyrics" && <LyricsView />}
      {tab === "eq" && <Equalizer />}
    </Focusable>
  );
};

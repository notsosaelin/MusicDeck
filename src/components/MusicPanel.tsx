import { Focusable, PanelSection, PanelSectionRow } from "@decky/ui";
import { FC, ReactNode } from "react";
import { FaCompactDisc, FaListOl, FaMusic, FaSearch, FaSlidersH } from "react-icons/fa";
import { LyricsView } from "./LyricsView";
import { player } from "../player";
import { pluginState, useStore } from "../store";
import { Browser } from "./Browser";
import { PanelTab, panelTab, popView, resetStack } from "../nav";
import { Equalizer } from "./Equalizer";
import { MiniPlayer, NowPlaying } from "./NowPlaying";
import { Queue } from "./Queue";
import { ACCENT, IconButton, Message, PLAYER_CSS } from "./common";

const TABS: { id: PanelTab; label: string; icon: ReactNode }[] = [
  { id: "playing", label: "Now playing", icon: <FaCompactDisc /> },
  { id: "library", label: "Library", icon: <FaMusic /> },
  { id: "search", label: "Search", icon: <FaSearch /> },
  { id: "queue", label: "Queue", icon: <FaListOl /> },
  { id: "eq", label: "Equalizer", icon: <FaSlidersH /> },
];

// GamepadButton.BUMPER_LEFT / BUMPER_RIGHT from @decky/ui (an ambient enum, which isolated builds can't import)
const L1 = 5;
const R1 = 6;

/**
 * L1 and R1 step through the tabs, wrapping round, as in Steam's own tabbed pages. Focus moves to
 * the new tab's button: what had it may be gone with the old tab, and Steam would spend the next
 * press finding focus again.
 */
function stepTab(by: number, panel: HTMLElement | null) {
  const at = TABS.findIndex((t) => t.id === panelTab.get().tab);
  const next = (at + by + TABS.length) % TABS.length;
  panelTab.set({ tab: TABS[next].id });
  window.requestAnimationFrame(() => panel?.querySelectorAll<HTMLElement>(".md-tabs button")[next]?.focus());
}

/** The tab strip at the top of the panel: plain icons over one line, the open tab underlined. */
const Tabs: FC<{ tab: PanelTab; queued: number }> = ({ tab, queued }) => (
  // in a section, for the same side margins as everything below it
  <PanelSection>
    <PanelSectionRow>
      <Focusable
        flow-children="row"
        className="md-tabs"
        style={{ display: "flex", gap: 4, borderBottom: "1px solid rgba(255, 255, 255, 0.1)", marginBottom: 8 }}
      >
        {TABS.map((t) => (
          <IconButton
            key={t.id}
            look="plain"
            label={`${t.label} (L1/R1)`}
            active={tab === t.id}
            style={{
              height: 40,
              fontSize: 16,
              borderRadius: 0,
              boxShadow: tab === t.id ? `inset 0 -2px 0 ${ACCENT}` : "none",
            }}
            onClick={() => {
              // tapping the tab you're already on goes back to its start, like most music apps
              if (tab === t.id && (t.id === "library" || t.id === "search")) resetStack(t.id);
              panelTab.set({ tab: t.id });
            }}
          >
            {t.icon}
            {t.id === "queue" && queued ? <span style={{ fontSize: 11 }}>{queued}</span> : null}
          </IconButton>
        ))}
      </Focusable>
    </PanelSectionRow>
  </PanelSection>
);

/**
 * The player in the Quick Access Menu (in place of Soundtracks, or as its own tab). Laid out like a
 * music app: tabs at the top, always in the same place; the full player (and the song's lyrics) on
 * Now playing; a one-row mini player above Library, Search and Queue, so browsing gets the space.
 */
export const MusicPanel: FC = () => {
  const state = useStore(pluginState);
  const { tab } = useStore(panelTab);
  const { queue } = useStore(player.state);
  const active = state?.services.find((s) => s.id === state.active_service);
  const browsing = tab === "library" || tab === "search";

  return (
    <Focusable
      // B steps back through library/search history before it closes the menu
      onButtonDown={(e: any) => {
        const button = e?.detail?.button;
        if (button === L1 || button === R1) {
          stepTab(button === L1 ? -1 : 1, e.currentTarget);
          e.stopPropagation?.();
        }
      }}
      onCancelButton={(e: any) => {
        const t = panelTab.get().tab;
        if ((t === "library" || t === "search") && popView(t)) e?.stopPropagation?.();
      }}
    >
      <style>{PLAYER_CSS}</style>
      <Tabs tab={tab} queued={queue.length} />
      {tab === "playing" ? (
        <>
          <NowPlaying />
          {queue.length > 0 && <LyricsView />}
        </>
      ) : (
        tab !== "eq" && <MiniPlayer onOpen={() => panelTab.set({ tab: "playing" })} />
      )}
      {browsing &&
        (!state ? null : active ? (
          <Browser instanceId={active.id} stack={tab} />
        ) : (
          <Message>Add a music source in Decky → MusicDeck</Message>
        ))}
      {tab === "queue" && <Queue />}
      {tab === "eq" && <Equalizer />}
    </Focusable>
  );
};

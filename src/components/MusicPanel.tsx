import { Focusable, PanelSection, PanelSectionRow } from "@decky/ui";
import { FC, ReactNode } from "react";
import { FaCompactDisc, FaExpand, FaListOl, FaMusic, FaSearch, FaSlidersH } from "react-icons/fa";
import { LyricsView } from "./LyricsView";
import { player } from "../player";
import { pluginState, useStore } from "../store";
import { Browser } from "./Browser";
import { PanelTab, TAB_ORDER, openFullPlayer, panelTab, popView, resetStack, setTabStrip, showTab } from "../nav";
import { Equalizer } from "./Equalizer";
import { MiniPlayer, NowPlaying } from "./NowPlaying";
import { Queue, confirmClearQueue } from "./Queue";
import { ACCENT, IconButton, Message, PLAYER_CSS } from "./common";

const TAB_LOOKS: Record<PanelTab, { label: string; icon: ReactNode }> = {
  playing: { label: "Now playing", icon: <FaCompactDisc /> },
  library: { label: "Library", icon: <FaMusic /> },
  search: { label: "Search", icon: <FaSearch /> },
  queue: { label: "Queue", icon: <FaListOl /> },
  eq: { label: "Equalizer", icon: <FaSlidersH /> },
};

// GamepadButton.BUMPER_LEFT / BUMPER_RIGHT from @decky/ui (an ambient enum, which isolated builds can't import)
const L1 = 5;
const R1 = 6;

/** L1 and R1 step through the tabs, wrapping round, as in Steam's own tabbed pages. */
function stepTab(by: number) {
  const at = TAB_ORDER.indexOf(panelTab.get().tab);
  showTab(TAB_ORDER[(at + by + TAB_ORDER.length) % TAB_ORDER.length]);
}

/** The tab strip at the top of the panel: plain icons over one line, the open tab underlined. */
const Tabs: FC<{ tab: PanelTab; queued: number }> = ({ tab, queued }) => (
  // in a section, for the same side margins as everything below it
  <PanelSection>
    <PanelSectionRow>
      <div ref={setTabStrip}>
        <Focusable
          flow-children="row"
          className="md-tabs"
          style={{ display: "flex", gap: 4, borderBottom: "1px solid rgba(255, 255, 255, 0.1)", marginBottom: 8 }}
        >
          {TAB_ORDER.map((id) => ({ id, ...TAB_LOOKS[id] })).map((t) => (
            <IconButton
              key={t.id}
              look="plain"
              label={t.label}
              // X on the Queue tab clears it (it asks first), wherever the queue is scrolled to
              onSecondary={t.id === "queue" && queued ? confirmClearQueue : undefined}
              secondaryLabel="Clear queue"
              active={tab === t.id}
              style={{ height: 40, fontSize: 16, borderRadius: 0 }}
              onClick={() => {
                // tapping the tab you're already on goes back to its start, like most music apps
                if (tab === t.id && (t.id === "library" || t.id === "search")) resetStack(t.id);
                panelTab.set({ tab: t.id });
              }}
            >
              {t.icon}
              {t.id === "queue" && queued ? <span style={{ fontSize: 11 }}>{queued}</span> : null}
              {/* the underline is an element, not an inset shadow: on a button of fractional width
                Chromium also draws a hairline down the shadow's right edge */}
              {tab === t.id && (
                <span style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 2, background: ACCENT }} />
              )}
            </IconButton>
          ))}
          {/* not a tab: opens the player full screen, in Steam's main window */}
          <IconButton
            look="plain"
            label="Full screen"
            onClick={openFullPlayer}
            style={{ height: 40, fontSize: 14, borderRadius: 0 }}
          >
            <FaExpand />
          </IconButton>
        </Focusable>
      </div>
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
          stepTab(button === L1 ? -1 : 1);
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
        tab !== "eq" && <MiniPlayer onOpen={() => showTab("playing")} />
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

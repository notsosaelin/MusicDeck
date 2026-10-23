import { DropdownItem, Focusable, Navigation, SliderField, Tabs, ToggleField } from "@decky/ui";
import { FC, ReactNode, RefObject, useEffect, useRef, useState } from "react";
import {
  FaMoon,
  FaPause,
  FaPlay,
  FaRandom,
  FaRedo,
  FaStepBackward,
  FaStepForward,
  FaVolumeDown,
  FaVolumeMute,
  FaVolumeUp,
} from "react-icons/fa";
import { MdRepeatOne } from "react-icons/md";
import { CenterSettings, setSetting } from "../../api";
import { CenterTab, centerTab } from "../../nav";
import { player } from "../../player";
import { mediaUrl, pluginState, refreshState, useStore } from "../../store";
import { openMusicDeck } from "../../qam";
import { Equalizer } from "../Equalizer";
import { canCast, showCastMenu } from "../CastMenu";
import { IconButton, PLAYER_CSS, RowText, formatTime, watchSize } from "../common";
import { ScrollingPlainLyrics, SyncedLyrics, useLyrics } from "../LyricsView";
import { CENTER_CSS, Cover, Notice, focusInto } from "./parts";
import {
  CenterStack,
  CollectionPage,
  HomePage,
  LibraryPage,
  QueuePage,
  SearchPage,
  cardKey,
  centerNav,
  focusKeyFor,
  popCenter,
} from "./pages";

/**
 * The height of an element, kept current. The page renders into Steam's main window while this code
 * runs in its shared context, so `window` here isn't the page's window; measure the element instead.
 */
function useHeight(ref: RefObject<HTMLElement | null>) {
  const [height, setHeight] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setHeight(el.clientHeight);
    measure();
    return watchSize(el, measure);
  }, []);
  return height;
}

/**
 * Screen off while the music plays: the backlight at Steam's minimum and the whole window black.
 * Handhelds only switch the backlight fully off for root (and Steam keeps a floor above zero), so
 * this is as dark as an ordinary app can make it, on any device. Any button or a tap wakes it, even
 * one Steam handles itself (its menu buttons), and leaving the page always puts the brightness back.
 */
function useScreenOff(): [boolean, () => void, () => void] {
  const [off, setOff] = useState(false);
  const offRef = useRef(false);
  const brightness = useRef<number | null>(null);
  const display = (window as any).SteamClient?.System?.Display;

  useEffect(() => {
    // Steam answers at once with the current brightness, then with every change
    const watch = display?.RegisterForBrightnessChanges?.((b: { flBrightness: number }) => {
      if (!offRef.current) brightness.current = b.flBrightness;
    });
    return () => watch?.unregister?.();
  }, []);

  const wake = () => {
    if (!offRef.current) return;
    offRef.current = false;
    setOff(false);
    if (brightness.current !== null) display?.SetBrightness?.(brightness.current);
  };
  const sleep = () => {
    if (offRef.current || brightness.current === null) return;
    offRef.current = true;
    setOff(true);
    display?.SetBrightness?.(0);
  };

  useEffect(() => {
    if (!off) return;
    // every controller input, wherever Steam sends it
    const input = (window as any).SteamClient?.Input?.RegisterForControllerInputMessages?.(
      (messages: { bS?: boolean }[]) => messages?.some?.((m) => m.bS) && wake(),
    );
    return () => input?.unregister?.();
  }, [off]);

  useEffect(() => wake, []); // leaving the page
  return [off, sleep, wake];
}

/**
 * The black that covers everything while the screen is off: fixed, above Steam's own header and hint
 * bar, so nothing stays lit. It takes the controller's focus as it appears, so every button reaches it
 * (and not the control that had focus, or Steam's tabs, which take B): any one wakes the screen and
 * does nothing else.
 */
const Blackout: FC<{ onWake: () => void }> = ({ onWake }) => {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // the controller goes back where it was when the screen wakes
    const was = ref.current?.ownerDocument.activeElement as HTMLElement | null;
    ref.current?.focus();
    return () => was?.focus?.();
  }, []);
  const wake = (e?: any) => {
    e?.stopPropagation?.();
    onWake();
  };
  return (
    <Focusable
      ref={ref}
      noFocusRing
      onActivate={wake}
      onClick={wake}
      onCancelButton={wake}
      onButtonDown={wake}
      onOKActionDescription="Wake"
      style={{ position: "fixed", inset: 0, zIndex: 100000, background: "#000000", cursor: "none" }}
    >
      {null}
    </Focusable>
  );
};

let volumeSaveTimer: number | undefined;

/** The volume slider; X on it chooses where the music plays (this Deck, a speaker, a TV). */
const Volume: FC = () => {
  const state = useStore(player.state);
  const item = state.queue[state.index];
  const icon = state.volume === 0 ? <FaVolumeMute /> : state.volume < 0.5 ? <FaVolumeDown /> : <FaVolumeUp />;
  const castable = !!item && canCast(item.instanceId);
  return (
    <Focusable
      style={{ flex: 1, minWidth: 0 }}
      onSecondaryButton={(e: any) => castable && showCastMenu(e?.currentTarget)}
      onSecondaryActionDescription={castable ? "Cast" : undefined}
    >
      <SliderField
        icon={icon}
        label={state.castingTo ? state.castingTo.name : undefined}
        value={Math.round(state.volume * 100)}
        min={0}
        max={100}
        step={5}
        bottomSeparator="none"
        childrenContainerWidth="max"
        onChange={(v) => {
          player.setVolume(v / 100);
          if (state.castingTo) return;
          window.clearTimeout(volumeSaveTimer);
          volumeSaveTimer = window.setTimeout(() => setSetting("volume", v / 100), 1000);
        }}
      />
    </Focusable>
  );
};

/** Shuffle, previous, play/pause, next, repeat. */
const Transport: FC<{ size?: "large" | "normal"; focusPlay?: boolean }> = ({ size = "large", focusPlay }) => {
  const state = useStore(player.state);
  const big = size === "large";
  const side = { height: big ? 48 : 40, fontSize: big ? 17 : 15 };
  const skip = { height: big ? 48 : 40, fontSize: big ? 20 : 17 };
  return (
    <Focusable flow-children="row" style={{ display: "flex", alignItems: "center", gap: 8, width: big ? 380 : 300 }}>
      <IconButton
        look="plain"
        label="Shuffle"
        active={state.shuffle}
        onClick={() => player.setShuffle(!state.shuffle)}
        style={side}
      >
        <FaRandom />
      </IconButton>
      <IconButton look="plain" label="Previous" onClick={() => player.previous()} style={skip}>
        <FaStepBackward />
      </IconButton>
      <IconButton
        look="round"
        preferredFocus={focusPlay}
        label={state.playing ? "Pause" : "Play"}
        onClick={() => player.toggle()}
        style={{ fontSize: big ? 20 : 17 }}
      >
        {state.playing ? <FaPause /> : <FaPlay style={{ marginLeft: 3 }} />}
      </IconButton>
      <IconButton look="plain" label="Next" onClick={() => player.next()} style={skip}>
        <FaStepForward />
      </IconButton>
      <IconButton
        look="plain"
        label="Repeat"
        active={state.repeat !== "off"}
        onClick={() => player.cycleRepeat()}
        style={side}
      >
        {state.repeat === "one" ? <MdRepeatOne /> : <FaRedo />}
      </IconButton>
    </Focusable>
  );
};

/**
 * Fits an element to the room below it in the scrolling area Steam's Tabs put it in (the tab bar
 * scrolls with the contents, so "100%" would overflow by its height), and stops that area scrolling
 * while it shows: Steam's focus scrolling keeps a margin around the focused control, and would
 * otherwise push a page that fits exactly up under the tab bar. Put back as it was on leaving.
 */
function useFitBelow(ref: RefObject<HTMLElement | null>) {
  const [room, setRoom] = useState(0);
  useEffect(() => {
    const el = ref.current;
    let scroller: HTMLElement | null = el?.parentElement ?? null;
    while (scroller && getComputedStyle(scroller).overflowY !== "auto") scroller = scroller.parentElement;
    if (!el || !scroller) return;
    const box = scroller;
    // both axes: with only one clipped, the browser treats it as hidden, which can still scroll
    const overflow = box.style.overflow;
    box.style.overflow = "clip";
    box.scrollTop = 0;
    const measure = () => setRoom(Math.floor(box.getBoundingClientRect().bottom - el.getBoundingClientRect().top - 4));
    measure();
    const stop = watchSize(box, measure);
    return () => {
      stop();
      box.style.overflow = overflow;
    };
  }, []);
  return room;
}

/** The seek bar, its times either side. */
const Seek: FC = () => {
  const { current, duration } = useStore(player.position);
  const time = { fontSize: 13, opacity: 0.7, width: 44, flexShrink: 0 } as const;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <span style={{ ...time, textAlign: "right" }}>{formatTime(current)}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <SliderField
          value={Math.min(current, duration || 0)}
          min={0}
          max={Math.max(duration, 1)}
          step={1}
          bottomSeparator="none"
          childrenContainerWidth="max"
          onChange={(v) => player.seek(v)}
        />
      </div>
      <span style={time}>{formatTime(duration)}</span>
    </div>
  );
};

/**
 * Now Playing, laid out as music apps do full screen: the cover as tall as there's room for, the
 * title and the song's lyrics beside it, and the seek bar, volume and transport underneath. Y turns
 * the screen off.
 */
const LYRICS_SIZES = { small: [18, 14], medium: [22, 17], large: [28, 21] } as const;

const NowPlayingTab: FC<{ onSleep: () => void; lyricsSize: CenterSettings["lyrics"] }> = ({ onSleep, lyricsSize }) => {
  const [syncedSize, plainSize] = LYRICS_SIZES[lyricsSize] ?? LYRICS_SIZES.medium;
  const state = useStore(player.state);
  const item = state.queue[state.index];
  const lyrics = useLyrics(item);
  const page = useRef<HTMLDivElement>(null);
  const room = useFitBelow(page);
  const area = useRef<HTMLDivElement>(null);
  const height = useHeight(area);
  const art = item?.track.art ? mediaUrl("art", item.instanceId, item.track.art) : undefined;
  const subtitle = item ? [item.track.artist, item.track.album].filter(Boolean).join(" · ") : "";
  const message = !item
    ? null
    : lyrics === undefined
      ? "Looking for lyrics…"
      : lyrics === null
        ? "No lyrics for this track"
        : "error" in lyrics
          ? `Couldn't load lyrics: ${lyrics.error}`
          : null;

  return (
    <div ref={page} style={{ height: room || undefined, boxSizing: "border-box", padding: "1vh 2.8vw 1.5vh" }}>
      {!item ? (
        <Notice>Nothing playing. Find something in Home or Search.</Notice>
      ) : (
        <Focusable
          onOptionsButton={onSleep}
          onOptionsActionDescription="Screen off"
          style={{ display: "flex", flexDirection: "column", gap: "2vh", height: "100%" }}
        >
          <div style={{ flex: 1, minHeight: 0, display: "flex", gap: "3.5vw" }}>
            <div
              style={{
                height: "100%",
                aspectRatio: "1",
                flexShrink: 0,
                borderRadius: 8,
                background: art ? `center / cover no-repeat url("${art}")` : "rgba(255, 255, 255, 0.08)",
                boxShadow: "0 8px 30px rgba(0, 0, 0, 0.5)",
              }}
            />
            <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "1.5vh" }}>
              <div style={{ display: "flex", alignItems: "flex-start", gap: 16 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <RowText size="large" scroll="always" title={item.track.title} subtitle={subtitle} />
                </div>
                <span
                  onClick={onSleep}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    fontSize: 13,
                    opacity: 0.55,
                    cursor: "pointer",
                  }}
                >
                  <FaMoon /> Screen off
                </span>
              </div>
              <div ref={area} style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                {message ? (
                  <div style={{ fontSize: 16, opacity: 0.6, paddingTop: height / 3 }}>{message}</div>
                ) : lyrics && !("error" in lyrics) && height > 0 ? (
                  lyrics.synced ? (
                    <SyncedLyrics lyrics={lyrics} height={height} fontSize={syncedSize} />
                  ) : (
                    <ScrollingPlainLyrics lyrics={lyrics} height={height} fontSize={plainSize} />
                  )
                ) : null}
              </div>
            </div>
          </div>
          <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {!item.track.live && <Seek />}
            <Focusable flow-children="row" style={{ display: "flex", alignItems: "center", gap: "2vw" }}>
              <Volume />
              <Transport focusPlay />
              {/* balances the volume, so the transport sits in the middle */}
              <div style={{ flex: 1 }} />
            </Focusable>
          </Focusable>
        </Focusable>
      )}
    </div>
  );
};

/** Along the bottom of the browsing tabs: what's playing and its progress. Tapping it opens Now Playing. */
const NowPlayingBar: FC = () => {
  const state = useStore(player.state);
  const { current, duration } = useStore(player.position);
  const item = state.queue[state.index];
  if (!item) return null;
  return (
    <div
      onClick={() => centerTab.set({ tab: "playing" })}
      style={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "10px 2.8vw",
        background: "rgba(0, 0, 0, 0.35)",
        borderTop: "1px solid rgba(255, 255, 255, 0.06)",
      }}
    >
      <Cover instanceId={item.instanceId} c={{ art: item.track.art, kind: "album" }} size={44} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <RowText
          title={item.track.title}
          subtitle={[item.track.artist, state.castingTo ? `On ${state.castingTo.name}` : ""]
            .filter(Boolean)
            .join(" · ")}
        />
      </div>
      <div style={{ fontSize: 22, opacity: 0.85, width: 32, textAlign: "center" }}>
        {state.playing ? <FaPause /> : <FaPlay />}
      </div>
      {!item.track.live && duration > 0 && (
        <div style={{ position: "absolute", left: 0, right: 0, top: 0, height: 2 }}>
          <div style={{ height: "100%", width: `${Math.min(current / duration, 1) * 100}%`, background: "#ffffff" }} />
        </div>
      )}
    </div>
  );
};

/** One browsing tab: its history's latest view, with the controller put back where it belongs. */
const Browse: FC<{ stack: CenterStack; instanceId: string; withoutCategories: boolean }> = ({
  stack,
  instanceId,
  withoutCategories,
}) => {
  const views = useStore(centerNav)[stack];
  const view = views[views.length - 1];
  const box = useRef<HTMLDivElement>(null);
  const viewKey = view.type === "collection" ? cardKey(view.collection) : view.type === "search" ? view.query : "home";
  useEffect(() => focusInto(box.current, focusKeyFor(stack, views.length - 1)), [viewKey, views.length]);
  return (
    // B goes back a page here, inside the tab: Steam's Tabs would otherwise take it first (to move
    // up to the tab bar, which it still does at the start of a tab)
    <Focusable
      ref={box}
      onCancelButton={(e: any) => {
        if (popCenter(stack)) e?.stopPropagation?.();
      }}
    >
      {view.type === "home" ? (
        stack === "library" ? (
          <LibraryPage instanceId={instanceId} />
        ) : (
          <HomePage instanceId={instanceId} withoutCategories={withoutCategories} />
        )
      ) : view.type === "search" ? (
        <SearchPage key={view.query} instanceId={instanceId} query={view.query} />
      ) : (
        <CollectionPage
          key={view.collection.kind + view.collection.id}
          instanceId={instanceId}
          stack={stack}
          collection={view.collection}
        />
      )}
    </Focusable>
  );
};

const TAB_TITLES: Record<CenterTab, string> = {
  playing: "Now Playing",
  home: "Home",
  library: "Library",
  search: "Search",
  queue: "Queue",
  sound: "Sound",
  customize: "Customize",
};

/** The tabs Customize leaves showing, in order. */
function visibleTabs(center: CenterSettings): CenterTab[] {
  return (Object.keys(TAB_TITLES) as CenterTab[]).filter(
    (t) => (t !== "library" || center.show_library) && (t !== "sound" || center.show_sound),
  );
}

const DEFAULT_CENTER: CenterSettings = {
  open_on: "last",
  background: "art",
  cards: "medium",
  lyrics: "medium",
  show_library: true,
  show_sound: true,
  idle_off: 0,
};

// GamepadButton.SELECT from @decky/ui (an ambient enum, which isolated builds can't import)
const VIEW_BUTTON = 13;

/** Leaves the media center for the Quick Access panel, where MusicDeck is. */
function backToPanel() {
  Navigation.NavigateBack();
  window.setTimeout(openMusicDeck, 150);
}

/**
 * Turns the screen off after `minutes` without input while Now Playing shows and music plays (Customize;
 * 0 = never). Any controller input, or the music stopping, starts the wait again.
 */
function useIdleScreenOff(minutes: number, active: boolean, sleep: () => void) {
  useEffect(() => {
    if (!minutes || !active) return;
    let last = Date.now();
    const input = (window as any).SteamClient?.Input?.RegisterForControllerInputMessages?.(() => {
      last = Date.now();
    });
    const timer = window.setInterval(() => {
      if (Date.now() - last >= minutes * 60_000) {
        last = Date.now();
        sleep();
      }
    }, 5000);
    return () => {
      window.clearInterval(timer);
      input?.unregister?.();
    };
  }, [minutes, active]);
}

const CHOICES: { key: keyof CenterSettings; label: string; options: { data: any; label: string }[] }[] = [
  {
    key: "open_on",
    label: "Open on",
    options: [
      { data: "last", label: "Where I left it" },
      { data: "playing", label: "Now Playing" },
      { data: "home", label: "Home" },
      { data: "library", label: "Library" },
    ],
  },
  {
    key: "background",
    label: "Background",
    options: [
      { data: "art", label: "Blurred album art" },
      { data: "plain", label: "Plain" },
    ],
  },
  {
    key: "cards",
    label: "Cover size",
    options: [
      { data: "small", label: "Small" },
      { data: "medium", label: "Medium" },
      { data: "large", label: "Large" },
    ],
  },
  {
    key: "lyrics",
    label: "Lyrics size",
    options: [
      { data: "small", label: "Small" },
      { data: "medium", label: "Medium" },
      { data: "large", label: "Large" },
    ],
  },
  {
    key: "idle_off",
    label: "Screen off when idle",
    options: [
      { data: 0, label: "Never" },
      { data: 1, label: "After 1 minute" },
      { data: 2, label: "After 2 minutes" },
      { data: 5, label: "After 5 minutes" },
      { data: 10, label: "After 10 minutes" },
    ],
  },
];

async function setCenter(patch: Partial<CenterSettings>) {
  const center = { ...DEFAULT_CENTER, ...pluginState.get()?.center, ...patch };
  await setSetting("center", center);
  await refreshState();
}

/** The media center's own settings, in Steam's settings style. */
const CustomizePage: FC = () => {
  const center = { ...DEFAULT_CENTER, ...useStore(pluginState)?.center };
  return (
    <div style={{ maxWidth: 760 }}>
      {CHOICES.map((c) => (
        <DropdownItem
          key={c.key}
          label={c.label}
          rgOptions={c.options}
          selectedOption={center[c.key]}
          onChange={(o) => setCenter({ [c.key]: o.data })}
        />
      ))}
      <ToggleField
        label="Library tab"
        description="Your playlists and the source's albums, artists and folders"
        checked={center.show_library}
        onChange={(on) => setCenter({ show_library: on })}
      />
      <ToggleField
        label="Sound tab"
        description="The equalizer"
        checked={center.show_sound}
        onChange={(on) => setCenter({ show_sound: on })}
      />
      <div style={{ fontSize: 13, opacity: 0.55, marginTop: 16, lineHeight: 1.5 }}>
        Volume levelling, crossfade and the rest of playback are in Decky → MusicDeck. Press the View button to go back
        to the Quick Access panel.
      </div>
    </div>
  );
};

/**
 * MusicDeck's media center, full screen in Steam's main window and laid out like Steam's own library:
 * tabs along the top (L1/R1), Home's rows as shelves of covers, the Library's playlists and
 * categories, albums and playlists as pages with their track lists, a Now Playing view with lyrics,
 * the queue, the equalizer and its own settings. Start plays or pauses from anywhere; B steps back
 * through a tab's pages, then up to the tabs, then to the Quick Access panel; so does View, at once.
 */
export const MediaCenter: FC = () => {
  const { tab } = useStore(centerTab);
  const state = useStore(pluginState);
  const center = { ...DEFAULT_CENTER, ...state?.center };
  const { queue, index, playing } = useStore(player.state);
  const [screenOff, sleep, wake] = useScreenOff();
  const item = queue[index];
  const art = item?.track.art ? mediaUrl("art", item.instanceId, item.track.art) : undefined;
  const live = state?.active_service ?? null;
  const tabs = visibleTabs(center);
  // a tab Customize just hid: back to the first
  const shown = tabs.includes(tab) ? tab : tabs[0];
  useIdleScreenOff(center.idle_off, shown === "playing" && playing && !screenOff, sleep);

  const browse = (stack: CenterStack) =>
    live ? (
      <Browse stack={stack} instanceId={live} withoutCategories={center.show_library} />
    ) : (
      <Notice>Add a music source in Decky → MusicDeck to browse it here.</Notice>
    );
  const content: Record<CenterTab, ReactNode> = {
    playing: <NowPlayingTab onSleep={sleep} lyricsSize={center.lyrics} />,
    home: browse("home"),
    library: browse("library"),
    search: browse("search"),
    queue: <QueuePage />,
    sound: (
      <div style={{ maxWidth: 760 }}>
        <Equalizer />
      </div>
    ),
    customize: <CustomizePage />,
  };

  return (
    <Focusable
      className="mc-center"
      onMenuButton={() => player.toggle()}
      onMenuActionDescription={item ? (playing ? "Pause" : "Play") : undefined}
      actionDescriptionMap={{ [VIEW_BUTTON]: "Quick Access" }}
      onButtonDown={(e: any) => {
        if (e?.detail?.button === VIEW_BUTTON && !screenOff) backToPanel();
      }}
      onCancelButton={(e: any) => {
        e?.stopPropagation?.();
        // reaching here, B wasn't taken by a page (going back) or by Steam's tabs (up to the tab bar)
        if (screenOff) wake();
        else backToPanel();
      }}
      // clip, not hidden: Steam's focus scrolling can move an overflow:hidden box, and did, by the
      // background's overhang
      style={{ position: "absolute", inset: 0, overflow: "clip", background: "#0e141b", color: "#dcdedf" }}
    >
      <style>{PLAYER_CSS + CENTER_CSS}</style>
      {art && center.background === "art" && (
        // blurred past its edges, in a layer of its own that clips the overhang
        <div style={{ position: "absolute", inset: 0, overflow: "clip" }}>
          <div
            style={{
              position: "absolute",
              inset: -60,
              backgroundImage: `url("${art}")`,
              backgroundSize: "cover",
              backgroundPosition: "center",
              filter: `blur(60px) brightness(${shown === "playing" ? 0.32 : 0.18}) saturate(1.2)`,
              transition: "filter 0.3s",
            }}
          />
        </div>
      )}
      <div
        style={{
          position: "absolute",
          // between Steam's header and its hint bar
          top: "8.6vh",
          bottom: "8.8vh",
          left: 0,
          right: 0,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div style={{ flex: 1, minHeight: 0 }}>
          <Tabs
            activeTab={shown}
            onShowTab={(t: string) => centerTab.set({ tab: t as CenterTab })}
            autoFocusContents
            tabs={tabs.map((id) => ({
              id,
              title: TAB_TITLES[id],
              // Now Playing fits the screen itself; the others scroll, down only (clip, not hidden: a
              // card growing at the edge mustn't make the whole tab scroll sideways)
              content:
                id === "playing" ? (
                  content[id]
                ) : (
                  <div style={{ padding: "2vh 2.8vw 3vh", overflowX: "clip" }}>{content[id]}</div>
                ),
            }))}
          />
        </div>
        {shown !== "playing" && <NowPlayingBar />}
      </div>
      {screenOff && <Blackout onWake={wake} />}
    </Focusable>
  );
};

import { DropdownItem, Focusable, SliderField, Tabs } from "@decky/ui";
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
import { MdForward30, MdRepeatOne, MdReplay10 } from "react-icons/md";
import { CenterSettings, CenterTabInfo, NowPlayingKind, ServiceInfo, setSetting } from "../../api";
import { CenterTab, centerTab, leaveMediaCenter } from "../../nav";
import { player } from "../../player";
import { Store, mediaUrl, pluginState, refreshState, useStore } from "../../store";
import { openMusicDeck } from "../../qam";
import { Equalizer } from "../Equalizer";
import { PlaybackSections, SectionFrame } from "../Settings";
import { canCast, prepareCastMenu, showCastMenu } from "../CastMenu";
import { IconButton, PLAYER_CSS, RowText, formatTime, watchSize } from "../common";
import { ScrollingPlainLyrics, SyncedLyrics, useLyrics } from "../LyricsView";
import {
  DetailsPanel,
  OnAirPanel,
  PANELS,
  RelatedPanel,
  SetlistPanel,
  Visualizer,
  isMusic,
  isTalk,
  useDetails,
} from "./nowplaying";
import { CENTER_CSS, Cover, Notice, SectionTitle, focusInto, snapRows } from "./parts";
import {
  CollectionPage,
  QueueList,
  SearchPage,
  BrowseIndex,
  TabPage,
  cardKey,
  sectionKey,
  centerNav,
  centerStack,
  focusKeyFor,
  popCenter,
  viewsOf,
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
  // the devices are looked for while Now Playing shows, so X opens the list at once
  useEffect(() => {
    if (castable) prepareCastMenu();
  }, [castable]);
  return (
    <Focusable
      style={{ flex: 1, minWidth: 0 }}
      onGamepadFocus={() => castable && prepareCastMenu()}
      onSecondaryButton={(e: any) => {
        e?.stopPropagation?.(); // or Steam's page behind takes the X too ("Change artwork")
        if (castable) showCastMenu(e?.currentTarget);
      }}
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
const Transport: FC<{ size?: "large" | "normal"; focusPlay?: boolean; kind?: NowPlayingKind }> = ({
  size = "large",
  focusPlay,
  kind = "music",
}) => {
  const state = useStore(player.state);
  const big = size === "large";
  const side = { height: big ? 48 : 40, fontSize: big ? 17 : 15 };
  const skip = { height: big ? 48 : 40, fontSize: big ? 20 : 17 };
  // talk skips back and ahead where music shuffles and repeats, with buttons just like them
  const talk = isTalk(kind);
  return (
    <Focusable
      flow-children="row"
      style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: big ? 380 : 300 }}
    >
      {talk ? (
        <IconButton look="plain" label="Back 10 seconds" onClick={() => player.skipBy(-10)} style={skip}>
          <MdReplay10 />
        </IconButton>
      ) : (
        <IconButton
          look="plain"
          label="Shuffle"
          active={state.shuffle}
          onClick={() => player.setShuffle(!state.shuffle)}
          style={side}
        >
          <FaRandom />
        </IconButton>
      )}
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
      {talk ? (
        <IconButton look="plain" label="Ahead 30 seconds" onClick={() => player.skipBy(30)} style={skip}>
          <MdForward30 />
        </IconButton>
      ) : (
        <IconButton
          look="plain"
          label="Repeat"
          active={state.repeat !== "off"}
          onClick={() => player.cycleRepeat()}
          style={side}
        >
          {state.repeat === "one" ? <MdRepeatOne /> : <FaRedo />}
        </IconButton>
      )}
    </Focusable>
  );
};

const SPEEDS = [0.8, 1, 1.2, 1.5, 1.75, 2];

/** How fast talk plays: A goes to the next speed, round again after the fastest. */
const SpeedButton: FC = () => {
  const { speed } = useStore(player.state);
  const next = SPEEDS.find((x) => x > speed + 0.01) ?? SPEEDS[0];
  return (
    <Focusable
      className="mc-pill"
      noFocusRing
      onActivate={() => player.setSpeed(next)}
      onClick={() => player.setSpeed(next)}
      onOKActionDescription={`Speed ${next}×`}
      style={{
        padding: "6px 14px",
        borderRadius: 16,
        fontSize: 15,
        fontWeight: 700,
        background: "rgba(255, 255, 255, 0.08)",
      }}
    >
      {`${speed}×`}
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

/** What Now Playing shows beside the cover, per kind of source; kept while the media center is closed. */
const nowPlayingSide = new Store<Partial<Record<NowPlayingKind, string>>>({});
const sideOf = (kind: NowPlayingKind, chosen: Partial<Record<NowPlayingKind, string>>) =>
  PANELS[kind].find((p) => p.id === chosen[kind]) ?? PANELS[kind][0];
/** R2: the next panel, round again after the last. */
const nextSide = (kind: NowPlayingKind) => {
  const panels = PANELS[kind];
  const at = panels.indexOf(sideOf(kind, nowPlayingSide.get()));
  nowPlayingSide.update({ [kind]: panels[(at + 1) % panels.length].id });
};

// GamepadButton.TRIGGER_RIGHT from @decky/ui (an ambient enum, which isolated builds can't import)
const R2 = 8;

/** The panels' names beside the title (Lyrics | Queue, On air | Station...): R2, or pressing one. */
const SideSwitch: FC<{ kind: NowPlayingKind; upNext: number }> = ({ kind, upNext }) => {
  const side = sideOf(kind, useStore(nowPlayingSide)).id;
  const pick = (id: string) => nowPlayingSide.update({ [kind]: id });
  return (
    <Focusable flow-children="row" style={{ display: "flex", gap: 6, flexShrink: 0 }}>
      {PANELS[kind].map(({ id, label }) => (
        <Focusable
          key={id}
          className={side === id ? "mc-pill mc-on" : "mc-pill"}
          noFocusRing
          onActivate={() => pick(id)}
          onClick={() => pick(id)}
          onOKActionDescription={label}
          style={{
            padding: "5px 12px",
            borderRadius: 14,
            fontSize: 13,
            fontWeight: 600,
            color: side === id ? "#0e141b" : "#dcdedf",
            background: side === id ? "#dcdedf" : "rgba(255, 255, 255, 0.08)",
          }}
        >
          {id === "queue" && upNext ? `${label} · ${upNext}` : label}
        </Focusable>
      ))}
    </Focusable>
  );
};

const NowPlayingTab: FC<{ onSleep: () => void; lyricsSize: CenterSettings["lyrics"] }> = ({ onSleep, lyricsSize }) => {
  const [syncedSize, plainSize] = LYRICS_SIZES[lyricsSize] ?? LYRICS_SIZES.medium;
  const state = useStore(player.state);
  const item = state.queue[state.index];
  const services = useStore(pluginState)?.services;
  const kind: NowPlayingKind = services?.find((s) => s.id === item?.instanceId)?.now_playing ?? "music";
  const side = sideOf(kind, useStore(nowPlayingSide)).id;
  const upNext = Math.max(player.playOrder.length - 1 - player.playOrder.indexOf(state.index), 0);
  const lyrics = useLyrics(side === "lyrics" ? item : undefined);
  const details = useDetails(item, ["notes", "station", "related", "info"].includes(side));
  const page = useRef<HTMLDivElement>(null);
  const room = useFitBelow(page);
  const area = useRef<HTMLDivElement>(null);
  const height = useHeight(area);
  const art = item?.track.art ? mediaUrl("art", item.instanceId, item.track.art) : undefined;
  const subtitle = item ? [item.track.artist, item.track.album].filter(Boolean).join(" · ") : "";
  // a book's chapters come as its "lyrics"
  const book = kind === "book";
  const message = !item
    ? null
    : lyrics === undefined
      ? book
        ? "Looking for chapters…"
        : "Looking for lyrics…"
      : lyrics === null
        ? book
          ? "No chapters in this part"
          : "No lyrics for this track"
        : "error" in lyrics
          ? `Couldn't load ${book ? "chapters" : "lyrics"}: ${lyrics.error}`
          : null;

  return (
    <div ref={page} style={{ height: room || undefined, boxSizing: "border-box", padding: "1vh 2.8vw 1.5vh" }}>
      {!item ? (
        <Notice>Nothing playing. Find something in Home or Search.</Notice>
      ) : (
        <Focusable
          // Y and R2 work without a hint: the moon and the Lyrics/Queue pills are on screen, and the
          // hint bar keeps to what can't be seen
          onOptionsButton={onSleep}
          onButtonDown={(e: any) => {
            if (e?.detail?.button === R2) {
              nextSide(kind);
              e.stopPropagation?.();
            }
          }}
          style={{ display: "flex", flexDirection: "column", gap: "2vh", height: "100%" }}
        >
          <div style={{ flex: 1, minHeight: 0, display: "flex", gap: "3.5vw" }}>
            <div
              style={{
                height: "100%",
                // on a squarer screen (docked) the row's height would take most of its width
                maxHeight: "40vw",
                aspectRatio: "1",
                flexShrink: 0,
                borderRadius: 8,
                // a station's logo is small: whole, on a dark square, rather than blown up to fill it
                background: !art
                  ? "rgba(255, 255, 255, 0.08)"
                  : kind === "radio"
                    ? `center / 62% no-repeat url("${art}"), rgba(255, 255, 255, 0.06)`
                    : `center / cover no-repeat url("${art}")`,
                boxShadow: "0 8px 30px rgba(0, 0, 0, 0.5)",
              }}
            />
            <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "1.5vh" }}>
              <div style={{ display: "flex", alignItems: "flex-start", gap: 16 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <RowText size="large" scroll="always" title={item.track.title} subtitle={subtitle} />
                </div>
                <SideSwitch kind={kind} upNext={upNext} />
                <span
                  onClick={onSleep}
                  title="Screen off"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    fontSize: 15,
                    opacity: 0.55,
                    cursor: "pointer",
                    padding: 6,
                  }}
                >
                  <FaMoon />
                </span>
              </div>
              <div ref={area} style={{ flex: 1, minHeight: 0, overflowY: side === "lyrics" ? "hidden" : "auto" }}>
                {side === "queue" ? (
                  <QueueList />
                ) : side === "onair" ? (
                  <OnAirPanel item={item} />
                ) : side === "station" ? (
                  <DetailsPanel details={details} show="facts" empty="Nothing more about this station" />
                ) : side === "info" ? (
                  <DetailsPanel details={details} show="facts" empty="Nothing more about this file" />
                ) : side === "notes" ? (
                  <DetailsPanel details={details} show="notes" empty="No notes for this one" />
                ) : side === "related" ? (
                  <RelatedPanel
                    item={item}
                    details={details}
                    empty={kind === "radio" ? "No stations like this one found" : "Nothing more from this show"}
                  />
                ) : side === "setlist" ? (
                  <SetlistPanel />
                ) : (lyrics === null || (lyrics && "error" in lyrics)) &&
                  isMusic(kind) &&
                  !state.castingTo &&
                  height > 0 ? (
                  // no lyrics, or the lyrics sites didn't answer (LRCLIB's 503s): the music, drawn
                  <Visualizer
                    height={height}
                    caption={lyrics === null ? "No lyrics for this track" : "Lyrics aren't available right now"}
                  />
                ) : message ? (
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
              <Transport focusPlay kind={kind} />
              {/* balances the volume, so the transport sits in the middle (talk's speed at its end) */}
              <div style={{ flex: 1, display: "flex", justifyContent: "flex-end" }}>
                {isTalk(kind) && <SpeedButton />}
              </div>
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

/**
 * One browsing tab (one of the source's own, or Search): its history's latest view, with the controller
 * put back where it belongs.
 */
const Browse: FC<{ instanceId: string; tab: string; sections: CenterTabInfo[] }> = ({ instanceId, tab, sections }) => {
  const stack = centerStack(instanceId, tab);
  const views = viewsOf(useStore(centerNav), stack);
  const view = views[views.length - 1];
  const box = useRef<HTMLDivElement>(null);
  const viewKey =
    view.type === "collection"
      ? cardKey(view.collection)
      : view.type === "search"
        ? view.query
        : view.type === "section"
          ? sectionKey(view.tab)
          : "home";
  useEffect(() => focusInto(box.current, focusKeyFor(stack, views.length - 1)), [viewKey, views.length]);
  useEffect(() => snapRows(box.current), []);
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
        tab === BROWSE.id && sections.length !== 1 ? (
          <BrowseIndex sections={sections} stack={stack} />
        ) : (
          // Browse with one section is that section
          <TabPage instanceId={instanceId} tab={tab === BROWSE.id ? sections[0].id : tab} stack={stack} />
        )
      ) : view.type === "section" ? (
        <TabPage key={view.tab} instanceId={instanceId} tab={view.tab} stack={stack} />
      ) : view.type === "search" ? (
        <SearchPage key={view.query} instanceId={instanceId} stack={stack} query={view.query} />
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

/** The media center's own tabs, around the source's Home */
const PLAYING: CenterTabInfo = { id: "playing", title: "Now Playing" };
const BROWSE: CenterTabInfo = { id: "browse", title: "Browse" };
const SEARCH: CenterTabInfo = { id: "search", title: "Search" };
const SETTINGS: CenterTabInfo = { id: "settings", title: "Settings" };

/** A source's own sections past its Home (albums, genres, charts... see MusicService.center_tabs): in Browse. */
const browseSections = (source: ServiceInfo | undefined): CenterTabInfo[] =>
  (source?.tabs ?? []).filter((t) => ![PLAYING.id, "home", SEARCH.id, SETTINGS.id].includes(t.id));

/**
 * Always the same five, whatever the source: Now Playing, its Home, Browse (its other sections), Search,
 * Settings. A row of every source's sections up top was too many to take in, and moved with the source.
 */
function centerTabs(source: ServiceInfo | undefined): CenterTabInfo[] {
  if (!source) return [PLAYING, SETTINGS];
  const home = source.tabs.find((t) => t.id === "home");
  return [PLAYING, ...(home ? [home] : []), ...(browseSections(source).length ? [BROWSE] : []), SEARCH, SETTINGS];
}

const DEFAULT_CENTER: CenterSettings = {
  open_on: "last",
  background: "art",
  cards: "medium",
  lyrics: "medium",
  show_library: true,
  show_browse: true,
  idle_off: 0,
};

// GamepadButton.SELECT from @decky/ui (an ambient enum, which isolated builds can't import)
const VIEW_BUTTON = 13;

/** Leaves the media center for the Quick Access panel, where MusicDeck is. */
const backToPanel = () => leaveMediaCenter(openMusicDeck);

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
  const current: Partial<CenterSettings> = { ...pluginState.get()?.center, ...patch };
  // only the choices there are: the backend refuses the whole setting over one it doesn't know
  const center = Object.fromEntries(
    (Object.keys(DEFAULT_CENTER) as (keyof CenterSettings)[]).map((k) => [k, current[k] ?? DEFAULT_CENTER[k]]),
  ) as unknown as CenterSettings;
  await setSetting("center", center);
  await refreshState();
}

/** A group of settings here: the heading this page's own groups use, a little space above. */
const CenterSection: SectionFrame = ({ title, children }) => (
  <div style={{ marginTop: 28 }}>
    <SectionTitle>{title}</SectionTitle>
    {children}
  </div>
);

/**
 * Settings: the equalizer, the media center's own choices, then playback, scrobbling and games (as in
 * Decky → MusicDeck), in Steam's settings style.
 */
const SettingsPage: FC = () => {
  const state = useStore(pluginState);
  const center = { ...DEFAULT_CENTER, ...state?.center };
  return (
    <div style={{ maxWidth: 760 }}>
      <Equalizer />
      <SectionTitle>Media center</SectionTitle>
      {CHOICES.map((c) => (
        <DropdownItem
          key={c.key}
          label={c.label}
          rgOptions={c.options}
          selectedOption={center[c.key]}
          onChange={(o) => setCenter({ [c.key]: o.data })}
        />
      ))}
      {state && <PlaybackSections state={state} Section={CenterSection} />}
      <div style={{ fontSize: 13, opacity: 0.55, marginTop: 24, lineHeight: 1.5 }}>
        Sources are added and chosen in Decky → MusicDeck. Press the View button to go back to the Quick Access panel.
      </div>
    </div>
  );
};

/**
 * MusicDeck's media center, full screen in Steam's main window and laid out like Steam's own library:
 * tabs along the top (L1/R1): Now Playing (lyrics or the queue beside the cover), Home (the source's
 * rows as shelves of covers, then its genres, moods and charts), the Library's playlists and
 * favourites, Search, and Settings (the equalizer and the center's own choices); albums and
 * playlists open as pages with their track lists. Start plays or pauses from anywhere; B steps back
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
  const source = state?.services.find((s) => s.id === live);
  const tabs = centerTabs(source);
  // a tab the source (switched since) doesn't have: its first ("library" was a tab of every source once)
  const shown = tabs.some((t) => t.id === tab)
    ? tab
    : [PLAYING.id, SETTINGS.id].includes(tab)
      ? tab
      : (tabs.find((t) => t.id === "home") ?? tabs[1] ?? PLAYING).id;
  useIdleScreenOff(center.idle_off, shown === "playing" && playing && !screenOff, sleep);

  const content = (id: CenterTab): ReactNode =>
    id === PLAYING.id ? (
      <NowPlayingTab onSleep={sleep} lyricsSize={center.lyrics} />
    ) : id === SETTINGS.id ? (
      <SettingsPage />
    ) : live ? (
      // keyed by source: a tab of the same name on another source is another page
      <Browse
        key={`${live}/${id}`}
        instanceId={live}
        tab={id}
        sections={id === BROWSE.id ? browseSections(source) : []}
      />
    ) : (
      <Notice>Add a music source in Decky → MusicDeck to browse it here.</Notice>
    );

  return (
    <Focusable
      className="mc-center"
      // Start plays or pauses anywhere (unhinted: the player's own button shows which it'll do)
      onMenuButton={() => player.toggle()}
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
            tabs={tabs.map(({ id, title }) => ({
              id,
              title,
              // Now Playing fits the screen itself; the others scroll, down only (clip, not hidden: a
              // card growing at the edge mustn't make the whole tab scroll sideways)
              content:
                id === PLAYING.id ? (
                  content(id)
                ) : (
                  <div style={{ padding: "2vh 2.8vw 3vh", overflowX: "clip" }}>{content(id)}</div>
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

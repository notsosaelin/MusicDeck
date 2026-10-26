import { DropdownItem, Focusable, SliderField, Tabs, footerClasses } from "@decky/ui";
import { FC, ReactNode, RefObject, useEffect, useRef, useState } from "react";
import { showingMusicDeck } from "../../songToasts";
import {
  FaCheck,
  FaHeart,
  FaMoon,
  FaPause,
  FaPlay,
  FaPlus,
  FaRandom,
  FaRegHeart,
  FaStop,
  FaRedo,
  FaStepBackward,
  FaStepForward,
  FaVolumeDown,
  FaVolumeMute,
  FaVolumeUp,
} from "react-icons/fa";
import { MdForward30, MdRepeatOne, MdReplay10 } from "react-icons/md";
import {
  CenterSettings,
  CenterTabInfo,
  Collection,
  NowPlayingKind,
  Section,
  ServiceInfo,
  Track,
  getCenterTab,
  setSetting,
} from "../../api";
import { CenterTab, browsePreview, centerTab, leaveMediaCenter } from "../../nav";
import { hasFeature, player } from "../../player";
import { likeWords, showPlaylistPicker, toggleStar } from "../TrackMenu";
import { useLoadedArt } from "../../art";
import { Store, mediaUrl, pluginState, useStore } from "../../store";
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
import { DEFAULT_CENTER, setCenter } from "./center";
import { CENTER_CSS, Cover, Notice, SectionTitle, StationArt, focusInto, snapRows } from "./parts";
import { SourcePager } from "./sources";
import { librarySources } from "../../library";
import { playCollection, useResult } from "../Browser";
import { useOnAir } from "../NowPlaying";
import {
  AllPage,
  CollectionPage,
  QueueList,
  SearchPage,
  BrowseIndex,
  TabPage,
  cardKey,
  sectionKey,
  allKey,
  centerNav,
  centerStack,
  DevicesShelf,
  focusKeyFor,
  popCenter,
  viewsOf,
} from "./pages";

/**
 * The height of an element, kept current. The page renders into Steam's main window while this code
 * runs in its shared context, so `window` here isn't the page's window; measure the element instead.
 * `shown`: whether the element is on the page now; it's measured again when it comes (a page opened
 * before the player had its queue back, after a reload, shows "Nothing playing" first).
 */
function useHeight(ref: RefObject<HTMLElement | null>, shown: boolean) {
  const [height, setHeight] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setHeight(el.clientHeight);
    measure();
    return watchSize(el, measure);
  }, [shown]);
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
  if (kind === "radio") return <RadioTransport big={big} focusPlay={focusPlay} />;
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

/**
 * A station's controls: favourite it, the station before and after (in a list of your stations; one
 * played on its own has none), play or stop (live radio doesn't pause: it starts again where it is now),
 * and add it to a list of stations.
 */
const RadioTransport: FC<{ big: boolean; focusPlay?: boolean }> = ({ big, focusPlay }) => {
  const state = useStore(player.state);
  const item = state.queue[state.index];
  const order = player.playOrder;
  const at = order.indexOf(state.index);
  const side = { height: big ? 48 : 40, fontSize: big ? 17 : 15 };
  const skip = { height: big ? 48 : 40, fontSize: big ? 20 : 17 };
  const starrable = !!item && hasFeature(item.instanceId, "star");
  return (
    <Focusable
      flow-children="row"
      style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: big ? 380 : 300 }}
    >
      <IconButton
        look="plain"
        label={item?.track.starred ? "Unfavourite" : "Favourite"}
        active={!!item?.track.starred}
        disabled={!starrable}
        onClick={() => item && toggleStar(item.instanceId, item.track)}
        style={side}
      >
        {item?.track.starred ? <FaHeart /> : <FaRegHeart />}
      </IconButton>
      <IconButton
        look="plain"
        label="Previous station"
        disabled={at <= 0}
        onClick={() => player.previous()}
        style={skip}
      >
        <FaStepBackward />
      </IconButton>
      <IconButton
        look="round"
        preferredFocus={focusPlay}
        label={state.playing ? "Stop" : "Play"}
        onClick={() => player.toggle()}
        style={{ fontSize: big ? 20 : 17 }}
      >
        {state.playing ? <FaStop /> : <FaPlay style={{ marginLeft: 3 }} />}
      </IconButton>
      <IconButton
        look="plain"
        label="Next station"
        disabled={at < 0 || at >= order.length - 1}
        onClick={() => player.next()}
        style={skip}
      >
        <FaStepForward />
      </IconButton>
      <IconButton
        look="plain"
        label="Add to a list"
        disabled={!item}
        onClick={() => item && showPlaylistPicker(item, window)}
        style={side}
      >
        <FaPlus />
      </IconButton>
    </Focusable>
  );
};

/**
 * Likes the song, in the source's own words (Save to Liked Songs, Like, Add to favourites): a plus, a tick in the accent
 * once it's liked. Only for a source that keeps likes.
 */
const LikeButton: FC = () => {
  const { queue, index } = useStore(player.state);
  const item = queue[index];
  if (!item || !hasFeature(item.instanceId, "star")) return null;
  const liked = !!item.track.starred;
  const words = likeWords(item.instanceId);
  return (
    <IconButton
      look="plain"
      label={liked ? words.remove : words.add}
      active={liked}
      onClick={() => toggleStar(item.instanceId, item.track)}
      style={{ height: 48, fontSize: 17 }}
    >
      {liked ? <FaCheck /> : <FaPlus />}
    </IconButton>
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

/** The panels beside the title (Lyrics, Queue; On air, Station...) as dots, the chosen one a white pill; the focused
 *  one lights up, its name in the footer's hint. R2 goes round them, or pressing one. */
const SideSwitch: FC<{ kind: NowPlayingKind; upNext: number }> = ({ kind, upNext }) => {
  const side = sideOf(kind, useStore(nowPlayingSide));
  const pick = (id: string) => nowPlayingSide.update({ [kind]: id });
  const name = (id: string, label: string) => (id === "queue" && upNext ? `${label} · ${upNext}` : label);
  return (
    <Focusable flow-children="row" style={{ display: "flex", alignItems: "center", flexShrink: 0 }}>
      {PANELS[kind].map(({ id, label }) => (
        <Focusable
          key={id}
          className={side.id === id ? "mc-dot mc-on" : "mc-dot"}
          noFocusRing
          onActivate={() => pick(id)}
          onClick={() => pick(id)}
          onOKActionDescription={name(id, label)}
        >
          <span />
        </Focusable>
      ))}
    </Focusable>
  );
};

/**
 * A turntable at rest, where the cover goes: a record with its grooves, the tone arm parked off it, and
 * on the record's label the cover of what Play would start (Steam's blue without one).
 */
const RestingRecord: FC<{ cover?: string }> = ({ cover }) => (
  <svg viewBox="0 0 100 100" style={{ width: "100%", height: "100%", display: "block" }}>
    <defs>
      <clipPath id="md-record-label">
        <circle cx="44" cy="52" r="15" />
      </clipPath>
      <radialGradient id="md-record-shine" cx="0.35" cy="0.3" r="0.8">
        <stop offset="0" stopColor="#2a3340" />
        <stop offset="1" stopColor="#0a0d11" />
      </radialGradient>
    </defs>
    <rect x="1" y="1" width="98" height="98" rx="5" fill="rgba(255, 255, 255, 0.045)" />
    <circle cx="44" cy="52" r="41" fill="url(#md-record-shine)" stroke="rgba(255, 255, 255, 0.08)" strokeWidth="0.6" />
    {[37, 33, 29, 25, 21].map((r) => (
      <circle key={r} cx="44" cy="52" r={r} fill="none" stroke="rgba(255, 255, 255, 0.055)" strokeWidth="0.5" />
    ))}
    {cover ? (
      <image
        href={cover}
        x="29"
        y="37"
        width="30"
        height="30"
        preserveAspectRatio="xMidYMid slice"
        clipPath="url(#md-record-label)"
      />
    ) : (
      <circle cx="44" cy="52" r="15" fill="#1a9fff" opacity="0.8" />
    )}
    <circle cx="44" cy="52" r="15" fill="none" stroke="rgba(0, 0, 0, 0.35)" strokeWidth="0.8" />
    <circle cx="44" cy="52" r="1.6" fill="#0e141b" />
    {/* the tone arm, parked beside the record */}
    <circle cx="88" cy="14" r="5" fill="#2c3440" stroke="rgba(255, 255, 255, 0.15)" strokeWidth="0.6" />
    <circle cx="88" cy="14" r="1.8" fill="#4a5564" />
    <path
      d="M 88 14 L 91 64 L 87.5 74"
      fill="none"
      stroke="rgba(220, 222, 223, 0.55)"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
    <rect x="84.5" y="72" width="6" height="7" rx="1" fill="#4a5564" transform="rotate(20 87.5 75.5)" />
  </svg>
);

/**
 * Now Playing with nothing playing: the player at rest, laid out as when something plays. A turntable
 * where the cover goes, the source's name, the visualizer's resting dots where the lyrics
 * go, the seek bar empty and the controls dimmed, but Play, which starts what was played last on this
 * source (its cover on the record's label), or opens Home when there's nothing to go back to.
 */
const NothingPlaying: FC = () => {
  const state = useStore(pluginState);
  const preview = useStore(browsePreview).id;
  const instanceId = preview ?? state?.active_service ?? "";
  const source = state?.services.find((s) => s.id === instanceId);
  const home = useResult<Section[]>(`${instanceId}:tab:home`, () =>
    instanceId ? getCenterTab(instanceId, "home") : Promise.resolve({ ok: false as const, error: "" }),
  );
  const jump = home?.ok ? home.data.find((s) => s.title === "Jump back in") : undefined;
  const last: Collection | undefined = jump?.items[0];
  const station: Track | undefined = last ? undefined : jump?.tracks?.[0];
  const art = last?.art ?? station?.art;
  // on the label once it has loaded: one that doesn't (a song off a player without a cover) leaves it blue
  const cover = useLoadedArt(art && instanceId ? mediaUrl("art", instanceId, art) : undefined);
  const play = () => {
    if (last) playCollection(instanceId, last);
    else if (station) player.playList(instanceId, [station], 0);
    else centerTab.set({ tab: "home" });
  };
  // Play takes the controller (Steam's own pick is the first thing, the volume slider)
  const playBox = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const timer = setTimeout(() => playBox.current?.querySelector<HTMLElement>("button, [tabindex]")?.focus(), 300);
    return () => clearTimeout(timer);
  }, []);
  const dim = { opacity: 0.28, fontSize: 18, display: "flex", alignItems: "center" } as const;
  const time = { fontSize: 13, opacity: 0.4, width: 44, flexShrink: 0 } as const;
  return (
    <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: "2vh", height: "100%" }}>
      <div style={{ flex: 1, minHeight: 0, display: "flex", gap: "3.5vw" }}>
        <div style={{ height: "100%", maxHeight: "40vw", aspectRatio: "1", flexShrink: 0 }}>
          <RestingRecord cover={cover} />
        </div>
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "1.5vh" }}>
          <RowText size="large" title={source?.name ?? ""} subtitle="" />
          <div style={{ flex: 1, minHeight: 0, display: "flex", alignItems: "center" }}>
            <div style={{ width: "100%" }}>
              <Visualizer height={120} />
            </div>
          </div>
        </div>
      </div>
      <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {/* the seek bar, empty */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, height: 40 }}>
          <span style={{ ...time, textAlign: "right" }}>0:00</span>
          <div style={{ flex: 1, height: 6, borderRadius: 3, background: "rgba(255, 255, 255, 0.12)" }} />
          <span style={time}>0:00</span>
        </div>
        <Focusable flow-children="row" style={{ display: "flex", alignItems: "center", gap: "2vw" }}>
          <Volume />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: 380 }}>
            <span style={dim}>
              <FaRandom />
            </span>
            <span style={{ ...dim, fontSize: 20 }}>
              <FaStepBackward />
            </span>
            <span ref={playBox} style={{ display: "flex" }}>
              <IconButton
                look="round"
                preferredFocus
                label={last ? `Play ${last.title}` : station ? `Play ${station.title}` : "Home"}
                onClick={play}
                style={{ fontSize: 20 }}
              >
                <FaPlay style={{ marginLeft: 3 }} />
              </IconButton>
            </span>
            <span style={{ ...dim, fontSize: 20 }}>
              <FaStepForward />
            </span>
            <span style={dim}>
              <FaRedo />
            </span>
          </div>
          <div style={{ flex: 1 }} />
        </Focusable>
      </Focusable>
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
  const details = useDetails(item, ["notes", "station", "related", "info", "onair"].includes(side));
  // a station's song (when it says): for On air, and its cover in the station's square
  const song = useOnAir(kind === "radio" ? item : undefined, state.playing);
  const page = useRef<HTMLDivElement>(null);
  const room = useFitBelow(page);
  const area = useRef<HTMLDivElement>(null);
  const height = useHeight(area, !!item);
  // the queue, a station's neighbours: scrolled by whole lines, as the browsing tabs are
  useEffect(() => snapRows(area.current, { under: 0 }), [!!item]);
  const art = item?.track.art ? mediaUrl("art", item.instanceId, item.track.art) : undefined;
  const subtitle = item ? [item.track.artist, item.track.album].filter(Boolean).join(" · ") : "";
  // a book's chapters come as its "lyrics"
  const book = kind === "book";
  const message = !item
    ? null
    : lyrics === undefined
      ? null
      : lyrics === null
        ? null
        : "error" in lyrics
          ? book
            ? `Couldn't load chapters: ${lyrics.error}`
            : null
          : null;
  // songs say nothing about their lyrics: they show, or the visualizer does (nothing while casting)

  return (
    <div ref={page} style={{ height: room || undefined, boxSizing: "border-box", padding: "1vh 2.8vw 1.5vh" }}>
      {!item ? (
        <NothingPlaying />
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
            {kind === "radio" ? (
              // a station: its logo as big as the square (or the song's cover, when it's found one)
              <StationArt
                url={song?.art ? mediaUrl("art", item.instanceId, song.art) : art}
                name={item.track.title}
                full={!!song?.art}
                style={{
                  height: "100%",
                  maxHeight: "40vw",
                  aspectRatio: "1",
                  flexShrink: 0,
                  borderRadius: 8,
                  boxShadow: "0 8px 30px rgba(0, 0, 0, 0.5)",
                }}
              />
            ) : (
              <div
                style={{
                  height: "100%",
                  // on a squarer screen (docked) the row's height would take most of its width
                  maxHeight: "40vw",
                  aspectRatio: "1",
                  flexShrink: 0,
                  borderRadius: 8,
                  background: art ? `center / cover no-repeat url("${art}")` : "rgba(255, 255, 255, 0.08)",
                  boxShadow: "0 8px 30px rgba(0, 0, 0, 0.5)",
                }}
              />
            )}
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
              {/* lyrics and On air fit themselves to the room; the lists scroll */}
              <div
                ref={area}
                style={{ flex: 1, minHeight: 0, overflowY: side === "lyrics" || side === "onair" ? "hidden" : "auto" }}
              >
                {side === "queue" ? (
                  <QueueList />
                ) : side === "onair" ? (
                  <OnAirPanel item={item} song={song} details={details} height={height} />
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
                  <Visualizer height={height} />
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
              {/* balances the volume, so the transport sits in the middle (like, and talk's speed, at its end; a
                  station's favourite is in its transport) */}
              <Focusable flow-children="row" style={{ flex: 1, display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 12 }}>
                {kind !== "radio" && <LikeButton />}
                {isTalk(kind) && <SpeedButton />}
              </Focusable>
            </Focusable>
          </Focusable>
        </Focusable>
      )}
    </div>
  );
};

/**
 * The media center's own height at its tallest (the screen) and whether it's now much shorter: Steam's
 * on-screen keyboard doesn't shorten the window, it shrinks the page the media center is in, from below.
 */
function useAreaHeight(ref: RefObject<HTMLElement | null>): { full: number; short: boolean } {
  const [size, setSize] = useState({ full: 0, now: 0 });
  useEffect(() => {
    const el = ref.current;
    const win = el?.ownerDocument.defaultView;
    if (!el || !win) return;
    const measure = () => setSize((was) => ({ full: Math.max(was.full, el.offsetHeight), now: el.offsetHeight }));
    measure();
    const Observer: typeof ResizeObserver = (win as any).ResizeObserver ?? ResizeObserver;
    const watch = new Observer(measure);
    watch.observe(el);
    return () => watch.disconnect();
  }, []);
  return { full: size.full, short: size.full > 0 && size.now < size.full * 0.8 };
}

/**
 * Where Steam's own header (search, notifications, clock) ends and its hint bar starts, in the media
 * center's own coordinates, as drawn now. Both are a set size in Steam's units, not a share of the
 * screen, so fractions of the height that fit the Steam Deck's screen left a gap above the hint bar on
 * taller ones (2560x1600). The hint bar is Steam's BasicFooter; the header, the bar across the top of the
 * window. Null until they're found (the fractions stand in).
 */
function useSteamBars(ref: RefObject<HTMLElement | null>): { top: number; bottom: number } | null {
  const [bars, setBars] = useState<{ top: number; bottom: number } | null>(null);
  useEffect(() => {
    const el = ref.current;
    const doc = el?.ownerDocument;
    const win = doc?.defaultView;
    if (!el || !doc || !win) return;
    const measure = () => {
      const area = el.getBoundingClientRect();
      if (!area.height) return;
      // a bar of Steam's along the window's edge at height y: not ours, across most of it, not tall
      const barAt = (y: number) =>
        doc
          .elementsFromPoint(win.innerWidth / 2, y)
          .filter((h) => !el.contains(h))
          .map((h) => h.getBoundingClientRect())
          .find(
            (r) =>
              (r.top <= 1 || r.bottom >= win.innerHeight - 1) &&
              r.width > win.innerWidth / 2 &&
              r.height > 8 &&
              r.height < win.innerHeight / 5,
          );
      const footer =
        doc.querySelector<HTMLElement>(`.${footerClasses.BasicFooter}`)?.getBoundingClientRect() ??
        barAt(win.innerHeight - 2);
      const header = barAt(2);
      const bottom =
        footer && footer.height > 8 && footer.top > area.top + area.height / 2 ? area.bottom - footer.top : 0;
      const top = header ? header.bottom - area.top : 0;
      setBars(bottom > 0 && top > 0 ? { top: Math.round(top), bottom: Math.round(bottom) } : null);
    };
    measure();
    // Steam lays its bars out after the page arrives; and the screen can change size
    const later = win.setTimeout(measure, 500);
    const stop = watchSize(el, measure);
    return () => {
      win.clearTimeout(later);
      stop();
    };
  }, []);
  return bars;
}

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
const Browse: FC<{
  instanceId: string;
  tab: string;
  sections: CenterTabInfo[];
  /** a source's Browse: its Home's rows first */
  withHome?: boolean;
  /** a device plugged in: its page is where Browse starts */
  root?: Collection;
  /** Your library: what's plugged in, a row above the rest (its pages are Local files') */
  devicesFrom?: string;
}> = ({ instanceId, tab, sections, withHome, root, devicesFrom }) => {
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
          : view.type === "all"
            ? allKey(view.title)
            : "home";
  useEffect(() => {
    // walking along Browse's source dots swaps what's under them: the controller stays on the dots
    if ((box.current?.ownerDocument.activeElement as HTMLElement | null)?.closest?.(".mc-sources")) return;
    return focusInto(box.current, focusKeyFor(stack, views.length - 1));
  }, [viewKey, views.length]);
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
        root ? (
          <CollectionPage instanceId={instanceId} stack={stack} collection={root} />
        ) : withHome ? (
          // its sections come in with its Home's rows, not before them (the controller lands on the first row)
          <TabPage
            instanceId={instanceId}
            tab="home"
            stack={stack}
            after={<BrowseIndex instanceId={instanceId} sections={sections} stack={stack} />}
          />
        ) : devicesFrom ? (
          <>
            <DevicesShelf instanceId={devicesFrom} stack={stack} />
            <BrowseIndex instanceId={instanceId} sections={sections} stack={stack} />
          </>
        ) : tab === BROWSE.id && sections.length !== 1 ? (
          <BrowseIndex instanceId={instanceId} sections={sections} stack={stack} />
        ) : (
          // Browse with one section is that section; Home is the one library's, every source's
          <TabPage instanceId={instanceId} tab={tab === BROWSE.id ? sections[0].id : tab === "home" ? "library:home" : tab} stack={stack} />
        )
      ) : view.type === "section" ? (
        <TabPage key={view.tab} instanceId={instanceId} tab={view.tab} stack={stack} />
      ) : view.type === "search" ? (
        <SearchPage key={view.query} instanceId={instanceId} stack={stack} query={view.query} />
      ) : view.type === "all" ? (
        <AllPage
          key={allKey(view.title)}
          instanceId={view.instanceId ?? instanceId}
          stack={stack}
          title={view.title}
          items={view.items}
          tracks={view.tracks}
        />
      ) : (
        <CollectionPage
          key={view.collection.kind + view.collection.id}
          // one from the one library's lists is its own source's
          instanceId={view.collection.source ?? instanceId}
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

/** Browse's "Your library": the one library's Your music (every source's playlists, albums, artists, songs). */
const librarySections = (): CenterTabInfo[] =>
  (pluginState.get()?.library_tabs ?? []).map((t): CenterTabInfo => ({ id: `library:${t.id}`, title: t.title, group: "yours" }));

/** A source's whole Browse past its Home: every section it has (its library, genres, charts...). */
const ownSections = (source: ServiceInfo): CenterTabInfo[] =>
  source.tabs.filter((t) => ![PLAYING.id, "home", SEARCH.id, SETTINGS.id].includes(t.id));

/** What Browse shows: "library" (every source's, and what's plugged in) or a source's id. */
const browseOn = new Store<string>("library");

/** How long the controller rests on a source's dot before its Browse loads */
const SETTLE_MS = 300;

/**
 * Browse: Your library, then each source, one at a time (SourcePager's dots); what's plugged in (phones, players, CDs)
 * is a row of Your library. Each keeps its own pages (B goes back within it).
 */
const BrowseBySource: FC<{ live: string }> = ({ live }) => {
  const chosen = useStore(browseOn);
  const state = useStore(pluginState);
  const services = librarySources(state?.services ?? []);
  const local = services.find((s) => s.type === "local");
  const places = [{ id: "library", name: "Your library" }, ...services.map((s) => ({ id: s.id, name: s.name }))];
  const at = places.some((p) => p.id === chosen) ? chosen : "library";
  return (
    <SourcePager
      places={places}
      at={at}
      onChoose={(id) => browseOn.set(id)}
      settleMs={SETTLE_MS}
      page={(id) => {
        const from = services.find((s) => s.id === id);
        return from ? (
          <Browse instanceId={from.id} tab={BROWSE.id} sections={ownSections(from)} withHome />
        ) : (
          <Browse instanceId={live} tab={BROWSE.id} sections={librarySections()} devicesFrom={local?.id} />
        );
      }}
    />
  );
};

/**
 * Always the same five, whatever the source: Now Playing, its Home, Browse (its other sections), Search,
 * Settings. A row of every source's sections up top was too many to take in, and moved with the source.
 */
function centerTabs(source: ServiceInfo | undefined): CenterTabInfo[] {
  if (!source) return [PLAYING, SETTINGS];
  const home = source.tabs.find((t) => t.id === "home");
  return [PLAYING, ...(home ? [home] : []), BROWSE, SEARCH, SETTINGS];
}

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

/**
 * The media center's own choices (what it opens on, its look, screen off), in its Settings and in the
 * panel's: `below` puts each value under its label, for the narrow panel, so none is cut short.
 */
export const CenterChoices: FC<{ Section: SectionFrame; below?: boolean }> = ({ Section, below }) => {
  const center = { ...DEFAULT_CENTER, ...useStore(pluginState)?.center };
  return (
    <Section title="Media center">
      {CHOICES.map((c) => (
        <DropdownItem
          key={c.key}
          layout={below ? "below" : undefined}
          label={c.label}
          rgOptions={c.options}
          selectedOption={center[c.key]}
          onChange={(o) => setCenter({ [c.key]: o.data })}
        />
      ))}
    </Section>
  );
};

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
  return (
    <div style={{ maxWidth: 760 }}>
      <Equalizer />
      <CenterChoices Section={CenterSection} />
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
  // on screen: no song toasts over it
  useEffect(showingMusicDeck, []);
  const { tab } = useStore(centerTab);
  const state = useStore(pluginState);
  const center = { ...DEFAULT_CENTER, ...state?.center };
  const { queue, index, playing } = useStore(player.state);
  const [screenOff, sleep, wake] = useScreenOff();
  const root = useRef<HTMLDivElement>(null);
  const { full, short } = useAreaHeight(root);
  const bars = useSteamBars(root);
  const item = queue[index];
  const art = item?.track.art ? mediaUrl("art", item.instanceId, item.track.art) : undefined;
  const preview = useStore(browsePreview).id;
  const live = preview ?? state?.active_service ?? null;
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
      id === BROWSE.id ? (
        <BrowseBySource live={live} />
      ) : (
        // keyed by source: a tab of the same name on another source is another page
        <Browse key={`${live}/${id}`} instanceId={live} tab={id} sections={[]} />
      )
    ) : (
      <Notice>Add a music source in Decky → MusicDeck to browse it here.</Notice>
    );

  return (
    <Focusable
      ref={root}
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
          // between Steam's header and its hint bar, as drawn (until they're found, the share of the
          // screen they take on a Steam Deck); with the keyboard up (the page shorter, the hint bar behind
          // the keyboard), down to the keyboard
          top: bars ? bars.top : full ? full * 0.086 : "8.6vh",
          bottom: short ? 0 : bars ? bars.bottom : full ? full * 0.088 : "8.8vh",
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
        {/* not while the keyboard's up: the room is the search field's */}
        {shown !== "playing" && !short && <NowPlayingBar />}
      </div>
      {screenOff && <Blackout onWake={wake} />}
    </Focusable>
  );
};

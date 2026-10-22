import { Focusable, SliderField } from "@decky/ui";
import { FC, RefObject, useEffect, useRef, useState } from "react";
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
import { setSetting } from "../api";
import { player } from "../player";
import { mediaUrl, useStore } from "../store";
import { canCast, showCastMenu } from "./CastMenu";
import { ACCENT, Art, IconButton, PLAYER_CSS, RowText, formatTime } from "./common";
import { ScrollingPlainLyrics, SyncedLyrics, useLyrics } from "./LyricsView";

// GamepadButton.BUMPER_LEFT / BUMPER_RIGHT from @decky/ui (an ambient enum, which isolated builds can't import)
const L1 = 5;
const R1 = 6;
const UP_NEXT_SHOWN = 30;

type Side = "lyrics" | "next";

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
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
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

/** The tracks after the playing one, in the order they'll play. */
const UpNext: FC = () => {
  const { queue, index } = useStore(player.state);
  const order = player.playOrder;
  const at = order.indexOf(index);
  const upcoming = order.slice(at + 1, at + 1 + UP_NEXT_SHOWN).map((i) => ({ i, item: queue[i] }));
  if (!upcoming.length) return <div style={{ fontSize: 20, opacity: 0.6, paddingTop: 24 }}>Nothing up next</div>;
  return (
    <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {upcoming.map(({ i, item }) => (
        <Focusable
          key={`${i}:${item.track.id}`}
          className="md-mini"
          onActivate={() => player.jumpTo(i)}
          onClick={() => player.jumpTo(i)}
          onOKActionDescription="Play"
          style={{ display: "flex", alignItems: "center", gap: 14, padding: 8, fontSize: 18 }}
        >
          <Art instanceId={item.instanceId} art={item.track.art} size={48} />
          <RowText title={item.track.title} subtitle={item.track.artist} />
        </Focusable>
      ))}
    </Focusable>
  );
};

const SideTabs: FC<{ side: Side; onSide: (s: Side) => void; onSleep?: () => void }> = ({ side, onSide, onSleep }) => (
  <div style={{ display: "flex", gap: 28, fontSize: 18, marginBottom: 16, alignItems: "center" }}>
    {(["lyrics", "next"] as const).map((s) => (
      <div
        key={s}
        onClick={() => onSide(s)}
        style={{
          paddingBottom: 6,
          cursor: "pointer",
          color: side === s ? "#ffffff" : "rgba(255,255,255,0.5)",
          borderBottom: `2px solid ${side === s ? ACCENT : "transparent"}`,
        }}
      >
        {s === "lyrics" ? "Lyrics" : "Up next"}
      </div>
    ))}
    <span style={{ fontSize: 13, opacity: 0.4 }}>L1 / R1</span>
    {onSleep && (
      // for touch; Y does the same
      <div
        onClick={onSleep}
        style={{
          marginLeft: "auto",
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 15,
          opacity: 0.6,
          cursor: "pointer",
        }}
      >
        <FaMoon /> Screen off
      </div>
    )}
  </div>
);

let volumeSaveTimer: number | undefined;

/**
 * The player full screen, in Steam's main window: the cover and title, beside the song's lyrics or
 * what plays next (L1/R1), over the seek bar, volume and transport. Y turns the screen off while
 * the music plays; X on the volume plays it on a speaker or TV; B is Steam's own back.
 */
export const FullPlayer: FC = () => {
  const state = useStore(player.state);
  const { current, duration } = useStore(player.position);
  const item = state.queue[state.index];
  const lyrics = useLyrics(item);
  const area = useRef<HTMLDivElement>(null);
  const height = useHeight(area);
  const [side, setSide] = useState<Side>("lyrics");
  const [screenOff, sleep, wake] = useScreenOff();
  const art = item?.track.art ? mediaUrl("art", item.instanceId, item.track.art) : undefined;
  const volumeIcon = state.volume === 0 ? <FaVolumeMute /> : state.volume < 0.5 ? <FaVolumeDown /> : <FaVolumeUp />;

  const message = !item
    ? "Play something to see its lyrics"
    : lyrics === undefined
      ? "Looking for lyrics…"
      : lyrics === null
        ? "No lyrics for this track"
        : "error" in lyrics
          ? `Couldn't load lyrics: ${lyrics.error}`
          : null;
  const subtitle = item ? [item.track.artist, item.track.album].filter(Boolean).join(" · ") : "";

  return (
    <Focusable
      onButtonDown={(e: any) => {
        const button = e?.detail?.button;
        if (screenOff) return; // the input listener wakes it
        if (button === L1 || button === R1) {
          setSide((s) => (s === "lyrics" ? "next" : "lyrics"));
          e.stopPropagation?.();
        }
      }}
      onCancelButton={(e: any) => {
        // B wakes the screen rather than leaving the page in the dark
        if (screenOff) {
          wake();
          e?.stopPropagation?.();
        }
      }}
      onOptionsButton={() => (screenOff ? wake() : item && sleep())}
      onOptionsActionDescription={item ? "Screen off" : undefined}
      style={{ position: "absolute", inset: 0, overflow: "hidden", background: "#0e141b", color: "#dcdedf" }}
    >
      <style>{PLAYER_CSS}</style>
      {art && (
        <div
          style={{
            position: "absolute",
            inset: -60,
            backgroundImage: `url("${art}")`,
            backgroundSize: "cover",
            backgroundPosition: "center",
            filter: "blur(48px) brightness(0.3) saturate(1.2)",
          }}
        />
      )}
      <div
        style={{
          position: "relative",
          display: "flex",
          flexDirection: "column",
          gap: "3vh",
          height: "100%",
          boxSizing: "border-box",
          // clear of Steam's header and footer bars
          padding: "11vh 6vw 10vh",
        }}
      >
        <div style={{ flex: 1, minHeight: 0, display: "flex", gap: "5vw" }}>
          {/* sized by height too, so the cover and title always fit above the controls */}
          <div
            style={{
              width: "min(28vw, 42vh)",
              flexShrink: 0,
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              gap: 16,
            }}
          >
            {art ? (
              <img src={art} style={{ width: "100%", aspectRatio: "1", objectFit: "cover", borderRadius: 8 }} />
            ) : (
              <div style={{ width: "100%", aspectRatio: "1", borderRadius: 8, background: "rgba(255,255,255,0.08)" }} />
            )}
            <RowText size="large" scroll="always" title={item?.track.title ?? "Nothing playing"} subtitle={subtitle} />
          </div>
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
            <SideTabs side={side} onSide={setSide} onSleep={item ? sleep : undefined} />
            <div ref={area} style={{ flex: 1, minHeight: 0, overflowY: side === "next" ? "auto" : "hidden" }}>
              {side === "next" ? (
                <UpNext />
              ) : message ? (
                <div style={{ fontSize: 24, opacity: 0.6, paddingTop: height / 3 }}>{message}</div>
              ) : lyrics && !("error" in lyrics) && height > 0 ? (
                lyrics.synced ? (
                  <SyncedLyrics lyrics={lyrics} height={height} fontSize={32} />
                ) : (
                  <ScrollingPlainLyrics lyrics={lyrics} height={height} fontSize={24} />
                )
              ) : null}
            </div>
          </div>
        </div>
        {item && (
          // the controls across the bottom, as music players lay them out full screen
          <Focusable flow-children="column" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {!item.track.live && (
              <SliderField
                value={Math.min(current, duration || 0)}
                min={0}
                max={Math.max(duration, 1)}
                step={1}
                bottomSeparator="none"
                childrenContainerWidth="max"
                description={
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, width: "100%" }}>
                    <span>{formatTime(current)}</span>
                    <span>{formatTime(duration)}</span>
                  </div>
                }
                onChange={(v) => player.seek(v)}
              />
            )}
            <Focusable flow-children="row" style={{ display: "flex", alignItems: "center", gap: "2vw" }}>
              <Focusable
                style={{ flex: 1, minWidth: 0 }}
                onSecondaryButton={(e: any) => canCast(item.instanceId) && showCastMenu(e?.currentTarget)}
                onSecondaryActionDescription={canCast(item.instanceId) ? "Cast" : undefined}
              >
                <SliderField
                  icon={volumeIcon}
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
              <Focusable flow-children="row" style={{ display: "flex", alignItems: "center", gap: 8, width: 380 }}>
                <IconButton
                  look="plain"
                  label="Shuffle"
                  active={state.shuffle}
                  onClick={() => player.setShuffle(!state.shuffle)}
                  style={{ height: 48, fontSize: 17 }}
                >
                  <FaRandom />
                </IconButton>
                <IconButton
                  look="plain"
                  label="Previous"
                  onClick={() => player.previous()}
                  style={{ height: 48, fontSize: 20 }}
                >
                  <FaStepBackward />
                </IconButton>
                <IconButton
                  look="round"
                  preferredFocus
                  label={state.playing ? "Pause" : "Play"}
                  onClick={() => player.toggle()}
                  style={{ fontSize: 20 }}
                >
                  {state.playing ? <FaPause /> : <FaPlay style={{ marginLeft: 3 }} />}
                </IconButton>
                <IconButton
                  look="plain"
                  label="Next"
                  onClick={() => player.next()}
                  style={{ height: 48, fontSize: 20 }}
                >
                  <FaStepForward />
                </IconButton>
                <IconButton
                  look="plain"
                  label="Repeat"
                  active={state.repeat !== "off"}
                  onClick={() => player.cycleRepeat()}
                  style={{ height: 48, fontSize: 17 }}
                >
                  {state.repeat === "one" ? <MdRepeatOne /> : <FaRedo />}
                </IconButton>
              </Focusable>
              {/* balances the volume, so the transport sits in the middle */}
              <div style={{ flex: 1 }} />
            </Focusable>
          </Focusable>
        )}
      </div>
      {screenOff && (
        // fixed, above Steam's own header and footer, so nothing on screen stays lit; it takes the
        // controller's focus, so the press that wakes the screen does nothing else
        <Focusable
          autoFocus
          noFocusRing
          onActivate={wake}
          onClick={wake}
          onCancelButton={(e: any) => {
            wake();
            e?.stopPropagation?.();
          }}
          onSecondaryButton={wake}
          onOptionsButton={wake}
          onOKActionDescription="Wake"
          style={{ position: "fixed", inset: 0, zIndex: 100000, background: "#000000", cursor: "none" }}
        >
          {null}
        </Focusable>
      )}
    </Focusable>
  );
};

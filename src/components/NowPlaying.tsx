import { Field, Focusable, PanelSection, PanelSectionRow, SliderField } from "@decky/ui";
import { FC, useEffect, useState } from "react";
import {
  FaHeart,
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
import { OnAir, onAir, setSetting } from "../api";
import { QueueItem, hasFeature, player } from "../player";
import { showTrackMenu, toggleStar } from "./TrackMenu";
import { canCast, prepareCastMenu, showCastMenu } from "./CastMenu";
import { useStore } from "../store";
import { ACCENT, Art, ButtonRow, IconButton, RowText, formatTime, scrollPanelToTop } from "./common";

const Seek: FC = () => {
  const { current, duration } = useStore(player.position);
  return (
    <PanelSectionRow>
      <SliderField
        value={Math.min(current, duration || 0)}
        min={0}
        max={Math.max(duration, 1)}
        step={1}
        bottomSeparator="none"
        // Times live inside the field so its focus highlight wraps them instead of cutting through
        description={
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, width: "100%" }}>
            <span>{formatTime(current)}</span>
            <span>{formatTime(duration)}</span>
          </div>
        }
        onChange={(v) => player.seek(v)}
      />
    </PanelSectionRow>
  );
};

const ON_AIR_POLL_MS = 20_000;

/** The song a live station is playing, polled while it plays (null when it doesn't say). */
export function useOnAir(item: QueueItem | undefined, playing: boolean): OnAir | null {
  const [song, setSong] = useState<OnAir | null>(null);
  const live = item?.track.live ? item : undefined;
  useEffect(() => {
    setSong(null);
    if (!live || !playing) return;
    let cancelled = false;
    const poll = () =>
      onAir(live.instanceId, live.track.id)
        .then((r) => !cancelled && setSong(r.ok ? r.data : null))
        .catch(() => {});
    poll();
    const timer = window.setInterval(poll, ON_AIR_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [live?.instanceId, live?.track.id, playing]);
  return song;
}

/** In place of the seek bar for live streams (what's on air shows next to the cover). */
const LiveBar: FC = () => (
  <PanelSectionRow>
    <div style={{ fontSize: 12, padding: "6px 0", color: "#ff5c5c", fontWeight: "bold", letterSpacing: 1 }}>● LIVE</div>
  </PanelSectionRow>
);

/**
 * The lines next to the cover. A station shows the song it's playing when it says (song, artist,
 * station); otherwise the station, where it is and its genres, like any track's title/artist/album.
 */
function cardText(
  item: QueueItem | undefined,
  song: OnAir | null,
  castingTo: string | null,
): { title: string; subtitle: string[] } {
  if (!item) return { title: "Nothing playing", subtitle: [] };
  const { title, artist, album } = item.track;
  const lines = song?.title
    ? { title: song.title, subtitle: [song.artist, title] }
    : { title, subtitle: [artist, album] };
  // casting: where the music is, in place of the last line, so the player keeps its height
  return castingTo ? { ...lines, subtitle: [lines.subtitle[0], `On ${castingTo}`] } : lines;
}

let volumeSaveTimer: number | undefined;

// transport icon sizes: skips read first after play/pause; shuffle and repeat are quieter, at the ends
const SKIP = { height: 44, fontSize: 18 };
const SIDE = { height: 44, fontSize: 15 };
const PLAY = { fontSize: 18 };

/** The Now Playing tab: the cover and title, seek bar, transport and volume (lyrics follow it). */
export const NowPlaying: FC = () => {
  const state = useStore(player.state);
  const item = state.queue[state.index];
  const song = useOnAir(item, state.playing);
  const volumeIcon = state.volume === 0 ? <FaVolumeMute /> : state.volume < 0.5 ? <FaVolumeDown /> : <FaVolumeUp />;

  return (
    <PanelSection>
      {/* Focusable so gamepad navigation (and therefore scrolling) can reach the top of the panel */}
      <PanelSectionRow>
        <Field
          // nothing loaded: nothing to do with the card, so the controller doesn't stop on it
          focusable={!!item}
          highlightOnFocus={!!item}
          bottomSeparator="none"
          childrenLayout="below"
          onActivate={(e: any) => item && showTrackMenu(item.instanceId, item.track, e?.currentTarget)}
          onClick={(e: any) => item && showTrackMenu(item.instanceId, item.track, e?.currentTarget)}
          onOKActionDescription="Options"
          onGamepadFocus={scrollPanelToTop}
          onSecondaryButton={() =>
            item && hasFeature(item.instanceId, "star") && toggleStar(item.instanceId, item.track)
          }
          onSecondaryActionDescription={
            item && hasFeature(item.instanceId, "star") ? (item.track.starred ? "Unfavourite" : "Favourite") : undefined
          }
        >
          <div style={{ display: "flex", gap: 12, alignItems: "center", minWidth: 0 }}>
            {/* sized so the player and the tabs fit the panel's first page together (PlayerPages) */}
            <Art instanceId={item?.instanceId ?? ""} art={song?.art ?? item?.track.art ?? null} size={76} />
            <RowText
              size="large"
              scroll="always"
              {...cardText(item, song, state.castingTo?.name ?? null)}
              badge={item?.track.starred && <FaHeart style={{ color: ACCENT, fontSize: 12, flexShrink: 0 }} />}
            />
          </div>
        </Field>
      </PanelSectionRow>
      {item && (item.track.live ? <LiveBar /> : <Seek />)}
      {/* nothing loaded: only the card; transport and volume would do nothing */}
      {item && (
        <ButtonRow>
          <IconButton
            look="plain"
            label="Shuffle"
            active={state.shuffle}
            onClick={() => player.setShuffle(!state.shuffle)}
            style={SIDE}
          >
            <FaRandom />
          </IconButton>
          <IconButton look="plain" label="Previous" onClick={() => player.previous()} style={SKIP}>
            <FaStepBackward />
          </IconButton>
          <IconButton
            look="round"
            label={state.playing ? "Pause" : "Play"}
            onClick={() => player.toggle()}
            style={PLAY}
          >
            {state.playing ? <FaPause /> : <FaPlay style={{ marginLeft: 3 }} />}
          </IconButton>
          <IconButton look="plain" label="Next" onClick={() => player.next()} style={SKIP}>
            <FaStepForward />
          </IconButton>
          <IconButton
            look="plain"
            label="Repeat"
            active={state.repeat !== "off"}
            onClick={() => player.cycleRepeat()}
            style={SIDE}
          >
            {state.repeat === "one" ? <MdRepeatOne /> : <FaRedo />}
          </IconButton>
        </ButtonRow>
      )}
      {item && (
        <PanelSectionRow>
          {/* X on the volume slider picks where the music plays (this Deck, a speaker, a TV) */}
          <Focusable
            onGamepadFocus={() => canCast(item.instanceId) && prepareCastMenu()}
            onSecondaryButton={(e: any) => {
              e?.stopPropagation?.();
              if (canCast(item.instanceId)) showCastMenu(e?.currentTarget);
            }}
            onSecondaryActionDescription={canCast(item.instanceId) ? "Cast" : undefined}
          >
            <SliderField
              icon={volumeIcon}
              // casting, the device's own volume (its name is on the card, not a row of its own here)
              value={Math.round(state.volume * 100)}
              min={0}
              max={100}
              step={5}
              bottomSeparator="none"
              onChange={(v) => {
                player.setVolume(v / 100);
                if (state.castingTo) return;
                window.clearTimeout(volumeSaveTimer);
                volumeSaveTimer = window.setTimeout(() => setSetting("volume", v / 100), 1000);
              }}
            />
          </Focusable>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};

/** The thin progress line under the mini player (redraws on its own, not the whole row). */
const MiniProgress: FC = () => {
  const { current, duration } = useStore(player.position);
  const done = duration > 0 ? Math.min(current / duration, 1) : 0;
  return (
    <div style={{ height: 2, background: "rgba(255, 255, 255, 0.1)", marginTop: 6 }}>
      <div style={{ height: "100%", width: `${done * 100}%`, background: ACCENT }} />
    </div>
  );
};

/**
 * What's playing, in one row above Library and Search (the mini player music apps keep
 * above their content): cover, title and artist, play/pause and next, and a thin progress line.
 * The row opens the Now Playing tab.
 */
export const MiniPlayer: FC<{ onOpen: () => void }> = ({ onOpen }) => {
  const state = useStore(player.state);
  const item = state.queue[state.index];
  const song = useOnAir(item, state.playing);
  if (!item) return null;
  const { title, subtitle } = cardText(item, song, state.castingTo?.name ?? null);
  return (
    <PanelSection>
      <PanelSectionRow>
        <Focusable flow-children="row" style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <Focusable
            className="md-mini"
            onActivate={onOpen}
            onClick={onOpen}
            onOKActionDescription="Now playing"
            onGamepadFocus={scrollPanelToTop}
            style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 10, padding: 4, margin: -4 }}
          >
            <Art instanceId={item.instanceId} art={song?.art ?? item.track.art ?? null} size={40} />
            <RowText title={title} subtitle={subtitle[0]} />
          </Focusable>
          <IconButton
            look="plain"
            label={state.playing ? "Pause" : "Play"}
            onClick={() => player.toggle()}
            style={{ flex: "0 0 40px", height: 40, fontSize: 16 }}
          >
            {state.playing ? <FaPause /> : <FaPlay />}
          </IconButton>
          <IconButton
            look="plain"
            label="Next"
            onClick={() => player.next()}
            style={{ flex: "0 0 40px", height: 40, fontSize: 16 }}
          >
            <FaStepForward />
          </IconButton>
        </Focusable>
      </PanelSectionRow>
      {!item.track.live && <MiniProgress />}
    </PanelSection>
  );
};

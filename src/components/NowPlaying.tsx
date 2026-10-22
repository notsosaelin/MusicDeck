import { Field, PanelSection, PanelSectionRow, SliderField } from "@decky/ui";
import { FC, ReactNode, useEffect, useState } from "react";
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
import { useStore } from "../store";
import { ACCENT, Art, ButtonRow, IconButton, RowText, formatTime } from "./common";

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
function useOnAir(item: QueueItem | undefined, playing: boolean): OnAir | null {
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
function cardText(item: QueueItem | undefined, song: OnAir | null): { title: string; subtitle: string[] } {
  if (!item) return { title: "Nothing playing", subtitle: ["Pick something from your library"] };
  const { title, artist, album } = item.track;
  if (song?.title) return { title: song.title, subtitle: [song.artist, title] };
  return { title, subtitle: [artist, album] };
}

let volumeSaveTimer: number | undefined;

/** `children` render at the bottom of the same section (the view switcher), keeping spacing tight. */
export const NowPlaying: FC<{ children?: ReactNode }> = ({ children }) => {
  const state = useStore(player.state);
  const item = state.queue[state.index];
  const song = useOnAir(item, state.playing);
  const volumeIcon = state.volume === 0 ? <FaVolumeMute /> : state.volume < 0.5 ? <FaVolumeDown /> : <FaVolumeUp />;

  return (
    <PanelSection>
      {/* Focusable so gamepad navigation (and therefore scrolling) can reach the top of the panel */}
      <PanelSectionRow>
        <Field
          focusable
          highlightOnFocus
          bottomSeparator="none"
          childrenLayout="below"
          onActivate={(e: any) => item && showTrackMenu(item.instanceId, item.track, e?.currentTarget)}
          onClick={(e: any) => item && showTrackMenu(item.instanceId, item.track, e?.currentTarget)}
          onOKActionDescription="Options"
          onSecondaryButton={() =>
            item && hasFeature(item.instanceId, "star") && toggleStar(item.instanceId, item.track)
          }
          onSecondaryActionDescription={
            item && hasFeature(item.instanceId, "star") ? (item.track.starred ? "Unfavourite" : "Favourite") : undefined
          }
        >
          <div style={{ display: "flex", gap: 12, alignItems: "center", minWidth: 0 }}>
            <Art instanceId={item?.instanceId ?? ""} art={song?.art ?? item?.track.art ?? null} size={72} />
            <RowText
              size="large"
              scroll="always"
              {...cardText(item, song)}
              badge={item?.track.starred && <FaHeart style={{ color: ACCENT, fontSize: 12, flexShrink: 0 }} />}
            />
          </div>
        </Field>
      </PanelSectionRow>
      {item && (item.track.live ? <LiveBar /> : <Seek />)}
      <ButtonRow>
        <IconButton label="Shuffle" active={state.shuffle} onClick={() => player.setShuffle(!state.shuffle)}>
          <FaRandom />
        </IconButton>
        <IconButton label="Previous" disabled={!item} onClick={() => player.previous()}>
          <FaStepBackward />
        </IconButton>
        <IconButton label={state.playing ? "Pause" : "Play"} disabled={!item} onClick={() => player.toggle()}>
          {state.playing ? <FaPause /> : <FaPlay />}
        </IconButton>
        <IconButton label="Next" disabled={!item} onClick={() => player.next()}>
          <FaStepForward />
        </IconButton>
        <IconButton label="Repeat" active={state.repeat !== "off"} onClick={() => player.cycleRepeat()}>
          {state.repeat === "one" ? <MdRepeatOne /> : <FaRedo />}
        </IconButton>
      </ButtonRow>
      <PanelSectionRow>
        <SliderField
          icon={volumeIcon}
          value={Math.round(state.volume * 100)}
          min={0}
          max={100}
          step={5}
          onChange={(v) => {
            player.setVolume(v / 100);
            window.clearTimeout(volumeSaveTimer);
            volumeSaveTimer = window.setTimeout(() => setSetting("volume", v / 100), 1000);
          }}
        />
      </PanelSectionRow>
      {children}
    </PanelSection>
  );
};

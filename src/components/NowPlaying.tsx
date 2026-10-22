import { Field, PanelSection, PanelSectionRow, SliderField } from "@decky/ui";
import { FC, ReactNode } from "react";
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
import { setSetting } from "../api";
import { hasFeature, player } from "../player";
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

let volumeSaveTimer: number | undefined;

/** `children` render at the bottom of the same section (the view switcher), keeping spacing tight. */
export const NowPlaying: FC<{ children?: ReactNode }> = ({ children }) => {
  const state = useStore(player.state);
  const item = state.queue[state.index];
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
            <Art instanceId={item?.instanceId ?? ""} art={item?.track.art ?? null} size={72} />
            <RowText
              size="large"
              title={
                item ? (
                  <span style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{item.track.title}</span>
                    {item.track.starred && <FaHeart style={{ color: ACCENT, fontSize: 12, flexShrink: 0 }} />}
                  </span>
                ) : (
                  "Nothing playing"
                )
              }
              subtitle={
                item ? (
                  <>
                    <div style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{item.track.artist}</div>
                    <div style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{item.track.album}</div>
                  </>
                ) : (
                  "Pick something from your library"
                )
              }
            />
          </div>
        </Field>
      </PanelSectionRow>
      {item && <Seek />}
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

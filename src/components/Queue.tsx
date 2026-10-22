import { PanelSection } from "@decky/ui";
import { FC } from "react";
import { FaTrash } from "react-icons/fa";
import { player } from "../player";
import { useStore } from "../store";
import { showTrackMenu } from "./TrackMenu";
import { ButtonRow, IconButton, Message, TrackRow } from "./common";

export const Queue: FC = () => {
  const { queue, index } = useStore(player.state);
  if (!queue.length) return <Message>The queue is empty</Message>;
  return (
    <>
      <PanelSection title={`Up next · ${queue.length} track${queue.length === 1 ? "" : "s"}`}>
        <ButtonRow>
          <IconButton onClick={() => player.clear()}>
            <FaTrash /> Clear queue
          </IconButton>
        </ButtonRow>
        {queue.map((item, i) => (
          <TrackRow
            key={i}
            track={item.track}
            active={i === index}
            onPlay={() => player.jumpTo(i)}
            onSecondary={() => player.removeAt(i)}
            secondaryLabel="Remove"
            onOptions={(e) => showTrackMenu(item.instanceId, item.track, e?.currentTarget)}
            optionsLabel="Options"
          />
        ))}
      </PanelSection>
    </>
  );
};

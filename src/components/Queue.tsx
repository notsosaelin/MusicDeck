import { ConfirmModal, PanelSection, showModal } from "@decky/ui";
import { FC, useState } from "react";
import { FaTrash } from "react-icons/fa";
import { QueueItem, player } from "../player";
import { useStore } from "../store";
import { showTrackMenu } from "./TrackMenu";
import { ButtonRow, IconButton, Message, TrackRow } from "./common";

/** Clears the queue after asking (X on the Queue tab, or the button below the queue). */
export function confirmClearQueue() {
  const n = player.state.get().queue.length;
  if (!n) return;
  showModal(
    <ConfirmModal
      strTitle="Clear the queue?"
      strDescription={`${n} track${n === 1 ? "" : "s"}`}
      strOKButtonText="Clear"
      bDestructiveWarning
      onOK={() => player.clear()}
    />,
  );
}

/** The queue in the order it plays (shuffled or not). Y → Move… picks a track up for the d-pad. */
export const Queue: FC = () => {
  const { queue, index } = useStore(player.state);
  const [moving, setMoving] = useState<QueueItem | null>(null);
  if (!queue.length) return <Message>The queue is empty</Message>;
  return (
    <>
      <PanelSection title={`Up next · ${queue.length} track${queue.length === 1 ? "" : "s"}`}>
        {player.playOrder.map((i, position) => {
          const item = queue[i];
          if (!item) return null;
          return (
            <TrackRow
              key={position} // rows stay put; moving a track moves its content (see RowMoving)
              track={item.track}
              active={i === index}
              onPlay={() => player.jumpTo(i)}
              onSecondary={() => player.removeAt(i)}
              secondaryLabel="Remove"
              onOptions={(e) =>
                showTrackMenu(item.instanceId, item.track, e?.currentTarget, {
                  queue: { position, startMoving: () => setMoving(item) },
                })
              }
              optionsLabel="Options"
              moving={
                moving === item
                  ? { onMove: (step) => player.move(position, position + step), onDone: () => setMoving(null) }
                  : undefined
              }
            />
          );
        })}
        {/* at the end and asked first: one stray press on the first row must not lose the queue */}
        <ButtonRow>
          <IconButton look="plain" label="Clear queue" onClick={confirmClearQueue}>
            <FaTrash /> Clear queue
          </IconButton>
        </ButtonRow>
      </PanelSection>
    </>
  );
};

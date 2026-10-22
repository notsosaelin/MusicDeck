import { DialogBody, DialogHeader, ModalRoot, TextField } from "@decky/ui";
import { DialogActions } from "./common";
import { FC, useRef, useState } from "react";

/** Small "name this" prompt (new playlist, rename). */
export const NameModal: FC<{
  closeModal?: () => void;
  title: string;
  initial?: string;
  action: string;
  onSubmit: (name: string) => void | Promise<void>;
}> = ({ closeModal, title, initial = "", action, onSubmit }) => {
  const [name, setName] = useState(initial);
  // a ref, not state: a double tap (or Enter then A) arrives before a re-render would disable the button
  const busy = useRef(false);
  const submit = async () => {
    if (!name.trim() || busy.current) return;
    busy.current = true;
    try {
      await onSubmit(name.trim());
      closeModal?.();
    } finally {
      busy.current = false;
    }
  };
  return (
    <ModalRoot closeModal={closeModal} onCancel={closeModal}>
      <DialogHeader>{title}</DialogHeader>
      <DialogBody>
        <TextField
          value={name}
          focusOnMount
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
      </DialogBody>
      <DialogActions primary={action} onPrimary={submit} primaryDisabled={!name.trim()} onCancel={closeModal} />
    </ModalRoot>
  );
};

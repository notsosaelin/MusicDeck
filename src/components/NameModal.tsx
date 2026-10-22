import { DialogBody, DialogHeader, ModalRoot, TextField } from "@decky/ui";
import { DialogActions } from "./common";
import { FC, useState } from "react";

/** Small "name this" prompt (new playlist, rename). */
export const NameModal: FC<{
  closeModal?: () => void;
  title: string;
  initial?: string;
  action: string;
  onSubmit: (name: string) => void | Promise<void>;
}> = ({ closeModal, title, initial = "", action, onSubmit }) => {
  const [name, setName] = useState(initial);
  const submit = async () => {
    if (!name.trim()) return;
    await onSubmit(name.trim());
    closeModal?.();
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

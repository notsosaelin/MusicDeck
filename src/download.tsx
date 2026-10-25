/**
 * Downloading a playlist or album for listening offline, the same wherever it's opened (the Quick Access panel,
 * the media center, desktop Steam, a "..." menu): a MusicDeck playlist itself, or anything else (another source's
 * playlist, an album) as the playlist saved from it, saved to your playlists now if it wasn't (main.py:
 * download_collection). The button says Download, Downloading n of m, Downloaded, or n of m · Retry, and asks again
 * as downloads finish; removing a download asks first (the files go, the playlist stays).
 */
import { ConfirmModal, showModal } from "@decky/ui";
import { useEffect, useState } from "react";
import { Collection, CollectionOffline, collectionOffline, downloadCollection, retryDownloads, saveAsPlaylist, savedCopy } from "./api";
import { invalidateBrowserCache } from "./nav";
import { notify } from "./notify";
import { pluginState, useStore } from "./store";

/** A source whose songs can be saved on the device. */
export const canDownload = (instanceId: string) =>
  !!pluginState.get()?.services.find((s) => s.id === instanceId)?.downloadable;

export const downloadable = (instanceId: string, c: Collection) => canDownload(instanceId) && ["playlist", "album"].includes(c.kind);

/** Turns a download on or off; says what happened. */
export async function setDownload(instanceId: string, c: Collection, on: boolean): Promise<CollectionOffline | null> {
  const r = await downloadCollection(instanceId, c.kind, c.id, on).catch((e) => ({ ok: false as const, error: String(e) }));
  if (!r.ok) {
    notify(on ? "Couldn't download it" : "Couldn't remove the download", r.error);
    return null;
  }
  notify(on ? "Downloading for offline" : "Download removed", c.kind === "playlist" && c.id.startsWith("md:") ? c.title : on ? `${c.title} · saved to your playlists` : c.title);
  return r.data;
}

/** Removing a download, after asking. */
export const confirmRemove = (instanceId: string, c: Collection, done?: (s: CollectionOffline | null) => void) =>
  showModal(
    <ConfirmModal
      strTitle={`Remove the download of ${c.title}?`}
      strDescription="Its songs are deleted from this device. The playlist stays."
      strOKButtonText="Remove"
      bDestructiveWarning
      onOK={() => setDownload(instanceId, c, false).then((s) => done?.(s))}
    />,
  );

export interface DownloadButtonState {
  label: string;
  /** downloaded, or downloading: shown lit */
  on: boolean;
  act: () => void;
}

/** The Download button for an album's or playlist's page; null where its source can't download. */
export function useDownload(instanceId: string, c: Collection): DownloadButtonState | null {
  const [state, setState] = useState<CollectionOffline | null>(null);
  const downloads = useStore(pluginState)?.offline;
  const can = downloadable(instanceId, c);
  useEffect(() => {
    if (!can) return;
    let live = true;
    collectionOffline(instanceId, c.kind, c.id)
      .then((s) => live && setState(s))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [instanceId, c.id, can, downloads?.tracks, downloads?.current, downloads?.failed, downloads?.pending]);
  if (!can) return null;
  if (!state?.on) return { label: "Download", on: false, act: () => setDownload(instanceId, c, true).then((s) => s && setState(s)) };
  const busy = !!downloads && (downloads.current !== null || downloads.pending > 0);
  const missing = state.saved < state.total;
  if (missing && busy) return { label: `Downloading ${state.saved} of ${state.total}`, on: true, act: () => confirmRemove(instanceId, c, setState) };
  if (missing) return { label: `${state.saved} of ${state.total} · Retry`, on: true, act: () => retryDownloads() };
  return { label: "Downloaded", on: true, act: () => confirmRemove(instanceId, c, setState) };
}

/** A source's playlist or album saved to your playlists, wherever its page is opened from: the copy (to open it), or
 *  null; and save(), which saves it and remembers the copy at once. */
export function useSavedCopy(instanceId: string, c: Collection): { copy: Collection | null; save: () => Promise<void> } {
  const [copy, setCopy] = useState<Collection | null>(null);
  // asked again when the plugin's state changes (a playlist saved or deleted elsewhere): a cheap call
  const state = useStore(pluginState);
  useEffect(() => {
    let live = true;
    savedCopy(instanceId, c.kind, c.id)
      .then((found) => live && setCopy(found))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [instanceId, c.id, state]);
  const save = async () => {
    const r = await saveAsPlaylist(instanceId, c.kind, c.id).catch((e) => ({ ok: false as const, error: String(e) }));
    if (!r.ok) return notify("Couldn't save playlist", r.error);
    setCopy(r.data);
    invalidateBrowserCache();
    notify("Saved to your playlists", c.title);
  };
  return { copy, save };
}

import { openFilePicker } from "@decky/api";
import {
  DialogBody,
  DialogButton,
  DialogFooter,
  DialogHeader,
  Field,
  Focusable,
  MenuItem,
  MenuSeparator,
  ModalRoot,
  Spinner,
  TextField,
  showModal,
} from "@decky/ui";
import { FC, ReactNode, useEffect, useRef, useState } from "react";
import {
  Collection,
  Change,
  LookupChanges,
  Release,
  TagField,
  TagInfo,
  TagKind,
  TagResult,
  albumArt,
  applyLookup,
  artistArt,
  findLyrics,
  getTags,
  lookupAlbum,
  lookupChanges,
  saveLyrics,
  setTags,
  undoTags,
} from "../api";
import { View, invalidateBrowserCache, navigation, setStack } from "../nav";
import { notify } from "../notify";
import { hasFeature } from "../player";
import { pluginState } from "../store";
import { DialogActions } from "./common";
import { centerNav } from "./media/pages";

// FileSelectionType.FILE from @decky/api (a const enum, which isolated builds can't import)
const SELECT_FILE = 0;
const IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "gif"];
const NUMERIC = new Set(["track", "disc", "year"]);

/**
 * An album renamed (or given another artist) has another id: open pages of it, in the panel and the
 * media center, show it under its new one rather than "no longer in the library".
 */
function retargetAlbum(oldId: string, newId: string, title?: string) {
  if (!newId || newId === oldId) return;
  const retarget = (views: View[]) =>
    views.map((v) =>
      v.type === "collection" && v.collection.kind === "album" && v.collection.id === oldId
        ? { ...v, collection: { ...v.collection, id: newId, title: title ?? v.collection.title } }
        : v,
    );
  const { stacks } = navigation.get();
  setStack("library", retarget(stacks.library));
  setStack("search", retarget(stacks.search));
  const center = centerNav.get();
  centerNav.set(Object.fromEntries(Object.entries(center).map(([k, views]) => [k, retarget(views)])));
}

/** "Saved", or how much was, and why the rest wasn't. */
function reportSaved(what: string, r: TagResult) {
  if (r.failed) notify(what, `${r.changed} of ${r.changed + r.failed} files saved: ${r.error}`);
  else notify(what, r.changed === 1 ? "Tags saved" : `Tags saved to ${r.changed} files`);
}

/**
 * Puts the controller on the first thing in a dialog's body once it's there: Steam would pick the
 * footer's button, scrolled to the bottom, with the top of a long dialog out of sight.
 */
function useFocusFirst(ready: boolean) {
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ready) body.current?.querySelector<HTMLElement>("[tabindex]")?.focus();
  }, [ready]);
  return body;
}

/** A dialog's footer with only Cancel (lists where picking an entry is the action). */
const CancelOnly: FC<{ onCancel?: () => void }> = ({ onCancel }) => (
  <DialogFooter>
    <DialogButton onClick={onCancel}>Cancel</DialogButton>
  </DialogFooter>
);

const Loading: FC = () => (
  <div style={{ display: "flex", justifyContent: "center", padding: 24 }}>
    <Spinner style={{ width: 32, height: 32 }} />
  </div>
);

/** The editor's fields in rows: one each, the numbers next to each other. */
function fieldRows(fields: TagField[]): TagField[][] {
  const rows: TagField[][] = [];
  for (const f of fields) {
    const last = rows[rows.length - 1];
    if (NUMERIC.has(f.field) && last && NUMERIC.has(last[0].field)) last.push(f);
    else rows.push([f]);
  }
  return rows;
}

/** Editing a track's tags, or what an album's tracks share. Only fields changed are written. */
const TagModal: FC<{ closeModal?: () => void; instanceId: string; kind: TagKind; id: string; title: string }> = ({
  closeModal,
  instanceId,
  kind,
  id,
  title,
}) => {
  const [info, setInfo] = useState<TagInfo | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const busy = useRef(false);
  useEffect(() => {
    getTags(instanceId, kind, id).then((r) => {
      if (!r.ok) {
        notify(title, r.error);
        closeModal?.();
        return;
      }
      setInfo(r.data);
      setValues(Object.fromEntries(r.data.fields.map((f) => [f.field, f.value])));
    });
  }, []);
  const changes = info
    ? Object.fromEntries(info.fields.filter((f) => values[f.field] !== f.value).map((f) => [f.field, values[f.field]]))
    : {};
  const run = async (work: () => Promise<{ ok: true; data: TagResult } | { ok: false; error: string }>) => {
    if (busy.current) return;
    busy.current = true;
    try {
      const r = await work();
      if (!r.ok) return notify(title, r.error);
      invalidateBrowserCache();
      if (kind === "album") retargetAlbum(id, r.data.album_id, values.album || undefined);
      reportSaved(title, r.data);
      closeModal?.();
    } finally {
      busy.current = false;
    }
  };
  return (
    <ModalRoot closeModal={closeModal} onCancel={closeModal}>
      <DialogHeader>{kind === "album" ? `Edit album · ${title}` : `Edit tags · ${title}`}</DialogHeader>
      <DialogBody>
        {!info ? (
          <Loading />
        ) : (
          <>
            {kind === "album" && (
              <div style={{ fontSize: 13, opacity: 0.6, marginBottom: 8 }}>
                Written to all {info.files} files. Fields marked Mixed stay as each file has them unless you change
                them.
              </div>
            )}
            {fieldRows(info.fields).map((row, r) => (
              // the short numbers (track, disc, year) share a row, so the dialog scrolls less
              <Focusable key={r} flow-children="row" style={{ display: "flex", gap: 16 }}>
                {row.map((f, i) => (
                  <div key={f.field} style={{ flex: 1, minWidth: 0 }}>
                    <TextField
                      label={f.label}
                      value={values[f.field] ?? ""}
                      // the controller starts at the top: Steam would put it on Save, scrolled to the bottom
                      focusOnMount={(r === 0 && i === 0) || undefined}
                      placeholder={f.mixed ? "Mixed" : f.field === "genre" ? "Several separated by ;" : undefined}
                      mustBeNumeric={NUMERIC.has(f.field) || undefined}
                      onChange={(e) => setValues({ ...values, [f.field]: e.target.value })}
                    />
                  </div>
                ))}
              </Focusable>
            ))}
          </>
        )}
      </DialogBody>
      {info?.undo && (
        <div style={{ display: "flex", justifyContent: "flex-start", padding: "0 0 8px" }}>
          <DialogButton
            style={{ width: "auto", minWidth: 0, padding: "0 20px" }}
            onClick={() => run(() => undoTags(instanceId, kind, id))}
          >
            Undo last edit
          </DialogButton>
        </div>
      )}
      <DialogActions
        primary="Save"
        onPrimary={() => run(() => setTags(instanceId, kind, id, changes))}
        primaryDisabled={!info || Object.keys(changes).length === 0}
        onCancel={closeModal}
      />
    </ModalRoot>
  );
};

export const editTags = (instanceId: string, kind: TagKind, id: string, title: string) =>
  showModal(<TagModal instanceId={instanceId} kind={kind} id={id} title={title} />);

/** One line of a release: what tells two pressings apart. */
const releaseLine = (r: Release, files: number) =>
  [r.artist, r.year, r.country, r.format, `${r.tracks} tracks${r.tracks !== files ? ` (yours: ${files})` : ""}`]
    .filter(Boolean)
    .join(" · ");

/** MusicBrainz's releases that may be the album; picking one shows what it would change. */
const LookupModal: FC<{ closeModal?: () => void; instanceId: string; album: Collection; files: number }> = ({
  closeModal,
  instanceId,
  album,
  files,
}) => {
  const [releases, setReleases] = useState<Release[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    lookupAlbum(instanceId, album.id).then((r) => (r.ok ? setReleases(r.data) : setError(r.error)));
  }, []);
  const pick = (r: Release) => {
    closeModal?.();
    showModal(<PreviewModal instanceId={instanceId} album={album} release={r} files={files} />);
  };
  const body = useFocusFirst(!!releases?.length);
  return (
    <ModalRoot closeModal={closeModal} onCancel={closeModal}>
      <DialogHeader>Look up · {album.title}</DialogHeader>
      <DialogBody>
        <div ref={body}>
          {error ? (
            <div style={{ opacity: 0.7 }}>{error}</div>
          ) : !releases ? (
            <Loading />
          ) : releases.length === 0 ? (
            <div style={{ opacity: 0.7 }}>
              MusicBrainz has nothing by that name. Fix the album's name or artist first.
            </div>
          ) : (
            <Focusable flow-children="column">
              {releases.map((r) => (
                <Field
                  key={r.id}
                  focusable
                  highlightOnFocus
                  label={r.title}
                  description={releaseLine(r, files)}
                  onActivate={() => pick(r)}
                  onClick={() => pick(r)}
                  onOKActionDescription="See changes"
                />
              ))}
            </Focusable>
          )}
        </div>
      </DialogBody>
      <CancelOnly onCancel={closeModal} />
    </ModalRoot>
  );
};

const ChangeLine: FC<{ change: [string, string, string] }> = ({ change: [label, before, after] }) => (
  <div style={{ fontSize: 14, lineHeight: 1.5 }}>
    <span style={{ opacity: 0.6 }}>{label}: </span>
    <span style={{ opacity: 0.6, textDecoration: before ? "line-through" : undefined }}>{before || "empty"}</span>
    {" → "}
    {after}
  </div>
);

const Heading: FC<{ children: ReactNode }> = ({ children }) => (
  <div style={{ fontSize: 12, fontWeight: 700, opacity: 0.6, textTransform: "uppercase", margin: "14px 0 4px" }}>
    {children}
  </div>
);

/**
 * Track changes grouped: one the same on several tracks ("radiohead" → "Radiohead" on all 12) is said
 * once, with how many; the rest under each file.
 */
function groupTrackChanges(tracks: [string, Change[]][]) {
  const key = (c: Change) => c.join("\u0000");
  const count = new Map<string, number>();
  for (const [, list] of tracks) for (const c of list) count.set(key(c), (count.get(key(c)) ?? 0) + 1);
  const shared: [Change, number][] = [];
  const seen = new Set<string>();
  for (const [, list] of tracks)
    for (const c of list)
      if ((count.get(key(c)) ?? 0) > 1 && !seen.has(key(c))) {
        seen.add(key(c));
        shared.push([c, count.get(key(c))!]);
      }
  const own = tracks
    .map(([file, list]) => [file, list.filter((c) => (count.get(key(c)) ?? 0) === 1)] as [string, Change[]])
    .filter(([, list]) => list.length > 0);
  return { shared, own };
}

/** One entry of a preview: a stop for the controller, so the d-pad scrolls a long one. */
const Entry: FC<{ children: ReactNode }> = ({ children }) => (
  <Focusable noFocusRing onActivate={() => {}} style={{ marginBottom: 6 }}>
    {children}
  </Focusable>
);

/** What a release would change, before anything is written. */
const PreviewModal: FC<{
  closeModal?: () => void;
  instanceId: string;
  album: Collection;
  release: Release;
  files: number;
}> = ({ closeModal, instanceId, album, release, files }) => {
  const [changes, setChanges] = useState<LookupChanges | null>(null);
  const [error, setError] = useState("");
  const busy = useRef(false);
  useEffect(() => {
    lookupChanges(instanceId, album.id, release.id).then((r) => (r.ok ? setChanges(r.data) : setError(r.error)));
  }, []);
  const nothing = changes && changes.album.length === 0 && changes.tracks.length === 0;
  const grouped = changes ? groupTrackChanges(changes.tracks) : { shared: [], own: [] };
  const body = useFocusFirst(!!changes && !nothing);
  const apply = async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      const r = await applyLookup(instanceId, album.id, release.id);
      if (!r.ok) return notify(album.title, r.error);
      invalidateBrowserCache();
      retargetAlbum(album.id, r.data.album_id, changes?.release.title);
      if (r.data.changed) reportSaved(changes?.release.title ?? album.title, r.data);
      if (r.data.art) notify(changes?.release.title ?? album.title, "Cover found");
      closeModal?.();
    } finally {
      busy.current = false;
    }
  };
  return (
    <ModalRoot closeModal={closeModal} onCancel={closeModal}>
      <DialogHeader>{release.title}</DialogHeader>
      <DialogBody>
        <div ref={body}>
          {error ? (
            <div style={{ opacity: 0.7 }}>{error}</div>
          ) : !changes ? (
            <Loading />
          ) : nothing ? (
            <div style={{ opacity: 0.7 }}>Your files already say what this release does.</div>
          ) : (
            <Focusable flow-children="column">
              {changes.album.length > 0 && <Heading>Album</Heading>}
              {changes.album.map((c, i) => (
                <Entry key={i}>
                  <ChangeLine change={c} />
                </Entry>
              ))}
              {changes.tracks.length > 0 && <Heading>Tracks</Heading>}
              {grouped.shared.map(([c, n], i) => (
                <Entry key={`shared${i}`}>
                  <div style={{ fontSize: 13, opacity: 0.5 }}>{n === files ? `Every track (${n})` : `${n} tracks`}</div>
                  <ChangeLine change={c} />
                </Entry>
              ))}
              {grouped.own.map(([file, list]) => (
                <Entry key={file}>
                  <div style={{ fontSize: 13, opacity: 0.5 }}>{file}</div>
                  {list.map((c, i) => (
                    <ChangeLine key={i} change={c} />
                  ))}
                </Entry>
              ))}
              {!changes.matched && (
                <div style={{ fontSize: 13, opacity: 0.6, marginTop: 12 }}>
                  The release has {release.tracks} tracks and the album {files}, so only the album's details change;
                  each track stays as it is.
                </div>
              )}
            </Focusable>
          )}
        </div>
      </DialogBody>
      <DialogActions primary="Apply" onPrimary={apply} primaryDisabled={!changes || !!nothing} onCancel={closeModal} />
    </ModalRoot>
  );
};

/** A picture picked from the Deck's files, or null when the picker is closed. */
async function pickImage(): Promise<string | null> {
  const home = pluginState.get()?.user_home || "/home";
  try {
    return (await openFilePicker(SELECT_FILE, home, true, true, undefined, IMAGE_EXTENSIONS)).realpath;
  } catch {
    return null; // picker cancelled
  }
}

async function pickAlbumArt(instanceId: string, album: Collection) {
  const path = await pickImage();
  if (!path) return;
  const r = await albumArt(instanceId, album.id, "set", path);
  if (!r.ok) return notify("Couldn't set artwork", r.error);
  invalidateBrowserCache();
  notify(album.title, "Artwork set");
}

async function findAlbumArt(instanceId: string, album: Collection) {
  notify(album.title, "Looking for its cover…");
  const r = await albumArt(instanceId, album.id, "find");
  if (!r.ok) return notify(album.title, r.error);
  if (!r.data) return notify(album.title, "No cover found on the Cover Art Archive");
  invalidateBrowserCache();
  notify(album.title, "Cover found");
}

async function clearAlbumArt(instanceId: string, album: Collection) {
  await albumArt(instanceId, album.id, "clear");
  invalidateBrowserCache();
  notify(album.title, "Back to the files' own artwork");
}

/** Lyrics for every track of an album without them, kept beside each as an .lrc. */
async function findAlbumLyrics(instanceId: string, album: Collection) {
  notify(album.title, "Looking for lyrics…");
  const r = await findLyrics(instanceId, album.id);
  if (!r.ok) return notify(album.title, r.error);
  const { found, total } = r.data;
  notify(
    album.title,
    total === 0
      ? "Every track has lyrics already"
      : found === 0
        ? "No lyrics found for its tracks"
        : `Lyrics saved for ${found} of ${total} tracks`,
  );
}

/** A track's lyrics, from the lyrics sites, kept beside it as an .lrc (synced when they are). */
export async function saveTrackLyrics(instanceId: string, trackId: string, title: string) {
  const r = await saveLyrics(instanceId, trackId);
  if (!r.ok) return notify(title, r.error);
  notify(title, r.data ? "Lyrics saved to a file beside the track" : "No lyrics found for it");
}

async function artistPicture(instanceId: string, artist: Collection, action: "set" | "find" | "clear") {
  let path: string | undefined;
  if (action === "set") {
    path = (await pickImage()) ?? undefined;
    if (!path) return;
  }
  if (action === "find") notify(artist.title, "Looking for their picture…");
  const r = await artistArt(instanceId, artist.id, action, path);
  if (!r.ok) return notify(action === "set" ? "Couldn't set the picture" : artist.title, r.error);
  if (action === "find" && !r.data) return notify(artist.title, "No picture found for them");
  invalidateBrowserCache();
  notify(artist.title, action === "clear" ? "Picture removed" : action === "find" ? "Picture found" : "Picture set");
}

/** A local artist's menu items: their picture (an artist with one has an art id, "a:..."). */
export function artistManageItems(instanceId: string, artist: Collection): ReactNode[] {
  if (!hasFeature(instanceId, "artwork")) return [];
  return [
    <MenuItem key="pic-set" onSelected={() => artistPicture(instanceId, artist, "set")}>
      Set picture
    </MenuItem>,
    <MenuItem key="pic-find" onSelected={() => artistPicture(instanceId, artist, "find")}>
      Find picture online
    </MenuItem>,
    ...(artist.art?.startsWith("a:")
      ? [
          <MenuItem key="pic-clear" onSelected={() => artistPicture(instanceId, artist, "clear")}>
            Remove picture
          </MenuItem>,
        ]
      : []),
  ];
}

/**
 * A local album's menu items for managing it: its tags, MusicBrainz, its picture. `info` (getTags) says
 * whether it has a picture of its own to take away.
 */
export function albumManageItems(instanceId: string, album: Collection, info: TagInfo | null): ReactNode[] {
  const items: ReactNode[] = [];
  if (hasFeature(instanceId, "tags")) {
    items.push(
      <MenuItem key="tags" onSelected={() => editTags(instanceId, "album", album.id, album.title)}>
        Edit album tags
      </MenuItem>,
      <MenuItem
        key="lookup"
        onSelected={() => showModal(<LookupModal instanceId={instanceId} album={album} files={info?.files ?? 0} />)}
      >
        Look up on MusicBrainz
      </MenuItem>,
      <MenuItem key="lyrics" onSelected={() => findAlbumLyrics(instanceId, album)}>
        Find lyrics
      </MenuItem>,
    );
  }
  if (hasFeature(instanceId, "artwork")) {
    items.push(
      <MenuSeparator key="art-sep" />,
      <MenuItem key="art-set" onSelected={() => pickAlbumArt(instanceId, album)}>
        Set artwork
      </MenuItem>,
      <MenuItem key="art-find" onSelected={() => findAlbumArt(instanceId, album)}>
        Find artwork online
      </MenuItem>,
    );
    if (info?.own_art) {
      items.push(
        <MenuItem key="art-clear" onSelected={() => clearAlbumArt(instanceId, album)}>
          Use the files' artwork
        </MenuItem>,
      );
    }
  }
  return items;
}

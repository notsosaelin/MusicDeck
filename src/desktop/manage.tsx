/**
 * Managing music from desktop Steam's Music section, beyond what one song's menu does (the Game Mode menus
 * open here as Steam's own: TrackMenu's showTrackMenu and showCollectionMenu): several songs at once (a
 * menu for the selection, Get Info for all of them), smart playlists, and dragging music around (songs onto
 * a playlist or a device in the sidebar; files from the desktop into Local files).
 */
import { DialogBody, DialogHeader, Menu, MenuItem, ModalRoot, TextField, showContextMenu, showModal } from "@decky/ui";
import { FC, useEffect, useState } from "react";
import * as api from "../api";
import { Collection, SmartPlaylist, SmartRule, Track } from "../api";
import { DialogActions } from "../components/common";
import { NameModal } from "../components/NameModal";
import { editTags } from "../components/TagEditor";
import { notify } from "../notify";
import { invalidateBrowserCache, playFrom } from "../nav";
import { player } from "../player";
import { Store, pluginState } from "../store";

// --- several songs ------------------------------------------------------------------------------------

export function showSelectionMenu(instanceId: string, tracks: Track[], from: Collection, target: EventTarget) {
  const devices = (pluginState.get()?.devices ?? []).filter((d) => d.kind !== "cd" && d.kind !== "disc" && d.kind !== "ios");
  showContextMenu(
    <Menu label={`${tracks.length} songs`}>
      <MenuItem onSelected={() => playFrom(instanceId, from, tracks, 0)}>Play</MenuItem>
      <MenuItem onSelected={() => player.enqueue(instanceId, tracks, true)}>Play next</MenuItem>
      <MenuItem onSelected={() => player.enqueue(instanceId, tracks)}>Add to queue</MenuItem>
      <MenuItem onSelected={() => pickPlaylist(instanceId, tracks, target)}>Add to playlist…</MenuItem>
      {devices.map((d) => (
        <MenuItem key={d.id} onSelected={() => copyTracks(instanceId, tracks, d.id, d.name)}>
          Copy to {d.name}
        </MenuItem>
      ))}
      {canTag(instanceId) && <MenuItem onSelected={() => bulkInfo(instanceId, tracks)}>Get Info…</MenuItem>}
    </Menu>,
    target,
  );
}

const canTag = (instanceId: string) =>
  !!pluginState.get()?.services.find((s) => s.id === instanceId)?.features.includes("tags");

async function copyTracks(instanceId: string, tracks: Track[], deviceId: string, name: string) {
  let started = 0;
  for (const t of tracks) {
    const r = await api.copyTo(instanceId, "track", t.id, deviceId).catch(() => null);
    if (r?.ok) started++;
  }
  notify("MusicDeck", started ? `Copying ${started} songs to ${name}` : `Couldn't copy to ${name}`);
}

export async function pickPlaylist(instanceId: string, tracks: Track[], target: EventTarget) {
  const lists = await api.localPlaylists(instanceId).catch(() => [] as Collection[]);
  const add = async (playlistId: string, title: string) => {
    const added = await api.playlistAdd(playlistId, tracks).catch(() => 0);
    invalidateBrowserCache();
    notify("MusicDeck", added ? `Added ${added} songs to ${title}` : `They're already in ${title}`);
  };
  showContextMenu(
    <Menu label="Add to playlist">
      <MenuItem
        onSelected={() =>
          showModal(
            <NameModal
              title="New playlist"
              action="Create"
              onSubmit={async (name) => {
                await api.createPlaylist(instanceId, name, tracks);
                invalidateBrowserCache();
                notify("MusicDeck", `Made ${name}`);
              }}
            />,
          )
        }
      >
        New playlist…
      </MenuItem>
      {lists.map((l) => (
        <MenuItem key={l.id} onSelected={() => add(l.id, l.title)}>
          {l.title}
        </MenuItem>
      ))}
    </Menu>,
    target,
  );
}

const BULK_FIELDS: [string, string][] = [
  ["artist", "Artist"],
  ["albumartist", "Album artist"],
  ["album", "Album"],
  ["genre", "Genre"],
  ["date", "Year"],
];

/** Get Info for several songs: what's typed is given to all of them; what's left empty stays as it is. */
function bulkInfo(instanceId: string, tracks: Track[]) {
  if (tracks.length === 1) {
    editTags(instanceId, "track", tracks[0].id, tracks[0].title);
    return;
  }
  showModal(<BulkInfo instanceId={instanceId} tracks={tracks} />);
}

const BulkInfo: FC<{ instanceId: string; tracks: Track[]; closeModal?: () => void }> = ({ instanceId, tracks, closeModal }) => {
  const common = (pick: (t: Track) => string) => (tracks.every((t) => pick(t) === pick(tracks[0])) ? pick(tracks[0]) : "");
  const [values, setValues] = useState<Record<string, string>>({
    artist: common((t) => t.artist),
    album: common((t) => t.album),
  });
  const [busy, setBusy] = useState(false);
  const apply = async () => {
    const changes = Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim()));
    if (!Object.keys(changes).length) return closeModal?.();
    setBusy(true);
    let changed = 0;
    for (const t of tracks) {
      const r = await api.setTags(instanceId, "track", t.id, changes).catch(() => null);
      if (r?.ok) changed += r.data.changed;
    }
    invalidateBrowserCache();
    notify("MusicDeck", `Changed ${changed} of ${tracks.length} songs`);
    closeModal?.();
  };
  return (
    <ModalRoot closeModal={closeModal} onCancel={closeModal}>
      <DialogHeader>Get Info: {tracks.length} songs</DialogHeader>
      <DialogBody>
        <div style={{ color: "#8b929a", marginBottom: 8 }}>What you type is given to every song; empty fields stay as they are.</div>
        {BULK_FIELDS.map(([key, label]) => (
          <TextField
            key={key}
            label={label}
            value={values[key] ?? ""}
            onChange={(e) => setValues({ ...values, [key]: e.target.value })}
          />
        ))}
      </DialogBody>
      <DialogActions primary={busy ? "Saving…" : "Save"} onPrimary={apply} primaryDisabled={busy} onCancel={closeModal} />
    </ModalRoot>
  );
};

// --- smart playlists --------------------------------------------------------------------------------

export const smartLists = new Store<SmartPlaylist[]>([]);

export async function loadSmart() {
  smartLists.set(await api.smartPlaylists().catch(() => smartLists.get()));
}

const FIELDS: [SmartRule["field"], string, "text" | "number" | "flag"][] = [
  ["artist", "Artist", "text"],
  ["albumartist", "Album artist", "text"],
  ["album", "Album", "text"],
  ["title", "Title", "text"],
  ["genre", "Genre", "text"],
  ["year", "Year", "number"],
  ["plays", "Plays", "number"],
  ["added_days", "Days since added", "number"],
  ["played_days", "Days since played", "number"],
  ["duration", "Length (seconds)", "number"],
  ["starred", "Favourite", "flag"],
];
const OPS: Record<string, [SmartRule["op"], string][]> = {
  text: [["contains", "contains"], ["is", "is"], ["is_not", "is not"], ["starts", "starts with"]],
  number: [["lt", "less than"], ["gt", "more than"], ["is", "is"], ["is_not", "is not"]],
  flag: [["is", "is"]],
};
const SORTS: [string, string][] = [
  ["added", "Recently added"],
  ["played", "Recently played"],
  ["plays", "Most played"],
  ["artist", "Artist"],
  ["title", "Title"],
  ["year", "Newest first"],
  ["random", "Random"],
];

const selectStyle = { background: "#23262e", color: "#dcdedf", border: "1px solid #3d4450", borderRadius: 3, height: 30, padding: "0 6px" };

export const SmartEditor: FC<{ source: string; existing?: SmartPlaylist; closeModal?: () => void; onSaved?: (p: SmartPlaylist) => void }> = ({
  source,
  existing,
  closeModal,
  onSaved,
}) => {
  const [name, setName] = useState(existing?.name ?? "");
  const [rules, setRules] = useState<SmartRule[]>(existing?.rules.rules ?? [{ field: "added_days", op: "lt", value: "30" }]);
  const [match, setMatch] = useState(existing?.rules.match ?? "all");
  const [sort, setSort] = useState(existing?.rules.sort ?? "added");
  const [limit, setLimit] = useState(String(existing?.rules.limit ?? 100));
  const [count, setCount] = useState<number | null>(null);
  const spec = { rules, match, sort, limit: Number(limit) || 0 } as SmartPlaylist["rules"];
  useEffect(() => {
    const t = window.setTimeout(() => {
      api.smartTracks(source, spec).then((r) => setCount(r.ok ? r.data.length : null)).catch(() => setCount(null));
    }, 300);
    return () => window.clearTimeout(t);
  }, [JSON.stringify(spec)]);
  const set = (i: number, patch: Partial<SmartRule>) => setRules(rules.map((r, n) => (n === i ? { ...r, ...patch } : r)));
  const save = async () => {
    const all = await api.saveSmartPlaylist({ id: existing?.id, name: name.trim(), source, rules: spec });
    smartLists.set(all);
    const saved = all.find((p) => p.name === name.trim() && p.source === source) ?? all[all.length - 1];
    onSaved?.(saved);
    closeModal?.();
  };
  return (
    <ModalRoot closeModal={closeModal} onCancel={closeModal}>
      <DialogHeader>{existing ? "Edit smart playlist" : "New smart playlist"}</DialogHeader>
      <DialogBody>
        <TextField label="Name" value={name} focusOnMount onChange={(e) => setName(e.target.value)} />
        <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "12px 0 8px" }}>
          Songs matching
          <select style={selectStyle} value={match} onChange={(e) => setMatch(e.target.value as "all" | "any")}>
            <option value="all">all</option>
            <option value="any">any</option>
          </select>
          of these:
        </div>
        {rules.map((r, i) => {
          const kind = FIELDS.find((f) => f[0] === r.field)?.[2] ?? "text";
          return (
            <div key={i} style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center" }}>
              <select
                style={selectStyle}
                value={r.field}
                onChange={(e) => {
                  const field = e.target.value as SmartRule["field"];
                  const k = FIELDS.find((f) => f[0] === field)?.[2] ?? "text";
                  set(i, { field, op: OPS[k][0][0], value: k === "flag" ? "true" : "" });
                }}
              >
                {FIELDS.map(([f, label]) => (
                  <option key={f} value={f}>
                    {label}
                  </option>
                ))}
              </select>
              <select style={selectStyle} value={r.op} onChange={(e) => set(i, { op: e.target.value as SmartRule["op"] })}>
                {OPS[kind].map(([op, label]) => (
                  <option key={op} value={op}>
                    {label}
                  </option>
                ))}
              </select>
              {kind === "flag" ? (
                <select style={selectStyle} value={r.value} onChange={(e) => set(i, { value: e.target.value })}>
                  <option value="true">yes</option>
                  <option value="false">no</option>
                </select>
              ) : (
                <input
                  style={{ ...selectStyle, flex: 1, minWidth: 0 }}
                  type={kind === "number" ? "number" : "text"}
                  value={r.value}
                  onChange={(e) => set(i, { value: e.target.value })}
                />
              )}
              <button style={{ ...selectStyle, cursor: "pointer" }} onClick={() => setRules(rules.filter((_, n) => n !== i))} aria-label="Remove rule">
                −
              </button>
            </div>
          );
        })}
        <button style={{ ...selectStyle, cursor: "pointer" }} onClick={() => setRules([...rules, { field: "artist", op: "contains", value: "" }])}>
          + Add a rule
        </button>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
          Sorted by
          <select style={selectStyle} value={sort} onChange={(e) => setSort(e.target.value)}>
            {SORTS.map(([s, label]) => (
              <option key={s} value={s}>
                {label}
              </option>
            ))}
          </select>
          at most
          <input style={{ ...selectStyle, width: 70 }} type="number" min={1} value={limit} onChange={(e) => setLimit(e.target.value)} />
          songs
          <span style={{ marginLeft: "auto", color: "#8b929a" }}>{count === null ? "" : `${count} songs now`}</span>
        </div>
      </DialogBody>
      <DialogActions primary="Save" onPrimary={save} primaryDisabled={!name.trim()} onCancel={closeModal} />
    </ModalRoot>
  );
};

export function showSmartMenu(p: SmartPlaylist, target: EventTarget, onDeleted: () => void) {
  showContextMenu(
    <Menu label={p.name}>
      <MenuItem onSelected={() => showModal(<SmartEditor source={p.source} existing={p} />)}>Edit rules…</MenuItem>
      <MenuItem
        tone="destructive"
        onSelected={async () => {
          smartLists.set(await api.deleteSmartPlaylist(p.id));
          onDeleted();
        }}
      >
        Delete
      </MenuItem>
    </Menu>,
    target,
  );
}

// --- dragging music ---------------------------------------------------------------------------------

/** What a drag from the Music section carries */
export const DRAG_TYPE = "application/x-musicdeck";
export type Dragged = { instanceId: string; tracks?: Track[]; collection?: Collection };

export function dragData(e: { dataTransfer: DataTransfer | null }): Dragged | null {
  try {
    const raw = e.dataTransfer?.getData(DRAG_TYPE);
    return raw ? (JSON.parse(raw) as Dragged) : null;
  } catch {
    return null;
  }
}

const songsOf = async (d: Dragged): Promise<Track[]> => {
  if (d.tracks) return d.tracks;
  if (!d.collection) return [];
  const r = await api.openCollection(d.instanceId, d.collection.kind, d.collection.id);
  return r.ok ? r.data.tracks : [];
};

/** Dropped on a playlist in the sidebar: its songs added to it. */
export async function dropOnPlaylist(d: Dragged, playlistId: string, title: string) {
  const tracks = await songsOf(d);
  if (!tracks.length) return;
  const added = await api.playlistAdd(playlistId, tracks).catch(() => 0);
  invalidateBrowserCache();
  notify("MusicDeck", added ? `Added ${added} songs to ${title}` : `They're already in ${title}`);
}

/** Dropped on a device in the sidebar: copied to it, as the menus' Copy to does. */
export async function dropOnDevice(d: Dragged, deviceId: string, name: string) {
  if (d.collection) {
    const r = await api.copyTo(d.instanceId, d.collection.kind, d.collection.id, deviceId).catch(() => null);
    notify("MusicDeck", r?.ok ? r.data : r && !r.ok ? r.error : `Couldn't copy to ${name}`);
  } else if (d.tracks) await copyTracks(d.instanceId, d.tracks, deviceId, name);
}

const MUSIC_FILE = /\.(mp3|flac|ogg|opus|m4a|aac|wav|aiff?|wma|alac|ape|wv|mpc|m3u8?|pls)$/i;

/** Files dragged in from the desktop: into Local files (its Imported folder), through the stream server. */
export async function importFiles(instanceId: string, files: File[]) {
  const state = pluginState.get();
  const source = state?.services.find((s) => s.id === instanceId);
  if (!state || source?.type !== "local") {
    notify("MusicDeck", "Choose Local files first: dropped music goes into your own library");
    return;
  }
  const music = files.filter((f) => MUSIC_FILE.test(f.name));
  if (!music.length) {
    notify("MusicDeck", "Nothing there is a music file");
    return;
  }
  let taken = 0;
  for (const f of music) {
    const url = `${state.server.base}/import/${encodeURIComponent(instanceId)}?k=${encodeURIComponent(state.server.key)}&name=${encodeURIComponent(f.name)}`;
    try {
      // as plain text: a "simple" request, which needs no CORS preflight
      const r = await fetch(url, { method: "POST", body: new Blob([f], { type: "text/plain" }) });
      if ((await r.json()).ok) taken++;
    } catch {}
  }
  notify("MusicDeck", taken ? `Added ${taken} of ${music.length} to Local files (Imported)` : "Couldn't add them");
}

import { openFilePicker } from "@decky/api";
import { ReactNode } from "react";
import { ConfirmModal, Menu, MenuItem, MenuSeparator, showContextMenu, showModal } from "@decky/ui";
import {
  Collection,
  LikeWords,
  Track,
  addToPlaylist,
  createPlaylist,
  createServicePlaylist,
  getUserPlaylists,
  isLocalPlaylist,
  localPlaylists,
  playlistAdd,
  playlistClearArt,
  playlistDelete,
  playlistRemove,
  playlistRename,
  playlistSetArt,
  saveAsPlaylist,
  getTags,
  setPlayed,
  star,
  copyTo,
  removeFromDevice,
  deleteFromLibrary,
  ejectDevice,
  renameDevice,
  onDevice,
  burnPlan,
  burnCd,
  openCollection,
  collectionOffline,
  savedCopy,
} from "../api";
import { invalidateBrowserCache, navigation, openInLibrary, popView } from "../nav";
import { notify } from "../notify";
import { SleepChoice, setSleepTimer, sleepTimerLabel } from "../sleep";
import { canPlayWithGame, musicFor, runningGame, toggleGameMusic } from "../games";
import { hasFeature, player } from "../player";
import { pluginState } from "../store";
import { NameModal } from "./NameModal";
import { canRate, rateTrack, starText } from "../ratings";
import { confirmRemove, downloadable, setDownload } from "../download";
import { albumManageItems, artistManageItems, editTags, saveTrackLyrics } from "./TagEditor";

type Item = { instanceId: string; track: Track };

/** A source's words for a favourite (Spotify's Liked Songs, SoundCloud's likes). */
export const likeWords = (instanceId: string): LikeWords =>
  pluginState.get()?.services.find((s) => s.id === instanceId)?.like_words ?? {
    add: "Add to favourites",
    remove: "Remove from favourites",
    added: "Added to favourites",
    removed: "Removed from favourites",
  };

export async function toggleStar(instanceId: string, track: Track) {
  const starred = !track.starred;
  const words = likeWords(instanceId);
  const r = await star(instanceId, track.id, starred, track);
  if (!r.ok) {
    notify(`Couldn't ${(starred ? words.add : words.remove).toLowerCase()}`, r.error, { instanceId, track });
    return;
  }
  player.updateTrack(instanceId, track.id, { starred });
  // lists showing this track (and the Favourites list) are now stale
  invalidateBrowserCache();
  notify(starred ? words.added : words.removed, track.title, { instanceId, track });
}

/** One to five stars for a song, or none. */
function showRateMenu(instanceId: string, track: Track, parent: EventTarget) {
  showContextMenu(
    <Menu label={`Rate ${track.title}`}>
      {[5, 4, 3, 2, 1].map((n) => (
        <MenuItem key={n} onSelected={() => rateTrack(instanceId, { ...track, rating: 0 }, n)}>
          {starText(n)}
          {track.rating === n ? "  ✓" : ""}
        </MenuItem>
      ))}
      {!!track.rating && (
        <MenuItem onSelected={() => rateTrack(instanceId, { ...track, rating: track.rating }, track.rating!)}>No rating</MenuItem>
      )}
    </Menu>,
    parent,
  );
}

/** A new playlist with the tracks in it: on the source when it keeps playlists, else MusicDeck's own. */
function newPlaylist(instanceId: string, tracks: Track[], what: string, server: boolean) {
  const item = { instanceId, track: tracks[0] };
  showModal(
    <NameModal
      title="New playlist"
      action="Create"
      onSubmit={async (name) => {
        if (server) {
          const made = await createServicePlaylist(instanceId, name, tracks.map((t) => t.id));
          if (!made.ok) return notify("Couldn't make the playlist", made.error, item);
        } else {
          await createPlaylist(instanceId, name, tracks);
        }
        invalidateBrowserCache();
        notify(`Added to ${name}`, what, item);
      }}
    />,
  );
}

/**
 * Sources with server-side playlists (Navidrome) add there; everything else uses MusicDeck's own
 * playlists, kept on the device.
 */
export function showPlaylistPicker(item: Item, parent: EventTarget) {
  return pickPlaylistFor(item.instanceId, [item.track], item.track.title, parent);
}

/** The playlist picker for several tracks at once (an album's, a playlist's); `what` names them in the toast. */
export async function pickPlaylistFor(instanceId: string, tracks: Track[], what: string, parent: EventTarget) {
  if (!tracks.length) return;
  const item = { instanceId, track: tracks[0] };
  const server = hasFeature(instanceId, "playlists");
  let playlists: Collection[];
  if (server) {
    const r = await getUserPlaylists(instanceId);
    if (!r.ok) {
      notify("Couldn't load playlists", r.error, item);
      return;
    }
    playlists = r.data;
  } else {
    playlists = await localPlaylists(instanceId);
  }
  showContextMenu(
    <Menu label="Add to playlist">
      <MenuItem onSelected={() => newPlaylist(instanceId, tracks, what, server)}>New playlist</MenuItem>
      {playlists.length > 0 && <MenuSeparator />}
      {playlists.map((p) => (
        <MenuItem
          key={p.id}
          onSelected={async () => {
            if (server) {
              const res = await addToPlaylist(instanceId, p.id, tracks.map((t) => t.id));
              if (!res.ok) return notify("Couldn't add to playlist", res.error, item);
            } else {
              const added = await playlistAdd(p.id, tracks);
              if (!added) return notify(`Already in ${p.title}`, what, item);
            }
            invalidateBrowserCache();
            notify(`Added to ${p.title}`, what, item);
          }}
        >
          {p.title}
        </MenuItem>
      ))}
    </Menu>,
    parent,
  );
}

/** The menu's download entry for a playlist or album: Download for offline, or Remove download. */
async function downloadItems(instanceId: string, c: Collection): Promise<ReactNode[]> {
  if (!downloadable(instanceId, c)) return [];
  const state = await collectionOffline(instanceId, c.kind, c.id).catch(() => null);
  if (state?.on)
    return [
      <MenuItem key="offline" onSelected={() => confirmRemove(instanceId, c)}>
        {state.saved < state.total ? `Downloaded ${state.saved} of ${state.total} · Remove download` : "Remove download"}
      </MenuItem>,
    ];
  return [
    <MenuItem key="offline" onSelected={() => setDownload(instanceId, c, true)}>
      Download for offline
    </MenuItem>,
  ];
}

/** The queue (what's playing and what's next, in the order it'll play) into a playlist, new or one of yours. */
export function saveQueue(target: EventTarget) {
  const { queue, index } = player.state.get();
  const order = player.playOrder;
  const at = order.indexOf(index);
  const tracks = (at >= 0 ? order.slice(at) : order).map((i) => queue[i]?.track).filter((t): t is Track => !!t);
  const instanceId = queue[index]?.instanceId ?? queue[0]?.instanceId;
  if (instanceId && tracks.length) pickPlaylistFor(instanceId, tracks, `${tracks.length} songs from the queue`, target);
}

/** What anything you can play (an album, a playlist) offers whatever the source: its songs after this one, at
 *  the end of the queue, or into a playlist. They're fetched when chosen. */
function playItems(instanceId: string, c: Collection, target: EventTarget) {
  const tracks = async () => {
    const r = await openCollection(instanceId, c.kind, c.id);
    if (!r.ok) notify("Couldn't open it", r.error);
    return r.ok ? r.data.tracks : [];
  };
  return [
    <MenuItem key="next" onSelected={async () => {
      const t = await tracks();
      if (t.length) {
        player.enqueue(instanceId, t, true);
        notify("Playing next", c.title);
      }
    }}>
      Play next
    </MenuItem>,
    <MenuItem key="queue" onSelected={async () => {
      const t = await tracks();
      if (t.length) {
        player.enqueue(instanceId, t);
        notify("Added to queue", c.title);
      }
    }}>
      Add to queue
    </MenuItem>,
    <MenuItem key="playlist" onSelected={async () => pickPlaylistFor(instanceId, await tracks(), c.title, target)}>
      Add to playlist
    </MenuItem>,
  ];
}

/** Episodes and books: "Played" shows in their second line (see podcasts.py), and lists refresh. */
async function markPlayed(instanceId: string, track: Track, played: boolean) {
  const done = await setPlayed(instanceId, track, played);
  if (!done.ok) return notify(track.title, done.error, { instanceId, track });
  invalidateBrowserCache();
  notify(played ? "Marked as played" : "Marked as unplayed", track.title, { instanceId, track });
}

/**
 * Copying between the Deck and what's plugged in (Local files only, services/shelf.py): the Deck's
 * music to each player, phone or stick; a device's to the Deck. A CD is ripped from its album page.
 */
function copyItems(instanceId: string, kind: string, id: string): ReactNode[] {
  const state = pluginState.get();
  if (state?.services.find((s) => s.id === instanceId)?.type !== "local") return [];
  const copy = async (target: string) => {
    const r = await copyTo(instanceId, kind, id, target);
    notify(r.ok ? "Local files" : "Couldn't copy", r.ok ? r.data : r.error);
  };
  if (onDevice(id)) {
    const device = state.devices.find((d) => id.startsWith(`dv~${d.id}~`));
    if (!device || device.kind === "cd") return [];
    // a data disc, an iPhone: copied from, never changed
    if (device.status.writable === false && device.kind !== "ipod") {
      return [
        <MenuItem key="copy-deck" onSelected={() => copy("deck")}>
          Copy to this Deck
        </MenuItem>,
      ];
    }
    // deleting from a player (or an iPod's library) is asked about first: it can't be undone
    const remove = () =>
      showModal(
        <ConfirmModal
          strTitle={`Remove from ${device.name}?`}
          strDescription={
            kind === "track"
              ? "The song is deleted from the device."
              : "Its songs are deleted from the device. Copies on this Deck stay."
          }
          strOKButtonText="Remove"
          bDestructiveWarning
          onOK={async () => {
            const r = await removeFromDevice(instanceId, kind, id);
            invalidateBrowserCache();
            notify(r.ok ? device.name : "Couldn't remove", r.ok ? r.data : r.error);
          }}
        />,
      );
    return [
      <MenuItem key="copy-deck" onSelected={() => copy("deck")}>
        Copy to this Deck
      </MenuItem>,
      <MenuItem key="remove-device" tone="destructive" onSelected={remove}>
        Remove from {device.name}
      </MenuItem>,
    ];
  }
  return state.devices
    .filter((d) => d.kind !== "cd" && (d.kind === "ipod" || d.status.writable !== false))
    .map((d) => (
      <MenuItem key={`copy-${d.id}`} onSelected={() => copy(d.id)}>
        Copy to {d.name}
      </MenuItem>
    ));
}

const BURNABLE = ["album", "artist", "playlist"];

/**
 * Burn to CD (Local files, with a CD drive and a burner: services/shelf.py, burn.py): an album, artist or
 * playlist as an audio CD. What it would be is asked first: its length, the disc's, and that a CD-RW with
 * something on it is erased.
 */
function burnItems(instanceId: string, kind: string, id: string): ReactNode[] {
  const state = pluginState.get();
  const local = state?.services.find((s) => s.id === instanceId)?.type === "local";
  if (!local || !state?.can_burn || onDevice(id) || !BURNABLE.includes(kind) || id.startsWith("deleted:")) return [];
  const start = async () => {
    const plan = await burnPlan(instanceId, kind, id);
    if (!plan.ok) return notify("Can't burn a CD", plan.error);
    const p = plan.data;
    showModal(
      <ConfirmModal
        strTitle={`Burn ${p.title} to CD?`}
        strDescription={
          `${p.songs} song${p.songs === 1 ? "" : "s"}, ${p.minutes} of the CD's ${p.capacity} minutes, as an audio CD ` +
          "any CD player plays, with its titles (CD-Text)." +
          (p.erase ? " The CD-RW is erased first: what's on it now goes." : "")
        }
        strOKButtonText="Burn"
        bDestructiveWarning={p.erase}
        onOK={async () => {
          const r = await burnCd(instanceId, kind, id);
          notify(r.ok ? "Burning a CD" : "Can't burn a CD", r.ok ? r.data : r.error);
        }}
      />,
    );
  };
  return [
    <MenuItem key="burn" onSelected={start}>
      Burn to CD
    </MenuItem>,
  ];
}

const DELETABLE = ["track", "album", "folder", "playlist"];

/**
 * Deleting from the Deck's library (Local files): a song, an album, a folder or a playlist file goes to
 * the trash, where Local files' Recently deleted brings it back. Artists aren't deleted whole. A
 * MusicDeck playlist has its own Delete; music on a device is removed from it instead.
 */
function deleteItems(instanceId: string, kind: string, id: string, title: string): ReactNode[] {
  const local = pluginState.get()?.services.find((s) => s.id === instanceId)?.type === "local";
  if (!local || onDevice(id) || isLocalPlaylist(id) || !DELETABLE.includes(kind) || id.startsWith("deleted:"))
    return [];
  const what: Record<string, string> = {
    track: "The song moves to the trash.",
    album: "Its songs move to the trash, with their folder when nothing else is in it.",
    folder: "The folder and everything in it move to the trash.",
    playlist: "The playlist file moves to the trash. Its songs stay.",
  };
  const remove = () =>
    showModal(
      <ConfirmModal
        strTitle={`Delete ${title}?`}
        strDescription={`${what[kind]} Recently deleted, in Local files, brings it back.`}
        strOKButtonText="Delete"
        bDestructiveWarning
        onOK={async () => {
          const r = await deleteFromLibrary(instanceId, kind, id);
          invalidateBrowserCache();
          notify(r.ok ? "Local files" : "Couldn't delete", r.ok ? r.data : r.error);
          // looking at what's gone: step back out
          const views = navigation.get().stacks.library;
          const top = views[views.length - 1];
          if (r.ok && top?.type === "collection" && top.collection.id === id) popView("library");
        }}
      />,
    );
  return [
    <MenuItem key="delete" tone="destructive" onSelected={remove}>
      Delete
    </MenuItem>,
  ];
}

/** Whether a collection can be deleted from the Deck's library. */
export const canDelete = (instanceId: string, c: Collection) =>
  deleteItems(instanceId, c.kind, c.id, c.title).length > 0;

/** A device's own menu (its card in Devices): eject it, and name it. */
function deviceMenu(c: Collection, target: EventTarget) {
  const device = pluginState.get()?.devices.find((d) => c.id === `dv~${d.id}~` || c.id.startsWith(`dv~${d.id}~`));
  if (!device) return;
  const eject = async () => {
    const r = await ejectDevice(device.id);
    invalidateBrowserCache();
    notify(device.name, r.ok ? r.data : r.error);
  };
  showContextMenu(
    <Menu label={device.name}>
      <MenuItem onSelected={eject}>{device.kind === "cd" ? "Eject disc" : "Eject"}</MenuItem>
      {device.kind !== "cd" && device.kind !== "disc" && (
        <MenuItem
          onSelected={() =>
            showModal(
              <NameModal
                title="Rename"
                initial={device.name}
                action="Save"
                onSubmit={async (name) => {
                  await renameDevice(device.id, name);
                  invalidateBrowserCache();
                }}
              />,
            )
          }
        >
          Rename
        </MenuItem>
      )}
    </Menu>,
    target,
  );
}

/** Whether an album, artist or playlist can be copied somewhere (to a device, or from one to the Deck). */
export const canCopy = (instanceId: string, c: Collection) =>
  (c.kind === "album" || c.kind === "artist" || c.kind === "playlist") &&
  (copyItems(instanceId, c.kind, c.id).length > 0 || burnItems(instanceId, c.kind, c.id).length > 0);

/** Context about where the menu was opened, for page-specific items. */
export interface MenuContext {
  /** the MusicDeck playlist being viewed, if any */
  playlistId?: string;
  /** a track in the Queue: where it plays, and how to pick it up and move it with the d-pad */
  queue?: { position: number; startMoving: () => void };
}

/** Moving a queued track (positions in play order, as the Queue lists them). */
function queueItems(position: number, startMoving: () => void) {
  const order = player.playOrder;
  const current = order.indexOf(player.state.get().index);
  const last = order.length - 1;
  // right after the playing track (removing it from above that track shifts the target up by one)
  const next = position > current ? current + 1 : current;
  return [
    <MenuItem key="move" onSelected={startMoving}>
      Move
    </MenuItem>,
    position !== current && position !== next && (
      <MenuItem key="next" onSelected={() => player.move(position, next)}>
        Play next
      </MenuItem>
    ),
    position > 0 && (
      <MenuItem key="up" onSelected={() => player.move(position, position - 1)}>
        Move up
      </MenuItem>
    ),
    position < last && (
      <MenuItem key="down" onSelected={() => player.move(position, position + 1)}>
        Move down
      </MenuItem>
    ),
    position < last && (
      <MenuItem key="end" onSelected={() => player.move(position, last)}>
        Move to the end
      </MenuItem>
    ),
  ];
}

/**
 * Everything you can do with a track, as a native context menu. Items only appear when the track's
 * source supports them, so the menu stays short.
 */
const SLEEP_MINUTES = [15, 30, 45, 60, 90];

function showSleepMenu(parent: EventTarget) {
  const choose = (choice: SleepChoice, message: string) => {
    setSleepTimer(choice);
    notify("Sleep timer", message);
  };
  showContextMenu(
    <Menu label="Sleep timer">
      {SLEEP_MINUTES.map((m) => (
        <MenuItem key={m} onSelected={() => choose(m, `Pausing in ${m} minutes`)}>
          {m} minutes
        </MenuItem>
      ))}
      <MenuItem onSelected={() => choose("track", "Pausing when this track ends")}>End of this track</MenuItem>
      {sleepTimerLabel() && (
        <>
          <MenuSeparator />
          <MenuItem tone="destructive" onSelected={() => choose(null, "Off")}>
            Turn off
          </MenuItem>
        </>
      )}
    </Menu>,
    parent,
  );
}

export function showTrackMenu(instanceId: string, track: Track, parent?: EventTarget | null, ctx: MenuContext = {}) {
  const target = parent ?? window;
  const item = { instanceId, track };
  const isCurrent = player.current?.instanceId === instanceId && player.current.track.id === track.id;
  // the Library tab browses the active source, so only offer "Go to" for its tracks
  const browsable = pluginState.get()?.active_service === instanceId;
  showContextMenu(
    <Menu label={track.title}>
      {/* in the Queue these move the track; elsewhere they add it */}
      {ctx.queue && queueItems(ctx.queue.position, ctx.queue.startMoving)}
      {!isCurrent && !ctx.queue && (
        <MenuItem
          onSelected={() => {
            player.enqueue(instanceId, [track], true);
            notify("Playing next", track.title, item);
          }}
        >
          Play next
        </MenuItem>
      )}
      {!isCurrent && !ctx.queue && (
        <MenuItem
          onSelected={() => {
            player.enqueue(instanceId, [track]);
            notify("Added to queue", track.title, item);
          }}
        >
          Add to queue
        </MenuItem>
      )}
      {hasFeature(instanceId, "radio") && (
        <MenuItem onSelected={() => player.startRadio(instanceId, track)}>Start radio</MenuItem>
      )}
      {hasFeature(instanceId, "progress") && (
        <MenuItem onSelected={() => markPlayed(instanceId, track, !track.artist.endsWith("Played"))}>
          {track.artist.endsWith("Played") ? "Mark as unplayed" : "Mark as played"}
        </MenuItem>
      )}
      {hasFeature(instanceId, "star") && (
        <MenuItem onSelected={() => toggleStar(instanceId, track)}>
          {track.starred ? likeWords(instanceId).remove : likeWords(instanceId).add}
        </MenuItem>
      )}
      {canRate(instanceId) && (
        <MenuItem onSelected={() => showRateMenu(instanceId, track, target)}>
          {track.rating ? `Rating ${starText(track.rating)}` : "Rate…"}
        </MenuItem>
      )}
      <MenuItem onSelected={() => showPlaylistPicker(item, target)}>Add to playlist</MenuItem>
      {copyItems(instanceId, "track", track.id)}
      {deleteItems(instanceId, "track", track.id, track.title)}
      {/* a device's files aren't the Deck's to change */}
      {hasFeature(instanceId, "tags") && !onDevice(track.id) && (
        <MenuItem onSelected={() => editTags(instanceId, "track", track.id, track.title)}>Edit tags</MenuItem>
      )}
      {hasFeature(instanceId, "tags") && !onDevice(track.id) && (
        <MenuItem onSelected={() => saveTrackLyrics(instanceId, track.id, track.title)}>Save lyrics</MenuItem>
      )}
      {ctx.playlistId && (
        <MenuItem
          tone="destructive"
          onSelected={async () => {
            await playlistRemove(ctx.playlistId!, track.id);
            invalidateBrowserCache();
          }}
        >
          Remove from this playlist
        </MenuItem>
      )}
      {isCurrent && <MenuSeparator />}
      {isCurrent && (
        <MenuItem onSelected={() => showSleepMenu(target)}>
          {sleepTimerLabel() ? `Sleep timer · ${sleepTimerLabel()}` : "Sleep timer"}
        </MenuItem>
      )}

      {browsable && (track.album_id || track.artist_id) && <MenuSeparator />}
      {browsable && track.album_id && (
        <MenuItem
          onSelected={() =>
            openInLibrary({
              id: track.album_id!,
              kind: "album",
              title: track.album,
              subtitle: track.artist,
              art: track.art,
            })
          }
        >
          {/* an episode's "album" is its show, a book file's the book: named rather than called an album */}
          {hasFeature(instanceId, "progress") ? `Go to ${track.album}` : "Go to album"}
        </MenuItem>
      )}
      {browsable && track.artist_id && (
        <MenuItem
          onSelected={() =>
            openInLibrary({ id: track.artist_id!, kind: "artist", title: track.artist, subtitle: "", art: null })
          }
        >
          Go to artist
        </MenuItem>
      )}
    </Menu>,
    target,
  );
}

// FileSelectionType.FILE from @decky/api (a const enum, which isolated builds can't import)
const SELECT_FILE = 0;
const IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "gif"];

/** Pick a picture for a MusicDeck playlist; it's copied into MusicDeck, so the original can move. */
async function pickArtwork(c: Collection) {
  const home = pluginState.get()?.user_home || "/home";
  let path: string;
  try {
    path = (await openFilePicker(SELECT_FILE, home, true, true, undefined, IMAGE_EXTENSIONS)).realpath;
  } catch {
    return; // picker cancelled
  }
  const r = await playlistSetArt(c.id, path);
  if (!r.ok) {
    notify("Couldn't set artwork", r.error);
    return;
  }
  invalidateBrowserCache();
  notify("Artwork updated", c.title);
}

/** Saves a source's playlist or album as a MusicDeck playlist (kept on the device, editable). */
export async function saveCollection(instanceId: string, c: Collection) {
  const r = await saveAsPlaylist(instanceId, c.kind, c.id);
  if (!r.ok) {
    notify("Couldn't save playlist", r.error);
    return;
  }
  invalidateBrowserCache();
  notify("Saved to your playlists", c.title);
}

/** "Play when <game> starts" while a game runs (album and playlist rows). */
function gameMusicItem(instanceId: string, c: Collection) {
  const game = runningGame.get();
  if (!game || !canPlayWithGame(c)) return null;
  const on = musicFor(game.appid, instanceId)?.collection.id === c.id;
  return (
    <MenuItem onSelected={() => toggleGameMusic(instanceId, c)}>
      {on ? `Don't play with ${game.name}` : `Play when ${game.name} starts`}
    </MenuItem>
  );
}

/** Options for a collection row: save remote playlists, manage MusicDeck playlists, play with a game. */
export async function showCollectionMenu(instanceId: string, c: Collection, parent?: EventTarget | null) {
  const target = parent ?? window;
  if (c.kind === "device") {
    deviceMenu(c, target);
    return;
  }
  if (c.kind === "folder") {
    showContextMenu(<Menu label={c.title}>{deleteItems(instanceId, "folder", c.id, c.title)}</Menu>, target);
    return;
  }
  if (isLocalPlaylist(c.id)) {
    const offline = await downloadItems(instanceId, c);
    showContextMenu(
      <Menu label={c.title}>
        {playItems(instanceId, c, target)}
        {offline}
        <MenuSeparator />
        {gameMusicItem(instanceId, c)}
        <MenuItem
          onSelected={() =>
            showModal(
              <NameModal
                title="Rename playlist"
                initial={c.title}
                action="Rename"
                onSubmit={async (name) => {
                  await playlistRename(c.id, name);
                  invalidateBrowserCache();
                }}
              />,
            )
          }
        >
          Rename
        </MenuItem>
        <MenuItem onSelected={() => pickArtwork(c)}>Set artwork</MenuItem>
        {copyItems(instanceId, "playlist", c.id)}
        {burnItems(instanceId, "playlist", c.id)}
        {c.art?.startsWith("mdart:") && (
          <MenuItem
            onSelected={async () => {
              await playlistClearArt(c.id);
              invalidateBrowserCache();
            }}
          >
            Use default artwork
          </MenuItem>
        )}
        <MenuSeparator />
        <MenuItem
          tone="destructive"
          onSelected={() =>
            showModal(
              <ConfirmModal
                strTitle={`Delete ${c.title}?`}
                strDescription="The music itself isn't affected."
                strOKButtonText="Delete"
                bDestructiveWarning
                onOK={async () => {
                  await playlistDelete(c.id);
                  invalidateBrowserCache();
                  // if we're looking at it, step back out
                  const views = navigation.get().stacks.library;
                  const top = views[views.length - 1];
                  if (top?.type === "collection" && top.collection.id === c.id) popView("library");
                }}
              />,
            )
          }
        >
          Delete
        </MenuItem>
      </Menu>,
      target,
    );
    return;
  }
  if (c.kind === "artist" && hasFeature(instanceId, "artwork")) {
    const copies = copyItems(instanceId, "artist", c.id);
    const manage = onDevice(c.id) ? [] : artistManageItems(instanceId, c);
    showContextMenu(
      <Menu label={c.title}>
        {copies}
        {burnItems(instanceId, "artist", c.id)}
        {copies.length > 0 && manage.length > 0 && <MenuSeparator />}
        {manage}
      </Menu>,
      target,
    );
    return;
  }
  if (c.kind === "playlist" || c.kind === "album") {
    const offline = await downloadItems(instanceId, c);
    // saved already (from wherever it's opened): no "Save to your playlists" again
    const saved = hasFeature(instanceId, "playlists") ? null : await savedCopy(instanceId, c.kind, c.id).catch(() => null);
    const show = (manage: ReactNode[]) => {
      // the rest, each only where it applies; a separator only between what's there
      const more = [
        gameMusicItem(instanceId, c),
        !hasFeature(instanceId, "playlists") && !saved && (
          <MenuItem key="save" onSelected={() => saveCollection(instanceId, c)}>Save to your playlists</MenuItem>
        ),
        ...[copyItems(instanceId, c.kind, c.id), burnItems(instanceId, c.kind, c.id)].flat(),
      ].filter(Boolean);
      showContextMenu(
        <Menu label={c.title}>
          {playItems(instanceId, c, target)}
          {offline}
          {more.length > 0 && <MenuSeparator />}
          {more}
          {manage.length > 0 && <MenuSeparator />}
          {manage}
          {canDelete(instanceId, c) && <MenuSeparator />}
          {deleteItems(instanceId, c.kind, c.id, c.title)}
        </Menu>,
        target,
      );
    };
    // a local album: its tags and picture too (whether it has a picture of its own decides "Use the files'")
    if (
      c.kind === "album" &&
      !onDevice(c.id) &&
      (hasFeature(instanceId, "tags") || hasFeature(instanceId, "artwork"))
    ) {
      getTags(instanceId, "album", c.id).then((r) => show(albumManageItems(instanceId, c, r.ok ? r.data : null)));
    } else {
      show([]);
    }
  }
}
